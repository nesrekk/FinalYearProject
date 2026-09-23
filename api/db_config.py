"""
db_config.py
=============
Shared Postgres connection config for the api/ services, loaded from
api/.env instead of hardcoded per-service. .env is gitignored — real
credentials never get committed.

Usage:
    from db_config import DB_CONFIG
    DB_POOL = pool.SimpleConnectionPool(minconn=1, maxconn=10, **DB_CONFIG)
"""

import os
from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD"),
    "dbname": os.getenv("DB_NAME", "nba_analytics"),
}
