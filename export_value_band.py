"""One-off: export every player whose *displayed* market value (recorded, or the
model's estimate when nothing is on record) falls inside a value band.

Runs the real scoring pipeline via api_server.get_scored(), so the numbers match
exactly what the app shows -- no reimplementation to drift out of step.

Usage (point it at the roster you want; without DATABASE_URL it falls back to a
throwaway sqlite under /tmp and will NOT be the roster you think it is):

    DATABASE_URL="sqlite:///./scouting.db" python export_value_band.py 60000 120000

Reads only, but importing api_server runs init_db(), which creates the app's
auth/shortlist tables in the target database if they aren't there yet.
"""
import csv
import os
import sys

os.environ.setdefault("SKIP_WARMUP", "1")
sys.path.insert(0, os.getcwd())  # run from the repo root

import api_server  # noqa: E402  (init_db() runs on import)

LO = int(sys.argv[1]) if len(sys.argv) > 1 else 60_000
HI = int(sys.argv[2]) if len(sys.argv) > 2 else 120_000
OUT = sys.argv[3] if len(sys.argv) > 3 else f"players_{LO//1000}k_{HI//1000}k.csv"

COLUMNS = [
    "id", "name", "position", "age", "country", "league", "tier", "club",
    "displayMarketValue", "marketValueEstimated", "marketValue",
    "undervaluedScore", "flag", "acquirabilityScore", "dealScore",
    "hasAgent", "contractExpires", "minutes", "systemFit",
    "clubContactEmail", "contactRoute",
]

scored = api_server.get_scored()
band = [p for p in scored if LO <= (p.get("displayMarketValue") or 0) <= HI]
band.sort(key=lambda p: p.get("undervaluedScore") or 0, reverse=True)

with open(OUT, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(COLUMNS)
    for p in band:
        row = []
        for c in COLUMNS:
            v = p.get(c, "")
            if c == "undervaluedScore" and isinstance(v, (int, float)):
                v = f"{v:.1f}"
            elif c == "marketValueEstimated":
                v = "yes" if v else "no"
            row.append(v)
        w.writerow(row)

est = sum(1 for p in band if p.get("marketValueEstimated"))
print(f"roster           {len(scored)}")
print(f"in {LO:,}-{HI:,}  {len(band)}  ({est} estimated, {len(band)-est} recorded)")
print(f"high priority    {sum(1 for p in band if p['flag'].startswith('High Priority'))}")
print(f"unrepresented    {sum(1 for p in band if p['hasAgent'] == 'No')}")
print(f"wrote            {OUT}")
