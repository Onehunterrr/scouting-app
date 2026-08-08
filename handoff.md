# Handoff — market-value filter shipped; row-render rewrite needs browser verification

Checkpoint at ~153k tokens. Original report: "I can't see all 5000 players, and
no players are valued between 60-100k."

## What the report actually was (resolved)

Neither was a data problem. 2,245 of the 5,000 players sit in the 60k-120k band.
Two UI limits hid them:

- **No market-value filter existed at all.** Filters were position/country/tier/
  age/agent/search only. The default sort (Undervalued Score desc) ranks *low*
  market value first by construction, so the cheap end filled every page anyone
  actually scrolled.
- **"All" was disabled in API mode**, capping the view at 100 rows/page, because
  `MAX_PAGE_SIZE` was 2000 and a larger page was rejected outright.

## Shipped and verified live (commit `2ca8da6`, deployed SUCCESS)

- `minValue`/`maxValue` on `GET /api/players`; Min/Max inputs in the filter
  panel (debounced 250ms, saved with views, cleared by Reset).
- **Both sides band on `displayMarketValue`, not the raw column.** 1,250 players
  have no value on record; banding on the raw 0 would drop every one of them out
  of every band even though the table shows them an estimate. Do not "simplify"
  this to `marketValue`.
- `MAX_PAGE_SIZE` 2000 -> 10000, mirrored client-side as `API_ALL_PAGE_SIZE`.
  Re-enables "All" in API mode and stops both CSV exports truncating at 2000.
- Live check, signed in: min 60000 / max 120000 -> **2,245 players, 92 high
  priority, 602 shown on estimate**, values spanning EUR 60,009-119,976, zero
  outside the band. Exactly matches the locally computed CSV.
- `players_60k_120k.csv` (2,245 rows) + `export_value_band.py` to regenerate any
  band. Run it with `DATABASE_URL="sqlite:///./scouting.db"` — without that it
  silently falls back to a throwaway sqlite under /tmp with a *different* roster
  (it returned 5,002 players the first time and I nearly believed it).

## UNCOMMITTED-TO-PROD WORK IN FLIGHT — read this first

`renderRows` in `build_html.py` was rewritten to fix a real performance problem:
**"All" on the unfiltered roster took 13.1s and froze the tab.** Measured split:
2.8s for the API round-trip (18 MB payload), ~10s building DOM.

The old code did `createElement` + `innerHTML` + 4 `addEventListener` +
`appendChild` per row — 20,000 listeners and 5,000 live insertions. Replaced
with `rowHtml(p, idx, newestBatch)` (pure player -> string), one
`tbody.innerHTML = rows.map(...).join("")`, and event delegation on the tbody
bound once via `bindRowEvents()` / `rowEventsBound`. Rows carry `data-idx` into
`currentRows`; `rowPlayer(el)` resolves a row back to its player.

Also fixed two malformed `<\span>` closing tags that browsers were silently
recovering from.

**State: committed locally, NOT pushed.** Build regenerates, JS parses under
`node --check`, `test_api.py` is 38/38. But the row *interactions* are not
browser-verified, and auto-deploy fires on push, so it must not go out untested.

### Next step — verify these four things, then push

Open the app (local file or a deploy) and confirm:
1. Clicking a row opens the player modal.
2. The star toggles the shortlist and the row re-renders starred.
3. The compare checkbox adds to the compare bar, does NOT open the modal, and
   still enforces `COMPARE_MAX`.
4. Keyboard nav (j/k or arrows) still focuses rows — `setKbFocus` indexes
   `document.querySelectorAll("#table-body tr")` by DOM position, which must
   stay in step with `currentRows`.

Then re-measure "All" unfiltered. Expect well under 13s; if the DOM build is
still slow, the remaining fix is row virtualization, not more micro-tuning.

## Still open (user asked for "all changes"; these are the rest)

1. **500/page option** — not added. Was offered as the cheap alternative to the
   render rewrite; the rewrite may make it unnecessary. Page-size select is at
   the `<option value="all">` block in `build_html.py`.
2. **Landing page prose** still hardcodes "5,000 players" and "six continents".
   Same drift risk that already bit the country count — fix the same way, read
   `/api/meta` (`4a43364` is the pattern to copy).
3. **Test deps never installed**: `gen_test.py` needs `openpyxl`;
   `test_app.js` / `smoke_test_v4.js` need `jsdom`. Both pre-existing.

## Two things I could NOT do — they need you, not an agent

- **Commit `2ca8da6`'s subject line is a stray `@`.** I used PowerShell
  here-string syntax in a bash shell. Amending worked locally but the
  force-push was rejected: `main` is a protected branch. Fixing it means
  temporarily lifting branch protection — your call. Body of the message is
  intact; only the summary line is wrong. Local was reset back in sync.
- **Rotate the Postgres password — STILL OPEN** (carried over, pre-dates this
  work). It was printed in plaintext by `railway variables --service postgres`
  in an earlier session. Do it attended: Postgres service -> Variables ->
  regenerate `POSTGRES_PASSWORD` -> let Postgres redeploy -> redeploy
  `scouting-app`. `DATABASE_URL` is a `${{Postgres.DATABASE_URL}}` reference so
  it updates itself. Verify: `/api/meta` returns 200 with
  `"backend":"postgresql"`. Not done from an agent session because if the two
  services redeploy out of order the site is down until it settles.

## Facts worth not rediscovering

- Railway project `appealing-surprise` / env `production` / service
  `scouting-app`. `railway deployment list --json` shows the deployed commit —
  that is how to confirm what is actually live.
- URL: https://scouting-app-production-2cd2.up.railway.app (`/` landing,
  `/app` app, `/api/...` REST). `/api/meta` answers in ~0.5s.
- **Auth runs before query validation.** Every unauthenticated probe returns
  401, including malformed params — so you cannot probe whether a query param
  exists without signing in. Don't waste time on curl probes.
- `railway redeploy` re-runs the *existing* image and ignores new commits. Push,
  or `railway up`.
- `_seed_players_if_empty` only seeds an *empty* table, so roster changes never
  propagate to an already-seeded prod. `refresh_prod_players.py` is the fix.
- Importing `api_server` runs `init_db()`, which creates auth tables in whatever
  DB you point at. It dirtied `scouting.db` once; that was reverted.
- `prod_backup_20260807.json` (gitignored) holds password hashes and TOTP
  secrets. Do not commit.

## Known pre-existing failures (not caused by this work)

- `gen_test.py` won't collect: missing `openpyxl`.
- `test_app.js` / `smoke_test_v4.js` fail on "initial render shows first page of
  50 rows" because `RAW_PLAYERS` is empty in the build (roster comes from the
  authed API). Reproduced against HEAD before any changes.
- `test_api.py`: 38 passed (36 baseline + 2 new for the value band and the
  raised page cap).

## Resume

Run `/clear`, then: "continue from handoff.md".
