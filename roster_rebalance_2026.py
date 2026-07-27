"""
roster_rebalance_2026.py -- one-shot roster surgery (2026-07-27).

Adds six Central European countries (Poland, Czechia, Slovakia, Hungary,
Austria, Switzerland) while holding the roster at exactly 5,000, per the
decision recorded in handoff.md. Existing over-represented countries are
trimmed proportionally to make room; the trimmed players are chosen by a
seeded RNG so the operation is reproducible.

Safety note (checked before writing this): the shipped scouting.db contains
NO user tables (users/shortlists/notes are created at API startup), so
removing players cannot orphan anyone's saved data.

Austria/Switzerland generate with the WELL_SCOUTED skew in player_gen.py
(more tier 3-4, fewer "No agent") so they don't flood the unrepresented flags.

Run:  python roster_rebalance_2026.py
Then: python db_migrate.py && python build_html.py && re-run calibration.
"""

import json
import random

import player_gen

SEED = 20260727
DATE = "2026-07-27"
NEW_COUNTRIES = ["Poland", "Czechia", "Slovakia", "Hungary", "Austria", "Switzerland"]
PER_NEW_COUNTRY = 130           # 6 x 130 = 780 slots freed from the old roster
TOTAL = 5000

rng = random.Random(SEED)

players = json.load(open("players_current.json", encoding="utf-8"))
assert len(players) == TOTAL, f"expected {TOTAL}, got {len(players)}"

by_country = {}
for p in players:
    by_country.setdefault(p["country"], []).append(p)

# --- trim: proportional to size, largest countries give up the most ----------
to_free = PER_NEW_COUNTRY * len(NEW_COUNTRIES)
counts = {c: len(v) for c, v in by_country.items()}
quota = {c: round(n * to_free / TOTAL) for c, n in counts.items()}
# fix rounding drift onto the largest countries, deterministically
drift = to_free - sum(quota.values())
for c in sorted(counts, key=lambda c: (-counts[c], c)):
    if drift == 0:
        break
    step = 1 if drift > 0 else -1
    quota[c] += step
    drift -= step

removed = []
kept = []
for c in sorted(by_country):                       # sorted -> reproducible
    group = by_country[c]
    cut = set(id(x) for x in rng.sample(group, quota[c]))
    for p in group:
        (removed if id(p) in cut else kept).append(p)

# --- generate the new countries ---------------------------------------------
used_names = set(p["name"] for p in kept)
new_players = []
for c in NEW_COUNTRIES:
    new_players.extend(player_gen.generate_players(
        PER_NEW_COUNTRY, used_names, DATE, rng=rng, country=c))

roster = kept + new_players
assert len(roster) == TOTAL, len(roster)
rng.shuffle(roster)                                # avoid a new-countries block at the tail

json.dump(roster, open("players_current.json", "w", encoding="utf-8"), indent=1)

from collections import Counter
cc = Counter(p["country"] for p in roster)
print(f"removed {len(removed)}, added {len(new_players)}, total {len(roster)}")
print(f"countries: {len(cc)}  min/max per country: {min(cc.values())}/{max(cc.values())}")
for c in NEW_COUNTRIES:
    agents = Counter(p["hasAgent"] for p in roster if p["country"] == c)
    tiers = Counter(p["tier"] for p in roster if p["country"] == c)
    print(f"  {c}: {cc[c]} players, tiers {dict(sorted(tiers.items()))}, agents {dict(agents)}")
