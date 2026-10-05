"""Round 8 step 5: players by id, +/- in the Game Log, the right team on each row.

R8-018  Typed names: 19 names belong to two players. impact_core.find_player() (and similarity_api's
        find_player_id()) take the latest player of an exact name, deterministically; every route that
        takes a typed name also takes an optional player_id, which picks the player exactly.
R8-019  Game Log and Game Finder show per-game on-floor plus-minus from player_game_onfloor (None where
        the game doesn't reconcile), and the Game Finder can filter and sort on it.
R8-020  The 22 player-seasons (2020-21 on) whose season row names a team the player never played for
        show the last team he played for in the play-by-play, on every page that shows that team.

Run:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_round8_ids.py
"""

import os
import re
import sys
import unicodedata

import psycopg2
import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from db_config import DB_CONFIG  # noqa: E402


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
def namesakes(cur):
    """{lower name: [(player_id, first season, last season), ...] latest career first} for shared names."""
    cur.execute("""
        WITH n AS (SELECT lower(player_name) ln, player_id, MIN(season) s0, MAX(season) s1,
                          SUM(COALESCE(min, 0) * COALESCE(gp, 0)) mins
                   FROM player_season_stats GROUP BY 1, 2)
        SELECT ln, player_id, s0, s1 FROM n
        WHERE ln IN (SELECT ln FROM n GROUP BY ln HAVING COUNT(DISTINCT player_id) > 1)
        ORDER BY ln, s1 DESC, mins DESC, player_id""")
    out = {}
    for ln, pid, s0, s1 in cur.fetchall():
        out.setdefault(ln, []).append((pid, s0, s1))
    return out


# ═════════════════════════════════════════════════════════════════════════════
# R8-018: players by id
# ═════════════════════════════════════════════════════════════════════════════

def test_shared_names_are_the_known_nineteen(namesakes):
    assert len(namesakes) == 19
    assert {"brandon williams", "mike james", "nate williams", "johnny davis"} <= set(namesakes)


def test_a_typed_name_takes_the_latest_player_and_an_id_picks_exactly(cur, namesakes):
    """Before step 5 an unordered DISTINCT ... LIMIT 1 returned the lower id: the 1998-99 to 2002-03
    Brandon Williams for anyone typing the name of the one playing now."""
    import impact_core
    import similarity_api
    for ln, players in namesakes.items():
        latest = players[0][0]
        assert impact_core.find_player(cur, ln)[0] == latest, ln
        assert similarity_api.find_player_id(cur, ln)[0] == latest, ln
        for pid, _, _ in players:
            assert impact_core.resolve_player(cur, ln, pid)[0] == pid, (ln, pid)
            assert similarity_api.find_player_id(cur, ln, pid)[0] == pid, (ln, pid)


def test_no_route_looks_a_typed_name_up_without_an_id():
    """Every router goes through resolve_player(), except the search palette's name -> id resolver."""
    routers = os.path.join(_API_DIR, "routers")
    callers = []
    for f in sorted(os.listdir(routers)):
        if f.endswith(".py"):
            text = open(os.path.join(routers, f)).read()
            callers += [f for _ in re.finditer(r"\bfind_player\(", text)]
    assert callers == ["player_profile.py"]


def _history_route_cases(namesakes):
    for ln, players in namesakes.items():
        for pid, s0, s1 in players:
            yield ln, pid, s0, s1


def test_history_route_by_name_and_by_id_for_all_nineteen(client, namesakes):
    for ln, pid, s0, s1 in _history_route_cases(namesakes):
        d = client.get(f"/players/history/{ln}", params={"player_id": pid}).json()
        assert d["player_id"] == pid, ln
        seasons = [s["season"] for s in d["seasons"]]
        assert min(seasons) == s0 and max(seasons) == s1, (ln, pid)
        if pid == namesakes[ln][0][0]:
            assert client.get(f"/players/history/{ln}").json()["player_id"] == pid, ln


def _ok_for(r, pid):
    """The response is this player's, or a 404 saying he has no data there (never the namesake's)."""
    if r.status_code == 404:
        return True
    assert r.status_code == 200, r.text
    d = r.json()
    got = d.get("player_id", d.get("query", {}).get("player_id"))
    return got == pid


@pytest.mark.parametrize("path,params", [
    ("/players/compare-profile/{n}", {"season": "{s}"}),
    ("/radar/{n}", {"season": "{s}"}),
    ("/players/scouting-report/{n}", {"season": "{s}"}),
    ("/players/playtype-profile/{n}", {"season": "{s}"}),
    ("/matchups/player/{n}", {"season": "{s}"}),
    ("/impact/player/{n}/{s}", {}),
    ("/players/profile/{n}", {"season": "{s}"}),
    ("/shots/quality-map/options", {"player": "{n}"}),
])
def test_season_routes_pick_by_id(client, namesakes, path, params):
    """Both Brandon Williamses and both Mike Jameses, each in his own last season."""
    for ln in ("brandon williams", "mike james"):
        for pid, _, s1 in namesakes[ln]:
            url = path.format(n=ln, s=s1)
            q = {k: v.format(n=ln, s=s1) for k, v in params.items()}
            r = client.get(url, params={**q, "player_id": pid})
            assert _ok_for(r, pid), (url, pid, r.text[:200])


def test_pair_synergy_takes_both_ids(client, namesakes):
    newer, older = namesakes["brandon williams"][0][0], namesakes["brandon williams"][1][0]
    r = client.get("/players/pair-synergy", params={"player_a": "Brandon Williams", "player_b": "Brandon Williams",
                                                    "player_a_id": newer, "player_b_id": older, "season": 2023})
    # Two different players now (the same name twice used to be "pick two different players").
    assert r.status_code != 400 or "different" not in r.text


def test_similarity_routes_pick_by_id(sim_client, namesakes):
    for pid, _, s1 in namesakes["brandon williams"]:
        for url, params in [(f"/clusters/player/brandon williams", {}),
                            (f"/similarity/career/brandon williams", {}),
                            (f"/similarity/season/brandon williams/{s1}", {}),
                            (f"/players/trajectory/brandon williams", {"season": s1})]:
            r = sim_client.get(url, params={**params, "player_id": pid})
            assert _ok_for(r, pid), (url, pid, r.text[:200])


# ═════════════════════════════════════════════════════════════════════════════
# R8-019: +/- in the Game Log and Game Finder
# ═════════════════════════════════════════════════════════════════════════════

def test_game_log_plus_minus_is_the_onfloor_table(client, cur):
    """Jokić 2022-23: every row's +/- equals player_game_onfloor (None where the game doesn't reconcile)."""
    d = client.get("/games/player-log/203999", params={"season": 2023}).json()
    cur.execute("""SELECT l.game_date, CASE WHEN o.game_ok THEN o.plus_minus END
                   FROM player_game_lines l JOIN player_game_onfloor o USING (player_id, game_id)
                   WHERE l.player_id = 203999 AND l.season = 2023 AND l.seconds > 0""")
    want = {dt.isoformat(): pm for dt, pm in cur.fetchall()}
    assert len(d["rows"]) == 69
    for r in d["rows"]:
        assert r["plus_minus"] == want[r["date"]], r["date"]
    known = [r["plus_minus"] for r in d["rows"] if r["plus_minus"] is not None]
    assert d["averages"]["plus_minus_games"] == len(known)
    assert d["averages"]["plus_minus"] == round(sum(known) / len(known), 2)
    assert "plus_minus" in d["notes"]


def test_game_finder_filters_and_sorts_on_plus_minus(client, cur):
    d = client.get("/games/finder", params={"f": "plus_minus:gte:40", "sort": "plus_minus", "limit": 200}).json()
    cur.execute("""SELECT count(*) FROM player_game_lines l
                   JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
                   JOIN player_game_onfloor o ON o.player_id = l.player_id AND o.game_id = l.game_id
                   WHERE l.seconds > 0 AND o.game_ok AND o.plus_minus >= 40""")
    assert d["total"] == cur.fetchone()[0] > 0
    pms = [r["plus_minus"] for r in d["results"]]
    assert all(p >= 40 for p in pms) and pms == sorted(pms, reverse=True)
    low = client.get("/games/finder", params={"f": "plus_minus:lte:-40", "sort": "plus_minus", "order": "asc"}).json()
    assert low["results"] and all(r["plus_minus"] <= -40 for r in low["results"])
    s = client.get("/games/finder", params={"f": "plus_minus:gte:15", "mode": "streaks", "limit": 3}).json()
    assert s["results"][0]["averages"]["plus_minus"] >= 15
    opts = client.get("/games/finder/options").json()
    assert {"key": "plus_minus", "label": "Plus-minus (on the floor)", "format": "signed1"} in opts["stats"]


def _fold(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().replace(".", "")


def test_game_finder_plus_minus_against_espns_box_score(client, cur):
    """Every player's +/- in one game (Denver at Indiana, 2022-11-09) against ESPN's own box score, read
    live (skipped when ESPN doesn't answer). The stints' free-throw rule equals ESPN's box-score +/- in 98%
    of player-games (300 random games, 2026-10-03)."""
    import impact_core
    game = "0022200160"
    box = impact_core.game_boxscore(game)
    if box.get("status") != "ok":
        pytest.skip("ESPN's box score didn't answer.")
    espn = {_fold(p["name"]): p["pm"] for side in ("home", "away") for p in box["boxscore"][side]
            if p.get("pm") is not None}
    cur.execute("SELECT team_abbreviation, game_date FROM team_game_fatigue WHERE game_id = %s", (game,))
    sides = cur.fetchall()
    ours = []
    for team, day in sides:
        d = client.get("/games/finder", params={"f": "min:gte:0", "team": team, "season_from": 2023,
                                                "season_to": 2023, "sort": "date", "order": "asc",
                                                "one_per_player": "false", "limit": 200}).json()
        ours += [r for r in d["results"] if r["nba_game_id"] == game]
    matched = [(r["player_name"], r["plus_minus"], espn[_fold(r["player_name"])]) for r in ours
               if _fold(r["player_name"]) in espn and r["plus_minus"] is not None]
    assert len(matched) >= 15, (len(ours), len(espn))
    assert sum(a == b for _, a, b in matched) / len(matched) >= 0.9, matched


# ═════════════════════════════════════════════════════════════════════════════
# R8-020: the right team on each row
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def fixes(cur):
    import season_team
    return season_team.season_team_fixes(cur)


def test_wrong_team_rows_are_the_measured_twenty_two(fixes):
    """Measured 2026-10-05: 22 rows 2020-21 to 2025-26 name a team the player never played for that season.
    A season load or a rebuild of player_game_lines can change the count: re-read it, then update this."""
    assert len(fixes) == 22
    assert fixes[(1630217, 2025)] == ("ORL", "MEM")      # Desmond Bane 2024-25
    assert fixes[(203484, 2025)] == ("MEM", "ORL")       # Kentavious Caldwell-Pope 2024-25
    assert fixes[(203076, 2026)] == ("WAS", "DAL")       # Anthony Davis 2025-26
    assert all(pid > 0 and 2021 <= s <= 2026 for pid, s in fixes)


def test_every_page_shows_the_team_he_played_for(client, sim_client, fixes):
    bane = (1630217, 2025)
    shown = fixes[bane][1]
    # Player Stats
    rows = client.get(f"/players/table/{bane[1]}").json()["results"]
    assert next(r for r in rows if r["player_id"] == bane[0])["team_abbreviation"] == shown
    # Leaderboard Builder: shown and filtered on
    lb = client.get("/leaderboard/custom", params={"stat": "pts", "season_from": 2025, "season_to": 2025,
                                                   "team": "MEM", "top_n": 50}).json()
    assert any(r["player_id"] == bane[0] and r["team"] == "MEM" for r in lb["results"])
    lb = client.get("/leaderboard/custom", params={"stat": "pts", "season_from": 2025, "season_to": 2025,
                                                   "team": "ORL", "top_n": 50}).json()
    assert not any(r["player_id"] == bane[0] for r in lb["results"])
    # Player profile season table
    prof = client.get(f"/player-profile/{bane[0]}").json()
    assert next(r for r in prof["seasons"]["rows"] if r["season"] == 2025)["team_abbreviation"] == shown
    # Trade Analyzer roster: on Memphis's, not Orlando's
    mem = client.get("/trade/roster/MEM/2025").json()
    orl = client.get("/trade/roster/ORL/2025").json()
    ids = lambda d: {p["player_id"] for p in (d.get("roster") or d.get("players") or [])}  # noqa: E731
    assert bane[0] in ids(mem) and bane[0] not in ids(orl)
    # Workbench: row field, team filter and the player search's latest team
    q = client.post("/workbench/query", json={"dataset": "player_season", "entities": [bane[0]],
                                               "columns": ["pts"], "season_from": 2025, "season_to": 2025})
    assert q.status_code == 200, q.text
    assert q.json()["rows"][0]["team"] == shown
    ad = client.get("/workbench/entities", params={"kind": "player", "q": "anthony davis"}).json()["results"]
    assert next(p for p in ad if p["id"] == 203076)["team"] == "DAL"
    # Shot-making and shot value (tables that copied the season row's team)
    sm = client.get("/shots/player/Desmond Bane/shot-making", params={"player_id": bane[0]}).json()
    rows = sm.get("seasons") or sm.get("rows") or []
    assert next(r for r in rows if r["season"] == 2025)["team_abbreviation"] == shown
    # Season Similarity's query row
    sim = sim_client.get("/similarity/season-profile/Desmond Bane/2025", params={"player_id": bane[0]}).json()
    assert sim["query"]["team"] == shown
