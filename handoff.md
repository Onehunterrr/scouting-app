# Handoff — ScoutEdge

## Scoring / market-value rework: COMPLETE and verified (commits 2cb4291, 308b94c)

All seven requested changes shipped, plus two bugs found while measuring.
Verified: Python vs JS **bit-exact** across 5,000 players and 16 fields; SQL view
matches Python (estimates identical, zero flag mismatches, max undervalued diff
0.05 vs a 0.1 tolerance); **pytest 29 passed**; frontend rebuilt (index.html and
Scouting_App_Prototype.html now both written by build_html.py).

Key calibration constants in scoring.py (mirrored in build_html.py):
SHRINK_K=900, LOW_SAMPLE_MINUTES=900, TIER_DECAY=0.155, VALUE_MEDIAN=79000,
VALUE_SPREAD=3.15, VALUE_BASE_REF=0.490347, POS_REF_MULT=1.8, floor 10k, ceil 500k.
Estimates land at med 79,000 / p90 168,510 / p99 307,000 / max 489,000.
Unknown-value share of High Priority went 58% -> 8% (roster baseline 25%).

If the roster changes, re-tune VALUE_BASE_REF and refresh DEFAULT_POS_REFS
(bisect until median(estimate)==79000); everything else self-adjusts.

## Acquirability + Deal Score: COMPLETE and verified

Implemented per acquirability_spec.md (design by Fable subagent). Python/JS parity
bit-exact over 5,000 players (max diff 3.6e-15); SQL view matches (max 0.05 = 1dp
rounding, 241/241 hot prospects agree); spec worked examples pinned in
test_acquirability_spec_examples; pytest 31 passed; frontend rebuilt with Deal box
in the modal. New fields: acquirabilityScore, dealScore, hotProspect,
contractYearsRemaining, dealExplain — all API-sortable automatically.
241 Hot Prospects (4.8%, target band 2-8%).

## Central European roster expansion: COMPLETE and verified

Added Poland, Czechia, Slovakia, Hungary, Austria, Switzerland (130 each; 38
countries, roster held at exactly 5,000 via proportional trim of 780 players,
seeded/reproducible in roster_rebalance_2026.py). Safety pre-check confirmed the
shipped scouting.db has no user tables, so no shortlists/notes could break.
AT/CH generate with the WELL_SCOUTED skew (tiers 3-4, more agents) -- only 11 of
237 Hot Prospects are AT/CH. Recalibrated: VALUE_BASE_REF 0.490347 -> 0.488432,
DEFAULT_POS_REFS refreshed, all three copies (Python/JS/SQL) updated.
Verified: PY-JS parity 0 mismatches on the new roster; median estimate 79,000;
237 Hot Prospects (4.7%); pytest 31 passed; DB + both HTML files rebuilt.

## Resume

Run `/clear`, then: "continue from handoff.md".
