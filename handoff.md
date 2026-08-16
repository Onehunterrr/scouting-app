# Handoff — value band rescale + daily auto-update

Both pieces of work are **complete, verified, and committed locally**. Nothing
is pushed. There is one manual step left that only the repo owner can do (a
GitHub secret), described at the bottom.

## What changed

### 1. Roster moved onto an EUR 80k-120k band

The old roster drew market values uniformly from EUR 8k-150k; half of it sat
below EUR 80k, which isn't worth a commission. The band is now:
**90% in EUR 80k-120k, 10% running above it, thinning to EUR 200k.**

The tail is deliberate, not decoration. `undervaluedScore` is
`performancePct - marketPct`, so a perfectly flat band would collapse the value
percentile to noise and reduce the whole product to "who has the best stats".

- `player_gen.py` — new band constants (`VALUE_BAND_LO/HI`, `VALUE_TAIL_HI`,
  `VALUE_TAIL_SHARE`, `NO_VALUE_SHARE`) plus `draw_market_value(rng)` and
  `band_quantile(q)`. Both generation sites now call `draw_market_value`.
- `rescale_market_values.py` (new) — remapped the existing 5,000 players
  **rank-preservingly** onto the band. It does not regenerate the roster: names
  had to stay stable because `refresh_prod_players.py` keys prod shortlists and
  ledger rows on `(name, country)`. Already applied to `players_current.json`
  and `scouting.db`. It is idempotent-safe to re-read but only meant to run once.
- Players with nothing on record (1,250 of 5,000) stay at 0 — the estimator
  fills those in, and it was recalibrated to the same band.

Result: recorded values min 80,006 / median 102,198 / max 199,783.

### 2. Estimator recalibrated (`scoring.py`)

Constants were fitted numerically against the live roster, not guessed:

```
VALUE_FLOOR = 80000.0     VALUE_CEIL  = 200000.0
VALUE_MEDIAN = 121452.0   VALUE_SPREAD = 0.4385
VALUE_FACTOR_EXP = 0.6701 VALUE_BASE_REF = 0.561641
FEE_REF = 80000.0         (was 10000.0 — the old dataset floor)
```

`VALUE_FACTOR_EXP` is **new machinery**, and the reason it exists matters if you
touch this again: the band is only 1.5x wide, but `leagueStrength x ageFactor x
minutesFactor` alone already spread 1.69x across the middle 90% of the roster.
On the old formula the economics consumed the entire band and the quality
composite had nowhere left to move the number — a naive fit drove the quality
term to **zero** (bottom-decile-quality estimate 95,010 vs top-decile 95,391).
The exponent damps those three factors so quality gets room. Fitted against two
constraints: p02→p90 spans 80k→120k, and top-decile quality estimates 1.25x
bottom-decile.

The composite is mildly anti-correlated (**-0.15**) with the economic
multiplier — thin minutes inflate per-90 rates while cutting the minutes
factor — which is why the two terms cancel if you don't constrain the fit.

Estimated values now land 89.6% in band vs 90.0% for recorded: same scale.

### 3. All three engine copies kept bit-exact

The value engine exists three times and they must not drift:
- `scoring.py` (Python)
- `build_html.py` (JS port, ~line 1493 constants, ~line 2172 `estimateMarketValue`)
- `db_schema.py` `UNDERVALUED_VIEW_SQL` (~line 180) — **this one is executed**,
  `db_migrate.py` creates the `player_scores` view from it.

The `player_scores` view baked into `scouting.db` was stale after the change and
had to be rebuilt (`DROP VIEW` + `executescript(UNDERVALUED_VIEW_SQL)`). That
was the cause of the `test_scores_match_sql_view` failure. **If you change the
formula again, rebuild that view or the test fails confusingly.**

Verified: SQL vs Python 0 mismatches across 5,000 rows; JS vs Python max
difference 7.1e-15 on all of `estimatedMarketValue`, `displayMarketValue`,
`undervaluedScore`, `acquirabilityScore`, `dealScore`.

### 4. Daily auto-update against the live DB

- `daily_update.py` (new) — connects via `DATABASE_URL`, drifts every player by
  one day of match activity, adds `NEW_PLAYERS_PER_DAY` (default 2) new players
  on the band, stamps `last_updated`. One transaction, `--apply` to write.
  Only the `players` table is touched and existing rows are updated in place,
  so ids stay stable and **no shortlist breaks**. Aborts and rolls back if the
  count is wrong, if any row missed today's stamp, or if any value fell below
  the floor.
- `.github/workflows/daily-update.yml` (new) — 03:00 UTC daily, plus
  `workflow_dispatch` with a dry-run toggle. `concurrency` group prevents two
  overlapping runs double-counting a day.
- `weekly_update.py` — `refresh_player()` gained `play_chance` / `agent_chance`
  params (defaults unchanged, so weekly behaviour is identical). Its market-value
  tick-up was **buggy for the new band**: it re-entered no-value players at
  `randint(8000, 20000)`, below the floor, and compounded 1.03-1.12x with no
  cap. Now enters at a band value and caps at `VALUE_TAIL_HI`.

Cadence note: weekly rates fired daily would give every player seven matches a
week. Daily uses `PLAY_CHANCE = 0.28` (~2 matches/wk) and `AGENT_CHANCE = 0.006`.

## Verification status

- `pytest test_api.py` — **65 passed**.
- `node --check` on the extracted app JS — OK.
- HTML regeneration is reproducible, so CI's stale-frontend check passes.
- `daily_update.py` exercised end-to-end against a SQLite copy: 5,000 → 5,002
  players, all stamped, values still in band.

Two tests were updated (not weakened) because the constants they pinned moved:
`test_acquirability_spec_examples` — recomputed, and kept in step with the
worked examples in `acquirability_spec.md`. Change both together.

`test_app.js` could not run: **`jsdom` is not installed** in this environment.
Pre-existing gap — CI never ran it either (CI only does `node --check`). Not
caused by this work, but it means the DOM-level suite is unverified.

## Known consequence, flagged deliberately

Acquirability rose across the board (median **44.1 → 51.2**). `FEE_REF` is
documented as the dataset floor, and the floor moved 10k → 80k, so every fee
term now sits near 1.0: at 80k it's 1.0, at 200k only 0.726. **The fee is no
longer much of a discriminator** — contract urgency and representation now carry
almost all of the acquirability signal. That follows directly from asking for a
narrow band and is not a bug, but if you want fee to bite again the lever is
`FEE_EXP` (currently 0.35).

`scoring.py`'s `market_history()` still floors `start_value` at 6000.0. With
values ≥80k and `start_frac ≥ 0.45` that floor is now unreachable — dead but
harmless, left alone to avoid touching another JS-parity surface.

## The one thing left — needs the repo owner

The daily workflow needs a **`DATABASE_URL` repository secret** before it will
do anything: Settings → Secrets and variables → Actions → New repository secret.

Use Railway's **`DATABASE_PUBLIC_URL`**, not the internal one — a GitHub runner
is outside Railway's network and cannot resolve `*.railway.internal`.

Until that secret exists the workflow fails fast with a clear error (that check
is deliberate — without it SQLAlchemy would fail with something far less
obvious).

After adding it: trigger the workflow manually with the apply box **unchecked**
to get a dry run against prod before letting the schedule write anything.

## Also still outstanding, from the previous handoff

The prod password rotation (`JWT_SECRET` still on the dev default) — unrelated
to this work, carried forward.
