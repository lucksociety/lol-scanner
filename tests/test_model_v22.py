"""Primary-model switch (2026-10-28): v2.1-ratings headline, v2.2 cross-region league offsets (backtested, not adopted)."""
import random
from scanner import ratings, ratings_v2, sim, sim_r, forecast_v2


def _xr_rows(n=200, p_lck=0.7, seed=3):
    rng = random.Random(seed); rows, pre = [], {}
    for i in range(n):
        mid = str(i)
        aw = sum(rng.random() < p_lck for _ in range(3)) ; bw = 3 - aw
        rows.append({"match_id": mid, "start": f"2026-05-{1 + i % 28:02d}T00:00:00Z", "state": "completed", "league": "Worlds",
                     "a_name": "L1", "b_name": "P1", "a_w": aw, "b_w": bw})
        pre[mid] = (1600.0, 1600.0, "16.1")
    return rows, pre, {"L1": "LCK", "P1": "LPL"}


def test_league_offsets_recover_direction_and_shrink():
    rows, pre, home = _xr_rows()
    o = ratings_v2.fit_league_offsets(rows, pre, home, "2027-01-01T00:00:00Z")["offsets"]
    assert o["LCK"] > 30 and o["LPL"] < -30 and abs(o["LCK"] + o["LPL"]) < 1e-6   # symmetric ridge, 2 leagues
    tight = ratings_v2.fit_league_offsets(rows, pre, home, "2027-01-01T00:00:00Z", sd=5.0)["offsets"]
    assert 0 < tight["LCK"] < o["LCK"]                                                   # prior shrinks toward 0


def test_league_offsets_walk_forward_and_same_league_ignored():
    rows, pre, home = _xr_rows()
    assert ratings_v2.fit_league_offsets(rows, pre, home, "2026-01-01T00:00:00Z")["offsets"] == {}   # nothing before cutoff
    assert ratings_v2.fit_league_offsets(rows, pre, {"L1": "LCK", "P1": "LCK"}, "2027-01-01T00:00:00Z")["offsets"] == {}


def test_ratings_sim_matches_backtested_simulator():
    for bo in (1, 3, 5):
        a, b = sim.simulate(0.62, bo, sims=20000, seed=1), sim_r.simulate(0.62, bo, sims=20000, seed=1)
        assert a["p_series_a"] == b["p_series_a"] and a["exact"] == b["exact"] and a["locked_score"] == b["locked_score"]
        assert b["p_game_reached"][0] == 1.0 and 0.55 < b["p_game_a"][0] < 0.69


def test_primary_is_ratings_only_v21():
    assert forecast_v2.PRIMARY == "v2.1-ratings" and forecast_v2._HEADLINE[forecast_v2.PRIMARY] == "ratings"
    assert forecast_v2.MODEL_VERSION.startswith("v2")          # still written to <date>.v2.json (new dates only)
    assert not ratings_v2.VARIANTS[forecast_v2._RATINGS_VARIANT[forecast_v2.PRIMARY]].get("league_offsets")
