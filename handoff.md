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

## QUEUED (new request, not started) — Central European roster coverage

User: *"I want my app to be a lot more central european players not just from the balkans."*
Correct diagnosis — the current 32 countries have **six Balkan** (Albania, Croatia, Montenegro,
North Macedonia, Serbia, Slovenia) and **zero true Central European** ones.

**Decisions the user already made (do not re-ask):**

- **Add six countries:** Poland, Czechia, Slovakia, Hungary, **Austria, Switzerland**.
- **Rebalance to hold the roster at 5,000** — i.e. reduce over-represented countries to make room,
  rather than growing the total.

**Two caveats the user was shown and accepted — but which still need handling in code:**

1. Austrian/Swiss lower divisions are wealthier and already well-scouted, so those players are less
   likely to be genuinely unrepresented (the app's wedge). Consider skewing AT/CH toward tier 3–4
   and a lower `hasAgent = "No"` rate so they don't dominate the unrepresented flags.
2. **Rebalancing deletes existing players.** The app has per-user shortlists and notes keyed by
   player id (`api_server.py` accounts; check `db_tables.py` / `db_schema.py` for the shortlist and
   notes tables and their FK/cascade behaviour). **Before deleting anything, check whether those
   tables reference `players.id`** and either remap or accept orphaning deliberately. Do not
   silently break saved shortlists.

**Work involved** (all in `player_gen.py` unless noted): per-country first/last name pools,
`SOURCE_CITIES`, `COUNTRY_TLD`, `COUNTRY_FEDERATION`, plus `PATHWAYS` in `transfer_pathways.py`.
Then regenerate `players_current.json`, re-run `db_migrate.py`, rebuild the frontend.

**Ordering note (already checked):** this does NOT invalidate the scoring rework. `compute_scores`
derives the position reference points from the cohort at runtime, so they self-adjust. Only
`VALUE_BASE_REF` needs re-tuning, via the existing calibration script (bisects until
`median(estimate) == 79000`) — one command, not a redo. `DEFAULT_POS_REFS` is a standalone-call
fallback and should be refreshed at the same time.

**Recommended sequence:** finish the scoring work (steps 1–7 above) first so the parity/SQL/test
baseline is green against the current dataset, *then* do the roster change and re-calibrate.

## Resume

Run `/clear`, then: "continue from handoff.md".
