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
    END AS factor3_per90
  FROM players
),
pct AS (
  SELECT *,
    -- CUME_DIST = count(peers <= value) / count(peers): the exact percentile
    -- convention the JS app and scoring.py use (percentileRank). PERCENT_RANK
    -- would diverge badly on ties (e.g. the ~240 players with market_value 0).
    CUME_DIST() OVER (PARTITION BY position ORDER BY factor1_per90) AS factor1_pct,
    CUME_DIST() OVER (PARTITION BY position ORDER BY factor2_per90) AS factor2_pct,
    CUME_DIST() OVER (PARTITION BY position ORDER BY factor3_per90) AS factor3_pct,
    CUME_DIST() OVER (PARTITION BY position ORDER BY -age)          AS youth_pct,
    CUME_DIST() OVER (PARTITION BY position ORDER BY market_value)  AS market_pct,
    CASE tier WHEN 2 THEN 1.00 WHEN 3 THEN 0.85 ELSE 0.70 END AS tier_mult
  FROM rates
),
perf AS (
  SELECT *,
    (factor1_pct * 0.25 + factor2_pct * 0.35 + factor3_pct * 0.20 + youth_pct * 0.20) * tier_mult AS performance_score
  FROM pct
),
final AS (
  SELECT *,
    CUME_DIST() OVER (PARTITION BY position ORDER BY performance_score) AS performance_pct
  FROM perf
)
SELECT
  id, name, country, position, tier, age, has_agent, market_value,
  ROUND(factor1_pct * 100, 1) AS factor1_percentile,
  ROUND(factor2_pct * 100, 1) AS factor2_percentile,
  ROUND(factor3_pct * 100, 1) AS factor3_percentile,
  ROUND(performance_pct * 100, 1) AS performance_percentile,
  ROUND(market_pct * 100, 1) AS market_percentile,
  ROUND((performance_pct - market_pct) * 100, 1) AS undervalued_score,
  CASE
    WHEN (performance_pct - market_pct) * 100 >= 40 AND has_agent = 'No' THEN 'High Priority - Unrepresented'
    WHEN (performance_pct - market_pct) * 100 >= 40 THEN 'High Priority'
    WHEN (performance_pct - market_pct) * 100 >= 20 AND has_agent = 'No' THEN 'Watchlist - Unrepresented'
    WHEN (performance_pct - market_pct) * 100 >= 20 THEN 'Watchlist'
    ELSE ''
  END AS flag
FROM final;
"""
