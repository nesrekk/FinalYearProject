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

LOCAL_ONLY (api/local_only.py; round 8 step 10, the owner's decision of 2026-10-05): six paper-only
per-unit tables (~0.6 GB with indexes) that no page reads stay in the local database. They are never
copied (asking for one with --tables stops the run), --check doesn't count them as missing, and
--drop-local-only removes them from Layerbase. Compare contents (not just counts) with
`DB_TARGET=layerbase python3 paper_manifest.py --out $TMPDIR/lb` then `paper_manifest.py --compare $TMPDIR/lb`.

Usage:
    cd scripts && python3 migrate_to_layerbase.py --all              # every table except LOCAL_ONLY
    (no flag, --help, or an unknown flag: prints this and writes nothing)
    cd scripts && python3 migrate_to_layerbase.py --tables a,b,c     # just these
    cd scripts && python3 migrate_to_layerbase.py --check            # compare row counts only, no writes
    cd scripts && python3 migrate_to_layerbase.py --drop-local-only  # drop the LOCAL_ONLY tables on Layerbase
    cd scripts && python3 migrate_to_layerbase.py --reindex [a,b]     # REINDEX TABLE on Layerbase, biggest index first

The copy creates each table's primary key before loading it, so the key index is built row by row and loosely
packed (pbp_events' was 181 MB on Layerbase vs 133 MB after a re-pack): run --reindex after a sync (all tables, or the
ones just copied). One table at a time, a fresh connection each, a 20 s lock timeout (a table another session holds a
lock on is skipped and named); it changes no data.
"""

import io
import os
import sys

import psycopg2
from dotenv import load_dotenv

_API_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api")
_ENV_PATH = os.path.join(_API_DIR, ".env")
load_dotenv(_ENV_PATH)
if _API_DIR not in sys.path:
    sys.path.append(_API_DIR)

from local_only import LOCAL_ONLY  # noqa: E402  (never copied to Layerbase)

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


def drop_local_only(cloud_conn, cloud_cur):
    """Drop every LOCAL_ONLY table that is on Layerbase; prints each one's size before it goes."""
    freed = 0
    for table in LOCAL_ONLY:
        cloud_cur.execute("SELECT pg_total_relation_size(to_regclass(%s));", (f"public.{table}",))
        size = cloud_cur.fetchone()[0]
        if size is None:
            print(f"  {table:35s} not on Layerbase")
            continue
        cloud_cur.execute(f'DROP TABLE "{table}";')
        cloud_conn.commit()
        freed += size
        print(f"  {table:35s} dropped ({size / 1e6:,.1f} MB with its indexes)")
    print(f"Dropped LOCAL_ONLY tables: {freed / 1e6:,.1f} MB freed.")


def reindex(only=None):
    """REINDEX TABLE on Layerbase one table at a time, biggest index first; prints the index size before and after."""
    import time
    mb = 1e6
    with psycopg2.connect(**CLOUD_CONFIG) as conn, conn.cursor() as cur:
        cur.execute("""SELECT c.relname, pg_indexes_size(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = 'public' AND c.relkind = 'r' AND pg_indexes_size(c.oid) > 0 ORDER BY 2 DESC""")
        todo = [(t, size) for t, size in cur.fetchall() if only is None or t in only]
    conn.close()
    before = after = 0
    for table, size in todo:
        conn = psycopg2.connect(**CLOUD_CONFIG)
        conn.autocommit = True
        cur = conn.cursor()
        t0 = time.time()
        cur.execute("SET lock_timeout = '20s'")
        try:
            cur.execute(f'REINDEX TABLE "{table}"')
        except psycopg2.errors.LockNotAvailable:
            print(f"  {table:35s} skipped: another session holds a lock on it", flush=True)
            conn.close()
            continue
        cur.execute("SELECT pg_indexes_size(%s::regclass)", (table,))
        new = cur.fetchone()[0]
        conn.close()
        before, after = before + size, after + new
        print(f"  {table:35s} {size / mb:8.1f} -> {new / mb:8.1f} MB  ({time.time() - t0:.0f} s)", flush=True)
    print(f"Indexes of the re-packed tables: {before / mb:,.1f} -> {after / mb:,.1f} MB.")


MODES = ("--all", "--tables", "--check", "--drop-local-only", "--reindex")


def main():
    # Round 9 step 8a (R9-042): an unknown flag (even --help) used to fall through to "copy every table". Now a run
    # needs exactly one known mode, and a full copy needs --all; anything else prints the usage and writes nothing.
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    modes = [a for a in flags if a in MODES]
    if any(a not in MODES for a in flags) or len(modes) != 1:
        print(__doc__)
        raise SystemExit(0 if set(flags) & {"-h", "--help"} else 2)
    if "--reindex" in sys.argv:
        i = sys.argv.index("--reindex")
        only = set(sys.argv[i + 1].split(",")) if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("--") else None
        reindex(only)
        return
    local_conn = psycopg2.connect(**LOCAL_CONFIG)
    local_cur = local_conn.cursor()
    cloud_conn = psycopg2.connect(**CLOUD_CONFIG)
    cloud_cur = cloud_conn.cursor()

    all_tables = list_tables(local_cur)
    tables = [t for t in all_tables if t not in LOCAL_ONLY]
    if "--drop-local-only" in sys.argv:
        drop_local_only(cloud_conn, cloud_cur)
        return
    if "--check" in sys.argv:
        cloud_cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
        cloud_tables = {r[0] for r in cloud_cur.fetchall()}
        mismatches = 0
        for table in tables:
            local_cur.execute(f'SELECT COUNT(*) FROM "{table}";')
            local_n = local_cur.fetchone()[0]
            if table in cloud_tables:
                cloud_cur.execute(f'SELECT COUNT(*) FROM "{table}";')
                cloud_n = cloud_cur.fetchone()[0]
            else:
                cloud_n = None
            if cloud_n != local_n:
                mismatches += 1
                print(f"  {table:35s} local {local_n:>9,}  cloud {cloud_n if cloud_n is not None else 'missing'}")
        on_cloud = sorted(set(LOCAL_ONLY) & cloud_tables)
        extra = sorted(cloud_tables - set(all_tables))
        print(f"{len(tables)} local tables to mirror, {mismatches} differ from Layerbase by row count. "
              f"Kept local, not compared: {', '.join(LOCAL_ONLY)}.")
        if on_cloud:
            print(f"LOCAL_ONLY tables still on Layerbase (--drop-local-only removes them): {', '.join(on_cloud)}")
        if extra:
            print(f"On Layerbase but not local: {', '.join(extra)}")
        return
    if "--tables" in sys.argv:
        wanted = sys.argv[sys.argv.index("--tables") + 1].split(",")
        unknown = set(wanted) - set(all_tables)
        if unknown:
            raise SystemExit(f"Not local tables: {', '.join(sorted(unknown))}")
        kept = set(wanted) & set(LOCAL_ONLY)
        if kept:
            raise SystemExit(f"Kept local, never copied (api/local_only.py): {', '.join(sorted(kept))}")
        tables = [t for t in tables if t in wanted]
    print(f"Migrating {len(tables)} tables (kept local: {', '.join(LOCAL_ONLY)})...")

    total_rows = 0
    total_indexes = 0
    for table in tables:
        local_cur.execute(f'SELECT COUNT(*) FROM "{table}";')
        local_n = local_cur.fetchone()[0]
        cloud_n, idx_n = migrate_table(local_cur, cloud_conn, cloud_cur, table, local_n)
        status = "OK" if local_n == cloud_n else f"MISMATCH (local={local_n})"
        print(f"  {table:35s} {cloud_n:>9,} rows  {idx_n} idx  [{status}]", flush=True)
        total_rows += cloud_n
        total_indexes += idx_n

    local_conn.close()
    cloud_conn.close()
    print(f"\nDone. {total_rows:,} total rows, {total_indexes} indexes, across {len(tables)} tables.")


if __name__ == "__main__":
    main()
