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
    ap.add_argument("--stage", default="all", choices=["all", "forecast", "edge", "dashboard", "grade", "window"])
    ap.add_argument("--no-odds", action="store_true", help="skip the Jupiter edge layer")
    ap.add_argument("--model", default=config.MODEL_VERSION, choices=["v1", "v2"], help="primary model (default v2)")
    ap.add_argument("--back", type=int, default=2, help="window stage: days before today (ET)")
    ap.add_argument("--ahead", type=int, default=7, help="window stage: days after today (ET)")
    a = ap.parse_args(argv)
    date = a.date
    events = None

    if a.stage == "window":
        # rolling Yesterday/Today/next-week pages: grade, freeze every date first, then odds, then dashboards
        from . import window
        events = schedule.fetch_all_events()
        today = window.today_et()
        for d in grade.frozen_dates():
            for v in ("v1", "v2"):
                for g in grade.grade(d, events, version=v):
                    print(f"[grade {v}] {g['series']}: locked {g['locked']} actual {g['actual']} brier {g['brier']}")
        window.run(today, a.model, odds=not a.no_odds, back=a.back, ahead=a.ahead)
        return 0

    if a.stage in ("all", "grade"):
        events = schedule.fetch_all_events()
        prev = (datetime.fromisoformat(date) - timedelta(days=1)).date().isoformat()
        for d in sorted(set(grade.frozen_dates()) | {prev, date}):
            for v in ("v1", "v2"):
                for g in grade.grade(d, events, version=v):
                    print(f"[grade {v}] {g['series']}: locked {g['locked']} actual {g['actual']} brier {g['brier']}")

    if a.stage in ("all", "forecast"):
        # v1 stays frozen as the baseline (calibration comparison); v2 is the primary model. A date that is already
        # frozen is never recomputed; series that appeared since (TBD -> teams set) are frozen as an addendum.
        from . import window
        from datetime import timezone
        events = events or schedule.fetch_all_events()
        rows = [r for r in schedule.series_rows(events) if forecast._et_date(r["start"]) == date]
        window.write_sched(window.by_date(schedule.series_rows(events), [date]))
        models = ("v1", "v2") if a.model == "v2" else ("v1",)
        for v in models:
            if forecast.frozen_path(date, v).exists():
                body = forecast.load_frozen(date, v)
                print(f"[forecast {v}] {date} already frozen at {body['frozen_at_et']} (sha256 {body['sha256'][:12]}); not recomputed")
        for line in window.freeze_date(date, events, rows, datetime.now(timezone.utc), models=models, refresh=[True]):
            print(line)

    if a.stage in ("all", "edge") and not a.no_odds:
        from . import edge  # imported only after the forecast stage has frozen
        res = edge.run(date, a.model)
        print(f"[edge] odds snapshot {res['snapshot_file']} | endpoints ok: {res['endpoints_ok']} | "
              f"coverage {res['coverage']}")

    if a.stage in ("all", "dashboard"):
        out = dashboard.render(date, a.model)   # docs/<date>.html; index.html = today ET
        print(f"[dashboard] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
