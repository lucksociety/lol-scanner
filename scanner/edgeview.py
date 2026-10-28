"""Presentation helpers for the POST-FORECAST Jupiter edge layer (dashboard, Page, Discord).

Firewall: everything here READS a frozen forecast + an edge log written after the freeze. Nothing here
writes to the forecast or feeds a market price back into any probability.
"""
import math
from itertools import product

from . import edge as _edge


# ---------------------------------------------------------------- pure pricing math (tested)
def devig_pair(ask_a, ask_b):
    """Proportional de-vig of a binary pair of asks -> (fair_a, fair_b, overround)."""
    s = ask_a + ask_b
    return ask_a / s, ask_b / s, s - 1


def cost_per_contract(ask):
    """Price paid per $1-payout contract including the Jupiter fee 0.07*p*(1-p)."""
    return ask + _edge.fee(ask)


def ev_per_dollar(q, ask):
    """Expected profit per $1 staked at `ask` (incl. fee) when the true probability is q."""
    return q / cost_per_contract(ask) - 1


# ---------------------------------------------------------------- display parsing
def market_side(leg):
    lab, t = leg["label"], leg["type"]
    if t == "moneyline":
        return "Series moneyline", lab.split(" ", 1)[1]
    if t == "map_handicap":
        code, line, _ = lab.split(" ")
        return f"Map handicap ±{abs(float(line)):.1f}", f"{code} {line}"
    if t in ("totals_over", "totals_under"):
        side, line, _ = lab.split(" ")
        return f"Total maps {line}", side
    if t == "game":
        _, g, code = lab.split(" ")
        return f"Game {g} winner", code
    if t == "exact":
        return "Exact score", lab.replace("Exact ", "", 1)
    return lab, ""


# ---------------------------------------------------------------- ratings-only comparison
_GRID = [-3 + 0.05 * i for i in range(121)]
_W = [math.exp(-x * x / 2) for x in _GRID]


def ratings_only_exact(p_map_a, bo):
    """Exact-score distribution if every map were an independent coin with the v2 ratings-only map probability,
    integrated over the same rating/map uncertainty the edge layer uses (sd hypot(.35,.25))."""
    need = bo // 2 + 1
    base = math.log(p_map_a / (1 - p_map_a)); sd = math.hypot(0.35, 0.25)
    out = {}
    for w, x in zip(_W, _GRID):
        p = 1 / (1 + math.exp(-(base + sd * x)))
        for lw in range(need):   # A wins `need`, B wins lw
            out[f"{need}-{lw}"] = out.get(f"{need}-{lw}", 0) + w * math.comb(need - 1 + lw, lw) * p ** need * (1 - p) ** lw
            out[f"{lw}-{need}"] = out.get(f"{lw}-{need}", 0) + w * math.comb(need - 1 + lw, lw) * (1 - p) ** need * p ** lw
    s = sum(_W)
    return {k: v / s for k, v in out.items()}


def ratings_only_prob(leg, card):
    """P(leg cashes) under v2 ratings-only (patch+lineup Elo, no draft layer)."""
    p = card["model"].get("p_map_a_elo_only")
    if p is None:
        return None
    bo = card["bo"]
    A, B = card["team_a"]["code"], card["team_b"]["code"]
    t = leg["type"]
    if t == "game":
        side_a = leg["label"].endswith(" " + A)
        pa = _edge.game_prob(p)
        return pa if side_a else 1 - pa       # games 1..need are always played
    side_a = None
    line = None
    if t == "moneyline":
        side_a = leg["label"].split(" ", 1)[1] == A
    elif t == "map_handicap":
        code, l, _ = leg["label"].split(" "); side_a = code == A; line = float(l)
    elif t in ("totals_over", "totals_under"):
        line = float(leg["label"].split(" ")[1])
    elif t == "exact":
        return None
    f = _edge.outcome_fn(t, side_a, line, None, bo)
    return _edge.prob_from_exact(ratings_only_exact(p, bo), f)


def enrich(row, card):
    """Return legs sorted by EV (desc) with market/side/implied/ratings-only fields added."""
    legs = []
    for l in row.get("legs", []):
        m, s = market_side(l)
        legs.append({**l, "market": m, "side": s, "implied": l["ask"],
                     "ratings_only_prob": ratings_only_prob(l, card),
                     "is_best": bool(row.get("best_bet")) and l["market_id"] == row["best_bet"]["market_id"],
                     "is_best_any": bool(row.get("best_ev_any")) and l["market_id"] == row["best_ev_any"]["market_id"]})
    legs.sort(key=lambda x: -x["ev_per_$1"])
    return legs


def best_summary(row):
    """Dict for the slate table / Discord: best lock-compatible +EV leg or PASS."""
    if not row or row.get("status") != "ok":
        return {"pass": True, "reason": "no Jupiter market"}
    b = row.get("best_bet")
    if not b:
        a = row.get("best_ev_any")
        return {"pass": True, "reason": "no +EV market cashes on the lock",
                "best_any": ({**a, "market": market_side(a)[0], "side": market_side(a)[1]} if a else None)}
    m, s = market_side(b)
    return {"pass": False, **b, "market": m, "side": s}
