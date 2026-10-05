"""
Round 8 step 7: the smaller open issues. Pins each fix:

R8-029  Hot Streak Checker: hot_streak_persistence stores the shuffled-null centre of every slope (2,000
        within-season shuffles, api/hot_streaks.py's SeasonMatrix, the same draws as the paper's
        hot-streak family) and net_share = slope - null; the card, the league list and the verdict show it.
R8-030  Garbage-Time Deflator on the corrected clock (pbp_event_clock).
R8-068  Stat Leaders (and its hustle stats, and the Dashboard's top scorer) rank only qualified players:
        the Leaderboard Builder's default floor (30+ games, 20+ minutes, attempts for percentages).
R8-073  lineup_seasons / pair_seasons carry the exact seconds; Pair Chemistry and the Workbench add those.
R8-009  2025-26's MVP, DPOY, ROY and All-NBA teams (Wikipedia, read 2026-10-06), and the Prediction Ledger
        says its 2025-26 rows were logged after the season.
"""

import os
import sys

import numpy as np
import psycopg2
import pytest
from fastapi.testclient import TestClient

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API_DIR)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, _API_DIR)

from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Local Postgres DB is not reachable.")


@pytest.fixture(scope="module")
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


def _exists(cur, table):
    cur.execute("SELECT to_regclass(%s)", (table,))
    return cur.fetchone()[0] is not None


# ─── R8-029 Hot Streak null centre ─────────────────────────────────────────

def test_hot_streak_null_centre_is_stored_and_equals_the_paper(cur):
    cur.execute("""SELECT stat, window_games, slope, null_slope, net_share, slope_lo, net_share_lo,
                          slope_season_only, null_slope_season_only, net_share_season_only, null_shuffles
                   FROM hot_streak_persistence""")
    rows = cur.fetchall()
    assert len(rows) == 42
    for stat, n, sl, null, net, lo, net_lo, sl0, null0, net0, shuffles in rows:
        assert shuffles == 2000
        assert net == pytest.approx(sl - null, abs=1e-12) and net_lo == pytest.approx(lo - null, abs=1e-12)
        assert net0 == pytest.approx(sl0 - null0, abs=1e-12)
        assert 0.05 < null < 0.9 and 0.15 < null0 < 0.55, (stat, n, null, null0)
    by = {(r[0], r[1]): r for r in rows}
    # Shooting runs carry on about nothing beyond the null centre; points and minutes over 5 games do.
    for stat in ("fg_pct", "fg3_pct", "ts_pct"):
        assert abs(by[(stat, 10)][4]) < 0.02
    assert by[("pts", 10)][4] > 0.15 and by[("min", 5)][4] > 0.25
    if _exists(cur, "paper_beliefs_summary"):
        # The same 2,000 shuffles as scripts/paper_beliefs.py's hot-streak family: equal, not just close.
        cur.execute("""SELECT h.stat, h.window_games, h.null_slope, b.agg_null_mean, h.null_slope_season_only,
                              b0.agg_null_mean
                       FROM hot_streak_persistence h
                       JOIN paper_beliefs_summary b ON b.key = 'streak:' || h.stat || ':' || h.window_games
                       JOIN paper_beliefs_summary b0 ON b0.key = 'streak0:' || h.stat || ':' || h.window_games""")
        got = cur.fetchall()
        assert len(got) == 42
        for stat, n, null, paper, null0, paper0 in got:
            assert null == paper and null0 == paper0, (stat, n)


def test_season_matrix_reproduces_the_stored_slopes(cur):
    import build_hot_streak_persistence as H
    import hot_streaks as HS
    conn = cur.connection
    lines, prior = H.load(conn)
    cur.execute("SELECT stat, window_games, prior_games, slope, slope_season_only FROM hot_streak_persistence")
    stored = {(s, n): (w, a, b) for s, n, w, a, b in cur.fetchall()}
    mat = HS.SeasonMatrix(HS.add_columns(lines), prior, {k: v[0] for k, v in stored.items()})
    rows = np.arange(mat.P)
    C = mat.cums(mat.ident, rows)
    for (stat, n), (_w, sl, sl0) in stored.items():
        okp, D, F, ok, D0, F0 = mat.windows(C, rows, stat, n)
        assert abs(mat.slope(D, F, okp) - sl) < 1e-9 and abs(mat.slope(D0, F0, ok) - sl0) < 1e-9


def test_hot_streak_routes_show_the_net_share(client):
    d = client.get("/games/hot-streak/1628983", params={"season": 2026, "stat": "fg3_pct", "window": 10}).json()
    if d.get("qualified"):
        per = d["persistence"]
        assert per["net_share"] == pytest.approx(per["share"] - per["null_share"], abs=0.0015)
        assert "shuffled" in d["verdict"] and "carries on" in d["verdict"]
    s = client.get("/games/hot-streaks", params={"season": 2026, "stat": "pts", "window": 10}).json()["summary"]
    assert s["net_share"] == pytest.approx(s["share_carries_on"] - s["null_share"], abs=1e-9)
    assert 0.1 < s["net_share"] < s["share_carries_on"]


# ─── R8-030 Garbage-Time Deflator on the corrected clock ───────────────────

def test_deflator_reads_the_corrected_clock(cur):
    import build_leverage_splits as B
    import inspect
    assert "pbp_event_clock" in inspect.getsource(B.load_events)
    # NBA clutch time (last 5 min of the 4th/OT, margin before the play within 5) counted on each clock in SQL;
    # the stored count must be the corrected clock's.
    sql = """
        WITH e AS (
            SELECT e.period, {secs} AS secs,
                   COALESCE(LAG(e.score_home - e.score_away) OVER w, 0) AS margin_before
            FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
            LEFT JOIN pbp_event_clock k ON k.event_id = e.id
            WHERE g.source = 'espn' AND g.season = 2026 AND g.game_id <> ALL(%s)
            WINDOW w AS (PARTITION BY e.game_id ORDER BY e.action_number, e.id))
        SELECT COUNT(*) FROM e WHERE period >= 4 AND secs <= 300 AND ABS(margin_before) <= 5"""
    finals = list(B.NBA_CUP_FINALS)
    cur.execute(sql.format(secs="COALESCE(k.seconds_remaining, e.seconds_remaining)"), (finals,))
    corrected = cur.fetchone()[0]
    cur.execute(sql.format(secs="e.seconds_remaining"), (finals,))
    espn = cur.fetchone()[0]
    cur.execute("SELECT clutch_events FROM leverage_validation WHERE season = 2026")
    assert cur.fetchone()[0] == corrected != espn


# ─── R8-068 Stat Leaders' floor ────────────────────────────────────────────

def test_stat_leaders_rank_only_qualified_players(client, cur):
    from routers.leaders import qualifying
    for key, att in (("fg3_pct", "fg3a"), ("ft_pct", "fta"), ("fg_pct", "fga"), ("stl", None), ("plus_minus", None)):
        d = client.get(f"/leaders/{key}", params={"season": 2026, "top_n": 10}).json()
        f = d["qualifying"]
        assert f == qualifying(key) and f["min_gp"] == 30 and f["min_mpg"] == 20 and f["attempts"] == att
        assert "Qualified" in f["text"]
        ids = [r["player_id"] for r in d["results"]]
        assert len(ids) == 10
        cur.execute(f"""SELECT player_id, gp, min, {att or 'NULL'} FROM player_season_stats
                        WHERE season = 2026 AND player_id = ANY(%s) AND team_abbreviation <> 'TOT'""", (ids,))
        for pid, gp, mpg, a in cur.fetchall():
            assert gp >= 30 and mpg >= 20 and (att is None or a >= f["min_attempts"]), (key, pid)
        # The same top 10 as the Leaderboard Builder's default view of that season.
        lb = client.get("/leaderboard/custom", params={"stat": key, "season_from": 2026, "season_to": 2026,
                                                       "top_n": 10}).json()
        assert [r["value"] for r in lb["results"]] == pytest.approx(
            [r["value"] / (100 if key.endswith("_pct") else 1) for r in d["results"]], abs=0.006)
    # 2025-26 3P% used to open on 1-for-1 shooters at 100%.
    top = client.get("/leaders/fg3_pct", params={"season": 2026, "top_n": 1}).json()["results"][0]
    assert top["value"] < 55
    hustle = client.get("/hustle/leaders", params={"stat": "charges_drawn", "season": 2026, "top_n": 5}).json()
    assert hustle["qualifying"]["min_gp"] == 30 and all(r["gp"] >= 30 for r in hustle["results"])


def test_live_leaders_apply_a_season_in_progress_floor(monkeypatch):
    import routers.leaders as L

    rows = [  # a season 10 games old: the floor is 7 games (70% of 10), 20 minutes, 2 3PA a game
        {"player_id": 1, "player_name": "Hot Hand", "team_abbr": "AAA", "value": 80.0, "gp": 2, "min": 25, "fg3a": 3},
        {"player_id": 2, "player_name": "Bench Arm", "team_abbr": "BBB", "value": 60.0, "gp": 10, "min": 12, "fg3a": 4},
        {"player_id": 3, "player_name": "Few Tries", "team_abbr": "CCC", "value": 55.0, "gp": 10, "min": 30, "fg3a": 1},
        {"player_id": 4, "player_name": "Real One", "team_abbr": "DDD", "value": 45.0, "gp": 9, "min": 32, "fg3a": 7},
        {"player_id": 5, "player_name": "Starter", "team_abbr": "EEE", "value": 40.0, "gp": 7, "min": 30, "fg3a": 5},
    ]
    monkeypatch.setattr(L, "fetch_nba_api_player_leaders",
                        lambda key, season, top_n=10: {"season": season, "stat_key": key, "stat_label": "3P%",
                                                       "results": rows})
    out = L.live_leaders("fg3_pct", 2027, 10)
    assert [r["player_name"] for r in out["results"]] == ["Real One", "Starter"]
    assert out["qualifying"]["min_gp"] == 7 and [r["rank"] for r in out["results"]] == [1, 2]


# ─── R8-073 exact lineup seconds ───────────────────────────────────────────

def test_lineup_and_pair_minutes_add_up_to_the_seconds(client, cur):
    for t in ("lineup_seasons", "pair_seasons"):
        # minutes = the exact seconds rounded once, half up (both sums are numeric now: same digits every run)
        cur.execute(f"""SELECT COUNT(*) FILTER (WHERE ROUND((seconds / 60)::numeric, 1)::float8 <> minutes),
                               COUNT(*) FROM {t}""")
        bad, n = cur.fetchone()
        assert n > 30000 and bad == 0, t
    # Mikal Bridges 2022-23 (BKN + PHX): the grid adds lineups; it ran ~3 minutes high when it added rounded minutes.
    cur.execute("SELECT SUM(seconds) / 60.0 FROM player_game_lines WHERE player_id = 1628969 AND season = 2023")
    on_floor = float(cur.fetchone()[0])
    total = 0.0
    for team in ("BKN", "PHX"):
        g = client.get("/lineups/pair-grid", params={"season": 2023, "team": team, "max_players": 15}).json()
        total += next(p["minutes"] for p in g["players"] if p["player_id"] == 1628969)
    assert abs(total - on_floor) <= 0.1
    # The Workbench, grouped by team: the summed seconds, not the summed rounded minutes.
    d = client.post("/workbench/query", json={"dataset": "lineup_season", "columns": ["minutes"],
                                             "season_from": 2023, "season_to": 2023, "group_by": "team",
                                             "min_poss": 0, "limit": 100})
    assert d.status_code == 200, d.text
    row = next(r for r in d.json()["rows"] if r["team"] == "BKN")
    cur.execute("SELECT SUM(seconds) / 60.0 FROM lineup_seasons WHERE season = 2023 AND team_abbreviation = 'BKN'")
    assert row["minutes"] == pytest.approx(float(cur.fetchone()[0]), abs=0.05)


# ─── R8-009 2025-26 awards ─────────────────────────────────────────────────

def test_2025_26_award_winners(cur):
    # Read 2026-10-06: https://en.wikipedia.org/wiki/2025%E2%80%9326_NBA_season (Awards),
    # https://en.wikipedia.org/wiki/NBA_Most_Valuable_Player_Award, .../NBA_Defensive_Player_of_the_Year_Award,
    # .../NBA_Rookie_of_the_Year_Award: MVP Shai Gilgeous-Alexander, DPOY Victor Wembanyama (unanimous),
    # ROY Cooper Flagg; All-NBA first team Cunningham, Dončić, Gilgeous-Alexander, Jokić, Wembanyama.
    cur.execute("SELECT award, player_id FROM award_winners WHERE season = 2026")
    assert dict(cur.fetchall()) == {"MVP": 1628983, "DPOY": 1641705, "ROY": 1642843}
    cur.execute("SELECT team_tier, COUNT(*) FROM all_nba_seasons WHERE season = 2026 GROUP BY 1 ORDER BY 1")
    assert cur.fetchall() == [(1, 5), (2, 5), (3, 5)]
    cur.execute("""SELECT player_name FROM all_nba_seasons WHERE season = 2026 AND team_tier = 1 ORDER BY 1""")
    assert [r[0] for r in cur.fetchall()] == ["Cade Cunningham", "Luka Dončić", "Nikola Jokić",
                                               "Shai Gilgeous-Alexander", "Victor Wembanyama"]


def test_prediction_ledger_says_when_rows_were_logged():
    from mvp_api import app
    d = TestClient(app).get("/ledger/summary").json()
    rows = [r for r in d["resolved"] if r["season"] == 2026]
    assert {r["model"] for r in rows} == {"mvp", "dpoy", "roy", "all_nba"}
    # Logged 2026-09-23, after the regular season ended 2026-04-12: the page must not read them as forecasts.
    assert all(r["logged_after_season"] and r["season_ended"] == "2026-04-12" for r in rows)
    assert "raw output" in d["methodology"]
