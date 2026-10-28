"""Post-match calibration log (Brier / log loss / exact-score hit) for frozen forecasts. Market-free."""
import csv, json, math
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
    done = set()
    if config.CALIB_FILE.exists():
        done = {(row["match_id"], row.get("model_version") or "elo-map-v1") for row in csv.DictReader(open(config.CALIB_FILE))}
    new = []
    for c in body["cards"]:
        r = res.get(c["match_id"])
        if not r or r["state"] != "completed" or r["a_w"] is None or (c["match_id"], mv) in done:
            continue
        y = 1 if r["a_w"] > r["b_w"] else 0
        p = min(max(c["sim"]["p_series_a"], 1e-6), 1 - 1e-6)
        actual = f'{r["a_w"]}-{r["b_w"]}'
        new.append({"date": date, "match_id": c["match_id"], "series": f'{c["team_a"]["code"]} vs {c["team_b"]["code"]}',
                    "bo": c["bo"], "p_a": round(p, 4), "a_won": y, "brier": round((p - y) ** 2, 4),
                    "logloss": round(-(y * math.log(p) + (1 - y) * math.log(1 - p)), 4),
                    "locked": c["sim"]["locked_score"], "actual": actual,
                    "lock_hit": int(actual == c["sim"]["locked_score"]),
                    "p_actual_exact": round(c["sim"]["exact"].get(actual, 0), 4), "model_version": mv})
    if new:
        write_header = not config.CALIB_FILE.exists()
        with open(config.CALIB_FILE, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(new[0].keys()))
            if write_header:
                w.writeheader()
            w.writerows(new)
    return new
