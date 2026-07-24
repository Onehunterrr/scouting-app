"""
health_monitor.py -- scheduled Data Health SCAN + regression check.

Recomputes the same data-health grade the app's RevOps "SCAN" card shows
(missing market values, unknown representation, missing club contacts, stale
records, duplicate candidates) over the canonical dataset, and compares it
against a committed baseline (health_baseline.json).

Exit code 0 = healthy (grade held or improved); exit code 1 = regression, so a
scheduled CI run turns red and can open an issue. Run:

    python3 health_monitor.py                 # check against baseline
    python3 health_monitor.py --update-baseline   # write current grade as the new baseline
"""

import datetime
import json
import os
import sys
from collections import Counter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "players_current.json")
BASELINE_FILE = os.path.join(BASE_DIR, "health_baseline.json")
STALE_DAYS = 30
# a regression is a drop of more than this many completeness points vs. baseline
REGRESSION_TOLERANCE = 2


def _to_epoch(date_str):
    try:
        return datetime.datetime.strptime(date_str[:10], "%Y-%m-%d").timestamp()
    except (ValueError, TypeError):
        return None


def compute_health(players):
    n = len(players) or 1
    missing_mv = sum(1 for p in players if not p.get("marketValue") or p["marketValue"] <= 0)
    unknown_agent = sum(1 for p in players if p.get("hasAgent") == "Unknown")
    missing_email = sum(1 for p in players if not p.get("clubContactEmail"))

    stamps = [_to_epoch(p.get("lastUpdated")) for p in players]
    latest = max((s for s in stamps if s is not None), default=datetime.datetime.now().timestamp())
    stale = sum(1 for s in stamps if s is None or (latest - s) > STALE_DAYS * 86400)

    name_counts = Counter(p.get("name") for p in players)
    dupe_records = sum(c for c in name_counts.values() if c > 1)

    dims = [
        1 - missing_mv / n,
        1 - unknown_agent / n,
        1 - missing_email / n,
        1 - stale / n,
        1 - dupe_records / n,
    ]
    score = round(sum(dims) / len(dims) * 100)
    grade = "A" if score >= 85 else ("B" if score >= 70 else "C")
    return {
        "records": n, "score": score, "grade": grade,
        "missingMarketValue": missing_mv, "unknownRepresentation": unknown_agent,
        "missingClubContact": missing_email, "stale": stale, "duplicateCandidates": dupe_records,
    }


def _emit(line, summary_lines):
    print(line)
    summary_lines.append(line)


def main():
    update = "--update-baseline" in sys.argv
    with open(DATA_FILE, encoding="utf-8") as f:
        players = json.load(f)
    h = compute_health(players)

    if update:
        with open(BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump({"score": h["score"], "grade": h["grade"],
                       "updated": datetime.date.today().isoformat()}, f, indent=2)
        print(f"Baseline updated to {h['grade']} / {h['score']}%")
        return 0

    summary = []
    _emit("## ScoutEdge Data Health SCAN", summary)
    _emit(f"- **Grade:** {h['grade']} / {h['score']}%  ({h['records']:,} records scanned)", summary)
    _emit(f"- Missing market value: {h['missingMarketValue']:,}", summary)
    _emit(f"- Unknown representation: {h['unknownRepresentation']:,}", summary)
    _emit(f"- Missing club contact: {h['missingClubContact']:,}", summary)
    _emit(f"- Stale (>{STALE_DAYS}d): {h['stale']:,}", summary)
    _emit(f"- Duplicate candidates: {h['duplicateCandidates']:,}", summary)

    regression = False
    if os.path.exists(BASELINE_FILE):
        with open(BASELINE_FILE, encoding="utf-8") as f:
            base = json.load(f)
        delta = h["score"] - base["score"]
        _emit(f"- Baseline: {base['grade']} / {base['score']}%  (delta {delta:+d} pts)", summary)
        if delta < -REGRESSION_TOLERANCE:
            regression = True
            _emit(f"\n**REGRESSION**: completeness dropped {-delta} points below baseline.", summary)
    else:
        _emit("- No baseline yet — run with --update-baseline to set one.", summary)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write("\n".join(summary) + "\n")

    if regression:
        print("\nData health regression detected.", file=sys.stderr)
        return 1
    print("\nData health OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
