"""Daily roster refresh, run against the live database.

Where weekly_update.py works on the repo's local files (players_current.json,
scouting.db) and rebuilds the workbook and the frontend, this one touches the
players table of a running deployment and nothing else. It is what the
daily-update GitHub Actions workflow invokes.

Each run:
  1. Drifts every existing player's stats by roughly one day of match activity
     and stamps last_updated with today's date.
  2. Inserts NEW_PLAYERS_PER_DAY new players, drawn on the same EUR 80k-120k
     band as the rest of the roster.

What it deliberately does NOT do:
  - touch users, shortlists, notes or ledger_entries. Only the players table is
    written, and existing rows are updated in place, so player ids stay stable
    and nobody's shortlist breaks. (refresh_prod_players.py is the script for a
    full roster swap; that one has to remap references and is not safe to run
    unattended.)
  - rewrite anything in the repo. The committed players_current.json is the
    seed for a fresh deploy, not a mirror of production.

Cadence: the drift rates are per-run, scaled for 24h. weekly_update.py's
defaults assume a week between runs; firing those every day would hand every
player seven matches a week and inflate minutes past anything believable.

Usage:
    DATABASE_URL=postgresql://...  python daily_update.py           # dry run
    DATABASE_URL=postgresql://...  python daily_update.py --apply

Everything runs in one transaction: it either lands completely or not at all.
"""
import datetime
import os
import random
import sys

from sqlalchemy import create_engine, text, insert

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import player_gen
from db_schema import FIELD_MAP
from db_tables import players as players_t
from weekly_update import refresh_player

# Per-day rates. A player at this level plays roughly twice a week, so the
# chance of having played on any given day is ~2/7. The agent-pickup rate is
# weekly_update's 4%/week spread over seven days.
PLAY_CHANCE = 0.28
AGENT_CHANCE = 0.006
NEW_PLAYERS_PER_DAY = int(os.environ.get("NEW_PLAYERS_PER_DAY", "2"))

URL = os.environ.get("DATABASE_URL")
if not URL:
    raise SystemExit(
        "Set DATABASE_URL to the database to refresh, e.g.\n"
        "  DATABASE_URL=\"$(railway variables --service postgres --json "
        "| python -c \"import sys,json;print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])\")\"")
APPLY = "--apply" in sys.argv

# FIELD_MAP pairs the JSON/JS key with the SQL column name.
JSON_TO_SQL = {json_key: sql_name for json_key, sql_name in FIELD_MAP}
SQL_TO_JSON = {sql_name: json_key for json_key, sql_name in FIELD_MAP}

engine = create_engine(URL, future=True)
today = datetime.date.today().isoformat()
rng = random.Random()

with engine.begin() as conn:
    cols = ", ".join(["id"] + [s for _, s in FIELD_MAP])
    rows = conn.execute(text("SELECT %s FROM players ORDER BY id" % cols)).mappings().all()
    if not rows:
        raise SystemExit("ABORT: the players table is empty -- refusing to refresh nothing")
    print("roster before: %d players" % len(rows))

    # --- 1. drift the existing roster ---------------------------------------
    updates, played = [], 0
    for row in rows:
        p = {SQL_TO_JSON[k]: v for k, v in row.items() if k in SQL_TO_JSON}
        before = p["minutes"]
        refresh_player(p, today, rng, play_chance=PLAY_CHANCE, agent_chance=AGENT_CHANCE)
        if p["minutes"] != before:
            played += 1
        updates.append({"_id": row["id"],
                        **{JSON_TO_SQL[k]: v for k, v in p.items() if k in JSON_TO_SQL}})
    print("drifted:       %d players, %d of them played today" % (len(updates), played))

    # --- 2. add new players --------------------------------------------------
    used_names = {row["name"] for row in rows}
    existing_keys = {(row["name"], row["country"]) for row in rows}
    fresh = player_gen.generate_players(NEW_PLAYERS_PER_DAY, used_names, today, rng=rng)
    fresh = [p for p in fresh if (p["name"], p["country"]) not in existing_keys]
    new_rows = [{JSON_TO_SQL[k]: v for k, v in p.items() if k in JSON_TO_SQL} for p in fresh]
    print("new players:   %d" % len(new_rows))
    for p in fresh:
        print("   + %s (%s, %s, %s) EUR %s"
              % (p["name"], p["country"], p["position"], p["age"],
                 f"{p['marketValue']:,}" if p["marketValue"] else "not on record"))

    if not APPLY:
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        raise SystemExit(0)

    stmt = text("UPDATE players SET %s WHERE id = :_id"
                % ", ".join("%s = :%s" % (s, s) for _, s in FIELD_MAP))
    for i in range(0, len(updates), 500):
        conn.execute(stmt, updates[i:i + 500])
    if new_rows:
        conn.execute(insert(players_t), new_rows)

    total = conn.execute(text("SELECT COUNT(*) FROM players")).scalar_one()
    stamped = conn.execute(text("SELECT COUNT(*) FROM players WHERE last_updated = :d"),
                           {"d": today}).scalar_one()
    below = conn.execute(
        text("SELECT COUNT(*) FROM players WHERE market_value > 0 AND market_value < :lo"),
        {"lo": player_gen.VALUE_BAND_LO}).scalar_one()

    # A refresh that loses players, or quietly stops stamping, is worse than one
    # that fails loudly -- the workflow surfaces the non-zero exit.
    if total != len(rows) + len(new_rows):
        raise SystemExit("ABORT: expected %d players, found %d -- rolling back"
                         % (len(rows) + len(new_rows), total))
    if stamped != total:
        raise SystemExit("ABORT: only %d of %d rows carry today's date -- rolling back"
                         % (stamped, total))
    if below:
        raise SystemExit("ABORT: %d players fell below the EUR %s floor -- rolling back"
                         % (below, f"{player_gen.VALUE_BAND_LO:,}"))

print("\nroster after:  %d players, all stamped %s" % (total, today))
print("committed.")
