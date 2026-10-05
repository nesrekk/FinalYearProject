#!/usr/bin/env bash
# scripts/rebuild_all.sh -- every pipeline of NBA Hub in dependency order, from the outside feeds to the
# paper's inputs (round 5 step 9). One `step` line per script below; the order is the one CLAUDE.md and the
# README give per pipeline, checked by api/tests/test_reproducibility.py against the tables each script reads
# and writes (scripts/paper_manifest.py PRODUCERS).
#
#   scripts/rebuild_all.sh                      # print the plan and exit (nothing runs)
#   scripts/rebuild_all.sh paper-inputs         # ~1.5 min: manifest -> numbers.tex -> figures -> SHA256SUMS
#   scripts/rebuild_all.sh paper paper-inputs   # ~50 min: every paper_* table, then the inputs
#   scripts/rebuild_all.sh derived              # hours: every table built from other tables (app + paper inputs)
#   scripts/rebuild_all.sh load derived paper paper-inputs   # from the local raw files up (see "load")
#   scripts/rebuild_all.sh fetch                # network fetches (see the tags); run before load/derived
#   options: --dry-run (print what would run)   --from <script.py> (resume at that step)
#            --offline (skip steps tagged net/nbaapi/key inside the stages asked for)
#
# Stages, in the order they run when several are named:
#   fetch         outside feeds over the network. Tags: net = public ESPN/CDN endpoints (reachable),
#                 nbaapi = stats.nba.com through nba_api (it timed out from the author's machine from 2026-09-26
#                 and answers again since 2026-10-05, 0.1-2.5 s a call; plain curl still times out, so a curl
#                 check says "down"; the stored tables are what those calls returned), key = needs CBBD_API_KEY
#                 in api/.env.
#   load          local raw files into source tables. Tags name the gitignored input (docs/DATASHEET.md says
#                 where each comes from): kaggle = nba_data/kaggle_1947_present/, shotcsv =
#                 shots_data/pbp_shots_1997_2026/, salaries = nba_data/salaries/, torvik = nba_data/college_teams/.
#                 Untagged load steps read CSVs committed in nba_data/.
#   derived       everything built from other tables. Rewrites the app's tables and the committed model
#                 files (models/*.pkl, scripts/*.pkl): expect `git status` to show them afterwards.
#   paper         the paper's own tables (paper_* ; never touches an app table).
#   paper-inputs  paper/manifest.json, paper/numbers.tex, paper/figures/*.pdf, paper/SHA256SUMS (read-only on
#                 the database). The one command that regenerates every number, table and figure file the
#                 paper inputs; paper/tables/*.tex are written by the paper stage's scripts. It also checks, offline,
#                 that every \cite has a refs.bib entry with a "% verified" line (paper_refs_check.py).
#
# What no stage can rebuild (docs/DATASHEET.md, "Not rebuildable from this repository"): player_season_stats'
# 2009-10 to 2024-25 base rows (loaded before the first commit from the committed nba_data/nba_<season>_*.csv
# by a script that is not in the repository; every later column is added by the load/derived steps below),
# mvp_seasons and mvp_winners (same), and anything stats.nba.com served while it is unreachable.
#
# Every step runs with OMP_NUM_THREADS=4 (the author's machine is an 8 GB fanless laptop: one heavy job at a
# time). Random draws are seeded in the scripts (docs/REPRODUCIBILITY.md lists the seeds). A failing step stops
# the run and prints the --from line to resume. After a derived rebuild, restart the three API services: most
# routers cache their tables per process.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.14/bin/python3}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-4}"

DRY=0; OFFLINE=0; FROM=""; STAGES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --offline) OFFLINE=1 ;;
    --from) FROM="${2:?--from needs a script name}"; shift ;;
    fetch|load|derived|paper|paper-inputs) STAGES+=("$1") ;;
    -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done
PLAN_ONLY=0
if [ ${#STAGES[@]} -eq 0 ]; then PLAN_ONLY=1; DRY=1; STAGES=(fetch load derived paper paper-inputs); fi

wanted() { local s; for s in "${STAGES[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }

# gitignored inputs each tag needs
need_input() {
  case "$1" in
    kaggle)   echo "nba_data/kaggle_1947_present/Player Per Game.csv" ;;
    shotcsv)  echo "shots_data/pbp_shots_1997_2026/pbp2026.csv" ;;
    salaries) echo "nba_data/salaries/nba_salaries_2000_2020.csv" ;;
    torvik)   echo "nba_data/college_teams/cbb26.csv" ;;
    *) echo "" ;;
  esac
}

SEEN_FROM=0; [ -z "$FROM" ] && SEEN_FROM=1
N=0; T_ALL=$(date +%s)

# step <stage> <tags|-> <script> [args...]
step() {
  local stage="$1" tags="$2" script="$3"; shift 3
  wanted "$stage" || return 0
  if [ "$SEEN_FROM" -eq 0 ]; then
    [ "$script" = "$FROM" ] && SEEN_FROM=1 || return 0
  fi
  local t skip=""
  for t in ${tags//,/ }; do
    case "$t" in
      net|nbaapi|key) [ "$OFFLINE" -eq 1 ] && skip="offline ($t)" ;;
    esac
    local f; f="$(need_input "$t")"
    if [ -n "$f" ] && [ ! -e "$ROOT/$f" ] && [ "$DRY" -eq 0 ]; then
      echo "missing gitignored input for $script: $f (docs/DATASHEET.md says where it comes from)" >&2; exit 1
    fi
  done
  N=$((N + 1))
  local label; label=$(printf '%-13s %-34s %s' "[$stage]" "$script $*" "${tags//-/}")
  if [ -n "$skip" ]; then echo "skip  $label  -- $skip"; return 0; fi
  if [ "$DRY" -eq 1 ]; then echo "  $label"; return 0; fi
  if [ ! -f "$ROOT/scripts/$script" ]; then echo "no such script: scripts/$script" >&2; exit 1; fi
  echo "==> $label"
  local t0; t0=$(date +%s)
  if ! (cd "$ROOT/scripts" && "$PY" "$script" "$@"); then
    echo "FAILED: $script. Resume with: scripts/rebuild_all.sh ${STAGES[*]} --from $script" >&2; exit 1
  fi
  echo "    done in $(( $(date +%s) - t0 )) s"
}

[ "$PLAN_ONLY" -eq 1 ] && echo "Plan (nothing runs; name stages to run them, --help for usage):"

# ------------------------------------------------------------------ fetch: outside feeds over the network
step fetch net      fetch_pbp_espn.py 2021 2026          # ESPN play-by-play via sportsdataverse (hosted release)
step fetch nbaapi   fetch_play_by_play.py                # nba_api sample (420 games stored, all ESPN twins; args: season n)
step fetch nbaapi   fetch_2025_26_season_data.py         # -> nba_data/nba_2025_26_*.csv (committed)
step fetch nbaapi   build_schedule_fatigue.py            # team_game_fatigue (LeagueGameFinder)
step fetch net      fetch_game_scores.py                 # ESPN scoreboard, one request per date (~7 min)
step fetch net      fetch_postseason_games.py            # ESPN scoreboard, play-in and playoffs (~3 min)
step fetch -        ledger_lock.py --verify              # Forecast Ledger: a lock is made once by hand before a season's
                                                         # first tip (--lock) and never rebuilt; this re-checks its hash
step fetch net      ledger_update.py                     # Forecast Ledger nightly: ESPN finals, odds from the tagged code,
                                                         # scores (appends; a rerun of a night adds nothing)
step fetch nbaapi   fetch_referee_officials.py           # resumable: grows coverage each run
step fetch nbaapi   fetch_spacing_data.py
step fetch nbaapi   fetch_matchups.py
step fetch nbaapi   fetch_defend_dashboard.py
step fetch nbaapi   fetch_hustle_stats.py
step fetch nbaapi   fetch_playtypes.py
step fetch nbaapi   fetch_shot_context.py
step fetch nbaapi   fetch_draft_combine.py
step fetch nbaapi   build_defense_tracking_stats.py
step fetch key      fetch_cbb_games.py                   # CollegeBasketballData.com, 84 calls
step fetch key      fetch_college_stats.py

# ------------------------------------------------------------------ load: local raw files into source tables
step load kaggle    load_kaggle_historical_seasons.py    # pre-2010 player_season_stats + player_id_map (bref_nba_ids)
step load -         load_2025_26_into_db.py              # 2025-26 player_season_stats from committed CSVs
step load -         add_shooting_efficiency_stats.py     # counting stats from committed CSVs (additive columns)
step load -         add_personal_fouls.py
step load shotcsv   load_pbp_shots.py                    # player_shots, 6.3M rows
step load kaggle    load_draft_history_bref.py
step load salaries  load_salaries.py
step load torvik    load_college_teams.py
step load -         fetch_all_nba_teams.py               # static label list inside the script
step load -         fetch_dpoy_roy_stats.py              # static winner lists + committed CSVs
step load -         load_nba75_team.py                   # static list inside the script
step load -         repair_espn_player_ids.py --apply    # fixes fuzzy-matched ESPN ids; prints "nothing to fix" when clean

# ------------------------------------------------------------------ derived: season tables, awards, clusters
step derived -        build_bpm_vorp.py                  # *_repro columns + bpm_position
step derived kaggle   load_bref_bpm_vorp.py              # Basketball-Reference's published BPM/VORP (after build_bpm_vorp)
step derived -        compute_impact_score.py
step derived -        upgrade_impact_scores.py
step derived kaggle   build_player_profile_data.py       # player_bio, player_awards, player_team_stints
step derived kaggle   build_first_nba_season.py
step derived kaggle   build_contract_value.py
step derived kaggle   build_league_averages.py
step derived kaggle   build_team_seasons.py
step derived kaggle   build_luck_schedule.py
step derived -        build_award_winners_table.py
step derived -        train_mvp_model.py
step derived -        build_dpoy_roy_models.py
step derived -        build_all_nba_model.py
step derived -        backtest_models.py
step derived -        build_shap_explanations.py
step derived -        calibrate_award_chances.py
step derived -        train_win_model.py
step derived -        cluster_players.py
step derived -        cluster_playtypes.py
step derived nbaapi   train_pair_synergy.py              # fetches two-man lineups live
step derived -        build_similarity_engine.py
step derived -        build_league_adjusted_similarity.py
step derived -        precompute_league_similarity.py
step derived -        precompute_career_similarity.py
step derived -        build_aging_curves.py
step derived -        build_dad_index.py
step derived -        build_gravity_index.py
step derived -        build_referee_tendencies.py
step derived kaggle,torvik build_college_pipeline.py
step derived -        build_ncaa_model.py
step derived kaggle,net build_greats.py                  # checks NBA CDN headshots
# ------------------------------------------------------------------ derived: shots
step derived -        build_league_zone_mix.py
step derived -        build_player_roles.py
step derived -        build_shot_making.py               # ~3 min; also shot_xfg
# ------------------------------------------------------------------ derived: play-by-play (order from CLAUDE.md)
step derived -        train_wpa_model.py
step derived -        build_event_clock.py               # ~1.5 min; read by possessions, rotations, play finder
step derived -        build_player_game_lines.py
step derived -        build_team_game_totals.py          # read by build_lineup_stints, build_possessions, build_player_on_off
step derived -        build_lineup_stints.py
step derived -        build_player_game_onfloor.py       # ~35 s; reads lineup_stints, lineup_stint_games
step derived -        build_player_on_off.py             # reads player_game_onfloor, team_game_totals, game_scores
step derived -        build_possessions.py               # ~3.5 min; reads lineup_stints, team_game_totals
step derived -        build_stat_stability.py
step derived -        build_hot_streak_persistence.py
step derived -        build_situational_splits.py
step derived -        build_projections.py
step derived -        build_rapm.py                      # ~5 min
step derived -        build_shot_value.py                # ~20 min; 30 look-ahead-free shot fits; reads player_game_lines, shot_xfg (folds); imports build_rapm
step derived -        build_rating_tracker.py            # ~8 min; reads player_rapm (validation); its fit row is read by paper_eval.py
step derived -        build_rotations.py
step derived -        build_rim_deterrence.py
step derived -        build_assist_network.py
step derived -        build_play_finder.py
step derived -        build_best_games.py
step derived kaggle   build_team_zone_mix.py
step derived -        compute_wpa.py
step derived -        build_leverage_splits.py
step derived -        build_scouting_reports.py          # reads player_leverage_splits
step derived -        build_season_sim.py
step derived -        build_coaching_decisions.py        # ~4 min; reads possessions, pbp_event_clock, game_pregame_odds

# ------------------------------------------------------------------ paper: the paper's own tables
step paper -          paper_xrapm.py                     # ~90 s
step paper -          paper_eval.py                      # ~7 min
step paper -          paper_tests.py                     # ~75 s
step paper -          build_pregame_availability.py      # ~20 s; app tables (Season Simulator what-if), reads paper_eval_predictions
step paper -          build_lineup_predictor.py          # ~40 s; app tables (Rotations' lineup panel); imports paper_eval/paper_tests for the protocol and tests
step paper -          build_report_card.py               # ~10 min; app tables (Model Report Card): every model season by season, rolling origin; checks against paper_eval_predictions
step paper -          paper_data_audit.py                # ~95 s; also paper/tables/data_audit.tex
step paper -          build_data_quality.py              # ~10 min; app tables (Data Quality): per-game flags + re-scores with flagged games dropped; must equal paper_data_audit
step paper -          paper_beliefs.py                   # ~22 min; also paper/tables/beliefs.tex
step paper -          paper_ablations.py                 # ~11 min; also paper/tables/ablations.tex

# ------------------------------------------------------------------ paper-inputs: one command for the paper's inputs
step paper-inputs -   paper_manifest.py --quiet          # ~40 s: paper/manifest.json, manifest.tsv
step paper-inputs -   paper_numbers.py --check           # paper/numbers.tex (stops if the manifest is stale)
step paper-inputs -   paper_refs_check.py                # no DB: every \cite has a verified refs.bib entry
step paper-inputs -   paper_figures.py                   # paper/figures/*.pdf
step paper-inputs -   paper_manifest.py --files          # paper/SHA256SUMS (check: cd paper && shasum -a 256 -c SHA256SUMS)

if [ "$SEEN_FROM" -eq 0 ]; then echo "--from $FROM: no such step in the stages asked for" >&2; exit 2; fi
if [ "$DRY" -eq 0 ]; then echo "all $N steps done in $(( $(date +%s) - T_ALL )) s"; fi
