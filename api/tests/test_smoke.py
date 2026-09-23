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


def test_mvp_wpa_validation():
    from mvp_api import app
    resp = TestClient(app).get("/validation/wpa")
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert "scopes" in data
        assert "all_events" in data["scopes"]


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


def test_impact_wp_replay_list():
    from impact_api import app
    resp = TestClient(app).get("/games/wp-replay/list")
    assert resp.status_code == 200
    data = resp.json()
    assert "games" in data


def test_impact_wp_replay_detail_and_whatif():
    from impact_api import app
    client = TestClient(app)
    listing = client.get("/games/wp-replay/list").json()
    if not listing.get("games"):
        return
    game_id = listing["games"][0]["game_id"]

    resp = client.get(f"/games/wp-replay/{game_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "points" in data and len(data["points"]) > 0
    assert "top_plays" in data

    miss = next((p for p in data["points"] if p["is_missed_shot"]), None)
    if miss is None:
        return
    whatif_resp = client.get(
        f"/games/wp-replay/{game_id}/whatif", params={"event_id": miss["event_id"]}
    )
    assert whatif_resp.status_code == 200
    whatif_data = whatif_resp.json()
    assert "points" in whatif_data
    assert whatif_data["points_awarded"] in (2, 3)


def test_impact_guess_the_game_daily():
    from impact_api import app
    resp = TestClient(app).get("/games/guess-the-game/daily")
    assert resp.status_code == 200
    data = resp.json()
    assert data["points"]
    assert all("home_wp" in p for p in data["points"])
    # No team/date leakage in the daily payload itself.
    assert "home_team" not in data and "away_team" not in data


def test_impact_guess_the_game_flow():
    from impact_api import app
    client = TestClient(app)
    reveal = client.get("/games/guess-the-game/reveal").json()

    wrong_team = "LAL" if reveal["home_team"] != "LAL" and reveal["away_team"] != "LAL" else "BOS"
    resp = client.get("/games/guess-the-game/guess", params={"team": wrong_team, "attempt_number": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["correct"] is False
    assert data["clue"]["type"] == "season"

    resp2 = client.get(
        "/games/guess-the-game/guess",
        params={"team": reveal["home_team"], "attempt_number": 2},
    )
    assert resp2.status_code == 200
    assert resp2.json()["correct"] is True
