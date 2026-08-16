"""Remap the roster's recorded market values onto the EUR 80k-120k band.

The old roster drew values uniformly from EUR 8k-150k, which left most of the
table below the level anyone would work a deal on. This moves the existing
players onto player_gen's band instead of regenerating them: regenerating would
mint new names, and refresh_prod_players.py keys production shortlists and
ledger rows on (name, country), so every saved shortlist would be dropped.

The remap is rank-preserving. Player i of n, sorted by their current value,
takes the i-th quantile of the new distribution -- so whoever is expensive
today is still expensive tomorrow, and only the scale moves. Players with no
value on record (market_value = 0) are left at 0; scoring.py's estimator fills
those in, and it is calibrated against this same band.

Usage:
    python rescale_market_values.py            # report only
    python rescale_market_values.py --apply    # rewrite JSON + SQLite
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from player_gen import band_quantile, VALUE_BAND_LO, VALUE_BAND_HI, VALUE_TAIL_HI

BASE = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(BASE, "players_current.json")
DB_FILE = os.path.join(BASE, "scouting.db")
APPLY = "--apply" in sys.argv


def build_mapping(values):
    """old value -> new value, rank-preserving.

    Ties collapse to a single new value so two players who are level today stay
    level. Quantiles use (i + 0.5) / n midpoints, so neither end of the band is
    over-represented by the rounding.
    """
    uniq = sorted({v for v in values if v and v > 0})
    n = len(uniq)
    if not n:
        return {}
    return {old: band_quantile((i + 0.5) / n) for i, old in enumerate(uniq)}


def describe(values, label):
    nz = sorted(v for v in values if v and v > 0)
    if not nz:
        print("  %-8s no recorded values" % label)
        return
    def pct(p):
        return nz[min(len(nz) - 1, int(len(nz) * p))]
    below = sum(1 for v in nz if v < VALUE_BAND_LO)
    inband = sum(1 for v in nz if VALUE_BAND_LO <= v <= VALUE_BAND_HI)
    print("  %-8s n=%d  min=%s  p25=%s  median=%s  p75=%s  max=%s"
          % (label, len(nz), f"{nz[0]:,}", f"{pct(.25):,}", f"{pct(.50):,}",
             f"{pct(.75):,}", f"{nz[-1]:,}"))
    print("           below %sk: %d (%.1f%%)   in band: %d (%.1f%%)   above: %d (%.1f%%)"
          % (VALUE_BAND_LO // 1000, below, 100 * below / len(nz),
             inband, 100 * inband / len(nz),
             len(nz) - below - inband, 100 * (len(nz) - below - inband) / len(nz)))


players = json.load(open(JSON_FILE, encoding="utf-8"))
old_values = [p.get("marketValue") or 0 for p in players]
mapping = build_mapping(old_values)
new_values = [mapping.get(v, 0) if v else 0 for v in old_values]

print("target band: EUR %s-%s, tail to EUR %s\n"
      % (f"{VALUE_BAND_LO:,}", f"{VALUE_BAND_HI:,}", f"{VALUE_TAIL_HI:,}"))
print("players_current.json (%d players, %d with nothing on record)"
      % (len(players), sum(1 for v in old_values if not v)))
describe(old_values, "before")
describe(new_values, "after")

# Rank preservation is the one property this script must not get wrong.
pairs = [(o, n) for o, n in zip(old_values, new_values) if o]
for (o1, n1), (o2, n2) in zip(sorted(pairs), sorted(pairs)[1:]):
    if o1 < o2 and n1 > n2:
        raise SystemExit("ABORT: remap inverted the order of %s and %s" % (o1, o2))
print("\nrank order preserved across all %d recorded values" % len(pairs))

if not APPLY:
    print("\nDRY RUN -- nothing written. Re-run with --apply.")
    raise SystemExit(0)

for p, v in zip(players, new_values):
    p["marketValue"] = v
with open(JSON_FILE, "w", encoding="utf-8") as fh:
    json.dump(players, fh, ensure_ascii=False)
print("\nwrote %s" % os.path.basename(JSON_FILE))

con = sqlite3.connect(DB_FILE)
with con:
    rows = con.execute("SELECT id, market_value FROM players").fetchall()
    con.executemany("UPDATE players SET market_value = ? WHERE id = ?",
                    [(mapping.get(v, 0) if v else 0, i) for i, v in rows])
lo, hi, zeros = con.execute(
    "SELECT MIN(NULLIF(market_value, 0)), MAX(market_value), "
    "SUM(CASE WHEN market_value = 0 THEN 1 ELSE 0 END) FROM players").fetchone()
con.close()
print("wrote %s -- recorded values now %s-%s, %d with nothing on record"
      % (os.path.basename(DB_FILE), f"{lo:,}", f"{hi:,}", zeros))
