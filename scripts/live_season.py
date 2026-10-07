"""
live_season.py
===============
Every table's place in the live 2026-27 season (round 9 step 1, 2026-10-06): which tables the daily update
may add 2026-27 rows to, which are recomputed season-to-date, which wait for the season's end, and which
stay frozen with the paper. Prints the table that docs/LIVE_SEASON.md carries, from the database (row counts,
sizes, season spans) and the classification below.

    cd scripts && python3 live_season.py            # the markdown table (reads the database, ~20 s)
    cd scripts && python3 live_season.py --check    # exit 1 if docs/LIVE_SEASON.md's table has a different
                                                    # classification (the numeric columns are dated, not checked)

The classes
-----------
  daily           rebuilt or appended for 2026-27 on every game day: step 9-2 fetches the day's games, step 9-3
                  rebuilds that season's rows only (never another season's)
  season-to-date  a model or aggregate recomputed for 2026-27 so far (step 9-4; a weekly fetch is enough for the
                  stats.nba.com dashboards): 2026-27 rows are added or replaced, earlier seasons' rows are not
                  touched, and any pooled fit stays fitted on seasons <= 2025-26 and is only applied to 2026-27
  season-end      refreshed once the season is over (awards, draft, clustering, projections, the
                  Basketball-Reference export)
  frozen          not rebuilt during the season: the paper's tables, and the pooled fits and checks without a
                  season dimension that the paper reads (api/paper_freeze.py explains why they cannot change)
  static          reference data that doesn't change
  ledger          the Forecast Ledger: only scripts/ledger_update.py writes it (the lock is never rebuilt)
  cache           written by the running app

The invariant api/tests/test_live_season.py checks: a table may be daily or season-to-date only if it has a
season dimension (api/paper_freeze.paper_predicate), so the paper's rows can be told from the season's.
"""

import argparse
import os
import re
import sys

import psycopg2

from db_config import DB_CONFIG

_API_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api")
if _API_DIR not in sys.path:
    sys.path.append(_API_DIR)
import paper_manifest as PM  # noqa: E402
from paper_freeze import EXPLICIT_PREDICATES, LEDGER_TABLES, MAX_PAPER_SEASON, TEXT_SEASON_TABLES, paper_predicate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "LIVE_SEASON.md")
BEGIN, END = "<!-- live_season:begin -->", "<!-- live_season:end -->"

CLASSES = ("daily", "season-to-date", "season-end", "frozen", "static", "ledger", "cache")

# Default class per producing script (scripts/paper_manifest.PRODUCERS); OVERRIDES refine single tables.
BY_PRODUCER = {
    # sources
    "fetch_pbp_espn.py": "daily", "load_pbp_shots.py": "daily", "fetch_game_scores.py": "daily",
    "fetch_postseason_games.py": "daily", "build_schedule_fatigue.py": "daily", "fetch_referee_officials.py": "daily",
    "fetch_spacing_data.py": "season-to-date", "fetch_matchups.py": "season-to-date", "fetch_defend_dashboard.py": "season-to-date",
    "fetch_hustle_stats.py": "season-to-date", "fetch_playtypes.py": "season-to-date", "fetch_shot_context.py": "season-to-date",
    "fetch_draft_combine.py": "season-end", "build_defense_tracking_stats.py": "static",
    "fetch_cbb_games.py": "season-end", "fetch_college_stats.py": "season-end", "load_college_teams.py": "season-end",
    "load_salaries.py": "static", "load_kaggle_historical_seasons.py": "season-end", "load_draft_history_bref.py": "season-end",
    "fetch_all_nba_teams.py": "season-end", "fetch_dpoy_roy_stats.py": "season-end", "load_nba75_team.py": "static",
    "(before the first commit)": "season-end",
    "api/shots_lib.py": "cache", "snapshot_predictions.py": "cache",
    "ledger_lock.py": "ledger", "ledger_update.py": "ledger",
    "daily_update.py": "daily",
    # derived
    "build_player_profile_data.py": "season-end", "build_first_nba_season.py": "season-end",
    "build_award_winners_table.py": "season-end", "build_contract_value.py": "static", "build_league_averages.py": "season-end",
    "build_team_seasons.py": "season-end", "build_luck_schedule.py": "season-to-date",
    "build_all_nba_model.py": "season-end", "backtest_models.py": "season-end", "build_shap_explanations.py": "season-end",
    "calibrate_award_chances.py": "season-end", "train_win_model.py": "season-end", "train_pair_synergy.py": "static",
    "cluster_players.py": "season-end", "cluster_playtypes.py": "season-end", "build_player_roles.py": "season-end",
    "precompute_league_similarity.py": "season-end", "precompute_career_similarity.py": "season-end",
    "build_aging_curves.py": "season-end", "build_projections.py": "season-end", "build_greats.py": "static",
    "build_dad_index.py": "season-to-date", "build_gravity_index.py": "season-to-date", "build_scouting_reports.py": "season-to-date",
    "build_referee_tendencies.py": "frozen", "build_college_pipeline.py": "season-end", "build_ncaa_model.py": "season-end",
    "build_league_zone_mix.py": "season-to-date", "build_shot_making.py": "season-to-date", "build_shot_value.py": "season-to-date",
    "build_team_zone_mix.py": "season-to-date",
    "train_wpa_model.py": "frozen", "build_event_clock.py": "daily", "compute_wpa.py": "frozen",
    "build_leverage_splits.py": "daily", "build_player_game_lines.py": "daily", "build_team_game_totals.py": "daily",
    "build_player_on_off.py": "daily", "build_lineup_stints.py": "daily", "build_player_game_onfloor.py": "daily",
    "build_possessions.py": "daily", "build_coaching_decisions.py": "frozen",
    "build_lineup_predictor.py": "frozen", "build_report_card.py": "frozen", "build_data_quality.py": "frozen",
    "build_stat_stability.py": "frozen", "build_hot_streak_persistence.py": "frozen", "build_situational_splits.py": "daily",
    "build_rapm.py": "season-to-date", "build_rating_tracker.py": "season-to-date", "build_rotations.py": "daily",
    "build_rim_deterrence.py": "daily", "build_assist_network.py": "daily", "build_play_finder.py": "daily",
    "build_best_games.py": "daily", "build_season_sim.py": "season-to-date", "build_pregame_availability.py": "frozen",
    # paper
    "paper_xrapm.py": "frozen", "paper_eval.py": "frozen", "paper_tests.py": "frozen", "paper_data_audit.py": "frozen",
    "paper_beliefs.py": "frozen", "paper_ablations.py": "frozen",
}

OVERRIDES = {
    # a producer's pooled or check tables stay with the paper while its per-season rows move
    "pbp_event_clock_meta": "frozen", "best_games_meta": "frozen", "leverage_index_grid": "frozen",
    "leverage_validation": "season-to-date", "luck_model_fit": "frozen", "luck_schedule_validation": "frozen",
    "gravity_validation": "frozen", "scouting_validation": "frozen", "zone_classifier_check": "static",
    "shot_making_validation": "frozen", "shot_hex_meta": "frozen", "shot_value_fit": "frozen", "shot_value_validation": "frozen",
    "rating_tracker_fit": "frozen", "rating_tracker_curve": "frozen",
    "pregame_model_fit": "frozen", "pregame_calibration": "frozen", "season_sim_params": "frozen",
    "season_sim_backtest_summary": "frozen", "season_sim_calibration": "frozen", "season_postseason": "season-end",
    "projection_ranges": "frozen", "projection_stats": "frozen",
    "player_game_onfloor_meta": "frozen", "possession_meta": "frozen",
    # a producer's tables that move at different paces
    "player_season_stats": "season-to-date", "player_id_map": "season-end",
    "league_minimum_salary": "static", "mvp_seasons": "season-end", "mvp_winners": "season-end",
    "prediction_ledger": "cache",
}

NOTES = {
    "pbp_events": "9-2 reads ESPN's game summary per final (site.api.espn.com .../summary?event=, 520 plays with coordinates, "
                  "participants and wallclock); the sportsdataverse release this fetch uses was last updated 2026-09-09 and has no 2027 file",
    "pbp_games": "one row per ESPN game (source 'espn'); the nba_api twins stay at 420 games",
    "player_shots": "ShotChartDetail per team and season type (fetch_season_shots.py's path, 90 calls a season), or per game "
                    "(game_id_nullable); 2026-27 rows get season '2026-27'",
    "game_scores": "ESPN scoreboard by US date; 9-2 must match 2026-27 finals to NBA ids without team_game_fatigue (LeagueGameFinder "
                   "answers again, so keep the join) ",
    "team_game_fatigue": "LeagueGameFinder, one call a season (2.4 s cold); plus_minus is not the margin (README)",
    "game_officials": "BoxScoreSummaryV2 answers nothing for 2025-04-10 on: 9-2 moves the fetch to BoxScoreSummaryV3 (officials with "
                      "personId) or ESPN's summary gameInfo.officials (names only)",
    "game_team_box": "LeagueGameFinder team rows (PF, FTA, FGA, OREB, TOV)",
    "player_season_stats": "2026-27 rows refreshed from LeagueDashPlayerStats (235 rows after one preseason game); every earlier season untouched",
    "player_bio": "no season dimension (a row per player, last_season moves): rookies' rows wait for the season's end (R9-003); "
                  "the 2026-27 lines identify rookies through player_season_stats' 2027 rows, not this fallback",
    "player_id_map": "as player_bio: rookies' Basketball-Reference <-> NBA ids at the season's end",
    "player_first_season": "as player_bio: rookies' first season (2027) at the season's end",
    "player_game_onfloor_meta": "checks over every game, no season dimension: the --season builds leave it as it is (R9-008)",
    "possession_meta": "as player_game_onfloor_meta",
    "lineup_stints": "build_lineup_stints.py --season 2027 deletes and rebuilds that season only, through the same Game.walk() "
                     "code; api/tests/test_season_rebuild.py proves --season 2026 equals the full build's 2025-26 rows",
    "player_game_lines": "heaves: since 2025-26 the NBA counts a missed end-of-quarter heave as a team attempt; ESPN logs it "
                         "('Heave Jump Shot', ~1,100 a season) and the lines charge the shooter (R8-088)",
    "situational_split_league": "the season 0 (pooled) row is the paper's: a --season run writes only its season's row",
    "coaching_decisions": "the league-wide tests (BH families) pool every season: rebuild at the season's end",
    "player_wpa_totals": "career totals with no season dimension: the Clutch WPA page cannot take 2026-27 until a season column is added "
                         "(R9-002); paper_beliefs recomputes and checks against this table",
    "wpa_clutch_league": "as player_wpa_totals",
    "hot_streak_persistence": "measured constants (slopes, null centre): frozen; the app's Hot Streak card reads them for 2026-27 too",
    "referee_tendencies": "pooled over 2020-21 to 2025-26 with no season dimension: a 2026-27 version needs a season or through column (R9-002)",
    "referee_crew_tendencies": "as referee_tendencies",
    "stat_stability": "the M constants 9-4 uses for early-season reliability warnings: frozen",
    "player_rapm": "9-4: one-season + prior for 2027 (wide intervals, said so); rapm_fits / rapm_lambda_cv gain 2027 rows, lambda by the same CV",
    "player_rating_tracker": "9-4: the filtered rating through today for 2027; hyperparameters from rating_tracker_fit, never re-tuned",
    "game_pregame_odds": "9-4: 2027 odds from the fit on <= 2025-26 (pregame_model_fit frozen), separate from the locked ledger",
    "season_sim_seasons": "9-4: the Season Simulator's 2027 row; season_sim_params frozen",
    "player_projections": "the 2027 projections are stored: 9-4 shows projected vs actual, never refits; 2028 at the season's end",
    "projection_backtest_rows": "a 2027 row appears only when the season is complete",
    "shot_xfg": "9-4 may score 2026-27 shots with the five fold models refitted on <= 2025-26 (the fit is deterministic: ORDER BY id); "
                "the stored 2020-21 to 2025-26 P(make) must not move",
    "shot_value_shots": "the filter is built to update game by game: 2027 rows from the frozen fit",
    "shot_value_states": "as shot_value_shots",
    "shot_value_added": "as shot_value_shots",
    "league_zone_mix": "one 2026-27 row set from the season's shots",
    "team_zone_mix": "as league_zone_mix",
    "lineup_stats": "LeagueDashLineups top 2,000 lineups a season: weekly is enough",
    "player_matchups": "LeagueSeasonMatchups, ~70,000 rows a season (136 MB for nine): weekly, the biggest nba_api fetch",
    "team_luck_schedule": "9-4: 2027 rows from the frozen luck_model_fit",
    "league_shot_zones": "GET /shots/league-zones/2027 would insert a 2026-27 row (ENABLE_LIVE_SHOT_FETCH off by default)",
    "prediction_ledger": "snapshot_predictions.py may log 2026-27 award predictions (season 2027)",
    "ledger_results": "LIVE: grows with the season; outside the manifest digest",
    "ledger_game_log": "LIVE", "ledger_team_log": "LIVE", "ledger_runs": "LIVE", "ledger_tests": "LIVE (from 100 scored games)",
    "defense_tracking_stats": "empty since its endpoint stopped answering",
    "daily_update_runs": "LIVE: one row per daily_update.py run (what was checked, written, left pending, failed); outside the manifest digest",
}

LATEST_SQL = {   # rows of the newest season for tables without a season column (the one-season size estimate)
    "pbp_events": "game_id IN (SELECT game_id FROM pbp_games WHERE season = (SELECT max(season) FROM pbp_games))",
    "pbp_event_clock": "event_id IN (SELECT e.id FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id "
                       "WHERE g.season = (SELECT max(season) FROM pbp_games))",
    "play_finder_events": "event_id IN (SELECT e.id FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id "
                          "WHERE g.season = (SELECT max(season) FROM pbp_games))",
    "game_officials": "substr(game_id, 4, 2) = (SELECT substr(max(game_id), 4, 2) FROM game_officials)",
    "game_officials_fetch_log": "substr(game_id, 4, 2) = (SELECT substr(max(game_id), 4, 2) FROM game_officials_fetch_log)",
    "pregame_availability_players": "game_id IN (SELECT game_id FROM pregame_availability_odds WHERE season = "
                                    "(SELECT max(season) FROM pregame_availability_odds))",
    "player_projections": "last_season = (SELECT max(last_season) FROM player_projections)",
}


def classify(table):
    kind, producer = PM.TABLES[table]
    return OVERRIDES.get(table, BY_PRODUCER.get(producer, "frozen" if kind == "paper" else None))


def season_dim(table, columns):
    if table in LEDGER_TABLES:
        return "2026-27 (lock)"
    if table in EXPLICIT_PREDICATES:
        return {"player_projections": "last_season", "game_officials": "game id", "game_officials_fetch_log": "game id",
                "pbp_events": "game id", "pregame_availability_players": "game id",
                "player_shots_cache_status": "updated_at"}.get(table, "event id")
    if "season" in columns:
        return "season (text)" if table in TEXT_SEASON_TABLES else "season (int)"
    return "—"


def granularity(producer):
    """How the producer writes today, read from its source: whole table, per season, incremental, or a mix."""
    path = os.path.join(ROOT, "scripts", producer)
    if not os.path.exists(path):
        return "—"
    with open(path) as f:
        src = f.read()
    parts = []
    if re.search(r"DROP TABLE|TRUNCATE", src, re.I):
        parts.append("whole table")
    if re.search(r"DELETE FROM \w+\s+WHERE[^;]*season", src, re.I):
        parts.append("per season")
    if "ON CONFLICT" in src:
        parts.append("incremental")
    return " + ".join(parts) or "—"


def inventory(conn):
    cur = conn.cursor()
    cur.execute("SET lock_timeout = '5s'")
    sch = PM.schemas(cur)
    cur.execute("""SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')""")
    size = dict(cur.fetchall())
    rows = []
    for t in PM.db_tables(cur):
        kind, producer = PM.TABLES[t]
        cols = sch[t][3]
        pred = paper_predicate(t, cols)
        cur.execute(f'SELECT count(*) FROM "{t}"')
        n = cur.fetchone()[0]
        last = None
        if t in LATEST_SQL:
            cur.execute(f'SELECT count(*) FROM "{t}" WHERE {LATEST_SQL[t]}')
            last = cur.fetchone()[0]
        elif "season" in cols and t not in LEDGER_TABLES and n:
            cur.execute(f'SELECT count(*) FROM "{t}" WHERE season = (SELECT max(season) FROM "{t}")')
            last = cur.fetchone()[0]
        mb = size[t] / 1e6
        rows.append({"table": t, "kind": kind, "producer": producer, "season_dim": season_dim(t, cols),
                     "paper_rows": "lock" if t in LEDGER_TABLES else ("capped" if pred else "whole"),
                     "cls": classify(t), "granularity": granularity(producer), "rows": n, "last": last, "mb": mb,
                     "mb_season": (mb * last / n) if (last is not None and n) else None, "note": NOTES.get(t, "")})
    return rows


HEAD = ["Table", "Kind", "Producer", "Season dim", "Paper rows", "Live season", "Rebuild today", "Rows", "Last season",
        "MB", "MB/season", "Note"]
CLASSIFICATION_COLUMNS = 7     # the first seven columns are the classification --check compares


def markdown(rows):
    out = ["| " + " | ".join(HEAD) + " |", "|" + "---|" * len(HEAD)]
    for r in rows:
        cells = [f"`{r['table']}`", r["kind"], f"`{r['producer']}`", r["season_dim"], r["paper_rows"], f"**{r['cls']}**",
                 r["granularity"], f"{r['rows']:,}", "" if r["last"] is None else f"{r['last']:,}", f"{r['mb']:.1f}",
                 "" if r["mb_season"] is None else f"{r['mb_season']:.1f}", r["note"].replace("|", "/")]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def summary(rows):
    by = {}
    for r in rows:
        d = by.setdefault(r["cls"], {"tables": 0, "mb": 0.0, "mb_season": 0.0, "rows_season": 0})
        d["tables"] += 1
        d["mb"] += r["mb"]
        d["mb_season"] += r["mb_season"] or 0.0
        d["rows_season"] += r["last"] or 0
    lines = ["| Live season | Tables | MB now | Rows of the newest season | MB one season adds |", "|---|---|---|---|---|"]
    for c in CLASSES:
        d = by.get(c)
        if d:
            lines.append(f"| **{c}** | {d['tables']} | {d['mb']:,.0f} | {d['rows_season']:,} | {d['mb_season']:,.0f} |")
    return "\n".join(lines)


def doc_block():
    with open(DOC) as f:
        text = f.read()
    i, j = text.index(BEGIN), text.index(END)
    return text[i + len(BEGIN):j].strip()


def classification_rows(md):
    out = []
    for line in md.splitlines():
        if line.startswith("| `"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out.append(tuple(cells[:CLASSIFICATION_COLUMNS]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="compare the classification with docs/LIVE_SEASON.md's table")
    args = ap.parse_args()
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    try:
        rows = inventory(conn)
    finally:
        conn.close()
    missing = [r["table"] for r in rows if r["cls"] not in CLASSES]
    if missing:
        raise SystemExit("tables with no live-season class (add them to live_season.BY_PRODUCER / OVERRIDES): " + ", ".join(missing))
    md = markdown(rows)
    if args.check:
        a, b = classification_rows(md), classification_rows(doc_block())
        if a != b:
            diff = [f"  doc: {x}\n  now: {y}" for x, y in zip(b, a) if x != y][:10] + (
                [f"  {len(a)} tables now, {len(b)} in the doc"] if len(a) != len(b) else [])
            raise SystemExit("docs/LIVE_SEASON.md's table is stale (rerun live_season.py and paste its output):\n" + "\n".join(diff))
        print(f"docs/LIVE_SEASON.md: the classification of all {len(a)} tables is current")
        return
    print(summary(rows))
    print()
    print(md)


if __name__ == "__main__":
    main()
