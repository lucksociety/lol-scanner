"""Builds the game-level corpus (gol.gg) the v2 model needs, keeping the crawl small and polite:
games of teams that play an international event, from tier-1 tournaments in the WINDOW_DAYS before it, plus
the event itself. Used both live (the current event) and by the walk-forward backtest (past events)."""
import json, re
from datetime import date, timedelta
from . import golgg, config

TIER1 = r"^(LCK|LPL|LEC|LCS|LTA|LCP|CBLOL|MSI|Worlds|First Stand|Esports World Cup|2025 Mid-Season Invitational|2026 First Stand)"
EXCL = r"(CL |Academy|Challengers|LPLOL)"
WINDOW_DAYS = 120          # look-back cap before an event
LAST_SERIES = 12           # per team: its last N tier-1 series before the event (pools, continuity, tendencies)
# International events (gol.gg names) in the backtest scope + the live one.
EVENTS = {
    "MSI 2025": ["2025 Mid-Season Invitational"],
    "Worlds 2025": ["Worlds 2025 Play-In", "Worlds 2025 Main Event"],
    "First Stand 2026": ["2026 First Stand"],
    "MSI 2026": ["MSI 2026"],
    "Worlds 2026": ["Worlds 2026 Play-In", "Worlds 2026 Main Event"],
}


def tier1_tournaments(refresh=False):
    out = []
    for s in ("S15", "S16"):
        for t in golgg.tournaments(s, refresh=refresh and s == "S16"):
            if re.search(TIER1, t["trname"]) and not re.search(EXCL, t["trname"]):
                out.append(t)
    return out


def select(refresh_live=False):
    trs = tier1_tournaments(refresh=refresh_live)
    rows = []
    for t in trs:
        live = t["lastgame"] >= (date.today() - timedelta(days=40)).isoformat()
        rows += golgg.matchlist(t["trname"], refresh=refresh_live and live)
    chosen = {}
    for ev, names in EVENTS.items():
        ev_rows = [r for r in rows if r["tournament"] in names]
        if not ev_rows:
            continue
        teams = {golgg.norm(r["a"]) for r in ev_rows} | {golgg.norm(r["b"]) for r in ev_rows}
        start = min(r["date"] for r in ev_rows)
        lo = (date.fromisoformat(start) - timedelta(days=WINDOW_DAYS)).isoformat()
        for r in ev_rows:
            chosen[(r["tournament"], r["first_game"])] = r
        for tm in teams:
            prior = [r for r in rows if lo <= r["date"] < start and r["tournament"] not in names
                     and tm in (golgg.norm(r["a"]), golgg.norm(r["b"]))]
            for r in sorted(prior, key=lambda r: r["date"])[-LAST_SERIES:]:
                chosen[(r["tournament"], r["first_game"])] = r
    return sorted(chosen.values(), key=lambda r: (r["date"], r["first_game"]))


def build(refresh_live=False, workers=4):
    sel = select(refresh_live)
    games = golgg.crawl(sel, workers=workers)
    p = config.CACHE_DIR / "golgg" / "corpus.json"
    p.write_text(json.dumps(games))
    return games


if __name__ == "__main__":
    import sys
    s = select(refresh_live="--refresh" in sys.argv)
    print(len(s), "series selected")
    g = build(refresh_live=False)
    print(len(g), "games")
