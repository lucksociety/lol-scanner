"""gol.gg game-level data (drafts, lineups, patches, champion stats). Market-free source.

Used by model v2 for: patch detection, lineups (who actually started), per-player champion pools, team draft
tendencies, and the current-patch meta table (gol.gg champion stats, per role). Raw HTML is never stored;
parsed game JSON is cached under .cache/golgg/games/<id>.json.
"""
import json, re, time, urllib.parse, urllib.request, http.cookiejar
from concurrent.futures import ThreadPoolExecutor
from . import config

BASE = "https://gol.gg"
UA = "Mozilla/5.0 (lol-scanner model-v2; research, low rate)"
CACHE = config.CACHE_DIR / "golgg"
GAMES = CACHE / "games"
ROLES = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]


def _fetch(url, data=None, opener=None, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
                                         headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            with (opener.open(req, timeout=60) if opener else urllib.request.urlopen(req, timeout=60)) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa
            last = e; time.sleep(2 * (i + 1))
    raise last


def _strip(h):
    return re.sub(r"<script.*?</script>", "", h, flags=re.S)


def norm(name):
    n = (name or "").lower().replace("&", "and")
    n = re.sub(r"[^a-z0-9]", "", n)
    for suf in ("esports", "gaming", "team", "club"):
        n = n.replace(suf, "")
    return n


def tournaments(season, refresh=False):
    p = CACHE / f"trlist_{season}.json"
    if not p.exists() or refresh:
        CACHE.mkdir(parents=True, exist_ok=True)
        p.write_text(_fetch(f"{BASE}/tournament/ajax.trlist.php", data={"season": season}))
    return json.loads(p.read_text())


def matchlist(trname, refresh=False):
    """Series rows of a tournament: first game id, teams, score, stage, patch, date."""
    p = CACHE / "matchlists" / (re.sub(r"[^A-Za-z0-9]+", "_", trname) + ".json")
    if p.exists() and not refresh:
        return json.loads(p.read_text())
    h = _strip(_fetch(f"{BASE}/tournament/tournament-matchlist/{urllib.parse.quote(trname)}/"))
    out = []
    for r in re.findall(r"<tr>(.*?)</tr>", h, re.S):
        m = re.search(r"game/stats/(\d+)/page-(summary|game)/", r)
        tds = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        if not m or len(tds) < 7:
            continue
        sc = re.match(r"(\d+)\s*-\s*(\d+)", tds[2])
        out.append({"tournament": trname, "first_game": int(m.group(1)), "multi": m.group(2) == "summary",
                    "a": tds[1], "b": tds[3], "a_w": int(sc.group(1)) if sc else None,
                    "b_w": int(sc.group(2)) if sc else None, "stage": tds[4], "patch": tds[5], "date": tds[6]})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    return out


def _parse_side(block):
    bans = re.findall(r"alt='([^']+)' src='../_img/champions_icon", block.split("Picks")[0].split("Bans", 1)[-1])
    picks = re.findall(r"alt='([^']+)' src='../_img/champions_icon", block.split("Picks", 1)[-1])
    return bans, picks


def parse_game(gid, h):
    t = _strip(h)
    patch = re.search(r"v(\d+\.\d+)\s*</div>", t)
    gt = re.search(r"Game Time.*?(\d+):(\d+)", re.sub(r"<[^>]+>", " ", t[t.find("Game Time") - 50:t.find("Game Time") + 400]), re.S)
    blue_i, red_i = t.find("blue-line-header"), t.find("red-line-header")
    pl_i = t.find("playersInfosLine")
    hb, hr = t[blue_i:red_i], t[red_i:pl_i]
    tn = lambda s: re.search(r"title='([^']+) stats'>([^<]+)</a>\s*-\s*(WIN|LOSS)", s)
    b, r = tn(hb), tn(hr)
    bb, bp = _parse_side(hb); rb, rp = _parse_side(hr)
    ms = list(re.finditer(r"alt='([^']+)' src='../_img/champions_icon/[^']+'/></a>&nbsp;<a class='link-blanc' "
                          r"href='../players/player-stats/(\d+)/[^']*' title='[^']*'>([^<]+)</a>", t))
    players = []
    for i, m in enumerate(ms[:10]):
        end = ms[i + 1].start() if i + 1 < len(ms) else m.end() + 20000
        seg = re.sub(r"<table class=.perkztab.*?</table>", "", t[m.end():end], flags=re.S)
        k = re.search(r"\b(\d+)/(\d+)/(\d+)\b", re.sub(r"<[^>]+>", " ", seg))
        players.append({"side": "blue" if i < 5 else "red", "role": ROLES[i % 5], "champ": m.group(1),
                        "pid": int(m.group(2)), "player": m.group(3).strip(),
                        "k": int(k.group(1)) if k else None, "d": int(k.group(2)) if k else None,
                        "a": int(k.group(3)) if k else None})
    nav = [(int(x), int(n)) for x, n in re.findall(r"game/stats/(\d+)/page-game/' title='[^']*'>Game (\d+)<", t)]
    if not (b and r and len(players) == 10):
        return None
    return {"id": gid, "patch": patch.group(1) if patch else None,
            "blue": b.group(2).strip(), "red": r.group(2).strip(), "blue_win": b.group(3) == "WIN",
            "blue_bans": bb, "red_bans": rb, "blue_picks": bp, "red_picks": rp, "players": players,
            "nav": nav, "duration_s": int(gt.group(1)) * 60 + int(gt.group(2)) if gt else None}


def game(gid):
    p = GAMES / f"{gid}.json"
    if p.exists():
        return json.loads(p.read_text())
    g = parse_game(gid, _fetch(f"{BASE}/game/stats/{gid}/page-game/"))
    GAMES.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(g))
    return g


def series_games(srow):
    """All parsed games of a matchlist series row, annotated with series context."""
    g1 = game(srow["first_game"])
    if not g1:
        return []
    ids = sorted({gid for gid, _ in g1.get("nav", [])} | {srow["first_game"]}) if srow["multi"] else [srow["first_game"]]
    out = []
    for n, gid in enumerate(ids, 1):
        g = g1 if gid == srow["first_game"] else game(gid)
        if g:
            g = dict(g, date=srow["date"], tournament=srow["tournament"], game_no=n, series_key=f'{srow["tournament"]}|{srow["first_game"]}',
                     series_bo_hint=len(ids))
            out.append(g)
    return out


def crawl(series_rows, workers=4):
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(series_games, series_rows))
    return [g for gs in res for g in gs]


def champion_table(patch, role="ALL", season="S16", refresh=False):
    """gol.gg champion stats for one patch (and role): picks, bans, wins, losses, presence."""
    p = CACHE / "champ" / f"{season}_{patch}_{role}.json"
    if p.exists() and not refresh:
        return json.loads(p.read_text())
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    url = f"{BASE}/champion/list/season-{season}/split-ALL/tournament-ALL/"
    h = _strip(_fetch(url, data={"cbtournament": "ALL", "patch": patch + ".", "role": role, "leaguePost": "",
                                 "side": "ALL", "draft": "ALL"}, opener=op))
    if f"value='{patch}.' selected" not in h:
        raise RuntimeError(f"gol.gg did not apply patch filter {patch}")
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        num = lambda x: int(x) if x.isdigit() else 0
        if len(cells) >= 7 and cells[1].isdigit():
            out.append({"champ": cells[0], "picks": num(cells[1]), "bans": num(cells[2]),
                        "wins": num(cells[4]), "losses": num(cells[5])})
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    return out
