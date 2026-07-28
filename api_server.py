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
import io
import json
import base64
import secrets
import shutil
import datetime
import math

import jwt
import pyotp
import qrcode
import qrcode.image.svg
from passlib.hash import bcrypt
from fastapi import FastAPI, HTTPException, Depends, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, insert, delete, update, func

import scoring
from db_tables import metadata, players as players_t, users as users_t, \
    shortlists as shortlists_t, notes as notes_t, row_to_player, \
    ledger_entries as ledger_t, watchlists as watchlists_t

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SQLITE_DIR = "/tmp/scouting_api"
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_SQLITE_DIR}/scouting.db"
SEED_DB = os.path.join(BASE_DIR, "scouting.db")
HTML_FILE = os.path.join(BASE_DIR, "Scouting_App_Prototype.html")

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me-in-prod")
JWT_ALGO = "HS256"
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "168"))
MFA_CHALLENGE_TTL_MINUTES = 5
MFA_ISSUER = os.environ.get("MFA_ISSUER", "ScoutEdge")

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


def _ensure_user_columns(eng):
    """Add columns introduced after the users table already existed. create_all
    only creates missing tables, not missing columns, so ALTER them in. Both
    SQLite and Postgres accept 'ADD COLUMN'; we swallow the 'already exists'
    error so this is safe to run on every startup."""
    from sqlalchemy import text
    for coldef in ("is_pro INTEGER DEFAULT 0", "role TEXT DEFAULT 'user'", "stripe_customer_id TEXT",
                   "totp_secret TEXT", "totp_enabled INTEGER DEFAULT 0", "backup_codes TEXT"):
        try:
            with eng.begin() as conn:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {coldef}"))
        except Exception:
            pass  # column already present


def init_db(url=None):
    """(Re-)initialise the engine + player cache. Tests call this with their
    own URL; normal startup uses env DATABASE_URL or the sqlite default."""
    global engine, _raw_players, _scored_cache
    url = url or os.environ.get("DATABASE_URL", DEFAULT_DB_URL)
    _seed_sqlite_if_needed(url)
    engine = create_engine(url, future=True)
    metadata.create_all(engine, checkfirst=True)
    _ensure_user_columns(engine)
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
# Lightweight in-memory rate limiting (best-effort; single-process friendly).
# Auth endpoints get a tight per-IP budget to blunt credential brute-forcing;
# the rest of the API gets a generous cap to absorb bursts without harming a
# normal session.
# ---------------------------------------------------------------------------
import time
from collections import defaultdict, deque

_RATE_BUCKETS = defaultdict(lambda: defaultdict(deque))
RATE_LIMITS = {"auth": (20, 60), "api": (300, 60)}  # (max_requests, window_seconds)
RATE_LIMIT_DISABLED = os.environ.get("RATE_LIMIT_DISABLED") == "1"  # test/dev escape hatch


def _client_ip(request):
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _rate_ok(ip, bucket):
    limit, window = RATE_LIMITS[bucket]
    now = time.time()
    dq = _RATE_BUCKETS[ip][bucket]
    while dq and now - dq[0] > window:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True


@app.middleware("http")
async def rate_limit(request, call_next):
    path = request.url.path
    bucket = "auth" if path.startswith("/api/auth/") else ("api" if path.startswith("/api/") else None)
    if bucket and not RATE_LIMIT_DISABLED and not _rate_ok(_client_ip(request), bucket):
        return JSONResponse(status_code=429, content={"detail": "Too many requests — please slow down."})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TotpCode(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class TotpDisable(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=4, max_length=32)


class MfaLogin(BaseModel):
    mfaToken: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=4, max_length=32)


class ShortlistBody(BaseModel):
    playerIds: list[int] = Field(max_length=1000)


class NoteBody(BaseModel):
    text: str = Field(max_length=5000)


class LedgerBody(BaseModel):
    playerIds: list[int] = Field(max_length=100)


class OutcomeBody(BaseModel):
    outcome: str = Field(pattern="^(pending|signed|rose|available|missed)$")


class WatchlistBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    filters: dict = Field(default_factory=dict)
    share: bool = False


class AdminPlayerBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country: str = Field(min_length=1, max_length=60)
    position: str = Field(pattern="^(GK|DF|MF|FW)$")
    tier: int = Field(ge=2, le=4)
    club: str = Field(default="", max_length=120)
    age: int = Field(ge=15, le=40)
    minutes: int = Field(default=900, ge=0, le=10000)
    goals: int = Field(default=0, ge=0, le=200)
    assists: int = Field(default=0, ge=0, le=200)
    progPasses: int = Field(default=0, ge=0, le=5000)
    progCarries: int = Field(default=0, ge=0, le=5000)
    tklInt: int = Field(default=0, ge=0, le=5000)
    marketValue: int = Field(default=0, ge=0, le=500000000)
    hasAgent: str = Field(default="Unknown", pattern="^(Yes|No|Unknown)$")
    contractExpires: int = Field(default=2027, ge=2024, le=2040)
    clubContactEmail: str = Field(default="", max_length=160)


ADMIN_USERNAMES = {u.strip().lower() for u in os.environ.get("ADMIN_USERNAMES", "").split(",") if u.strip()}


def make_token(user_id, username):
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def make_mfa_token(user_id, username):
    """Short-lived challenge token issued after a correct password when 2FA is
    on. scope='mfa' means current_user() refuses it, so it unlocks nothing but
    the /api/auth/login/2fa step."""
    payload = {
        "sub": str(user_id),
        "username": username,
        "scope": "mfa",
        "exp": datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(minutes=MFA_CHALLENGE_TTL_MINUTES),
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
    if payload.get("scope") == "mfa":
        raise HTTPException(status_code=401, detail="Two-factor verification required")
    uid, uname = int(payload["sub"]), payload["username"]
    is_pro, role = 0, "user"
    try:
        with engine.connect() as conn:
            row = conn.execute(
                select(users_t.c.is_pro, users_t.c.role).where(users_t.c.id == uid)
            ).first()
            if row:
                is_pro = row[0] or 0
                role = row[1] or "user"
    except Exception:
        pass
    is_admin = role == "admin" or uname.lower() in ADMIN_USERNAMES
    return {"id": uid, "username": uname, "is_pro": bool(is_pro), "is_admin": is_admin}


def require_admin(user=Depends(current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


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
    user=Depends(current_user),
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
def list_player_ids(user=Depends(current_user)):
    """Lightweight id <-> (name, country) map for shortlist key syncing."""
    return {"players": [{"id": p["id"], "name": p["name"], "country": p["country"]}
                        for p in get_players()]}


@app.get("/api/players/all")
def list_all_players(user=Depends(current_user)):
    """Full player dataset (authed) so the client loads it AFTER sign-in rather
    than having it embedded in the served HTML. Closes the view-source leak:
    no player data is present in the page until a user is authenticated."""
    return {"players": get_players()}


@app.get("/api/players/{player_id}")
def get_player(player_id: int, user=Depends(current_user)):
    for p in get_scored():
        if p.get("id") == player_id:
            return p
    raise HTTPException(status_code=404, detail="Player not found")


@app.get("/api/players/{player_id}/value")
def get_player_value(player_id: int, user=Depends(current_user)):
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


@app.get("/api/stats/summary")
def roster_summary(country: str | None = None, user=Depends(current_user)):
    """Aggregate stats for the (optionally country-filtered) roster: size, average
    age, average known market value, and the share of represented players."""
    players = get_scored()
    if country:
        players = [p for p in players if p["country"] == country]
    known = [p["marketValue"] for p in players if p.get("marketValue")]
    return {
        "count": len(players),
        "avgAge": round(sum(p["age"] for p in players) / len(players), 1) if players else 0,
        "avgKnownMarketValue": round(sum(known) / len(known)) if known else 0,
        "withAgentPct": round(100 * sum(1 for p in players if p["hasAgent"] == "Yes") / len(players)) if players else 0,
    }


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
    # 2FA on: the password alone yields no session, only a 5-minute challenge.
    if row.get("totp_enabled") and row.get("totp_secret"):
        return {"mfaRequired": True,
                "mfaToken": make_mfa_token(row["id"], row["username"]),
                "username": row["username"]}
    return {"token": make_token(row["id"], row["username"]), "username": row["username"]}


# ---------------------------------------------------------------------------
# Two-factor auth (TOTP -- Google Authenticator, Authy, 1Password, ...)
# ---------------------------------------------------------------------------
def _load_user(uid):
    with engine.connect() as conn:
        return conn.execute(select(users_t).where(users_t.c.id == uid)).mappings().first()


def _totp_ok(secret, code):
    """valid_window=1 accepts the adjacent 30s step, covering clock drift."""
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(str(code).strip().replace(" ", ""), valid_window=1)


def _new_backup_codes(n=10):
    """Plaintext codes go to the user once; only bcrypt hashes are stored."""
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return codes, json.dumps([bcrypt.hash(c) for c in codes])


def _consume_backup_code(row, code):
    """True if `code` matches an unused backup code; burns it on success."""
    try:
        hashes = json.loads(row.get("backup_codes") or "[]")
    except ValueError:
        return False
    cleaned = str(code).strip().lower()
    for h in hashes:
        try:
            matched = bcrypt.verify(cleaned, h)
        except ValueError:
            matched = False
        if matched:
            hashes.remove(h)
            with engine.begin() as conn:
                conn.execute(update(users_t).where(users_t.c.id == row["id"])
                             .values(backup_codes=json.dumps(hashes)))
            return True
    return False


@app.post("/api/auth/login/2fa")
def login_2fa(body: MfaLogin):
    try:
        payload = jwt.decode(body.mfaToken, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Challenge expired -- sign in again")
    if payload.get("scope") != "mfa":
        raise HTTPException(status_code=401, detail="Invalid challenge token")
    row = _load_user(int(payload["sub"]))
    if not row or not row.get("totp_enabled"):
        raise HTTPException(status_code=401, detail="Two-factor is not enabled for this account")
    if not (_totp_ok(row.get("totp_secret"), body.code) or _consume_backup_code(row, body.code)):
        raise HTTPException(status_code=401, detail="Incorrect code")
    return {"token": make_token(row["id"], row["username"]), "username": row["username"]}


@app.post("/api/me/2fa/setup")
def mfa_setup(user=Depends(current_user)):
    """Mint a secret and return its QR. Stored but inert until /enable."""
    row = _load_user(user["id"])
    if row and row.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="Two-factor is already enabled")
    secret = pyotp.random_base32()
    with engine.begin() as conn:
        conn.execute(update(users_t).where(users_t.c.id == user["id"]).values(totp_secret=secret))
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["username"], issuer_name=MFA_ISSUER)
    buf = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).save(buf)
    qr_svg = base64.b64encode(buf.getvalue()).decode()
    return {"secret": secret, "otpauthUri": uri,
            "qrDataUri": "data:image/svg+xml;base64," + qr_svg}


@app.post("/api/me/2fa/enable")
def mfa_enable(body: TotpCode, user=Depends(current_user)):
    row = _load_user(user["id"])
    if not row or not row.get("totp_secret"):
        raise HTTPException(status_code=400, detail="Start setup first")
    if row.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="Two-factor is already enabled")
    if not _totp_ok(row["totp_secret"], body.code):
        raise HTTPException(status_code=400, detail="That code didn't match -- check the time on your phone and try again")
    codes, hashed = _new_backup_codes()
    with engine.begin() as conn:
        conn.execute(update(users_t).where(users_t.c.id == user["id"])
                     .values(totp_enabled=1, backup_codes=hashed))
    return {"enabled": True, "backupCodes": codes}


@app.post("/api/me/2fa/disable")
def mfa_disable(body: TotpDisable, user=Depends(current_user)):
    """Password AND a live code -- a stolen session alone can't strip 2FA."""
    row = _load_user(user["id"])
    if not row or not row.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="Two-factor is not enabled")
    if not bcrypt.verify(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect password")
    if not (_totp_ok(row.get("totp_secret"), body.code) or _consume_backup_code(row, body.code)):
        raise HTTPException(status_code=401, detail="Incorrect code")
    with engine.begin() as conn:
        conn.execute(update(users_t).where(users_t.c.id == user["id"])
                     .values(totp_enabled=0, totp_secret=None, backup_codes=None))
    return {"enabled": False}


@app.post("/api/me/2fa/backup-codes")
def mfa_regen_backup_codes(body: TotpCode, user=Depends(current_user)):
    row = _load_user(user["id"])
    if not row or not row.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="Two-factor is not enabled")
    if not _totp_ok(row.get("totp_secret"), body.code):
        raise HTTPException(status_code=401, detail="Incorrect code")
    codes, hashed = _new_backup_codes()
    with engine.begin() as conn:
        conn.execute(update(users_t).where(users_t.c.id == user["id"]).values(backup_codes=hashed))
    return {"backupCodes": codes}


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
# Account + billing (Pro tier)
# ---------------------------------------------------------------------------
import json as _json
import secrets

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_ENABLED = bool(STRIPE_SECRET_KEY and STRIPE_PRICE_ID)


def require_pro(user=Depends(current_user)):
    if not user["is_pro"]:
        raise HTTPException(status_code=402, detail="This is a Pro feature — upgrade to unlock.")
    return user


def _set_pro(uid, val):
    with engine.begin() as conn:
        conn.execute(update(users_t).where(users_t.c.id == uid).values(is_pro=1 if val else 0))


@app.get("/api/me")
def get_me(user=Depends(current_user)):
    row = _load_user(user["id"])
    return {"username": user["username"], "isPro": user["is_pro"], "isAdmin": user["is_admin"],
            "billingEnabled": STRIPE_ENABLED,
            "mfaEnabled": bool(row and row.get("totp_enabled"))}


@app.post("/api/billing/checkout")
def billing_checkout(user=Depends(current_user)):
    if not STRIPE_ENABLED:
        _set_pro(user["id"], True)   # demo mode: no Stripe keys -> unlock immediately
        return {"mode": "demo", "isPro": True}
    try:
        import stripe
        stripe.api_key = STRIPE_SECRET_KEY
        app_url = os.environ.get("APP_URL", "")
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            client_reference_id=str(user["id"]),
            success_url=app_url + "/app?upgraded=1",
            cancel_url=app_url + "/app",
        )
        return {"mode": "stripe", "url": session.url}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Stripe error: {e}")


@app.post("/api/billing/cancel")
def billing_cancel(user=Depends(current_user)):
    _set_pro(user["id"], False)
    return {"isPro": False}


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    """Stripe -> us. A user only becomes Pro here, after Stripe confirms the
    payment (checkout.session.completed), and reverts to free when the
    subscription is cancelled. This is what makes the paywall real."""
    if not STRIPE_ENABLED:
        raise HTTPException(status_code=404, detail="Billing not configured")
    import stripe
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    try:
        if secret:
            event = stripe.Webhook.construct_event(payload, sig, secret)
        else:
            event = _json.loads(payload)   # dev only: no signature verification
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid webhook: {e}")
    etype = event["type"]
    obj = event["data"]["object"]
    if etype == "checkout.session.completed":
        uid = obj.get("client_reference_id")
        cust = obj.get("customer")
        if uid:
            with engine.begin() as conn:
                conn.execute(update(users_t).where(users_t.c.id == int(uid))
                             .values(is_pro=1, stripe_customer_id=cust))
    elif etype == "customer.subscription.deleted":
        cust = obj.get("customer")
        if cust:
            with engine.begin() as conn:
                conn.execute(update(users_t).where(users_t.c.stripe_customer_id == cust).values(is_pro=0))
    return {"received": True}


# ---------------------------------------------------------------------------
# Scout Ledger -- dated prediction snapshots + outcomes (the compounding moat)
# ---------------------------------------------------------------------------
@app.get("/api/me/ledger")
def get_ledger(user=Depends(current_user)):
    with engine.connect() as conn:
        rows = conn.execute(
            select(ledger_t).where(ledger_t.c.user_id == user["id"]).order_by(ledger_t.c.id.desc())
        ).mappings().all()
    return {"entries": [dict(r) for r in rows]}


@app.post("/api/me/ledger")
def add_ledger(body: LedgerBody, user=Depends(require_pro)):
    scored = {p["id"]: p for p in get_scored()}
    today = datetime.date.today().isoformat()
    added = []
    with engine.begin() as conn:
        pending = {r[0] for r in conn.execute(
            select(ledger_t.c.player_id).where(
                (ledger_t.c.user_id == user["id"]) & (ledger_t.c.outcome == "pending"))
        ).all()}
        for pid in body.playerIds:
            p = scored.get(pid)
            if not p or pid in pending:
                continue
            conn.execute(insert(ledger_t).values(
                user_id=user["id"], player_id=pid, player_name=p["name"], snapshot_date=today,
                undervalued_score=p["undervaluedScore"], market_value=p.get("displayMarketValue") or 0,
                outcome="pending"))
            added.append(pid)
    return {"added": added}


@app.put("/api/me/ledger/{entry_id}/outcome")
def set_ledger_outcome(entry_id: int, body: OutcomeBody, user=Depends(current_user)):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with engine.begin() as conn:
        r = conn.execute(update(ledger_t).where(
            (ledger_t.c.id == entry_id) & (ledger_t.c.user_id == user["id"])
        ).values(outcome=body.outcome, outcome_at=now))
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
    return {"id": entry_id, "outcome": body.outcome}


@app.delete("/api/me/ledger/{entry_id}")
def delete_ledger(entry_id: int, user=Depends(current_user)):
    with engine.begin() as conn:
        conn.execute(delete(ledger_t).where(
            (ledger_t.c.id == entry_id) & (ledger_t.c.user_id == user["id"])))
    return {"deleted": entry_id}


@app.get("/api/me/ledger/stats")
def ledger_stats(user=Depends(current_user)):
    with engine.connect() as conn:
        outcomes = [r[0] for r in conn.execute(
            select(ledger_t.c.outcome).where(ledger_t.c.user_id == user["id"])).all()]
    resolved = [o for o in outcomes if o != "pending"]
    hits = sum(1 for o in resolved if o in ("signed", "rose"))
    return {"total": len(outcomes), "pending": len(outcomes) - len(resolved),
            "resolved": len(resolved), "hits": hits,
            "hitRate": round(100 * hits / len(resolved)) if resolved else 0}


# ---------------------------------------------------------------------------
# Saved views (watchlists) + public shareable links
# ---------------------------------------------------------------------------
def _safe_json(s):
    try:
        return _json.loads(s) if s else {}
    except Exception:
        return {}


def _apply_filters(scored, f):
    pos = f.get("position") or ""
    tier = f.get("tier")
    country = f.get("country") or ""
    max_age = f.get("maxAge")
    agents = f.get("hasAgent")
    if isinstance(agents, str):
        agents = [a for a in agents.split(",") if a]
    agent_set = set(agents) if agents else None
    q = (f.get("q") or "").strip().lower()
    out = []
    for p in scored:
        if pos and p["position"] != pos:
            continue
        if tier and p["tier"] != int(tier):
            continue
        if country and p["country"] != country:
            continue
        if max_age and p["age"] > int(max_age):
            continue
        if agent_set is not None and p["hasAgent"] not in agent_set:
            continue
        if q and not (q in p["name"].lower() or q in p["club"].lower() or q in p["country"].lower()):
            continue
        out.append(p)
    out.sort(key=lambda p: p["undervaluedScore"], reverse=True)
    return out


@app.get("/api/me/watchlists")
def get_watchlists(user=Depends(current_user)):
    with engine.connect() as conn:
        rows = conn.execute(
            select(watchlists_t).where(watchlists_t.c.user_id == user["id"]).order_by(watchlists_t.c.id.desc())
        ).mappings().all()
    return {"watchlists": [{"id": r["id"], "name": r["name"], "filters": _safe_json(r["filters"]),
                            "shareToken": r["share_token"], "createdAt": r["created_at"]} for r in rows]}


@app.post("/api/me/watchlists")
def add_watchlist(body: WatchlistBody, user=Depends(require_pro)):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    token = secrets.token_urlsafe(9) if body.share else None
    with engine.begin() as conn:
        count = conn.execute(select(func.count()).select_from(watchlists_t).where(
            watchlists_t.c.user_id == user["id"])).scalar_one()
        if count >= 50:
            raise HTTPException(status_code=400, detail="Saved-view limit reached (50)")
        res = conn.execute(insert(watchlists_t).values(
            user_id=user["id"], name=body.name.strip(),
            filters=_json.dumps(body.filters)[:4000], share_token=token, created_at=now))
        wid = res.inserted_primary_key[0]
    return {"id": wid, "name": body.name.strip(), "shareToken": token}


@app.delete("/api/me/watchlists/{wid}")
def delete_watchlist(wid: int, user=Depends(current_user)):
    with engine.begin() as conn:
        conn.execute(delete(watchlists_t).where(
            (watchlists_t.c.id == wid) & (watchlists_t.c.user_id == user["id"])))
    return {"deleted": wid}


@app.get("/api/shared/{token}")
def get_shared_view(token: str):
    """Public read-only shared view. Returns the saved filter + a capped preview
    of matching players with contact PII stripped, so a public link never leaks
    club emails or routing details."""
    with engine.connect() as conn:
        row = conn.execute(
            select(watchlists_t).where(watchlists_t.c.share_token == token)
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Shared view not found")
    filt = _safe_json(row["filters"])
    players = _apply_filters(get_scored(), filt)[:25]
    preview = [{"id": p["id"], "name": p["name"], "country": p["country"], "position": p["position"],
                "tier": p["tier"], "age": p["age"], "undervaluedScore": p["undervaluedScore"],
                "flag": p["flag"], "hasAgent": p["hasAgent"]} for p in players]
    return {"name": row["name"], "filters": filt, "players": preview, "count": len(preview)}


# ---------------------------------------------------------------------------
# Admin -- fast data entry for real players (the moat-collection backbone)
# ---------------------------------------------------------------------------
@app.post("/api/admin/players")
def admin_add_player(body: AdminPlayerBody, admin=Depends(require_admin)):
    from db_tables import JSON_TO_SQL
    global _raw_players, _scored_cache
    today = datetime.date.today().isoformat()
    p = {
        "name": body.name.strip(), "country": body.country.strip(),
        "league": f"{body.country.strip()} Tier {body.tier}", "tier": body.tier,
        "club": body.club.strip() or "Unknown", "position": body.position, "age": body.age,
        "minutes": body.minutes, "goals": body.goals, "assists": body.assists,
        "progPasses": body.progPasses, "progCarries": body.progCarries, "tklInt": body.tklInt,
        "saves": 0, "goalsConceded": 0, "passCompletionPct": 0.0, "sweeperActions": 0.0,
        "cleanSheets": 0, "marketValue": body.marketValue, "hasAgent": body.hasAgent,
        "contractExpires": body.contractExpires, "clubContactEmail": body.clubContactEmail.strip(),
        "contactRoute": "", "federationRegistry": "", "dateAdded": today, "lastUpdated": today,
    }
    row = {JSON_TO_SQL[k]: v for k, v in p.items() if k in JSON_TO_SQL}
    try:
        with engine.begin() as conn:
            res = conn.execute(insert(players_t).values(**row))
            pid = res.inserted_primary_key[0]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not add player (duplicate name + country?): {e}")
    _raw_players = None   # invalidate caches so the new player appears immediately
    _scored_cache = {}
    return {"id": pid, "name": body.name.strip()}


# ---------------------------------------------------------------------------
# Static frontend -- one process serves the marketing landing page ("/") and
# the scouting app ("/app"); the REST API lives under "/api".
# ---------------------------------------------------------------------------
LANDING_FILE = os.path.join(BASE_DIR, "landing.html")


@app.get("/")
def serve_landing():
    if os.path.exists(LANDING_FILE):
        return FileResponse(LANDING_FILE, media_type="text/html")
    # Fall back to the app itself if the landing page is missing.
    if os.path.exists(HTML_FILE):
        return FileResponse(HTML_FILE, media_type="text/html")
    raise HTTPException(status_code=404, detail="landing.html not found next to api_server.py")


@app.get("/app")
def serve_app():
    if os.path.exists(HTML_FILE):
        return FileResponse(HTML_FILE, media_type="text/html")
    raise HTTPException(status_code=404, detail="Scouting_App_Prototype.html not found next to api_server.py")


from fastapi.responses import HTMLResponse

SHARED_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>ScoutEdge — Shared view</title>
<style>
 body{margin:0;background:#1b1712;color:#f0e6d8;font-family:system-ui,sans-serif;padding:28px}
 .wrap{max-width:900px;margin:0 auto}
 h1{font-size:22px;margin:0 0 2px}.sub{color:#a89a86;font-size:13px;margin-bottom:20px}
 .brand{color:#cf7d5a;font-weight:800}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #3d3527}
 th{color:#a89a86;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
 .flag{font-size:11px;font-weight:600;color:#6fa87a}
 .uv{font-weight:700;color:#cf7d5a}
 a.cta{display:inline-block;margin-top:22px;background:#c1653f;color:#1a130e;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:700}
 .empty{color:#a89a86;padding:30px 0}
</style></head><body><div class="wrap">
 <h1><span class="brand">ScoutEdge</span> · <span id="vname">Shared view</span></h1>
 <div class="sub">A public scouting board · <span id="vcount">…</span> players</div>
 <div id="content" class="empty">Loading…</div>
 <a class="cta" href="/app">Open ScoutEdge →</a>
</div><script>
 var token = location.pathname.split("/").pop();
 fetch("/api/shared/" + encodeURIComponent(token)).then(function(r){ if(!r.ok) throw 0; return r.json(); })
 .then(function(d){
   document.getElementById("vname").textContent = d.name || "Shared view";
   document.getElementById("vcount").textContent = d.count;
   if(!d.players || !d.players.length){ document.getElementById("content").textContent = "No players match this view."; return; }
   var rows = d.players.map(function(p){ return "<tr><td>"+p.name+"</td><td>"+p.position+"</td><td>"+p.country+"</td><td>"+p.age+"</td><td class='uv'>"+p.undervaluedScore.toFixed(1)+"</td><td class='flag'>"+(p.flag||"")+"</td></tr>"; }).join("");
   document.getElementById("content").innerHTML = "<table><thead><tr><th>Player</th><th>Pos</th><th>Country</th><th>Age</th><th>Undervalued</th><th>Flag</th></tr></thead><tbody>"+rows+"</tbody></table>";
 }).catch(function(){ document.getElementById("content").textContent = "This shared view was not found."; });
</script></body></html>"""


@app.get("/shared/{token}")
def serve_shared_page(token: str):
    return HTMLResponse(SHARED_PAGE)


def _legal_page(title, updated, sections):
    body = "".join(f"<h2>{h}</h2>{''.join(f'<p>{para}</p>' for para in paras)}" for h, paras in sections)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>ScoutEdge — {title}</title>
<style>
 body{{margin:0;background:#1b1712;color:#f0e6d8;font-family:Georgia,'Times New Roman',serif;line-height:1.6}}
 .wrap{{max-width:760px;margin:0 auto;padding:40px 24px 80px}}
 .brand{{color:#cf7d5a;font-weight:800;font-size:20px;text-decoration:none}}
 h1{{font-size:30px;margin:22px 0 4px}} .upd{{color:#a89a86;font-size:13px;margin-bottom:28px}}
 h2{{font-size:18px;margin:28px 0 8px;color:#f0e6d8}} p{{color:#cbbfad;font-size:15px;margin:0 0 12px}}
 a.cta{{display:inline-block;margin-top:26px;background:#c1653f;color:#1a130e;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:700;font-family:system-ui,sans-serif}}
 a{{color:#cf7d5a}}
</style></head><body><div class="wrap">
 <a class="brand" href="/">ScoutEdge</a>
 <h1>{title}</h1><div class="upd">Last updated: {updated}</div>
 {body}
 <a class="cta" href="/app">Back to ScoutEdge →</a>
</div></body></html>"""


LEGAL_UPDATED = "July 24, 2026"
LEGAL_CONTACT = os.environ.get("LEGAL_CONTACT_EMAIL", "huntercrossman7@gmail.com")

TERMS_SECTIONS = [
    ("1. Agreement", [f"By creating an account or using ScoutEdge (the “Service”), you agree to these Terms of Service. If you do not agree, do not use the Service. Questions: {LEGAL_CONTACT}."]),
    ("2. The Service", ["ScoutEdge is a football-scouting web application that ranks players by a transparent, model-based “Undervalued Score.” Scores, market values, and estimates are model output for research and shortlisting — not professional advice or a guarantee of any outcome. Always follow up with real scouting.", "Portions of the current dataset are procedurally generated sample data and do not represent real individuals."]),
    ("3. Accounts", ["You are responsible for your login credentials and for all activity under your account. Do not reuse an important password. You must provide accurate information and be at least 16 years old to create an account."]),
    ("4. Acceptable use", ["You agree not to disrupt or attack the Service, access other users’ data, scrape or resell the platform in bulk, or use it for any unlawful purpose. We may suspend accounts that violate these terms."]),
    ("5. Subscriptions & billing", ["Some features require a paid “Pro” subscription, billed through our payment processor. Subscriptions renew until cancelled; you can cancel anytime from Settings, effective at the end of the current period. Except where required by law, payments are non-refundable."]),
    ("6. Intellectual property", ["The Service, its software, and its content are owned by ScoutEdge and its owner. Content you create (shortlists, notes) remains yours; you grant us the limited right to store and process it to provide the Service."]),
    ("7. Disclaimers & liability", ["The Service is provided “as is,” without warranties of any kind. To the maximum extent permitted by law, ScoutEdge is not liable for any indirect or consequential damages, and total liability is limited to the amount you paid us in the prior 12 months."]),
    ("8. Termination", ["You may stop using the Service and delete your account at any time. We may suspend or terminate access for violations of these terms."]),
    ("9. Changes", ["We may update these terms as the product evolves. Material changes will be reflected by the “Last updated” date; continued use constitutes acceptance."]),
    ("10. Governing law", ["These terms are governed by the laws of the State of Utah, USA, without regard to conflict-of-laws rules."]),
    ("11. Contact", [f"Questions about these terms: {LEGAL_CONTACT}."]),
]

PRIVACY_SECTIONS = [
    ("Overview", [f"This Privacy Policy explains what data ScoutEdge collects and how we use it. Contact: {LEGAL_CONTACT}."]),
    ("Information we collect", ["Account data: your username and a securely hashed password (we never store your password in plain text).", "Usage data you create: your shortlists and per-player notes.", "Payment data: if you subscribe, billing is handled by our payment processor (Stripe); we do not store your card details.", "Technical data: your browser stores a login token and your dashboard-layout preferences locally (localStorage)."]),
    ("How we use it", ["To provide and secure the Service, sync your shortlists and notes across devices, process subscriptions, and improve the product. We do not sell your personal data."]),
    ("Storage & security", ["Data is stored in our database (PostgreSQL) and protected with access controls, hashed passwords, and transport encryption (HTTPS). No system is perfectly secure, but we take reasonable measures to protect your data."]),
    ("Sharing", ["We share data only with service providers that help us run the Service (e.g., hosting, payment processing), and when required by law. Public “shared views” you create expose only non-personal player rankings — never contact details."]),
    ("Your rights", [f"You can view, edit, or delete your shortlists and notes in the app, and you can request account deletion by contacting {LEGAL_CONTACT}."]),
    ("Cookies & local storage", ["We use browser local storage for your login session and preferences. We do not use third-party advertising trackers."]),
    ("Changes", ["We may update this policy; the “Last updated” date reflects the latest version."]),
    ("Contact", [f"Privacy questions: {LEGAL_CONTACT}."]),
]


@app.get("/terms")
def serve_terms():
    return HTMLResponse(_legal_page("Terms of Service", LEGAL_UPDATED, TERMS_SECTIONS))


@app.get("/privacy")
def serve_privacy():
    return HTMLResponse(_legal_page("Privacy Policy", LEGAL_UPDATED, PRIVACY_SECTIONS))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
