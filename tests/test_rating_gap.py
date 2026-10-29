from datetime import datetime, timedelta, timezone
import pytest
from scanner.ratings import Ratings, LEAGUE_PRIOR, OFFSEASON_SHRINK
from scanner.ratings_v2 import PatchRatings

@pytest.mark.parametrize("factory", [Ratings, lambda: PatchRatings(1.0, None)])
def test_long_break_shrinks_once_per_result_gap(factory):
    ratings = factory()
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ratings.update_series("A", "B", 3, 0, "LCK", t)
    old = ratings.r["A"]
    later = t + timedelta(days=60)
    expected = old + OFFSEASON_SHRINK * (LEAGUE_PRIOR["LCK"] - old)
    assert ratings.get("A", "LCK", later) == pytest.approx(expected)
    for _ in range(5):
        assert ratings.get("A", "LCK", later) == pytest.approx(expected)
    assert ratings.last["A"] == t
    ratings.update_series("A", "B", 1, 0, "LCK", later)
    old = ratings.r["A"]
    assert ratings.get("A", "LCK", later + timedelta(days=60)) == pytest.approx(
        old + OFFSEASON_SHRINK * (LEAGUE_PRIOR["LCK"] - old))
