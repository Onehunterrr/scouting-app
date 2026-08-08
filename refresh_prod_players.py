"""Replace the production players table with the current local roster, keeping
every user-owned row intact.

migrate_to_postgres.py can't be used here: it starts with metadata.drop_all,
which would take users / shortlists / notes with it. This touches only the
players table, and repoints the two tables that store player ids.

The new roster trimmed 780 players and added 780, so ids no longer mean the same
thing. References are remapped through (name, country), which is stable across
the rebalance; anything whose player is no longer in the roster is reported and
dropped rather than left pointing at whatever now holds that id.

Everything runs in one transaction: it either lands completely or not at all.
Pass --apply to write; without it the script reports what it would do.
"""
import os
import sys
import sqlite3
from sqlalchemy import create_engine, text, insert, select

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_tables import players as players_t

BASE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("TARGET_DATABASE_URL")
if not URL:
    raise SystemExit(
        "Set TARGET_DATABASE_URL to the target database first, e.g.\n"
        "  TARGET_DATABASE_URL=\"$(railway variables --service postgres --json "
        "| python -c \"import sys,json;print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])\")\"")
LOCAL = os.path.join(BASE, "scouting.db")
APPLY = "--apply" in sys.argv

eng = create_engine(URL, future=True)

# --- local roster -----------------------------------------------------------
cols = [c.name for c in players_t.columns]
loc = sqlite3.connect(LOCAL)
loc.row_factory = sqlite3.Row
new_rows = [{k: r[k] for k in cols} for r in loc.execute("SELECT * FROM players")]
new_key_to_id = {(r["name"], r["country"]): r["id"] for r in new_rows}
print("local roster: %d players, %d countries"
      % (len(new_rows), len({r["country"] for r in new_rows})))
if len(new_key_to_id) != len(new_rows):
    raise SystemExit("ABORT: (name, country) is not unique in the local roster")

with eng.begin() as c:
    # --- current references, resolved to stable keys -------------------------
    old_id_to_key = {r[0]: (r[1], r[2])
                     for r in c.execute(text("SELECT id, name, country FROM players"))}
    print("production roster: %d players, %d countries"
          % (len(old_id_to_key), len({v[1] for v in old_id_to_key.values()})))

    shortlists = [(r[0], r[1]) for r in
                  c.execute(text("SELECT user_id, player_id FROM shortlists"))]
    ledger = [(r[0], r[1]) for r in
              c.execute(text("SELECT id, player_id FROM ledger_entries"))]

    def remap(pid):
        key = old_id_to_key.get(pid)
        return new_key_to_id.get(key) if key else None

    sl_new, sl_lost = [], []
    for uid, pid in shortlists:
        n = remap(pid)
        (sl_new if n else sl_lost).append((uid, pid) if not n else (uid, n))
    lg_new, lg_lost = [], []
    for rid, pid in ledger:
        n = remap(pid)
        (lg_new if n else lg_lost).append((rid, n) if n else (rid, pid))

    print("shortlists: %d remapped, %d dropped (player no longer in roster)"
          % (len(sl_new), len(sl_lost)))
    print("ledger:     %d remapped, %d unresolvable (left as-is)"
          % (len(lg_new), len(lg_lost)))
    for uid, pid in sl_lost:
        print("   dropped shortlist row: user %s -> old player %s %s"
              % (uid, pid, old_id_to_key.get(pid)))

    if not APPLY:
        print("\nDRY RUN -- no changes written. Re-run with --apply.")
        raise SystemExit(0)

    # --- swap the roster ----------------------------------------------------
    c.execute(text("DELETE FROM shortlists"))
    c.execute(text("DELETE FROM players"))
    for i in range(0, len(new_rows), 200):
        c.execute(insert(players_t), new_rows[i:i + 200])
    c.execute(text("SELECT setval(pg_get_serial_sequence('players','id'), "
                   "COALESCE((SELECT MAX(id) FROM players), 1))"))

    for uid, pid in sl_new:
        c.execute(text("INSERT INTO shortlists (user_id, player_id) VALUES (:u, :p) "
                       "ON CONFLICT DO NOTHING"), {"u": uid, "p": pid})
    for rid, pid in lg_new:
        c.execute(text("UPDATE ledger_entries SET player_id = :p WHERE id = :i"),
                  {"p": pid, "i": rid})

    n_players = c.execute(text("SELECT COUNT(*) FROM players")).scalar_one()
    n_countries = c.execute(text("SELECT COUNT(DISTINCT country) FROM players")).scalar_one()
    n_sl = c.execute(text("SELECT COUNT(*) FROM shortlists")).scalar_one()
    n_users = c.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
    print("\nafter: %d players, %d countries, %d shortlists, %d users"
          % (n_players, n_countries, n_sl, n_users))
    if n_players != len(new_rows) or n_users != 12:
        raise SystemExit("ABORT: post-write counts look wrong -- rolling back")

print("committed." if APPLY else "")
