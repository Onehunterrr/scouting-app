"""One-time / re-runnable import: players_current.json -> scouting.db (SQLite).
Idempotent: drops and recreates the players table each run, so this is safe
to re-run after a JSON change until the DB is the sole source of truth."""
import json, sqlite3, sys
from db_schema import SCHEMA_SQL, FIELD_MAP, UNDERVALUED_VIEW_SQL

JSON_FILE = sys.argv[1] if len(sys.argv) > 1 else "players_current.json"
DB_FILE = sys.argv[2] if len(sys.argv) > 2 else "scouting.db"

players = json.load(open(JSON_FILE))

conn = sqlite3.connect(DB_FILE)
conn.execute("DROP TABLE IF EXISTS players")
conn.executescript(SCHEMA_SQL)

cols = [sql_col for _, sql_col in FIELD_MAP]
placeholders = ",".join("?" for _ in cols)
insert_sql = f"INSERT INTO players ({','.join(cols)}) VALUES ({placeholders})"

rows = []
for p in players:
    rows.append(tuple(p.get(json_key) for json_key, _ in FIELD_MAP))

conn.executemany(insert_sql, rows)
conn.executescript(UNDERVALUED_VIEW_SQL)
conn.commit()

count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
by_pos = conn.execute("SELECT position, COUNT(*) FROM players GROUP BY position ORDER BY position").fetchall()
idx_count = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0]
print(f"imported {count} players into {DB_FILE}, {idx_count} indexes created")
print("by position:", by_pos)

# quick sanity query exercising the view + an indexed filter, to prove the
# "complex query pattern" claim isn't just theoretical
sample = conn.execute("""
    SELECT name, position, country, undervalued_score, flag
    FROM player_scores
    WHERE has_agent = 'No'
    ORDER BY undervalued_score DESC
    LIMIT 5
""").fetchall()
print("top 5 unrepresented by undervalued score (via SQL view):")
for row in sample:
    print(" ", row)

conn.close()
