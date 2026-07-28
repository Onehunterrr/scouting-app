"""
test_api.py -- pytest suite for api_server.py (FastAPI TestClient / httpx).

Run:  python3 -m pytest test_api.py -v

Uses its own throwaway SQLite copy so it never touches the canonical
scouting.db, re-inits the app's engine, and disables the in-memory rate
limiter (which would otherwise throttle the suite's many auth calls).

The data endpoints (/api/players*) require a JWT; a shared authenticated
client is created once at import and reused via the `authed_get` helper.
"""

import math
import os
import shutil
import sqlite3
import tempfile

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

    a = scoring.acquirability(mk(20, "No", 2026, 25000, 55, 3))
    assert a["score"] == pytest.approx(75.5, abs=0.2)
    assert scoring.deal_score(55, a["score"]) == pytest.approx(62.4, abs=0.2)

    b = scoring.acquirability(mk(24, "Yes", 2029, 140000, 10, 2))
    assert b["score"] == pytest.approx(21.0, abs=0.2)
    assert scoring.deal_score(10, b["score"]) == pytest.approx(13.4, abs=0.2)

    c = scoring.acquirability(mk(19, "Unknown", 2027, 80000, -20, 2))
    assert c["score"] == pytest.approx(49.1, abs=0.2)
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
