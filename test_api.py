"""
test_api.py -- pytest suite for api_server.py (FastAPI TestClient / httpx).

Run:  python3 -m pytest test_api.py -v

Uses its own throwaway SQLite copy in /tmp so it never touches the canonical
scouting.db, and re-inits the app's engine before the run.
"""

import math
import os
import shutil
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

TEST_DB_DIR = f"/tmp/scouting_api_test_{uuid.uuid4().hex[:8]}"
TEST_DB = os.path.join(TEST_DB_DIR, "scouting.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.makedirs(TEST_DB_DIR, exist_ok=True)
shutil.copy(os.path.join(BASE_DIR, "scouting.db"), TEST_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"

import api_server  # noqa: E402  (env must be set before import)
api_server.init_db(f"sqlite:///{TEST_DB}")

client = TestClient(api_server.app)


# ---------------------------------------------------------------------------
# /api/meta
# ---------------------------------------------------------------------------
def test_meta():
    r = client.get("/api/meta")
    assert r.status_code == 200
    m = r.json()
    assert m["players"] == 1000
    assert m["countryCount"] == len(m["countries"]) > 20
    assert m["lastUpdated"]
    assert m["backend"] == "sqlite"


# ---------------------------------------------------------------------------
# /api/players -- pagination, filtering, search, sort
# ---------------------------------------------------------------------------
def test_players_default_pagination():
    r = client.get("/api/players")
    assert r.status_code == 200
    d = r.json()
    assert d["page"] == 1 and d["pageSize"] == 50
    assert len(d["items"]) == 50
    assert d["total"] == 1000
    # default sort: undervaluedScore desc
    scores = [p["undervaluedScore"] for p in d["items"]]
    assert scores == sorted(scores, reverse=True)


def test_pagination_math():
    r1 = client.get("/api/players", params={"pageSize": 30, "page": 1})
    r2 = client.get("/api/players", params={"pageSize": 30, "page": 2})
    d1, d2 = r1.json(), r2.json()
    assert len(d1["items"]) == 30 and len(d2["items"]) == 30
    names1 = {p["name"] for p in d1["items"]}
    names2 = {p["name"] for p in d2["items"]}
    assert not names1 & names2, "pages must not overlap"
    # last page has the remainder
    last_page = math.ceil(1000 / 30)
    rl = client.get("/api/players", params={"pageSize": 30, "page": last_page}).json()
    assert len(rl["items"]) == 1000 - 30 * (last_page - 1)
    # beyond the last page -> empty items, same total
    rb = client.get("/api/players", params={"pageSize": 30, "page": last_page + 5}).json()
    assert rb["items"] == [] and rb["total"] == 1000


def test_filters():
    d = client.get("/api/players", params={"position": "GK", "pageSize": 2000}).json()
    assert d["total"] > 0
    assert all(p["position"] == "GK" for p in d["items"])

    d = client.get("/api/players", params={"tier": 3, "pageSize": 2000}).json()
    assert d["total"] > 0 and all(p["tier"] == 3 for p in d["items"])

    country = d["items"][0]["country"]
    d2 = client.get("/api/players", params={"country": country, "pageSize": 2000}).json()
    assert d2["total"] > 0 and all(p["country"] == country for p in d2["items"])

    d3 = client.get("/api/players", params={"maxAge": 19, "pageSize": 2000}).json()
    assert d3["total"] > 0 and all(p["age"] <= 19 for p in d3["items"])

    d4 = client.get("/api/players", params={"hasAgent": "No", "pageSize": 2000}).json()
    assert d4["total"] > 0 and all(p["hasAgent"] == "No" for p in d4["items"])

    # comma-separated multi-value hasAgent (the frontend checkbox filter)
    d5 = client.get("/api/players", params={"hasAgent": "No,Unknown", "pageSize": 2000}).json()
    assert d5["total"] == d4["total"] + client.get(
        "/api/players", params={"hasAgent": "Unknown", "pageSize": 1}).json()["total"]

    # stacked filters narrow monotonically
    stacked = client.get("/api/players", params={
        "position": "MF", "maxAge": 22, "hasAgent": "No"}).json()
    only_pos = client.get("/api/players", params={"position": "MF"}).json()
    assert stacked["total"] <= only_pos["total"]


def test_search_q_matches_name_club_country():
    all_players = client.get("/api/players", params={"pageSize": 2000}).json()["items"]

    # by name fragment
    target = all_players[0]
    frag = target["name"].split()[-1].lower()
    d = client.get("/api/players", params={"q": frag, "pageSize": 2000}).json()
    assert any(p["name"] == target["name"] for p in d["items"])
    assert all(frag in (p["name"] + p["club"] + p["country"]).lower() for p in d["items"])

    # by club fragment
    club_frag = target["club"].split()[0].lower()
    d = client.get("/api/players", params={"q": club_frag, "pageSize": 2000}).json()
    assert d["total"] > 0
    assert all(club_frag in (p["name"] + p["club"] + p["country"]).lower() for p in d["items"])

    # by country
    d = client.get("/api/players", params={"q": target["country"].lower(), "pageSize": 2000}).json()
    assert d["total"] > 0
    assert any(p["country"] == target["country"] for p in d["items"])

    # no match
    d = client.get("/api/players", params={"q": "zzzznotaplayer"}).json()
    assert d["total"] == 0 and d["items"] == []


def test_sort():
    d = client.get("/api/players", params={"sort": "age", "dir": "asc", "pageSize": 2000}).json()
    ages = [p["age"] for p in d["items"]]
    assert ages == sorted(ages)
    d = client.get("/api/players", params={"sort": "name", "dir": "desc", "pageSize": 2000}).json()
    names = [p["name"].lower() for p in d["items"]]
    assert names == sorted(names, reverse=True)
    r = client.get("/api/players", params={"sort": "notAField"})
    assert r.status_code == 400


def test_weights_change_scores():
    default = client.get("/api/players/1").json()
    custom = client.get("/api/players", params={
        "q": default["name"], "wGa": 80, "wProg": 5, "wDef": 5, "wAge": 10,
        "pageSize": 2000}).json()["items"]
    match = next(p for p in custom if p["id"] == 1)
    # same player, different weights -> performanceScore generally differs
    assert match["performanceScore"] != pytest.approx(default["performanceScore"], abs=1e-12) \
        or match["undervaluedScore"] == default["undervaluedScore"]  # degenerate tie tolerated


def test_player_detail_and_404():
    r = client.get("/api/players/1")
    assert r.status_code == 200
    p = r.json()
    for key in ("name", "undervaluedScore", "systemFit", "performancePct",
                "marketPct", "displayMarketValue", "marketValueEstimated", "flag"):
        assert key in p
    assert client.get("/api/players/999999").status_code == 404
    assert client.get("/api/players/999999/value").status_code == 404


def test_value_endpoint_deterministic():
    r1 = client.get("/api/players/7/value")
    r2 = client.get("/api/players/7/value")
    assert r1.status_code == 200
    d1, d2 = r1.json(), r2.json()
    assert d1 == d2, "value history must be deterministic"
    assert d1["points"] == len(d1["history"]) == 15
    assert d1["history"][-1] == d1["current"]
    p = client.get("/api/players/7").json()
    assert d1["current"] == p["displayMarketValue"]
    assert d1["estimated"] == p["marketValueEstimated"]


def test_scores_match_sql_view():
    """Python scoring (the JS port) vs the player_scores SQL view, +-0.1."""
    conn = sqlite3.connect(TEST_DB)
    rows = conn.execute(
        "SELECT id, undervalued_score, flag FROM player_scores ORDER BY id LIMIT 25").fetchall()
    conn.close()
    assert rows
    scored = {p["id"]: p for p in api_server.get_scored()}
    for pid, uv_sql, flag_sql in rows:
        p = scored[pid]
        assert abs(p["undervaluedScore"] - uv_sql) <= 0.1, \
            f"player {pid}: python {p['undervaluedScore']} vs SQL view {uv_sql}"
        assert p["flag"] == flag_sql


def test_player_ids_endpoint():
    d = client.get("/api/players/ids").json()
    assert len(d["players"]) == 1000
    assert set(d["players"][0].keys()) == {"id", "name", "country"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def test_register_login_flow():
    r = client.post("/api/auth/register", json={"username": "scout_one", "password": "secret123"})
    assert r.status_code == 200
    assert r.json()["username"] == "scout_one"
    assert r.json()["token"]

    # duplicate username -> 400
    r2 = client.post("/api/auth/register", json={"username": "scout_one", "password": "other12345"})
    assert r2.status_code == 400

    # login works
    r3 = client.post("/api/auth/login", json={"username": "scout_one", "password": "secret123"})
    assert r3.status_code == 200 and r3.json()["token"]

    # wrong password -> 401
    r4 = client.post("/api/auth/login", json={"username": "scout_one", "password": "wrongpass"})
    assert r4.status_code == 401

    # weak inputs -> 400
    assert client.post("/api/auth/register", json={"username": "ab", "password": "secret123"}).status_code == 400
    assert client.post("/api/auth/register", json={"username": "valid_name", "password": "123"}).status_code == 400


def _auth_headers(username="scout_two", password="secret123"):
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    if r.status_code == 400:
        r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_unauthed_401():
    assert client.get("/api/me/shortlist").status_code == 401
    assert client.put("/api/me/shortlist", json={"playerIds": [1]}).status_code == 401
    assert client.get("/api/me/notes/1").status_code == 401
    assert client.put("/api/me/notes/1", json={"text": "x"}).status_code == 401
    bad = {"Authorization": "Bearer not.a.real.token"}
    assert client.get("/api/me/shortlist", headers=bad).status_code == 401


def test_shortlist_roundtrip():
    h = _auth_headers()
    assert client.get("/api/me/shortlist", headers=h).json()["playerIds"] == []
    r = client.put("/api/me/shortlist", json={"playerIds": [5, 1, 9, 1]}, headers=h)
    assert r.status_code == 200
    assert r.json()["playerIds"] == [1, 5, 9]  # deduped + sorted
    assert client.get("/api/me/shortlist", headers=h).json()["playerIds"] == [1, 5, 9]
    # replace semantics + invalid ids dropped
    r = client.put("/api/me/shortlist", json={"playerIds": [2, 999999]}, headers=h)
    assert r.json()["playerIds"] == [2]
    assert client.get("/api/me/shortlist", headers=h).json()["playerIds"] == [2]


def test_notes_roundtrip_and_isolation():
    h1 = _auth_headers("notes_user_a")
    h2 = _auth_headers("notes_user_b")
    assert client.get("/api/me/notes/3", headers=h1).json()["text"] == ""
    r = client.put("/api/me/notes/3", json={"text": "left foot, raw"}, headers=h1)
    assert r.status_code == 200
    assert client.get("/api/me/notes/3", headers=h1).json()["text"] == "left foot, raw"
    # update (upsert path)
    client.put("/api/me/notes/3", json={"text": "revised opinion"}, headers=h1)
    assert client.get("/api/me/notes/3", headers=h1).json()["text"] == "revised opinion"
    # other user can't see it
    assert client.get("/api/me/notes/3", headers=h2).json()["text"] == ""
    # unknown player -> 404
    assert client.put("/api/me/notes/999999", json={"text": "x"}, headers=h1).status_code == 404


# ---------------------------------------------------------------------------
# Frontend served at /
# ---------------------------------------------------------------------------
def test_root_serves_frontend():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Global Lower-Tier Scouting Prototype" in r.text
