# ScoutEdge

*Platform Documentation: Features, Data, and Scouting Models*

Prepared July 27, 2026

## 1. Executive Summary

ScoutEdge is a football (soccer) talent-scouting analytics platform focused on lower-division players (competitive tiers 2–4) across 38 countries, with an emphasis on Central Europe, the Balkans, South America, Africa, Asia, and CONCACAF. The platform identifies players whose statistical output exceeds what the market currently pays for them, evaluates how realistically each player could be acquired, and explains every recommendation in plain language. It operates as a web application backed by a REST API, with a fully offline-capable single-file frontend, secure user accounts, and a paid subscription tier.

## 2. Application Features

### 2.1 Core Scouting Interface

- Ranked player leaderboard across 5,000 players, sortable on any metric, including the Undervalued Score, Deal Score, and Acquirability Score.

- Filtering by position, country, tier, age, agent status, and free-text search over names and clubs.

- Adjustable scoring weights (attacking output, progression, defensive actions, youth) that recompute all scores live.

- A per-player detail view containing the full statistical profile, percentile breakdowns, market-value history chart, tactical system fit, transfer-pathway suggestions, and contact/verification guidance.

- Explainability panels showing precisely why each player is flagged: output percentile, value percentile, age, minutes, sample-size caveats, and acquirability drivers.

- Flag system: High Priority (undervalued gap of 40 points or more), Watchlist (20 or more), each with an Unrepresented variant for players without an agent, plus the Hot Prospect call-list designation (Deal Score of 55 or more).

### 2.2 Accounts and Commercial Features

- User registration and login with JWT authentication; all data endpoints are authenticated.

- Per-user shortlists and private player notes.

- Saved Views: named filter presets, optionally shareable through public tokens with personal data stripped.

- Scout Ledger (Pro): dated prediction snapshots recorded against eventual outcomes, building a proprietary track record.

- Pro subscription tier with a genuine Stripe checkout and webhook-driven entitlement, including cancellation handling.

- Administrative role with a gated player-management endpoint.

- Rate limiting on authentication and data endpoints; input-size caps on all user-submitted content.

### 2.3 Operations and Delivery

- REST API (FastAPI) providing server-side filtering, sorting, pagination, and scoring identical to the frontend.

- Offline mode: the application is a single self-contained HTML file that works double-clicked, with no server, and silently upgrades to API mode when a server is reachable.

- Scheduled Data Health monitoring comparing each refresh against a stored baseline.

- Weekly data-refresh pipeline, Docker deployment configuration, and a documented migration path from SQLite to PostgreSQL.

- Automated test suite (31 tests) covering the API surface, authentication, billing, scoring, and model regression cases.

- Terms of Service and Privacy pages; the dataset is served exclusively through the authenticated API so it cannot be scraped from the page source.

## 3. Data

### 3.1 Provenance

The current dataset is a generated, fictional sample of 5,000 players, produced by a seeded, reproducible generator. It is statistically realistic (positional distributions, per-90 rates, and market values are internally coherent) and is used to develop and demonstrate the platform ahead of licensed real-data integration. No real person is represented, and destination clubs in transfer suggestions are likewise fictional. All numbers in this document reflect the dataset as of July 27, 2026.

### 3.2 Coverage

- 5,000 players across 38 countries and approximately 290 league instances (tiers 2–4).

- Regions: Central Europe (Poland, Czechia, Slovakia, Hungary, Austria, Switzerland), the Balkans, the Baltics, the Caucasus, South America, West/Central/Southern Africa, East/Southeast/Central Asia, CONCACAF, and Oceania.

- Positions: 1,514 midfielders, 1,496 defenders, 1,172 forwards, 818 goalkeepers.

- Austria and Switzerland are generated with a deliberate skew (more tier 3–4 players, higher agent representation) reflecting how thoroughly those markets are already scouted.

### 3.3 Data Dictionary

| Field | Type | Description |
|---|---|---|
| name / country / league / club | text | Identity and affiliation; name+country is unique |
| tier | integer 2–4 | Competitive level of the player's league |
| position | GK / DF / MF / FW | Primary position |
| age | integer 17–26 | Age in years |
| minutes | integer 800–2,744 | Competitive minutes in the current season |
| goals, assists | integer | Attacking end product |
| progPasses, progCarries | integer | Progressive passes and carries |
| tklInt | integer | Tackles plus interceptions |
| saves, goalsConceded, cleanSheets | integer | Goalkeeping outcomes |
| passCompletionPct | real | Goalkeeper distribution accuracy (%) |
| sweeperActions | real | Goalkeeper defensive actions outside the box |
| marketValue | integer (EUR) | Known market value; 0 when unknown (~25% of players) |
| hasAgent | Yes / No / Unknown | Representation status |
| contractExpires | integer year 2026–2029 | Contract expiry year |
| clubContactEmail, contactRoute | text | Official contact channel and recommended route |
| federationRegistry | text | National federation registry for identity verification |
| dateAdded, lastUpdated | date | Dataset lineage stamps |

### 3.4 Storage and Consistency

Data is held canonically in SQLite (players table plus users, shortlists, notes, watchlists, and ledger tables) with a documented PostgreSQL migration path. A SQL view, player_scores, reproduces the entire scoring pipeline in pure SQL using window functions. The scoring engine exists in exactly three places—Python (scoring.py), JavaScript (embedded in the frontend), and SQL (the view)—and all three are verified to agree: Python and JavaScript to bit-exactness across all 5,000 players, and SQL to within display rounding (±0.05).

## 4. Scouting Models

### 4.1 Performance Score

Each player is reduced to three position-appropriate per-90 rates: for outfield players, goals plus assists, progressive actions, and tackles plus interceptions; for goalkeepers, a shot-stopping metric (saves less a goals-conceded penalty), distribution accuracy, and sweeper actions. Every rate is stabilised by empirical-Bayes shrinkage toward the position group's minutes-weighted mean, with a prior worth 900 minutes (about ten matches), so small samples cannot dominate the leaderboard. Shrunken rates are converted to percentiles within the position group, combined using the user-adjustable weights, and multiplied by a continuous league-strength coefficient (an exponential curve over tier, with a per-league override table prepared for real coefficients). Players under 900 minutes are marked low-sample and cannot receive the High Priority flag.

### 4.2 Market-Value Estimation

Where a market value is unknown, the platform estimates one. A quality composite is computed from the shrunken rates against position-relative reference points (1.8 times the position mean). The estimate is log-scale—right-skewed like real transfer markets—centred so the cohort median equals €79,000, floored at €10,000 and capped at €500,000, and adjusted multiplicatively by league strength, an age-value curve, and a minutes factor. The age curve rises through the late teens, plateaus across the 24–27 peak, and declines thereafter, replacing the naïve assumption that younger is always more valuable. The estimator is deterministic: identical inputs always produce identical values.

### 4.3 Undervalued Score

The Undervalued Score is the difference between a player's performance percentile and market-value percentile within their position group, scaled to –100 to +100. Unknown values use the model's estimate for ranking purposes, so missing data is never mistaken for cheapness. Thresholds: 40 or above earns High Priority; 20 or above earns Watchlist; both add an Unrepresented variant when no agent is on record.

### 4.4 Acquirability Score

Acquirability (0–100) answers a distinct question: how realistically could this player be signed now? It multiplies a contract-window component (expiring contracts score highest, with a re-signing discount for players aged 20 and under), a representation component (unrepresented players are the most approachable), and a fee-feasibility component (log-scale in value), further adjusted by seller leverage (league strength) and a regulatory gate for minors (FIFA RSTP Article 19). The combination is multiplicative because real-world obstacles compound.

### 4.5 Deal Score and the Call List

The Deal Score (0–100) crosses the undervalued gap with acquirability using a weighted geometric mean (exponents 0.60 and 0.40 respectively). A player who is not undervalued scores exactly zero regardless of availability, and neither component alone can carry a player onto the list. Players scoring 55 or above with an adequate minutes sample receive the Hot Prospect designation—currently 237 players, 4.7% of the roster, a deliberately selective call list.

### 4.6 Tactical System Fit

A rules-based classifier reads each player's percentile profile and assigns a best-fit playing system—High Press/Gegenpressing, Possession/Build-from-the-Back, Counter-Attack/Transition, Low Block, Direct/Target, and goalkeeper-specific profiles (Sweeper-Keeper variants and Shot-Stopper)—each with a written rationale identifying the drivers behind the classification.

### 4.7 Explainability

Every scored player carries two structured explanation objects: one for the Undervalued Score (output and value percentiles, the four metric percentiles, age, minutes, sample-size and estimation caveats) and one for the Deal Score (contract, representation, fee, and league drivers with plain-language texts). These render in the player detail view so no recommendation is ever an unexplained verdict.

## 5. Verification and Quality Assurance

- 31 automated tests covering authentication, data endpoints, billing, scoring behaviour, and model regression cases pinned to hand-computed examples.

- Cross-implementation parity: Python and JavaScript scoring verified bit-exact across all 5,000 players and every derived field; the SQL view verified to within ±0.05.

- Deterministic pipeline end to end: dataset generation, scoring, value estimation, and value-history charts are all seeded or pure functions—no random noise is presented as signal.

- Calibration checks: the estimated-value distribution is verified against the known-value distribution (median €79,000, right-skewed, nothing truncated at the bounds).

- Data Health monitoring compares each data refresh against a stored baseline and reports anomalies.

## 6. Document Control

*This document reflects the repository state as of July 27, 2026. The dataset is a fictional, generated sample pending licensed real-data integration; all model mechanics, infrastructure, and verification described above are implemented and operational against that sample.*
