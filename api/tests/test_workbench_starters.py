"""
test_workbench_starters.py
==========================
Guards round 7 step 8 of the Workbench:

  * the starter boards (frontend/src/utils/starterBoards.json) name only real
    things: every player id is a player on file under that name with a season
    in the board's range, every team code a franchise, every dataset, stat and
    tool key one the catalogue (or the tool list) offers, every block bound to
    a set of the board; and every table and the finder actually return rows
    (the specs are built the way tableSpec.buildSpec / finderSpec build them,
    with the board's explicit seasons);
  * their notes carry no measured number (those come from the blocks, live);
  * the server's response cache (routers/workbench.py response_cache) answers a
    repeated summary from memory with the same content, whatever the key
    order, and never keeps a refused request.

Skips when the database is unreachable.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_workbench_starters.py
"""

import json
import os
import re
import sys

import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API not in sys.path:
    sys.path.insert(0, _API)
_FRONT = os.path.join(os.path.dirname(_API), "frontend", "src", "utils")

from db_config import DB_CONFIG  # noqa: E402
from teams_lib import FRANCHISES  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")

with open(os.path.join(_FRONT, "starterBoards.json"), encoding="utf-8") as f:
    STARTERS = json.load(f)["boards"]
with open(os.path.join(_FRONT, "workbenchTools.js"), encoding="utf-8") as f:
    _tools_src = f.read()
TOOL_ENTITY = dict(re.findall(r"^\s{4}(\w+): \{ label: '[^']*', icon: '[^']*', entity: '(player|team)'", _tools_src, re.M))
BLOCK_TYPES = {"set", "table", "chart", "note", "tool", "finder"}


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from impact_api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def catalogue(client):
    r = client.get("/workbench/catalogue")
    assert r.status_code == 200
    return {d["key"]: d for d in r.json()["datasets"]}


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    yield conn.cursor()
    conn.close()


def _blocks(kind):
    return [(s, b) for s in STARTERS for b in s["blocks"] if b["type"] == kind]


def test_tool_list_parsed():
    assert set(TOOL_ENTITY) >= {"card", "shots", "rotation", "assists"}


def test_four_to_six_boards_with_unique_keys():
    keys = [s["key"] for s in STARTERS]
    assert 4 <= len(keys) <= 6 and len(set(keys)) == len(keys)
    for s in STARTERS:
        assert s["name"] and s["blurb"] and s["blocks"]
        assert len(s["name"]) <= 120
        assert {b["type"] for b in s["blocks"]} <= BLOCK_TYPES


def test_members_are_real(cur):
    for s in STARTERS:
        for st in s["sets"]:
            for m in st["members"]:
                if st["kind"] == "player":
                    cur.execute("SELECT player_name, min(season), max(season) FROM player_season_stats "
                                "WHERE player_id = %s GROUP BY 1", (m["id"],))
                    rows = cur.fetchall()
                    assert len(rows) == 1, (s["key"], m)
                    assert rows[0][0] == m["name"], (s["key"], m, rows[0][0])
                else:
                    assert m["id"] in FRANCHISES, (s["key"], m)


def test_blocks_use_their_board_sets_and_catalogue(catalogue):
    for s in STARTERS:
        sets = {x["id"]: x for x in s["sets"]}
        for b in s["blocks"]:
            st = b["settings"]
            if b["type"] in ("set", "tool"):
                assert st["setId"] in sets, (s["key"], b)
            if b["type"] == "tool":
                assert st["tool"] in TOOL_ENTITY
                assert sets[st["setId"]]["kind"] == TOOL_ENTITY[st["tool"]]
                assert any(m["id"] == st["member"] for m in sets[st["setId"]]["members"])
            if b["type"] in ("table", "chart"):
                ds = catalogue[st["dataset"]]
                ok = {c["key"] for c in ds["columns"] if c["status"] == "verified"}
                keys = st.get("columns", []) + [st.get(k) for k in ("x", "y", "size") if st.get(k)]
                assert keys and set(keys) <= ok, (s["key"], b.get("title"), set(keys) - ok)
                if st.get("setId"):
                    kind = sets[st["setId"]]["kind"]
                    assert kind == ds["entity"] or (kind == "player" and ds["players_filter"])
                lo, hi = st.get("seasonFrom"), st.get("seasonTo")
                assert lo and hi and ds["seasons"]["from"] <= lo <= hi <= ds["seasons"]["to"], (s["key"], b.get("title"))
            if b["type"] == "finder":
                ds = catalogue[st["dataset"]]
                ok = {c["key"] for c in ds["columns"] if c["status"] == "verified"}
                assert {c["stat"] for c in st["conditions"]} <= ok


def test_member_seasons_in_range(cur):
    # Each player has a season inside the range of every player table/chart bound to his set.
    for s, b in _blocks("table") + _blocks("chart"):
        st = b["settings"]
        if not st.get("setId") or st["dataset"] != "player_season":
            continue
        members = next(x for x in s["sets"] if x["id"] == st["setId"])["members"]
        for m in members:
            cur.execute("SELECT count(*) FROM player_season_stats WHERE player_id = %s AND season BETWEEN %s AND %s",
                        (m["id"], st["seasonFrom"], st["seasonTo"]))
            assert cur.fetchone()[0] > 0, (s["key"], m["name"])


def _table_spec(s, st):
    """tableSpec.buildSpec, for the settings a starter uses."""
    spec = {"dataset": st["dataset"], "columns": st["columns"], "season_from": st["seasonFrom"],
            "season_to": st["seasonTo"], "group_by": st.get("groupBy", "none"), "per": st.get("per", "game"),
            "sort": st.get("sort", []), "limit": st.get("limit", 50)}
    if st.get("setId"):
        ids = [m["id"] for m in next(x for x in s["sets"] if x["id"] == st["setId"])["members"]]
        spec["entities"] = ids
    if st.get("minGames"):
        spec["min_games"] = st["minGames"]
    if st.get("minPoss"):
        spec["min_poss"] = st["minPoss"]
    return spec


def test_every_table_returns_rows(client):
    for s, b in _blocks("table"):
        spec = _table_spec(s, b["settings"])
        r = client.post("/workbench/query", json=spec)
        assert r.status_code == 200, (s["key"], b.get("title"), r.text)
        d = r.json()
        assert d["rows"], (s["key"], b.get("title"))
        if spec.get("entities") and spec["group_by"] in ("none", "entity") and spec["season_from"] == spec["season_to"] \
                and spec["dataset"] in ("player_season", "player_onoff"):
            # one season, every member on file that season: one row each
            assert {r["player_id"] for r in d["rows"]} == set(spec["entities"]), (s["key"], b.get("title"))


def test_finder_finds_the_known_seasons(client):
    (s, b), = _blocks("finder")
    st = b["settings"]
    spec = {"dataset": st["dataset"], "scope": st["scope"], "season_from": st["seasonFrom"], "min_games": st["minGames"],
            "conditions": [{"type": "value", "stat": c["stat"], "op": c["op"], "value": c["value"], "per": c["per"]}
                           for c in st["conditions"]], "limit": st["limit"]}
    r = client.post("/workbench/finder", json=spec)
    assert r.status_code == 200, r.text
    names = {row["player_name"] for row in r.json()["rows"]}
    # ESPN regular-season averages, read 2026-10-03:
    # Jokić 2020-21: 72 GP, 26.4 PTS, 10.8 REB, 8.3 AST
    #   https://www.espn.com/nba/player/stats/_/id/3112335/nikola-jokic
    # Westbrook 2016-17: 81 GP, 10.2-24.0 FG, 2.5-7.2 3P, 8.8-10.4 FT (31.6 PTS), 10.7 REB, 10.4 AST
    #   https://www.espn.com/nba/player/stats/_/id/3468/russell-westbrook
    assert {"Nikola Jokić", "Russell Westbrook"} <= names


def test_notes_state_no_measured_number():
    for s, b in _blocks("note"):
        text = b["settings"]["text"]
        assert not re.search(r"\d+\.\d", text), (s["key"], text)


def test_response_cache(client):
    import routers.workbench as W
    body = {"spec": {"dataset": "team_season", "columns": ["n_rtg"], "season_from": 2020, "season_to": 2026},
            "column": "n_rtg", "by": "season"}
    reordered = {"column": "n_rtg", "by": "season",
                 "spec": {"season_to": 2026, "season_from": 2020, "columns": ["n_rtg"], "dataset": "team_season",
                          "group_by": "none"}}
    first = client.post("/workbench/context", json=body)
    size = len(W._responses)
    again = client.post("/workbench/context", json=reordered)
    assert first.status_code == again.status_code == 200
    assert first.content == again.content
    assert len(W._responses) == size          # answered from the cache, nothing added
    bad = {**body, "column": "not_a_column"}
    assert client.post("/workbench/context", json=bad).status_code == 400
    assert client.post("/workbench/context", json=bad).status_code == 400
    assert len(W._responses) == size          # a refusal is never kept
    assert W.RESPONSE_CACHE_SIZE >= len(W._responses)
