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


def test_mvp_ledger_summary():
    from mvp_api import app
    resp = TestClient(app).get("/ledger/summary")
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert "live" in data
        assert "resolved" in data
        assert set(data["live"].keys()) == {"mvp", "dpoy", "roy", "all_nba"}


# ─── similarity_api (port 8001) ─────────────────────────────────────────────

def test_similarity_docs_load():
    from similarity_api import app
    resp = TestClient(app).get("/docs")
    assert resp.status_code == 200


def test_similarity_league_evolution():
    from similarity_api import app
    resp = TestClient(app).get("/clusters/evolution")
    assert resp.status_code == 200
    data = resp.json()
    assert data["seasons"]
    assert data["archetypes"]
    assert set(data["archetype_shares"].keys()) == set(data["archetypes"])
    assert len(data["trends"]) == len(data["seasons"])


# ─── impact_api (port 8002) ─────────────────────────────────────────────────

def test_impact_playoff_comparison_shape():
    from impact_api import app
    resp = TestClient(app).get(
        "/players/playoff-comparison/Nikola Jokic", params={"season": 2025}
    )
    assert resp.status_code in (200, 404)


def test_impact_draft_prospect_comp_with_measurements():
    from impact_api import app
    client = TestClient(app)
    resp = client.get("/prospects/comp/Zach Edey")
    assert resp.status_code == 200
    data = resp.json()
    assert "combine_measurements" in data["prospect"]
    assert data["measurements_included"] is False

    resp2 = client.get("/prospects/comp/Zach Edey", params={"include_measurements": True})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["measurements_included"] is True
    assert data2["bridge_pool_size"] <= data["bridge_pool_size"]


def test_impact_length_study():
    from impact_api import app
    resp = TestClient(app).get("/draft/length-study")
    assert resp.status_code == 200
    data = resp.json()
    ids = [p["player_id"] for p in data["points"]]
    assert len(ids) == len(set(ids))  # no duplicate players
    assert data["n"] == len(ids)
    assert "wingspan_minus_height_vs_dbpm" in data["correlations"]


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


def test_impact_with_without_star():
    from impact_api import app
    resp = TestClient(app).get(
        "/teams/with-without/PHI/2024", params={"player_name": "Joel Embiid"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["with_player"]["n"] + data["without_player"]["n"] > 70  # ~real 82-game season
    assert data["with_player"]["win_pct"] is not None


def test_impact_pair_synergy():
    from impact_api import app
    resp = TestClient(app).get(
        "/players/pair-synergy",
        params={"player_a": "Nikola Jokic", "player_b": "Jamal Murray"},
    )
    assert resp.status_code in (200, 503)  # 503 if the model hasn't been trained yet
    if resp.status_code == 200:
        data = resp.json()
        assert "predicted_synergy" in data
        assert "predicted_pair_net_rating" in data
        # These two have shared real minutes on DEN every season on file.
        assert data["observed"] is not None
        assert data["observed"]["min"] > 0


def test_impact_rest_study():
    from impact_api import app
    resp = TestClient(app).get("/schedule/rest-study")
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        b2b = next((b for b in data["buckets"] if b["rest_days"] == 0), None)
        rested = next((b for b in data["buckets"] if b["rest_days"] == 1), None)
        assert b2b and rested
        # Real, well-documented effect: back-to-backs should show a lower real win% than one rest day.
        assert b2b["win_pct"] < rested["win_pct"]


def test_impact_schedule_difficulty():
    from impact_api import app
    resp = TestClient(app).get("/schedule/difficulty")
    assert resp.status_code in (200, 404, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert len(data["results"]) == 30
        miles = [r["total_travel_miles"] for r in data["results"] if r["total_travel_miles"] is not None]
        assert miles == sorted(miles, reverse=True)  # ranked by real total travel, descending


def test_impact_attach_rest_tags():
    # Tests the rest-tag attachment logic directly against real stored
    # schedule data, rather than through /games/by-date — that endpoint's
    # live scoreboard fetch is slow for historical dates (a pre-existing
    # characteristic unrelated to this feature), which would make the
    # smoke suite slow for no real coverage benefit.
    import impact_api
    games = [{"away": {"abbr": "ORL"}, "home": {"abbr": "LAC"}}]
    result = impact_api._attach_rest_tags(games, "2023-10-31")
    away_rest = result[0]["away"].get("rest")
    home_rest = result[0]["home"].get("rest")
    if away_rest and home_rest:  # only assert specifics if team_game_fatigue is populated
        assert away_rest["rest_days"] == 0 and away_rest["is_b2b"] is True
        assert home_rest["rest_days"] == 1
        assert away_rest["rest_disadvantage"] is True
        assert home_rest["rest_disadvantage"] is False


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


def test_impact_hustle_leaders():
    from impact_api import app
    resp = TestClient(app).get("/hustle/leaders", params={"stat": "deflections", "top_n": 5})
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert data["stat"] == "deflections"
        assert len(data["results"]) <= 5
        assert all("player_name" in r and "value" in r for r in data["results"])


def test_impact_playtype_profile():
    from impact_api import app
    resp = TestClient(app).get("/players/playtype-profile/Nikola Jokic")
    assert resp.status_code in (200, 404, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert data["play_types"]
        assert all("freq" in pt and "ppp" in pt for pt in data["play_types"])


# ─── similarity_api: Offensive Style Clusters ───────────────────────────────

def test_similarity_playtype_archetypes():
    from similarity_api import app
    resp = TestClient(app).get("/clusters/playtype-archetypes")
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert data["styles"]
        assert all("style" in s and "n_player_seasons" in s for s in data["styles"])


def test_similarity_playtype_season_clusters():
    from similarity_api import app
    resp = TestClient(app).get("/clusters/playtype-season/2025")
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert data["season"] == 2025
        assert data["players"]
        assert all("pca_x" in p and "style" in p for p in data["players"])
