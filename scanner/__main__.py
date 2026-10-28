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
    ap.add_argument("--model", default=config.MODEL_VERSION, choices=["v1", "v2"], help="primary model (default v2)")
    a = ap.parse_args(argv)
    date = a.date
    events = None

    if a.stage in ("all", "grade"):
        events = schedule.fetch_all_events()
        prev = (datetime.fromisoformat(date) - timedelta(days=1)).date().isoformat()
        for d in (prev, date):
            for v in ("v1", "v2"):
                for g in grade.grade(d, events, version=v):
                    print(f"[grade {v}] {g['series']}: locked {g['locked']} actual {g['actual']} brier {g['brier']}")

    if a.stage in ("all", "forecast"):
        # v1 stays frozen as the baseline (calibration comparison); v2 is the primary model.
        models = ["v1", "v2"] if a.model == "v2" else ["v1"]
        for v in models:
            if forecast.frozen_path(date, v).exists():
                body = forecast.load_frozen(date, v)
                print(f"[forecast {v}] {date} already frozen at {body['frozen_at_et']} (sha256 {body['sha256'][:12]}); not recomputed")
                continue
            events = events or schedule.fetch_all_events()
            if v == "v1":
                body = forecast.freeze(forecast.build_forecast(date, events=events))
            else:
                from . import forecast_v2
                body = forecast.freeze(forecast_v2.build_forecast(date, events=events))
            print(f"[forecast {v}] FROZEN {date} at {body['frozen_at_et']} | {len(body['cards'])} series | sha256 {body['sha256'][:12]}")
            for c in body["cards"]:
                print(f"   {c['team_a']['code']} vs {c['team_b']['code']} Bo{c['bo']}: {c['team_a']['code']} "
                      f"{c['sim']['p_series_a']:.1%} | lock {c['sim']['locked_score']}")

    if a.stage in ("all", "edge") and not a.no_odds:
        from . import edge  # imported only after the forecast stage has frozen
        res = edge.run(date, a.model)
        print(f"[edge] odds snapshot {res['snapshot_file']} | endpoints ok: {res['endpoints_ok']} | "
              f"coverage {res['coverage']}")

    if a.stage in ("all", "dashboard"):
        out = dashboard.render(date, a.model)
        print(f"[dashboard] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
