from scanner import v2


def engine(games, names=None):
    obj = v2.Engine.__new__(v2.Engine)
    obj.games, obj.names = games, names or {}
    return obj


def test_los_alias_uses_real_history_without_requiring_previous_join():
    e = engine([{"date": "2026-10-29", "blue": "Los Grandes", "red": "FURIA"}])
    assert e.golgg_name("LOS", "2026-10-30") == "Los Grandes"
    assert e.golgg_name("LØS", "2026-10-30") == "Los Grandes"
    assert e.golgg_name("LOS", "2026-10-29") is None
    assert v2._sim("LOS", "Los Grandes") == 1
    assert v2._sim("LOS", "Los Ratones") < .6


def test_latest_pre_cutoff_roster_alias_and_no_future_only_alias():
    e = engine([{"date": "2025-10-01", "blue": "Hanwha Life eSports", "red": "G2"},
                {"date": "2026-10-01", "blue": "Hanwha Life Esports", "red": "G2"}],
               {"Hanwha Life eSports": "Hanwha Life Esports", "Hanwha Life Esports": "Hanwha Life Esports"})
    assert e.golgg_name("Hanwha Life Esports", "2026-09-01") == "Hanwha Life eSports"
    assert e.golgg_name("Hanwha Life Esports", "2026-10-30") == "Hanwha Life Esports"
    assert e.golgg_name("Hanwha Life Esports", "2025-01-01") is None


def test_los_historical_results_join_to_official_identity():
    games = {"series": [{"date": "2026-10-20", "blue": "Los Grandes", "red": "FURIA"}]}
    row = {"state": "completed", "start": "2026-10-20T18:00:00Z", "a_w": 1, "b_w": 0,
           "a": "LOS", "b": "FUR", "a_name": "LOS", "b_name": "FURIA"}
    joined, names = v2.join(games, [row])
    assert joined["series"] == row and names["Los Grandes"] == "LOS"
