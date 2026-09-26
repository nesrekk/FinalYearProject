"""
db_config.py
=============
Shared Postgres connection config for scripts/, loaded from api/.env (the
same file the api/ services read) instead of hardcoded per-script. Resolves
api/.env by an absolute path from this file's own location, so it works
regardless of which directory a script is run from.

DB_TARGET in .env picks which database DB_CONFIG points at:
  - "local" (default): the DB_HOST/DB_USER/... vars, unchanged behavior.
  - "cloud": the SUPABASE_DB_HOST/... vars (Supabase Session pooler).
  - "layerbase": the LAYERBASE_DB_HOST/... vars (Layerbase Postgres, full
    parity mirror including player_shots/pbp_events).

Usage:
    from db_config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
"""

import os
from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api", ".env")
load_dotenv(_ENV_PATH)

DB_TARGET = os.getenv("DB_TARGET", "local").lower()

if DB_TARGET == "cloud":
    DB_CONFIG = {
        "host": os.getenv("SUPABASE_DB_HOST"),
        "port": os.getenv("SUPABASE_DB_PORT", "5432"),
        "user": os.getenv("SUPABASE_DB_USER"),
        "password": os.getenv("SUPABASE_DB_PASSWORD"),
        "dbname": os.getenv("SUPABASE_DB_NAME", "postgres"),
    }
elif DB_TARGET == "layerbase":
    DB_CONFIG = {
        "host": os.getenv("LAYERBASE_DB_HOST"),
        "port": os.getenv("LAYERBASE_DB_PORT", "5432"),
        "user": os.getenv("LAYERBASE_DB_USER"),
        "password": os.getenv("LAYERBASE_DB_PASSWORD"),
        "dbname": os.getenv("LAYERBASE_DB_NAME", "postgres"),
    }
else:
    DB_CONFIG = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD"),
        "dbname": os.getenv("DB_NAME", "nba_analytics"),
    }
