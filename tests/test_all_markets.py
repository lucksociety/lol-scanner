"""Every Jupiter LoL market is either priced from the frozen sim or listed as not modeled; nothing is dropped."""
import json, pathlib, pytest
from scanner import edge, edgeview

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = json.loads((ROOT / "tests/fixtures/jupiter_markets_2026-10-28.json").read_text())


def _card(bo, lock, exact, names=("Ground Zero Gaming", "GZ", "Frank Esports", "FNK"), p_game=None, reach=None):
    sim = {"locked_score": lock, "exact": exact}
    if p_game:
        sim["p_game_a"], sim["p_game_reached"] = p_game, reach
    return {"bo": bo, "team_a": {"name": names[0], "code": names[1]}, "team_b": {"name": names[2], "code": names[3]},
            "model": {"p_map_a": 0.6}, "sim": sim}


BO5 = {"3-0": .2, "3-1": .25, "3-2": .2, "2-3": .15, "1-3": .12, "0-3": .08}


def test_bo5_every_market_accounted_for():
    ms = FIX["POLY-1270992"]
    c = _card(5, "3-1", BO5, p_game=[.6, .6, .6, .6, .6], reach=[1, 1, 1, .8, .35])
    legs, best, best_any, nm, unpriced = edge.evaluate(c, {"markets": ms}, {}, ms)
    priced_ids = {l["market_id"].split(":")[0] for l in legs}
    accounted = priced_ids | {x["market_id"] for x in nm} | {x["market_id"] for x in unpriced}
    assert accounted == {m["marketId"] for m in ms}                     # nothing silently dropped
    labels = {l["label"] for l in legs}
    # alt handicaps both sides, both totals lines both sides, games 1-4 both sides
    for lab in ["GZ -1.5 maps", "FNK +1.5 maps", "GZ -2.5 maps", "FNK +2.5 maps", "Over 3.5 maps", "Under 3.5 maps",
                "Over 4.5 maps", "Under 4.5 maps", "Game 4 GZ", "Game 4 FNK", "ML GZ", "ML FNK"]:
        assert lab in labels, lab
    by = {l["label"]: l for l in legs}
    assert by["GZ -2.5 maps"]["our_prob"] == pytest.approx(.2)
    assert by["FNK +2.5 maps"]["our_prob"] == pytest.approx(.8)
    assert by["Over 4.5 maps"]["our_prob"] == pytest.approx(.35)
    assert by["GZ -2.5 maps"]["cashes_on_lock"] is False and by["GZ -1.5 maps"]["cashes_on_lock"] is True
    # game 4: reached 80%, unplayed resolves 50-50 -> expected payout .8*.6 + .2*.5
    assert by["Game 4 GZ"]["our_prob"] == pytest.approx(.8 * .6 + .2 * .5)
    assert by["Game 4 GZ"]["cashes_on_lock"] is True       # 3-1 lock: game 4 decides it, won by GZ
    assert by["Game 2 GZ"]["cashes_on_lock"] is None       # order-dependent
    assert {x["type"] for x in nm} == {"lol_both_teams_baron", "lol_both_teams_dragon", "lol_both_teams_inhibitors",
                                       "lol_quadra_kill", "lol_penta_kill"}
    assert all(x["reason"] for x in nm) and len(nm) == 25


def test_kill_totals_listed_not_modeled():
    ms = FIX["POLY-1262496"]
    c = _card(3, "2-0", {"2-0": .38, "2-1": .28, "1-2": .2, "0-2": .14}, names=("Invictus Gaming", "IG", "LYON", "LYON"))
    legs, best, _, nm, _ = edge.evaluate(c, {"markets": ms}, {}, ms)
    kills = [x for x in nm if x["type"] == "kill_over_under_game"]
    assert len(kills) == 4 and all(x["yes_ask"] for x in kills)
    assert {l["type"] for l in legs} == {"moneyline", "map_handicap", "totals_over", "totals_under", "game"}
    if best:
        assert best["ev_per_$1"] > 0 and best["our_prob"] > 0.5
        assert best["ev_per_$1"] == max(l["ev_per_$1"] for l in legs if l["eligible"] and l["our_prob"] > 0.5)


def test_game_outcome_on_lock():
    f = edge.outcome_fn("game", True, None, 3, 3)
    assert f(2, 0) is False          # game 3 never played -> 50-50, not a cash
    assert f(2, 1) is True and f(1, 2) is False
    assert edge.outcome_fn("game", True, None, 1, 3)(2, 0) is True
    assert edge.outcome_fn("game", False, None, 1, 3)(2, 1) is None


def test_single_id_yes_no_market_prices_both_sides():
    m = {"marketId": "POLY-9", "sportsMarketType": "totals", "title": "Over", "sportsLine": "2.5", "status": "open",
         "rulesPrimary": "", "pricing": {"buyYesPriceUsd": 400000, "buyNoPriceUsd": 620000}}
    c = _card(3, "2-0", {"2-0": .4, "2-1": .3, "1-2": .2, "0-2": .1})
    legs, *_ = edge.evaluate(c, {"markets": [m]}, {}, [m])
    by = {l["label"]: l for l in legs}
    assert by["Over 2.5 maps"]["our_prob"] == pytest.approx(.5)
    assert by["NOT Over 2.5 maps"]["our_prob"] == pytest.approx(.5) and by["NOT Over 2.5 maps"]["cashes_on_lock"] is True
    assert by["NOT Over 2.5 maps"]["fair_prob"] == pytest.approx(.62 / 1.02, abs=1e-4)


def test_pagination_follows_has_next(monkeypatch):
    pages = {0: {"data": [{"eventId": "E1", "subcategory": "lol", "metadata": {"title": "LoL: A vs B"}}],
                 "pagination": {"hasNext": True}},
             100: {"data": [{"eventId": "E2", "subcategory": "lol", "metadata": {"title": "LoL: C vs D"}}],
                   "pagination": {"hasNext": False}}}
    monkeypatch.setattr(edge, "_get", lambda path, **p: pages[p["start"]])
    evs = edge.fetch_lol_events([])
    assert {e["eventId"] for e in evs} == {"E1", "E2"}


def test_market_side_labels():
    for lab, t in [("Game 4 GZ", "game"), ("FNK +2.5 maps", "map_handicap"), ("NOT Over 2.5 maps", "totals_over")]:
        m, s = edgeview.market_side({"label": lab, "type": t})
        assert m and s
