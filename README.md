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

## Model v2 (`--model v2`, default since 2026-10-28): patch, lineups, probable drafts

v1 stays frozen next to it as the baseline (`forecast_log/<date>.json`); v2 is written to `forecast_log/<date>.v2.json`.
Both are frozen (SHA-256) before any market data is read; the edge layer reads only the frozen file.

**Patch.** Each game is tagged with its patch. Before every series, older-patch evidence is decayed by
λ^(Δpatch) toward the league prior, and K is scaled. (λ, K-scale) are **tuned** walk-forward by one-step-ahead map log loss on
*international* maps before the cutoff. If there is no international history in the window, the default is (1.0, 1.0).
Revision disclosed: the first attempt tuned on all maps. It picked K-scale 2–3 and made forecasts worse
(`calibration/backtest_v1_vs_v2_attempt1_allmaps_tuning.json`), so the objective was narrowed after seeing that backtest.
For 2026-10-28 the tuned value is λ=0.8, K-scale=0.8. The patch meta is gol.gg champion stats for the current patch, blended 0.6 with the previous patch and shrunk toward 50%.

**Lineups.** The expected five come from the lolesports getTeams roster and the event's latest starters.
- CONFIRMED: on the official roster and started every event game.
- PROBABLE: anything else.
- UNCERTAIN: a different player started one of the last 3 event games. Both lineups are then simulated.

Roster continuity (maps the five have played together) enters the outcome model as exp(−n/8).

**Drafts.** The model is a conditional logit over the real ban/pick order. Its features are player pool (current and prior patch), meta, team bans, comfort and roster needs; weights are fitted by MLE.
- Fearless (no repeat picks across the games of a Bo3/Bo5) is enforced. It was verified from data: Worlds 2026 had 0 repeat picks in 10 multi-game series.
- Each sim samples drafts among the top-4 legal options at every step. This is how "highest-probability drafts" is implemented.
- The draft score becomes a per-map win adjustment through a ridge logistic with an Elo offset (fitted), capped at ±0.45 logit.
- Top drafts per map come from beam search. A full ban+pick sequence has a tiny probability, so per-role pick marginals are also shown.

**Fitted / tuned:** Elo λ and K-scale (grid), draft weights (MLE), outcome coefficients (ridge MLE), q_loser_blue (frequency).
**Hand-set:** PREV_W .6, POOL_ALPHA 5, META_K 20, PROF_K 6, KDA_K 4, TEAMBAN_BETA 6, DRAFT_CAP .45, RIDGE 8, CONT_TAU 8, TOPK 4, rating_sd .35, map_sd .20.

### Walk-forward backtest (same 123 international series, MSI 2025 → Worlds 2026; `calibration/backtest_v1_vs_v2.json`)

| model | Brier | log loss | winner acc | exact-lock hit |
|---|---|---|---|---|
| v1 | 0.1950 | 0.5710 | 69.1% | 41.5% |
| v2 ratings only (patch + lineup Elo, v1 sim) | **0.1929** | **0.5668** | 73.2% | 43.1% |
| v2 full (ratings + draft sims) | 0.2023 | 0.5896 | **74.0%** | 43.1% |

**Plainly:** v2 full does **not** beat v1 on Brier or log loss. It wins on winner accuracy and exact-lock hit.
The patch and lineup ratings improve every metric. The draft layer makes probabilities worse, mostly at Worlds 2026 (Brier .2675 vs .2228 without drafts).
The draft model itself predicts held-out picks far better than uniform (pick NLL ≈3.4 vs 5.95; top-5 ≈44%). Its effect on win probability is the weak part.
v2 full is the primary output because the owner asked for draft-driven sims. The ratings-only map probability is recorded on every card (`p_map_a_elo_only`).

Integrity: the zero-market firewall and integrity declaration are unchanged. The only change is that `data_sources` now lists lolesports getTeams/getEventDetails, gol.gg game pages and champion stats, and Data Dragon. None of these is a market source.

## Discord `/lol`

`/lol [date:YYYY-MM-DD]` in the Luck Society Discord server (guild-locked: guild-only command + Worker and workflow allowlist) runs the scanner for that slate (default: today ET) and posts a Luck Society report:
time (ET), league, matchup, Bo, locked exact score and %, series win %, and the Jupiter best bet with EV, or PASS.
Flow: Discord → Cloudflare Worker (`worker/`, Ed25519-verified) → `workflow_dispatch` of `discord-lol.yml` → forecast freeze →
post-forecast Jupiter edge → dashboard → Discord follow-up. Invite: https://discord.com/oauth2/authorize?client_id=1564914230837248060&scope=bot%20applications.commands&permissions=18432
