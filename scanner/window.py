"""Rolling date window: every date from today-BACK to today+AHEAD (America/New_York) gets a dashboard page and a
simulator slate, so the site always has Yesterday / Today / Tomorrow / next week selectable.

Order per run (same integrity rule as the single-date path):
  1) schedule snapshot (lolesports, market-free)
  2) FORECAST: freeze every not-yet-frozen, not-yet-started series of every date in the window (base file, or an
     addendum when the date was frozen earlier and new series appeared, e.g. TBD -> teams set). Pre-start model upgrades and missing-history retries create immutable revisions.
  3) only then the POST-FORECAST Jupiter edge layer, once per date that still has unstarted frozen series
  4) dashboards for every date + docs/data/dates.json manifest + index.html = today ET + simulator export
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from . import config, schedule
from .forecast import _et_date, frozen_path, frozen_ids, freeze, freeze_addendum, load_frozen

ET = ZoneInfo(config.TZ)
BACK, AHEAD = 2, 7
SCHED = lambda: config.DOCS_DIR / "data" / "schedule.json"   # noqa: E731
MANIFEST = lambda: config.DOCS_DIR / "data" / "dates.json"   # noqa: E731
KEEP = ("match_id", "start", "state", "league", "block", "bo", "a", "b", "a_name", "b_name", "a_img", "b_img", "a_w", "b_w")


def today_et(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(ET).date().isoformat()


def window_dates(today, back=BACK, ahead=AHEAD):
    d0 = datetime.fromisoformat(today).date()
    return [(d0 + timedelta(days=i)).isoformat() for i in range(-back, ahead + 1)]


def start_et(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).strftime("%a %b %d, %I:%M %p ET")


def by_date(rows, dates):
    out = {d: [] for d in dates}
    for r in rows:
        d = _et_date(r["start"])
        if d in out:
            out[d].append({**{k: r.get(k) for k in KEEP}, "start_et": start_et(r["start"])})
    for d in out:
        out[d].sort(key=lambda r: r["start"])
    return out


def load_sched():
    p = SCHED()
    return json.loads(p.read_text()) if p.exists() else {"dates": {}}


def write_sched(grouped, now=None):
    """Merge this window's schedule rows into docs/data/schedule.json (older dates are kept as an archive)."""
    now = now or datetime.now(timezone.utc)
    cur = load_sched()
    dates = {**cur.get("dates", {}), **grouped}
    body = {"generated_at_utc": now.isoformat(), "generated_at_et": now.astimezone(ET).strftime("%Y-%m-%d %I:%M %p ET"),
            "source": "lolesports getSchedule (market-free)", "dates": dict(sorted(dates.items()))}
    SCHED().parent.mkdir(parents=True, exist_ok=True)
    SCHED().write_text(json.dumps(body, separators=(",", ":")))
    return body


def pending_ids(rows, now):
    """Series that can still be forecast honestly: both teams known, not started, start in the future."""
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {r["match_id"] for r in rows if r["a"] != "TBD" and r["b"] != "TBD" and r["state"] == "unstarted" and r["start"] > now_iso}


def freeze_date(date, events, rows, now, models=("v1", "v2"), refresh=None, clock=None):
    """Freeze the pending series of one date (base or addendum). Returns a log list."""
    from . import forecast, forecast_v2
    log = []
    want = pending_ids(rows, now)
    for v in models:
        new = want - frozen_ids(date, v)
        revise = set()
        if v == "v2" and frozen_path(date, v).exists():
            current = load_frozen(date, v)
            primary = forecast_v2.MODEL_VERSION
            revise = {c["match_id"] for c in current["cards"] if c.get("model_version") != primary
                      or c.get("frozen_at_utc", "")[:10] < now.date().isoformat()} & want
            # Missing history is recoverable: retry on a model upgrade or the next UTC data day.
            revise |= {s["match_id"] for s in current.get("skipped", [])
                       if s.get("model_version") != primary or s.get("frozen_at_utc", "")[:10] < now.date().isoformat()} & want
        new |= revise
        if not new:
            continue
        if v == "v1":
            body = forecast.build_forecast(date, now=now, events=events, only=new)
        else:
            body = forecast_v2.build_forecast(date, now=now, events=events, only=new,
                                              refresh_corpus=refresh.pop() if refresh else False)
        if not body["cards"] and not body.get("skipped"):
            continue
        # Fitting can take minutes. Stamp the completed forecast, and recheck start
        # times at publication instead of treating the earlier build time as a lock.
        written_at = clock() if clock else datetime.now(timezone.utc)
        eligible = pending_ids(rows, written_at)
        body["cards"] = [c for c in body["cards"] if c["match_id"] in eligible]
        body["skipped"] = [s for s in body.get("skipped", []) if s["match_id"] in eligible]
        if not body["cards"] and not body["skipped"]:
            continue
        body = freeze(body, now=written_at) if not frozen_path(date, v).exists() else freeze_addendum(body, now=written_at, revision=bool(revise))
        log.append(f"[forecast {v}] FROZEN {date}{' addendum ' + str(body.get('addendum')) if body.get('addendum') else ''} "
                   f"at {body['frozen_at_et']} | {len(body['cards'])} series + {len(body.get('skipped', []))} not modeled")
        for c in body["cards"]:
            log.append(f"   {c['team_a']['code']} vs {c['team_b']['code']} Bo{c['bo']}: lock {c['sim']['locked_score']}")
    return log


def needs_edge(date, rows):
    if not frozen_path(date, "v2").exists() and not frozen_path(date, None).exists():
        return False
    fc = load_frozen(date, "v2" if frozen_path(date, "v2").exists() else None)
    state = {r["match_id"]: r["state"] for r in rows}
    return any(state.get(c["match_id"], "unstarted") == "unstarted" for c in fc["cards"])


def day_summary(date, rows):
    """Manifest entry for the date selector."""
    from . import edgeview as ev
    n_series = len(rows)
    locked = bets = 0
    ver = "v2" if frozen_path(date, "v2").exists() else None
    if frozen_path(date, ver).exists():
        fc = load_frozen(date, ver)
        ids = {c["match_id"] for c in fc["cards"]}
        locked = len(ids)
        n_series = len(ids | {r["match_id"] for r in rows} | {s["match_id"] for s in fc.get("skipped", [])})
        ep = config.EDGE_DIR / f"{date}{'.v2' if str(fc.get('model_version', '')).startswith('v2') else ''}.json"
        if ep.exists():
            bets = sum(1 for r in json.loads(ep.read_text()).get("series", []) if not ev.best_summary(r)["pass"])
    return {"date": date, "n_series": n_series, "n_locked": locked, "n_bets": bets}


def write_manifest(today, grouped):
    """docs/data/dates.json: every date with a page (window + archive), newest last."""
    pages = {q.stem for q in config.DOCS_DIR.glob("20??-??-??.html")}
    sched = load_sched().get("dates", {})
    dates = sorted(pages | set(grouped))
    body = {"today_et_at_build": today, "window": [window_dates(today)[0], window_dates(today)[-1]],
            "dates": [day_summary(d, grouped.get(d, sched.get(d, []))) for d in dates]}
    MANIFEST().parent.mkdir(parents=True, exist_ok=True)
    MANIFEST().write_text(json.dumps(body, indent=1))
    return body


def run(today=None, model="v2", odds=True, back=BACK, ahead=AHEAD, now=None):
    from . import dashboard, simexport
    now = now or datetime.now(timezone.utc)
    today = today or today_et(now)
    dates = window_dates(today, back, ahead)
    events = schedule.fetch_all_events()
    rows = schedule.series_rows(events)
    grouped = by_date(rows, dates)
    write_sched(grouped, now)
    for d in dates:
        print(f"[window] {d}: " + (", ".join(f"{r['a']} vs {r['b']} Bo{r['bo']} ({r['state']})" for r in grouped[d]) or "no series"))
    # 1) FORECAST: every date frozen before any odds are fetched
    refresh = [True]
    for d in dates:
        if d < today:
            continue   # never freeze a past date after the fact
        for line in freeze_date(d, events, grouped[d], datetime.now(timezone.utc),
                                models=("v1", "v2") if model == "v2" else ("v1",), refresh=refresh):
            print(line)
    # 2) POST-FORECAST edge layer (Jupiter fetched once, after all freezes)
    if odds:
        from . import edge
        log = []
        jev = edge.fetch_lol_events(log)
        print(f"[edge] Jupiter lists {len(jev)} LoL events: " + "; ".join(e.get("metadata", {}).get("title", "?") for e in jev))
        for d in dates:
            if needs_edge(d, grouped[d]):
                try:
                    res = edge.run(d, model, jup_events=jev, jup_log=log)
                    print(f"[edge] {d}: coverage {res['coverage']}")
                except Exception as e:  # one bad date must not block the other pages
                    print(f"[edge] {d}: failed ({e})")
    # 3) dashboards (two passes so every page's date selector has the final counts)
    write_manifest(today, grouped)
    for d in dates:
        dashboard.render(d, model, write_index=False)
    write_manifest(today, grouped)
    dashboard.write_index(today)
    try:
        simexport.export(dates, model)
    except Exception as e:
        print(f"[window] sim export skipped: {e}")
    return dates
