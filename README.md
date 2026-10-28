# lol-scanner

Daily League of Legends esports scanner with a hard wall between **forecast** and **market**:

1. **Forecast (market-free, frozen).** Results-only map Elo (lolesports results since 2025) → 100k Monte Carlo
   per series → exact Bo1/Bo3/Bo5 score distribution, locked exact score, side/handicap/total probabilities,
   adversarial review. Written once to `forecast_log/<date>.json` with timestamp + SHA-256.
2. **Post-forecast edge layer** (`scanner/edge.py`). Only after the freeze: pulls Jupiter prediction-market
   prices for LoL, de-vigs each binary pair, and shows price / no-vig prob / our prob / edge / EV per $1.
   **BEST BET** = highest-EV Jupiter line (all markets incl. alt lines, both sides) with EV > 0; otherwise **PASS**.
   The locked score stays the headline prediction; "cashes on lock" is shown per line as info only. Sanity guard:
   lines priced < 0.03 or > 0.97, or with no volume and no orderbook, are skipped (`LOL_MIN_ASK` / `LOL_MAX_ASK`).
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

> Primary headline since 2026-10-28: **v2.1-ratings** (see below). Draft sims are an informational panel.

v1 stays frozen next to it as the baseline (`forecast_log/<date>.json`); v2 is written to `forecast_log/<date>.v2.json`.
Both are frozen (SHA-256) before any market data is read; the edge layer reads only the frozen file.

**Patch.** Each game is tagged with its patch. Before every series, older-patch evidence is decayed by
λ^(Δpatch) toward the league prior, and K is scaled. (λ, K-scale) are **tuned** walk-forward by one-step-ahead map log loss on
*international* maps before the cutoff. If there is no international history in the window, the default is (1.0, 1.0).
Revision disclosed: the first attempt tuned on all maps. It picked K-scale 2–3 and made forecasts worse
(`calibration/backtest_v1_vs_v2_attempt1_allmaps_tuning.json`), so the objective was narrowed after seeing that backtest.
For 2026-10-28 v2 tuned λ=0.8, K-scale=0.8 (a grid corner caused by a bug; see v2.1 below). The patch meta is gol.gg champion stats for the current patch, blended 0.6 with the previous patch and shrunk toward 50%.

**Lineups.** The expected five come from the lolesports getTeams roster and the event's latest starters.
- CONFIRMED: on the official roster and started every event game.
- PROBABLE: anything else.
- UNCERTAIN: a different player started one of the last 3 event games. Both lineups are then simulated.

Roster continuity (maps the five have played together) entered the v2 outcome model as exp(−n/8). v2.1 removes it because it was selection-biased.

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

### v2.1 (`v2.1-patch-lineup-draft`, applies to new freezes after 2026-10-28; see `calibration/MODEL_AUDIT_2026-10-28.md`)
- (λ, K) tuning and K_INTL now cover **cross-region events only** (Worlds/MSI/First Stand/EWC). v2 also counted KeSPA Cup and Asian Games, which drove λ to 0.8. Worlds 2026 now tunes λ=0.96, K×0.5.
- The roster-continuity term is removed (ρ=0) because it was selection-biased.
- gol.gg alias fix: the most recent alias before the date is used, not the oldest. HLE had been simulated with its 2025 roster.
- No temperature: walk-forward τ made out-of-sample Brier worse.

| model | Brier | log loss | winner acc | exact-lock hit |
|---|---|---|---|---|
| v2.1 ratings only | **0.1914** | **0.5632** | 73.2% | 44.7% |
| v2.1 full | 0.1995 | 0.5817 | 73.2% | 45.5% |

Oct 28–30 stay frozen on v2. The v2.1 re-forecast of those dates is evaluation-only (`calibration/reforecast_2026-10-28_to_30_v2.1_EVAL_ONLY.json`).

### Primary model since 2026-10-28: `v2.1-ratings-patch` (ratings-only headline)
The headline is the v2.1 patch-recency map Elo with the ratings-only simulator (`scanner/sim_r.py`: rating sd 0.35, map sd 0.25, no side or draft term). That headline is what gets locked, graded, priced by the edge layer and exported to the browser sim.
The draft-path simulation still runs. It is stored as `draft_sim` on each card and shown as an **informational** panel.
Reason: on the 123-series backtest, the draft layer made Brier worse in every version (v2.1 full .1995 vs ratings-only .1914).

**v2.2 (cross-region league offsets) was tested and NOT adopted.** For each home league, v2.2 adds an Elo offset. The offsets are fitted with a ridge (prior SD 50 Elo, set before the backtest) on prequential residuals of cross-region maps before each cutoff. This uses results only.
Rule declared before the run: adopt v2.2 only if out-of-sample Brier **and** log loss both improve. They did not.

| model (123 series, walk-forward, `calibration/backtest_v2.1_ratings_vs_v2.2.json`) | Brier | log loss | winner acc | calib. slope |
|---|---|---|---|---|
| **v2.1 ratings-only (PRIMARY)** | **0.1914** | **0.5632** | 73.2% | 1.29 ± 0.28 |
| v2.2 offsets, sd 50 (pre-declared) | 0.1933 | 0.5719 | 73.2% | 1.02 ± 0.23 |
| v2.2 sensitivity: sd 25 / sd 100 | 0.1919 / 0.1950 | 0.5652 / 0.5836 | 73.2% / 68.3% | 1.18 / 0.85 |

Per event (Brier, v2.1-ratings → v2.2 sd 50):
- MSI25: .1431 → .1431 (no offsets: no earlier cross-region data)
- Worlds25: .1923 → .1877
- First Stand 26: .1536 → .1885
- MSI26: .2221 → .2279
- Worlds26: .2167 → .2118

The offsets fix the LCK under-rating at Worlds. At First Stand 2026 they over-correct: G2 beat Gen.G 3-0 with v2.2 at 5.5%. v2.2 stays in code (`variant="v2.2"`) for re-testing as more cross-region data arrives.

## Discord `/lol`

`/lol [date]` (pick Today / Tomorrow / Yesterday / any of the next 14 or past 7 days from the autocomplete list, or type YYYY-MM-DD) in the Luck Society Discord server (guild-locked: guild-only command + Worker and workflow allowlist) runs the scanner for that slate (default: today ET) and posts a Luck Society report:
time (ET), league, matchup, Bo, locked exact score and %, series win %, and the Jupiter best bet with EV, or PASS.
Flow: Discord → Cloudflare Worker (`worker/`, Ed25519-verified) → `workflow_dispatch` of `discord-lol.yml` → forecast freeze →
post-forecast Jupiter edge → dashboard → Discord follow-up. Invite: https://discord.com/oauth2/authorize?client_id=1564914230837248060&scope=bot%20applications.commands&permissions=18432
