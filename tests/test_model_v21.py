"""Model v2.1 audit fixes (2026-10-28): cross-region-only international K / tuning, and no continuity term."""
from datetime import datetime, timezone
import numpy as np
from scanner import ratings, ratings_v2, draftmodel as dm


class _Cal:
    def on(self, d): return "16.20"
    def ord(self, p): return 0


def _rows(league):
    t = "2026-07-21T08:00:00Z"
    return [{"match_id": "1", "start": t, "state": "completed", "league": "LCK", "a_name": "A", "b_name": "B", "a_w": 0, "b_w": 0},
            {"match_id": "2", "start": t, "state": "completed", "league": league, "a_name": "A", "b_name": "B", "a_w": 1, "b_w": 0}]


def test_kespa_cup_is_domestic_k_in_v21_but_intl_in_v2():
    for variant, k in (("v2", ratings.K_INTL), ("v2.1", ratings.K_DOMESTIC)):
        kil = ratings_v2.VARIANTS[variant]["k_intl_leagues"]
        R, _, _ = ratings_v2.elo_pass(_rows("KeSPA Cup"), _Cal(), 1.0, kscale=1.0, k_intl_leagues=kil)
        assert abs((R.r["A"] - 1650) - k * 0.5) < 1e-9


def test_worlds_keeps_intl_k_in_v21():
    kil = ratings_v2.VARIANTS["v2.1"]["k_intl_leagues"]
    R, _, _ = ratings_v2.elo_pass(_rows("Worlds"), _Cal(), 1.0, kscale=1.0, k_intl_leagues=kil)
    assert abs((R.r["A"] - 1650) - ratings.K_INTL * 0.5) < 1e-9


def test_v21_scores_only_cross_region_events():
    assert ratings_v2.VARIANTS["v2.1"]["score_leagues"] == {"Worlds", "MSI", "First Stand", "Esports World Cup"}
    assert {"KeSPA Cup", "Asian Games"} <= ratings_v2.VARIANTS["v2"]["score_leagues"]
    assert min(ratings_v2.VARIANTS["v2.1"]["kgrid"]) < min(ratings_v2.KSCALE_GRID)   # v2 optimum sat on the grid edge


def test_patchratings_matches_v1_update_when_unscaled():
    a = ratings.Ratings(); b = ratings_v2.PatchRatings(1.0, _Cal(), 1.0)
    when = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for R in (a, b):
        R.update_series("X", "Y", 2, 1, "LCK", when); R.update_series("X", "Z", 0, 2, "Worlds", when)
    assert all(abs(a.r[t] - b.r[t]) < 1e-9 for t in a.r)


def test_fit_outcome_without_continuity_has_rho_zero():
    rng = np.random.default_rng(0); n = 400
    X = rng.normal(size=(n, 5)); off = rng.normal(size=n); cont = rng.normal(size=n)
    y = (rng.random(n) < 1 / (1 + np.exp(-(off - 1.0 * cont)))).astype(float)
    with_c = dm.fit_outcome(X, off, y, cont, use_cont=True)
    no_c = dm.fit_outcome(X, off, y, cont, use_cont=False)
    assert with_c["rho"] < -0.3 and no_c["rho"] == 0.0 and len(no_c["gamma"]) == 5 and not no_c["use_cont"]
