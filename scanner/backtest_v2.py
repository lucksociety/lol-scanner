"""Walk-forward backtest: v1 vs v2 on the SAME international series (Worlds / MSI / First Stand since 2025-06-01).

Per test event, every v2 parameter (Elo patch-decay lambda + K-scale, draft model, draft-score/side/continuity
regression) is refit using only data before the event's first day; pools/meta/continuity then update game by
game with results strictly before each series' day. v1 is the frozen v1 code path, unchanged.
"""
import json, math, time
from collections import defaultdict
import numpy as np
from . import config, schedule, ratings, sim, sim_v2, patches, v2, draftmodel as dm

EVENT_CUTOFFS = {"MSI 2025": "2025-06-27", "Worlds 2025": "2025-10-14", "First Stand 2026": "2026-03-16",
                 "MSI 2026": "2026-06-28", "Worlds 2026": "2026-10-15"}


def _metrics(recs, key):
    n = len(recs); br = ll = hs = hl = 0.0
    for r in recs:
        p = min(max(r[key]["p"], 1e-4), 1 - 1e-4); y = r["y"]
        br += (p - y) ** 2; ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        hs += (p >= .5) == (y == 1); hl += r[key]["lock"] == r["score"]
    return {"series": n, "brier": round(br / n, 4), "logloss": round(ll / n, 4),
            "winner_acc": round(hs / n, 3), "exact_lock_hit": round(hl / n, 3)}


def run(sims=10000, since="2025-06-01", leagues=("Worlds", "MSI", "First Stand"), out_path=None):
    t0 = time.time()
    events = json.loads((config.CACHE_DIR / "schedule_pages/events.json").read_text())
    rows = schedule.series_rows(events)
    cal = patches.load(); games = v2.load_corpus()
    E = v2.Engine(games, rows, cal)
    by_mid = {}
    for k, r in E.join.items():
        by_mid[r["match_id"]] = k
    tests = [r for r in rows if r["league"] in leagues and r["state"] == "completed" and r["start"] >= since
             and r["a_w"] is not None and r["a_w"] + r["b_w"] > 0]
    cuts = sorted(EVENT_CUTOFFS.items(), key=lambda kv: kv[1])
    recs, fits, held = [], {}, defaultdict(list)
    cur = None
    for r in sorted(tests, key=lambda x: x["start"]):
        ev = [n for n, c in cuts if c <= r["start"][:10]][-1]
        if ev != cur:
            cur = ev; cut = EVENT_CUTOFFS[ev]
            fits[ev] = E.fit(cut)
            fits[ev] = {k: v for k, v in fits[ev].items()}
        day = r["start"][:10]
        # advance stats game by game, scoring the draft model on held-out event games before adding them
        while E._cursor < len(E.games) and E.games[E._cursor]["date"] < day:
            g = E.games[E._cursor]
            if g["date"] >= EVENT_CUTOFFS[cur]:
                pb, pr = v2.lineup_of(g, "blue"), v2.lineup_of(g, "red")
                cb = dm.team_context(E.S, g["blue"], pb, pr, g["patch"]); cr = dm.team_context(E.S, g["red"], pr, pb, g["patch"])
                held[cur].append((g, cb, cr, {E.U.i(c) for c in g["locked"]} - {None}))
            E.S.add(g); E._cursor += 1
        y = 1 if r["a_w"] > r["b_w"] else 0
        score = f'{r["a_w"]}-{r["b_w"]}'
        # ---- v1 (unchanged code path)
        R1, _, _ = ratings.build(rows, r["start"])
        pm1 = ratings.p_map(R1.get(r["a_name"], r["league"]), R1.get(r["b_name"], r["league"]))
        s1 = sim.simulate(pm1, r["bo"], sims=20000, seed=1)
        # ---- v2 Elo only (patch-recency ratings, v1 simulator) : ablation
        ra, rb, P = E.pre[r["match_id"]]
        s15 = sim.simulate(ratings.p_map(ra, rb), r["bo"], sims=20000, seed=1)
        # ---- v2 full
        ta, tb = E.golgg_name(r["a_name"]), E.golgg_name(r["b_name"])
        la = E.last_lineup(ta, day)[0] if ta else None
        lb = E.last_lineup(tb, day)[0] if tb else None
        sk = by_mid.get(r["match_id"]); g1 = None
        if sk:
            g0 = E.series[sk][0]
            g1 = E.names.get(g0["blue"]) == r["a_name"]
        Pp = P if E.S.games.get(P) else (E.S.prev_patch(P) or P)
        sp = E.spec((ra - rb) * v2.LN10_400, r["bo"], r["bo"] > 1, Pp, ta or "?", tb or "?",
                    [(1.0, la or [None] * 5)], [(1.0, lb or [None] * 5)], g1)
        s2 = sim_v2.simulate(sp, sims=sims, seed=1, track_drafts=False)
        recs.append({"match_id": r["match_id"], "start": r["start"], "event": cur, "a": r["a_name"], "b": r["b_name"],
                     "bo": r["bo"], "score": score, "y": y, "joined": bool(sk), "lineups_known": bool(la and lb),
                     "v1": {"p": s1["p_series_a"], "lock": s1["locked_score"]},
                     "v2_elo_only": {"p": s15["p_series_a"], "lock": s15["locked_score"]},
                     "v2": {"p": s2["p_series_a"], "lock": s2["locked_score"]}})
    res = {"scope": f"{'/'.join(leagues)} since {since}; walk-forward per event", "sims": {"v1": 20000, "v2_elo_only": 20000, "v2": sims},
           "v1": _metrics(recs, "v1"), "v2_elo_only": _metrics(recs, "v2_elo_only"), "v2": _metrics(recs, "v2"),
           "coverage": {"series": len(recs), "g1_side_from_data": sum(r["joined"] for r in recs),
                        "lineups_known": sum(r["lineups_known"] for r in recs)},
           "by_event": {ev: {k: _metrics([x for x in recs if x["event"] == ev], k) for k in ("v1", "v2_elo_only", "v2")}
                        for ev in dict.fromkeys(x["event"] for x in recs)},
           "fits": {ev: {"elo_lambda": f["elo_lambda"], "elo_kscale": f["elo_kscale"], "draft_w_pick": f["draft"]["w_pick"],
                         "draft_w_ban": f["draft"]["w_ban"], "outcome": f["outcome"], "q_loser_blue": f["q_loser_blue"]}
                    for ev, f in fits.items()},
           "draft_model_heldout": {ev: dm.eval_draft(items, E.U, fits[ev]["draft"]["w_pick"], fits[ev]["draft"]["w_ban"])
                                   for ev, items in held.items() if items},
           "fearless_check": {k: v for k, v in E.fearless.items() if "Worlds 2026" in k or not v["fearless"]},
           "seconds": round(time.time() - t0, 1), "series_detail": recs}
    if out_path:
        out_path.write_text(json.dumps(res, indent=1, default=float))
    return res


if __name__ == "__main__":
    import sys
    p = config.ROOT / "calibration" / "backtest_v1_vs_v2.json"
    r = run(sims=int(sys.argv[1]) if len(sys.argv) > 1 else 10000, out_path=p)
    print(json.dumps({k: v for k, v in r.items() if k != "series_detail"}, indent=1, default=float))
