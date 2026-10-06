"""
test_season_rebuild.py
======================
Round 9 step 3 (2026-10-06): the season-level builds' `--season N` mode (scripts/season_mode.py), which deletes and
rebuilds one season's rows through the same code as the full build, and the daily update's rebuild phase.

The main proof: the fourteen builds of the daily chain, run with `--season 2026` in `rebuild_all.sh` order into
copies of their tables in the schema `zz_season_rebuild` (search_path through PGOPTIONS, the ledger-gameday pattern;
the public tables are never written), give **byte-identical 2025-26 rows** to the stored full build's (the
content hash of the season's rows, computed the same way on both sides before any build ran), leave every other
season's rows and every table without a season dimension (the `*_meta` tables, `best_games_meta`,
`leverage_index_grid`) exactly as they were, and leave the public tables untouched. The chain's wall time is the
daily rebuild's: it is printed per step and asserted under the step's budget.

Local database only (it creates and drops a schema; ~6-9 min: the copies ~1.5 min, the chain ~5-7 min).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_season_rebuild.py
"""

import os
import re
import subprocess
import sys
import time

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
import season_mode as SM  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402

PY = sys.executable
SCHEMA = "zz_season_rebuild"
SEASON = 2026
NEIGHBOUR = 2025            # the one other season copied into the biggest event tables (the "untouched" check)
BUDGET_SECONDS = 15 * 60    # the chain's wall time (the plan's target for a day's rebuild is 10 minutes on the M5)

EVENTS_OF_SEASON = ("event_id IN (SELECT e.id FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id "
                    "WHERE g.source = 'espn' AND g.season = {s})")
PF_EVENTS_OF_SEASON = "game_no IN (SELECT game_no FROM play_finder_games WHERE season = {s})"

# The daily chain in rebuild_all.sh order: script -> {table written: the rows of season {s}}.
STEPS = [
    ("build_event_clock.py", {"pbp_event_clock": EVENTS_OF_SEASON}),
    ("build_player_game_lines.py", {"player_game_lines": "season = {s}"}),
    ("build_team_game_totals.py", {"team_game_totals": "season = {s}"}),
    ("build_lineup_stints.py", {t: "season = {s}" for t in ("lineup_stints", "lineup_stint_games", "lineup_stint_seasons",
                                                            "lineup_seasons", "pair_seasons")}),
    ("build_player_game_onfloor.py", {"player_game_onfloor": "season = {s}"}),
    ("build_player_on_off.py", {"player_on_off": "season = {s}", "player_on_off_seasons": "season = {s}"}),
    ("build_possessions.py", {t: "season = {s}" for t in ("possessions", "possession_games", "possession_seasons")}),
    ("build_situational_splits.py", {"player_situational_splits": "season = {s}", "situational_split_league": "season = {s}"}),
    ("build_rotations.py", {"rotation_closing_games": "season = {s}", "rotation_closing_stints": "season = {s}"}),
    ("build_rim_deterrence.py", {"rim_deterrence": "season = {s}", "rim_deterrence_seasons": "season = {s}"}),
    ("build_assist_network.py", {t: "season = {s}" for t in ("assist_pairs", "player_assisted_share", "assist_seasons")}),
    ("build_play_finder.py", {"play_finder_events": PF_EVENTS_OF_SEASON, "play_finder_games": "season = {s}",
                              "play_finder_seasons": "season = {s}"}),
    ("build_best_games.py", {"best_games": "season = {s}"}),
    ("build_leverage_splits.py", {t: "season = {s}" for t in ("player_leverage_splits", "player_leverage_summary",
                                                              "leverage_validation")}),
]
WRITTEN = {t: pred for _, tables in STEPS for t, pred in tables.items()}
# tables without a season dimension the --season mode must leave alone (copied whole, hashed before and after)
UNTOUCHED = ["pbp_event_clock_meta", "player_game_onfloor_meta", "possession_meta", "best_games_meta", "leverage_index_grid"]
# the biggest event tables are copied with one neighbouring season only (the season under test is rebuilt anyway);
# pbp_event_clock is copied whole because the deflator's pooled constants read every event's clock
PARTIAL = {"possessions": f"season = {NEIGHBOUR}", "play_finder_events": PF_EVENTS_OF_SEASON.format(s=NEIGHBOUR)}


def _db_reachable():
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


def _hash(cur, table, where=None):
    """(rows, content hash): the two 64-bit halves of each row's md5 summed (paper_manifest.content_hash's rule, no
    sort: order-independent and fast on millions of rows), the same on either side of the same session."""
    cur.execute(f"""SELECT count(*), sum(('x' || substr(h, 1, 16))::bit(64)::bigint::numeric),
                           sum(('x' || substr(h, 17, 16))::bit(64)::bigint::numeric)
                    FROM (SELECT md5(t::text) AS h FROM {table} t{' WHERE ' + where if where else ''}) s""")
    n, a, b = cur.fetchone()
    return n, a or 0, b or 0


def _rest(cur, table, pred):
    """The hash of every row NOT of the season: the whole table's sums less the season's (the sums are additive), which
    spares an anti-join over millions of event rows."""
    whole, part = _hash(cur, table), _hash(cur, table, pred)
    return tuple(w - p for w, p in zip(whole, part))


def copy_table(cur, table, schema=SCHEMA, where=None):
    """A copy of public.<table> in `schema` with its primary key and indexes (built after the rows, as the builds do)."""
    cur.execute(f"CREATE TABLE {schema}.{table} (LIKE public.{table} INCLUDING DEFAULTS)")
    cur.execute(f"INSERT INTO {schema}.{table} SELECT * FROM public.{table}{' WHERE ' + where if where else ''}")
    cur.execute("""SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
                   WHERE conrelid = ('public.' || %s)::regclass AND contype = 'p'""", (table,))
    for name, cdef in cur.fetchall():
        cur.execute(f"ALTER TABLE {schema}.{table} ADD CONSTRAINT {name} {cdef}")
    cur.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = %s", (table,))
    for (idef,) in cur.fetchall():
        if " UNIQUE INDEX " in idef and re.search(r"_pkey ON ", idef):
            continue        # the primary key's own index
        cur.execute(re.sub(r" ON public\.", f" ON {schema}.", idef, count=1))
    cur.execute(f"ANALYZE {schema}.{table}")


def make_schema(cur, schema=SCHEMA):
    cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    cur.execute(f"CREATE SCHEMA {schema}")
    for t in list(WRITTEN) + UNTOUCHED:
        copy_table(cur, t, schema, PARTIAL.get(t))


def run_step(script, season, schema=SCHEMA, timeout=1800):
    env = {**os.environ, "PGOPTIONS": f"-c search_path={schema},public", "OMP_NUM_THREADS": "4"}
    t0 = time.time()
    r = subprocess.run([PY, script, "--season", str(season)], cwd=_SCRIPTS, env=env, capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, time.time() - t0, r.stdout + r.stderr


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
    before = {t: _hash(cur, f"public.{t}", pred.format(s=SEASON)) for t, pred in WRITTEN.items()}
    public_counts = {t: _hash(cur, f"public.{t}")[0] for t in WRITTEN}
    make_schema(cur)
    copy_seconds = time.time() - t0
    others = {t: _rest(cur, f"{SCHEMA}.{t}", pred.format(s=SEASON)) for t, pred in WRITTEN.items()}
    untouched = {t: _hash(cur, f"{SCHEMA}.{t}") for t in UNTOUCHED}
    runs = {}
    try:
        for script, _ in STEPS:
            runs[script] = run_step(script, SEASON)
            if runs[script][0] != 0:
                break
        after = {t: _hash(cur, f"public.{t}", pred.format(s=SEASON)) for t, pred in WRITTEN.items()}
        yield {"cur": cur, "runs": runs, "before": before, "after": after, "others": others, "untouched": untouched,
               "public_counts": public_counts, "copy_seconds": copy_seconds}
    finally:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.close()


@pytest.mark.parametrize("script,tables", STEPS, ids=[s for s, _ in STEPS])
def test_season_rows_equal_the_full_build(chain, script, tables):
    """The season's rows from `--season 2026` equal the stored full build's, other seasons' rows are untouched."""
    run = chain["runs"].get(script)
    assert run is not None, f"{script} did not run (an earlier step failed)"
    code, seconds, out = run
    assert code == 0, f"{script} failed ({seconds:.0f}s):\n{out[-4000:]}"
    cur = chain["cur"]
    for t, pred in tables.items():
        mine = _hash(cur, f"{SCHEMA}.{t}", pred.format(s=SEASON))
        assert mine == chain["before"][t], f"{script}: {t}'s {SEASON} rows differ from the stored full build's " \
                                           f"(rows {mine[0]} vs {chain['before'][t][0]})"
        rest = _rest(cur, f"{SCHEMA}.{t}", pred.format(s=SEASON))
        assert rest == chain["others"][t], f"{script}: {t}'s other seasons' rows changed"
    print(f"\n{script}: {seconds:.0f}s, {', '.join(f'{t} {chain['before'][t][0]:,} rows' for t in tables)}")


def test_tables_without_a_season_dimension_are_left_alone(chain):
    cur = chain["cur"]
    for t in UNTOUCHED:
        assert _hash(cur, f"{SCHEMA}.{t}") == chain["untouched"][t], f"{t} changed"


def test_public_tables_were_not_written(chain):
    cur = chain["cur"]
    assert chain["after"] == chain["before"], "a --season build wrote a public table"
    assert {t: _hash(cur, f"public.{t}")[0] for t in WRITTEN} == chain["public_counts"]


def test_chain_runs_within_the_daily_budget(chain):
    secs = {s: r[1] for s, r in chain["runs"].items()}
    total = sum(secs.values())
    print(f"\ncopies {chain['copy_seconds']:.0f}s; chain {total:.0f}s: " + ", ".join(f"{s} {v:.0f}s" for s, v in secs.items()))
    assert len(secs) == len(STEPS) and total < BUDGET_SECONDS, f"{total:.0f}s for {len(secs)} of {len(STEPS)} steps"


# ── season_mode.py and the daily update's rebuild phase (no chain) ─────────────

def test_parse_season_and_the_sequential_ids():
    assert SM.parse_season(["--season", "2027"]) == 2027 and SM.parse_season(["--dry-run"]) is None
    assert SM.season_label(2027) == "2026-27"
    with pytest.raises(SystemExit):
        SM.parse_season(["--season"])
    if not _db_reachable():
        pytest.skip("Postgres DB is not reachable")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM lineup_stints WHERE season < 2026")
    n_before = cur.fetchone()[0]
    cur.execute("SELECT min(stint_id), count(*) FROM lineup_stints WHERE season = 2026")
    first, n = cur.fetchone()
    assert first == n_before + 1 == SM.next_id(cur, "lineup_stints", "stint_id", 2026, n)
    cur.execute("SELECT count(*) FROM play_finder_games WHERE season < 2026")
    n_pf = cur.fetchone()[0]
    assert SM.next_id(cur, "play_finder_games", "game_no", 2026, 1) == n_pf + 1
    with pytest.raises(SystemExit):        # 2024-25's rows can't grow into 2025-26's ids
        SM.next_id(cur, "lineup_stints", "stint_id", 2025, 10 ** 6)
    with pytest.raises(SystemExit):
        SM.require_tables(cur, ["zz_no_such_table"], 2027)
    conn.close()


def test_daily_update_rebuilds_in_rebuild_all_order():
    with open(os.path.join(_SCRIPTS, "rebuild_all.sh")) as f:
        plan = [ln.split()[3] for ln in f if ln.startswith("step ")]
    chain_scripts = [s for s, _ in STEPS]
    assert D.REBUILD_STEPS == chain_scripts
    assert [s for s in plan if s in chain_scripts] == chain_scripts, "not rebuild_all.sh's order"
    for s in chain_scripts:
        with open(os.path.join(_SCRIPTS, s)) as f:
            text = f.read()
        assert "parse_season()" in text or '"--season"' in text, f"{s} takes no --season"
        assert "--season" in text.split('"""')[1], f"{s}'s docstring doesn't say how --season works"
