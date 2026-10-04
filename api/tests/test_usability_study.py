"""
test_usability_study.py
=======================
Guards round 7 step 10, the usability study kit (docs/USABILITY_STUDY.md):

  * the tasks (frontend/src/utils/studyTasks.json) are answerable from the
    data as it is: every stored answer, the finder's player count for task 2,
    the latest season the table check assumes and the shot chart of task 4
    are re-derived from the database;
  * the grader (scripts/usability_summary.py) passes every task on the boards
    the app really produced in the developer's pilot (P0, 2026-10-04: the same
    sets and block settings, member names and layout left out), and fails
    each task on the near misses a participant could leave (default seasons,
    wrong axes, wrong season, no share, no type-in) with a reason;
  * SUS scoring, answer matching, and the report: pilots left out by default,
    one file per participant, sessions of another task set refused.

Skips when the database is unreachable.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_usability_study.py
"""

import copy
import json
import os
import sys

import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API)
for _d in (_API, os.path.join(_ROOT, "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")


@pytest.fixture(scope="module")
def S():
    import usability_summary
    return usability_summary


@pytest.fixture(scope="module")
def study(S):
    return S.load_tasks()


@pytest.fixture(scope="module")
def truth(S, study):
    return S.truths(study)


# ─── The pilot's boards (P0, 2026-10-04, developer) ─────────────────────────

FOUND = [1629029, 203954, 1628983, 203081, 203507, 1628369, 203999, 201939, 201142, 1630162, 1628378, 202695,
         1626164, 202681, 1628374, 1628973, 203076, 2544, 1627750, 1641705, 203897, 1626157, 1630166, 1629639,
         201935, 1641718, 1630559, 204001, 202710, 1629627, 202331, 1626181, 1627783, 202711, 1630217, 1630530,
         1629638, 1630169, 1629636, 203924, 1629632, 1630178]

T1_SET = {"id": "s1", "name": "Players 1", "kind": "player", "members": [{"id": 203999}, {"id": 203954}]}
T1_TABLE = {"id": "b2", "type": "table", "settings": {
    "dataset": "player_season", "setId": "s1", "columns": ["pts", "reb", "ast", "ts_pct"], "seasonFrom": 2021,
    "seasonTo": None, "groupBy": "none", "per": "game", "sort": [], "limit": 50}}
T2_SET = {"id": "s2", "name": "Efficient scorers", "kind": "player", "members": [{"id": i} for i in FOUND]}
T2_CHART = {"id": "b5", "type": "chart", "settings": {
    "dataset": "player_season", "setId": "s2", "seasonFrom": None, "seasonTo": None, "per": "game", "minGames": 20,
    "chart": "scatter", "x": "usg_pct", "y": "ts_pct", "color": "member", "context": True, "trend": False}}
T4_TOOL = {"id": "b7", "type": "tool", "settings": {"tool": "shots", "setId": "s2", "member": 201939, "season": 2016,
                                                     "view": None, "games": None}}
T6_FINDER = {"id": "b3", "type": "finder", "settings": {
    "dataset": "player_season", "scope": "season", "seasonFrom": None, "seasonTo": None, "minGames": 40,
    "conditions": [{"type": "value", "stat": "reb", "op": "gte", "value": 10, "value2": None, "per": "game", "minN": None},
                   {"type": "value", "stat": "ast", "op": "gte", "value": 10, "value2": None, "per": "game", "minN": None}]}}


def board(sets, blocks):
    return [{"id": "board", "name": "My board", "sets": sets,
             "blocks": [{"id": "b1", "type": "set", "settings": {"setId": s["id"]}} for s in sets] + blocks}]


def record(key, boards, start, end, **over):
    r = {"key": key, "started": start, "ended": end, "endedBy": "done", "seconds": round((end - start) / 1000),
         "hints": 0, "stuck": [], "answer": "", "outcome": "success", "seq": 6, "boards": boards}
    r.update(over)
    return r


def pilot_session(participant="P0", pilot=True):
    t0 = 1791098253733
    b1 = board([T1_SET], [T1_TABLE])
    b2 = board([T1_SET, T2_SET], [T1_TABLE, T2_CHART])
    b4 = board([T1_SET, T2_SET], [T1_TABLE, T2_CHART, T4_TOOL])
    b6 = board([T1_SET, T2_SET], [T1_TABLE, T2_CHART, T4_TOOL, T6_FINDER])
    tasks = [
        record("t1", b1, t0, t0 + 82731, answer="2025-26"),
        record("t2", b2, t0 + 105758, t0 + 171692, seq=5),
        record("t3", b2, t0 + 174002, t0 + 201316, answer="8", seq=5),
        record("t4", b4, t0 + 203632, t0 + 225675),
        record("t5", b4, t0 + 227982, t0 + 237710, seq=7),
        record("t6", b6, t0 + 240022, t0 + 259836),
    ]
    events = [
        {"t": t0 + 228496, "task": "t5", "type": "share", "ok": False, "chars": 2104, "tooBig": True},
        {"t": t0 + 230000, "task": "t5", "type": "export", "all": False},
        {"t": t0 + 243021, "task": "t6", "type": "parse", "ok": True, "text": "players who averaged at least 10 rebounds and 10 assists a game in a season"},
        {"t": t0 + 256325, "task": "t6", "type": "find", "conditions": 2},
    ]
    return copy.deepcopy({"format": "nba-hub-usability", "version": 1, "id": f"{participant}-x", "participant": participant,
            "pilot": pilot, "taskSet": "workbench-2026-10-04", "taskKeys": [t["key"] for t in tasks],
            "device": {"width": 1280, "height": 900, "touch": False, "theme": "paper"},
            "started": "2026-10-04T07:17:24.023Z", "ended": "2026-10-04T07:22:05.200Z",
            "tasks": tasks, "events": events, "sus": None, "notes": ""})


def by_key(study):
    return {t["key"]: t for t in study["tasks"]}


# ─── The tasks match the data ───────────────────────────────────────────────

def test_task_file_is_well_formed(study):
    keys = [t["key"] for t in study["tasks"]]
    assert len(keys) == len(set(keys)) and study["taskSet"]
    assert {t["check"]["kind"] for t in study["tasks"]} <= {"table", "finder_chart", "answer", "tool", "event", "parse_finder"}
    for t in study["tasks"]:
        assert t["text"] and t["hint"] and t["success"]
        assert bool(t.get("question")) == bool(t.get("answer"))
        if t.get("answer"):
            assert t["answer"] in t["answerAccept"]
    assert len(study["sus"]) == 10 and len({k for k, _ in study["stuckTags"]}) == len(study["stuckTags"])


def test_answers_come_from_the_data(truth, study):
    from routers.workbench import QuerySpec, workbench_query
    tasks = by_key(study)
    # t1: Jokić's assists per game by season, 2020-21 to 2025-26.
    rows = workbench_query(QuerySpec(dataset="player_season", entities=[203999], columns=["ast"],
                                     season_from=2021, season_to=2026, limit=50))["rows"]
    best = max(rows, key=lambda r: r["ast"])
    assert len(rows) == 6 and f"{best['season'] - 1}-{str(best['season'])[-2:]}" == tasks["t1"]["answer"]
    # t2 and t3 are re-derived by truths() itself (it stops when they differ).
    assert len(truth["t2"]) == tasks["t2"]["check"]["players"] == 42
    assert set(FOUND) == truth["t2"]
    assert str(truth["t3"]) == tasks["t3"]["answer"]


def test_table_check_assumes_the_real_latest_season(study):
    from routers.workbench import _dataset_meta
    assert by_key(study)["t1"]["check"]["latest"] == _dataset_meta()["player_season"]["to"]


def test_the_shot_chart_of_task_4_has_shots():
    with psycopg2.connect(**DB_CONFIG) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM player_shots WHERE player_id = 201939 AND season = '2015-16' "
                    "AND game_id LIKE '002%%'")
        n = cur.fetchone()[0]
    assert n > 1000


# ─── The grader ─────────────────────────────────────────────────────────────

def test_every_task_passes_on_the_pilots_boards(S, study, truth):
    rows = S.grade(study, [pilot_session()], truth)
    assert [r["task"] for r in rows] == ["t1", "t2", "t3", "t4", "t5", "t6"]
    for r in rows:
        assert r["completed"], (r["task"], r["check_reason"])
        assert r["check"] in (True, None)
    assert [r["answer_ok"] for r in rows if r["task"] in ("t1", "t3")] == [True, True]


def near_miss(study, truth, S, key, mutate):
    s = pilot_session()
    rec = next(t for t in s["tasks"] if t["key"] == key)
    mutate(rec, s)
    row = next(r for r in S.grade(study, [s], truth) if r["task"] == key)
    return row


def test_near_misses_fail_with_a_reason(S, study, truth):
    def default_seasons(rec, s):
        rec["boards"][0]["blocks"][1]["settings"]["seasonFrom"] = None   # = the last five seasons, from 2021-22
    def one_player(rec, s):
        rec["boards"][0]["sets"][0]["members"] = [{"id": 203999}]
    def wrong_axes(rec, s):
        rec["boards"][0]["blocks"][-1]["settings"]["y"] = "pts"
    def half_the_players(rec, s):
        rec["boards"][0]["sets"][1]["members"] = rec["boards"][0]["sets"][1]["members"][:21]
    def wrong_season(rec, s):
        rec["boards"][0]["blocks"][-1]["settings"]["season"] = 2026
    def no_share(rec, s):
        s["events"] = [e for e in s["events"] if e["task"] != "t5"]
    def no_typing(rec, s):
        s["events"] = [e for e in s["events"] if e["type"] != "parse"]
    def wrong_answer(rec, s):
        rec["answer"] = "2024-25"
    cases = [("t1", default_seasons, "no table"), ("t1", one_player, "no player set holds both"),
             ("t2", wrong_axes, "no scatter"), ("t2", half_the_players, "no set holds"),
             ("t4", wrong_season, "no shot chart"), ("t5", no_share, "no share link"),
             ("t6", no_typing, "never filled"), ("t1", wrong_answer, None)]
    for key, mutate, reason in cases:
        row = near_miss(study, truth, S, key, mutate)
        assert row["completed"] is False, (key, mutate.__name__)
        if reason:
            assert row["check"] is False and reason in row["check_reason"], (key, row["check_reason"])
        else:
            assert row["answer_ok"] is False


def test_missing_boards_fail_rather_than_pass(S, study, truth):
    s = pilot_session()
    for t in s["tasks"]:
        t["boards"] = None
    rows = {r["task"]: r for r in S.grade(study, [s], truth)}
    assert rows["t3"]["completed"] and rows["t5"]["completed"]       # no board needed
    assert not any(rows[k]["completed"] for k in ("t1", "t2", "t4", "t6"))


def test_sus_and_answers(S, study):
    assert S.sus_score([3] * 10) == 50.0
    assert S.sus_score([5, 1] * 5) == 100.0 and S.sus_score([1, 5] * 5) == 0.0
    assert S.sus_score([3] * 9) is None and S.sus_score(None) is None
    t1 = by_key(study)["t1"]
    assert S.check_answer(t1, {"answer": " 2025–26 "}) and S.check_answer(t1, {"answer": "2025/26"})
    assert not S.check_answer(t1, {"answer": "2024-25"})
    assert S.check_answer(by_key(study)["t4"], {"answer": ""}) is None


def test_report_counts_and_refusals(S, study, truth, tmp_path):
    real = pilot_session("P1", pilot=False)
    real["sus"] = [4, 2, 4, 2, 4, 2, 4, 2, 4, 2]
    real["tasks"][1]["outcome"] = "help"
    real["tasks"][1]["hints"] = 1
    real["tasks"][1]["stuck"] = [{"t": real["tasks"][1]["started"] + 5000, "tag": "results", "note": "looked for a copy button"}]
    real["tasks"][2]["answer"] = "7"           # moderator says success, the answer is wrong
    real["tasks"][5] = {"key": "t6", "outcome": "skipped", "started": None, "ended": None}
    for name, s in (("p0.json", pilot_session()), ("p1.json", real)):
        (tmp_path / name).write_text(json.dumps(s))
    sessions = S.load_sessions([str(tmp_path)])
    assert len(sessions) == 2
    out = tmp_path / "RESULTS.md"
    S.main([str(tmp_path), "--out", str(out)])
    text = out.read_text()
    assert "1 participant (P1)" in text and "pilot sessions left out: 1" in text
    assert "| t2 Find players, then plot them | 1 of 1 | 0 / 1 / 0 / 0 |" in text
    assert "| t3 Count big games | 0 of 1 |" in text and "| t6 Type it in English (optional) | 0 of 0 | 0 / 0 / 0 / 1 |" in text
    assert "Getting Finder results into a set | 1 | 1 | t2" in text and "looked for a copy button" in text
    assert "SUS 75.0" in text
    assert "P1 t3: moderator success; answer “7” isn't an accepted one" in text
    assert "Completed overall: 4 of 5 attempts." in text
    # One file per participant; sessions of another task set are refused.
    (tmp_path / "p1b.json").write_text(json.dumps(real))
    with pytest.raises(SystemExit):
        S.main([str(tmp_path)])
    (tmp_path / "p1b.json").unlink()
    other = copy.deepcopy(real)
    other["participant"], other["taskSet"] = "P2", "another-set"
    (tmp_path / "p2.json").write_text(json.dumps(other))
    with pytest.raises(SystemExit):
        S.main([str(tmp_path)])
    (tmp_path / "bad.json").write_text(json.dumps({"format": "something else"}))
    with pytest.raises(SystemExit):
        S.load_sessions([str(tmp_path / "bad.json")])
