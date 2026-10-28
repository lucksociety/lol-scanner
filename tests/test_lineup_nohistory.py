import pytest
from scanner import forecast_v2


class _E:
    games = []
    def last_lineup(self, g, d):
        return None, None


def test_team_without_history_raises_nohistory():
    with pytest.raises(forecast_v2.NoHistory):
        forecast_v2.lineup_status(_E(), None, [], ["Worlds 2026"], "2026-10-30")
