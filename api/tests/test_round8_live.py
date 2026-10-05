"""
test_round8_live.py
====================
Round 8 step 4 (docs/qa/ROUND8_ISSUES.md): the live pages no longer depend on stats.nba.com.
Live Scores, the box score and the standings read ESPN's public API with a 3 s timeout, stored
results come first, and every route says which season and source it shows. With/Without a Star
reads stored data from 2020-21, Pair Synergy's observed pair comes from pair_seasons, the live
player search is gone, and a page view can no longer write player_shots.

Most checks run against the local database with the outside call replaced (monkeypatched), so
they pass offline. The few that read ESPN for real are skipped when ESPN isn't reachable within
3 s, never faked. Outside facts carry their URL and read date.

Usage:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_round8_live.py
"""

import inspect
import os
import sys
import time

import psycopg2
import pytest
from fastapi.testclient import TestClient

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND = os.path.join(os.path.dirname(_API_DIR), "frontend", "src")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Local Postgres DB is not reachable.")


def _espn_reachable() -> bool:
    import espn_live
    espn_live.clear_cache()
    return espn_live.scoreboard("2025-01-02") is not None


needs_espn = pytest.mark.skipif(not _espn_reachable(), reason="ESPN's site API didn't answer within 3 s.")


def _read(rel):
    with open(os.path.join(_FRONTEND, rel), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def client():
    from impact_api import app
    return TestClient(app)


@pytest.fixture
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        yield conn.cursor()
    finally:
        conn.close()


@pytest.fixture
def espn_down(monkeypatch):
    """ESPN unreachable: every reader answers None, at once."""
    import espn_live
    espn_live.clear_cache()
    monkeypatch.setattr(espn_live, "scoreboard", lambda date_str: None)
    monkeypatch.setattr(espn_live, "boxscore", lambda espn_id: None)
    monkeypatch.setattr(espn_live, "standings", lambda season: None)
    yield
    espn_live.clear_cache()


# ─── Timeouts and dead code ───────────────────────────────────────────────────

def test_every_live_call_fails_within_three_seconds():
    """The plan's rule: a clear state within 3 s instead of a hang (R8-008)."""
    import espn_live
    import impact_core
    assert espn_live.TIMEOUT_SECONDS <= 3
    assert impact_core._LIVE_REQUEST_TIMEOUT_SECONDS <= 3
    src = inspect.getsource(impact_core)
    # Every nba_api endpoint built in impact_core uses the shared timeout, none a literal 30/45 s.
    assert "timeout=30" not in src and "timeout=45" not in src


def test_dead_fallbacks_and_live_search_are_gone():
    """R8-003: cdn.nba.com's liveData JSON has been 403 since at least 2026-10-05; balldontlie needed a
    key nobody set; the live player search and profile asked stats.nba.com for names the rest of the
    app couldn't resolve anyway (R8-017)."""
    import impact_core
    import routers.pair_synergy
    import routers.players_search_profile
    import routers.with_without_star
    code = [line for line in inspect.getsource(impact_core).splitlines() if not line.strip().startswith("#")]
    src = "\n".join(code)
    assert "liveData" not in src and "balldontlie" not in src.lower()
    assert "scoreboardv2" not in src and "boxscoretraditionalv2" not in src
    assert "nba_api" not in inspect.getsource(routers.players_search_profile)
    assert "nba_api" not in inspect.getsource(routers.pair_synergy)
    assert "_fetch_lineup_stats_season" not in inspect.getsource(routers.pair_synergy)


# ─── /games/by-date and /games/boxscore ───────────────────────────────────────

@needs_db
def test_by_date_reads_stored_results_first(client, cur, espn_down):
    """A stored date needs no network: the regular season from game_scores (R8-031), the play-in and
    playoffs from postseason_games, scores equal to the tables, ESPN ids as game ids."""
    r = client.get("/games/by-date", params={"date": "2025-01-02"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "stored" and body["status"] == "ok" and body["_source"]["live"] is False
    games = body["games"]
    cur.execute("SELECT COUNT(DISTINCT game_id) FROM game_scores WHERE game_date = '2025-01-02';")
    assert len(games) == cur.fetchone()[0] == 6
    cur.execute(
        """SELECT game_id, espn_id, team_abbreviation, pts_for FROM game_scores
           WHERE game_date = '2025-01-02' AND is_home;"""
    )
    home = {espn: (gid, team, pts) for gid, espn, team, pts in cur.fetchall()}
    for g in games:
        assert g["status"] == "FINAL" and g["kind"] == "Regular season"
        gid, team, pts = home[g["id"]]
        assert g["nba_game_id"] == gid and g["home"]["abbr"] == team and g["home"]["score"] == pts
        assert g["home"]["winner"] == (g["home"]["score"] > g["away"]["score"])
        assert "rest" in g["home"]  # team_game_fatigue rest tags still attached

    # 2025 Finals game 1 (2025-06-05, OKC 110-111 IND; test_known_facts pins the series): playoffs, not play-in.
    finals = client.get("/games/by-date", params={"date": "2025-06-05"}).json()
    assert finals["source"] == "stored" and [g["kind"] for g in finals["games"]] == ["Playoffs"]
    assert finals["games"][0]["note"] == "NBA Finals - Game 1"
    cur.execute("SELECT game_date FROM postseason_games WHERE stage = 'play-in' ORDER BY game_date DESC LIMIT 1;")
    play_in = client.get("/games/by-date", params={"date": cur.fetchone()[0].isoformat()}).json()
    assert play_in["games"] and all(g["kind"] == "Play-in" for g in play_in["games"])


@needs_db
def test_by_date_says_when_espn_is_unreachable(client, espn_down):
    """R8-002/R8-003: a date that isn't stored and can't be fetched used to look like a day without
    games ("No games found")."""
    t = time.time()
    body = client.get("/games/by-date", params={"date": "2031-01-15"}).json()
    assert time.time() - t < 2
    assert body["games"] == [] and body["source"] == "none" and body["status"] == "unreachable"
    assert "ESPN" in body["message"] and "3 s" in body["message"]
    assert client.get("/games/by-date", params={"date": "yesterday"}).json()["status"] == "bad_date"


@needs_db
@needs_espn
def test_by_date_lists_opening_night_from_espn(client, cur):
    """R8-002: the 2026-27 opener (2026-10-20) is on ESPN's schedule (and in the locked
    ledger_schedule, read-only here) but stats.nba.com's scoreboard had nothing for it."""
    t = time.time()
    body = client.get("/games/by-date", params={"date": "2026-10-20"}).json()
    assert time.time() - t < 4
    assert body["source"] == "espn" and body["status"] == "ok" and body["_source"]["live"] is True
    cur.execute("SELECT home, away FROM ledger_schedule WHERE game_date = '2026-10-20';")
    expected = {(h, a) for h, a in cur.fetchall()}
    assert {(g["home"]["abbr"], g["away"]["abbr"]) for g in body["games"]} == expected and len(expected) == 3
    for g in body["games"]:
        assert g["status"] == "SCHEDULED" and g["status_text"].endswith("ET") and g["kind"] == "Regular season"
        assert g["home"]["score"] is None and g["away"]["score"] is None
    today = client.get("/games/by-date").json()
    assert today["status"] in ("ok", "unreachable") and today["date"] >= "2026-10-05"


@needs_db
@needs_espn
def test_boxscore_takes_both_ids_and_has_no_nan(client):
    """R8-005: plus-minus was the text "nan" for every player who didn't play. An NBA id is mapped to
    ESPN's through game_scores; the ESPN id works directly. LAL 110-103 MIN on 2024-10-22: LeBron
    James 16 points, 35 minutes (https://www.espn.com/nba/boxscore/_/gameId/401704628, read 2026-10-05)."""
    by_nba = client.get("/games/boxscore/0022400062").json()
    assert by_nba["status"] == "ok" and by_nba["espn_id"] == "401704628" and by_nba["game_status"] == "FINAL"
    by_espn = client.get("/games/boxscore/401704628").json()
    assert by_espn["boxscore"] == by_nba["boxscore"]
    rows = by_nba["boxscore"]["home"] + by_nba["boxscore"]["away"]
    assert by_nba["teams"]["home"]["abbr"] == "LAL" and by_nba["teams"]["away"]["abbr"] == "MIN"
    assert all(r["pm"] is None or isinstance(r["pm"], int) for r in rows)
    assert "nan" not in str(by_nba).lower().replace("financial", "")
    dnp = [r for r in rows if r["min"] is None]
    assert dnp and all(r["pm"] is None and r["dnp_reason"] for r in dnp)
    lebron = next(r for r in by_nba["boxscore"]["home"] if r["name"] == "LeBron James")
    assert lebron["pts"] == 16 and lebron["min"] == "35" and lebron["starter"] is True


@needs_db
def test_boxscore_unknown_and_unreachable_states(client, espn_down):
    unknown = client.get("/games/boxscore/0029900001").json()
    assert unknown["status"] == "unknown_game" and unknown["boxscore"] == {"away": [], "home": []}
    down = client.get("/games/boxscore/401704628").json()
    assert down["status"] == "unreachable" and "ESPN" in down["message"]


# ─── /meta/current ────────────────────────────────────────────────────────────

@needs_db
def test_meta_current_labels_every_block_with_its_season(client):
    """R8-004: the dashboard mixed 2026-27 standings with 2025-26 numbers under one label."""
    body = client.get("/meta/current").json()
    assert body["stored_season"] <= body["season"]
    assert body["standings_source"] in ("espn", "stored") and body["standings_season"] is not None
    assert body["team_stats_source"] in ("nba_api", "local_db") and body["top_scorer_source"] in ("nba_api", "local_db")
    if body["team_stats_source"] == "local_db":
        assert body["team_stats_season"] == body["stored_season"]
    if body["top_scorer_source"] == "local_db":
        assert body["top_scorer_season"] == body["stored_season"]
    assert isinstance(body["standings_played"], bool)
    assert len(body["standings"]["eastern"]) == 15 and len(body["standings"]["western"]) == 15
    assert body["_source"]["upstream_api"]


@needs_db
def test_meta_current_standings_fall_back_to_the_stored_record(client, cur, espn_down):
    """Without ESPN the standings are the latest stored season's real record (team_seasons), named."""
    body = client.get("/meta/current").json()
    assert body["standings_source"] == "stored"
    cur.execute("SELECT MAX(season) FROM team_seasons WHERE NOT is_league_avg AND w IS NOT NULL;")
    season = cur.fetchone()[0]
    assert body["standings_season"] == season and body["standings_played"] is True
    cur.execute(
        "SELECT abbreviation, w, l FROM team_seasons WHERE season = %s AND NOT is_league_avg AND w IS NOT NULL;",
        (season,),
    )
    stored = {a: (int(w), int(l)) for a, w, l in cur.fetchall()}
    rows = body["standings"]["eastern"] + body["standings"]["western"]
    assert {r["abbr"]: (r["w"], r["l"]) for r in rows} == stored
    assert [r["rank"] for r in body["standings"]["eastern"]] == list(range(1, 16))
    assert body["standings"]["eastern"][0]["gb"] == "-"


@needs_db
@needs_espn
def test_espn_standings_are_regular_season_only():
    """ESPN's default standings count preseason games before opening night (checked 2026-10-05:
    TOR 0-1 on 2026-10-05, a preseason loss); the regular-season type is asked for explicitly."""
    import espn_live
    espn_live.clear_cache()
    st = espn_live.standings(2027)
    assert st is not None and not espn_live.played(st)
    last = espn_live.standings(2026)
    assert espn_live.played(last)
    rows = {r["abbr"]: r for r in last["eastern"] + last["western"]}
    assert len(rows) == 30 and all(r["w"] + r["l"] == 82 for r in rows.values())
    assert {"GSW", "NOP", "NYK", "SAS", "UTA", "WAS"} <= set(rows)  # ESPN's codes mapped to the NBA's


# ─── /leaders ─────────────────────────────────────────────────────────────────

@needs_db
def test_leaders_default_falls_back_to_the_latest_stored_season(client, cur, monkeypatch):
    """R8-001: between seasons the page showed an empty "Top 10 · 2027"."""
    import routers.leaders as leaders

    def no_live(*a, **k):
        return None
    monkeypatch.setattr(leaders, "fetch_nba_api_player_leaders", no_live)
    body = client.get("/leaders/pts").json()
    cur.execute("SELECT MAX(season) FROM player_season_stats;")
    latest = cur.fetchone()[0]
    assert body["season"] == latest and body["requested_season"] >= latest
    assert body["results"], "the fallback must show the latest stored leaders"
    if body["fallback"]:
        assert body["note"] and f"{latest - 1}-{str(latest)[-2:]}" in body["note"]
    cur.execute(
        "SELECT player_name, pts FROM player_season_stats WHERE season = %s AND team_abbreviation <> 'TOT' "
        "ORDER BY pts DESC LIMIT 1;", (latest,),
    )
    name, pts = cur.fetchone()
    assert body["results"][0]["player_name"] == name and body["results"][0]["value"] == round(float(pts), 2)
    assert body["_source"]["tables"] == ["player_season_stats"]

    # A stored season never goes to the network.
    def boom(*a, **k):
        raise AssertionError("live call for a stored season")
    monkeypatch.setattr(leaders, "fetch_nba_api_player_leaders", boom)
    stored = client.get("/leaders/reb", params={"season": latest - 1, "top_n": 3}).json()
    assert stored["season"] == latest - 1 and stored["fallback"] is False and len(stored["results"]) == 3
    # An explicit future season with nothing live is a 404 that names the latest stored season.
    monkeypatch.setattr(leaders, "fetch_nba_api_player_leaders", no_live)
    r = client.get("/leaders/pts", params={"season": latest + 5})
    assert r.status_code == 404 and f"{latest - 1}-{str(latest)[-2:]}" in r.json()["detail"]


# ─── /players/search ──────────────────────────────────────────────────────────

@needs_db
def test_player_search_is_stored_and_accent_insensitive(client):
    body = client.get("/players/search", params={"q": "jokic"}).json()
    assert "Nikola Jokić" in body["results"]
    assert client.get("/players/search", params={"q": "curry", "limit": 3}).json()["results"][:1] != []
    assert client.get("/players/search", params={"q": "j"}).json()["results"] == []


# ─── With/Without a Star ──────────────────────────────────────────────────────

@needs_db
def test_with_without_reads_stored_data_from_2020_21(client, cur, monkeypatch):
    """R8-021 (and R8-008): the 2023-24 76ers with and without Embiid, 31-8 and 16-27 (79.5% / 37.2%,
    README), now from game_scores + player_game_lines; the average margin is the real final margin."""
    import routers.with_without_star as ww

    def boom(*a, **k):
        raise AssertionError("live call for a stored season")
    monkeypatch.setattr(ww, "_fetch_team_game_log", boom)
    body = client.get("/teams/with-without/PHI/2024", params={"player_name": "Joel Embiid"}).json()
    assert body["source"] == "stored" and body["games"] == 82
    assert (body["with_player"]["wins"], body["with_player"]["losses"]) == (31, 8)
    assert (body["without_player"]["wins"], body["without_player"]["losses"]) == (16, 27)
    assert body["with_player"]["win_pct"] == 0.795 and body["without_player"]["win_pct"] == 0.372
    cur.execute(
        """SELECT ROUND(AVG(pts_for - pts_against)::numeric, 2) FROM game_scores g
           WHERE season = 2024 AND team_abbreviation = 'PHI' AND EXISTS (
               SELECT 1 FROM player_game_lines l WHERE l.season = 2024 AND l.player_id = 203954
                 AND l.team_abbreviation = 'PHI' AND l.game_date = g.game_date AND l.seconds > 0);"""
    )
    assert body["with_player"]["avg_point_diff"] == float(cur.fetchone()[0])
    assert body["_source"]["tables"] == ["game_scores", "player_game_lines"] and body["_source"]["live"] is False
    # The id, when the page passes it, wins over the name.
    by_id = client.get("/teams/with-without/PHI/2024", params={"player_name": "x", "player_id": 203954}).json()
    assert by_id["player_name"] == "Joel Embiid" and by_id["with_player"] == body["with_player"]


@needs_db
def test_with_without_before_2020_21_says_when_the_source_is_unreachable(client, monkeypatch):
    import routers.with_without_star as ww
    from fastapi import HTTPException
    from impact_core import LIVE_GAME_LOG_UNAVAILABLE

    def down(*a, **k):
        raise HTTPException(status_code=503, detail=LIVE_GAME_LOG_UNAVAILABLE)
    monkeypatch.setattr(ww, "_fetch_team_game_log", down)
    r = client.get("/teams/with-without/GSW/2016", params={"player_name": "Stephen Curry"})
    assert r.status_code == 503 and "2020-21" in r.json()["detail"] and "3 s" in r.json()["detail"]


# ─── Pair Synergy ─────────────────────────────────────────────────────────────

@needs_db
def test_pair_synergy_observed_comes_from_pair_seasons(client, cur):
    body = client.get("/players/pair-synergy", params={"player_a": "Nikola Jokic", "player_b": "Jamal Murray"}).json()
    season = body["season"]
    cur.execute(
        """SELECT team_abbreviation, minutes, net_rating FROM pair_seasons
           WHERE season = %s AND player_a = 203999 AND player_b = 1627750 ORDER BY minutes DESC LIMIT 1;""",
        (season,),
    )
    team, minutes, net = cur.fetchone()
    assert body["observed_source"] == "pair_seasons"
    assert body["observed"]["team_abbreviation"] == team and body["observed"]["min"] == round(float(minutes), 1)
    assert body["observed"]["net_rating"] == round(float(net), 2)
    assert "pair_seasons" in body["_source"]["tables"]
    earlier = client.get("/players/pair-synergy", params={"player_a": "Nikola Jokic", "player_b": "Jamal Murray", "season": 2020}).json()
    assert earlier["observed"] is None and earlier["observed_source"] == "none" and "2020-21" in earlier["methodology"]


# ─── The routes that still need stats.nba.com say so ──────────────────────────

@needs_db
def test_playoff_comparison_distinguishes_unreachable_from_missed_playoffs(client, monkeypatch):
    import routers.playoff_forecaster as pf
    monkeypatch.setattr(pf, "_fetch_playoff_stats_season", lambda season: None)
    r = client.get("/players/playoff-comparison/Nikola Jokic", params={"season": 2025})
    assert r.status_code == 503 and "stats.nba.com" in r.json()["detail"]
    monkeypatch.setattr(pf, "_fetch_playoff_stats_season", lambda season: {})
    r = client.get("/players/playoff-comparison/Nikola Jokic", params={"season": 2025})
    assert r.status_code == 200 and r.json()["playoffs"] is None and "did not make" in r.json()["note"]


@needs_db
def test_heliocentricity_unreachable_is_a_503_with_the_reason(client, monkeypatch):
    import routers.heliocentricity as h
    monkeypatch.setattr(h, "_fetch_pt_possession_stats", lambda season: None)
    r = client.get("/players/heliocentricity")
    assert r.status_code == 503 and "stats.nba.com" in r.json()["detail"]


# ─── Shot charts never write on a page view ───────────────────────────────────

@needs_db
def test_live_shot_fetch_is_off_by_default(client, cur):
    """R8-007: a GET for an uncached player used to fetch his career into player_shots (a table the
    paper manifest hashes); the first `/shots/league-zones/2026` inserted 5 rows during step 2c."""
    import shots_lib
    if os.getenv("ENABLE_LIVE_SHOT_FETCH", "").lower() in ("true", "1", "yes"):
        pytest.skip("ENABLE_LIVE_SHOT_FETCH is on in this environment")
    assert shots_lib.LIVE_FETCH_ENABLED is False
    cur.execute("SELECT COUNT(*) FROM league_shot_zones;")
    zones_before = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM player_shots_cache_status;")
    cache_before = cur.fetchone()[0]
    r = client.get("/shots/league-zones/2031")
    assert r.status_code == 404 and "ENABLE_LIVE_SHOT_FETCH" in r.json()["detail"]
    cur.execute(
        """SELECT p.player_name FROM player_season_stats p
           WHERE p.season = (SELECT MAX(season) FROM player_season_stats)
             AND NOT EXISTS (SELECT 1 FROM player_shots_cache_status c WHERE c.player_id = p.player_id)
             AND NOT EXISTS (SELECT 1 FROM player_season_stats q WHERE q.player_name = p.player_name AND q.player_id <> p.player_id)
           ORDER BY p.min DESC LIMIT 1;"""
    )
    row = cur.fetchone()
    if row:
        r = client.get(f"/shots/player/{row[0]}")
        assert r.status_code == 404 and "ENABLE_LIVE_SHOT_FETCH" in r.json()["detail"]
    cur.execute("SELECT COUNT(*) FROM league_shot_zones;")
    assert cur.fetchone()[0] == zones_before
    cur.execute("SELECT COUNT(*) FROM player_shots_cache_status;")
    assert cur.fetchone()[0] == cache_before


# ─── Frontend ─────────────────────────────────────────────────────────────────

def test_pages_use_nba_dates_and_the_routes_season_labels():
    date_js = _read("utils/date.js")
    assert "America/New_York" in date_js and "export function nbaDateIso" in date_js
    scores = _read("components/pages/LiveScores.jsx")
    assert "nbaDateIso()" in scores and "feed.message" in scores and "game.statusText" in scores
    assert "<th>+/-</th>" in scores and "dnp_reason" in scores
    api = _read("services/api.js")
    assert "fetchGamesByDate(nbaDateIso())" in api and "const d = date || nbaDateIso();" in api
    dash = _read("components/pages/DashboardHome.jsx")
    assert "m.stored_season ?? m.season" in dash and "meta.top_scorer_season" in dash and "fetchGamesByDate(nbaDateIso())" in dash
    assert "meta?.stored_season" in _read("components/pages/LandingPage.jsx")
    standings = _read("components/pages/StandingsSection.jsx")
    assert "meta.standings_season" in standings and "meta.standings_source" in standings
    assert "team_stats_season" in _read("components/pages/TeamComparison.jsx")
    leaders = _read("components/pages/StatLeaders.jsx")
    assert "data?.note" in leaders and "SourceBadge" in leaders
    ww = _read("components/WithWithoutStarSection.jsx")
    assert "picked?.player_id" in ww and "live-fetched from the NBA's own real per-game data" not in ww
    assert "unreachable from the build machine since 2026-09-26, so" not in _read("components/pages/methodologyContent.js")
