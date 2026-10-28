"""Series simulator: exact Bo1/Bo3/Bo5 score distributions from a map-win probability.

Per-simulation map probability is drawn from a rating-uncertainty distribution (logit-normal), and each map gets
an extra small logit shock (draft/side/Fearless variance), so the series distribution is wider than a naive
binomial with fixed p.
"""
import numpy as np


def simulate(p_map, bo, sims=100_000, seed=0, rating_sd=0.35, map_sd=0.25):
    rng = np.random.default_rng(seed)
    need = bo // 2 + 1
    base = np.log(p_map / (1 - p_map))
    series_logit = base + rng.normal(0, rating_sd, sims)
    aw = np.zeros(sims, dtype=np.int8); bw = np.zeros(sims, dtype=np.int8)
    for _ in range(bo):
        live = (aw < need) & (bw < need)
        if not live.any():
            break
        lg = series_logit + rng.normal(0, map_sd, sims)
        win = rng.random(sims) < 1 / (1 + np.exp(-lg))
        aw += (live & win); bw += (live & ~win)
    scores = {}
    for x in range(need + 1):
        for y in range(need + 1):
            if (x == need) ^ (y == need):
                scores[f"{x}-{y}"] = float(np.mean((aw == x) & (bw == y)))
    maps = aw.astype(int) + bw.astype(int)
    out = {
        "bo": bo, "sims": sims, "p_map_a": float(p_map),
        "p_series_a": float(np.mean(aw == need)),
        "exact": scores,
        "e_maps": float(maps.mean()),
        "maps_dist": {str(k): float(np.mean(maps == k)) for k in range(need, bo + 1)},
    }
    lock = max(scores, key=scores.get)
    out["locked_score"] = lock
    out["locked_score_prob"] = scores[lock]
    return out
