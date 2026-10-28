"""Model v2 engine: patch-aware Elo + roster continuity + draft-path simulation.

Walk-forward discipline: `Engine.fit(cutoff)` only reads games/series that happened strictly before `cutoff`.
"""
import difflib, json, math
from collections import defaultdict
from datetime import date as Date, timedelta
import numpy as np
from . import config, golgg, ratings, ratings_v2, draftmodel as dm, sim_v2

LN10_400 = math.log(10) / 400.0
CONT_TAU = 8.0        # hand-set: continuity feature = exp(-maps_together / CONT_TAU)
DRAFT_FIT_GAMES = 1200


def cont_feat(n):
    return math.exp(-min(n, 60) / CONT_TAU)


def load_corpus():
    p = config.CACHE_DIR / "golgg" / "corpus.json"
    games = [g for g in json.loads(p.read_text()) if g and g.get("patch")]
    games.sort(key=lambda g: (g["date"], g["series_key"], g["game_no"]))
    return games


def fearless_by_tournament(games):
    """Data check of the ruleset: a tournament is Fearless if (almost) no champion is re-picked within a series."""
    ser = defaultdict(list)
    for g in games:
        ser[g["series_key"]].append(g)
    rep = defaultdict(lambda: [0, 0])
    for k, gs in ser.items():
        if len(gs) < 2:
            continue
        seen, viol = set(), 0
        for g in sorted(gs, key=lambda x: x["game_no"]):
            pk = {dm.ck(c) for c in g["blue_picks"] + g["red_picks"]}
            viol += len(pk & seen); seen |= pk
        rep[gs[0]["tournament"]][0] += 1; rep[gs[0]["tournament"]][1] += (viol > 0)
    # tolerance: a stray remake / data glitch should not flip the ruleset
    return {t: {"multi_game_series": n, "series_with_repeat_picks": v, "fearless": v <= 0.1 * n} for t, (n, v) in rep.items()}


def attach_series_context(games, fearless):
    ser = defaultdict(list)
    for g in games:
        ser[g["series_key"]].append(g)
    for k, gs in ser.items():
        gs.sort(key=lambda x: x["game_no"])
        locked = []
        for i, g in enumerate(gs):
            g["fearless"] = fearless.get(g["tournament"], {}).get("fearless", True)
            g["locked"] = list(locked) if g["fearless"] else []
            locked += g["blue_picks"] + g["red_picks"]
            g["prev_winner"] = None if i == 0 else (gs[i - 1]["blue"] if gs[i - 1]["blue_win"] else gs[i - 1]["red"])
            g["prev_loser"] = None if i == 0 else (gs[i - 1]["red"] if gs[i - 1]["blue_win"] else gs[i - 1]["blue"])
    return ser


def _sim(a, b):
    a, b = golgg.norm(a), golgg.norm(b)
    if not a or not b:
        return 0.0
    if a == b or (len(a) > 3 and a in b) or (len(b) > 3 and b in a):
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def join(series, rows):
    """corpus series_key -> (lolesports row, golgg_team->lolesports name). Uses date +/- 1 day, names, map count."""
    by_day = defaultdict(list)
    for r in rows:
        if r["state"] == "completed" and r["a_w"] is not None:
            by_day[r["start"][:10]].append(r)
    out, names = {}, {}
    for k, gs in series.items():
        g0 = gs[0]; d = Date.fromisoformat(g0["date"])
        ta, tb = g0["blue"], g0["red"]
        best, bs = None, 0.0
        for dd in (d, d - timedelta(days=1), d + timedelta(days=1)):
            for r in by_day.get(dd.isoformat(), []):
                if r["a_w"] + r["b_w"] != len(gs):
                    continue
                for x, y, flip in ((ta, tb, False), (tb, ta, True)):
                    s = min(max(_sim(x, r["a_name"]), _sim(x, r["a"])), max(_sim(y, r["b_name"]), _sim(y, r["b"])))
                    if s > bs:
                        best, bs = (r, flip), s
        if best and bs >= 0.6:
            r, flip = best
            out[k] = r
            names[ta] = r["b_name"] if flip else r["a_name"]
            names[tb] = r["a_name"] if flip else r["b_name"]
    return out, names


def lineup_of(g, side):
    return [p["pid"] for p in sorted((p for p in g["players"] if p["side"] == side), key=lambda p: dm.ROLES.index(p["role"]))]


class Engine:
    def __init__(self, games, rows, cal):
        self.games, self.rows, self.cal = games, rows, cal
        self.fearless = fearless_by_tournament(games)
        self.series = attach_series_context(games, self.fearless)
        self.join, self.names = join(self.series, rows)
        self.U = dm.Universe(extra=[c for g in games for c in g["blue_picks"] + g["red_picks"]])

    # ---------------------------------------------------------------- fitting (walk-forward)
    def fit(self, cutoff_date, cutoff_iso=None, variant=None):
        """variant 'v2' = frozen v2 behaviour (reproducible); 'v2.1' (default) = audit fixes: cross-region-only Elo
        tuning/K and no lineup-continuity term (see ratings_v2.VARIANTS and draftmodel.fit_outcome)."""
        variant = variant or ratings_v2.DEFAULT_VARIANT
        cutoff_iso = cutoff_iso or cutoff_date + "T00:00:00Z"
        (lam, ks), _ = ratings_v2.tune_lambda(self.rows, self.cal, cutoff_iso, variant=variant)
        kil = ratings_v2.VARIANTS[variant]["k_intl_leagues"]
        R, pre, _ = ratings_v2.elo_pass(self.rows, self.cal, lam, kscale=ks, k_intl_leagues=kil)
        # v2.2: per-home-league offsets from prequential cross-region residuals before the cutoff (results only)
        lo = (ratings_v2.fit_league_offsets(self.rows, pre, R.home, cutoff_iso)
              if ratings_v2.VARIANTS[variant].get("league_offsets") else {"offsets": {}, "note": f"not used in {variant}"})
        S = dm.Stats(self.U, self.cal)
        items, X, off, y, cont = [], [], [], [], []
        lb = [0, 0]
        for g in self.games:
            if g["date"] >= cutoff_date:
                break
            P = g["patch"]
            pb, pr = lineup_of(g, "blue"), lineup_of(g, "red")
            cb = dm.team_context(S, g["blue"], pb, pr, P); cr = dm.team_context(S, g["red"], pr, pb, P)
            locked = {self.U.i(c) for c in g["locked"]} - {None}
            items.append((g, cb, cr, locked))
            if len(items) > DRAFT_FIT_GAMES:
                items.pop(0)
            row = self.join.get(g["series_key"])
            if row:
                ra, rb, _ = pre[row["match_id"]]
                blue_is_a = self.names.get(g["blue"]) == row["a_name"]
                o = (ra - rb) * LN10_400 * (1 if blue_is_a else -1)
                cbl = [self.U.i(p["champ"]) for p in sorted((p for p in g["players"] if p["side"] == "blue"), key=lambda p: dm.ROLES.index(p["role"]))]
                crd = [self.U.i(p["champ"]) for p in sorted((p for p in g["players"] if p["side"] == "red"), key=lambda p: dm.ROLES.index(p["role"]))]
                if None not in cbl and None not in crd:
                    X.append(dm.score_features(cb, self.U, cbl) - dm.score_features(cr, self.U, crd))
                    off.append(o); y.append(1.0 if g["blue_win"] else 0.0)
                    cont.append(cont_feat(S.continuity(g["blue"], pb)) - cont_feat(S.continuity(g["red"], pr)))
            if g["prev_loser"]:
                lb[0] += 1; lb[1] += (g["blue"] == g["prev_loser"])
            S.add(g)
        draft = dm.fit_draft(items, self.U)
        outcome = dm.fit_outcome(np.array(X), np.array(off), np.array(y), np.array(cont), use_cont=(variant == "v2"))
        self.params = {"cutoff": cutoff_date, "variant": variant, "elo_lambda": lam, "elo_kscale": ks,
                       "elo_intl_k_leagues": sorted(kil), "league_offsets": lo, "draft": draft, "outcome": outcome,
                       "q_loser_blue": (lb[1] + 1) / (lb[0] + 2), "q_loser_blue_n": lb[0]}
        self.S, self.pre, self.R = S, pre, R
        self._cursor = sum(1 for g in self.games if g["date"] < cutoff_date)
        return self.params

    def rating(self, match_id, a_name, b_name):
        """Pre-series ratings (ra, rb, patch) incl. the v2.2 league offsets (0 for v2/v2.1)."""
        ra, rb, P = self.pre[match_id]
        o = self.params.get("league_offsets", {}).get("offsets", {})
        return ra + ratings_v2.offset(a_name, self.R.home, o), rb + ratings_v2.offset(b_name, self.R.home, o), P

    def advance(self, to_date):
        """Add games with date < to_date to the stats (pools/meta/continuity) without refitting parameters."""
        while self._cursor < len(self.games) and self.games[self._cursor]["date"] < to_date:
            self.S.add(self.games[self._cursor]); self._cursor += 1

    # ---------------------------------------------------------------- per-series spec
    def team_arrays(self, team, pids, opp_pids, P):
        ctx = dm.team_context(self.S, team, pids, opp_pids, P)
        w_p, w_b = np.array(self.params["draft"]["w_pick"]), np.array(self.params["draft"]["w_ban"])
        gam = np.array(self.params["outcome"]["gamma"])
        U = ctx["pick_f"] @ w_p
        V = ctx["ban_f"] @ w_b
        D = ctx["score"] @ gam[:4]
        return U, V, D

    def spec(self, elo_logit, bo, fearless, P, team_a, team_b, lineups_a, lineups_b, g1_a_blue):
        """lineups_*: list of (weight, [pid or None]*5). Team names are gol.gg names (for ban tendencies)."""
        combos = []
        for wa, la in lineups_a:
            for wb, lb in lineups_b:
                Ua, Va, Da = self.team_arrays(team_a, la, lb, P)
                Ub, Vb, Db = self.team_arrays(team_b, lb, la, P)
                combos.append({"w": wa * wb, "U": np.stack([Ua, Ub]), "V": np.stack([Va, Vb]), "D": np.stack([Da, Db]),
                               "cont": np.array([cont_feat(self.S.continuity(team_a, la)), cont_feat(self.S.continuity(team_b, lb))])})
        o = self.params["outcome"]
        return {"bo": bo, "fearless": fearless and bo > 1, "elo_logit": elo_logit, "side_blue": o["side_blue"],
                "rho": o["rho"], "q_loser_blue": self.params["q_loser_blue"], "g1_a_blue": g1_a_blue,
                "combos": combos, "comp_coef": o["gamma"][4], "U_ap": self.U.ap, "U_tank": self.U.tank}

    def last_lineup(self, team, before_date):
        for g in reversed(self.games):
            if g["date"] < before_date and team in (g["blue"], g["red"]):
                return lineup_of(g, "blue" if g["blue"] == team else "red"), g
        return None, None

    def golgg_name(self, ls_name, before_date=None):
        """lolesports team name -> gol.gg team name. gol.gg renames teams between seasons ('Hanwha Life eSports' 2025 vs
        'Hanwha Life Esports' 2026, 'Gen.G eSports' vs 'Gen.G', 'DRX' vs 'Kiwoom DRX', ...). Bug fixed in the 2026-10-28
        audit: the first-seen (oldest) gol.gg name used to win, so HLE was simulated with its 2025 five (Peanut/Viper) and
        2025 draft pools. Now the alias with the most recent game before `before_date` wins (walk-forward safe)."""
        cands = [gn for gn, ln in self.names.items() if ln == ls_name]
        if cands:
            last = {}
            for g in self.games:
                if before_date is None or g["date"] < before_date:
                    for t in (g["blue"], g["red"]):
                        if t in cands:
                            last[t] = g["date"]
            return max(cands, key=lambda gn: (last.get(gn, ""), gn))
        best = max(self.names, key=lambda gn: _sim(gn, ls_name), default=None)
        return best if best and _sim(best, ls_name) >= 0.6 else None


# ---------------------------------------------------------------- beam search: most likely drafts for one map
def _lsm(x):
    m = x.max()
    return x - (m + np.log(np.exp(x - m).sum()))


def beam_drafts(combo, a_blue, locked, comp_coef, U_ap, U_tank, base_logit, side_blue, rho,
                width=40, topk=sim_v2.TOPK, keep=3):
    """Most likely full ban/pick sequences under the same top-k-truncated draft model the simulator samples from.
    Returns [{"p": seq prob (merged over pick orderings), "picks_a": [5 idx by role], "picks_b": [...],
              "bans_a": [...], "bans_b": [...], "win_a": map win prob for this draft}]"""
    Uc, Vc, Dc = combo["U"], combo["V"], combo["D"]
    C = Uc.shape[-1]
    beams = [(0.0, frozenset(locked), (frozenset(), frozenset()), ((-1,) * 5, (-1,) * 5), ((), ()))]
    for side, kind in dm.ORDER:
        team = (0 if a_blue else 1) if side == "B" else (1 if a_blue else 0)
        nxt = []
        for lp, used, filled, picks, bans in beams:
            if kind == "ban":
                base = Vc[team].astype(float).copy()
                base[list(used)] = -np.inf
                idx = np.argsort(-base)[:topk]
                lq = _lsm(base[idx])
                for c, l in zip(idx, lq):
                    b = list(bans); b[team] = b[team] + (int(c),)
                    nxt.append((lp + l, used | {int(c)}, filled, picks, tuple(b)))
            else:
                base = Uc[team].astype(float).copy()
                if used:
                    base[:, list(used)] = -np.inf
                for r in filled[team]:
                    base[r, :] = -np.inf
                flat = base.ravel()
                idx = np.argsort(-flat)[:topk]
                lq = _lsm(flat[idx])
                for f, l in zip(idx, lq):
                    r, c = int(f // C), int(f % C)
                    pk = [list(picks[0]), list(picks[1])]; pk[team][r] = c
                    fl = [set(filled[0]), set(filled[1])]; fl[team].add(r)
                    nxt.append((lp + l, used | {c}, (frozenset(fl[0]), frozenset(fl[1])), (tuple(pk[0]), tuple(pk[1])), bans))
        nxt.sort(key=lambda x: -x[0])
        beams = nxt[:width]
    merged = {}
    for lp, used, filled, picks, bans in beams:
        key = picks
        if key not in merged:
            merged[key] = [0.0, bans]
        merged[key][0] += math.exp(lp)
    out = []
    for picks, (p, bans) in sorted(merged.items(), key=lambda kv: -kv[1][0])[:keep]:
        sc = []
        for t in (0, 1):
            pc = np.array(picks[t])
            sc.append(float(Dc[t][np.arange(5), pc].sum() + comp_coef * (-0.5 * abs(U_ap[pc].sum() - 2.0) - (U_tank[pc].sum() < 1))))
        dl = max(-dm.DRAFT_CAP, min(dm.DRAFT_CAP, sc[0] - sc[1]))
        lg = base_logit + side_blue * (1 if a_blue else -1) + rho * (combo["cont"][0] - combo["cont"][1]) + dl
        out.append({"p": p, "picks_a": list(picks[0]), "picks_b": list(picks[1]), "bans_a": list(bans[0]),
                    "bans_b": list(bans[1]), "draft_delta": dl, "win_a": 1 / (1 + math.exp(-lg))})
    return out
