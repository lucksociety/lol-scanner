"""Ratings-only series simulator (primary headline from v2.1-ratings / v2.2).

Same draws as scanner.sim.simulate (identical RNG sequence, rating_sd 0.35 / map_sd 0.25, no side or draft term), plus
per-game win/reach probabilities so the edge layer can price single-game markets. scanner.sim is left untouched (v1).
"""
import numpy as np
from . import sim

RATING_SD, MAP_SD = 0.35, 0.25


def simulate(p_map, bo, sims=100_000, seed=0, rating_sd=RATING_SD, map_sd=MAP_SD):
    rng = np.random.default_rng(seed)
    need = bo // 2 + 1
    base = np.log(p_map / (1 - p_map))
    series_logit = base + rng.normal(0, rating_sd, sims)
    aw = np.zeros(sims, dtype=np.int8); bw = np.zeros(sims, dtype=np.int8)
    reach, gwin = [0.0] * bo, [0.0] * bo
    for g in range(bo):
        live = (aw < need) & (bw < need)
        if not live.any():
            break
        lg = series_logit + rng.normal(0, map_sd, sims)
        win = rng.random(sims) < 1 / (1 + np.exp(-lg))
        reach[g] = float(live.sum()); gwin[g] = float((live & win).sum())
        aw += (live & win); bw += (live & ~win)
    scores = {}
    for x in range(need + 1):
        for y in range(need + 1):
            if (x == need) ^ (y == need):
                scores[f"{x}-{y}"] = float(np.mean((aw == x) & (bw == y)))
    maps = aw.astype(int) + bw.astype(int)
    lock = max(scores, key=scores.get)
    return {"bo": bo, "sims": sims, "p_map_a": float(p_map), "p_series_a": float(np.mean(aw == need)), "exact": scores,
            "e_maps": float(maps.mean()), "maps_dist": {str(k): float(np.mean(maps == k)) for k in range(need, bo + 1)},
            "locked_score": lock, "locked_score_prob": scores[lock],
            "p_game_a": [gwin[g] / reach[g] if reach[g] else None for g in range(bo)],
            "p_game_reached": [reach[g] / sims for g in range(bo)],
            "simulator": "ratings-only (rating_sd %.2f, map_sd %.2f; no side/draft term)" % (rating_sd, map_sd)}


assert sim.simulate.__defaults__[-2:] == (RATING_SD, MAP_SD)   # keep in lockstep with the backtested v1 simulator
