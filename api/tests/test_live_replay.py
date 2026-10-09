"""
test_live_replay.py
====================
Round 10 step 1 (docs/LIVE_GAME.md, docs/qa/ROUND10_ISSUES.md): the replay-as-live harness (scripts/live_replay.py)
serves a finished game's ESPN summary as if it were live. Checked here on a made-up summary (offline) and on one
stored 2025-26 game (OKC-HOU, 2025-10-21, two overtimes), which comes from the daily update's cache or
live_data/replay/ and is fetched from ESPN once when neither has it (skipped when ESPN doesn't answer).

What is pinned: a cut keeps the first n plays and ESPN's win-probability entries for them; the status reads
scheduled / in progress / halftime / end of period / final with the period and clock of the last play; the scores
and line scores come from the plays and agree with ESPN's own at the final; the final cut is the stored summary
byte for byte; the box score carries every athlete's id and name but no numbers before the final;
scripts/espn_summary.py's mapping of a cut equals the mapping of the whole game for every play but the last; the
replay advances on the plays' wallclocks scaled by the speed, and under manual control when told; the server
answers ESPN's own paths so api/espn_live.py reads it unchanged, and its failure modes time out or answer 500.

Usage:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_live_replay.py
"""

import copy
import json
import os
import sys
import time

import pytest
import requests

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(os.path.dirname(_API_DIR), "scripts")
for d in (_API_DIR, _SCRIPTS):
    if d not in sys.path:
        sys.path.insert(0, d)

import espn_live  # noqa: E402
import live_replay as LR  # noqa: E402

REAL_GAME = "401809243"   # OKC 125, HOU 124 (2OT), 2025-10-21: stored as espn_401809243 in pbp_games


def _wall(minute, second=0):
    return f"2025-10-21T23:{minute:02d}:{second:02d}Z"


def _play(i, period, clock, home, away, kind="Jump Shot", wall=None, team="1", athlete="100"):
    return {"id": f"9{i:03d}", "sequenceNumber": str(i), "type": {"id": "1", "text": kind}, "text": f"play {i}",
            "awayScore": away, "homeScore": home, "period": {"number": period, "displayValue": f"{period}"},
            "clock": {"displayValue": clock}, "scoringPlay": False, "scoreValue": 0, "team": {"id": team},
            "participants": [{"athlete": {"id": athlete}}], "wallclock": wall or _wall(i), "shootingPlay": False}


def synthetic_summary():
    """A tiny two-period game with a halftime, every play on a wallclock one minute apart."""
    plays = [
        _play(0, 1, "12:00", 0, 0, kind="Jumpball"),
        _play(1, 1, "11:40", 2, 0),
        _play(2, 1, "11:10", 2, 3),
        _play(3, 1, "0.0", 2, 3, kind="End Period"),
        _play(4, 2, "12:00", 2, 3),
        _play(5, 2, "6:00", 4, 3),
        _play(6, 2, "0.0", 4, 3, kind="End Period"),
        _play(7, 3, "12:00", 4, 3),
        _play(8, 3, "5:30", 4, 6),
        _play(9, 3, "0.0", 4, 6, kind="End Period"),
        _play(10, 4, "12:00", 4, 6),
        _play(11, 4, "0.0", 7, 6, kind="End Period"),
        _play(12, 4, "0.0", 7, 6, kind="End Game"),
    ]
    wp = [{"homeWinPercentage": 0.5 + 0.01 * i, "tiePercentage": 0.0, "playId": p["id"]} for i, p in enumerate(plays)]
    athletes = [{"active": True, "athlete": {"id": "100", "displayName": "Home Player"}, "starter": True,
                 "stats": ["30", "7", "3-5", "1-2", "0-0", "4", "2", "1", "0", "0", "1", "3", "2", "+1"]}]
    return {
        "header": {
            "id": "900001", "uid": "s:40~l:46~e:900001", "timeValid": True,
            "season": {"year": 2026, "type": 2},
            "competitions": [{
                "id": "900001", "date": "2025-10-21T23:30Z", "neutralSite": False,
                "liveAvailable": False, "playByPlaySource": "full", "boxscoreSource": "full", "boxscoreAvailable": True,
                "status": {"type": {"id": "3", "name": "STATUS_FINAL", "state": "post", "completed": True,
                                    "description": "Final", "detail": "Final", "shortDetail": "Final"}},
                "competitors": [
                    {"id": "1", "homeAway": "home", "order": 0, "winner": True, "score": "7",
                     "record": [{"type": "total", "summary": "1-0"}],
                     "linescores": [{"displayValue": "2"}, {"displayValue": "2"}, {"displayValue": "0"}, {"displayValue": "3"}],
                     "team": {"id": "1", "abbreviation": "OKC", "location": "Oklahoma City", "name": "Thunder", "displayName": "Oklahoma City Thunder"}},
                    {"id": "2", "homeAway": "away", "order": 1, "winner": False, "score": "6",
                     "record": [{"type": "total", "summary": "0-1"}],
                     "linescores": [{"displayValue": "3"}, {"displayValue": "0"}, {"displayValue": "3"}, {"displayValue": "0"}],
                     "team": {"id": "2", "abbreviation": "HOU", "location": "Houston", "name": "Rockets", "displayName": "Houston Rockets"}},
                ],
            }],
        },
        "boxscore": {"players": [{"team": {"id": "1", "abbreviation": "OKC"}, "statistics": [
            {"names": ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK", "OREB", "DREB", "PF", "+/-"],
             "athletes": athletes, "totals": ["240", "7"]}]}], "teams": []},
        "gameInfo": {"venue": {"id": "1", "fullName": "Paycom Center", "address": {"city": "Oklahoma City", "state": "OK"}}},
        "plays": plays,
        "winprobability": wp,
        "leaders": [{"team": {"id": "1"}, "leaders": [{"name": "points", "leaders": [{"displayValue": "7"}]}]}],
        "againstTheSpread": [{"team": {"id": "1"}, "records": []}],
        "pickcenter": [{"provider": {"name": "Draft Kings"}, "spread": -3.5, "overUnder": 220.5}],
        "meta": {"gameState": "post", "lastUpdatedAt": _wall(12), "firstPlayWallClock": _wall(0), "lastPlayWallClock": _wall(12)},
        "wallclockAvailable": True,
    }


def _status(c):
    return c["header"]["competitions"][0]["status"]


def _scores(c):
    return {x["homeAway"]: x.get("score") for x in c["header"]["competitions"][0]["competitors"]}


# ─── Cuts ───────────────────────────────────────────────────────────────────

def test_cut_keeps_the_first_n_plays_and_their_win_probability():
    s = synthetic_summary()
    for n in (0, 1, 5, 12, 13):
        c = LR.cut(s, n)
        assert [p["id"] for p in c.get("plays", [])] == [p["id"] for p in s["plays"][:n]]   # no `plays` key before tip
        assert [w["playId"] for w in c["winprobability"]] == [p["id"] for p in s["plays"][:n]]
    assert LR.cut(s, 99)["plays"] == s["plays"]          # clamped
    assert "plays" not in LR.cut(s, -4)                  # clamped to the pre-tip cut
    assert s["plays"] == synthetic_summary()["plays"]    # the stored summary is never modified


def test_status_reads_scheduled_in_progress_halftime_end_of_period_and_final():
    s = synthetic_summary()
    before = _status(LR.cut(s, 0))
    assert before["type"]["name"] == "STATUS_SCHEDULED" and before["type"]["state"] == "pre"
    assert _scores(LR.cut(s, 0)) == {"home": None, "away": None}
    q1 = _status(LR.cut(s, 3))
    assert (q1["type"]["name"], q1["type"]["state"], q1["period"], q1["displayClock"]) == ("STATUS_IN_PROGRESS", "in", 1, "11:10")
    assert q1["type"]["shortDetail"] == "11:10 - 1st" and q1["clock"] == 670.0
    end1 = _status(LR.cut(s, 4))
    assert end1["type"]["name"] == "STATUS_END_PERIOD" and end1["type"]["shortDetail"] == "End of 1st"
    half = _status(LR.cut(s, 7))
    assert half["type"]["name"] == "STATUS_HALFTIME" and half["type"]["detail"] == "Halftime" and half["period"] == 2
    final = _status(LR.cut(s, 13))
    assert final["type"]["name"] == "STATUS_FINAL" and final["type"]["completed"] is True
    # api/espn_live.py reads every one of these the way it reads ESPN's
    assert espn_live._status(LR.cut(s, 3)["header"]["competitions"][0])[:2] == ("LIVE", "Q1 11:10")
    assert espn_live._status(LR.cut(s, 7)["header"]["competitions"][0])[:2] == ("LIVE", "Halftime")
    assert espn_live._status(LR.cut(s, 13)["header"]["competitions"][0])[0] == "FINAL"
    assert espn_live._status(LR.cut(s, 0)["header"]["competitions"][0])[0] == "SCHEDULED"


def test_scores_and_line_scores_come_from_the_plays():
    s = synthetic_summary()
    c = LR.cut(s, 9)     # after the 3rd period's "5:30" play: 4-6
    assert _scores(c) == {"home": "4", "away": "6"}
    lines = {x["homeAway"]: [l["displayValue"] for l in x["linescores"]] for x in c["header"]["competitions"][0]["competitors"]}
    assert lines == {"home": ["2", "2", "0"], "away": ["3", "0", "3"]}
    assert all("winner" not in x for x in c["header"]["competitions"][0]["competitors"])
    home, away = LR.line_scores(s["plays"])
    assert home == [2, 2, 0, 3] and away == [3, 0, 3, 0]


def test_final_cut_is_the_stored_summary_and_earlier_cuts_hide_the_blocks_a_cut_cannot_rebuild():
    s = synthetic_summary()
    assert json.dumps(LR.cut(s, 13), sort_keys=True) == json.dumps(s, sort_keys=True)
    c = LR.cut(s, 6)
    assert c["leaders"] == [{"team": {"id": "1"}, "leaders": []}]   # the per-team shell ESPN serves before tip
    assert c["againstTheSpread"] == s["againstTheSpread"]           # an empty shell in every state (HOU-DAL 2026-10-09)
    assert c["pickcenter"] == s["pickcenter"]                     # the pre-game line is known before tip
    assert c["meta"]["gameState"] == "in" and c["meta"]["lastUpdatedAt"] == s["plays"][5]["wallclock"]
    pre = LR.cut(s, 0)
    assert pre["meta"]["gameState"] == "pre" and "lastUpdatedAt" not in pre["meta"]
    assert "plays" not in pre and "players" not in pre["boxscore"] and pre["winprobability"] == []
    assert pre["boxscore"]["teams"] == s["boxscore"]["teams"]
    athlete = c["boxscore"]["players"][0]["statistics"][0]["athletes"][0]
    assert athlete["athlete"] == {"id": "100", "displayName": "Home Player"} and athlete["stats"] == []
    assert c["boxscore"]["players"][0]["statistics"][0]["totals"] == []
    comp = c["header"]["competitions"][0]
    assert comp["boxscoreSource"] == "none" and comp["liveAvailable"] is True


# ─── Timing ─────────────────────────────────────────────────────────────────

def test_replay_advances_on_the_wallclocks_scaled_by_the_speed():
    t = [0.0]
    r = LR.Replay(synthetic_summary(), speed=60.0, clock=lambda: t[0])   # one wallclock minute a second
    assert r.n_now() == 1                       # the first play is on the floor at once
    t[0] = 2.0
    assert r.n_now() == 3                       # plays at +0, +1, +2 minutes
    t[0] = 5.5
    assert r.n_now() == 6
    t[0] = 100
    assert r.n_now() == 13 and r.status()["status"] == "STATUS_FINAL"


def test_replay_manual_control_and_resume():
    t = [0.0]
    r = LR.Replay(synthetic_summary(), speed=60.0, paused=True, start_play=4, clock=lambda: t[0])
    assert r.n_now() == 4 and r.status()["manual"] is True
    t[0] = 50
    assert r.n_now() == 4                       # paused: time passing changes nothing
    r.advance(3)
    assert r.n_now() == 7 and r.status()["status"] == "STATUS_HALFTIME"
    r.resume(speed=60.0)
    t[0] = 51.5                                 # 90 wallclock seconds later: the plays at +0 and +60 s
    assert r.n_now() == 9 and r.status()["manual"] is False
    r.pause()
    t[0] = 500
    assert r.n_now() == 9


def test_replay_without_wallclocks_uses_the_pace():
    s = synthetic_summary()
    for p in s["plays"]:
        p.pop("wallclock")
    t = [0.0]
    r = LR.Replay(s, speed=1.0, pace=10.0, clock=lambda: t[0])
    assert r.n_now() == 1 and r.status()["wallclock"] is False
    t[0] = 25
    assert r.n_now() == 3


# ─── The server ─────────────────────────────────────────────────────────────

@pytest.fixture
def server():
    srv = LR.serve([LR.Replay(synthetic_summary(), paused=True, start_play=5)], port=0)
    yield srv
    srv.stop()


def test_server_answers_espn_paths_and_espn_live_reads_it_unchanged(server, monkeypatch):
    sb = requests.get(server.scoreboard_url, params={"dates": "20251021"}, timeout=3).json()
    assert [e["id"] for e in sb["events"]] == ["900001"]
    assert requests.get(server.scoreboard_url, params={"dates": "20251022"}, timeout=3).json()["events"] == []
    summ = requests.get(server.summary_url, params={"event": "900001"}, timeout=3).json()
    assert len(summ["plays"]) == 5 and _status(summ)["type"]["name"] == "STATUS_IN_PROGRESS"
    assert requests.get(server.summary_url, params={"event": "1"}, timeout=3).status_code == 404
    monkeypatch.setattr(espn_live, "SCOREBOARD_URL", server.scoreboard_url)
    monkeypatch.setattr(espn_live, "SUMMARY_URL", server.summary_url)
    espn_live.clear_cache()
    games = espn_live.scoreboard("2025-10-21")
    assert len(games) == 1 and games[0]["status"] == "LIVE" and games[0]["status_text"] == "Q2 12:00"
    assert (games[0]["home"]["abbr"], games[0]["home"]["score"], games[0]["away"]["abbr"], games[0]["away"]["score"]) == ("OKC", 2, "HOU", 3)
    assert games[0]["kind"] == "Regular season" and games[0]["venue"] == "Paycom Center"
    box = espn_live.boxscore("900001")
    assert box["status"] == "LIVE" and box["teams"]["home"]["abbr"] == "OKC"
    espn_live.clear_cache()


def test_server_control_moves_the_replay(server):
    base = server.base_url
    st = requests.get(base + "/__replay/control", params={"event": "900001", "advance": "2"}, timeout=3).json()
    assert st["n"] == 7 and st["status"] == "STATUS_HALFTIME"
    st = requests.get(base + "/__replay/control", params={"event": "900001", "set": "13"}, timeout=3).json()
    assert st["status"] == "STATUS_FINAL"
    assert requests.get(base + "/__replay/status", timeout=3).json()["900001"]["n"] == 13
    summ = requests.get(server.summary_url, params={"event": "900001"}, timeout=3).json()
    assert summ["header"]["competitions"][0]["competitors"][0]["winner"] is True


def test_server_failure_modes(server):
    base = server.base_url
    assert requests.get(base + "/__replay/fail", params={"mode": "500"}, timeout=3).json() == {"fail": "500"}
    assert requests.get(server.summary_url, params={"event": "900001"}, timeout=3).status_code == 500
    assert requests.get(server.scoreboard_url, params={"dates": "20251021"}, timeout=3).status_code == 500
    requests.get(base + "/__replay/fail", params={"mode": "timeout", "seconds": "2"}, timeout=3)
    t0 = time.time()
    with pytest.raises(requests.exceptions.ReadTimeout):
        requests.get(server.summary_url, params={"event": "900001"}, timeout=0.5)
    assert time.time() - t0 < 2
    assert requests.get(base + "/__replay/fail", params={"mode": "none"}, timeout=3).json() == {"fail": "none"}
    assert requests.get(server.summary_url, params={"event": "900001"}, timeout=3).status_code == 200
    assert requests.get(base + "/__replay/fail", params={"mode": "bogus"}, timeout=3).status_code == 400


# ─── A real game ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def real_game():
    try:
        s = LR.summary_source(REAL_GAME, fetch=False)
        if s is None:
            s = LR.summary_source(REAL_GAME)
    except Exception as exc:
        pytest.skip(f"ESPN's summary of {REAL_GAME} isn't cached and couldn't be fetched: {exc}")
    return s


def test_real_game_cuts_agree_with_espn_and_map_like_the_whole_game(real_game):
    import espn_summary as ES
    s = real_game
    plays = s["plays"]
    assert len(plays) == 595 and max(p["period"]["number"] for p in plays) == 6
    assert len(s["winprobability"]) == len(plays)
    assert all(p.get("wallclock") for p in plays)
    assert json.dumps(LR.cut(s, len(plays)), sort_keys=True) == json.dumps(s, sort_keys=True)
    home, away = LR.line_scores(plays)
    espn_lines = {c["homeAway"]: [int(l["displayValue"]) for l in c["linescores"]] for c in s["header"]["competitions"][0]["competitors"]}
    assert home == espn_lines["home"] and away == espn_lines["away"]
    ends = [i for i, p in enumerate(plays) if p["type"]["text"] == "End Period"]
    names = [_status(LR.cut(s, i + 1))["type"]["name"] for i in ends]
    assert names == ["STATUS_END_PERIOD", "STATUS_HALFTIME"] + ["STATUS_END_PERIOD"] * 4
    assert _status(LR.cut(s, len(plays)))["type"]["shortDetail"] == "Final/2OT"

    class NoMatch:
        def match(self, name, season):
            return None, name

    for n in (1, 120, 300, 594):
        c = LR.cut(s, n)
        game_row, rows, _ = ES.event_rows(c, 2026, NoMatch())
        _, full_rows, _ = ES.event_rows(s, 2026, NoMatch())
        # the latest play has no end time until the next play arrives, unless it is the first play of the game
        # or of a period (hoopR's fixed ends: 2880, 2160, 1440, 720, 300)
        mapped = n if (n == 1 or plays[n - 1]["period"]["number"] != plays[n - 2]["period"]["number"]) else n - 1
        assert len(rows) == mapped
        assert rows == full_rows[:mapped]
        assert game_row[:5] == ("espn_401809243", 2026, game_row[2], "OKC", "HOU")
        st = _status(c)
        assert st["type"]["state"] == "in" and st["period"] == plays[n - 1]["period"]["number"]
