# Acquirability Score + Deal Score — committed design spec (Fable, 2026-07-27)

Status: DESIGN COMPLETE, implementation queued. Implement in scoring.py, the JS in
build_html.py, and the player_scores SQL view, with the same bit-exact parity harness
used for the scoring rework (scratchpad extract_js.py / run_js.js). Add fields
acquirabilityScore, dealScore, hotProspect; leave the existing flag field untouched.

## Constants

```
SEASON_YEAR            = 2026     # "now" for contract math; fixed constant, NEVER a clock call
CONTRACT_URGENCY_0     = 1.00     # expires this summer: free-transfer territory
CONTRACT_URGENCY_1     = 0.75     # final year: club sells at a discount or loses the player free
CONTRACT_URGENCY_2     = 0.35     # two years left: club holds the cards
CONTRACT_URGENCY_3     = 0.15     # locked down; low not zero because buyouts happen
CONTRACT_URGENCY_NULL  = 0.35     # contractExpires missing -> assume mid-contract
RESIGN_AGE_LO          = 20.0     # at/below: clubs fight to re-sign expiring prospects
RESIGN_AGE_SPAN        = 3.0      # ramp 20 -> 23
RESIGN_FLOOR           = 0.75     # U20 contract urgency haircut
REP_NO                 = 1.00     # unrepresented: the product wedge
REP_UNKNOWN            = 0.70     # near base-rate expectation minus wasted-call risk
REP_YES                = 0.35     # agent = gatekeeper + fee inflation; penalised, not vetoed
FEE_REF                = 10000.0  # dataset floor: €10k fee -> feasibility 1.0
FEE_EXP                = 0.35     # each price doubling cuts feasibility ~21.5%; €500k -> 0.254
LS_MAX                 = 1.000    # tier-2 league strength
LS_SPAN                = 0.267    # LS_MAX - 0.733 (tier 4)
LEAGUE_MULT_FLOOR      = 0.85     # tier-2 seller leverage costs at most 15%
MINOR_GATE             = 0.40     # age<18: FIFA RSTP Art.19 bars international transfers
W_CONTRACT             = 0.45
W_REP                  = 0.30
W_FEE                  = 0.25
DEAL_EXP_UV            = 0.60
DEAL_EXP_ACQ           = 0.40
HOT_DEAL_THRESHOLD     = 55
```

## Formulas (per-row; only +,-,*,/,exp,pow,min,max,CASE — SQL-portable)

```
yearsRemaining = min(3, max(0, contractExpires - SEASON_YEAR))   # null -> C = 0.35
C   = {0:1.00, 1:0.75, 2:0.35, 3:0.15}[yearsRemaining]
# non-linear: the real cliff is the final-year boundary (1.00/0.75 vs 0.35/0.15)

R = RESIGN_FLOOR + (1-RESIGN_FLOOR) * min(1, max(0, (age - 20.0) / 3.0))
# 17-20 -> 0.75, 21 -> 0.833, 22 -> 0.917, 23+ -> 1.0. Applied to C always (inert
# when C small). Broader age-upside term REJECTED: already priced into uv.
contractComp = C * R

Rep = {No: 1.00, Yes: 0.35, Unknown: 0.70}[hasAgent]

vEff = max(coalesce(displayMarketValue, 79000), 10000)
Fee  = pow(10000 / vEff, 0.35)          # log-linear; €10k->1.0, €79k->0.485, €500k->0.254

invLS = min(1, max(0, (1.000 - leagueStrength) / 0.267))   # tier2->0, 3->0.538, 4->1
Lmult = 0.85 + 0.15 * invLS                                # 0.85 / 0.931 / 1.00

minorGate = age < 18 ? 0.40 : 1.0

acquirabilityScore = 100 * pow(contractComp, 0.45) * pow(Rep, 0.30) * pow(Fee, 0.25)
                         * Lmult * minorGate
# MULTIPLICATIVE (weighted geometric mean): blockers compound. Range ~[6.6, 100].

U = max(0, undervaluedScore) / 100
dealScore = 100 * pow(U, 0.60) * pow(acquirabilityScore / 100, 0.40)
# uv <= 0 -> dealScore = 0 exactly. Not gameable by one component
# (Acq=100,uv=+5 -> 16.5; uv=+100,Acq=6.6 -> 33.7; bar is 55).

hotProspect = dealScore >= 55 AND NOT lowSample
# dealScore>=55 implies uv>=37 even at Acq=100, so Hot Prospect is a refinement
# subset of High Priority/Watchlist, never a contradiction. flag field untouched.
```

## Worked examples (verify implementation against these)

(a) age 20, no agent, expires 2026, €25k, uv +55, tier 3:
    contractComp=0.75, Rep=1.00, Fee=0.7256, Lmult=0.9307 -> **Acq=75.5, Deal=62.4, HOT**
(b) age 24, agent Yes, expires 2029, €140k, uv +10, tier 2:
    contractComp=0.15, Rep=0.35, Fee=0.3971, Lmult=0.85 -> **Acq=21.0, Deal=13.4**
(c) age 19, agent Unknown, expires 2027, €80k, uv −20, tier 2:
    contractComp=0.5625, Rep=0.70, Fee=0.4830, Lmult=0.85 -> **Acq=49.1, Deal=0** (uv<0)

## Edge cases
- contractExpires null -> C=0.35 + note "Contract year unknown — assumed two years remaining"
- value null/0 -> vEff falls back to 79000 (median), never the flattering 10k floor
- lowSample: scores still compute; hotProspect blocked; reuse estimated-value note
- uv <= 0 -> dealScore exactly 0

## Explain templates
Drivers keyed contractComp / repComp / feeComp / leagueFriction:
- yrs 0: "Contract expires this window ({year}) — free-transfer territory"
  yrs 1: "Final contract year (expires {year}) — selling club under pressure"
  yrs 2: "Two years remaining ({year}) — club holds leverage"
  yrs 3: "Under contract to {year} — locked down"
- age<=20 & yrs<=1: "Age {age}: club likely to fight re-signing"
  age 17: "Under 18 — international transfer barred (FIFA Art. 19); domestic route only"
- Rep No: "Unrepresented — direct club/federation route"
  Unknown: "Representation unknown — likely approachable at this level"
  Yes: "Agented — approach runs through representation"
- Fee: "Est. fee €{value} — {high|moderate|low} feasibility" (>=0.65 / 0.40-0.65 / <0.40)
- League: "Tier {tier} seller — {little|moderate|strong} holding leverage" (invLS >=0.67 / 0.33-0.67 / <0.33)
Summary: "Deal {dealScore}: {uvPhrase} (UV {uv:+d}) × {acqPhrase} ({acq}/100) — {topDriver}."
uv<=0: "Deal 0: not undervalued (UV {uv:+d}) — excluded from call list regardless of acquirability."

## Expected distribution
~2.7% of roster (≈130-140 players) at Hot Prospect; stays in the 2-8% usable band
even under fatter uv tails.

## Implementation checklist
1. scoring.py: constants + acquirability_score(p) + deal_score(p) + explain drivers/notes,
   computed in pass 5 after undervaluedScore.
2. build_html.py JS: identical port + Deal Score column (sortable) + modal chips section.
3. db_schema.py view: same expressions in SQL (CASE for the year buckets & rep).
4. api_server.py: add dealScore/acquirabilityScore to the sort whitelist if sorts are whitelisted.
5. Parity harness (scratchpad extract_js.py / run_js.js): extend the field list with
   acquirabilityScore, dealScore, hotProspect, explain.summary; require bit-exact.
6. Rebuild view in scouting.db, python build_html.py, pytest. Verify examples (a)(b)(c) by hand.
