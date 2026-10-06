"""
test_ledger_gameday.py
=======================
Forecast Ledger, a simulated opening week (round 8.5 step A). Runs the real
scripts/ledger_update.py (frozen code from the tag, lock check, odds, standings,
run log) end to end, six times, on COPIES of the locked ledger tables in the
schema zz_ledger_gameday, with ESPN's scoreboard replaced by events built from
the locked schedule and the clock set to chosen US Eastern times:

  A   2026-10-20 12:00 ET, nothing played: opening night's odds are logged before
      tip and equal the locked preseason odds; record-only odds are 0.5
  A2  the same run again: nothing new in the game log
  B   2026-10-21 12:00 ET, opening night final, one 10-21 game postponed: odds
      for the other 10-21 games from the three finals, none for the postponed
      game, no new 10-20 rows
  B2  the same run again: nothing new
  C   2026-10-23 12:00 ET (the 10-22 run was missed), one 10-22 game still in
      progress, the postponed game moved to 10-23: 10-22's odds are logged and
      labelled recomputed (after tip), 10-23 waits, no standings for 10-23
  D   2026-10-23 13:00 ET, everything before 10-23 final: 10-23's odds (with the
      moved game on its new date), standings, then a rerun adds nothing

and checks the real public ledger tables are untouched. Local database only
(never a cloud mirror: it creates and drops a schema).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_ledger_gameday.py
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _d in (_API, _SCRIPTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import db_config  # noqa: E402
import ledger_live as LV  # noqa: E402

SCHEMA = "zz_ledger_gameday"
LOCK_TABLES = ["ledger_lock", "ledger_meta", "ledger_schedule", "ledger_rosters", "ledger_hindcast", "ledger_forecasts"]
SEASON = 2027
EASTERN = ZoneInfo("America/New_York")
PY = sys.executable
NBA_TO_ESPN = {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}
D20, D21, D22, D23 = (date(2026, 10, d) for d in (20, 21, 22, 23))

# Runs ledger_update.main() with requests.get answering from a JSON file and a fixed clock.
DRIVER = textwrap.dedent("""
    import json, os, sys
    from datetime import datetime
    sys.path.insert(0, os.environ["LEDGER_SCRIPTS"])
    import requests
    data = json.load(open(os.environ["FAKE_ESPN"]))

    class Resp:
        def __init__(self, j): self.j = j
        def raise_for_status(self): pass
        def json(self): return self.j

    def get(url, params=None, timeout=None):
        if "scoreboard" not in url:
            raise RuntimeError("unexpected request " + url)
        if params.get("limit") == 1:
            return Resp({"leagues": [{"season": {"year": data["season"]}, "calendar": data["calendar"]}]})
        return Resp({"events": data["dates"].get(params["dates"], [])})
    requests.get = get

    import ledger_update as U
    FIXED = datetime.fromisoformat(os.environ["FAKE_NOW"])

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED.astimezone(tz) if tz else FIXED
    U.datetime = FakeDateTime
    sys.argv = ["ledger_update.py"]
    sys.exit(U.main())
""")


def _frozen_equals_working_tree():
    """The working-tree model files are the tag's (so this process can recompute with the frozen code)."""
    for f in ("api/ledger_lib.py", "api/season_sim_lib.py", "api/luck_lib.py"):
        tag = subprocess.run(["git", "rev-parse", f"ledger-2026-27:{f}"], cwd=_ROOT, capture_output=True, text=True).stdout.strip()
        wt = subprocess.run(["git", "hash-object", f], cwd=_ROOT, capture_output=True, text=True).stdout.strip()
        if not tag or tag != wt:
            return False
    return True


def _connect(**kw):
    try:
        return psycopg2.connect(**db_config.DB_CONFIG, connect_timeout=3, **kw)
    except Exception:
        return None


_CONN = _connect() if db_config.DB_TARGET == "local" else None


def _has(table):
    if _CONN is None:
        return False
    cur = _CONN.cursor()
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    return cur.fetchone()[0] is not None


pytestmark = [
    pytest.mark.skipif(db_config.DB_TARGET != "local", reason="creates a zz_ schema: local database only"),
    pytest.mark.skipif(_CONN is None, reason="Postgres DB is not reachable"),
    pytest.mark.skipif(not _has("ledger_lock"), reason="no Forecast Ledger lock"),
]


def _public_fingerprint():
    """Row count and an order-independent content hash of every public ledger_* table."""
    cur = _CONN.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                "AND table_name LIKE 'ledger%' ORDER BY 1")
    out = {}
    for (t,) in cur.fetchall():
        cur.execute(f"SELECT COUNT(*), COALESCE(SUM(('x' || LEFT(md5(x::text), 15))::bit(60)::bigint), 0) FROM public.{t} x")
        out[t] = cur.fetchone()
    _CONN.rollback()
    return out


@pytest.fixture(scope="module")
def sim():
    cur = _CONN.cursor()
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCHEMA}")
    for t in LOCK_TABLES:
        cur.execute(f"CREATE TABLE {SCHEMA}.{t} (LIKE public.{t} INCLUDING ALL)")
        cur.execute(f"INSERT INTO {SCHEMA}.{t} SELECT * FROM public.{t}")
    _CONN.commit()
    before = _public_fingerprint()
    zz = _connect(options=f"-c search_path={SCHEMA}")
    try:
        yield zz
    finally:
        zz.close()
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        _CONN.commit()
    assert _public_fingerprint() == before, "the real ledger tables changed"


def _schedule(zz):
    s = pd.read_sql("SELECT * FROM ledger_schedule WHERE season = %s ORDER BY game_date, tip_utc, espn_id", zz, params=(SEASON,))
    s["game_date"] = pd.to_datetime(s.game_date).dt.date
    return s


def _event(r, day, tip, state):
    status, hp, ap = state.get("status", "STATUS_SCHEDULED"), state.get("home_pts"), state.get("away_pts")

    def side(code, side_name, pts):
        abbr = "TBD" if code is None else NBA_TO_ESPN.get(code, code)
        return {"homeAway": side_name, "team": {"abbreviation": abbr}, "score": "" if pts is None else str(pts)}
    return {"id": r.espn_id, "date": tip, "season": {"year": SEASON, "type": 2},
            "competitions": [{"competitors": [side(r.home, "home", hp), side(r.away, "away", ap)],
                              "notes": [{"headline": r.note}] if r.note else [], "timeValid": bool(r.time_valid),
                              "neutralSite": bool(r.neutral_site), "venue": {"fullName": r.venue, "address": {"city": r.city}},
                              "status": {"type": {"name": status, "completed": status == "STATUS_FINAL"},
                                         "period": 4 if status == "STATUS_FINAL" else 0}}]}


def _write_espn(path, sched, states):
    """ESPN's scoreboard for the season: every locked event on its date; `states[espn_id]` overrides
    status / scores, and `move_to` = (date, tip) lists the event again on a new date (postponed, rescheduled)."""
    dates = {}
    for r in sched.itertuples():
        st = states.get(r.espn_id, {})
        if "move_to" in st:
            dates.setdefault(r.game_date.strftime("%Y%m%d"), []).append(_event(r, r.game_date, r.tip_utc, {"status": "STATUS_POSTPONED"}))
            new_day, new_tip = st["move_to"]
            dates.setdefault(new_day.strftime("%Y%m%d"), []).append(_event(r, new_day, new_tip, st))
        else:
            dates.setdefault(r.game_date.strftime("%Y%m%d"), []).append(_event(r, r.game_date, r.tip_utc, st))
    cal = sorted(f"{d[:4]}-{d[4:6]}-{d[6:]}T07:00Z" for d in dates)
    with open(path, "w") as f:
        json.dump({"season": SEASON, "calendar": cal, "dates": dates}, f)


def _run(tmp, sched, states, now_et):
    espn = os.path.join(tmp, "espn.json")
    _write_espn(espn, sched, states)
    driver = os.path.join(tmp, "driver.py")
    with open(driver, "w") as f:
        f.write(DRIVER)
    env = {**os.environ, "DB_TARGET": "local", "PGOPTIONS": f"-c search_path={SCHEMA}", "FAKE_ESPN": espn,
           "LEDGER_SCRIPTS": _SCRIPTS, "FAKE_NOW": now_et.replace(tzinfo=EASTERN).isoformat()}
    p = subprocess.run([PY, driver], cwd=_SCRIPTS, env=env, capture_output=True, text=True, timeout=600)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout


def _final(home_pts, away_pts):
    return {"status": "STATUS_FINAL", "home_pts": home_pts, "away_pts": away_pts}


def _log(zz):
    df = pd.read_sql("SELECT * FROM ledger_game_log WHERE season = %s", zz, params=(SEASON,))
    df["game_date"] = pd.to_datetime(df.game_date).dt.date
    return df


def _q(zz, sql):
    cur = zz.cursor()
    cur.execute(sql)
    out = cur.fetchall()
    zz.rollback()
    return out


def _team_log(zz, as_of):
    return pd.read_sql("SELECT * FROM ledger_team_log WHERE season = %s AND as_of = %s ORDER BY forecast, team",
                       zz, params=(SEASON, as_of)).drop(columns=["computed_at"])


def test_simulated_opening_week(sim, tmp_path):
    zz, tmp = sim, str(tmp_path)
    sched = _schedule(zz)
    g = {d: sched[sched.counted & (sched.game_date == d)] for d in (D20, D21, D22, D23)}
    assert [len(g[d]) for d in (D20, D21, D22, D23)] == [3, 11, 2, 12]
    locked = pd.read_sql("SELECT forecast, key, p_home FROM ledger_forecasts WHERE season = %s AND kind = 'game'",
                         zz, params=(SEASON,)).set_index(["forecast", "key"]).p_home

    # A: opening day, before the first tip
    out = _run(tmp, sched, {}, datetime(2026, 10, 20, 12, 0))
    log = _log(zz)
    assert len(log) == 9 and set(log.game_date) == {D20} and log.before_tip.all() and (log.games_used == 0).all()
    assert (log[log.forecast == "record"].p_home == 0.5).all()
    for f in ("as_is", "roster"):
        x = log[log.forecast == f]
        assert np.abs(x.p_home.to_numpy() - locked.loc[[(f, i) for i in x.espn_id]].to_numpy()).max() < 2e-6
    tl_a = _team_log(zz, D20)
    assert len(tl_a) == 60 and (tl_a.games == 0).all()
    assert "frozen code: ledger-2026-27" in out

    # A2: same run again
    _run(tmp, sched, {}, datetime(2026, 10, 20, 12, 0))
    assert len(_log(zz)) == 9
    pd.testing.assert_frame_equal(_team_log(zz, D20), tl_a)
    assert _q(zz, "SELECT COUNT(*), SUM(new_rows), COUNT(waiting) FROM ledger_runs") == [(2, 9, 0)]

    # B: opening night final, one 10-21 game postponed (no new date yet)
    finals20 = {r.espn_id: _final(110 + 3 * i, 104 + 5 * i) for i, r in enumerate(g[D20].itertuples())}
    moved = g[D21].espn_id.iloc[0]
    states = {**finals20, moved: {"status": "STATUS_POSTPONED"}}
    _run(tmp, sched, states, datetime(2026, 10, 21, 12, 0))
    log = _log(zz)
    new = log[log.game_date == D21]
    assert len(log) == 9 + 3 * 10 and len(new) == 30 and moved not in set(new.espn_id)
    assert new.before_tip.all() and (new.games_used == 3).all() and (new.last_result_date.astype(str) == str(D20)).all()
    res = pd.read_sql("SELECT * FROM ledger_results WHERE season = %s", zz, params=(SEASON,)).set_index("espn_id")
    for i, st in finals20.items():
        assert res.loc[i, "completed"] and (res.loc[i, "home_pts"], res.loc[i, "away_pts"]) == (st["home_pts"], st["away_pts"])
    assert res.loc[moved, "status"] == "STATUS_POSTPONED" and not res.loc[moved, "completed"]
    tl_b = _team_log(zz, D21)
    for f, x in tl_b.groupby("forecast"):
        assert x.wins.sum() == x.losses.sum() == 3 and x.games.sum() == 6
    # the logged odds are exactly what the frozen code gives from the stored results
    if _frozen_equals_working_tree():
        import ledger_lib as LL
        import ledger_update as U
        import luck_lib as luck
        import season_sim_lib as L
        lock = U.read_lock(zz.cursor(), LL, SEASON)
        r = pd.read_sql("SELECT * FROM ledger_results WHERE season = %s", zz, params=(SEASON,))
        r["game_date"] = pd.to_datetime(r.game_date).dt.date
        s = U.schedule_frame(LL, r)
        day = s[s.counts & (s.game_date == D21) & ~s.status.isin(U.NOT_PLAYED)]
        got = pd.DataFrame(U.odds_for_date(LL, L, luck, D21, day, r[r.counts & r.completed & (r.game_date < D21)],
                                           lock["teams"], U.priors(LL, lock), lock["beta"], SEASON))
        j = new.merge(got, on=["espn_id", "forecast"], suffixes=("", "_now"))
        assert len(j) == 30 and np.abs(j.p_home - j.p_home_now).max() < 1e-12
        assert (j.results_digest == j.results_digest_now).all()
        zz.rollback()

    # B2: same run again
    _run(tmp, sched, states, datetime(2026, 10, 21, 12, 0))
    assert len(_log(zz)) == 39

    # C: the 10-22 run was missed; one 10-22 game still in progress; the postponed game moved to 10-23
    finals21 = {r.espn_id: _final(100 + i, 97 - i) for i, r in enumerate(g[D21].itertuples()) if r.espn_id != moved}
    late = g[D22].espn_id.iloc[1]
    finals22 = {g[D22].espn_id.iloc[0]: _final(99, 101)}
    new_tip = "2026-10-24T03:00Z"
    states = {**finals20, **finals21, **finals22, late: {"status": "STATUS_IN_PROGRESS"},
              moved: {"move_to": (D23, new_tip)}}
    out = _run(tmp, sched, states, datetime(2026, 10, 23, 12, 0))
    log = _log(zz)
    new = log[log.game_date == D22]
    assert len(log) == 39 + 6 and len(new) == 6 and not new.before_tip.any()      # recomputed after tip
    assert (new.games_used == 3 + 10).all() and D23 not in set(log.game_date)
    runs = _q(zz, "SELECT waiting FROM ledger_runs ORDER BY started_at DESC LIMIT 1")
    assert runs[0][0].startswith("2026-10-23: waiting for 1 earlier game")
    assert len(_team_log(zz, D23)) == 0
    assert "waiting for 1 earlier game" in out

    # D: everything before 10-23 final
    states = {**states, late: _final(120, 118)}
    _run(tmp, sched, states, datetime(2026, 10, 23, 13, 0))
    log = _log(zz)
    new = log[log.game_date == D23]
    assert len(new) == 3 * 13 and moved in set(new.espn_id) and new.before_tip.all()
    assert (new.games_used == 3 + 10 + 2).all()
    assert set(new[new.espn_id == moved].tip_utc) == {new_tip}
    res = pd.read_sql("SELECT espn_id, game_date, status FROM ledger_results WHERE season = %s", zz, params=(SEASON,)).set_index("espn_id")
    assert str(res.loc[moved, "game_date"]) == str(D23) and res.loc[moved, "status"] == "STATUS_SCHEDULED"
    tl_d = _team_log(zz, D23)
    for f, x in tl_d.groupby("forecast"):
        assert x.wins.sum() == 15 and x.games.sum() == 30

    # D2: rerun adds nothing
    n = len(log)
    _run(tmp, sched, states, datetime(2026, 10, 23, 13, 0))
    assert len(_log(zz)) == n
    pd.testing.assert_frame_equal(_team_log(zz, D23), tl_d)
    assert _q(zz, "SELECT COUNT(*), SUM(new_rows) FROM ledger_runs") == [(7, n)]
    assert _q(zz, "SELECT COUNT(*) FROM (SELECT espn_id, forecast, game_date, results_digest FROM ledger_game_log "
                  "GROUP BY 1, 2, 3, 4 HAVING COUNT(*) > 1) d") == [(0,)]
    assert _q(zz, "SELECT COUNT(*) FROM ledger_game_log WHERE last_result_date >= game_date") == [(0,)]

    # scoring: the 15 finals, each under all five versions (in-season and locked preseason)
    df = LV.scored(zz, SEASON)
    assert df.espn_id.nunique() == 15 and len(LV.common(df)) == 15 * 5
    late_ids = set(g[D22].espn_id)
    assert not df[df.espn_id.isin(late_ids) & df.version.isin(LV.IN_SEASON)].before_tip.any()
    assert df[~df.espn_id.isin(late_ids)].before_tip.all()
