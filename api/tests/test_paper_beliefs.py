"""
test_paper_beliefs.py
======================
Guards round 5 step 6, the popular-beliefs framework (scripts/paper_beliefs.py ->
paper_beliefs, paper_beliefs_summary, paper_beliefs_meta, paper/tables/beliefs.tex):

  * every family's summary counts re-derive from its stored unit rows (n, the
    p < 0.05 count, 0.05 n, and the Benjamini-Hochberg survivors from the stored
    p-values), so the summary can never drift from the rows;
  * every unit's p-value is a valid permutation p: (1 + r) / (B + 1) with r a
    count, never zero, never above one, and its null interval holds its null mean;
  * every unit still carries the platform's own number (platform_stat): clutch
    lifts equal player_wpa_totals.clutch_lift, league streak slopes equal
    hot_streak_persistence.slope, split gaps equal player_situational_splits.vs_league,
    franchise luck means equal team_luck_schedule, referee differences equal
    referee_tendencies' (a rebuild of any of those without a rerun fails here);
    for the streak, luck and referee families the tested statistic is that number,
    for clutch and splits it is the proportional-adjustment twin, which agrees in
    sign with the platform's for nearly every unit;
  * the table rows the paper prints are the script's TABLE_ROWS, each with a macro
    stem, and the table file (untracked; skipped when absent) uses only macros
    paper_numbers.py defines;
  * the Miller-Sanjurjo check is stored: every streak family has a positive null
    centre and a null-centred share.

Skips when the database is unreachable or the script was never run.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_beliefs.py
"""

import os
import re
import sys

import numpy as np
import psycopg2
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in (os.path.join(_ROOT, "api"), os.path.join(_ROOT, "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import paper_beliefs as PB  # noqa: E402
import paper_numbers as P  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402


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
    c.execute("SELECT to_regclass('paper_beliefs') IS NOT NULL AND to_regclass('paper_beliefs_summary') IS NOT NULL")
    if not c.fetchone()[0]:
        conn.close()
        pytest.skip("paper_beliefs.py has not been run")
    yield c
    conn.close()


@pytest.fixture(scope="module")
def summary(cur):
    cur.execute("""SELECT key, family, n_units, k05, expected05, k_fdr, perms1, perms2, agg_obs, agg_null_mean, agg_adjusted,
                          macro, in_table FROM paper_beliefs_summary""")
    return {r[0]: r[1:] for r in cur.fetchall()}


def test_table_rows_are_the_script_list(summary):
    stems = {k: v[10] for k, v in summary.items() if v[11]}
    assert stems == {k: m for _f, k, m, _l, _u in PB.TABLE_ROWS}
    assert len({m for _f, _k, m, _l, _u in PB.TABLE_ROWS}) == len(PB.TABLE_ROWS)
    for _f, _k, m, _l, _u in PB.TABLE_ROWS:
        assert re.fullmatch(r"[A-Z][A-Za-z]*", m), m


@needs_db
def test_summary_counts_rederive_from_unit_rows(cur, summary):
    cur.execute("SELECT key, p_value, bh_reject, bh_q FROM paper_beliefs ORDER BY key, unit_id, season")
    by_key = {}
    for key, p, rj, q in cur.fetchall():
        by_key.setdefault(key, []).append((p, rj, q))
    for key, (family, n, k05, exp, kfdr, *_r) in summary.items():
        rows = by_key.get(key, [])
        assert len(rows) == n, key
        assert exp == pytest.approx(0.05 * n), key
        p = np.array([r[0] for r in rows])
        assert int((p < 0.05).sum()) == k05, key
        reject, qv = PB.bh(p)
        assert int(reject.sum()) == kfdr, key
        assert [bool(r[1]) for r in rows] == reject.tolist(), key
        assert np.allclose([r[2] for r in rows], qv), key
    # Families with no unit rows are the aggregate-only ones (the season-only streak slopes).
    assert {k for k in summary if k not in by_key} == {k for k, v in summary.items() if k.startswith("streak0:")}
    assert all(summary[k][1] == 0 for k in summary if k.startswith("streak0:"))


@needs_db
def test_p_values_are_valid_permutation_ps(cur):
    cur.execute("""SELECT p_value, n_perm, stat, null_mean, null_lo, null_hi, null_sd FROM paper_beliefs""")
    for p, b, stat, m, lo, hi, sd in cur.fetchall():
        assert 0 < p <= 1
        r = p * (b + 1) - 1
        assert abs(r - round(r)) < 1e-6 and 0 <= round(r) <= b, (p, b)
        assert lo <= m <= hi and sd >= 0 and np.isfinite(stat)


@needs_db
def test_families_and_floors(summary):
    families = {v[0] for v in summary.values()}
    assert families == set(PB.FAMILIES)
    for floor in PB.CLUTCH_FLOORS:
        assert f"clutch:{floor}" in summary
    assert all(v[5] == PB.PERMS for k, v in summary.items() if k.startswith(("clutch", "split")))
    assert all(v[5] == PB.STREAK_PERMS for k, v in summary.items() if k.startswith("streak"))


@needs_db
def test_clutch_statistics_equal_player_wpa_totals(cur):
    cur.execute("""SELECT b.unit_id::bigint, b.platform_stat, w.clutch_lift, b.n_obs, w.clutch_chances, b.stat
                   FROM paper_beliefs b JOIN player_wpa_totals w ON w.person_id = b.unit_id::bigint
                   WHERE b.key = 'clutch:50'""")
    rows = cur.fetchall()
    assert len(rows) >= 300
    for _pid, platform, stored, n, chances, _stat in rows:
        assert abs(platform - stored) <= 5.1e-5 and n == chances
    # The proportional statistic is the additive one's twin: same sign for nearly everyone, close in size.
    same_sign = sum(1 for _p, platform, _s, _n, _c, stat in rows if (stat > 0) == (platform > 0))
    assert same_sign >= 0.9 * len(rows)
    assert max(abs(stat - platform) for _p, platform, _s, _n, _c, stat in rows) < 0.2
    cur.execute("SELECT count(*) FROM player_wpa_totals WHERE clutch_chances >= 50")
    assert cur.fetchone()[0] == len(rows)


@needs_db
def test_streak_slopes_equal_hot_streak_persistence(cur, summary):
    cur.execute("SELECT stat, window_games, slope, slope_season_only FROM hot_streak_persistence")
    for stat, n, slope, slope0 in cur.fetchall():
        assert abs(summary[f"streak:{stat}:{n}"][7] - slope) < 1e-6, (stat, n)
        assert abs(summary[f"streak0:{stat}:{n}"][7] - slope0) < 1e-6, (stat, n)
        # The Miller-Sanjurjo check: a positive null centre and the null-centred share stored beside it.
        obs, null_mean, adjusted = summary[f"streak:{stat}:{n}"][7:10]
        assert null_mean > 0 and abs(adjusted - (obs - null_mean)) < 1e-9, (stat, n)
    cur.execute("SELECT max(abs(stat - platform_stat)) FROM paper_beliefs WHERE family IN ('streak', 'referee')")
    assert cur.fetchone()[0] == 0


@needs_db
def test_split_statistics_equal_player_situational_splits(cur):
    cur.execute("""SELECT count(*), max(abs(b.platform_stat - s.vs_league)), sum((b.n_obs = s.games_a + s.games_b)::int)
                   FROM paper_beliefs b
                   JOIN player_situational_splits s
                     ON s.player_id = split_part(b.unit_id, '_', 1)::bigint AND s.season = b.season
                    AND b.key = 'split:' || s.split || ':' || s.stat AND s.qualified
                   WHERE b.family = 'split'""")
    n, dev, same_n = cur.fetchone()
    cur.execute("SELECT count(*) FROM player_situational_splits WHERE qualified")
    assert n == cur.fetchone()[0] == same_n and dev < 1e-9
    cur.execute("SELECT count(*) FROM paper_beliefs WHERE family = 'split'")
    assert cur.fetchone()[0] == n
    # The proportional twin: a zero rate stays a zero, and the two agree in sign for nearly every unit.
    cur.execute("""SELECT count(*), sum(((stat > 0) = (platform_stat > 0))::int), max(abs(stat - platform_stat))
                   FROM paper_beliefs WHERE family = 'split'""")
    n, same, dev = cur.fetchone()
    assert same >= 0.9 * n and dev < 3


@needs_db
def test_luck_and_referee_statistics_equal_the_platform(cur, summary):
    cur.execute("""SELECT max(abs(b.stat - t.m)), max(abs(b.stat - b.platform_stat)) FROM paper_beliefs b
                   JOIN (SELECT franchise, avg(luck_per82) m FROM team_luck_schedule GROUP BY 1) t ON t.franchise = b.unit_id
                   WHERE b.key = 'luck:luck_per82'""")
    assert max(cur.fetchone()) < 1e-9
    cur.execute("SELECT count(*) FROM paper_beliefs WHERE key = 'luck:luck_per82'")
    assert cur.fetchone()[0] == 30
    cur.execute("SELECT value FROM luck_schedule_validation WHERE metric = 'luck_next_luck_r'")
    assert abs(summary["luck:luck_per82"][7] - cur.fetchone()[0]) < 1e-9
    for c, col in (("fouls", "fouls_diff"), ("fta", "fta_diff")):
        cur.execute(f"""SELECT count(*), max(abs(b.stat - r.{col})), sum((b.n_obs = r.n_games)::int) FROM paper_beliefs b
                        JOIN referee_tendencies r ON r.official_id = b.unit_id::bigint WHERE b.key = 'referee:official:{c}'""")
        n, dev, same = cur.fetchone()
        cur.execute("SELECT count(*) FROM referee_tendencies WHERE n_games >= %s", (PB.REF_FLOOR,))
        assert n == cur.fetchone()[0] == same and dev <= 5.1e-4
        cur.execute(f"""SELECT count(*), max(abs(b.stat - r.{col})) FROM paper_beliefs b
                        JOIN referee_crew_tendencies r ON r.crew_key = b.unit_id WHERE b.key = 'referee:crew:{c}'""")
        n, dev = cur.fetchone()
        cur.execute("SELECT count(*) FROM referee_crew_tendencies WHERE n_games >= %s", (PB.CREW_FLOOR,))
        assert n == cur.fetchone()[0] and dev <= 5.1e-4


@needs_db
def test_table_file_uses_only_defined_macros(cur):
    if not os.path.exists(PB.TABLE_TEX):
        pytest.skip("paper/tables/beliefs.tex is not on this machine (paper/ is untracked)")
    with open(PB.TABLE_TEX) as f:
        tex = f.read()
    used = P.used_macros(tex)
    assert used, "the table names no macro"
    # Every count cell is a macro (the row labels may name definitional floors and windows: 100+ chances, last 10 games).
    body = [line for line in P.strip_comments(tex).splitlines() if line.count("&") == 5]
    assert len(body) == len(PB.TABLE_ROWS) + 1, "one data row per TABLE_ROWS entry plus the header"
    for line in body[1:]:
        cells = [c.strip().rstrip("\\").strip() for c in line.split("&")]
        assert all(re.fullmatch(r"\\pnBl[A-Za-z]+", c) for c in cells[2:]), line
    N = P.Numbers()
    N.raw["games"] = (0, 0)
    P.beliefs(cur, N)
    assert not N.failed, N.failed
    assert used <= N.names, sorted(used - N.names)
