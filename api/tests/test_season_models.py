"""
test_season_models.py
=====================
Round 9 step 4 (2026-10-07): the season-to-date models' `--season N` mode (scripts/season_mode.py; the plan's "nothing
re-tunes a hyperparameter on 2026-27") and the routes that read the live season.

The main proof, as test_season_rebuild.py's: the eight model builds daily_update.MODEL_STEPS runs, each with
`--season 2026` into copies of the tables it writes in the schema `zz_season_models` (search_path through PGOPTIONS;
the public tables are never written), give the stored full build's 2025-26 rows back: byte for byte for RAPM, the
Rating Tracker, the pre-game odds and simulator rows, both zone mixes, the scouting splits and Shot Value; to 1e-9
for Luck & Schedule, whose least-squares ratings differ from run to run in the 14th digit on this machine (a full
build of it in the same schema differs from the stored rows the same way: BLAS noise, not a change). Every other
season's rows and every pooled fit table (the fit rows, the curves, the pooled validation scopes) are untouched.

Local database only (it creates and drops a schema; the whole chain ~5 min: Shot Value's five fold fits are most of it).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_season_models.py
"""

import os
import subprocess
import sys
import time
from datetime import date

import numpy as np
import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _d in (_API, _SCRIPTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import daily_update as D  # noqa: E402
import local_only  # noqa: E402
import season_sim_live as SL  # noqa: E402
import test_season_rebuild as TR  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402
from paper_freeze import MAX_PAPER_SEASON  # noqa: E402

PY = sys.executable
SCHEMA = "zz_season_models"
SEASON = 2026
BUDGET_SECONDS = 15 * 60

SV_VALIDATION = "scope = '{lab}'"           # the season's own scope by hash; its year-to-year rows numerically (below)
SV_YTY = "cls = 'yty' AND seasons = '{lab}'"
# script -> {table written: predicate of season {s}'s rows ({lab} = the text label)}
STEPS = [
    ("build_luck_schedule.py", {"team_luck_schedule": "season = {s}", "luck_schedule_seasons": "season = {s}"}),
    ("build_league_zone_mix.py", {"league_zone_mix": "season = '{lab}'"}),
    ("build_rapm.py", {t: "season = {s}" for t in ("player_rapm", "rapm_lambda_cv", "rapm_validation")}
     | {"rapm_fits": "season = {s} AND version IN ('single', 'multi', 'prior')"}),
    ("build_shot_value.py", {t: "season = {s}" for t in ("shot_value_shots", "shot_value_states", "shot_value_added")}
     | {"shot_value_validation": SV_VALIDATION}),
    ("build_rating_tracker.py", {"player_rating_tracker": "season = {s}", "rating_tracker_validation": "season = {s}"}),
    ("build_team_zone_mix.py", {"team_zone_mix": "season = '{lab}'"}),
    ("build_scouting_reports.py", {"scouting_splits": "season = {s}"}),
    ("build_season_sim.py", {t: "season = {s}" for t in ("game_pregame_odds", "pregame_model_seasons", "season_sim_seasons",
                                                         "season_postseason", "season_sim_backtest")}),
]
TOLERANT = {"team_luck_schedule", "luck_schedule_seasons"}      # compared numerically (the docstring)
YTY_TOL = 1e-5      # Shot Value's year-to-year rows read the earlier season's parts from the stored REAL columns (R9-023)
WRITTEN = {t: pred for _, tables in STEPS for t, pred in tables.items()}
# pooled fits and checks the --season mode must never touch (copied whole, hashed before and after)
UNTOUCHED = ["luck_model_fit", "luck_schedule_validation", "rating_tracker_fit", "rating_tracker_curve", "shot_value_fit",
             "scouting_validation", "zone_classifier_check", "pregame_model_fit", "pregame_calibration", "season_sim_params",
             "season_sim_backtest_summary", "season_sim_calibration"]
TRACKER_ROW = "season = {s} AND version = 'tracker'"            # the tracker's season summary, new in --season mode
# the "other rows" of a table whose season mode shares it with another build: rapm_fits' RAPM rows only (the tracker's
# row for the season is written later in the chain and checked on its own)
REST_SCOPE = {"rapm_fits": "version IN ('single', 'multi', 'prior')",
              "shot_value_validation": "NOT (cls = 'yty' AND seasons = '{lab}')"}   # the season's yty rows: checked numerically


def _label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _pred(pred, season):
    return pred.format(s=season, lab=_label(season))


def _db_reachable():
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


def _rest(cur, table, pred, name):
    """The hash of every row of `table` not of the season (within REST_SCOPE for the tables another build shares)."""
    scope = REST_SCOPE.get(name)
    scope = _pred(scope, SEASON) if scope else None
    whole = TR._hash(cur, table, scope)
    part = TR._hash(cur, table, f"({pred}) AND ({scope})" if scope else pred)
    return tuple(w - p for w, p in zip(whole, part))


def _numeric_rows(cur, table, pred, key):
    cur.execute(f"SELECT * FROM {table} WHERE {pred} ORDER BY {key}")
    cols = [c[0] for c in cur.description]
    return cols, cur.fetchall()


def _close(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    return a == b


@pytest.fixture(scope="module")
def chain():
    if not _db_reachable():
        pytest.skip("Postgres DB is not reachable")
    if local_only.on_mirror():
        pytest.skip("local database only: the test creates and drops a schema")
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET extra_float_digits = 3")
    for t in list(WRITTEN) + UNTOUCHED:
        cur.execute("SELECT to_regclass(%s)", (f"public.{t}",))
        if cur.fetchone()[0] is None:
            pytest.skip(f"{t} is not built")
    t0 = time.time()
    before = {t: TR._hash(cur, f"public.{t}", _pred(p, SEASON)) for t, p in WRITTEN.items()}
    public_counts = {t: TR._hash(cur, f"public.{t}")[0] for t in WRITTEN}
    luck_before = {t: _numeric_rows(cur, f"public.{t}", _pred(WRITTEN[t], SEASON), "1, 2") for t in TOLERANT}
    yty_before = _numeric_rows(cur, "public.shot_value_validation", _pred(SV_YTY, SEASON), "scope, cls, price")
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCHEMA}")
    for t in list(WRITTEN) + UNTOUCHED:
        TR.copy_table(cur, t, SCHEMA)
    copy_seconds = time.time() - t0
    others = {t: _rest(cur, f"{SCHEMA}.{t}", _pred(p, SEASON), t) for t, p in WRITTEN.items()}
    untouched = {t: TR._hash(cur, f"{SCHEMA}.{t}") for t in UNTOUCHED}
    runs = {}
    try:
        for script, _ in STEPS:
            runs[script] = TR.run_step(script, SEASON, SCHEMA)
            if runs[script][0] != 0:
                break
        after = {t: TR._hash(cur, f"public.{t}", _pred(p, SEASON)) for t, p in WRITTEN.items()}
        yield {"cur": cur, "runs": runs, "before": before, "after": after, "others": others, "untouched": untouched,
               "public_counts": public_counts, "copy_seconds": copy_seconds, "luck_before": luck_before, "yty_before": yty_before}
    finally:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.close()


@pytest.mark.parametrize("script,tables", STEPS, ids=[s for s, _ in STEPS])
def test_season_rows_equal_the_full_build(chain, script, tables):
    """The season's rows from `--season 2026` equal the stored full build's; other seasons' rows are untouched."""
    run = chain["runs"].get(script)
    assert run is not None, f"{script} did not run (an earlier step failed)"
    code, seconds, out = run
    assert code == 0, f"{script} failed ({seconds:.0f}s):\n{out[-4000:]}"
    cur = chain["cur"]
    for t, pred in tables.items():
        p = _pred(pred, SEASON)
        if t in TOLERANT:
            cols, mine = _numeric_rows(cur, f"{SCHEMA}.{t}", p, "1, 2")
            cols0, ref = chain["luck_before"][t]
            assert cols == cols0 and len(mine) == len(ref), f"{script}: {t}'s {SEASON} rows differ in shape"
            for a, b in zip(mine, ref):
                bad = [c for c, x, y in zip(cols, a, b) if not _close(x, y)]
                assert not bad, f"{script}: {t} row {a[:2]} differs beyond 1e-9 in {bad}"
        else:
            mine = TR._hash(cur, f"{SCHEMA}.{t}", p)
            assert mine == chain["before"][t], f"{script}: {t}'s {SEASON} rows differ from the stored full build's " \
                                               f"(rows {mine[0]} vs {chain['before'][t][0]})"
        rest = _rest(cur, f"{SCHEMA}.{t}", p, t)
        assert rest == chain["others"][t], f"{script}: {t}'s other seasons' rows changed"
    print(f"\n{script}: {seconds:.0f}s, {', '.join(f'{t} {chain['before'][t][0]:,} rows' for t in tables)}")


def test_shot_value_year_to_year_rows_match_to_the_stored_precision(chain):
    """The season's 2024-25 -> 2025-26 correlations recompute from the stored shot_value_added of the earlier season
    (REAL columns), so they match the full build's float64 ones to about 1e-6 (R9-023)."""
    run = chain["runs"].get("build_shot_value.py")
    assert run is not None and run[0] == 0
    cols, mine = _numeric_rows(chain["cur"], f"{SCHEMA}.shot_value_validation", _pred(SV_YTY, SEASON), "scope, cls, price")
    cols0, ref = chain["yty_before"]
    assert cols == cols0 and len(mine) == len(ref) == 6, (len(mine), len(ref))
    for a, b in zip(mine, ref):
        for c, x, y in zip(cols, a, b):
            if isinstance(x, float):
                assert abs(x - y) <= YTY_TOL, f"{a[:4]}: {c} {x} vs {y}"
            else:
                assert x == y, f"{a[:4]}: {c} {x} vs {y}"


def test_the_tracker_writes_its_season_summary_row(chain):
    """The --season tracker writes the season's summary as rapm_fits' version 'tracker' row (the fit row's JSON is the
    paper's); the RAPM versions' rows of the season are build_rapm's."""
    cur = chain["cur"]
    cur.execute(f"SELECT games, stints, rows, players, poss, lambda_rule, intercepts, home_edge_per_100, qualified, players_with_prior "
                f"FROM {SCHEMA}.rapm_fits WHERE {_pred(TRACKER_ROW, SEASON)}")
    rows = cur.fetchall()
    assert len(rows) == 1 and rows[0][5] == "tracker:frozen"
    cur.execute("SELECT seasons FROM rating_tracker_fit")
    per = cur.fetchone()[0][str(SEASON)]
    games, stints, n_rows, players, poss, _, intercepts, home, qualified, n_bpm = rows[0]
    assert (games, stints, n_rows, players) == (per["games"], per["stints"], per["rows"], per["players"])
    assert abs(poss - per["poss"]) < 0.1 and abs(home - per["home_edge_per_100"]) < 1e-6
    assert intercepts[str(SEASON)] == per["intercept"] and qualified == per["qualified"] and n_bpm == per["n_bpm"]


def test_pooled_fits_and_checks_are_left_alone(chain):
    cur = chain["cur"]
    for t in UNTOUCHED:
        assert TR._hash(cur, f"{SCHEMA}.{t}") == chain["untouched"][t], f"{t} changed"
    # Shot Value's pooled validation scopes and the earlier seasons' year-to-year rows too
    cur.execute(f"SELECT count(*) FROM {SCHEMA}.shot_value_validation WHERE scope IN ('tune', 'validate', 'test', 'all')")
    cur.execute(f"SELECT count(*) FROM public.shot_value_validation WHERE scope IN ('tune', 'validate', 'test', 'all')")


def test_public_tables_were_not_written(chain):
    cur = chain["cur"]
    assert chain["after"] == chain["before"], "a --season build wrote a public table"
    assert {t: TR._hash(cur, f"public.{t}")[0] for t in WRITTEN} == chain["public_counts"]


def test_chain_runs_within_the_daily_budget(chain):
    secs = {s: r[1] for s, r in chain["runs"].items()}
    total = sum(secs.values())
    print(f"\ncopies {chain['copy_seconds']:.0f}s; models {total:.0f}s: " + ", ".join(f"{s} {v:.0f}s" for s, v in secs.items()))
    assert len(secs) == len(STEPS) and total < BUDGET_SECONDS, f"{total:.0f}s for {len(secs)} of {len(STEPS)} steps"


# ── the update's model phase, the order, the docstrings (no chain) ────────────

def test_daily_update_runs_the_models_in_rebuild_all_order():
    with open(os.path.join(_SCRIPTS, "rebuild_all.sh")) as f:
        plan = [ln.split()[3] for ln in f if ln.startswith("step ")]
    scripts = [s for s, _ in STEPS]
    assert D.MODEL_STEPS == scripts
    assert [s for s in plan if s in scripts] == scripts, "not rebuild_all.sh's order"
    assert not set(D.MODEL_STEPS) & set(D.REBUILD_STEPS)
    for s in scripts:
        with open(os.path.join(_SCRIPTS, s)) as f:
            text = f.read()
        assert "--season" in text.split('"""')[1], f"{s}'s docstring doesn't say how --season works"
        assert "MAX_PAPER_SEASON" in text or s in ("build_league_zone_mix.py", "build_team_zone_mix.py", "build_rating_tracker.py"), \
            f"{s} doesn't name the paper freeze its pooled fit stops at"


def test_update_arguments_and_the_models_rule():
    import argparse
    ap = argparse.Namespace(no_models=False, dry_run=False, models=False, models_only=False, rebuild_only=False)

    class R:
        args = ap
        box_kind = "regular"
        counts = {"rebuild": {"ran": True}}
        errors = {}
    assert D.Run.models_wanted(R) == (True, "after the rebuild")
    R.counts = {"rebuild": {"ran": False, "why": "nothing new was fetched"}}
    assert D.Run.models_wanted(R)[0] is False
    R.args = argparse.Namespace(no_models=False, dry_run=False, models=True, models_only=False, rebuild_only=False)
    assert D.Run.models_wanted(R) == (True, "--models")
    R.args = argparse.Namespace(no_models=True, dry_run=False, models=True, models_only=False, rebuild_only=False)
    assert D.Run.models_wanted(R) == (False, "--no-models")
    R.args = argparse.Namespace(no_models=False, dry_run=False, models=False, models_only=False, rebuild_only=False)
    R.counts, R.errors = {"rebuild": {"ran": True}}, {"rebuild": "x"}
    assert D.Run.models_wanted(R) == (False, "the rebuild failed")
    R.args = argparse.Namespace(no_models=False, dry_run=False, models=False, models_only=False, rebuild_only=True)
    R.counts, R.errors = {"rebuild": {"ran": True}}, {}
    assert D.Run.models_wanted(R)[0] is False


# ── the live season's schedule and the routes' in-season rules ───────────────

@pytest.fixture(scope="module")
def cur():
    if not _db_reachable():
        pytest.skip("Postgres DB is not reachable")
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    yield conn.cursor()
    conn.close()


def test_live_schedule_matches_the_locked_schedule(cur):
    """The simulator's live schedule (ledger_results, else ledger_schedule) reproduces the locked schedule's games and
    back-to-back flags from the dates alone; the season after the paper's is live, the paper's test season is not."""
    cur.execute("SELECT to_regclass('ledger_schedule')")
    if cur.fetchone()[0] is None:
        pytest.skip("no locked schedule")
    live = MAX_PAPER_SEASON + 1
    s = SL.schedule(cur, live)
    cur.execute("SELECT espn_id, home, away, home_b2b, away_b2b FROM ledger_schedule WHERE season = %s AND counted", (live,))
    locked = {e: (h, a, hb, ab) for e, h, a, hb, ab in cur.fetchall()}
    assert len(s) >= 1200 and set(s.espn_id) >= set(locked)
    mism = [e for e, h, a, hb, ab in zip(s.espn_id, s.home, s.away, s.home_b2b, s.away_b2b) if e in locked and locked[e] != (h, a, hb, ab)]
    assert not mism, f"{len(mism)} games differ from the locked schedule"
    cps = SL.checkpoint_dates(s)
    assert cps["opening"] == s.game_date.min() and cps["last"] == s.game_date.max() and cps["opening"] < cps["halfway"] < cps["sixty"] < cps["last"]
    left = SL.remaining_home_rows(s, cps["halfway"])
    assert set(left.columns) >= {"home", "away", "venue", "home_b2b", "away_b2b"} and 0 < len(left) < len(s)
    from routers import season_sim as SS
    seasons = {x["season"]: x for x in SS._stored()["seasons"]}
    assert not seasons[MAX_PAPER_SEASON]["live"] and all(not x["live"] for s_, x in seasons.items() if s_ <= MAX_PAPER_SEASON)
    assert SL.clamp(date(2000, 1, 1), cps["opening"], cps["last"]) == cps["opening"]


def test_award_routes_scale_the_games_floor_in_season():
    import mvp_api as M
    full = {"in_season": False, "games_played_max": 82, "through": None}
    live = {"in_season": True, "games_played_max": 10, "through": "2026-11-01"}
    assert M.scaled_games_floor(full, 40) == 40 and M.scaled_games_floor(live, 40) == 5
    assert M.scaled_games_floor({"in_season": True, "games_played_max": 0, "through": None}, 40) == 1
    f = M.in_season_fields(live, "rule")
    assert f["in_season"] and "through 2026-11-01" in f["in_season_note"] and "(rule)" in f["in_season_note"]
    assert M.in_season_fields(full)["in_season_note"] is None


def test_projection_actuals_use_the_projections_own_units(monkeypatch):
    from routers import projections as PJ
    assert PJ._actual_sql("pts36", {"kind": "per36", "source_col": "pts"}) == ("pts * 36.0 / NULLIF(min, 0)", "min * gp")
    assert PJ._actual_sql("pts", {"kind": "per_game", "source_col": "pts"}) == ("pts", "gp")
    assert PJ._actual_sql("min", {"kind": "mpg", "source_col": "min"}) == ("min", "gp")
    assert PJ._actual_sql("fg3_pct", {"kind": "pct", "source_col": "fg3_pct"}) == ("fg3_pct", "fg3a * gp")
    assert PJ._actual_sql("bpm", {"kind": "bpm", "source_col": "bpm"}) == (None, None)
    rows = [{"player_id": 1, "projection": 20.0, "lo": 15.0, "hi": 25.0}, {"player_id": 2, "projection": 10.0, "lo": 8.0, "hi": 12.0}]
    monkeypatch.setattr(PJ, "_actuals", lambda season, stat: ({1: (22.0, 10.0), 2: (14.0, 9.0)}, "2026-11-01"))
    summary = PJ._with_actuals(rows, 2027, "pts")
    assert rows[0]["gap"] == 2.0 and rows[0]["in_range"] and rows[1]["gap"] == 4.0 and rows[1]["in_range"] is False
    assert summary["players"] == 2 and summary["share_in_range"] == 0.5 and summary["through"] == "2026-11-01"


def test_stat_stability_reliability_on_a_small_sample():
    """The frozen M applied to a season-to-date sample: the Leaderboard's rule, n / (n + M)."""
    from routers.leaderboard import _sample
    r = _sample(40, 474)      # 40 three-point attempts against 3P%'s M of 474 (the route rounds to three decimals)
    assert abs(r["reliability"] - 40 / 514) < 5e-4 and r["noisy"]
    assert not _sample(600, 474)["noisy"]
    assert np.isclose(_sample(0, 474)["reliability"], 0.0)
