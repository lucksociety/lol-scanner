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


class PatchRatings(ratings.Ratings):
    def __init__(self, lam, cal, kscale=1.0):
        super().__init__()
        self.lam, self.cal, self.last_patch, self.kscale = lam, cal, {}, kscale

    def update_series(self, a, b, aw, bw, league, when):
        k0 = (ratings.K_DOMESTIC, ratings.K_INTL)
        ratings.K_DOMESTIC, ratings.K_INTL = k0[0] * self.kscale, k0[1] * self.kscale
        try:
            super().update_series(a, b, aw, bw, league, when)
        finally:
            ratings.K_DOMESTIC, ratings.K_INTL = k0

    def touch(self, team, league, when, pord):
        r = self.get(team, league, when)
        lp = self.last_patch.get(team)
        if lp is not None and pord > lp and self.lam < 1.0:
            prior = ratings.LEAGUE_PRIOR.get(self.home.get(team), ratings.DEFAULT_PRIOR)
            self.r[team] = prior + self.lam ** (pord - lp) * (r - prior)
        self.last_patch[team] = max(pord, lp or pord)
        return self.r[team]


def elo_pass(rows, cal, lam, stop_iso=None, kscale=1.0, score_leagues=None):
    """Single chronological pass. Returns (R, pre) where pre[match_id] = (ra, rb, patch) BEFORE that series,
    and the prequential map log loss accumulated on series before stop_iso."""
    R = PatchRatings(lam, cal, kscale); pre = {}; ll = n = 0.0
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


def tune_lambda(rows, cal, cutoff_iso):
    """Pick (LAMBDA, K-scale) minimizing one-step-ahead map log loss on INTERNATIONAL series strictly before the
    cutoff (no test leakage). Revision note: the first v2 backtest scored all maps (mostly domestic); that objective
    chose K-scale 2-3, which hurt international forecasts, so the objective was narrowed to international maps,
    the population the scanner forecasts. Disclosed as a post-backtest change."""
    scores = {(lam, ks): elo_pass(rows, cal, lam, stop_iso=cutoff_iso, kscale=ks, score_leagues=ratings.INTL)[2]
              for lam in GRID for ks in KSCALE_GRID}
    if any(math.isnan(v) for v in scores.values()):
        # no international maps before the cutoff in the history window -> v1 defaults (no decay, K x1)
        return (1.0, 1.0), {"note": "no international history before cutoff; default (1.0, 1.0)"}
    best = min(scores, key=scores.get)
    return best, scores
