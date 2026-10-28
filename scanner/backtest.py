"""Walk-forward backtest of the market-free model on international series (honest disclosure numbers)."""
import json, math
from . import config, schedule, ratings, sim


def run(events=None, leagues=("Worlds", "MSI", "First Stand"), since="2025-06-01"):
    events = events if events is not None else json.loads((config.CACHE_DIR / "schedule_pages/events.json").read_text())
    rows = schedule.series_rows(events)
    out, br, ll, hit_side, hit_lock = [], 0, 0, 0, 0
    for r in rows:
        if r["league"] not in leagues or r["state"] != "completed" or r["start"] < since or r["a_w"] is None:
            continue
        if r["a_w"] + r["b_w"] == 0:
            continue
        R, _, _ = ratings.build(rows, r["start"])
        pm = ratings.p_map(R.get(r["a_name"], r["league"]), R.get(r["b_name"], r["league"]))
        s = sim.simulate(pm, r["bo"], sims=20000, seed=1)
        y = 1 if r["a_w"] > r["b_w"] else 0
        p = min(max(s["p_series_a"], 1e-4), 1 - 1e-4)
        br += (p - y) ** 2; ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        hit_side += (p >= .5) == (y == 1)
        hit_lock += s["locked_score"] == f'{r["a_w"]}-{r["b_w"]}'
        out.append(r)
    n = len(out)
    return {"series": n, "brier": round(br / n, 4), "logloss": round(ll / n, 4),
            "side_hit": round(hit_side / n, 3), "exact_lock_hit": round(hit_lock / n, 3),
            "scope": f"{'/'.join(leagues)} since {since}, walk-forward (ratings refit before each series)"}
