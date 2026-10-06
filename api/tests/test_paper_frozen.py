"""
test_paper_frozen.py
=====================
Round 9 step 1 (2026-10-06): the paper is frozen on 2025-26 while the live 2026-27 season adds rows every
day. api/paper_freeze.py holds the one constant (MAX_PAPER_SEASON = 2026) and the rule for picking the
paper's rows of each table; scripts/paper_manifest.py hashes only those rows. These tests prove the guard:

  * the constant is the protocol's test season, and paper_predicate() follows the documented rules;
  * every table the season adds to has a predicate, and the predicate excludes exactly a fake 2026-27 row
    (one fake row is built per table from its newest row, inline with UNION ALL and CTEs that shadow the
    joined tables: no view, no copy, nothing created in the database);
  * no paper-stage script names such a table directly after FROM/JOIN in a SQL literal (it must go through
    paper_freeze.F(), which carries the bound), checked statically on the source;
  * every paper-stage loader, run for real with a recording cursor, bounds every such table it reads at the
    freeze (this also covers the shared loaders they call with `through=`);
  * the manifest's digest leaves the live ledger log out and its hashes apply the predicates.

All fourteen paper-stage files are capped (the last four, paper_numbers / paper_data_audit / build_data_quality /
api/data_quality_lib, right after the round 8.5 step C commit: docs/qa/ROUND9_ISSUES.md R9-001).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_frozen.py
"""

import ast
import io
import os
import re
import sys
import tokenize

import psycopg2
import psycopg2.extensions
import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API_DIR)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _d in (_API_DIR, _SCRIPTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import paper_freeze as PF  # noqa: E402
import paper_manifest as PM  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402


def _db_reachable():
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")

# Paper-stage code (scripts/rebuild_all.sh's paper and paper-inputs stages, and the Data Quality page's live checks).
CAPPED_FILES = [
    "scripts/paper_xrapm.py", "scripts/paper_eval.py", "scripts/paper_tests.py", "scripts/build_pregame_availability.py",
    "scripts/build_lineup_predictor.py", "scripts/build_report_card.py", "scripts/paper_beliefs.py",
    "scripts/paper_ablations.py", "scripts/paper_figures.py", "scripts/paper_manifest.py",
    "scripts/paper_numbers.py", "scripts/paper_data_audit.py", "scripts/build_data_quality.py", "api/data_quality_lib.py",
]

WRITE_KEYWORDS = ("DELETE", "INTO", "UPDATE", "TABLE", "EXISTS")
FAKE_ESPN_GAME, FAKE_NBA_GAME, FAKE_ESPN_ID, FAKE_INT_ID = "espn_402700001", "0022600001", "402700001", 999999999


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(**DB_CONFIG)
    c.set_session(readonly=True, autocommit=True)
    yield c
    c.close()


@pytest.fixture(scope="module")
def catalogue(conn):
    """table -> (predicate or None, [(column, data_type)])."""
    cur = conn.cursor()
    cur.execute("""SELECT table_name, column_name, data_type FROM information_schema.columns
                   WHERE table_schema = 'public' ORDER BY table_name, ordinal_position""")
    cols = {}
    for t, c, dt in cur.fetchall():
        cols.setdefault(t, []).append((c, dt))
    tables = PM.db_tables(cur)
    return {t: (PF.paper_predicate(t, [c for c, _ in cols[t]]), cols[t]) for t in tables}


# ── the constant and the rules ───────────────────────────────────────────────

def test_constant_is_the_protocols_test_season():
    import paper_eval as PE
    assert PF.MAX_PAPER_SEASON == 2026 == PE.TEST
    assert PF.MAX_PAPER_SEASON_LABEL == PF.season_label(PF.MAX_PAPER_SEASON) == "2025-26"


def test_predicate_rules():
    assert PF.paper_predicate("lineup_stints", ["stint_id", "season"]) == "(season IS NULL OR season <= 2026)"
    assert PF.paper_predicate("player_shots", ["id", "season"]) == "(season IS NULL OR season <= '2025-26')"
    assert PF.paper_predicate("stat_stability", ["stat", "m"]) is None            # no season dimension: read whole
    assert PF.paper_predicate("ledger_schedule", ["season", "espn_id"]) is None   # the lock is 2026-27 by design
    assert PF.paper_predicate("pbp_events", ["id", "game_id"]) == \
        "game_id IN (SELECT game_id FROM pbp_games WHERE season <= 2026)"
    assert PF.paper_predicate("player_projections", ["season", "last_season"]) == "(last_season IS NULL OR last_season <= 2026)"
    assert PF.F("lineup_stints") == '(SELECT * FROM "lineup_stints" WHERE (season IS NULL OR season <= 2026)) AS lineup_stints'
    assert PF.F("pbp_games", "g").endswith(") AS g")
    with pytest.raises(ValueError):
        PF.paper_rows("ledger_lock")


# ── the predicates, on a fake 2026-27 row per table ─────────────────────────

def _fake_override(table, cols):
    """SQL for the jsonb that turns the newest row of `table` (alias x) into a 2026-27 row."""
    parts = []
    for c, dt in cols:
        if c == "season":
            parts += [f"'{c}'", "'2026-27'" if dt == "text" else "2027"]
        elif c == "last_season":
            parts += [f"'{c}'", "2027"]
        elif dt == "date":
            parts += [f"'{c}'", f'(x."{c}" + interval \'1 year\')::date']
        elif dt.startswith("timestamp"):
            parts += [f"'{c}'", f'x."{c}" + interval \'1 year\'']
        elif c == "game_id" and dt == "text":
            parts += [f"'{c}'", f"CASE WHEN x.game_id LIKE 'espn_%%' THEN '{FAKE_ESPN_GAME}' ELSE '{FAKE_NBA_GAME}' END"]
        elif c == "nba_game_id":
            parts += [f"'{c}'", f"'{FAKE_NBA_GAME}'"]
        elif c == "espn_id" and dt == "text":
            parts += [f"'{c}'", f"'{FAKE_ESPN_ID}'"]
        elif c in ("id", "event_id", "stint_id", "shot_id") and dt in ("integer", "bigint"):
            parts += [f"'{c}'", str(FAKE_INT_ID)]
    return "jsonb_build_object(" + ", ".join(parts) + ")" if parts else "'{}'::jsonb"


def _order_col(cols):
    names = [c for c, _ in cols]
    for c in ("season", "last_season", "game_date", "id", "event_id", "game_id"):
        if c in names:
            return f'"{c}" DESC NULLS LAST'
    return "1"


def _shadow_ctes(table, catalogue):
    """CTEs that shadow the tables a predicate joins, each with the same fake 2026-27 game or event added."""
    ctes = []
    if table in ("pbp_events", "pbp_event_clock", "play_finder_events"):
        ctes.append(f"pbp_games AS (SELECT * FROM public.pbp_games UNION ALL SELECT (jsonb_populate_record(NULL::public.pbp_games, "
                    f"to_jsonb(x) || {_fake_override('pbp_games', catalogue['pbp_games'][1])})).* "
                    f"FROM (SELECT * FROM public.pbp_games WHERE source = 'espn' ORDER BY season DESC LIMIT 1) x)")
    if table in ("pbp_event_clock", "play_finder_events"):
        ctes.append(f"pbp_events AS (SELECT * FROM public.pbp_events UNION ALL SELECT (jsonb_populate_record(NULL::public.pbp_events, "
                    f"to_jsonb(x) || {_fake_override('pbp_events', catalogue['pbp_events'][1])})).* "
                    f"FROM (SELECT * FROM public.pbp_events ORDER BY id DESC LIMIT 1) x)")
    if table == "pregame_availability_players":
        ctes.append(f"pregame_availability_odds AS (SELECT * FROM public.pregame_availability_odds UNION ALL SELECT "
                    f"(jsonb_populate_record(NULL::public.pregame_availability_odds, to_jsonb(x) || "
                    f"{_fake_override('pregame_availability_odds', catalogue['pregame_availability_odds'][1])})).* "
                    f"FROM (SELECT * FROM public.pregame_availability_odds ORDER BY season DESC LIMIT 1) x)")
    return ctes


@needs_db
def test_every_table_the_season_adds_to_has_a_predicate(catalogue):
    """A table with a season dimension (a season column, or a game/event key) is capped; the lock isn't."""
    for t, (pred, cols) in catalogue.items():
        names = {c for c, _ in cols}
        if t in PF.LEDGER_TABLES:
            assert pred is None, t
        elif "season" in names or t in PF.EXPLICIT_PREDICATES:
            assert pred, f"{t} has a season dimension but no paper-rows predicate"
        else:
            assert pred is None, t
    # game- or event-keyed tables without a season column are all accounted for by name
    keyed = {t for t, (pred, cols) in catalogue.items()
             if "season" not in {c for c, _ in cols} and {c for c, _ in cols} & {"game_id", "event_id"} and t not in PF.LEDGER_TABLES}
    assert keyed <= set(PF.EXPLICIT_PREDICATES), keyed - set(PF.EXPLICIT_PREDICATES)
    assert PF.TEXT_SEASON_TABLES == {t for t, (pred, cols) in catalogue.items() if dict(cols).get("season") == "text"}


@needs_db
def test_predicates_exclude_exactly_a_fake_2026_27_row(conn, catalogue):
    """For every capped table: the table plus one fake 2026-27 row, filtered by its predicate, is the table."""
    cur = conn.cursor()
    cur.execute("SET statement_timeout = '120s'")
    checked = 0
    for t, (pred, cols) in sorted(catalogue.items()):
        if not pred:
            continue
        ctes = _shadow_ctes(t, catalogue) + [
            f"fake AS (SELECT (jsonb_populate_record(NULL::public.\"{t}\", to_jsonb(x) || {_fake_override(t, cols)})).* "
            f"FROM (SELECT * FROM public.\"{t}\" ORDER BY {_order_col(cols)} LIMIT 1) x)",
            f"u AS (SELECT * FROM public.\"{t}\" UNION ALL SELECT * FROM fake)"]
        cur.execute("WITH " + ", ".join(ctes) + f" SELECT (SELECT count(*) FROM fake), (SELECT count(*) FROM u), "
                    f"(SELECT count(*) FROM u WHERE {pred}), (SELECT count(*) FROM public.\"{t}\")")
        n_fake, n_union, n_capped, n_plain = cur.fetchone()
        if n_plain == 0:
            continue                       # nothing to copy a fake row from (an empty table)
        assert n_fake == 1 and n_union == n_plain + 1, (t, n_fake, n_union, n_plain)
        assert n_capped == n_plain, f"{t}: predicate {pred!r} keeps {n_capped} of {n_union} rows, the table has {n_plain}"
        checked += 1
    assert checked >= 120, checked


@needs_db
def test_paper_tables_hold_no_rows_past_the_freeze(conn, catalogue):
    cur = conn.cursor()
    for t, (pred, cols) in sorted(catalogue.items()):
        kind = PM.TABLES[t][0]
        if kind == "paper" and "season" in {c for c, _ in cols}:
            cur.execute(f'SELECT max(season) FROM "{t}"')
            mx = cur.fetchone()[0]
            assert mx is None or mx <= PF.MAX_PAPER_SEASON, (t, mx)


# ── the manifest ────────────────────────────────────────────────────────────

def test_manifest_digest_skips_the_live_ledger_log():
    e = [{"table": "a", "schema_md5": "1", "content_md5": "2", "rows": 3},
         {"table": "ledger_runs", "schema_md5": "x", "content_md5": "y", "rows": 4}]
    assert PM.digest(e) == PM.digest(e[:1])
    assert PM.digest(e + [{"table": "ledger_runs", "schema_md5": "x", "content_md5": "changed", "rows": 5}]) == PM.digest(e[:1])
    assert "ledger_results" in PM.LIVE and "ledger_tests" in PM.LIVE


@needs_db
def test_manifest_hashes_and_counts_apply_the_predicates(conn, catalogue):
    """Today no capped table has a 2026-27 row, so the capped hash equals the plain one; and the WHERE is really
    applied (a predicate that keeps nothing gives the empty table's hash)."""
    cur = conn.cursor()
    for t in ("pbp_games", "game_scores", "lineup_stint_games", "player_projections", "game_officials"):
        pred = catalogue[t][0]
        assert pred
        assert PM.content_hash(cur, t, pred) == PM.content_hash(cur, t), t
    n, h = PM.content_hash(cur, "pbp_games", "FALSE")
    assert n == 0 and h == PM.content_hash(cur, "pbp_games", "season > 9999")[1]
    preds = {t: catalogue[t][0] for t in ("pbp_games", "game_scores")}
    assert PM.row_counts(cur, list(preds), preds) == PM.row_counts(cur, list(preds))
    assert PM.row_counts(cur, ["pbp_games"], {"pbp_games": "FALSE"}) == {"pbp_games": 0}


# ── static: no paper-stage SQL literal names a capped table directly ─────────

def _literal_spans(src):
    lines = src.splitlines(keepends=True)
    off = [0]
    for ln in lines:
        off.append(off[-1] + len(ln))
    pos = lambda rc: off[rc[0] - 1] + rc[1]  # noqa: E731
    spans, stack = [], []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING:
            spans.append((pos(tok.start), pos(tok.end)))
        elif tok.type == tokenize.FSTRING_START:
            stack.append(pos(tok.start))
        elif tok.type == tokenize.FSTRING_END:
            s = stack.pop()
            if not stack:
                spans.append((s, pos(tok.end)))
    return sorted(spans)


def _docstring_starts(src):
    lines = src.splitlines(keepends=True)
    off = [0]
    for ln in lines:
        off.append(off[-1] + len(ln))
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                out.add(off[first.lineno - 1] + first.col_offset)
    return out


def _direct_reads(src, tables):
    """(line, text) of every FROM/JOIN <capped table> inside a non-docstring string literal that isn't a write."""
    pat = re.compile(r'\b(FROM|JOIN)\s+"?(' + "|".join(sorted(tables, key=len, reverse=True)) + r')"?(?![A-Za-z0-9_])')
    spans, docs, out = _literal_spans(src), _docstring_starts(src), []
    for m in pat.finditer(src):
        lit = next((s for s in spans if s[0] <= m.start() < s[1]), None)
        if lit is None or lit[0] in docs:
            continue
        before = src[lit[0]:m.start()].rstrip().split()
        if before and before[-1].strip("\"'frbuFRBU").upper() in WRITE_KEYWORDS:
            continue
        out.append((src.count("\n", 0, m.start()) + 1, m.group(0)))
    return out


def _capped_tables(catalogue):
    return {t for t, (pred, _) in catalogue.items() if pred}


@needs_db
@pytest.mark.parametrize("rel", CAPPED_FILES)
def test_paper_stage_scripts_read_capped_tables_through_F(rel, catalogue):
    with open(os.path.join(_ROOT, rel)) as f:
        src = f.read()
    hits = _direct_reads(src, _capped_tables(catalogue))
    assert not hits, f"{rel} names a table the season adds to without paper_freeze.F(): " + \
        "; ".join(f"L{ln}: {txt}" for ln, txt in hits[:12])


@needs_db
def test_the_static_check_catches_a_bare_read(catalogue):
    src = 'x = 1\ndef f(cur):\n    """FROM lineup_stints in a docstring is fine."""\n' \
          '    cur.execute("DELETE FROM lineup_stints WHERE season = 2027")\n' \
          '    cur.execute(f"SELECT * FROM {F(\'pbp_games\')} JOIN lineup_stints s USING (game_id)")\n'
    assert _direct_reads(src, _capped_tables(catalogue)) == [(5, "JOIN lineup_stints")]


# ── dynamic: the loaders, run for real, bound every capped table they read ───

class _Recording(psycopg2.extensions.cursor):
    log = []

    def execute(self, sql, vars=None):
        _Recording.log.append(sql.decode() if isinstance(sql, bytes) else str(sql))
        return super().execute(sql, vars)

    def copy_expert(self, sql, file, size=8192):
        _Recording.log.append(str(sql))
        return super().copy_expert(sql, file, size)


_BOUND = re.compile(r"\b(?:last_)?season(?:\s*\+\s*1)?\s*(<=?)\s*(?:(\d{4})|'(\d{4})-\d{2}')")


def _bounded_after(text, table, catalogue, cap=PF.MAX_PAPER_SEASON):
    """Does `text` (the SQL after a table reference) bound the season at the freeze: a `season <= 2026`-style
    comparison (also `g.season`, `season + 1 <= 2026`, `season < 2027`, `season <= '2025-26'`) or the table's
    explicit predicate (the game-id digit rule has no season word)?"""
    if catalogue[table][0] in text:
        return True
    for m in _BOUND.finditer(text):
        op, year, label = m.groups()
        end = int(year) if year else int(label) + 1
        if end - (1 if op == "<" else 0) <= cap:
            return True
    return False


def _unbounded_reads(sqls, catalogue):
    """[(table, sql head)] for every capped table named after FROM/JOIN in an executed statement whose text after that
    point never bounds it at the freeze (the F() wrapper, a loader's `season <= 2026`, or the explicit predicate)."""
    tables = _capped_tables(catalogue)
    pat = re.compile(r'\b(?:FROM|JOIN)\s+"?(' + "|".join(sorted(tables, key=len, reverse=True)) + r')"?(?![A-Za-z0-9_])')
    bad = []
    for sql in sqls:
        for m in pat.finditer(sql):
            t = m.group(1)
            before = sql[:m.start()].rstrip().split()
            if before and before[-1].upper() in WRITE_KEYWORDS:
                continue
            if not _bounded_after(sql[m.end():], t, catalogue):
                bad.append((t, " ".join(sql.split())[:160]))
    return bad


def _record(fn):
    _Recording.log = []
    c = psycopg2.connect(**DB_CONFIG, cursor_factory=_Recording)
    c.set_session(readonly=True, autocommit=True)
    try:
        fn(c)
    finally:
        c.close()
    return list(_Recording.log)


@needs_db
def test_paper_stage_loaders_bound_every_capped_table(catalogue):
    """Runs the loaders of the capped scripts (and the shared loaders they call with `through=`) on the real
    database with a recording cursor (~1 min: the shot and lineup loads are the slow ones)."""
    import build_hot_streak_persistence as HS
    import build_lineup_predictor as LPd
    import build_pregame_availability as PA
    import build_rapm as R
    import compute_wpa as W
    import paper_ablations as AB
    import paper_eval as PE
    import paper_tests as PT
    import paper_xrapm as X
    import situational_splits as SPDEF

    def loaders(c):
        cur = c.cursor()
        R.load_rows(c, through=PF.MAX_PAPER_SEASON)
        R.load_bpm(c, through=PF.MAX_PAPER_SEASON)
        X.load_stints(c)
        X.load_aware_inputs(c)
        X.ft_shrinkage(cur)
        PF.paper_game_ids(c)
        PE.load_games(c)
        PE.load_shots_with_ids(c)
        PT.load_shot_games(c)
        PA.load(c)
        LPd.load(c)
        HS.load(c, through=PF.MAX_PAPER_SEASON)
        cur.execute(f"SELECT count(*) FROM ({W.events_sql(PF.MAX_PAPER_SEASON).rstrip().rstrip(';')}) q")
        cur.execute(SPDEF.lines_sql(PF.MAX_PAPER_SEASON).replace("ORDER BY l.player_id, l.game_date", "LIMIT 1"))
        AB.load_stored(c, ("impact", "xfg", "sim"))

    sqls = _record(loaders)
    assert len(sqls) >= 40, len(sqls)
    bad = _unbounded_reads(sqls, catalogue)
    assert not bad, "unbounded reads of tables the season adds to: " + "; ".join(f"{t}: {s}" for t, s in bad[:8])


@needs_db
def test_the_dynamic_check_catches_an_unbounded_read(catalogue):
    sqls = ["SELECT count(*) FROM lineup_stints WHERE tracked_ok",
            "SELECT count(*) FROM lineup_stints WHERE tracked_ok AND season <= 2026",
            PF.F("player_shots"), "DELETE FROM paper_eval_predictions"]
    assert _unbounded_reads(sqls, catalogue) == [("lineup_stints", "SELECT count(*) FROM lineup_stints WHERE tracked_ok")]


@needs_db
def test_paper_numbers_and_the_audit_bound_every_capped_table(catalogue):
    """paper_numbers.build() (every macro's query) and paper_data_audit's SQL checks, recorded (~30 s). The audit's
    heavier parse-based checks (identity, shot, clock lag) and the Data Quality page's live checks were verified
    the same way by hand when the cap landed (R9-001); they go through the same shared loaders with `through=`."""
    import paper_data_audit as DA
    import paper_numbers as PN

    def run(c):
        PN.build(c)
        A = DA.Audit()
        cur = c.cursor()
        DA.feed_checks(cur, A)
        DA.score_checks(cur, A)
        DA.table_checks(cur, A)
        DA.chart_checks(cur, A)

    sqls = _record(run)
    bad = _unbounded_reads(sqls, catalogue)
    assert not bad, "; ".join(f"{t}: {s}" for t, s in bad[:8])
