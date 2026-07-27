"""
Canonical SQLite schema for the scouting database.

Why SQLite now, not Postgres:
  This is still a single-user local prototype (one person, running locally,
  no concurrent writers, no network access needed). SQLite is a real
  relational database -- proper types, indexes, foreign-key-capable, full
  SQL including window functions -- with zero operational overhead: no
  server process, no credentials, no hosting bill, and the whole database
  is one file that travels with the rest of the deliverables.

  The moment this becomes a hosted, multi-user product (the life-services
  app's User Accounts backlog item, or if this scouting app gets a real
  login system), the honest recommendation is PostgreSQL: concurrent
  writes, network access, row-level security for multi-tenant data, a
  mature managed-hosting ecosystem (Supabase, RDS, etc.), and native JSON
  support if semi-structured fields are still needed. SQLite does not
  handle concurrent multi-writer access well, which is the actual reason
  to move, not scale in row count -- SQLite comfortably handles millions
  of rows; it's concurrency, not volume, that would force the change.

This module defines the schema once so db_migrate.py (JSON -> SQLite),
weekly_update.py (read/refresh/insert), and export_json_from_db.py
(SQLite -> JSON, for the existing xlsx/html build pipeline) all agree
on column names and types.
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS players (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL,
    country               TEXT NOT NULL,
    league                TEXT NOT NULL,
    tier                  INTEGER NOT NULL CHECK (tier IN (2,3,4)),
    club                  TEXT NOT NULL,
    position              TEXT NOT NULL CHECK (position IN ('GK','DF','MF','FW')),
    age                   INTEGER NOT NULL CHECK (age BETWEEN 15 AND 40),
    minutes               INTEGER NOT NULL CHECK (minutes >= 0),
    goals                 INTEGER NOT NULL DEFAULT 0,
    assists               INTEGER NOT NULL DEFAULT 0,
    prog_passes           INTEGER NOT NULL DEFAULT 0,
    prog_carries          INTEGER NOT NULL DEFAULT 0,
    tkl_int               INTEGER NOT NULL DEFAULT 0,
    saves                 INTEGER NOT NULL DEFAULT 0,
    goals_conceded        INTEGER NOT NULL DEFAULT 0,
    pass_completion_pct   REAL NOT NULL DEFAULT 0,
    sweeper_actions        REAL NOT NULL DEFAULT 0,
    clean_sheets          INTEGER NOT NULL DEFAULT 0,
    market_value          INTEGER NOT NULL DEFAULT 0,
    has_agent             TEXT NOT NULL CHECK (has_agent IN ('Yes','No','Unknown')),
    contract_expires      INTEGER,
    club_contact_email    TEXT,
    contact_route         TEXT,
    federation_registry   TEXT,
    date_added            TEXT NOT NULL,
    last_updated          TEXT NOT NULL,
    UNIQUE(name, country)
);

CREATE INDEX IF NOT EXISTS idx_players_position       ON players(position);
CREATE INDEX IF NOT EXISTS idx_players_country         ON players(country);
CREATE INDEX IF NOT EXISTS idx_players_tier            ON players(tier);
CREATE INDEX IF NOT EXISTS idx_players_age             ON players(age);
CREATE INDEX IF NOT EXISTS idx_players_has_agent       ON players(has_agent);
CREATE INDEX IF NOT EXISTS idx_players_date_added      ON players(date_added);
CREATE INDEX IF NOT EXISTS idx_players_position_tier   ON players(position, tier);
"""

# FIELD_MAP: JSON key -> SQL column name (JSON stays camelCase for the JS app;
# SQL stays snake_case, which is the conventional style for the column names
# a DBA/analyst would expect).
FIELD_MAP = [
    ("name", "name"), ("country", "country"), ("league", "league"), ("tier", "tier"),
    ("club", "club"), ("position", "position"), ("age", "age"), ("minutes", "minutes"),
    ("goals", "goals"), ("assists", "assists"), ("progPasses", "prog_passes"),
    ("progCarries", "prog_carries"), ("tklInt", "tkl_int"), ("saves", "saves"),
    ("goalsConceded", "goals_conceded"), ("passCompletionPct", "pass_completion_pct"),
    ("sweeperActions", "sweeper_actions"), ("cleanSheets", "clean_sheets"),
    ("marketValue", "market_value"), ("hasAgent", "has_agent"),
    ("contractExpires", "contract_expires"), ("clubContactEmail", "club_contact_email"),
    ("contactRoute", "contact_route"), ("federationRegistry", "federation_registry"),
    ("dateAdded", "date_added"), ("lastUpdated", "last_updated"),
]

# A worked example of the "complex query pattern" this migration is for: the
# Undervalued Score computed entirely in SQL via window functions, partitioned
# by position exactly like the xlsx/JS versions. Not used by the build
# pipeline (which still goes through JSON for now) -- included so the
# database is genuinely queryable on its own, not just a JSON dump with
# extra steps.
# Mirrors scoring.py's compute_scores exactly -- raw per-90 rates, empirical-Bayes
# shrinkage toward the position-group minutes-weighted mean, the continuous league
# strength coefficient, the age-value curve, the estimated-value engine, and the
# undervalued gap ranked on displayMarketValue. Requires SQLite built with the
# math functions (exp/pow); 3.35+ ships them and the bundled Python build has them.
#
# Two things this view deliberately cannot do -- see scoring.py:
#   1. LEAGUE_STRENGTH per-league overrides are a Python dict, so the view only
#      implements the tier-curve fallback. It will diverge the moment a league is
#      given an explicit coefficient; that table has to be materialised into the
#      database before SQL can honour it.
#   2. Floating-point summation order inside SUM() OVER () is not guaranteed to
#      match Python's list order, so the shrinkage means can differ in the last
#      bits. test_scores_match_sql_view allows +-0.1 on undervalued_score, which
#      absorbs it comfortably.
UNDERVALUED_VIEW_SQL = """
DROP VIEW IF EXISTS player_scores;
CREATE VIEW player_scores AS
WITH rates AS (
  SELECT *,
    CASE WHEN position = 'GK' THEN
      CASE WHEN minutes > 0 THEN (CAST(saves AS REAL) - 1.5 * goals_conceded) / minutes * 90 ELSE 0 END
    ELSE
      CASE WHEN minutes > 0 THEN CAST(goals + assists AS REAL) / minutes * 90 ELSE 0 END
    END AS factor1_per90,
    CASE WHEN position = 'GK' THEN pass_completion_pct
    ELSE
      CASE WHEN minutes > 0 THEN CAST(prog_passes + prog_carries AS REAL) / minutes * 90 ELSE 0 END
    END AS factor2_per90,
    CASE WHEN position = 'GK' THEN
      CASE WHEN minutes > 0 THEN sweeper_actions / minutes * 90 ELSE 0 END
    ELSE
      CASE WHEN minutes > 0 THEN CAST(tkl_int AS REAL) / minutes * 90 ELSE 0 END
    END AS factor3_per90,
    CASE WHEN (saves + goals_conceded) > 0
         THEN CAST(saves AS REAL) / (saves + goals_conceded) ELSE 0 END AS save_pct,
    CASE WHEN minutes > 0 THEN CAST(goals_conceded AS REAL) / (minutes / 90.0) ELSE 0 END AS gc_per90,
    CASE WHEN minutes > 0 THEN CAST(clean_sheets AS REAL) / (minutes / 90.0) ELSE 0 END AS cs_rate
  FROM players
),
-- Position-group minutes-weighted means (the empirical-Bayes prior).
means AS (
  SELECT *,
    SUM(factor1_per90 * minutes) OVER w / SUM(minutes) OVER w AS mean_ga,
    SUM(factor2_per90 * minutes) OVER w / SUM(minutes) OVER w AS mean_prog,
    SUM(factor3_per90 * minutes) OVER w / SUM(minutes) OVER w AS mean_def,
    SUM(save_pct      * minutes) OVER w / SUM(minutes) OVER w AS mean_save_pct,
    SUM(gc_per90      * minutes) OVER w / SUM(minutes) OVER w AS mean_gc,
    SUM(cs_rate       * minutes) OVER w / SUM(minutes) OVER w AS mean_cs
  FROM rates
  WINDOW w AS (PARTITION BY position)
),
-- shrunk = (rate * minutes + posMean * K) / (minutes + K), K = 900.
shrunk AS (
  SELECT *,
    (factor1_per90 * minutes + mean_ga   * 900.0) / (minutes + 900.0) AS ga_per90,
    (factor2_per90 * minutes + mean_prog * 900.0) / (minutes + 900.0) AS prog_per90,
    (factor3_per90 * minutes + mean_def  * 900.0) / (minutes + 900.0) AS def_per90,
    (save_pct * minutes + mean_save_pct * 900.0) / (minutes + 900.0) AS s_save_pct,
    (gc_per90 * minutes + mean_gc       * 900.0) / (minutes + 900.0) AS s_gc_per90,
    (cs_rate  * minutes + mean_cs       * 900.0) / (minutes + 900.0) AS s_cs_rate,
    minutes < 900 AS low_sample,
    exp(-0.155 * (tier - 2.0)) AS league_strength,
    CASE
      WHEN age >= 24 AND age <= 27 THEN 1.0
      WHEN age < 24 THEN 0.70 + 0.30 * pow(MIN(1.0, MAX(0.0, (age - 16.0) / 8.0)), 0.85)
      ELSE exp(-0.055 * pow(age - 27.0, 1.35))
    END AS age_factor
  FROM means
),
-- Quality composite against position-relative references (1.8x the group mean).
composite AS (
  SELECT *,
    CASE WHEN position = 'GK' THEN
        0.5 * MIN(1.0, MAX(0.0, (s_save_pct - 0.55) / 0.35))
      + 0.3 * MIN(1.0, MAX(0.0, 1 - s_gc_per90 / 2.2))
      + 0.2 * MIN(1.0, MAX(0.0, s_cs_rate / 0.45))
    ELSE
        (CASE position WHEN 'FW' THEN 0.55 WHEN 'MF' THEN 0.40 ELSE 0.25 END)
          * MIN(1.0, MAX(0.0, ga_per90   / (mean_ga   * 1.8)))
      + (CASE position WHEN 'MF' THEN 0.40 ELSE 0.33 END)
          * MIN(1.0, MAX(0.0, prog_per90 / (mean_prog * 1.8)))
      + (CASE position WHEN 'DF' THEN 0.45 ELSE 0.27 END)
          * MIN(1.0, MAX(0.0, def_per90  / (mean_def  * 1.8)))
    END AS quality_composite
  FROM shrunk
),
valued AS (
  SELECT *,
    MAX(10000.0, MIN(500000.0,
      79000.0 * exp(3.15 * (quality_composite - 0.488432))
              * league_strength * age_factor
              * (0.85 + 0.15 * MIN(1.0, MAX(0.0, minutes / 2200.0)))
    )) AS raw_value
  FROM composite
),
estimated AS (
  SELECT *,
    CASE WHEN raw_value < 250000 THEN ROUND(raw_value / 100.0) * 100
         WHEN raw_value < 1000000 THEN ROUND(raw_value / 1000.0) * 1000
         ELSE ROUND(raw_value / 10000.0) * 10000 END AS estimated_market_value
  FROM valued
),
display AS (
  SELECT *,
    CASE WHEN market_value > 0 THEN CAST(market_value AS REAL)
         ELSE estimated_market_value END AS display_market_value
  FROM estimated
),
pct AS (
  SELECT *,
    -- CUME_DIST = count(peers <= value) / count(peers): the exact percentile
    -- convention the JS app and scoring.py use (percentileRank). PERCENT_RANK
    -- would diverge badly on ties.
    CUME_DIST() OVER (PARTITION BY position ORDER BY ga_per90)   AS factor1_pct,
    CUME_DIST() OVER (PARTITION BY position ORDER BY prog_per90) AS factor2_pct,
    CUME_DIST() OVER (PARTITION BY position ORDER BY def_per90)  AS factor3_pct,
    CUME_DIST() OVER (PARTITION BY position ORDER BY -age)       AS youth_pct,
    -- display_market_value, NOT market_value: ranking on the raw field sorted
    -- every unknown-value player (0) to the bottom of the value distribution and
    -- handed them a near-maximal undervalued gap.
    CUME_DIST() OVER (PARTITION BY position ORDER BY display_market_value) AS market_pct
  FROM display
),
perf AS (
  SELECT *,
    (factor1_pct * 0.25 + factor2_pct * 0.35 + factor3_pct * 0.20 + youth_pct * 0.20) * league_strength AS performance_score
  FROM pct
),
final AS (
  SELECT *,
    CUME_DIST() OVER (PARTITION BY position ORDER BY performance_score) AS performance_pct
  FROM perf
),
-- Acquirability (see acquirability_spec.md): contract window x representation x
-- fee feasibility, with league-friction and minor-transfer multipliers.
acq AS (
  SELECT *,
    100.0
    * pow(
        (CASE
           WHEN contract_expires IS NULL THEN 0.35
           WHEN MIN(3, MAX(0, contract_expires - 2026)) = 0 THEN 1.00
           WHEN MIN(3, MAX(0, contract_expires - 2026)) = 1 THEN 0.75
           WHEN MIN(3, MAX(0, contract_expires - 2026)) = 2 THEN 0.35
           ELSE 0.15 END)
        * (0.75 + 0.25 * MIN(1.0, MAX(0.0, (age - 20.0) / 3.0))),
        0.45)
    * pow(CASE has_agent WHEN 'No' THEN 1.00 WHEN 'Yes' THEN 0.35 ELSE 0.70 END, 0.30)
    * pow(pow(10000.0 / MAX(display_market_value, 10000.0), 0.35), 0.25)
    * (0.85 + 0.15 * MIN(1.0, MAX(0.0, (1.000 - league_strength) / 0.267)))
    * (CASE WHEN age < 18 THEN 0.40 ELSE 1.0 END)
    AS acquirability_score
  FROM final
),
deal AS (
  SELECT *,
    -- uv <= 0 -> deal = 0 exactly: an overvalued player is never a deal.
    100.0 * pow(MAX(0.0, performance_pct - market_pct), 0.60)
          * pow(acquirability_score / 100.0, 0.40) AS deal_score
  FROM acq
)
SELECT
  id, name, country, position, tier, age, has_agent, market_value, minutes,
  low_sample,
  ROUND(acquirability_score, 1) AS acquirability_score,
  ROUND(deal_score, 1) AS deal_score,
  deal_score >= 55 AND NOT low_sample AS hot_prospect,
  ROUND(league_strength, 4) AS league_strength,
  estimated_market_value,
  display_market_value,
  ROUND(factor1_pct * 100, 1) AS factor1_percentile,
  ROUND(factor2_pct * 100, 1) AS factor2_percentile,
  ROUND(factor3_pct * 100, 1) AS factor3_percentile,
  ROUND(performance_pct * 100, 1) AS performance_percentile,
  ROUND(market_pct * 100, 1) AS market_percentile,
  ROUND((performance_pct - market_pct) * 100, 1) AS undervalued_score,
  -- Low-sample players cap at Watchlist: they cannot earn High Priority.
  CASE
    WHEN (performance_pct - market_pct) * 100 >= 40 AND NOT low_sample AND has_agent = 'No' THEN 'High Priority - Unrepresented'
    WHEN (performance_pct - market_pct) * 100 >= 40 AND NOT low_sample THEN 'High Priority'
    WHEN ((performance_pct - market_pct) * 100 >= 20
          OR ((performance_pct - market_pct) * 100 >= 40 AND low_sample)) AND has_agent = 'No' THEN 'Watchlist - Unrepresented'
    WHEN ((performance_pct - market_pct) * 100 >= 20
          OR ((performance_pct - market_pct) * 100 >= 40 AND low_sample)) THEN 'Watchlist'
    ELSE ''
  END AS flag
FROM deal;
"""
