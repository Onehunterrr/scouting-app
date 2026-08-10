# Handoff — two local commits waiting on one deploy

Everything below is verified. **Nothing is pushed.** The user's call this
session was to hold `f94bb48` and bundle it with the backend work, so the next
push deploys both commits at once.

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

Push. Decision already taken: hold `f94bb48`, bundle it with the backend work
below, deploy both together.

## SECOND UNPUSHED COMMIT — the three backend items

`_RATE_BUCKETS` eviction, conditional GET, and the request-path cost. All in
`api_server.py`; `test_api.py` is **65 passed** (44 baseline + 21 new).

### 1. Rate-bucket leak — fixed

`_RATE_BUCKETS` is keyed by client IP and nothing ever removed a key, so every
IP that ever connected held two deques for the process lifetime — and an
attacker could drive it by varying `X-Forwarded-For`.

`_sweep_rate_buckets()` runs from `_rate_ok()` every `RATE_SWEEP_SECONDS` (300)
or whenever the dict exceeds `RATE_MAX_TRACKED_IPS` (20000). It prunes
timestamps past the widest window and drops IPs left with nothing — that state
is indistinguishable from never-seen, so it changes no decision. Only the
over-cap eviction is lossy: it drops least-recently-active IPs, handing them a
fresh budget. That trade is deliberate and is the same exposure a restart
already creates.

### 2. ETag / Cache-Control on `/api/players` — added

`W/"<boot token>-<roster version>-<sha of every query param>"`, plus
`Cache-Control: private, max-age=0, must-revalidate`.

- `_roster_version` bumps in the ONE place a rebuilt roster becomes live
  (inside `get_players()`, under the lock). Don't bump it anywhere else.
- The boot token covers *code*: `scoring.py` or `LIST_OMIT_FIELDS` can change
  the body without the roster moving, and a deploy restarts the process. It
  also means **this stops helping if the service is ever scaled past one
  replica** — different boot tokens, so revalidation always misses. It never
  becomes *wrong*, just useless.
- The check runs before the filter pass, so a 304 skips the work, not just the
  bytes. Verified over real HTTP: `304`, 0 bytes.
- Staleness is bounded by `CACHE_CHECK_SECONDS`, exactly like the roster cache
  it sits on top of — a 304 can only be as stale as the cache already was.
- `invalidate_caches()` forces a version bump even when the data is unchanged,
  so an admin refresh makes every client re-download. Conservative on purpose.

**Not verified: whether Chrome revalidates on its own.** `apiFetch` uses default
fetch options (no `no-store`, no cache-buster), and `must-revalidate` is one of
the directives that permits storing an `Authorization`-bearing response, so it
should. But the Chrome extension dropped mid-check and no headless browser is
installed, so this is reasoned, not observed. **Confirm in devtools after the
deploy** — look for `304` on the second `/api/players` load. If Chrome declines
to store it, the server side is still correct and the fix is client-side
(send `If-None-Match` explicitly from `apiFetch`).

### 3. The "sort ceiling" — the handoff's diagnosis was wrong

Measured on the 5,000-row roster, `GET /api/players?pageSize=10000` was 962 ms:

| | |
|---|---|
| `jsonable_encoder` | **516 ms** |
| `json.dumps` | 164 ms |
| `_list_item` copies | 33 ms |
| summary passes | 1.8 ms |
| **sort** | **1.4 ms** |
| filter pass | 0.8 ms |

The sort was **0.15%** of the request. The real ceiling was FastAPI running
`jsonable_encoder` over every value of anything that isn't already a `Response`
— and it had nothing to convert, because the payload is already
`str/int/float/bool` end to end.

Both fixes are in:

- **`get_sorted()`** caches display order per `(weights, sort, dir)`, so the
  request does one filtering pass and no sort. Stable sort means filtering an
  ordered list == sorting the filtered one, ties included —
  `test_presorted_order_matches_sorting_the_filtered_set` pins that. It must be
  invalidated everywhere `_scored_cache` is, and `get_sorted()` resolves
  `get_scored()` **first** for the same reason `get_scored()` resolves
  `get_players()` first. Small win, but it is the correct structure.
- **`/api/players` and `/api/players/all` return `JSONResponse`**, which makes
  FastAPI skip the encoder. This is the actual win.

**962 ms -> 170 ms (5.7x)** for the full roster; `/api/players/all`, which runs
on every sign-in, **250 ms -> 33 ms**.

Output is **byte-identical**: 14 representative queries plus `/api/players/all`
compared by SHA-256, length and content-type before and after. `JSONResponse`
uses the same `json.dumps` settings FastAPI would have.

What makes the bypass safe is that the payload is JSON primitives with no
non-finite floats — audited across all 5,000 players and pinned by
`test_list_payload_is_json_primitives_only` /
`test_players_all_payload_is_json_primitives_only`, so if scoring ever emits a
`datetime`, `Decimal` or `NaN` it fails in the suite rather than as malformed
JSON in a browser. `test_encoder_probe_would_notice_a_regression` guards the
guard — it asserts a still-dict-returning endpoint *does* hit the encoder, so
the `calls == []` assertions can't pass vacuously.

**Don't "tidy" either endpoint back to returning a plain dict.**

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

## Other backend findings

1. ~~`_RATE_BUCKETS` grows unbounded~~ — **fixed above.**
2. ~~No ETag / Cache-Control on `/api/players`~~ — **fixed above.**
3. ~~Filter+sort is a full Python pass per request~~ — **fixed above, though the
   diagnosis was wrong: the cost was `jsonable_encoder`, not the sort.**
4. **Railway's edge already gzips** (`Content-Encoding: gzip` confirmed on prod).
   Adding `GZipMiddleware` would buy nothing — the 18 MB in the old handoff was
   the *decompressed* size. Don't re-propose it.
5. **Every other dict-returning endpoint still pays `jsonable_encoder`.** Only
   the two roster endpoints were converted, because those are the ones where it
   was worth the loss of FastAPI's conversion safety net. `/api/players/ids` is
   the next largest if it ever matters. Measure before converting: on small
   payloads the encoder is noise.
6. `orjson` is **not** installed. `json.dumps` is now the largest single cost of
   a full-roster response (164 ms). An `ORJSONResponse` would likely cut that
   several-fold, but it is a new dependency — not taken unilaterally.

## Still open (carried over)

1. **Rotate the Postgres password — STILL OPEN. Needs the user, not an agent.**
   Printed in plaintext by
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
