import ast, json, pathlib, pytest
from datetime import datetime, timezone
from scanner import sim, edge, forecast, config

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("bo,keys", [(1, {"1-0", "0-1"}), (3, {"2-0", "2-1", "1-2", "0-2"}),
                                     (5, {"3-0", "3-1", "3-2", "2-3", "1-3", "0-3"})])
def test_exact_distribution_sums_to_one(bo, keys):
    s = sim.simulate(0.6, bo, sims=20000, seed=1)
    assert set(s["exact"]) == keys
    assert abs(sum(s["exact"].values()) - 1) < 1e-9
    assert s["locked_score"] == max(s["exact"], key=s["exact"].get)
    assert s["p_series_a"] > 0.5


def _card(bo, lock, exact):
    return {"bo": bo, "team_a": {"name": "Alpha", "code": "AAA"}, "team_b": {"name": "Beta", "code": "BBB"},
            "model": {"p_map_a": 0.6}, "sim": {"locked_score": lock, "exact": exact}}


def _mk(mid, t, title, ask, line=None, rules=""):
    return {"marketId": mid, "sportsMarketType": t, "title": title, "sportsLine": line, "status": "open",
            "rulesPrimary": rules, "pricing": {"buyYesPriceUsd": int(ask * 1e6), "sellYesPriceUsd": int((ask - .01) * 1e6), "volume": 100}}


def test_bo3_mapping_and_max_ev_rule():
    ex = {"2-0": .40, "2-1": .25, "1-2": .20, "0-2": .15}
    c = _card(3, "2-0", ex)
    ev = {"markets": [
        _mk("M-0", "moneyline", "Alpha", .55), _mk("M-1", "moneyline", "Beta", .47),
        _mk("H-0", "map_handicap", "Alpha", .30, "-1.5", 'resolve to "Alpha" if Alpha wins 2 or more games than Beta'),
        _mk("H-1", "map_handicap", "Beta", .72, "-1.5", 'resolve to "Alpha" if Alpha wins 2 or more games than Beta'),
        _mk("T-0", "totals", "Over", .40, "2.5"), _mk("T-1", "totals", "Under", .62, "2.5")]}
    legs, best, best_any, *_ = edge.evaluate(c, ev, {})
    by = {l["label"]: l for l in legs}
    assert by["AAA -1.5 maps"]["our_prob"] == pytest.approx(.40)
    assert by["BBB +1.5 maps"]["our_prob"] == pytest.approx(.60)
    assert by["Over 2.5 maps"]["our_prob"] == pytest.approx(.45)
    assert by["BBB +1.5 maps"]["cashes_on_lock"] is False      # dies on locked 2-0
    assert by["AAA -1.5 maps"]["cashes_on_lock"] is True
    plus = [l for l in legs if l["ev_per_$1"] > 0 and l["our_prob"] > 0.5]
    assert best is not None and best["ev_per_$1"] == max(l["ev_per_$1"] for l in plus)
    # rule r3: AAA -1.5 @0.30 (40% shot) is the top raw EV but model prob <= 50%, so it is NOT the bet;
    # ML AAA (65% @0.55) is the top-EV line above 50%. best_any keeps the excluded line for transparency.
    assert best["label"] == "ML AAA" and best["qualifies"]
    assert best_any["label"] == "AAA -1.5 maps" and not best_any["qualifies"] and best_any["ev_per_$1"] > best["ev_per_$1"]
    for pair in ("M", "H", "T"):
        ls = [l for l in legs if l["pair_id"] == pair]
        assert sum(l["fair_prob"] for l in ls) == pytest.approx(1, abs=1e-3)


def test_anti_lock_market_can_be_best_bet():
    ex = {"2-0": .35, "2-1": .10, "1-2": .30, "0-2": .25}     # lock 2-0 but Beta wins the series 55%
    c = _card(3, "2-0", ex)
    ev = {"markets": [_mk("M-0", "moneyline", "Alpha", .57), _mk("M-1", "moneyline", "Beta", .45)]}
    legs, best, best_any, *_ = edge.evaluate(c, ev, {})
    assert best["label"] == "ML BBB" and best["cashes_on_lock"] is False and best_any is best


def test_no_bet_when_only_plus_ev_lines_are_under_50pct():
    ex = {"2-0": .30, "2-1": .25, "1-2": .25, "0-2": .20}     # Alpha 55%, Beta 45%
    c = _card(3, "2-0", ex)
    # Beta ML @0.30 is hugely +EV (45% shot) but under 50%; Alpha @0.72 (55%) is -EV -> NO BET
    ev = {"markets": [_mk("M-0", "moneyline", "Alpha", .72), _mk("M-1", "moneyline", "Beta", .30)]}
    legs, best, best_any, *_ = edge.evaluate(c, ev, {})
    assert best is None and best_any["label"] == "ML BBB" and best_any["ev_per_$1"] > 0 and best_any["our_prob"] < 0.5
    assert not any(l["qualifies"] for l in legs)


def test_exactly_50pct_does_not_qualify():
    assert not edge.qualifies({"skip_reason": None, "our_prob": 0.5, "ev_per_$1": 0.2})
    assert edge.qualifies({"skip_reason": None, "our_prob": 0.5001, "ev_per_$1": 0.0001})
    assert not edge.qualifies({"skip_reason": "price < 0.03", "our_prob": 0.9, "ev_per_$1": 0.5})
    assert not edge.qualifies({"skip_reason": None, "our_prob": 0.9, "ev_per_$1": 0})
    assert edge.BEST_BET_RULE_VERSION.startswith("r3") and "50%" in edge.BEST_BET_RULE and "NO BET" in edge.BEST_BET_RULE


def test_pass_when_nothing_plus_ev():
    ex = {"2-0": .30, "2-1": .25, "1-2": .25, "0-2": .20}
    c = _card(3, "2-0", ex)
    ev = {"markets": [_mk("M-0", "moneyline", "Alpha", .60), _mk("M-1", "moneyline", "Beta", .46)]}
    legs, best, *_ = edge.evaluate(c, ev, {})
    assert best is None and all(l["ev_per_$1"] <= 0 for l in legs)


def test_sanity_guard_skips_pinned_and_dead_lines():
    ex = {"2-0": .30, "2-1": .25, "1-2": .25, "0-2": .20}
    c = _card(3, "2-0", ex)
    # Beta ML at 0.02 would be the top raw EV, but a sub-0.03 price is skipped; Alpha at 0.50 is the pick
    ev = {"markets": [_mk("M-0", "moneyline", "Alpha", .50), _mk("M-1", "moneyline", "Beta", .02)]}
    legs, best, *_ = edge.evaluate(c, ev, {})
    by = {l["label"]: l for l in legs}
    assert by["ML BBB"]["ev_per_$1"] > by["ML AAA"]["ev_per_$1"] > 0
    assert by["ML BBB"]["eligible"] is False and "0.03" in by["ML BBB"]["skip_reason"]
    assert best["label"] == "ML AAA"
    # a 0.98 line is skipped too
    assert edge.sanity_skip({"ask": .98, "volume": 10}) and edge.sanity_skip({"ask": .5, "volume": 10}) is None
    # no volume and no orderbook -> skipped; volume or a book -> fine
    assert edge.sanity_skip({"ask": .5, "volume": 0, "has_book": False}) == "no volume and no orderbook"
    assert edge.sanity_skip({"ask": .5, "volume": 0, "has_book": True}) is None


def test_bo1_ignores_handicap_and_totals():
    c = _card(1, "1-0", {"1-0": .6, "0-1": .4})
    ev = {"markets": [_mk("M-0", "moneyline", "Alpha", .5), _mk("M-1", "moneyline", "Beta", .52),
                      _mk("H-0", "map_handicap", "Alpha", .3, "-1.5"), _mk("T-0", "totals", "Over", .4, "2.5")]}
    legs, best, *_ = edge.evaluate(c, ev, {})
    assert {l["type"] for l in legs} == {"moneyline"}
    assert best["label"] == "ML AAA"


def test_bo5_lines():
    ex = {"3-0": .2, "3-1": .25, "3-2": .2, "2-3": .15, "1-3": .12, "0-3": .08}
    c = _card(5, "3-1", ex)
    f = edge.outcome_fn("map_handicap", True, -1.5, None, 5); assert f(3, 1) and not f(3, 2)
    f = edge.outcome_fn("map_handicap", False, 2.5, None, 5); assert f(3, 0) is False and f(3, 1)
    assert edge.prob_from_exact(ex, edge.outcome_fn("totals_over", None, 3.5, None, 5)) == pytest.approx(.72)
    assert edge.prob_from_exact(ex, edge.outcome_fn("totals_over", None, 4.5, None, 5)) == pytest.approx(.35)


def test_freeze_is_immutable_and_hash_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORECAST_DIR", tmp_path)
    body = {"date": "2099-01-01", "integrity": forecast.INTEGRITY, "cards": []}
    forecast.freeze(body, now=datetime(2099, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(FileExistsError):
        forecast.freeze(body)
    assert forecast.load_frozen("2099-01-01")["integrity"]["market_inputs_to_forecast"] == "NONE"
    p = tmp_path / "2099-01-01.json"; d = json.loads(p.read_text()); d["cards"] = [{"x": 1}]; p.write_text(json.dumps(d))
    with pytest.raises(RuntimeError):
        forecast.load_frozen("2099-01-01")


def test_edge_refuses_without_frozen_forecast(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORECAST_DIR", tmp_path)
    with pytest.raises(SystemExit):
        edge.run("2099-02-02")


def test_forecast_modules_never_import_market_code():
    for mod in ("forecast.py", "ratings.py", "sim.py", "schedule.py", "forecast_v2.py", "v2.py", "sim_v2.py",
                "draftmodel.py", "ratings_v2.py", "golgg.py", "corpus.py", "patches.py", "champs.py", "backtest_v2.py", "sim_r.py"):
        tree = ast.parse((ROOT / "scanner" / mod).read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert "edge" not in names and "edge" not in mods, mod
        assert "jup" not in (ROOT / "scanner" / mod).read_text().lower(), mod


def test_no_secrets_committed():
    import re
    pat = re.compile(r"(api[_-]?key|token|secret)\s*[=:]\s*['\"][A-Za-z0-9_\-]{20,}['\"]", re.I)
    for p in ROOT.rglob("*"):
        if p.is_file() and ".git" not in p.parts and ".cache" not in p.parts and p.suffix in {".py", ".yml", ".md", ".txt", ".json", ".html"}:
            assert not pat.search(p.read_text(errors="ignore")), p


def _toy_spec(bo, fearless, C=12):
    import numpy as np
    rng = np.random.default_rng(0)
    combo = {"w": 1.0, "U": rng.normal(0, 1, (2, 5, C)), "V": rng.normal(0, 1, (2, C)),
             "D": np.zeros((2, 5, C)), "cont": np.zeros(2)}
    # strongly prefer champions 0..9 so that, without Fearless, game 2 repeats game 1's picks
    combo["U"][:, :, :10] += 6.0
    return {"bo": bo, "fearless": fearless, "elo_logit": 0.3, "side_blue": 0.1, "rho": 0.0, "q_loser_blue": 0.5,
            "g1_a_blue": True, "combos": [combo], "comp_coef": 0.0, "U_ap": np.zeros(C), "U_tank": np.ones(C)}


@pytest.mark.parametrize("bo", [1, 3, 5])
def test_sim_v2_exact_distribution(bo):
    from scanner import sim_v2
    s = sim_v2.simulate(_toy_spec(bo, bo > 1, C=60), sims=4000, seed=1)
    assert abs(sum(s["exact"].values()) - 1) < 1e-9 and len(s["exact"]) == 2 * (bo // 2 + 1)
    assert s["p_series_a"] > 0.5


def test_sim_v2_fearless_blocks_repeat_picks():
    import numpy as np
    from scanner import sim_v2, v2
    sp = _toy_spec(3, True, C=40)
    bd1 = v2.beam_drafts(sp["combos"][0], True, set(), 0.0, sp["U_ap"], sp["U_tank"], 0.3, 0.1, 0.0)
    used = set(bd1[0]["picks_a"]) | set(bd1[0]["picks_b"])
    bd2 = v2.beam_drafts(sp["combos"][0], False, used, 0.0, sp["U_ap"], sp["U_tank"], 0.3, 0.1, 0.0)
    assert not (set(bd2[0]["picks_a"]) | set(bd2[0]["picks_b"])) & used
    s = sim_v2.simulate(sp, sims=2000, seed=2)
    rm = np.array(s["role_marginals"])
    # game 2 can never use the 10 champions game 1 used, so its picks spread to champions >= 10
    assert rm[1][:, :, 10:].sum() > rm[0][:, :, 10:].sum() + 1.0


def test_v2_freeze_does_not_touch_v1(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORECAST_DIR", tmp_path)
    forecast.freeze({"date": "2099-03-03", "integrity": forecast.INTEGRITY, "model_version": "elo-map-v1", "cards": []})
    h1 = (tmp_path / "2099-03-03.json").read_text()
    forecast.freeze({"date": "2099-03-03", "integrity": forecast.INTEGRITY, "model_version": "v2-x", "cards": []})
    assert (tmp_path / "2099-03-03.json").read_text() == h1 and (tmp_path / "2099-03-03.v2.json").exists()
    assert forecast.load_frozen("2099-03-03", "v2")["model_version"] == "v2-x"
    from scanner.forecast_v2 import INTEGRITY_V2
    assert INTEGRITY_V2["market_inputs_to_forecast"] == "NONE" and INTEGRITY_V2["excluded"] == forecast.INTEGRITY["excluded"]
