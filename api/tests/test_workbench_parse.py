"""
test_workbench_parse.py
=======================
Guards round 7 step 9, the Workbench's "type it in English" box
(POST /workbench/parse, api/routers/workbench_parse.py; the work in
api/workbench_parse_lib.py). No test calls Google: the model's answer is
faked, so these check what the server does with whatever comes back.

  * what the model may answer with comes from the catalogue: every stat key
    in the answer schema is a verified finder stat and every verified finder
    stat is in it; the opponent box's codes are today's 30 franchises;
  * the model's answer is untrusted input: unknown or injection-shaped stats,
    filters that need game logs on season stats, seasons outside the data,
    combined values of one-per-season stats and runs without a length are
    left out and each one is said; percentages typed out of 100 become
    shares; the finder's defaults (40 games, the attempts floor) are the
    block's; what comes out compiles with the finder's own compiler;
  * the endpoint: an answer → spec + the finder's sentence + notes; no key →
    503 saying so; nothing usable → 422 with what wasn't understood; a typed
    text that's empty or too long → refused; the per-minute guard → 429;
    today's answers come from memory without a second call;
  * the test sentences (≥ 50 held out, every expected answer compiles) and
    the stored evaluation (same sentences, the app's model);
  * the key is in no file git would commit.

Skips when the database is unreachable.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_workbench_parse.py
"""

import json
import os
import subprocess
import sys

import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API not in sys.path:
    sys.path.insert(0, _API)

from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")


@pytest.fixture(scope="module")
def P():
    import workbench_parse_lib
    return workbench_parse_lib


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from impact_api import app
    return TestClient(app)


@pytest.fixture()
def router(monkeypatch):
    from routers import workbench_parse as R
    monkeypatch.setattr(R, "_calls", type(R._calls)())
    monkeypatch.setattr(R, "_answers", type(R._answers)())
    return R


def intent(**over):
    """A well-formed answer in intent_schema's shape (the model's side)."""
    base = {"dataset": "player_season", "scope": "season", "season_from": None, "season_to": None,
            "min_games": None, "home": "any", "result": "any", "opponent": None, "min_minutes": None,
            "conditions": [], "not_understood": []}
    base.update(over)
    return base


def value(stat, op="gte", v=None, **over):
    c = {"type": "value", "stat": stat, "per": "game", "op": op, "value": v, "value2": None,
         "min_attempts": None, "tests": [], "count_op": "gte", "count": None}
    c.update(over)
    return c


def counted(kind, tests, count, count_op="gte"):
    return {"type": kind, "stat": None, "per": "game", "op": "gte", "value": None, "value2": None,
            "min_attempts": None, "tests": [{"stat": s, "op": o, "value": v, "value2": None} for s, o, v in tests],
            "count_op": count_op, "count": count}


# ─── what the model may answer with ─────────────────────────────────────────

def test_schema_keys_are_the_catalogues(P):
    import workbench_catalogue as WC
    schema = P.intent_schema()
    stats = set(schema["properties"]["conditions"]["items"]["properties"]["tests"]["items"]["properties"]["stat"]["enum"])
    verified = {c.key for k in P.FINDER_DATASETS for c in WC.DATASETS[k].columns.values() if c.status == "verified"}
    assert stats == verified
    excluded = {c.key for k in P.FINDER_DATASETS for c in WC.DATASETS[k].columns.values() if c.status != "verified"}
    assert excluded and not (excluded & (stats - verified))
    cond_stats = set(schema["properties"]["conditions"]["items"]["properties"]["stat"]["enum"]) - {None}
    assert cond_stats == verified
    codes = [c for c in schema["properties"]["opponent"]["enum"] if c]
    assert len(codes) == 30 and "BOS" in codes and "NOP" in codes
    prompt = P.system_prompt()
    for k in verified:
        assert f"  {k}: " in prompt, k


# ─── the model's answer is untrusted input ──────────────────────────────────

def test_a_good_answer_becomes_the_finders_spec(P):
    spec, sentence, dropped = P.to_spec(intent(season_from=2023, conditions=[
        value("pts", v=25), value("ts_pct", v=60)]))
    assert dropped == []
    assert spec["dataset"] == "player_season" and spec["scope"] == "season"
    assert spec["season_from"] == 2023 and spec["season_to"] is None     # None = the latest on file
    assert spec["min_games"] == 40                                         # the block's default games floor
    pts, ts = spec["conditions"]
    assert pts == {"type": "value", "stat": "pts", "op": "gte", "value": 25, "per": "game"}
    assert ts["value"] == 0.6 and ts["min_n"] == 200                       # 60% → 0.6; 5 FGA a game × 40 games
    assert sentence.startswith("Players who, in a single season from 2022-23 to")
    assert "true shooting % of at least 60% (on at least 200 shooting attempts)" in sentence


def test_unknown_and_injection_shaped_stats_are_left_out_and_said(P):
    spec, _s, dropped = P.to_spec(intent(conditions=[
        value("pts; DROP TABLE player_season_stats; --", v=20), value("ast", v=8)]))
    assert [c["stat"] for c in spec["conditions"]] == ["ast"]
    assert any("Condition 1" in d and "no stat" in d for d in dropped)
    with pytest.raises(P.ParseError) as e:
        P.to_spec(intent(conditions=[value("1=1", v=3)]))
    assert e.value.status == 422 and e.value.dropped


def test_game_only_boxes_on_season_stats_are_said(P):
    spec, _s, dropped = P.to_spec(intent(home="away", opponent="BOS", conditions=[value("pts", v=30)]))
    assert spec["filters"] == []
    assert any("only game logs know single games" in d for d in dropped)
    spec, _s, dropped = P.to_spec(intent(dataset="player_game", home="away", opponent="BOS", result="wins",
                                         min_minutes=30, conditions=[value("pts", v=30)]))
    assert dropped == []
    assert sorted((f["key"], f["value"]) for f in spec["filters"]) == [
        ("home", False), ("min", 30), ("opponent", "BOS"), ("result", True)]
    _sp, _s, dropped = P.to_spec(intent(dataset="player_game", opponent="ZZZ", conditions=[value("pts", v=30)]))
    assert any("ZZZ" in d for d in dropped)


def test_seasons_outside_the_data_are_said(P):
    spec, _s, dropped = P.to_spec(intent(dataset="player_game", season_from=2015, season_to=2016,
                                         conditions=[counted("count", [("pts", "gte", 50)], 1)]))
    assert spec["season_from"] is None and spec["season_to"] is None
    assert any("2014-15 to 2015-16" in d and "2020-21" in d for d in dropped)
    spec, _s, dropped = P.to_spec(intent(season_from=2030, season_to=2024, conditions=[value("pts", v=20)]))
    assert (spec["season_from"], spec["season_to"]) == (2024, None) and dropped == []   # swapped, then clamped
    # One season: "combined" and "in a single season" are the same search; the boxes say the latter.
    for lo, hi in ((2023, 2023), (2026, 2026)):
        spec, sentence, _d = P.to_spec(intent(dataset="player_game", scope="span", season_from=lo, season_to=hi,
                                              conditions=[counted("count", [("reb", "gte", 20)], 3)]))
        assert spec["scope"] == "season" and sentence.startswith(f"Players who, in {lo - 1}-{str(lo)[-2:]},")


def test_rules_of_the_finder_hold(P):
    # A one-per-season stat can't be combined over seasons.
    with pytest.raises(P.ParseError):
        P.to_spec(intent(scope="span", conditions=[value("rapm", v=3)]))
    # per36 on a rate means nothing; per100 on minutes isn't offered.
    spec, _s, dropped = P.to_spec(intent(conditions=[value("ts_pct", v=55, per="per36"), value("min", v=30, per="per100")]))
    assert [c["per"] for c in spec["conditions"]] == ["game", "game"]
    assert any("Minutes" in d and "per 100" in d for d in dropped)
    # A run needs its length; a count without one is "at least once".
    spec, _s, dropped = P.to_spec(intent(dataset="player_game", scope="span", conditions=[
        counted("streak", [("pts", "gte", 20)], None), counted("count", [("pts", "gte", 50)], None)]))
    assert [(c["type"], c["count"]) for c in spec["conditions"]] == [("count", 1)]
    assert any("a run needs its length" in d for d in dropped)
    # between keeps its ends in order; a % between is two shares.
    spec, _s, _d = P.to_spec(intent(conditions=[value("fg3_pct", op="between", v=42, value2=36)]))
    assert spec["conditions"][0]["value"] == [0.36, 0.42]
    # A count or run test in game logs, three tests in the same game.
    spec, sentence, _d = P.to_spec(intent(dataset="player_game", conditions=[
        counted("count", [("pts", "gte", 10), ("reb", "gte", 10), ("ast", "gte", 10)], 10)]))
    assert spec["min_games"] is None and "in the same game" in sentence


def test_shares_typed_as_shares_are_kept_and_said(P):
    spec, _s, dropped = P.to_spec(intent(conditions=[value("fg3_pct", v=0.4)]))
    assert spec["conditions"][0]["value"] == 0.4
    assert any("read 0.4 as 40%" in d for d in dropped)


# ─── the endpoint ───────────────────────────────────────────────────────────

def test_endpoint_returns_the_spec_and_the_finders_sentence(P, client, router, monkeypatch):
    calls = []

    def fake(text, system, schema, model=P.MODEL, key=None, timeout=P.TIMEOUT_S):
        calls.append(text)
        return intent(conditions=[value("pts", v=25)], not_understood=["guards"]), {"usage": {}, "model_version": "fake"}

    monkeypatch.setattr(P, "ask_gemini", fake)
    r = client.post("/workbench/parse", json={"text": "  Guards who averaged 25 points  in a season "})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["text"] == "Guards who averaged 25 points in a season"
    assert d["spec"]["conditions"][0]["stat"] == "pts" and d["not_understood"] == ["guards"]
    assert d["sentence"].startswith("Players who") and d["_source"]["tables"]
    assert "intent" not in d
    # The same sentence today comes from memory.
    r = client.post("/workbench/parse", json={"text": "guards who averaged 25 points in a season"})
    assert r.status_code == 200 and len(calls) == 1
    # A finder spec that the finder itself runs.
    spec = {k: v for k, v in d["spec"].items() if v is not None}
    assert client.post("/workbench/finder", json=spec).status_code == 200


def test_endpoint_refusals(P, client, router, monkeypatch):
    monkeypatch.setattr(P, "api_key", lambda: None)
    r = client.post("/workbench/parse", json={"text": "who averaged 30 points"})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]["message"]
    s = client.get("/workbench/parse/status").json()
    assert s["available"] is False and "Google" in s["sends"]
    assert client.post("/workbench/parse", json={"text": "   "}).status_code == 400
    assert client.post("/workbench/parse", json={"text": "x" * 301}).status_code == 400
    assert client.post("/workbench/parse", json={"text": "x", "sql": "SELECT 1"}).status_code == 422

    monkeypatch.setattr(P, "api_key", lambda: "test-key")
    monkeypatch.setattr(P, "ask_gemini", lambda *a, **k: (intent(conditions=[], not_understood=["or"]), {"usage": {}, "model_version": None}))
    r = client.post("/workbench/parse", json={"text": "who averaged 25 points or 10 rebounds"})
    assert r.status_code == 422
    assert r.json()["detail"]["not_understood"] == ["or"]


def test_per_minute_guard(P, client, router, monkeypatch):
    monkeypatch.setattr(router, "PER_MINUTE", 2)
    monkeypatch.setattr(P, "ask_gemini", lambda *a, **k: (intent(conditions=[value("ast", v=9)]), {"usage": {}, "model_version": None}))
    codes = [client.post("/workbench/parse", json={"text": f"who averaged {n} assists"}).status_code for n in (7, 8, 9)]
    assert codes == [200, 200, 429]


def test_google_errors_become_plain_messages(P, monkeypatch):
    import requests

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._body = status, body

        def json(self):
            return self._body

    def post_returning(status, body):
        return lambda *a, **k: Resp(status, body)

    cases = [
        (429, {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}},
         429, "midnight Pacific"),
        (429, {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
                                     {"retryDelay": "31s"}]}}, 429, "about 31 s"),
        (400, {"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid.",
                         "details": [{"reason": "API_KEY_INVALID"}]}}, 503, "refused the server's Gemini key"),
        (200, {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}, 502, "SAFETY"),
        (200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{not json"}]}}]}, 502, "valid JSON"),
        (500, {"error": {"message": "boom secret-key"}}, 502, "boom …"),
    ]
    for status, body, want, words in cases:
        monkeypatch.setattr(requests, "post", post_returning(status, body))
        with pytest.raises(P.ParseError) as e:
            P.ask_gemini("x", "s", {}, key="secret-key")
        assert e.value.status == want and words in str(e.value), (status, str(e.value))

    def timeout(*a, **k):
        raise requests.Timeout()
    monkeypatch.setattr(requests, "post", timeout)
    with pytest.raises(P.ParseError) as e:
        P.ask_gemini("x", "s", {}, key="k")
    assert e.value.status == 504


# ─── the test sentences and the stored evaluation ───────────────────────────

def test_sentences_were_written_to_be_scored(P):
    d = json.load(open(os.path.join(_API, "workbench_parse_sentences.json")))
    s = d["sentences"]
    assert len({x["id"] for x in s}) == len(s)
    test = [x for x in s if x["split"] == "test"]
    assert len(test) >= 50 and len([x for x in s if x["split"] == "dev"]) >= 30
    assert sum(x["unsupported"] for x in test) >= 5
    for x in s:
        if x["expect"]["conditions"] is not None:
            P.finalize(x["expect"])          # every expected answer is a spec the finder accepts


def test_stored_evaluation_is_of_these_sentences(P):
    path = os.path.join(_API, "workbench_parse_eval.json")
    if not os.path.exists(path):
        pytest.skip("not evaluated yet (scripts/workbench_parse_eval.py --split test --write)")
    ev = json.load(open(path))
    s = json.load(open(os.path.join(_API, "workbench_parse_sentences.json")))
    test = {x["id"]: x["text"] for x in s["sentences"] if x["split"] == "test"}
    assert ev["split"] == "test" and ev["model"] == P.MODEL and ev["n"] == len(test)
    assert ev["sentences_written"] == s["written"] <= ev["evaluated"]
    for run in ev["runs_detail"]:
        assert {r["id"]: r["text"] for r in run["results"]} == test
        assert run["summary"]["all_right"] == sum(r["all_right"] for r in run["results"])
    assert ev["all_right_by_run"] == [r["summary"]["all_right"] for r in ev["runs_detail"]]


def test_stored_evaluation_stays_current_after_its_day(P, router, monkeypatch):
    # The prompt names today's date, so its hash changes every day; the page
    # must not call the stored evaluation "an earlier version of the prompt"
    # the morning after (found 2026-10-04, round 7 step 10's pilot).
    import datetime
    ev = {"evaluated": "2026-10-03", "model": P.MODEL, "prompt_version": P.prompt_version(datetime.date(2026, 10, 3))}
    assert P.prompt_version(datetime.date(2026, 10, 3)) != P.prompt_version(datetime.date(2026, 10, 4))
    real = P.latest_seasons
    monkeypatch.setattr(P, "latest_seasons", lambda today=None: real(today or datetime.date(2026, 10, 4)))
    assert router._eval_current(ev)
    assert not router._eval_current({**ev, "prompt_version": "000000000000"})
    assert not router._eval_current({**ev, "model": "another-model"})
    # When "this season" means another season, it is no longer the same prompt.
    monkeypatch.setattr(P, "latest_seasons", lambda today=None: (2027, 2027) if today is None else real(today))
    assert not router._eval_current(ev)


def test_key_is_in_no_file_git_would_commit(P):
    key = P.api_key()
    if not key:
        pytest.skip("no GEMINI_API_KEY here")
    root = os.path.dirname(_API)
    # Patterns from stdin, so the key never appears in a command line.
    r = subprocess.run(["git", "grep", "-q", "--untracked", "-F", "-f", "-"], cwd=root, input=key + "\n",
                       capture_output=True, text=True)
    assert r.returncode == 1, "the Gemini key is in a file git would commit"
    ignored = subprocess.run(["git", "check-ignore", "-q", "api/.env"], cwd=root)
    assert ignored.returncode == 0
