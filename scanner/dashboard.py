"""Static dashboard (GitHub Pages ready): docs/index.html (latest) + docs/<date>.html (archive).
Forecast cards render from the frozen forecast; the edge panel renders separately and is labelled post-forecast."""
import csv, html, json
from . import config
from .forecast import load_frozen, frozen_path

E = html.escape


def _pct(x):
    return f"{100 * x:.1f}%"


def _bar(exact, a, b):
    cells = []
    for k, v in sorted(exact.items(), key=lambda kv: (-int(kv[0].split('-')[0]) + int(kv[0].split('-')[1]))):
        x, y = k.split("-")
        who = a if int(x) > int(y) else b
        cells.append(f'<div class="sc"><b>{E(who)} {max(int(x), int(y))}-{min(int(x), int(y))}</b><span>{_pct(v)}</span>'
                     f'<i style="width:{min(100, v * 220):.0f}%"></i></div>')
    return "".join(cells)


def _form(f):
    return " ".join(f'<span class="f {"w" if x["won"] else "l"}" title="{E(x["date"])} {E(x["league"])}">'
                    f'{E(x["opp"])} {E(x["score"])}</span>' for x in f[:6])


def _lineups(c):
    A, B = c["team_a"], c["team_b"]
    if "lineup" not in A:
        return ""
    tag = {"CONFIRMED": "w", "PROBABLE": "p", "UNCERTAIN": "l"}
    rows = []
    for t in (A, B):
        cells = "".join(f'<span class="f {tag[x["status"]]}" title="{E(x["status"])} · started {x["starts_this_event"]}/{x["event_games"]} event games">'
                        f'{E(x["role"][:3])} {E(x["name"])} · {E(x["status"])}</span>' for x in t["lineup"])
        rows.append(f"<div>{E(t['code'])}: {cells}</div>")
    combos = c["model"].get("lineup_combos", [])
    extra = "" if len(combos) <= 1 else '<div class="muted">Simulated lineups: ' + " · ".join(
        f'{E(x["a"])} / {E(x["b"])} ({_pct(x["w"])})' for x in combos) + "</div>"
    return f'<div class="form"><b>Expected lineups</b>{"".join(rows)}{extra}</div>'


def _drafts(c):
    if not c.get("drafts"):
        return ""
    A, B = c["team_a"]["code"], c["team_b"]["code"]
    out = []
    for g in c["drafts"]:
        lines = []
        for i, d in enumerate(g["top_drafts"][:3]):
            lines.append(f'<tr><td>#{i + 1}</td><td>{E(", ".join(d["picks"][A]))}</td><td>{E(", ".join(d["picks"][B]))}</td>'
                         f'<td>{d["seq_prob"]:.1e}</td><td>{_pct(d["win_a"])}</td></tr>')
        marg = " · ".join(f'{E(r[:3])} {E(v[0]["champ"])} {_pct(v[0]["p"])}' for r, v in g["role_pick_marginals"][A].items())
        margb = " · ".join(f'{E(r[:3])} {E(v[0]["champ"])} {_pct(v[0]["p"])}' for r, v in g["role_pick_marginals"][B].items())
        out.append(f'<details><summary>Game {g["game"]} · reached {_pct(g["p_reached"])} · {E(A)} win {_pct(g["p_win_a_sim"])} (sim) · '
                   f'{E(g["assumed_sides"])} on modal path · Fearless-locked before: {g["fearless_locked_before"]}</summary>'
                   f'<table><tr><th>Draft</th><th>{E(A)} picks</th><th>{E(B)} picks</th><th>Seq. prob (bans+picks)</th><th>{E(A)} win</th></tr>{"".join(lines)}</table>'
                   f'<p class="muted">Most likely pick per role in the sims - {E(A)}: {marg}<br>{E(B)}: {margb}</p></details>')
    return '<div class="form"><b>Top drafts per map (beam search on the fitted draft model; sims sample the same model)</b>' + "".join(out) + "</div>"


def _card(c, edge_row, v1=None):
    A, B, s = c["team_a"], c["team_b"], c["sim"]
    A.setdefault("elo", A.get("elo_v2", 0)); B.setdefault("elo", B.get("elo_v2", 0))
    la, lb = map(int, s["locked_score"].split("-"))
    lock_team = A["code"] if la > lb else B["code"]
    need = c["bo"] // 2 + 1
    derived = []
    if c["bo"] >= 3:
        pa = s["p_series_a"]
        cover_a = sum(v for k, v in s["exact"].items() if int(k.split('-')[0]) - int(k.split('-')[1]) >= 2)
        cover_b = sum(v for k, v in s["exact"].items() if int(k.split('-')[1]) - int(k.split('-')[0]) >= 2)
        tl = need * 2 - 1.5 if c["bo"] == 3 else None
        derived.append(f"{A['code']} -1.5: {_pct(cover_a)} · {B['code']} -1.5: {_pct(cover_b)}")
        if c["bo"] == 3:
            over = s["maps_dist"].get("3", 0)
            derived.append(f"Over 2.5 maps: {_pct(over)} · Under 2.5: {_pct(1 - over)}")
        else:
            o35 = s["maps_dist"].get("4", 0) + s["maps_dist"].get("5", 0)
            derived.append(f"Over 3.5: {_pct(o35)} · Over 4.5: {_pct(s['maps_dist'].get('5', 0))}")
    flags = "".join(f"<li>{E(f)}</li>" for f in c["adversarial_review"])
    out = f"""
<section class="card">
 <div class="hdr"><span class="lg">{E(c['league'])} · {E(c['stage'])} · Bo{c['bo']}</span><span>{E(c['start_et'])}</span></div>
 <div class="teams">
  <div class="t"><img src="{E(A['img'] or '')}" alt=""><div><b>{E(A['name'])}</b><small>{E(A['code'])} · Elo {A['elo']:.0f} · event {E(A['event_record'])}</small></div><em>{_pct(s['p_series_a'])}</em></div>
  <div class="t"><img src="{E(B['img'] or '')}" alt=""><div><b>{E(B['name'])}</b><small>{E(B['code'])} · Elo {B['elo']:.0f} · event {E(B['event_record'])}</small></div><em>{_pct(1 - s['p_series_a'])}</em></div>
 </div>
 <div class="lock">LOCKED EXACT SCORE: <b>{E(lock_team)} {max(la, lb)}-{min(la, lb)}</b> ({_pct(s['locked_score_prob'])}) · map win {A['code']} {_pct(c['model']['p_map_a'])} · E[maps] {s['e_maps']:.2f}</div>
 <div class="dist">{_bar(s['exact'], A['code'], B['code'])}</div>
 <div class="derived">{'<br>'.join(E(d) for d in derived)}</div>
 <div class="form"><div>{E(A['code'])}: {_form(A['form'])}</div><div>{E(B['code'])}: {_form(B['form'])}</div></div>
 {_lineups(c)}
 {_drafts(c)}
 {_v1cmp(c, v1)}
 <details><summary>Adversarial review / disclosed limitations</summary><ul>{flags}</ul></details>
 <div class="integrity">Integrity: market inputs to this forecast = <b>NONE</b> · {s['sims']:,} sims · {_model_line(c)}</div>
 {_edge_panel(c, edge_row)}
</section>"""
    return out


def _model_line(c):
    m = c["model"]
    if "train_maps" in m:
        return f"results-only Elo ({m['train_maps']:,} maps, cutoff {E(m['history_cutoff_utc'])})"
    return (f"model v2: patch {E(m['patch'])} · Fearless {'on' if m['fearless'] else 'off'} · G1 side: {E(m['g1_side_source'])} · "
            f"cutoff {E(m['history_cutoff_utc'])}")


def _v1cmp(c, v1):
    if not v1:
        return ""
    s = v1["sim"]
    return (f'<div class="muted">v1 (frozen earlier, results-only Elo): {E(c["team_a"]["code"])} {_pct(s["p_series_a"])} · '
            f'lock {E(s["locked_score"])} ({_pct(s["locked_score_prob"])})</div>')


def _edge_panel(c, r):
    head = '<div class="edge"><div class="eh">POST-FORECAST EDGE PANEL · Jupiter prediction markets · computed after the forecast froze, never fed back</div>'
    if r is None:
        return head + '<p class="muted">Edge layer not run yet.</p></div>'
    if r["status"] != "ok":
        return head + '<p class="pass">no Jupiter market</p></div>'
    rows = []
    for l in r["legs"]:
        lk = {True: "✔", False: "✘", None: "–"}[l["cashes_on_lock"]]
        cls = "pos" if l["ev_per_$1"] > 0 else ""
        best = " best" if r["best_bet"] and l["market_id"] == r["best_bet"]["market_id"] else ""
        rows.append(f'<tr class="{cls}{best}"><td>{E(l["label"])}</td><td>{l["ask"]:.2f}</td>'
                    f'<td>{_pct(l["fair_prob"])}</td><td>{_pct(l["our_prob"])}</td><td>{l["edge_pct"]:+.1f}</td>'
                    f'<td>{l["ev_per_$1"]:+.3f}</td><td>{lk}</td><td>{l["volume"] or 0:,}</td></tr>')
    warn = "".join(f'<p class="warn">⚠ {E(w)}</p>' for w in r.get("warnings", []))
    ex = "" if r.get("exact_score_offered") else '<p class="muted">Exact-score market: not offered on Jupiter for this series.</p>'
    verdict_cls = "bet" if r["best_bet"] else "pass"
    return (head + f'<p class="{verdict_cls}">{E(r["verdict"])}</p>{warn}'
            f'<table><tr><th>Market</th><th>Price</th><th>No-vig</th><th>Our</th><th>Edge pp</th><th>EV/$1</th><th>Cashes on lock</th><th>Vol</th></tr>'
            + "".join(rows) + f'</table>{ex}<p class="muted"><a href="{E(r["jupiter_url"])}">{E(r["jupiter_title"])}</a></p></div>')


CSS = """
:root{--bg:#0b0d12;--card:#141824;--ink:#e8ecf3;--mut:#8a93a6;--acc:#c9a227;--pos:#2ecc71;--neg:#e74c3c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
header{padding:22px 18px;border-bottom:1px solid #222a3b}h1{margin:0;font-size:22px;color:var(--acc)}header p{margin:6px 0 0;color:var(--mut)}
main{max-width:980px;margin:auto;padding:14px}.card{background:var(--card);border:1px solid #232b3d;border-radius:14px;padding:16px;margin:16px 0}
.hdr{display:flex;justify-content:space-between;color:var(--mut);font-size:13px}.teams{display:grid;gap:8px;margin:12px 0}
.t{display:flex;align-items:center;gap:12px}.t img{width:38px;height:38px;object-fit:contain}.t div{flex:1}.t small{display:block;color:var(--mut)}
.t em{font-style:normal;font-size:22px;font-weight:700}.lock{background:#1d2333;border-left:4px solid var(--acc);padding:8px 10px;border-radius:6px}
.dist{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px;margin:10px 0}.sc{position:relative;background:#1a2030;border-radius:6px;padding:6px 8px;overflow:hidden}
.sc span{float:right}.sc i{position:absolute;left:0;bottom:0;height:3px;background:var(--acc)}.derived,.form{color:var(--mut);font-size:13px;margin:6px 0}
.f{display:inline-block;padding:1px 6px;border-radius:4px;margin:1px;font-size:12px}.f.w{background:#173d2a}.f.l{background:#4a1d1d}.f.p{background:#3d3517}
.integrity{font-size:12px;color:var(--mut);border-top:1px dashed #2a3347;margin-top:10px;padding-top:8px}
.edge{margin-top:14px;border:1px dashed #3b4660;border-radius:10px;padding:10px;background:#10131b}.eh{font-size:12px;letter-spacing:.04em;color:#7fb3ff;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}th,td{padding:4px 6px;text-align:right;border-bottom:1px solid #1f2637}th:first-child,td:first-child{text-align:left}
tr.pos td{color:var(--pos)}tr.best td{background:#26301a;font-weight:700}.bet{color:var(--pos);font-weight:700}.pass{color:var(--acc);font-weight:700}
.warn{color:#ffb86b;font-size:13px}.muted{color:var(--mut);font-size:12px}a{color:#7fb3ff}details{margin-top:6px;color:var(--mut);font-size:13px}
footer{color:var(--mut);font-size:12px;padding:20px;text-align:center}
"""


def render(date, version=None):
    if version and not frozen_path(date, version).exists():
        version = None   # e.g. v2 not frozen for an old date -> fall back to whatever is frozen
    if not frozen_path(date, version).exists():
        raise SystemExit(f"no frozen forecast for {date}")
    fc = load_frozen(date, version)
    is_v2 = str(fc.get("model_version", "")).startswith("v2")
    v1 = {}
    if is_v2 and frozen_path(date, "v1").exists():
        v1 = {c["match_id"]: c for c in load_frozen(date, "v1")["cards"]}
    ep = config.EDGE_DIR / f"{date}{'.v2' if is_v2 else ''}.json"
    edge = json.loads(ep.read_text()) if ep.exists() else None
    erows = {r["match_id"]: r for r in (edge or {}).get("series", [])}
    cards = "".join(_card(c, erows.get(c["match_id"]) if edge else None, v1.get(c["match_id"])) for c in fc["cards"]) or \
        '<section class="card"><p>No series on the official schedule for this date.</p></section>'
    calib = ""
    if config.CALIB_FILE.exists():
        rows = list(csv.DictReader(open(config.CALIB_FILE)))
        if rows:
            n = len(rows); br = sum(float(r["brier"]) for r in rows) / n; ll = sum(float(r["logloss"]) for r in rows) / n
            hit = sum(int(r["lock_hit"]) for r in rows) / n
            calib = f"Calibration log: {n} graded series · Brier {br:.3f} · log loss {ll:.3f} · exact-lock hit {hit:.0%}"
    edge_meta = (f"Odds snapshot {E(edge['odds_fetched_at_et'])} · coverage {E(edge['coverage'])}" if edge
                 else "Edge layer not run")
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LoL Scanner · {E(date)}</title><style>{CSS}</style></head><body>
<header><h1>LoL Esports Scanner · {E(date)}</h1>
<p>{E(fc.get('model_version', 'elo-map-v1'))} forecast frozen {E(fc['frozen_at_et'])} · sha256 {E(fc['sha256'][:16])}… · {E(fc['integrity']['protocol'])} · market inputs to forecast: <b>NONE</b></p>
<p>{edge_meta}. Edge panels are post-forecast and labelled as such. {E(calib)}</p></header>
<main>{cards}</main>
<footer>Forecast: {"model v2 = patch-recency map Elo + roster continuity + draft-path Monte Carlo (Fearless enforced), data: lolesports, gol.gg, Data Dragon" if is_v2 else "results-only map Elo + Monte Carlo series sim (lolesports data)"}. Edge: Jupiter asks, de-vigged proportionally per binary pair, fee {E(edge['fee_model'] if edge else '')}. BEST BET = highest +EV market that also cashes on the locked exact score; otherwise PASS. Not financial advice.</footer>
</body></html>"""
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (config.DOCS_DIR / f"{date}.html").write_text(doc)
    (config.DOCS_DIR / "index.html").write_text(doc)
    (config.DOCS_DIR / ".nojekyll").write_text("")
    return config.DOCS_DIR / "index.html"
