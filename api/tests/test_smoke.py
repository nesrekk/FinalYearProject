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
    assert sum(p["minutes"] for p in mb) <= mb[0]["season_minutes_all_teams"] + 1

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
