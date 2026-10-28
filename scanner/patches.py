"""Competitive patch calendar, detected from gol.gg tier-1 match lists (each series row carries its patch)."""
import bisect, glob, json, re
from collections import Counter, defaultdict
from . import config


def pkey(p):
    m = re.match(r"(\d+)\.(\d+)", p or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


class Calendar:
    def __init__(self, rows):
        by_date = defaultdict(Counter)
        for r in rows:
            if pkey(r.get("patch")):
                by_date[r["date"]][r["patch"]] += 1
        self.dates = sorted(by_date)
        self.patch = [by_date[d].most_common(1)[0][0] for d in self.dates]
        allp = sorted({pkey(p) for p in self.patch} | {pkey(r["patch"]) for r in rows if pkey(r.get("patch"))})
        self.ordinal = {p: i for i, p in enumerate(allp)}

    def on(self, date):
        i = bisect.bisect_right(self.dates, date[:10]) - 1
        return self.patch[max(i, 0)] if self.dates else None

    def ord(self, patch):
        return self.ordinal.get(pkey(patch), 0)

    def prev(self, patch):
        o = self.ord(patch)
        inv = {v: k for k, v in self.ordinal.items()}
        p = inv.get(o - 1)
        return f"{p[0]}.{p[1]}" if p else None


def load():
    rows = []
    for f in glob.glob(str(config.CACHE_DIR / "golgg" / "matchlists" / "*.json")):
        rows += json.loads(open(f).read())
    return Calendar(rows)
