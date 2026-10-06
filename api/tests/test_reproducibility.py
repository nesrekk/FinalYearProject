"""
test_reproducibility.py
========================
Guards the reproducibility package of round 5 step 9:

  * scripts/rebuild_all.sh parses, prints its plan without running anything,
    names only scripts that exist, and runs every table's producer;
  * its order respects the data: no step reads a derived or paper table
    whose producer runs later (reads found in the script and in every local
    module it imports), and the check does catch a swapped pair;
  * every table in the database has a producer in paper_manifest.PRODUCERS;
  * paper/manifest.json (if present) is current: its digest re-derives from
    its own entries, and its table set, schemas and row counts equal the
    live database's (a rebuild without rerunning the manifest fails here,
    like the data-quality audit's test); a tampered copy is caught;
  * the table hash does not depend on row order;
  * paper/SHA256SUMS (if present) matches the files it lists;
  * the legacy seasons of player_season_stats are exactly the committed
    season CSVs' players with 200+ minutes (the rule the paper's
    availability section and docs/DATASHEET.md state);
  * requirements.txt covers every third-party import and pins the installed
    versions.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_reproducibility.py
"""

import ast
import copy
import glob
import hashlib
import os
import re
import subprocess
import sys
from importlib import metadata

import psycopg2
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(_ROOT, "scripts")
_API = os.path.join(_ROOT, "api")
for _d in (_API, _SCRIPTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import paper_manifest as PM  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402

RUNNER = os.path.join(_SCRIPTS, "rebuild_all.sh")


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")
needs_manifest = pytest.mark.skipif(not os.path.exists(PM.MANIFEST_JSON), reason="paper/manifest.json not built")


def plan():
    """[(stage, tags, script, args)] in run order, parsed from the runner's step lines."""
    out = []
    with open(RUNNER) as f:
        for line in f:
            if line.startswith("step "):
                parts = line.split("#")[0].split()
                out.append((parts[1], parts[2], parts[3], parts[4:]))
    return out


READ_RE = re.compile(r"\b(?:FROM|JOIN)\s+\"?([a-z_][a-z0-9_]*)", re.I)
F_RE = re.compile(r"\bF\(\s*['\"]([a-z_][a-z0-9_]*)['\"]")   # a paper-stage read through paper_freeze.F(table)
LOCAL = {os.path.basename(p)[:-3]: p for p in glob.glob(os.path.join(_API, "*.py")) + glob.glob(os.path.join(_SCRIPTS, "*.py"))}
# Modules whose SQL text is a rule, not a read: paper_freeze's predicates name pbp_games / pbp_events /
# pregame_availability_odds for every table they restrict (round 9 step 1); importing them reads nothing.
RULE_MODULES = {"paper_freeze"}


def reads(script):
    """Tables named after FROM/JOIN in the script and in every local module it imports, transitively. An
    imported module's own output tables don't count: importing a producer's code isn't reading its table."""
    seen, todo, found = set(), [os.path.join(_SCRIPTS, script)], set()
    while todo:
        path = todo.pop()
        if path in seen or os.path.basename(path)[:-3] in RULE_MODULES:
            continue
        seen.add(path)
        with open(path) as f:
            text = f.read()
        own = set(PM.PRODUCERS.get(os.path.basename(path), (None, []))[1]) if len(seen) > 1 else set()
        found |= (set(READ_RE.findall(text)) | set(F_RE.findall(text))) - own
        for node in ast.walk(ast.parse(text)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else []
            for n in names:
                if n.split(".")[0] in LOCAL:
                    todo.append(LOCAL[n.split(".")[0]])
    return found


def order_violations(steps):
    first = {}
    for i, (_, _, script, _) in enumerate(steps):
        first.setdefault(script, i)
    bad = []
    for i, (stage, _, script, _) in enumerate(steps):
        if stage not in ("derived", "paper", "paper-inputs"):
            continue
        for t in sorted(reads(script)):
            kind, producer = PM.TABLES.get(t, (None, None))
            if kind in ("derived", "paper") and producer != script and first.get(producer, 10 ** 9) > i:
                bad.append(f"{script} reads {t}, whose producer {producer} runs later")
    return bad


def test_runner_parses_and_prints_its_plan_without_running():
    assert subprocess.run(["bash", "-n", RUNNER]).returncode == 0
    r = subprocess.run(["bash", RUNNER], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("Plan (nothing runs") and "==>" not in r.stdout
    assert r.stdout.count("\n") == len(plan()) + 1


def test_every_step_exists_and_every_producer_runs():
    steps = plan()
    assert all(os.path.isfile(os.path.join(_SCRIPTS, s)) for _, _, s, _ in steps)
    assert {st for st, _, _, _ in steps} == {"fetch", "load", "derived", "paper", "paper-inputs"}
    in_plan = {s for _, _, s, _ in steps}
    not_run = {"(before the first commit)", "api/shots_lib.py", "snapshot_predictions.py"}  # legacy / app caches
    assert {p for p in PM.PRODUCERS if p not in not_run} <= in_plan
    for script, (kind, _) in PM.PRODUCERS.items():
        if kind == "paper":
            assert [st for st, _, s, _ in steps if s == script] == ["paper"]


def test_order_respects_what_each_step_reads():
    steps = plan()
    assert order_violations(steps) == []
    # and the check catches a real mistake: stints before the build that writes team_game_totals
    i = next(k for k, s in enumerate(steps) if s[2] == "build_team_game_totals.py")
    j = next(k for k, s in enumerate(steps) if s[2] == "build_lineup_stints.py")
    swapped = list(steps)
    swapped[i], swapped[j] = swapped[j], swapped[i]
    assert any("team_game_totals" in v for v in order_violations(swapped))


@needs_db
def test_every_table_has_a_producer():
    conn = PM.connect()
    try:
        assert PM.unregistered(PM.db_tables(conn.cursor())) == []
    finally:
        conn.close()


@needs_db
def test_table_hash_ignores_row_order():
    conn = PM.connect()
    cur = conn.cursor()
    sql = """SELECT count(*), sum(('x' || substr(h, 1, 16))::bit(64)::bigint::numeric),
                    sum(('x' || substr(h, 17, 16))::bit(64)::bigint::numeric)
             FROM (SELECT md5(t::text) AS h FROM (VALUES {}) t(a, b)) s"""
    rows = ["(1, 'x')", "(2, 'y')", "(3, NULL)", "(2, 'y')"]
    cur.execute(sql.format(", ".join(rows)))
    a = cur.fetchone()
    cur.execute(sql.format(", ".join(reversed(rows))))
    b = cur.fetchone()
    cur.execute(sql.format(", ".join(rows[:3] + ["(2, 'z')"])))
    c = cur.fetchone()
    conn.close()
    assert a == b and a != c


@needs_db
@needs_manifest
def test_manifest_is_current_and_tampering_is_caught():
    m = PM.load_manifest()
    assert PM.digest(m["tables"]) == m["database"]["digest_sha256"]
    assert m["database"]["tables"] == len(m["tables"]) and m["database"]["rows"] == sum(e["rows"] for e in m["tables"])
    assert all(PM.TABLES[e["table"]] == (e["kind"], e["producer"]) for e in m["tables"])
    conn = PM.connect()
    try:
        cur = conn.cursor()
        assert PM.stale_reasons(cur, m) == [], "rerun scripts/paper_manifest.py (rebuild_all.sh paper-inputs)"
        bad = copy.deepcopy(m)
        bad["tables"][0]["rows"] += 1
        bad["tables"][1]["schema_md5"] = "0" * 32
        bad["tables"].pop()
        reasons = PM.stale_reasons(cur, bad)
        assert len(reasons) == 3
    finally:
        conn.close()
    # one small table's content hash re-derives
    small = min(m["tables"], key=lambda e: e["rows"] if e["rows"] else 10 ** 12)
    conn = PM.connect()
    try:
        assert PM.content_hash(conn.cursor(), small["table"]) == (small["rows"], small["content_md5"])
    finally:
        conn.close()


@pytest.mark.skipif(not os.path.exists(PM.SUMS), reason="paper/SHA256SUMS not built")
def test_file_sums_match_the_files():
    with open(PM.SUMS) as f:
        listed = [line.split("  ", 1) for line in f.read().splitlines()]
    assert listed
    for h, rel in listed:
        with open(os.path.join(PM.PAPER_DIR, rel), "rb") as g:
            assert hashlib.sha256(g.read()).hexdigest() == h, f"{rel} changed since SHA256SUMS was written"
    with open(PM.SUMS) as f:
        assert f.read() == PM.file_sums(), "a paper input was added or removed since SHA256SUMS was written"


@needs_db
def test_legacy_season_rows_are_the_committed_csvs_players_with_200_minutes():
    import pandas as pd
    first, last = PM.LEGACY_SEASONS
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        for s in range(first, last + 1):
            csv = pd.read_csv(os.path.join(_ROOT, "nba_data", f"nba_{s - 1}_{str(s)[-2:]}_season.csv"))
            cur.execute("SELECT player_id, gp, pts FROM player_season_stats WHERE season = %s", (s,))
            db = {r[0]: r[1:] for r in cur.fetchall()}
            kept = csv[csv.GP * csv.MIN >= PM.LEGACY_MIN_MINUTES]
            assert set(kept.PLAYER_ID) == set(db), s
            assert all(db[p][0] == g and abs(db[p][1] - pts) < 0.051 for p, g, pts in zip(kept.PLAYER_ID, kept.GP, kept.PTS)), s
    finally:
        conn.close()


def _third_party_imports():
    mods = set()
    files = glob.glob(os.path.join(_SCRIPTS, "*.py")) + glob.glob(os.path.join(_API, "**", "*.py"), recursive=True)
    local = {os.path.basename(f)[:-3] for f in files} | {"routers", "tests"}
    for f in files:
        with open(f) as g:
            tree = ast.parse(g.read())
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods.add(n.module.split(".")[0])
    return {m for m in mods if m not in sys.stdlib_module_names and m not in local}


def test_requirements_cover_imports_and_pin_installed_versions():
    with open(os.path.join(_ROOT, "requirements.txt")) as f:
        pins = dict(line.strip().split("==") for line in f if line.strip() and not line.startswith("#"))
    dist_of = {m: d for m, ds in metadata.packages_distributions().items() for d in ds}
    needed = {dist_of.get(m, m) for m in _third_party_imports()}
    lower = {k.lower() for k in pins}
    assert {d.lower() for d in needed} <= lower, sorted({d for d in needed if d.lower() not in lower})
    for dist, version in pins.items():
        assert metadata.version(dist) == version, dist
