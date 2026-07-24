"""
api_server.py -- FastAPI REST backend for the Global Lower-Tier Scouting app.

One process = the full demo:
    uvicorn api_server:app --host 0.0.0.0 --port 8000
    (or: python3 api_server.py)
then open http://localhost:8000 -- GET / serves Scouting_App_Prototype.html,
which detects the API and switches itself into client-server mode. Opened as a
plain file (double-click), the same HTML falls back to its embedded data.

Database: SQLAlchemy engine from env DATABASE_URL. Defaults to a SQLite copy in
/tmp/scouting_api/scouting.db (seeded from ./scouting.db on first run). Set
DATABASE_URL=postgresql://... and the same code runs on Postgres unchanged --
no sqlite-only SQL in any query path (all queries go through SQLAlchemy Core;
scoring happens in Python via scoring.py, the exact port of the frontend
engine).

Auth: JWT (HS256, PyJWT), bcrypt password hashes (passlib). Secret from env
JWT_SECRET (dev default provided -- change it in production).
"""

import os
import shutil
import datetime
import math

import jwt
from passlib.hash import bcrypt
from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, select, insert, delete, update, func

import scoring
from db_tables import metadata, players as players_t, users as users_t, \
    shortlists as shortlists_t, notes as notes_t, row_to_player

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SQLITE_DIR = "/tmp/scouting_api"
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_SQLITE_DIR}/scouting.db"
SEED_DB = os.path.join(BASE_DIR, "scouting.db")
HTML_FILE = os.path.join(BASE_DIR, "Scouting_App_Prototype.html")

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me-in-prod")
JWT_ALGO = "HS256"
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "168"))

MAX_PAGE_SIZE = 2000

engine = None
_raw_players = None          # list of camelCase dicts straight from the DB
_scored_cache = {}           # weights tuple -> scored list


def _seed_sqlite_if_needed(url):
    """For the default sqlite URL, copy the shipped scouting.db into place."""
    if not url.startswith("sqlite:///"):
        return
    path = url[len("sqlite:///"):]
    if not path or os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(SEED_DB):
        shutil.copy(SEED_DB, path)


def _seed_players_if_empty(eng):
    """Populate the players table from the shipped scouting.db when the target
    database has no players yet. This lets a fresh non-SQLite database (e.g. a
    newly provisioned Postgres) come up fully populated on first boot.

    It only ever touches the players table, and only when it is empty, so it is
    safe to run on every startup: existing players are left alone and the
    users / shortlists / notes tables (i.e. real accounts) are never modified.
    """
    if not os.path.exists(SEED_DB):
        return
    # Nothing to do if this engine already points at the shipped/seeded SQLite.
    if str(eng.url) == f"sqlite:///{SEED_DB}":
        return
    try:
        with eng.connect() as conn:
            existing = conn.execute(
                select(func.count()).select_from(players_t)).scalar_one()
        if existing:
            return
        seed_eng = create_engine(f"sqlite:///{SEED_DB}", future=True)
        with seed_eng.connect() as sconn:
            rows = [dict(m) for m in
                    sconn.execute(select(players_t)).mappings().all()]
        if not rows:
            return
        with eng.begin() as conn:
            for i in range(0, len(rows), 200):
                conn.execute(insert(players_t), rows[i:i + 200])
        # Postgres tracks its own PK sequence -- resync it so future INSERTs
        # (e.g. weekly_update) don't collide with the copied ids.
        if eng.url.get_backend_name().startswith("postgres"):
            from sqlalchemy import text
            with eng.begin() as conn:
                conn.execute(text(
                    "SELECT setval(pg_get_serial_sequence('players', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM players), 1))"))
        print(f"[init_db] seeded {len(rows)} players into empty database")
    except Exception as exc:  # never let seeding crash startup
        print(f"[init_db] player seeding skipped: {exc}")


def init_db(url=None):
    """(Re-)initialise the engine + player cache. Tests call this with their
    own URL; normal startup uses env DATABASE_URL or the sqlite default."""
    global engine, _raw_players, _scored_cache
    url = url or os.environ.get("DATABASE_URL", DEFAULT_DB_URL)
    _seed_sqlite_if_needed(url)
    engine = create_engine(url, future=True)
    metadata.create_all(engine, checkfirst=True)
    _seed_players_if_empty(engine)
    _raw_players = None
    _scored_cache = {}
    return engine


def get_players():
    """All players from the DB as camelCase dicts, cached in memory."""
    global _raw_players
    if _raw_players is None:
        with engine.connect() as conn:
            rows = conn.execute(select(players_t).order_by(players_t.c.id)).mappings().all()
        _raw_players = [row_to_player(r) for r in rows]
    return _raw_players


def get_scored(weights=None):
    w = weights or scoring.DEFAULT_WEIGHTS
    key = (w["ga"], w["prog"], w["def"], w["age"])
    if key not in _scored_cache:
        # keep the cache small: default weights + last custom set
        if len(_scored_cache) > 4:
            _scored_cache.clear()
        _scored_cache[key] = scoring.compute_scores(get_players(), w)
    return _scored_cache[key]


init_db()

app = FastAPI(title="Scouting API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
class Credentials(BaseModel):
    username: str
    password: str


class ShortlistBody(BaseModel):
    playerIds: list[int]


class NoteBody(BaseModel):
    text: str


def make_token(user_id, username):
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def current_user(authorization: str = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization[len("Bearer "):].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"id": int(payload["sub"]), "username": payload["username"]}


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------
def _parse_weights(wGa, wProg, wDef, wAge):
    if wGa is None and wProg is None and wDef is None and wAge is None:
        return None
    d = scoring.DEFAULT_WEIGHTS
    return {"ga": wGa if wGa is not None else d["ga"],
            "prog": wProg if wProg is not None else d["prog"],
            "def": wDef if wDef is not None else d["def"],
            "age": wAge if wAge is not None else d["age"]}


@app.get("/api/players")
def list_players(
    position: str | None = None,
    tier: int | None = None,
    country: str | None = None,
    maxAge: int | None = None,
    hasAgent: str | None = None,
    q: str | None = None,
    ids: str | None = None,
    sort: str = "undervaluedScore",
    dir: str = "desc",
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    wGa: int | None = Query(default=None, ge=0, le=100),
    wProg: int | None = Query(default=None, ge=0, le=100),
    wDef: int | None = Query(default=None, ge=0, le=100),
    wAge: int | None = Query(default=None, ge=0, le=100),
):
    scored = get_scored(_parse_weights(wGa, wProg, wDef, wAge))

    agent_vals = set(v.strip() for v in hasAgent.split(",")) if hasAgent else None
    id_set = None
    if ids is not None:
        try:
            id_set = set(int(v) for v in ids.split(",") if v.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="ids must be a comma-separated list of integers")
    ql = q.strip().lower() if q else None

    rows = []
    for p in scored:
        if position and p["position"] != position:
            continue
        if tier is not None and p["tier"] != tier:
            continue
        if country and p["country"] != country:
            continue
        if maxAge is not None and p["age"] > maxAge:
            continue
        if agent_vals is not None and p["hasAgent"] not in agent_vals:
            continue
        if id_set is not None and p.get("id") not in id_set:
            continue
        if ql and not (ql in p["name"].lower() or ql in p["club"].lower()
                       or ql in p["country"].lower()):
            continue
        rows.append(p)

    # sort exactly like the frontend: case-insensitive for strings
    if rows and sort not in rows[0]:
        raise HTTPException(status_code=400, detail=f"Unknown sort key: {sort}")
    reverse = dir != "asc"

    def sort_key(p):
        v = p.get(sort)
        if isinstance(v, str):
            return v.lower()
        if v is None:
            return -math.inf
        if isinstance(v, bool):
            return int(v)
        return v

    rows.sort(key=sort_key, reverse=reverse)

    total = len(rows)
    start = (page - 1) * pageSize
    items = rows[start:start + pageSize]

    # summary over the WHOLE filtered set (the frontend stat chips)
    summary = {
        "highPriority": sum(1 for p in rows if p["flag"].startswith("High Priority")),
        "unrepresented": sum(1 for p in rows if p["flag"].endswith("Unrepresented")),
        "avgAge": round(sum(p["age"] for p in rows) / total, 1) if total else 0,
    }

    return {"items": items, "total": total, "page": page, "pageSize": pageSize,
            "summary": summary}


@app.get("/api/players/ids")
def list_player_ids():
    """Lightweight id <-> (name, country) map for shortlist key syncing."""
    return {"players": [{"id": p["id"], "name": p["name"], "country": p["country"]}
                        for p in get_players()]}


@app.get("/api/players/{player_id}")
def get_player(player_id: int):
    for p in get_scored():
        if p.get("id") == player_id:
            return p
    raise HTTPException(status_code=404, detail="Player not found")


@app.get("/api/players/{player_id}/value")
def get_player_value(player_id: int):
    for p in get_scored():
        if p.get("id") == player_id:
            current = p["displayMarketValue"]
            history = scoring.market_history(p, current)
            return {
                "id": player_id,
                "name": p["name"],
                "current": current,
                "knownMarketValue": p["marketValue"],
                "estimated": p["marketValueEstimated"],
                "estimatedMarketValue": p["estimatedMarketValue"],
                "history": history,
                "points": len(history),
            }
    raise HTTPException(status_code=404, detail="Player not found")


@app.get("/api/meta")
def get_meta():
    ps = get_players()
    countries = sorted(set(p["country"] for p in ps))
    last_updated = max((p["lastUpdated"] or "" for p in ps), default="")
    newest_batch = max((p["dateAdded"] or "" for p in ps), default="")
    return {
        "players": len(ps),
        "countries": countries,
        "countryCount": len(countries),
        "lastUpdated": last_updated,
        "newestBatch": newest_batch,
        "newThisWeek": sum(1 for p in ps if p["dateAdded"] == newest_batch),
        "backend": engine.url.get_backend_name(),
    }


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.post("/api/auth/register")
def register(creds: Credentials):
    username = creds.username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    if len(creds.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    with engine.begin() as conn:
        existing = conn.execute(
            select(users_t.c.id).where(func.lower(users_t.c.username) == username.lower())
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Username already taken")
        result = conn.execute(insert(users_t).values(
            username=username,
            password_hash=bcrypt.hash(creds.password),
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        ))
        user_id = result.inserted_primary_key[0]
    return {"token": make_token(user_id, username), "username": username}


@app.post("/api/auth/login")
def login(creds: Credentials):
    with engine.connect() as conn:
        row = conn.execute(
            select(users_t).where(func.lower(users_t.c.username) == creds.username.strip().lower())
        ).mappings().first()
    if not row or not bcrypt.verify(creds.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"token": make_token(row["id"], row["username"]), "username": row["username"]}


# ---------------------------------------------------------------------------
# Per-user shortlist + notes
# ---------------------------------------------------------------------------
@app.get("/api/me/shortlist")
def get_shortlist(user=Depends(current_user)):
    with engine.connect() as conn:
        rows = conn.execute(
            select(shortlists_t.c.player_id).where(shortlists_t.c.user_id == user["id"])
        ).all()
    return {"playerIds": sorted(r[0] for r in rows)}


@app.put("/api/me/shortlist")
def put_shortlist(body: ShortlistBody, user=Depends(current_user)):
    valid_ids = {p["id"] for p in get_players()}
    ids = sorted(set(body.playerIds) & valid_ids)
    with engine.begin() as conn:
        conn.execute(delete(shortlists_t).where(shortlists_t.c.user_id == user["id"]))
        if ids:
            conn.execute(insert(shortlists_t),
                         [{"user_id": user["id"], "player_id": pid} for pid in ids])
    return {"playerIds": ids}


@app.get("/api/me/notes/{player_id}")
def get_note(player_id: int, user=Depends(current_user)):
    with engine.connect() as conn:
        row = conn.execute(
            select(notes_t.c.text).where(
                (notes_t.c.user_id == user["id"]) & (notes_t.c.player_id == player_id))
        ).first()
    return {"playerId": player_id, "text": row[0] if row else ""}


@app.put("/api/me/notes/{player_id}")
def put_note(player_id: int, body: NoteBody, user=Depends(current_user)):
    if player_id not in {p["id"] for p in get_players()}:
        raise HTTPException(status_code=404, detail="Player not found")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with engine.begin() as conn:
        existing = conn.execute(
            select(notes_t.c.player_id).where(
                (notes_t.c.user_id == user["id"]) & (notes_t.c.player_id == player_id))
        ).first()
        if existing:
            conn.execute(update(notes_t).where(
                (notes_t.c.user_id == user["id"]) & (notes_t.c.player_id == player_id)
            ).values(text=body.text, updated_at=now))
        else:
            conn.execute(insert(notes_t).values(
                user_id=user["id"], player_id=player_id, text=body.text, updated_at=now))
    return {"playerId": player_id, "text": body.text}


# ---------------------------------------------------------------------------
# Static frontend -- one process serves the whole demo
# ---------------------------------------------------------------------------
@app.get("/")
def serve_frontend():
    if os.path.exists(HTML_FILE):
        return FileResponse(HTML_FILE, media_type="text/html")
    raise HTTPException(status_code=404, detail="Scouting_App_Prototype.html not found next to api_server.py")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
