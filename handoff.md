# Handoff — production data not rendering (Railway)

Checkpoint at ~151k tokens. Original report: "my data on the railway website is
not being presented."

## Deployment facts (discovered, not assumed)

- Railway project `appealing-surprise` / env `production`, service `scouting-app`
- URL: https://scouting-app-production-2cd2.up.railway.app
  (`/` = landing.html, `/app` = the app, `/api/...` = REST)
- **Already on Postgres**, not SQLite: `DATABASE_URL` is set to the internal
  `postgres.railway.internal` URL. `JWT_SECRET` is set to a strong random value.
- Railway CLI is installed and authenticated as huntercrossman7@gmail.com.
  `railway variables --service postgres --json` yields `DATABASE_PUBLIC_URL`
  (sakura.proxy.rlwy.net), which is reachable from this machine.
- Auto-deploy on push to `main` is ON. `railway redeploy` re-runs the *existing*
  image and does NOT pick up new commits — use a push, or `railway up`.

## Two real bugs, both fixed

**1. Production roster was stale (fixed, verified).** Production held the old
32-country roster; local `scouting.db` has 38. `_seed_players_if_empty` only
seeds an *empty* table, so every roster change since Postgres was first seeded
was a silent no-op. This is structural — it will recur on the next roster change.
Fixed by `refresh_prod_players.py` (committed): replaces only `players`, remaps
shortlist + ledger player ids through (name, country), one transaction, aborts on
bad post-write counts. Ran it: 38 countries live, 12 users / 9 shortlists intact,
9/9 and 5/5 references remapped, none dropped.

**2. Stale sessions render an empty table (fixed, NOT yet verified in prod).**
Any token the server rejects — expired past TOKEN_TTL_HOURS=168, or signed with a
since-rotated JWT_SECRET — left `authToken` truthy, so the app believed it was
signed in, 401'd on every call, and showed an empty table under "No players match
the current filters." Confirmed live in the browser: 7 requests on `/app`, 6 were
401. Setting JWT_SECRET at some point would have invalidated everyone's token at
once — the most likely trigger for the original report.

## Commits (all pushed to origin/main)

- `d9e81cd` persistence + cold-start: `default_db_url()` (PERSIST_DIR →
  RAILWAY_VOLUME_MOUNT_PATH → /tmp), boot-time cache warmup (SKIP_WARMUP=1 opts
  out), detectApi 15s/3 retries when served over http(s) vs 1.5s for file://
- `8a25712` track platform docs + sample call list
- `c2605ee` clearStaleAuth() on 401 + fix the finally block that overwrote every
  empty-state message; add refresh_prod_players.py

## Next steps

1. **Verify `c2605ee` in production.** It pushed at checkpoint time; confirm the
   deploy landed, then load `/app` with a stale token in localStorage and check
   it says "Your session expired" rather than the filters message. The browser
   profile used for testing already has a stale token, so just reload `/app`.
2. **landing.html hardcodes "32 countries"** in three places (lines ~316, ~424,
   ~446) — now wrong, roster is 38. Not yet fixed. Consider reading
   `/api/meta.countryCount` instead of hardcoding, so it can't drift again.
3. **Rotate the Postgres password.** `railway variables --service postgres`
   printed it in plaintext during this session.
4. `prod_backup_20260807.json` (4.2 MB, repo root, gitignored) is the pre-refresh
   dump of all six tables — restore source if anything looks wrong. Contains
   password hashes and TOTP secrets; do not commit.

## Things ruled out (don't re-investigate)

Volume/`/tmp` wipe (never applied — it's on Postgres), plan upgrade, container
size, player count, custom domain, cold starts. `/api/meta` answers in 0.37s.
Acquirability/deal scores are computed in Python, not stored, so they were never
stale.

## Known pre-existing failures (not caused by this work)

- `gen_test.py` won't collect: missing `openpyxl`
- `test_app.js` / `smoke_test_v4.js` fail on "initial render shows first page of
  50 rows" because `RAW_PLAYERS` is empty in the build (roster comes from the
  authed API). Identical failure reproduced against HEAD before any changes.
  They need `jsdom`, which is not installed in the repo.
- `test_api.py`: 36 passed throughout.

## Resume

Run `/clear`, then: "continue from handoff.md".
