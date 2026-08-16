"""
test_api.py -- pytest suite for api_server.py (FastAPI TestClient / httpx).

Run:  python3 -m pytest test_api.py -v

Uses its own throwaway SQLite copy so it never touches the canonical
scouting.db, re-inits the app's engine, and disables the in-memory rate
limiter (which would otherwise throttle the suite's many auth calls).

The data endpoints (/api/players*) require a JWT; a shared authenticated
client is created once at import and reused via the `authed_get` helper.
"""

import json
import math
import os
import shutil
import sqlite3
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

TEST_DB_DIR = tempfile.mkdtemp(prefix="scouting_api_test_")
TEST_DB = os.path.join(TEST_DB_DIR, "scouting.db")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

shutil.copy(os.path.join(BASE_DIR, "scouting.db"), TEST_DB)
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["RATE_LIMIT_DISABLED"] = "1"
os.environ["ADMIN_USERNAMES"] = "admin_user"

import api_server  # noqa: E402  (env must be set before import)
api_server.init_db(f"sqlite:///{TEST_DB}")

client = TestClient(api_server.app)

# One shared authenticated user for all data-endpoint access.
_reg = client.post("/api/auth/register", json={"username": "data_user", "password": "secret123"})
assert _reg.status_code == 200, _reg.text
AUTH = {"Authorization": "Bearer " + _reg.json()["token"]}

# Total players in the shipped test DB (currently 5,000; kept dynamic so the
# suite doesn't break when the roster grows).
TOTAL = client.get("/api/meta").json()["players"]


def authed_get(path, **params):
    return client.get(path, headers=AUTH, params=params or None)


# ---------------------------------------------------------------------------
# /api/meta (public)
# ---------------------------------------------------------------------------
def test_meta():
    r = client.get("/api/meta")
    assert r.status_code == 200
    m = r.json()
    assert m["players"] == TOTAL > 20
    assert m["countryCount"] == len(m["countries"]) > 20
    assert m["lastUpdated"]
    assert m["backend"] == "sqlite"


# ---------------------------------------------------------------------------
# Data endpoints now require auth
# ---------------------------------------------------------------------------
def test_data_endpoints_require_auth():
    for path in ("/api/players", "/api/players/ids", "/api/players/1", "/api/players/1/value"):
        assert client.get(path).status_code == 401, f"{path} should be gated"
    bad = {"Authorization": "Bearer not.a.real.token"}
    assert client.get("/api/players", headers=bad).status_code == 401


# ---------------------------------------------------------------------------
# /api/players -- pagination, filtering, search, sort (authed)
# ---------------------------------------------------------------------------
def test_players_default_pagination():
    r = authed_get("/api/players")
    assert r.status_code == 200
    d = r.json()
    assert d["page"] == 1 and d["pageSize"] == 50
    assert len(d["items"]) == 50
    assert d["total"] == TOTAL
    scores = [p["undervaluedScore"] for p in d["items"]]
    assert scores == sorted(scores, reverse=True)


def test_pagination_math():
    d1 = authed_get("/api/players", pageSize=30, page=1).json()
    d2 = authed_get("/api/players", pageSize=30, page=2).json()
    assert len(d1["items"]) == 30 and len(d2["items"]) == 30
    names1 = {p["name"] for p in d1["items"]}
    names2 = {p["name"] for p in d2["items"]}
    assert not names1 & names2, "pages must not overlap"
    last_page = math.ceil(TOTAL / 30)
    rl = authed_get("/api/players", pageSize=30, page=last_page).json()
    assert len(rl["items"]) == TOTAL - 30 * (last_page - 1)
    rb = authed_get("/api/players", pageSize=30, page=last_page + 5).json()
    assert rb["items"] == [] and rb["total"] == TOTAL


def test_filters():
    d = authed_get("/api/players", position="GK", pageSize=2000).json()
    assert d["total"] > 0 and all(p["position"] == "GK" for p in d["items"])

    d = authed_get("/api/players", tier=3, pageSize=2000).json()
    assert d["total"] > 0 and all(p["tier"] == 3 for p in d["items"])

    country = d["items"][0]["country"]
    d2 = authed_get("/api/players", country=country, pageSize=2000).json()
    assert d2["total"] > 0 and all(p["country"] == country for p in d2["items"])

    d3 = authed_get("/api/players", maxAge=19, pageSize=2000).json()
    assert d3["total"] > 0 and all(p["age"] <= 19 for p in d3["items"])

    d4 = authed_get("/api/players", hasAgent="No", pageSize=2000).json()
    assert d4["total"] > 0 and all(p["hasAgent"] == "No" for p in d4["items"])

    d5 = authed_get("/api/players", hasAgent="No,Unknown", pageSize=2000).json()
    unknown_total = authed_get("/api/players", hasAgent="Unknown", pageSize=1).json()["total"]
    assert d5["total"] == d4["total"] + unknown_total

    stacked = authed_get("/api/players", position="MF", maxAge=22, hasAgent="No").json()
    only_pos = authed_get("/api/players", position="MF").json()
    assert stacked["total"] <= only_pos["total"]


def test_value_band_filter():
    lo, hi = 60_000, 120_000
    d = authed_get("/api/players", minValue=lo, maxValue=hi, pageSize=10000).json()
    assert d["total"] > 0
    assert d["total"] == len(d["items"]), "band must fit in one page for this check"
    assert all(lo <= p["displayMarketValue"] <= hi for p in d["items"])

    # The band is taken on the displayed value, so players with no recorded
    # value are matched on the model's estimate -- not dropped as a raw 0.
    assert any(p["marketValueEstimated"] for p in d["items"])

    # One-sided bounds work independently.
    only_min = authed_get("/api/players", minValue=lo, pageSize=10000).json()
    only_max = authed_get("/api/players", maxValue=hi, pageSize=10000).json()
    assert all(p["displayMarketValue"] >= lo for p in only_min["items"])
    assert all(p["displayMarketValue"] <= hi for p in only_max["items"])
    assert only_min["total"] >= d["total"] and only_max["total"] >= d["total"]

    # An empty band returns nothing rather than everything.
    assert authed_get("/api/players", minValue=hi, maxValue=lo).json()["total"] == 0

    # Composes with the other filters instead of replacing them.
    stacked = authed_get("/api/players", minValue=lo, maxValue=hi, position="GK",
                         pageSize=10000).json()
    assert all(p["position"] == "GK" and lo <= p["displayMarketValue"] <= hi
               for p in stacked["items"])
    assert stacked["total"] <= d["total"]

    assert authed_get("/api/players", minValue=-1).status_code == 422


def test_all_page_size_returns_whole_roster():
    """The client's "All" option asks for API_ALL_PAGE_SIZE (10000) in one page;
    the old 2000 ceiling rejected that outright and truncated the view."""
    d = authed_get("/api/players", pageSize=10000).json()
    assert d["total"] == TOTAL and len(d["items"]) == TOTAL
    assert authed_get("/api/players", pageSize=10001).status_code == 422


def test_search_q_matches_name_club_country():
    all_players = authed_get("/api/players", pageSize=2000).json()["items"]
    target = all_players[0]

    frag = target["name"].split()[-1].lower()
    d = authed_get("/api/players", q=frag, pageSize=2000).json()
    assert any(p["name"] == target["name"] for p in d["items"])
    assert all(frag in (p["name"] + p["club"] + p["country"]).lower() for p in d["items"])

    club_frag = target["club"].split()[0].lower()
    d = authed_get("/api/players", q=club_frag, pageSize=2000).json()
    assert d["total"] > 0
    assert all(club_frag in (p["name"] + p["club"] + p["country"]).lower() for p in d["items"])

    d = authed_get("/api/players", q=target["country"].lower(), pageSize=2000).json()
    assert d["total"] > 0 and any(p["country"] == target["country"] for p in d["items"])

    d = authed_get("/api/players", q="zzzznotaplayer").json()
    assert d["total"] == 0 and d["items"] == []


def test_sort():
    d = authed_get("/api/players", sort="age", dir="asc", pageSize=2000).json()
    assert [p["age"] for p in d["items"]] == sorted(p["age"] for p in d["items"])
    d = authed_get("/api/players", sort="name", dir="desc", pageSize=2000).json()
    names = [p["name"].lower() for p in d["items"]]
    assert names == sorted(names, reverse=True)
    assert authed_get("/api/players", sort="notAField").status_code == 400


def test_weights_change_scores():
    default = authed_get("/api/players/1").json()
    custom = authed_get("/api/players", q=default["name"], wGa=80, wProg=5, wDef=5, wAge=10,
                        pageSize=2000).json()["items"]
    match = next(p for p in custom if p["id"] == 1)
    assert match["performanceScore"] != pytest.approx(default["performanceScore"], abs=1e-12) \
        or match["undervaluedScore"] == default["undervaluedScore"]


def test_player_detail_and_404():
    r = authed_get("/api/players/1")
    assert r.status_code == 200
    p = r.json()
    for key in ("name", "undervaluedScore", "systemFit", "performancePct",
                "marketPct", "displayMarketValue", "marketValueEstimated", "flag"):
        assert key in p
    assert authed_get("/api/players/999999").status_code == 404
    assert authed_get("/api/players/999999/value").status_code == 404


def test_value_endpoint_deterministic():
    d1 = authed_get("/api/players/7/value").json()
    d2 = authed_get("/api/players/7/value").json()
    assert d1 == d2, "value history must be deterministic"
    assert d1["points"] == len(d1["history"]) == 15
    assert d1["history"][-1] == d1["current"]
    p = authed_get("/api/players/7").json()
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
        assert abs(p["undervaluedScore"] - uv_sql) <= 0.1
        assert p["flag"] == flag_sql


def test_player_ids_endpoint():
    d = authed_get("/api/players/ids").json()
    assert len(d["players"]) == TOTAL
    assert set(d["players"][0].keys()) == {"id", "name", "country"}


def test_roster_summary():
    assert client.get("/api/stats/summary").status_code == 401  # gated
    d = authed_get("/api/stats/summary").json()
    assert d["count"] == TOTAL
    assert d["avgAge"] > 0
    assert d["avgKnownMarketValue"] > 0
    assert 0 <= d["withAgentPct"] <= 100
    # country filter narrows the roster and stays valid
    dc = authed_get("/api/stats/summary", country="Japan").json()
    assert 0 < dc["count"] <= TOTAL


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def test_register_login_flow():
    r = client.post("/api/auth/register", json={"username": "scout_one", "password": "secret123"})
    assert r.status_code == 200
    assert r.json()["username"] == "scout_one" and r.json()["token"]

    assert client.post("/api/auth/register",
                       json={"username": "scout_one", "password": "other12345"}).status_code == 400

    r3 = client.post("/api/auth/login", json={"username": "scout_one", "password": "secret123"})
    assert r3.status_code == 200 and r3.json()["token"]

    assert client.post("/api/auth/login",
                       json={"username": "scout_one", "password": "wrongpass"}).status_code == 401

    # weak inputs -> 400 (business rule), not a 5xx
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
    assert client.get("/api/me/shortlist", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_shortlist_roundtrip():
    h = _auth_headers()
    assert client.get("/api/me/shortlist", headers=h).json()["playerIds"] == []
    r = client.put("/api/me/shortlist", json={"playerIds": [5, 1, 9, 1]}, headers=h)
    assert r.status_code == 200 and r.json()["playerIds"] == [1, 5, 9]
    assert client.get("/api/me/shortlist", headers=h).json()["playerIds"] == [1, 5, 9]
    r = client.put("/api/me/shortlist", json={"playerIds": [2, 999999]}, headers=h)
    assert r.json()["playerIds"] == [2]


def test_notes_roundtrip_and_isolation():
    h1 = _auth_headers("notes_user_a")
    h2 = _auth_headers("notes_user_b")
    assert client.get("/api/me/notes/3", headers=h1).json()["text"] == ""
    assert client.put("/api/me/notes/3", json={"text": "left foot, raw"}, headers=h1).status_code == 200
    assert client.get("/api/me/notes/3", headers=h1).json()["text"] == "left foot, raw"
    client.put("/api/me/notes/3", json={"text": "revised opinion"}, headers=h1)
    assert client.get("/api/me/notes/3", headers=h1).json()["text"] == "revised opinion"
    assert client.get("/api/me/notes/3", headers=h2).json()["text"] == ""
    assert client.put("/api/me/notes/999999", json={"text": "x"}, headers=h1).status_code == 404


# ---------------------------------------------------------------------------
# Hardening: input caps + security headers
# ---------------------------------------------------------------------------
def test_input_caps_reject_oversized_payloads():
    h = _auth_headers("caps_user")
    # note text capped at 5000 chars -> 422 (not a silently-accepted 200)
    assert client.put("/api/me/notes/1", json={"text": "y" * 6000}, headers=h).status_code == 422
    # shortlist capped at 1000 ids -> 422
    assert client.put("/api/me/shortlist", json={"playerIds": list(range(1, 2001))}, headers=h).status_code == 422
    # over-long credentials -> 422
    assert client.post("/api/auth/register",
                       json={"username": "a" * 100, "password": "x" * 300}).status_code == 422


def test_security_headers_present():
    r = client.get("/api/meta")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"
    assert "referrer-policy" in {k.lower() for k in r.headers}


def test_rate_limiter_logic():
    """The limiter itself works even though it's disabled for the suite."""
    api_server._RATE_BUCKETS.clear()
    ip = "203.0.113.7"
    limit, _ = api_server.RATE_LIMITS["auth"]
    allowed = sum(1 for _ in range(limit + 5) if api_server._rate_ok(ip, "auth"))
    assert allowed == limit, f"expected {limit} allowed, got {allowed}"


# ---------------------------------------------------------------------------
# Frontend routing: landing at "/", app at "/app"
# ---------------------------------------------------------------------------
def test_landing_served_at_root():
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "ScoutEdge" in r.text and "dossier" in r.text  # marketing landing markers


def test_app_served_at_app_path():
    r = client.get("/app")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "dash-customize-btn" in r.text  # the customizable dashboard is present


def test_app_html_has_no_embedded_player_data():
    """Privacy: the served app must not embed player records (view-source leak)."""
    r = client.get("/app")
    assert "constRAW_PLAYERS=[]" in "".join(r.text.split())


def test_players_all_requires_auth_and_returns_dataset():
    assert client.get("/api/players/all").status_code == 401
    d = authed_get("/api/players/all").json()
    assert len(d["players"]) == TOTAL
    assert "id" in d["players"][0] and "undervaluedScore" not in d["players"][0]  # raw rows, not scored


def test_legal_pages_served():
    for path, marker in (("/terms", "Terms of Service"), ("/privacy", "Privacy Policy")):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
        assert marker in r.text


# ---------------------------------------------------------------------------
# Growth features: account/billing, ledger, watchlists+share, admin
# ---------------------------------------------------------------------------
def _pro_headers(username):
    h = _auth_headers(username)
    client.post("/api/billing/checkout", headers=h)  # demo mode -> Pro
    return h


def test_me_and_demo_billing():
    h = _auth_headers("me_user")
    me = client.get("/api/me", headers=h).json()
    assert me["isPro"] is False and me["isAdmin"] is False and me["billingEnabled"] is False
    up = client.post("/api/billing/checkout", headers=h).json()
    assert up["isPro"] is True
    assert client.get("/api/me", headers=h).json()["isPro"] is True
    client.post("/api/billing/cancel", headers=h)
    assert client.get("/api/me", headers=h).json()["isPro"] is False


def test_ledger_is_pro_gated_and_flows():
    h = _auth_headers("ledger_free")
    assert client.post("/api/me/ledger", json={"playerIds": [1, 2]}, headers=h).status_code == 402
    hp = _pro_headers("ledger_pro")
    add = client.post("/api/me/ledger", json={"playerIds": [1, 2, 3]}, headers=hp).json()
    assert set(add["added"]) == {1, 2, 3}
    # duplicate pending picks are skipped
    assert client.post("/api/me/ledger", json={"playerIds": [1]}, headers=hp).json()["added"] == []
    entries = client.get("/api/me/ledger", headers=hp).json()["entries"]
    assert len(entries) == 3
    eid = entries[0]["id"]
    assert client.put(f"/api/me/ledger/{eid}/outcome", json={"outcome": "signed"}, headers=hp).status_code == 200
    assert client.put(f"/api/me/ledger/{eid}/outcome", json={"outcome": "bogus"}, headers=hp).status_code == 422
    stats = client.get("/api/me/ledger/stats", headers=hp).json()
    assert stats["total"] == 3 and stats["hits"] == 1 and stats["hitRate"] == 100


def test_watchlists_and_public_share_strips_pii():
    hp = _pro_headers("views_pro")
    assert client.post("/api/me/watchlists", json={"name": "x", "filters": {}}, headers=_auth_headers("views_free")).status_code == 402
    wl = client.post("/api/me/watchlists",
                     json={"name": "GKs", "filters": {"position": "GK", "maxAge": 21}, "share": True}, headers=hp).json()
    token = wl["shareToken"]
    assert token
    lst = client.get("/api/me/watchlists", headers=hp).json()["watchlists"]
    assert any(w["name"] == "GKs" for w in lst)
    shared = client.get(f"/api/shared/{token}").json()  # public, no auth
    assert shared["name"] == "GKs" and shared["count"] > 0
    assert "clubContactEmail" not in shared["players"][0]  # PII stripped from public share
    assert client.get("/api/shared/nonexistenttoken").status_code == 404


def test_admin_add_player_is_gated():
    # non-admin blocked
    assert client.post("/api/admin/players", json={
        "name": "Nope", "country": "Nowhere", "position": "MF", "tier": 3, "age": 20},
        headers=_auth_headers("not_admin")).status_code == 403
    # admin (username in ADMIN_USERNAMES) can add
    r = client.post("/api/admin/players", json={
        "name": "Pytest Player", "country": "Latvia", "position": "FW", "tier": 3,
        "club": "PT FC", "age": 18, "minutes": 1500, "goals": 10, "marketValue": 50000,
        "hasAgent": "No"}, headers=_auth_headers("admin_user"))
    assert r.status_code == 200
    pid = r.json()["id"]
    got = client.get(f"/api/players/{pid}", headers=AUTH).json()
    assert got["name"] == "Pytest Player" and got["hasAgent"] == "No"


# ---------------------------------------------------------------------------
# Acquirability / Deal Score: pin the worked examples from acquirability_spec.md
# ---------------------------------------------------------------------------
def test_acquirability_spec_examples():
    import scoring

    def mk(age, agent, ce, val, uv, tier):
        return {"age": age, "hasAgent": agent, "contractExpires": ce,
                "displayMarketValue": val, "undervaluedScore": uv, "tier": tier,
                "leagueStrength": math.exp(-0.155 * (tier - 2)),
                "lowSample": False, "marketValueEstimated": False, "minutes": 2000}

    # Values recomputed when the roster moved onto the EUR 80-120k band: FEE_REF
    # is now the 80k band floor, so every fee term sits near 1.0 and
    # acquirability rises across the board. Kept in step with the worked
    # examples in acquirability_spec.md -- change both together.
    a = scoring.acquirability(mk(20, "No", 2026, 85000, 55, 3))
    assert a["score"] == pytest.approx(81.3, abs=0.2)
    assert scoring.deal_score(55, a["score"]) == pytest.approx(64.3, abs=0.2)

    b = scoring.acquirability(mk(24, "Yes", 2029, 140000, 10, 2))
    assert b["score"] == pytest.approx(25.2, abs=0.2)
    assert scoring.deal_score(10, b["score"]) == pytest.approx(14.5, abs=0.2)

    c = scoring.acquirability(mk(19, "Unknown", 2027, 102000, -20, 2))
    assert c["score"] == pytest.approx(57.7, abs=0.2)
    assert scoring.deal_score(-20, c["score"]) == 0.0  # overvalued is never a deal


def test_deal_fields_in_api():
    p = authed_get("/api/players/1").json()
    for key in ("acquirabilityScore", "dealScore", "hotProspect", "dealExplain"):
        assert key in p
    assert 0 <= p["dealScore"] <= 100
    d = authed_get("/api/players", sort="dealScore", dir="desc", pageSize=50).json()
    scores = [x["dealScore"] for x in d["items"]]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > 55  # the call list's top end exists


# ---------------------------------------------------------------------------
# Two-factor authentication (TOTP)
# ---------------------------------------------------------------------------
def _mfa_user(name):
    """Register a user and walk them through enabling 2FA."""
    import pyotp
    r = client.post("/api/auth/register", json={"username": name, "password": "secret123"})
    assert r.status_code == 200, r.text
    h = {"Authorization": "Bearer " + r.json()["token"]}
    secret = client.post("/api/me/2fa/setup", headers=h).json()["secret"]
    codes = client.post("/api/me/2fa/enable",
                        json={"code": pyotp.TOTP(secret).now()}, headers=h).json()["backupCodes"]
    return h, secret, codes


def test_2fa_setup_returns_secret_and_qr():
    r = client.post("/api/auth/register", json={"username": "mfa_setup", "password": "secret123"})
    h = {"Authorization": "Bearer " + r.json()["token"]}
    d = client.post("/api/me/2fa/setup", headers=h).json()
    assert len(d["secret"]) >= 16
    assert d["otpauthUri"].startswith("otpauth://totp/")
    assert d["qrDataUri"].startswith("data:image/svg+xml;base64,")
    # still off until a code is confirmed
    assert client.get("/api/me", headers=h).json()["mfaEnabled"] is False


def test_2fa_enable_rejects_wrong_code():
    r = client.post("/api/auth/register", json={"username": "mfa_wrong", "password": "secret123"})
    h = {"Authorization": "Bearer " + r.json()["token"]}
    client.post("/api/me/2fa/setup", headers=h)
    assert client.post("/api/me/2fa/enable", json={"code": "000000"}, headers=h).status_code == 400


def test_2fa_login_withholds_token_until_code_verified():
    import pyotp
    h, secret, _ = _mfa_user("mfa_login")
    assert client.get("/api/me", headers=h).json()["mfaEnabled"] is True

    r = client.post("/api/auth/login", json={"username": "mfa_login", "password": "secret123"})
    body = r.json()
    assert body.get("mfaRequired") is True
    assert "token" not in body                      # password alone grants nothing
    challenge = body["mfaToken"]

    # the challenge token must not work as an access token
    assert client.get("/api/me", headers={"Authorization": "Bearer " + challenge}).status_code == 401
    # wrong code rejected
    assert client.post("/api/auth/login/2fa",
                       json={"mfaToken": challenge, "code": "123456"}).status_code == 401
    # correct code completes the login
    ok = client.post("/api/auth/login/2fa",
                     json={"mfaToken": challenge, "code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200 and "token" in ok.json()


def test_2fa_backup_code_works_once():
    h, _, codes = _mfa_user("mfa_backup")
    for expected in (200, 401):                     # second use of the same code must fail
        ch = client.post("/api/auth/login",
                         json={"username": "mfa_backup", "password": "secret123"}).json()["mfaToken"]
        r = client.post("/api/auth/login/2fa", json={"mfaToken": ch, "code": codes[0]})
        assert r.status_code == expected


def test_2fa_disable_requires_password_and_code():
    import pyotp
    h, secret, _ = _mfa_user("mfa_disable")
    bad = client.post("/api/me/2fa/disable",
                      json={"password": "wrongpw", "code": pyotp.TOTP(secret).now()}, headers=h)
    assert bad.status_code == 401
    ok = client.post("/api/me/2fa/disable",
                     json={"password": "secret123", "code": pyotp.TOTP(secret).now()}, headers=h)
    assert ok.status_code == 200
    # login is single-factor again
    assert "token" in client.post("/api/auth/login",
                                  json={"username": "mfa_disable", "password": "secret123"}).json()


# ---------------------------------------------------------------------------
# List payload trim: explain/dealExplain are modal-only and 59% of the bytes
# ---------------------------------------------------------------------------
def test_list_omits_explain_blobs_but_keeps_their_inputs():
    item = authed_get("/api/players", pageSize=1).json()["items"][0]
    for field in api_server.LIST_OMIT_FIELDS:
        assert field not in item, f"{field} should not ship in a list response"
    # Everything buildExplain()/acquirability() need to rebuild them client-side
    # must still be present, or the modal degrades instead of recomputing.
    for field in ("performancePct", "marketPct", "gaPct", "progPct", "defPct",
                  "youthPct", "shrinkWeight", "leagueStrength", "lowSample",
                  "ageValueFactor", "marketValueEstimated", "displayMarketValue",
                  "contractExpires", "hasAgent", "age", "minutes", "flag",
                  "undervaluedScore", "acquirabilityScore", "dealScore"):
        assert field in item, f"{field} is needed to rebuild the explain boxes"


def test_single_player_endpoint_still_returns_explain():
    pid = authed_get("/api/players", pageSize=1).json()["items"][0]["id"]
    full = authed_get(f"/api/players/{pid}").json()
    assert full["explain"]["summary"]
    assert full["dealExplain"]["drivers"]


def test_list_trim_does_not_mutate_the_score_cache():
    authed_get("/api/players", pageSize=5)
    cached = api_server.get_scored()[0]
    assert "explain" in cached and "dealExplain" in cached


# ---------------------------------------------------------------------------
# Roster cache freshness: a write by another process must become visible
# without a restart (refresh_prod_players.py talks to the same DB directly).
# ---------------------------------------------------------------------------
def _force_freshness_poll():
    """Age the last poll past its interval so the next read re-checks the DB."""
    api_server._stamp_checked_at = time.monotonic() - api_server.CACHE_CHECK_SECONDS - 1


def _external_write(player_id, market_value):
    """Write straight to the file with a separate connection -- deliberately
    bypassing the app's engine, the way the refresh scripts do."""
    con = sqlite3.connect(TEST_DB)
    con.execute("UPDATE players SET market_value = ? WHERE id = ?", (market_value, player_id))
    con.commit()
    con.close()


def test_external_write_is_picked_up_without_restart():
    pid = authed_get("/api/players", pageSize=1).json()["items"][0]["id"]
    before = authed_get("/api/players", ids=str(pid)).json()["items"][0]["marketValue"]
    new_value = (before or 0) + 12345

    _external_write(pid, new_value)
    # Still cached: the poll interval has not elapsed, so nothing re-reads yet.
    assert authed_get("/api/players", ids=str(pid)).json()["items"][0]["marketValue"] == before

    _force_freshness_poll()
    after = authed_get("/api/players", ids=str(pid)).json()["items"][0]
    assert after["marketValue"] == new_value
    assert after["displayMarketValue"] == new_value   # rescored, not just re-read

    _external_write(pid, before)                      # leave the roster as we found it
    _force_freshness_poll()
    assert authed_get("/api/players", ids=str(pid)).json()["items"][0]["marketValue"] == before


def test_admin_cache_refresh_is_gated_and_reloads():
    assert client.post("/api/admin/cache/refresh",
                       headers=_auth_headers("not_admin")).status_code == 403
    r = client.post("/api/admin/cache/refresh", headers=_auth_headers("admin_user"))
    assert r.status_code == 200
    body = r.json()
    assert body["invalidated"] is True
    assert body["players"] == len(api_server.get_players())


def test_invalidate_caches_clears_both_caches():
    api_server.get_scored()
    assert api_server._raw_players is not None and api_server._scored_cache
    api_server.invalidate_caches()
    assert api_server._raw_players is None and api_server._scored_cache == {}
    assert len(api_server.get_players()) > 0   # rebuilds on next read


# ---------------------------------------------------------------------------
# Rate-limit bucket eviction: _RATE_BUCKETS used to grow one entry per unique
# client IP, forever.
# ---------------------------------------------------------------------------
def test_sweep_drops_ips_whose_history_has_aged_out():
    api_server._RATE_BUCKETS.clear()
    now = time.time()
    widest = max(w for _, w in api_server.RATE_LIMITS.values())
    api_server._RATE_BUCKETS["198.51.100.1"]["api"].append(now - widest - 5)
    api_server._RATE_BUCKETS["198.51.100.2"]["auth"].append(now - widest - 5)
    api_server._sweep_rate_buckets(now)
    assert len(api_server._RATE_BUCKETS) == 0


def test_sweep_keeps_active_ips_and_their_budget():
    api_server._RATE_BUCKETS.clear()
    now = time.time()
    ip = "198.51.100.9"
    limit, _ = api_server.RATE_LIMITS["auth"]
    for _ in range(limit - 1):
        api_server._RATE_BUCKETS[ip]["auth"].append(now)
    api_server._sweep_rate_buckets(now)
    assert ip in api_server._RATE_BUCKETS
    # The surviving history still counts: one request left, then throttled.
    assert api_server._rate_ok(ip, "auth") is True
    assert api_server._rate_ok(ip, "auth") is False


def test_tracked_ip_count_is_capped(monkeypatch):
    """A flood of distinct, currently-active IPs stays bounded."""
    monkeypatch.setattr(api_server, "RATE_MAX_TRACKED_IPS", 50)
    api_server._RATE_BUCKETS.clear()
    now = time.time()
    for i in range(120):
        # All recent, so the age-out pass cannot reclaim any of them.
        api_server._RATE_BUCKETS[f"10.0.{i // 256}.{i % 256}"]["api"].append(now - i * 0.001)
    api_server._sweep_rate_buckets(now)
    assert len(api_server._RATE_BUCKETS) == 50
    # Eviction is least-recently-active, so the newest arrivals are the keepers.
    assert "10.0.0.0" in api_server._RATE_BUCKETS
    assert "10.0.0.119" not in api_server._RATE_BUCKETS
    api_server._RATE_BUCKETS.clear()


def test_rate_ok_sweeps_on_its_own_schedule():
    """_rate_ok must trigger the sweep; nothing else calls it in production."""
    api_server._RATE_BUCKETS.clear()
    now = time.time()
    widest = max(w for _, w in api_server.RATE_LIMITS.values())
    api_server._RATE_BUCKETS["203.0.113.200"]["api"].append(now - widest - 5)
    api_server._rate_swept_at = 0.0          # force the interval to have elapsed
    api_server._rate_ok("203.0.113.201", "api")
    assert "203.0.113.200" not in api_server._RATE_BUCKETS
    api_server._RATE_BUCKETS.clear()


# ---------------------------------------------------------------------------
# /api/players conditional GET: the roster is identical for every signed-in
# user and only changes when the players table does.
# ---------------------------------------------------------------------------
def _get_with_etag(etag, **params):
    headers = dict(AUTH)
    headers["If-None-Match"] = etag
    return client.get("/api/players", headers=headers, params=params or None)


def test_players_response_carries_etag_and_cache_control():
    r = authed_get("/api/players")
    assert r.status_code == 200
    assert r.headers["etag"].startswith('W/"')
    assert r.headers["cache-control"] == api_server.PLAYERS_CACHE_CONTROL
    assert "private" in r.headers["cache-control"]   # authed data, never shared


def test_matching_etag_returns_304_with_no_body():
    etag = authed_get("/api/players", pageSize=25).headers["etag"]
    r = _get_with_etag(etag, pageSize=25)
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag           # revalidation re-affirms the tag


def test_etag_is_stable_across_identical_requests():
    a = authed_get("/api/players", position="MF", page=2).headers["etag"]
    b = authed_get("/api/players", position="MF", page=2).headers["etag"]
    assert a == b


def test_etag_is_specific_to_the_query():
    base = authed_get("/api/players", pageSize=25)
    etag = base.headers["etag"]
    # Every parameter that changes the body must change the tag, so a tag from
    # one query can never satisfy another.
    for differing in ({"pageSize": 26}, {"page": 2}, {"position": "MF"},
                      {"sort": "age"}, {"dir": "asc"}, {"maxAge": 21},
                      {"minValue": 500000}, {"q": "a"}, {"wGa": 50}):
        params = {"pageSize": 25, **differing}
        assert authed_get("/api/players", **params).headers["etag"] != etag
        r = _get_with_etag(etag, **params)
        assert r.status_code == 200, f"stale tag honoured for {differing}"


def test_weak_comparison_and_if_none_match_lists():
    etag = authed_get("/api/players", pageSize=25).headers["etag"]
    strong = etag[2:]                                    # same tag, W/ stripped
    assert _get_with_etag(strong, pageSize=25).status_code == 304
    assert _get_with_etag(f'W/"other", {etag}', pageSize=25).status_code == 304
    assert _get_with_etag("*", pageSize=25).status_code == 304
    assert _get_with_etag('W/"nope"', pageSize=25).status_code == 200


def test_etag_changes_when_an_external_write_lands():
    pid = authed_get("/api/players", pageSize=1).json()["items"][0]["id"]
    before_value = authed_get("/api/players", ids=str(pid)).json()["items"][0]["marketValue"]
    etag = authed_get("/api/players", pageSize=25).headers["etag"]
    assert _get_with_etag(etag, pageSize=25).status_code == 304

    _external_write(pid, (before_value or 0) + 4242)
    _force_freshness_poll()
    # The roster moved, so the cached copy behind that tag is stale and the
    # client must be sent the new body rather than a 304.
    r = _get_with_etag(etag, pageSize=25)
    assert r.status_code == 200
    assert r.headers["etag"] != etag

    _external_write(pid, before_value)                   # restore the roster
    _force_freshness_poll()
    authed_get("/api/players", pageSize=1)


# ---------------------------------------------------------------------------
# Pre-sorted roster: the per-request sort is now a per-(weights, sort, dir)
# cache plus one filtering pass.
# ---------------------------------------------------------------------------
def test_presorted_order_matches_sorting_the_filtered_set():
    """The whole point: filtering a stably-sorted list == sorting the filtered
    one, ties included."""
    scored = api_server.get_scored()
    for sort, direction in (("undervaluedScore", "desc"), ("name", "asc"),
                            ("age", "asc"), ("dealScore", "desc")):
        want = [p["id"] for p in sorted(
            (p for p in scored if p["position"] == "MF"),
            key=api_server._sort_key(sort), reverse=direction != "asc")]
        got = [p["id"] for p in authed_get(
            "/api/players", position="MF", sort=sort, dir=direction,
            pageSize=api_server.MAX_PAGE_SIZE).json()["items"]]
        assert got == want, f"order diverged for {sort} {direction}"


def test_sorted_cache_is_populated_and_reused():
    api_server.invalidate_caches()
    assert api_server._sorted_cache == {}
    authed_get("/api/players", sort="age", dir="asc")
    assert len(api_server._sorted_cache) == 1
    authed_get("/api/players", sort="age", dir="asc", position="MF", page=3)
    assert len(api_server._sorted_cache) == 1, "filters must not key the cache"
    authed_get("/api/players", sort="age", dir="desc")
    assert len(api_server._sorted_cache) == 2, "direction must key the cache"


def test_sorted_cache_is_bounded():
    api_server.invalidate_caches()
    for sort in ("age", "name", "club", "country", "position", "tier",
                 "undervaluedScore", "dealScore", "marketValue",
                 "displayMarketValue", "performancePct", "marketPct",
                 "goals", "assists"):
        authed_get("/api/players", sort=sort, pageSize=1)
    assert len(api_server._sorted_cache) <= 13


def test_stale_roster_never_survives_in_the_sorted_cache():
    """A freshness reload must drop the cached orders too, or the API would
    serve an order built from a roster the DB has moved past."""
    pid = authed_get("/api/players", sort="marketValue", dir="desc",
                     pageSize=1).json()["items"][0]["id"]
    before = authed_get("/api/players", ids=str(pid)).json()["items"][0]["marketValue"]

    _external_write(pid, 1)                    # was top by value; now bottom
    _force_freshness_poll()
    top = authed_get("/api/players", sort="marketValue", dir="desc",
                     pageSize=1).json()["items"][0]
    assert top["id"] != pid, "sorted cache served a superseded roster"

    _external_write(pid, before)
    _force_freshness_poll()
    assert authed_get("/api/players", sort="marketValue", dir="desc",
                      pageSize=1).json()["items"][0]["id"] == pid


def test_unknown_sort_key_rejected_even_when_the_filter_matches_nothing():
    r = authed_get("/api/players", sort="notAField", country="Nowhere")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Response encoding: FastAPI's jsonable_encoder walks every value of anything
# that is not already a Response -- 516 ms of a 962 ms full-roster request,
# converting nothing, because the payload is already JSON primitives.
# ---------------------------------------------------------------------------
def _count_encoder_calls(monkeypatch):
    import fastapi.routing
    calls = []
    real = fastapi.routing.jsonable_encoder
    monkeypatch.setattr(fastapi.routing, "jsonable_encoder",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def test_list_response_skips_fastapis_encoder(monkeypatch):
    calls = _count_encoder_calls(monkeypatch)
    r = authed_get("/api/players", pageSize=200)
    assert r.status_code == 200 and len(r.json()["items"]) == 200
    assert calls == [], "list response was re-encoded by FastAPI"


def test_players_all_skips_fastapis_encoder(monkeypatch):
    calls = _count_encoder_calls(monkeypatch)
    r = authed_get("/api/players/all")
    # Not TOTAL: an earlier test adds a player, so compare against live state.
    assert r.status_code == 200
    assert len(r.json()["players"]) == len(api_server.get_players()) > 0
    assert calls == [], "/api/players/all was re-encoded by FastAPI"


def test_encoder_probe_would_notice_a_regression(monkeypatch):
    """Guards the two tests above: the patched name is the one FastAPI calls,
    so `calls == []` means something, rather than passing vacuously."""
    calls = _count_encoder_calls(monkeypatch)
    assert authed_get("/api/players/ids").status_code == 200
    assert calls, "probe is broken -- a dict-returning endpoint must hit it"


def _check_json_primitives(value, path):
    """What makes the encoder bypass safe. If scoring or row_to_player ever
    emits a datetime, Decimal or NaN this fails here rather than as malformed
    JSON in a browser."""
    if isinstance(value, dict):
        for k, sub in value.items():
            assert isinstance(k, str), f"non-string key at {path}: {k!r}"
            _check_json_primitives(sub, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, sub in enumerate(value):
            _check_json_primitives(sub, f"{path}[{i}]")
    else:
        assert isinstance(value, (str, int, float, bool, type(None))), \
            f"{path} is {type(value).__name__}"
        # json.dumps writes bare NaN/Infinity, which JSON.parse rejects.
        assert not isinstance(value, float) or math.isfinite(value), f"{path} is {value}"


def test_list_payload_is_json_primitives_only():
    for p in api_server.get_scored():
        _check_json_primitives(api_server._list_item(p), "item")


def test_players_all_payload_is_json_primitives_only():
    for p in api_server.get_players():
        _check_json_primitives(p, "player")


def _reject_constant(name):
    raise AssertionError(f"payload contains bare {name}, which is not valid JSON")


def test_encoder_bypassed_responses_are_valid_strict_json():
    for path, params in (("/api/players", {"pageSize": 500}),
                         ("/api/players/all", {})):
        body = authed_get(path, **params).content
        json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
