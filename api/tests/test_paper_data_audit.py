"""
test_paper_data_audit.py
=========================
Guards round 5 step 5, the data-quality audit (scripts/paper_data_audit.py ->
paper_data_audit, paper_data_audit_classes, paper/tables/data_audit.tex):

  * the stored class list is the script's CLASSES, every class has a known
    handling kind, and no size cell types a measured number by hand (only \\pn
    macros, plus the definitional constants the cells name: 5 s, 99th, 0, 1+ ...);
  * every row paper_numbers.py prints has a known format and a unique macro, and
    every macro a size cell uses is defined by paper_numbers.py;
  * the audit still describes the database: its counts equal live queries on
    the tables it measured (so a rebuild without a rerun of the audit fails
    here), the wrong-player repair has nothing left to fix, and the counts the
    README recorded before the audit re-derive exactly (66 events in 9 games,
    8,688 missed threes at a median 26 ft; 8,708 since round 8 step 6a);
  * the table file in paper/ (untracked on purpose; skipped when absent) is the
    one the class list generates.

Skips when the database is unreachable or the audit was never run.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_data_audit.py
"""

import os
import re
import sys

import psycopg2
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in (os.path.join(_ROOT, "api"), os.path.join(_ROOT, "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import paper_data_audit as DA  # noqa: E402
import paper_numbers as P  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402

# Definitional constants a size cell may name (thresholds and units, not measurements).
ALLOWED = ("over 5 s", "within 2 s", "99th percentile", "1+ point", "$\\neq 5\\times$", "$(0,0)$", "1 February", "$=0$", "$\\div 5$")


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    c = conn.cursor()
    c.execute("SELECT to_regclass('paper_data_audit') IS NOT NULL AND to_regclass('paper_data_audit_classes') IS NOT NULL")
    if not c.fetchone()[0]:
        conn.close()
        pytest.skip("paper_data_audit.py has not been run")
    yield c
    conn.close()


@pytest.fixture(scope="module")
def audit(cur):
    cur.execute("SELECT key, season, value FROM paper_data_audit")
    return {(k, s): v for k, s, v in cur.fetchall()}


def test_size_cells_hold_no_hand_typed_number():
    for _feed, key, _name, how, size, kind, _handling in DA.CLASSES:
        assert kind in DA.KINDS, key
        rest = re.sub(r"\\pn[A-Z][A-Za-z]*(\{\})?", "", size)
        for token in ALLOWED:
            rest = rest.replace(token, "")
        assert not re.search(r"\d", rest), (key, rest)
        assert not re.search(r"\d", re.sub(r"\$[^$]*\$", "", how).replace("1 February", "")), key


@needs_db
def test_stored_classes_are_the_script_list(cur):
    cur.execute("SELECT feed, key, error_class, detection, size_tex, handling_kind, handling FROM paper_data_audit_classes ORDER BY ord")
    assert [tuple(r) for r in cur.fetchall()] == [tuple(c) for c in DA.CLASSES]


@needs_db
def test_printed_rows_have_known_formats_and_defined_macros(cur):
    cur.execute("SELECT macro, fmt FROM paper_data_audit WHERE macro IS NOT NULL")
    printed = cur.fetchall()
    assert len({m for m, _ in printed}) == len(printed)
    assert all(f in P.AUDIT_FORMATS for _, f in printed)
    conn = P.connect()
    try:
        defined = P.defined_macros(P.build(conn))
    finally:
        conn.close()
    used = set().union(*(P.used_macros(c[4]) for c in DA.CLASSES))
    assert used <= defined, sorted(used - defined)


@needs_db
def test_audit_matches_the_database_now(cur, audit):
    cur.execute("SELECT count(*) - sum(game_ok::int) FROM lineup_stint_games WHERE season <= 2026")  # the paper's seasons
    assert audit[("unrec_games", 0)] == cur.fetchone()[0]
    cur.execute("SELECT count(*), count(DISTINCT game_id) FROM pbp_events WHERE game_id IN (SELECT game_id FROM pbp_games WHERE source = 'nba_api')")
    assert (audit[("twin_rows", 0)], audit[("twin_games", 0)]) == cur.fetchone()
    assert audit[("twin_same_teams", 0)] + audit[("twin_neutral", 0)] == audit[("twin_games", 0)]
    cur.execute("""SELECT count(DISTINCT f.game_id) FROM team_game_fatigue f JOIN game_scores g
                   ON g.game_id = f.game_id AND g.team_abbreviation = f.team_abbreviation
                   WHERE abs(f.plus_minus - (g.pts_for - g.pts_against)) > 1e-6 AND f.season <= 2026""")
    assert audit[("pm_bad", 0)] == cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM pbp_events e JOIN pbp_games g USING (game_id)
                   WHERE g.source = 'espn' AND coalesce(e.player_name, '') <> '' AND e.person_id IS NULL""")
    assert audit[("unid_events", 0)] == cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM player_game_lines WHERE team_abbreviation = 'NaN' OR team_abbreviation IS NULL")
    assert audit[("nan_team_rows", 0)] == cur.fetchone()[0]
    per_season = [v for (k, s), v in audit.items() if k == "wrong_team_rows" and s]
    assert sum(per_season) == audit[("wrong_team_rows", 0)] and all(3 <= v <= 5 for v in per_season)


@needs_db
def test_recorded_counts_re_derive(audit):
    # Before the audit these were copied from the README (paper_numbers.RECORDED, 2026-09-29); the audit re-derives them.
    assert audit[("wrong_player_left", 0)] == 0                       # repair_espn_player_ids.find() finds nothing
    assert (audit[("wrong_player_events", 0)], audit[("wrong_player_games", 0)]) == (66, 9)
    # 8,688 when first counted; 8,708 since round 8 step 6a matched the feed's no-id players by name, so more of their
    # missed shots line up with the chart (step 6b reran the audit); 8,709 since round 8.5 step C re-fetched the 2025-26
    # chart (the four games it lacked add one)
    assert audit[("miss_threes_as_twos", 0)] == 8709 and round(audit[("miss_threes_median_ft", 0)]) == 26
    # The feed still holds 12 team-less substitutions in 9 games, but since round 8 step 6a the parser ignores the ones
    # naming nobody leaving, so no game line gains seconds from them (until then: one player-game in each of the 9 games)
    assert (audit[("teamless_subs", 0)], audit[("teamless_games", 0)]) == (12, 9)
    assert (audit[("teamless_player_games", 0)] == audit[("teamless_lines_higher", 0)]
            == audit[("teamless_pg_in_sub_games", 0)] == 0)


def test_table_file_is_generated_from_the_class_list():
    if not os.path.exists(DA.TEX_OUT):
        pytest.skip("paper/ is untracked and not present in this checkout")
    with open(DA.TEX_OUT) as f:
        assert f.read() == DA.tex_table()
