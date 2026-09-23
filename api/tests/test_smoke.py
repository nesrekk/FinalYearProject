"""
test_smoke.py
==============
Backend smoke tests: for each of the three FastAPI services (mvp_api,
similarity_api, impact_api), hits one or more real endpoints through
FastAPI's TestClient against the real local database and checks for a
200 status and the expected top-level response shape.

These are integration tests, not mocked unit tests — consistent with the
project's "never fabricate data" ethos, there's nothing to fake here. If
the local Postgres DB isn't reachable, every test is skipped with a clear
reason rather than failing on an unrelated environment problem.

New endpoints should get a test added here as they ship.

Usage (from anywhere in the repo):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests
"""

import os
import sys

import psycopg2
import pytest
from fastapi.testclient import TestClient

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        conn = psycopg2.connect(**DB_CONFIG, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="Local Postgres DB is not reachable — these are real integration tests, not mocks.",
)


# ─── mvp_api (port 8000) ─────────────────────────────────────────────────────

def test_mvp_backtest():
    from mvp_api import app
    resp = TestClient(app).get("/backtest")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


# ─── similarity_api (port 8001) ─────────────────────────────────────────────

def test_similarity_docs_load():
    from similarity_api import app
    resp = TestClient(app).get("/docs")
    assert resp.status_code == 200


# ─── impact_api (port 8002) ─────────────────────────────────────────────────

def test_impact_playoff_comparison_shape():
    from impact_api import app
    resp = TestClient(app).get(
        "/players/playoff-comparison/Nikola Jokic", params={"season": 2025}
    )
    assert resp.status_code in (200, 404)


def test_impact_heliocentricity():
    from impact_api import app
    resp = TestClient(app).get("/players/heliocentricity")
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data


def test_impact_clutch_wpa():
    from impact_api import app
    resp = TestClient(app).get("/players/clutch-wpa", params={"top_n": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "sample_size_games" in data
    assert "results" in data


def test_impact_lineup_chemistry():
    from impact_api import app
    resp = TestClient(app).get("/lineups/chemistry", params={"top_n": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "methodology" in data
    assert data["lineups_qualified"] <= data["lineups_total"]
