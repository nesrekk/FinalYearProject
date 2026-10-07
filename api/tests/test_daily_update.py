"""
test_daily_update.py
=====================
Round 9 step 2 (2026-10-06): scripts/daily_update.py, the live season's one command, and scripts/espn_summary.py,
ESPN's game summary mapped to the play-by-play rows the hosted release gave:

  * the clock rule reproduces the release's seconds_remaining (Float32 arithmetic to four decimals, 2880 / 2160 /
    1440 / 720 / 300 at the period starts, the last play dropped), on synthetic plays;
  * a 2025-26 game's summary, mapped, equals its stored release rows in every column (four games, 2,069 events,
    one of them two overtimes; ids are compared where the release had one);
  * the through-date rule (the last date whose games are all final or not played);
  * a real preseason run (the 2026-27 preseason through 2026-10-05: eight finals) into copies of the tables in the
    schema zz_daily_update, never the public tables: finals, play-by-play with every name matched, game_scores
    against the LeagueGameFinder rows, officials with ids and names, the shot chart against the box score, the season
    stats with impact scores; then a second run writes nothing new, a dry run writes nothing at all, and the public
    tables are untouched;
  * the per-season impact scores equal the whole-table scripts' for that season;
  * the run log is registered (paper_manifest.LIVE, live_season, rebuild_all.sh, .gitignore).

Tests that read ESPN or stats.nba.com skip when the feeds don't answer. Local database only (the run tests create and
drop a schema). About 3 minutes with the network (the chart is fetched per team, 30 calls).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_daily_update.py
"""

import json
import os
import subprocess
import sys
import tempfile
import warnings
from datetime import date

import numpy as np
import pandas as pd
import psycopg2
import pytest
import requests

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _d in (_API, _SCRIPTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import compute_impact_score as CI  # noqa: E402
import daily_update as D  # noqa: E402
import espn_summary as ES  # noqa: E402
import fetch_pbp_espn as FP  # noqa: E402
import live_season as LS  # noqa: E402
import paper_manifest as PM  # noqa: E402
import upgrade_impact_scores as UI  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

PY = sys.executable
SCHEMA = "zz_daily_update"
WRITTEN = ["pbp_games", "pbp_events", "game_scores", "postseason_games", "team_game_fatigue", "game_team_box", "game_officials",
           "game_officials_fetch_log", "player_shots", "player_shots_cache_status", "player_season_stats"]
FULL_COPY = {"player_season_stats"}            # the name matcher reads every season
SERIAL = {"pbp_events": "id", "player_shots": "id"}
CACHE = os.path.join(tempfile.gettempdir(), "nba_hub_daily_update_test")   # reruns reuse the fetched answers
# 2025-26 games with stored release rows: regular (BOS-ORL), two overtimes (OKC-HOU), a then-unmatched name (DAL-CHI), IND-DET
RELEASE_GAMES = ["401811041", "401809243", "401811048", "401811043"]
# --no-rebuild: the season rebuild (round 9 step 3) never runs for the preseason anyway; said explicitly, since the builds
# would follow the schema's search_path into the public tables
PRESEASON = ["--season-types", "preseason", "--from-date", "2026-10-02", "--date", "2026-10-05", "--sleep", "0.6", "--no-rebuild"]
EVENT_COLS = ["period", "seconds_remaining", "score_home", "score_away", "team_id", "team_tricode", "action_type", "description"]


def _db_reachable():
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


def _espn_reachable():
    try:
        return requests.get(ES.SUMMARY_URL.replace("/summary", "/scoreboard"), params={"dates": "20261005", "limit": 1},
                            timeout=6).status_code == 200
    except Exception:
        return False


def _nba_api_reachable():
    try:
        from nba_api.stats.endpoints import leaguegamefinder
        leaguegamefinder.LeagueGameFinder(season_nullable="2026-27", season_type_nullable="Pre Season",
                                          league_id_nullable="00", timeout=15).get_data_frames()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")
needs_espn = pytest.mark.skipif(not _espn_reachable(), reason="ESPN's site API doesn't answer")


def _summary(espn_id):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"summary_{espn_id}.json.gz")
    if os.path.exists(path):
        return ES.load_summary(path)
    s = ES.fetch_summary(espn_id)
    ES.save_summary(s, path)
    return s


# ── the clock and the through date (no network, no database) ─────────────────

def _plays(spec):
    return [{"period": {"number": p}, "clock": {"displayValue": c}} for p, c in spec]


def test_clock_rule_matches_the_release():
    assert ES.start_seconds(1, 0, 55.1) == 2215.1001 and ES.start_seconds(3, 0, 57.3) == 777.3
    assert ES.start_seconds(2, 12, 0) == 2160.0 and ES.start_seconds(4, 0, 0.1) == 0.1 and ES.start_seconds(5, 4, 45) == 285.0
    assert ES.clock_parts("1:34") == (1.0, 34.0) and ES.clock_parts("45.3") == (0.0, 45.3)
    plays = _plays([(1, "12:00"), (1, "11:40"), (1, "0.5"), (2, "12:00"), (2, "3:00"), (3, "12:00"), (4, "12:00"), (4, "0.1"),
                    (5, "5:00"), (5, "4:45"), (5, "0.0")])
    assert ES.end_seconds(plays) == [2880.0, 2160.5, 2160.0, 2160.0, 1440.0, 1440.0, 720.0, 300.0, 300.0, 0.0, None]


def test_through_date_is_the_last_fully_settled_date():
    d = [date(2026, 10, 20) + pd.Timedelta(days=i) for i in range(4)]
    g = lambda day, final, status="STATUS_FINAL" if True else "": {"date": day, "final": final, "not_played": status in D.NOT_PLAYED}  # noqa: E731
    games = [g(d[0], True), g(d[1], True), g(d[1], False, "STATUS_POSTPONED"), g(d[2], False, "STATUS_IN_PROGRESS"), g(d[3], False, "STATUS_SCHEDULED")]
    assert D.through_date(games, d) == d[1]
    assert D.through_date([g(d[0], False, "STATUS_SCHEDULED")], d) == date(2026, 10, 19)
    assert D.through_date([], d) == d[3]          # no games at all: every date is settled
    assert D.fill_official_names([(1, "A B"), (2, ""), (3, "C D")], ["C D", "X Y", "A B"]) == [(1, "A B"), (2, "X Y"), (3, "C D")]
    assert D.fill_official_names([(1, "A B"), (2, "")], ["A B", "X Y", "Z Q"]) == [(1, "A B"), (2, "")]


# ── the mapping against the stored release rows ─────────────────────────────

@needs_db
@needs_espn
def test_summary_rows_equal_the_stored_release_rows():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    matcher = FP.PlayerMatcher(cur)
    checked = 0
    for espn_id in RELEASE_GAMES:
        s = _summary(espn_id)
        game = pd.read_sql_query("SELECT * FROM pbp_games WHERE game_id = %s", conn, params=(f"espn_{espn_id}",)).iloc[0]
        stored = pd.read_sql_query("SELECT * FROM pbp_events WHERE game_id = %s ORDER BY action_number", conn,
                                   params=(f"espn_{espn_id}",))
        game_row, rows, _ = ES.event_rows(s, int(game.season), matcher)
        assert game_row[:6] == (f"espn_{espn_id}", int(game.season), game.game_date, game.home_team, game.away_team, bool(game.home_win))
        mine = pd.DataFrame(rows, columns=["game_id", "action_number", *EVENT_COLS[:6], "person_id", "player_name", "action_type",
                                           "sub_type", "description"])
        assert len(mine) == len(stored) == len(s["plays"]) - 1, (espn_id, len(mine), len(stored))
        m = stored.merge(mine, on="action_number", suffixes=("_s", ""), how="outer", indicator=True)
        assert (m._merge == "both").all()
        for col in EVENT_COLS:
            a, b = m[col + "_s"], m[col]
            same = (a == b) | (a.isna() & b.isna())
            assert same.all(), (espn_id, col, m.loc[~same, ["action_number", col + "_s", col]].head(3).to_dict("records"))
        with_id = m.person_id_s.notna()
        assert (m.loc[with_id, "person_id_s"].astype("int64") == m.loc[with_id, "person_id"].astype("int64")).all(), espn_id
        assert (m.loc[with_id, "player_name_s"] == m.loc[with_id, "player_name"]).all(), espn_id
        # a name the release couldn't match keeps ESPN's spelling (the match may succeed now that the season table has grown)
        named = m.player_name_s.notna() & ~with_id
        assert (m.loc[named, "player_name_s"] == m.loc[named, "player_name"]).all() or \
            m.loc[named & (m.player_name_s != m.player_name), "person_id"].notna().all(), espn_id
        checked += len(m)
    conn.close()
    assert checked >= 2000


# ── a preseason run into copies of the tables ───────────────────────────────

def _make_schema(cur, schema):
    cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    cur.execute(f"CREATE SCHEMA {schema}")
    for t in WRITTEN:
        cur.execute(f"CREATE TABLE {schema}.{t} (LIKE public.{t} INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES)")
        if t in SERIAL:
            cur.execute(f"CREATE SEQUENCE {schema}.{t}_{SERIAL[t]}_seq")
            cur.execute(f"ALTER TABLE {schema}.{t} ALTER COLUMN {SERIAL[t]} SET DEFAULT nextval('{schema}.{t}_{SERIAL[t]}_seq')")
        if t in FULL_COPY:
            cur.execute(f"INSERT INTO {schema}.{t} SELECT * FROM public.{t}")


def _run(schema, *extra, block_network=False):
    env = {**os.environ, "PGOPTIONS": f"-c search_path={schema},public", "LIVE_DATA_DIR": os.path.join(CACHE, "live_data")}
    if block_network:
        env.update(HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9")
    r = subprocess.run([PY, "daily_update.py", *PRESEASON, *extra], cwd=_SCRIPTS, env=env, capture_output=True, text=True,
                       timeout=1500)
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    return r.returncode, summary, r.stdout + r.stderr


def _counts(cur, schema):
    out = {}
    for t in WRITTEN + ["daily_update_runs"]:
        cur.execute("SELECT 1 FROM pg_tables WHERE schemaname = %s AND tablename = %s", (schema, t))
        if cur.fetchone():
            cur.execute(f"SELECT count(*) FROM {schema}.{t}" + (" WHERE season = 2027" if t == "player_season_stats" else ""))
            out[t] = cur.fetchone()[0]
    return out


@pytest.fixture(scope="module")
def preseason_run():
    if not (_db_reachable() and _espn_reachable() and _nba_api_reachable()):
        pytest.skip("needs the database, ESPN and stats.nba.com")
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()
    public_before = _counts(cur, "public")
    _make_schema(cur, SCHEMA)
    _make_schema(cur, SCHEMA + "_dry")
    try:
        live = _run(SCHEMA)
        again = _run(SCHEMA, "--offline", block_network=True)
        dry = _run(SCHEMA + "_dry", "--dry-run", "--offline", block_network=True)
        yield {"conn": conn, "cur": cur, "live": live, "again": again, "dry": dry, "public_before": public_before}
    finally:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}_dry CASCADE")
        conn.close()


def test_preseason_run_stores_every_feed(preseason_run):
    P = preseason_run
    code, summary, out = P["live"]
    assert code == 0, out[-3000:]
    assert summary.startswith("daily_update 2026-10-05 (live, preseason): 4 dates 2026-10-02..2026-10-05 (complete through 2026-10-05), 8 finals;")
    assert "FAILED" not in summary
    cur = P["cur"]
    q = lambda sql, *p: (cur.execute(sql, p), cur.fetchall())[1]  # noqa: E731
    games = q(f"SELECT game_id, season, game_date, home_team, away_team, home_win FROM {SCHEMA}.pbp_games ORDER BY game_date, game_id")
    assert len(games) == 8 and all(g[1] == 2027 and g[6 - 1] in (True, False) for g in games)
    assert {g[2] for g in games} == {date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5)}
    for gid, *_ in games:
        s = ES.load_summary(os.path.join(CACHE, "live_data", "2027", "espn_summary", f"{gid[5:]}.json.gz"))
        (n, first, nulls, teams), = q(f"""SELECT count(*), min(seconds_remaining) FILTER (WHERE action_number = 1),
                                          count(*) FILTER (WHERE person_id IS NULL AND player_name IS NOT NULL),
                                          count(DISTINCT team_tricode) FROM {SCHEMA}.pbp_events WHERE game_id = %s""", gid)
        assert n == len(s["plays"]) - 1 and first == 2880 and teams == 2, gid
        assert nulls == 0, f"{gid}: {nulls} events without an id (rookies get theirs from the season rows loaded first)"
    (n_sc, n_ok, n_espn), = q(f"""SELECT count(*), count(*) FILTER (WHERE (s.pts_for > s.pts_against) = f.win), count(s.espn_id)
                                   FROM {SCHEMA}.game_scores s JOIN {SCHEMA}.team_game_fatigue f USING (game_id, team_abbreviation)""")
    assert n_sc == n_ok == n_espn == 16
    # LeagueGameFinder's preseason table lists every game played by the run day, not only through --date (16 rows on
    # 2026-10-06, 24 on 2026-10-07; round 9 issue R9-028): at least the eight finals' sixteen
    assert q(f"SELECT count(*) FROM {SCHEMA}.game_team_box")[0][0] >= 16
    assert q(f"SELECT count(*) FROM {SCHEMA}.team_game_fatigue WHERE rest_days IS NULL")[0][0] >= 16 - 8
    assert q(f"SELECT count(*) FROM {SCHEMA}.postseason_games")[0][0] == 0
    (logged, with_officials, unnamed), = q(f"""SELECT (SELECT count(*) FROM {SCHEMA}.game_officials_fetch_log),
        (SELECT count(DISTINCT game_id) FROM {SCHEMA}.game_officials), (SELECT count(*) FROM {SCHEMA}.game_officials WHERE official_name = '')""")
    assert logged >= 8 and with_officials >= 7 and unnamed == 0   # every preseason game played by the run day (R9-028)
    # the chart against the box score: equal in all but at most one game (0012600067's chart is short of the box by 17)
    rows = q(f"""SELECT b.game_id, sum(b.fga), (SELECT count(*) FROM {SCHEMA}.player_shots s WHERE s.game_id = b.game_id)
                 FROM {SCHEMA}.game_team_box b WHERE b.game_id IN (SELECT game_id FROM {SCHEMA}.game_scores) GROUP BY 1""")
    assert len(rows) == 8 and sum(1 for _, box, chart in rows if box == chart) >= 7 and all(chart <= box for _, box, chart in rows), rows
    assert q(f"SELECT count(*) FROM {SCHEMA}.player_shots WHERE season <> '2026-27' OR shot_zone_basic IS NOT NULL")[0][0] == 0
    (n_ps, n_z, n_age, n_pf), = q(f"""SELECT count(*), count(impact_score), count(age), count(pf) FROM {SCHEMA}.player_season_stats
                                    WHERE season = 2027""")
    assert n_ps >= 263 and n_z >= 0.95 * n_ps and n_age == n_pf == n_ps
    runs = q(f"SELECT mode, status, through_date, pbp_games_new, shots_inserted, shots_deleted, season_stats_players FROM {SCHEMA}.daily_update_runs ORDER BY started_at")
    assert runs[0][:4] == ("live", "ok", date(2026, 10, 5), 8) and runs[0][4] > 1000 and runs[0][6] == n_ps


def test_second_run_writes_nothing_new_and_a_dry_run_nothing_at_all(preseason_run):
    P = preseason_run
    code, summary, out = P["again"]
    assert code == 0, out[-3000:]
    assert "pbp +0 games / 0 events" in summary and "shots +0/-0" in summary and "(offline, preseason)" in summary
    cur = P["cur"]
    cur.execute(f"SELECT mode, status, pbp_games_new, pbp_events_new, shots_inserted, shots_deleted, officials_games FROM {SCHEMA}.daily_update_runs ORDER BY started_at")
    runs = cur.fetchall()
    assert len(runs) == 2 and runs[1] == ("offline", "ok", 0, 0, 0, 0, 0)
    code, summary, out = P["dry"]
    assert code == 0, out[-3000:]
    assert summary.endswith("[dry run: nothing written]") and "pbp +8 games" in summary
    assert all(v == 0 for v in _counts(cur, SCHEMA + "_dry").values()) and "daily_update_runs" not in _counts(cur, SCHEMA + "_dry")
    assert _counts(cur, "public") == P["public_before"], "a run touched a public table"


# ── the per-season scores and the registration ──────────────────────────────

@needs_db
def test_season_impact_scores_equal_the_whole_table_scripts():
    conn = psycopg2.connect(**DB_CONFIG)
    df = pd.read_sql_query(f"SELECT player_id, season, {', '.join(CI.COMPONENTS)} FROM player_season_stats WHERE season >= 2024", conn)
    whole = CI.impact_z(df).set_index(["player_id", "season"]).impact_score
    one = CI.impact_z(df[df.season == 2026]).set_index(["player_id", "season"]).impact_score
    assert len(one) > 400 and np.allclose(whole.loc[one.index], one)
    stored = pd.read_sql_query("SELECT player_id, season, impact_score FROM player_season_stats WHERE season = 2026", conn) \
        .set_index(["player_id", "season"]).impact_score
    assert np.allclose(stored.loc[one.index], one, atol=1e-9), "impact_z differs from the stored whole-table run"
    d = pd.read_sql_query(f"SELECT {', '.join(UI.REQUIRED_COLS)} FROM player_season_stats WHERE season >= 2024", conn)
    d = d.dropna(subset=[c for c in UI.REQUIRED_COLS if c not in ("player_id", "player_name", "season")]).reset_index(drop=True)
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        w = UI.compute_star_score(UI.compute_raw_score(d.copy())).set_index(["player_id", "season"])
        o = UI.compute_star_score(UI.compute_raw_score(d[d.season == 2026].copy().reset_index(drop=True))).set_index(["player_id", "season"])
    assert np.allclose(w.loc[o.index, "impact_score_raw"], o.impact_score_raw)
    assert np.allclose(w.loc[o.index, "impact_score_star"].fillna(-99), o.impact_score_star.fillna(-99))
    conn.close()


def test_run_log_is_registered_as_live_and_daily():
    assert "daily_update_runs" in PM.LIVE and PM.TABLES["daily_update_runs"] == ("source", "daily_update.py")
    assert LS.classify("daily_update_runs") == "daily" and LS.BY_PRODUCER["daily_update.py"] == "daily"
    with open(os.path.join(_SCRIPTS, "rebuild_all.sh")) as f:
        steps = [ln.split() for ln in f if ln.startswith("step ")]
    assert any(s[1] == "fetch" and s[3] == "daily_update.py" for s in steps)
    with open(os.path.join(_ROOT, ".gitignore")) as f:
        assert "live_data/" in f.read().split()
    assert D.KIND_OF_ESPN == {1: "preseason", 2: "regular", 3: "playoffs", 5: "playin"}
    import fetch_season_shots as SS
    assert set(D.TYPES) == set(SS.ALL_TYPES) and set(SS.TYPES) == {"regular", "playoffs", "playin"}
