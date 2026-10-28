"""Static dashboard (GitHub Pages ready): docs/index.html (latest) + docs/<date>.html (archive).
Forecast cards render from the frozen forecast; the edge panel renders separately and is labelled post-forecast."""
import csv, html, json, shutil
from pathlib import Path
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
                   f'<div class="tw"><table><tr><th>Draft</th><th>{E(A)} picks</th><th>{E(B)} picks</th><th>Seq. prob (bans+picks)</th><th>{E(A)} win</th></tr>{"".join(lines)}</table></div>'
                   f'<p class="muted">Most likely pick per role in the sims - {E(A)}: {marg}<br>{E(B)}: {margb}</p></details>')
    return '<div class="form"><b>Top drafts per map (beam search on the fitted draft model; sims sample the same model)</b>' + "".join(out) + "</div>"


def _img(u):
    u = u or ""
    return "https://" + u[len("http://"):] if u.startswith("http://") else u   # avoid mixed content on GitHub Pages


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
  <div class="t"><img src="{E(_img(A['img']))}" alt="{E(A['code'])} logo"><div><b>{E(A['name'])}</b><small>{E(A['code'])} · Elo {A['elo']:.0f} · event {E(A['event_record'])}</small></div><em>{_pct(s['p_series_a'])}</em></div>
  <div class="t"><img src="{E(_img(B['img']))}" alt="{E(B['code'])} logo"><div><b>{E(B['name'])}</b><small>{E(B['code'])} · Elo {B['elo']:.0f} · event {E(B['event_record'])}</small></div><em>{_pct(1 - s['p_series_a'])}</em></div>
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
            f'<div class="tw"><table><tr><th>Market</th><th>Price</th><th>No-vig</th><th>Our</th><th>Edge pp</th><th>EV/$1</th><th>Cashes on lock</th><th>Vol</th></tr>'
            + "".join(rows) + f'</table></div>{ex}<p class="muted"><a href="{E(r["jupiter_url"])}">{E(r["jupiter_title"])}</a></p></div>')


CSS = """
/* Luck Society palette: k-means on the @lucksociety X avatar + banner (see brand/README.md) */
:root{--bg:#01171a;--card:#052220;--card2:#0a2b27;--line:#1f341c;--ink:#efe6cc;--mut:#9fb3a8;
--gold:#d79d07;--gold-hi:#f6d12b;--bronze:#a9700a;--clover:#036718;--clover-hi:#1b9625;--pos:#3fbf4f;--neg:#ff6b5b;--black:#040402}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.brand{position:relative;background:var(--black) url(assets/luck-society-banner.jpg) center/cover no-repeat;border-bottom:2px solid var(--gold)}
.brand .ov{background:linear-gradient(90deg,rgba(1,23,26,.96) 0%,rgba(1,23,26,.82) 55%,rgba(1,23,26,.35) 100%);padding:22px 18px}
.bi{max-width:980px;margin:auto;display:flex;gap:16px;align-items:center}
.logo{width:72px;height:72px;border-radius:50%;border:2px solid var(--gold);flex:none;background:var(--black)}
h1{margin:0;font:700 26px/1.15 Georgia,"Times New Roman",serif;letter-spacing:.08em;text-transform:uppercase;color:var(--gold-hi)}
.tag{margin:4px 0 0;color:var(--gold);font-size:13px;letter-spacing:.04em}.voice{margin:6px 0 0;color:var(--ink);font-size:14px}
.meta{max-width:980px;margin:12px auto 0;color:var(--mut);font-size:13px}.meta p{margin:4px 0}.meta b{color:var(--gold-hi)}
main{max-width:980px;margin:auto;padding:14px}
.card{background:var(--card);border:1px solid var(--line);border-top:3px solid var(--gold);border-radius:14px;padding:16px;margin:16px 0}
h2{font:700 16px/1.3 Georgia,"Times New Roman",serif;letter-spacing:.06em;text-transform:uppercase;color:var(--gold-hi);margin:0 0 8px}
.hdr{display:flex;flex-wrap:wrap;gap:4px 12px;justify-content:space-between;color:var(--mut);font-size:13px}
.teams{display:grid;gap:8px;margin:12px 0}.t{display:flex;align-items:center;gap:12px}
.t img{width:38px;height:38px;object-fit:contain}.t div{flex:1;min-width:0}.t small{display:block;color:var(--mut)}
.t em{font-style:normal;font-size:24px;font-weight:700;color:var(--gold-hi);font-family:Georgia,serif}
.lock{background:var(--card2);border-left:4px solid var(--gold);padding:8px 10px;border-radius:6px}.lock b{color:var(--gold-hi)}
.dist{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:6px;margin:10px 0}
.sc{position:relative;background:var(--card2);border:1px solid var(--line);border-radius:6px;padding:6px 8px;overflow:hidden}
.sc span{float:right}.sc i{position:absolute;left:0;bottom:0;height:3px;background:linear-gradient(90deg,var(--bronze),var(--gold-hi))}
.derived,.form{color:var(--mut);font-size:13px;margin:6px 0}.form b{color:var(--ink)}
.f{display:inline-block;padding:1px 6px;border-radius:4px;margin:2px 1px;font-size:12px;color:var(--ink)}
.f.w{background:var(--clover)}.f.l{background:#5a1f1a}.f.p{background:#4a3a0c}
.integrity{font-size:12px;color:var(--mut);border-top:1px dashed var(--line);margin-top:10px;padding-top:8px}.integrity b{color:var(--gold-hi)}
.edge{margin-top:14px;border:1px dashed var(--bronze);border-radius:10px;padding:10px;background:#03191a}
.eh{font-size:12px;letter-spacing:.05em;color:var(--gold-hi);font-weight:700}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
th,td{padding:5px 6px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}th{color:var(--gold);font-weight:600}
th:first-child,td:first-child{text-align:left}
tr.pos td{color:var(--pos)}tr.best td{background:#0d3a1a;font-weight:700}.bet{color:var(--pos);font-weight:700}.pass{color:var(--gold-hi);font-weight:700}
.warn{color:#ffb86b;font-size:13px}.muted{color:var(--mut);font-size:12px}a{color:var(--gold-hi)}
details{margin-top:6px;color:var(--mut);font-size:13px}summary{cursor:pointer;color:var(--ink)}
footer{color:var(--mut);font-size:12px;padding:20px;text-align:center;border-top:1px solid var(--line)}
@media (max-width:560px){.brand .ov{background:rgba(1,23,26,.9)}h1{font-size:20px}.logo{width:54px;height:54px}.t em{font-size:20px}.card{padding:12px;border-radius:10px}main{padding:8px}td,th{padding:4px}}
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
    bt = ""
    btp = config.ROOT / "calibration" / "backtest_v1_vs_v2.json" if hasattr(config, "ROOT") else Path(__file__).resolve().parent.parent / "calibration" / "backtest_v1_vs_v2.json"
    if is_v2 and btp.exists():
        b = json.loads(btp.read_text())
        names = [("v1", "v1"), ("v2_elo_only", "v2 ratings only"), ("v2", "v2 full (draft sims)")]
        trs = "".join(f"<tr><td>{n}</td><td>{b[k]['brier']:.4f}</td><td>{b[k]['logloss']:.4f}</td><td>{b[k]['winner_acc']:.1%}</td><td>{b[k]['exact_lock_hit']:.1%}</td></tr>" for k, n in names if k in b)
        verdict = ("v2 full does NOT beat v1 on Brier or log loss; it is better on winner accuracy and exact-lock hit. "
                   "Patch+lineup ratings alone beat v1 on every metric; the draft layer worsens calibration.") \
            if b["v2"]["brier"] > b["v1"]["brier"] else "v2 full beats v1 on Brier."
        bt = (f'<section class="card"><h2>Walk-forward backtest v1 vs v2 ({b["v1"]["series"]} international series, same set)</h2>'
              f'<div class="tw"><table><tr><th>model</th><th>Brier</th><th>log loss</th><th>winner acc</th><th>exact-lock hit</th></tr>{trs}</table></div>'
              f'<p><b>{E(verdict)}</b></p></section>')
    edge_meta = (f"Odds snapshot {E(edge['odds_fetched_at_et'])} · coverage {E(edge['coverage'])}" if edge
                 else "Edge layer not run")
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Luck Society · LoL Scanner · {E(date)}</title><meta name="theme-color" content="#01171a"><link rel="icon" href="assets/luck-society-avatar.jpg"><style>{CSS}</style></head><body>
<header class="brand"><div class="ov"><div class="bi"><img class="logo" src="assets/luck-society-avatar.jpg" alt="Luck Society clover coin logo">
<div><h1>Luck Society · LoL Scanner</h1><p class="tag">Sports Betting • Crypto • Stocks • Predictions 🍀</p>
<p class="voice">{E(date)} slate 🔒 Score locked before we look at a single price. We only back a bet that cashes on the lock. Let's stay green 🍀</p></div></div>
<div class="meta"><p>{E(fc.get('model_version', 'elo-map-v1'))} forecast frozen {E(fc['frozen_at_et'])} · sha256 {E(fc['sha256'][:16])}… · {E(fc['integrity']['protocol'])} · market inputs to forecast: <b>NONE</b></p>
<p>{edge_meta}. Edge panels are post-forecast and labelled as such. {E(calib)}</p></div></div></header>
<main>{cards}{bt}</main>
<footer>Forecast: {"model v2 = patch-recency map Elo + roster continuity + draft-path Monte Carlo (Fearless enforced), data: lolesports, gol.gg, Data Dragon" if is_v2 else "results-only map Elo + Monte Carlo series sim (lolesports data)"}. Edge: Jupiter asks, de-vigged proportionally per binary pair, fee {E(edge['fee_model'] if edge else '')}. BEST BET = highest +EV market that also cashes on the locked exact score; otherwise PASS. Not financial advice.</footer>
</body></html>"""
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (config.DOCS_DIR / f"{date}.html").write_text(doc)
    (config.DOCS_DIR / "index.html").write_text(doc)
    (config.DOCS_DIR / ".nojekyll").write_text("")
    src = Path(__file__).resolve().parent.parent / "brand"
    (config.DOCS_DIR / "assets").mkdir(exist_ok=True)
    for f in ("luck-society-avatar.jpg", "luck-society-banner.jpg"):
        if (src / f).exists():
            shutil.copyfile(src / f, config.DOCS_DIR / "assets" / f)
    return config.DOCS_DIR / "index.html"
