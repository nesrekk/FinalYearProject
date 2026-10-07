"""
test_live_season.py
====================
Round 9 step 1: scripts/live_season.py classifies every table for the live 2026-27 season and docs/LIVE_SEASON.md
carries its table. Checks: every table has a class; a table may be daily or season-to-date only if it has a
season dimension (api/paper_freeze.paper_predicate), so the paper's rows can be told from the season's; the
doc's classification is current (the numeric columns are dated and not compared).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_live_season.py
"""

import os
import sys

import psycopg2
import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API_DIR)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _d in (_API_DIR, _SCRIPTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import live_season as LS  # noqa: E402
import paper_freeze as PF  # noqa: E402
import paper_manifest as PM  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402
import local_only  # noqa: E402


def _db_reachable():
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")
# The classification is of the local database's 219 tables; the mirror leaves out LOCAL_ONLY on purpose (R9-044).
whole_db = pytest.mark.skipif(local_only.on_mirror(), reason="classifies every local table; the mirror has no LOCAL_ONLY tables")


@pytest.fixture(scope="module")
def rows():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    try:
        return LS.inventory(conn)
    finally:
        conn.close()


def test_every_producer_and_override_is_known():
    assert set(LS.BY_PRODUCER) <= set(PM.PRODUCERS), set(LS.BY_PRODUCER) - set(PM.PRODUCERS)
    assert set(LS.OVERRIDES) <= set(PM.TABLES), set(LS.OVERRIDES) - set(PM.TABLES)
    assert set(LS.NOTES) <= set(PM.TABLES), set(LS.NOTES) - set(PM.TABLES)
    assert set(LS.BY_PRODUCER.values()) | set(LS.OVERRIDES.values()) <= set(LS.CLASSES)
    for t in PM.TABLES:
        assert LS.classify(t) in LS.CLASSES, f"{t} ({PM.TABLES[t][1]}) has no live-season class"


@needs_db
def test_tables_that_change_during_the_season_have_a_season_dimension(rows):
    bad = [r["table"] for r in rows if r["cls"] in ("daily", "season-to-date") and r["paper_rows"] != "capped"]
    assert not bad, f"daily / season-to-date without a season dimension (the paper's rows can't be told apart): {bad}"
    assert all(r["paper_rows"] == "lock" for r in rows if r["cls"] == "ledger")
    assert {r["table"] for r in rows if r["cls"] == "ledger"} == PF.LEDGER_TABLES


@needs_db
@whole_db
def test_frozen_covers_the_paper_and_the_pooled_tables(rows):
    cls = {r["table"]: r["cls"] for r in rows}
    assert all(cls[t] == "frozen" for t, (kind, _) in PM.TABLES.items() if kind == "paper"), "a paper table isn't frozen"
    for t in ("stat_stability", "hot_streak_persistence", "rating_tracker_fit", "shot_value_fit", "season_sim_params",
              "pregame_model_fit", "luck_model_fit", "player_wpa_totals", "referee_tendencies", "shot_making_validation"):
        assert cls[t] == "frozen", t


@needs_db
@whole_db
def test_doc_table_is_current(rows):
    md = LS.markdown(rows)
    assert LS.classification_rows(md) == LS.classification_rows(LS.doc_block()), \
        "docs/LIVE_SEASON.md's table is stale: cd scripts && python3 live_season.py, paste the table between the markers"
    assert len(LS.classification_rows(md)) == len(rows) == len(PM.db_tables(psycopg2.connect(**DB_CONFIG).cursor()))
