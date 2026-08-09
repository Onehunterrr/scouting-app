# Handoff — payload trim + cache-freshness shipped locally; NOT pushed

Checkpoint at ~152k tokens. Everything below is verified. One decision is left:
whether to push (push auto-deploys).

## Already live in prod (nothing to do)

- `2ca8da6` market-value filter (`minValue`/`maxValue`, `MAX_PAGE_SIZE` 10000).
- `47983c2` row-render rewrite. **Pushed and verified live this session.** The
  four interaction checks the previous handoff asked for all passed against a
  local run before the push:
  row click -> modal; star toggles + re-renders and does *not* open the modal;
  compare checkbox adds to the bar, skips the modal, and `COMPARE_MAX` refuses
  the 5th; arrow-key nav keeps DOM index == `data-idx` == `currentRows[i]` and
  Enter opens the right player. "All" unfiltered went **13.1s -> 3.0s**.
  Live check: `/app` serves the `rowHtml` build, landing 200 in 0.24s.

## UNCOMMITTED-TO-PROD WORK — committed locally, NOT pushed

Two backend changes, both verified. `git log origin/main..HEAD` will show them.

### 1. List payload trim (`api_server.py`, `build_html.py`)

`explain` + `dealExplain` are nested prose/driver objects ~2.2 KB per player and
**only the modal reads them**. `LIST_OMIT_FIELDS` + `_list_item()` strip them
from `/api/players` responses only; `/api/players/{id}` still returns them whole.

`_list_item()` returns a **copy** — those dicts are the shared `_scored_cache`
entries and must never be mutated by a request. Do not "optimise" that to a pop.

Client side, `openModal` now rebuilds either blob on demand:
`p.explain || (p.explain = buildExplain(p))` and the `buildDealExplain(p,
acquirability(p))` equivalent, memoised onto the player object.

Measured on the full 5,000-row response: **18.98 MB -> 9.5 MB raw (-50%),
2.79 MB -> 1.79 MB gzipped (-36%)**. Browser-verified: with the trimmed payload
the modal still renders 2 ex-boxes, 11 chips, 4 driver lines, identical text.

### 2. Roster cache freshness (`api_server.py`)

`_raw_players` / `_scored_cache` lived for the process lifetime, so a write by
another process — `refresh_prod_players.py` uses its own engine — was invisible
until the container restarted. That is why the roster refresh needed a redeploy.

Now: poll a cheap aggregate fingerprint at most once every
`CACHE_CHECK_SECONDS` (env, default 60) and rebuild only when it moves.
`_db_stamp()` = `(COUNT(id), MAX(last_updated), SUM(market_value))`.
**SUM is in the stamp on purpose** — the e2e test changed one player's value
while count and date stayed identical, which COUNT+MAX alone would have missed.

- `get_scored()` now resolves `get_players()` **first**, because the freshness
  check can empty `_scored_cache`; reading the score cache first would serve a
  scored list built from a superseded roster. Don't reorder those lines.
- `invalidate_caches()` replaces the ad-hoc global resets.
- New `POST /api/admin/cache/refresh` (admin-gated) for the immediate button
  right after a bulk load, so nobody has to redeploy.
- Thread-safe: FastAPI runs sync endpoints in a threadpool. `_cache_lock` +
  double-checked None guard in `get_players()`.
- `import time` / `import threading` moved to the top of the module. **Required**
  — `warm_caches()` runs at import, before the old mid-file `import time`.

End-to-end proof against a real external sqlite writer, `CACHE_CHECK_SECONDS=3`:
before the interval the response stayed cached; after it the log printed
`[cache] players table changed (5000, '2026-07-27', 295928502) -> (…296372946);
reloading` and the player came back rescored (UV 14.5 -> -41.2), not just re-read.

### Tests

`test_api.py` **44 passed** (38 baseline + 6 new): list omits the blobs but keeps
every field the client needs to rebuild them; single-player endpoint still has
them; the trim doesn't mutate the score cache; an external write is picked up
without a restart; the admin endpoint is gated and reloads; `invalidate_caches()`
clears both. JS parses under `node --check`.

### Next step

Decide on the push. Everything is verified; the only reason it is sitting local
is that push auto-deploys and that was the user's call last time.

## Data accuracy — measured, nothing implemented yet

Backtested `estimate_market_value` against the 3,750 players that *do* have a
recorded value (`players_current.json` + `scoring.compute_scores`):

- **Median absolute error 50.3%**; only 24.2% land within ±25%, 49.7% within
  ±50%. Median signed bias **+15.4%** (over-estimates).
- **The undervalued gap partly self-cancels for the 1,250 estimated-value
  players.** `marketPct` ranks `displayMarketValue`, which for them *is* a
  monotone function of the same quality composite driving `performancePct`.
  Correlation between the two: **+0.015 for known-value players vs +0.731 for
  estimated ones.** UV spread halves (sd 40.9 -> 20.5) and the High Priority
  rate drops **16.4% -> 4.4%**. That 25% of the roster is structurally
  under-surfaced — the mirror image of the bug `scoring.py:816` describes fixing.

Proposed direction (not started, not agreed): treat estimated-value players as a
separate confidence class — a value *band* rather than a point estimate, and
keep them out of a gap they cannot meaningfully express — plus fill the 1,250
missing values and 1,535 unknown-representation records.

## Other backend findings (not implemented)

1. **`_RATE_BUCKETS` grows unbounded** — `defaultdict` keyed by client IP with no
   eviction; every unique IP leaks two deques for the process lifetime.
2. **No ETag / Cache-Control** on `/api/players`; every reload re-downloads an
   identical roster.
3. Filter+sort is a full Python pass per request. Fine at 5k; ceiling ~50k.
4. **Railway's edge already gzips** (`Content-Encoding: gzip` confirmed on prod).
   Adding `GZipMiddleware` would buy nothing — the 18 MB in the old handoff was
   the *decompressed* size. Don't re-propose it.

## Still open (carried over)

1. **Rotate the Postgres password — STILL OPEN.** Printed in plaintext by
   `railway variables --service postgres` in an earlier session. Do it attended:
   Postgres service -> Variables -> regenerate `POSTGRES_PASSWORD` -> let it
   redeploy -> redeploy `scouting-app`. `DATABASE_URL` is a
   `${{Postgres.DATABASE_URL}}` reference so it updates itself. Verify:
   `/api/meta` returns 200 with `"backend":"postgresql"`. Not done from an agent
   session — if the two services redeploy out of order the site is down until it
   settles.
2. **Commit `2ca8da6`'s subject line is a stray `@`** (PowerShell here-string in
   a bash shell). Amending worked locally but the force-push was rejected: `main`
   is protected. Fixing it means temporarily lifting branch protection — user's
   call. Body of the message is intact.
3. **500/page option** — never added; the render rewrite may make it unnecessary.
4. **Landing page prose** still hardcodes "5,000 players" and "six continents".
   Read `/api/meta` instead — `4a43364` is the pattern to copy.
5. **Test deps never installed**: `gen_test.py` needs `openpyxl`;
   `test_app.js` / `smoke_test_v4.js` need `jsdom`. Both pre-existing.

## Facts worth not rediscovering

- **Run the app locally like this** (never against the real `scouting.db` —
  importing `api_server` runs `init_db()`, which creates auth tables in whatever
  DB you point at, and it dirtied `scouting.db` once):
  `PERSIST_DIR=<scratch dir> PORT=8011 python api_server.py`
  `default_db_url()` copies the shipped `scouting.db` into that mount, so the
  real file is untouched. Register a throwaway user via `POST
  /api/auth/register`, then set `localStorage["scoutingAuthV1"]` =
  `{"token":…,"username":…}` to sign the browser in.
- `alert()` at `build_html.py:2958` (compare limit) will freeze the Chrome
  extension. Override `window.alert` before exercising the compare checkbox.
- Railway project `appealing-surprise` / env `production` / service
  `scouting-app`. `railway deployment list --json` shows the deployed commit.
- URL: https://scouting-app-production-2cd2.up.railway.app (`/` landing,
  `/app` app, `/api/...` REST).
- **Auth runs before query validation** — every unauthenticated probe returns
  401, including malformed params. Don't waste time on curl probes.
- `railway redeploy` re-runs the *existing* image and ignores new commits.
- `_seed_players_if_empty` only seeds an *empty* table, so roster changes never
  propagate to an already-seeded prod. `refresh_prod_players.py` is the fix.
- `export_value_band.py` needs `DATABASE_URL="sqlite:///./scouting.db"` — without
  it, it silently falls back to a throwaway sqlite under /tmp with a *different*
  roster.
- `prod_backup_20260807.json` (gitignored) holds password hashes and TOTP
  secrets. Do not commit.

## Known pre-existing failures (not caused by this work)

- `gen_test.py` won't collect: missing `openpyxl`.
- `test_app.js` / `smoke_test_v4.js` fail on "initial render shows first page of
  50 rows" because `RAW_PLAYERS` is empty in the build (roster comes from the
  authed API). Reproduced against HEAD before any changes.

## Resume

Run `/clear`, then: "continue from handoff.md".
