# Round 9 issue list (the live 2026-27 season)

Round 9 keeps the app current through the 2026-27 season while the paper stays frozen on 2025-26 (plan pasted by
the owner per step; design in `docs/LIVE_SEASON.md`). This file is its spine, in the format of
`docs/qa/ROUND8_ISSUES.md`: every problem found gets one numbered entry here, and every later step reads it first and
updates it in its own commit. **Never delete an entry; mark it.**

- **Severity:** `broken` (a page, route or run doesn't work) · `wrong number` (a value is wrong, stale or at risk of
  being wrong) · `slow` · `looks wrong` (wording, labels, missing badge) · `design` (a decision a later step must take,
  written down so it isn't lost).
- **Status:** `open` · `fixed in <commit>` · `won't fix` + why · `decided` (+ the decision) for design entries.
- **Step:** the round-9 step expected to take it (1 design and guards, 2 daily fetch, 3 daily rebuild, 4 season-to-date
  models, 5 current-season app mode, 6 ledger + weekly report, 7 the real week, 8 close-out).

**Round 9 step 1 (2026-10-06): 9 entries, 1 fixed (R9-001, in the step's follow-up commit), 8 open** (R9-002 to R9-009:
decisions for steps 2-4, each entry says which). Step 1 also fixed round 8's R8-086 (the manifest's digest in a7ff2ea,
the `\pnLgAsOf` pin in the follow-up).

**Round 9 step 2 (2026-10-06): 15 entries in all; step 2 fixed R9-004, R9-005, R9-006, decided R9-003 and R9-010, and
logged R9-011 to R9-015** (R9-011 for step 5, R9-012 a season-end decision, R9-013 to R9-015 observations the daily update
handles). Open for later steps: R9-002, R9-007, R9-008, R9-009, R9-011, R9-012.

## Step 1: design and guards (2026-10-06)

### R9-001 · Four paper-stage files still read every season
- **Severity:** wrong number (the paper would move with the live season) · **Step:** 1 (follow-up right after the round 8.5 step C commit) · **Status:** fixed in the round 9 step 1 follow-up commit (2026-10-06): the four files wrapped (174 sites), the shared loaders they call take `through=` (`pbp_lineups.load_season_names` / `load_espn` / `match_coordinates` / `chart_matches`, `repair_espn_player_ids.find` / `unique_names`, `build_event_clock.chart_clock` / `clock_check`; defaults unchanged, checked equal on today's data), `\pnLgAsOf` = the lock date; `paper_numbers.build()`, every audit check and every Data Quality live check recorded with 0 unbounded reads in 170 statements; every macro value unchanged; `paper-inputs` digest `e8f6204b932c41bb`
- **Where:** `scripts/paper_numbers.py` (~35 reads of season-bearing tables: counts of shots, events, stints, the lines, `rapm_fits`, `possessions`, `player_rating_tracker`, ...), `scripts/paper_data_audit.py` (~40, all of the audit's feed and table checks), `scripts/build_data_quality.py` (~20) and `api/data_quality_lib.py` (~25, the Data Quality page's live checks, which must equal the audit). Also `paper_numbers.py` `ledger()`'s `\pnLgAsOf` = `max(ledger_runs.started_at)`, which every `ledger_update.py` run moves (the second half of R8-086).
- **Reproduce:** `api/tests/test_paper_frozen.py`'s strict xfails `test_paper_stage_scripts_read_capped_tables_through_F[<file>]` and `test_paper_numbers_and_the_audit_bound_every_capped_table`: they fail (as expected) because these files name `pbp_events`, `lineup_stints`, `player_shots`, ... without `paper_freeze.F()`.
- **Found by:** step 1's audit of every paper-stage read. The other nine paper-stage scripts were capped in step 1's commit; these four were open in the round 8.5 step C chat (its heave measurements and chart claims) at the time, so editing them would have collided. **Fix:** the same mechanical `F()` wrapping plus `\pnLgAsOf` = the lock date (or the date of the last run before the freeze), then drop the `PENDING` markers in the test.

### R9-002 · Pooled app tables without a season dimension can't take 2026-27 rows
- **Severity:** design · **Step:** 3 / 4 · **Status:** open
- **Where:** `player_wpa_totals` and `wpa_clutch_league` (Clutch WPA: career totals 2020-21 to 2025-26, `compute_wpa.py`), `referee_tendencies` and `referee_crew_tendencies` (`build_referee_tendencies.py`, pooled over every season), `hot_streak_persistence`, `stat_stability*` (measured constants). The paper reads all of them (`paper_beliefs.py` recomputes clutch WPA and the referee numbers and stops on any difference; `paper_xrapm.py` reads stat stability's M).
- **Reproduce:** `scripts/live_season.py` classifies them frozen; `api/tests/test_live_season.py` fails if one is made daily or season-to-date without a season dimension.
- **Found by:** step 1's classification. **Decision needed:** add a `season` (or `through`) column so the paper's rows (<= 2025-26) and the live season's can coexist, or keep these pages at "through 2025-26" for round 9. Clutch WPA and Referee Tendencies are the pages a live-season user would expect to update.

### R9-003 · Rookie identity rows wait for the season's end
- **Severity:** looks wrong (a 2026-27 rookie's profile has stats but "bio not on file") · **Step:** 2 · **Status:** decided (step 2, 2026-10-06): rookies get their ids through `player_season_stats`' 2027 rows, which `daily_update.py` refreshes from LeagueDashPlayerStats before it maps the night's play-by-play (every name in the eight preseason games' 4,256 events matched an id; without that order, 682 events of 47 rookies had none); `player_bio` / `player_id_map` / `player_first_season` stay as they are until the season-end rebuild (a rookie's profile shows his season line and "bio not on file")
- **Where:** `player_bio`, `player_id_map`, `player_first_season` (built from the Kaggle export): one row per player, no season dimension (`player_bio.last_season` moves for every active player), so adding rookies during the season would change the paper's inputs (the manifest hashes these tables whole; `paper_data_audit.py` and `paper_beliefs.py` read `player_bio`).
- **Found by:** step 1's invariant check. The 2026-27 lines don't depend on it: `pbp_lineups.load_season_names()` takes ids from `player_season_stats`' 2027 rows, which 9-2 refreshes daily from LeagueDashPlayerStats (rookies included). **Options for 9-2:** a season-dimensioned side table for in-season bios, or accept the gap until the season-end rebuild.

### R9-004 · The hosted ESPN play-by-play release can't feed a daily update
- **Severity:** broken (for the live season) · **Step:** 2 · **Status:** fixed in the round 9 step 2 commit (2026-10-06): `scripts/espn_summary.py` maps ESPN's game summary to the release's rows (play position, period, the release's Float32 clock to four decimals, scores, team, first participant named from the box score and matched by `fetch_pbp_espn.PlayerMatcher`, type text, text), and `api/tests/test_daily_update.py` proves every column equal on 2,069 stored events of four 2025-26 games, one of them two overtimes
- **Where:** `scripts/fetch_pbp_espn.py` reads sportsdataverse's release (`play_by_play_<season>.parquet`): the 2026 file was last modified 2026-09-09 and there is no 2027 file (404 on 2026-10-06).
- **Reproduce:** `HEAD https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_pbp/play_by_play_2027.parquet`.
- **Found by:** step 1's source measurements. **Fix (9-2):** read ESPN's game summary per final (`site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=<id>`: 522 plays with type, text, clock, period, scores, team id, participants' athlete ids, coordinates, wallclock; 1.5 s, 440 kB) and map it to `pbp_events`' columns; prove on a 2025-26 game that the mapped rows equal the release's (same `action_number`, `description`, `action_type`, ids), since the lines parser reads the text.

### R9-005 · Officials: BoxScoreSummaryV2 answers nothing for games from 2025-04-10
- **Severity:** broken (for the live season) · **Step:** 2 · **Status:** fixed in the round 9 step 2 commit (2026-10-06): `fetch_referee_officials.fetch_game_officials()` reads BoxScoreSummaryV3 (`personId` + name; answers for old games too: 0022400001, 0022500001, 0022501230 checked), and `daily_update.py` uses it for the live season's games; the V2 gap of 2024-25's last games and 2025-26 is R9-012
- **Where:** `scripts/fetch_referee_officials.py` (`boxscoresummaryv2`); on 2026-10-06 game `0012600004` returned an empty body (`JSONDecodeError`); nba_api itself warns about it.
- **Found by:** step 1's measurements. **Fix (9-2):** `BoxScoreSummaryV3` (`boxScoreSummary.officials` with `personId`, 1.3 s) or ESPN's summary `gameInfo.officials` (names only).

### R9-006 · stats.nba.com needs nba_api's own headers; the per-game shot chart is slow
- **Severity:** slow · **Step:** 2 · **Status:** fixed in the round 9 step 2 commit (2026-10-06): `daily_update.py` passes no headers of its own and fetches the chart per team and season type through `fetch_season_shots.fetch_all` (30 calls of 1-2 s, only on days a final of that type has no or fewer shots stored than the box score's FGA)
- **Where:** a probe with a minimal header set (`User-Agent`, `Referer`, `Origin`) timed out at 30 s on four endpoints; the same calls with the library's default headers answered in 0.5-2.4 s. `ShotChartDetail` with `game_id_nullable` took 23 s for 177 shots; per team and season type 0.95 s for 94.
- **Found by:** step 1's measurements. **Fix:** never pass custom headers; fetch shots per team (fetch_season_shots.py's path, 90 calls a season), not per game.

### R9-007 · End-of-quarter heaves in the 2026-27 lines (R8-088)
- **Severity:** wrong number · **Step:** 3 · **Status:** open
- **Where:** since 2025-26 the NBA counts a missed end-of-quarter heave as a team attempt; ESPN logs it (`action_type` 'Heave Jump Shot', about 1,100 a season) and `build_player_game_lines.py` charges the shooter (round 8.5 step C's R8-088 in `docs/qa/ROUND8_ISSUES.md`).
- **Found by:** the round 8.5 step C chat (relayed to step 1). **For 9-3:** decide the lines' treatment before the first 2026-27 rebuild and apply it to 2025-26 and 2026-27 alike (a change to 2025-26 is a paper change: owner's call).

### R9-008 · Check tables without a season dimension in a per-season rebuild
- **Severity:** design · **Step:** 3 · **Status:** open
- **Where:** `player_game_onfloor_meta`, `possession_meta`, `pbp_event_clock_meta`, `best_games_meta`, `lineup_stint_seasons`' pooled checks, `situational_split_league`'s season 0 row: the full builds write checks over every game; a `--season 2027` run must not rewrite them over a changed row set (the paper reads several).
- **Found by:** step 1's classification. **Fix (9-3):** the `--season` mode leaves the meta tables as they are (or adds a `season` column and writes only its own season's row).

### R9-009 · Pooled fits must stay fitted on seasons <= 2025-26
- **Severity:** design · **Step:** 4 · **Status:** open
- **Where:** the shot-making cross-fit (`build_shot_making.py`: `shot_xfg`, hex, league rows), Shot Value's fit (`shot_value_fit`, 2010-11 to 2019-20) and running league level, the pre-game model and simulator constants (`pregame_model_fit`, `season_sim_params`), the luck fit (`luck_model_fit`), the Rating Tracker's hyperparameters (`rating_tracker_fit`), the WPA model, stat stability's M. Each is read by a paper script; refitting any of them with 2026-27 data changes the paper's numbers.
- **Found by:** step 1. **Rule for 9-4:** every season-to-date build reads its fit from the stored row and applies it to 2026-27 (`--fit-through 2026` behaviour); `api/tests/test_paper_frozen.py`'s manifest test and `test_live_season.py` keep these tables frozen.

## Step 2: the daily fetch (2026-10-06)

### R9-010 · The run log moves the paper's two table counts by one
- **Severity:** design · **Step:** 2 · **Status:** decided (2026-10-06): accepted and documented. `daily_update_runs` is a public table (one row per run) listed in `paper_manifest.LIVE`, so the manifest digest (`e8f6204b932c41bb`), the row total, every claim, every figure and every other macro are unchanged, but the two macros that count tables read one more once the table exists: `\pnTableCount` 199 → 200 (base tables in schema public without the paper's own `paper_*` tables; the Platform section) and `\pnManTables` 218 → 219 (every base table; Data and Code Availability). Both sentences stay true; no result moves.
- **Where:** `scripts/paper_numbers.py` (`platform()`'s `TableCount`, `manifest()`'s `ManTables`), `paper/numbers.tex` lines 29 and 1405; `paper/manifest.json` gains the entry (`live: true`, 0 paper rows); `paper/SHA256SUMS` follows.
- **Found by:** step 2, when adding the run log the plan asked for ("a run log table added to paper_manifest.LIVE"). The plan's "paper-inputs byte-identical through the round" and that instruction conflict on this one macro. **For 9-8:** the close-out's byte-identity check allows exactly these two lines of `numbers.tex` (and the manifest / SHA256SUMS entries that follow from them) besides the ledger sentence of 9-6.

### R9-011 · Tests and pages pinned to 2025-26 as the newest season
- **Severity:** wrong number (from opening night) · **Step:** 5 · **Status:** open
- **Where:** tests that take `MAX(season)` of `player_season_stats` or assert `season == 2026`: `api/tests/test_consistency.py` (263, 534), `test_round8_live.py` (271, 388), `test_smoke.py` (947, 2945), `test_round8_pages.py` (133), `test_round8_step7.py` (236), `test_report_card.py` (78, paper tables: fine); pages whose season pickers default to 2025-26 and routes that take the newest season as "current".
- **Reproduce:** once `daily_update.py` has stored 2026-27 rows (from 2026-10-20), `player_season_stats` has season 2027 rows with 1-5 games: a route that picks `MAX(season)` serves a 2-game season as the current one, and those tests fail.
- **Found by:** step 2's grep while building the loader. **For 9-5:** the shared "current season" rule (the plan's step 5) must say when 2026-27 becomes the default (e.g. after N games) and the tests must pin 2025-26 explicitly where they mean the complete season.

### R9-012 · Officials missing for 2024-25's last games and all of 2025-26 (the V2 gap)
- **Severity:** wrong number · **Step:** season end (owner's call) · **Status:** open
- **Where:** `game_officials` has 3 of 1,230 games of 2025-26 (and none from 2025-04-10 of 2024-25): those games are in `game_officials_fetch_log` with zero officials from the V2 fetch and are never retried. V3 answers for them (checked on 0022500001, 0022501230).
- **Found by:** step 2's V3 probe. **Why not now:** `referee_tendencies` / `referee_crew_tendencies` are pooled over every season and read by the paper (`paper_beliefs.py` recomputes them); backfilling 2025-26 officials changes the paper's referee numbers. Backfill (delete the zero-official log rows of those games, rerun `fetch_referee_officials.py`, rebuild the referee tables, rerun `paper-inputs`) at the season's end with the owner's OK.

### R9-013 · A game's shot chart can be short of the box score for good
- **Severity:** wrong number (NBA.com's) · **Step:** 2 · **Status:** decided: reported and re-fetched, not fixed
- **Where:** preseason game 0012600067 (DEN-UTA, 2026-10-04): ShotChartDetail gives 165 attempts (80 + 85) against 182 FGA in the box score; most of the second quarter is missing for both teams, and a re-fetch two days later gives the same 165. The same happened to 1 of 1,230 games of 2025-26 (round 8.5 step C).
- **Found by:** step 2's preseason run. **What the update does:** `step_shots` compares each game's chart attempts with `game_team_box`'s FGA, re-fetches the season type while a short game is among the dates checked, and reports `short_of_box` in the run log; the merge keeps every unchanged shot's id. A permanently short game stops triggering once its date leaves the checked range.

### R9-014 · BoxScoreSummaryV3 lists an official with an id and no name
- **Severity:** looks wrong · **Step:** 2 · **Status:** fixed in the round 9 step 2 commit (2026-10-06)
- **Where:** official 8834 in 0012600009 (TOR-MIA, 2026-10-03): `name` ' ', first and family name empty (a first-year official). `daily_update.fill_official_names()` takes the one name ESPN's summary lists that V3 doesn't ("Kastine Evans"); when the rule can't decide, the row keeps the empty name and the run log counts it (`officials_without_a_name`).

### R9-015 · What the preseason run measured (for the runbook, 9-7)
- **Severity:** design · **Step:** 2 · **Status:** decided (recorded)
- **Where:** the 2026-27 preseason through 2026-10-05 (eight finals), run at 05:37 ET on 2026-10-06: every feed had every game final by the previous evening (ESPN scoreboard and summaries, LeagueGameFinder 16 rows, LeagueDashPlayerStats 263 players, V3 officials for 7 of 8 games (one game lists none), the chart for all 8 games, one short, R9-013). A live run took 92 s (the chart 60 s of it: 30 calls at 1.5 s plus a 0.6 s pause); the same day again from the caches 1 s; nothing is written twice. The same-day lag after a game night (how soon after a final each feed answers) is still unmeasured: opening night.
