"""
Round 8 step 10: close-out, the Layerbase slimming. Six paper-only per-unit tables (api/local_only.py's
LOCAL_ONLY) stay in the local database and are dropped from the Layerbase mirror. These tests pin that:

  * no page or route reads them (only the Data Coverage map names one, and it says "kept local" on the mirror);
  * every one is a table a script in this repository writes (paper_manifest.PRODUCERS);
  * scripts/migrate_to_layerbase.py and paper_manifest.py --compare use the same list;
  * --compare skips them and still catches a changed table;
  * on DB_TARGET=layerbase a test stopped by their absence is skipped (conftest.py), and only for these tables.
"""

import copy
import os
import re
import sys

import psycopg2
import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API_DIR)
for _d in (os.path.join(_ROOT, "scripts"), _API_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import conftest  # noqa: E402
import local_only  # noqa: E402
import paper_manifest as PM  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")
needs_manifest = pytest.mark.skipif(not os.path.exists(PM.MANIFEST_JSON), reason="paper/manifest.json not built")


def _code_files():
    for base, exts in ((os.path.join(_ROOT, "api"), (".py",)), (os.path.join(_ROOT, "frontend", "src"), (".js", ".jsx", ".json"))):
        for dirpath, dirnames, files in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ("tests", "node_modules", "__pycache__")]
            for f in files:
                if f.endswith(exts) and f != "local_only.py":
                    yield os.path.join(dirpath, f)


def test_no_page_or_route_reads_a_local_only_table():
    pattern = re.compile(r"\b(" + "|".join(local_only.LOCAL_ONLY) + r")\b")
    hits = {}
    for path in _code_files():
        with open(path, encoding="utf-8") as f:
            found = set(pattern.findall(f.read()))
        if found:
            hits[os.path.relpath(path, _ROOT)] = found
    # Only the Data Coverage map names one (report_card_units, counted; "kept local" on the mirror).
    assert hits == {"api/routers/meta.py": {"report_card_units", "report_card_game_sums"}}, hits


def test_local_only_tables_are_written_by_scripts_here():
    for t in local_only.LOCAL_ONLY:
        kind, producer = PM.TABLES[t]
        assert kind in ("derived", "paper") and producer.endswith(".py"), (t, kind, producer)


def test_migration_and_manifest_use_the_same_list():
    import migrate_to_layerbase as M
    assert M.LOCAL_ONLY is local_only.LOCAL_ONLY and PM.LOCAL_ONLY is local_only.LOCAL_ONLY
    assert len(set(local_only.LOCAL_ONLY)) == 6


@needs_manifest
def test_compare_skips_local_only_and_catches_a_change():
    here = PM.load_manifest()
    mirror = copy.deepcopy(here)
    mirror["tables"] = [e for e in mirror["tables"] if e["table"] not in local_only.LOCAL_ONLY]
    lines, ok = PM.compare(here, mirror)
    assert ok, lines
    assert lines[-1].startswith("skipped, kept local") and all(t in lines[-1] for t in local_only.LOCAL_ONLY)
    changed = copy.deepcopy(mirror)
    changed["tables"][0]["content_md5"] = "0" * 32
    del changed["tables"][1]
    lines, ok = PM.compare(here, changed)
    assert not ok
    assert any(here["tables"][0]["table"] not in local_only.LOCAL_ONLY and "content_md5 differ" in ln for ln in lines)
    assert any("missing from the other database" in ln for ln in lines)


def test_conftest_names_only_local_only_tables():
    err = psycopg2.errors.UndefinedTable('relation "paper_eval_predictions" does not exist\nLINE 1: SELECT ...')
    assert conftest.local_only_table(err) == "paper_eval_predictions"
    try:
        try:
            raise err
        except psycopg2.Error as inner:
            raise RuntimeError("Execution failed on sql 'SELECT ...'") from inner
    except RuntimeError as wrapped:
        assert conftest.local_only_table(wrapped) == "paper_eval_predictions"
    assert conftest.local_only_table(psycopg2.errors.UndefinedTable('relation "player_shots" does not exist')) is None
    assert conftest.local_only_table(AssertionError("shot_xfg rows differ")) is None


@needs_db
def test_data_coverage_marks_a_local_only_table_that_is_absent(monkeypatch):
    from fastapi.testclient import TestClient
    from impact_api import app
    from routers import meta
    fake = {"table": "zz_not_on_this_database", "label": "test", "group": "Models", "range_sql": None,
            "range_fmt": None, "source": "test", "gap": "", "used_by": []}
    monkeypatch.setattr(meta, "COVERAGE_MAP", meta.COVERAGE_MAP + [fake])
    monkeypatch.setattr(meta, "LOCAL_ONLY", meta.LOCAL_ONLY + ("zz_not_on_this_database",))
    meta._coverage.cache_clear()
    try:
        rows = {r["table"]: r for r in TestClient(app).get("/meta/coverage").json()["tables"]}
    finally:
        meta._coverage.cache_clear()
    row = rows["zz_not_on_this_database"]
    assert row["exists"] is False and row["local_only"] is True and row["note"] == "kept local, not on the cloud mirror"
    assert rows["report_card_units"]["local_only"] is True
    assert sum(r["local_only"] for r in rows.values()) == 2  # report_card_units + the fake one
