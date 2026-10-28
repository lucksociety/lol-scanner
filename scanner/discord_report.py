"""Post the Luck Society /lol report to Discord (edits the deferred interaction reply).

Reads the FROZEN forecast and the POST-FORECAST edge log only. Secrets come from the environment
(DISCORD_BOT_TOKEN, DISCORD_INTERACTION_TOKEN) and are never printed.

  python -m scanner.discord_report --date D [--error MSG] [--dry-run]
env: DISCORD_APPLICATION_ID, DISCORD_INTERACTION_TOKEN, DISCORD_CHANNEL_ID, DISCORD_REQUESTED_AT (unix s),
     DISCORD_BOT_TOKEN (fallback when the 15-min interaction token is expired or rejected)
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from . import config, edgeview as ev
from .forecast import load_frozen, frozen_path

GOLD, GOLD_HI, GREEN, RED = 0xD79D07, 0xF6D12B, 0x036718, 0xB3261E
PAGES = "https://lucksociety.github.io/lol-scanner"
LOGO = f"{PAGES}/assets/luck-society-avatar.jpg"
API = "https://discord.com/api/v10"
TOKEN_SAFE_S = 13 * 60   # interaction tokens live 15 min; leave margin


def _pct(x):
    return f"{100 * x:.1f}%"


# Discord embed limits (https://discord.com/developers/docs/resources/message#embed-object-embed-limits)
LIM_DESC, LIM_FIELDS, LIM_FIELD_NAME, LIM_FIELD_VAL, LIM_TOTAL = 4096, 25, 256, 1024, 6000
TOTAL_BUDGET = 5800   # headroom under 6000
FOOTER = "Luck Society · zero-market forecast, Jupiter edge after the freeze · not financial advice"


def _cut(s, n):
    return s if len(s) <= n else s[:n - 1] + "…"


def _embed_chars(em):
    return (len(em.get("title", "")) + len(em.get("description", "")) + len(em.get("footer", {}).get("text", ""))
            + sum(len(f["name"]) + len(f["value"]) for f in em.get("fields", [])))


def _series_field(c, row, have_edge, short=False):
    """One embed field per series: teams · format · time / lock + prob · winner % / best bet + EV, or NO BET (nothing > 50% model prob and +EV), or PASS (no market)."""
    A, B, s = c["team_a"], c["team_b"], c["sim"]
    la, lb = map(int, s["locked_score"].split("-"))
    lock = f"{A['code'] if la > lb else B['code']} {max(la, lb)}-{min(la, lb)}"
    pa = s["p_series_a"]
    fav, pf = (A["code"], pa) if pa >= 0.5 else (B["code"], 1 - pa)
    when = str(c.get("start_et", "")).split(", ")[-1]      # "Wed Oct 28, 04:00 PM ET" -> "04:00 PM ET"
    bs = ev.best_summary(row) if have_edge else {"pass": True, "reason": "edge layer not run"}
    if bs["pass"]:
        bet = f"💰 **{bs.get('verdict', 'PASS')}** · {bs['reason']}"
    else:
        bet = f"💰 **{bs['market']} · {bs['side']} @ {bs['ask']:.2f}** · **EV {bs['ev_per_$1']:+.3f}/$1**"
        if not short:
            bet += f"\nour {_pct(bs['our_prob'])} vs no-vig {_pct(bs['fair_prob'])} · edge {bs['edge_pct']:+.1f} pp"
    name = f"{A['code']} vs {B['code']} · Bo{c['bo']} · 🕓 {when}"
    if row and row.get("status") == "ok" and not short:
        nm = len(row.get("not_modeled") or [])
        bet += f"\n📊 {row.get('n_priced_legs', len(row.get('legs', [])))} Jupiter lines priced" + (f" · {nm} props not modeled" if nm else "")
    val = f"🔒 **{lock}** ({_pct(s['locked_score_prob'])}) · {fav} {_pct(pf)} to win\n{bet}"
    return {"name": _cut(name, LIM_FIELD_NAME), "value": _cut(val, LIM_FIELD_VAL), "inline": False}, bs


def render(date, fc, edge):
    """Build the /lol message. ONE embed: header + one field per series (NO BET / PASS and not-modeled included).

    Why one embed: Discord merges consecutive embeds that share the same `url` into a single gallery card and shows
    only the first one's text, so per-series embeds pointing at the dashboard were silently dropped."""
    rows = {r["match_id"]: r for r in (edge or {}).get("series", [])}
    link = f"{PAGES}/{date}.html"
    cards = fc.get("cards", [])
    skipped = fc.get("skipped") or []
    desc = ("🔒 Score locked before we look at a single price. Then we hunt the biggest edge on the board. Let's stay green 🍀\n"
            f"Forecast **{fc.get('model_version', 'v1')}** frozen {fc['frozen_at_et']} · market inputs to forecast: **NONE**\n"
            + (f"POST-FORECAST Jupiter odds snapshot: {edge['odds_fetched_at_et']} (never fed back into the forecast)"
               if edge else "Edge layer unavailable for this run: forecast only."))
    if not cards and not skipped:
        desc += "\n\nNo series on the official schedule for this date."
    em = {"title": f"🍀 Luck Society · LoL Scanner · {date}", "url": link, "color": GOLD,
          "thumbnail": {"url": LOGO}, "description": _cut(desc, LIM_DESC), "fields": [],
          "footer": {"text": FOOTER}, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    def build(short):
        fields, bets = [], 0
        for c in cards:
            f, bs = _series_field(c, rows.get(c["match_id"]), edge is not None, short)
            fields.append(f); bets += not bs["pass"]
        for sk in skipped:
            fields.append({"name": _cut(f"{sk['series']} · not modeled", LIM_FIELD_NAME),
                           "value": _cut(f"⚪ {sk.get('reason', 'no model')}", 200 if short else LIM_FIELD_VAL), "inline": False})
        return fields, bets

    fields, bets = build(short=False)
    em["fields"] = fields
    if len(fields) > LIM_FIELDS or _embed_chars(em) > TOTAL_BUDGET:
        fields, bets = build(short=True)          # drop the secondary lines first
    # still too much: keep as many series as fit, then one overflow field pointing to the dashboard
    over = 0
    while True:
        keep = fields[:len(fields) - over]
        extra = ([{"name": f"+{over} more series", "value": f"Full tables: {link}", "inline": False}] if over else [])
        em["fields"] = keep + extra
        if len(em["fields"]) <= LIM_FIELDS and _embed_chars(em) <= TOTAL_BUDGET:
            break
        over += 1
    em["color"] = GREEN if bets else GOLD
    n = len(cards) + len(skipped)
    summary = f"{n} series · {bets} bet{'s' if bets != 1 else ''} · {n - bets} NO BET/not modeled" if n else "no series"
    return {"content": f"🍀 **/lol {date}** · {summary} · full tables: {link}", "embeds": [em],
            "allowed_mentions": {"parse": []}}


def build_payload(date):
    version = "v2" if frozen_path(date, "v2").exists() else None
    if not frozen_path(date, version).exists():
        return {"content": f"🍀 No frozen forecast for {date}: no official LoL series found for that date.",
                "embeds": [], "allowed_mentions": {"parse": []}}
    fc = load_frozen(date, version)
    ep = config.EDGE_DIR / f"{date}{'.v2' if version == 'v2' else ''}.json"
    edge = json.loads(ep.read_text()) if ep.exists() else None
    return render(date, fc, edge)


def to_text(payload):
    """Plain-text rendering of the payload (for --dry-run / logs)."""
    out = [payload.get("content", "")]
    for em in payload.get("embeds", []):
        out += ["", em.get("title", ""), em.get("description", "")]
        for f in em.get("fields", []):
            out += ["", f"▸ {f['name']}", f["value"]]
        if em.get("footer"):
            out += ["", em["footer"]["text"]]
    return "\n".join(out)


def error_payload(date, msg):
    return {"content": "", "allowed_mentions": {"parse": []},
            "embeds": [{"title": f"🍀 Luck Society · LoL Scanner · {date}", "color": RED, "thumbnail": {"url": LOGO},
                        "description": f"⚠️ The scan did not finish: {msg[:500]}\nNo bet is posted."}]}


def progress_payload(date, msg):
    return {"content": f"🍀 Scanning {date}… {msg[:400]}", "embeds": [], "allowed_mentions": {"parse": []}}


def edit_progress(payload):
    """Interim status: only edits the deferred reply (never posts a new channel message)."""
    app = os.environ.get("DISCORD_APPLICATION_ID", "")
    itok = os.environ.get("DISCORD_INTERACTION_TOKEN", "")
    req_at = int(os.environ.get("DISCORD_REQUESTED_AT") or 0)
    if not (itok and app) or (req_at and time.time() - req_at >= TOKEN_SAFE_S):
        print("[discord] progress: no live interaction token; skipped")
        return True
    st, body = _send("PATCH", f"{API}/webhooks/{app}/{itok}/messages/@original", payload)
    print(f"[discord] progress edit @original -> HTTP {st}")
    return 200 <= st < 300


def _send(method, url, payload, auth=None):
    h = {"Content-Type": "application/json", "User-Agent": "DiscordBot (https://github.com/lucksociety/lol-scanner, 1.0)"}
    if auth:
        h["Authorization"] = auth
    r = urllib.request.Request(url, data=json.dumps(payload).encode(), method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]


def post(payload):
    app = os.environ.get("DISCORD_APPLICATION_ID", "")
    itok = os.environ.get("DISCORD_INTERACTION_TOKEN", "")
    chan = os.environ.get("DISCORD_CHANNEL_ID", "")
    req_at = int(os.environ.get("DISCORD_REQUESTED_AT") or 0)
    age = time.time() - req_at if req_at else 0
    if itok and app and age < TOKEN_SAFE_S:
        st, body = _send("PATCH", f"{API}/webhooks/{app}/{itok}/messages/@original", payload)
        print(f"[discord] webhook edit @original -> HTTP {st} (token age {age:.0f}s)")
        if 200 <= st < 300:
            return True
        print(f"[discord] webhook edit failed: {body}")
    bot = os.environ.get("DISCORD_BOT_TOKEN", "")
    if chan and bot:
        p = dict(payload)
        if age >= TOKEN_SAFE_S:
            p["content"] = (p.get("content") or "") + "\n_(posted to the channel because the scan outlived Discord's 15-minute reply window)_"
        st, body = _send("POST", f"{API}/channels/{chan}/messages", p, auth=f"Bot {bot}")
        print(f"[discord] bot-token channel post -> HTTP {st}")
        if 200 <= st < 300:
            return True
        print(f"[discord] channel post failed: {body}")
    return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--error", default=None)
    ap.add_argument("--progress", default=None, help="interim status line (edits the deferred reply only)")
    ap.add_argument("--dry-run", action="store_true", help="build and print the payload; post nothing")
    a = ap.parse_args(argv)
    if a.progress:
        payload = progress_payload(a.date, a.progress)
        if a.dry_run:
            print(json.dumps(payload, indent=1, ensure_ascii=False)); return 0
        return 0 if edit_progress(payload) else 1
    payload = error_payload(a.date, a.error) if a.error else build_payload(a.date)
    if a.dry_run:
        print(json.dumps(payload, indent=1, ensure_ascii=False))
        print("\n--- rendered ---\n" + to_text(payload))
        return 0
    return 0 if post(payload) else 1


if __name__ == "__main__":
    sys.exit(main())
