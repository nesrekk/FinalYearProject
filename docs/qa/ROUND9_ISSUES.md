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

**Round 9 step 3 (2026-10-06): 20 entries in all; step 3 decided R9-007 and R9-008 and logged R9-016 to R9-020** (what
building the `--season` mode found: three builds draw every season's intervals from one seeded stream, the deflator's
pooled constants, the sequential ids, the day's cost, the run log's two new columns). Open for later steps: R9-002, R9-009,
R9-011, R9-012.

**Round 9 step 4 (2026-10-07): 28 entries in all; step 4 decided R9-002 and R9-009 and logged R9-021 to R9-028** (what the
season-to-date models' `--season` mode found: a build whose stored rows aren't bit-reproducible on this machine, the
tracker's season summary, Shot Value's year-to-year rows and folds, shot-making left for the season's end, the award
models' in-season pool, the models' cost, and a scratch-schema accident). Open for later steps: R9-011, R9-012, R9-024.

**Round 9 step 5 (2026-10-07): 37 entries in all; step 5 fixed R9-011 and logged R9-029 to R9-037** (the current-season
rule, and what a crawl, a page sweep and the test suite on a live-season copy of the database found: three pages that
crashed or answered 500 on a short season, routes that opened on a season their table doesn't have, floors no
early-season player clears, tests that assumed the newest season is complete, a cache table that would have broken
`paper-inputs` after the first live run, and the pages still on the newest complete season by design). Open for later steps: R9-012, R9-024, R9-028,
R9-035.

## Step 1: design and guards (2026-10-06)

### R9-001 · Four paper-stage files still read every season
- **Severity:** wrong number (the paper would move with the live season) · **Step:** 1 (follow-up right after the round 8.5 step C commit) · **Status:** fixed in the round 9 step 1 follow-up commit (2026-10-06): the four files wrapped (174 sites), the shared loaders they call take `through=` (`pbp_lineups.load_season_names` / `load_espn` / `match_coordinates` / `chart_matches`, `repair_espn_player_ids.find` / `unique_names`, `build_event_clock.chart_clock` / `clock_check`; defaults unchanged, checked equal on today's data), `\pnLgAsOf` = the lock date; `paper_numbers.build()`, every audit check and every Data Quality live check recorded with 0 unbounded reads in 170 statements; every macro value unchanged; `paper-inputs` digest `e8f6204b932c41bb`
- **Where:** `scripts/paper_numbers.py` (~35 reads of season-bearing tables: counts of shots, events, stints, the lines, `rapm_fits`, `possessions`, `player_rating_tracker`, ...), `scripts/paper_data_audit.py` (~40, all of the audit's feed and table checks), `scripts/build_data_quality.py` (~20) and `api/data_quality_lib.py` (~25, the Data Quality page's live checks, which must equal the audit). Also `paper_numbers.py` `ledger()`'s `\pnLgAsOf` = `max(ledger_runs.started_at)`, which every `ledger_update.py` run moves (the second half of R8-086).
- **Reproduce:** `api/tests/test_paper_frozen.py`'s strict xfails `test_paper_stage_scripts_read_capped_tables_through_F[<file>]` and `test_paper_numbers_and_the_audit_bound_every_capped_table`: they fail (as expected) because these files name `pbp_events`, `lineup_stints`, `player_shots`, ... without `paper_freeze.F()`.
- **Found by:** step 1's audit of every paper-stage read. The other nine paper-stage scripts were capped in step 1's commit; these four were open in the round 8.5 step C chat (its heave measurements and chart claims) at the time, so editing them would have collided. **Fix:** the same mechanical `F()` wrapping plus `\pnLgAsOf` = the lock date (or the date of the last run before the freeze), then drop the `PENDING` markers in the test.

### R9-002 · Pooled app tables without a season dimension can't take 2026-27 rows
- **Severity:** design · **Step:** 3 / 4 · **Status:** decided (step 4, 2026-10-07): these pages stay "through 2025-26" for round 9. Clutch WPA, the referee tables, Hot Streak persistence and Stat Stability's M are pooled measures the paper reads whole (the manifest hashes every row), so adding 2026-27 to them would move `paper-inputs`; giving them a season column is a schema change for the next deliberate paper rebuild (season end, owner's call). Stat Stability's constants are applied to the live season's samples as they are (the `stability` Methodology card says so)
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
- **Severity:** wrong number · **Step:** 3 · **Status:** decided (step 3, 2026-10-06): the lines' treatment is kept as it is for both seasons (the shooter is charged, as ESPN logs it), so 2026-27 is built by exactly the code 2025-26 was and the two seasons read alike; changing it means rebuilding 2025-26's lines and their chain, which moves paper numbers (the `chart_heaves*` audit class): the owner's call, outside round 9
- **Where:** since 2025-26 the NBA counts a missed end-of-quarter heave as a team attempt; ESPN logs it (`action_type` 'Heave Jump Shot', about 1,100 a season) and `build_player_game_lines.py` charges the shooter (round 8.5 step C's R8-088 in `docs/qa/ROUND8_ISSUES.md`).
- **Found by:** the round 8.5 step C chat (relayed to step 1). **For 9-3:** decide the lines' treatment before the first 2026-27 rebuild and apply it to 2025-26 and 2026-27 alike (a change to 2025-26 is a paper change: owner's call).

### R9-008 · Check tables without a season dimension in a per-season rebuild
- **Severity:** design · **Step:** 3 · **Status:** decided (step 3, 2026-10-06): a `--season` run never touches `pbp_event_clock_meta`, `player_game_onfloor_meta`, `possession_meta`, `best_games_meta` or `leverage_index_grid` (no season column added: the paper hashes them whole), writes only its season's row of `lineup_stint_seasons`, `possession_seasons`, `rim_deterrence_seasons`, `assist_seasons`, `play_finder_seasons`, `player_on_off_seasons`, `leverage_validation` and `situational_split_league` (the season-0 row stays), and says so in its output; `api/tests/test_season_rebuild.py` hashes the five untouched tables before and after the chain
- **Where:** `player_game_onfloor_meta`, `possession_meta`, `pbp_event_clock_meta`, `best_games_meta`, `lineup_stint_seasons`' pooled checks, `situational_split_league`'s season 0 row: the full builds write checks over every game; a `--season 2027` run must not rewrite them over a changed row set (the paper reads several).
- **Found by:** step 1's classification. **Fix (9-3):** the `--season` mode leaves the meta tables as they are (or adds a `season` column and writes only its own season's row).

### R9-009 · Pooled fits must stay fitted on seasons <= 2025-26
- **Severity:** design · **Step:** 4 · **Status:** decided (step 4, 2026-10-07): every season-to-date build's `--season N` mode reads its pooled fit from the stored row and applies it to N, and where the fit is cheap to recompute it is recomputed from the seasons up to `paper_freeze.MAX_PAPER_SEASON` and must reproduce the stored row before the season is priced (R9-017's rule): RAPM's lambda and prior scale = 2025-26's stored choices (`lambda_rule` 'frozen:2025-26'); the Rating Tracker's five hyperparameters and sigma^2 from `rating_tracker_fit`; the simulator's prior constants (checked against `season_sim_params` to 1e-9) and the four forms' coefficients (the leave-one-season-out fit on the paper's seasons, which for a live season is the stored all-season fit, checked to 1e-9); Luck & Schedule's three curves (checked against `luck_model_fit`); Shot Value's four classes' hyperparameters and league-level shrinkage from `shot_value_fit`, with five location models fitted on 1996-97 to 2025-26 (the full build's own rule for a priced season); the scouting splits' play-type fallback SD from the paper's seasons. The fit rows themselves are never written in `--season` mode. `api/tests/test_season_models.py` proves the mode reproduces the stored 2025-26 rows and leaves the twelve pooled tables untouched
- **Where:** the shot-making cross-fit (`build_shot_making.py`: `shot_xfg`, hex, league rows), Shot Value's fit (`shot_value_fit`, 2010-11 to 2019-20) and running league level, the pre-game model and simulator constants (`pregame_model_fit`, `season_sim_params`), the luck fit (`luck_model_fit`), the Rating Tracker's hyperparameters (`rating_tracker_fit`), the WPA model, stat stability's M. Each is read by a paper script; refitting any of them with 2026-27 data changes the paper's numbers.
- **Found by:** step 1. **Rule for 9-4:** every season-to-date build reads its fit from the stored row and applies it to 2026-27 (`--fit-through 2026` behaviour); `api/tests/test_paper_frozen.py`'s manifest test and `test_live_season.py` keep these tables frozen.

## Step 2: the daily fetch (2026-10-06)

### R9-010 · The run log moves the paper's two table counts by one
- **Severity:** design · **Step:** 2 · **Status:** decided (2026-10-06): accepted and documented. `daily_update_runs` is a public table (one row per run) listed in `paper_manifest.LIVE`, so the manifest digest (`e8f6204b932c41bb`), the row total, every claim, every figure and every other macro are unchanged, but the two macros that count tables read one more once the table exists: `\pnTableCount` 199 → 200 (base tables in schema public without the paper's own `paper_*` tables; the Platform section) and `\pnManTables` 218 → 219 (every base table; Data and Code Availability). Both sentences stay true; no result moves.
- **Where:** `scripts/paper_numbers.py` (`platform()`'s `TableCount`, `manifest()`'s `ManTables`), `paper/numbers.tex` lines 29 and 1405; `paper/manifest.json` gains the entry (`live: true`, 0 paper rows); `paper/SHA256SUMS` follows.
- **Found by:** step 2, when adding the run log the plan asked for ("a run log table added to paper_manifest.LIVE"). The plan's "paper-inputs byte-identical through the round" and that instruction conflict on this one macro. **For 9-8:** the close-out's byte-identity check allows exactly these two lines of `numbers.tex` (and the manifest / SHA256SUMS entries that follow from them) besides the ledger sentence of 9-6.

### R9-011 · Tests and pages pinned to 2025-26 as the newest season
- **Severity:** wrong number (from opening night) · **Step:** 5 · **Status:** fixed in the round 9 step 5 commit (2026-10-07): the rule is `api/current_season.py` (R9-029); the season pickers read it (`frontend/src/utils/season.js`); `test_consistency.py`'s two tests pin 2025-26 (the standings truth and the shot-chart span); the other tests the entry lists read their own seasons and pass with live rows (checked on the step's live-season copy, R9-036)
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

## Step 3: the daily rebuild of the current season (2026-10-06)

### R9-016 · Three builds draw every season's intervals from one seeded random stream
- **Severity:** design · **Step:** 3 · **Status:** decided (2026-10-06): in `--season` mode these three compute every season exactly as the full build does and write only the season's rows
- **Where:** `build_player_on_off.py` (`np.random.default_rng(SEED)` once, the bootstrap drawn per player-season-team in sorted order), `build_rim_deterrence.py` (the same, in `player_rows()`), `build_situational_splits.py` (a module-level `RNG`: the league rows' bootstrap in loop order and the chance baseline's shuffle over every season's games at once). A row's interval therefore depends on the draws made for the rows before it, across seasons.
- **Reproduce:** compute 2025-26 alone and compare `player_on_off.on_off_ci_low` with the stored value: different digits.
- **Found by:** step 3, designing the `--season` mode. **Why not re-seed per unit:** that changes every stored interval of 2020-21 to 2025-26 (the paper reads these tables: a manifest change the round forbids). **What it means:** a `--season 2027` run costs the full build's time for these three and gives 2027 the intervals a full build would give it today; the earlier seasons keep the intervals the 2026-10-06 build gave them. The point estimates don't depend on the stream. Re-seeding per unit is the right fix at the next deliberate paper rebuild (owner's call).

### R9-017 · The deflator's pooled constants
- **Severity:** design · **Step:** 3 · **Status:** decided (2026-10-06): fitted on seasons <= `paper_freeze.MAX_PAPER_SEASON`, checked, applied to the live season
- **Where:** `build_leverage_splits.py` normalises every event's Leverage Index by two league-wide constants (the per-event scoring-outcome mix and the mean expected swing) and measures the 3PA text rule's accuracy on every event: pooled over every season in the full build (R9-009's class). A full run with 2026-27 events would move them at the fourth decimal and re-price every stored season; a `--season` run that used the growing set would price October's games differently each day.
- **Found by:** step 3. **Rule:** `--season N` computes the constants from the events of seasons <= 2025-26 (`FIT_THROUGH`), stops unless they reproduce the stored `leverage_index_grid` to 1e-9, and applies them to the season's events; the grid is never rewritten. A later full build (season end) refits them with the paper's rebuild.

### R9-018 · Two tables number their rows across seasons
- **Severity:** design · **Step:** 3 · **Status:** decided (2026-10-06)
- **Where:** `lineup_stints.stint_id` and `play_finder_games.game_no` (also in `play_finder_events`) are 1..n over every game in date order. `season_mode.next_id()` gives a `--season` run the ids one past the greatest id of an earlier season (what the full build gives that season, since its games all come after the earlier seasons') and stops when a later season's ids are in the way; checked on 2025-26 (`stint_id` 240,491.., `game_no` 6,001..). Nothing joins on `stint_id` across tables (`possessions` uses `stint_no` within the game).

### R9-019 · What a day's rebuild costs
- **Severity:** design · **Step:** 3 · **Status:** decided (recorded; the real week, 9-7, re-measures it on game days)
- **Where:** `api/tests/test_season_rebuild.py` runs the fourteen builds with `--season 2026` (a whole 1,230-game season, the worst case: a day adds 5-15 games to a season that is rebuilt whole) and prints each one's seconds; `docs/LIVE_SEASON.md` section 6 has the measured times. The three R9-016 builds and the deflator (which loads every event for its constants) are most of it.

### R9-020 · The run log gained two columns
- **Severity:** design · **Step:** 3 · **Status:** decided (2026-10-06)
- **Where:** `daily_update_runs.rebuild_seconds`, `rebuild_steps` (JSON: each build's status and seconds), added by `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` on every run. The table is in `paper_manifest.LIVE`: outside the digest and the staleness check, so `paper-inputs` is unchanged except the table's own schema line in `paper/manifest.json` (not a paper file; R9-010's two table-count macros don't move again).

## Step 4: the season-to-date models (2026-10-07)

### R9-021 · Luck & Schedule's stored rows aren't bit-reproducible on this machine
- **Severity:** design · **Step:** 4 · **Status:** decided (recorded; the test compares numerically)
- **Where:** `build_luck_schedule.py`'s SRS comes from `numpy.linalg.lstsq` (LAPACK through Accelerate): a full build run into a scratch schema on 2026-10-07 differed from the stored `team_luck_schedule` rows in 509 of 510 team-seasons at the 13th-14th digit (SRS up to 9e-14, home court 8e-15, expected wins 0), the same run-to-run noise `build_season_sim.py` and the tracker's fit were known to show (CLAUDE.md). The `--season` mode therefore cannot be proved byte for byte: `api/tests/test_season_models.py` compares its 2025-26 rows to the stored ones to 1e-9 (the seven other builds are compared by content hash).
- **Found by:** step 4's equality check. The simulator's odds reproduce exactly once the mode uses the full build's own matrix slice for the prediction (`X[~tr] @ b`): a 1-ulp difference in 2 of 1,230 games came from a differently shaped product.

### R9-022 · The Rating Tracker's per-season summary lives in the frozen fit row
- **Severity:** design · **Step:** 4 · **Status:** decided (2026-10-07)
- **Where:** the full build keeps each season's players / rows / games / stints / possessions / intercept / home term / BPM measurements / qualified count in `rating_tracker_fit.seasons` (JSON), one row without a season dimension that the paper hashes whole; the RAPM page's tracker tab reads it per season. A live season can't be added there.
- **Decision:** `build_rating_tracker.py --season N` writes the season's summary as the row version 'tracker', season N of `rapm_fits` (the one per-season model table; `lambda` there is lambda_0, `prior_scale` is k, `lambda_rule` 'tracker:frozen'); `build_rapm.py --season N` deletes only the three RAPM versions' rows of the season; `routers/rapm.py` reads the JSON for the paper's seasons and the row for any other (`_tracker_season_rows()`), and leaves the row out of `/rapm/validation`'s fit list. Also decided: for the last season on file the smoother's estimate equals the filter's, so the live season's "with hindsight" rows equal its "as of then" rows, and the earlier seasons' with-hindsight rows (which a full build would revise with the new season's games) stay the paper's; the Methodology card says so.

### R9-023 · Shot Value's year-to-year rows and folds in `--season` mode
- **Severity:** design · **Step:** 4 · **Status:** decided (2026-10-07)
- **Where:** (a) the validation's year-to-year correlations (class 'yty', N-1 -> N) need the earlier season's per-player parts, which the `--season` run reads from the stored `shot_value_added` (REAL columns), where the full build has them in float64: the correlations can differ in the sixth or seventh digit, so the test compares the season's own scope by hash and the yty rows numerically. (b) A player's fold must reproduce `shot_xfg`'s (the paper's shuffle over the players seen up to 2025-26); a player seen only in the live season takes the next fold in turn (`season_folds()`; 0 newer players when run for 2025-26, checked equal to the full build's). (c) A live season has no `p_xfg` validation row (`shot_xfg` is frozen) and `shot_value_fit`'s 'models' row keeps the paper seasons' iteration counts only.
- **Found by:** step 4's equality check (the first run also deleted the pooled 'test' scope rows, whose `seasons` label equals the season's: the delete now names the scope).

### R9-024 · Shot-making (expected FG%, the quality map, the hex grid) has no 2026-27 rows
- **Severity:** looks wrong (the Shot Charts tabs say "not on file" for 2026-27) · **Step:** season end (or 9-8 if wanted) · **Status:** open
- **Where:** `build_shot_making.py` cross-fits five by-player fold models over every season at once (`shot_xfg`, `player_shot_making`, `player_shot_hex`, `shot_hex_league`, `shot_making_league`): the paper reads `shot_xfg` and `shot_making_validation`, and the docs' classification allowed a `--season` mode that scores 2026-27 with the fold models refitted on <= 2025-26. Not built in step 4 (the step's scope was the six models the plan names; Shot Value already carries the live season's look-ahead-free prices). The pages read the tables that exist, so 2026-27 simply isn't offered.
- **Fix:** a `--season N` mode on `build_shot_making.py` on the pattern of `build_shot_value.py`'s (five fold models on the paper's seasons, the paper's folds, the season's rows only), or the season-end full rebuild.

### R9-025 · The award models in a live season
- **Severity:** design · **Step:** 4 · **Status:** decided (2026-10-07; the `awards` Methodology card)
- **Where:** `/mvp|dpoy|roy|allnba/predict/{season}` read `player_season_stats`' per-game line of the season (the live season's rows are refreshed daily). The DPOY and All-NBA pools required 40 games, so nobody qualified until December; ROY read `player_first_season`, which has no 2026-27 rookie until the season-end rebuild (R9-003).
- **Decision:** `mvp_api.season_progress()` (in-season = `luck_schedule_seasons.complete` is false, else a season past the paper's) scales the games floor to the games played so far (ceil of 40 x most games played / 82, at least 1), ROY counts a player with no earlier season on file as a rookie until the first-season table is rebuilt, and every response carries `in_season`, `through`, `games_played_max` and an `in_season_note` the Awards Race page shows ("season to date, not a final forecast"). Nothing is retrained.

### R9-026 · What a day's model builds cost
- **Severity:** design · **Step:** 4 · **Status:** decided (recorded; 9-7 re-measures on game days)
- **Where:** `daily_update.py` runs the eight model builds after a clean rebuild (`--no-models` skips them, `--models` forces them, `--models-only` runs nothing else). Measured on a whole 1,230-game season (`--season 2026`, the worst case) and on the eight-game preseason trial: see `docs/LIVE_SEASON.md` section 7. Shot Value's five fold fits (about two minutes) are most of it whatever the season's size; RAPM's three fits and the simulator's feature pass are the rest.

### R9-027 · A full build run through a scratch schema's search_path drops public tables it didn't find there
- **Severity:** broken (two public tables dropped for a few minutes) · **Step:** 4 · **Status:** fixed (restored from the 2026-10-06 dump the same hour; content hashes equal to the paper manifest's)
- **Where:** to see whether a full `build_luck_schedule.py` reproduces the stored rows, it was run with `PGOPTIONS=-c search_path=zz_m_luck,public` into a schema holding copies of the two tables the `--season` mode writes, not the two the full build also drops and recreates (`luck_model_fit`, `luck_schedule_validation`): its `DROP TABLE IF EXISTS` resolved to `public` and the CREATE went into the scratch schema. `pg_restore -t` from `~/nba_backups/nba_analytics_pre85c_2026-10-06.dump` put them back (the primary keys re-added by hand: `-t` restores the table and its data, not its constraints), and `paper_manifest.content_hash` equals the manifest's for both.
- **Rule (CLAUDE.md verification pitfalls):** a full build may only run through a search_path into a schema that holds a copy of **every** table it drops or creates; the `--season` modes are safe because they never drop (the scratch runner copies what they write). The `zz_` schemas of the step were dropped afterwards.

### R9-028 · The LeagueGameFinder step isn't bounded by `--date`
- **Severity:** looks wrong (a test's count) · **Step:** 7 (the runbook) · **Status:** open (the test relaxed)
- **Where:** `daily_update.step_nba_games` fetches the season type's whole LeagueGameFinder table and rebuilds `team_game_fatigue` / `game_team_box` from every game it lists, while the scoreboard, game_scores and the play-by-play stop at `--date`. On 2026-10-07 the preseason run of `api/tests/test_daily_update.py` (`--date 2026-10-05`) stored 24 box rows (12 games) against the 16 (8 games) of 2026-10-06, because four more preseason games had been played by the run day. Harmless for a real daily run (today is the date) but it means a `--date` replay isn't a pure replay for those two tables. The test now asserts at least 16.

## Step 5: the app in "current season" mode (2026-10-07)

How it was checked: a scratch schema `zz_trial27` (the step-4 trial's pattern: copies of every table the fetch, the
rebuild and the models write, then `daily_update.py` on the 2026-27 preseason through 2026-10-05 stored as season 2027,
then the fourteen + eight builds with `--season 2027`) stands in for opening night: eight finals, 16 teams with one
game. The three backends ran with `PGOPTIONS=-c search_path=zz_trial27,public`; `scripts/qa_route_crawl.py` called all
211 routes and `frontend/qa/page_scan.js` swept 36 pages at 1280/375 x Paper/Ink. First crawl: 11 not 2xx, 5 empty;
after the fixes below: 1 not 2xx (`/shots/league-zones/2025`, a table the trial copied partly; 200 on the real
database) and 1 empty (`/ledger/live/games`, no scored game yet). On the real database (no live season):
`docs/qa/crawl_2026-10-07.tsv`, 211 routes, 0 not 2xx, 0 over 3 s.

### R9-029 · One rule for the current season
- **Severity:** design · **Step:** 5 · **Status:** decided (2026-10-07)
- **Where:** `api/current_season.py` (`status()`, cached five minutes; `GET /meta/season` in `api/routers/season_status.py`)
  and `frontend/src/utils/season.js` (read once before the first render in `main.jsx`, 1.5 s timeout, the last answer
  kept in localStorage, fallback 2025-26).
- **Decision:** the app opens on the newest season whose regular season is complete (`luck_schedule_seasons.complete`)
  until a newer season has `DEFAULT_AFTER_GAMES` = 1 regular-season final in `game_scores` (written by
  `daily_update.py` from opening night), then on that season: "once it has games", as the plan says, with warnings
  rather than a delay. The answer carries the live season's through date, games played of the schedule
  (`luck_schedule_seasons.scheduled`, else the ledger's 1,200 counting games), games a team, the next season's first tip
  before it starts (the locked schedule: 2026-10-20), and `early`: for eight stats the median sample of a rotation player
  (15+ minutes a game) so far and its reliability n / (n + M) with Stat Stability's frozen M. Tools with a games floor
  open on the newest complete season until the live season's teams can meet it (`default_season_for(min_games)`, both
  sides: Radar, Player Comparison, Heliocentricity, Similarity 20 games; the Leaderboard Builder's default 30; Hot
  Streaks window + 10). Every page showing the live season says so: `common/LiveSeasonNote.jsx` (the full line with the
  early-season warnings: Player Stats, Player Comparison, Rookies, Stat Leaders, Impact, With/Without, Radar,
  Heliocentricity) or its inline `LiveSeasonTag` ("so far, through Oct 22 · 34 games") beside the season picker (On/Off,
  Pair and Lineup Chemistry, Luck & Schedule, Rim, Rotations, Possessions, Assists, Splits, RAPM, Best Games, Breakouts,
  Leaderboard Builder, Shot Charts, the team page, every profile block's picker, the Game Log); the shared
  `SeasonSelect` labels it "2026-27 (so far)".

### R9-030 · Assist Network answered 500 for a season without NBA.com assists
- **Severity:** broken · **Step:** 5 · **Status:** fixed in the round 9 step 5 commit
- **Where:** `/assists/options`, `/assists/pairs`, `/assists/team`: `assist_seasons.ast_vs_nba` is NaN for a season with
  no NBA.com season rows to compare with (the trial's preseason; also a live season if the season stats step failed),
  and JSON can't carry NaN. `build_assist_network.py` now stores NULL there (every stored season is unchanged: all have
  the ratio) and the router reads a stored NaN as None.

### R9-031 · Possession Explorer crashed on a short season
- **Severity:** broken · **Step:** 5 · **Status:** fixed in the round 9 step 5 commit
- **Where:** `/possessions/league` gave a start type with no transition (or no settled) possession yet only the half it
  had; the page's transition table read both. The route now always gives both halves (0 possessions, ppp None) and the
  page shows "—" for the gap. Also: the options' "all six seasons" pooled points per possession and the team-trait table
  (share beyond chance, year-to-year r) now pool the complete seasons only (`current_season.status()['latest_complete']`),
  so the page's "six seasons" stays true and a season with a few games a team doesn't join the averages.

### R9-032 · Garbage Time crashed on a season with no qualified player
- **Severity:** broken · **Step:** 5 · **Status:** fixed in the round 9 step 5 commit
- **Where:** the section's validation line read `ppg_vs_official_r.toFixed()`, None when nobody qualifies; the route's
  default season is now the newest with a qualified player (`player_leverage_summary.qualified`), and the line shows "—".

### R9-033 · Routes that opened on a season their table doesn't have
- **Severity:** broken (404 or an empty page from opening night) · **Step:** 5 · **Status:** fixed in the round 9 step 5 commit
- **Where:** routes whose default was `MAX(season)` of `player_season_stats` (the live season from opening night) but
  which read a table the daily update doesn't refresh: `/matchups/player/*` (player_matchups), `/players/playtype-profile/*`
  (player_playtypes), `/hustle/leaders` (player_hustle), `/teams/compare/*` (team_seasons: Basketball-Reference's team
  ratings, rebuilt at the season's end), `/players/garbage-time/player/*` and `/games/hot-streak/*` (the league's newest
  season, not the player's: a player who hasn't played yet got a 404), `/era/translate` (its default target had no league
  averages yet: 400). Each now defaults to the newest season its own table has (`current_season.latest_season_in`; for a
  player, his own newest), and the pages with hard-coded pickers for these tools stay on the newest complete season
  (Matchup Finder, Offensive Style, Player Archetypes, Playoff Forecaster: `to={latestCompleteSeason()}`).

### R9-034 · Floors nobody clears in a young season
- **Severity:** looks wrong (empty tables) · **Step:** 5 · **Status:** fixed in the round 9 step 5 commit
- **Where:** Stat Leaders' stored path and the Dashboard's top-scorer tile used the 30-game floor for the live season
  (empty until December): `routers/leaders.stored_floor()` scales it to 70% of the most games played so far, the live
  path's rule (R8-068). The Leaderboard Builder's default season (no link) is the newest one whose teams have played its
  30-game floor; `/defense/rim-deterrence` defaults to the newest season where someone clears its minutes floor;
  `/games/hot-streaks` to the newest season with window + 10 games. Player Stats hid every live-season row: its position
  chips filter on `bpm_position`, which the season-end BPM build writes; rows without one now show while every chip is on
  ("position n/a").

### R9-035 · Pages that stay on the newest complete season by design
- **Severity:** design · **Step:** season end (or 9-8 if wanted) · **Status:** open (recorded)
- **Where:** pages whose data isn't refreshed by the daily update keep showing the newest season they have, labelled
  with its season: the team page (team_seasons), Team Comparison's ratings, shot-making / the quality map (R9-024), DAD,
  Gravity, Spacing Lab, Role Finder, Matchup Finder, play types and hustle (tracking fetches not in the daily run),
  Player Archetypes and roles (season-end clustering), Era Translator's targets, Similarity (stored season vectors from
  20 games on). Bringing any of them into the season is a fetch or a `--season` build, not a page change.

### R9-036 · Tests that assumed the newest season is complete
- **Severity:** broken (the suite would have gone red on opening night) · **Step:** 5 · **Status:** fixed in the round 9 step 5 commit
- **Where:** the suite run with `PGOPTIONS=-c search_path=zz_trial27,public` (the live-season copy; the four tests that build
  their own schemas left out): **49 failed + 5 errors** at first. After cleaning the copy's own artifacts (two event
  tables had been copied with one season only, and importing `impact_core` with that search_path created an empty
  `league_shot_zones` in the scratch schema, shadowing public's: `shots_lib.ensure_schema()`'s
  `CREATE TABLE IF NOT EXISTS` checks the first schema only) and pinning every test that means the complete seasons:
  **0 failed** (589 passed, 2 skipped on the copy; 636 passed, 1 skipped on the real database). The pins: season lists and counts over "every
  season" (`WHERE season <= 2026`, `if s <= 2026`) in test_smoke (23 places), test_consistency (the seeded samples
  are drawn from 2020-21 to 2025-26 so they stay the same draw), test_known_facts, test_data_quality, test_rating_tracker,
  test_round85_shots, test_round8_rebuild, test_paper_beliefs / paper_data_audit / paper_xrapm (the paper's seasons),
  test_pregame_availability, test_lineup_predictor, test_workbench(_step7), test_usability_study; Stat Leaders' test
  applies the route's (scaled) floor; four round-8 static tests now pin `utils/season.js` instead of the literal 2026.
  Routes fixed on the way (also in R9-033/R9-034): Team Comparison's recent form and head-to-head read finals with a stored
  score only (a LeagueGameFinder row whose final isn't stored gave a None margin; R9-028), the composite leaderboard's
  default season meets its floor, Learn the Game's sample season is the newest complete one, Luck & Schedule's model
  chart and Best Games' calibration / favourites pool complete seasons only.

### R9-037 · The first live run would have broken `paper-inputs`
- **Severity:** broken (paper stage) · **Step:** 5 · **Status:** fixed in the round 9 step 5 commit
- **Where:** `daily_update.py`'s shot step adds a `player_shots_cache_status` row for each new player whose shots it
  stores (rookies; the shot pages need it while `ENABLE_LIVE_SHOT_FETCH` is off). The table has no season dimension, so the
  manifest counted it whole: after the first live run `paper_manifest.stale_reasons()` reported "2,842 rows in the
  manifest, 2,901 now" and `paper_numbers.py` stopped on its manifest claim (found on the live-season copy, where the
  preseason run added 59). **Fix:** `paper_freeze.EXPLICIT_PREDICATES['player_shots_cache_status']` = the rows that
  existed at the freeze (`updated_at < 2026-10-07`; all 2,842 are from 2026-09-25, and the fetch never updates an
  existing row); the content hash and row count under it equal today's, so the digest and every macro are unchanged:
  `rebuild_all.sh paper-inputs` reran with `numbers.tex` byte-identical; `manifest.json` records the predicate
  (`capped_tables` 132 → 133) and `SHA256SUMS` its two manifest lines. `docs/LIVE_SEASON.md`'s table says "updated_at,
  capped" for it (`live_season.py --check` passes). The fake-row test covers it (a timestamp a year later is excluded).

## Step 6: the Forecast Ledger in the daily run, the weekly report, the paper's forward test (2026-10-07)

How it was checked (no 2026-27 game has been played yet, so on a simulated season): `api/tests/test_weekly_report.py`
(4 tests, ~25 s, local database only) runs the real `ledger_update.py` once a day at 12:00 ET from 2026-10-20 to
2026-11-09 on copies of the locked tables in schema `zz_weekly_report` (test_ledger_gameday's fake ESPN and clock; every
earlier game final, scores from a seeded stream): three weeks, 92 games through 2026-11-01 and 146 through 11-08 (the
run of 11-09 stores the paired tests). The weekly report for 11-02..11-08 equals `ledger_live`'s own scoring, the
stored tests and the morning-after standings; the route and the Markdown file give the same report; the paper's
ledger section emits the forward-test macros at an as-of with tests and refuses one without; the daily update's ledger
step runs and skips when it should. The app's Weekly report tab was checked against a scratch copy with the same three
weeks (backend with `PGOPTIONS`; schema dropped after) and the real database (empty state), page scan at 1280/375 x
Paper/Ink: clean. `ledger_update.py` itself is unchanged; `daily_update.py --ledger-only --dry-run --date 2026-10-21`
ran it against ESPN (35 s: opening night's nine odds rows, 10-21 waiting for 10-20's finals, nothing written).

### R9-038 · The paper's forward-test claim would have stopped `paper-inputs` on opening night
- **Severity:** broken (paper stage, from 2026-10-21) + design · **Step:** 6 (pin), later (the sentence) · **Status:** open
  (the pin fixed in the round 9 step 6 commit; the sentence waits for a run date with stored tests)
- **Where:** `paper_numbers.ledger()` counted every game the ledger had scored (`LgScored`) and claimed it was 0, on
  purpose, so the first scored game would stop `rebuild_all.sh paper-inputs` and force the rewrite of "As of
  \pnLgAsOf{} no game of the season has been played". But a rewrite from `ledger_tests` isn't possible until 100 games
  are scored under every version (the ledger's MIN_TEST_GAMES; the run of 2026-11-03 is the first, by the schedule), so
  `paper-inputs` would have been red for two weeks, and after a rewrite every nightly run would change the printed
  numbers again (the paper must stay byte-identical through the round). **Decided:** the paper reads the ledger as of one
  explicit run date, `api/paper_freeze.py` `LEDGER_AS_OF` (`None` = the lock date: `LgScored` counts the games dated
  before it, 0, and `numbers.tex` is byte-identical, checked); `paper_numbers.py` prints a note (not a failure) once games
  are scored. Setting a date makes the section emit `\pnLgFw*` (games, through date, each version's Brier and log loss,
  every pair's difference with its interval and p from that morning's `ledger_tests`), each checked against the logged
  odds, and claim that the date has stored tests covering exactly the games before it. **Left:** choosing the date and
  rewriting the sentence in all three lengths (docs/LEDGER_RUNBOOK.md, "Updating the paper"): from 2026-11-03 at the
  earliest, owner's call when (round 9 step 7 or 8, or a later chat).

### R9-039 · The weekly report's first two weeks have no paired test
- **Severity:** design (recorded) · **Step:** 6 · **Status:** decided
- **Where:** `ledger_tests` starts at 100 games scored under every version (a judgment call of round 6 step 2, part of the
  ledger's documented rules). By the schedule that is 43 games after opening week and 92 after the second; the first
  report with paired tests is the third (2026-11-02..08, 146 games). **Decided:** the report doesn't compute its own paired
  tests before then (that would be a second rule beside the ledger's); it shows each version's Brier and log loss with a
  95% interval on its own (games resampled, 2,000 times, seeded by season, week and version) and says "None yet: the
  ledger stores paired tests from 100 games (N so far)".

### R9-040 · "Rewrite the paper's sentence when the claim fails" depends on paper-inputs being run that day
- **Severity:** design · **Step:** 6 · **Status:** decided (see R9-038)
- **Where:** the plan's trigger for the paper's only round-9 change. With the as-of pin nothing fails any more; the
  trigger is now a person: `paper_numbers.py` prints `note: the Forecast Ledger has scored N games ...` on every run once
  games are scored, the weekly report shows when tests start, and the runbook lists the three steps.

### R9-041 · The ledger runbook's "things that change because of a run" was stale
- **Severity:** looks wrong (docs) · **Step:** 6 · **Status:** fixed in the round 9 step 6 commit
- **Where:** `docs/LEDGER_RUNBOOK.md` still said each run changes `\pnLgAsOf` and `\pnManDigest` (true before round 9
  step 1, which pinned the first and took the live tables out of the digest) and that the claim `LgScored == 0` stops
  `paper-inputs` (R9-038). Rewritten; the runbook also gained the daily update's ledger step, the weekly report and
  "Updating the paper".
