"""
migrate_to_layerbase.py
=========================
One-time (re-runnable) full migration of the local Postgres database to the
project's Layerbase cloud instance. Unlike migrate_to_supabase.py, this
copies EVERY table, including player_shots (6.3M rows) and pbp_events
(3.6M rows) -- Layerbase's free tier is real Postgres with a 5GB cap, which
fits the whole ~2.15GB local DB, so there's no need to exclude the two big
tables. The local DB stays the source of truth; this is a rebuildable
mirror for sessions/deploys that can't reach localhost.

No pg_dump/psql binary exists on this machine (checked directly), so this
reconstructs each table's schema generically from information_schema
(columns, types, nullability, primary key), same approach as
migrate_to_supabase.py. Difference from that script: this also recreates
every non-PK index (from pg_indexes), and streams large tables in
row-number chunks instead of buffering a whole table in memory at once.
SERIAL/sequence defaults still aren't recreated -- copied data already has
explicit real IDs, and this migration is for real read access, not
local/cloud parity for writes.

Usage:
    cd scripts && python3 migrate_to_layerbase.py
"""

import io
import os

import psycopg2
from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api", ".env")
load_dotenv(_ENV_PATH)

LOCAL_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD"),
    "dbname": os.getenv("DB_NAME", "nba_analytics"),
}
CLOUD_CONFIG = {
    "host": os.getenv("LAYERBASE_DB_HOST"),
    "port": os.getenv("LAYERBASE_DB_PORT", "5432"),
    "user": os.getenv("LAYERBASE_DB_USER"),
    "password": os.getenv("LAYERBASE_DB_PASSWORD"),
    "dbname": os.getenv("LAYERBASE_DB_NAME", "postgres"),
}

# Tables above this many rows are streamed in chunks instead of one COPY.
CHUNK_THRESHOLD = 500_000
CHUNK_SIZE = 200_000

TYPE_MAP = {
    "int4": "INTEGER", "int8": "BIGINT", "int2": "SMALLINT",
    "float8": "DOUBLE PRECISION", "float4": "REAL", "numeric": "NUMERIC",
    "bool": "BOOLEAN", "varchar": "TEXT", "text": "TEXT", "bpchar": "TEXT",
    "timestamp": "TIMESTAMP", "timestamptz": "TIMESTAMPTZ", "date": "DATE",
    "jsonb": "JSONB", "json": "JSON",
    "_int8": "BIGINT[]",  # lineup_stats.player_ids
}


def list_tables(cur):
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;")
    return [r[0] for r in cur.fetchall()]


def get_columns(cur, table):
    cur.execute(
        """SELECT column_name, udt_name, is_nullable
           FROM information_schema.columns
           WHERE table_schema='public' AND table_name=%s
           ORDER BY ordinal_position;""",
        (table,),
    )
    return cur.fetchall()


def get_primary_key(cur, table):
    cur.execute(
        """SELECT a.attname FROM pg_index i
           JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
           WHERE i.indrelid = %s::regclass AND i.indisprimary
           ORDER BY array_position(i.indkey, a.attnum);""",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def get_non_pk_indexes(cur, table):
    """indexdef strings for every index on `table` that isn't the PK's own index."""
    cur.execute(
        """SELECT indexname, indexdef FROM pg_indexes
           WHERE schemaname='public' AND tablename=%s;""",
        (table,),
    )
    return [(name, ddl) for name, ddl in cur.fetchall() if not name.endswith("_pkey")]


def build_create_table(table, columns, pk_cols):
    col_defs = []
    for name, udt_name, nullable in columns:
        sql_type = TYPE_MAP.get(udt_name, udt_name.upper())
        col_sql = f'"{name}" {sql_type}'
        if nullable == "NO" and name not in pk_cols:
            col_sql += " NOT NULL"
        col_defs.append(col_sql)
    ddl = f'CREATE TABLE IF NOT EXISTS "{table}" (\n  ' + ",\n  ".join(col_defs)
    if pk_cols:
        pk_list = ", ".join(f'"{c}"' for c in pk_cols)
        ddl += f",\n  PRIMARY KEY ({pk_list})"
    ddl += "\n);"
    return ddl


def copy_chunk(local_cur, cloud_cur, table, col_list, offset, limit):
    buf = io.StringIO()
    local_cur.copy_expert(
        f'COPY (SELECT {col_list} FROM "{table}" ORDER BY ctid OFFSET {offset} LIMIT {limit}) '
        f'TO STDOUT WITH CSV',
        buf,
    )
    buf.seek(0)
    cloud_cur.copy_expert(f'COPY "{table}" ({col_list}) FROM STDIN WITH CSV', buf)


def migrate_table(local_cur, cloud_conn, cloud_cur, table, row_count):
    columns = get_columns(local_cur, table)
    col_names = [c[0] for c in columns]
    pk_cols = get_primary_key(local_cur, table)
    indexes = get_non_pk_indexes(local_cur, table)

    ddl = build_create_table(table, columns, pk_cols)
    cloud_cur.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE;')
    cloud_cur.execute(ddl)
    cloud_conn.commit()

    col_list = ", ".join(f'"{c}"' for c in col_names)

    if row_count > CHUNK_THRESHOLD:
        offset = 0
        while offset < row_count:
            copy_chunk(local_cur, cloud_cur, table, col_list, offset, CHUNK_SIZE)
            cloud_conn.commit()
            offset += CHUNK_SIZE
            print(f"    ...{min(offset, row_count):,}/{row_count:,} rows", end="\r")
        print()
    else:
        buf = io.StringIO()
        local_cur.copy_expert(f'COPY "{table}" ({col_list}) TO STDOUT WITH CSV', buf)
        buf.seek(0)
        cloud_cur.copy_expert(f'COPY "{table}" ({col_list}) FROM STDIN WITH CSV', buf)
        cloud_conn.commit()

    for name, indexdef in indexes:
        cloud_cur.execute(indexdef)
    cloud_conn.commit()

    cloud_cur.execute(f'SELECT COUNT(*) FROM "{table}";')
    return cloud_cur.fetchone()[0], len(indexes)


def main():
    local_conn = psycopg2.connect(**LOCAL_CONFIG)
    local_cur = local_conn.cursor()
    cloud_conn = psycopg2.connect(**CLOUD_CONFIG)
    cloud_cur = cloud_conn.cursor()

    tables = list_tables(local_cur)
    print(f"Migrating {len(tables)} tables (full copy, no exclusions)...")

    total_rows = 0
    total_indexes = 0
    for table in tables:
        local_cur.execute(f'SELECT COUNT(*) FROM "{table}";')
        local_n = local_cur.fetchone()[0]
        cloud_n, idx_n = migrate_table(local_cur, cloud_conn, cloud_cur, table, local_n)
        status = "OK" if local_n == cloud_n else f"MISMATCH (local={local_n})"
        print(f"  {table:35s} {cloud_n:>9,} rows  {idx_n} idx  [{status}]")
        total_rows += cloud_n
        total_indexes += idx_n

    local_conn.close()
    cloud_conn.close()
    print(f"\nDone. {total_rows:,} total rows, {total_indexes} indexes, across {len(tables)} tables.")


if __name__ == "__main__":
    main()
