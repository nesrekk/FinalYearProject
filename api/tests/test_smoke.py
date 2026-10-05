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


def test_mvp_wpa_model_compare():
    from mvp_api import app
    resp = TestClient(app).get("/validation/wpa/compare")
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        model_types = {m["model_type"] for m in data["models"]}
        assert model_types == {"logreg_calibrated", "gradient_boosting"}
        for m in data["models"]:
            assert 0 <= m["scopes"]["all_events"]["roc_auc"] <= 1


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


def test_similarity_season_profile_matches_stored_and_filters():
    from similarity_api import app
    client = TestClient(app)
    # Unfiltered, the live computation must reproduce the stored top 10.
    stored = client.get("/similarity/season/Stephen Curry/2016").json()["results"]
    resp = client.get("/similarity/season-profile/Stephen Curry/2016")
    assert resp.status_code == 200
    data = resp.json()
    _assert_has_source(data)
    live = data["results"]
    assert [(r["player_id"], r["season"]) for r in live] == [(r["player_id"], r["season"]) for r in stored]
    for a, b in zip(live, stored):
        assert abs(a["similarity_score"] - b["similarity_score"]) < 1e-3
    assert data["query"]["stats"]["pts"] == pytest.approx(30.1)

    # Filters: no Curry seasons, no short seasons, scores still descending.
    data = client.get("/similarity/season-profile/Stephen Curry/2016",
                      params={"exclude_self": True, "min_gp": 40, "top_n": 25}).json()
    rows = data["results"]
    assert len(rows) == 25
    assert all(r["player_id"] != data["query"]["player_id"] for r in rows)
    assert all(r["gp"] >= 40 for r in rows)
    scores = [r["similarity_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    assert all(len(r["closest_on"]) == 2 and r["differs_most"]["feature"] for r in rows)
    data = client.get("/similarity/season-profile/Stephen Curry/2016",
                      params={"one_per_player": True, "top_n": 25}).json()
    ids = [r["player_id"] for r in data["results"]]
    assert len(ids) == len(set(ids)) == 25

    # Before 2009-10 the inputs don't exist: a clear 404, not an empty list.
    resp = client.get("/similarity/season-profile/Michael Jordan/1996")
    assert resp.status_code == 404
    assert "2009-10" in resp.json()["detail"]


def test_similarity_stat_line_finds_real_seasons():
    from similarity_api import app, LINE_STATS, LINE_ATTEMPTS
    from routers.leaderboard import STATS, ATTEMPT_DEFAULTS
    # First seasons and attempt floors must match the Leaderboard catalogue.
    for k, (_label, _fmt, first, att) in LINE_STATS.items():
        assert STATS[k][3] == first and STATS[k][5] == att, k
    assert LINE_ATTEMPTS == ATTEMPT_DEFAULTS
    client = TestClient(app)

    # A real season's own line, read in its own season, finds itself at 0.
    resp = client.get("/similarity/stat-line", params={
        "line": "pts:30.1,ast:6.7,ts_pct:0.669,fg3a:11.2", "season": 2016})
    assert resp.status_code == 200
    data = resp.json()
    _assert_has_source(data)
    top = data["results"][0]
    assert (top["player_name"], top["season"], top["distance"]) == ("Stephen Curry", 2016, 0.0)
    assert [x["key"] for x in data["line"]] == ["pts", "ast", "ts_pct", "fg3a"]
    assert all(x["season_n"] > 100 for x in data["line"])
    dists = [r["distance"] for r in data["results"]]
    assert dists == sorted(dists)
    ids = [r["player_id"] for r in data["results"]]
    assert len(ids) == len(set(ids))  # one season per player by default
    assert all(r["gp"] >= 20 for r in data["results"])
    assert all(abs(sum(r["share_of_distance"].values()) - 1) < 0.01 for r in data["results"][1:])

    data = client.get("/similarity/stat-line", params={"line": "pts:50.4,reb:25.7", "season": 1962}).json()
    assert (data["results"][0]["player_name"], data["results"][0]["season"]) == ("Wilt Chamberlain", 1962)

    # Season range: every match inside it.
    data = client.get("/similarity/stat-line", params={
        "line": "blk:3,reb:12", "season_from": 2015, "season_to": 2020}).json()
    assert all(2015 <= r["season"] <= 2020 for r in data["results"])

    # Clear errors: a stat not recorded in the chosen season, bad keys, percent as 60.
    resp = client.get("/similarity/stat-line", params={"line": "stl:2,pts:20", "season": 1970})
    assert resp.status_code == 400 and "1973-74" in resp.json()["detail"]
    assert client.get("/similarity/stat-line", params={"line": "foo:2"}).status_code == 400
    assert client.get("/similarity/stat-line", params={"line": "ts_pct:60"}).status_code == 400


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


def test_impact_clutch_split():
    from impact_api import app
    client = TestClient(app)
    assert client.get("/players/clutch-split", params={"min_clutch_chances": 7}).status_code == 400
    resp = client.get("/players/clutch-split", params={"min_clutch_chances": 100})
    assert resp.status_code == 200
    data = resp.json()
    league = data["league"]
    # Leverage-neutral WPA per scoring chance is in points; the league's real
    # points / (FGA + FTA + TOV) was 0.90-0.93 in 2020-21 to 2025-26.
    assert 0.85 < league["nonclutch_pts_rate"] < 0.97
    assert league["clutch_pts_rate"] < league["nonclutch_pts_rate"]
    assert league["clutch_leverage_ratio"] > 2
    results = data["results"]
    assert len(results) == data["n_players"] > 50
    assert all(r["clutch_chances"] >= 100 for r in results)
    assert all(r["ci_low"] <= r["clutch_lift"] <= r["ci_high"] for r in results)
    # Clutch differences are mostly noise: few players clear 95%.
    assert data["n_outside_zero"] < 0.2 * data["n_players"]


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
    import impact_core
    games = [{"away": {"abbr": "ORL"}, "home": {"abbr": "LAC"}}]
    result = impact_core._attach_rest_tags(games, "2023-10-31")
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

    # A real game has dozens of missed shots; the default season is now the
    # latest one, which is ESPN-sourced (see fetch_pbp_espn.py) — this count
    # would silently be 0 if missed-shot detection only understood nba_api's
    # "Missed Shot" action_type vocabulary and not ESPN's own.
    misses = [p for p in data["points"] if p["is_missed_shot"]]
    assert len(misses) > 10

    miss = misses[0]
    whatif_resp = client.get(
        f"/games/wp-replay/{game_id}/whatif", params={"event_id": miss["event_id"]}
    )
    assert whatif_resp.status_code == 200
    whatif_data = whatif_resp.json()
    assert "points" in whatif_data
    assert whatif_data["points_awarded"] in (2, 3)


def test_impact_wp_replay_whatif_3pt_scoring():
    from impact_api import app
    client = TestClient(app)
    listing = client.get("/games/wp-replay/list").json()
    if not listing.get("games"):
        return

    # Real 3-point misses say "3PT" (nba_api) or "three point" (ESPN) in
    # their real description, never both vocabularies for the same source —
    # find one and confirm it's scored as a real 3, not silently as a 2.
    for game in listing["games"][:5]:
        data = client.get(f"/games/wp-replay/{game['game_id']}").json()
        three_pt_miss = next(
            (p for p in data.get("points", [])
             if p["is_missed_shot"] and ("3pt" in (p["description"] or "").lower()
                                          or "three point" in (p["description"] or "").lower())),
            None,
        )
        if three_pt_miss is None:
            continue
        whatif_data = client.get(
            f"/games/wp-replay/{game['game_id']}/whatif", params={"event_id": three_pt_miss["event_id"]}
        ).json()
        assert whatif_data["points_awarded"] == 3
        return


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


# ─── impact_api: Matchup Finder ─────────────────────────────────────────────

def test_impact_matchups_scorer_role():
    from impact_api import app
    resp = TestClient(app).get(
        "/matchups/player/Luka Doncic", params={"role": "scorer", "season": 2024, "top_n": 5}
    )
    assert resp.status_code in (200, 404, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert data["role"] == "scorer"
        assert data["toughest"] and data["easiest"]
        assert all("reliable" in r and "matchup_fg_pct" in r for r in data["toughest"])


def test_impact_matchups_defender_role():
    from impact_api import app
    resp = TestClient(app).get(
        "/matchups/player/Luka Doncic", params={"role": "defender", "season": 2024, "top_n": 5}
    )
    assert resp.status_code in (200, 404, 503)
    if resp.status_code == 200:
        assert resp.json()["role"] == "defender"


def test_impact_matchups_invalid_role():
    from impact_api import app
    resp = TestClient(app).get("/matchups/player/Luka Doncic", params={"role": "bogus"})
    assert resp.status_code == 400


# ─── A2: Source Badges ───────────────────────────────────────────────────────
# Spot-checks _source (api/source_badge.py) on one endpoint per service —
# a representative sample of the ~20 main Analytics endpoints that got one
# in the A2 sweep, not an exhaustive per-endpoint test.

def _assert_has_source(data):
    src = data.get("_source")
    assert src is not None
    assert isinstance(src.get("tables"), list) and src["tables"]
    assert src.get("upstream_api")
    assert "live" in src and "as_of" in src


def test_mvp_predict_has_source():
    from mvp_api import app
    resp = TestClient(app).get("/mvp/predict/2025")
    assert resp.status_code == 200
    _assert_has_source(resp.json())


def test_similarity_season_has_source():
    from similarity_api import app
    resp = TestClient(app).get("/similarity/season/LeBron James/2024")
    assert resp.status_code == 200
    _assert_has_source(resp.json())


def test_impact_matchups_has_source():
    from impact_api import app
    resp = TestClient(app).get("/matchups/player/Luka Doncic", params={"season": 2024})
    assert resp.status_code in (200, 404, 503)
    if resp.status_code == 200:
        _assert_has_source(resp.json())


# ─── C6: Referee Tendencies ─────────────────────────────────────────────────

def test_referee_tendencies_shape():
    from impact_api import app
    resp = TestClient(app).get("/referees/tendencies", params={"min_games": 1})
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert "officials" in data and isinstance(data["officials"], list)
        assert "methodology" in data
        _assert_has_source(data)
        for o in data["officials"]:
            assert o["n_games"] >= 1
            assert "small_n_warning" in o
            assert "fouls_diff_pct" in o and "fta_diff_pct" in o


def test_referee_tendencies_min_games_filter():
    from impact_api import app
    resp = TestClient(app).get("/referees/tendencies", params={"min_games": 25})
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert all(o["n_games"] >= 25 for o in data["officials"])
        assert all(not o["small_n_warning"] for o in data["officials"])


def test_referee_crew_tendencies_shape():
    from impact_api import app
    resp = TestClient(app).get("/referees/crew-tendencies", params={"min_games": 1})
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert "crews" in data and isinstance(data["crews"], list)
        assert "methodology" in data
        assert "repeat_crews" in data and "total_crews_tracked" in data
        _assert_has_source(data)
        for c in data["crews"]:
            assert c["n_games"] >= 1
            assert "small_n_warning" in c
            assert "official_names" in c and "official_ids" in c
            assert "fouls_diff_pct" in c and "fta_diff_pct" in c


def test_referee_crew_tendencies_min_games_filter():
    from impact_api import app
    resp = TestClient(app).get("/referees/crew-tendencies", params={"min_games": 3})
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        data = resp.json()
        assert all(c["n_games"] >= 3 for c in data["crews"])


# ─── UI redesign: real landing-page headline stats ──────────────────────────

def test_meta_site_stats():
    from impact_api import app
    resp = TestClient(app).get("/meta/site-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_seasons"] > 0
    assert data["n_player_seasons"] > 0
    assert data["n_college_seasons"] > 0
    assert data["season_max"] >= data["season_min"]
    _assert_has_source(data)


def test_team_comparison_shape():
    from impact_api import app
    resp = TestClient(app).get("/teams/compare/LAL/BOS")
    assert resp.status_code == 200
    data = resp.json()
    for side in ("team_a", "team_b"):
        team = data[side]
        assert team["advanced_stats"]["offRating"] > 80
        assert len(team["roster"]) > 0
        assert team["roster"][0]["pts"] >= team["roster"][-1]["pts"]
    h2h = data["head_to_head"]
    assert h2h["games_played"] == h2h["team_a_wins"] + h2h["team_b_wins"]
    _assert_has_source(data)


def test_team_comparison_unknown_team():
    from impact_api import app
    resp = TestClient(app).get("/teams/compare/ZZZ/BOS")
    assert resp.status_code == 404


def test_news_current_shape():
    from impact_api import app
    resp = TestClient(app).get("/news/current", params={"limit": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data and isinstance(data["items"], list)
    for item in data["items"]:
        assert item["headline"]
        assert item["category"] in (
            "News", "Injuries", "Trade Rumors", "Game Recap", "Player Watch",
        )


def test_hof_career_leaders():
    from impact_api import app
    resp = TestClient(app).get("/hof/career-leaders", params={"stat": "pts", "limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    leaders = data["leaders"]
    assert len(leaders) == 5
    # Real, well-known record: LeBron James passed Kareem for the real
    # all-time scoring lead in 2023 — should be #1 given full 1950-2026 data.
    assert leaders[0]["player_name"] == "LeBron James"
    totals = [l["career_total"] for l in leaders]
    assert totals == sorted(totals, reverse=True)
    _assert_has_source(data)
    # Real NBA 75th Anniversary Team badge — LeBron is on the real list.
    assert leaders[0]["is_nba75"] is True


def test_hof_greatest_seasons():
    from impact_api import app
    resp = TestClient(app).get("/hof/greatest-seasons", params={"stat": "pts", "limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    seasons = data["seasons"]
    # Real NBA record: Wilt Chamberlain's 50.4 PPG in 1961-62 is the real
    # highest-scoring season in NBA history.
    assert seasons[0]["player_name"] == "Wilt Chamberlain"
    assert seasons[0]["season_label"] == "1961-62"
    assert seasons[0]["value"] > 49
    _assert_has_source(data)


def test_hof_greatest_seasons_invalid_stat():
    from impact_api import app
    resp = TestClient(app).get("/hof/greatest-seasons", params={"stat": "not_a_real_column"})
    assert resp.status_code == 400


def test_hof_longevity():
    from impact_api import app
    resp = TestClient(app).get("/hof/longevity", params={"limit": 5})
    assert resp.status_code == 200
    data = resp.json()
    # Real NBA record: Robert Parish played more real career games (1,611)
    # than anyone else in NBA history.
    assert any(l["player_name"] == "Robert Parish" for l in data["leaders"])
    _assert_has_source(data)


def test_backtest_compare_has_three_models():
    """Real 3-model LOSO comparison (scripts/backtest_models.py must have
    been re-run for this to reflect all three — Gradient Boosting was added
    2026-09; see that script's docstring)."""
    from mvp_api import app
    resp = TestClient(app).get("/backtest/MVP/compare")
    assert resp.status_code == 200
    data = resp.json()
    model_types = {m["model_type"] for m in data["models"]}
    assert model_types == {"logreg", "random_forest", "gradient_boosting"}
    for m in data["models"]:
        assert 0 <= m["roc_auc"] <= 1
        assert 0 <= m["top1_accuracy"] <= 1


def test_garbage_time_shape_and_validation():
    """Garbage-Time Deflator (scripts/build_leverage_splits.py). The rebuilt
    per-game scoring must track the real official per-game line almost
    exactly — this is the feature's own real validation metric."""
    from impact_api import app
    resp = TestClient(app).get("/players/garbage-time", params={"season": 2025, "min_ppg": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert data["season"] == 2025
    assert len(data["top_scorers"]) == 30
    assert all(r["qualified"] for r in data["top_scorers"])
    assert all(r["ppg_raw"] >= 10 for r in data["empty_calories"])
    v = data["validation"]
    assert v["ppg_vs_official_r"] > 0.99
    assert v["ppg_vs_official_mae"] < 0.1
    assert v["points_attribution_rate"] > 0.97
    shares = v["event_share_by_bucket"]
    assert abs(sum(shares.values()) - 1) < 1e-6
    for r in data["top_scorers"]:
        assert r["ppg_filtered"] <= r["ppg_ex_garbage"] <= r["ppg_raw"] + 1e-9
    _assert_has_source(data)


def test_garbage_time_player_card():
    from impact_api import app
    client = TestClient(app)
    # Real player: Shai Gilgeous-Alexander (1628983), 2025-26.
    resp = client.get("/players/garbage-time/player/1628983", params={"season": 2026})
    assert resp.status_code == 200
    data = resp.json()
    assert [s["bucket"] for s in data["splits"]] == ["garbage", "low", "medium", "high"]
    assert sum(s["pts"] for s in data["splits"]) == data["player"]["pts"]
    assert data["small_sample_warning"] is False
    _assert_has_source(data)
    assert client.get("/players/garbage-time", params={"season": 1999}).status_code == 404


def test_dad_index_shape_and_sanity():
    """DAD Index (scripts/build_dad_index.py). Real sniff test: 2023-24's
    Lu Dort — one of the league's known point-of-attack stoppers — draws
    harder-than-average assignments."""
    from impact_api import app
    client = TestClient(app)
    resp = client.get("/defense/dad", params={"season": 2024})
    assert resp.status_code == 200
    data = resp.json()
    assert data["season"] == 2024
    defs = data["defenders"]
    assert len(defs) == data["validation"]["n_qualified"] > 200
    assert all(d["total_poss"] >= 1000 for d in defs)
    assert [d["dad"] for d in defs] == sorted((d["dad"] for d in defs), reverse=True)
    assert abs(sum(d["dad_z"] for d in defs) / len(defs)) < 1e-6
    assert data["validation"]["year_over_year_r"] > 0.3
    dort = next(d for d in defs if d["player_name"] == "Luguentz Dort")
    assert dort["dad_z"] > 1
    assert 1 <= len(dort["top_assignments"]) <= 3
    assert dort["quadrant"] in data["quadrants"]
    _assert_has_source(data)
    assert client.get("/defense/dad", params={"season": 2010}).status_code == 404


def test_scouting_report_shape_and_sanity():
    """Scouting Report v1 (scripts/build_scouting_reports.py). Real sniff
    test: 2024-25 Giannis Antetokounmpo's finishing at the rim is a real,
    significant strength."""
    from impact_api import app
    client = TestClient(app)
    resp = client.get("/players/scouting-report/Giannis Antetokounmpo", params={"season": 2025})
    assert resp.status_code == 200
    data = resp.json()
    assert data["qualified"] is True and data["minutes"] >= 1500
    assert data["expected_by_chance"] == round(data["n_tested"] * 0.05, 1)
    for side in ("strengths", "weaknesses"):
        assert len(data[side]) <= 3
        for f in data[side]:
            assert f["significant"] and f["p"] < 0.05 and f["category"] != "leverage"
            assert f["n"] >= 50
    assert any(f["split"] == "Restricted Area" for f in data["strengths"])
    assert all(s["category"] != "leverage" for s in data["all_tested"])
    # Categories are scouting keys only when their own next-season
    # persistence check passes; leverage fails it, shot context (v2) passes.
    assert "leverage" not in data["reliable_categories"]
    assert {"zone", "playtype", "close_def", "touch", "dribbles"} <= set(data["reliable_categories"])
    assert all(s["category"] in data["reliable_categories"] for s in data["all_tested"])
    assert all(s["split"].endswith("|3PT") for s in data["all_tested"]
               if s["category"] in ("close_def", "touch", "dribbles"))
    assert data["cant_tell"]
    assert data["validation"]["persistence"]["zone"]["same_direction_rate"] > 0.6
    assert data["validation"]["zone_classifier_max_fga_error"] < 0.05
    _assert_has_source(data)
    assert client.get("/players/scouting-report/Stephen Curry", params={"season": 2012}).status_code == 404


def test_classify_zone_real_geometry():
    """Restricted area = 4 ft radius from the hoop; the lane ends 13.75 ft in
    front of the hoop (19 ft from the baseline)."""
    from shots_lib import classify_zone
    assert classify_zone(0, 39, 4, "2PT Field Goal") == "Restricted Area"
    assert classify_zone(30, 30, 4, "2PT Field Goal") == "In The Paint (Non-RA)"
    assert classify_zone(0, 137, 14, "2PT Field Goal") == "In The Paint (Non-RA)"
    assert classify_zone(0, 150, 15, "2PT Field Goal") == "Mid-Range"
    assert classify_zone(-230, 50, 23, "3PT Field Goal") == "Corner 3"


def test_league_shot_sample_shape_and_sanity():
    """Landing-page court: a fixed random sample of real latest-season shots,
    with the season's own totals alongside."""
    from impact_api import app
    data = TestClient(app).get("/shots/league-sample", params={"n": 2000}).json()
    assert data["n_sample"] == len(data["points"]) == 2000
    assert data["n_season_shots"] > data["n_sample"]
    for x, y, made in data["points"]:
        assert -250 <= x <= 250 and -50 <= y <= 420 and made in (0, 1)
    # Real NBA field-goal percentages sit in the 0.40s.
    assert 0.40 < data["season_fg_pct"] < 0.52
    assert abs(data["sample_fg_pct"] - data["season_fg_pct"]) < 0.04
    _assert_has_source(data)


def test_learn_basics_shape_and_sanity():
    """Learn the Game: real zone splits, pace, scoring and a real stat line."""
    from impact_api import app
    data = TestClient(app).get("/learn/basics").json()
    zones = {z["zone"]: z for z in data["zones"]}
    assert set(zones) == {"Restricted Area", "In The Paint (Non-RA)", "Mid-Range", "Corner 3", "Above the Break 3"}
    assert sum(z["fga"] for z in data["zones"]) == data["n_shots"]
    assert abs(sum(z["share_of_shots"] for z in data["zones"]) - 1) < 0.01
    # Rim shots are the best shot in basketball; long twos the worst.
    assert zones["Restricted Area"]["points_per_shot"] == max(z["points_per_shot"] for z in data["zones"])
    assert zones["Mid-Range"]["points_per_shot"] < zones["Above the Break 3"]["points_per_shot"]
    assert 0.30 < data["three_share"] < 0.50
    assert data["zone_classifier_check"]["max_fga_error"] < 0.05
    assert 90 < data["pace"]["possessions_per_team_game"] < 110
    assert 100 < data["scoring"]["points_per_team_game"] < 125
    assert data["example_player"]["pts"] > 20
    _assert_has_source(data)


def test_spacing_gravity_shape_and_sanity():
    """Gravity Index (scripts/build_gravity_index.py). Real sniff test:
    Stephen Curry is near the top of 2024-25 Gravity; Rudy Gobert (0 real
    3PA) is in the bottom decile."""
    from impact_api import app
    resp = TestClient(app).get("/spacing/gravity", params={"season": 2025, "top_n": 10})
    assert resp.status_code == 200
    data = resp.json()
    board = data["leaderboard"]
    assert len(board) == 10
    assert [r["gravity"] for r in board] == sorted((r["gravity"] for r in board), reverse=True)
    assert any(r["player_name"] == "Stephen Curry" for r in board)
    gobert = next(p for p in data["players"] if p["player_name"] == "Rudy Gobert")
    assert gobert["percentile"] < 10
    assert data["tracking_coverage"]["share"] > 0.95
    v = data["validation"]
    assert v["n_lineups"] > 1000 and v["ci_low"] <= v["coef_spacing"] <= v["ci_high"]
    assert v["significant"] == (v["p_spacing"] < 0.05)
    _assert_has_source(data)


def test_spacing_lineup_builder():
    from impact_api import app
    client = TestClient(app)
    # Real 2024-25 Knicks starting five (their most-used real lineup).
    five = "1626157,1628384,1628404,1628969,1628973"
    resp = client.get("/spacing/lineup", params={"season": 2025, "player_ids": five})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["players"]) == 5
    assert data["real_lineup"] is not None and data["real_lineup"]["poss"] >= 100
    assert 0 <= data["percentile_vs_real_lineups"] <= 100
    if data["validation"]["significant"]:
        p = data["predicted_ortg_change"]
        assert p["ci_low"] <= p["vs_median_lineup"] <= p["ci_high"]
    else:
        assert data["predicted_ortg_change"] is None and data["no_effect_message"]
    assert data["within_real_range"] is True
    # The five highest-Gravity players far exceed any real lineup's spacing:
    # no extrapolated prediction may be shown.
    top5 = [r["player_id"] for r in client.get("/spacing/gravity", params={"season": 2026, "top_n": 5}).json()["leaderboard"]]
    ext = client.get("/spacing/lineup", params={"season": 2026, "player_ids": ",".join(map(str, top5))}).json()
    assert ext["within_real_range"] is False
    assert ext["predicted_ortg_change"] is None and ext["no_effect_message"]
    assert client.get("/spacing/lineup", params={"player_ids": "1,2,3"}).status_code == 400
    assert client.get("/spacing/lineup", params={"season": 2025, "player_ids": "1626157,1626157,1628404,1628969,1628973"}).status_code == 400


def test_contract_value_shape_and_sanity():
    """Contract Surplus Value (scripts/load_salaries.py -> build_contract_value.py).
    Needs the gitignored nba_data/salaries/ CSVs to have been loaded; skipped
    cleanly if the tables were never built. Real sniff test: Stephen Curry's
    2015-16 contract ($11.4M, the season he was unanimous MVP) is the
    biggest bargain of that season."""
    from impact_api import app
    client = TestClient(app)
    resp = client.get("/contracts/value", params={"season": 2016})
    if resp.status_code == 503:
        pytest.skip("contract_value not built on this machine (salary CSVs are local-only).")
    assert resp.status_code == 200
    data = resp.json()
    assert data["bargains"][0]["player_name"] == "Stephen Curry"
    assert all(l["salary"] >= 25_000_000 for l in data["liabilities"])
    s = data["summary"]
    assert s["included"] and s["cost_per_win"] > 0 and s["minutes_coverage"] >= 0.9
    # Calibrated WAR sums to the real wins above replacement by construction.
    assert 0 < s["war_scale_k"] < 1
    for p in data["bargains"]:
        assert abs(p["fair_value"] - p["salary"] - p["surplus"]) < 1
    _assert_has_source(data)
    assert client.get("/contracts/value", params={"season": 2022}).status_code == 404
    assert client.get("/contracts/player/201939", params={"season": 2022}).json()["available"] is False


def test_college_pipeline_shape_and_sanity():
    """College-to-NBA pipeline (scripts/load_college_teams.py ->
    build_college_pipeline.py). Needs the gitignored Kaggle CSVs; skipped
    cleanly if the tables were never built. Real sniff tests: 2014-15
    Kentucky (38-1, Final Four) was Torvik's No. 1 team and Karl-Anthony
    Towns went first overall from it; the 2025 class is too new to judge."""
    from impact_api import app
    client = TestClient(app)
    resp = client.get("/college/pipeline")
    if resp.status_code == 503:
        pytest.skip("college pipeline not built on this machine (Kaggle CSVs are local-only).")
    assert resp.status_code == 200
    data = resp.json()
    kat = next(p for p in data["players"] if p["player_name"] == "Karl-Anthony Towns")
    assert (kat["college_team"], kat["college_season"], kat["overall_pick"]) == ("Kentucky", 2015, 1)
    assert kat["barthag_rank"] == 1 and kat["postseason"] == "F4" and kat["mature"]
    flagg = next(p for p in data["players"] if p["player_name"] == "Cooper Flagg")
    assert flagg["ws4_vs_expected"] is None and not flagg["mature"]
    assert data["counts"]["analysed"] == sum(p["mature"] for p in data["players"])
    assert sum(t["n"] for t in data["tiers"]) == data["counts"]["analysed"]
    assert sum(r["n"] for r in data["runs"]) == data["counts"]["analysed"]
    e = data["effect"]
    assert e["ci_low"] <= e["ws4_per_10_margin"] <= e["ci_high"]
    assert all(s["n"] >= data["min_school_picks"] for s in data["schools"])
    _assert_has_source(data)


def test_march_madness_shape_and_sanity():
    """March Madness model (scripts/fetch_cbb_games.py -> build_ncaa_model.py).
    Skipped cleanly if never built. Real sniff tests: every backtest champion
    agrees with the independent Torvik/Kaggle file's champions; 2026 is the
    held-out test season; bracket odds are internally consistent."""
    from impact_api import app
    client = TestClient(app)
    resp = client.get("/college/madness")
    if resp.status_code == 503:
        pytest.skip("March Madness model not built on this machine.")
    assert resp.status_code == 200
    data = resp.json()
    assert data["season"] == 2026 and data["season_summary"]["is_test"]
    assert data["season_summary"]["champion"] == "Michigan"
    assert [s["season"] for s in data["seasons"]] == [s for s in range(2013, 2027) if s != 2020]
    assert data["backtest_summary"]["n_seasons"] == 12

    import psycopg2 as _pg
    conn = _pg.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT season, team FROM college_team_seasons WHERE postseason = 'Champions'")
    torvik = dict(cur.fetchall())
    conn.close()
    same_school = {"UConn": "Connecticut"}
    for s in data["seasons"]:
        if s["season"] in torvik:
            assert same_school.get(s["champion"], s["champion"]) == torvik[s["season"]]

    teams = data["teams"]
    assert len(teams) == 68
    assert abs(sum(t["p_champ"] for t in teams) - 1) < 0.01
    assert abs(sum(t["p_f4"] for t in teams) - 4) < 0.02
    for t in teams:
        assert t["p_r64"] >= t["p_r32"] >= t["p_s16"] >= t["p_e8"] >= t["p_f4"] >= t["p_final"] >= t["p_champ"]
    assert len(data["games"]) == 67
    assert data["chosen_feature_set"] in {v["feature_set"] for v in data["variants"]}
    _assert_has_source(data)
    assert client.get("/college/madness", params={"season": 2020}).status_code == 404


def test_draft_value_shape_and_sanity():
    """Draft Value Guide (scripts/load_draft_history_bref.py). Real sniff
    tests: Michael Jordan went 3rd in 1984 and made 14 All-Star teams; 1985's
    first pick is Patrick Ewing with his own NBA id (not his son's); value
    falls with the pick; nobody's NBA id is used for two different people."""
    from impact_api import app
    client = TestClient(app)
    resp = client.get("/draft/value-curve")
    if resp.status_code == 404:
        pytest.skip("draft_history not built on this machine.")
    assert resp.status_code == 200
    avgs = [b["avg_ws_first5"] for b in resp.json()["buckets"]]
    assert avgs == sorted(avgs, reverse=True)
    _assert_has_source(resp.json())

    c1984 = client.get("/draft/1984").json()["results"]
    mj = next(p for p in c1984 if p["player_name"] == "Michael Jordan")
    assert mj["overall_pick"] == 3 and mj["all_star_selections"] == 14 and mj["player_id"] == 893
    ewing = client.get("/draft/1985").json()["results"][0]
    assert ewing["player_name"] == "Patrick Ewing" and ewing["player_id"] == 121

    best = client.get("/draft/best-value", params={"limit": 5}).json()
    assert all(r["value_over_expectation"] > 0 for r in best["results"])

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""SELECT count(*) FROM (SELECT d.player_id FROM draft_history d
                   JOIN draft_pick_outcomes o USING (player_id, draft_year)
                   WHERE d.player_id > 0 GROUP BY d.player_id HAVING count(DISTINCT o.bref_id) > 1) x""")
    shared = cur.fetchone()[0]
    conn.close()
    assert shared == 0


def test_player_roles_shape_and_sanity():
    """Player roles (scripts/build_player_roles.py). Real sniff tests: 2018-19
    James Harden is a Lead Creator, 2012-13 DeAndre Jordan a Rim-Running Big,
    2016-17 Klay Thompson an Above-the-Break Shooter; every role is stable
    under resampling; the six-way family survives for Pair Synergy/Trivia."""
    from similarity_api import app
    client = TestClient(app)
    resp = client.get("/clusters/archetypes")
    if resp.status_code == 404:
        pytest.skip("player roles not built on this machine.")
    roles = resp.json()["archetypes"]
    assert len(roles) == 10 and len({r["archetype"] for r in roles}) == 10
    assert all(r["stability_ari"] >= 0.8 and r["description"] for r in roles)
    for season, name, role in [(2019, "James Harden", "Lead Creator"),
                               (2013, "DeAndre Jordan", "Rim-Running Big"),
                               (2017, "Klay Thompson", "Above-the-Break Shooter")]:
        players = client.get(f"/clusters/season/{season}").json()["players"]
        assert next(p for p in players if p["player_name"] == name)["archetype"] == role
    assert all(p["family"] for p in client.get("/clusters/season/2026").json()["players"])
    _assert_has_source(resp.json())


def test_player_id_map_fixes():
    """player_id_map rebuilt with scripts/bref_nba_ids.py (2026-09-27). Real
    sniff tests: stars once missing from pre-2010 seasons are present with
    their own ids; ids that used to hold two players' careers are split;
    no NBA id is matched to two Basketball-Reference players; Patrick Ewing
    (not his son) is on the 75th Anniversary Team."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM player_id_map")
    if cur.fetchone()[0] == 0:
        conn.close()
        pytest.skip("player_id_map not built on this machine.")
    expect = {121: (1986, 2002), 56: (1991, 2007), 896: (1990, 2003), 913: (1992, 2001),
              2739: (2005, 2008), 77103: (1965, 1972)}
    for pid, span in expect.items():
        cur.execute("SELECT min(season), max(season) FROM player_season_stats WHERE player_id = %s", (pid,))
        assert cur.fetchone() == span, pid
    cur.execute("""SELECT count(*) FROM (SELECT nba_player_id FROM player_id_map
                   WHERE nba_player_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1) x""")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT player_id FROM nba75_team WHERE player_name = 'Patrick Ewing'")
    assert cur.fetchone()[0] == 121
    cur.execute("""SELECT count(*) FROM nba75_team n
                   WHERE NOT EXISTS (SELECT 1 FROM player_season_stats p WHERE p.player_id = n.player_id)""")
    assert cur.fetchone()[0] == 0
    conn.close()


def test_bpm_is_basketball_reference_published():
    """BPM/VORP come from Basketball-Reference (scripts/load_bref_bpm_vorp.py).
    Real sniff tests against the published 2025-26 lines: SGA BPM 11.7 /
    VORP 7.8, Jokic 14.2 / 9.2 (the in-house reproduction had SGA at 22.0);
    the reproduction survives in dbpm_repro for Pair Synergy; the BPM
    leaderboard's top value is in a realistic range."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""SELECT player_name, bpm, vorp, bpm_repro FROM player_season_stats
                   WHERE season = 2026 AND player_name IN ('Shai Gilgeous-Alexander', 'Nikola Jokić')""")
    rows = {r[0]: r[1:] for r in cur.fetchall()}
    conn.close()
    if not rows or rows["Shai Gilgeous-Alexander"][2] is None:
        pytest.skip("published BPM not loaded on this machine.")
    assert rows["Shai Gilgeous-Alexander"][:2] == (11.7, 7.8)
    assert rows["Nikola Jokić"][:2] == (14.2, 9.2)
    assert rows["Shai Gilgeous-Alexander"][2] > 15  # the old reproduction, kept aside
    from impact_api import app
    top = TestClient(app).get("/impact/bpm/2026").json()
    best = max(r["bpm"] for r in (top.get("results") or top.get("leaderboard") or []))
    assert 8 < best < 16


def test_greats_shape_and_sanity():
    """Greats of the Game (scripts/build_greats.py). Real sniff tests: all 76
    of the 75th Anniversary Team plus today's stars by the stated rule;
    Jordan 5 MVPs, 14 All-Star selections, 32,292 points; Kareem 6 MVPs;
    Mikan's totals include his BAA seasons; the three players the NBA CDN
    has no photo for are flagged; Curry's trivia includes the three-point
    record (from the data) and a sourced line with a Wikipedia link."""
    from impact_api import app
    resp = TestClient(app).get("/greats")
    if resp.status_code == 503:
        pytest.skip("greats not built on this machine.")
    assert resp.status_code == 200
    data = resp.json()
    by = {g["player_name"]: g for g in data["greats"]}
    assert data["counts"]["team75"] == 76 and data["counts"]["total"] == len(data["greats"])
    assert data["counts"]["stars"] >= 1 and "All-NBA" in data["stars_rule"]
    mj = by["Michael Jordan"]
    assert (mj["mvps"], mj["all_star"], mj["pts"]) == (5, 14, 32292)
    assert by["Kareem Abdul-Jabbar"]["mvps"] == 6
    assert by["George Mikan"]["first_season"] == 1949
    assert {n for n, g in by.items() if not g["has_photo"]} == {"Jason Kidd", "Lenny Wilkens", "Patrick Ewing"}
    curry = by["Stephen Curry"]["trivia"]
    assert any(t["basis"] == "data" and "three-pointers" in t["text"] for t in curry)
    assert any(t["basis"] == "source" and t["url"].startswith("https://en.wikipedia.org/") for t in curry)
    assert all(g["facts"] for g in data["greats"])
    _assert_has_source(data)


def test_custom_leaderboard_known_records_and_guards():
    from impact_api import app
    client = TestClient(app)
    opts = client.get("/leaderboard/options").json()
    assert {s["key"] for s in opts["stats"]} >= {"pts", "fg3_pct", "bpm", "net_rating"}
    _assert_has_source(opts)

    # Wilt's 50.4 in 1961-62 is the best scoring season on record.
    data = client.get("/leaderboard/custom", params={
        "stat": "pts", "season_from": 1950, "season_to": 2026, "min_gp": 40, "top_n": 3}).json()
    top = data["results"][0]
    assert (top["player_name"], top["season"], top["value"]) == ("Wilt Chamberlain", 1962, 50.4)
    _assert_has_source(data)

    # Steals start in 1973-74: an earlier range is clipped and says so.
    data = client.get("/leaderboard/custom", params={"stat": "stl", "season_from": 1960, "season_to": 2026}).json()
    assert data["filters"]["season_from"] == 1974 and data["notes"]
    assert data["results"][0]["player_name"] == "Alvin Robertson"

    # Shooting percentages get an attempts floor by default.
    data = client.get("/leaderboard/custom", params={"stat": "fg3_pct", "season_from": 2026}).json()
    assert data["filters"]["min_attempts"] == 2.0
    assert all(r["context"]["fg3a"] >= 2.0 for r in data["results"])
    assert data["results"][0]["value"] < 0.6

    # Lower is better for turnovers; unknown stats and impossible ranges fail clearly.
    assert client.get("/leaderboard/custom", params={"stat": "tov"}).json()["filters"]["order"] == "low"
    assert client.get("/leaderboard/custom", params={"stat": "nope"}).status_code == 400
    assert client.get("/leaderboard/custom", params={
        "stat": "net_rating", "season_from": 1990, "season_to": 2000}).status_code == 404


def test_raw_impact_has_games_floor():
    from impact_api import app
    data = TestClient(app).get("/impact/raw/2026", params={"top_n": 20}).json()
    assert data["min_games"] == 30 and data["min_minutes"] == 20.0
    assert data["results"] and all(r["player_name"] != "Colby Jones" for r in data["results"])


def test_award_chances_are_calibrated():
    from mvp_api import app
    client = TestClient(app)
    cal = client.get("/awards/calibration")
    if cal.status_code == 503:
        pytest.skip("award_chance_calibration not built (run scripts/calibrate_award_chances.py)")
    data = cal.json()
    _assert_has_source(data)
    by = {r["award"]: r for r in data["awards"]}
    assert set(by) == {"MVP", "DPOY", "ROY", "ALL_NBA"}
    for award in ("MVP", "DPOY", "ROY"):
        r = by[award]
        # Raw probabilities over-count (several "certain" winners); calibration must beat plain shares.
        assert r["raw_sum_mean"] > 2 and r["logloss_calibrated"] < r["logloss_before"] < r["logloss_uniform"]
    assert abs(by["ALL_NBA"]["chance_sum_mean"] - 15) < 0.5

    # Over the whole field the MVP chances add up to 100%, in the model's order.
    res = client.get("/mvp/predict/2026", params={"top_n": 2000}).json()["results"]
    assert abs(sum(r["mvp_chance"] for r in res) - 1) < 0.01
    chances = [r["mvp_chance"] for r in res]
    assert chances == sorted(chances, reverse=True) and chances[0] < 0.99

    allnba = client.get("/allnba/predict/2026").json()["results"]
    assert all(0 < r["all_nba_chance"] < 1 for r in allnba)


def test_roy_pool_is_first_nba_season():
    from mvp_api import app
    res = TestClient(app).get("/roy/predict/2026", params={"top_n": 500}).json()
    names = {r["player_name"] for r in res["results"]}
    # Real 2025-26 rookies are in; players with earlier NBA stints (missing from
    # player_season_stats but on Basketball-Reference) are not.
    assert {"Cooper Flagg", "Kon Knueppel"} <= names
    assert not names & {"Bronny James", "Alondes Williams", "Daniss Jenkins", "Sidy Cissoko"}
    assert "Basketball-Reference" in res["candidate_pool_rule"]


def test_composite_metric_z_scores_and_guards():
    from impact_api import app
    client = TestClient(app)
    data = client.get("/leaderboard/composite", params={"weights": "pts:1,ts_pct:1", "season_from": 2026}).json()
    _assert_has_source(data)
    top = data["results"][0]
    # Score is the weighted sum of the parts' z-scores, and the list is sorted by it.
    assert abs(top["score"] - sum(p["contribution"] for p in top["parts"].values())) < 0.01
    scores = [r["score"] for r in data["results"]]
    assert scores == sorted(scores, reverse=True)

    # Lower-is-better stats are flipped: fewest turnovers score highest.
    tov = client.get("/leaderboard/composite", params={"weights": "tov:1", "top_n": 5}).json()["results"]
    assert tov[0]["parts"]["tov"]["z"] > 0 and tov[0]["parts"]["tov"]["value"] <= tov[-1]["parts"]["tov"]["value"]

    # Across eras, Jokic 2025-26 leads points + rebounds + assists (z within each season).
    era = client.get("/leaderboard/composite", params={
        "weights": "pts:1,reb:1,ast:1", "season_from": 1974, "season_to": 2026, "top_n": 3}).json()
    assert era["results"][0]["player_name"] == "Nikola Jokić"

    for bad in ("pts:0", "age:1", "nope:1", "pts:9"):
        assert client.get("/leaderboard/composite", params={"weights": bad}).status_code == 400


def test_regression_explorer_known_relationships():
    from impact_api import app
    client = TestClient(app)
    # Assists and turnovers move together strongly; errors are clustered by player.
    d = client.get("/explore/regression", params={"x": "ast", "y": "tov", "season_from": 2016, "season_to": 2026}).json()
    _assert_has_source(d)
    f = d["fit"]
    assert f["r"] > 0.7 and f["ci_low"] > 0 and f["n_players"] < f["n"]
    assert len(d["points"]) <= 2500 and d["filters"]["within_season"] is True
    # Slope agrees with r * sd_y / sd_x (plain OLS identity on the fitted scale).
    assert abs(f["slope"] - f["r"] * f["sd_y"] / f["sd_x"]) < 1e-3

    # Shooting % gets the attempts floor; stats that don't overlap in time fail clearly.
    d = client.get("/explore/regression", params={"x": "usg_pct", "y": "ts_pct", "season_from": 2016}).json()
    assert any("FGA" in n for n in d["notes"])
    assert client.get("/explore/regression", params={
        "x": "net_rating", "y": "bpm", "season_from": 1990, "season_to": 2000}).status_code == 404
    assert client.get("/explore/regression", params={"x": "pts", "y": "pts"}).status_code == 400


def test_breakout_detector_known_seasons():
    from impact_api import app
    client = TestClient(app)
    d = client.get("/explore/breakouts", params={"season": 2017, "top_n": 10}).json()
    _assert_has_source(d)
    names = [r["player_name"] for r in d["results"]]
    # 2016-17: Giannis won Most Improved Player; Jokic broke out.
    assert "Giannis Antetokounmpo" in names and "Nikola Jokić" in names
    scores = [r["score"] for r in d["results"]]
    assert scores == sorted(scores, reverse=True)
    # Score is the mean of the per-stat changes in standing.
    r = d["results"][0]
    assert abs(r["score"] - sum(v["delta_z"] for v in r["stats"].values()) / len(r["stats"])) < 0.02
    # Breakouts partly regress: historically well under 100% of the jump is kept.
    assert 0.3 < d["persistence"]["median_share_kept"] < 0.95

    down = client.get("/explore/breakouts", params={"season": 2017, "direction": "down", "top_n": 5}).json()
    assert all(x["score"] < 0 for x in down["results"])
    assert client.get("/explore/breakouts", params={"season": 1950}).status_code == 404
    assert client.get("/explore/breakouts", params={"stats": "age"}).status_code == 400


def test_stat_stability_known_rules_of_thumb():
    """Split-half reliability per catalogue stat (scripts/build_stat_stability.py)."""
    from impact_api import app
    from routers.leaderboard import STATS
    client = TestClient(app)
    resp = client.get("/leaderboard/stability")
    if resp.status_code == 503:
        pytest.skip("stat_stability not built (run scripts/build_stat_stability.py)")
    d = resp.json()
    _assert_has_source(d)
    by = {s["key"]: s for s in d["stats"]}
    # Every catalogue stat but age is covered, and the script's list matches the catalogue.
    assert set(by) == set(STATS) - {"age"}
    sys.path.insert(0, os.path.join(os.path.dirname(_API_DIR), "scripts"))
    import build_stat_stability
    assert set(build_stat_stability.CATALOGUE) == set(STATS) - {"age"}
    assert all(s["year_to_year"] for s in d["stats"])
    # Rules of thumb: 3P% needs hundreds of attempts (published: 242 to 750),
    # free-throw % and rebound % settle fast.
    assert 242 <= by["fg3_pct"]["split_half"]["stable_n"] <= 750
    assert by["ft_pct"]["split_half"]["stable_n"] < 100
    assert by["reb_pct"]["split_half"]["stable_n"] < 150
    assert by["fg3_pct"]["year_to_year"]["r"] < by["reb_pct"]["year_to_year"]["r"]
    for s in d["stats"]:
        sh = s["split_half"]
        if sh:
            assert sh["ci"][0] <= sh["stable_n"] <= sh["ci"][1]
    assert by["bpm"]["split_half"] is None  # season totals only

    # Fed back into the Leaderboard: reliability = n / (n + M) per row.
    lb = client.get("/leaderboard/custom", params={"stat": "fg3_pct", "season_from": 2026}).json()
    m = lb["stability"]["stable_n"]
    for r in lb["results"]:
        n = r["sample"]["n"]
        assert abs(r["sample"]["reliability"] - n / (n + m)) < 0.002
        assert r["sample"]["noisy"] == (r["sample"]["reliability"] < 0.5)
    # ... and into the Breakout Detector (BPM has no split-half estimate).
    b = client.get("/explore/breakouts", params={"season": 2017, "top_n": 5}).json()
    stats = b["results"][0]["stats"]
    assert "reliability" in stats["ts_pct"] and "reliability" not in stats["bpm"]


def test_player_profile_known_players():
    from impact_api import app
    client = TestClient(app)
    jokic = client.get("/player-profile/203999").json()
    _assert_has_source(jokic)
    wins = {(a["season"], a["award"]) for a in jokic["awards"]["rows"] if a["winner"] and a["award"] == "MVP"}
    assert wins == {(2021, "MVP"), (2022, "MVP"), (2024, "MVP")}
    assert jokic["player"]["draft"]["pick"] == 41 and jokic["player"]["greats"]
    for block in ("defense", "gravity", "contracts"):
        assert jokic[block]["rows"], block
    assert jokic["clutch"]["clutch_plays"] > 0 and jokic["shots"]["zones"]["fga"] > 0
    # Profile breakout flags match the Breakout Detector's own list.
    flag = next(f for f in jokic["breakouts"]["flags"] if f["season"] == 2017)
    listed = client.get("/explore/breakouts", params={"season": 2017, "top_n": 25}).json()["results"]
    assert next(r["rank"] for r in listed if r["player_id"] == 203999) == flag["rank"]

    # Traded mid-season: games per team (Basketball-Reference), 2021-22 BKN 44 + PHI 21.
    harden = client.get("/player-profile/201935").json()
    row = next(s for s in harden["seasons"]["rows"] if s["season"] == 2022)
    assert [(t["team"], t["gp"]) for t in row["stints"]] == [("BKN", 44), ("PHI", 21)]

    # Pre-tracking legend: season rows and awards, no tracking blocks.
    jordan = client.get("/player-profile/893").json()
    assert sum(a["award"] == "MVP" and a["winner"] for a in jordan["awards"]["rows"]) == 5
    assert not jordan["defense"]["rows"] and not jordan["gravity"]["rows"] and jordan["clutch"] is None
    assert jordan["shots"]["seasons"][0]["season"] == 1997  # shot locations start in 1996-97

    zones = client.get("/player-profile/203999/shot-zones", params={"season": 2016}).json()
    assert sum(z["fga"] for z in zones["zones"]) == zones["fga"]
    assert client.get("/player-profile/203999/shot-zones", params={"season": 1990}).status_code == 404
    assert client.get("/player-profile/999999999").status_code == 404


def test_game_log_and_finder_match_known_games():
    """player_game_lines (ESPN play-by-play rebuild) via /games/finder and /games/player-log."""
    from impact_api import app
    client = TestClient(app)
    opts = client.get("/games/finder/options").json()
    _assert_has_source(opts)
    assert opts["seasons"]["from"] == 2021
    # The three NBA Cup finals are the only lines left out (not in the standings).
    assert [x["date"] for x in opts["left_out"]] == ["2023-12-09", "2024-12-17", "2025-12-16"]
    assert opts["accuracy"]["points_mean_abs_error"] < 0.01

    def only(f, **kw):
        d = client.get("/games/finder", params={"f": f, **kw}).json()
        assert d["total"] == 1, d["total"]
        return d["results"][0]

    # Known box scores: Doncic 73 (2024-01-26), Embiid 70 (2024-01-22), Mitchell 71 in OT (2023-01-02).
    g = only("pts:eq:73")
    assert (g["player_name"], g["date"], g["opponent"]) == ("Luka Dončić", "2024-01-26", "ATL")
    assert (g["fgm"], g["fga"], g["fg3m"], g["fg3a"], g["ftm"], g["fta"], g["reb"], g["ast"]) == (25, 33, 8, 13, 15, 16, 10, 7)
    g = only("pts:gte:70,reb:gte:18")
    assert (g["player_name"], g["opponent"], g["fgm"], g["fga"], g["ftm"], g["fta"]) == ("Joel Embiid", "SAS", 24, 41, 21, 23)
    g = only("pts:gte:71,ast:gte:11")
    assert (g["player_name"], g["fgm"], g["fga"], g["fg3m"], g["fg3a"], g["ftm"], g["fta"]) == \
        ("Donovan Mitchell", 22, 34, 7, 15, 20, 25)
    assert g["min"] > 48  # overtime game

    # Known streaks: Curry's 11 straight 30-point games (Mar 29 - Apr 19, 2021), Embiid's 22 in 2023-24.
    s = client.get("/games/finder", params={"f": "pts:gte:30", "mode": "streaks", "season_from": 2021,
                                            "season_to": 2021}).json()["results"][0]
    assert (s["player_name"], s["games"], s["start_date"], s["end_date"]) == ("Stephen Curry", 11, "2021-03-29", "2021-04-19")
    s = client.get("/games/finder", params={"f": "pts:gte:30", "mode": "streaks", "season_from": 2024,
                                            "season_to": 2024}).json()["results"][0]
    assert (s["player_name"], s["games"]) == ("Joel Embiid", 22)

    # Game log: every game NBA.com counts, in date order; the Cup final is left out.
    log = client.get("/games/player-log/203999", params={"season": 2024}).json()
    _assert_has_source(log)
    assert log["games"] == log["nba_gp"] == len(log["rows"]) == 79
    assert [r["date"] for r in log["rows"]] == sorted(r["date"] for r in log["rows"])
    lebron = client.get("/games/player-log/2544", params={"season": 2024}).json()
    assert lebron["cup_final_games"] == 1 and "2023-12-09" not in {r["date"] for r in lebron["rows"]}
    prof = client.get("/player-profile/203999").json()["game_log"]
    assert {r["season"] for r in prof["seasons"]} == {2021, 2022, 2023, 2024, 2025, 2026}
    assert client.get("/games/player-log/203999", params={"season": 2010}).status_code == 404

    # Whitelisted inputs only.
    for bad in ({"f": "pts;drop table x:gte:1"}, {"f": "pts:like:1"}, {"sort": "pts desc"}, {"team": "XXX"},
                {"mode": "streaks"}):
        assert client.get("/games/finder", params=bad).status_code in (400, 422), bad


def test_espn_ids_not_fuzzy_matched_to_another_player():
    """scripts/repair_espn_player_ids.py: ESPN names with no player_season_stats row that season were once
    fuzzy-matched to someone else (Keon Johnson -> Keldon Johnson, Jalen -> Jaden McDaniels)."""
    from impact_api import app
    client = TestClient(app)
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    # Nobody has lines for two teams on one date.
    cur.execute("""SELECT count(*) FROM (SELECT player_id, game_date FROM player_game_lines GROUP BY 1, 2
                   HAVING count(DISTINCT team_abbreviation) > 1) x""")
    assert cur.fetchone()[0] == 0
    # Keon Johnson's five Nets games in 2023-24 are his, shot for shot as on the NBA chart (8-21, 4-10 from three).
    cur.execute("""SELECT count(*), sum(fgm), sum(fga), sum(fg3m), sum(fg3a) FROM player_game_lines
                   WHERE player_id = 1630553 AND season = 2024""")
    assert cur.fetchone() == (5, 8, 21, 4, 10)
    cur.execute("SELECT count(*) FROM player_game_lines WHERE player_id IN (1629640, 1630183) AND team_abbreviation IN ('BKN', 'WAS')")
    assert cur.fetchone()[0] == 0
    conn.close()
    log = client.get("/games/player-log/1629640", params={"season": 2024}).json()
    assert log["games"] == log["nba_gp"] == 69  # Keldon Johnson, Spurs only


def test_hot_streak_checker_persistence_and_noise():
    """hot_streak_persistence (scripts/build_hot_streak_persistence.py) + /games/hot-streak(s)."""
    from impact_api import app
    from hot_streaks import STATS, WINDOWS
    client = TestClient(app)
    opts = client.get("/games/hot-streak/options").json()
    _assert_has_source(opts)
    per = {(r["stat"], r["window_games"]): r for r in opts["persistence"]}
    assert set(per) == {(s, w) for s in STATS for w in WINDOWS}  # script and API share one catalogue
    for r in per.values():
        # Held out 2024-25 and 2025-26: beats "stays at baseline" and "keeps his last-N rate".
        assert r["rmse_model"] < r["rmse_baseline"] and r["rmse_model"] < r["rmse_window"], r["stat"]
        assert r["slope_lo"] <= r["slope"] <= r["slope_hi"]
    for w in WINDOWS:
        # Shooting runs are mostly noise; minutes (role) carry on far more.
        assert per[("fg3_pct", w)]["slope"] < 0.35 and per[("ts_pct", w)]["slope"] < 0.35
        assert per[("min", w)]["slope"] > per[("fg3_pct", w)]["slope"] + 0.3
    assert per[("fg3_pct", 5)]["slope"] < 0.15

    # One player, mid-season, with his next games to compare.
    d = client.get("/games/hot-streak/201939", params={"season": 2025, "stat": "fg3_pct", "window": 10,
                                                       "as_of": "2025-01-20"}).json()
    _assert_has_source(d)
    assert d["qualified"] and d["window"]["games"] == 10 and d["what_happened_next"]["games"] == 10
    per_d = d["persistence"]
    assert abs(per_d["expected_next"] - (d["baseline"]["value"] + per_d["intercept"] + per_d["share"] * d["gap"])) < 1e-3
    assert 0 <= d["unusual"]["p"] <= 1 and d["verdict"]
    early = client.get("/games/hot-streak/201939", params={"season": 2025, "window": 10, "as_of": "2024-11-01"}).json()
    assert early["qualified"] is False and "baseline" in early["reason"]

    # League list: 10-game 3P% runs are about as often "significant" as chance alone predicts.
    lg = client.get("/games/hot-streaks", params={"season": 2025, "stat": "fg3_pct", "window": 10,
                                                  "as_of": "2025-01-15"}).json()
    s = lg["summary"]
    assert s["tested"] > 150 and s["significant"] <= 1.5 * s["expected_by_chance"]
    assert lg["next_games"] and lg["next_games"]["share_carried"] < 0.3
    zs = [r["unusual"]["z"] for r in lg["results"]]
    assert zs == sorted(zs, reverse=True) and all(r["gap"] > 0 for r in lg["results"])
    cold = client.get("/games/hot-streaks", params={"season": 2025, "stat": "pts", "direction": "cold"}).json()
    assert all(r["gap"] < 0 for r in cold["results"])
    assert client.get("/games/hot-streaks", params={"stat": "nope"}).status_code == 400
    assert client.get("/games/hot-streaks", params={"window": 7}).status_code == 400
    assert client.get("/games/hot-streaks", params={"season": 2010}).status_code == 404
    assert client.get("/player-profile/resolve", params={"name": "jokic"}).json()["player_id"] == 203999
    assert client.get("/player-profile/resolve", params={"name": "jokic"}).json()["player_id"] == 203999
    assert client.get("/player-profile/resolve", params={"name": "jokic"}).json()["player_id"] == 203999


def test_shot_zone_history_matches_box_scores():
    """Shot mix over a career: regular-season shots per season by zone, with
    the league's own mix. Stored shots should match box-score FGA closely."""
    from impact_api import app
    client = TestClient(app)
    data = client.get("/shots/player/Stephen Curry/zone-history").json()
    _assert_has_source(data)
    assert data["zones"] == ["Restricted Area", "In The Paint (Non-RA)", "Mid-Range", "Corner 3", "Above the Break 3"]
    seasons = {s["season"]: s for s in data["seasons"]}
    assert data["seasons_before_coverage"] == 0
    # 2015-16: 1,598 FGA (Basketball-Reference); more than half of them threes.
    s16 = seasons["2015-16"]
    assert abs(s16["fga"] - 1598) <= 5
    assert sum(z["fga"] for z in s16["zones"]) == s16["fga"]
    assert abs(sum(z["share"] for z in s16["zones"]) - 1) < 0.01
    threes = sum(z["share"] for z in s16["zones"] if z["zone"].endswith("3"))
    assert threes > 0.5
    # League 3PA share 2015-16 was 0.285 (Basketball-Reference 3PAr).
    lg3 = sum(z["league_share"] for z in s16["zones"] if z["zone"].endswith("3"))
    assert abs(lg3 - 0.285) < 0.01
    # 2019-20: five games, flagged as a small sample.
    assert seasons["2019-20"]["small_sample"] and not s16["small_sample"]

    hakeem = client.get("/shots/player/Hakeem Olajuwon/zone-history").json()
    assert hakeem["seasons_before_coverage"] == 12
    assert client.get("/shots/player/Magic Johnson/zone-history").status_code == 404


def test_pair_chemistry_grid_known_team():
    from impact_api import app
    client = TestClient(app)
    d = client.get("/lineups/pair-grid", params={"season": 2016, "team": "GSW"}).json()
    _assert_has_source(d)
    t = d["team_summary"]
    # 2015-16 Warriors: 73-9.
    assert (t["wins"], t["losses"]) == (73, 9)
    # Only the top 2,000 league lineups are stored, so coverage is partial.
    assert 0.3 < t["coverage"] < 1
    names = {p["player_id"]: p["player_name"] for p in d["players"]}
    assert {"Stephen Curry", "Draymond Green", "Klay Thompson"} <= set(names.values())
    for p in d["players"]:
        assert p["minutes"] <= p["season_minutes_all_teams"] + 1
    by_pair = {frozenset((names[c["a"]], names[c["b"]])): c for c in d["pairs"]}
    cg = by_pair[frozenset(("Stephen Curry", "Draymond Green"))]
    curry = next(p for p in d["players"] if p["player_name"] == "Stephen Curry")
    assert cg["qualified"] and 1500 < cg["minutes"] <= curry["minutes"]
    assert abs(cg["net_rating"] - (cg["off_rating"] - cg["def_rating"])) < 0.15
    assert all(c["qualified"] == (c["minutes"] >= d["min_minutes"]) for c in d["pairs"])

    # A traded player appears under each team with only that team's lineups.
    bkn = client.get("/lineups/pair-grid", params={"season": 2023, "team": "BKN", "max_players": 15}).json()
    phx = client.get("/lineups/pair-grid", params={"season": 2023, "team": "PHX", "max_players": 15}).json()
    mb = [next(p for p in g["players"] if p["player_name"] == "Mikal Bridges") for g in (bkn, phx)]
    # Since round 8 step 6a every one of his stints is tracked, so the sum reaches his season total; the grid adds
    # lineup minutes each rounded to 0.1 with ties up (about +0.008 a lineup, ~3 of his 2,963 minutes: R8-073).
    # Counting the other team's lineups would double it.
    assert sum(p["minutes"] for p in mb) <= mb[0]["season_minutes_all_teams"] * 1.002 + 1

    assert client.get("/lineups/pair-grid", params={"season": 1990}).status_code == 404
    assert client.get("/lineups/pair-grid", params={"team": "XXX"}).status_code == 404
    assert client.get("/lineups/pair-grid", params={"max_players": 7}).status_code == 400


def test_era_translator_known_seasons():
    from impact_api import app
    client = TestClient(app)
    hits = client.get("/era/players", params={"q": "wilt chamb"}).json()["results"]
    wilt = hits[0]["player_id"]
    d = client.get("/era/translate", params={"player_id": wilt, "season": 1962, "target": 2026}).json()
    _assert_has_source(d)
    rows = {r["key"]: r for r in d["rows"]}
    # 1961-62 pace 126.2 vs 2025-26 99.4 (Basketball-Reference): 50.4 points shrinks to about 40.
    assert d["environment"]["source"]["pace"] == 126.2 and d["environment"]["source"]["pace_source"] == "bref_estimate"
    pts = rows["pts"]
    assert pts["original"] == 50.4 and 38 < pts["pace_adjusted"] < 41
    assert abs(pts["pace_adjusted"] - 50.4 * d["environment"]["pace_factor"]) < 0.01
    # Same share of league scoring: league points a team game, 118.8 then.
    assert abs(pts["league_adjusted"] - 50.4 * pts["league_target"] / 118.8) < 0.01
    # He led the league in scoring; steals weren't recorded yet.
    assert pts["standing"]["rank"] == 1 and rows["stl"]["original"] is None
    # Walt Bellamy led 1961-62 in FG%, so Wilt is second.
    assert rows["fg_pct"]["standing"]["rank"] == 2
    # Percentages move by the change in league average; no three-point line in 1961-62.
    ts = rows["ts_pct"]
    assert abs(ts["league_adjusted"] - (0.536 - ts["league_source"] + ts["league_target"])) < 1e-6
    back = client.get("/era/translate", params={"player_id": 201566, "season": 2017, "target": 1962}).json()
    assert {r["key"]: r for r in back["rows"]}["fg3m"]["pace_adjusted"] is None
    # 1949-50 has no published pace: estimated here and said so.
    mikan = client.get("/era/translate", params={"player_id": 600012, "season": 1950}).json()
    assert mikan["environment"]["source"]["pace_source"] == "estimated_here"
    assert any("estimated by this app" in n for n in mikan["notes"])
    assert client.get("/era/translate", params={"player_id": wilt, "season": 1950}).status_code == 404
    assert client.get("/era/translate", params={"player_id": wilt, "season": 1962, "target": 1947}).status_code == 400


def test_role_finder_presets_land_known_players():
    """Role Player Finder: the presets' weights are a judgment call, so the
    check is that well-known role players land near the top of their role
    (2024-25, the last season with full data and salaries)."""
    from impact_api import app
    client = TestClient(app)
    opts = client.get("/roles/finder/options").json()
    _assert_has_source(opts)
    assert opts["seasons"]["from"] == 2018 and opts["seasons"]["to"] >= 2026
    assert {p["key"] for p in opts["presets"]} >= {"three_and_d", "rim_protector", "point_of_attack", "floor_spacer"}
    assert all(p["weights"] for p in opts["presets"])  # every preset shows its weights

    def top(preset, n=5, **params):
        d = client.get("/roles/finder", params={"preset": preset, "season": 2025, "top_n": n, **params}).json()
        return d, [r["player_name"] for r in d["results"]]

    d, names = top("rim_protector")
    _assert_has_source(d)
    assert {"Victor Wembanyama", "Walker Kessler"} <= set(names)
    row = d["results"][0]
    assert abs(row["score"] - sum(p["contribution"] for p in row["parts"].values())) < 0.01
    assert row["position"] in ("F", "F-C", "C-F", "C") and d["pool"] < d["pool_total"]
    assert all(r["salary"] is not None for r in d["results"]) and d["filters"]["salary_available"]

    assert {"Luguentz Dort", "Dorian Finney-Smith"} <= set(top("three_and_d")[1])
    assert {"Alex Caruso", "Dyson Daniels"} <= set(top("point_of_attack")[1])
    assert top("floor_spacer")[1][0] == "Malik Beasley"
    assert {"Walker Kessler", "Steven Adams"} <= set(top("glass_cleaner")[1])
    # Usage cap on the secondary-creator preset is on the percent scale (usage is stored as a fraction).
    d, _ = top("secondary_creator", n=50)
    assert d["filters"]["max_usg"] == 24 and all(r["context"]["usg_pct"] <= 24 for r in d["results"])
    # Stretch big compares within the chosen positions: Turner and Porzingis in the top 10.
    d, names = top("stretch_big", n=10)
    assert d["filters"]["relative"] == "positions" and {"Myles Turner", "Kristaps Porziņģis"} <= set(names)

    # Salary filter applies only where contract data exists.
    d, _ = top("three_and_d", n=10, max_salary=5_000_000)
    assert d["filters"]["max_salary"] == 5_000_000 and all(r["salary"] <= 5_000_000 for r in d["results"])
    d = client.get("/roles/finder", params={"preset": "three_and_d", "season": 2026, "max_salary": 5_000_000}).json()
    assert d["filters"]["max_salary"] is None and any("salary" in n for n in d["notes"])

    # Custom weights and guards.
    d = client.get("/roles/finder", params={"preset": "custom", "weights": "blk36:1,reb_pct:1", "positions": "C"}).json()
    assert d["results"] and all(r["position"] == "C" for r in d["results"])
    for params in ({"preset": "nope"}, {"preset": "custom"}, {"preset": "custom", "weights": "x:1"},
                   {"positions": "PG"}, {"relative": "team"}):
        assert client.get("/roles/finder", params=params).status_code == 400
    assert client.get("/roles/finder", params={"season": 2015}).status_code == 404


def test_trade_impact_combines_wins_spacing_and_payroll():
    """Trade Impact (api/routers/trade_impact.py): the Trade Analyzer's win
    model, the Spacing Lab's lineup spacing and Contract Value on one screen.
    Real 2024-25 case: Josh Hart (NYK) for Duncan Robinson (MIA)."""
    from impact_api import app
    client = TestClient(app)
    params = {"season": 2025, "team_a": "NYK", "player_a_id": 1628404, "team_b": "MIA", "player_b_id": 1629130}
    resp = client.get("/trade/impact", params=params)
    assert resp.status_code == 200
    data = resp.json()
    assert data["trade"]["team_a"]["sends"]["player_id"] == 1628404
    assert data["trade"]["team_b"]["receives"]["player_id"] == 1628404
    for side in ("team_a", "team_b"):
        w = data["wins"][side]
        assert w["available"] and 0 < w["before_pct"] < 1 and 0 < w["after_pct"] < 1
        assert abs(w["delta_wins"] - (w["after_pct"] - w["before_pct"]) * 82) < 1e-6
        s = data["spacing"][side]
        assert s["available"] and len(s["before"]["players"]) == 5 and len(s["after"]["players"]) == 5
        assert s["lineup"]["poss"] >= 100 and s["before"]["real_lineup"]["poss"] == s["lineup"]["poss"]
        for rep in (s["before"], s["after"]):
            assert abs(sum(p["gravity"] for p in rep["players"]) - rep["spacing"]) < 1e-6
        p = data["payroll"][side]
        assert p["available"] and p["before"]["n_priced"] == p["after"]["n_priced"]
        assert abs(p["delta_payroll"] - (p["incoming"]["salary"] - p["outgoing"]["salary"])) < 1e-6
    # Both were starters in their team's most-used five, so the incoming player takes that spot.
    a, b = data["spacing"]["team_a"], data["spacing"]["team_b"]
    assert a["rule"] == "outgoing_starter" and a["replaced_player_id"] == 1628404
    after_ids = {p["player_id"] for p in a["after"]["players"]}
    assert 1629130 in after_ids and 1628404 not in after_ids
    # A non-shooter out, one of the league's top shooters in: spacing rises for NYK, falls for MIA.
    assert a["delta_spacing"] > 0 and b["delta_spacing"] < 0
    # Money moves symmetrically: one team's payroll change is the other's negative.
    assert abs(data["payroll"]["team_a"]["delta_payroll"] + data["payroll"]["team_b"]["delta_payroll"]) < 1e-6
    # A season without reliable salary data says so instead of showing zeros.
    no_pay = client.get("/trade/impact", params={**params, "season": 2024}).json()
    assert no_pay["payroll"]["available"] is False and "2023-24" in no_pay["payroll"]["reason"]
    assert no_pay["spacing"]["available"] is True
    # Guards.
    assert client.get("/trade/impact", params={**params, "team_b": "NYK"}).status_code == 400
    assert client.get("/trade/impact", params={**params, "player_b_id": 1628404}).status_code == 400


# ─── Round 3, Phase 12: Data Coverage page ───────────────────────────────────

def test_meta_coverage():
    from impact_api import app
    from routers.meta import COVERAGE_MAP
    resp = TestClient(app).get("/meta/coverage")
    assert resp.status_code == 200
    data = resp.json()
    _assert_has_source(data)
    assert len(data["tables"]) == len(COVERAGE_MAP)
    assert set(data["groups"]) == {row["group"] for row in data["tables"]}
    for row in data["tables"]:
        # Every table in the hand-maintained map must actually exist in this
        # database and return a real row count, not a placeholder.
        assert row["exists"] is True, f"{row['table']} is in COVERAGE_MAP but missing from the database"
        assert row["n_rows"] > 0
        assert isinstance(row["used_by"], list)


def test_aging_curves_peaks_and_player_overlay():
    from impact_api import app
    client = TestClient(app)
    d = client.get("/aging/curves", params={"stat": "pts", "era": "all"}).json()
    _assert_has_source(d)
    s = d["summary"]
    # Scoring peaks in the mid-to-late twenties; athletic stats peak earlier.
    assert 25 <= s["peak_age"] <= 28 and s["pairs"] > 10000
    peaks = {x["key"]: x["peaks"] for x in d["stats"]}
    assert peaks["blk"]["all"]["age"] < peaks["pts"]["all"]["age"] < peaks["ast"]["all"]["age"]
    assert {e["era"] for e in d["eras"]} == {"all", "three_point", "modern"}
    pts = {p["age"]: p for p in d["points"]}
    # Anchored at 27, and declining well before 34; every age on the curve has 30+ pairs.
    assert abs(pts[27]["change_vs_ref"]) < 1e-9 and pts[34]["change_vs_ref"] < pts[30]["change_vs_ref"] < 0
    assert all(p["pairs"] is None or p["pairs"] >= 30 for p in d["points"])
    assert all(p["ci_lo"] <= p["change_vs_ref"] <= p["ci_hi"] for p in d["points"])
    # One age convention: LeBron (born 1984-12-30) was 19 on 1 Feb 2004 and 25 on 1 Feb 2010.
    lb = client.get("/aging/player", params={"player_id": 2544, "stat": "bpm"}).json()
    ages = {x["season"]: x["age"] for x in lb["seasons"]}
    assert ages[2004] == 19 and ages[2010] == 25 and lb["offset"] > 5 and lb["path"]
    # BPM starts in 1973-74: Wilt's seasons are listed but can't be placed.
    wilt = client.get("/aging/player", params={"player_id": 76375, "stat": "bpm"}).json()
    assert wilt["qualified_seasons"] == 0 and wilt["path"] is None
    assert client.get("/aging/curves", params={"stat": "nope"}).status_code == 400
    assert client.get("/aging/curves", params={"stat": "pts", "era": "x"}).status_code == 400


def test_on_off_known_cases():
    """On/off for every player (api/routers/on_off.py, scripts/build_player_on_off.py):
    every minute of every regular-season game 2020-21 on, from the play-by-play lines."""
    from impact_api import app
    client = TestClient(app)
    d = client.get("/lineups/on-off", params={"season": 2024, "team": "DEN"}).json()
    _assert_has_source(d)
    t = d["team_summary"]
    # 2023-24 Nuggets: 57-25; the rebuilt lineups track nearly every player-minute.
    assert (t["wins"], t["losses"]) == (57, 25) and abs(t["net_rating"] - (t["ortg"] - t["drtg"])) < 0.15
    assert 0.98 < t["tracked_share"] <= 1
    jokic = next(p for p in d["players"] if p["player_name"] == "Nikola Jokić")
    assert d["star"]["player_id"] == jokic["player_id"]
    # A famously large on/off: the Nuggets were a net negative without him.
    assert jokic["qualified"] and jokic["net_off"] < 0 and jokic["on_off_net"] > 15 and jokic["on_off_ci_low"] > 5
    assert abs(jokic["on_off_net"] - (jokic["net_on"] - jokic["net_off"])) < 0.05
    for p in d["players"]:
        assert p["qualified"] == (p["minutes_on"] >= d["min_minutes"])
        if p["on_off_ci_low"] is not None:
            assert p["on_off_ci_low"] <= p["on_off_net"] <= p["on_off_ci_high"]
    # League view: only rows over the floor, best on-off first; the league's
    # possession-weighted on-court net is about zero (each side's five players share every possession).
    lg = client.get("/lineups/on-off", params={"season": 2024, "min_minutes": 1000}).json()
    assert lg["players"] and all(p["minutes_on"] >= 1000 and p["qualified"] for p in lg["players"])
    assert lg["players"][0]["player_name"] == "Nikola Jokić"
    assert abs(lg["season_summary"]["league_net_on_weighted"]) < 0.1
    assert lg["noise"]["ci_excludes_zero"] > lg["noise"]["expected_by_chance"]
    # Stars: at most one top-usage player per team, every team accounted for.
    st = client.get("/lineups/on-off/stars", params={"season": 2024}).json()
    _assert_has_source(st)
    assert len(st["stars"]) + len(st["teams_without_star"]) == 30
    assert len({s["team_abbreviation"] for s in st["stars"]}) == len(st["stars"])
    den = next(s for s in st["stars"] if s["team_abbreviation"] == "DEN")
    assert den["player_name"] == "Nikola Jokić" and den["team"]["wins"] == 57
    # Profile block, and guards.
    prof = client.get("/player-profile/203999").json()["on_off"]
    assert {r["season"] for r in prof["rows"]} >= {2021, 2022, 2023, 2024, 2025, 2026}
    assert client.get("/lineups/on-off", params={"season": 2010}).status_code == 404
    assert client.get("/lineups/on-off", params={"team": "XXX"}).status_code == 404


# ─── Round 3, Phase 8: Expected FG% / shot-making ────────────────────────────

def test_shot_making_known_shooters_and_model_check():
    """Expected FG% per shot (scripts/build_shot_making.py) and what it says
    about famous seasons; the model must beat its baselines out of sample."""
    from impact_api import app
    client = TestClient(app)
    lb = client.get("/shots/shot-making/leaderboard", params={"season": 2016}).json()
    _assert_has_source(lb)
    assert lb["min_fga"] == 200 and lb["rows"][0]["player_name"] == "Stephen Curry"
    curry = lb["rows"][0]
    assert curry["fga"] == 1598 and curry["rank"] == 1 and curry["shot_making"] > 0.10
    assert abs(curry["efg_pct"] - 0.630) < 0.003          # Basketball-Reference: .630
    assert all(r["qualified"] and r["fga"] >= 200 for r in lb["rows"])
    assert abs(lb["league"]["efg_pct"] - lb["league"]["x_efg_pct"]) < 0.005   # calibrated within the season
    quality = client.get("/shots/shot-making/leaderboard", params={"season": 2025, "sort": "quality"}).json()
    gobert = quality["rows"][0]
    assert gobert["player_name"] == "Rudy Gobert" and gobert["quality_rank"] == 1
    assert abs(gobert["shot_making"]) < 2 * gobert["se"]   # rim-only big: best diet, no shot-making
    worst = client.get("/shots/shot-making/leaderboard", params={"season": 2023, "order": "asc", "limit": 30}).json()
    assert "Russell Westbrook" in [r["player_name"] for r in worst["rows"]]
    assert client.get("/shots/shot-making/leaderboard", params={"season": 1990}).status_code == 404

    p = client.get("/shots/player/Kyle Korver/shot-making").json()
    _assert_has_source(p)
    s15 = next(r for r in p["rows"] if r["season"] == 2015)
    assert s15["rank"] == 1 and s15["fg3_pct"] > 0.48 and s15["margin95"] == round(1.96 * s15["se"], 4)
    assert client.get("/shots/player/Magic Johnson/shot-making").status_code == 404

    m = client.get("/shots/shot-making/model").json()
    by = {r["model_type"]: r for r in m["holdout"]}
    assert by["hgb"]["deployed"] and by["hgb"]["log_loss"] < by["logreg"]["log_loss"] < by["constant"]["log_loss"]
    assert by["hgb"]["log_loss"] < by["zone_baseline"]["log_loss"]
    assert len(by["hgb"]["reliability_bins"]) >= 5
    assert all(abs(b["predicted_mean"] - b["observed_rate"]) < 0.05
               for b in by["hgb"]["reliability_bins"] if b["n"] >= 1000)
    y2y = m["crossfit"]["notes"]["year_to_year"]["200"]
    assert y2y["quality"] > 0.8 > y2y["shot_making"] > 0.4

    prof = client.get("/player-profile/201939").json()   # Curry
    assert prof["shot_making"]["min_fga"] == 200
    assert any(r["season"] == 2016 and r["rank"] == 1 for r in prof["shot_making"]["rows"])


def test_projections_baseline_beats_last_season_and_covers():
    """Next-season projections (api/routers/projections.py, scripts/build_projections.py):
    the backtest must beat 'same as last season' on the bread-and-butter stats,
    its 80% range must hold about 80% of actuals, and a current star must get a
    projection with a sensible range; a retired player gets a reason instead."""
    from impact_api import app
    client = TestClient(app)
    d = client.get("/projections", params={"stat": "pts"}).json()
    _assert_has_source(d)
    assert d["season"] == d["latest_season"] + 1 and d["players"] > 400
    bt = d["backtest"]["all"]
    assert bt["mae"] < bt["mae_last"] < bt["mae_league"]
    assert 0.76 <= bt["coverage80"] <= 0.84 and 0.85 <= bt["slope"] <= 1.15
    assert len(d["backtest"]["by_season"]) >= 25
    # Every row has a range around its projection; ranges never collapse to a point.
    for r in d["rows"]:
        assert r["lo"] < r["projection"] < r["hi"]
    keys = {s["key"] for s in d["catalogue"]}
    assert {"min", "pts", "pts36", "fg3_pct", "usg_pct", "bpm"} <= keys
    # The whole catalogue beats the league average; per-36 and shooting stats beat last season too.
    for s in d["catalogue"]:
        b = s["backtest"]
        assert b["mae"] < b["mae_league"], s["key"]
        if s["key"] in ("pts36", "reb36", "fg_pct", "ft_pct", "ts_pct", "bpm", "min"):
            assert b["mae"] < b["mae_last"], s["key"]
    # Jokić: three seasons used, a projection near his level, age from his birth date (1995-02-19).
    j = client.get("/projections/player/203999").json()
    rows = {r["stat"]: r for r in j["rows"]}
    assert j["player"]["seasons_used"] == 3 and j["player"]["age_next"] == d["season"] - 1996
    assert 18 < rows["pts"]["projection"] < 30 and rows["pts"]["lo"] < rows["pts"]["projection"] < rows["pts"]["hi"]
    # 3P% is the noisier stat, so his own numbers get less of the weight than for points.
    assert rows["pts"]["own_weight"] > 0.9 and rows["fg3_pct"]["own_weight"] < rows["pts"]["own_weight"]
    # Wilt: no projection, with the reason.
    w = client.get("/projections/player/76375").json()
    assert w["rows"] == [] and "1972-73" in w["reason"]
    assert client.get("/projections", params={"stat": "nope"}).status_code == 400
    # The profile carries the same block.
    p = client.get("/player-profile/203999").json()
    assert p["projections"]["season"] == d["season"]
    assert {r["stat"] for r in p["projections"]["rows"]} >= {"min", "pts", "bpm"}

def test_situational_splits_league_effects_and_noise():
    """player_situational_splits + situational_split_league (scripts/build_situational_splits.py)."""
    from impact_api import app
    from situational_splits import SPLITS, STATS
    client = TestClient(app)
    opts = client.get("/splits/situational/options").json()
    _assert_has_source(opts)
    pooled = {(r["split"], r["stat"]): r for r in opts["pooled"]}
    assert set(pooled) == {(sp, st) for sp in SPLITS for st in STATS}  # script and API share one catalogue
    # Home court: small but positive; strong opponents cost points and efficiency.
    home = pooled[("home", "pts")]
    assert 0 < home["league_ci_low"] and home["league_diff"] < 1.0
    assert pooled[("home", "ts_pct")]["league_ci_low"] > 0
    assert pooled[("opp", "pts")]["league_ci_high"] < 0 and pooled[("opp", "ts_pct")]["league_ci_high"] < 0
    # 2020-21 was played mostly without fans: its home effect is below the pooled one.
    lb21 = client.get("/splits/situational/leaderboard", params={"season": 2021, "split": "home", "stat": "pts"}).json()
    assert lb21["league"]["league_diff"] < home["league_diff"]
    # Travel is confounded with home/away (long trips end at home less often); the controlled fit is reported.
    assert pooled[("travel", "pts")]["home_share_a"] < pooled[("travel", "pts")]["home_share_b"]
    assert pooled[("travel", "pts")]["venue_adj_diff"] is not None and pooled[("home", "pts")]["venue_adj_diff"] is None
    for r in pooled.values():
        assert r["chance_outside_95"] > 0 and r["players"] > 300
        assert r["league_ci_low"] <= r["league_diff"] <= r["league_ci_high"]
    # Shooting splits are noise: no more standouts than the shuffle, and nothing carries into next season.
    for sp in SPLITS:
        assert pooled[(sp, "ts_pct")]["yoy_r"] < 0.15, sp
    assert pooled[("rest", "ts_pct")]["outside_95"] <= 1.25 * pooled[("rest", "ts_pct")]["chance_outside_95"]

    # Leaderboard: qualified rows only, sorted, bad inputs rejected.
    lb = client.get("/splits/situational/leaderboard",
                    params={"season": 2025, "split": "rest", "stat": "pts", "sort": "z", "limit": 30}).json()
    _assert_has_source(lb)
    zs = [r["z"] for r in lb["results"]]
    assert zs == sorted(zs, reverse=True) and all(r["qualified"] for r in lb["results"])
    assert all(r["games_a"] >= 10 and r["games_b"] >= 10 for r in lb["results"])
    assert lb["qualified"] == lb["total"] <= lb["stored"]
    assert client.get("/splits/situational/leaderboard", params={"split": "nope"}).status_code == 400
    assert client.get("/splits/situational/leaderboard", params={"season": 2010}).status_code == 404

    # One player: the two home/away sides add back to his game log (Jokić, 2024-25).
    p = client.get("/splits/situational/player/203999", params={"season": 2025}).json()
    _assert_has_source(p)
    log = client.get("/games/player-log/203999", params={"season": 2025}).json()
    home_pts = next(s for s in next(x for x in p["splits"] if x["split"] == "home")["stats"] if s["stat"] == "pts")["row"]
    assert home_pts["games_a"] + home_pts["games_b"] == log["games"]
    pts = (home_pts["value_a"] * home_pts["minutes_a"] + home_pts["value_b"] * home_pts["minutes_b"]) / 36
    assert abs(pts - log["averages"]["pts"] * log["games"]) < 1
    assert client.get("/splits/situational/player/977").status_code == 404  # Kobe: before the play-by-play lines
    assert client.get("/player-profile/203999").json()["situational_splits"]["seasons"]
    assert client.get("/player-profile/977").json()["situational_splits"]["seasons"] == []


def test_luck_schedule_srs_and_carryover():
    """Luck & schedule (api/routers/luck_schedule.py, scripts/build_luck_schedule.py) from the
    real final scores in game_scores (scripts/fetch_game_scores.py)."""
    from impact_api import app
    client = TestClient(app)
    d = client.get("/teams/luck-schedule", params={"season": 2016}).json()
    _assert_has_source(d)
    assert len(d["teams"]) == 30 and d["season_info"]["games"] == 1230 and d["season_info"]["complete"]
    by = {t["team_abbreviation"]: t for t in d["teams"]}
    # 2015-16: Warriors 73-9 on a +10.8 margin (lucky), SRS +10.38 like Basketball-Reference's, Spurs right behind.
    gsw, sas = by["GSW"], by["SAS"]
    assert (gsw["wins"], gsw["losses"]) == (73, 9) and gsw["srs_rank"] == 1 and abs(gsw["srs"] - 10.38) < 0.02
    assert sas["srs_rank"] == 2 and gsw["luck"] > 5 and abs(sas["luck"]) < 1
    for t in d["teams"]:
        assert t["wins"] + t["losses"] == t["games"] == 82
        assert abs(t["luck"] - (t["wins"] - t["exp_wins"])) < 0.01
        assert t["close3_w"] + t["close3_l"] <= t["close5_w"] + t["close5_l"] <= t["games"]
    # Ratings sum to zero; league-wide luck is about zero.
    assert abs(sum(t["srs"] for t in d["teams"])) < 0.05
    assert abs(sum(t["luck"] for t in d["teams"])) < 5
    # 2020-21 had mostly empty arenas: the smallest home-court edge on file.
    m = client.get("/teams/luck-schedule/model").json()
    hca = {s["season"]: s["hca"] for s in m["seasons"]}
    assert min(hca, key=hca.get) == 2021 and hca[2021] < 1.5
    assert len(m["points"]) == 510 and sum(f["chosen"] for f in m["fits"]) == 1
    c = m["checks"]
    assert c["bref_wins_mismatch"]["value"] == 0 and c["bref_srs_r"]["value"] > 0.999
    # Luck barely carries over; margin does. Ratings + schedule beat the record at midseason.
    assert c["luck_next_luck_r"]["value"] < 0.3 < c["mov_next_mov_r"]["value"]
    assert c["midseason_rmse_srs_schedule"]["value"] < c["midseason_rmse_record"]["value"]
    # As-of: standings before the date plus the schedule left add up to the full season.
    a = client.get("/teams/luck-schedule", params={"season": 2016, "as_of": "2016-01-18"}).json()
    assert a["as_of_info"]["played_games"] + a["as_of_info"]["left_games"] == 1230
    for t in a["teams"]:
        assert t["games"] + t["rem_games"] == 82 and t["rem_home"] + t["rem_away"] == t["rem_games"]
        assert t["wins"] + t["rem_actual_wins"] == t["final_wins"]
        assert t["wins"] <= t["proj_wins"] <= t["wins"] + t["rem_games"]
    # One franchise across abbreviations; guards.
    h = client.get("/teams/luck-schedule/team/NOH").json()
    assert h["franchise"] == "NOP" and set(h["abbreviations"]) == {"NOH", "NOP"} and len(h["seasons"]) == 17
    assert client.get("/teams/luck-schedule", params={"season": 1990}).status_code == 404
    assert client.get("/teams/luck-schedule", params={"season": 2016, "as_of": "2015-10-01"}).status_code == 400
    assert client.get("/teams/luck-schedule/team/XXX").status_code == 404


def test_team_profile_blocks_and_coverage():
    """Team page (api/routers/team_profile.py): team_seasons (scripts/build_team_seasons.py) and
    team_zone_mix (scripts/build_team_zone_mix.py) plus the stored tables it aggregates."""
    from impact_api import app
    client = TestClient(app)
    d = client.get("/team-profile/BOS", params={"season": 2024}).json()
    _assert_has_source(d)
    t = d["summary"]["team"]
    # 2023-24 Celtics: 64-18, first in SRS, and the same record in every block that has one.
    assert (t["w"], t["l"]) == (64, 18) and d["summary"]["ranks"]["srs"] == 1 and d["summary"]["n_teams"] == 30
    assert (d["luck"]["wins"], d["luck"]["losses"]) == (64, 18) and d["luck"]["srs_rank"] == 1
    games = d["games"]["games"]
    assert len(games) == 82 and sum(g["pts_for"] > g["pts_against"] for g in games) == 64
    home = next(s for s in d["games"]["splits"] if s["key"] == "home")
    away = next(s for s in d["games"]["splits"] if s["key"] == "away")
    assert home["n"] + away["n"] == 82
    names = [p["player_name"] for p in d["roster"]["players"]]
    assert names[0] == "Jayson Tatum" and "Jrue Holiday" in names
    assert d["lineups"]["available"] and d["on_off"]["available"] and d["pairs"]["available"]
    assert not d["payroll"]["available"] and "2023-24" in d["payroll"]["reason"]
    mix = d["shot_mix"]
    assert abs(sum(z["share"] for z in mix["zones"]) - 1) < 0.01 and mix["fga"] > 6500
    # Franchise history: every season under its own name, joined into one franchise.
    fr = {r["season"]: r["abbreviation"] for r in client.get("/team-profile/OKC", params={"season": 2005}).json()["franchise_history"]}
    assert fr[2005] == "SEA" and fr[2009] == "OKC"
    # Asking for the franchise in a season it played under another name opens that team, with a note.
    sea = client.get("/team-profile/OKC", params={"season": 2005}).json()
    assert sea["abbreviation"] == "SEA" and sea["note"] and sea["summary"]["team"]["w"] == 52
    assert not sea["games"]["available"] and sea["games"]["reason"]
    assert sea["shot_mix"]["available"] and sea["roster"]["players"][0]["player_name"] == "Ray Allen"
    # Either code for the Suns works; 2015-16 Warriors 73-9 from Basketball-Reference.
    assert client.get("/team-profile/PHO", params={"season": 2024}).json()["abbreviation"] == "PHX"
    assert client.get("/team-profile/PHX", params={"season": 2005}).json()["abbreviation"] == "PHO"
    gsw = client.get("/team-profile/GSW", params={"season": 2016}).json()["summary"]["team"]
    assert (gsw["w"], gsw["l"]) == (73, 9)
    # A player the season rows list under a later team isn't on this roster (play-by-play decides).
    mem = client.get("/team-profile/MEM", params={"season": 2025}).json()
    assert "Cole Anthony" not in [p["player_name"] for p in mem["roster"]["players"]]
    assert any(x["player_name"] == "Cole Anthony" for x in mem["roster"]["left_out"])
    assert mem["payroll"]["available"] and mem["payroll"]["n_teams"] == 30
    # Every block that's missing says why; guards.
    old = client.get("/team-profile/CHI", params={"season": 1996}).json()
    for k in ("games", "luck", "payroll", "lineups", "on_off", "shot_mix"):
        assert old[k]["available"] or old[k]["reason"]
    assert old["summary"]["team"]["w"] == 72
    assert client.get("/team-profile/XYZ").status_code == 404
    assert client.get("/team-profile/OKC", params={"season": 1960}).status_code == 404


def test_margins_come_from_final_scores():
    """Game Log, Game Finder, Schedule Fatigue, Team Comparison and Situational Splits read margins from
    game_scores (real final scores), not team_game_fatigue.plus_minus (summed player +/- / 5, wrong in 160 games)."""
    from impact_api import app
    client = TestClient(app)
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        # Game Log: SAS lost 101-126 at DEN on 2022-11-05 (plus_minus says -20).
        log = client.get("/games/player-log/1629640", params={"season": 2023}).json()
        g = next(r for r in log["rows"] if r["date"] == "2022-11-05")
        assert (g["opponent"], g["win"], g["margin"]) == ("DEN", False, -25)
        assert all((r["margin"] > 0) == r["win"] for r in log["rows"])
        # Game Finder's margin sort uses the same real margin.
        d = client.get("/games/finder", params={"player_id": 1629640, "season_from": 2023, "season_to": 2023,
                                                "sort": "margin", "order": "asc", "one_per_player": False,
                                                "limit": 200}).json()
        margins = [r["margin"] for r in d["results"]]
        assert margins == sorted(margins) and -25 in margins

        # Team Comparison: LAL beat CHI by 11 on 2026-01-26 (plus_minus says 10.6).
        cmp_ = client.get("/teams/compare/LAL/CHI").json()
        m = next(x for x in cmp_["head_to_head"]["recent_meetings"] if x["date"] == "2026-01-26")
        assert (m["team_a_won"], m["team_a_point_diff"]) == (True, 11)
        for x in cmp_["team_a"]["recent_form"]["results"]:
            assert (x["point_diff"] > 0) == x["win"]

        # Schedule Fatigue: back-to-back average margin equals the real scores'.
        cur.execute("""SELECT AVG(g.pts_for - g.pts_against) FROM team_game_fatigue f
                       JOIN game_scores g USING (game_id, team_abbreviation)
                       WHERE f.rest_days = 0 AND f.season = 2018""")
        real = float(cur.fetchone()[0])
        b2b = next(b for b in client.get("/schedule/rest-study", params={"season": 2018}).json()["buckets"]
                   if b["rest_days"] == 0)
        assert abs(b2b["avg_point_diff"] - real) < 0.001

        # Situational Splits: 2020-21's top 10 by real margin is exactly ten teams (plus_minus tied NYK and DAL
        # for 10th), and the stored rows were built from it: Jokic's games vs. those teams.
        cur.execute("""WITH r AS (SELECT team_abbreviation,
                                         RANK() OVER (ORDER BY AVG(pts_for - pts_against) DESC) AS rk
                                  FROM game_scores WHERE season = 2021 GROUP BY 1)
                       SELECT array_agg(team_abbreviation) FROM r WHERE rk <= 10""")
        top10 = cur.fetchone()[0]
        assert len(top10) == 10 and "NYK" in top10 and "DAL" not in top10
        cur.execute("""SELECT count(*) FROM player_game_lines l
                       JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
                       WHERE l.player_id = 203999 AND l.season = 2021 AND l.seconds > 0 AND f.opponent = ANY(%s)""",
                    (top10,))
        jokic_top10 = cur.fetchone()[0]
        cur.execute("""SELECT games_a FROM player_situational_splits
                       WHERE player_id = 203999 AND season = 2021 AND split = 'opp' AND stat = 'pts'""")
        assert cur.fetchone()[0] == jokic_top10
    finally:
        conn.close()


def test_lineup_stints_reconcile_and_feed_lineup_tools():
    """Five-man stints from play-by-play (scripts/build_lineup_stints.py, shared parser scripts/pbp_lineups.py,
    api/lineups_lib.py): every stint of every regular-season game 2020-21 on, reconciled per game; Pair Chemistry,
    Lineup Chemistry and the team page read them from 2020-21 and lineup_stats before."""
    from impact_api import app
    client = TestClient(app)
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute("SELECT season, games, games_ok, tracked_minutes_share FROM lineup_stint_seasons ORDER BY season")
        rows = cur.fetchall()
        assert [r[0] for r in rows] == [2021, 2022, 2023, 2024, 2025, 2026]
        # 7,220 of 7,232 games reconcile on points, seconds and possessions; 99.6-99.98% of minutes are tracked (93.7-99.8%
        # before round 8 step 6a matched ESPN's no-id players through player_bio).
        assert sum(r[2] for r in rows) / sum(r[1] for r in rows) > 0.995
        assert all(r[3] > 0.995 for r in rows)
        # Stint points add up to the real final score, and stint seconds to the game length, in every reconciled game.
        cur.execute("""SELECT COUNT(*) FROM (SELECT game_id, SUM(home_pts) hp, SUM(away_pts) ap, SUM(seconds) secs
                                             FROM lineup_stints GROUP BY 1) x
                       JOIN lineup_stint_games g USING (game_id)
                       WHERE g.game_ok AND (x.hp <> g.final_home OR x.ap <> g.final_away OR ABS(x.secs - g.game_length) > 0.5)""")
        assert cur.fetchone()[0] == 0
        # Possession components equal team_game_totals (same credit rules) in every reconciled game.
        cur.execute("""SELECT COUNT(*) FROM (
                           SELECT game_id, home_team AS team, SUM(home_fga) fga, SUM(home_fta) fta, SUM(home_oreb) oreb, SUM(home_tov) tov
                           FROM lineup_stints GROUP BY 1, 2
                           UNION ALL
                           SELECT game_id, away_team, SUM(away_fga), SUM(away_fta), SUM(away_oreb), SUM(away_tov)
                           FROM lineup_stints GROUP BY 1, 2) s
                       JOIN lineup_stint_games g USING (game_id)
                       JOIN team_game_totals t ON t.game_id = s.game_id AND t.team_abbreviation = s.team
                       WHERE g.game_ok AND (s.fga, s.fta, s.oreb, s.tov) <> (t.fga, t.fta, t.oreb, t.tov)""")
        assert cur.fetchone()[0] == 0
        # Every player's stint seconds equal his player_game_lines seconds (one parser; until round 8 step 6a the
        # lines counted 9 player-games twice after a substitution ESPN tagged to no team).
        cur.execute("""WITH s AS (SELECT game_id, pid, SUM(seconds) secs FROM (
                                    SELECT game_id, unnest(home_ids) pid, seconds FROM lineup_stints
                                    UNION ALL SELECT game_id, unnest(away_ids), seconds FROM lineup_stints) x GROUP BY 1, 2)
                       SELECT COUNT(*) FILTER (WHERE ABS(s.secs - l.seconds) > 0.2), COUNT(*)
                       FROM s JOIN player_game_lines l ON l.game_id = s.game_id AND l.player_id = s.pid""")
        off, n = cur.fetchone()
        assert n > 150000 and off == 0
        # Tracked stints have five a side; the 2025-26 Thunder's most-used lineup agrees with lineup_stats within 15 minutes.
        cur.execute("SELECT COUNT(*) FROM lineup_stints WHERE tracked_ok AND (n_home <> 5 OR n_away <> 5)")
        assert cur.fetchone()[0] == 0
        cur.execute("""SELECT l.minutes, t.minutes FROM lineup_stats t
                       JOIN lineup_seasons l ON l.season = t.season AND l.team_abbreviation = t.team_abbreviation
                        AND l.player_ids = (SELECT array_agg(x ORDER BY x) FROM unnest(t.player_ids) x)::integer[]
                       WHERE t.season = 2026 AND t.team_abbreviation = 'OKC' ORDER BY t.minutes DESC LIMIT 1""")
        ours, theirs = cur.fetchone()
        assert theirs > 150 and abs(ours - theirs) < 15
    finally:
        conn.close()

    # Pair Chemistry from stints: 2023-24 Nuggets 57-25, Jokić + Murray the biggest-minute star pair and clearly positive.
    d = client.get("/lineups/pair-grid", params={"season": 2024, "team": "DEN"}).json()
    _assert_has_source(d)
    assert d["source"] == "stints" and "lineup_seasons" in d["_source"]["tables"]
    t = d["team_summary"]
    assert (t["wins"], t["losses"]) == (57, 25) and 0.9 < t["coverage"] <= 1
    assert d["coverage"]["games"] == 82 and d["coverage"]["excluded"] == []
    names = {p["player_id"]: p["player_name"] for p in d["players"]}
    assert names[d["players"][0]["player_id"]] == "Nikola Jokić"
    jm = next(c for c in d["pairs"] if {names[c["a"]], names[c["b"]]} == {"Nikola Jokić", "Jamal Murray"})
    assert jm["qualified"] and 1300 < jm["minutes"] < 1500 and jm["net_rating"] > 10
    assert all(abs(c["net_rating"] - (c["off_rating"] - c["def_rating"])) < 0.15 for c in d["pairs"])
    assert d["sources"]["2020"] == "lineup_stats" and d["sources"]["2021"] == "stints"
    # Before 2020-21 the stored top-2,000 list is still the source (2015-16 Warriors 73-9, partial coverage).
    g = client.get("/lineups/pair-grid", params={"season": 2016, "team": "GSW"}).json()
    assert g["source"] == "lineup_stats" and (g["team_summary"]["wins"], g["team_summary"]["losses"]) == (73, 9)
    assert 0.3 < g["team_summary"]["coverage"] < 1 and g["coverage"] is None

    # Lineup Chemistry: stored both ways, names the source, respects the floor and the order.
    for season, source in ((2026, "stints"), (2019, "lineup_stats")):
        l = client.get("/lineups/chemistry", params={"season": season, "min_minutes": 100, "top_n": 5}).json()
        _assert_has_source(l)
        assert l["source"] == source and l["lineups_qualified"] <= l["lineups_total"] and len(l["results"]) == 5
        assert all(r["min"] >= 100 and len(r["players"]) == 5 for r in l["results"])
        nets = [r["net_rating"] for r in l["results"]]
        assert nets == sorted(nets, reverse=True)
        w = client.get("/lineups/chemistry", params={"season": season, "min_minutes": 100, "top_n": 5, "order": "worst"}).json()
        assert w["results"][0]["net_rating"] <= nets[-1]
    assert client.get("/lineups/chemistry", params={"season": 1990}).status_code == 404

    # Team page: the lineups block says which source it used.
    bos = client.get("/team-profile/BOS", params={"season": 2024}).json()
    assert bos["lineups"]["source"] == "stints" and bos["pairs"]["source"] == "stints" and bos["lineups"]["coverage_share"] > 0.9
    assert bos["lineups"]["stint_coverage"]["games"] == 82
    gsw = client.get("/team-profile/GSW", params={"season": 2016}).json()
    assert gsw["lineups"]["source"] == "lineup_stats" and gsw["pairs"]["available"]


# ─── Round 6, Step 3: possessions ─────────────────────────────────────────────

def test_possessions_reconcile_and_clock():
    """Possessions from play-by-play (scripts/build_possessions.py, rules in scripts/pbp_possessions.py on the
    shared parser): every game 2020-21 on, reconciled per game; times on the rebuilt clock, checked against
    NBA.com's own play-by-play of the twin games."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute("""SELECT season, COUNT(*), SUM(game_ok::int), AVG((home_poss + away_poss) / 2.0),
                              AVG((home_poss + away_poss - home_est - away_est + home_team_oreb + away_team_oreb) / 2.0)
                       FROM possession_games GROUP BY 1 ORDER BY 1""")
        rows = cur.fetchall()
        assert [r[0] for r in rows] == [2021, 2022, 2023, 2024, 2025, 2026]
        # 7,220 of 7,232 games add up (the stints' own set); about 99-101 possessions a team-game; counted possessions sit within ~1.5 of the
        # box-score estimate once team offensive rebounds (which continue a possession) are taken out of it.
        assert sum(r[2] for r in rows) / sum(r[1] for r in rows) > 0.995
        assert all(98 < float(r[3]) < 102 and 0.5 < float(r[4]) < 2.5 for r in rows)
        # In every reconciled game: points (with technicals) equal the final score, and FGA, FTA, OREB, TOV equal
        # team_game_totals (same credit rules as the stints).
        cur.execute("""WITH s AS (
                           SELECT game_id, offense AS team, SUM(pts + off_tech_pts) AS pts, SUM(fga) fga,
                                  SUM(fta) fta, SUM(oreb) oreb, SUM(tov) tov FROM possessions GROUP BY 1, 2),
                       d AS (SELECT game_id, defense AS team, SUM(def_tech_pts) AS pts FROM possessions GROUP BY 1, 2)
                       SELECT COUNT(*) FILTER (WHERE s.pts + d.pts <> CASE WHEN s.team = g.home_team THEN g.final_home ELSE g.final_away END),
                              COUNT(*) FILTER (WHERE (s.fga, s.oreb, s.tov) <> (t.fga, t.oreb, t.tov) OR s.fta > t.fta),
                              COUNT(*)
                       FROM s JOIN d USING (game_id, team) JOIN possession_games g USING (game_id)
                       JOIN team_game_totals t ON t.game_id = s.game_id AND t.team_abbreviation = s.team
                       WHERE g.game_ok""")
        bad_pts, bad_comp, n = cur.fetchone()
        assert n > 14000 and bad_pts == 0 and bad_comp == 0
        # Possessions alternate: the same side twice in a row within a period is rare (a gap in ESPN's log).
        cur.execute("""SELECT COUNT(*) FILTER (WHERE offense = prev), COUNT(*) FROM (
                           SELECT offense, LAG(offense) OVER (PARTITION BY game_id, period ORDER BY poss_no) prev
                           FROM possessions) x""")
        same, total = cur.fetchone()
        assert total > 1_400_000 and same / total < 0.002
        # Every possession points at a real stint of its game; tracked possessions at a tracked stint.
        cur.execute("""SELECT COUNT(*) FILTER (WHERE s.stint_no IS NULL), COUNT(*) FILTER (WHERE p.tracked_ok AND NOT s.tracked_ok)
                       FROM possessions p LEFT JOIN lineup_stints s ON s.game_id = p.game_id AND s.stint_no = p.stint_no
                       WHERE p.season = 2025""")
        assert cur.fetchone() == (0, 0)
        # The rebuilt clock is within 2 s of NBA.com's for >90% of events (ESPN's own: about a third).
        cur.execute("SELECT value FROM possession_meta WHERE key = 'clock_check'")
        chk = cur.fetchone()[0]
        assert chk["twin_games"] >= 400 and chk["all"]["corrected_within_2s"] > 0.9 > 0.5 > chk["all"]["espn_within_2s"]
        assert chk["classes"]["fg_made"]["espn_lag_median"] >= 10 and chk["classes"]["fg_made"]["corrected_within_2s"] > 0.98
        # No transition call where ESPN's clock can't place the start (after turnovers).
        cur.execute("""SELECT COUNT(*) FROM possessions WHERE start_type IN ('steal', 'dead_tov')
                       AND (transition IS NOT NULL OR first_attempt_sec IS NOT NULL)""")
        assert cur.fetchone()[0] == 0
        # League points per possession: after a steal > after a defensive rebound > after a made shot, every season.
        cur.execute("""SELECT season, MAX(ppp) FILTER (WHERE start_type = 'steal'), MAX(ppp) FILTER (WHERE start_type = 'dreb'),
                              MAX(ppp) FILTER (WHERE start_type = 'made_fg')
                       FROM possession_seasons WHERE team = 'ALL' GROUP BY 1""")
        assert all(st > dr > mk for _, st, dr, mk in cur.fetchall())
    finally:
        conn.close()



def test_event_clock_table_and_readers():
    """The corrected clock (scripts/build_event_clock.py -> pbp_event_clock, rules in
    pbp_possessions.corrected_clock()): one row per ESPN event, consistent columns, never running backwards, and
    read the same way by possessions, the Play Finder, the parser's optional clock argument and Game Replay."""
    import pandas as pd
    sys.path.insert(0, os.path.join(os.path.dirname(_API_DIR), "scripts"))
    from pbp_lineups import Game, game_clock, load_season_names
    from pbp_possessions import CLOCK_SOURCES
    from impact_api import app
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute("""SELECT COUNT(*), COUNT(c.event_id), COUNT(*) FILTER (WHERE c.source <> ALL(%s)),
                              COUNT(*) FILTER (WHERE abs(c.seconds_remaining - (CASE WHEN e.period <= 4
                                  THEN (5 - e.period) * 720 ELSE 300 END - c.period_t)) > 1e-9),
                              COUNT(*) FILTER (WHERE c.period_t < 0 OR c.period_t > CASE WHEN e.period <= 4 THEN 720 ELSE 300 END)
                       FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
                       LEFT JOIN pbp_event_clock c ON c.event_id = e.id WHERE g.source = 'espn'""", (list(CLOCK_SOURCES),))
        n, covered, bad_source, bad_secs, outside = cur.fetchone()
        assert n > 3_400_000 and covered == n and bad_source == bad_secs == outside == 0
        cur.execute("SELECT COUNT(*) FROM pbp_event_clock c LEFT JOIN pbp_events e ON e.id = c.event_id WHERE e.id IS NULL")
        assert cur.fetchone()[0] == 0
        # Never backwards within a period (in the parser's event order).
        cur.execute("""SELECT COUNT(*) FROM (
                           SELECT c.period_t, LAG(c.period_t) OVER (PARTITION BY e.game_id, e.period ORDER BY e.action_number, e.id) prev
                           FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id JOIN pbp_event_clock c ON c.event_id = e.id
                           WHERE g.source = 'espn' AND g.season = 2025) x WHERE period_t < prev""")
        assert cur.fetchone()[0] == 0
        # Possessions took their clock check from the clock's own meta; the corrected clock is within 2 s of NBA.com's
        # for > 90% of events, ESPN's for about a third; Game Replay's model scores no worse on it (so no refit).
        cur.execute("""SELECT p.value = c.value, c.value FROM possession_meta p, pbp_event_clock_meta c
                       WHERE p.key = 'clock_check' AND c.key = 'clock_check'""")
        same, chk = cur.fetchone()
        assert same and chk["all"]["corrected_within_2s"] > 0.9 > 0.5 > chk["all"]["espn_within_2s"]
        cur.execute("SELECT value FROM pbp_event_clock_meta WHERE key = 'wp_check'")
        wp = cur.fetchone()[0]
        for phase in ("all", "last_5_min", "last_minute"):
            assert wp[phase]["log_loss_corrected"] <= wp[phase]["log_loss_espn"] + 1e-4
        # The Play Finder's clock is the stored one (tenths left in the period).
        cur.execute("""SELECT COUNT(*), COUNT(*) FILTER (WHERE p.clock <> round(((CASE WHEN p.period <= 4 THEN 720 ELSE 300 END)
                                                                                    - c.period_t) * 10))
                       FROM play_finder_events p JOIN play_finder_games g USING (game_no)
                       JOIN pbp_event_clock c ON c.event_id = p.event_id WHERE g.season = 2026""")
        rows, differ = cur.fetchone()
        assert rows > 500_000 and differ == 0
        # The parser: without a clock ESPN's times, with one the stored times; stints keep their events either way.
        gid = "espn_401584689"
        ev = pd.read_sql_query(
            """SELECT e.game_id, e.action_number, e.id, e.period, e.seconds_remaining, e.score_home, e.score_away,
                      e.team_tricode, e.person_id, e.player_name, e.action_type, e.description, c.period_t,
                      c.anchored AS clock_anchored, c.source AS clock_source
               FROM pbp_events e LEFT JOIN pbp_event_clock c ON c.event_id = e.id
               WHERE e.game_id = %s ORDER BY e.action_number, e.id""", conn, params=(gid,))
        ev[["description", "action_type"]] = ev[["description", "action_type"]].fillna("")
        ev["person_id"] = ev["person_id"].astype("Int64").astype(object).where(ev["person_id"].notna(), None)
        cur.execute("SELECT season, home_team FROM pbp_games WHERE game_id = %s", (gid,))
        season, home = cur.fetchone()
        names, all_names = load_season_names(cur)
        clock, anchored = game_clock(ev)
        assert len(clock) == len(ev) and anchored
        plain = Game(gid, season, None, ev, names[season], all_names)
        timed = Game(gid, season, None, ev, names[season], all_names, clock=clock)
        plain.home = timed.home = home
        stored = dict(zip(ev.action_number, (5 - ev.period).clip(lower=0) * 720 - ev.period_t))
        for e in plain.parse():
            assert plain.secs(e) == e["secs"]
            if e["period"] <= 4:
                assert abs(timed.secs(e) - stored[e["action_number"]]) < 1e-9
        a, _ = plain.stints(home)
        b, _ = timed.stints(home)
        key = lambda s: (s["home"], s["away"], s["action_from"], s["action_to"], s["h_fga"], s["a_fga"])  # noqa: E731
        assert [key(s) for s in a if s["action_from"] is not None] == [key(s) for s in b if s["action_from"] is not None]
    finally:
        conn.close()
    # Game Replay: ESPN games on the corrected clock, times never backwards.
    r = TestClient(app).get(f"/games/wp-replay/{gid}").json()
    _assert_has_source(r)
    assert r["clock"] == "corrected" and "pbp_event_clock" in str(r["_source"])
    t = [p["seconds_elapsed"] for p in r["points"]]
    assert all(y >= x - 1e-9 for x, y in zip(t, t[1:]))


def test_possession_explorer():
    """Possession Explorer (api/routers/possessions.py, round 6 step 4): the league and every team by how the
    possession began, from possession_seasons; the player block's on/off split live from possessions and
    lineup_stints. Totals agree across the three reads, the transition split covers the same timed possessions as
    the stored share, and a player's on-court possessions agree with On/Off's estimate."""
    from impact_api import app
    from impact_core import get_db
    client = TestClient(app)
    o = client.get("/possessions/options").json()
    _assert_has_source(o)
    assert [s["season"] for s in o["seasons"]] == [2021, 2022, 2023, 2024, 2025, 2026] and len(o["teams"]) == 30
    # Six seasons pooled: a steal is worth about 0.2 points more than the inbound after a make.
    p = o["pooled"]
    assert 0.15 < p["steal"]["ppp"] - p["made_fg"]["ppp"] < 0.25 and p["steal"]["ppp"] > p["dead_tov"]["ppp"]
    assert sum(v["poss"] for k, v in p.items() if k != "all") == p["all"]["poss"]
    assert o["transition"]["window_seconds"] == 7 and len(o["transition"]["dreb_ppp_by_second"]) == 24

    d = client.get("/possessions/league", params={"season": 2025}).json()
    _assert_has_source(d)
    lg = {r["start_type"]: r for r in d["league"]}
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT COUNT(*), SUM(p.pts) FROM possessions p JOIN possession_games g USING (game_id)
                       WHERE g.game_ok AND p.season = 2025""")
        n, pts = cur.fetchone()
    assert (lg["all"]["poss"], lg["all"]["pts"]) == (n, pts) and abs(lg["all"]["ppp"] - pts / n) < 1e-4
    assert lg["all"]["lo"] < lg["all"]["ppp"] < lg["all"]["hi"]
    # 30 teams; their offence and defence possessions each add up to the league's; ranks run 1-30.
    teams = d["teams"]
    assert len(teams) == 30
    assert sum(t["by_start"]["all"]["off"]["poss"] for t in teams) == n == sum(t["by_start"]["all"]["def"]["poss"] for t in teams)
    assert sorted(t["by_start"]["all"]["off"]["rank"] for t in teams) == list(range(1, 31))
    best_def = min(teams, key=lambda t: t["by_start"]["all"]["def"]["ppp"])
    assert best_def["by_start"]["all"]["def"]["rank"] == 1
    # Transition vs settled covers exactly the timed possessions behind the stored share.
    tr = {x["start_type"]: x for x in d["transition"]}
    assert tr["all"]["trans"]["poss"] + tr["all"]["settled"]["poss"] == lg["all"]["timed_poss"]
    assert tr["all"]["trans"]["poss"] == lg["all"]["trans_poss"] and tr["dreb"]["trans"]["ppp"] > tr["dreb"]["settled"]["ppp"] + 0.3
    # Second-chance points allowed add up to the league's second-chance points.
    assert sum(t["totals"]["d_second_chance_pg"] * t["totals"]["games"] for t in teams) == pytest.approx(lg["all"]["second_chance_pts"], abs=30)
    # Signal check: whole-possession efficiency is mostly a team trait, efficiency after a steal mostly isn't.
    sig = {(s["start_type"], s["side"]): s for s in d["signal"]}
    assert sig[("all", "off")]["real_share"] > 0.6 > 0.4 > sig[("steal", "off")]["real_share"]
    assert len(sig[("all", "off")]["yty"]) == 5
    assert client.get("/possessions/league", params={"season": 2019}).status_code == 400

    t = client.get("/possessions/team/okc").json()
    assert t["team"] == "OKC" and [s["season"] for s in t["seasons"]] == [2021, 2022, 2023, 2024, 2025, 2026]
    okc25 = next(x for x in teams if x["team"] == "OKC")
    for key in ("all", "steal"):
        for side in ("off", "def"):
            a, b = t["seasons"][4]["by_start"][key][side], okc25["by_start"][key][side]
            assert (a["poss"], a["pts"], a["ppp"]) == (b["poss"], b["pts"], b["ppp"])
    assert client.get("/possessions/team/XXX").status_code == 400

    # Player block: on + off = every tracked possession of his team in his games, per start type; on-court
    # possessions within 5% of On/Off's estimated possessions (counted vs FGA + 0.44 FTA - OREB + TOV).
    j = client.get("/possessions/player/203999", params={"season": 2026}).json()
    _assert_has_source(j)
    den = j["teams"][0]
    rows = {r["start_type"]: r for r in den["rows"]}
    for side in ("off", "def"):
        for where in ("on", "off_court"):
            assert sum(r[f"{side}_{where}"]["poss"] for k, r in rows.items() if k != "all" and r[f"{side}_{where}"]) == rows["all"][f"{side}_{where}"]["poss"]
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT poss_on FROM player_on_off WHERE player_id = 203999 AND season = 2026 AND team_abbreviation = 'DEN'")
        est = cur.fetchone()[0]
    assert abs(rows["all"]["off_on"]["poss"] / est - 1) < 0.05
    assert rows["all"]["off_diff"]["lo"] < rows["all"]["off_diff"]["diff"] < rows["all"]["off_diff"]["hi"]
    assert client.get("/possessions/player/1").status_code == 404
    prof = client.get("/player-profile/203999").json()
    assert prof["possessions"]["seasons"] == [2021, 2022, 2023, 2024, 2025, 2026]

# ─── Round 4, Phase 2: RAPM ───────────────────────────────────────────────────

def test_rapm_versions_validation_and_profile():
    """RAPM (scripts/build_rapm.py, api/routers/rapm.py): ridge regression on every tracked five-man stint,
    three versions, lambda by game-grouped cross-validation, game-bootstrap errors, held-out and next-season
    tests against BPM, on/off and everyone-average. The plain single-season version doesn't beat BPM at
    predicting next season's games; that known result is stored, not hidden."""
    from impact_api import app
    client = TestClient(app)
    o = client.get("/rapm/options").json()
    _assert_has_source(o)
    versions = {v["id"]: v for v in o["versions"]}
    assert set(versions) == {"single", "multi", "prior", "tracker", "shotaware"} and o["qualified_poss"] == 1000 and o["bootstraps"] >= 200
    assert versions["single"]["seasons"] == [2021, 2022, 2023, 2024, 2025, 2026]
    assert versions["multi"]["seasons"] == [2023, 2024, 2025, 2026]   # three seasons on file from 2022-23 on

    # 2023-24 single season: lambda chosen inside the grid, home edge about 2 points per 100, Jokić top 5
    # among 1,000+ possession players, RAPM = O + D, intervals contain the estimate, correlation with BPM
    # positive but well under 1.
    d = client.get("/rapm", params={"version": "single", "season": 2024}).json()
    _assert_has_source(d)
    fit = d["fit"]
    assert fit["lambda_rule"] == "cv_min" and 250 < fit["lambda"] < 128000 and fit["cv_rmse"] < fit["cv_rmse_zero"]
    assert 1.0 < fit["home_edge_per_100"] < 3.5 and fit["games"] > 1200 and fit["cv_folds"] == 5
    q = [p for p in d["players"] if p["qualified"]]
    assert len(q) == d["noise"]["qualified"] and all(p["poss"] >= 1000 for p in q)
    jokic = next(p for p in q if p["player_name"] == "Nikola Jokić")
    assert jokic["rapm_rank"] <= 5 and jokic["rapm"] > 4 and jokic["orapm"] > 2
    for p in d["players"]:
        assert abs(p["rapm"] - (p["orapm"] + p["drapm"])) < 0.002
        assert p["rapm_ci_low"] <= p["rapm"] <= p["rapm_ci_high"] and p["rapm_se"] > 0
    assert 0.4 < d["noise"]["corr_with_bpm"] < 0.8
    assert d["noise"]["ci_excludes_zero"] > d["noise"]["expected_by_chance"]
    assert len(d["lambda_curve"]) >= 10 and abs(min(c["cv_rmse"] for c in d["lambda_curve"]) - fit["cv_rmse"]) < 0.001
    # Team filter keeps the league rank; a bad team is a 404; a possessions floor is applied live.
    den = client.get("/rapm", params={"version": "single", "season": 2024, "team": "DEN"}).json()
    assert all("DEN" in p["team_list"] for p in den["players"])
    assert next(p for p in den["players"] if p["player_id"] == jokic["player_id"])["rapm_rank"] == jokic["rapm_rank"]
    assert client.get("/rapm", params={"season": 2024, "team": "XXX"}).status_code == 404
    hi = client.get("/rapm", params={"version": "single", "season": 2024, "min_poss": 4000}).json()
    assert hi["noise"]["qualified"] < d["noise"]["qualified"] and all(p["qualified"] == (p["poss"] >= 4000) for p in hi["players"])

    # The prior version keeps the single-season lambda and says so; the cross-validation minimum (stored) is
    # further out. The three-season window ending 2025-26 pools 2023-24 to 2025-26.
    pr = client.get("/rapm", params={"version": "prior", "season": 2024}).json()
    assert pr["fit"]["lambda_rule"] == "single_lambda" and pr["fit"]["lambda"] == fit["lambda"]
    assert pr["fit"]["cv_best_lambda"] >= pr["fit"]["lambda"] and pr["fit"]["cv_best_rmse"] <= pr["fit"]["cv_rmse"]
    assert pr["fit"]["players_with_prior"] > 400 and any(p["prior_o"] is not None for p in pr["players"])
    mu = client.get("/rapm", params={"version": "multi", "season": 2026}).json()
    assert (mu["fit"]["seasons_from"], mu["fit"]["seasons_to"]) == (2024, 2026) and mu["fit"]["players"] > 650
    assert client.get("/rapm", params={"version": "multi", "season": 2021}).status_code == 404
    assert client.get("/rapm", params={"version": "nope"}).status_code == 400

    # Validation: every model beats everyone-average on held-out games; next season, on/off used as published is
    # far too big (its best scale is well under 1) and plain RAPM does not beat BPM; year-to-year, BPM is
    # steadier than single-season RAPM.
    v = client.get("/rapm/validation").json()
    _assert_has_source(v)
    rows = v["validation"]
    held = {r["model"]: r for r in rows if r["test"] == "held_out_games" and r["season"] == 2024}
    assert {"rapm_single", "rapm_multi", "rapm_prior", "bpm", "onoff", "zero"} <= set(held)
    assert held["rapm_single"]["game_rmse"] < held["zero"]["game_rmse"] and held["rapm_single"]["game_corr"] > 0.3
    nxt = {r["model"]: r for r in rows if r["test"] == "next_season" and r["season"] == 2025}
    assert nxt["rapm_single"]["game_rmse"] < nxt["zero"]["game_rmse"] and nxt["bpm"]["game_rmse"] < nxt["zero"]["game_rmse"]
    assert nxt["onoff"]["scale_fit"] < 0.6 and nxt["onoff"]["game_rmse"] > nxt["zero"]["game_rmse"]
    assert nxt["rapm_single"]["game_rmse"] >= nxt["bpm"]["game_rmse"] - 0.3   # the known result: no clear win over BPM
    assert 0.8 < nxt["rapm_single"]["coverage"] <= 1 and nxt["rapm_single"]["coverage_all10"] < nxt["rapm_single"]["coverage"]
    y2y = {r["model"]: r for r in rows if r["test"] == "year_to_year" and r["season"] == 2025}
    assert y2y["bpm"]["corr"] > y2y["rapm_single"]["corr"] > y2y["onoff"]["corr"] - 0.05 and y2y["rapm_single"]["players"] > 200
    # Data Coverage lists the table; the profile carries the block with league ranks.
    cov = client.get("/meta/coverage").json()
    assert any(t["table"] == "player_rapm" and t["n_rows"] > 8000 for t in cov["tables"])
    prof = client.get("/player-profile/203999").json()["rapm"]
    assert prof["qualified_poss"] == 1000 and {r["version"] for r in prof["rows"]} == {"single", "multi", "prior"}
    j24 = next(r for r in prof["rows"] if r["version"] == "single" and r["season"] == 2024)
    assert j24["qualified"] and j24["rank"] == jokic["rapm_rank"] and j24["n_qualified"] == d["noise"]["qualified"]


def test_rotations_minutes_closing_and_team_block():
    """Rotations (api/routers/rotations.py, scripts/build_rotations.py): per-game rotation charts, team-season
    heatmaps and closing lineups from the play-by-play stints; the closing stretch is the stints cut at 5:00 left in
    the fourth, which must glue back into lineup_stints exactly."""
    from impact_api import app
    client = TestClient(app)
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        # The cut stints (on the corrected clock since round 6 step 3b) glue back into lineup_stints' stints (same
        # fives, counts, points) in every game but three, each an event-less stint the corrected clock gives no time:
        # espn_401468511, where ESPN logs a "free throw 2 of 2" with no 1 of 2, the clock gives it the previous trip's
        # time and an 11 s stint collapses (README Known real gaps); espn_401468743 and espn_401704644 (since round 8
        # step 6b), free-throw trips with substitutions between the shots: ESPN's clock gives the lineup between the
        # substitutions 12-15 s, the corrected clock puts the whole trip at its first free throw, and with free throws
        # credited at the foul (step 6a) that lineup has nothing credited either, so the cut replay keeps no stint for
        # it. Stint boundaries move by 2 s or less except in a handful of games. Score at 5:00 + closing stretch =
        # real final.
        cur.execute("""SELECT COUNT(*), COUNT(*) FILTER (WHERE game_ok), COUNT(*) FILTER (WHERE game_ok AND final_matches),
                              COUNT(*) FILTER (WHERE stint_shift > 2)
                       FROM rotation_closing_games""")
        n, ok, final_ok, moved = cur.fetchone()
        assert n > 7000 and ok == final_ok and moved <= 20
        cur.execute("SELECT game_id FROM rotation_closing_games WHERE NOT matches_stints ORDER BY 1")
        assert [g for (g,) in cur.fetchall()] == ["espn_401468511", "espn_401468743", "espn_401704644"]
        cur.execute("""SELECT COUNT(*) FROM rotation_closing_games g JOIN (
                           SELECT game_id, SUM(home_pts) hp, SUM(away_pts) ap FROM rotation_closing_stints GROUP BY 1) c
                       USING (game_id)
                       WHERE g.game_ok AND (g.home_at_cut + c.hp <> g.home_final OR g.away_at_cut + c.ap <> g.away_final)""")
        assert cur.fetchone()[0] == 0
        # Stint seconds per player-game equal player_game_lines everywhere (until round 8 step 6a the lines counted
        # 9 player-games twice, one in each ESPN game with a substitution logged with no team; R8-023).
        cur.execute("""WITH s AS (SELECT game_id, pid, SUM(seconds) secs FROM (
                           SELECT game_id, unnest(home_ids) pid, seconds FROM lineup_stints
                           UNION ALL SELECT game_id, unnest(away_ids), seconds FROM lineup_stints) x GROUP BY 1, 2)
                       SELECT COUNT(*) FROM s JOIN player_game_lines l ON l.game_id = s.game_id AND l.player_id = s.pid
                       WHERE ABS(s.secs - l.seconds) > 0.2""")
        assert cur.fetchone()[0] == 0
    finally:
        conn.close()

    o = client.get("/rotations/options").json()
    _assert_has_source(o)
    assert o["seasons"] == [2021, 2022, 2023, 2024, 2025, 2026] and all(len(t) == 30 for t in o["teams"].values())

    # Opening night 2023-24: DEN 119, LAL 107; LeBron played 29 minutes. Every player's minutes equal his Game Log's,
    # and each side's player-seconds (plus unidentified slots) fill five places for the whole game.
    g = client.get("/rotations/game/espn_401584689").json()
    _assert_has_source(g)
    assert (g["home_team"], g["away_team"], g["final"]["home"], g["final"]["away"]) == ("DEN", "LAL", 119, 107)
    assert g["margin"][-1]["home"] - g["margin"][-1]["away"] == 12
    lebron = next(p for p in g["away"]["players"] if p["player_id"] == 2544)
    assert 29 <= lebron["minutes"] < 30 and lebron["starter"]
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        cur.execute("SELECT player_id, seconds FROM player_game_lines WHERE game_id = 'espn_401584689' AND seconds > 0")
        lines = {int(p): float(s) for p, s in cur.fetchall()}
    finally:
        conn.close()
    mine = {p["player_id"]: p["seconds"] for side in ("home", "away") for p in g[side]["players"]}
    assert mine.keys() == lines.keys() and all(abs(mine[k] - lines[k]) < 0.2 for k in lines)
    for side in ("home", "away"):
        filled = sum(p["seconds"] for p in g[side]["players"]) + sum((u["to"] - u["from"]) * u["missing"] for u in g[side]["unidentified"])
        assert abs(filled - 5 * g["length"]) < 1
    assert g["nba_game_id"] == "0022300061" and client.get("/rotations/game/0022300061").json()["game_id"] == g["game_id"]

    # Denver 2023-24: Jokic started all 79 games he played; the real starting five started together most; starters
    # are on the floor at the start of both halves; every heatmap column adds up to five players.
    t = client.get("/rotations/team", params={"team": "DEN", "season": 2024}).json()
    _assert_has_source(t)
    assert t["games"] == 82 and t["games_counted"] == 82 and not t["excluded"]
    jokic = next(p for p in t["players"] if p["player_id"] == 203999)
    assert jokic["games"] == 79 and jokic["starts"] == 79
    assert {p["player_id"] for p in t["starting_five"]["players"]} == {203999, 1627750, 1629008, 203932, 203484}
    assert jokic["share"][0] > 0.9 and jokic["share"][24] > 0.85 and jokic["share"][12] < 0.3
    for m in range(48):
        assert abs(sum(p["share"][m] for p in t["players"]) + t["unidentified"][m] - 5) < 0.02
    c = t["closing"]
    assert c["available"] and c["record"]["wins"] + c["record"]["losses"] == c["close_games"] > 10
    assert c["lineups"] and all(x["minutes"] >= c["min_minutes"] for x in c["lineups"])
    assert c["stretch_minutes"] >= sum(x["minutes"] for x in c["lineups"])   # the table is a subset
    assert client.get("/rotations/team", params={"team": "DEN", "season": 2019}).status_code == 404
    assert client.get("/rotations/team", params={"team": "PHO", "season": 2024}).json()["team"] == "PHX"

    # Team page: the block is there from 2020-21 and "Not on file" before; Game Replay opens a linked game's season.
    assert client.get("/team-profile/DEN", params={"season": 2024}).json()["rotations"]["available"]
    assert not client.get("/team-profile/DEN", params={"season": 2019}).json()["rotations"]["available"]
    lst = client.get("/games/wp-replay/list", params={"game_id": "espn_401584689"}).json()
    assert lst["season"] == 2024 and any(x["game_id"] == "espn_401584689" for x in lst["games"])
    cov = client.get("/meta/coverage").json()
    assert any(x["table"] == "rotation_closing_games" and x["n_rows"] > 7000 for x in cov["tables"])


def test_rim_deterrence_known_cases():
    """Rim deterrence (scripts/build_rim_deterrence.py, api/routers/rim_deterrence.py): every attempt placed in
    its lineup_stints stint, distance from the NBA shot chart's coordinates, opponents' rim attempts/FG%/points
    with each defender on vs. off. Gobert is the textbook case; on/off noise is disclosed, not hidden."""
    from impact_api import app
    from impact_core import get_db
    client = TestClient(app)
    d = client.get("/defense/rim-deterrence", params={"season": 2025}).json()
    _assert_has_source(d)
    lg = d["league"]
    # Event -> stint mapping is exact; nearly every two gets its distance from coordinates; the
    # no-distance rule is right ~86% of the time where the shot chart can check it.
    assert lg["stints_fga_mismatch"] == 0 and lg["three_agree"] > 0.98
    src = lg["sources"]
    assert src["coords"] / (src["coords"] + src["text"] + src["rule"] + src["unknown"]) > 0.97
    assert 0.8 < lg["rule"]["share_right"] < 0.92
    assert 22 < lg["bands"]["rim"]["per100"] < 28 and 0.6 < lg["bands"]["rim"]["fg"] < 0.72
    # The league's rim share matches player_shots' own coordinates (under 4 ft, regular season).
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT AVG((shot_type NOT LIKE '3%%' AND SQRT(loc_x^2 + loc_y^2) < 40)::int)
                       FROM player_shots WHERE season = '2024-25' AND game_id LIKE '002%%'""")
        assert abs(float(cur.fetchone()[0]) - lg["bands"]["rim"]["share"]) < 0.005
    # League view: 1,000+ minutes by default, biggest rim-points drop first; Gobert near the top and
    # clearly below zero on rim attempts.
    assert d["min_minutes"] == 1000 and all(p["qualified"] and p["minutes_on"] >= 1000 for p in d["players"])
    diffs = [p["rim_pts100_diff"] for p in d["players"]]
    assert diffs == sorted(diffs)
    gobert = next(p for p in d["players"] if p["player_id"] == 203497)
    assert gobert["rim_pts100_rank"] <= 3 and gobert["rim_fga100_hi"] < 0 and gobert["big"]
    for p in d["players"]:
        for k in ("rim_fga100", "rim_fg", "rim_pts100"):
            if p[f"{k}_lo"] is not None:
                assert p[f"{k}_lo"] - 1e-3 <= p[f"{k}_diff"] <= p[f"{k}_hi"] + 1e-3
        assert sum(p["bands"][b]["on"]["fga"] for b in p["bands"]) == p["fga_on"]
        assert p["bands"]["rim"]["on"]["fga"] == p["rim_fga_on"]
        assert abs(p["rim_fga100_on"] - 100 * p["rim_fga_on"] / p["poss_on"]) < 0.01
    # More intervals clear zero than chance; the attempts gap repeats year to year more than the FG% gap.
    assert d["noise"]["rim_fga100"]["ci_excludes_zero"] > 2 * d["noise"]["rim_fga100"]["expected_by_chance"]
    st = d["stability"]
    assert st["pairs"] > 500 and 0.2 < st["rim_fga100"] < 0.5 and st["rim_fg"] < st["rim_fga100"]
    # Team view keeps league ranks; the centers filter only keeps listed centers.
    mn = client.get("/defense/rim-deterrence", params={"season": 2025, "team": "MIN"}).json()
    assert mn["players"] and all(p["team_abbreviation"] == "MIN" for p in mn["players"])
    assert next(p for p in mn["players"] if p["player_id"] == 203497)["rim_pts100_rank"] == gobert["rim_pts100_rank"]
    bigs = client.get("/defense/rim-deterrence", params={"season": 2025, "position": "bigs"}).json()
    assert bigs["players"] and all(p["big"] for p in bigs["players"])
    # Profile block: every season 2020-21 on.
    prof = client.get("/player-profile/203497").json()["rim_deterrence"]
    assert {r["season"] for r in prof["rows"]} == {2021, 2022, 2023, 2024, 2025, 2026}
    # Guards.
    assert client.get("/defense/rim-deterrence", params={"season": 2010}).status_code == 404
    assert client.get("/defense/rim-deterrence", params={"team": "XXX"}).status_code == 404
    assert client.get("/defense/rim-deterrence", params={"position": "guards"}).status_code == 400


def test_assist_network_matches_game_log_and_known_duos():
    """Assist network (scripts/build_assist_network.py, api/routers/assist_network.py): every assisted basket
    from the play-by-play, passer named in the text, matched by the same parser as player_game_lines, so the
    assists must equal the Game Log's; well-known duos and shot creators land where they should."""
    from impact_api import app
    from impact_core import get_db
    client = TestClient(app)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('assist_pairs')")
        if cur.fetchone()[0] is None:
            pytest.skip("assist_pairs not built (run scripts/build_assist_network.py)")
        # Same parser: each player-season's assists (pairs + the 4 dropped data errors aside) equal his
        # player_game_lines assists over the same games (the NBA Cup finals are left out of both).
        cur.execute("""
            WITH mine AS (SELECT season, player_id, SUM(ast) ast FROM player_assisted_share GROUP BY 1, 2),
                 lines AS (SELECT season, player_id, SUM(ast) ast FROM player_game_lines
                           WHERE game_id IN (SELECT 'espn_' || espn_id FROM game_scores WHERE espn_id IS NOT NULL)
                           GROUP BY 1, 2)
            SELECT COUNT(*), COUNT(*) FILTER (WHERE COALESCE(m.ast, 0) <> COALESCE(l.ast, 0))
            FROM mine m FULL JOIN lines l USING (season, player_id)""")
        n, differ = cur.fetchone()
        cur.execute("SELECT SUM(data_errors) FROM assist_seasons")
        assert n > 3000 and differ <= cur.fetchone()[0]
    opts = client.get("/assists/options").json()
    _assert_has_source(opts)
    assert opts["seasons"] == [2021, 2022, 2023, 2024, 2025, 2026]
    for lg in opts["league"].values():
        assert lg["lines_mismatch"] == 0 and 0.995 < lg["ast_vs_nba"] < 1.005
        assert 0.58 < lg["assisted_share"] < 0.66 and lg["share3"] > 0.8 > lg["share2"] > 0.45
        assert lg["unknown_passer"] < 0.01 * lg["assisted"]
    # League's top duo of 2023-24: Haliburton -> Turner.
    duos = client.get("/assists/pairs", params={"season": 2024}).json()
    top = duos["pairs"][0]
    assert (top["passer_id"], top["scorer_id"]) == (1630169, 1626167) and top["ast"] > 200
    assert [p["ast"] for p in duos["pairs"]] == sorted((p["ast"] for p in duos["pairs"]), reverse=True)
    threes = client.get("/assists/pairs", params={"season": 2024, "sort": "three"}).json()["pairs"]
    assert [p["ast3"] for p in threes] == sorted((p["ast3"] for p in threes), reverse=True)
    # Team view adds up; Jokic -> Gordon led Denver in 2022-23, mostly at the rim; Green -> Curry leads
    # Golden State every season.
    den = client.get("/assists/team", params={"team": "DEN", "season": 2023}).json()
    _assert_has_source(den)
    e = den["edges"][0]
    assert (e["passer_id"], e["scorer_id"]) == (203999, 203932) and e["kinds"]["rim"] > 0.7 * e["ast"]
    assert sum(x["ast"] for x in den["edges"]) == sum(p["ast"] for p in den["players"])
    assert sum(p["ast_fgm2"] + p["ast_fgm3"] for p in den["players"]) == den["totals"]["assisted"]
    assert all(sum(x["kinds"].values()) == x["ast"] and x["ast2"] + x["ast3"] == x["ast"] for x in den["edges"])
    assert den["totals"]["games"] <= 82
    for season in opts["seasons"]:
        g = client.get("/assists/team", params={"team": "GSW", "season": season}).json()["edges"][0]
        assert (g["passer_id"], g["scorer_id"]) == (203110, 201939)
    # Creators make their own shots; spot-up shooters' threes are set up.
    for pid, season in ((201935, 2024), (1628983, 2024), (1629029, 2024)):       # Harden, SGA, Doncic
        assert client.get(f"/assists/player/{pid}", params={"season": season}).json()["summary"]["share2"] < 0.25
    for pid, season in ((202691, 2024), (1629130, 2021)):                        # Klay, Duncan Robinson
        r = client.get(f"/assists/player/{pid}", params={"season": season}).json()
        assert r["summary"]["share3"] > 0.9 > r["league"]["share3"]
    # Profile and team page blocks.
    assert client.get("/player-profile/1630169").json()["assists"]["seasons"] == [2021, 2022, 2023, 2024, 2025]
    assert client.get("/team-profile/DEN", params={"season": 2023}).json()["assists"]["available"] is True
    assert client.get("/team-profile/DEN", params={"season": 2019}).json()["assists"]["available"] is False
    # Guards.
    assert client.get("/assists/team", params={"team": "XXX", "season": 2024}).status_code == 404
    assert client.get("/assists/team", params={"team": "DEN", "season": 2010}).status_code == 404
    assert client.get("/assists/pairs", params={"sort": "drop table"}).status_code == 400
    assert client.get("/assists/player/1").status_code == 404


def test_play_finder_matches_game_log_and_bam_83():
    """Play Finder (scripts/build_play_finder.py, api/routers/play_finder.py): every play from the shared
    play-by-play parser with the same shot-chart calls on misses, so each player-game's counts equal his Game Log
    line, threes included; Bam Adebayo's 83 on 2026-03-10 comes back whole; filters are whitelisted."""
    from impact_api import app
    from impact_core import get_db
    client = TestClient(app)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('play_finder_events')")
        if cur.fetchone()[0] is None:
            pytest.skip("play_finder_events not built (run scripts/build_play_finder.py)")
        cur.execute("SELECT SUM(lines_checked), SUM(lines_differ), SUM(lines_fg3a_differ), SUM(games), SUM(rows) "
                    "FROM play_finder_seasons")
        checked, differ, fg3a_differ, games, rows = cur.fetchone()
        assert checked > 150_000 and differ == 0 and fg3a_differ == 0 and games == 7229
        # The Game Log's threes: missed shots take the NBA shot chart's call, so 3PA total NBA.com's within 0.2%
        # every season (the text alone left them 0.6-1.9% short); Bam went 7-22 from three that night.
        cur.execute("""WITH l AS (SELECT player_id, season, SUM(fg3a) f FROM player_game_lines GROUP BY 1, 2)
                       SELECT MIN(r), MAX(r) FROM (SELECT SUM(l.f) / SUM(s.fg3a * s.gp) r FROM l
                       JOIN player_season_stats s USING (player_id, season) WHERE s.gp >= 20 GROUP BY s.season) x""")
        lo, hi = cur.fetchone()
        assert 0.998 < lo <= hi < 1.002
        cur.execute("SELECT fg3m, fg3a FROM player_game_lines WHERE player_id = 1628389 AND game_date = '2026-03-10'")
        assert cur.fetchone() == (7, 22)
        cur.execute("SELECT COUNT(*) FROM play_finder_events")
        assert cur.fetchone()[0] == rows
        cur.execute("SELECT SUM(plays) FROM play_finder_games")
        assert cur.fetchone()[0] == rows
    opts = client.get("/plays/finder/options").json()
    _assert_has_source(opts)
    assert opts["seasons"] == {"from": 2021, "to": 2026} and len(opts["teams"]) == 30
    # Bam Adebayo, 83 points on 2026-03-10: 20 field goals (7 threes), 36 free throws.
    d = client.get("/plays/finder", params={"player_id": 1628389, "date_from": "2026-03-10",
                                            "date_to": "2026-03-10", "limit": 200}).json()
    _assert_has_source(d)
    n = {c["key"]: c["n"] for c in d["by_cat"]}
    assert n["made2"] + n["made3"] == 20 and n["made3"] == 7 and n["ftm"] == 36 and d["points"] == 83
    assert d["total"] == len(d["results"]) and {r["team"] for r in d["results"]} == {"MIA"}
    # Every offered category and sort is accepted; clutch/margin/distance filters hold on every row.
    for c in opts["categories"]:
        assert client.get("/plays/finder", params={"cat": c["key"], "game": d["results"][0]["game_id"],
                                                   "limit": 1}).status_code == 200
    d = client.get("/plays/finder", params={"cat": "made3", "clutch": 1, "season_from": 2026, "season_to": 2026,
                                            "limit": 200}).json()
    assert d["total"] > 500 and all(r["clutch"] and r["period"] >= 4 and abs(r["margin_before"]) <= 5
                                    and r["cat"] == "made3" for r in d["results"])
    d = client.get("/plays/finder", params={"cat": "made", "dist_min": 40, "sort": "dist", "limit": 50}).json()
    dists = [r["dist"] for r in d["results"]]
    assert dists and min(dists) >= 40 and dists == sorted(dists, reverse=True)
    d = client.get("/plays/finder", params={"cat": "made", "margin_min": -3, "margin_max": 0, "period": "4,ot",
                                            "clock_max": 3, "limit": 200}).json()
    assert all(-3 <= r["margin_before"] <= 0 and r["period"] >= 4 and r["seconds_left"] <= 3 for r in d["results"])
    # Well-known leaders: Jokić sets up the most Nuggets baskets; the blocks leaders are rim protectors.
    d = client.get("/plays/finder", params={"cat": "ast", "team": "DEN", "season_from": 2024, "season_to": 2024,
                                            "limit": 1}).json()
    assert d["most"][0]["player_id"] == 203999
    top_blocks = {p["player_id"] for p in client.get("/plays/finder", params={"cat": "blk", "limit": 1}).json()["most"][:5]}
    assert 203497 in top_blocks  # Rudy Gobert
    for bad in ({"cat": "x"}, {"team": "ZZZ"}, {"sort": "id; drop table"}, {"period": "9"}):
        assert client.get("/plays/finder", params=bad).status_code == 400
    assert client.get("/plays/finder", params={"game": "nope"}).status_code == 404
    assert client.get("/plays/finder", params={"offset": 10_001}).status_code == 422


def test_season_simulator_pregame_odds_and_backtest():
    """Season simulator (scripts/build_season_sim.py, api/routers/season_sim.py, api/season_sim_lib.py): pre-game
    odds for every game 2010-11 on, fitted leave-one-season-out, and 10,000 simulated seasons from any morning with
    tiebreaks and the play-in. The honest backtest result (the record carried forward predicts the playoff field
    about as well as the model by midseason) is stored, not hidden."""
    from impact_api import app
    from impact_core import TEAM_META
    import season_sim_lib as L
    client = TestClient(app)
    # One conference map for every season on file; matches the app's team metadata for the 30 current codes.
    assert {t: L.CONFERENCE[t] for t in TEAM_META} == {t: m["conference"].title()[:4] for t, m in TEAM_META.items()}
    assert L.CONFERENCE["NJN"] == "East" and L.CONFERENCE["NOH"] == "West"

    o = client.get("/season-sim/options").json()
    _assert_has_source(o)
    assert [s["season"] for s in o["seasons"]] == list(range(2011, 2027)) and o["play_in_from"] == 2021
    assert o["form"] == "prior_rest" and 0.1 < o["beta"]["exp_margin"] < 0.2
    assert o["beta"]["home_b2b"] < -0.15 and o["beta"]["away_b2b"] > 0.15 and o["runs"] == 10000

    # 2015-16 at the halfway date: the 37-4 Warriors are in every run; odds add up exactly (8 playoff spots a
    # conference; each team's finish odds sum to 1); the real outcome is on every row.
    d = client.get("/season-sim", params={"season": 2016, "as_of": "2016-01-18"}).json()
    _assert_has_source(d)
    assert d["checkpoint"] == "halfway" and d["info"]["played_games"] == 614 and d["info"]["left_games"] == 616
    assert not d["play_in"]
    west, east = d["conferences"]["West"], d["conferences"]["East"]
    gsw = next(t for t in west if t["team"] == "GSW")
    assert (gsw["wins"], gsw["losses"]) == (37, 4) and gsw["p_playoffs"] == 1.0 and 0.4 < gsw["p_first"] < 0.6
    assert 65 < gsw["mean_wins"] < 70 and gsw["final"] == {"wins": 73, "losses": 9, "position": 1, "playoffs": True,
                                                            "play_in": False, "top6": None}
    cle = next(t for t in east if t["team"] == "CLE")
    assert cle["p_first"] > 0.75 and cle["final"]["position"] == 1
    for conf in (west, east):
        # The endpoint rounds each probability to 3 decimals, so 15 of them can be 0.0075 off in all.
        assert len(conf) == 15 and abs(sum(t["p_playoffs"] for t in conf) - 8) < 0.01
        assert sorted(t["position_now"] for t in conf) == list(range(1, 16))
        assert sum(t["final"]["playoffs"] for t in conf) == 8
        for t in conf:
            assert abs(sum(t["p_seed"]) - 1) < 2e-3 and t["wins_p10"] <= t["mean_wins"] <= t["wins_p90"]
            assert sum(t["wins_hist"]) == 10000 and t["wins"] + t["losses"] + t["games_left"] == t["games_final"] == 82
    assert len(d["games_on_date"]) == 10 and all(0 < g["p_home"] < 1 for g in d["games_on_date"])

    # Play-in era: top-6 and play-in odds add up to 6 and 4; the 2023-24 Celtics are certain at halfway.
    d = client.get("/season-sim", params={"season": 2024, "as_of": "2024-01-19"}).json()
    assert d["play_in"]
    east = d["conferences"]["East"]
    bos = next(t for t in east if t["team"] == "BOS")
    assert bos["p_playoffs"] == 1.0 and bos["p_top6"] > 0.99 and bos["final"]["top6"]
    assert abs(sum(t["p_top6"] for t in east) - 6) < 0.01 and abs(sum(t["p_playin"] for t in east) - 4) < 0.01
    assert sum(t["final"]["play_in"] for t in east) == 4 and sum(t["final"]["playoffs"] for t in east) == 8
    # Opening day: nothing played, the prior alone; a same-link rerun gives the same numbers (fixed seed).
    d = client.get("/season-sim", params={"season": 2026, "as_of": "2025-10-21"}).json()
    assert d["info"]["played_games"] == 0 and d["info"]["left_games"] == 1230 and not d["info"]["ratings_from_srs"]
    okc = next(t for t in d["conferences"]["West"] if t["team"] == "OKC")
    assert okc["p_playoffs"] > 0.9 and okc["wins_p10"] < okc["final"]["wins"] < okc["wins_p90"] + 5
    assert client.get("/season-sim", params={"season": 2026, "as_of": "2025-10-21"}).json()["conferences"] == d["conferences"]
    assert client.get("/season-sim", params={"season": 2016, "as_of": "2015-01-01"}).status_code == 400
    assert client.get("/season-sim", params={"season": 2009}).status_code == 404

    # The model page: the chosen form has the lowest held-out log loss, calibration holds by decile, the rest
    # effect is in the raw win rates, and the backtest keeps the baselines.
    m = client.get("/season-sim/model").json()
    _assert_has_source(m)
    ll = {f["form"]: f["loso_log_loss"] for f in m["forms"]}
    assert m["chosen"] == "prior_rest" and ll["prior_rest"] < ll["prior"] < ll["current"] < ll["baseline"] < 0.64
    assert all(0.64 < f["favourite_win_rate"] < 0.68 for f in m["forms"])
    for c in m["calibration"]["prior_rest"]:
        if c["n"] >= 300:
            assert abs(c["predicted"] - c["actual"]) < 0.03
    rest = {(r["home_b2b"], r["away_b2b"]): r["home_win_rate"] for r in m["rest"]}
    assert rest[(True, False)] < rest[(False, False)] < rest[(False, True)]
    era = {r["era"]: r for r in m["era"]}
    assert 0.57 < era["to_2020"]["home_win_rate"] < 0.60 and 0.54 < era["from_2021"]["home_win_rate"] < 0.57
    s = {(r["checkpoint"], r["method"], r["metric"]): r["value"] for r in m["backtest"]["summary"]}
    assert s[("halfway", "model", "playoffs_log_loss")] < s[("halfway", "record", "playoffs_log_loss")] < s[("halfway", "standings", "playoffs_log_loss")]
    assert s[("sixty", "model", "playoffs_brier")] < 0.07 and s[("halfway", "model", "wins_mae")] < s[("halfway", "record", "wins_mae")]
    assert 0.7 < s[("halfway", "model", "wins_cover80")] < 0.85 and s[("opening", "model", "wins_mae")] < 9
    assert {r["name"] for r in m["params"].values()} >= {"carry", "tau2", "hca_n0", "runs", "chosen_form"}

    # Stored facts: 16 playoff teams every season; the 2025-26 play-in field from ESPN's own games.
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT season, COUNT(*) FILTER (WHERE playoffs), COUNT(*) FILTER (WHERE play_in) FROM season_postseason GROUP BY 1 ORDER BY 1")
    rows = cur.fetchall()
    assert [r[0] for r in rows] == list(range(2010, 2027)) and all(r[1] == 16 for r in rows)
    assert all(r[2] == (8 if r[0] >= 2021 else 2 if r[0] == 2020 else 0) for r in rows)
    cur.execute("SELECT team_abbreviation FROM season_postseason WHERE season = 2026 AND play_in ORDER BY 1")
    assert [r[0] for r in cur.fetchall()] == ["CHA", "GSW", "LAC", "MIA", "ORL", "PHI", "PHX", "POR"]
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT season) FROM game_pregame_odds")
    assert cur.fetchone() == (19118, 16)
    cur.execute("SELECT COUNT(*) FROM postseason_games WHERE stage = 'play-in' AND season = 2026")
    assert cur.fetchone()[0] == 6
    conn.close()


def test_best_games_and_upsets():
    """Best Games & Upsets (scripts/build_best_games.py, api/routers/best_games.py, api/best_games.py): every game
    2020-21 on scored from win-probability swings on the reconciled score, with the formula on the page being the
    formula that made the stored numbers; upsets from the Season Simulator's held-out pre-game odds. Famous games
    sit where they should: the 35-point Clippers comeback tops the comebacks, Warriors-Lakers in double overtime is
    the best game, no big blowout is near the top."""
    from impact_api import app
    from impact_core import get_db
    import best_games as B
    client = TestClient(app)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('best_games')")
        if cur.fetchone()[0] is None:
            pytest.skip("best_games not built (run scripts/build_best_games.py)")
        cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE NOT score_ok) FROM best_games")
        assert cur.fetchone() == (7229, 7)
        # The stored score is the page's formula applied to the stored parts, in every game.
        cur.execute("SELECT swing, lead_changes, periods, final_margin, excitement, pts_home, pts_away, ties, comeback, "
                    "largest_lead, win_min_wp, peak_dwp FROM best_games")
        for swing, lc, periods, margin, x, ph, pa, ties, comeback, lead, low, peak in cur.fetchall():
            assert abs(B.excitement(swing, lc, periods - 4, margin) - x) < 2e-3
            assert margin == abs(ph - pa) and 0 <= low <= 1 and peak <= swing + 1e-6
            assert comeback >= 0 and lead >= 0 and ties >= 0 and (periods - 4) in (0, 1, 2, 3)
        # Every peak play is an event of its own game (event ids are pbp_events ids).
        cur.execute("""SELECT COUNT(*) FROM best_games b JOIN pbp_events e ON e.id = b.peak_event_id
                       WHERE e.game_id <> b.game_id""")
        assert cur.fetchone()[0] == 0

    o = client.get("/best-games/options").json()
    _assert_has_source(o)
    assert [s["season"] for s in o["seasons"]["best"]] == list(range(2021, 2027))
    assert [s["season"] for s in o["seasons"]["upsets"]] == list(range(2011, 2027))
    assert o["formula"]["rank_corr_swing"] > 0.95 and o["formula"]["lead_change_w"] == B.LEAD_CHANGE_W
    assert 0.65 < o["favourites"]["won"] < 0.67 and o["favourites"]["games"] == 19118
    for c in o["calibration"]:
        if c["games"] >= 200:   # the long shots come in about as often as the model says
            assert abs(c["expected"] - c["actual"]) < 0.02

    # The best game of six seasons: Lakers 145, Warriors 144 in double overtime (2024-01-27).
    d = client.get("/best-games", params={"limit": 25}).json()
    _assert_has_source(d)
    assert d["total"] == 7222 and d["results"][0]["date"] == "2024-01-27"
    top = d["results"][0]
    assert (top["home"], top["away"], top["pts_home"], top["pts_away"], top["overtimes"]) == ("GSW", "LAL", 144, 145, 2)
    assert top["peak"] and top["peak"]["event_id"] > 0 and "Curry" in top["peak"]["description"]
    xs = [g["excitement"] for g in d["results"]]
    assert xs == sorted(xs, reverse=True)
    # Kings 176, Clippers 175 (2OT, 2023-02-24) is in the top 25.
    assert any(g["date"] == "2023-02-24" and g["home"] == "LAC" for g in d["results"])
    # Comebacks: the Clippers' 35 down at Washington (2022-01-25) is the biggest in the six seasons.
    c = client.get("/best-games", params={"sort": "comeback", "limit": 3}).json()["results"]
    assert (c[0]["date"], c[0]["winner"], c[0]["comeback"]) == ("2022-01-25", "LAC", 35) and c[0]["win_min_wp"] < 0.01
    assert {"2024-03-25", "2022-01-25"} <= {g["date"] for g in client.get("/best-games", params={"sort": "comeback", "limit": 12}).json()["results"]}
    # No 30-point game is anywhere near the top, and the closest finishes are one-point games.
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT MIN(rk) FROM (SELECT final_margin, RANK() OVER (ORDER BY excitement DESC) rk
                       FROM best_games WHERE score_ok) t WHERE final_margin >= 30""")
        assert cur.fetchone()[0] > 1000
    assert all(g["final_margin"] == 1 for g in client.get("/best-games", params={"sort": "close", "limit": 10}).json()["results"])
    ot = client.get("/best-games", params={"ot": "true", "limit": 100}).json()
    assert ot["total"] == 366   # all 366 overtime games are ranked (the 7 unreconciled games went 48 minutes)
    assert all(g["overtimes"] >= 1 for g in ot["results"])
    lal = client.get("/best-games", params={"team": "LAL", "season": 2024, "limit": 100}).json()
    assert lal["total"] == 82 and all("LAL" in (g["home"], g["away"]) and g["season"] == 2024 for g in lal["results"])
    assert client.get("/best-games", params={"sort": "nope"}).status_code == 400
    assert client.get("/best-games", params={"team": "XXX"}).status_code == 400
    assert client.get("/best-games", params={"season": 2019}).status_code == 404
    assert client.get("/best-games", params={"limit": 1000}).status_code == 422

    # Upsets: lowest pre-game chance first; the Kings at Golden State without Curry (2017-11-27) is the biggest.
    u = client.get("/upsets", params={"limit": 25}).json()
    _assert_has_source(u)
    assert u["games"] == 19118 and 0.33 < u["upset_rate"] < 0.35
    assert (u["results"][0]["date"], u["results"][0]["winner"]) == ("2017-11-27", "SAC")
    ps = [r["winner_chance"] for r in u["results"]]
    assert ps == sorted(ps) and ps[0] < 0.06 and all(p < 0.5 for p in ps)
    assert all((r["replay_id"] is None) == (r["season"] < 2021) for r in u["results"])
    # Franchise codes: the Nets' games as NJN (to 2011-12) and BKN are one team; the 2015-16 Warriors' losses as
    # favourites include the Bucks ending 24-0 (2015-12-12) and the Lakers (2016-03-06).
    assert client.get("/upsets", params={"team": "NJN"}).json()["total"] == client.get("/upsets", params={"team": "BKN"}).json()["total"]
    g = client.get("/upsets", params={"team": "GSW", "side": "lost", "season": 2016, "limit": 50}).json()
    dates = {r["date"] for r in g["results"]}
    assert {"2015-12-12", "2016-03-06"} <= dates and all(r["loser"] == "GSW" and r["winner_chance"] < 0.5 for r in g["results"])
    assert g["games"] == 82 and g["total"] == len(g["results"]) and g["total"] < 15
    assert client.get("/upsets", params={"side": "won"}).status_code == 400
    assert client.get("/upsets", params={"season": 2010}).status_code == 404
    late = client.get("/upsets", params={"min_games": 20, "limit": 100}).json()
    assert all(min(r["games_played"]) >= 20 for r in late["results"]) and late["total"] < u["total"]


def test_shot_quality_map_cells_reconcile_and_known_shooters():
    """Shot quality map (scripts/build_shot_making.py, api/shot_hex.py, api/routers/shot_quality_map.py): every
    qualified player-season's hexagon cells plus its off-the-map shots equal its player_shot_making attempts and
    makes, the cells' expected makes equal the model's, and the famous shooting spots show where they should."""
    import numpy as np
    from impact_api import app
    from impact_core import get_db
    import shot_hex as H
    client = TestClient(app)
    # The grid: every cell of the half court gets an id that fits a smallint and a centre within one radius.
    xs, ys = np.meshgrid(np.arange(-250, 251, 5), np.arange(-52, 471, 5))
    ids = H.cell_id(xs.ravel(), ys.ravel())
    assert ids.min() >= 0 and ids.max() < 32767
    cx, cy = H.center(ids)
    assert np.hypot(cx - xs.ravel(), cy - ys.ravel()).max() <= H.SIZE + 1e-6
    assert H.cell_id(0, 0) == H.cell_id(3, -4) and tuple(H.center(H.cell_id(0, 0))) == (0.0, 0.0)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('player_shot_hex')")
        if cur.fetchone()[0] is None:
            pytest.skip("player_shot_hex not built (run scripts/build_shot_making.py)")
        cur.execute("SELECT COUNT(*) FROM player_shot_hex")
        assert cur.fetchone()[0] == 9026
        cur.execute("SELECT COUNT(*) FROM player_shot_making WHERE qualified")
        assert cur.fetchone()[0] == 9026
        # Cells + off-map = attempts and makes; expected makes agree with the model's expected FG% (a real column).
        cur.execute("""SELECT COUNT(*) FROM player_shot_hex h JOIN player_shot_making m USING (player_id, season)
                       WHERE (SELECT COALESCE(SUM(v), 0) FROM unnest(h.fga) v) + h.off_fga <> m.fga
                          OR (SELECT COALESCE(SUM(v), 0) FROM unnest(h.fgm) v) + h.off_fgm <> m.fgm
                          OR ABS((SELECT COALESCE(SUM(v), 0) FROM unnest(h.xm) v) + h.off_xm - m.x_fg_pct * m.fga) > 0.05 + 1e-4 * m.fga""")
        assert cur.fetchone()[0] == 0
        cur.execute("""SELECT MIN(cardinality(cells)), MAX(cardinality(cells)) FROM player_shot_hex
                       WHERE cardinality(cells) > 0""")
        lo, hi = cur.fetchone()
        assert lo >= 5 and hi < 800     # the half court has 765 cells
        # A full location era maps nearly every shot; the unlocated seasons say so.
        cur.execute("""SELECT l.season, SUM(l.fga)::float / MAX(s.fga) FROM shot_hex_league l
                       JOIN shot_making_league s USING (season) GROUP BY l.season ORDER BY 1""")
        share = dict(cur.fetchall())
        assert all(share[s] > 0.995 for s in range(2011, 2027))
        assert all(0.6 < share[s] < 0.8 for s in range(1997, 2011))

    o = client.get("/shots/quality-map/options", params={"player": "Kyle Korver"}).json()
    _assert_has_source(o)
    assert o["min_fga"] == 200 and 2015 in {s["season"] for s in o["seasons"]}

    def region(cells, pred):
        rows = [c for c in cells if pred(c["x"], c["y"])]
        fga, fgm = sum(c["fga"] for c in rows), sum(c["fgm"] for c in rows)
        return fga, fgm / fga, sum(c["xm"] for c in rows) / fga
    dist = lambda x, y: (x * x + y * y) ** 0.5  # noqa: E731

    # Curry 2015-16 from 26-30 feet: well above an average shooter on the same shots (as Shot-making says).
    m = client.get("/shots/quality-map", params={"player": "Stephen Curry", "season": 2016}).json()
    _assert_has_source(m)
    assert m["totals"]["fga"] + m["off_map"]["fga"] == 1598 and m["team"] == "GSW"
    fga, fg, exp = region(m["cells"], lambda x, y: 26 <= dist(x, y) < 30)
    assert fga > 250 and fg > 0.40 and fg - exp > 0.07
    # Korver 2014-15 in the corners: very good from the spot every league corner cell is heavily shot.
    k = client.get("/shots/quality-map", params={"player": "Kyle Korver", "season": 2015}).json()
    fga, fg, exp = region(k["cells"], lambda x, y: abs(x) > 21 and y < 9)
    assert fga > 80 and fg > 0.50 and fg - exp > 0.10
    # Gobert 2024-25: at the rim he makes more than the league does there, and shoots almost nothing away from it.
    g = client.get("/shots/quality-map", params={"player": "Rudy Gobert", "season": 2025}).json()
    fga, fg, exp = region(g["cells"], lambda x, y: dist(x, y) <= 4)
    assert fga > 350 and fg > 0.70
    assert region(g["cells"], lambda x, y: dist(x, y) > 10)[0] < 0.1 * g["totals"]["fga"]
    # Before 2010-11 the unlocated shots are off the map, and the response says so.
    s = client.get("/shots/quality-map", params={"player": "Shaquille O'Neal", "season": 2001}).json()
    assert s["off_map"]["fga"] > 300 and "no location" in s["off_map"]["reason"] and s["totals"]["fga"] + s["off_map"]["fga"] > 1300
    # Defaults and errors.
    assert client.get("/shots/quality-map", params={"player": "Stephen Curry"}).json()["season"] == 2026
    assert client.get("/shots/quality-map", params={"player": "Stephen Curry", "season": 2000}).status_code == 404
    assert client.get("/shots/quality-map", params={"player": "zzzzzz"}).status_code == 404
    assert client.get("/shots/quality-map", params={"player": "x"}).status_code == 422


def test_coaching_decisions():
    """Coaching Decisions (scripts/build_coaching_decisions.py, api/routers/coaching.py, round 6 step 5): every
    stored league effect recomputes from the stored decision points with the shared estimator; decision points sit
    in their windows; the families' summaries agree with their rows; the endpoints answer for every scope."""
    import numpy as np
    import pandas as pd
    from coaching_lib import DECISIONS, att
    from impact_api import app
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        rows = pd.read_sql_query("SELECT * FROM coaching_decisions", conn)
        tests = pd.read_sql_query("SELECT * FROM coaching_decision_tests", conn)
        summary = pd.read_sql_query("SELECT * FROM coaching_decision_summary", conn)
    finally:
        conn.close()
    assert sorted(rows.decision.unique()) == sorted(DECISIONS) and sorted(rows.season.unique()) == list(range(2021, 2027))
    n = rows.decision.value_counts()
    assert n["timeout"] > 9000 and n["challenge"] > 6500 and n["foul_up3"] > 500 and n["twoforone"] > 20000
    # Windows: runs with 2+ minutes left; 2-for-1 starts 28-40 s from the end of quarters 1-3; up 3 with <= 24 s in the 4th/OT.
    t, c, f, w = (rows[rows.decision == k] for k in DECISIONS)
    assert (t.sec_left >= 120).all() and (t["size"] >= 8).all() and (t.margin < 0).mean() > 0.5
    assert w.period.max() <= 3 and w.sec_left.between(28, 40, inclusive="left").all()
    assert (f.period >= 4).all() and (f.margin == 3).all() and f.sec_left.between(0, 24, inclusive="right").all()
    assert f.game_id.is_unique                                      # the first up-3 situation of a game only
    # Challenges: unknown outcome <=> no treatment flag; most challenges are won (2023-24 on: a second challenge after a win).
    assert ((c.detail == "unknown") == c.treated.isna()).all()
    assert 0.5 < c.treated.dropna().astype(float).mean() < 0.7
    # Every stored league / season / secondary effect is the shared estimator on the stored rows.
    col_of = {"coaching:league": "outcome"}
    for r in tests[tests.level.isin(["league", "season"]) & ~tests.family.str.startswith("sensitivity")].itertuples():
        dec = r.key if r.family in ("coaching:league",) or r.family.startswith("season:") else r.family.split(":")[1]
        col = col_of.get(r.family, "outcome" if r.family.startswith("season:") else r.key)
        d = rows[(rows.decision == dec) & rows.treated.notna() & rows[col].notna()]
        if r.season:
            d = d[d.season == r.season]
        got = att(d[col].to_numpy(float), d.treated.astype(bool).to_numpy(), pd.factorize(d.stratum)[0])
        assert abs(got[0] - r.stat) < 1e-9, (r.family, r.key, r.season)
        assert r.ci_lo <= r.stat <= r.ci_hi and 0 < r.p <= 1
    # Families: k of n agrees with the rows; the four league beliefs are one family.
    for s in summary.itertuples():
        fam = tests[tests.family == s.family]
        assert len(fam) == s.units and int(fam.survives.sum()) == s.survivors and int((fam.p < 0.05).sum()) == s.p05
    assert summary.set_index("family").loc["coaching:league", "units"] == 4
    assert all(summary.set_index("family").loc[f"{k}:team", "units"] == 30 for k in ("timeout", "twoforone", "challenge"))
    # The results the page and README state (all six seasons): shooting early pays; a won challenge beats a lost one;
    # no positive timeout effect; fouling up 3 can't be told from chance.
    lg = tests[tests.family == "coaching:league"].set_index("key")
    assert lg.loc["twoforone", "ci_lo"] > 0 and lg.loc["twoforone", "survives"]
    assert lg.loc["challenge", "ci_lo"] > 0 and lg.loc["challenge", "survives"]
    assert lg.loc["timeout", "stat"] < 0.1 and not lg.loc["timeout", "survives"]
    assert lg.loc["foul_up3", "p"] > 0.05

    client = TestClient(app)
    o = client.get("/coaching/options").json()
    _assert_has_source(o)
    assert [x["key"] for x in o["decisions"]] == list(DECISIONS) and len(o["teams"]) == 30 and set(o["league"]) == set(DECISIONS)
    for k in DECISIONS:
        for params in ({}, {"team": "BOS"}, {"season": 2025}, {"team": "BOS", "season": 2025}):
            d = client.get(f"/coaching/decision/{k}", params=params).json()
            _assert_has_source(d)
            assert d["n"] > 0 and d["breakdowns"] and len(d["teams"]) == 30 and len(d["trend"]) == 6
        assert d["scope_test"] is None                              # a team-season has no stored test
    lg_d = client.get("/coaching/decision/timeout").json()
    assert abs(lg_d["scope_test"]["stat"] - lg_d["live"]["stat"]) < 1e-6
    # A team's re-read effect is matched within the team strata, like its stored test.
    bos = client.get("/coaching/decision/twoforone", params={"team": "BOS"}).json()
    assert abs(bos["scope_test"]["stat"] - bos["live"]["stat"]) < 1e-6
    ch = client.get("/coaching/decision/challenge").json()["breakdowns"]
    assert sum(r["won"] for r in ch["by_season"]) == int((c.treated == True).sum())  # noqa: E712
    assert len(client.get("/coaching/tests").json()["league"]) == len(tests[tests.level == "league"])
    assert client.get("/coaching/decision/nope").status_code == 404
    assert client.get("/coaching/decision/timeout", params={"team": "XXX"}).status_code == 404
    assert client.get("/coaching/decision/timeout", params={"season": 2019}).status_code == 404
    assert np.isfinite(o["counts"]["crossings"])
