"""LoL Esports schedule/results (public lolesports gateway). Market-free data source."""
import json, time, urllib.request, urllib.parse
from . import config

UA = "lol-scanner/1.0 (+github actions)"


def _get(path, **params):
    if not config.LOLESPORTS_API_KEY:
        raise SystemExit("LOLESPORTS_API_KEY is not set (see README: Configuration)")
    url = f"{config.LOLESPORTS_BASE}/{path}?" + urllib.parse.urlencode({"hl": "en-US", **params})
    req = urllib.request.Request(url, headers={"x-api-key": config.LOLESPORTS_API_KEY, "User-Agent": UA})
    last = None
    for i in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception as e:  # noqa
            last = e; time.sleep(1.5 * (i + 1))
    raise last


def fetch_all_events(since=None, max_pages=80):
    """Walk getSchedule pages backwards until `since` (ISO date). Returns list of match events."""
    since = since or config.HISTORY_START
    cache = config.CACHE_DIR / "schedule_pages"
    cache.mkdir(parents=True, exist_ok=True)
    events, token, pages = [], None, 0
    while pages < max_pages:
        d = _get("getSchedule", **({"pageToken": token} if token else {}))["data"]["schedule"]
        evs = [e for e in d["events"] if e.get("type") == "match" and e.get("match")]
        events += evs
        pages += 1
        token = d["pages"].get("older")
        if not token or (evs and min(e["startTime"] for e in evs) < since):
            break
    seen, out = set(), []
    for e in events:
        mid = e["match"]["id"]
        if mid in seen:
            continue
        seen.add(mid); out.append(e)
    out.sort(key=lambda e: e["startTime"])
    (cache / "events.json").write_text(json.dumps(out))
    return [e for e in out if e["startTime"] >= since]


def series_rows(events):
    """Flatten events into dict rows."""
    rows = []
    for e in events:
        m = e["match"]; t = m["teams"]
        if len(t) != 2:
            continue
        rows.append({
            "match_id": m["id"], "start": e["startTime"], "state": e["state"],
            "league": e["league"]["name"], "block": e.get("blockName") or "",
            "bo": (m.get("strategy") or {}).get("count") or 1,
            "a": t[0]["code"], "b": t[1]["code"], "a_name": t[0]["name"], "b_name": t[1]["name"],
            "a_img": t[0].get("image"), "b_img": t[1].get("image"),
            "a_w": (t[0].get("result") or {}).get("gameWins"), "b_w": (t[1].get("result") or {}).get("gameWins"),
            "a_rec": t[0].get("record"), "b_rec": t[1].get("record"),
        })
    return rows
