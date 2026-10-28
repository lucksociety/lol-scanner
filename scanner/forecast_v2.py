"""Stage 1 (model v2): independent forecast with patch, lineups and draft paths. Frozen BEFORE any market data.

Integrity: imports nothing from scanner.edge; inputs are lolesports (schedule/results, official rosters, game
sides), gol.gg (pro game drafts, lineups, patch, champion stats) and Riot Data Dragon (champion tags). No odds.
"""
import json, math
from collections import Counter, defaultdict
from datetime import datetime, timezone
import numpy as np
from . import config, schedule, ratings, patches, golgg, corpus, v2, sim_v2, draftmodel as dm
from .forecast import ET, INTEGRITY, _et_date, _form, _event_record, freeze  # noqa: F401  (same freeze/hash path)

MODEL_VERSION = "v2.1-patch-lineup-draft"   # v2.1 (2026-10-28 audit): cross-region Elo tuning/K, no continuity term
MODEL_VERSIONS = {"v2": "v2-patch-lineup-draft", "v2.1": MODEL_VERSION}
INTEGRITY_V2 = dict(INTEGRITY)
INTEGRITY_V2["data_sources"] = [
    "lolesports.com persisted gateway: getSchedule (results + schedule), getTeams (official rosters), "
    "getEventDetails (game sides)",
    "gol.gg: pro game pages (patch, bans/picks, lineups, KDA) and per-patch champion statistics",
    "Riot Data Dragon: champion tags (AP/AD, tank)",
]
ROLE_LS = {"top": 0, "jungle": 1, "mid": 2, "bottom": 3, "support": 4}


# ---------------------------------------------------------------- lineups
def official_roster(teams, code, name):
    """All active lolesports teams with this code (main + academy share codes); disambiguated in lineup_status."""
    return [t for t in teams if t.get("code") == code and t.get("status") == "active"]


class NoHistory(Exception):
    """A team has no pro games in the corpus, so there is no lineup / draft-pool basis to simulate it."""


def lineup_status(E, gname, roster, event_tournaments, before_date):
    """Per role: expected starter + status. CONFIRMED = on the official roster and started every game of this event
    in that role; PROBABLE = started the latest game but not every event game (or not matched to the roster);
    UNCERTAIN = another player started one of the team's last 3 event games in that role -> both simulated."""
    ev = [g for g in E.games if g["tournament"] in event_tournaments and g["date"] < before_date and gname in (g["blue"], g["red"])]
    if not ev:
        last, _ = E.last_lineup(gname, before_date)
        ev = [g for g in E.games if g["date"] < before_date and gname in (g["blue"], g["red"])][-5:]
    names = {}
    starts = [defaultdict(float) for _ in range(5)]
    seq = []
    for i, g in enumerate(ev):
        side = "blue" if g["blue"] == gname else "red"
        pl = sorted((p for p in g["players"] if p["side"] == side), key=lambda p: dm.ROLES.index(p["role"]))
        seq.append([p["pid"] for p in pl])
        for r, p in enumerate(pl):
            names[p["pid"]] = p["player"]; starts[r][p["pid"]] += 0.8 ** (len(ev) - 1 - i)
    seen = {golgg.norm(n) for n in names.values()}
    rosters = [{golgg.norm(p.get("summonerName", "")) for p in t.get("players", [])} for t in (roster or [])]
    roster_names = max(rosters, key=lambda rs: len(rs & seen), default=set())
    if not seq or any(len(x) < 5 for x in seq[-1:]):
        raise NoHistory(gname)
    out = []
    for r in range(5):
        latest = seq[-1][r]
        all_same = all(s[r] == latest for s in seq)
        recent_alt = {s[r] for s in seq[-3:]} - {latest}
        on_roster = golgg.norm(names[latest]) in roster_names
        if recent_alt:
            status = "UNCERTAIN"
        elif all_same and on_roster:
            status = "CONFIRMED"
        else:
            status = "PROBABLE"
        tot = sum(starts[r].values())
        alts = sorted(((pid, w / tot) for pid, w in starts[r].items()), key=lambda x: -x[1])
        out.append({"role": dm.ROLES[r], "pid": latest, "name": names[latest], "status": status,
                    "on_official_roster": on_roster, "event_games": len(seq),
                    "starts_this_event": sum(1 for s in seq if s[r] == latest),
                    "alternatives": [{"pid": p, "name": names[p], "w": round(w, 3)} for p, w in alts if p != latest]
                    if status == "UNCERTAIN" else []})
    return out


def lineup_combos(st):
    """Weighted lineups for the sim: the expected five plus, for each UNCERTAIN role, the alternative five."""
    base = [s["pid"] for s in st]
    combos = []
    unc = [(r, s) for r, s in enumerate(st) if s["status"] == "UNCERTAIN" and s["alternatives"]]
    w_base = 1.0
    for r, s in unc:
        alt = s["alternatives"][0]; w = min(alt["w"], 0.5)
        lu = list(base); lu[r] = alt["pid"]
        combos.append((w, lu, f'{alt["name"]} in for {s["name"]} ({s["role"]})'))
        w_base -= w
    w_base = max(w_base, 0.2)
    return [(w_base, base, "expected five")] + combos


# ---------------------------------------------------------------- meta from gol.gg champion stats
def external_meta(E, P):
    roles = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]
    C = E.U.C
    rc = np.zeros((5, C, 2)); bans = np.zeros(C)
    for r, role in enumerate(roles):
        for row in golgg.champion_table(P, role):
            ci = E.U.i(row["champ"])
            if ci is not None:
                rc[r, ci, 0] += row["picks"]; rc[r, ci, 1] += row["wins"]
    allt = golgg.champion_table(P, "ALL")
    for row in allt:
        ci = E.U.i(row["champ"])
        if ci is not None:
            bans[ci] += row["bans"]
    G = sum(x["picks"] for x in allt) / 10.0
    prev = E.S.prev_patch(P)
    if prev:
        rc += dm.PREV_W * E.S.rc[prev]; bans += dm.PREV_W * E.S.bans[prev]; G += dm.PREV_W * E.S.games[prev]
    share, wrdev, presence = dm.meta_arrays(rc, bans, G)
    top = {}
    for r in range(5):
        n = rc[r, :, 0]
        ix = np.argsort(-n)[:5]
        top[dm.ROLES[r]] = [{"champ": E.U.name[i], "games": round(float(n[i]), 1),
                             "wr_shrunk": round(float(0.5 + wrdev[r, i]), 3)} for i in ix]
    return {"patch": P, "share": share, "wrdev": wrdev, "presence": presence}, \
           {"patch": P, "prev_patch_blend": prev, "prev_weight": dm.PREV_W, "games_on_patch_golgg": round(G, 1), "top_by_role": top}


def g1_side(match_id, code_a):
    """True if team A is blue in game 1 per the official event details; None if unknown."""
    try:
        d = schedule._get("getEventDetails", id=match_id)["data"]["event"]["match"]
    except Exception:
        return None, None
    ids = {t["id"]: t.get("code") for t in d.get("teams", [])}
    for g in d.get("games", []):
        if g.get("number") == 1:
            for t in g.get("teams", []):
                if t.get("side") == "blue":
                    return ids.get(t["id"]) == code_a, "lolesports getEventDetails game 1 sides"
    return None, None


def _names(E, idx):
    return [E.U.name[i] for i in idx]


def draft_summary(E, sp, out, combo, base_logit, a_code, b_code, g1_a_blue):
    """Top drafts per map (beam search) along the modal path + per-role pick marginals from the simulation."""
    games = []
    locked = set(); a_blue = True if g1_a_blue is None else bool(g1_a_blue)
    for g in range(sp["bo"]):
        if out["p_game_reached"][g] == 0:
            break
        bd = v2.beam_drafts(combo, a_blue, locked if sp["fearless"] else set(), sp["comp_coef"], sp["U_ap"], sp["U_tank"],
                            base_logit, sp["side_blue"], sp["rho"])
        rm = np.array(out["role_marginals"][g])
        marg = {}
        for t, code in ((0, a_code), (1, b_code)):
            marg[code] = {dm.ROLES[r]: [{"champ": E.U.name[i], "p": round(float(rm[t, r, i]), 3)}
                                        for i in np.argsort(-rm[t, r])[:3]] for r in range(5)}
        games.append({"game": g + 1, "p_reached": round(out["p_game_reached"][g], 4),
                      "p_win_a_sim": round(out["p_game_a"][g], 4),
                      "assumed_sides": f"{a_code if a_blue else b_code} blue",
                      "fearless_locked_before": len(locked) if sp["fearless"] else 0,
                      "top_drafts": [{"seq_prob": d["p"], "win_a": round(d["win_a"], 4), "draft_delta_logit": round(d["draft_delta"], 3),
                                      "picks": {a_code: _names(E, d["picks_a"]), b_code: _names(E, d["picks_b"])},
                                      "bans": {a_code: _names(E, d["bans_a"]), b_code: _names(E, d["bans_b"])}} for d in bd],
                      "role_pick_marginals": marg})
        if not bd:
            break
        top = bd[0]
        locked |= set(top["picks_a"]) | set(top["picks_b"])
        a_won = top["win_a"] >= 0.5
        loser_is_a = not a_won
        loser_blue = sp["q_loser_blue"] >= 0.5
        a_blue = loser_blue if loser_is_a else (not loser_blue)
    return games


def build_forecast(date, now=None, events=None, sims=None, refresh_corpus=True, only=None, variant=None):
    now = now or datetime.now(timezone.utc)
    sims = sims or config.SIMS
    events = events if events is not None else schedule.fetch_all_events()
    rows = schedule.series_rows(events)
    if refresh_corpus:
        corpus.build(refresh_live=True)
    cal = patches.load(); games = v2.load_corpus()
    E = v2.Engine(games, rows, cal)
    params = E.fit(date, variant=variant)
    try:
        teams = schedule._get("getTeams")["data"]["teams"]
    except Exception:
        teams = json.loads((config.CACHE_DIR / "teams_all.json").read_text())["data"]["teams"]
    slate = [r for r in rows if _et_date(r["start"]) == date and r["a"] != "TBD" and r["b"] != "TBD"
             and (only is None or r["match_id"] in only)]
    live_patch = cal.on(date)
    try:
        ext, meta_info = external_meta(E, live_patch)
        E.S.external_meta = ext
    except Exception as e:  # gol.gg unreachable -> corpus-only meta (disclosed on the card)
        meta_info = {"patch": live_patch, "error": f"gol.gg champion stats unavailable ({e}); meta from corpus games"}
    ev_trs = [t for names in corpus.EVENTS.values() for t in names if t.startswith("Worlds 2026")]
    cards, skipped = [], []
    for i, row in enumerate(slate):
        cutoff = min(row["start"], now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        R1, _, _ = ratings.build(rows, cutoff)
        a, b = row["a_name"], row["b_name"]
        ra, rb, P = E.pre[row["match_id"]]
        P = live_patch
        ga, gb = E.golgg_name(a, date), E.golgg_name(b, date)
        try:
            sa = lineup_status(E, ga, official_roster(teams, row["a"], a), ev_trs, date)
            sb = lineup_status(E, gb, official_roster(teams, row["b"], b), ev_trs, date)
        except NoHistory as e:  # not modeled -> no card, so no bet can be recommended on it
            skipped.append({"match_id": row["match_id"], "series": f'{row["a"]} vs {row["b"]}', "start_utc": row["start"],
                            "reason": f"no pro-game history in the gol.gg corpus for {row['a'] if (ga is None or e.args[0] == ga) else row['b']}"
                                      f"{'' if e.args[0] else ' (team not mapped to gol.gg)'}; not modeled (PASS)"})
            continue
        ca, cb = lineup_combos(sa), lineup_combos(sb)
        g1, g1_src = g1_side(row["match_id"], row["a"])
        elo_logit = (ra - rb) * v2.LN10_400
        sp = E.spec(elo_logit, row["bo"], row["bo"] > 1, P, ga, gb, [(w, l) for w, l, _ in ca], [(w, l) for w, l, _ in cb], g1)
        out = sim_v2.simulate(sp, sims=sims, seed=config.SEED + i)
        best = max(range(len(sp["combos"])), key=lambda k: sp["combos"][k]["w"])
        drafts = draft_summary(E, sp, out, sp["combos"][best], elo_logit, row["a"], row["b"], g1)
        combos_desc = [{"w": round(wa * wb, 3), "a": da, "b": db, "share_in_sims": round(out["lineup_combo_share"][k], 4)}
                       for k, ((wa, _, da), (wb, _, db)) in enumerate((x, y) for x in ca for y in cb)]
        simout = {k: v for k, v in out.items() if k not in ("drafts", "role_marginals")}
        simout["p_map_a_by_game"] = [round(x, 4) for x in out.get("p_map_a_by_game", [])]
        o = params["outcome"]
        cards.append({
            "match_id": row["match_id"], "start_utc": row["start"],
            "start_et": datetime.fromisoformat(row["start"].replace("Z", "+00:00")).astimezone(ET).strftime("%a %b %d, %I:%M %p ET"),
            "league": row["league"], "stage": row["block"], "bo": row["bo"],
            "team_a": {"code": row["a"], "name": a, "img": row["a_img"], "elo_v2": round(ra, 1), "elo_v1": round(R1.get(a, row["league"]), 1),
                       "home": R1.home.get(a), "event_record": _event_record(rows, a, row["league"], row["block"], cutoff, row["start"][:4]),
                       "form": _form(rows, a, cutoff), "lineup": sa, "golgg_name": ga},
            "team_b": {"code": row["b"], "name": b, "img": row["b_img"], "elo_v2": round(rb, 1), "elo_v1": round(R1.get(b, row["league"]), 1),
                       "home": R1.home.get(b), "event_record": _event_record(rows, b, row["league"], row["block"], cutoff, row["start"][:4]),
                       "form": _form(rows, b, cutoff), "lineup": sb, "golgg_name": gb},
            "model": {"p_map_a": round(float(np.mean([x for x in out["p_game_a"] if x is not None][:1])), 4),
                      "p_map_a_elo_only": round(1 / (1 + math.exp(-elo_logit)), 4), "patch": P, "fearless": sp["fearless"],
                      "g1_a_blue": g1, "g1_side_source": g1_src or "unknown -> 50/50", "lineup_combos": combos_desc,
                      "continuity_maps_together": {row["a"]: E.S.continuity(ga, [s["pid"] for s in sa]),
                                                   row["b"]: E.S.continuity(gb, [s["pid"] for s in sb])},
                      "history_cutoff_utc": cutoff},
            "sim": simout,
            "drafts": drafts,
            "adversarial_review": flags(row, sa, sb, out, drafts, E, ga, gb, rho=sp["rho"]),
        })
    body = {"date": date, "integrity": INTEGRITY_V2, "model_version": MODEL_VERSIONS[params["variant"]],
            "params": {"ratings_variant": params["variant"], "elo_patch_lambda": params["elo_lambda"], "elo_kscale": params["elo_kscale"],
                       "elo_intl_k_leagues": params["elo_intl_k_leagues"],
                       "draft_model": params["draft"], "outcome_model": params["outcome"],
                       "q_loser_blue": params["q_loser_blue"], "q_loser_blue_n": params["q_loser_blue_n"],
                       "fitted": ["elo_patch_lambda (tuned on grid)", "elo_kscale (tuned on grid)", "draft_model weights (MLE)",
                                  "outcome_model side/draft coefficients (ridge MLE; continuity term only in v2)", "q_loser_blue (frequency)"],
                       "hand_set": {"PREV_W": dm.PREV_W, "POOL_ALPHA": dm.POOL_ALPHA, "META_K": dm.META_K, "PROF_K": dm.PROF_K,
                                    "KDA_K": dm.KDA_K, "TEAMBAN_BETA": dm.TEAMBAN_BETA, "DRAFT_CAP": dm.DRAFT_CAP, "RIDGE": dm.RIDGE,
                                    "CONT_TAU": v2.CONT_TAU, "TOPK_draft_sampling": sim_v2.TOPK, "rating_sd": 0.35, "map_sd": 0.20},
                       "fearless_check": {k: v for k, v in E.fearless.items() if k.startswith("Worlds 2026")},
                       "meta": meta_info, "corpus_games": len(games), "corpus_series_joined": len(E.join)},
            "cards": cards}
    if skipped:
        body["skipped"] = skipped
    return body


def flags(row, sa, sb, out, drafts, E, ga, gb, rho=0.0):
    f = []
    for code, st, gn in ((row["a"], sa, ga), (row["b"], sb, gb)):
        unc = [s for s in st if s["status"] == "UNCERTAIN"]
        if unc:
            f.append(f"{code}: uncertain starter(s) {', '.join(s['role'] + ' ' + s['name'] for s in unc)} -> both lineups simulated")
        if E.S.continuity(gn, [s["pid"] for s in st]) < 5:
            f.append(f"{code}: this exact five has fewer than 5 tracked maps together"
                     + (" (continuity penalty applies)" if rho else " (shown for context; v2.1 applies no continuity penalty)"))
    if row["bo"] > 1:
        f.append("Fearless: later-game drafts depend on earlier picks; G2/G3 draft trees are wide (top-draft probabilities are small)")
    f.append("Draft score is capped at +/-%.2f logit and regularised; drafts move map odds modestly by design" % dm.DRAFT_CAP)
    f.append("Lineups inferred from official roster + latest pro games (Leaguepedia unavailable/rate-limited)")
    return f
