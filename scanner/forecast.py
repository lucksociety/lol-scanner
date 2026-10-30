"""Stage 1: independent forecast. Builds and FREEZES the slate forecast before any market data exists.

Integrity: this module imports nothing from scanner.edge and never touches odds. Inputs are the lolesports
schedule/results feed only. Output is written once per date (immutable) with a SHA-256 over its content.
"""
import hashlib, json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from . import config, schedule, ratings, sim

ET = ZoneInfo(config.TZ)
INTEGRITY = {
    "protocol": "Zero-Market Influence Prediction Protocol v4.0",
    "market_inputs_to_forecast": "NONE",
    "data_sources": ["lolesports.com persisted gateway: getSchedule (results + schedule)"],
    "excluded": ["sportsbook odds", "prediction-market prices", "betting sites/ads", "tipster picks"],
}


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


VERSION_SUFFIX = {"v1": "", "v2": ".v2"}


def version_of(body):
    return "v2" if str(body.get("model_version", "")).startswith("v2") else "v1"


def frozen_path(date, version=None):
    """v1 -> forecast_log/<date>.json (unchanged); v2 -> forecast_log/<date>.v2.json.
    version=None resolves to the primary model (config.MODEL_VERSION) if frozen, else the v1 file."""
    if version is None:
        v = config.MODEL_VERSION
        p = config.FORECAST_DIR / f"{date}{VERSION_SUFFIX[v]}.json"
        return p if p.exists() else config.FORECAST_DIR / f"{date}.json"
    return config.FORECAST_DIR / f"{date}{VERSION_SUFFIX[version]}.json"


def _et_date(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).date().isoformat()


def _form(rows, team, before, n=8):
    out = []
    for r in reversed(rows):
        if r["start"] >= before or r["state"] != "completed" or r["a_w"] is None:
            continue
        if team in (r["a_name"], r["b_name"]):
            me_a = r["a_name"] == team
            w, l = (r["a_w"], r["b_w"]) if me_a else (r["b_w"], r["a_w"])
            out.append({"date": _et_date(r["start"]), "opp": r["b"] if me_a else r["a"], "score": f"{w}-{l}",
                        "won": w > l, "league": r["league"]})
            if len(out) >= n:
                break
    return out


def _event_record(rows, team, league, block, before, year):
    w = l = 0
    for r in rows:
        if (r["league"] == league and r["block"] == block and r["state"] == "completed" and r["start"] < before
                and r["start"][:4] == year and team in (r["a_name"], r["b_name"]) and r["a_w"] is not None):
            me_a = r["a_name"] == team
            won = (r["a_w"] > r["b_w"]) if me_a else (r["b_w"] > r["a_w"])
            w += won; l += (not won)
    return f"{w}-{l}"


def _flags(R, a, b, pm, fa, fb, row):
    """Adversarial review: reasons the number could be wrong (auto-generated, model-honest)."""
    f = []
    for t, form in ((a, fa), (b, fb)):
        if R.n.get(t, 0) < 40:
            f.append(f"{t}: thin sample ({R.n.get(t, 0)} maps in history) -> rating prior-dominated")
        if form and sum(x["league"] in ratings.INTL for x in form) == 0:
            f.append(f"{t}: no recent international series; cross-region strength is inferred")
    if R.home.get(a) != R.home.get(b):
        f.append(f"Cross-region matchup ({R.home.get(a)} vs {R.home.get(b)}): largest source of Elo error")
    if 0.42 < pm < 0.58:
        f.append("Near coin-flip per map: exact-score lock has low hit-rate by construction")
    if row["bo"] == 1:
        f.append("Bo1: single draft/side variance dominates")
    f.append("Model is results-only Elo: no draft/patch/roster-news adjustment (disclosed limitation)")
    return f


def build_forecast(date, now=None, events=None, only=None):
    now = now or datetime.now(timezone.utc)
    events = events if events is not None else schedule.fetch_all_events()
    rows = schedule.series_rows(events)
    slate = [r for r in rows if _et_date(r["start"]) == date and r["a"] != "TBD" and r["b"] != "TBD"
             and (only is None or r["match_id"] in only)]
    cards = []
    for i, row in enumerate(slate):
        cutoff = min(row["start"], now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        R, ll, nmaps = ratings.build(rows, cutoff)
        a, b = row["a_name"], row["b_name"]
        ra, rb = R.get(a, row["league"]), R.get(b, row["league"])
        pm = ratings.p_map(ra, rb)
        s = sim.simulate(pm, row["bo"], sims=config.SIMS, seed=config.SEED + i)
        fa, fb = _form(rows, a, cutoff), _form(rows, b, cutoff)
        year = row["start"][:4]
        cards.append({
            "match_id": row["match_id"], "start_utc": row["start"],
            "start_et": datetime.fromisoformat(row["start"].replace("Z", "+00:00")).astimezone(ET).strftime("%a %b %d, %I:%M %p ET"),
            "league": row["league"], "stage": row["block"], "bo": row["bo"],
            "team_a": {"code": row["a"], "name": a, "img": row["a_img"], "elo": round(ra, 1), "home": R.home.get(a),
                        "event_record": _event_record(rows, a, row["league"], row["block"], cutoff, year), "form": fa},
            "team_b": {"code": row["b"], "name": b, "img": row["b_img"], "elo": round(rb, 1), "home": R.home.get(b),
                        "event_record": _event_record(rows, b, row["league"], row["block"], cutoff, year), "form": fb},
            "model": {"p_map_a": round(pm, 4), "elo_diff": round(ra - rb, 1), "train_maps": nmaps,
                      "train_logloss": round(ll, 4), "history_cutoff_utc": cutoff},
            "sim": s,
            "adversarial_review": _flags(R, a, b, pm, fa, fb, row),
        })
    body = {"date": date, "integrity": INTEGRITY, "model_version": "elo-map-v1", "cards": cards}
    return body


def addendum_paths(date, version=None):
    """Addenda: series that appeared on the schedule (e.g. TBD -> teams set) after <date> was first frozen.
    Each addendum is its own immutable, hash-stamped freeze: forecast_log/<date>[.v2].addN.json."""
    base = frozen_path(date, version)
    stem = base.name[:-len(".json")]
    return sorted(base.parent.glob(f"{stem}.add*.json"), key=lambda q: int(q.name[len(stem) + 4:-5] or 0))


def _stamp(body, now):
    body = dict(body)
    body["frozen_at_utc"] = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    body["frozen_at_et"] = now.astimezone(ET).strftime("%Y-%m-%d %I:%M:%S %p ET")
    body["sha256"] = canonical_hash({k: v for k, v in body.items() if k != "sha256"})
    return body


def _log(body, p, event="FROZEN"):
    with open(config.FORECAST_DIR / "forecast_log.jsonl", "a") as f:
        f.write(json.dumps({"event": event, "date": body["date"], "frozen_at_utc": body["frozen_at_utc"],
                            "sha256": body["sha256"], "n_series": len(body["cards"]),
                            "model_version": body.get("model_version"), "file": p.name,
                            "locks": {c["match_id"]: f'{c["team_a"]["code"]} vs {c["team_b"]["code"]} -> {c["sim"]["locked_score"]}'
                                      for c in body["cards"]},
                            "market_inputs_to_forecast": "NONE"}) + "\n")


def freeze(body, now=None):
    """Write the forecast once. Refuses to overwrite an existing frozen file."""
    now = now or datetime.now(timezone.utc)
    p = frozen_path(body["date"], version_of(body))
    if p.exists():
        raise FileExistsError(f"{p} already frozen; forecasts are immutable")
    config.FORECAST_DIR.mkdir(parents=True, exist_ok=True)
    body = _stamp(body, now)
    p.write_text(json.dumps(body, indent=2))
    _log(body, p)
    return body


def freeze_addendum(body, now=None, revision=False):
    """Freeze series that were not on the date's original frozen slate. Never touches the base file or earlier
    addenda; duplicate series require an explicit, pre-start-only revision."""
    now = now or datetime.now(timezone.utc)
    ver = version_of(body)
    base = frozen_path(body["date"], ver)
    if not base.exists():
        raise FileNotFoundError(f"{base} missing: freeze the base slate first")
    have = load_frozen(body["date"], ver)
    dup = {c["match_id"] for c in body["cards"]} & {c["match_id"] for c in have["cards"]}
    if dup and not revision:
        raise FileExistsError(f"series {sorted(dup)} already frozen for {body['date']}; forecasts are immutable")
    if revision:
        # An append-only replacement is only legal before the series starts.
        for c in body["cards"] + body.get("skipped", []):
            start = datetime.fromisoformat(c["start_utc"].replace("Z", "+00:00"))
            cutoff = c.get("model", {}).get("history_cutoff_utc")
            if start <= now or (cutoff and datetime.fromisoformat(cutoff.replace("Z", "+00:00")) > now):
                raise ValueError("forecast revisions must be frozen before start using only available history")
        body = {**body, "revision": "pre-start primary/data-day upgrade or missing-history retry", "supersedes_sha256": (have.get("addenda") or [{"sha256": have["sha256"]}])[-1]["sha256"]}
    n = len(addendum_paths(body["date"], ver)) + 1
    p = base.with_name(f"{base.name[:-5]}.add{n}.json")
    body = _stamp({**body, "addendum": n}, now)
    p.write_text(json.dumps(body, indent=2))
    _log(body, p, "FROZEN_ADDENDUM")
    return body


def _verified(p, date):
    body = json.loads(p.read_text())
    want = body.get("sha256")
    got = canonical_hash({k: v for k, v in body.items() if k != "sha256"})
    if not body.get("frozen_at_utc") or want != got:
        raise RuntimeError(f"forecast {date} ({p.name}) is not frozen or hash mismatch ({want} != {got})")
    return body


def load_frozen(date, version=None):
    """Read-only loader with hash verification (used by edge.py and dashboard.py). Addenda (series frozen later
    for the same date) are verified one by one and merged in; each card keeps its own freeze time."""
    snapshots = [(frozen_path(date, version), _verified(frozen_path(date, version), date))]
    snapshots += [(p, _verified(p, date)) for p in addendum_paths(date, version)]
    body = dict(snapshots[0][1])
    cards, skipped = {}, {}
    body["addenda"] = []
    for i, (path, snap) in enumerate(snapshots):
        if i:
            body["addenda"].append({"file": path.name, "frozen_at_utc": snap["frozen_at_utc"],
                "frozen_at_et": snap["frozen_at_et"], "sha256": snap["sha256"], "n_series": len(snap["cards"])})
        for card in snap["cards"]:
            mid = card["match_id"]
            if mid not in cards or snap.get("revision"):
                cards[mid] = {**card, "model_version": snap["model_version"],
                    "frozen_at_et": snap["frozen_at_et"], "frozen_at_utc": snap["frozen_at_utc"]}
                skipped.pop(mid, None)
        for skip in snap.get("skipped", []):
            if skip["match_id"] not in cards:
                skipped[skip["match_id"]] = {**skip, "model_version": snap["model_version"], "frozen_at_utc": snap["frozen_at_utc"]}
    body["cards"] = sorted(cards.values(), key=lambda c: c.get("start_utc", ""))
    body["skipped"] = list(skipped.values())
    latest = snapshots[-1][1]
    body["frozen_at_utc_latest"] = latest["frozen_at_utc"]
    # Individual cards retain their source timestamp/version, including mixed slates.
    if latest.get("revision"):
        body.update({k: latest[k] for k in ("model_version", "frozen_at_utc", "frozen_at_et", "params") if k in latest})
    return body


def frozen_ids(date, version=None):
    """match_ids already frozen (cards + skipped/not-modeled) for a date, or an empty set."""
    if not frozen_path(date, version).exists():
        return set()
    b = load_frozen(date, version)
    return {c["match_id"] for c in b["cards"]} | {s["match_id"] for s in b.get("skipped", [])}
