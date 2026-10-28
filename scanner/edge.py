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


def _paged(path, log, label, cap=3000, **params):
    """GET a paginated Jupiter list endpoint until pagination.hasNext is false (never stops at end=100)."""
    out, s = [], 0
    while s < cap:
        d = _get(path, start=s, end=s + 100, **params)
        data = d.get("data", []) if isinstance(d, dict) else d
        out += data
        pg = d.get("pagination", {}) if isinstance(d, dict) else {}
        if not pg.get("hasNext") or not data:
            break
        s += 100
    log.append({"endpoint": label, "ok": True, "n": len(out)})
    return out


def _is_lol_title(t):
    t = (t or "").lower()
    return t.startswith("lol:") or "league of legends" in t


def fetch_lol_events(log):
    """All LoL events, paginated: /events?category=esports&subcategory=lol (paged), plus a paged scan of the whole
    esports category for LoL events that are not tagged subcategory=lol. Deduplicated by eventId."""
    seen = {}
    try:
        for e in _paged("/events", log, "GET /events?category=esports&subcategory=lol&includeMarkets=true&includeAllMarkets=true (paged)",
                        category="esports", subcategory="lol", includeMarkets="true", includeAllMarkets="true"):
            seen[e["eventId"]] = e
    except Exception as e:  # noqa
        log.append({"endpoint": "GET /events?category=esports&subcategory=lol", "ok": False, "error": str(e)[:200]})
    try:
        for e in _paged("/events", log, "GET /events?category=esports (paged, LoL by subcategory/title)",
                        category="esports", includeMarkets="true", includeAllMarkets="true"):
            if e.get("subcategory") == "lol" or _is_lol_title(e.get("metadata", {}).get("title")):
                seen.setdefault(e["eventId"], e)
    except Exception as e:  # noqa
        log.append({"endpoint": "GET /events?category=esports", "ok": False, "error": str(e)[:200]})
    return list(seen.values())


def fetch_event_markets(ev, log):
    """The /events list endpoint omits per-game prop markets (baron/dragon/inhib/quadra/penta, sometimes kill totals).
    /events/{id}/markets?includeAllMarkets=true returns every market; merge both by marketId."""
    ms = {m["marketId"]: m for m in ev.get("markets", [])}
    try:
        for m in _paged(f"/events/{ev['eventId']}/markets", log, f"GET /events/{ev['eventId']}/markets?includeAllMarkets=true (paged)",
                        includeAllMarkets="true"):
            ms[m["marketId"]] = {**ms.get(m["marketId"], {}), **m}
    except Exception as e:  # noqa
        log.append({"endpoint": f"GET /events/{ev['eventId']}/markets", "ok": False, "error": str(e)[:200]})
    return list(ms.values())


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


def _event_start(e):
    """Jupiter event start as an aware UTC datetime (beginAt unix seconds; else closeTime), or None."""
    b = e.get("beginAt") or (e.get("metadata") or {}).get("beginAt")
    try:
        if b:
            return datetime.fromtimestamp(int(b), timezone.utc)
    except (TypeError, ValueError):
        pass
    ct = (e.get("metadata") or {}).get("closeTime")
    try:
        return datetime.fromisoformat(ct.replace("Z", "+00:00")) if ct else None
    except ValueError:
        return None


def _day_gap(card, e):
    """|ET calendar-day difference| between the official lolesports start and Jupiter's start (None = unknown).
    Jupiter/Polymarket slugs and close times are often a day off (UTC / next-day close), so +/-1 day still matches."""
    st = _event_start(e)
    if not st or not card.get("start_utc"):
        return None
    cs = datetime.fromisoformat(card["start_utc"].replace("Z", "+00:00"))
    return abs((st.astimezone(ET).date() - cs.astimezone(ET).date()).days)


def match_event(card, events, max_days=1):
    """Same two teams (either order) and a Jupiter start within +/-max_days ET days of the official start;
    the closest start wins (the same pair can meet twice in one event, e.g. Swiss then knockouts)."""
    best = None
    for e in events:
        teams = {m.get("title") for m in e.get("markets", []) if m.get("sportsMarketType") == "moneyline"}
        if len(teams) != 2:
            continue
        ta, tb = sorted(teams)
        if not ((_same(card["team_a"], ta) and _same(card["team_b"], tb)) or
                (_same(card["team_a"], tb) and _same(card["team_b"], ta))):
            continue
        gap = _day_gap(card, e)
        if gap is not None and gap > max_days:
            continue
        key = 99 if gap is None else gap
        if best is None or key < best[0]:
            best = (key, e)
    return best[1] if best else None


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
            n = a + b
            if game_no > n:
                return False     # game not played -> Jupiter resolves 50-50 (half payout), not a cash
            a_won = a > b
            if game_no == n:     # the deciding game is always won by the series winner
                return a_won == bool(side_is_a)
            if min(a, b) == 0:   # sweep: the winner took every game
                return a_won == bool(side_is_a)
            return None          # depends on game order
        return f
    return lambda a, b: None


def prob_from_exact(exact, f):
    p = 0.0
    for k, v in exact.items():
        a, b = map(int, k.split("-"))
        if f(a, b):
            p += v
    return p


NOT_MODELED_REASON = {
    "lol_both_teams_baron": "in-game objective prop (both teams Baron); the sim models map winners only",
    "lol_both_teams_dragon": "in-game objective prop (both teams dragon); the sim models map winners only",
    "lol_both_teams_inhibitors": "in-game objective prop (both teams inhibitor); the sim models map winners only",
    "lol_quadra_kill": "player multikill prop; the sim models map winners only",
    "lol_penta_kill": "player multikill prop; the sim models map winners only",
    "kill_over_under_game": "per-game total kills; the sim does not output kills",
    "kill_handicap_game": "per-game kill handicap; the sim does not output kills",
}
_PROP_TITLES = {"lol_both_teams_baron": "Both teams Baron", "lol_both_teams_dragon": "Both teams dragon",
                "lol_both_teams_inhibitors": "Both teams inhib", "lol_quadra_kill": "Any quadra kill",
                "lol_penta_kill": "Any penta kill", "kill_over_under_game": "Kills"}


def not_modeled_entry(m):
    t = m.get("sportsMarketType") or "unknown"
    pr = m.get("pricing") or {}
    g = m.get("gameNumber")
    name = _PROP_TITLES.get(t, m.get("title") or t)
    side = m.get("title", "") if t == "kill_over_under_game" else ""
    label = " ".join(x for x in [f"Game {g}" if g else "", name, side] if x)
    return {"market_id": m["marketId"], "type": t, "label": label, "game": g, "status": m.get("status"),
            "yes_ask": (pr.get("buyYesPriceUsd") or 0) / 1e6 or None, "no_ask": (pr.get("buyNoPriceUsd") or 0) / 1e6 or None,
            "volume": pr.get("volume"),
            "reason": NOT_MODELED_REASON.get(t, f"market type '{t}' is not derivable from the series/map simulation")}


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
        if g and g <= bo:  # every listed game; unplayed games resolve 50-50 (priced as expected payout)
            return (f"Game {g} {A['code'] if is_a else B['code']}", "game", is_a, None, g)
    if t in ("correct_score", "exact_score", "series_score"):
        s = re.search(r"(\d)\s*[-:]\s*(\d)", title)
        if s:
            x, y = int(s.group(1)), int(s.group(2))
            first_is_a = not _same(B, title.split(str(x))[0]) if (is_a or is_b) else True
            return (f"Exact {title}", "exact", first_is_a, (x, y), None)
    return None


def _game_q(sim, card, g, side_is_a, bo):
    """Expected payout of a Game-g winner contract. Jupiter resolves an unplayed game 50-50, so
    q = P(reached) * P(side wins | reached) + (1 - P(reached)) * 0.5."""
    pg = (sim.get("p_game_a") or [None] * bo)[g - 1]
    reach = (sim.get("p_game_reached") or [None] * bo)[g - 1]
    if reach is None:   # v1 forecast: reach from the exact-score distribution
        reach = sum(v for k, v in sim["exact"].items() if sum(map(int, k.split("-"))) >= g)
    if pg is None:
        pg = game_prob(card["model"]["p_map_a"])
    win = pg if side_is_a else 1 - pg
    return reach * win + (1 - reach) * 0.5


def _legs_for_market(m, c):
    """A Jupiter market can be one side of a -0/-1 pair (buyYes only) or a single Yes/No market (buyYes + buyNo).
    Returns [(leg_suffix, ask, bid, label, side_is_a_or_flip)] for each buyable side."""
    pr = m.get("pricing") or {}
    yes = (pr.get("buyYesPriceUsd") or 0) / 1e6
    no = (pr.get("buyNoPriceUsd") or 0) / 1e6
    out = []
    if yes and yes < 1:
        out.append(("", yes, (pr.get("sellYesPriceUsd") or 0) / 1e6, False))
    if no and no < 1 and not re.search(r"-[01]$", m["marketId"]):
        out.append((":NO", no, (pr.get("sellNoPriceUsd") or 0) / 1e6, True))
    return out


def evaluate(card, ev, obs, markets=None):
    """Price every Jupiter market derivable from the frozen sim. Returns (legs, best, best_any, not_modeled, unpriced)."""
    sim = card["sim"]; exact = sim["exact"]; bo = card["bo"]
    la, lb = map(int, sim["locked_score"].split("-"))
    legs, not_modeled, unpriced = [], [], []
    for m in (markets if markets is not None else ev.get("markets", [])):
        c = classify(m, card)
        if not c:
            not_modeled.append(not_modeled_entry(m))
            continue
        sides = _legs_for_market(m, c)
        if m.get("status") != "open" or not sides:
            unpriced.append({"market_id": m["marketId"], "label": c[0], "status": m.get("status"),
                             "reason": "closed" if m.get("status") != "open" else "no ask on the book"})
            continue
        for suffix, ask, bid, flip in sides:
            label, kind, side_is_a, line, g = c
            if flip:   # buying NO on a single-id market = the complementary outcome
                f0 = outcome_fn(kind, side_is_a, line, g, bo)
                f = (lambda f0: (lambda a, b: (None if f0(a, b) is None else not f0(a, b))))(f0)
                q = (1 - _game_q(sim, card, g, side_is_a, bo)) if kind == "game" else 1 - prob_from_exact(exact, f0)
                label = f"NOT {label}"
            else:
                f = outcome_fn(kind, side_is_a, line, g, bo)
                q = _game_q(sim, card, g, side_is_a, bo) if kind == "game" else prob_from_exact(exact, f)
            cost = ask + fee(ask)
            ob = obs.get(m["marketId"])
            depth = None
            book = (ob or {}).get("yes" if flip else "no")
            if book:
                best_opp = max(book, key=lambda x: x[0])
                if abs((100 - best_opp[0]) / 100 - ask) < 0.006:
                    depth = round(best_opp[1], 2)
            pid = m["marketId"].rsplit("-", 1)[0] if re.search(r"-[01]$", m["marketId"]) else m["marketId"]
            legs.append({"market_id": m["marketId"] + suffix, "pair_id": pid, "label": label, "type": kind,
                         "game": g, "line": line if not isinstance(line, tuple) else list(line),
                         "ask": ask, "bid": bid, "volume": (m.get("pricing") or {}).get("volume"),
                         "fee": round(fee(ask), 4), "cost": round(cost, 4), "our_prob": round(q, 4),
                         "cashes_on_lock": f(la, lb), "contracts_at_ask": depth})
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
    not_modeled.sort(key=lambda x: (x["game"] or 0, x["type"], x["label"]))
    for x in legs:
        x["has_book"] = bool(obs.get(x["market_id"].replace(":NO", "")))
        x["skip_reason"] = sanity_skip(x)
        x["eligible"] = x["skip_reason"] is None
        x["qualifies"] = qualifies(x)
    # BEST BET (rule BEST_BET_RULE_VERSION): highest-EV Jupiter line among lines with model probability > 50% AND
    # EV > 0 that pass the price/liquidity guard. None qualifies -> NO BET. The lock is the headline prediction only;
    # cashes_on_lock is informational and never filters the pick. best_any = top +EV guarded line at any
    # probability, recorded for transparency only (it is never the bet).
    best = max((x for x in legs if x["qualifies"]), key=lambda x: x["ev_per_$1"], default=None)
    best_any = max((x for x in legs if x["eligible"] and x["ev_per_$1"] > 0), key=lambda x: x["ev_per_$1"], default=None)
    return legs, best, best_any, not_modeled, unpriced


def qualifies(leg):
    """Best-bet eligibility: passes the sanity guard, model probability > MIN_MODEL_PROB, EV > 0."""
    return leg.get("skip_reason") is None and leg["our_prob"] > MIN_MODEL_PROB and leg["ev_per_$1"] > 0


MIN_MODEL_PROB = 0.50
BEST_BET_RULE_VERSION = "r3-2026-10-28-prob50-maxEV"   # r2 (624ca75) = pure max-EV; r1 = had to cash on the lock
BEST_BET_RULE = (f"prob>50%+max-EV: highest-EV Jupiter line among lines with model probability > {MIN_MODEL_PROB:.0%} AND EV > 0 "
                 f"(any market/side); skip ask < {config.MIN_ASK} or > {config.MAX_ASK} or no volume and no orderbook; "
                 f"none qualifies -> NO BET; cashes_on_lock is info only")
NO_BET_REASON = f"no Jupiter line has model probability > {MIN_MODEL_PROB:.0%} and +EV after fees"


def sanity_skip(leg):
    """Liquidity / price guard so dead or pinned lines can't win the best bet. Returns a reason or None."""
    if leg["ask"] < config.MIN_ASK:
        return f"price < {config.MIN_ASK:.2f}"
    if leg["ask"] > config.MAX_ASK:
        return f"price > {config.MAX_ASK:.2f}"
    if not (leg.get("volume") or 0) > 0 and not leg.get("has_book") and not leg.get("contracts_at_ask"):
        return "no volume and no orderbook"
    return None


def inventory(markets):
    inv = {}
    for m in markets:
        t = m.get("sportsMarketType") or "unknown"
        x = inv.setdefault(t, {"markets": 0, "lines": set(), "games": set()})
        x["markets"] += 1
        if m.get("sportsLine") not in (None, "") and t not in ("child_moneyline",):
            x["lines"].add(str(m["sportsLine"]))
        if m.get("gameNumber"):
            x["games"].add(m["gameNumber"])
    return {t: {"markets": v["markets"], "lines": sorted(v["lines"]), "games": sorted(v["games"])} for t, v in sorted(inv.items())}


def run(date, version=None, jup_events=None, jup_log=None):
    if version and not frozen_path(date, version).exists():
        version = None
    if not frozen_path(date, version).exists():
        raise SystemExit(f"edge.py: no frozen forecast for {date}; run the forecast stage first")
    fc = load_frozen(date, version)  # hash-verified, read-only
    ver = "v2" if str(fc.get("model_version", "")).startswith("v2") else "v1"
    frozen_at = datetime.fromisoformat(fc["frozen_at_utc"].replace("Z", "+00:00"))
    fetched = datetime.now(timezone.utc)
    latest = datetime.fromisoformat(fc.get("frozen_at_utc_latest", fc["frozen_at_utc"]).replace("Z", "+00:00"))
    assert fetched > max(frozen_at, latest), "odds fetch must happen strictly after the forecast freeze (incl. addenda)"
    log = list(jup_log or [])
    events = jup_events if jup_events is not None else fetch_lol_events(log)
    rows, raw = [], {}
    obs_ok = 0
    for card in fc["cards"]:
        ev = match_event(card, events)
        if not ev:
            rows.append({"match_id": card["match_id"], "series": f'{card["team_a"]["code"]} vs {card["team_b"]["code"]}',
                         "status": "no Jupiter market", "legs": [], "best_bet": None, "verdict": "no Jupiter market"})
            continue
        markets = fetch_event_markets(ev, log)
        ev = {**ev, "markets": markets}
        obs = {}
        for m in markets:
            if classify(m, card) and m.get("status") == "open":
                ob = fetch_orderbook(m["marketId"], log)
                if ob:
                    obs[m["marketId"]] = ob; obs_ok += 1
        legs, best, best_any, not_modeled, unpriced = evaluate(card, ev, obs, markets)
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
                     "best_bet": best, "best_ev_any": best_any, "best_bet_rule_version": BEST_BET_RULE_VERSION,
                     "market_inventory": inventory(markets), "n_markets": len(markets), "n_priced_legs": len(legs),
                     "not_modeled": not_modeled, "unpriced": unpriced,
                     "verdict": (f"BEST BET: {best['label']} @ {best['ask']:.2f} (EV {best['ev_per_$1']:+.3f}/$1, model "
                                 f"{best['our_prob']:.1%} vs no-vig {best['fair_prob']:.1%})") if best else f"NO BET ({NO_BET_REASON})"})
        raw[ev["eventId"]] = {"event": ev, "orderbooks": obs}
    log.append({"endpoint": "GET /orderbook/{marketId}", "ok": obs_ok > 0, "n": obs_ok})
    stamp = fetched.strftime("%Y%m%dT%H%M%SZ")
    snap_dir = config.ODDS_DIR / date; snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / f"{stamp}.json"
    snap_file.write_text(json.dumps({"fetched_at_utc": fetched.isoformat(), "forecast_sha256": fc["sha256"],
                                     "forecast_model_version": fc.get("model_version"),
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
           "forecast_model_version": fc.get("model_version"), "forecast_file": frozen_path(date, version).name,
           "forecast_sha256": fc["sha256"], "forecast_frozen_at_utc": fc["frozen_at_utc"],
           "odds_fetched_at_utc": fetched.isoformat(),
           "odds_fetched_at_et": fetched.astimezone(ET).strftime("%Y-%m-%d %I:%M:%S %p ET"),
           "snapshot_file": str(snap_file.relative_to(config.ROOT)), "endpoints": log,
           "endpoints_ok": sorted({l["endpoint"] for l in log if l.get("ok")}), "coverage": cov,
           "jupiter_lol_events_listed": [{"id": e["eventId"], "title": e["metadata"].get("title")} for e in events],
           "fee_model": f"{config.JUP_FEE_COEF} * p * (1-p) per $1 contract",
           "best_bet_rule": BEST_BET_RULE, "best_bet_rule_version": BEST_BET_RULE_VERSION,
           "min_model_prob": MIN_MODEL_PROB, "series": rows}
    config.EDGE_DIR.mkdir(parents=True, exist_ok=True)
    (config.EDGE_DIR / f"{date}{'.v2' if ver == 'v2' else ''}.json").write_text(json.dumps(out, indent=2))
    out["snapshot_file"] = str(snap_file.relative_to(config.ROOT))
    return out
