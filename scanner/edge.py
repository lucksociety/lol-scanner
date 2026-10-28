"""Stage 2: POST-FORECAST edge layer (Jupiter prediction markets).

Hard rules enforced here:
  * Reads the frozen forecast via forecast.load_frozen (SHA-256 verified) and refuses to run if it is missing.
  * Asserts the odds fetch time is strictly AFTER the forecast's frozen_at timestamp.
  * Never writes to forecast_log/. Outputs go to odds_snapshots/ (raw prices, for CLV grading) and edge_log/.
  * Nothing computed here flows back into any forecast number.
"""
import json, math, re, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from . import config
from .forecast import load_frozen, frozen_path

ET = ZoneInfo(config.TZ)
UA = "Mozilla/5.0 (lol-scanner edge layer)"
ALIASES = {  # lolesports name -> extra tokens Jupiter/Polymarket may use
    "team liquid alienware": ["team liquid"], "los": ["los grandes"], "ctbc flying oyster": ["cfo"],
    "bilibili gaming": ["blg"], "top esports": ["tes"], "hanwha life esports": ["hle"], "gen.g esports": ["gen.g", "geng"],
    "dplus kia": ["dk", "dplus"], "movistar koi": ["mkoi", "koi"], "invictus gaming": ["ig"],
    "anyone's legend": ["al"], "team secret whales": ["tsw"], "karmine corp": ["kc"],
}


# ------------------------------------------------------------------ http
def _get(path, **params):
    url = f"{config.JUP_BASE}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    h = {"User-Agent": UA, "Accept": "application/json"}
    if config.JUP_API_KEY:
        h["x-api-key"] = config.JUP_API_KEY
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40) as r:
                return json.load(r)
        except Exception as e:  # noqa
            last = e; time.sleep(1.5 * (i + 1))
    raise last


def fetch_lol_events(log):
    """Primary: /events?category=esports&subcategory=lol. Fallback: full esports category filtered to lol."""
    try:
        d = _get("/events", category="esports", subcategory="lol", includeMarkets="true",
                 includeAllMarkets="true", start=0, end=100)
        log.append({"endpoint": "GET /events?category=esports&subcategory=lol&includeMarkets=true", "ok": True,
                    "n": len(d.get("data", []))})
        if d.get("data"):
            return d["data"]
    except Exception as e:  # noqa
        log.append({"endpoint": "GET /events?category=esports&subcategory=lol", "ok": False, "error": str(e)[:200]})
    out, s = [], 0
    while s < 2000:
        d = _get("/events", category="esports", includeMarkets="true", includeAllMarkets="true", start=s, end=s + 100)
        out += [e for e in d.get("data", []) if e.get("subcategory") == "lol"]
        if not d.get("pagination", {}).get("hasNext"):
            break
        s += 100
    log.append({"endpoint": "GET /events?category=esports (paged, filtered lol)", "ok": True, "n": len(out)})
    return out


def fetch_orderbook(mid, log):
    try:
        ob = _get(f"/orderbook/{mid}")
        return ob
    except Exception as e:  # noqa
        log.append({"endpoint": f"GET /orderbook/{mid}", "ok": False, "error": str(e)[:120]})
        return None


# ------------------------------------------------------------------ matching
def _norm(s):
    return re.sub(r"[^a-z0-9. ]", "", (s or "").lower()).strip()


def _tokens(name, code):
    n = _norm(name)
    return {n, _norm(code), *ALIASES.get(n, [])}


def _same(team, jname):
    j = _norm(jname)
    toks = _tokens(team["name"], team["code"])
    return j in toks or any(t and (t == j or (len(t) > 3 and (t in j or j in t))) for t in toks)


def match_event(card, events):
    for e in events:
        title = e.get("metadata", {}).get("title", "")
        teams = {m.get("title") for m in e.get("markets", []) if m.get("sportsMarketType") == "moneyline"}
        if len(teams) != 2:
            continue
        ta, tb = sorted(teams)
        if ((_same(card["team_a"], ta) and _same(card["team_b"], tb)) or
                (_same(card["team_a"], tb) and _same(card["team_b"], ta))):
            return e
    return None


# ------------------------------------------------------------------ pricing
def fee(p):
    return config.JUP_FEE_COEF * p * (1 - p)


def game_prob(p_map_a):
    """Marginal P(A wins a given game) under the frozen sim's uncertainty model (rating_sd .35, map_sd .25).
    Pure function of the frozen p_map_a; does not modify the forecast."""
    base = math.log(p_map_a / (1 - p_map_a)); sd = math.hypot(0.35, 0.25)
    xs = [-3 + 0.05 * i for i in range(121)]
    w = [math.exp(-x * x / 2) for x in xs]
    return sum(wi / (1 + math.exp(-(base + sd * x))) for wi, x in zip(w, xs)) / sum(w)


def outcome_fn(kind, side_is_a, line, game_no, bo):
    """Returns f(a_w, b_w) -> True/False/None (None = undetermined by final score)."""
    need = bo // 2 + 1
    if kind == "moneyline":
        return lambda a, b: (a > b) if side_is_a else (b > a)
    if kind == "map_handicap":
        return lambda a, b: ((a - b) + line > 0) if side_is_a else ((b - a) + line > 0)
    if kind == "totals_over":
        return lambda a, b: (a + b) > line
    if kind == "totals_under":
        return lambda a, b: (a + b) < line
    if kind == "exact":
        x, y = line
        return lambda a, b: (a, b) == ((x, y) if side_is_a else (y, x))
    if kind == "game":
        def f(a, b):
            if (side_is_a and b == 0) or (not side_is_a and a == 0):
                return True
            if (side_is_a and a == 0) or (not side_is_a and b == 0):
                return False
            return None  # depends on game order
        return f
    return lambda a, b: None


def prob_from_exact(exact, f):
    p = 0.0
    for k, v in exact.items():
        a, b = map(int, k.split("-"))
        if f(a, b):
            p += v
    return p


def classify(m, card):
    """Map one Jupiter market leg -> (label, kind, side_is_a, line, game_no) or None if unsupported."""
    t = m.get("sportsMarketType"); title = m.get("title", ""); bo = card["bo"]
    A, B = card["team_a"], card["team_b"]
    line = m.get("sportsLine")
    is_a = _same(A, title); is_b = _same(B, title)
    if t == "moneyline" and (is_a or is_b):
        return (f"ML {A['code'] if is_a else B['code']}", "moneyline", is_a, None, None)
    if t == "map_handicap" and (is_a or is_b) and bo >= 3:
        rules = m.get("rulesPrimary", "")
        r = re.search(r"if (.+?) wins? (\d) or more games than", rules)
        L = float(line) if line not in (None, "") else -1.5
        fav_is_a = _same(A, r.group(1)) if r else None
        if fav_is_a is None:
            fav_is_a = is_a if L < 0 else not is_a
        mine = L if (is_a == fav_is_a) else -L
        code = A["code"] if is_a else B["code"]
        return (f"{code} {mine:+.1f} maps", "map_handicap", is_a, mine, None)
    if t == "totals" and bo >= 3:
        L = float(line)
        if title.lower().startswith("over"):
            return (f"Over {L} maps", "totals_over", None, L, None)
        if title.lower().startswith("under"):
            return (f"Under {L} maps", "totals_under", None, L, None)
    if t == "child_moneyline" and (is_a or is_b):
        g = m.get("gameNumber") or int(line or 0)
        if g and g <= bo // 2 + 1:  # always-played games only
            return (f"Game {g} {A['code'] if is_a else B['code']}", "game", is_a, None, g)
    if t in ("correct_score", "exact_score", "series_score"):
        s = re.search(r"(\d)\s*[-:]\s*(\d)", title)
        if s:
            x, y = int(s.group(1)), int(s.group(2))
            first_is_a = not _same(B, title.split(str(x))[0]) if (is_a or is_b) else True
            return (f"Exact {title}", "exact", first_is_a, (x, y), None)
    return None


def evaluate(card, ev, obs):
    sim = card["sim"]; exact = sim["exact"]; bo = card["bo"]
    la, lb = map(int, sim["locked_score"].split("-"))
    legs = []
    for m in ev.get("markets", []):
        c = classify(m, card)
        pr = m.get("pricing") or {}
        ask = (pr.get("buyYesPriceUsd") or 0) / 1e6
        if not c or not ask or ask >= 1 or m.get("status") != "open":
            continue
        label, kind, side_is_a, line, g = c
        f = outcome_fn(kind, side_is_a, line, g, bo)
        if kind == "game":
            pa = game_prob(card["model"]["p_map_a"]); q = pa if side_is_a else 1 - pa
        else:
            q = prob_from_exact(exact, f)
        cost = ask + fee(ask)
        on_lock = f(la, lb)
        ob = obs.get(m["marketId"])
        depth = None
        if ob and ob.get("no"):
            best_no = max(ob["no"], key=lambda x: x[0])
            if abs((100 - best_no[0]) / 100 - ask) < 0.006:
                depth = round(best_no[1], 2)
        legs.append({"market_id": m["marketId"], "pair_id": m["marketId"].rsplit("-", 1)[0], "label": label,
                     "type": kind, "ask": ask, "bid": (pr.get("sellYesPriceUsd") or 0) / 1e6,
                     "volume": pr.get("volume"), "fee": round(fee(ask), 4), "cost": round(cost, 4),
                     "our_prob": round(q, 4), "cashes_on_lock": on_lock, "contracts_at_ask": depth})
    # de-vig per binary pair (proportional normalisation of the two asks)
    pairs = {}
    for l in legs:
        pairs.setdefault(l["pair_id"], []).append(l)
    for pid, ls in pairs.items():
        s = sum(x["ask"] for x in ls)
        for x in ls:
            x["overround"] = round(s - 1, 4) if len(ls) == 2 else None
            x["fair_prob"] = round(x["ask"] / s, 4) if len(ls) == 2 else round(x["ask"], 4)
            x["edge_pct"] = round(100 * (x["our_prob"] - x["fair_prob"]), 2)
            x["ev_per_$1"] = round(x["our_prob"] / x["cost"] - 1, 4)
    order = {"moneyline": 0, "map_handicap": 1, "totals_over": 2, "totals_under": 2, "exact": 3, "game": 4}
    legs.sort(key=lambda x: (order.get(x["type"], 9), x["label"]))
    plus = [x for x in legs if x["ev_per_$1"] > 0]
    compat = [x for x in plus if x["cashes_on_lock"] is True]
    best = max(compat, key=lambda x: x["ev_per_$1"]) if compat else None
    best_any = max(plus, key=lambda x: x["ev_per_$1"]) if plus else None
    return legs, best, best_any


def run(date):
    if not frozen_path(date).exists():
        raise SystemExit(f"edge.py: no frozen forecast for {date}; run the forecast stage first")
    fc = load_frozen(date)  # hash-verified, read-only
    frozen_at = datetime.fromisoformat(fc["frozen_at_utc"].replace("Z", "+00:00"))
    fetched = datetime.now(timezone.utc)
    assert fetched > frozen_at, "odds fetch must happen strictly after the forecast freeze"
    log = []
    events = fetch_lol_events(log)
    rows, raw = [], {}
    obs_ok = 0
    for card in fc["cards"]:
        ev = match_event(card, events)
        if not ev:
            rows.append({"match_id": card["match_id"], "series": f'{card["team_a"]["code"]} vs {card["team_b"]["code"]}',
                         "status": "no Jupiter market", "legs": [], "best_bet": None, "verdict": "no Jupiter market"})
            continue
        obs = {}
        for m in ev.get("markets", []):
            if m.get("sportsMarketType") in ("moneyline", "map_handicap", "totals", "child_moneyline",
                                             "correct_score", "exact_score", "series_score"):
                ob = fetch_orderbook(m["marketId"], log)
                if ob:
                    obs[m["marketId"]] = ob; obs_ok += 1
        legs, best, best_any = evaluate(card, ev, obs)
        rules = next((m.get("rulesPrimary", "") for m in ev.get("markets", []) if m.get("sportsMarketType") == "moneyline"), "")
        sched = re.search(r"initially scheduled for ([^.]*?ET)", rules)
        warn = []
        if sched:
            want = datetime.fromisoformat(card["start_utc"].replace("Z", "+00:00")).astimezone(ET)
            if want.strftime("%B %-d, %Y") not in sched.group(1):
                warn.append(f"Jupiter/Polymarket rules list '{sched.group(1)}' but official lolesports start is "
                            f"{card['start_et']} -- confirm resolution terms before trading")
        offered = {l["type"] for l in legs}
        rows.append({"match_id": card["match_id"], "series": f'{card["team_a"]["code"]} vs {card["team_b"]["code"]}',
                     "status": "ok", "jupiter_event_id": ev["eventId"], "jupiter_title": ev["metadata"].get("title"),
                     "jupiter_url": f"https://jup.ag/prediction/{ev['eventId']}",
                     "exact_score_offered": "exact" in offered, "warnings": warn, "legs": legs,
                     "best_bet": best, "best_ev_any": best_any,
                     "verdict": (f"BEST BET: {best['label']} @ {best['ask']:.2f} (EV {best['ev_per_$1']:+.3f}/$1, cashes on lock "
                                 f"{card['sim']['locked_score']})") if best else "PASS (no +EV market that cashes on the locked score)"})
        raw[ev["eventId"]] = {"event": ev, "orderbooks": obs}
    log.append({"endpoint": "GET /orderbook/{marketId}", "ok": obs_ok > 0, "n": obs_ok})
    stamp = fetched.strftime("%Y%m%dT%H%M%SZ")
    snap_dir = config.ODDS_DIR / date; snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / f"{stamp}.json"
    snap_file.write_text(json.dumps({"fetched_at_utc": fetched.isoformat(), "forecast_sha256": fc["sha256"],
                                     "forecast_frozen_at_utc": fc["frozen_at_utc"], "source": config.JUP_BASE,
                                     "events": raw}, separators=(",", ":")))
    with open(config.ODDS_DIR / "odds_log.jsonl", "a") as f:
        for r in rows:
            for l in r["legs"]:
                f.write(json.dumps({"fetched_at_utc": fetched.isoformat(), "date": date, "match_id": r["match_id"],
                                    "series": r["series"], "market_id": l["market_id"], "label": l["label"],
                                    "ask": l["ask"], "bid": l["bid"], "fair_prob": l["fair_prob"],
                                    "our_prob": l["our_prob"]}) + "\n")
    cov = f"{sum(r['status'] == 'ok' for r in rows)}/{len(rows)} series"
    out = {"date": date, "label": "POST-FORECAST EDGE LAYER (market data; not an input to the forecast)",
           "forecast_sha256": fc["sha256"], "forecast_frozen_at_utc": fc["frozen_at_utc"],
           "odds_fetched_at_utc": fetched.isoformat(),
           "odds_fetched_at_et": fetched.astimezone(ET).strftime("%Y-%m-%d %I:%M:%S %p ET"),
           "snapshot_file": str(snap_file.relative_to(config.ROOT)), "endpoints": log,
           "endpoints_ok": sorted({l["endpoint"] for l in log if l.get("ok")}), "coverage": cov,
           "jupiter_lol_events_listed": [{"id": e["eventId"], "title": e["metadata"].get("title")} for e in events],
           "fee_model": f"{config.JUP_FEE_COEF} * p * (1-p) per $1 contract", "series": rows}
    config.EDGE_DIR.mkdir(parents=True, exist_ok=True)
    (config.EDGE_DIR / f"{date}.json").write_text(json.dumps(out, indent=2))
    out["snapshot_file"] = str(snap_file.relative_to(config.ROOT))
    return out
