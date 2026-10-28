"""Model v2 draft layer: champion pools, patch meta, a probabilistic ban/pick model and a draft-strength score.

Everything here is built from game data (gol.gg drafts + lineups, Data Dragon tags). No market inputs.

* Player pools: per player and champion, games / wins / KDA on the current patch (weight 1.0) and the player's
  most recent earlier patch (weight PREV_W).
* Meta: per role and champion, pick share, presence (picks+bans per game) and win rate shrunk toward 50%.
* Draft model (FITTED by maximum likelihood on historical drafts before the cutoff): a sequential
  conditional-logit model over the real tournament draft order (B1 R1 B2 R2 B3 R3 | B R R B B R | R4 B4 R5 B5 |
  R B B R). Pick options are (champion, unfilled role); ban options are champions. Features:
    picks: log P(player in that role plays champ) [Dirichlet-smoothed pool with meta prior], log presence,
           shrunk role win-rate deviation
    bans:  log threat (max over opponent players of the same usage probability), log presence,
           log team ban tendency (smoothed)
  Fearless: champions picked earlier in the series are unavailable to both teams (Bo3/Bo5 only).
* Draft score (FITTED by ridge logistic regression on map results with the Elo logit as offset): per team,
  sum over roles of shrunk player-on-champion win-rate deviation, comfort (log games), KDA proficiency,
  meta win-rate deviation, plus a small composition-fit term (AP/AD balance, has a tank). The resulting logit
  shift is clipped to +/- DRAFT_CAP.
"""
import math
from collections import defaultdict
import numpy as np
from scipy.optimize import minimize
from . import champs as chmeta
from .champs import ck

ROLES = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]
ORDER = [("B", "ban"), ("R", "ban"), ("B", "ban"), ("R", "ban"), ("B", "ban"), ("R", "ban"),
         ("B", "pick"), ("R", "pick"), ("R", "pick"), ("B", "pick"), ("B", "pick"), ("R", "pick"),
         ("R", "ban"), ("B", "ban"), ("R", "ban"), ("B", "ban"),
         ("R", "pick"), ("B", "pick"), ("B", "pick"), ("R", "pick")]
PREV_W = 0.6          # hand-set: weight of the prior patch in pools/meta (current patch = 1.0)
POOL_ALPHA = 5.0      # hand-set: Dirichlet strength of the meta prior inside a player's pool
META_K = 20.0         # hand-set: meta win-rate shrink (pseudo-games at 50%)
PROF_K = 6.0          # hand-set: player-on-champion win-rate shrink (pseudo-games at meta rate)
KDA_K = 4.0           # hand-set: KDA proficiency shrink
TEAMBAN_BETA = 6.0    # hand-set: team ban tendency smoothing
DRAFT_CAP = 0.45      # hand-set: max |logit shift| from the draft score
RIDGE = 8.0           # hand-set: L2 penalty for the draft-score regression


class Universe:
    def __init__(self, extra=()):
        meta = chmeta.load()
        keys = sorted(set(meta) | {ck(x) for x in extra})
        self.keys = keys
        self.idx = {k: i for i, k in enumerate(keys)}
        self.name = [meta[k]["name"] if k in meta else k for k in keys]
        self.ap = np.array([meta.get(k, {}).get("ap", False) for k in keys], dtype=float)
        self.tank = np.array([meta.get(k, {}).get("tank", False) for k in keys], dtype=float)
        self.C = len(keys)

    def i(self, name):
        return self.idx.get(ck(name))


def _kda(p):
    if p.get("k") is None:
        return None
    return math.log((p["k"] + p["a"] + 1.0) / (p["d"] + 1.0))


class Stats:
    """Incremental counts by patch. add(game) after a game is final; snapshot(...) reads only what was added."""

    def __init__(self, U, cal):
        self.U, self.cal = U, cal
        self.pc = defaultdict(lambda: defaultdict(lambda: [0, 0, 0.0]))   # (pid) -> (patch, c) -> [n, w, sumkda]
        self.pk = defaultdict(lambda: defaultdict(float))                   # pid -> patch -> sum kda (for mean)
        self.pn = defaultdict(lambda: defaultdict(int))                     # pid -> patch -> n
        self.rc = defaultdict(lambda: np.zeros((5, U.C, 2)))                # patch -> role x champ x [picks, wins]
        self.bans = defaultdict(lambda: np.zeros(U.C))                       # patch -> bans per champ
        self.games = defaultdict(int)                                        # patch -> games
        self.tb = defaultdict(lambda: defaultdict(lambda: np.zeros(U.C)))   # team -> patch -> bans
        self.tg = defaultdict(lambda: defaultdict(int))                      # team -> patch -> games
        self.lineups = defaultdict(int)                                      # (team, pids) -> maps together
        self.external_meta = None

    def add(self, g):
        P = g["patch"]; self.games[P] += 1
        for side in ("blue", "red"):
            team = g[side]; win = g["blue_win"] == (side == "blue")
            for c in g[f"{side}_bans"]:
                ci = self.U.i(c)
                if ci is not None:
                    self.bans[P][ci] += 1; self.tb[team][P][ci] += 1
            self.tg[team][P] += 1
            pl = [p for p in g["players"] if p["side"] == side]
            self.lineups[(team, tuple(p["pid"] for p in pl))] += 1
            for p in pl:
                ci, ri = self.U.i(p["champ"]), ROLES.index(p["role"])
                if ci is None:
                    continue
                self.rc[P][ri, ci, 0] += 1; self.rc[P][ri, ci, 1] += win
                kd = _kda(p) or 0.0
                rec = self.pc[p["pid"]][(P, ci)]; rec[0] += 1; rec[1] += win; rec[2] += kd
                self.pk[p["pid"]][P] += kd; self.pn[p["pid"]][P] += 1

    # ---------- snapshots ----------
    def meta(self, P):
        """Returns pick share by role (5,C), role win-rate dev (5,C) shrunk, presence (C,)."""
        if self.external_meta is not None and self.external_meta.get("patch") == P:
            return self.external_meta["share"], self.external_meta["wrdev"], self.external_meta["presence"]
        prev = self.prev_patch(P)
        rc = self.rc[P].copy(); bans = self.bans[P].copy(); G = float(self.games[P])
        if prev:
            rc += PREV_W * self.rc[prev]; bans = bans + PREV_W * self.bans[prev]; G += PREV_W * self.games[prev]
        return meta_arrays(rc, bans, G)

    def prev_patch(self, P, pid=None):
        po = self.cal.ord(P)
        cands = (self.pn[pid].keys() if pid is not None else self.games.keys())
        older = [q for q in cands if self.cal.ord(q) < po]
        return max(older, key=self.cal.ord) if older else None

    def pool(self, pid, P):
        """(n, w, kda_dev) arrays over champions for one player, patch-weighted."""
        C = self.U.C; n = np.zeros(C); w = np.zeros(C); kd = np.zeros(C)
        prev = self.prev_patch(P, pid)
        tot_n = tot_k = 0.0
        for (pp, ci), (cn, cw, ck_) in self.pc.get(pid, {}).items():
            wt = 1.0 if pp == P else (PREV_W if pp == prev else 0.0)
            if wt:
                n[ci] += wt * cn; w[ci] += wt * cw; kd[ci] += wt * ck_
                tot_n += wt * cn; tot_k += wt * ck_
        mean_k = tot_k / tot_n if tot_n else 0.0
        kdev = np.where(n > 0, kd / np.maximum(n, 1e-9) - mean_k, 0.0)
        return n, w, kdev

    def team_bans(self, team, P):
        prev = self.prev_patch(P)
        b = self.tb[team][P].copy(); g = float(self.tg[team][P])
        if prev:
            b += PREV_W * self.tb[team][prev]; g += PREV_W * self.tg[team][prev]
        return b, g

    def continuity(self, team, pids):
        return self.lineups.get((team, tuple(pids)), 0)


def meta_arrays(rc, bans, G):
    C = rc.shape[1]
    picks = rc[:, :, 0]; wins = rc[:, :, 1]
    share = (picks + 0.05) / (picks.sum(1, keepdims=True) + 0.05 * C)
    wrdev = (wins + META_K * 0.5) / (picks + META_K) - 0.5
    presence = (picks.sum(0) + bans + 0.2) / (max(G, 1.0) + 0.2 * 10)
    presence = np.clip(presence, 1e-3, 1.0)
    return share, wrdev, presence


def team_context(S, team, pids, opp_pids, P):
    """Feature tensors for one team in one game.
    pick_f (5,C,3), ban_f (C,3), score parts (5,C,4) = [prof, comfort, kda, metawr]."""
    share, wrdev, presence = S.meta(P)
    C = S.U.C
    usage = np.zeros((5, C)); prof = np.zeros((5, C)); comf = np.zeros((5, C)); kda = np.zeros((5, C))
    for r, pid in enumerate(pids):
        if pid is None:
            n = np.zeros(C); w = np.zeros(C); kd = np.zeros(C)
        else:
            n, w, kd = S.pool(pid, P)
        usage[r] = (n + POOL_ALPHA * share[r]) / (n.sum() + POOL_ALPHA)
        prof[r] = (w + PROF_K * (0.5 + wrdev[r])) / (n + PROF_K) - 0.5 - wrdev[r]
        comf[r] = np.log1p(n)
        kda[r] = n * kd / (n + KDA_K)
    opp_usage = np.zeros((5, C))
    for r, pid in enumerate(opp_pids):
        n, _, _ = S.pool(pid, P) if pid is not None else (np.zeros(C), None, None)
        opp_usage[r] = (n + POOL_ALPHA * share[r]) / (n.sum() + POOL_ALPHA)
    tb, tg = S.team_bans(team, P)
    banrate = presence * 0.5
    tend = (tb + TEAMBAN_BETA * banrate) / (tg + TEAMBAN_BETA)
    pick_f = np.stack([np.log(usage), np.broadcast_to(np.log(presence), (5, C)), wrdev * 10.0], -1)
    ban_f = np.stack([np.log(opp_usage.max(0)), np.log(presence), np.log(np.clip(tend, 1e-4, None))], -1)
    score = np.stack([prof, comf, kda, wrdev], -1)
    return {"pick_f": pick_f.astype(np.float32), "ban_f": ban_f.astype(np.float32), "score": score.astype(np.float32)}


def comp_fit(U, champ_idx):
    """Composition fit for a 5-champion team: AP/AD balance and frontline. Range about [-1.5, 0]."""
    ap = U.ap[champ_idx].sum(-1); tank = U.tank[champ_idx].sum(-1)
    return -0.5 * np.abs(ap - 2.0) - (tank < 1).astype(float)


# ------------------------------------------------------------------ draft likelihood (fit)
def draft_steps(g, U, fearless_locked):
    """Sequence of (team 'B'/'R', kind, champ idx, role idx) reconstructed from gol.gg per-team ordered lists."""
    role_of = {(p["side"], U.i(p["champ"])): ROLES.index(p["role"]) for p in g["players"]}
    q = {("B", "ban"): list(g["blue_bans"]), ("R", "ban"): list(g["red_bans"]),
         ("B", "pick"): list(g["blue_picks"]), ("R", "pick"): list(g["red_picks"])}
    steps = []
    for t, kind in ORDER:
        if not q[(t, kind)]:
            steps.append((t, kind, None, None)); continue
        c = U.i(q[(t, kind)].pop(0))
        r = role_of.get(("blue" if t == "B" else "red", c)) if kind == "pick" else None
        steps.append((t, kind, c, r))
    return steps


def build_fit_rows(items, U):
    """items: list of (game, ctx_blue, ctx_red, fearless_locked set). Returns stacked arrays for the logit fit."""
    PF, PM, PY, BF, BM, BY = [], [], [], [], [], []
    C = U.C
    for g, cb, cr, locked in items:
        used = np.zeros(C, bool)
        for ci in locked:
            used[ci] = True
        filled = {"B": np.zeros(5, bool), "R": np.zeros(5, bool)}
        for t, kind, c, r in draft_steps(g, U, locked):
            ctx = cb if t == "B" else cr
            if c is None:
                continue
            if kind == "ban":
                mask = ~used
                if mask[c]:
                    BF.append(ctx["ban_f"]); BM.append(mask.copy()); BY.append(c)
                used[c] = True
            else:
                if r is None or filled[t][r]:
                    used[c] = True; continue
                mask = (~used)[None, :] & (~filled[t])[:, None]
                if mask[r, c]:
                    PF.append(ctx["pick_f"].reshape(5 * C, 3)); PM.append(mask.reshape(-1)); PY.append(r * C + c)
                used[c] = True; filled[t][r] = True
    return (np.array(PF), np.array(PM), np.array(PY)), (np.array(BF), np.array(BM), np.array(BY))


def _fit_clogit(F, M, Y, l2=0.01):
    k = F.shape[-1]

    def f(w):
        z = F @ w
        z = np.where(M, z, -1e9)
        zmax = z.max(1, keepdims=True)
        e = np.exp(z - zmax); s = e.sum(1)
        lse = np.log(s) + zmax[:, 0]
        ll = z[np.arange(len(Y)), Y] - lse
        p = e / s[:, None]
        grad = F[np.arange(len(Y)), Y] - np.einsum("nc,nck->nk", p, F)
        return -(ll.sum()) + l2 * (w @ w), -(grad.sum(0)) + 2 * l2 * w

    r = minimize(f, np.ones(k) * 0.5, jac=True, method="L-BFGS-B")
    n = len(Y)
    return r.x, float(r.fun / n), n


def fit_draft(items, U):
    (PF, PM, PY), (BF, BM, BY) = build_fit_rows(items, U)
    wp, nllp, n_p = _fit_clogit(PF, PM, PY)
    wb, nllb, n_b = _fit_clogit(BF, BM, BY)
    return {"w_pick": wp.tolist(), "w_ban": wb.tolist(), "nll_pick": nllp, "nll_ban": nllb,
            "n_pick_steps": n_p, "n_ban_steps": n_b,
            "uniform_nll_pick": float(np.mean(np.log(PM.sum(1)))), "uniform_nll_ban": float(np.mean(np.log(BM.sum(1))))}


# ------------------------------------------------------------------ outcome regression (draft score + roster + side)
def score_features(ctx, U, champ_by_role):
    idx = np.array(champ_by_role)
    parts = ctx["score"][np.arange(5), idx]          # (5,4)
    return np.concatenate([parts.sum(0), [comp_fit(U, idx[None, :])[0]]])   # 5 features


def fit_outcome(X, offset, y, cont, use_cont=True):
    """logit P(blue win) = offset + b0 + gamma . (x_blue - x_red) + rho * cont_diff ; ridge on gamma/rho.

    use_cont=False (model v2.1) drops the lineup-continuity term (rho = 0). Audit 2026-10-28: the corpus only holds each
    team's last 12 tier-1 series before an international event, so 'few maps together' in training mostly marks the
    non-qualifying opponents that appear in a qualifier's window, i.e. it encodes who qualified later (selection on
    the outcome). It fit rho ~ -1.0 to -1.4 in-sample, then moved Worlds 2026 maps by up to ~1 logit for a single
    roster change (TES with 369 in for ZUIAN: +0.95 logit to BLG) and was the largest source of v2's Worlds 2026 error."""
    if not use_cont:
        cont = np.zeros(0)
    Z = np.column_stack([np.ones(len(y)), X] + ([cont] if use_cont else []))

    def f(w):
        z = offset + Z @ w
        p = 1 / (1 + np.exp(-z))
        ll = y * np.log(p + 1e-12) + (1 - y) * np.log(1 - p + 1e-12)
        pen = RIDGE * (w[1:] @ w[1:]) / len(y) * len(y) / 100.0
        g = Z.T @ (p - y) + np.concatenate([[0], 2 * RIDGE * w[1:] / 100.0])
        return -ll.sum() + pen, g

    r = minimize(f, np.zeros(Z.shape[1]), jac=True, method="L-BFGS-B")
    w = r.x
    base = offset
    p0 = 1 / (1 + np.exp(-base))
    ll0 = -np.mean(y * np.log(p0 + 1e-12) + (1 - y) * np.log(1 - p0 + 1e-12))
    p1 = 1 / (1 + np.exp(-(offset + Z @ w)))
    ll1 = -np.mean(y * np.log(p1 + 1e-12) + (1 - y) * np.log(1 - p1 + 1e-12))
    gamma = w[1:-1] if use_cont else w[1:]
    return {"side_blue": float(w[0]), "gamma": gamma.tolist(), "rho": float(w[-1]) if use_cont else 0.0, "use_cont": bool(use_cont),
            "train_logloss_elo_only": float(ll0), "train_logloss_with_draft": float(ll1), "n_maps": int(len(y))}


def eval_draft(items, U, w_pick, w_ban):
    """Held-out draft-model quality: mean NLL per step, uniform-baseline NLL, top-1 / top-5 accuracy."""
    (PF, PM, PY), (BF, BM, BY) = build_fit_rows(items, U)
    out = {}
    for name, F, M, Y, w in (("pick", PF, PM, PY, w_pick), ("ban", BF, BM, BY, w_ban)):
        if len(Y) == 0:
            continue
        z = np.where(M, F @ np.array(w), -1e9)
        zmax = z.max(1, keepdims=True); lse = np.log(np.exp(z - zmax).sum(1)) + zmax[:, 0]
        nll = float(np.mean(lse - z[np.arange(len(Y)), Y]))
        rank = (z > z[np.arange(len(Y)), Y][:, None]).sum(1)
        out[name] = {"steps": int(len(Y)), "nll": round(nll, 3), "uniform_nll": round(float(np.mean(np.log(M.sum(1)))), 3),
                     "top1": round(float(np.mean(rank == 0)), 3), "top5": round(float(np.mean(rank < 5)), 3)}
    return out
