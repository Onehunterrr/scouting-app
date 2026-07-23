"""
One-time migration:
  1. Backfill the new GK-specific fields (saves, goalsConceded, passCompletionPct,
     sweeperActions, cleanSheets) onto every existing player -- 0 for outfield
     players, freshly generated (scaled off their existing minutes) for GKs,
     since those fields didn't exist in the schema before today.
  2. Expand the roster from its current size up to 1000 players using the same
     generator (and therefore the same name pools / schema) as the weekly
     growth job, so the bulk-added players are indistinguishable in structure
     from organically-added ones.

Safe to run once. Not part of the weekly cadence -- that stays at +10/week
from whatever size this leaves the database at.
"""
import json, random, sys
import player_gen

TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
DATA_FILE = "players_current.json"
today = sys.argv[2] if len(sys.argv) > 2 else "2026-07-17"

random.seed(4242)  # deterministic, separate stream from the original seed=42 baseline

players = json.load(open(DATA_FILE))
before = len(players)

backfilled = 0
for p in players:
    if "saves" in p:
        continue
    backfilled += 1
    stats = player_gen.gen_stats(p["position"], p["minutes"])
    # keep existing goals/assists/progPasses/progCarries/tklInt untouched for
    # continuity (those were already valid for this player); only add the new
    # GK-specific fields, which genuinely did not exist before.
    p["saves"] = stats["saves"]
    p["goalsConceded"] = stats["goalsConceded"]
    p["passCompletionPct"] = stats["passCompletionPct"]
    p["sweeperActions"] = stats["sweeperActions"]
    p["cleanSheets"] = stats["cleanSheets"]

used_names = {p["name"] for p in players}
to_add = max(0, TARGET - len(players))
new_players = player_gen.generate_players(to_add, used_names, today, rng=random)
players.extend(new_players)

# --- basic validation / dedup pass ---
seen = set()
dupes = []
for p in players:
    key = (p["name"], p["country"])
    if key in seen:
        dupes.append(key)
    seen.add(key)
required = ["name","country","position","age","minutes","tier","marketValue","hasAgent","dateAdded","lastUpdated"]
missing_fields = [p["name"] for p in players if any(f not in p for f in required)]

with open(DATA_FILE, "w") as f:
    json.dump(players, f, indent=2)

print(f"before={before}, backfilled_gk_fields={backfilled}, added={len(new_players)}, total={len(players)}")
print(f"validation: {len(dupes)} duplicate (name,country) pairs, {len(missing_fields)} players missing required fields")
if dupes:
    print("duplicates:", dupes[:10])
if missing_fields:
    print("missing-field players:", missing_fields[:10])
