# Deploying the Scouting App (client-server mode)

The app now has two modes, and **both keep working**:

- **Offline mode** — double-click `Scouting_App_Prototype.html`. No install, no
  server. The 1000 players are embedded in the file; shortlists and notes save
  to your browser's localStorage. This is unchanged and is always the fallback.
- **Client-server mode** — run the API (`api_server.py`) and open
  `http://localhost:8000`. The same HTML file detects the API on load (a 1.5 s
  ping to `/api/meta`) and switches to server-side search/filter/sort/
  pagination plus user accounts (register/login in the sidebar) with per-user
  shortlists and notes stored in the database.

---

## 1. Run locally (simplest)

```bash
pip install -r requirements.txt
uvicorn api_server:app --host 0.0.0.0 --port 8000
# or equivalently:  python3 api_server.py
```

Open http://localhost:8000 — done. One process serves both the frontend
(`GET /`) and the REST API (`/api/...`).

By default the server copies the shipped `scouting.db` (SQLite) to
`/tmp/scouting_api/scouting.db` on first start and uses it from there. To keep
the database somewhere permanent:

```bash
DATABASE_URL=sqlite:////absolute/path/to/scouting.db uvicorn api_server:app
```

Environment variables:

| Variable       | Default                                   | Purpose                          |
|----------------|-------------------------------------------|----------------------------------|
| `DATABASE_URL` | `sqlite:////tmp/scouting_api/scouting.db` | Any SQLAlchemy URL (SQLite or Postgres) |
| `JWT_SECRET`   | dev default (change in production!)       | Signs login tokens               |
| `PORT`         | `8000`                                    | Listen port (`python3 api_server.py`) |

## 2. Run with docker-compose (Postgres included)

```bash
docker compose up --build
```

This starts `postgres:16`, migrates the SQLite data into it on boot
(`migrate_to_postgres.py`), and serves the app on http://localhost:8000 backed
by Postgres. Edit `JWT_SECRET` in `docker-compose.yml` before any real use.

Note: the migrate step in the compose file drops and recreates tables on every
boot (handy for demos). Once you have real accounts you want to keep, comment
that step out of the `api` service's `command`.

## 3. Migrate SQLite → Postgres manually

```bash
pip install -r requirements.txt   # includes psycopg2-binary
python3 migrate_to_postgres.py postgresql://user:pass@host:5432/scouting
```

The script creates all tables (players / users / shortlists / notes) on the
target with the shared SQLAlchemy metadata, copies every row, and verifies row
counts. `api_server.py` needs **zero code changes** for Postgres — it has no
SQLite-specific SQL; only `DATABASE_URL` changes.

## 4. Free hosting (Render / Railway / Fly.io) — step by step

The repo/zip contains everything a PaaS needs: `requirements.txt`,
`Dockerfile`, and the app itself.

### Render (easiest)
1. Push these files to a GitHub repo.
2. render.com → **New → Web Service** → connect the repo.
3. Runtime: **Docker** (it will pick up the `Dockerfile` automatically), or
   choose Python and set the start command to
   `uvicorn api_server:app --host 0.0.0.0 --port $PORT`.
4. (Optional, for persistent accounts) **New → PostgreSQL** (free tier), copy
   its **Internal Database URL**.
5. On the web service → **Environment**:
   - `DATABASE_URL` = the Postgres URL from step 4 (omit to run on the
     baked-in SQLite copy — fine for a demo, but the free filesystem is
     ephemeral, so accounts/notes reset on redeploys)
   - `JWT_SECRET` = a long random string (e.g. `openssl rand -hex 32`)
6. If you set `DATABASE_URL`, seed it once from the Render **Shell** tab:
   `python3 migrate_to_postgres.py "$DATABASE_URL" sqlite:///./scouting.db`
7. Deploy. Your public URL (e.g. `https://scouting-app.onrender.com`) serves
   the full app.

### Railway
1. railway.app → **New Project → Deploy from GitHub repo** (Dockerfile is
   auto-detected).
2. **Add PostgreSQL** from the plugin marketplace; Railway injects
   `DATABASE_URL` automatically (prefix it to
   `postgresql+psycopg2://` if needed — plain `postgresql://` also works).
3. Add `JWT_SECRET` under Variables, seed with the same
   `migrate_to_postgres.py` one-liner (Railway shell), done.

### Fly.io
1. `fly launch` in the project directory (uses the Dockerfile; say no to
   immediately deploying).
2. `fly postgres create` + `fly postgres attach` (sets `DATABASE_URL`), or skip
   for SQLite-in-image demos.
3. `fly secrets set JWT_SECRET=$(openssl rand -hex 32)`
4. `fly deploy`, seed via `fly ssh console` + the migrate one-liner.

## 5. SQLite keeps working

Nothing about the upgrade retires the SQLite file. `scouting.db` remains the
canonical local store: the API uses it by default, `migrate_to_postgres.py`
reads from it, and the standalone HTML file needs no database at all. Postgres
is only required when you want a hosted, multi-user deployment with
concurrent writers.

## 6. API surface (for reference)

| Method | Path                        | Auth | Notes |
|--------|-----------------------------|------|-------|
| GET    | `/`                         | –    | Serves Scouting_App_Prototype.html |
| GET    | `/api/meta`                 | –    | Counts, countries, lastUpdated |
| GET    | `/api/players`              | –    | `position, tier, country, maxAge, hasAgent, q, ids, sort, dir, page, pageSize (50), wGa/wProg/wDef/wAge` → `{items, total, page, pageSize, summary}` |
| GET    | `/api/players/ids`          | –    | Lightweight id↔name/country map |
| GET    | `/api/players/{id}`         | –    | Full player + computed scores |
| GET    | `/api/players/{id}/value`   | –    | Current value + estimated flag + deterministic 15-point history (matches the frontend chart exactly) |
| POST   | `/api/auth/register`        | –    | `{username, password}` → JWT (400 on duplicate) |
| POST   | `/api/auth/login`           | –    | → JWT (401 on bad credentials) |
| GET/PUT| `/api/me/shortlist`         | JWT  | `{playerIds: [...]}` |
| GET/PUT| `/api/me/notes/{playerId}`  | JWT  | `{text}` |

All player payloads include the computed fields (`undervaluedScore`,
`performancePct`, `marketPct`, `systemFit`, `displayMarketValue`,
`marketValueEstimated`, per-90s, percentiles) from `scoring.py`, a verified
exact port of the frontend engine.
