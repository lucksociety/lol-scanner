# Zero-Market Influence Prediction Protocol v4.0 (as implemented in this repo)

1. **Zero market influence.** Forecast inputs are official results/schedule only (lolesports gateway).
   No sportsbook odds, prediction-market prices, betting ads or tipster picks are read before the freeze.
   Enforced in code: `scanner/forecast.py`, `ratings.py`, `sim.py`, `schedule.py` never import market code
   (unit-tested), and every card carries `market_inputs_to_forecast: NONE`.
2. **Exact series distributions.** Every series gets the full Bo1/Bo3/Bo5 exact-score distribution from a
   100,000-run Monte Carlo with rating uncertainty + per-map variance; the modal score is the **locked score**.
3. **Freeze before odds.** The slate is written once to `forecast_log/<date>.json` with a UTC/ET timestamp and
   SHA-256, and appended to `forecast_log/forecast_log.jsonl`. Files are immutable (re-freeze raises).
4. **Post-forecast edge layer only.** `scanner/edge.py` loads the frozen file (hash-verified), asserts the odds
   fetch time is after the freeze, and writes only to `odds_snapshots/` and `edge_log/`.
5. **Best bet = biggest edge among likely outcomes** (rule `r3-2026-10-28-prob50-maxEV`). The locked score is the
   headline prediction. BEST BET = the highest-EV Jupiter line (every market and alt line, both sides) among lines
   with **model probability > 50% AND EV > 0** after fees, whether or not it cashes on the lock ("cashes on lock"
   is shown per line as info only). Sanity guard: lines priced under 0.03 or over 0.97, or with no volume and no
   orderbook, can't be the best bet. If no line meets both conditions: **NO BET**. Every edge log records the rule
   in `best_bet_rule` and `best_bet_rule_version` (r2 = pure max-EV, r1 = had to cash on the lock).
6. **Honest method disclosure + adversarial review.** Each card lists the model's limits and the reasons it
   could be wrong (thin samples, cross-region inference, coin-flip maps, no draft/patch/roster adjustment).
7. **Calibration log.** After results post, `calibration/calibration_log.csv` records Brier, log loss,
   exact-lock hit and P(actual score) per series; odds snapshots allow closing-line-value grading.
