"""No-vig / EV math for the Jupiter edge layer, and the presentation helpers built on it."""
import copy, json, math
from pathlib import Path
import pytest
from scanner import edge, edgeview as ev

ROOT = Path(__file__).resolve().parent.parent


def test_devig_pair_hand_computed():
    fa, fb, over = ev.devig_pair(0.15, 0.86)
    assert fa == pytest.approx(0.15 / 1.01) and fb == pytest.approx(0.86 / 1.01)
    assert fa + fb == pytest.approx(1.0) and over == pytest.approx(0.01)
    fa, fb, over = ev.devig_pair(0.48, 0.48)          # under-round book: fair stays 50/50, overround negative
    assert fa == pytest.approx(0.5) and over == pytest.approx(-0.04)


def test_fee_and_ev_hand_computed():
    assert edge.fee(0.5) == pytest.approx(0.07 * 0.25)
    assert ev.cost_per_contract(0.60) == pytest.approx(0.60 + 0.07 * 0.6 * 0.4)        # 0.6168
    assert ev.ev_per_dollar(0.6194, 0.60) == pytest.approx(0.6194 / 0.6168 - 1)          # ~ +0.0042
    assert ev.ev_per_dollar(0.60, 0.60) < 0                                            # fair price loses the fee
    # breakeven probability equals cost
    assert ev.ev_per_dollar(ev.cost_per_contract(0.3), 0.3) == pytest.approx(0.0, abs=1e-12)


def test_edge_log_matches_formulas():
    for p in sorted((ROOT / "edge_log").glob("*.json")):
        for r in json.loads(p.read_text()).get("series", []):
            pairs = {}
            for l in r.get("legs", []):
                pairs.setdefault(l["pair_id"], []).append(l)
            for ls in pairs.values():
                for l in ls:
                    if len(ls) == 2:
                        o = [x for x in ls if x is not l][0]
                        assert l["fair_prob"] == pytest.approx(ev.devig_pair(l["ask"], o["ask"])[0], abs=1e-4)
                    assert l["edge_pct"] == pytest.approx(100 * (l["our_prob"] - l["fair_prob"]), abs=0.011)
                    assert l["ev_per_$1"] == pytest.approx(ev.ev_per_dollar(l["our_prob"], l["ask"]), abs=1e-3)
            b = r.get("best_bet")
            d = json.loads(p.read_text())
            if d.get("best_bet_rule", "").startswith("max-EV"):   # current rule: top eligible +EV line, lock ignored
                ok = [l for l in r["legs"] if l.get("eligible") and l["ev_per_$1"] > 0]
                if b:
                    assert b["ev_per_$1"] > 0 and b["eligible"] and b["ev_per_$1"] == max(l["ev_per_$1"] for l in ok)
                elif r.get("status") == "ok":
                    assert not ok
            elif b:   # legacy logs (before the lock rule was removed): best bet had to cash on the lock
                assert b["ev_per_$1"] > 0 and b["cashes_on_lock"] is True


@pytest.mark.parametrize("bo", [1, 3, 5])
def test_ratings_only_exact_is_distribution(bo):
    d = ev.ratings_only_exact(0.6, bo)
    assert sum(d.values()) == pytest.approx(1.0)
    pa = sum(v for k, v in d.items() if int(k[0]) > int(k[-1]))
    assert 0.5 < pa < 1
    if bo == 1:
        assert pa == pytest.approx(edge.game_prob(0.6))


def test_enrich_is_read_only_and_sorted():
    fc = json.loads((ROOT / "forecast_log/2026-10-28.v2.json").read_text())
    e = json.loads((ROOT / "edge_log/2026-10-28.v2.json").read_text())
    cards = {c["match_id"]: c for c in fc["cards"]}
    for r in e["series"]:
        c = cards[r["match_id"]]; before = copy.deepcopy(c)
        legs = ev.enrich(r, c)
        assert c == before                                    # firewall: odds never touch the forecast
        assert [l["ev_per_$1"] for l in legs] == sorted((l["ev_per_$1"] for l in legs), reverse=True)
        assert sum(l["is_best"] for l in legs) == (1 if r["best_bet"] else 0)
        for l in legs:
            assert l["market"] and l["side"]


def test_discord_payload_limits():
    from scanner import discord_report as dr
    p = dr.build_payload("2026-10-28")
    assert 1 <= len(p["embeds"]) <= 10 and p["allowed_mentions"] == {"parse": []}
    txt = json.dumps(p)
    assert "PASS" in txt and "EV" in txt and "NONE" in txt
    for em in p["embeds"]:
        assert len(em.get("description", "")) <= 4096
        for f in em.get("fields", []):
            assert len(f["value"]) <= 1024
