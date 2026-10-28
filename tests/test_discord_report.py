import json, pytest
from scanner import discord_report as dr


def _card(i, bo=3, lock="2-0", pa=0.62):
    return {"match_id": f"m{i}", "bo": bo, "league": "Worlds", "stage": "Swiss", "start_et": "Wed Oct 28, 04:00 PM ET",
            "team_a": {"code": f"A{i}", "name": f"Alpha {i}"}, "team_b": {"code": f"B{i}", "name": f"Beta {i}"},
            "sim": {"locked_score": lock, "locked_score_prob": 0.35, "p_series_a": pa}}


def _row(i, bet):
    leg = {"label": f"Game 2 A{i}", "type": "game", "ask": 0.60, "our_prob": 0.62, "fair_prob": 0.594,
           "edge_pct": 2.5, "ev_per_$1": 0.004, "cashes_on_lock": True}
    return {"match_id": f"m{i}", "status": "ok", "best_bet": leg if bet else None, "best_ev_any": None if bet else leg}


FC = {"model_version": "v2", "frozen_at_et": "2026-10-28 03:50:13 AM ET"}
EDGE = {"odds_fetched_at_et": "2026-10-28 10:51:09 AM ET"}


def _check_limits(p):
    assert len(p["embeds"]) == 1          # one embed: Discord merges same-url embeds and hides their text
    em = p["embeds"][0]
    assert len(em["description"]) <= dr.LIM_DESC and len(em["fields"]) <= dr.LIM_FIELDS
    for f in em["fields"]:
        assert len(f["name"]) <= dr.LIM_FIELD_NAME and len(f["value"]) <= dr.LIM_FIELD_VAL
    assert dr._embed_chars(em) <= dr.LIM_TOTAL
    return em


def test_every_series_listed_even_when_all_pass():
    fc = {**FC, "cards": [_card(1), _card(2, bo=1, lock="0-1", pa=0.4), _card(3, bo=5, lock="3-1")],
          "skipped": [{"series": "X vs Y", "reason": "no lineup"}]}
    edge = {**EDGE, "series": [_row(1, False), _row(2, True)]}   # m3 has no Jupiter market
    em = _check_limits(dr.render("2026-10-28", fc, edge))
    names = [f["name"] for f in em["fields"]]
    assert names[0].startswith("A1 vs B1 · Bo3") and names[1].startswith("A2 vs B2 · Bo1") and names[2].startswith("A3 vs B3 · Bo5")
    assert "04:00 PM ET" in names[0] and "not modeled" in names[3]
    v = [f["value"] for f in em["fields"]]
    assert "**A1 2-0** (35.0%)" in v[0] and "A1 62.0% to win" in v[0] and "PASS" in v[0]
    assert "**B2 1-0**" in v[1] and "B2 60.0% to win" in v[1] and "EV +0.004/$1" in v[1]
    assert "PASS" in v[2] and "no Jupiter market" in v[2]


def test_no_edge_layer_still_lists_series():
    em = _check_limits(dr.render("2026-10-28", {**FC, "cards": [_card(1)]}, None))
    assert len(em["fields"]) == 1 and "edge layer not run" in em["fields"][0]["value"]


@pytest.mark.parametrize("n", [24, 25, 40])
def test_big_slate_stays_within_limits_with_overflow(n):
    fc = {**FC, "cards": [_card(i) for i in range(n)], "skipped": [{"series": "S vs T", "reason": "r" * 2000}]}
    edge = {**EDGE, "series": [_row(i, i % 2) for i in range(n)]}
    em = _check_limits(dr.render("2026-10-28", fc, edge))
    if n + 1 > dr.LIM_FIELDS:
        assert em["fields"][-1]["name"].endswith("more series")


def test_real_oct28_payload_lists_both_series():
    p = dr.build_payload("2026-10-28")
    em = _check_limits(p)
    txt = dr.to_text(p)
    assert "MKOI vs T1 · Bo3" in txt and "LYON vs IG · Bo3" in txt
    assert "**T1 2-0** (33.1%)" in txt and "T1 61.4% to win" in txt
    assert "**IG 2-0** (37.6%)" in txt and "IG 65.7% to win" in txt
