# lol-scanner

Daily League of Legends esports scanner with a hard wall between **forecast** and **market**:

1. **Forecast (market-free, frozen).** Results-only map Elo (lolesports results since 2025) → 100k Monte Carlo
   per series → exact Bo1/Bo3/Bo5 score distribution, locked exact score, side/handicap/total probabilities,
   adversarial review. Written once to `forecast_log/<date>.json` with timestamp + SHA-256.
2. **Post-forecast edge layer** (`scanner/edge.py`). Only after the freeze: pulls Jupiter prediction-market
   prices for LoL, de-vigs each binary pair, and shows price / no-vig prob / our prob / edge / EV per $1.
   **BEST BET** = highest +EV market that also cashes on the locked score; otherwise **PASS**.
   Raw odds snapshots go to `odds_snapshots/<date>/<UTC stamp>.json` (+ `odds_log.jsonl`) for CLV grading.
3. **Dashboard**: static `docs/index.html` (+ `docs/<date>.html` archive), GitHub Pages ready.

Method rules: see [`protocol_v4.md`](protocol_v4.md).

## Setup
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in LOLESPORTS_API_KEY, then: set -a; . ./.env; set +a
```

## Run
```bash
python -m scanner --date 2026-10-28                 # grade -> forecast(freeze) -> edge -> dashboard
python -m scanner --date 2026-10-28 --stage forecast # freeze only
python -m scanner --date 2026-10-28 --stage edge     # odds layer (refuses if no frozen forecast)
python -m scanner --date 2026-10-28 --stage dashboard
python -m scanner --date 2026-10-28 --no-odds        # forecast + dashboard only
python -m pytest -q tests
```
`--date` is the slate date in America/New_York. A frozen date is never recomputed.

## Configuration (env vars, nothing secret committed)
| Var | Purpose |
|---|---|
| `LOLESPORTS_API_KEY` | **Required.** `x-api-key` for `esports-api.lolesports.com/persisted/gw` (the key the lolesports.com web client sends; grab it from your browser's network tab). Store as an Actions secret. |
| `JUP_BASE` | Jupiter prediction API base (default `https://prediction-market-api.jup.ag/api/v1`) |
| `JUP_API_KEY` | Optional, only for `https://api.jup.ag/prediction/v1` |
| `JUP_FEE_COEF` | Taker-fee model `coef·p·(1−p)` (default 0.07) |
| `LOL_SIMS`, `LOL_SEED`, `LOL_HISTORY_START`, `LOL_TZ` | Sim count, RNG seed, history window, slate timezone |

## Jupiter endpoints used
- `GET /events?category=esports&subcategory=lol&includeMarkets=true&includeAllMarkets=true&start=0&end=100`
  (max page 100; fallback: page `category=esports` and filter `subcategory == "lol"`)
- `GET /orderbook/{marketId}` – depth at the ask (buy-YES ask = 100 − best NO bid)
- Also available: `GET /events/{eventId}`, `GET /markets/{marketId}`, `GET /events/search?query=`

Market mapping: `moneyline` (series ML, all formats); `map_handicap` (±1.5 Bo3, ±1.5/±2.5 Bo5; ignored for Bo1);
`totals` (2.5 Bo3, 3.5/4.5 Bo5; ignored for Bo1); `child_moneyline` (Game N winner, always-played games only);
exact-score markets if listed. No market → `no Jupiter market`. Polymarket-sourced rules whose scheduled date
disagrees with the official schedule are flagged on the card.

## GitHub Actions
`.github/workflows/daily-scan.yml` runs at 12:00 UTC (08:00 EDT / 07:00 EST) and on `workflow_dispatch`
(optional `date` input). Order: tests → grade → **forecast + commit** → edge → dashboard → commit + push.
Pages: Settings → Pages → Deploy from branch → `main` / `/docs`.

## Layout
```
scanner/      config, schedule, ratings, sim, forecast, edge, grade, backtest, dashboard, __main__
forecast_log/ frozen forecasts + forecast_log.jsonl      (never written by edge.py)
odds_snapshots/ raw Jupiter snapshots + odds_log.jsonl   (CLV grading)
edge_log/     per-date edge tables
calibration/  calibration_log.csv (Brier / log loss / lock hit)
docs/         static dashboard (GitHub Pages)
```

Model limits (disclosed): results-only Elo, no draft/patch/roster-news adjustment; cross-region strength is
inferred from international results. Walk-forward backtest on Worlds/MSI/First Stand since 2025-06:
see `python -c "from scanner import backtest; print(backtest.run())"`. Not financial advice.
