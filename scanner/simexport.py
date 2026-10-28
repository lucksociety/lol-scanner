"""Export docs/data/sim_data.json for the in-browser 'Simulate' panel (GitHub Pages, no backend, no secrets).

Contents: v2 patch-weighted map Elo for every recently active team (+ v1 Elo, official roster), the slate's frozen
forecast summary (for preloading and side-by-side comparison only), and the latest POST-FORECAST Jupiter legs per
series. The browser sim is a ratings-based Monte Carlo; it is NOT the frozen protocol forecast and nothing it
computes is written back anywhere.
"""
import glob, json, math
from datetime import datetime, timezone, timedelta
from . import config, ratings, ratings_v2, schedule, patches, sim_r
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
            "ask": leg["ask"], "pair_id": leg["pair_id"], "market_id": leg["market_id"], "volume": leg.get("volume"), "has_book": leg.get("has_book", True),
            "jupiter": True}


def _team_table(now, params):
    p = config.CACHE_DIR / "schedule_pages" / "events.json"
    if not p.exists():
        return None, None
    rows = schedule.series_rows([e for e in json.loads(p.read_text()) if e["startTime"] >= config.HISTORY_START])
    cal = patches.load()
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Ratings for the browser sim always follow the CURRENT primary model, tuned walk-forward at `now` (not the
    # params of whichever frozen file is newest: Oct 28-30 2026 are frozen on v2, whose lambda=0.8 fit was a bug).
    from .forecast_v2 import PRIMARY, _RATINGS_VARIANT
    var = _RATINGS_VARIANT[PRIMARY]
    past = [r for r in rows if r["start"] < now_iso]
    (lam, ks), _ = ratings_v2.tune_lambda(past, cal, now_iso, variant=var)
    kil = ratings_v2.VARIANTS[var]["k_intl_leagues"]
    R, pre, _ = ratings_v2.elo_pass(past, cal, lam, kscale=ks, k_intl_leagues=kil)
    offs = (ratings_v2.fit_league_offsets(past, pre, R.home, now_iso)["offsets"]
            if ratings_v2.VARIANTS[var].get("league_offsets") else {})
    params.update({"_export_variant": PRIMARY, "_export_lambda": lam, "_export_kscale": ks})
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
        r2 += ratings_v2.offset(nm, R.home, offs)                         # v2.2 only (0 otherwise)
        out.append({**m, "league": league or "", "elo_v2": round(r2, 1), "elo_v1": round(R1.r.get(nm, r2), 1),
                    "maps": maps.get(nm, 0), "roster": roster.get(nm, [])})
    out.sort(key=lambda t: -t["elo_v2"])
    return out, cur


def _slate_entry(c, fc):
    A, B, s = c["team_a"], c["team_b"], c["sim"]
    return {"match_id": c["match_id"], "a": A["code"], "b": B["code"], "a_name": A["name"], "b_name": B["name"],
            "bo": c["bo"], "fearless": bool(c["model"].get("fearless", c["bo"] > 1)), "start_et": c["start_et"],
            "start_utc": c.get("start_utc"), "league": c["league"], "g1_a_blue": c["model"].get("g1_a_blue"), "modeled": True,
            "elo_v2": [A.get("elo_v2"), B.get("elo_v2")],
            "lineups": [[{"role": x["role"], "name": x["name"], "status": x["status"]} for x in t.get("lineup", [])] for t in (A, B)],
            "frozen": {"model_version": fc.get("model_version"), "frozen_at_et": c.get("frozen_at_et") or fc.get("frozen_at_et"),
                       "p_map_a": c["model"].get("p_map_a"), "p_map_a_elo_only": c["model"].get("p_map_a_elo_only"),
                       "p_series_a": s["p_series_a"], "locked_score": s["locked_score"], "exact": s["exact"],
                       "p_game_a": s.get("p_game_a"), "p_game_reached": s.get("p_game_reached")}}


def _status_entry(r, status, reason):
    return {"match_id": r["match_id"], "a": r["a"], "b": r["b"], "a_name": r.get("a_name"), "b_name": r.get("b_name"),
            "bo": r.get("bo") or 1, "fearless": (r.get("bo") or 1) > 1, "start_et": r.get("start_et", ""), "start_utc": r.get("start"),
            "league": r.get("league", ""), "g1_a_blue": None, "modeled": False, "status": status, "reason": reason,
            "lineups": [[], []], "frozen": None}


def export(dates, version="v2"):
    """Rebuild the simulator slate for `dates` (list, or one ISO date). Every scheduled series is listed: frozen
    cards (preload + frozen lock), plus not-modeled / TBD / not-scanned rows (still simmable on ratings when the
    teams are known)."""
    from . import window
    from .dashboard import _unmodeled_reason
    if isinstance(dates, str):
        dates = [dates]
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = OUT(); path.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(path.read_text()) if path.exists() else {}
    sched = window.load_sched().get("dates", {})
    slate = dict(prev.get("slate", {}))
    odds = dict(prev.get("odds", {}))
    params = {}
    for date in dates:
        ver = version if frozen_path(date, version).exists() else None
        fc = load_frozen(date, ver) if frozen_path(date, ver).exists() else {"cards": [], "params": {}}
        params = fc.get("params") or params
        slate[date] = []
        edge_p = config.EDGE_DIR / f"{date}{'.v2' if str(fc.get('model_version', '')).startswith('v2') else ''}.json"
        edge = json.loads(edge_p.read_text()) if (fc["cards"] and edge_p.exists()) else None
        erows = {r["match_id"]: r for r in (edge or {}).get("series", [])}
        for c in fc["cards"]:
            A, B = c["team_a"], c["team_b"]
            slate[date].append(_slate_entry(c, fc))
            r = erows.get(c["match_id"])
            if r and r.get("status") == "ok":
                legs = [x for x in (leg_spec(l, A["code"], B["code"]) for l in r["legs"]) if x]
                odds[c["match_id"]] = {"a": A["code"], "b": B["code"], "date": date, "fetched_at_et": edge["odds_fetched_at_et"],
                                       "title": r.get("jupiter_title"), "url": r.get("jupiter_url"), "legs": legs,
                                       "not_modeled": r.get("not_modeled", [])}
        have = {c["match_id"] for c in fc["cards"]}
        skipped = {x["match_id"]: x["reason"] for x in fc.get("skipped", [])}
        for r in sched.get(date, []):
            if r["match_id"] not in have:
                st, why = _unmodeled_reason(r, skipped, now_iso)
                slate[date].append(_status_entry(r, st, why))
        slate[date].sort(key=lambda m: m.get("start_utc") or "")
    if not params:   # ratings params from the newest frozen v2 forecast
        for q in sorted(config.FORECAST_DIR.glob("20??-??-??.v2.json"), reverse=True):
            params = json.loads(q.read_text()).get("params") or {}
            if params:
                break
    teams, patch = _team_table(now, params)
    if teams is None:   # cold cache: keep the previous ratings table rather than publishing nothing
        teams, patch = prev.get("teams", []), prev.get("patch")
    from .forecast_v2 import PRIMARY, MODEL_VERSIONS
    ratings_head = MODEL_VERSIONS[PRIMARY].startswith(("v2.1-ratings", "v2.2"))
    keep = sorted(slate)[-21:]
    slate = {d: slate[d] for d in keep}
    live = {m["match_id"] for d in keep for m in slate[d]}
    odds = {k: v for k, v in odds.items() if k in live}
    out = {"generated_at_utc": now.isoformat(), "patch": patch, "latest_date": max(keep) if keep else None,
           "today_et": window.today_et(now),
           "label": "Ratings export for the in-browser sim (no market inputs). Odds are post-forecast snapshots for the edge comparison only.",
           # primary = ratings-only headline: the browser sim uses the same simulator (no side/draft/Fearless term)
           "params": {"model_version": MODEL_VERSIONS[PRIMARY], "elo_scale": ratings.SCALE,
                      "rating_sd": sim_r.RATING_SD, "map_sd": sim_r.MAP_SD,
                      "side_blue_logit": 0.0 if ratings_head else params.get("outcome_model", {}).get("side_blue", 0.0),
                      "q_loser_blue": params.get("q_loser_blue", 0.5),
                      "fee_coef": config.JUP_FEE_COEF, "fearless_extra_sd": 0.0 if ratings_head else 0.10,
                      "elo_patch_lambda": params.get("_export_lambda"), "elo_kscale": params.get("_export_kscale")},
           "teams": teams, "slate": slate, "odds": odds}
    path.write_text(json.dumps(out, separators=(",", ":")))
    return path
