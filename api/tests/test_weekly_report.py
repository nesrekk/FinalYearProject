"""
test_weekly_report.py
=====================
Round 9 step 6: the weekly guide report and the daily update's ledger step, on a simulated first fortnight.

The real scripts/ledger_update.py runs once a day at 12:00 US Eastern from opening day (2026-10-20) to 2026-11-09, on
COPIES of the locked ledger tables in schema zz_weekly_report (test_ledger_gameday.py's pattern and helpers: ESPN's
scoreboard answered from the locked schedule, the clock fixed), with every earlier game final (scores drawn from a
seeded stream). That gives three complete weeks: 92 games through 2026-11-01 (no paired test yet: they start at 100)
and 146 through 11-08 (the run of 11-09 stores them). Then:

  - api/weekly_report_lib.build() for the week ending 2026-11-08 agrees with ledger_live's own scoring, the stored
    paired tests of 11-09, the standings of the morning of 11-09 and the results; two builds are identical; the week
    ending 11-01 has no tests and says so
  - GET /ledger/weekly returns the same report; scripts/weekly_report.py writes it as Markdown
  - paper_numbers.ledger() at an as-of date after the first games emits the forward-test macros from ledger_tests,
    and at the lock date stays as before (0 games scored)
  - daily_update.py's ledger step: what it runs and when it skips (no network)

and checks the real public ledger tables are untouched. Local database only.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_weekly_report.py
"""

import hashlib
import json
import os
import subprocess
import sys
import types
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API)
_SCRIPTS = os.path.join(_ROOT, "scripts")
_TESTS = os.path.dirname(os.path.abspath(__file__))
for _d in (_API, _SCRIPTS, _TESTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import db_config  # noqa: E402
import ledger_live as LV  # noqa: E402
import test_ledger_gameday as G  # noqa: E402  (its helpers: the fake ESPN, the driver, the public fingerprint)

SCHEMA = "zz_weekly_report"
SEASON = 2027
FIRST, LAST = date(2026, 10, 20), date(2026, 11, 9)
WEEK1, WEEK2, WEEK3 = date(2026, 10, 25), date(2026, 11, 1), date(2026, 11, 8)
MON3 = date(2026, 11, 9)
RESAMPLES = 400

pytestmark = G.pytestmark

DRIVER = G.DRIVER.replace('sys.argv = ["ledger_update.py"]',
                          'sys.argv = ["ledger_update.py"] + os.environ.get("FAKE_ARGS", "").split()')


def _final_for(espn_id):
    """A seeded final: the home side wins about 57% of the time, margins 1-25."""
    rng = np.random.default_rng(int(hashlib.md5(espn_id.encode()).hexdigest()[:8], 16))
    home_wins = rng.random() < 0.57
    margin = int(rng.integers(1, 26))
    loser = int(rng.integers(92, 118))
    return G._final(loser + margin, loser) if home_wins else G._final(loser, loser + margin)


def _run(tmp, sched, states, now_et):
    espn = os.path.join(tmp, "espn.json")
    G._write_espn(espn, sched, states)
    driver = os.path.join(tmp, "driver.py")
    with open(driver, "w") as f:
        f.write(DRIVER)
    env = {**os.environ, "DB_TARGET": "local", "PGOPTIONS": f"-c search_path={SCHEMA}", "FAKE_ESPN": espn,
           "LEDGER_SCRIPTS": _SCRIPTS, "FAKE_NOW": now_et.replace(tzinfo=G.EASTERN).isoformat(),
           "FAKE_ARGS": f"--resamples {RESAMPLES}"}
    p = subprocess.run([G.PY, driver], cwd=_SCRIPTS, env=env, capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout


@pytest.fixture(scope="module")
def fortnight(tmp_path_factory):
    tmp = str(tmp_path_factory.mktemp("weekly"))
    cur = G._CONN.cursor()
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCHEMA}")
    for t in G.LOCK_TABLES:
        cur.execute(f"CREATE TABLE {SCHEMA}.{t} (LIKE public.{t} INCLUDING ALL)")
        cur.execute(f"INSERT INTO {SCHEMA}.{t} SELECT * FROM public.{t}")
    G._CONN.commit()
    before = G._public_fingerprint()
    zz = G._connect(options=f"-c search_path={SCHEMA}")
    try:
        sched = G._schedule(zz)
        day = FIRST
        while day <= LAST:
            states = {r.espn_id: _final_for(r.espn_id) for r in sched[sched.counted & (sched.game_date < day)].itertuples()}
            _run(tmp, sched, states, datetime(day.year, day.month, day.day, 12, 0))
            day += timedelta(days=1)
        # the reading connection also sees public (best_games, game_pregame_odds, player_rating_tracker: no 2026-27 rows)
        zr = G._connect(options=f"-c search_path={SCHEMA},public")
        yield types.SimpleNamespace(zz=zz, zr=zr, sched=sched, tmp=tmp)
        zr.close()
    finally:
        zz.close()
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        G._CONN.commit()
    assert G._public_fingerprint() == before, "the real ledger tables changed"


def test_week_report_agrees_with_the_ledger(fortnight):
    import weekly_report_lib as W
    zr = fortnight.zr
    assert W.weeks(zr, SEASON) == [WEEK1, WEEK2, WEEK3]
    rep = W.build(zr, SEASON, WEEK3)
    assert rep["start"] == "2026-11-02" and rep["end"] == "2026-11-08"
    sched = fortnight.sched
    played = sched[sched.counted & (sched.game_date <= WEEK3)]
    assert rep["games_season"] == len(played)
    assert rep["games_week"] == int((played.game_date >= date(2026, 11, 2)).sum())
    lg = rep["ledger"]
    # the like-for-like scores are ledger_live's own, on the games dated up to the week's end
    df = LV.scored(zr, SEASON)
    df["game_date"] = pd.to_datetime(df.game_date).dt.date
    both = LV.common(df[df.game_date <= WEEK3])
    assert lg["common_season"] == both.espn_id.nunique() == len(played)
    ref = {m["version"]: m for m in LV.metrics(both)}
    assert [r["version"] for r in lg["season"]] == [v for v in LV.VERSIONS if v in ref]
    for r in lg["season"]:
        assert abs(r["brier"] - ref[r["version"]]["brier"]) < 1e-12 and abs(r["log_loss"] - ref[r["version"]]["log_loss"]) < 1e-12
        assert r["brier_lo"] < r["brier"] < r["brier_hi"] and r["log_loss_lo"] < r["log_loss"] < r["log_loss_hi"]
    assert sum(r["n"] for r in lg["week"]) == 5 * rep["games_week"]
    # the paired tests are the ones the run of the morning after stored, on the same games
    assert lg["tests_as_of"] == "2026-11-09"
    alls = [t for t in lg["tests"] if t["variant"] == "all"]
    assert len(alls) == len(LV.PAIRS) * len(LV.METRICS) and {t["n"] for t in alls} == {lg["common_season"]}
    t = next(t for t in alls if (t["model_a"], t["model_b"], t["metric"]) == ("as_is", "record", "brier"))
    assert abs(t["value_a"] - ref["as_is"]["brier"]) < 1e-9 and abs(t["value_b"] - ref["record"]["brier"]) < 1e-9
    # every game was logged before tip in this simulation
    assert lg["recomputed_week"] == 0 and {t["n"] for t in lg["tests"]} == {lg["common_season"]}
    # misses: the week's games, worst first
    ms = lg["misses"]
    assert len(ms) == W.MISSES and all("2026-11-02" <= m["date"] <= "2026-11-08" for m in ms)
    assert [m["roster_log_loss"] for m in ms] == sorted((m["roster_log_loss"] for m in ms), reverse=True)
    for m in ms:
        assert abs(m["winner_chance_roster"] - np.exp(-m["roster_log_loss"])) < 1e-9
    # standings: the morning after the week, records from the results
    st = rep["standings"]
    assert st["as_of"] == "2026-11-09" and st["was_as_of"] == "2026-11-02" and len(st["teams"]) == 30
    tl = pd.read_sql("SELECT team, wins, losses, exp_final_wins FROM ledger_team_log WHERE season = %s AND as_of = %s "
                     "AND forecast = 'roster'", fortnight.zz, params=(SEASON, MON3)).set_index("team")
    for row in st["teams"]:
        assert (row["wins"], row["losses"]) == (tl.loc[row["team"], "wins"], tl.loc[row["team"], "losses"])
        assert abs(row["roster"]["exp_final_wins"] - tl.loc[row["team"], "exp_final_wins"]) < 1e-12
    assert sum(r["wins"] for r in st["teams"]) == sum(r["losses"] for r in st["teams"]) == rep["games_season"]
    movers = rep["notable"]["movers"]
    assert len(movers) == W.MOVERS and abs(movers[0]["change"]) >= abs(movers[-1]["change"])
    assert rep["notable"]["best_game"] is None and rep["notable"]["tracker"] == []     # nothing of 2026-27 stored in public
    # deterministic
    assert json.dumps(W.build(zr, SEASON, WEEK3), sort_keys=True, default=str) == json.dumps(rep, sort_keys=True, default=str)
    # the week before: 92 games, no paired test stored yet
    w2 = W.build(zr, SEASON, WEEK2)
    assert w2["ledger"]["common_season"] == 92 and w2["ledger"]["tests"] == [] and w2["ledger"]["tests_as_of"] is None
    # a week with no finals: an empty-state report, no error
    pre = W.build(zr, SEASON, date(2026, 10, 18))
    assert pre["games_season"] == 0 and pre["ledger"]["season"] == []


def test_route_and_markdown(fortnight, tmp_path):
    import weekly_report_lib as W
    import weekly_report as WR
    rep = W.build(fortnight.zr, SEASON, WEEK3)
    md = WR.render(rep)
    for needle in ("# Weekly report: 2026-27, week of 2026-11-02 to 2026-11-08", f"{rep['games_week']} games this week",
                   "## 2. Standings against the locked forecast", f"run of {rep['ledger']['tests_as_of']}",
                   f"{rep['ledger']['season'][0]['brier']:.4f}"):
        assert needle in md, needle
    assert "None yet: the ledger stores paired tests from 100 games" in WR.render(W.build(fortnight.zr, SEASON, WEEK2))
    assert "No game of the season" in WR.render(W.build(fortnight.zr, SEASON, date(2026, 10, 18)))
    # the script, through the schema's search_path, writes the same text
    env = {**os.environ, "DB_TARGET": "local", "PGOPTIONS": f"-c search_path={SCHEMA},public"}
    p = subprocess.run([G.PY, "weekly_report.py", "--end", str(WEEK3), "--out-dir", str(tmp_path)], cwd=_SCRIPTS, env=env,
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (tmp_path / f"{WEEK3}.md").read_text() == md
    # the route (the same library) on the same connection
    from routers import ledger as R
    body = R._weekly_payload(fortnight.zr, SEASON, WEEK3)
    assert json.dumps(body["report"], sort_keys=True) == json.dumps(R._plain(rep), sort_keys=True)
    assert body["weeks"] == [str(WEEK1), str(WEEK2), str(WEEK3)]


def test_paper_forward_test_macros(fortnight):
    import paper_numbers as PN
    cur = fortnight.zr.cursor()
    # at the paper's as-of (the lock date, the default) nothing is scored and the macros are as before
    N = PN.Numbers()
    PN.ledger(cur, N)
    vals = {n[2:]: v for _, n, v, _ in N.items}
    assert not N.failed and vals["LgScored"] == "0" and vals["LgAsOf"] == "2026-09-30" and "LgFwGames" not in vals
    # moved past the first games: the forward-test macros come from ledger_tests of that morning
    N = PN.Numbers()
    PN.ledger(cur, N, as_of=MON3)
    assert not N.failed, N.failed
    vals = {n[2:]: v for _, n, v, _ in N.items}
    tests = pd.read_sql("SELECT * FROM ledger_tests WHERE season = %s AND as_of = %s AND variant = 'all'", fortnight.zz,
                        params=(SEASON, MON3))
    n = int(tests.n.iloc[0])
    assert vals["LgScored"] == PN.integer(n) and vals["LgFwGames"] == PN.integer(n)
    assert vals["LgAsOf"] == "2026-11-09" and vals["LgFwThrough"] == "2026-11-08"
    t = tests[(tests.model_a == "roster") & (tests.model_b == "as_is") & (tests.metric == "log_loss")].iloc[0]
    assert vals["LgFwDLlRosterAsIs"] == PN.dec(t["diff"], 4)
    assert vals["LgFwDLlRosterAsIsLo"] == PN.dec(t["ci_lo"], 4)
    # an as-of with no stored tests is refused by a claim
    N = PN.Numbers()
    PN.ledger(cur, N, as_of=date(2026, 11, 2))
    assert any("stored paired tests" in f for f in N.failed)
    fortnight.zr.rollback()


def test_daily_update_ledger_step():
    import argparse
    import daily_update as D
    base = dict(dry_run=False, offline=False, date=None, from_date=None, season=2027, season_types="regular,playoffs,playin",
                sleep=1.0, refresh_shots=False, force_shots=False, no_rebuild=False, rebuild=False, rebuild_only=False,
                no_models=False, models=False, models_only=False, no_ledger=False, ledger_only=False)

    def run(**kw):
        return D.Run(argparse.Namespace(**{**base, **kw}))
    r = run()
    assert r.ledger_wanted() == (True, "") and r.ledger_command()[1:] == ["ledger_update.py"]
    r.conn.close()
    r = run(dry_run=True, date=date(2026, 10, 21))
    assert r.ledger_wanted()[0] and r.ledger_command()[1:] == ["ledger_update.py", "--dry-run", "--today", "2026-10-21"]
    r.conn.close()
    r = run(offline=True)
    assert r.ledger_command()[1:] == ["ledger_update.py", "--offline"]
    r.conn.close()
    for kw, why in ((dict(no_ledger=True), "--no-ledger"), (dict(season_types="preseason"), "only with the regular season"),
                    (dict(date=date(2026, 10, 21)), "--date outside a dry run"), (dict(rebuild_only=True), "--rebuild-only"),
                    (dict(season=2028), "the ledger scores 2026-27 only")):
        r = run(**kw)
        ok, reason = r.ledger_wanted()
        r.conn.close()
        assert not ok and reason.startswith(why), (kw, reason)
