"""
migrate_to_supabase.py
========================
One-time (re-runnable) migration of the local Postgres database to the
project's Supabase cloud instance, so a cloud/remote session (which can't
reach localhost) can query the same real data.

Skips player_shots and pbp_events (959MB + 911MB = ~1.9GB of the local
DB's 2.1GB total) to stay under Supabase's free-tier 500MB cap. The
local DB stays the full, complete source of truth — shot charts and
Game Replay/play-by-play features simply aren't available when
DB_TARGET=cloud, a real, disclosed limitation, not a bug.

No pg_dump/psql binary exists on this machine (checked directly), so
this reconstructs each table's schema generically from information_schema
(columns, types, nullability, primary key) rather than using pg_dump's
own dump format, then streams real rows over with COPY. Simplification,
disclosed: SERIAL/sequence defaults aren't recreated (the copied data
already has explicit real IDs; a cloud-side INSERT with no ID would need
a real sequence this script doesn't set up), and indexes beyond the
primary key aren't recreated — this migration is for real read access
from a cloud session, not full local/cloud parity.

Usage:
    cd scripts && python3 migrate_to_supabase.py
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
    "host": os.getenv("SUPABASE_DB_HOST"),
    "port": os.getenv("SUPABASE_DB_PORT", "5432"),
    "user": os.getenv("SUPABASE_DB_USER"),
    "password": os.getenv("SUPABASE_DB_PASSWORD"),
    "dbname": os.getenv("SUPABASE_DB_NAME", "postgres"),
}

EXCLUDE_TABLES = {"player_shots", "pbp_events"}

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


def migrate_table(local_cur, cloud_conn, cloud_cur, table):
    columns = get_columns(local_cur, table)
    col_names = [c[0] for c in columns]
    pk_cols = get_primary_key(local_cur, table)

    ddl = build_create_table(table, columns, pk_cols)
    cloud_cur.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE;')
    cloud_cur.execute(ddl)

    col_list = ", ".join(f'"{c}"' for c in col_names)
    buf = io.StringIO()
    local_cur.copy_expert(f'COPY "{table}" ({col_list}) TO STDOUT WITH CSV', buf)
    buf.seek(0)
    cloud_cur.copy_expert(f'COPY "{table}" ({col_list}) FROM STDIN WITH CSV', buf)
    cloud_conn.commit()

    cloud_cur.execute(f'SELECT COUNT(*) FROM "{table}";')
    return cloud_cur.fetchone()[0]


def main():
    local_conn = psycopg2.connect(**LOCAL_CONFIG)
    local_cur = local_conn.cursor()
    cloud_conn = psycopg2.connect(**CLOUD_CONFIG)
    cloud_cur = cloud_conn.cursor()

    tables = [t for t in list_tables(local_cur) if t not in EXCLUDE_TABLES]
    print(f"Migrating {len(tables)} tables (skipping {sorted(EXCLUDE_TABLES)})...")

    total_rows = 0
    for table in tables:
        local_cur.execute(f'SELECT COUNT(*) FROM "{table}";')
        local_n = local_cur.fetchone()[0]
        cloud_n = migrate_table(local_cur, cloud_conn, cloud_cur, table)
        status = "OK" if local_n == cloud_n else f"MISMATCH (local={local_n})"
        print(f"  {table:35s} {cloud_n:>8,} rows  [{status}]")
        total_rows += cloud_n

    local_conn.close()
    cloud_conn.close()
    print(f"\nDone. {total_rows:,} total rows migrated across {len(tables)} tables.")


if __name__ == "__main__":
    main()
