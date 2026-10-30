"""Post-match calibration log (Brier / log loss / exact-score hit) for frozen forecasts. Market-free."""
import csv, json, math, os, tempfile
from . import config, schedule
from .forecast import load_frozen, frozen_path


def grade(date, events=None, version=None):
    if not frozen_path(date, version).exists():
        return []
    body = load_frozen(date, version)
    mv = body.get("model_version", "elo-map-v1")
    events = events if events is not None else schedule.fetch_all_events()
    res = {r["match_id"]: r for r in schedule.series_rows(events)}
    config.CALIB_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if config.CALIB_FILE.exists():
        with open(config.CALIB_FILE, newline="") as f:
            existing = list(csv.DictReader(f))
    by_key = {(row["match_id"], row.get("model_version") or "elo-map-v1"): row for row in existing}
    new = []
    for c in body["cards"]:
        mv = c.get("model_version", body.get("model_version", "elo-map-v1"))
        r = res.get(c["match_id"])
        if not r or r["state"] != "completed" or r["a_w"] is None or not c.get("sim"):
            continue
        # Result feeds can reverse teams after scheduling. Align identity before scoring.
        def matches(team, side):
            return (team.get("code", "").casefold() == str(r[side]).casefold()
                    or team.get("name", "").casefold() == str(r[side + "_name"]).casefold())
        if matches(c["team_a"], "a") and matches(c["team_b"], "b"):
            aw, bw = r["a_w"], r["b_w"]
        elif matches(c["team_a"], "b") and matches(c["team_b"], "a"):
            aw, bw = r["b_w"], r["a_w"]
        else:
            continue  # an ID alone is insufficient when participants do not match
        target = c["bo"] // 2 + 1
        if (not isinstance(aw, int) or not isinstance(bw, int) or min(aw, bw) < 0
                or max(aw, bw) != target or aw == bw or aw + bw > c["bo"]):
            continue
        y = int(aw > bw)
        p = min(max(c["sim"]["p_series_a"], 1e-6), 1 - 1e-6)
        actual = f'{aw}-{bw}'
        row = {"date": date, "match_id": c["match_id"], "series": f'{c["team_a"]["code"]} vs {c["team_b"]["code"]}',
                    "bo": c["bo"], "p_a": round(p, 4), "a_won": y, "brier": round((p - y) ** 2, 4),
                    "logloss": round(-(y * math.log(p) + (1 - y) * math.log(1 - p)), 4),
                    "locked": c["sim"]["locked_score"], "actual": actual,
                    "lock_hit": int(actual == c["sim"]["locked_score"]),
                    "p_actual_exact": round(c["sim"]["exact"].get(actual, 0), 4), "model_version": mv}
        key = (c["match_id"], mv)
        if {k: str(v) for k, v in row.items()} != by_key.get(key):
            by_key[key] = {k: str(v) for k, v in row.items()}
            new.append(row)
    if new:
        # Reconcile corrections in place instead of permanently skipping an already graded match.
        # Forecast files remain immutable; atomic replacement avoids partial calibration logs.
        fields = list(new[0])
        with tempfile.NamedTemporaryFile("w", newline="", dir=config.CALIB_FILE.parent, delete=False) as f:
            tmp = f.name
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(by_key.values())
        os.replace(tmp, config.CALIB_FILE)
    return new


def frozen_dates():
    """All persisted slate dates, including old unresolved games after a missed daily run."""
    import re
    return sorted({p.name[:10] for p in config.FORECAST_DIR.glob('*.json') if re.match(r'^\d{4}-\d{2}-\d{2}',p.name)})
