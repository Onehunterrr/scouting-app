"""
migrate_to_postgres.py -- copy the scouting database from SQLite to any
SQLAlchemy-supported target (PostgreSQL in practice; any URL works).

Usage:
    python3 migrate_to_postgres.py <target_url> [source_url]

    target_url   e.g. postgresql://user:pass@host:5432/scouting
                 (or postgresql+psycopg2://...; any SQLAlchemy URL is accepted,
                  so a sqlite:/// target also works for a dry run)
    source_url   defaults to sqlite:///./scouting.db next to this script

Environment variables DATABASE_URL (target) and SOURCE_URL (source) are used
when the arguments are omitted.

Copies: players (via db_schema.FIELD_MAP so column names never drift), plus
users / shortlists / notes if they exist in the source. Creates all tables on
the target with the shared SQLAlchemy metadata (db_tables.py), inserts in
chunks, and verifies row counts at the end.
"""

import os
import sys

from sqlalchemy import create_engine, select, insert, func, inspect

from db_tables import metadata, players, users, shortlists, notes

CHUNK = 200


def table_count(engine, table):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def copy_table(src, dst, table, name):
    insp = inspect(src)
    if not insp.has_table(table.name):
        print(f"  {name}: not present in source, skipped")
        return 0
    with src.connect() as sconn:
        rows = [dict(m) for m in sconn.execute(select(table)).mappings().all()]
    if not rows:
        print(f"  {name}: 0 rows")
        return 0
    with dst.begin() as dconn:
        for i in range(0, len(rows), CHUNK):
            dconn.execute(insert(table), rows[i:i + CHUNK])
    src_n, dst_n = len(rows), table_count(dst, table)
    status = "OK" if src_n == dst_n else "MISMATCH"
    print(f"  {name}: {src_n} rows copied, target now has {dst_n} [{status}]")
    if src_n != dst_n:
        raise SystemExit(f"Row count mismatch on {name}: source {src_n} vs target {dst_n}")
    return dst_n


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    target_url = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATABASE_URL"))
    source_url = (sys.argv[2] if len(sys.argv) > 2
                  else os.environ.get("SOURCE_URL", f"sqlite:///{os.path.join(base, 'scouting.db')}"))
    if not target_url:
        raise SystemExit("Usage: python3 migrate_to_postgres.py <target_url> [source_url]")
    if target_url == source_url:
        raise SystemExit("Target and source URLs are identical -- refusing to copy onto itself.")

    print(f"source: {source_url}")
    print(f"target: {target_url}")

    src = create_engine(source_url, future=True)
    dst = create_engine(target_url, future=True)

    # fresh target tables (drop first so re-runs are idempotent)
    metadata.drop_all(dst, checkfirst=True)
    metadata.create_all(dst)

    print("copying tables:")
    copy_table(src, dst, players, "players")
    copy_table(src, dst, users, "users")
    copy_table(src, dst, shortlists, "shortlists")
    copy_table(src, dst, notes, "notes")

    # Postgres keeps its own sequence for autoincrement PKs -- resync it so new
    # INSERTs after migration don't collide with copied ids.
    if dst.url.get_backend_name().startswith("postgres"):
        from sqlalchemy import text
        with dst.begin() as conn:
            for tbl in ("players", "users"):
                conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {tbl}), 1))"))
        print("postgres id sequences resynced")

    print("migration complete. Point the API at it with:")
    print(f"  DATABASE_URL='{target_url}' python3 -m uvicorn api_server:app")


if __name__ == "__main__":
    main()
