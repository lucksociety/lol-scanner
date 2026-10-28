"""Static dashboard (GitHub Pages ready): docs/index.html (latest) + docs/<date>.html (archive).
Forecast cards render from the frozen forecast; the edge panel renders separately and is labelled post-forecast."""
import csv, html, json, shutil
from pathlib import Path
from . import config
from . import edgeview as ev
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


def _card(c, edge_row, v1=None, snap_et=""):
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
 <div class="hdr"><span class="lg">{E(c['league'])} · {E(c['stage'])} · Bo{c['bo']}</span><span>{E(c['start_et'])} {_simbtn(c, c.get('_date', ''))}</span></div>
 <div class="teams">
  <div class="t"><img src="{E(_img(A['img']))}" alt="{E(A['code'])} logo"><div><b>{E(A['name'])}</b><small>{E(A['code'])} · Elo {A['elo']:.0f} · event {E(A['event_record'])}</small></div><em>{_pct(s['p_series_a'])}</em></div>
  <div class="t"><img src="{E(_img(B['img']))}" alt="{E(B['code'])} logo"><div><b>{E(B['name'])}</b><small>{E(B['code'])} · Elo {B['elo']:.0f} · event {E(B['event_record'])}</small></div><em>{_pct(1 - s['p_series_a'])}</em></div>
 </div>
 <div class="lock">LOCKED EXACT SCORE: <b>{E(lock_team)} {max(la, lb)}-{min(la, lb)}</b> ({_pct(s['locked_score_prob'])}) · map win {A['code']} {_pct(c['model']['p_map_a'])} · E[maps] {s['e_maps']:.2f}</div>
 <div class="dist">{_bar(s['exact'], A['code'], B['code'])}</div>
 <div class="derived">{'<br>'.join(E(d) for d in derived)}</div>
 {_edge_panel(c, edge_row, snap_et)}
 <div class="form"><div>{E(A['code'])}: {_form(A['form'])}</div><div>{E(B['code'])}: {_form(B['form'])}</div></div>
 {_lineups(c)}
 {_drafts(c)}
 {_v1cmp(c, v1)}
 <details><summary>Adversarial review / disclosed limitations</summary><ul>{flags}</ul></details>
 <div class="integrity">Integrity: market inputs to this forecast = <b>NONE</b> · {s['sims']:,} sims · {_model_line(c)}</div>
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


def _p(x):
    return "–" if x is None else _pct(x)


def _edge_panel(c, r, snap_et=""):
    head = ('<div class="edge"><div class="eh">POST-FORECAST EDGE PANEL · Jupiter prediction markets · computed after the forecast froze, '
            f'never fed back{(" · odds snapshot " + E(snap_et)) if snap_et else ""}</div>')
    if r is None:
        return head + '<p class="muted">Edge layer not run yet.</p></div>'
    if r["status"] != "ok":
        return head + '<p class="pass">PASS · no Jupiter market for this series</p></div>'
    rows = []
    for l in ev.enrich(r, c):
        lk = {True: "✓", False: "✗", None: "–"}[l["cashes_on_lock"]]
        cls = ("pos" if l["ev_per_$1"] > 0 else "neg") + (" best" if l["is_best"] else "")
        tag = ' <span class="bb">BEST BET</span>' if l["is_best"] else (' <span class="nl">+EV, dies on lock</span>' if l["is_best_any"] else "")
        rows.append(f'<tr class="{cls}"><td>{E(l["market"])}{tag}</td><td>{E(l["side"])}</td><td>{l["ask"]:.2f}</td><td>{_pct(l["implied"])}</td>'
                    f'<td>{_pct(l["fair_prob"])}</td><td>{_pct(l["our_prob"])}</td><td>{_p(l["ratings_only_prob"])}</td><td>{l["edge_pct"]:+.1f}</td>'
                    f'<td>{l["ev_per_$1"]:+.3f}</td><td class="lk">{lk}</td><td>{l["volume"] or 0:,}</td></tr>')
    warn = "".join(f'<p class="warn">⚠ {E(w)}</p>' for w in r.get("warnings", []))
    ex = "" if r.get("exact_score_offered") else '<p class="muted">Exact-score market: not offered on Jupiter for this series.</p>'
    ex += _not_modeled(r)
    b, a = r.get("best_bet"), r.get("best_ev_any")
    if b:
        m, sd = ev.market_side(b)
        verdict = (f'<div class="verdict bet">BEST BET · {E(m)} · {E(sd)} @ {b["ask"]:.2f} · our {_pct(b["our_prob"])} vs no-vig {_pct(b["fair_prob"])} · '
                   f'edge {b["edge_pct"]:+.1f} pp · EV {b["ev_per_$1"]:+.3f}/$1 · cashes on lock ✓</div>')
    else:
        verdict = '<div class="verdict pass">PASS · no lock-compatible Jupiter market has +EV</div>'
    sec = ""
    if a and (not b or a["market_id"] != b["market_id"]):
        m, sd = ev.market_side(a)
        sec = (f'<p class="secondary">Secondary (ignores the lock rule, NOT a recommendation): best +EV overall = {E(m)} · {E(sd)} @ {a["ask"]:.2f} · '
               f'EV {a["ev_per_$1"]:+.3f}/$1 · cashes on lock {"✓" if a["cashes_on_lock"] else "✗"}</p>')
    return (head + verdict + sec + warn +
            '<div class="tw"><table class="mk"><tr><th>Market</th><th>Side</th><th>Price</th><th>Implied</th><th>No-vig</th><th>Our sim (v2)</th>'
            '<th>Ratings-only</th><th>Edge pp</th><th>EV/$1</th><th>Cashes on lock</th><th>Vol</th></tr>'
            + "".join(rows) + '</table></div>'
            '<p class="muted">Sorted by EV per $1 (fee 0.07·p·(1−p) included). Implied = raw ask; No-vig = ask normalised within its binary pair. '
            'Our sim = v2 draft-path exact-score distribution; Ratings-only = v2 patch+lineup Elo without the draft layer (comparison only). '
            f'BEST BET = top +EV market that also cashes on the locked score.</p>{ex}'
            f'<p class="muted"><a href="{E(r["jupiter_url"])}">{E(r["jupiter_title"])}</a></p></div>')


def _not_modeled(r):
    """Every Jupiter market the sim cannot price is listed (never silently dropped)."""
    inv = r.get("market_inventory") or {}
    out = ""
    if inv:
        parts = [f'{E(t)} ×{v["markets"]}' + (f' (lines {E(", ".join(v["lines"]))})' if v["lines"] else "")
                 + (f' (games {", ".join(map(str, v["games"]))})' if v["games"] else "") for t, v in inv.items()]
        out += (f'<p class="muted">Jupiter market inventory: {r.get("n_markets", 0)} markets · {r.get("n_priced_legs", 0)} legs priced from the sim · '
                f'{len(r.get("not_modeled") or [])} not modeled · ' + " · ".join(parts) + '</p>')
    nm = r.get("not_modeled") or []
    if nm:
        p = lambda x: "–" if not x else f"{x:.2f}"
        rows = "".join(f'<tr><td>{E(x["label"])}</td><td>{E(x["type"])}</td><td>{p(x["yes_ask"])}</td><td>{p(x["no_ask"])}</td>'
                       f'<td>{x.get("volume") or 0:,}</td><td>{E(x["reason"])}</td></tr>' for x in nm)
        out += (f'<details><summary>Not modeled: {len(nm)} Jupiter prop markets (listed, not priced)</summary><div class="tw"><table class="mk">'
                '<tr><th>Market</th><th>Type</th><th>Yes ask</th><th>No ask</th><th>Vol</th><th>Why not priced</th></tr>'
                + rows + '</table></div></details>')
    for u in r.get("unpriced") or []:
        out += f'<p class="muted">Unpriced: {E(u["label"])} ({E(u["reason"])})</p>'
    return out


def _simbtn(c, date):
    return (f'<a class="simbtn" href="simulate.html?match={E(c["match_id"])}&amp;date={E(date)}" '
            f'title="Open the in-browser ratings sim + edge finder preloaded with this series">Simulate</a>')


def _slate(fc, erows, edge):
    trs = []
    for c in fc["cards"]:
        A, B, s = c["team_a"], c["team_b"], c["sim"]
        la, lb = map(int, s["locked_score"].split("-"))
        lock = f'{A["code"] if la > lb else B["code"]} {max(la, lb)}-{min(la, lb)}'
        win = f'{A["code"]} {_pct(s["p_series_a"])} · {B["code"]} {_pct(1 - s["p_series_a"])}'
        bs = ev.best_summary(erows.get(c["match_id"])) if edge else {"pass": True, "reason": "edge layer not run"}
        if bs["pass"]:
            bet = f'<td class="pass">PASS</td><td colspan="5" class="muted">{E(bs["reason"])}</td>'
        else:
            bet = (f'<td class="bet">{E(bs["market"])} · {E(bs["side"])}</td><td>{bs["ask"]:.2f}</td><td>{_pct(bs["our_prob"])}</td>'
                   f'<td>{_pct(bs["fair_prob"])}</td><td>{bs["edge_pct"]:+.1f}</td><td>{bs["ev_per_$1"]:+.3f}</td>')
        trs.append(f'<tr><td>{E(c["start_et"])}</td><td>{E(c["league"])}</td><td>{E(A["code"])} vs {E(B["code"])} <span class="bo">Bo{c["bo"]}</span></td>'
                   f'<td><b>{E(lock)}</b> ({_pct(s["locked_score_prob"])})</td><td>{win}</td>{bet}'
                   f'<td>{_simbtn(c, fc.get("date", ""))}</td></tr>')
    snap = f' · odds snapshot <b>{E(edge["odds_fetched_at_et"])}</b>' if edge else ""
    return ('<section class="card slate"><h2>Slate · locked score + Jupiter best bet</h2>'
            f'<p class="eh">POST-FORECAST: Jupiter prices read only after the forecast froze; they never feed back into it{snap}</p>'
            '<div class="tw"><table><tr><th>Time (ET)</th><th>League</th><th>Matchup</th><th>Lock (exact %)</th><th>Series win</th>'
            '<th>Best bet (market · side)</th><th>Jupiter price</th><th>Our prob</th><th>No-vig mkt</th><th>Edge pp</th><th>EV/$1</th><th>Sim</th></tr>'
            + "".join(trs) + '</table></div><p class="muted">PASS = no Jupiter market that cashes on the locked exact score has +EV after fees.</p></section>')


CSS = """
/* Luck Society palette: k-means on the @lucksociety X avatar + banner (see brand/README.md) */
:root{--bg:#01171a;--card:#052220;--card2:#0a2b27;--line:#1f341c;--ink:#efe6cc;--mut:#9fb3a8;
--gold:#d79d07;--gold-hi:#f6d12b;--bronze:#a9700a;--clover:#036718;--clover-hi:#1b9625;--pos:#3fbf4f;--neg:#ff6b5b;--black:#040402}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.brand{position:relative;background:var(--black) url(assets/luck-society-banner.jpg) center/cover no-repeat;border-bottom:2px solid var(--gold)}
.brand .ov{background:linear-gradient(90deg,rgba(1,23,26,.96) 0%,rgba(1,23,26,.82) 55%,rgba(1,23,26,.35) 100%);padding:22px 18px}
.bi{max-width:1200px;margin:auto;display:flex;gap:16px;align-items:center}
.logo{width:72px;height:72px;border-radius:50%;border:2px solid var(--gold);flex:none;background:var(--black)}
h1{margin:0;font:700 26px/1.15 Georgia,"Times New Roman",serif;letter-spacing:.08em;text-transform:uppercase;color:var(--gold-hi)}
.tag{margin:4px 0 0;color:var(--gold);font-size:13px;letter-spacing:.04em}.voice{margin:6px 0 0;color:var(--ink);font-size:14px}
.meta{max-width:1200px;margin:12px auto 0;color:var(--mut);font-size:13px}.meta p{margin:4px 0}.meta b{color:var(--gold-hi)}
main{max-width:1200px;margin:auto;padding:14px}
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
tr.pos td{color:var(--pos)}tr.neg td{color:var(--mut)}td.lk{font-weight:700}.bb{background:var(--gold);color:var(--black);border-radius:4px;padding:0 5px;font-size:11px;margin-left:4px}.nl{border:1px solid var(--bronze);color:var(--gold);border-radius:4px;padding:0 5px;font-size:11px;margin-left:4px}.bo{background:var(--clover);color:var(--ink);border-radius:4px;padding:0 5px;font-size:11px}.verdict{margin:8px 0;padding:8px 10px;border-radius:6px;background:var(--card2);border-left:4px solid var(--gold)}.verdict.bet{border-left-color:var(--pos)}.secondary{color:var(--mut);font-size:12px;font-style:italic}.slate td.pass{font-size:15px}tr.best td{background:#0d3a1a;font-weight:700}.bet{color:var(--pos);font-weight:700}.pass{color:var(--gold-hi);font-weight:700}
.simbtn{display:inline-block;background:var(--gold);color:var(--black)!important;font-weight:700;text-decoration:none;border-radius:6px;padding:2px 10px;font-size:12px;margin-left:6px}.simbtn:hover{background:var(--gold-hi)}
.nav{max-width:1200px;margin:10px auto 0}.nav a{margin-right:8px}
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
    snap = edge["odds_fetched_at_et"] if edge else ""
    for c in fc["cards"]:
        c["_date"] = date
    cards = "".join(_card(c, erows.get(c["match_id"]) if edge else None, v1.get(c["match_id"]), snap) for c in fc["cards"]) or \
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
<p>{edge_meta}. Edge panels are post-forecast and labelled as such. {E(calib)}</p></div>
<div class="nav"><a class="simbtn" href="simulate.html">🎲 Simulate: ratings sim + edge finder</a></div></div></header>
<main>{_slate(fc, erows, edge) if fc['cards'] else ''}{cards}{bt}</main>
<footer>Forecast: {"model v2 = patch-recency map Elo + roster continuity + draft-path Monte Carlo (Fearless enforced), data: lolesports, gol.gg, Data Dragon" if is_v2 else "results-only map Elo + Monte Carlo series sim (lolesports data)"}. Edge: Jupiter asks, de-vigged proportionally per binary pair, fee {E(edge['fee_model'] if edge else '')}. BEST BET = highest +EV market that also cashes on the locked exact score; otherwise PASS. Not financial advice.</footer>
</body></html>"""
    config.DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (config.DOCS_DIR / f"{date}.html").write_text(doc)
    latest = max((q.stem for q in config.DOCS_DIR.glob("20??-??-??.html")), default=date)
    if date >= latest:   # an on-demand /lol run for an older date must not replace the current front page
        (config.DOCS_DIR / "index.html").write_text(doc)
    (config.DOCS_DIR / ".nojekyll").write_text("")
    src = Path(__file__).resolve().parent.parent / "brand"
    (config.DOCS_DIR / "assets").mkdir(exist_ok=True)
    for f in ("luck-society-avatar.jpg", "luck-society-banner.jpg"):
        if (src / f).exists():
            shutil.copyfile(src / f, config.DOCS_DIR / "assets" / f)
    write_simulate_page()
    try:
        from . import simexport
        simexport.export(date, version or "v2")
    except Exception as e:  # the forecast dashboard must never fail because of the optional sim export
        print(f"[dashboard] sim export skipped: {e}")
    return config.DOCS_DIR / "index.html"


SIM_CSS = """
.simgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.simgrid label{display:block;font-size:12px;color:var(--gold);letter-spacing:.04em;text-transform:uppercase;margin-bottom:3px}
select,input{background:var(--card2);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:7px 8px;font:inherit;width:100%}
input[type=checkbox]{width:auto;accent-color:var(--clover-hi)}
.chk{display:flex;gap:6px;align-items:center;color:var(--ink);font-size:13px;margin:4px 0}
button{background:var(--gold);color:var(--black);border:0;border-radius:8px;padding:10px 18px;font:700 15px/1 Georgia,serif;letter-spacing:.08em;cursor:pointer}
button:hover{background:var(--gold-hi)}button:disabled{opacity:.5;cursor:wait}
button.ghost{background:transparent;color:var(--gold-hi);border:1px solid var(--bronze);padding:3px 8px;font:12px/1.2 inherit;letter-spacing:0}
.bar{height:6px;background:var(--card2);border-radius:3px;overflow:hidden;margin:8px 0}.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--clover),var(--gold-hi));transition:width .15s}
.badge{display:inline-block;background:var(--clover);color:var(--ink);border-radius:4px;padding:1px 7px;font-size:11px;letter-spacing:.06em;font-weight:700}
.sc.lk{border-color:var(--gold-hi)}
#mk input{width:80px;padding:4px 6px}#mk input[type=checkbox]{width:auto}#mk td small.src{display:block;color:var(--mut);font-size:10px}
.addrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:6px;align-items:end;margin-top:8px}
"""


def write_simulate_page():
    """Static Simulate page (GitHub Pages). JS lives in web/ and is copied to docs/assets/."""
    web = Path(__file__).resolve().parent.parent / "web"
    (config.DOCS_DIR / "assets").mkdir(parents=True, exist_ok=True)
    for f in ("sim-core.js", "sim-worker.js", "simulate.js"):
        if (web / f).exists():
            shutil.copyfile(web / f, config.DOCS_DIR / "assets" / f)
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Luck Society · LoL Simulator + Edge Finder</title><meta name="theme-color" content="#01171a"><link rel="icon" href="assets/luck-society-avatar.jpg"><style>{CSS}{SIM_CSS}</style></head><body>
<header class="brand"><div class="ov"><div class="bi"><img class="logo" src="assets/luck-society-avatar.jpg" alt="Luck Society clover coin logo">
<div><h1>Luck Society · LoL Simulator</h1><p class="tag">Sports Betting • Crypto • Stocks • Predictions 🍀</p>
<p class="voice">Pick any two teams, sim the series, then put any price against it. Let's stay green 🍀</p></div></div>
<div class="meta"><p><span class="badge">RATINGS-BASED SIM</span> Runs in your browser on the exported v2 patch-weighted map Elo. It is <b>separate from the frozen protocol lock</b> on the <a href="index.html">dashboard</a> (that one adds lineups + draft-path sims and is frozen before any price is read). <span id="gen"></span></p></div>
<div class="nav"><a class="simbtn" href="index.html">← Dashboard</a></div></div></header>
<main>
<section class="card"><h2>1 · Matchup</h2>
<div class="simgrid">
 <div><label for="preset">Slate series</label><select id="preset"></select></div>
 <div><label for="ta">Team A</label><select id="ta"></select></div>
 <div><label for="tb">Team B</label><select id="tb"></select></div>
 <div><label for="bo">Format</label><select id="bo"><option value="1">Bo1</option><option value="3" selected>Bo3</option><option value="5">Bo5</option></select>
  <label class="chk"><input type="checkbox" id="fearless" checked> Fearless draft</label></div>
</div>
<p class="derived" id="base"></p><div class="form" id="roster"></div><div class="lock" id="frozen"></div>
<p><button class="ghost" id="swap">swap A ⇄ B</button></p>
<details><summary>Overrides + sim settings</summary><div class="simgrid">
 <div><label for="ovr">Map win % for <span id="sa">A</span> (neutral side, blank = ratings)</label><input id="ovr" placeholder="e.g. 58" inputmode="decimal"></div>
 <div><label for="ovrg">Per-game map win % (comma list, overrides the above)</label><input id="ovrg" placeholder="e.g. 60,55,50"></div>
 <div><label for="g1">Game 1 blue side</label><select id="g1"><option value="rand">unknown (50/50)</option><option value="A">Team A blue</option><option value="B">Team B blue</option></select>
  <label class="chk"><input type="checkbox" id="side" checked> blue-side edge (fitted)</label></div>
 <div><label for="n">Simulations</label><input id="n" value="100000" inputmode="numeric"><label for="seed">Seed</label><input id="seed" value="20261028" inputmode="numeric"></div>
 <div><label for="fx">Fearless extra map sd per later game</label><input id="fx" value="0.10" inputmode="decimal">
  <label class="chk"><input type="checkbox" id="unc" checked> rating + map uncertainty</label></div>
</div><p class="muted">Later games: the loser picks side (fitted blue rate). Fearless widens later-map variance (draft pools deplete) by the value above; this is a ratings-level approximation, not the dashboard's draft model.</p></details>
<p><button id="go">🎲 SIMULATE</button> <span class="muted" id="status"></span></p><div class="bar"><i id="prog"></i></div>
</section>
<section class="card"><h2>2 · Results <span class="badge">RATINGS-BASED</span></h2><div id="res"><p class="muted">Run SIMULATE.</p></div></section>
<section class="card"><h2>3 · Edge finder · Jupiter or any price</h2>
<p class="muted" id="oddsnote"></p><div id="verdict"></div>
<div class="tw"><table class="mk" id="mk"></table></div>
<div class="addrow">
 <div><label class="muted" for="ck">Add market</label><select id="ck"><option value="ml">Series ML</option><option value="hcp">Map handicap</option><option value="total">Total maps</option><option value="game">Game N winner</option><option value="exact">Exact score</option></select></div>
 <div><label class="muted" for="cs">Side</label><select id="cs"><option value="A" class="lblA">A</option><option value="B" class="lblB">B</option></select></div>
 <div><label class="muted" for="cl">Line / score</label><input id="cl" placeholder="-1.5"></div>
 <div><label class="muted" for="cg">Game #</label><input id="cg" placeholder="1" inputmode="numeric"></div>
 <div><label class="muted" for="cp">Price</label><input id="cp" placeholder="0.62 or -150"></div>
 <div><label class="muted" for="co">Opp. price (opt)</label><input id="co" placeholder="0.40 or +130"></div>
 <div><button class="ghost" id="add">+ add</button></div>
</div>
<p class="muted">Prices: 0.62, 62%, 62c, or American (-150 / +130). Implied = price; No-vig = price ÷ (price + opposite side) when both sides are priced. Fee = Jupiter 0.07·p·(1−p), on by default for 0–1 prices, off for American odds (the vig is in the price). EV/$1 = model ÷ (price + fee) − 1. Game-winner markets: an unplayed game resolves 50-50 on Jupiter, so Model = expected payout. Totals side: A = Over, B = Under. BEST BET = highest +EV market that cashes on this sim's lock, otherwise PASS. Not financial advice.</p>
</section></main>
<footer>Luck Society · in-browser ratings sim (Web Worker) · data: lolesports results → v2 patch-recency Elo, official rosters · Jupiter prices are post-forecast snapshots, never inputs to any forecast.</footer>
<script src="assets/sim-core.js"></script><script src="assets/simulate.js"></script></body></html>"""
    (config.DOCS_DIR / "simulate.html").write_text(doc)
