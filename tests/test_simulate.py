"""Simulate panel: export shape, page wiring, and the JS pricing core / worker (run under Node when available)."""
import json, shutil, subprocess, pathlib, pytest
from scanner import simexport, edge, dashboard, config

ROOT = pathlib.Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def test_leg_spec_maps_every_priced_leg():
    e = json.loads((ROOT / "edge_log/2026-10-28.v2.json").read_text())
    fc = json.loads((ROOT / "forecast_log/2026-10-28.v2.json").read_text())
    cards = {c["match_id"]: c for c in fc["cards"]}
    for r in e["series"]:
        c = cards[r["match_id"]]
        for l in r["legs"]:
            s = simexport.leg_spec(l, c["team_a"]["code"], c["team_b"]["code"])
            assert s and s["kind"] in ("ml", "hcp", "total", "game", "exact") and 0 < s["ask"] < 1


def test_simulate_page_static_and_secret_free(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOCS_DIR", tmp_path)
    dashboard.write_simulate_page()
    html = (tmp_path / "simulate.html").read_text()
    for a in ("sim-core.js", "simulate.js", "sim-worker.js"):
        assert (tmp_path / "assets" / a).exists()
    assert "RATINGS-BASED" in html and "frozen protocol lock" in html and "#01171a" in html
    js = "".join((tmp_path / "assets" / a).read_text() for a in ("sim-core.js", "simulate.js", "sim-worker.js"))
    for bad in ("DISCORD_BOT_TOKEN", "x-api-key", "LOLESPORTS_API_KEY", "fetch(\"http"):
        assert bad not in js


def test_sim_data_export_shape():
    d = json.loads((ROOT / "docs/data/sim_data.json").read_text())
    assert d["teams"] and all({"code", "name", "elo_v2", "elo_v1"} <= set(t) for t in d["teams"])
    assert {"side_blue_logit", "q_loser_blue", "rating_sd", "map_sd", "fee_coef"} <= set(d["params"])
    for date, ms in d["slate"].items():
        for m in ms:
            if m.get("frozen"):
                assert m["frozen"]["locked_score"] in m["frozen"]["exact"]
            else:   # listed so the slate never drops a series, but never carries a lock
                assert m.get("modeled") is False and m.get("status")


JS_TEST = r"""
const C = require(process.argv[1] + '/web/sim-core.js');
const assert = require('assert');
const near = (a, b, e) => assert(Math.abs(a - b) < (e || 1e-9), a + ' vs ' + b);
near(C.parsePrice('-150').p, 0.6); near(C.parsePrice('+130').p, 100 / 230); near(C.parsePrice('0.62').p, 0.62);
near(C.parsePrice('62%').p, 0.62); assert(C.parsePrice('abc') === null); assert(C.parsePrice('-150').american);
// outcome functions agree with scanner/edge.py (printed by python into CASES)
const cases = JSON.parse(process.argv[2]);
for (const c of cases) { const f = C.outcomeFn(c.mk); assert.strictEqual(f(c.a, c.b), c.want, JSON.stringify(c)); }
// evaluate: lock rule + best bet
const res = { n: 100, bo: 3, exact: { '2-0': 40, '2-1': 25, '1-2': 20, '0-2': 15 }, reach: [100, 100, 45], gwin: [60, 60, 25] };
const rows = [
  { kind: 'ml', side: 'A', price: '0.55', pair_id: 'M' }, { kind: 'ml', side: 'B', price: '0.47', pair_id: 'M' },
  { kind: 'hcp', side: 'B', line: 1.5, price: '0.30', pair_id: 'H' }, { kind: 'hcp', side: 'A', line: -1.5, price: '0.72', pair_id: 'H' },
  { kind: 'game', side: 'A', game: 3, price: '0.40' }];
const ev = C.evaluate(rows, res, 0.07);
near(ev.rows[0].q, 0.65); near(ev.rows[0].fair, 0.55 / 1.02); near(ev.rows[0].ev, 0.65 / (0.55 + 0.07 * 0.55 * 0.45) - 1);
near(ev.rows[2].q, 0.60); assert.strictEqual(ev.rows[2].cashesOnLock, false);
near(ev.rows[4].q, 0.45 * (25 / 45) + 0.55 * 0.5);
assert(ev.best && ev.best.cashesOnLock === true && ev.best.ev > 0);
assert.strictEqual(ev.lock.key, '2-0');
// worker: no uncertainty, no side -> analytic Bo3 at p=.6
global.self = { postMessage: m => { if (m.type === 'done') global.out = m; } };
require(process.argv[1] + '/web/sim-worker.js');
self.onmessage({ data: { bo: 3, base: [Math.log(1.5), Math.log(1.5), Math.log(1.5)], n: 200000, seed: 7, fearless: false, g1: 'rand',
  useSide: false, sideBlue: 0.17, qLoserBlue: 0.47, uncertainty: false, ratingSd: 0.35, mapSd: 0.2, fearlessExtra: 0 } });
const o = global.out; const tot = Object.values(o.exact).reduce((a, b) => a + b, 0); assert.strictEqual(tot, 200000);
near((o.exact["2-0"] + o.exact["2-1"]) / 200000, 0.648, 0.006); near(o.exact['2-0'] / 200000, 0.36, 0.006);
console.log('ok');
"""


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_js_core_matches_python():
    cases = []
    for bo in (1, 3, 5):
        need = bo // 2 + 1
        scores = [(need, y) for y in range(need)] + [(y, need) for y in range(need)]
        mks = [("ml", "A", None, None), ("ml", "B", None, None)]
        for L in (1.5, 2.5):
            mks += [("hcp", "A", -L, None), ("hcp", "B", L, None), ("hcp", "B", -L, None), ("hcp", "A", L, None)]
        for t in (1.5, 2.5, 3.5, 4.5):
            mks += [("total", "over", t, None), ("total", "under", t, None)]
        for g in range(1, bo + 1):
            mks += [("game", "A", None, g), ("game", "B", None, g)]
        for kind, side, line, g in mks:
            pk = {"ml": "moneyline", "hcp": "map_handicap", "game": "game"}.get(kind) or ("totals_over" if side == "over" else "totals_under")
            f = edge.outcome_fn(pk, side == "A", line, g, bo)
            for a, b in scores:
                want = f(a, b)
                cases.append({"mk": {"kind": kind, "side": side, "line": line, "game": g}, "a": a, "b": b, "want": want})
    r = subprocess.run([NODE, "-e", JS_TEST, str(ROOT), json.dumps(cases)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-2000:]
