"""Export scouting.db (SQLite, now the canonical store) back to
players_current.json, so the existing build_xlsx_v3.py / build_html.py /
gen_test.py pipeline keeps working unchanged. SQLite is the source of
truth going forward; JSON is a generated view of it, not the other way
around."""
import json, sqlite3, sys
from db_schema import FIELD_MAP

DB_FILE = sys.argv[1] if len(sys.argv) > 1 else "scouting.db"
JSON_FILE = sys.argv[2] if len(sys.argv) > 2 else "players_current.json"

conn = sqlite3.connect(DB_FILE)
conn.row_factory = sqlite3.Row
cols = [sql_col for _, sql_col in FIELD_MAP]
rows = conn.execute(f"SELECT {','.join(cols)} FROM players ORDER BY id").fetchall()

players = []
for row in rows:
    p = {}
    for json_key, sql_col in FIELD_MAP:
        v = row[sql_col]
        # SQLite returns REAL columns as float even for whole numbers;
        # keep the JSON output looking the same as before (ints stay ints).
        if sql_col in ("pass_completion_pct", "sweeper_actions"):
            v = round(v, 1) if v is not None else 0
        p[json_key] = v
    players.append(p)

with open(JSON_FILE, "w") as f:
    json.dump(players, f, indent=2)

print(f"exported {len(players)} players from {DB_FILE} -> {JSON_FILE}")
conn.close()
