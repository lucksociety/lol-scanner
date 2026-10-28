"""Model v2 series simulator: every simulated map draws its own draft from the fitted draft model, conditioned on
that simulation's earlier games (Fearless removes champions picked earlier in the series for BOTH teams).

Per map:  logit P(A wins) = elo_logit + series_noise + map_noise + side_blue*(+1 if A blue else -1)
                            + rho*(continuity_A - continuity_B) + clip(draft_score_A - draft_score_B)
Sides: Game 1 from the official schedule; afterwards the loser of the previous map chooses and takes blue side
with probability q_loser_blue (estimated from historical series).
"""
from collections import Counter, defaultdict
import numpy as np
from .draftmodel import ORDER, DRAFT_CAP


def _gumbel(rng, shape):
    return -np.log(-np.log(rng.random(shape, dtype=np.float32) + 1e-20) + 1e-20)


TOPK = 4   # hand-set: each ban/pick step samples only among the K most likely legal options (renormalised)


def _topk_mask(base, k):
    """-inf everything outside the per-row top-k of `base` (n, M)."""
    if k is None or k >= base.shape[1]:
        return base
    th = np.partition(base, -k, axis=1)[:, -k][:, None]
    return np.where(base >= th, base, -np.inf)


def simulate(spec, sims=100_000, seed=0, chunk=20_000, rating_sd=0.35, map_sd=0.20, track_drafts=True, topk=TOPK):
    """spec keys: bo, fearless, elo_logit, side_blue, rho, q_loser_blue, g1_a_blue (bool or None),
    combos: list of {w, U:(2,5,C), V:(2,C), D:(2,5,C), comp_coef, cont:(2,)}, U_ap, U_tank."""
    rng = np.random.default_rng(seed)
    bo = spec["bo"]; need = bo // 2 + 1
    combos = spec["combos"]; K = len(combos)
    C = combos[0]["U"].shape[-1]
    Uall = np.stack([c["U"] for c in combos]).astype(np.float32)        # (K,2,5,C)
    Vall = np.stack([c["V"] for c in combos]).astype(np.float32)        # (K,2,C)
    Dall = np.stack([c["D"] for c in combos]).astype(np.float32)        # (K,2,5,C)
    cont = np.stack([c["cont"] for c in combos])                          # (K,2)
    wts = np.array([c["w"] for c in combos], float); wts /= wts.sum()
    ap, tank = spec["U_ap"].astype(np.float32), spec["U_tank"].astype(np.float32)
    comp_coef = spec["comp_coef"]
    scores = Counter(); maps_n = Counter(); draft_stats = [defaultdict(lambda: [0, 0.0]) for _ in range(bo)]
    reach = np.zeros(bo); combo_count = np.zeros(K)
    role_cnt = np.zeros((bo, 2, 5, C)); game_win = np.zeros(bo)
    done = 0
    while done < sims:
        n = min(chunk, sims - done); done += n
        k = rng.choice(K, size=n, p=wts); combo_count += np.bincount(k, minlength=K)
        series_lg = spec["elo_logit"] + rng.normal(0, rating_sd, n)
        locked = np.zeros((n, C), bool)
        aw = np.zeros(n, np.int8); bw = np.zeros(n, np.int8)
        if spec.get("g1_a_blue") is None:
            a_blue = rng.random(n) < 0.5
        else:
            a_blue = np.full(n, bool(spec["g1_a_blue"]))
        prev_a_won = None
        for g in range(bo):
            live = (aw < need) & (bw < need)
            if not live.any():
                break
            if g > 0:
                loser_is_a = ~prev_a_won
                takes_blue = rng.random(n) < spec["q_loser_blue"]
                a_blue = np.where(loser_is_a, takes_blue, ~takes_blue)
            reach[g] += live.sum()
            used = locked.copy() if spec["fearless"] else np.zeros((n, C), bool)
            filled = np.zeros((n, 2, 5), bool)        # team 0 = A, 1 = B
            picks = np.full((n, 2, 5), -1, np.int32)
            rows = np.arange(n)
            for side, kind in ORDER:
                team = np.where(a_blue, 0, 1) if side == "B" else np.where(a_blue, 1, 0)
                if kind == "ban":
                    base = Vall[k, team].copy(); base[used] = -np.inf
                    z = _topk_mask(base, topk) + _gumbel(rng, (n, C))
                    c = z.argmax(1); used[rows, c] = True
                else:
                    base = Uall[k, team].copy()
                    base[np.broadcast_to(used[:, None, :], base.shape)] = -np.inf
                    base[filled[rows, team]] = -np.inf
                    z = _topk_mask(base.reshape(n, -1), topk) + _gumbel(rng, (n, 5 * C))
                    flat = z.argmax(1)
                    r, c = flat // C, flat % C
                    used[rows, c] = True; filled[rows, team, r] = True; picks[rows, team, r] = c
            sc = np.zeros((n, 2), np.float32)
            for t in (0, 1):
                pc = picks[:, t, :]
                sc[:, t] = Dall[k, t][np.arange(n)[:, None], np.arange(5)[None, :], pc].sum(1) \
                    + comp_coef * (-0.5 * np.abs(ap[pc].sum(1) - 2.0) - (tank[pc].sum(1) < 1))
            dlt = np.clip(sc[:, 0] - sc[:, 1], -DRAFT_CAP, DRAFT_CAP)
            det = (spec["elo_logit"] + spec["side_blue"] * np.where(a_blue, 1.0, -1.0)
                   + spec["rho"] * (cont[k, 0] - cont[k, 1]) + dlt)
            lg = series_lg - spec["elo_logit"] + det + rng.normal(0, map_sd, n)
            win = rng.random(n) < 1 / (1 + np.exp(-lg))
            game_win[g] += (live & win).sum()
            if track_drafts:
                li = np.flatnonzero(live)
                for t in (0, 1):
                    for r in range(5):
                        role_cnt[g, t, r] += np.bincount(picks[li, t, r], minlength=C)
                pdet = 1 / (1 + np.exp(-det))
                keys = np.concatenate([picks[:, 0, :], picks[:, 1, :], a_blue[:, None].astype(np.int32)], 1)
                for i in np.flatnonzero(live):
                    st = draft_stats[g][keys[i].tobytes()]
                    st[0] += 1; st[1] += pdet[i]
            aw += (live & win); bw += (live & ~win)
            prev_a_won = win
            if spec["fearless"]:
                for t in (0, 1):
                    locked[rows[:, None], picks[:, t, :]] |= live[:, None]
        for x, y in zip(aw.tolist(), bw.tolist()):
            scores[f"{x}-{y}"] += 1
        for m in (aw.astype(int) + bw.astype(int)).tolist():
            maps_n[m] += 1
    exact = {}
    for x in range(need + 1):
        for y in range(need + 1):
            if (x == need) ^ (y == need):
                exact[f"{x}-{y}"] = scores.get(f"{x}-{y}", 0) / sims
    lock = max(exact, key=exact.get)
    out = {"bo": bo, "sims": sims, "p_series_a": sum(v for kk, v in exact.items() if int(kk.split("-")[0]) == need),
           "exact": exact, "locked_score": lock, "locked_score_prob": exact[lock],
           "e_maps": sum(m * c for m, c in maps_n.items()) / sims,
           "maps_dist": {str(m): maps_n.get(m, 0) / sims for m in range(need, bo + 1)},
           "lineup_combo_share": (combo_count / combo_count.sum()).tolist()}
    p_map = []
    if track_drafts:
        tops = []
        for g in range(bo):
            tot = reach[g]
            if tot == 0:
                break
            items = sorted(draft_stats[g].items(), key=lambda kv: -kv[1][0])[:3]
            tops.append({"game": g + 1, "p_reached": tot / sims, "n_distinct": len(draft_stats[g]),
                         "top": [{"key": np.frombuffer(kb, dtype=np.int32).tolist(), "p": cnt / tot,
                                  "win_a": s / cnt} for kb, (cnt, s) in items]})
            p_map.append(sum(s for _, (c_, s) in draft_stats[g].items()) / tot)
        out["drafts"] = tops
        out["p_map_a_by_game"] = p_map
        out["role_marginals"] = [(role_cnt[g] / max(reach[g], 1)).tolist() for g in range(len(tops))]
    out["p_game_a"] = [float(game_win[g] / reach[g]) if reach[g] else None for g in range(bo)]
    out["p_game_reached"] = [float(reach[g] / sims) for g in range(bo)]
    return out
