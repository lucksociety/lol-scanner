"""Date handling: rolling window, ET dates, addenda freezes, Jupiter +/-1 day matching, index = today ET, date selector."""
import json
from datetime import datetime, timezone
import pytest
from scanner import config, forecast, edge, window, dashboard, simexport


def _card(mid, a, b, start):
    return {"match_id": mid, "start_utc": start, "start_et": window.start_et(start), "league": "Worlds", "stage": "Swiss", "bo": 3,
            "team_a": {"code": a, "name": a, "img": "", "elo_v2": 1600, "event_record": "0-0", "form": []},
            "team_b": {"code": b, "name": b, "img": "", "elo_v2": 1600, "event_record": "0-0", "form": []},
            "model": {"p_map_a": .5, "patch": "16.20", "fearless": True, "g1_side_source": "x", "history_cutoff_utc": start},
            "sim": {"p_series_a": .5, "locked_score": "2-0", "locked_score_prob": .3, "exact": {"2-0": .3, "2-1": .2, "1-2": .2, "0-2": .3},
                    "maps_dist": {"2": .6, "3": .4}, "e_maps": 2.4, "sims": 1000},
            "drafts": [], "adversarial_review": []}


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    for k, sub in (("FORECAST_DIR", "forecast_log"), ("EDGE_DIR", "edge_log"), ("DOCS_DIR", "docs"), ("ODDS_DIR", "odds")):
        (tmp_path / sub).mkdir()
        monkeypatch.setattr(config, k, tmp_path / sub)
    monkeypatch.setattr(config, "CALIB_FILE", tmp_path / "calib.csv")
    return tmp_path


def test_window_dates_and_et_boundary():
    assert window.window_dates("2026-10-28") == ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30",
                                                 "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04"]
    # 03:30 UTC on the 29th is still the 28th in New York
    assert window.today_et(datetime(2026, 10, 29, 3, 30, tzinfo=timezone.utc)) == "2026-10-28"
    rows = [{"match_id": "1", "start": "2026-10-29T00:00:00Z", "a": "A", "b": "B", "state": "unstarted"}]
    assert [r["match_id"] for r in window.by_date(rows, ["2026-10-28"])["2026-10-28"]] == ["1"]


def test_addendum_freeze_merges_and_stays_immutable(tmp_repo):
    t0 = datetime(2026, 10, 28, 8, tzinfo=timezone.utc)
    forecast.freeze({"date": "2026-10-31", "integrity": forecast.INTEGRITY, "model_version": "v2-x",
                     "cards": [_card("m1", "AAA", "BBB", "2026-10-31T17:00:00Z")]}, now=t0)
    add = {"date": "2026-10-31", "integrity": forecast.INTEGRITY, "model_version": "v2-x", "cards": [_card("m2", "CCC", "DDD", "2026-10-31T20:00:00Z")],
           "skipped": [{"match_id": "m3", "series": "EEE vs FFF", "reason": "no history"}]}
    forecast.freeze_addendum(add, now=datetime(2026, 10, 31, 9, tzinfo=timezone.utc))
    fc = forecast.load_frozen("2026-10-31", "v2")
    assert [c["match_id"] for c in fc["cards"]] == ["m1", "m2"] and fc["skipped"][0]["match_id"] == "m3"
    assert fc["frozen_at_utc_latest"].startswith("2026-10-31T09") and len(fc["addenda"]) == 1
    assert forecast.frozen_ids("2026-10-31", "v2") == {"m1", "m2", "m3"}
    with pytest.raises(FileExistsError):   # an already-frozen series is never re-frozen
        forecast.freeze_addendum({**add, "skipped": []})
    p = tmp_repo / "forecast_log" / "2026-10-31.v2.add1.json"
    d = json.loads(p.read_text()); d["cards"][0]["sim"]["locked_score"] = "0-2"; p.write_text(json.dumps(d))
    with pytest.raises(RuntimeError):
        forecast.load_frozen("2026-10-31", "v2")


def test_pending_ids_never_freezes_started_or_tbd():
    now = datetime(2026, 10, 28, 15, tzinfo=timezone.utc)
    rows = [{"match_id": "a", "a": "T1", "b": "MKOI", "state": "unstarted", "start": "2026-10-28T20:00:00Z"},
            {"match_id": "b", "a": "TBD", "b": "TBD", "state": "unstarted", "start": "2026-10-31T17:00:00Z"},
            {"match_id": "c", "a": "X", "b": "Y", "state": "inProgress", "start": "2026-10-28T14:00:00Z"},
            {"match_id": "d", "a": "X", "b": "Y", "state": "unstarted", "start": "2026-10-28T14:00:00Z"}]
    assert window.pending_ids(rows, now) == {"a"}


def _jev(eid, a, b, begin):
    mk = lambda t: {"marketId": eid + t, "title": t, "sportsMarketType": "moneyline"}
    return {"eventId": eid, "beginAt": str(int(datetime.fromisoformat(begin).timestamp())), "metadata": {"title": f"LoL: {a} vs {b}"},
            "markets": [mk(a), mk(b)]}


def test_jupiter_match_within_one_et_day_and_closest():
    c = _card("m", "MKOI", "T1", "2026-10-28T20:00:00Z")
    c["team_a"]["name"] = "Movistar KOI"
    # Jupiter dated the series Oct 29 (UTC/next-day): still the same series
    e1 = _jev("J1", "Movistar KOI", "T1", "2026-10-29T20:00:00+00:00")
    assert edge.match_event(c, [e1])["eventId"] == "J1"
    # three days away: a different meeting of the same teams -> not matched
    e2 = _jev("J2", "T1", "Movistar KOI", "2026-11-01T20:00:00+00:00")
    assert edge.match_event(c, [e2]) is None
    e3 = _jev("J3", "T1", "Movistar KOI", "2026-10-28T20:00:00+00:00")
    assert edge.match_event(c, [e1, e2, e3])["eventId"] == "J3"


def test_dashboard_lists_every_series_and_index_is_today(tmp_repo, monkeypatch):
    monkeypatch.setattr(window, "today_et", lambda now=None: "2026-10-28")
    forecast.freeze({"date": "2026-10-28", "integrity": forecast.INTEGRITY, "model_version": "v2-x",
                     "cards": [_card("m1", "MKOI", "T1", "2026-10-28T20:00:00Z")],
                     "skipped": [{"match_id": "m9", "series": "NEW vs T1", "start_utc": "2026-10-28T22:00:00Z", "reason": "no pro-game history"}]})
    sched = {"2026-10-28": [{"match_id": "m1", "start": "2026-10-28T20:00:00Z", "start_et": "Wed Oct 28, 04:00 PM ET", "state": "unstarted",
                             "league": "Worlds", "bo": 3, "a": "MKOI", "b": "T1", "a_w": None, "b_w": None},
                            {"match_id": "m9", "start": "2026-10-28T22:00:00Z", "start_et": "Wed Oct 28, 06:00 PM ET", "state": "unstarted",
                             "league": "Worlds", "bo": 3, "a": "NEW", "b": "T1", "a_w": None, "b_w": None}],
             "2026-10-29": [], "2026-10-31": [{"match_id": "t", "start": "2026-10-31T17:00:00Z", "start_et": "Sat Oct 31, 01:00 PM ET",
                                               "state": "unstarted", "league": "Worlds", "bo": 3, "a": "TBD", "b": "TBD", "a_w": None, "b_w": None}]}
    window.write_sched(sched)
    for d in ("2026-10-29", "2026-10-28", "2026-10-31"):
        dashboard.render(d, "v2", write_index=False)
    window.write_manifest("2026-10-28", sched)
    dashboard.write_index("2026-10-28")
    idx = (tmp_repo / "docs" / "index.html").read_text()
    assert "Wed Oct 28, 2026" in idx and 'data-index="1"' in idx      # index = today ET, not the latest page (Oct 31)
    assert "MKOI vs T1" in idx and "NEW vs T1" in idx and "NOT MODELED" in idx
    assert 'id="datenav"' in idx and 'type="date"' in idx and "Tomorrow" in idx and "Yesterday" in idx
    d31 = (tmp_repo / "docs" / "2026-10-31.html").read_text()
    assert "TEAMS TBD" in d31
    assert "No games" in (tmp_repo / "docs" / "2026-10-29.html").read_text()
    m = json.loads((tmp_repo / "docs" / "data" / "dates.json").read_text())
    assert {x["date"]: x["n_series"] for x in m["dates"]} == {"2026-10-28": 2, "2026-10-29": 0, "2026-10-31": 1}
    # simulator slate keeps every series of the date, with not-modeled / TBD rows flagged
    monkeypatch.setattr(simexport, "_team_table", lambda now, params: ([], "16.20"))
    simexport.export(["2026-10-28", "2026-10-31"], "v2")
    sd = json.loads((tmp_repo / "docs" / "data" / "sim_data.json").read_text())
    assert [(m["a"], m["modeled"]) for m in sd["slate"]["2026-10-28"]] == [("MKOI", True), ("NEW", False)]
    assert sd["slate"]["2026-10-31"][0]["status"] == "TEAMS TBD" and sd["today_et"]
