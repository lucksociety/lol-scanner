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
            "rulesPrimary": rules, "pricing": {"buyYesPriceUsd": int(ask * 1e6), "sellYesPriceUsd": int((ask - .01) * 1e6)}}


def test_bo3_mapping_and_lock_rule():
    ex = {"2-0": .40, "2-1": .25, "1-2": .20, "0-2": .15}
    c = _card(3, "2-0", ex)
    ev = {"markets": [
        _mk("M-0", "moneyline", "Alpha", .55), _mk("M-1", "moneyline", "Beta", .47),
        _mk("H-0", "map_handicap", "Alpha", .30, "-1.5", 'resolve to "Alpha" if Alpha wins 2 or more games than Beta'),
        _mk("H-1", "map_handicap", "Beta", .72, "-1.5", 'resolve to "Alpha" if Alpha wins 2 or more games than Beta'),
        _mk("T-0", "totals", "Over", .40, "2.5"), _mk("T-1", "totals", "Under", .62, "2.5")]}
    legs, best, _ = edge.evaluate(c, ev, {})
    by = {l["label"]: l for l in legs}
    assert by["AAA -1.5 maps"]["our_prob"] == pytest.approx(.40)
    assert by["BBB +1.5 maps"]["our_prob"] == pytest.approx(.60)
    assert by["Over 2.5 maps"]["our_prob"] == pytest.approx(.45)
    assert by["BBB +1.5 maps"]["cashes_on_lock"] is False      # dies on locked 2-0
    assert by["AAA -1.5 maps"]["cashes_on_lock"] is True
    assert best is not None and best["cashes_on_lock"] is True
    for pair in ("M", "H", "T"):
        ls = [l for l in legs if l["pair_id"] == pair]
        assert sum(l["fair_prob"] for l in ls) == pytest.approx(1, abs=1e-3)


def test_pass_when_only_anti_lock_markets_are_plus_ev():
    ex = {"2-0": .30, "2-1": .25, "1-2": .25, "0-2": .20}
    c = _card(3, "2-0", ex)
    ev = {"markets": [_mk("M-0", "moneyline", "Alpha", .80), _mk("M-1", "moneyline", "Beta", .22)]}
    legs, best, best_any = edge.evaluate(c, ev, {})
    assert best is None and best_any["label"] == "ML BBB"


def test_bo1_ignores_handicap_and_totals():
    c = _card(1, "1-0", {"1-0": .6, "0-1": .4})
    ev = {"markets": [_mk("M-0", "moneyline", "Alpha", .5), _mk("M-1", "moneyline", "Beta", .52),
                      _mk("H-0", "map_handicap", "Alpha", .3, "-1.5"), _mk("T-0", "totals", "Over", .4, "2.5")]}
    legs, best, _ = edge.evaluate(c, ev, {})
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
    for mod in ("forecast.py", "ratings.py", "sim.py", "schedule.py"):
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
