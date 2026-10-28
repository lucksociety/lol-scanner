"""CLI: python -m scanner --date YYYY-MM-DD [--stage all|forecast|edge|dashboard|grade]

Order is enforced: `forecast` freezes the slate first; `edge` refuses to run unless a verified frozen forecast
exists; odds are fetched only inside scanner.edge.
"""
import argparse, json, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from . import config, forecast, schedule, grade, dashboard


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m scanner")
    ap.add_argument("--date", default=datetime.now(ZoneInfo(config.TZ)).date().isoformat(), help="slate date in ET")
    ap.add_argument("--stage", default="all", choices=["all", "forecast", "edge", "dashboard", "grade"])
    ap.add_argument("--no-odds", action="store_true", help="skip the Jupiter edge layer")
    a = ap.parse_args(argv)
    date = a.date
    events = None

    if a.stage in ("all", "grade"):
        events = schedule.fetch_all_events()
        prev = (datetime.fromisoformat(date) - timedelta(days=1)).date().isoformat()
        for d in (prev, date):
            for g in grade.grade(d, events):
                print(f"[grade] {g['series']}: locked {g['locked']} actual {g['actual']} brier {g['brier']}")

    if a.stage in ("all", "forecast"):
        if forecast.frozen_path(date).exists():
            body = forecast.load_frozen(date)
            print(f"[forecast] {date} already frozen at {body['frozen_at_et']} (sha256 {body['sha256'][:12]}); not recomputed")
        else:
            events = events or schedule.fetch_all_events()
            body = forecast.freeze(forecast.build_forecast(date, events=events))
            print(f"[forecast] FROZEN {date} at {body['frozen_at_et']} | {len(body['cards'])} series | sha256 {body['sha256'][:12]}")
            for c in body["cards"]:
                print(f"   {c['team_a']['code']} vs {c['team_b']['code']} Bo{c['bo']}: {c['team_a']['code']} "
                      f"{c['sim']['p_series_a']:.1%} | lock {c['sim']['locked_score']}")

    if a.stage in ("all", "edge") and not a.no_odds:
        from . import edge  # imported only after the forecast stage has frozen
        res = edge.run(date)
        print(f"[edge] odds snapshot {res['snapshot_file']} | endpoints ok: {res['endpoints_ok']} | "
              f"coverage {res['coverage']}")

    if a.stage in ("all", "dashboard"):
        out = dashboard.render(date)
        print(f"[dashboard] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
