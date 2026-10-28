"""Model v2 team ratings: v1 map-level Elo + patch-recency decay.

Decay: when a team plays its first series on a newer patch, its rating is pulled toward its league prior:
    r <- prior + LAMBDA ** (patches elapsed) * (r - prior)
so a result from d patches ago keeps roughly LAMBDA**d of its influence; same-patch results count fully.
LAMBDA is TUNED (not hand-set) by one-step-ahead log loss on all maps before each test event (walk-forward).
"""
import math
from datetime import datetime
from . import ratings

GRID = (1.0, 0.98, 0.96, 0.94, 0.92, 0.90, 0.85, 0.80)
KSCALE_GRID = (0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)   # recency through the update size (higher = older results fade faster)

# ---- v2.1 (2026-10-28 model audit) -------------------------------------------------------------------------------
# Bug found: ratings.INTL includes KeSPA Cup (an LCK-only domestic cup) and Asian Games (national teams). Under v2 those
# maps (a) were scored by the lambda/K tuning objective and (b) got the international K (30). 53 of the 204 "international"
# series before Worlds 2026 were such games; they drove the Worlds 2026 fit to the grid corner (lambda 0.80, K x0.8), which
# decays every team back to its hand-set league prior each patch (T1 1658 ~ LCK prior 1650). Prequential calibration
# slope on cross-region maps under that fit: 1.52 +/- 0.22 (underconfident). v2.1 scores and up-weights only true
# cross-region events and widens the K grid (the v2 optimum sat on its lower edge).
CROSS_REGION = frozenset({"Worlds", "MSI", "First Stand", "Esports World Cup"})
KSCALE_GRID_V21 = (0.5, 0.6, 0.7, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
VARIANTS = {
    "v2": {"score_leagues": frozenset(ratings.INTL), "k_intl_leagues": frozenset(ratings.INTL), "kgrid": KSCALE_GRID},
    "v2.1": {"score_leagues": CROSS_REGION, "k_intl_leagues": CROSS_REGION, "kgrid": KSCALE_GRID_V21},
    # v2.2 = v2.1 ratings + per-home-league offsets learned from cross-region results (see fit_league_offsets)
    "v2.2": {"score_leagues": CROSS_REGION, "k_intl_leagues": CROSS_REGION, "kgrid": KSCALE_GRID_V21, "league_offsets": True},
}
DEFAULT_VARIANT = "v2.1"


class PatchRatings(ratings.Ratings):
    def __init__(self, lam, cal, kscale=1.0, k_intl_leagues=None):
        super().__init__()
        self.lam, self.cal, self.last_patch, self.kscale = lam, cal, {}, kscale
        self.k_intl_leagues = ratings.INTL if k_intl_leagues is None else k_intl_leagues

    def update_series(self, a, b, aw, bw, league, when):
        """Same as ratings.Ratings.update_series, with K scaled by kscale and the international K applied only to
        `k_intl_leagues` (v2: ratings.INTL, v2.1: cross-region events only)."""
        self.get(a, league, when); self.get(b, league, when)
        k = (ratings.K_INTL if league in self.k_intl_leagues else ratings.K_DOMESTIC) * self.kscale
        seq = []
        for i in range(max(aw, bw)):
            if i < aw: seq.append(1)
            if i < bw: seq.append(0)
        for s in seq:
            d = k * (s - ratings.p_map(self.r[a], self.r[b]))
            self.r[a] += d; self.r[b] -= d
        self.last[a] = self.last[b] = when
        self.n[a] += aw + bw; self.n[b] += aw + bw

    def touch(self, team, league, when, pord):
        r = self.get(team, league, when)
        lp = self.last_patch.get(team)
        if lp is not None and pord > lp and self.lam < 1.0:
            prior = ratings.LEAGUE_PRIOR.get(self.home.get(team), ratings.DEFAULT_PRIOR)
            self.r[team] = prior + self.lam ** (pord - lp) * (r - prior)
        self.last_patch[team] = max(pord, lp or pord)
        return self.r[team]


def elo_pass(rows, cal, lam, stop_iso=None, kscale=1.0, score_leagues=None, k_intl_leagues=None):
    """Single chronological pass. Returns (R, pre) where pre[match_id] = (ra, rb, patch) BEFORE that series,
    and the prequential map log loss accumulated on series before stop_iso."""
    R = PatchRatings(lam, cal, kscale, k_intl_leagues); pre = {}; ll = n = 0.0
    for row in rows:
        when = datetime.fromisoformat(row["start"].replace("Z", "+00:00"))
        patch = cal.on(row["start"][:10]); pord = cal.ord(patch)
        a, b = row["a_name"], row["b_name"]
        ra, rb = R.touch(a, row["league"], when, pord), R.touch(b, row["league"], when, pord)
        pre[row["match_id"]] = (ra, rb, patch)
        if row["state"] != "completed" or row["a_w"] is None or (row["a_w"] + row["b_w"]) == 0:
            continue
        if stop_iso and row["start"] >= stop_iso:
            continue  # do not learn from (or score) anything at/after the cutoff
        if score_leagues is None or row["league"] in score_leagues:
            p = min(max(ratings.p_map(ra, rb), 1e-4), 1 - 1e-4)
            ll += -(row["a_w"] * math.log(p) + row["b_w"] * math.log(1 - p)); n += row["a_w"] + row["b_w"]
        R.update_series(a, b, row["a_w"], row["b_w"], row["league"], when)
    return R, pre, (ll / n if n else float("nan"))


def tune_lambda(rows, cal, cutoff_iso, variant=DEFAULT_VARIANT):
    """Pick (LAMBDA, K-scale) minimizing one-step-ahead map log loss on INTERNATIONAL series strictly before the
    cutoff (no test leakage). Revision note: the first v2 backtest scored all maps (mostly domestic); that objective
    chose K-scale 2-3, which hurt international forecasts, so the objective was narrowed to international maps,
    the population the scanner forecasts. Disclosed as a post-backtest change."""
    V = VARIANTS[variant]
    scores = {(lam, ks): elo_pass(rows, cal, lam, stop_iso=cutoff_iso, kscale=ks, score_leagues=V["score_leagues"],
                                  k_intl_leagues=V["k_intl_leagues"])[2]
              for lam in GRID for ks in V["kgrid"]}
    if any(math.isnan(v) for v in scores.values()):
        # no international maps before the cutoff in the history window -> v1 defaults (no decay, K x1)
        return (1.0, 1.0), {"note": "no international history before cutoff; default (1.0, 1.0)"}
    best = min(scores, key=scores.get)
    return best, scores


# ---- v2.2: results-only cross-region strength adjustment ------------------------------------------------------------
# Audit finding: on cross-region maps the Elo under-rated LCK (LCK vs LPL 63.5% actual vs 49.9% expected). Elo only moves
# league strength through the few international series, and patch decay pulls teams back toward hand-set league priors.
# v2.2 adds a per-home-league offset (Elo points) fitted on PREQUENTIAL residuals of cross-region maps strictly before the
# cutoff: P(A wins map) = p_map(ra + o[home A], rb + o[home B]), with each pre-series rating built only from earlier
# results. Ridge prior o ~ N(0, LEAGUE_OFFSET_SD^2) shrinks thin leagues to 0. No market input.
LEAGUE_OFFSET_SD = 50.0     # hand-set before the backtest (prior SD of a league offset, Elo points); sensitivity reported


def fit_league_offsets(rows, pre, home, cutoff_iso, sd=LEAGUE_OFFSET_SD, leagues=CROSS_REGION, iters=50):
    """MAP estimate of per-league offsets from cross-region series before cutoff_iso.
    pre[match_id] = (ra, rb, patch) pre-series ratings; home: team -> home league. Same-league pairs carry no info."""
    data = []
    for r in rows:
        if (r["league"] not in leagues or r["state"] != "completed" or r["a_w"] is None or r["a_w"] + r["b_w"] == 0
                or r["start"] >= cutoff_iso or r["match_id"] not in pre):
            continue
        la, lb = home.get(r["a_name"]), home.get(r["b_name"])
        if not la or not lb or la == lb:
            continue
        ra, rb, _ = pre[r["match_id"]]
        data.append((la, lb, ra - rb, r["a_w"], r["b_w"]))
    L = sorted({x for d in data for x in d[:2]})
    if not data:
        return {"offsets": {}, "n_series": 0, "n_maps": 0, "sd": sd}
    ix = {l: i for i, l in enumerate(L)}
    c = math.log(10) / ratings.SCALE
    o = [0.0] * len(L)
    for _ in range(iters):
        g = [-oi / sd ** 2 for oi in o]
        H = [[(1.0 / sd ** 2 if i == j else 0.0) for j in range(len(L))] for i in range(len(L))]
        for la, lb, dr, aw, bw in data:
            i, j = ix[la], ix[lb]
            p = 1 / (1 + math.exp(-c * (dr + o[i] - o[j])))
            gr = c * (aw - (aw + bw) * p); h = c * c * (aw + bw) * p * (1 - p)
            g[i] += gr; g[j] -= gr
            H[i][i] += h; H[j][j] += h; H[i][j] -= h; H[j][i] -= h
        step = _solve(H, g)
        o = [oi + si for oi, si in zip(o, step)]
        if max(abs(si) for si in step) < 1e-6:
            break
    n_l = {l: 0 for l in L}
    for la, lb, _, aw, bw in data:
        n_l[la] += aw + bw; n_l[lb] += aw + bw
    return {"offsets": {l: round(o[ix[l]], 2) for l in L}, "maps_by_league": n_l, "n_series": len(data),
            "n_maps": sum(d[3] + d[4] for d in data), "sd": sd}


def _solve(A, b):
    n = len(b); M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for k in range(n):
        piv = max(range(k, n), key=lambda r: abs(M[r][k])); M[k], M[piv] = M[piv], M[k]
        for r in range(k + 1, n):
            f = M[r][k] / M[k][k]
            for cc in range(k, n + 1):
                M[r][cc] -= f * M[k][cc]
    x = [0.0] * n
    for k in range(n - 1, -1, -1):
        x[k] = (M[k][n] - sum(M[k][j] * x[j] for j in range(k + 1, n))) / M[k][k]
    return x


def offset(team, home, offsets):
    return (offsets or {}).get(home.get(team), 0.0)
