"""
db_tables.py -- SQLAlchemy Core table definitions shared by api_server.py and
migrate_to_postgres.py. Defined once here so the same metadata.create_all()
works against SQLite (local/offline) and PostgreSQL (hosted) unchanged.

Column names come from db_schema.FIELD_MAP so the SQL side never drifts from
the JSON/JS side.
"""

from sqlalchemy import (MetaData, Table, Column, Integer, Float, Text,
                        UniqueConstraint, Index)
from db_schema import FIELD_MAP

metadata = MetaData()

_INT_COLS = {"tier", "age", "minutes", "goals", "assists", "prog_passes",
             "prog_carries", "tkl_int", "saves", "goals_conceded",
             "clean_sheets", "market_value", "contract_expires"}
_FLOAT_COLS = {"pass_completion_pct", "sweeper_actions"}


def _col_type(sql_name):
    if sql_name in _INT_COLS:
        return Integer
    if sql_name in _FLOAT_COLS:
        return Float
    return Text


players = Table(
    "players", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    *[Column(sql_name, _col_type(sql_name)) for _, sql_name in FIELD_MAP],
    UniqueConstraint("name", "country", name="uq_players_name_country"),
)

Index("ix_players_position", players.c.position)
Index("ix_players_country", players.c.country)
Index("ix_players_tier", players.c.tier)

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", Text, unique=True, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("is_pro", Integer, default=0),      # 1 = Pro subscriber
    Column("role", Text, default="user"),      # "user" | "admin"
)

# Scout Ledger: a user's dated prediction snapshots + their eventual outcome.
# This is the proprietary "our calls vs. reality" dataset that compounds.
ledger_entries = Table(
    "ledger_entries", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False),
    Column("player_id", Integer, nullable=False),
    Column("player_name", Text, nullable=False),
    Column("snapshot_date", Text, nullable=False),
    Column("undervalued_score", Float),
    Column("market_value", Integer),
    Column("outcome", Text, nullable=False, default="pending"),  # pending|signed|rose|available|missed
    Column("outcome_at", Text),
)
Index("ix_ledger_user", ledger_entries.c.user_id)

# Saved views: a named filter preset, optionally shareable via a public token.
watchlists = Table(
    "watchlists", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False),
    Column("name", Text, nullable=False),
    Column("filters", Text, nullable=False, default="{}"),
    Column("share_token", Text),
    Column("created_at", Text, nullable=False),
)
Index("ix_watchlists_user", watchlists.c.user_id)
Index("ix_watchlists_share", watchlists.c.share_token)

shortlists = Table(
    "shortlists", metadata,
    Column("user_id", Integer, primary_key=True),
    Column("player_id", Integer, primary_key=True),
)

notes = Table(
    "notes", metadata,
    Column("user_id", Integer, primary_key=True),
    Column("player_id", Integer, primary_key=True),
    Column("text", Text, nullable=False, default=""),
    Column("updated_at", Text, nullable=False),
)

# JSON camelCase key -> SQL column, plus the reverse, for row <-> dict mapping
JSON_TO_SQL = dict(FIELD_MAP)
SQL_TO_JSON = {sql: js for js, sql in FIELD_MAP}


def row_to_player(row_mapping):
    """SQLAlchemy row mapping -> camelCase player dict (with id)."""
    d = {"id": row_mapping["id"]}
    for js, sql in FIELD_MAP:
        d[js] = row_mapping[sql]
    return d
