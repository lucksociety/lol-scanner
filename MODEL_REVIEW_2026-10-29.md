# Model and Discord review — 2026-10-29

Ratings.get() repeatedly applied the offseason shrink for the same long gap when called by forecast/tuning/update paths. It now records which last-result timestamp has already been shrunk; repeated reads do not keep erasing evidence. A later real result opens a new gap. The last actual result timestamp stays intact.

Validation: 61 Python tests and all 3 Worker test files pass. Both Ratings and PatchRatings are covered by a regression that checks repeated reads and a second future gap. Existing frozen files are untouched.

This changes ratings for long-break teams. Earlier v2.1 backtest numbers do not validate the corrected implementation; repeat the chronological tuning/backtest before claiming an accuracy gain or retuning uncertainty. That full replay requires the ignored lolesports schedule cache and its API key; only the gol.gg seed is committed. Retain ratings-only as the headline until a draft challenger improves proper scoring metrics on a fresh holdout.

A read-only PR workflow now runs the model/command regressions. No dashboard presentation files were changed.

## Deployment prerequisites

Merge after checks pass. Redeploy the existing Worker for NBA/NHL/LoL changes as applicable; restart the NFL gateway bot after updating its checkout. Live Discord delivery, command registration and hosted secret state have not been verified by these offline tests.
