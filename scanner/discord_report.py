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


def build_payload(date):
    version = "v2" if frozen_path(date, "v2").exists() else None
    if not frozen_path(date, version).exists():
        return {"content": f"🍀 No frozen forecast for {date}: no official LoL series found for that date.",
                "embeds": [], "allowed_mentions": {"parse": []}}
    fc = load_frozen(date, version)
    ep = config.EDGE_DIR / f"{date}{'.v2' if version == 'v2' else ''}.json"
    edge = json.loads(ep.read_text()) if ep.exists() else None
    rows = {r["match_id"]: r for r in (edge or {}).get("series", [])}
    link = f"{PAGES}/{date}.html"
    head = {
        "title": f"🍀 Luck Society · LoL Scanner · {date}",
        "url": link, "color": GOLD,
        "thumbnail": {"url": LOGO},
        "description": ("🔒 Score locked before we look at a single price. We only back a bet that cashes on the lock. Let's stay green 🍀\n"
                        f"Forecast **{fc.get('model_version', 'v1')}** frozen {fc['frozen_at_et']} · market inputs to forecast: **NONE**\n"
                        + (f"POST-FORECAST Jupiter odds snapshot: {edge['odds_fetched_at_et']} (never fed back into the forecast)"
                           if edge else "Edge layer unavailable for this run: forecast only.")),
    }
    embeds = [head]
    cards = fc["cards"]
    if not cards:
        head["description"] += "\n\nNo series on the official schedule for this date."
    for c in cards[:9]:   # Discord: max 10 embeds per message
        A, B, s = c["team_a"], c["team_b"], c["sim"]
        la, lb = map(int, s["locked_score"].split("-"))
        lock = f"{A['code'] if la > lb else B['code']} {max(la, lb)}-{min(la, lb)}"
        bs = ev.best_summary(rows.get(c["match_id"])) if edge else {"pass": True, "reason": "edge layer not run"}
        if bs["pass"]:
            bet = f"**PASS** · {bs['reason']}"
            a = bs.get("best_any")
            if a:
                bet += f"\n_secondary, ignores lock: {a['market']} · {a['side']} @ {a['ask']:.2f}, EV {a['ev_per_$1']:+.3f}/$1, dies on lock_"
        else:
            bet = (f"**{bs['market']} · {bs['side']} @ {bs['ask']:.2f}**\nour {_pct(bs['our_prob'])} vs no-vig {_pct(bs['fair_prob'])} · "
                   f"edge {bs['edge_pct']:+.1f} pp · **EV {bs['ev_per_$1']:+.3f}/$1** · cashes on lock ✅")
        embeds.append({
            "title": f"{A['code']} vs {B['code']} · Bo{c['bo']}",
            "url": link,
            "color": GREEN if not bs["pass"] else GOLD_HI,
            "description": f"🕓 {c['start_et']} · {c['league']} · {c.get('stage', '')}",
            "fields": [
                {"name": "🔒 Locked exact score", "value": f"**{lock}** ({_pct(s['locked_score_prob'])})", "inline": True},
                {"name": "Series win", "value": f"{A['code']} {_pct(s['p_series_a'])} · {B['code']} {_pct(1 - s['p_series_a'])}", "inline": True},
                {"name": "💰 Best bet (Jupiter, post-forecast)", "value": bet[:1024], "inline": False},
            ],
        })
    if len(cards) > 9:
        head["description"] += f"\n\n+{len(cards) - 9} more series on the dashboard."
    embeds[-1]["footer"] = {"text": "Luck Society · zero-market forecast, Jupiter edge after the freeze · not financial advice"}
    embeds[-1]["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"content": f"🍀 **/lol {date}** · full tables: {link}", "embeds": embeds, "allowed_mentions": {"parse": []}}


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
        return 0
    return 0 if post(payload) else 1


if __name__ == "__main__":
    sys.exit(main())
