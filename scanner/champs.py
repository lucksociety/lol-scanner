"""Champion metadata from Riot Data Dragon (static game data, market-free): tags and attack/magic profile,
used only for the small composition-fit term."""
import json, re, urllib.request
from . import config


def ck(name):
    """Canonical champion key: gol.gg uses both "K'Sante" and "KSante"; Data Dragon uses ids like "KSante"."""
    k = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    return {"nunuwillump": "nunu", "wukong": "monkeyking", "renataglasc": "renata"}.get(k, k)


def load(version="16.20.1"):
    p = config.CACHE_DIR / f"ddragon_{version}.json"
    if not p.exists():
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
        with urllib.request.urlopen(url, timeout=60) as r:
            p.write_bytes(r.read())
    d = json.loads(p.read_text())["data"]
    out = {}
    for cid, c in d.items():
        info = c["info"]
        out[ck(c["name"])] = {"name": c["name"], "tags": c["tags"], "ap": info["magic"] > info["attack"],
                              "tank": "Tank" in c["tags"]}
        out.setdefault(ck(cid), out[ck(c["name"])])
    return out
