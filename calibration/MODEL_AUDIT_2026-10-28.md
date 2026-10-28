# Model audit, 2026-10-28: MKOI vs T1 (v2 → v2.1)

Trigger: Jupiter priced T1 at about 86% to beat MKOI (Bo3). Frozen v2 had T1 at 61.4%, with a 2-0 lock at 33.1%.
The audit uses results only. Market prices were used only to compare. Nothing from the market enters the forecast.

## MKOI vs T1 decomposition (MKOI's probability)

| step | v2 (frozen) | v2.1 |
|---|---|---|
| tuned λ / K-scale | 0.80 / 0.80 (grid corner) | 0.96 / 0.50 |
| Elo MKOI – T1 | 1584.3 – 1657.5 | 1588.1 – 1703.1 |
| map p (Elo) | .396 | .340 |
| Bo3 analytic p²(3−2p) | .346 | .269 |
| + rating/map noise | .357 | .283 |
| + side (MKOI blue in G1, loser picks side) | .378 | .302 |
| + draft layer (full) | **.386** (T1 61.4%) | **.331 (T1 66.9%)** |
| lock | T1 2-0, 33.1% | T1 2-0, 37.1% |

The Bo3 conversion was checked against the analytic formula. For p = .6039, the sim with no noise gives .654 and the analytic value is .654. 2-0 is .3634 vs .3647.
At the market's 86%, the implied map probability is about .765, an Elo gap of about 205. v2.1 has a gap of 115.

## Root causes

1. **KeSPA Cup and Asian Games counted as "international".** They made up 53 of the 204 "international" series before Worlds 2026.
   - They were scored in the (λ, K) tuning objective and got K_INTL.
   - The tuning ended at the grid corner, λ = 0.80. That decayed T1 almost to the LCK prior: 1657.5, against 1745.5 in v1.
   - Prequential cross-region map calibration slope at λ = .8 was 1.52 ± 0.22, which means underconfident.
   - Fix: v2.1 tunes on and applies K_INTL to cross-region events only (Worlds, MSI, First Stand, EWC). The K grid is widened.
2. **The roster-continuity term was selection-biased.** It had ρ ≈ −1.39 at Worlds 2026.
   - The corpus keeps each team's last 12 series before an event, so "maps together" stands in for "qualified for later events".
   - It moved BLG–TES from .603 to .871, because TES subbed 369 in for ZUIAN.
   - In the backtest it gave TSW .91 vs LOS, and LOS won 2-0.
   - Fix: v2.1 drops the term (ρ = 0). Continuity is still shown on cards.
3. **gol.gg alias bug.** `golgg_name` took the *oldest* alias.
   - HLE was simulated with its 2025 roster: Peanut, and pid 1228, which is BLG's Viper.
   - Gen.G, DRX, BRION, TT, Fluxo and EDG were also affected.
   - Fix: use the alias with the most recent game before the forecast date. This applies to both variants.
   - Backtest effect is negligible (v2 Brier .2023 → .2025). It matters for G2–HLE.

## Calibration and temperature (tested, rejected)
- Walk-forward map-level temperature came out at τ ≈ 1.38–1.45. Applying it out of sample made series Brier worse (.1914 → .1938), and the series slope after τ was 0.93.
- Series slopes (logistic, 123 series):
  - v2 full: 0.94 ± .22
  - v2 ratings-only: 1.31 ± .28
  - v2.1 ratings-only: 1.29 ± .28
  - v2.1 full: 1.01 ± .23
- Underconfidence is not proven at series level, so v2.1 has **no temperature**.
- Favourites priced 55–70%:
  - v2 full: predicted .630, won .692 (n = 52)
  - v2.1 full: predicted .626, won .706 (n = 51)
  - The gap is within noise for n ≈ 50.
- Historic market comparison: not possible. odds_snapshots exist only for Oct 28–30, none of them settled.

## Walk-forward backtest (123 series, MSI 2025 → Worlds 2026, 10k sims)

| model | Brier | log loss | acc | lock hit |
|---|---|---|---|---|
| v1 | .1950 | .5710 | 69.1% | 41.5% |
| v2 ratings-only | .1929 | .5668 | 73.2% | 43.1% |
| v2 full (frozen model) | .2023 | .5896 | 74.0% | 43.1% |
| v2.1 ratings-only | **.1914** | **.5632** | 73.2% | 44.7% |
| v2.1 full | .1995 | .5817 | 73.2% | **45.5%** |

Full-model Brier by event, v2 → v2.1:
- MSI25: .1463 → .1671
- Worlds25: .1879 → .1895
- FS26: .1436 → .1473
- MSI26: .2255 → .2439
- Worlds26: .2675 → .2266

The gain is concentrated in Worlds 2026, where the bug was active. Earlier events get slightly worse without the continuity term. The draft layer still hurts against ratings-only. Files:
- `backtest_v1_vs_v2.json` (unchanged)
- `backtest_v1_vs_v2.1.json`

## Re-forecast of Oct 28–30 (EVALUATION ONLY, not frozen)
`reforecast_2026-10-28_to_30_v2.1_EVAL_ONLY.json`. The frozen v2 files are untouched.

| series | frozen v2 | v2.1 | no-vig market (Oct 28 11:41 ET, comparison only) |
|---|---|---|---|
| MKOI–T1 | T1 61.4% | T1 66.9% | T1 85.2% |
| LYON–IG | IG 65.7% | IG 57.6% | IG 66.3% |
| CFO–KC | KC 73.9% | KC 81.9% | KC 79.2% |
| BLG–TES | BLG 87.1% | BLG 72.1% | BLG 75.3% |
| G2–HLE | HLE 56.7% | HLE 59.9% | HLE 79.2% |

The remaining gap on T1 is not a bug. Results-only Elo sees T1's weak summer of 2026, with losses to HLE, Gen.G and DK, where T1 fell from about 1770 to about 1700. LCK vs LPL cross-region results are also under-rated: 63.5% actual against 49.9% expected.


## Follow-up (owner-approved, 2026-10-28 ~1:10 PM ET): primary = v2.1-ratings; v2.2 tested, not adopted
- The headline is now ratings-only v2.1 (`v2.1-ratings-patch`). Draft sims are informational only.
- v2.2 adds per-home-league Elo offsets: ridge with prior SD 50 (pre-declared), fitted on prequential cross-region residuals before each cutoff.
  - At the Worlds 2026 cutoff the offsets were LCK +64, LTA North +34, LPL +9, LEC −15, LCP −15.
  - Backtest: v2.2 Brier .1933 / log loss .5719, against v2.1-ratings .1914 / .5632. That fails the adoption rule.
  - Per-event Brier: Worlds25 .1923→.1877, FS26 .1536→.1885, MSI26 .2221→.2279, Worlds26 .2167→.2118.
  - Calibration slope 1.29 → 1.02. Sensitivity: sd 25 gives .1919/.5652; sd 100 gives .1950/.5836.

EVAL ONLY: Oct 28–30 at the original freeze cutoffs, 50k sims, in `eval_2026-10-28_to_30_v2.1-ratings_vs_market_EVAL_ONLY.json`.

| series | frozen v2 | **v2.1-ratings (primary)** | v2.2 (not adopted) | no-vig market 11:41 ET (comparison only) |
|---|---|---|---|---|
| MKOI–T1 | T1 61.4% | **T1 71.6%**, lock T1 2-0 43.3% | T1 82.5% | T1 85.2% |
| LYON–IG | IG 65.7% | **IG 61.7%**, IG 2-0 34.2% | IG 57.0% | IG 66.3% |
| CFO–KC | KC 73.9% | **KC 76.2%**, KC 2-0 47.9% | KC 78.4% | KC 79.2% |
| BLG–TES | BLG 87.1% | **BLG 67.7%**, BLG 2-0 39.6% | BLG 67.7% | BLG 75.2% |
| G2–HLE | HLE 56.7% | **HLE 63.2%**, HLE 2-0 35.7% | HLE 75.7% | HLE 79.2% |

v2.2 lands closer to the market on T1 and HLE. That is not evidence for it: the market is not ground truth, and odds are never an input. v2.2 lost on out-of-sample results.
