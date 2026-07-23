# Global Lower-Tier Scouting Prototype

A football scouting tool focused on undervalued, unrepresented players in lower-tier leagues worldwide. 5,000-player sample database (fictional data, structured like real scraped data), transparent percentile-based scoring with a dedicated goalkeeper model, and a full client-server architecture.

**Live app:** open `index.html` (GitHub Pages) — works fully standalone in offline mode.

## Quick start

| Mode | How |
|---|---|
| Offline (no install) | Open `Scouting_App_Prototype.html` (or `index.html`) in any browser |
| Client-server | `pip install -r requirements.txt && uvicorn api_server:app` → http://localhost:8000 |
| Postgres demo | `docker compose up --build` |

## What's inside

- `Scouting_App_Prototype.html` / `index.html` — the app: filters, search (name/club/country), pagination, sortable columns, adjustable scoring weights, shortlists, per-player notes, keyboard nav, CSV export, radar-chart comparison, player profiles with market-value trend chart, goalkeeper-specific stats, system-fit breakdown, score transparency panel, suggested transfer targets with contact info
- `api_server.py` — FastAPI REST backend (players, filtering/search/pagination, per-player value history, JWT auth, server-side shortlists & notes); serves the frontend at `/`
- `scoring.py` — the scoring engine (Python port, bit-exact with the frontend JS)
- `scouting.db` — SQLite database (canonical store); `players_current.json` — JSON export
- `Scouting_Model.xlsx` — the same model as a live Excel workbook
- `test_api.py` (16 pytest endpoint tests), `gen_test.py`/`test_app.js` (3-pass jsdom UI suite)
- `docker-compose.yml`, `migrate_to_postgres.py`, `DEPLOY.md` — Postgres migration + free-hosting guide
- `weekly_update.py` — weekly refresh: updates all stats, adds 10 players, rebuilds and re-tests everything

## Honest caveats

All player data is fictional/procedurally generated. Market values, trend graphs, and estimates are model output, not a real market feed. See `Scouting_App_ToS_and_Disclaimer_DRAFT.docx` (not lawyer-reviewed).
