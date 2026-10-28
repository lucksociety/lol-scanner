"""Export docs/data/sim_data.json for the in-browser 'Simulate' panel (GitHub Pages, no backend, no secrets).

Contents: v2 patch-weighted map Elo for every recently active team (+ v1 Elo, official roster), the slate's frozen
forecast summary (for preloading and side-by-side comparison only), and the latest POST-FORECAST Jupiter legs per
series. The browser sim is a ratings-based Monte Carlo; it is NOT the frozen protocol forecast and nothing it
computes is written back anywhere.
"""
import glob, json, math
from datetime import datetime, timezone, timedelta
from . import config, ratings, ratings_v2, schedule, patches
from .forecast import load_frozen, frozen_path

OUT = lambda: config.DOCS_DIR / "data" / "sim_data.json"   # noqa: E731
ACTIVE_DAYS = 400


def _img(u):
    u = u or ""
    return "https://" + u[len("http://"):] if u.startswith("http://") else u


def leg_spec(leg, code_a, code_b):
    """Structured market for the browser: kind/side/line/game (side 'A'/'B'/'over'/'under'), plus NOT flag."""
    lab = leg["label"]; neg = lab.startswith("NOT ")
    lab = lab[4:] if neg else lab
    t = leg["type"]; side = None; line = None; game = None
    if t == "moneyline":
        kind = "ml"; side = "A" if lab.split(" ", 1)[1] == code_a else "B"
    elif t == "map_handicap":
        kind = "hcp"; code, l, _ = lab.split(" "); side = "A" if code == code_a else "B"; line = float(l)
    elif t in ("totals_over", "totals_under"):
        kind = "total"; side = "over" if t == "totals_over" else "under"; line = float(lab.split(" ")[1])
    elif t == "game":
        kind = "game"; _, g, code = lab.split(" "); game = int(g); side = "A" if code == code_a else "B"
    elif t == "exact":
        kind = "exact"; x, y = leg.get("line") or (None, None); side = "A"; line = [x, y]
    else:
        return None
    return {"kind": kind, "side": side, "line": line, "game": game, "neg": neg, "label": leg["label"],
            "ask": leg["ask"], "pair_id": leg["pair_id"], "market_id": leg["market_id"], "volume": leg.get("volume"),
            "jupiter": True}


def _team_table(now, params):
    p = config.CACHE_DIR / "schedule_pages" / "events.json"
    if not p.exists():
        return None, None
    rows = schedule.series_rows([e for e in json.loads(p.read_text()) if e["startTime"] >= config.HISTORY_START])
    cal = patches.load()
    lam, ks = params.get("elo_patch_lambda", 1.0), params.get("elo_kscale", 1.0)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    R, _, _ = ratings_v2.elo_pass([r for r in rows if r["start"] < now_iso], cal, lam, kscale=ks)
    R1, _, _ = ratings.build(rows, now_iso)
    cur = cal.on(now.date().isoformat()); cord = cal.ord(cur)
    meta, maps = {}, {}
    for r in rows:
        if r["start"] >= now_iso:
            continue
        for s in ("a", "b"):
            nm = r[f"{s}_name"]
            if r["state"] == "completed" and r["a_w"] is not None:
                maps[nm] = maps.get(nm, 0) + (r["a_w"] or 0) + (r["b_w"] or 0)
                meta[nm] = {"code": r[s], "name": nm, "img": _img(r[f"{s}_img"]), "last": r["start"][:10]}
    try:
        tall = json.loads((config.CACHE_DIR / "teams_all.json").read_text())["data"]["teams"]
    except Exception:
        tall = []
    roster = {}
    for t in tall:
        if t.get("status") == "active":
            roster.setdefault(t.get("name"), [{"role": p.get("role"), "name": p.get("summonerName")} for p in t.get("players", [])])
    cutoff = (now - timedelta(days=ACTIVE_DAYS)).date().isoformat()
    out = []
    for nm, m in meta.items():
        if m["last"] < cutoff or nm not in R.r:
            continue
        league = R.home.get(nm) or R1.home.get(nm)
        r2 = R.touch(nm, league, now, cord) if cal.dates else R.r[nm]   # patch-recency decay up to today's patch
        out.append({**m, "league": league or "", "elo_v2": round(r2, 1), "elo_v1": round(R1.r.get(nm, r2), 1),
                    "maps": maps.get(nm, 0), "roster": roster.get(nm, [])})
    out.sort(key=lambda t: -t["elo_v2"])
    return out, cur


def export(date, version="v2"):
    now = datetime.now(timezone.utc)
    path = OUT(); path.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(path.read_text()) if path.exists() else {}
    ver = version if frozen_path(date, version).exists() else None
    fc = load_frozen(date, ver) if frozen_path(date, ver).exists() else {"cards": [], "params": {}}
    params = fc.get("params", {}) or {}
    teams, patch = _team_table(now, params)
    if teams is None:   # cold cache: keep the previous ratings table rather than publishing nothing
        teams, patch = prev.get("teams", []), prev.get("patch")
    o = params.get("outcome_model", {})
    hs = params.get("hand_set", {})
    slate = dict(prev.get("slate", {}))
    odds = dict(prev.get("odds", {}))
    slate[date] = []
    edge_p = config.EDGE_DIR / f"{date}{'.v2' if str(fc.get('model_version', '')).startswith('v2') else ''}.json"
    edge = json.loads(edge_p.read_text()) if edge_p.exists() else None
    erows = {r["match_id"]: r for r in (edge or {}).get("series", [])}
    for c in fc["cards"]:
        A, B, s = c["team_a"], c["team_b"], c["sim"]
        slate[date].append({"match_id": c["match_id"], "a": A["code"], "b": B["code"], "a_name": A["name"], "b_name": B["name"],
                            "bo": c["bo"], "fearless": bool(c["model"].get("fearless", c["bo"] > 1)), "start_et": c["start_et"],
                            "league": c["league"], "g1_a_blue": c["model"].get("g1_a_blue"),
                            "elo_v2": [A.get("elo_v2"), B.get("elo_v2")],
                            "lineups": [[{"role": x["role"], "name": x["name"], "status": x["status"]} for x in t.get("lineup", [])] for t in (A, B)],
                            "frozen": {"model_version": fc.get("model_version"), "frozen_at_et": fc.get("frozen_at_et"),
                                       "p_map_a": c["model"].get("p_map_a"), "p_map_a_elo_only": c["model"].get("p_map_a_elo_only"),
                                       "p_series_a": s["p_series_a"], "locked_score": s["locked_score"], "exact": s["exact"],
                                       "p_game_a": s.get("p_game_a"), "p_game_reached": s.get("p_game_reached")}})
        r = erows.get(c["match_id"])
        if r and r.get("status") == "ok":
            legs = [x for x in (leg_spec(l, A["code"], B["code"]) for l in r["legs"]) if x]
            odds[c["match_id"]] = {"a": A["code"], "b": B["code"], "date": date, "fetched_at_et": edge["odds_fetched_at_et"],
                                   "title": r.get("jupiter_title"), "url": r.get("jupiter_url"), "legs": legs,
                                   "not_modeled": r.get("not_modeled", [])}
    keep = sorted(slate)[-14:]
    slate = {d: slate[d] for d in keep}
    live = {m["match_id"] for d in keep for m in slate[d]}
    odds = {k: v for k, v in odds.items() if k in live}
    out = {"generated_at_utc": now.isoformat(), "patch": patch, "latest_date": max(keep) if keep else date,
           "label": "Ratings export for the in-browser sim (no market inputs). Odds are post-forecast snapshots for the edge comparison only.",
           "params": {"elo_scale": ratings.SCALE, "rating_sd": hs.get("rating_sd", 0.35), "map_sd": hs.get("map_sd", 0.20),
                      "side_blue_logit": o.get("side_blue", 0.0), "q_loser_blue": params.get("q_loser_blue", 0.5),
                      "fee_coef": config.JUP_FEE_COEF, "fearless_extra_sd": 0.10,
                      "elo_patch_lambda": params.get("elo_patch_lambda"), "elo_kscale": params.get("elo_kscale")},
           "teams": teams, "slate": slate, "odds": odds}
    path.write_text(json.dumps(out, separators=(",", ":")))
    return path
