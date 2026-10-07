# The live 2026-27 season: design and guards (round 9 step 1, 2026-10-06)

Round 9 keeps NBA Hub current through the 2026-27 season (first tip 2026-10-20 19:00 UTC): one command a day
fetches the new games and refreshes every season-to-date page, while **the paper stays frozen on 2025-26**. This
file is step 1's design: what each live source gives (measured), what every table is during the season, the
paper freeze guard that makes the rest safe, and the size of one season. Steps 9-2 (daily fetch), 9-3 (daily
rebuild of the current season only), 9-4 (season-to-date models) build on it; the issue list is
`docs/qa/ROUND9_ISSUES.md`.

**Decided here**

1. `api/paper_freeze.py` holds the one constant, `MAX_PAPER_SEASON = 2026` (2025-26, the protocol's test season),
   and the rule for picking **the paper's rows** of a table: `season <= 2026` (text seasons `<= '2025-26'`), the
   game's or event's season for the play-by-play tables, `last_season <= 2026` for projections. Paper-stage scripts
   read tables the season adds to only through `F(table)` (the table restricted to the paper's rows); shared loaders
   take the bound as `through=`. Tested statically and dynamically (`api/tests/test_paper_frozen.py`).
2. `scripts/paper_manifest.py` counts and hashes each table **over the paper's rows**, and leaves the Forecast
   Ledger's live log out of the digest (R8-086): a database that has gained a whole season of rows produces the same
   manifest, `numbers.tex` and figures. "`rebuild_all.sh paper-inputs` byte-identical through the round" is now a
   property of the code, not a hope.
3. **A table may change during the season only if it has a season dimension.** Every daily or season-to-date
   table below has one (`api/tests/test_live_season.py` checks it). A pooled table without one that the paper reads
   (clutch WPA totals, referee tendencies, stat stability, hot-streak persistence, every model's fit row) stays
   frozen until it gains a season or `through` column (R9-002, R9-003).
4. **Pooled fits are fitted on seasons <= 2025-26 and only applied to 2026-27** (shot-making's cross-fit, Shot Value's
   fit, the pre-game model and simulator constants, the luck fit, the Rating Tracker's hyperparameters, the WPA model):
   nothing re-tunes on the live season (R9-009). Per-season tables (RAPM, lineups, lines, stints, possessions, ...)
   gain 2027 rows; rows of earlier seasons are never rewritten by the daily update.
5. The daily play-by-play comes from **ESPN's game summary per game, not the sportsdataverse release** (its 2026 file
   was last updated 2026-09-09 and there is no 2027 file; R9-004); officials from `BoxScoreSummaryV3` (V2 answers
   nothing for games from 2025-04-10; R9-005); shots from `ShotChartDetail` per team (fetch_season_shots.py's path);
   finals, team rows and season stats from ESPN's scoreboard, `LeagueGameFinder` and `LeagueDashPlayerStats`.

## 1. What each live source gives (measured 2026-10-06, 2026-27 preseason)

Eight preseason games were final (2026-10-03 to 10-05 US dates); opening night is 2026-10-20 (BOS@DET 19:00Z,
PHI@NY 23:00Z, OKC@SA 01:30Z). Times are from this Mac; `nba_api` 1.11.4 with the library's own headers (a probe
with a minimal `User-Agent`/`Referer` set timed out at 30 s on every stats.nba.com call; R9-006).

| Source | Call | What it gives | Measured | How it fails / notes | Round 9 use |
|---|---|---|---|---|---|
| ESPN scoreboard | `site.api.espn.com/.../scoreboard?dates=YYYYMMDD` | every game of a US date: id, tip (UTC), teams, scores, `status.type.completed`, `season.type` (1 preseason, 2 regular, 3 playoffs, 5 play-in), `playByPlayAvailable` | 0.5-1.3 s a date; 1 / 2 / 5 / 4 events on 10-03 to 10-06; 3 and 11 on 10-20 / 10-21 | the same endpoint `fetch_game_scores.py`, `ledger_update.py` and `api/espn_live.py` already read; dates are US dates (R8-067) | **9-2:** which games are final since the last run |
| ESPN game summary | `.../summary?event=<id>` | the play-by-play (522 plays for LAL@SAC 2026-10-05: `id`, `sequenceNumber`, `type` (id + text), `text`, `period`, `clock`, `scoringPlay`, `scoreValue`, `pointsAttempted`, `shootingPlay`, `team.id`, `participants[].athlete.id`, `homeScore`/`awayScore`, `coordinate`, `wallclock`), the box score (33 players with statistics), `gameInfo.officials` (3 names), win probability | 1.5 s, 440 kB a game; also at `site.web.api.espn.com/apis/site/v2/...` (same payload) | a different shape from the sportsdataverse rows `fetch_pbp_espn.py` loads (`type.text` vs `type_text`, athlete ids under `participants`, the clock as `displayValue`): 9-2 must map it to `pbp_events`' columns and prove a mapped game equals the release's rows on a 2025-26 game | **9-2:** `pbp_events` / `pbp_games` for every final; officials' names as a fallback |
| sportsdataverse release | `github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_pbp/play_by_play_<season>.parquet` | a whole season's ESPN play-by-play in one file (`fetch_pbp_espn.py`) | 2026: 22.4 MB parquet (281 MB csv), **last modified 2026-09-09**; 2027: **404** | updated irregularly, no 2027 file before the season: unusable for a daily update (R9-004) | history only (2020-21 to 2025-26 as loaded) |
| nba_api LeagueGameFinder | `season_nullable='2026-27', season_type_nullable='Pre Season'`, league 00 | one team row per team-game: ids `0012600004`..., date, matchup, W/L, box line, `PLUS_MINUS` | 2.4 s cold, 0.5 s warm; 16 rows / 8 games (10-03 to 10-05), every final through the previous US evening | regular season: 0 rows until 10-20; `plus_minus` is not the margin (README) | **9-2:** `team_game_fatigue` (via `build_schedule_fatigue.py`'s per-season path), `game_team_box`, the NBA game ids to match ESPN's finals |
| nba_api LeagueDashPlayerStats | `season='2026-27'`, Pre Season / Regular Season, PerGame | one row per player who has played: the base table `fetch_2025_26_season_data.py` writes | 0.5 s; 235 rows after one preseason game each (max GP 1); 2025-26 control 582 rows | regular season 0 rows until 10-20 | **9-2:** `player_season_stats`' 2027 rows, refreshed daily (rookies get their ids here) |
| nba_api ShotChartDetail | per team (`team_id`, `player_id=0`, `season_nullable`, `season_type_all_star`, `context_measure_simple='FGA'`) or per game (`game_id_nullable`) | every attempt with coordinates, the rows `fetch_season_shots.py` stages | per team 0.95 s (ATL: 94 shots, 1 game); **per game 23 s** (177 shots) | the per-game form is slow: fetch per team and season type, as round 8.5 step C does (90 calls a season) | **9-2:** `player_shots`' 2026-27 rows |
| nba_api BoxScoreSummaryV2 | `game_id` | officials (the fetch `fetch_referee_officials.py` uses) | **empty answer** (`JSONDecodeError`) for `0012600004`; nba_api itself warns: no data for games from 2025-04-10 | broken for the live season (R9-005) | none |
| nba_api BoxScoreSummaryV3 | `game_id` | `boxScoreSummary.officials` (`personId`, `name`, ...), arena, attendance, status | 1.3 s | not used anywhere yet | **9-2:** `game_officials` for 2026-27 (ids, like V2's) |
| nba_api PlayByPlayV3 | `game_id` | 542 actions a game: `actionNumber`, `clock`, `period`, `teamId`, `personId`, `xLegacy`/`yLegacy`, `shotDistance`, `shotResult`, `isFieldGoal`, scores, `description`, `actionType`, `subType`, `shotValue` | 0.5 s | one call per game (the 420 stored twins came from it); NBA.com's clock, not ESPN's (the corrected clock's twin check uses exactly this) | not in the daily run; the twin check's source if ever re-measured |

How soon after a final the sources answer was not measurable on 2026-10-06 (no game was in progress from this
time zone): every game final by the previous US evening was in LeagueGameFinder, LeagueDashPlayerStats and
ShotChartDetail the next morning (IST). **Step 9-2 measures the same-day lag on opening night** and logs it.

## 2. Every table during the season

Generated by `cd scripts && python3 live_season.py` from the database and the classification in that script
(`BY_PRODUCER`, `OVERRIDES`, `NOTES`); `python3 live_season.py --check` and `api/tests/test_live_season.py` fail
when a table's class in this file differs from the script's. The numeric columns are as of 2026-10-06, after the
round 8.5 step C rebuild and step 2's first run (which created `daily_update_runs`, the 219th table).

Columns: **Season dim** = how the paper's rows are told from the season's (`season (int)` end year, `season (text)`
'YYYY-YY', `game id`, `event id`, `last_season`, `2026-27 (lock)`, or `—` none); **Paper rows** = what
`paper_manifest.py` hashes (`capped` = the predicate, `whole` = every row, `lock`); **Live season** = the class
(below); **Rebuild today** = how the producing script writes now (read from its source: whole table, per season,
incremental), which is what step 9-3 changes to a `--season` mode for the daily tables; **Last season** = rows of
the newest season (the one-season size estimate's basis); **MB/season** = that share of the table's size.

The classes:

- **daily**: rebuilt or appended for 2026-27 on every game day. Step 9-2 fetches the day's finals (source tables);
  step 9-3 deletes and rebuilds **only season 2027's rows** of each derived table through the same code as the full
  build (a test proves `--season 2026` gives byte-identical rows to the full rebuild's 2025-26 rows).
- **season-to-date**: a model or aggregate recomputed for 2026-27 so far (step 9-4; a weekly fetch is enough for the
  stats.nba.com dashboards): 2027 rows added or replaced, earlier seasons untouched, pooled fits held at <= 2025-26.
- **season-end**: refreshed once the season is over (awards, draft, clustering, projections, the Kaggle export).
- **frozen**: not rebuilt during the season: the paper's tables, the paper-stage app tables (Model Report Card,
  Data Quality, the lineup predictor, availability odds), and the pooled fits and checks without a season dimension.
- **static**, **ledger** (only `ledger_update.py` writes it), **cache** (the running app).

| Live season | Tables | MB now | Rows of the newest season | MB one season adds |
|---|---|---|---|---|
| **daily** | 39 | 2,855 | 2,437,301 | 353 |
| **season-to-date** | 36 | 432 | 535,569 | 59 |
| **season-end** | 47 | 104 | 46,706 | 9 |
| **frozen** | 73 | 683 | 1,190,340 | 229 (never added: frozen) |
| **static** | 10 | 3 | 927 | 0 |
| **ledger** | 11 | 1 | 0 | ~5 by April (the live log) |
| **cache** | 3 | 0 | 65 | 0 |

<!-- live_season:begin -->
| Live season | Tables | MB now | Rows of the newest season | MB one season adds |
| **daily** | 39 | 2,855 | 2,437,302 | 353 |
| **season-to-date** | 36 | 432 | 535,569 | 59 |
| **season-end** | 47 | 104 | 46,706 | 9 |
| **frozen** | 73 | 683 | 1,190,340 | 229 |
| **static** | 10 | 3 | 927 | 0 |
| **ledger** | 11 | 1 | 0 | 0 |
| **cache** | 3 | 0 | 65 | 0 |
| Table | Kind | Producer | Season dim | Paper rows | Live season | Rebuild today | Rows | Last season | MB | MB/season | Note |
| `aging_curve_summary` | derived | `build_aging_curves.py` | — | whole | **season-end** | whole table | 72 |  | 0.0 |  |  |
| `aging_curves` | derived | `build_aging_curves.py` | — | whole | **season-end** | whole table | 1,455 |  | 0.3 |  |  |
| `aging_league_average` | derived | `build_aging_curves.py` | season (int) | capped | **season-end** | whole table | 1,424 | 24 | 0.2 | 0.0 |  |
| `all_nba_backtest_seasons` | derived | `build_all_nba_model.py` | season (int) | capped | **season-end** | incremental | 16 | 1 | 0.1 | 0.0 |  |
| `all_nba_backtest_summary` | derived | `build_all_nba_model.py` | — | whole | **season-end** | incremental | 1 |  | 0.0 |  |  |
| `all_nba_seasons` | source | `fetch_all_nba_teams.py` | season (int) | capped | **season-end** | — | 255 | 15 | 0.1 | 0.0 |  |
| `assist_pairs` | derived | `build_assist_network.py` | season (int) | capped | **daily** | whole table | 41,496 | 7,504 | 5.9 | 1.1 |  |
| `assist_seasons` | derived | `build_assist_network.py` | season (int) | capped | **daily** | whole table | 6 | 1 | 0.0 | 0.0 |  |
| `award_chance_calibration` | derived | `calibrate_award_chances.py` | — | whole | **season-end** | whole table | 4 |  | 0.0 |  |  |
| `award_winners` | derived | `build_award_winners_table.py` | season (int) | capped | **season-end** | whole table | 51 | 3 | 0.0 | 0.0 |  |
| `best_games` | derived | `build_best_games.py` | season (int) | capped | **daily** | whole table | 7,229 | 1,229 | 1.8 | 0.3 |  |
| `best_games_meta` | derived | `build_best_games.py` | — | whole | **frozen** | whole table | 7 |  | 0.0 |  |  |
| `career_similarity` | derived | `precompute_career_similarity.py` | — | whole | **season-end** | whole table | 15,640 |  | 1.0 |  |  |
| `cbb_games` | source | `fetch_cbb_games.py` | season (int) | capped | **season-end** | whole table + per season | 84,460 | 6,318 | 13.5 | 1.0 |  |
| `cluster_archetypes` | derived | `cluster_players.py` | — | whole | **season-end** | whole table | 6 |  | 0.0 |  |  |
| `coaching_decision_meta` | derived | `build_coaching_decisions.py` | — | whole | **frozen** | whole table | 73 |  | 0.0 |  |  |
| `coaching_decision_summary` | derived | `build_coaching_decisions.py` | — | whole | **frozen** | whole table | 4 |  | 0.0 |  |  |
| `coaching_decision_tests` | derived | `build_coaching_decisions.py` | season (int) | capped | **frozen** | whole table | 124 | 3 | 0.1 | 0.0 |  |
| `coaching_decisions` | derived | `build_coaching_decisions.py` | season (int) | capped | **frozen** | whole table | 40,541 | 7,656 | 6.7 | 1.3 | the league-wide tests (BH families) pool every season: rebuild at the season's end |
| `college_draft_pipeline` | derived | `build_college_pipeline.py` | — | whole | **season-end** | whole table | 613 |  | 0.1 |  |  |
| `college_pipeline_meta` | derived | `build_college_pipeline.py` | — | whole | **season-end** | whole table | 17 |  | 0.0 |  |  |
| `college_player_season_stats` | source | `fetch_college_stats.py` | season (int) | capped | **season-end** | incremental | 105,726 | 9,788 | 28.6 | 2.6 |  |
| `college_team_seasons` | source | `load_college_teams.py` | season (int) | capped | **season-end** | whole table | 4,967 | 365 | 1.0 | 0.1 |  |
| `contract_value` | derived | `build_contract_value.py` | season (int) | capped | **static** | whole table | 6,502 | 431 | 1.2 | 0.1 |  |
| `contract_value_seasons` | derived | `build_contract_value.py` | season (int) | capped | **static** | whole table | 16 | 1 | 0.0 | 0.0 |  |
| `dad_validation` | derived | `build_dad_index.py` | season (int) | capped | **season-to-date** | whole table | 9 | 1 | 0.0 | 0.0 |  |
| `daily_update_runs` | source | `daily_update.py` | season (int) | capped | **daily** | per season + incremental | 2 | 2 | 0.0 | 0.0 | LIVE: one row per daily_update.py run (what was checked, written, left pending, failed); outside the manifest digest |
| `data_quality_game_flags` | derived | `build_data_quality.py` | season (int) | capped | **frozen** | whole table | 7,232 | 1,230 | 5.2 | 0.9 |  |
| `data_quality_meta` | derived | `build_data_quality.py` | — | whole | **frozen** | whole table | 19 |  | 0.1 |  |  |
| `data_quality_sensitivity` | derived | `build_data_quality.py` | — | whole | **frozen** | whole table | 1,026 |  | 0.4 |  |  |
| `defender_dad` | derived | `build_dad_index.py` | season (int) | capped | **season-to-date** | whole table | 4,902 | 574 | 3.6 | 0.4 |  |
| `defender_dfg` | source | `fetch_defend_dashboard.py` | season (int) | capped | **season-to-date** | per season | 4,968 | 580 | 0.7 | 0.1 |  |
| `defense_tracking_stats` | source | `build_defense_tracking_stats.py` | season (int) | capped | **static** | per season | 0 |  | 0.0 |  | empty since its endpoint stopped answering |
| `dpoy_seasons` | source | `fetch_dpoy_roy_stats.py` | — | whole | **season-end** | whole table | 15 |  | 0.0 |  |  |
| `draft_combine` | source | `fetch_draft_combine.py` | — | whole | **season-end** | — | 1,133 |  | 0.3 |  |  |
| `draft_history` | source | `load_draft_history_bref.py` | — | whole | **season-end** | whole table | 8,383 |  | 1.4 |  |  |
| `draft_pick_outcomes` | source | `load_draft_history_bref.py` | — | whole | **season-end** | whole table | 8,383 |  | 0.8 |  |  |
| `game_officials` | source | `fetch_referee_officials.py` | game id | capped | **daily** | incremental | 17,848 | 57 | 2.4 | 0.0 | BoxScoreSummaryV2 answers nothing for 2025-04-10 on: 9-2 moves the fetch to BoxScoreSummaryV3 (officials with personId) or ESPN's summary gameInfo.officials (names only) |
| `game_officials_fetch_log` | source | `fetch_referee_officials.py` | game id | capped | **daily** | incremental | 7,166 | 1,201 | 0.7 | 0.1 |  |
| `game_pregame_odds` | derived | `build_season_sim.py` | season (int) | capped | **season-to-date** | whole table | 19,118 | 1,230 | 3.8 | 0.2 | 9-4: 2027 odds from the fit on <= 2025-26 (pregame_model_fit frozen), separate from the locked ledger |
| `game_scores` | source | `fetch_game_scores.py` | season (int) | capped | **daily** | whole table | 40,696 | 2,460 | 5.1 | 0.3 | ESPN scoreboard by US date; 9-2 must match 2026-27 finals to NBA ids without team_game_fatigue (LeagueGameFinder answers again, so keep the join)  |
| `game_team_box` | source | `fetch_referee_officials.py` | season (int) | capped | **daily** | incremental | 14,460 | 2,460 | 2.0 | 0.3 | LeagueGameFinder team rows (PF, FTA, FGA, OREB, TOV) |
| `gravity_tracking_coverage` | derived | `build_gravity_index.py` | season (int) | capped | **season-to-date** | whole table | 13 | 1 | 0.0 | 0.0 |  |
| `gravity_validation` | derived | `build_gravity_index.py` | — | whole | **frozen** | whole table | 1 |  | 0.0 |  |  |
| `greats` | derived | `build_greats.py` | — | whole | **static** | whole table | 83 |  | 0.1 |  |  |
| `greats_meta` | derived | `build_greats.py` | — | whole | **static** | whole table | 1 |  | 0.0 |  |  |
| `hot_streak_persistence` | derived | `build_hot_streak_persistence.py` | — | whole | **frozen** | whole table | 42 |  | 0.1 |  | measured constants (slopes, null centre): frozen; the app's Hot Streak card reads them for 2026-27 too |
| `league_minimum_salary` | source | `load_salaries.py` | season (int) | capped | **static** | whole table | 16 | 1 | 0.0 | 0.0 |  |
| `league_season_averages` | derived | `build_league_averages.py` | season (int) | capped | **season-end** | whole table | 80 | 1 | 0.0 | 0.0 |  |
| `league_shot_zones` | cache | `api/shots_lib.py` | season (text) | capped | **cache** | — | 10 | 5 | 0.0 | 0.0 | GET /shots/league-zones/2027 would insert a 2026-27 row (ENABLE_LIVE_SHOT_FETCH off by default) |
| `league_zone_mix` | derived | `build_league_zone_mix.py` | season (text) | capped | **season-to-date** | whole table | 150 | 5 | 0.1 | 0.0 | one 2026-27 row set from the season's shots |
| `ledger_forecasts` | ledger | `ledger_lock.py` | 2026-27 (lock) | lock | **ledger** | — | 2,460 |  | 0.5 |  |  |
| `ledger_game_log` | ledger | `ledger_update.py` | 2026-27 (lock) | lock | **ledger** | per season + incremental | 0 |  | 0.0 |  | LIVE |
| `ledger_hindcast` | ledger | `ledger_lock.py` | 2026-27 (lock) | lock | **ledger** | — | 480 |  | 0.1 |  |  |
| `ledger_lock` | ledger | `ledger_lock.py` | 2026-27 (lock) | lock | **ledger** | — | 1 |  | 0.0 |  |  |
| `ledger_meta` | ledger | `ledger_lock.py` | 2026-27 (lock) | lock | **ledger** | — | 44 |  | 0.1 |  |  |
| `ledger_results` | ledger | `ledger_update.py` | 2026-27 (lock) | lock | **ledger** | per season + incremental | 1,206 |  | 0.3 |  | LIVE: grows with the season; outside the manifest digest |
| `ledger_rosters` | ledger | `ledger_lock.py` | 2026-27 (lock) | lock | **ledger** | — | 607 |  | 0.1 |  |  |
| `ledger_runs` | ledger | `ledger_update.py` | 2026-27 (lock) | lock | **ledger** | per season + incremental | 4 |  | 0.0 |  | LIVE |
| `ledger_schedule` | ledger | `ledger_lock.py` | 2026-27 (lock) | lock | **ledger** | — | 1,206 |  | 0.2 |  |  |
| `ledger_team_log` | ledger | `ledger_update.py` | 2026-27 (lock) | lock | **ledger** | per season + incremental | 60 |  | 0.0 |  | LIVE |
| `ledger_tests` | ledger | `ledger_update.py` | 2026-27 (lock) | lock | **ledger** | per season + incremental | 0 |  | 0.0 |  | LIVE (from 100 scored games) |
| `leverage_index_grid` | derived | `build_leverage_splits.py` | — | whole | **frozen** | whole table | 2,448 |  | 0.2 |  |  |
| `leverage_validation` | derived | `build_leverage_splits.py` | season (int) | capped | **season-to-date** | whole table | 6 | 1 | 0.0 | 0.0 |  |
| `lineup_predictor_fit` | derived | `build_lineup_predictor.py` | — | whole | **frozen** | whole table | 137 |  | 0.1 |  |  |
| `lineup_predictor_metrics` | derived | `build_lineup_predictor.py` | — | whole | **frozen** | whole table | 72 |  | 0.1 |  |  |
| `lineup_predictor_players` | derived | `build_lineup_predictor.py` | season (int) | capped | **frozen** | whole table | 3,293 | 661 | 0.6 | 0.1 |  |
| `lineup_predictor_teams` | derived | `build_lineup_predictor.py` | season (int) | capped | **frozen** | whole table | 150 | 30 | 0.1 | 0.0 |  |
| `lineup_predictor_tests` | derived | `build_lineup_predictor.py` | — | whole | **frozen** | whole table | 204 |  | 0.1 |  |  |
| `lineup_predictor_units` | derived | `build_lineup_predictor.py` | season (int) | capped | **frozen** | whole table | 81,816 | 18,213 | 28.3 | 6.3 |  |
| `lineup_seasons` | derived | `build_lineup_stints.py` | season (int) | capped | **daily** | whole table | 120,628 | 23,092 | 28.3 | 5.4 |  |
| `lineup_stats` | source | `fetch_spacing_data.py` | season (int) | capped | **season-to-date** | per season | 26,000 | 2,000 | 8.3 | 0.6 | LeagueDashLineups top 2,000 lineups a season: weekly is enough |
| `lineup_stint_games` | derived | `build_lineup_stints.py` | season (int) | capped | **daily** | whole table | 7,232 | 1,230 | 1.5 | 0.3 |  |
| `lineup_stint_seasons` | derived | `build_lineup_stints.py` | season (int) | capped | **daily** | whole table | 6 | 1 | 0.0 | 0.0 |  |
| `lineup_stints` | derived | `build_lineup_stints.py` | season (int) | capped | **daily** | whole table | 294,027 | 53,537 | 106.8 | 19.5 | build_lineup_stints.py --season 2027 deletes and rebuilds that season only, through the same Game.walk() code; api/tests/test_season_rebuild.py proves --season 2026 equals the full build's 2025-26 rows |
| `luck_model_fit` | derived | `build_luck_schedule.py` | — | whole | **frozen** | whole table | 3 |  | 0.0 |  |  |
| `luck_schedule_seasons` | derived | `build_luck_schedule.py` | season (int) | capped | **season-to-date** | whole table | 17 | 1 | 0.0 | 0.0 |  |
| `luck_schedule_validation` | derived | `build_luck_schedule.py` | — | whole | **frozen** | whole table | 19 |  | 0.0 |  |  |
| `model_backtest_seasons` | derived | `backtest_models.py` | season (int) | capped | **season-end** | whole table + incremental | 132 | 9 | 0.1 | 0.0 |  |
| `model_backtest_summary` | derived | `backtest_models.py` | — | whole | **season-end** | whole table + incremental | 9 |  | 0.0 |  |  |
| `mvp_seasons` | legacy | `(before the first commit)` | — | whole | **season-end** | — | 15 |  | 0.0 |  |  |
| `mvp_winners` | legacy | `(before the first commit)` | season (int) | capped | **season-end** | — | 16 | 1 | 0.0 | 0.0 |  |
| `nba75_team` | source | `load_nba75_team.py` | — | whole | **static** | — | 76 |  | 0.0 |  |  |
| `ncaa_bracket_odds` | derived | `build_ncaa_model.py` | season (int) | capped | **season-end** | whole table | 882 | 68 | 0.2 | 0.0 |  |
| `ncaa_model_backtest` | derived | `build_ncaa_model.py` | season (int) | capped | **season-end** | whole table | 60 | 5 | 0.0 | 0.0 |  |
| `ncaa_model_seasons` | derived | `build_ncaa_model.py` | season (int) | capped | **season-end** | whole table | 13 | 1 | 0.0 | 0.0 |  |
| `ncaa_tourney_games` | derived | `build_ncaa_model.py` | season (int) | capped | **season-end** | whole table | 869 | 67 | 0.2 | 0.0 |  |
| `pair_seasons` | derived | `build_lineup_stints.py` | season (int) | capped | **daily** | whole table | 32,220 | 5,564 | 6.4 | 1.1 |  |
| `pair_synergy_validation` | derived | `train_pair_synergy.py` | — | whole | **static** | whole table | 1 |  | 0.0 |  |  |
| `paper_ablation_meta` | paper | `paper_ablations.py` | — | whole | **frozen** | whole table | 90 |  | 0.1 |  |  |
| `paper_ablation_metrics` | paper | `paper_ablations.py` | — | whole | **frozen** | whole table | 987 |  | 0.3 |  |  |
| `paper_ablation_predictions` | paper | `paper_ablations.py` | season (int) | capped | **frozen** | whole table | 439,976 | 76,895 | 56.5 | 9.9 |  |
| `paper_ablation_shot_games` | paper | `paper_ablations.py` | season (int) | capped | **frozen** | whole table | 17,220 | 8,610 | 3.4 | 1.7 |  |
| `paper_ablation_tests` | paper | `paper_ablations.py` | — | whole | **frozen** | whole table | 372 |  | 0.2 |  |  |
| `paper_beliefs` | paper | `paper_beliefs.py` | season (int) | capped | **frozen** | whole table | 89,274 | 12,658 | 19.9 | 2.8 |  |
| `paper_beliefs_meta` | paper | `paper_beliefs.py` | — | whole | **frozen** | whole table | 22 |  | 0.0 |  |  |
| `paper_beliefs_summary` | paper | `paper_beliefs.py` | — | whole | **frozen** | whole table | 137 |  | 0.1 |  |  |
| `paper_data_audit` | paper | `paper_data_audit.py` | season (int) | capped | **frozen** | whole table | 274 | 12 | 0.1 | 0.0 |  |
| `paper_data_audit_classes` | paper | `paper_data_audit.py` | — | whole | **frozen** | whole table | 18 |  | 0.0 |  |  |
| `paper_eval_choices` | paper | `paper_eval.py` | — | whole | **frozen** | whole table | 43 |  | 0.1 |  |  |
| `paper_eval_metrics` | paper | `paper_eval.py` | — | whole | **frozen** | whole table | 1,693 |  | 0.5 |  |  |
| `paper_eval_predictions` | paper | `paper_eval.py` | season (int) | capped | **frozen** | whole table | 2,056,785 | 923,980 | 391.8 | 176.0 |  |
| `paper_eval_tests` | paper | `paper_tests.py` | — | whole | **frozen** | whole table | 958 |  | 0.3 |  |  |
| `paper_xrapm_fits` | paper | `paper_xrapm.py` | season (int) | capped | **frozen** | whole table | 36 | 6 | 0.0 | 0.0 |  |
| `paper_xrapm_lambda_cv` | paper | `paper_xrapm.py` | season (int) | capped | **frozen** | whole table | 1,836 | 306 | 0.1 | 0.0 |  |
| `paper_xrapm_meta` | paper | `paper_xrapm.py` | season (int) | capped | **frozen** | whole table | 152 | 21 | 0.1 | 0.0 |  |
| `paper_xrapm_players` | paper | `paper_xrapm.py` | season (int) | capped | **frozen** | whole table | 20,424 | 3,492 | 3.3 | 0.6 |  |
| `paper_xrapm_stints` | paper | `paper_xrapm.py` | season (int) | capped | **frozen** | whole table | 294,027 | 53,537 | 79.6 | 14.5 |  |
| `pbp_event_clock` | derived | `build_event_clock.py` | event id | capped | **daily** | whole table | 3,401,830 | 596,049 | 286.8 | 50.3 |  |
| `pbp_event_clock_meta` | derived | `build_event_clock.py` | — | whole | **frozen** | whole table | 8 |  | 0.0 |  |  |
| `pbp_events` | source | `fetch_pbp_espn.py` | game id | capped | **daily** | per season | 3,609,582 | 596,049 | 794.0 | 131.1 | 9-2 reads ESPN's game summary per final (site.api.espn.com .../summary?event=, 520 plays with coordinates, participants and wallclock); the sportsdataverse release this fetch uses was last updated 2026-09-09 and has no 2027 file |
| `pbp_games` | source | `fetch_pbp_espn.py` | season (int) | capped | **daily** | per season | 7,652 | 1,230 | 0.9 | 0.1 | one row per ESPN game (source 'espn'); the nba_api twins stay at 420 games |
| `play_finder_events` | derived | `build_play_finder.py` | event id | capped | **daily** | whole table | 3,287,148 | 569,310 | 210.7 | 36.5 |  |
| `play_finder_games` | derived | `build_play_finder.py` | season (int) | capped | **daily** | whole table | 7,229 | 1,229 | 1.2 | 0.2 |  |
| `play_finder_seasons` | derived | `build_play_finder.py` | season (int) | capped | **daily** | whole table | 6 | 1 | 0.0 | 0.0 |  |
| `player_assisted_share` | derived | `build_assist_network.py` | season (int) | capped | **daily** | whole table | 3,920 | 661 | 0.7 | 0.1 |  |
| `player_awards` | derived | `build_player_profile_data.py` | season (int) | capped | **season-end** | whole table | 7,255 | 28 | 0.8 | 0.0 |  |
| `player_bio` | derived | `build_player_profile_data.py` | — | whole | **season-end** | whole table | 4,938 |  | 0.7 |  | no season dimension (a row per player, last_season moves): rookies' rows wait for the season's end (R9-003); the 2026-27 lines identify rookies through player_season_stats' 2027 rows, not this fallback |
| `player_clusters` | derived | `cluster_players.py` | season (int) | capped | **season-end** | whole table | 5,420 | 350 | 1.4 | 0.1 |  |
| `player_first_season` | derived | `build_first_nba_season.py` | — | whole | **season-end** | whole table | 4,417 |  | 0.4 |  | as player_bio: rookies' first season (2027) at the season's end |
| `player_game_lines` | derived | `build_player_game_lines.py` | season (int) | capped | **daily** | whole table | 154,345 | 26,641 | 39.6 | 6.8 | heaves: since 2025-26 the NBA counts a missed end-of-quarter heave as a team attempt; ESPN logs it ('Heave Jump Shot', ~1,100 a season) and the lines charge the shooter (R8-088) |
| `player_game_onfloor` | derived | `build_player_game_onfloor.py` | season (int) | capped | **daily** | whole table | 154,334 | 26,641 | 22.4 | 3.9 |  |
| `player_game_onfloor_meta` | derived | `build_player_game_onfloor.py` | — | whole | **frozen** | whole table | 10 |  | 0.0 |  | checks over every game, no season dimension: the --season builds leave it as it is (R9-008) |
| `player_gravity` | derived | `build_gravity_index.py` | season (int) | capped | **season-to-date** | whole table | 5,695 | 582 | 1.3 | 0.1 |  |
| `player_hustle` | source | `fetch_hustle_stats.py` | season (int) | capped | **season-to-date** | per season | 5,602 | 581 | 1.1 | 0.1 |  |
| `player_id_map` | source | `load_kaggle_historical_seasons.py` | — | whole | **season-end** | per season | 5,105 |  | 0.6 |  | as player_bio: rookies' Basketball-Reference <-> NBA ids at the season's end |
| `player_leverage_splits` | derived | `build_leverage_splits.py` | season (int) | capped | **daily** | whole table | 10,976 | 2,173 | 1.6 | 0.3 |  |
| `player_leverage_summary` | derived | `build_leverage_splits.py` | season (int) | capped | **daily** | whole table | 2,791 | 579 | 0.7 | 0.1 |  |
| `player_matchups` | source | `fetch_matchups.py` | season (int) | capped | **season-to-date** | per season | 571,608 | 70,003 | 135.8 | 16.6 | LeagueSeasonMatchups, ~70,000 rows a season (136 MB for nine): weekly, the biggest nba_api fetch |
| `player_on_off` | derived | `build_player_on_off.py` | season (int) | capped | **daily** | whole table | 3,920 | 661 | 1.2 | 0.2 |  |
| `player_on_off_seasons` | derived | `build_player_on_off.py` | season (int) | capped | **daily** | whole table | 6 | 1 | 0.0 | 0.0 |  |
| `player_playtypes` | source | `fetch_playtypes.py` | season (int) | capped | **season-to-date** | incremental | 43,681 | 3,282 | 7.7 | 0.6 |  |
| `player_projections` | derived | `build_projections.py` | last_season | capped | **season-end** | whole table | 17,012 | 16,983 | 3.6 | 3.6 | the 2027 projections are stored: 9-4 shows projected vs actual, never refits; 2028 at the season's end |
| `player_rapm` | derived | `build_rapm.py` | season (int) | capped | **season-to-date** | whole table | 9,977 | 1,965 | 2.7 | 0.5 | 9-4: one-season + prior for 2027 (wide intervals, said so); rapm_fits / rapm_lambda_cv gain 2027 rows, lambda by the same CV |
| `player_rating_tracker` | derived | `build_rating_tracker.py` | season (int) | capped | **season-to-date** | whole table | 6,808 | 1,164 | 1.9 | 0.3 | 9-4: the filtered rating through today for 2027; hyperparameters from rating_tracker_fit, never re-tuned |
| `player_roles` | derived | `build_player_roles.py` | season (int) | capped | **season-end** | whole table | 5,386 | 350 | 1.4 | 0.1 |  |
| `player_salaries` | source | `load_salaries.py` | season (int) | capped | **static** | whole table | 8,418 | 489 | 1.5 | 0.1 |  |
| `player_season_stats` | source | `load_kaggle_historical_seasons.py` | season (int) | capped | **season-to-date** | per season | 23,408 | 582 | 9.2 | 0.2 | 2026-27 rows refreshed from LeagueDashPlayerStats (235 rows after one preseason game); every earlier season untouched |
| `player_shot_context` | source | `fetch_shot_context.py` | season (int) | capped | **season-to-date** | per season | 77,464 | 6,523 | 10.6 | 0.9 |  |
| `player_shot_hex` | derived | `build_shot_making.py` | season (int) | capped | **season-to-date** | whole table | 9,026 | 350 | 16.2 | 0.6 |  |
| `player_shot_making` | derived | `build_shot_making.py` | season (int) | capped | **season-to-date** | whole table | 14,513 | 582 | 3.0 | 0.1 |  |
| `player_shot_tracking` | source | `fetch_spacing_data.py` | season (int) | capped | **season-to-date** | per season | 6,908 | 582 | 0.9 | 0.1 |  |
| `player_shots` | source | `load_pbp_shots.py` | season (text) | capped | **daily** | per season + incremental | 6,334,308 | 234,673 | 986.9 | 36.6 | ShotChartDetail per team and season type (fetch_season_shots.py's path, 90 calls a season), or per game (game_id_nullable); 2026-27 rows get season '2026-27' |
| `player_shots_cache_status` | cache | `api/shots_lib.py` | updated_at | capped | **cache** | — | 2,842 |  | 0.3 |  |  |
| `player_situational_splits` | derived | `build_situational_splits.py` | season (int) | capped | **daily** | whole table | 123,530 | 20,878 | 30.2 | 5.1 |  |
| `player_team_stints` | derived | `build_player_profile_data.py` | season (int) | capped | **season-end** | whole table | 5,829 | 151 | 0.6 | 0.0 |  |
| `player_wpa_totals` | derived | `compute_wpa.py` | — | whole | **frozen** | whole table | 880 |  | 0.2 |  | career totals with no season dimension: the Clutch WPA page cannot take 2026-27 until a season column is added (R9-002); paper_beliefs recomputes and checks against this table |
| `playtype_cluster_archetypes` | derived | `cluster_playtypes.py` | — | whole | **season-end** | whole table | 6 |  | 0.0 |  |  |
| `playtype_clusters` | derived | `cluster_playtypes.py` | season (int) | capped | **season-end** | whole table | 5,668 | 425 | 1.5 | 0.1 |  |
| `possession_games` | derived | `build_possessions.py` | season (int) | capped | **daily** | whole table | 7,232 | 1,230 | 1.5 | 0.3 |  |
| `possession_meta` | derived | `build_possessions.py` | — | whole | **frozen** | whole table | 5 |  | 0.0 |  | as player_game_onfloor_meta |
| `possession_seasons` | derived | `build_possessions.py` | season (int) | capped | **daily** | whole table | 2,033 | 339 | 0.5 | 0.1 |  |
| `possessions` | derived | `build_possessions.py` | season (int) | capped | **daily** | whole table | 1,442,587 | 247,055 | 295.0 | 50.5 |  |
| `postseason_games` | source | `fetch_postseason_games.py` | season (int) | capped | **daily** | whole table | 1,458 | 91 | 0.3 | 0.0 |  |
| `prediction_ledger` | cache | `snapshot_predictions.py` | season (int) | capped | **cache** | — | 60 | 60 | 0.1 | 0.1 | snapshot_predictions.py may log 2026-27 award predictions (season 2027) |
| `pregame_availability_fit` | derived | `build_pregame_availability.py` | season (int) | capped | **frozen** | whole table | 129 | 11 | 0.1 | 0.0 |  |
| `pregame_availability_odds` | derived | `build_pregame_availability.py` | season (int) | capped | **frozen** | whole table | 7,230 | 1,230 | 2.6 | 0.4 |  |
| `pregame_availability_players` | derived | `build_pregame_availability.py` | game id | capped | **frozen** | whole table | 187,077 | 32,933 | 36.3 | 6.4 |  |
| `pregame_availability_tests` | derived | `build_pregame_availability.py` | — | whole | **frozen** | whole table | 48 |  | 0.1 |  |  |
| `pregame_calibration` | derived | `build_season_sim.py` | — | whole | **frozen** | whole table | 40 |  | 0.0 |  |  |
| `pregame_model_fit` | derived | `build_season_sim.py` | — | whole | **frozen** | whole table | 4 |  | 0.0 |  |  |
| `pregame_model_seasons` | derived | `build_season_sim.py` | season (int) | capped | **season-to-date** | whole table | 64 | 4 | 0.0 | 0.0 |  |
| `projection_backtest` | derived | `build_projections.py` | season (int) | capped | **season-end** | whole table | 913 | 34 | 0.2 | 0.0 |  |
| `projection_backtest_rows` | derived | `build_projections.py` | season (int) | capped | **season-end** | whole table | 259,919 | 10,996 | 37.1 | 1.6 | a 2027 row appears only when the season is complete |
| `projection_ranges` | derived | `build_projections.py` | — | whole | **frozen** | whole table | 295 |  | 0.1 |  |  |
| `projection_stats` | derived | `build_projections.py` | — | whole | **frozen** | whole table | 34 |  | 0.0 |  |  |
| `rapm_fits` | derived | `build_rapm.py` | season (int) | capped | **season-to-date** | whole table | 16 | 3 | 0.0 | 0.0 |  |
| `rapm_lambda_cv` | derived | `build_rapm.py` | season (int) | capped | **season-to-date** | whole table | 680 | 119 | 0.1 | 0.0 |  |
| `rapm_validation` | derived | `build_rapm.py` | season (int) | capped | **season-to-date** | whole table | 92 | 18 | 0.1 | 0.0 |  |
| `rating_tracker_curve` | derived | `build_rating_tracker.py` | — | whole | **frozen** | whole table | 15 |  | 0.0 |  |  |
| `rating_tracker_fit` | derived | `build_rating_tracker.py` | — | whole | **frozen** | whole table | 1 |  | 0.0 |  |  |
| `rating_tracker_validation` | derived | `build_rating_tracker.py` | season (int) | capped | **season-to-date** | whole table | 59 | 12 | 0.0 | 0.0 |  |
| `referee_crew_tendencies` | derived | `build_referee_tendencies.py` | — | whole | **frozen** | — | 5,373 |  | 1.6 |  | as referee_tendencies |
| `referee_tendencies` | derived | `build_referee_tendencies.py` | — | whole | **frozen** | — | 97 |  | 0.0 |  | pooled over 2020-21 to 2025-26 with no season dimension: a 2026-27 version needs a season or through column (R9-002) |
| `report_card_choices` | derived | `build_report_card.py` | season (int) | capped | **frozen** | whole table + incremental | 210 | 32 | 0.1 | 0.0 |  |
| `report_card_game_sums` | derived | `build_report_card.py` | season (int) | capped | **frozen** | whole table + incremental | 95,811 | 23,340 | 14.1 | 3.4 |  |
| `report_card_meta` | derived | `build_report_card.py` | — | whole | **frozen** | whole table + incremental | 8 |  | 0.0 |  |  |
| `report_card_pooled` | derived | `build_report_card.py` | — | whole | **frozen** | whole table + incremental | 260 |  | 0.1 |  |  |
| `report_card_tests` | derived | `build_report_card.py` | season (int) | capped | **frozen** | whole table + incremental | 2,072 | 344 | 0.6 | 0.1 |  |
| `report_card_units` | derived | `build_report_card.py` | season (int) | capped | **frozen** | whole table + incremental | 162,001 | 25,140 | 27.4 | 4.2 |  |
| `rim_deterrence` | derived | `build_rim_deterrence.py` | season (int) | capped | **daily** | whole table | 3,919 | 661 | 1.8 | 0.3 |  |
| `rim_deterrence_seasons` | derived | `build_rim_deterrence.py` | season (int) | capped | **daily** | whole table | 6 | 1 | 0.0 | 0.0 |  |
| `role_archetypes` | derived | `build_player_roles.py` | — | whole | **season-end** | whole table | 10 |  | 0.1 |  |  |
| `rotation_closing_games` | derived | `build_rotations.py` | season (int) | capped | **daily** | whole table | 7,232 | 1,230 | 1.3 | 0.2 |  |
| `rotation_closing_stints` | derived | `build_rotations.py` | season (int) | capped | **daily** | whole table | 37,274 | 6,617 | 7.9 | 1.4 |  |
| `roy_seasons` | source | `fetch_dpoy_roy_stats.py` | — | whole | **season-end** | whole table | 15 |  | 0.0 |  |  |
| `scouting_splits` | derived | `build_scouting_reports.py` | season (int) | capped | **season-to-date** | whole table | 34,276 | 2,488 | 7.0 | 0.5 |  |
| `scouting_validation` | derived | `build_scouting_reports.py` | — | whole | **frozen** | whole table | 7 |  | 0.0 |  |  |
| `season_postseason` | derived | `build_season_sim.py` | season (int) | capped | **season-end** | whole table | 510 | 30 | 0.1 | 0.0 |  |
| `season_sim_backtest` | derived | `build_season_sim.py` | season (int) | capped | **season-to-date** | whole table | 3,360 | 210 | 0.7 | 0.0 |  |
| `season_sim_backtest_summary` | derived | `build_season_sim.py` | — | whole | **frozen** | whole table | 48 |  | 0.0 |  |  |
| `season_sim_calibration` | derived | `build_season_sim.py` | — | whole | **frozen** | whole table | 112 |  | 0.1 |  |  |
| `season_sim_params` | derived | `build_season_sim.py` | — | whole | **frozen** | whole table | 8 |  | 0.0 |  |  |
| `season_sim_seasons` | derived | `build_season_sim.py` | season (int) | capped | **season-to-date** | whole table | 16 | 1 | 0.0 | 0.0 | 9-4: the Season Simulator's 2027 row; season_sim_params frozen |
| `season_similarity` | derived | `precompute_league_similarity.py` | — | whole | **season-end** | whole table | 72,790 |  | 5.8 |  |  |
| `shap_explanations` | derived | `build_shap_explanations.py` | season (int) | capped | **season-end** | whole table | 660 | 660 | 0.1 | 0.1 |  |
| `shot_hex_league` | derived | `build_shot_making.py` | season (int) | capped | **season-to-date** | whole table | 18,774 | 564 | 1.3 | 0.0 |  |
| `shot_hex_meta` | derived | `build_shot_making.py` | — | whole | **frozen** | whole table | 7 |  | 0.0 |  |  |
| `shot_making_league` | derived | `build_shot_making.py` | season (int) | capped | **season-to-date** | whole table | 30 | 1 | 0.0 | 0.0 |  |
| `shot_making_validation` | derived | `build_shot_making.py` | — | whole | **frozen** | whole table | 5 |  | 0.0 |  |  |
| `shot_value_added` | derived | `build_shot_value.py` | season (int) | capped | **season-to-date** | whole table | 3,407 | 582 | 1.0 | 0.2 | as shot_value_shots |
| `shot_value_fit` | derived | `build_shot_value.py` | — | whole | **frozen** | whole table | 5 |  | 0.0 |  |  |
| `shot_value_shots` | derived | `build_shot_value.py` | season (int) | capped | **season-to-date** | whole table | 1,282,300 | 219,160 | 116.7 | 19.9 | the filter is built to update game by game: 2027 rows from the frozen fit |
| `shot_value_states` | derived | `build_shot_value.py` | season (int) | capped | **season-to-date** | whole table | 13,628 | 2,328 | 1.6 | 0.3 | as shot_value_shots |
| `shot_value_validation` | derived | `build_shot_value.py` | — | whole | **frozen** | whole table | 260 |  | 0.1 |  |  |
| `shot_xfg` | derived | `build_shot_making.py` | season (int) | capped | **season-to-date** | whole table | 1,282,300 | 219,160 | 94.7 | 16.2 | 9-4 may score 2026-27 shots with the five fold models refitted on <= 2025-26 (the fit is deterministic: ORDER BY id); the stored 2020-21 to 2025-26 P(make) must not move |
| `situational_split_league` | derived | `build_situational_splits.py` | season (int) | capped | **daily** | whole table | 308 | 44 | 0.1 | 0.0 | the season 0 (pooled) row is the paper's: a --season run writes only its season's row |
| `stat_stability` | derived | `build_stat_stability.py` | — | whole | **frozen** | whole table | 35 |  | 0.0 |  | the M constants 9-4 uses for early-season reliability warnings: frozen |
| `stat_stability_curve` | derived | `build_stat_stability.py` | — | whole | **frozen** | whole table | 285 |  | 0.1 |  |  |
| `stat_year_to_year` | derived | `build_stat_stability.py` | — | whole | **frozen** | whole table | 30 |  | 0.0 |  |  |
| `team_game_fatigue` | source | `build_schedule_fatigue.py` | season (int) | capped | **daily** | per season | 40,696 | 2,460 | 5.5 | 0.3 | LeagueGameFinder, one call a season (2.4 s cold); plus_minus is not the margin (README) |
| `team_game_totals` | derived | `build_team_game_totals.py` | season (int) | capped | **daily** | whole table | 14,464 | 2,460 | 2.8 | 0.5 |  |
| `team_luck_schedule` | derived | `build_luck_schedule.py` | season (int) | capped | **season-to-date** | whole table | 510 | 30 | 0.2 | 0.0 | 9-4: 2027 rows from the frozen luck_model_fit |
| `team_seasons` | derived | `build_team_seasons.py` | season (int) | capped | **season-end** | whole table | 1,804 | 31 | 0.7 | 0.0 |  |
| `team_zone_mix` | derived | `build_team_zone_mix.py` | season (text) | capped | **season-to-date** | whole table | 8,920 | 300 | 1.2 | 0.0 | as league_zone_mix |
| `win_model_backtest` | derived | `train_win_model.py` | season (int) | capped | **season-end** | whole table | 32 | 2 | 0.0 | 0.0 |  |
| `wpa_clutch_league` | derived | `compute_wpa.py` | — | whole | **frozen** | whole table | 1 |  | 0.0 |  | as player_wpa_totals |
| `wpa_model_validation` | derived | `train_wpa_model.py` | — | whole | **frozen** | whole table | 4 |  | 0.0 |  |  |
| `zone_classifier_check` | derived | `build_scouting_reports.py` | season (text) | capped | **static** | whole table | 10 | 5 | 0.0 | 0.0 |  |
<!-- live_season:end -->

## 3. The paper freeze guard

**The constant.** `api/paper_freeze.py`: `MAX_PAPER_SEASON = 2026`, `MAX_PAPER_SEASON_LABEL = '2025-26'`,
`paper_predicate(table, columns)` (the rule per table), `paper_rows(table)` (a WHERE predicate), `F(table, alias)`
(a FROM-item: `(SELECT * FROM "t" WHERE <predicate>) AS alias`; Postgres pulls it up, so plans don't change) and
`paper_game_ids(conn)` (the ESPN game ids of the paper's seasons, for `pbp_lineups.load_espn(conn, game_ids=...)`).
A NULL or 0 season is a pooled row and stays the paper's.

**Where it is applied.** Every `FROM`/`JOIN` of a season-bearing table in `paper_xrapm.py`, `paper_eval.py`,
`paper_tests.py`, `build_pregame_availability.py`, `build_lineup_predictor.py`, `build_report_card.py`,
`paper_beliefs.py`, `paper_ablations.py` and `paper_figures.py` goes through `F()` (87 sites, rewritten
mechanically and checked: every loader returns byte-identical frames before and after on today's data). The shared
loaders those scripts call take the bound as an argument, their defaults unchanged for the app's builds:
`build_rapm.load_rows(conn, through=)` / `load_bpm`, `compute_wpa.load_events(conn, through=)` (and `events_sql()`),
`build_hot_streak_persistence.load(conn, through=)`, `situational_splits.lines_sql(through)` (`LINES_SQL` is
`lines_sql()`), `season_sim_lib.GAMES_REST_SQL`'s `{where}` slot, `pbp_lineups.load_espn(game_ids=)`.
The last four, `paper_numbers.py`, `paper_data_audit.py`, `build_data_quality.py` and `api/data_quality_lib.py`
(174 sites), were capped right after the round 8.5 step C commit (R9-001), together with the shared loaders the audit
calls (`pbp_lineups.load_season_names` / `load_espn` / `match_coordinates` / `chart_matches`,
`repair_espn_player_ids.find` / `unique_names`, `build_event_clock.chart_clock` / `clock_check`, all with an optional
`through=` and unchanged defaults). `\pnLgAsOf` (the ledger's "as of" date, which every `ledger_update.py` run used to
move) is the lock date now. Checked: `paper_numbers.build()`, every audit check and every Data Quality live check
recorded with a cursor, 0 unbounded reads in 170 statements; every macro value unchanged.

**The manifest.** `paper_manifest.py` records each table's `paper_rows` predicate, counts and hashes the paper's
rows only, lists the five live ledger tables (`LIVE`) without digesting them, and `stale_reasons()` compares
capped counts. On 2026-10-06 the capped and plain hashes agree for every table (no table holds 2026-27 rows except the
lock and `player_projections`' target season), so the digest changes once, by definition only (the live tables leave
it), when this lands: `e8f6204b932c41bb` after the round 8.5 step C rebuild, the same from the step C chat's run and
from this step's rerun after the cap (1,362 macros, every claim passes, 14 file hashes).

**The tests** (`api/tests/test_paper_frozen.py`, 25 tests, ~1.5 min):

- the constant equals the protocol's `paper_eval.TEST`; the predicate rules as documented;
- every table with a season dimension has a predicate, the lock has none, and for each of the 131 capped tables one
  **fake 2026-27 row** built from its newest row (season 2027 / '2026-27', dates a year later, game `espn_402700001`
  / `0022600001`, ids 999999999; joined tables shadowed by CTEs carrying the same fake game) is excluded by its
  predicate and nothing else is: inline `UNION ALL`, no view, no copy, nothing created in the database;
- no paper table holds a row past the freeze;
- the manifest digest skips the live tables and its hashes apply the predicates;
- **static:** no SQL literal in a paper-stage file names a capped table after `FROM`/`JOIN` (docstrings and writes
  excepted);
- **dynamic:** every paper-stage loader, run for real with a recording cursor, bounds every capped table it reads
  (`season <= 2026`-style, incl. `season + 1 <=`, text seasons, the game-id rule), which also checks the shared
  loaders' `through=`.

**Rules for the next steps**

- **9-2** writes only 2027 rows into daily source tables (`season = 2027`, '2026-27', game ids of the season); it
  never updates or deletes a row of an earlier season. Rookies get their ids from `player_season_stats`' 2027 rows;
  `player_bio` / `player_id_map` / `player_first_season` have no season dimension and wait for the season's end
  (R9-003).
- **9-3** rebuilds season 2027 only, in `rebuild_all.sh` order, through the same functions as the full build; a
  table without a season dimension (the `*_meta` check tables, `player_wpa_totals`, the referee tables) is left as it
  is or first gains a season column (R9-002, R9-008). `situational_split_league`'s pooled row (season 0) is the paper's.
- **9-4** adds 2027 rows to the per-season model tables with every hyperparameter and pooled fit read from the stored
  fit rows (`rating_tracker_fit`, `shot_value_fit`, `pregame_model_fit`, `season_sim_params`, `luck_model_fit`,
  `rapm_fits`' rule) and fitted on <= 2025-26; the fit rows themselves do not change (R9-009).
- Any step: a table the paper reads may change only in rows the predicate excludes; `paper-inputs` must stay
  byte-identical (the manifest, `numbers.tex`, figures) except the ledger's forward-test sentence in step 9-6.

## 4. The size of one season

From the table above (rows of the newest season, at each table's current bytes per row):

| | MB |
|---|---|
| daily tables (38) | 353 |
| season-to-date tables (36) | 59 |
| **one season of live data** | **~410** |
| the same with indexes as packed today (the copy script builds keys row by row: `--reindex`) | ~500-550 |

The six biggest: `pbp_events` 131 MB, `possessions` 51, `pbp_event_clock` 50, `player_shots` 37, `play_finder_events`
37, `shot_value_shots` 20. Disk is no concern (512 GB). **Layerbase has 1,513 MB free of its 5 GB tier** after round
8 step 10: one live season fits (~0.5 GB with indexes), with ~1 GB to spare; a second season would not without
dropping something. Sync weekly at most (each sync needs the owner's OK), only the changed daily / season-to-date
tables, `--reindex` after. The frozen tables (683 MB) and the paper's six `LOCAL_ONLY` tables never grow.

## 5. Step 2: the daily fetch (2026-10-06)

`scripts/daily_update.py` is the live season's one command (its docstring is the reference; `docs/qa/ROUND9_ISSUES.md`
R9-010 to R9-015 record what building it found). Once a day, after the night's games are final (about 14:00 IST, 04:30
US Eastern, the ledger's hour), it:

1. reads ESPN's scoreboard for every US date from the last run's **through date** (the last date whose games were all
   final, postponed or cancelled) to today, so a run that is missed or one that is repeated costs nothing;
2. rebuilds the season's `team_game_fatigue` rows and upserts `game_team_box` from one LeagueGameFinder call per season
   type; upserts `game_scores` (finals matched to NBA ids by date and the pair of teams, as `fetch_game_scores.py`
   does) and `postseason_games`;
3. replaces the season's `player_season_stats` rows from LeagueDashPlayerStats (Base + Advanced, plus age and fouls) and
   recomputes the three impact scores for that season only (the same per-season z the whole-table scripts give);
4. stores every regular-season final's play-by-play from **ESPN's game summary** (`scripts/espn_summary.py`), mapped to
   the hosted release's rows exactly: on 2,069 stored events of four 2025-26 games (one with two overtimes) every column
   is equal, the clock included once it is computed as the release did (Float32, four decimals; R9-004). Names are
   matched by `fetch_pbp_espn.PlayerMatcher` against the season rows loaded in step 3, so **rookies have ids** (R9-003:
   in the preseason run every one of 4,256 events matched; with the season rows missing, 682 of them had none), then
   `repair_espn_player_ids.find()` runs on the new games and every name is looked up in the lines parser's index;
5. fetches officials with ids from BoxScoreSummaryV3 (R9-005; a name V3 leaves empty comes from ESPN's summary, R9-014);
6. fetches the shot chart per team for a season type when a final has no shots stored or fewer than the box score's FGA,
   and merges it with `fetch_season_shots.py`'s rule (unchanged shots keep their ids; a merge that would delete more than
   max(50, 10%) of a type's stored shots stops; R9-006, R9-013);
7. writes one row to **`daily_update_runs`** (listed in `paper_manifest.LIVE`: outside the digest) and prints one line.

Each step is its own transaction; a failed step is named in the summary and the run exits 1, the others still run (the
play-by-play step waits for the next run when the season stats failed). `--dry-run` runs everything in one rolled-back
transaction; `--offline` rebuilds from the caches in `live_data/<season>/` (gitignored). Rows of earlier seasons are
never written (every DELETE and UPDATE is bound to the season or to the new games' ids), the ledger and the paper's
tables never. **Measured on the 2026-27 preseason** (`api/tests/test_daily_update.py`, which runs the command on copies
of the tables in a `zz_` schema against the real feeds): eight finals through 2026-10-05, every feed complete the next
morning, one game's chart short of the box score for good (R9-013); a live run 92 s (the chart 60 s of it), the same
day from the caches 1 s, a second run writes nothing. The first real run (2026-10-06, before opening night) found
nothing to check and only created the run log. The paper's inputs: the manifest digest, every claim, every figure and
every macro but the two table counts (`\pnTableCount` 199 → 200, `\pnManTables` 218 → 219: the run log) are unchanged (R9-010).

## 6. Step 3: the daily rebuild of the current season (2026-10-06)

Every season-level build of the daily chain takes **`--season N`** (`scripts/season_mode.py`): it deletes only that
season's rows of each table it writes and inserts the season's new rows through the same functions as the full build
(no DDL, no new index, no second implementation), needs the full build's tables (it never creates them), and leaves
alone every table without a season dimension (the `*_meta` check tables, `best_games_meta`, `leverage_index_grid`:
R9-008) and every other season's rows. The fourteen, in `rebuild_all.sh` order (`daily_update.REBUILD_STEPS`):
`build_event_clock.py`, `build_player_game_lines.py`, `build_team_game_totals.py`, `build_lineup_stints.py`,
`build_player_game_onfloor.py`, `build_player_on_off.py`, `build_possessions.py`, `build_situational_splits.py`,
`build_rotations.py`, `build_rim_deterrence.py`, `build_assist_network.py`, `build_play_finder.py`,
`build_best_games.py`, `build_leverage_splits.py`. The shared loaders take the season (`pbp_lineups.load_espn`,
`match_coordinates`, `chart_matches`, `miss_three_calls`: `season=`, defaults unchanged); the shot chart matched
within one season gives that season's attempts the same matches as the whole chart (the match is keyed by the NBA
game id).

Three things the full builds do across seasons, and what the season mode does about them:

- **One seeded random stream** (R9-016): on/off, rim deterrence and the situational splits draw every player-season's
  bootstrap (and the splits' chance shuffle) from one stream in sorted order, so a row's interval depends on the rows
  before it. In `--season` mode these three compute every season exactly as the full build does and write only the
  season's rows: the live season gets the intervals a full build would give it today, the stored seasons keep theirs.
- **Pooled constants** (R9-017): the deflator's Leverage Index is normalised by league-wide constants pooled over every
  event. `--season N` computes them from seasons <= `paper_freeze.MAX_PAPER_SEASON`, stops unless they reproduce the
  stored `leverage_index_grid` to 1e-9, and applies them to the season's events (R9-009's rule for pooled fits).
- **Sequential ids** (R9-018): `lineup_stints.stint_id` and the Play Finder's `game_no` continue from the season
  before, which is what the full build gives a season whose games all come after the earlier seasons'.

**The proof** (`api/tests/test_season_rebuild.py`): the fourteen builds run with `--season 2026` in `rebuild_all.sh`
order into copies of their tables in the schema `zz_season_rebuild` (search_path through PGOPTIONS; the public tables
are never written), and the 2025-26 rows of every table they write have **the same content hash as the stored full
build's** (paper_manifest's summed-halves hash over the season's rows, taken from the public tables before any build
ran); every other season's rows and the five untouched tables hash the same before and after; the public tables are
untouched. The chain's time is the daily rebuild's worst case (a whole 1,230-game season; a game day adds 5-15 games
to a season that is rebuilt whole):

| build | `--season 2026` | rows of 2025-26 |
|---|---|---|
| `build_event_clock.py` | 13 s | pbp_event_clock 596,049 rows |
| `build_player_game_lines.py` | 12 s | player_game_lines 26,641 rows |
| `build_team_game_totals.py` | 1 s | team_game_totals 2,460 rows |
| `build_lineup_stints.py` | 16 s | lineup_stints 53,537 rows, lineup_stint_games 1,230 rows, lineup_stint_seasons 1 rows, lineup_seasons 23,092 rows, pair_seasons 5,564 rows |
| `build_player_game_onfloor.py` | 6 s | player_game_onfloor 26,641 rows |
| `build_player_on_off.py` | 6 s | player_on_off 661 rows, player_on_off_seasons 1 rows |
| `build_possessions.py` | 27 s | possessions 247,055 rows, possession_games 1,230 rows, possession_seasons 339 rows |
| `build_situational_splits.py` | 61 s | player_situational_splits 20,878 rows, situational_split_league 44 rows |
| `build_rotations.py` | 13 s | rotation_closing_games 1,230 rows, rotation_closing_stints 6,617 rows |
| `build_rim_deterrence.py` | 48 s | rim_deterrence 661 rows, rim_deterrence_seasons 1 rows |
| `build_assist_network.py` | 5 s | assist_pairs 7,504 rows, player_assisted_share 661 rows, assist_seasons 1 rows |
| `build_play_finder.py` | 16 s | play_finder_events 569,310 rows, play_finder_games 1,229 rows, play_finder_seasons 1 rows |
| `build_best_games.py` | 2 s | best_games 1,229 rows |
| `build_leverage_splits.py` | 19 s | player_leverage_splits 2,173 rows, player_leverage_summary 579 rows, leverage_validation 1 rows |
| **all fourteen** | **246 s (4.1 min; the schema copies another 27 s, the test 5 min)** | |

On the eight-game 2026-27 preseason (a `--season 2027` trial by hand in a scratch schema holding full copies of the
tables the builds read whole) the same chain took 160 s: the three every-season builds (splits 61 s, rim 56 s, on/off
6 s) and the deflator (20 s, every event loaded for its constants) are a game day's fixed cost, the per-game builds
1-8 s each. Five prints and summaries assumed a season with many games and were made to say n/a or 0 (the lines'
NBA.com check, on/off's league mean, rim's rule share, the Play Finder's distance-source columns, the deflator's PPG
check). The situational splits write no 2026-27 row until players have ten games on each side of a split.

**In the daily update:** `daily_update.py` runs the fourteen after its fetch steps when the run stored new play-by-play
or changed the shot chart (`--rebuild` forces it, `--no-rebuild` skips it, `--rebuild-only` runs nothing else; a dry
run never rebuilds; only the regular season is rebuilt, never the tests' preseason runs), each as
`python3 <script> --season N` in its own process (memory freed between builds; output in
`live_data/<season>/rebuild/<date>_<script>.log`), stopping at the first failure since every build reads the one
before it; the run row keeps every build's status and seconds (`rebuild_seconds`, `rebuild_steps`; R9-020) and the
summary line says `rebuild 14 of 14 builds in N s` or why it was skipped. The update restarts nothing: it prints
`restart impact_api` (the API caches these tables per process). Decided on the way: the lines' heave treatment stays
as it is for both seasons (R9-007).

**Not in the daily chain** (and why): `build_stat_stability.py`, `build_hot_streak_persistence.py`, `compute_wpa.py`
and the referee tables are frozen pooled measures (R9-002); `build_projections.py`, `build_rapm.py`,
`build_shot_value.py`, `build_rating_tracker.py`, `build_team_zone_mix.py` and `build_scouting_reports.py` are step
9-4's season-to-date models; the paper stage never runs during the season.

## 7. Step 4: the season-to-date models (2026-10-07)

The six models the plan names, and two aggregates beside them, now take **`--season N`** too (`scripts/season_mode.py`),
under one rule, R9-009's: **every pooled fit stays fitted on seasons up to the paper's test season and is only applied to
the live season**; nothing is tuned on 2026-27. Where the fit is cheap to recompute, the build recomputes it from the
paper's seasons and stops unless it reproduces the stored row (R9-017's pattern); where it isn't, the build reads the
stored row. Per model (the decision is also on its Methodology card):

| Model | `--season N` does | The pooled fit, where it comes from | What the live season gets |
|---|---|---|---|
| RAPM (`build_rapm.py`) | the three versions refit on the season's stints, only its rows replaced | lambda and the prior scale = 2025-26's stored choices (`rapm_fits`; `lambda_rule` 'frozen:2025-26'); the season's own cross-validation curve is still computed and shown | wide early-season intervals (the bootstrap stream is replayed past the earlier seasons' draws, so they are the intervals a full build would give it); the BPM-prior version shrinks toward zero until Basketball-Reference's BPM exists for the season; the page says "season to date, through <date>" |
| Rating Tracker (`build_rating_tracker.py`) | the filter run through the season at the stored hyperparameters, only its rows replaced | the five hyperparameters and sigma^2 from `rating_tracker_fit` (chosen on 2020-21 to 2023-24, never re-tuned) | the "as of then" rating through today; "with hindsight" equals it until a next season exists; the earlier seasons' with-hindsight rows stay the paper's; the season's summary goes to `rapm_fits` as version 'tracker' (R9-022) |
| Projections (`build_projections.py`, not rerun) | nothing: the 2026-27 projections were stored before the season | the M values and the aging curves | the Projections page shows each player's line so far next to his projection (gap, inside the 80% range?) with the date it runs through, and a summary; BPM has no in-season actual |
| Season Simulator (`build_season_sim.py`) | the season's held-out pre-game odds, its per-form scores so far and its season row; the backtest and playoff facts at the season's end | the prior constants (recomputed from <= 2025-26, checked against `season_sim_params` to 1e-9) and the four forms' leave-one-season-out coefficients (for a live season the stored all-season fit, checked to 1e-9) | the simulator from any morning up to today, with the remaining games from ESPN's schedule as the Forecast Ledger last read it (`api/season_sim_live.py`; back-to-backs from the dates, equal to the locked schedule's); the page says it is the app's model, not the locked ledger |
| Award chances (`mvp_api.py`, no build) | nothing: the calibrated models read the season-to-date per-game line | the models and their calibration | the DPOY / All-NBA games floor scaled to the games played so far, 2026-27 rookies = no earlier season on file, every response labelled "season to date" (R9-025) |
| Stat Stability (no build) | nothing | the M constants (2020-21 to 2025-26) | reliability n / (n + M) with n the sample so far (the Leaderboard's rule); the warnings early in a season are the point |
| Luck & Schedule (`build_luck_schedule.py`) | the season's team rows and season row | the three expected-win curves (recomputed from <= 2025-26, checked against `luck_model_fit`) | luck and SRS so far; `scheduled` counts ESPN's schedule, `complete` false until the last game |
| Shot Value (`build_shot_value.py`) | the season priced: five fold models fitted on 1996-97 to N-1 (the full build's rule), the skill filter through N | the four classes' hyperparameters and shrinkage from `shot_value_fit`; the paper's player folds, newcomers in turn | SVA, states and the season's validation rows (no `p_xfg` row; R9-023) |
| League / team zone mix, scouting splits | the season's rows | the scouting fallback SD from the paper's seasons | the shot-mix lines; scouting splits once players reach 1,500 minutes |

Not in-season: shot-making (expected FG%, the quality map; R9-024), the shot-aware RAPM (the paper's), and the pooled
pages of R9-002 (Clutch WPA, referees, hot streaks): "through 2025-26" for the round.

**The proof** (`api/tests/test_season_models.py`): the eight builds run with `--season 2026` into copies of the tables they
write in the schema `zz_season_models` and give the stored full build's 2025-26 rows back, byte for byte for seven of
them and to 1e-9 for Luck & Schedule (its least-squares ratings differ run to run in the 14th digit on this machine:
R9-021), with every other season's rows and the twelve pooled fit tables untouched and the public tables untouched. The
chain's time on a whole 1,230-game season (the worst case; measured 2026-10-07 in the scratch runs, OMP_NUM_THREADS 4):

| build | seconds | note |
|---|---|---|
| `build_luck_schedule.py` | 1 | |
| `build_league_zone_mix.py` | 1 | |
| `build_rapm.py` | 50 | single 9 s, the three-season window 26 s, the BPM prior 12 s (300 bootstraps each) |
| `build_shot_value.py` | 116 | the five fold fits ~105 s (4.5M shots each); loading the chart 5 s |
| `build_rating_tracker.py` | 6 | the filter over every season |
| `build_team_zone_mix.py` | 1 | |
| `build_scouting_reports.py` | 1 | |
| `build_season_sim.py` | 10 | the features of every season, the backtest of the season |
| **the eight** | **186** | after the fourteen daily builds' 246 s: a game day's whole chain about 7 minutes |

**`daily_update.py` runs them** after a rebuild that ran clean (`--no-models` skips, `--models` forces, `--models-only`
runs nothing else), each in its own process with its log under `live_data/<season>/rebuild/`, stopping at the first
failure; the run row's `rebuild_steps` lists them with phase 'models'; the update prints "restart impact_api (and mvp_api
after the models)". A `--season 2027` trial on the cached 2026-27 preseason (eight games in a scratch schema holding full copies of every
table the fetch, the fourteen and the eight write, the step-3 pattern) ran the fourteen in 186 s and the eight in 44 s
(RAPM 21 s: 235 players over 7 tracked games, none qualified, lambda frozen; the tracker 6 s; the simulator 9 s: 8 odds
rows and a season row saying "8 of 1,200 games played, through 2026-10-05"; luck 16 team rows, `complete` false; the
shot-based builds and the scouting splits had nothing to write, preseason games not being regular-season ones). Found
and fixed on the way: a season without a published BPM (every live season until Basketball-Reference's arrive) made
RAPM's held-out BPM baseline singular, so that row and the on/off row are skipped when nobody has one, and the page's
note says the BPM-prior version shrinks toward zero until then.

The routes: `/rapm` carries `fit.live` (games, through date, the frozen rule, whether BPM exists); `/season-sim/options`
and `/season-sim` carry `live`, `today_date`, `played_through`, `schedule_source`, `live_note`; `/projections` and
`/projections/player/{id}` carry `actual`, `gap`, `in_range` per row and `actual_summary` / `actual_through`; the award
routes carry `in_season`, `through`, `games_played_max`, `in_season_note`. The pages show each note.

## 8. Step 5: the app in "current season" mode (2026-10-07)

**The rule** (`api/current_season.py`, `GET /meta/season`; R9-029): the app opens on the newest season whose regular
season is complete until a newer season has one regular-season final stored in `game_scores`, then on that season. So
it opens on 2025-26 today and on 2026-27 from the morning after opening night (the first `daily_update.py` run that stores
a final). The frontend reads the answer once before its first render (`frontend/src/utils/season.js`: `currentSeason()`,
`latestCompleteSeason()`, `isLiveSeason()`, `defaultSeasonFor(minGames)`, `seasonRange()`, `shortDate()`); no component
holds a season literal any more (a test greps for one). Change `DEFAULT_AFTER_GAMES` to make the switch wait.

**What a page shows for the live season:** `common/LiveSeasonNote.jsx`: "2026-27 so far, through Oct 22: 34 of 1,200
games played (2-3 a team). Early season: how much of a typical rotation player's number is signal so far: FG% 20%,
3P% 5%, ..." (the stats under 50%, from Stat Stability's frozen M), or its inline form beside a season picker. Tools with
a games floor wait for the live season's teams to reach it before defaulting to it; their pickers still offer it.

**Defaults that now follow each table** (R9-033, R9-034): routes on tables the daily update doesn't refresh open on the
newest season their table has; Stat Leaders' and the Dashboard's floors scale with the games played so far; Garbage
Time, Rim Deterrence and Hot Streaks open on the newest season someone qualifies in. Pages that stay on the newest
complete season by design are listed in R9-035.

**New on the pages:** the Dashboard's "This week" (`GET /dashboard/week`: the seven days of finals ending on the current
season's last stored date, the latest night's results, the biggest upset by the held-out pre-game odds and the best game
by excitement, each with its Game Replay; before a season starts, the last week of the newest complete season and the
next season's first tip); Live Scores keeps the date in the link (`?page=scores&date=`) and gives every final with stored
play-by-play a Game Replay link beside its box score (`replay_id` on `/games/by-date`).

**How it was checked without a live season:** the step-4 trial schema (`zz_trial27`: the 2026-27 preseason through
2026-10-05 stored as season 2027, the daily chain and the models run with `--season 2027`) with the three backends
pointed at it by `PGOPTIONS`, a crawl of every route and a page sweep (Step 5 section of `docs/qa/ROUND9_ISSUES.md`).
`api/tests/test_current_season.py` (8 tests) checks the rule on the real database and on a scratch copy with a fake
opening night, the scaled floors, the week's upset and best game against the Best Games tables, the replay ids, the
per-table defaults and the frontend's lack of season literals.

**The test suite on the copy** (R9-036) failed 49 tests and errored 5 at first: tests that iterated "every season" and
meant the complete ones. They now pin 2025-26 (`season <= 2026`) and pass on the copy and on the real database. The same
run found **R9-037**: the daily update's shot step adds `player_shots_cache_status` rows for rookies, a table without a
season dimension that the manifest counted whole, so the first live run would have stopped `paper_numbers.py`; the table
now has a paper-rows predicate (the rows at the freeze) and `paper-inputs` is byte-identical. Rule for later steps: a
table a daily step writes needs a season dimension or a predicate in `paper_freeze.EXPLICIT_PREDICATES`.

## For the guide

- The paper is frozen on 2025-26 by code: one constant, every paper script reads through it, and the manifest hashes
  only those rows; tests prove a fake 2026-27 row changes nothing (round 9 step 1, 2026-10-06).
- Every one of the 218 tables is classified for the live season (38 daily, 36 season-to-date, 47 season-end, 73 frozen)
  with the rule that only tables with a season dimension may change.
- The live feeds were measured on the 2026-27 preseason: ESPN's game summary (1.5 s, full play-by-play with
  coordinates) replaces the stale hosted release; officials need BoxScoreSummaryV3; everything else answers in under
  a second.
- One season adds about 410 MB; Layerbase can hold it.
- Step 2 (2026-10-06): one command, `daily_update.py`, fetches every final since the last run and refreshes the season's
  source tables; the play-by-play mapping equals the old release's rows exactly, tested on the 2026-27 preseason.
- Step 3 (2026-10-06): the same command then rebuilds the season's fourteen derived tables for that season only, through
  the full builds' own code; a test proves the mode gives byte-identical rows for 2025-26, and a whole season takes 4
  minutes, so a game day is well inside the 10-minute target.
- Step 4 (2026-10-07): the season-to-date models (RAPM, the Rating Tracker, projections vs actual, the Season Simulator,
  award chances, Stat Stability's warnings, plus Luck & Schedule, Shot Value, the zone mixes and the scouting splits)
  follow the live season under one rule: every pooled fit stays fitted on the paper's seasons and is only applied to
  2026-27, checked against the stored fit rows before it is used; a test proves the mode reproduces 2025-26's stored
  rows; the eight builds add about three minutes to a game day; every page says "season to date".
- Step 5 (2026-10-07): the app opens on 2026-27 from the morning after opening night (one rule, one endpoint), every
  page showing the live season says "so far, through <date>" with the games played and, where it matters, how much of a
  player's numbers is still noise; the Dashboard has a "This week" panel (results, biggest upset, best game) and Live
  Scores links each final to Game Replay. Checked on a copy of the database holding the preseason as a stand-in season:
  three pages that broke on a short season and about a dozen routes that defaulted to a season they don't have, all fixed.
