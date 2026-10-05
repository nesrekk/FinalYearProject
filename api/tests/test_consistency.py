"""
test_consistency.py
====================
Round 8 step 3: the same number agrees wherever it is shown. Each test takes a
quantity the app shows on more than one page, reads it through every route
that shows it, on a sample of players, teams and seasons drawn with a fixed
seed, and asserts they agree to the rounding each route applies. Where two
pages differ by definition, the difference is stated on both pages and pinned
here with its size, so a bigger gap fails:

  * player season line        Leaderboard Builder, profile, Player Comparison, Player
                              Stats table, Stat Line Finder, Workbench player_season
  * team record and margin    team page (summary, games, luck blocks), Luck & Schedule,
                              Season Simulator, Workbench team_season / team_game, the
                              Standings and Team Comparison stored fallback
  * team ratings              Team Comparison's advanced block = the team page
  * RAPM, Rating Tracker      RAPM page, profile, Workbench (values, intervals, ranks)
  * per-game lines            Game Log, Game Finder, Play Finder, Workbench player_game,
                              the profile's game-log season counts
  * on/off                    On/Off page, profile, team page, Workbench player_onoff
  * shot totals               Shot Charts (dots), profile zones, the zones route,
                              shot-making, quality map, shot value, zone history

Stated differences (the size pinned in the test that owns it):
  * the Season Simulator's as-of view is "that morning": at the season's last
    date it shows the record before that day's games;
  * the Workbench's on/off interval is closed-form (game-clustered), the On/Off
    page's a 2,000-resample game bootstrap: same centre, half-width within 25%;
  * the Game Log's games vs NBA.com's GP differ by at most 2 (the page shows both);
  * a player's minutes on the Game Log equal his stints' minutes except in the
    9 phantom-minute games (R8-023, Step 6a);
  * the shot chart's regular-season FGA equals the season row's FGA x GP to the
    per-game rounding (0.05 x GP + 2) in every season but 2025-26, where the four
    games with no chart rows explain every gap (R8-028, Step 6);
  * the quality map's cells plus its off-map count (beyond half court) equal the
    regular-season FGA;
  * Team Comparison's basic per-game block (the stored fallback) takes points from
    the final scores and the rest from the play-by-play lines, which run about
    half a rebound a game under NBA.com's team totals (team rebounds).

Run:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_consistency.py
"""

import os
import random
import sys

import psycopg2
import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from db_config import DB_CONFIG  # noqa: E402
from season_team import shown_team  # noqa: E402

SEED = 20261005
N_PLAYERS = 8
N_TEAMS = 6
RAW = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov"]


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable.")


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    c = conn.cursor()
    yield c
    c.close()
    conn.close()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from impact_api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def sim_client():
    from fastapi.testclient import TestClient
    from similarity_api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def players(cur):
    """(player_id, season) player-seasons 2020-21 on with 30+ games and 20+ minutes a game."""
    cur.execute("""SELECT player_id, season FROM player_season_stats
                   WHERE season >= 2021 AND gp >= 30 AND min >= 20 ORDER BY player_id, season""")
    return random.Random(SEED).sample(cur.fetchall(), N_PLAYERS)


@pytest.fixture(scope="module")
def teams(cur):
    """(season, team) team-seasons 2010-11 on (the simulator's span)."""
    cur.execute("SELECT season, team_abbreviation FROM team_luck_schedule WHERE season >= 2011 ORDER BY 1, 2")
    return random.Random(SEED).sample(cur.fetchall(), N_TEAMS)


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def one(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchone()


def _q(client, **spec):
    r = client.post("/workbench/query", json=spec)
    assert r.status_code == 200, r.text
    return r.json()


def _name(cur, pid):
    return one(cur, "SELECT player_name FROM player_season_stats WHERE player_id = %s ORDER BY season DESC LIMIT 1",
               (pid,))[0]


def _profile(client, pid):
    r = client.get(f"/player-profile/{pid}")
    assert r.status_code == 200, r.text
    return r.json()


# ═════════════════════════════════════════════════════════════════════════════
# Player season line
# ═════════════════════════════════════════════════════════════════════════════

def test_player_season_line_everywhere(cur, client, sim_client, players):
    """Games, points, true shooting and minutes of a player-season are the same number on the profile,
    the Player Stats table, the Leaderboard Builder, Player Comparison, the Stat Line Finder and the
    Workbench (all read player_season_stats; the table rounds to 1 decimal, the others to 3-4)."""
    for pid, season in players:
        name, team, gp, pts, ts, mins, reb, ast = one(
            cur, """SELECT player_name, team_abbreviation, gp, pts, ts_pct, min, reb, ast
                    FROM player_season_stats WHERE player_id = %s AND season = %s""", (pid, season))
        # The team the pages show and filter on (season_team.py, R8-020): not one he never played for.
        team = shown_team(cur, pid, season, team)
        prow = next(r for r in _profile(client, pid)["seasons"]["rows"] if r["season"] == season)
        assert (prow["gp"], prow["pts"], prow["ts_pct"], prow["min"]) == (gp, pts, ts, mins), (name, season)
        assert prow["team_abbreviation"] == team, (name, season)
        trow = next(r for r in client.get(f"/players/table/{season}").json()["results"] if r["player_id"] == pid)
        assert (trow["gp"], trow["traditional"]["pts"], trow["advanced"]["ts_pct"], trow["min"]) == \
            (gp, round(pts, 1), round(ts, 3), round(mins, 1)), (name, season)
        lb = client.get("/leaderboard/custom", params={"stat": "pts", "season_from": season, "season_to": season,
                                                       "team": team, "min_gp": 0, "min_mpg": 0, "top_n": 100}).json()
        lrow = next(r for r in lb["results"] if r["player_id"] == pid)
        assert (lrow["context"]["gp"], lrow["value"], lrow["context"]["ts_pct"]) == (gp, pts, ts), (name, season)
        cmp_ = client.get(f"/players/compare-profile/{name}", params={"season": season})
        if cmp_.status_code == 200:
            c = cmp_.json()
            assert c["player_id"] == pid
            assert (c["bio"]["gp"], c["tale_of_the_tape"]["pts"], c["tale_of_the_tape"]["ts_pct"], c["bio"]["min"]) == \
                (gp, pts, ts, round(mins, 1)), (name, season)
        else:
            # Under the comparison page's pool floor, or a namesake (R8-018, Step 5): the page says which.
            assert cmp_.status_code == 404 and ("qualified-pool" in cmp_.json()["detail"] or
                                                c_name_differs(cur, name)), cmp_.text
        w = _q(client, dataset="player_season", columns=["pts", "ts_pct", "min", "reb", "ast"], entities=[pid],
               season_from=season, season_to=season)["rows"][0]
        assert (w["n_games"], w["pts"], w["ts_pct"], w["min"], w["reb"], w["ast"]) == (gp, pts, ts, mins, reb, ast)
        sl = sim_client.get("/similarity/stat-line", params={
            "line": f"pts:{pts},ts_pct:{ts}", "season": season, "season_from": season, "season_to": season,
            "top_n": 5, "min_gp": 0}).json()
        srow = next(r for r in sl["results"] if r["player_id"] == pid)
        assert (srow["gp"], srow["stats"]["pts"]["value"], srow["stats"]["ts_pct"]["value"]) == (gp, pts, ts)
        assert srow["distance"] == 0.0


def c_name_differs(cur, name):
    cur.execute("SELECT count(DISTINCT player_id) FROM player_season_stats WHERE player_name = %s", (name,))
    return cur.fetchone()[0] > 1


# ═════════════════════════════════════════════════════════════════════════════
# Team record and margin
# ═════════════════════════════════════════════════════════════════════════════

def test_team_record_and_margin_everywhere(cur, client, teams):
    """A team-season's wins, losses, games and point margin: the team page's summary (team_seasons),
    its games block (counted from game_scores), its luck block and the Luck & Schedule page
    (team_luck_schedule), the Season Simulator's final record (season_postseason), the Workbench's
    team_season and team_game datasets, and the Standings fallback (round 8 R8-062). The simulator's
    as-of view is "that morning", so at the season's last date it shows the record before that day's
    games: pinned as wins + losses + games left = games."""
    from routers.meta import db_standings
    for season, team in teams:
        tp = client.get(f"/team-profile/{team}", params={"season": season}).json()
        s = tp["summary"]["team"]
        w, l, g, mov = int(s["w"]), int(s["l"]), int(s["g"]), s["mov"]
        games = tp["games"]["games"]
        gw = sum(1 for x in games if x["pts_for"] > x["pts_against"])
        gm = sum(x["pts_for"] - x["pts_against"] for x in games) / len(games)
        assert (gw, len(games) - gw, len(games)) == (w, l, g), (season, team)
        assert gm == pytest.approx(mov, abs=0.006)
        lk = tp["luck"]
        assert (lk["wins"], lk["losses"], lk["games"]) == (w, l, g) and lk["mov"] == pytest.approx(mov, abs=0.006)
        ls = client.get("/teams/luck-schedule", params={"season": season}).json()
        lrow = next(r for r in ls["teams"] if r["team_abbreviation"] == team)
        assert (lrow["wins"], lrow["losses"], lrow["games"]) == (w, l, g) and lrow["mov"] == pytest.approx(mov, abs=0.006)
        sim = client.get("/season-sim", params={"season": season, "as_of": ls["season_info"]["last_date"]}).json()
        srow = next(r for conf in sim["conferences"].values() for r in conf if r["team"] == team)
        assert (srow["final"]["wins"], srow["final"]["losses"]) == (w, l)
        assert srow["wins"] + srow["losses"] + srow["games_left"] == g and srow["wins"] <= w and srow["games_left"] >= 0
        wts = _q(client, dataset="team_season", columns=["w", "l", "mov", "g"], entities=[team],
                 season_from=season, season_to=season)["rows"][0]
        assert (wts["w"], wts["l"], wts["g"]) == (w, l, g) and wts["mov"] == pytest.approx(mov, abs=0.006)
        wtg = _q(client, dataset="team_game", columns=["wins", "games", "margin"], entities=[team],
                 season_from=season, season_to=season, group_by="entity")["rows"][0]
        assert (wtg["wins"], wtg["n_games"]) == (w, g) and wtg["margin"] == pytest.approx(mov, abs=0.006)
        assert (team, w, l) in {(a, int(x), int(y)) for a, x, y in db_standings(cur, season)}


def test_team_comparison_advanced_block_is_the_team_page(cur, client, teams):
    """Team Comparison's offensive, defensive and net rating equal the team page's (team_seasons,
    Basketball-Reference) and its turnovers per game are NBA.com's team box score from 2020-21
    (round 8 R8-063: before, the ratings were a games-weighted mean of the players' own on-court
    ratings, 0.9 points off the team's net rating on average and up to 4.0). Recent form and the
    head-to-head margins are the real final scores."""
    for season, team in teams:
        other = "BOS" if team != "BOS" else "LAL"
        tc = client.get(f"/teams/compare/{team}/{other}", params={"season": season}).json()
        adv = tc["team_a"]["advanced_stats"]
        tp = client.get(f"/team-profile/{team}", params={"season": season}).json()["summary"]["team"]
        assert (adv["offRating"], adv["defRating"], adv["netRating"]) == \
            (round(tp["o_rtg"], 1), round(tp["d_rtg"], 1), round(tp["n_rtg"], 1)), (season, team)
        box = one(cur, "SELECT SUM(tov)::float / COUNT(*) FROM game_team_box WHERE season = %s AND team_abbreviation = %s",
                  (season, team))[0]
        if box is not None:
            assert adv["tov"] == round(box, 1) and adv["tovSource"] == "game_team_box"
        else:
            assert season < 2021 and adv["tovSource"] == "player_season_stats" and 8 <= adv["tov"] <= 20
        for g in tc["team_a"]["recent_form"]["results"]:
            pf, pa = one(cur, "SELECT pts_for, pts_against FROM game_scores WHERE team_abbreviation = %s AND game_date = %s",
                         (team, g["date"]))
            assert g["point_diff"] == pf - pa and g["win"] == (pf > pa)


def test_standings_and_team_stats_fallback_are_the_real_season(cur, client):
    """The Dashboard / Standings / Team Comparison route's stored fallback (round 8 R8-062): every
    team's record is team_seasons' (before: its best player's w_pct x 82, CLE 82-0), points per game
    are the real final scores (before: every player's season row under his last team divided by
    the roster's most games, CLE 147.5 for 119.5), and the rest comes from the play-by-play lines,
    whose team totals ran within 0.5 rebounds, 0.2 assists and 0.4 FG% points of NBA.com's own
    2025-26 team block on 2026-10-05 (team rebounds belong to nobody in the lines)."""
    from routers.meta import db_standings, db_team_stats
    season = one(cur, "SELECT MAX(season) FROM player_season_stats")[0]
    cur.execute("""SELECT abbreviation, w, l, pts_per_game FROM team_seasons
                   WHERE season = %s AND NOT is_league_avg ORDER BY 1""", (season,))
    truth = {a: (int(w), int(l), float(p)) for a, w, l, p in cur.fetchall()}
    standings = {a: (int(w), int(l)) for a, w, l in db_standings(cur, season)}
    assert standings == {a: (w, l) for a, (w, l, _) in truth.items()}
    stats = db_team_stats(cur, season)
    assert set(stats) == set(truth)
    for a, (_, _, ppg) in truth.items():
        assert stats[a]["ppg"] == pytest.approx(ppg, abs=0.051), a
        for k in ("rpg", "apg", "spg", "bpg", "fgPct", "threePct", "ftPct"):
            assert stats[a][k] is not None, (a, k)
        assert 35 <= stats[a]["rpg"] <= 55 and 20 <= stats[a]["apg"] <= 35 and 40 <= stats[a]["fgPct"] <= 55
    # The live parser (stats.nba.com's LeagueDashTeamStats has no TEAM_ABBREVIATION column: the code
    # comes from the name; nothing played = no block, so the stored season shows instead of zeros).
    from impact_core import parse_team_stats_rows, TEAM_NAME_TO_ABBR
    headers = ["TEAM_ID", "TEAM_NAME", "GP", "W", "L", "W_PCT", "MIN", "FGM", "FGA", "FG_PCT", "FG3M", "FG3A",
               "FG3_PCT", "FTM", "FTA", "FT_PCT", "OREB", "DREB", "REB", "AST", "TOV", "STL", "BLK", "BLKA", "PF",
               "PFD", "PTS", "PLUS_MINUS"]
    row = [1610612739, "Cleveland Cavaliers", 82, 64, 18, 0.78, 48.3, 44.0, 91.4, 0.481, 15.8, 38.5, 0.411, 15.7,
           20.4, 0.769, 11.6, 33.0, 44.6, 28.5, 13.4, 8.5, 5.0, 4.1, 17.9, 18.7, 121.9, 9.6]
    clippers = [1610612746, "LA Clippers", 82, 50, 32, 0.61, 48.4, 41.0, 88.0, 0.466, 13.0, 35.0, 0.371, 17.0, 21.0,
                0.81, 10.0, 34.0, 44.0, 25.0, 13.0, 9.0, 4.5, 4.0, 18.0, 18.0, 112.0, 4.0]
    parsed = parse_team_stats_rows(headers, [row, clippers])
    assert parsed["CLE"] == {"name": "Cleveland Cavaliers", "abbr": "CLE", "ppg": 121.9, "rpg": 44.6, "apg": 28.5,
                             "spg": 8.5, "bpg": 5.0, "fgPct": 48.1, "threePct": 41.1, "ftPct": 76.9}
    assert parsed["LAC"]["ppg"] == 112.0 and TEAM_NAME_TO_ABBR["LA Clippers"] == "LAC"
    assert parse_team_stats_rows(headers, [row[:2] + [0] + row[3:]]) is None


# ═════════════════════════════════════════════════════════════════════════════
# RAPM and the Rating Tracker
# ═════════════════════════════════════════════════════════════════════════════

VERSIONS = (("single", "rapm", None), ("prior", "rapm_prior", None), ("multi", "rapm_multi", None),
            ("tracker", "tracker", "filtered"), ("tracker", "tracker_smoothed", "smoothed"))


def test_rapm_and_tracker_everywhere(client, players):
    """A player-season's rating, its interval and its league rank are the same on the RAPM page (3
    decimals), the profile (2 decimals) and the Workbench (4 decimals) for every version: one season,
    BPM prior, three-season window, Rating Tracker as of then and in hindsight."""
    seen = 0
    for pid, season in players:
        prof = _profile(client, pid)
        for version, key, kind in VERSIONS:
            params = {"version": version, "season": season}
            if kind:
                params["kind"] = kind
            page = client.get("/rapm", params=params).json()
            if "players" not in page:
                continue                          # the version has no such season (multi starts 2022-23)
            prow = next((r for r in page["players"] if r["player_id"] == pid), None)
            rows = prof["rating_tracker"]["rows"] if version == "tracker" else prof["rapm"]["rows"]
            frow = next((r for r in rows if r["season"] == season and r.get("kind", r.get("version")) == (kind or version)),
                        None)
            (w,) = _q(client, dataset="player_season", columns=[key], entities=[pid], season_from=season,
                      season_to=season)["rows"]
            if prow is None:
                assert frow is None and w[key] is None, (pid, season, version, kind)
                continue
            seen += 1
            assert frow is not None and w[key] is not None, (pid, season, version, kind)
            assert abs(prow["rapm"] - frow["rapm"]) <= 0.006 and abs(prow["rapm"] - w[key]) <= 0.0006
            assert abs(prow["rapm_ci_low"] - frow["ci_low"]) <= 0.006 and abs(prow["rapm_ci_high"] - frow["ci_high"]) <= 0.006
            assert w["ci"][key] == pytest.approx([prow["rapm_ci_low"], prow["rapm_ci_high"]], abs=0.0006)
            assert prow["qualified"] == frow["qualified"]
            if prow["qualified"]:
                assert prow["rapm_rank"] == frow["rank"], (pid, season, version, kind, prow["rapm_rank"], frow["rank"])
    assert seen >= 10


def test_rapm_page_ties_share_a_rank(cur, client):
    """The stored ratings carry three decimals, so a few pairs a season tie; the page ranks them alike,
    as the profile's RANK() does (round 8 R8-064: before, the page numbered them 1, 2, 3 ... in a
    sorted order, so a tied player's rank differed by one between the two pages)."""
    version, season, value, ids = one(cur, """SELECT version, season, rapm, array_agg(player_id) FROM player_rapm
                                             WHERE qualified GROUP BY 1, 2, 3 HAVING count(*) > 1
                                             ORDER BY 2 DESC, 1 LIMIT 1""")
    page = client.get("/rapm", params={"version": version, "season": season}).json()
    ranks = {r["player_id"]: r["rapm_rank"] for r in page["players"] if r["player_id"] in ids}
    assert len(set(ranks.values())) == 1, ranks
    better = sum(1 for r in page["players"] if r["qualified"] and r["rapm"] > value)
    assert set(ranks.values()) == {better + 1}


# ═════════════════════════════════════════════════════════════════════════════
# Per-game lines
# ═════════════════════════════════════════════════════════════════════════════

def test_game_lines_everywhere(cur, client, players):
    """A player-season's game lines are the same rows on the Game Log, the Game Finder, the Workbench's
    player_game dataset (every box-score column; minutes to 0.05) and the profile's game-log season
    count, and the Play Finder's plays for a game add up to that game's line (made and missed twos and
    threes, free throws, rebounds, assists, steals, blocks, turnovers). The Game Log's games and NBA.com's
    GP differ by at most 2 (the page shows both)."""
    for pid, season in players[:5]:
        log = client.get(f"/games/player-log/{pid}", params={"season": season})
        assert log.status_code == 200, log.text
        log = log.json()
        gf = client.get("/games/finder", params={"player_id": pid, "season_from": season, "season_to": season,
                                                 "limit": 200, "sort": "date", "order": "asc"}).json()
        wb = _q(client, dataset="player_game", columns=RAW + ["min", "plus_minus"], entities=[pid],
                season_from=season, season_to=season, limit=200, sort=[{"key": "date", "dir": "asc"}])
        prof_games = next(s for s in _profile(client, pid)["game_log"]["seasons"] if s["season"] == season)["games"]
        assert log["games"] == gf["total"] == wb["n"]["matched"] == prof_games == len(log["rows"])
        assert log["nba_gp"] is not None and abs(log["games"] - log["nba_gp"]) <= 2
        finder = {r["date"]: r for r in gf["results"]}
        bench = {r["date"]: r for r in wb["rows"]}
        for r in log["rows"]:
            f, w = finder[r["date"]], bench[r["date"]]
            for k in RAW:
                assert r[k] == f[k] == w[k], (pid, r["date"], k)
            assert abs(r["min"] - f["min"]) < 1e-9 and abs(w["min"] - r["min"]) <= 0.051
            assert (r["team"], r["opponent"], r["home"], r["win"]) == (f["team"], f["opponent"], f["home"], f["win"]) == \
                (w["team"], w["opponent"], w["home"], w["win"])
            # On-floor +/- (Workbench) = player_game_onfloor where the game reconciles (the Game Log
            # shows none yet: R8-019, Step 5).
            pm = one(cur, """SELECT CASE WHEN game_ok THEN plus_minus END FROM player_game_onfloor o
                             JOIN game_scores g ON g.game_id = %s WHERE o.player_id = %s AND o.game_id = 'espn_' || g.espn_id""",
                     (w["game_id"], pid))
            assert pm is not None and w["plus_minus"] == pm[0], (pid, r["date"])
        for r in log["rows"][:3]:
            pf = client.get("/plays/finder", params={"player_id": pid, "date_from": r["date"], "date_to": r["date"],
                                                     "limit": 1}).json()
            c = {x["key"]: x["n"] for x in pf["by_cat"]}
            made = c.get("made2", 0) + c.get("made3", 0)
            assert made == r["fgm"] and made + c.get("miss2", 0) + c.get("miss3", 0) == r["fga"]
            assert c.get("made3", 0) == r["fg3m"] and c.get("made3", 0) + c.get("miss3", 0) == r["fg3a"]
            assert c.get("ftm", 0) == r["ftm"] and c.get("ftm", 0) + c.get("ftx", 0) == r["fta"]
            assert c.get("oreb", 0) == r["oreb"] and c.get("dreb", 0) == r["dreb"]
            assert c.get("ast2", 0) + c.get("ast3", 0) == r["ast"]
            assert (c.get("stl", 0), c.get("blk", 0), c.get("tov", 0)) == (r["stl"], r["blk"], r["tov"])
            assert pf["points"] == r["pts"]


def test_game_log_minutes_equal_the_stints(cur):
    """Minutes on the Game Log (player_game_lines) and in the Rotations' stints (player_game_onfloor,
    the stints' seconds) are the same in every player-game but the 9 phantom-minute games of a
    team-less substitution (R8-023, Step 6a)."""
    n, bad = one(cur, """SELECT COUNT(*), COUNT(*) FILTER (WHERE ABS(o.seconds - l.seconds) > 1)
                         FROM player_game_lines l JOIN player_game_onfloor o USING (player_id, game_id)
                         WHERE l.seconds > 0""")
    assert n >= 150000 and bad == 9


def test_game_log_games_match_nba_gp(cur):
    """For players with one team in a season, the Game Log's games (lines played) and NBA.com's GP agree
    for 95%+ of player-seasons and never differ by more than 2 (2026-10-05: at most 16 of 510 differ,
    by 1, except one by 2)."""
    cur.execute("""WITH lt AS (SELECT player_id, season, COUNT(DISTINCT team_abbreviation) teams,
                                      COUNT(*) FILTER (WHERE seconds > 0) gp
                               FROM player_game_lines WHERE game_id IN (SELECT 'espn_' || espn_id FROM game_scores)
                               GROUP BY 1, 2)
                   SELECT lt.season, COUNT(*), COUNT(*) FILTER (WHERE lt.gp <> s.gp), MAX(ABS(lt.gp - s.gp))
                   FROM lt JOIN player_season_stats s USING (player_id, season) WHERE lt.teams = 1 GROUP BY 1""")
    for season, n, differ, worst in cur.fetchall():
        assert differ / n <= 0.05 and worst <= 2, (season, n, differ, worst)


# ═════════════════════════════════════════════════════════════════════════════
# On/off
# ═════════════════════════════════════════════════════════════════════════════

def test_on_off_everywhere(cur, client, players):
    """A player-season-team's on/off net rating and interval: the On/Off page (2 decimals), the profile
    (2), the team page's block (1, when he is among its top or bottom three or its star) and the
    Workbench's player_onoff (computed live to 4 decimals from the same games, so it agrees with the
    stored 2-decimal value to its rounding; its interval is closed-form, half-width within 25% of
    the page's bootstrap, centre the same)."""
    seen = 0
    for pid, season in players:
        row = one(cur, """SELECT team_abbreviation, minutes_on, on_off_net, on_off_ci_low, on_off_ci_high
                          FROM player_on_off WHERE player_id = %s AND season = %s ORDER BY minutes_on DESC LIMIT 1""",
                  (pid, season))
        if row is None:
            continue
        seen += 1
        team, mon, net, lo, hi = row
        page = next(r for r in client.get("/lineups/on-off", params={"season": season, "team": team}).json()["players"]
                    if r["player_id"] == pid)
        assert (page["minutes_on"], page["on_off_net"], page["on_off_ci_low"], page["on_off_ci_high"]) == \
            (round(mon, 2), round(net, 2), round(lo, 2), round(hi, 2))
        prof = next(r for r in _profile(client, pid)["on_off"]["rows"] if r["season"] == season and r["team"] == team)
        assert (prof["minutes_on"], prof["on_off_net"], prof["ci_low"], prof["ci_high"]) == \
            (round(mon, 2), round(net, 2), round(lo, 2), round(hi, 2))
        blk = client.get(f"/team-profile/{team}", params={"season": season}).json()["on_off"]
        shown = [r for r in blk.get("top", []) + blk.get("bottom", []) + ([blk["star"]] if blk.get("star") else [])
                 if r["player_id"] == pid]
        for t in shown:
            assert (t["on_off_net"], t["ci_low"], t["ci_high"]) == (round(net, 1), round(lo, 1), round(hi, 1))
        wb = next(r for r in _q(client, dataset="player_onoff", columns=["on_off_net", "minutes_on"], entities=[pid],
                                season_from=season, season_to=season)["rows"] if r["team"] == team)
        # player_on_off stores 2 decimals (minutes 1), the Workbench computes to 4.
        assert abs(wb["on_off_net"] - net) <= 0.006 and abs(wb["minutes_on"] - mon) <= 0.06
        w_lo, w_hi = wb["ci"]["on_off_net"]
        assert (w_hi - w_lo) / (hi - lo) == pytest.approx(1, abs=0.25)
    assert seen >= 5


# ═════════════════════════════════════════════════════════════════════════════
# Shot totals
# ═════════════════════════════════════════════════════════════════════════════

def test_shot_totals_everywhere(cur, client, players):
    """A player-season's regular-season attempts and makes: the Shot Charts dots (regular season by
    default), the profile's shot seasons and zones, the zones route (round 8 R8-065: it counted playoff
    and play-in shots too), shot-making, the quality map (cells plus its off-map count), shot value
    and the shot-mix history all count the same shots (player_shots, game_id '002…')."""
    for pid, season in players:
        name, sl = _name(cur, pid), label(season)
        reg_n, reg_m, all_n = one(cur, """SELECT COUNT(*) FILTER (WHERE game_id LIKE '002%%'),
                                                 COUNT(*) FILTER (WHERE game_id LIKE '002%%' AND shot_made_flag = 1),
                                                 COUNT(*)
                                          FROM player_shots WHERE player_id = %s AND season = %s""", (pid, sl))
        if not reg_n:
            continue
        page = client.get(f"/shots/player/{name}", params={"season": sl, "player_id": pid}).json()
        assert page["player_id"] == pid and page["source"] == "cache" and len(page["shots"]) == all_n
        reg = [s for s in page["shots"] if str(s["game_id"]).startswith("002")]
        assert (len(reg), sum(1 for s in reg if int(s["shot_made_flag"]) == 1)) == (reg_n, reg_m)
        prof = _profile(client, pid)
        assert next(s for s in prof["shots"]["seasons"] if s["season"] == season)["fga"] == reg_n
        pz = client.get(f"/player-profile/{pid}/shot-zones", params={"season": season}).json()
        assert (pz["fga"], pz["fgm"]) == (reg_n, reg_m)
        zones = client.get(f"/shots/player/{name}/zones", params={"season": season}).json()
        if zones["player_id"] == pid:
            assert (sum(z["fga"] for z in zones["zones"]), sum(z["fgm"] for z in zones["zones"])) == (reg_n, reg_m)
        sm = next(r for r in client.get(f"/shots/player/{name}/shot-making", params={"player_id": pid}).json()["rows"]
                  if r["season"] == season)
        assert (sm["fga"], sm["fgm"]) == (reg_n, reg_m)
        qm = client.get("/shots/quality-map", params={"player": name, "season": season, "player_id": pid})
        if qm.status_code == 200:
            q = qm.json()
            assert (q["totals"]["fga"] + q["off_map"]["fga"], q["totals"]["fgm"] + q["off_map"]["fgm"]) == (reg_n, reg_m)
        else:
            assert reg_n < 200
        sv = next(r for r in client.get(f"/shots/shot-value/player/{pid}").json()["seasons"] if r["season"] == season)
        assert (sv["fga"], sv["fgm"]) == (reg_n, reg_m)
        zh = next(r for r in client.get(f"/shots/player/{name}/zone-history", params={"player_id": pid}).json()["seasons"]
                  if r["season"] == sl)
        assert (zh["fga"], zh["fgm"]) == (reg_n, reg_m)


def test_shot_zones_route_counts_the_regular_season(cur, client):
    """The zones route for a player-season with playoff shots returns the regular-season counts, like
    the profile's zones and the league zones it is compared with (R8-065)."""
    pid, name, sl, reg, total = one(cur, """SELECT player_id, MAX(player_name), season,
                                                   COUNT(*) FILTER (WHERE game_id LIKE '002%%'), COUNT(*)
                                            FROM player_shots WHERE season >= '2020-21' GROUP BY 1, 3
                                            HAVING COUNT(*) > COUNT(*) FILTER (WHERE game_id LIKE '002%%') + 50
                                            ORDER BY 1, 3 LIMIT 1""")
    season = int(sl[:4]) + 1
    z = client.get(f"/shots/player/{name}/zones", params={"season": season}).json()
    if z["player_id"] != pid:
        pytest.skip(f"{name} is a shared name (R8-018)")
    assert sum(x["fga"] for x in z["zones"]) == reg < total and z["games"] == "regular season"


def test_shot_chart_fga_matches_the_season_table(cur):
    """The shot chart's regular-season FGA per player-season equals the season row's FGA x GP to the
    per-game rounding (0.05 x GP + 2) for every player with 20+ games, 2009-10 to 2024-25, except
    two a hair over it (Bogut 2015-16 and Nurkić 2016-17, each 6 attempts short of the season row).
    In 2025-26 the four games with no chart rows (R8-028, Step 6) explain every gap: adding those
    games' lines FGA brings every player back inside the tolerance."""
    cur.execute("""WITH sh AS (SELECT player_id, LEFT(season, 4)::int + 1 AS season, COUNT(*) fga
                               FROM player_shots WHERE game_id LIKE '002%%' AND season >= '2009-10' GROUP BY 1, 2)
                   SELECT sh.season, COUNT(*), COUNT(*) FILTER (WHERE ABS(sh.fga - s.fga * s.gp) > 0.05 * s.gp + 2),
                          MAX(ABS(sh.fga - s.fga * s.gp))
                   FROM sh JOIN player_season_stats s USING (player_id, season) WHERE s.gp >= 20 GROUP BY 1 ORDER BY 1""")
    rows = cur.fetchall()
    assert [s for s, *_ in rows][0] == 2010 and len(rows) >= 17
    for season, n, off, worst in rows:
        if season == 2026:
            assert off > 0            # R8-028 (if this fails the chart was re-fetched: drop the branch below)
            continue
        assert off <= (1 if season in (2016, 2017) else 0) and worst <= 6, (season, n, off, worst)
    cur.execute("""SELECT game_id FROM game_scores WHERE season = 2026
                   AND game_id NOT IN (SELECT DISTINCT game_id FROM player_shots WHERE season = '2025-26')
                   GROUP BY 1 ORDER BY 1""")
    missing = [r[0] for r in cur.fetchall()]
    assert missing == ["0022500259", "0022500260", "0022500261", "0022500265"]
    (still_off,) = one(cur, """
        WITH sh AS (SELECT player_id AS pid, COUNT(*) fga FROM player_shots WHERE game_id LIKE '002%%' AND season = '2025-26'
                    GROUP BY 1),
             miss AS (SELECT l.player_id AS pid, SUM(l.fga) fga FROM player_game_lines l
                      JOIN game_scores g ON 'espn_' || g.espn_id = l.game_id AND g.team_abbreviation = l.team_abbreviation
                      WHERE g.game_id = ANY(%s) GROUP BY 1)
        SELECT COUNT(*) FILTER (WHERE ABS(sh.fga + COALESCE(miss.fga, 0) - s.fga * s.gp) > 0.05 * s.gp + 2)
        FROM sh JOIN player_season_stats s ON s.player_id = sh.pid AND s.season = 2026
        LEFT JOIN miss ON miss.pid = sh.pid WHERE s.gp >= 20""", (missing,))
    assert still_off == 0
