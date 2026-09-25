"""
db_config.py
=============
Shared Postgres connection config for the api/ services, loaded from
api/.env instead of hardcoded per-service. .env is gitignored — real
credentials never get committed.

DB_TARGET in .env picks which database DB_CONFIG points at:
  - "local" (default): the DB_HOST/DB_USER/... vars, unchanged behavior.
  - "cloud": the SUPABASE_DB_HOST/... vars (Supabase Session pooler).

Usage:
    from db_config import DB_CONFIG
    DB_POOL = pool.SimpleConnectionPool(minconn=1, maxconn=10, **DB_CONFIG)
"""

import os
from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
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
else:
    DB_CONFIG = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD"),
        "dbname": os.getenv("DB_NAME", "nba_analytics"),
    }
