# Handoff — ScoutEdge scoring / market-value model rework

Checkpoint at ~150k context tokens. Code is **written but not yet verified, built, or tested.**

## Goal

Improve accuracy of the scoring / market-value model. The model lives in **two places that
must stay numerically identical**:

- `scoring.py` (Python, used by `api_server.py:157` via `scoring.compute_scores`)
- the JS copy inside the `build_html.py` template (one big `"""` string, lines 38–3633)

There is also a `player_scores` SQL view in `db_schema.py` that duplicates the percentile logic.

## Status: what is DONE

### `scoring.py` — fully rewritten (all 7 requested changes)

1. **Main bug fixed.** `marketPct` now ranks on `displayMarketValue`, not raw `marketValue`.
   Previously the 1,231 unknown-value players (marketValue=0) sorted to the bottom of the value
   distribution and got a near-maximal undervalued gap.
   **Measured: unknown-value share of High Priority went 58% → 8%** (roster baseline 25%).
2. **Empirical-Bayes shrinkage.** `shrink_rate(rate, minutes, pos_mean)` =
   `(rate*minutes + posMean*K)/(minutes + K)`, `SHRINK_K = 900`. Position-group means are
   minutes-weighted (`_weighted_mean`). `lowSample = minutes < LOW_SAMPLE_MINUTES (900)`;
   low-sample players are capped at Watchlist and cannot earn High Priority.
3. **Value scale recalibrated.** Jitter removed. Log-scale, right-skewed, `VALUE_FLOOR=10000`,
   `VALUE_CEIL=500000`. Calibrated: `VALUE_MEDIAN=79000`, `VALUE_SPREAD=3.15`,
   `VALUE_BASE_REF=0.490347`.
   Resulting estimates: med 79,000 / p90 168,510 / p99 307,000 / max 489,000 / min 19,700 —
   nothing truncated at either clamp. (Known values: min 8,019 / med 79,387 / max 149,981.)
4. **Continuous league strength.** `league_strength(p)` → `LEAGUE_STRENGTH` dict (intentionally
   **empty**, pending real data) falling back to `tier_strength(tier) = exp(-0.155*(tier-2))`
   → 1.000 / 0.856 / 0.733. Used in BOTH the value engine (replacing the 1.15/1.0/0.85
   `tierFactor`) and `performanceScore` (replacing the `TIER_MULT` 1.00/0.85/0.70 buckets).
   `p["tierMult"]` retained as an alias = `leagueStrength` so existing consumers keep working.
5. **Age-value curve.** `age_value_factor(age)`: rising limb from 16 (floor 0.70, exponent 0.85),
   flat 1.0 across the 24–27 peak, `exp(-0.055*(a-27)^1.35)` decline after. Replaces the linear
   youth premium. NOTE: dataset ages are 17–26, so the decline limb never fires on this data.
6. **Explainability.** `build_explain(p)` → `p["explain"]` with `summary` (e.g. "Flagged because:
   88th pct output, 22nd pct value, age 19, 1,740 mins."), all percentile drivers, `lowSample`,
   `shrinkWeight`, `leagueStrength`, `ageValueFactor`, `marketValueEstimated`, and `notes[]`.
   Helpers `_ordinal` / `_group_thousands` are hand-rolled so Python and JS agree exactly.
7. **`market_history` noise removed.** Now a smooth deterministic geometric trajectory with a
   smoothstep ease; last point still anchored to the current value.

### Two extra fixes found while measuring (both intentional, both need mentioning to the user)

- **`percentile_ranker`** added: `percentile_rank` was O(n²) — 150M comparisons per
  `compute_scores` call. Sorted-array + `bisect_right` is **integer-exact**, so numbers are
  bit-identical; only cost changes. **compute_scores went from minutes to 0.06s.** The JS copy
  (`percentileRanker`, binary upper-bound) matters even more — the browser recomputed on every
  weight change.
- **Quality-composite saturation bug.** The old global clamp references (`ga90/0.7`, `prog90/7`,
  `def90/4.5`) were badly miscalibrated for this roster: **MF progression 100% clamped, GK
  distribution 100% clamped** (pass completion is 0–100, reference was 7), FW attacking 87%,
  DF defending 74%. Those terms carried zero information. Now position-relative:
  `POS_REF_MULT = 1.8` × the group's minutes-weighted mean, with `DEFAULT_POS_REFS` as the
  single-player fallback. This affects the **value estimate only** — the performance score is
  rank-based and was never affected by clamping. GK save%/GC/clean-sheet references were
  measured and left alone; they already discriminate.

### `build_html.py` — JS port written (NOT yet verified against Python)

Ported in step with `scoring.py`: constants block, `percentileRanker`, `clamp01`,
`tierStrength`, `leagueStrength`, `ageValueFactor`, `ageBand`, `shrinkRate`, `shrinkWeight`,
`weightedMean`, `ordinal`, `groupThousands`, `qualityComposite`, `roundValue`,
`estimateMarketValue`, `marketHistory`, `buildExplain`, and the 5-pass `computeScores`.
`TIER_MULT` const was deleted.

**Constraint: the template is a plain (non-raw) `"""` string — backslashes are interpreted by
Python, so the JS must contain none.** Braces are safe (assembled with `.replace()`, not
`.format()`).

## Status: what is NOT DONE (next steps, in order)

1. **Verify Python↔JS parity.** Harness is written but never run:
   - `<scratchpad>/extract_js.py` — slices the scoring functions out of the template into
     `js_engine.js` (brace-matching extractor) for Node.
   - Scratchpad dir:
     `C:\Users\hunte\AppData\Local\Temp\claude\C--Users-hunte-Desktop-ScoutEdge\28113f29-bc5c-4708-beed-f3398f247ca5\scratchpad`
   - Run it, then compare all 5,000 players field-by-field (`estimatedMarketValue`,
     `undervaluedScore`, `performancePct`, `marketPct`, `flag`, `explain.summary`) Python vs Node.
   - Watch for: `Math.pow`/`math.pow` 1-ulp differences on the age curve; float summation order
     in `weightedMean` (kept in array order deliberately); `Math.round` vs `_js_round`.
2. **Surface `explain` + `lowSample` in the player detail modal.** Anchor: `m-explain`
   `innerHTML` around **build_html.py:2598** (search `document.getElementById("m-explain")`).
   Two strings there are now **factually wrong and must be updated**:
   - the `calc-note` (~line 2595) still claims *"the market percentile uses the raw 0 (never the
     display estimate) -- estimates can never inflate the score"* — that is exactly the bug that
     was fixed; it is now false.
   - the `calc-chip` (~line 2586) says `Tier ${p.tier} multiplier` — it is now a continuous
     league-strength coefficient, not a tier bucket.
3. **Update `db_schema.py`'s `player_scores` view** (`UNDERVALUED_VIEW_SQL`, tier_mult CASE at
   ~line 122). SQLite here **does** have `exp`/`ln`/`pow` (verified, 3.45.1). Needs: shrinkage via
   window functions, continuous tier curve, and `market_pct` over `displayMarketValue`.
   **RISK — flag to user:** `market_pct` now depends on the full estimator, so the view must
   reproduce it near-exactly. `test_scores_match_sql_view` (test_api.py:189) asserts
   `undervaluedScore` within ±0.1, but one rank flip among 5,000 peers = ~2.0 undervalued points,
   so tolerance is unforgiving. If exact reproduction proves infeasible, the honest options are
   (a) materialise `estimated_market_value` into the players table at migration time and have the
   view read it, or (b) narrow the test to `performance_pct` and document the gap. Per-league
   `LEAGUE_STRENGTH` overrides can't be expressed in SQL at all until materialised — say so.
4. **Regenerate `scouting.db`** — `test_api.py:27` copies the shipped DB, so the view change only
   lands after the DB is rebuilt (`db_migrate.py`).
5. **Rebuild the frontend:** `python build_html.py` (regenerates `index.html` and
   `Scouting_App_Prototype.html`).
6. **Run `python -m pytest test_api.py -v`.**
7. **Spot-check 3–4 players** as the user asked: one unknown-`marketValue`, one low-minutes, one
   young — confirm the flags now make sense.

## Verified numbers so far (don't re-derive)

- Roster: 5,000 players; tiers 2/3/4 = 2306/1695/999; FW/MF/DF/GK = 1167/1514/1487/832.
- `marketValue` known for 3,769, zero for 1,231 (24.6%). Minutes 800–2,744 (median 1,742) —
  **nothing under 500**, so a hard minutes floor is moot; that is why `lowSample` was used instead.
- Minutes-weighted position means (basis of `DEFAULT_POS_REFS`, ×1.8):
  FW ga .9480 prog 6.4963 def .5108 / MF .7530 11.2525 2.2084 / DF .2040 4.6261 5.3024 /
  GK .8988 73.1944 .7911
- New flag counts: none 3633, Watchlist 377, High Priority 361, HP-Unrep 318, WL-Unrep 311.
  (Old: none 3556, HP 411, WL 375, HP-Unrep 365, WL-Unrep 293.)

## Decisions already made with the user — do not re-litigate

- Value scale stays **calibrated to this population** (median ≈ €79k, ceiling €500k). An earlier
  answer favouring a €3M lognormal tail was **superseded** by the user's follow-up spec: do NOT
  inflate to millions.
- An earlier answer chose "exclude sub-500-minute players entirely"; also **superseded** — the
  follow-up spec says the floor is moot and to use `lowSample` + a High Priority gate instead.

## QUEUED (new request, not started) — Central European roster coverage

User: *"I want my app to be a lot more central european players not just from the balkans."*
Correct diagnosis — the current 32 countries have **six Balkan** (Albania, Croatia, Montenegro,
North Macedonia, Serbia, Slovenia) and **zero true Central European** ones.

**Decisions the user already made (do not re-ask):**

- **Add six countries:** Poland, Czechia, Slovakia, Hungary, **Austria, Switzerland**.
- **Rebalance to hold the roster at 5,000** — i.e. reduce over-represented countries to make room,
  rather than growing the total.

**Two caveats the user was shown and accepted — but which still need handling in code:**

1. Austrian/Swiss lower divisions are wealthier and already well-scouted, so those players are less
   likely to be genuinely unrepresented (the app's wedge). Consider skewing AT/CH toward tier 3–4
   and a lower `hasAgent = "No"` rate so they don't dominate the unrepresented flags.
2. **Rebalancing deletes existing players.** The app has per-user shortlists and notes keyed by
   player id (`api_server.py` accounts; check `db_tables.py` / `db_schema.py` for the shortlist and
   notes tables and their FK/cascade behaviour). **Before deleting anything, check whether those
   tables reference `players.id`** and either remap or accept orphaning deliberately. Do not
   silently break saved shortlists.

**Work involved** (all in `player_gen.py` unless noted): per-country first/last name pools,
`SOURCE_CITIES`, `COUNTRY_TLD`, `COUNTRY_FEDERATION`, plus `PATHWAYS` in `transfer_pathways.py`.
Then regenerate `players_current.json`, re-run `db_migrate.py`, rebuild the frontend.

**Ordering note (already checked):** this does NOT invalidate the scoring rework. `compute_scores`
derives the position reference points from the cohort at runtime, so they self-adjust. Only
`VALUE_BASE_REF` needs re-tuning, via the existing calibration script (bisects until
`median(estimate) == 79000`) — one command, not a redo. `DEFAULT_POS_REFS` is a standalone-call
fallback and should be refreshed at the same time.

**Recommended sequence:** finish the scoring work (steps 1–7 above) first so the parity/SQL/test
baseline is green against the current dataset, *then* do the roster change and re-calibrate.

## Resume

Run `/clear`, then: "continue from handoff.md".
