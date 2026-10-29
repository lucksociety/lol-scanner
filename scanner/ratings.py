"""Map-level Elo built ONLY from completed series results (lolesports schedule). No market inputs.

Each completed series contributes its map wins/losses (order unknown -> interleaved). League tier priors
seed new teams; international events (Worlds/MSI/First Stand/EWC) carry a higher K so cross-region
strength is learned from head-to-head play rather than assumed.
"""
import math
from collections import defaultdict

# Seed ratings by league (only used the first time a team appears).
LEAGUE_PRIOR = {
    "LCK": 1650, "LPL": 1640, "LEC": 1560, "LCS": 1510, "LTA North": 1510, "LCP": 1500, "PCS": 1480,
    "VCS": 1470, "CBLOL": 1450, "LTA South": 1440, "LJL": 1430, "LLA": 1420,
    "LCK Challengers": 1480, "Worlds": 1500, "MSI": 1500, "First Stand": 1550, "Esports World Cup": 1520,
}
DEFAULT_PRIOR = 1380            # ERLs / academy leagues
INTL = {"Worlds", "MSI", "First Stand", "Esports World Cup", "KeSPA Cup", "Asian Games"}
K_DOMESTIC, K_INTL = 18.0, 30.0
SCALE = 400.0
OFFSEASON_SHRINK = 0.25         # pull toward league prior after a >45-day gap (roster churn)


def p_map(ra, rb):
    return 1.0 / (1.0 + 10 ** (-(ra - rb) / SCALE))


class Ratings:
    def __init__(self):
        self.r, self.home, self.last, self.n = {}, {}, {}, defaultdict(int)
        self._gap_shrunk = {}

    def get(self, team, league=None, when=None):
        if team not in self.r:
            self.r[team] = float(LEAGUE_PRIOR.get(league, DEFAULT_PRIOR))
            if league and league not in INTL:
                self.home[team] = league
        elif when and team in self.last:
            gap = (when - self.last[team]).days
            if gap > 45 and self._gap_shrunk.get(team) != self.last[team]:
                prior = LEAGUE_PRIOR.get(self.home.get(team), DEFAULT_PRIOR)
                self.r[team] += OFFSEASON_SHRINK * (prior - self.r[team])
                self._gap_shrunk[team] = self.last[team]
        if league and league not in INTL and team not in self.home:
            self.home[team] = league
        return self.r[team]

    def update_series(self, a, b, aw, bw, league, when):
        ra, rb = self.get(a, league, when), self.get(b, league, when)
        k = K_INTL if league in INTL else K_DOMESTIC
        seq = []
        for i in range(max(aw, bw)):          # interleave map results
            if i < aw: seq.append(1)
            if i < bw: seq.append(0)
        for s in seq:
            e = p_map(self.r[a], self.r[b])
            d = k * (s - e)
            self.r[a] += d; self.r[b] -= d
        self.last[a] = self.last[b] = when
        self.n[a] += aw + bw; self.n[b] += aw + bw


def team_key(row, side):
    return row[f"{side}_name"]


def build(rows, before_iso):
    """Fit ratings on all completed series that STARTED before `before_iso`. Returns (Ratings, log_loss, n)."""
    from datetime import datetime
    R = Ratings(); ll, n = 0.0, 0
    for row in rows:
        if row["state"] != "completed" or row["start"] >= before_iso:
            continue
        aw, bw = row["a_w"], row["b_w"]
        if aw is None or bw is None or aw + bw == 0:
            continue
        when = datetime.fromisoformat(row["start"].replace("Z", "+00:00"))
        a, b = team_key(row, "a"), team_key(row, "b")
        p = p_map(R.get(a, row["league"], when), R.get(b, row["league"], when))
        p = min(max(p, 1e-4), 1 - 1e-4)
        ll += -(aw * math.log(p) + bw * math.log(1 - p)); n += aw + bw
        R.update_series(a, b, aw, bw, row["league"], when)
    return R, (ll / n if n else float("nan")), n
