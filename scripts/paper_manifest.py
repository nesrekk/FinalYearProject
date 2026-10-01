"""
paper_manifest.py
==================
What the database held when the paper's numbers were made: one row per table
with its row count and a content hash, so a reader with the artifact can check
that their rebuild holds the same data (round 5 step 9, reproducibility).

    cd scripts && python3 paper_manifest.py            # ~1 min: writes ../paper/manifest.json and manifest.tsv
    cd scripts && python3 paper_manifest.py --files    # a few seconds: writes ../paper/SHA256SUMS
    cd paper && shasum -a 256 -c SHA256SUMS            # check the generated paper inputs
    DB_TARGET=layerbase python3 paper_manifest.py --out $TMPDIR/lb   # another database, compared with diff

scripts/rebuild_all.sh paper-inputs runs the whole chain in one command:
manifest -> paper_numbers.py (prints the manifest's table count, row count and
digest, and stops if the manifest is stale) -> paper_figures.py -> --files.

The hash of a table
-------------------
Postgres prints every row as text (`t::text`, the row's columns in their
table order) and takes its md5 on the server. The 128 bits are split into two
64-bit halves, and each half is summed over the table as an exact numeric, so
the result doesn't depend on the order the rows come back in (tables have no
natural order and several have no primary key). The table's content hash is
md5("<rows>|<sum of first halves>|<sum of second halves>"); an empty table
hashes its count alone. The largest table, player_shots (6.3M rows), takes
about 10 s; all 167 tables about a minute. It is a checksum against accidental
change, not a cryptographic commitment: a sum of hashes can be forged, a
changed row cannot go unnoticed by accident.

Row text depends on session settings, so they are fixed before hashing:
timezone UTC (local Postgres runs in Asia/Kolkata, the Layerbase mirror in
UTC: without this every timestamptz table looks different), extra_float_digits
1 (Postgres 12+'s shortest exact float text; the same on any 12+ server),
DateStyle ISO, IntervalStyle postgres, bytea_output hex. The schema (column
names and types in table order) is hashed separately, so a new column shows up
as a schema change, not only as a content change.

Some tables carry a timestamp of when they were built (computed_at,
updated_at, fetched_at): their content hash changes on every rebuild even when
the data don't. The manifest lists every table's timestamp columns
(`time_columns`; a few, such as cbb_games.start_date, are data, not build
times), so a reader comparing a rebuild knows which differences to expect.

The database digest is the sha256 of one line per table, sorted by name:
"<table>\\t<schema md5>\\t<content md5>\\t<rows>". It changes if anything in
any table changes. The paper prints its first 16 hex digits.

Kinds and producers
-------------------
Every table carries the script that writes it (TABLES below) and a kind:
  source   loaded from an outside feed or file (ESPN, stats.nba.com,
           CollegeBasketballData.com, the Basketball-Reference-derived Kaggle
           export, third-party CSVs); scripts/rebuild_all.sh's fetch/load stages
  derived  built by a script in this repository from other tables
  paper    written by a scripts/paper_*.py script
  cache    written by the running app (a lazily filled cache or a live ledger)
  ledger   the Forecast Ledger's preseason lock (scripts/ledger_lock.py --lock): written once
           before a season's first tip from ESPN reads made at that moment, never rebuilt; the
           rebuild step only re-checks its stored SHA-256 (--verify). Also its nightly log
           (scripts/ledger_update.py), appended during the season from that night's ESPN read;
           those tables are LIVE: their row counts grow between manifests on purpose, so
           stale_reasons() checks only that they exist with the same columns (the manifest
           records their rows and content as of the day it was made)
  legacy   loaded before this repository's first commit by a script that is
           not in it (docs/DATASHEET.md says which)
A table in the database that isn't in TABLES stops the run (and the test), so
a new pipeline must say where its table comes from.

Read-only: one read-only session; nothing in the database is created or
changed. Output is deterministic: two runs against the same database and the
same commit give byte-identical files (no timestamps are written).
"""

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys

import psycopg2

from db_config import DB_CONFIG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAPER_DIR = os.path.join(ROOT, "paper")
MANIFEST_JSON = os.path.join(PAPER_DIR, "manifest.json")
MANIFEST_TSV = os.path.join(PAPER_DIR, "manifest.tsv")
SUMS = os.path.join(PAPER_DIR, "SHA256SUMS")
# The generated paper inputs --files hashes (relative to paper/); the hand-edited paper itself is not one.
FILE_GLOBS = ("numbers.tex", "manifest.json", "manifest.tsv", "tables/*.tex", "figures/*.pdf")

SESSION = ("SET timezone = 'UTC'", "SET extra_float_digits = 1", "SET DateStyle = 'ISO, MDY'",
           "SET IntervalStyle = 'postgres'", "SET bytea_output = 'hex'")

# script -> (kind, tables it writes). A table written by two scripts is listed under the one that creates it;
# the others are named in its note in docs/DATASHEET.md.
PRODUCERS = {
    # ---- sources: outside feeds and files
    "fetch_pbp_espn.py": ("source", ["pbp_events", "pbp_games"]),   # + fetch_play_by_play.py (nba_api sample), repair_espn_player_ids.py
    "load_pbp_shots.py": ("source", ["player_shots"]),               # + api/shots_lib.py fills players the files lack
    "fetch_game_scores.py": ("source", ["game_scores"]),
    "fetch_postseason_games.py": ("source", ["postseason_games"]),
    "build_schedule_fatigue.py": ("source", ["team_game_fatigue"]),  # stats.nba.com LeagueGameFinder
    "fetch_referee_officials.py": ("source", ["game_officials", "game_officials_fetch_log", "game_team_box"]),
    "fetch_spacing_data.py": ("source", ["lineup_stats", "player_shot_tracking"]),
    "fetch_matchups.py": ("source", ["player_matchups"]),
    "fetch_defend_dashboard.py": ("source", ["defender_dfg"]),
    "fetch_hustle_stats.py": ("source", ["player_hustle"]),
    "fetch_playtypes.py": ("source", ["player_playtypes"]),
    "fetch_shot_context.py": ("source", ["player_shot_context"]),
    "fetch_draft_combine.py": ("source", ["draft_combine"]),
    "build_defense_tracking_stats.py": ("source", ["defense_tracking_stats"]),
    "fetch_cbb_games.py": ("source", ["cbb_games"]),
    "fetch_college_stats.py": ("source", ["college_player_season_stats"]),
    "load_college_teams.py": ("source", ["college_team_seasons"]),
    "load_salaries.py": ("source", ["player_salaries", "league_minimum_salary"]),
    "load_kaggle_historical_seasons.py": ("source", ["player_season_stats", "player_id_map"]),
    "load_draft_history_bref.py": ("source", ["draft_history", "draft_pick_outcomes"]),
    "fetch_all_nba_teams.py": ("source", ["all_nba_seasons"]),
    "fetch_dpoy_roy_stats.py": ("source", ["dpoy_seasons", "roy_seasons"]),
    "load_nba75_team.py": ("source", ["nba75_team"]),
    # ---- legacy: no loader in the repository
    "(before the first commit)": ("legacy", ["mvp_seasons", "mvp_winners"]),
    # ---- cache: written by the running app
    "api/shots_lib.py": ("cache", ["league_shot_zones", "player_shots_cache_status"]),
    "snapshot_predictions.py": ("cache", ["prediction_ledger"]),     # + resolve_predictions.py
    # ---- ledger: locked once before a season's first tip, never rebuilt
    "ledger_lock.py": ("ledger", ["ledger_meta", "ledger_schedule", "ledger_rosters", "ledger_hindcast",
                                  "ledger_forecasts", "ledger_lock"]),
    # ---- ledger, live: appended each night of the season by the nightly update (LIVE below)
    "ledger_update.py": ("ledger", ["ledger_results", "ledger_game_log", "ledger_team_log", "ledger_runs", "ledger_tests"]),
    # ---- derived: season tables, awards, clusters
    "build_player_profile_data.py": ("derived", ["player_bio", "player_awards", "player_team_stints"]),
    "build_first_nba_season.py": ("derived", ["player_first_season"]),
    "build_award_winners_table.py": ("derived", ["award_winners"]),
    "build_contract_value.py": ("derived", ["contract_value", "contract_value_seasons"]),
    "build_league_averages.py": ("derived", ["league_season_averages"]),
    "build_team_seasons.py": ("derived", ["team_seasons"]),
    "build_luck_schedule.py": ("derived", ["team_luck_schedule", "luck_schedule_seasons", "luck_model_fit",
                                           "luck_schedule_validation"]),
    "build_all_nba_model.py": ("derived", ["all_nba_backtest_seasons", "all_nba_backtest_summary"]),
    "backtest_models.py": ("derived", ["model_backtest_seasons", "model_backtest_summary"]),
    "build_shap_explanations.py": ("derived", ["shap_explanations"]),
    "calibrate_award_chances.py": ("derived", ["award_chance_calibration"]),
    "train_win_model.py": ("derived", ["win_model_backtest"]),
    "train_pair_synergy.py": ("derived", ["pair_synergy_validation"]),
    "cluster_players.py": ("derived", ["player_clusters", "cluster_archetypes"]),
    "cluster_playtypes.py": ("derived", ["playtype_clusters", "playtype_cluster_archetypes"]),
    "build_player_roles.py": ("derived", ["player_roles", "role_archetypes"]),
    "precompute_league_similarity.py": ("derived", ["season_similarity"]),
    "precompute_career_similarity.py": ("derived", ["career_similarity"]),
    "build_aging_curves.py": ("derived", ["aging_curves", "aging_curve_summary", "aging_league_average"]),
    "build_projections.py": ("derived", ["player_projections", "projection_backtest", "projection_backtest_rows",
                                         "projection_ranges", "projection_stats"]),
    "build_greats.py": ("derived", ["greats", "greats_meta"]),
    "build_dad_index.py": ("derived", ["defender_dad", "dad_validation"]),
    "build_gravity_index.py": ("derived", ["player_gravity", "gravity_validation", "gravity_tracking_coverage"]),
    "build_scouting_reports.py": ("derived", ["scouting_splits", "scouting_validation", "zone_classifier_check"]),
    "build_referee_tendencies.py": ("derived", ["referee_tendencies", "referee_crew_tendencies"]),
    "build_college_pipeline.py": ("derived", ["college_draft_pipeline", "college_pipeline_meta"]),
    "build_ncaa_model.py": ("derived", ["ncaa_bracket_odds", "ncaa_model_backtest", "ncaa_model_seasons",
                                       "ncaa_tourney_games"]),
    # ---- derived: shots
    "build_league_zone_mix.py": ("derived", ["league_zone_mix"]),
    "build_shot_making.py": ("derived", ["player_shot_making", "shot_making_league", "shot_making_validation",
                                         "player_shot_hex", "shot_hex_league", "shot_hex_meta", "shot_xfg"]),
    "build_team_zone_mix.py": ("derived", ["team_zone_mix", "game_pair"]),
    # ---- derived: play-by-play
    "train_wpa_model.py": ("derived", ["wpa_model_validation"]),
    "build_event_clock.py": ("derived", ["pbp_event_clock", "pbp_event_clock_meta"]),
    "compute_wpa.py": ("derived", ["player_wpa_totals", "wpa_clutch_league"]),
    "build_leverage_splits.py": ("derived", ["player_leverage_splits", "player_leverage_summary",
                                             "leverage_index_grid", "leverage_validation"]),
    "build_player_game_lines.py": ("derived", ["player_game_lines"]),
    "build_player_on_off.py": ("derived", ["team_game_totals", "player_on_off", "player_on_off_seasons"]),
    "build_lineup_stints.py": ("derived", ["lineup_stints", "lineup_stint_games", "lineup_stint_seasons",
                                           "lineup_seasons", "pair_seasons"]),
    "build_possessions.py": ("derived", ["possessions", "possession_games", "possession_seasons", "possession_meta"]),
    "build_coaching_decisions.py": ("derived", ["coaching_decisions", "coaching_decision_tests", "coaching_decision_summary",
                                                "coaching_decision_meta"]),
    "build_stat_stability.py": ("derived", ["stat_stability", "stat_stability_curve", "stat_year_to_year"]),
    "build_hot_streak_persistence.py": ("derived", ["hot_streak_persistence"]),
    "build_situational_splits.py": ("derived", ["player_situational_splits", "situational_split_league"]),
    "build_rapm.py": ("derived", ["player_rapm", "rapm_fits", "rapm_lambda_cv", "rapm_validation"]),
    "build_rotations.py": ("derived", ["rotation_closing_games", "rotation_closing_stints"]),
    "build_rim_deterrence.py": ("derived", ["rim_deterrence", "rim_deterrence_seasons"]),
    "build_assist_network.py": ("derived", ["assist_pairs", "player_assisted_share", "assist_seasons"]),
    "build_play_finder.py": ("derived", ["play_finder_events", "play_finder_games", "play_finder_seasons"]),
    "build_best_games.py": ("derived", ["best_games", "best_games_meta"]),
    "build_season_sim.py": ("derived", ["game_pregame_odds", "pregame_model_fit", "pregame_model_seasons",
                                        "pregame_calibration", "season_postseason", "season_sim_params",
                                        "season_sim_backtest", "season_sim_backtest_summary",
                                        "season_sim_calibration", "season_sim_seasons"]),
    # ---- paper
    "paper_xrapm.py": ("paper", ["paper_xrapm_stints", "paper_xrapm_players", "paper_xrapm_fits",
                                 "paper_xrapm_lambda_cv", "paper_xrapm_meta"]),
    "paper_eval.py": ("paper", ["paper_eval_predictions", "paper_eval_metrics", "paper_eval_choices"]),
    "paper_tests.py": ("paper", ["paper_eval_tests"]),
    "paper_data_audit.py": ("paper", ["paper_data_audit", "paper_data_audit_classes"]),
    "paper_beliefs.py": ("paper", ["paper_beliefs", "paper_beliefs_summary", "paper_beliefs_meta"]),
    "paper_ablations.py": ("paper", ["paper_ablation_predictions", "paper_ablation_shot_games",
                                     "paper_ablation_metrics", "paper_ablation_tests", "paper_ablation_meta"]),
}

# player_season_stats rows loaded before the first commit (kind legacy in spirit, though later scripts add columns
# and seasons): for these seasons (end years) the rows are exactly the players of the committed
# nba_data/nba_<season>_season.csv with games x minutes per game >= LEGACY_MIN_MINUTES (checked for every season by
# api/tests/test_reproducibility.py); 2025-26 comes from load_2025_26_into_db.py, pre-2010 from the Kaggle loader.
LEGACY_SEASONS = (2010, 2025)
LEGACY_MIN_MINUTES = 200

TABLES = {}
for _script, (_kind, _tables) in PRODUCERS.items():
    for _t in _tables:
        if _t in TABLES:
            raise ValueError(f"{_t} listed under two producers")
        TABLES[_t] = (_kind, _script)

TIME_TYPES = ("timestamp without time zone", "timestamp with time zone", "time without time zone",
              "time with time zone")


def connect():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    for s in SESSION:
        cur.execute(s)
    return conn


def db_tables(cur):
    cur.execute("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY 1""")
    return [r[0] for r in cur.fetchall()]


def schemas(cur):
    """table -> (schema md5, column count, timestamp columns), from the catalogue (fast)."""
    cur.execute("""SELECT table_name, column_name, data_type, udt_name FROM information_schema.columns
                   WHERE table_schema = 'public' ORDER BY table_name, ordinal_position""")
    cols = {}
    for t, c, dt, udt in cur.fetchall():
        cols.setdefault(t, []).append((c, dt, udt))
    out = {}
    for t, cs in cols.items():
        text = "\n".join(f"{c}\t{udt}" for c, dt, udt in cs)
        out[t] = (hashlib.md5(text.encode()).hexdigest(), len(cs), [c for c, dt, _ in cs if dt in TIME_TYPES])
    return out


def row_counts(cur, tables):
    """Exact row count of every table in one statement (~2 s for the whole database)."""
    if not tables:
        return {}
    cur.execute(" UNION ALL ".join(f"SELECT %s, count(*) FROM \"{t}\"" for t in tables), tables)
    return dict(cur.fetchall())


def content_hash(cur, table):
    cur.execute(f"""SELECT count(*), sum(('x' || substr(h, 1, 16))::bit(64)::bigint::numeric),
                           sum(('x' || substr(h, 17, 16))::bit(64)::bigint::numeric)
                    FROM (SELECT md5(t::text) AS h FROM "{table}" t) s""")
    n, a, b = cur.fetchone()
    key = f"{n}" if n == 0 else f"{n}|{a}|{b}"
    return n, hashlib.md5(key.encode()).hexdigest()


def digest(entries):
    lines = "".join(f"{e['table']}\t{e['schema_md5']}\t{e['content_md5']}\t{e['rows']}\n"
                    for e in sorted(entries, key=lambda e: e["table"]))
    return hashlib.sha256(lines.encode()).hexdigest()


def unregistered(tables):
    return sorted(t for t in tables if t not in TABLES)


def git_state():
    """(HEAD commit, whether the code the pipeline runs has uncommitted changes)."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "scripts", "api", "requirements.txt",
                                "requirements-lock.txt"], cwd=ROOT, capture_output=True, text=True,
                               check=True).stdout.strip()
        return head, bool(dirty)
    except (OSError, subprocess.CalledProcessError):
        return None, None


def build_manifest(conn, progress=False):
    cur = conn.cursor()
    tables = db_tables(cur)
    missing = unregistered(tables)
    if missing:
        raise SystemExit("tables with no producer in paper_manifest.PRODUCERS (say which script writes them): "
                         + ", ".join(missing))
    sch = schemas(cur)
    cur.execute("SHOW server_version")
    version = cur.fetchone()[0]
    entries = []
    for t in tables:
        n, h = content_hash(cur, t)
        kind, producer = TABLES[t]
        smd5, ncol, tcols = sch[t]
        entries.append({"table": t, "kind": kind, "producer": producer, "rows": n, "columns": ncol,
                        "schema_md5": smd5, "content_md5": h, "time_columns": tcols})
        if progress:
            print(f"  {t:34s} {n:>10,d}  {h}", flush=True)
    cur.close()
    head, dirty = git_state()
    return {
        "about": "Row count and content hash of every table in the nba_analytics database; "
                 "written by scripts/paper_manifest.py (method in its docstring).",
        "code": {"git_commit": head, "uncommitted_changes_in_pipeline_code": dirty},
        "database": {"server_version": version, "session": list(SESSION), "tables": len(entries),
                     "rows": sum(e["rows"] for e in entries), "digest_sha256": digest(entries),
                     "by_kind": {k: sum(1 for e in entries if e["kind"] == k)
                                 for k in ("source", "derived", "paper", "cache", "legacy")}},
        "tables": entries,
    }


def render_tsv(m):
    head = ["table", "kind", "producer", "rows", "columns", "schema_md5", "content_md5", "time_columns"]
    out = ["# " + m["about"], f"# database digest (sha256): {m['database']['digest_sha256']}",
           f"# git commit: {m['code']['git_commit']}", "\t".join(head)]
    for e in m["tables"]:
        out.append("\t".join(str(e[k]) if k != "time_columns" else ",".join(e[k]) for k in head))
    return "\n".join(out) + "\n"


def write_manifest(conn, progress=False, out_dir=PAPER_DIR):
    m = build_manifest(conn, progress)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(m, f, indent=1, sort_keys=False)
        f.write("\n")
    with open(os.path.join(out_dir, "manifest.tsv"), "w") as f:
        f.write(render_tsv(m))
    return m


def load_manifest(path=MANIFEST_JSON):
    with open(path) as f:
        return json.load(f)


# Tables appended on a schedule (the Forecast Ledger's nightly log): a later row count is expected.
LIVE = {"ledger_results", "ledger_game_log", "ledger_team_log", "ledger_runs", "ledger_tests"}


def stale_reasons(cur, m):
    """Why a stored manifest no longer describes the database: table set, schemas and row counts
    (checked live in ~2 s; a change of content that keeps every count and schema is not caught; LIVE tables'
    row counts are not compared)."""
    now = db_tables(cur)
    have = {e["table"]: e for e in m["tables"]}
    reasons = []
    if set(now) != set(have):
        added, gone = sorted(set(now) - set(have)), sorted(set(have) - set(now))
        reasons.append(f"tables added {added} / dropped {gone}")
    common = [t for t in now if t in have]
    sch = schemas(cur)
    reasons += [f"{t}: schema changed" for t in common if sch[t][0] != have[t]["schema_md5"]]
    counts = row_counts(cur, common)
    reasons += [f"{t}: {have[t]['rows']:,} rows in the manifest, {counts[t]:,} now"
                for t in common if counts[t] != have[t]["rows"] and t not in LIVE]
    return reasons


def file_sums():
    paths = sorted({p for g in FILE_GLOBS for p in glob.glob(os.path.join(PAPER_DIR, g))})
    lines = []
    for p in paths:
        with open(p, "rb") as f:
            lines.append(f"{hashlib.sha256(f.read()).hexdigest()}  {os.path.relpath(p, PAPER_DIR)}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--files", action="store_true",
                    help="only hash the generated paper inputs into paper/SHA256SUMS (run after paper_figures.py)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--out", default=PAPER_DIR, help="directory for manifest.json/.tsv (default paper/); use another "
                    "one to compare a second database, e.g. DB_TARGET=layerbase ... --out $TMPDIR/lb")
    args = ap.parse_args()
    if args.files:
        text = file_sums()
        with open(SUMS, "w") as f:
            f.write(text)
        print(f"wrote {text.count(chr(10))} file hashes to {SUMS}")
        return
    conn = connect()
    try:
        m = write_manifest(conn, progress=not args.quiet, out_dir=args.out)
    finally:
        conn.close()
    d = m["database"]
    print(f"wrote manifest.json and manifest.tsv to {args.out}: {d['tables']} tables, {d['rows']:,} rows, "
          f"digest {d['digest_sha256'][:16]}")
    if m["code"]["uncommitted_changes_in_pipeline_code"]:
        print("note: scripts/ or api/ has uncommitted changes; the manifest records the commit they sit on")


if __name__ == "__main__":
    sys.exit(main())
