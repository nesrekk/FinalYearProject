# CLAUDE.md — session handoff for NBA Hub

Auto-loaded by every new chat: only the rules and the current focus (keep it under 30 KB). **Keep it current:** when a session ships something or makes a real decision, in the same commit as the README, update "Where we are" and "Open items", add any new standing rule to "Rules that bite" (one line) and put the step's full record at the top of `docs/HISTORY.md`.

## Read first
- `README.md` is the full, authoritative handoff doc (features, schema, conventions, known gaps, roadmap with dated "Just shipped" entries). This file is the short version plus things learned the hard way.
- `docs/HISTORY.md` holds the round-by-round history (every step's record, newest first): search it before re-deriving anything. Round plans: `paper/ROUND*_PLAN.md` (untracked; the owner pastes one step per chat). Issue lists: `docs/qa/ROUND*_ISSUES.md`.
- **The Obsidian vault is the chats' shared working memory** (owner's decision, 2026-10-07): `~/Desktop/Second Brain/FInalYearProject/` (folder spelled with a capital I). Any chat may read and write any note.
  - Start of a chat: read `Home.md` → `Status.md` → `Owner-ToDo.md` → the newest notes in `Guide/` (the guide's feedback shapes plans) → the recent rows of `Decisions.md`.
  - During: add a `Decisions.md` row (date, decision, why, who) for any lasting choice; update `Owner-ToDo.md`; new unplanned ideas go to `Ideas/`.
  - End of a chat that commits: put the step's "For the guide" block in `Guide/_Next-meeting.md` and run `python3 scripts/vault_status.py` (the 3.14 Python below; it rewrites `Status.md`: never hand-edit that file).
  - Never copy repo content into the vault (link the path), never put secrets there, use absolute dates. The repo (README, this file) stays the truth for what's built. The old vault is in `Archive/` (hidden; don't update it).

## Where we are (2026-10-07)
- Rounds 1-8 and 8.5 are done (each step's record in `docs/HISTORY.md`), except round 8 step 9 (usability fixes), which waits for the owner's 5-8 study sessions (`docs/USABILITY_STUDY.md`).
- Round 9, the live 2026-27 season (`paper/ROUND9_PLAN.md`): steps 1-6 and 8a are done. 9-7 (one real week of daily runs) starts on or after 2026-10-27; 9-8b (final close-out, in-season sync rhythm) is on the owner's to-do after it.
- Round 10, the live game companion + ask in English everywhere (`paper/ROUND10_PLAN.md`, whose order table pairs the steps): **10-6 done** (2026-10-09, `api/ask_sentences.json`, commit `c0e2d50`: 150 sentences, 60 dev / 90 test, committed alone before any prompt; R10-009 to R10-011). 10-1 ran beside it in another chat (its own commit says where it stands). Next: 10-7 (the intent engine, CLI) after 10-6; 10-2, and 10-3 if chosen, before 2026-10-20.
- The paper is frozen on 2025-26 (`api/paper_freeze.py`); three lengths, venue not chosen yet.
- **Daily routine from opening night (2026-10-20):** after the night's games (~14:00 IST): `cd scripts && DB_TARGET=local /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 daily_update.py` (ledger, fetch, `--season 2027` rebuilds, the eight models; a `daily_update_runs` row), then restart impact_api and mvp_api; Mondays `weekly_report.py` + commit `docs/weekly/<Sunday>.md`. Runbook `docs/LEDGER_RUNBOOK.md`; no scheduled job without the owner's OK.
- Tests: **642** (641 pass + 1 skipped until the first game is logged; full run on 2026-10-07 in round 9 step 8a, exit 0): `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests`. Frontend lint clean (`cd frontend && npx eslint src` exits 0); keep it that way.
- Local Postgres (`nba_analytics`, ~3.9 GB, 219 tables on 2026-10-07) is the source of truth.

## Rules that bite
One line each; search `docs/HISTORY.md` for the record behind one.

**Data sources and the parser**
- `scripts/pbp_lineups.py` is the one lineup parser (`Game.walk()` drives `run()` for the lines and `stints()`): never write a second one. Its optional arguments (`Game(..., miss_threes=, clock=)`, `stints(split_at=)`, the loaders' `season=` / `through=` / `game_ids=`) leave the default output byte-identical.
- Pass `miss_threes=pbp_lineups.miss_three_calls(...)` in any script that counts 3PA from the parser (ESPN's text calls thousands of charted missed threes twos).
- Free throws are credited to the five on the floor at the foul (`is_foul_anchor()`): a reader that maps free throws to stints by action range must move the ones in `lineup_stints.foul_ft_actions` (as `paper_xrapm.map_to_stints()` does).
- Event → stint: `e.action_number BETWEEN s.action_from AND s.action_to` within the game. Use `tracked_ok` stints and disclose `lineup_stint_games` exclusions. `Game.parse()` needs `game.home` set first (`walk()` does it).
- Keep the builds' stop checks (`build_lineup_stints.py`: lines = stint sums; `build_player_game_onfloor.py`: replay = stints).
- Read the game clock only from `pbp_event_clock` (scripts: `load_espn(conn, clock=True)` + `game_clock(ev)` → `Game(..., clock=)` / `possessions(..., clock=)`; SQL: `JOIN pbp_event_clock c ON c.event_id = e.id`). ESPN's own clock is late by event type. Stints/minutes and Guess the Game stay on ESPN's clock on purpose.
- Never match ESPN and NBA.com shots on the clock (they differ by up to ~26 s): `pbp_lineups.match_coordinates()` / `chart_matches()` match by order within game, shooter and period. Distance comes from `player_shots` coordinates, never `shot_distance` (0 on 11-17% of threes); take NBA.com's 2/3 call where matched.
- Possessions come from `pbp_possessions.possessions()`, timing inside one from the stored times / `corrected_clock()`; turnover times can't be recovered.
- ESPN's timeout times are late about 1 in 5: order timeouts by the log, not the clock. ESPN rewrites the log after an overturned challenge.
- ESPN's play-by-play includes the three NBA Cup finals (no `game_scores` link): `player_game_lines` keeps them; anything season-level built on the lines drops them (the Game Log's join on `team_game_fatigue` does).
- Since 2025-26 a missed end-of-quarter heave (`action_type = 'Heave Jump Shot'`) is a team attempt; the parser charges it to the shooter (R8-088 open; R9-007: the treatment stays).
- Per-game margin: `game_scores` (join on game_id + team_abbreviation), never `team_game_fatigue.plus_minus`. ESPN id ↔ NBA id: `game_scores.espn_id`. Per-game on-court +/-: `player_game_onfloor` (game_ok rows), never the lines' `tm_pts - op_pts`.
- On/off team totals come from the play-by-play with the lines' credit rules (`team_game_totals`), never `game_team_box`. Its `pts` stays the pbp's last score (data quality compares it with `game_scores`): don't "fix" it.
- Team codes are Basketball-Reference's before 2009-10 and NBA's after: resolve them through `api/teams_lib.py` (`app_abbr`, `FRANCHISES`), never ad hoc. The team shown on a player-season row comes from `api/season_team.py` (`player_season_stats`' wrong team on 3-5 rows a season stays on purpose).
- Ages: `player_bio.birth_date` (age on Feb 1). `player_season_stats.age` mixes two conventions; the award/similarity models were trained on it, so don't "fix" it without retraining them.
- Names to ids: `impact_core.find_player()` / `resolve_player` (latest career of the exact name; 19 names belong to two players); pass `player_id` from anything that knows it. Basketball-Reference ↔ NBA ids only through `bref_nba_ids.resolve`.
- Rerun `repair_espn_player_ids.py` after any play-by-play re-fetch (it should print "nothing to fix").
- Postseason truth: `season_postseason`, not `team_seasons.playoffs`. `team_seasons.attend_g` before 1980-81 is junk where `attend` is NULL.
- Dates are US Eastern; ESPN stamps UTC (`fetch_postseason_games.local_date()`; the frontend's "today" is `utils/date.js` `nbaDateIso()`). ESPN standings need `seasontype=2`.
- stats.nba.com answers through `nba_api` with its own headers (plain curl times out: a curl check says "down"); BoxScoreSummaryV2 is empty from 2025-04-10: use V3. Live pages read `api/espn_live.py` first; every live call has a 3 s timeout; a failed live fetch returns None and isn't cached.
- `ENABLE_LIVE_SHOT_FETCH` stays off (with it on, a shot-chart GET writes `player_shots` / `league_shot_zones`); `prewarm_shots.py` turns it on for itself.
- `fetch_season_shots.py --apply` keeps every unchanged shot's id; stored conventions: `shot_zone_basic` NULL, `shot_distance` = coordinate distance rounded half-up.
- BPM/VORP in `player_season_stats` are Basketball-Reference's (`load_bref_bpm_vorp.py`, run after `build_bpm_vorp.py`).
- Never train the March Madness model on `college_team_seasons` (those Torvik ratings include the tournament). Keep `player_clusters` (Trivia hard-codes its six names). Never compare raw clutch and non-clutch WPA per play (3.7× the leverage). `player_rapm.rapm` is float4: compare it in SQL via `::text::float8`.
- Facts are never added from memory: read the page that day and keep its URL and date beside the fact (known-facts tests, Greats trivia in `SOURCED`, paper references). Basketball-Reference answers 403; ESPN, Wikipedia, NBA.com, Crossref and OpenAlex work.

**Reproducible builds, rerun orders, restarts**
- A fit on `player_shots` reads `ORDER BY id`; an order-dependent read of `player_game_lines` (bootstraps resample by position) needs a full tiebreaker (`game_id` last). Sum float columns as numeric in any reproducible SQL aggregate.
- Shared modules, never fork: `pbp_lineups`, `pbp_possessions`, `rating_tracker_lib`, `shot_value_lib` (scripts/); `season_sim_lib`, `luck_lib`, the other `api/*_lib.py`, `hot_streaks` (the shuffle engine `paper_beliefs` imports), `situational_splits`, `stat_samples`, `best_games`, `play_finder`, `shot_hex`, `current_season` (api/); `paper_beliefs`' test machinery (coaching imports it).
- `scripts/rebuild_all.sh` holds every pipeline in dependency order (no argument prints the plan; `--dry-run`, `--from`, `--offline`). Downstream of `player_shots` = the transitive closure of `test_reproducibility.reads()`.
- After new play-by-play, a parser change or a `player_shots` reload: `rebuild_all.sh derived --from build_event_clock.py` (event clock first: its chart matching uses the parser's names; then lines → team totals → stints → on-floor → on/off → possessions → … as in `daily_update.REBUILD_STEPS`).
- Then for the paper: `build_shot_value.py` → `paper_xrapm.py` → `paper_eval.py --only impact` → `paper_tests.py` → `rebuild_all.sh paper-inputs`; `paper_data_audit.py` → `build_data_quality.py`; `build_report_card.py` after `paper_eval.py`.
- Awards after a season load or retraining: `build_first_nba_season.py` → `build_dpoy_roy_models.py` → `backtest_models.py` → `build_shap_explanations.py` → `calibrate_award_chances.py` → restart mvp_api. The UI shows `*_chance`, never the raw `*_probability`.
- `player_id_map`: `load_kaggle_historical_seasons.py` → `load_draft_history_bref.py` → `load_salaries.py` → `build_contract_value.py`, then `build_player_profile_data.py`.
- Restart impact_api after any rebuild (routers lru-cache their tables; a backend started before a new router was committed 404s on it); restart mvp_api after award or daily model runs, the similarity service after reloading `player_season_stats`.
- Not a change on a rerun: last-digit moves in `build_season_sim.py`, the tracker fit and Luck & Schedule (compare to 1e-9); role flips of borderline players (K-means over all seasons).

**Paper, reproducibility, the freeze**
- Never commit `paper/` (untracked on purpose). Before editing the paper, copy it to `paper/versions/nba_hub_paper_<date>_<step>.tex`.
- The paper types no measured number by hand: each is a `\pn` macro from `scripts/paper_numbers.py` (half-up on the stored value), and each data-dependent sentence is an `N.claim(...)`. Definitional constants may be typed.
- After any rebuild: `scripts/rebuild_all.sh paper-inputs` (~1 min, read-only: manifest → `paper_numbers.py --check` → figures → `SHA256SUMS`).
- Every table must be in `paper_manifest.PRODUCERS` and have a `step` line in `rebuild_all.sh` (`test_reproducibility.py` checks the order). An appended-on-a-schedule table goes in `paper_manifest.LIVE`; a table a daily step writes without a season column needs a predicate in `paper_freeze.EXPLICIT_PREDICATES`.
- The freeze (`api/paper_freeze.py`, `MAX_PAPER_SEASON = 2026`): a paper-stage script (paper / paper-inputs stages + `api/data_quality_lib.py`) names a season-bearing table only through `F(table)`, and the shared loaders it calls take `through=` (`test_paper_frozen.py` checks statically and dynamically).
- Every pooled fit stays fitted on seasons ≤ `MAX_PAPER_SEASON`; the live season only applies it, and fit rows are never written in season (R9-009). Only a table with a season dimension may change during the season (`scripts/live_season.py --check` against `docs/LIVE_SEASON.md`).
- The forward-test sentence: set `paper_freeze.LEDGER_AS_OF` to a run date with tests (from 2026-11-03), rerun paper-inputs, rewrite the sentence in all three lengths (`docs/LEDGER_RUNBOOK.md`, "Updating the paper"); when is the owner's call.
- Protocol: tune 2020-21 to 2023-24, validate 2024-25, test 2025-26; nothing is chosen on the test season (never re-pick settings after seeing it).
- Three lengths (`paper/nba_hub_paper.tex`, `_8p`, `_6p`), each with `\ifanon`: `scripts/paper_build.sh [long|8p|6p]` (tectonic); `paper_numbers.py --check --paper <file>` for a short one. Every float `[!t]`; keep shared table cells short.
- References: each `paper/refs.bib` entry carries a `% verified:` line; run `scripts/paper_refs_check.py --online` after editing one.
- New figure: a function in `paper_figures.FIGURES`, `C.expect` on every printed number, a `C.claim` per caption sentence. `Fig.~\ref{fig:streaks}` and `figures/fig_forest.pdf` appear exactly once in the .tex (a test edits them).
- The Rating Tracker conditions on BPM, never scores it; `paper_eval` refuses `--quick` fits. The held-out xRAPM task trains on `fold_xpts`.
- Kept until the next deliberate paper rebuild (dropping them changes the digest): `pair_synergy_validation` + `train_pair_synergy.py`, `dbpm_repro`. `paper_ablation_predictions` has no primary key on purpose.
- A double-blind artifact is built from an allowlist, never the repo (`docs/REPRODUCIBILITY.md`).

**Live season, daily update, ledger**
- `--season N` mode (`scripts/season_mode.py`; `daily_update.REBUILD_STEPS`, `MODEL_STEPS`) deletes that season's rows and inserts them through the full build's own code (no DDL, no index, no second implementation), never touching the `*_meta` check tables or `leverage_index_grid`. On/off, rim and the splits share one seeded bootstrap stream, so they compute every season and write only N's; `stint_id` / Play Finder's `game_no` continue via `next_id`. A new chain build needs all this plus a case in `test_season_rebuild.py` / `test_season_models.py`.
- The daily fetch reuses the existing fetchers (`fetch_season_shots`, `build_schedule_fatigue.fetch_season_games`, `fetch_game_scores._match`, `compute_impact_score.impact_z`, `repair_espn_player_ids`, `fetch_referee_officials`): extend them, don't copy them. Live play-by-play = ESPN's game summary (`scripts/espn_summary.py`); season stats load before play-by-play (rookies' ids).
- The ledger is locked (2026-09-30, SHA-256 `c1a48402…3d17`, tag `ledger-2026-27` = c9071a8, on GitHub): never move the tag. `ledger_update.py` imports `ledger_lib` / `season_sim_lib` / `luck_lib` from the tag (`load_frozen()`); `api/ledger_live.py` never imports the model files. A relock (`--lock --relock`) works only before the first tip and makes the emailed hash stale.
- The default season: `api/current_season.py` (`default_season_for()`, `latest_season_in()`: a route defaults to the newest season its own table has, never `MAX(season)` of `player_season_stats`); frontend `utils/season.js`, read at render time, never into a module constant; no season literal in a component (a test greps).
- A test that means the complete seasons pins `season <= 2026` and draws its seeded samples from complete seasons (R9-036).
- Test against copies in a `zz_` schema + `PGOPTIONS='-c search_path=zz_x,public'` (patterns: `test_ledger_gameday.py`, `test_daily_update._make_schema` + `test_season_rebuild.copy_table`); drop it after. Only `--season` modes are safe that way: a full build drops the public tables it doesn't find there (R9-027). Importing `impact_core` that way creates empty `league_shot_zones` / `player_shots_cache_status` shadows: copy them first. No views or DDL on shared tables in tests.
- Never hash a big table with an anti-join (`NOT (id IN (...))` ran 30+ min): subtract summed-halves hashes.
- Early-season gaps, not bugs: no 2027 `situational_split_league` row until players reach 10 games a side; `player_on_off_seasons.league_net_on_weighted` NULL until a game reconciles.

**Workbench and the stat catalogue**
- Add or change a stat in `api/workbench_catalogue.py`, never as a literal in a router (the Leaderboard's and Game Finder's `STATS` are built from it; `test_workbench.py` freezes the old literals). A new stat also needs `api/stat_samples.py` + the stability script's `CATALOGUE`; a hot-streak stat `hot_streaks.STATS` + `PRIOR_SQL`; Stat Line Finder's `LINE_STATS` mirrors the Leaderboard. Season Similarity's `_season_matrix()` must match `precompute_league_similarity.py`.
- Workbench SQL comes only from catalogue text with bound values, via `execute_readonly()` (read-only, 8 s timeout, rolled back). The Finder compiles only through `workbench.py`'s helpers (`_value_sql`, `_num_condition`, …); `workbench_finder.py`'s docstring is its semantics.
- `_model_join()` renames columns because `stat_samples.SEASON_SAMPLE_SQL` uses unqualified names. `ORDER BY a, b DESC` sorts only b descending: put DESC on every key.
- `components/workbench/PlotChart.jsx` is the only file that imports `@observablehq/plot`.
- Boards: `utils/workbenchStore.js` `updateBoard()` re-reads inside the transaction; anything from a file or link goes through `cleanBoard()`; blocks read members from their set. A new block type: `BLOCK_INFO`, `BLOCK_TYPES`, `cleanSettings()`, `renderBody()`; `BlockStatus.jsx` is every block's loading/error line.
- After editing `utils/starterBoards.json` run `test_workbench_starters.py`; starter notes state no measured number.
- English parse: `GEMINI_API_KEY` in `api/.env` (a test greps commits for it); the model's answer is untrusted (`to_spec()`); no `maxItems` on nested arrays with enums (Gemini refuses); never edit `api/workbench_parse_sentences.json` or `api/ask_sentences.json` to suit an answer (add dated sentences instead); eval `--split dev` while tuning, `--split test --runs 3 --write` once. `ask_sentences.json` carries its own scoring rules and season tokens (`current` / `last` / `current-N`, `yesterday`) that an eval resolves through `current_season.status()` on its day (R10-009).
- `utils/studyTasks.json` is frozen once a real usability session runs; `studyEvent` detail fields can't be named `t` or `type`.
- In this `C`-locale DB `lower()` folds only ASCII (entity search translates accents itself).

**Frontend, tokens, charts**
- Colours are tokens in `styles/tokens.css` (Paper / Ink), never raw hex: orange text `--brand-text`; chart marks `--chart-brand` (never `fill`/`stroke: var(--brand)` on a chart; a test greps); series `--series-0..9` / `--wb-series-N`, `--positive` / `--negative`. No opacity fades on text. Charts take colour through CSS variables/classes (export and the Report snapshot rely on it).
- One number formatter: `utils/format.js` (`signed`, `bySign`, `withSign`, `plain`, `seasonLabel`); never a new local `signed()`.
- No page shows placeholder or mock data.
- Navigate only through `onNavigate(page, hash, params)` / `pushPage`; tool inputs via `utils/useUrlState.js` (`useInitialParams`, `parseParam`, `useUrlSync`) + `CopyLinkButton` + `SaveViewButton`; `openFullUrl()` replays a URL exactly, `openPage()` rebuilds it.
- Reuse the common pieces: `PlayerName` (link + watchlist star), `TeamLink` (not bare `TeamLogo`), `SeasonSelect`, `TableExport` (not on Guess the Player), `ChartExport` with a meaningful `name` (it carries the Report button), `usePlayerSuggestions` + `NamesakeNote`, `LiveSeasonNote` / `LiveSeasonTag` on live pages; the Analytics tab list is `components/analytics/analyticsTabs.js`.
- A new per-player block goes in `/player-profile/{id}` with a "Not on file" reason in `missingReasons()` (`components/pages/playerProfileShared.js`).
- Fixed-position overlays inside pages are portalled to `<body>`. Stored or imported SVG is shown only through `<img>`. `watchlist.js` returns the same array on no-op reads.
- Methodology: update a model's card in the same commit as the model or its validation; remove an `OPEN_ISSUES` entry in the commit that fixes it; written numbers carry `CHECKED_ON`.
- Disclosures travel with reused numbers: shot-making's `not_on_file`, Pair Chemistry's coverage, the availability odds' "upper bound".
- Charts stay hand-built SVG (Bklit was removed); for a hard chart type install just that component.
- ESLint here misses an undefined JSX component (a removed import): check imports by hand.

**Layerbase and sync**
- Every write to Layerbase needs the owner's OK in that chat. `migrate_to_layerbase.py` takes exactly one mode (`--check`, `--tables a,b`, `--all`, `--drop-local-only`, `--reindex`); `--reindex` after every copy. Find what changed with `paper_manifest.py --compare DIR` (content hashes, not row counts).
- `api/local_only.py` `LOCAL_ONLY` tables are never copied; grep `api/` and `frontend/src` before adding one.
- Layerbase's pooler refuses startup options (`PGOPTIONS` fails) and can pass one client's `SET` to the next: set what you rely on after connecting. Compare hashes with `SET timezone = 'UTC'` on both sides (local runs Asia/Kolkata).
- Layerbase tests run in batches; after an SSL drop or a read-only spell, rerun the failed tests alone before suspecting the data. `test_every_verified_column_runs` (8 s timeout) and `test_play_finder_season_filter_is_fast` (5 s budget) fail there: a slower server, not the data. Don't serve the Workbench from it (~8× slower). **Logins timing out (TCP open, no handshake) = the free tier hibernated (dashboard: Wake) and/or a VPN is on**: wake it, VPN off, then retry.

**Testing and verification in the hidden browser pane**
- The hidden pane never advances framer-motion or IntersectionObserver: use the harness `frontend/zz-harness.html` described in `frontend/qa/page_scan.js`'s header (`MotionGlobalConfig.skipAnimations = true` before importing `/src/main.jsx`, IntersectionObserver stub, rAF shim, `?zzdb=1`). Delete it before committing.
- In browser-tool calls use `window.qa` and import with `?t=Date.now()`; a call gets ~45 s, so start sweeps without awaiting and poll `window.__zzResults`; match button `textContent` case-insensitively (CSS uppercases `innerText`).
- `navigate` to the URL the tab already shows doesn't reload: add `&t=N`. A restarted backend needs a few seconds to import `impact_core`.
- Before timing a page, clear localStorage `nbahub_cache*`; time routes with the backends restarted; compare old code with `git stash push -- api/`.
- Route crawl: `./start.sh`, then `scripts/qa_route_crawl.py` (`--skip-quota` leaves out the paid routes).
- Browser check on a simulated season: the test's fixture in a kept schema + impact_api with `PGOPTIONS='-c search_path=<schema>,public'` (temporary launch config); remove both after.
- Issue lists: every problem is one numbered entry in `docs/qa/ROUND*_ISSUES.md`; never delete one, mark it fixed or won't fix; read the list first, update it in the step's commit. Page checks are pinned in `api/tests/test_round8_pages.py`.

**Machine**
- Fanless M5 Air (16 GB): one heavy job at a time (builds, fits, full suites); `OMP_NUM_THREADS=4` on heavy fits; never hold two big tables (`player_shots`, `pbp_events`, `play_finder_events`) in memory at once. Stop any dev server you started (`preview_stop`). Drop `zz_*` snapshot tables after a diff.
- Never use `scipy.linalg.cho_factor` / `scipy.linalg.cholesky` on this Mac (memory corruption in Accelerate): call `scipy.linalg.lapack.dpotrf` / `dpotrs` / `dpotri` as `rating_tracker_lib.py` does. A bus error or an "impossible" assertion in a long numeric run: suspect this first and distrust every table that run touched.
- When two chats share the checkout, run long chains from a git worktree (symlink `api/.env` and `paper/` in; `npm install` in its `frontend/`).

## Open items
- **Layerbase mirror in sync** as of 2026-10-07, after round 9 step 8a: 207 of 207 shared tables content-hash identical (digest `fa40bd022bc99631` both sides); **3,488 MB, 1,512 MB free of the 5 GB tier**. Its tests (642, four batches): 566 passed, 74 skipped, 2 failed, both Layerbase-only slowness (`test_every_verified_column_runs`; `test_play_finder_season_filter_is_fast`, 12 s vs its 5 s budget); five SSL-drop failures passed rerun alone (sync history in `docs/HISTORY.md`).
- **Not on Layerbase from opening night (2026-10-20):** every 2026-27 row `daily_update.py` writes. Sync weekly at most, owner's OK each time, `--reindex` after.
- Open issues (details in the issue lists): R8-010, R8-082, R8-088 (heaves; owner's call), R9-012 (officials backfill at season end; owner's call), R9-024, R9-028, R9-035, R9-038.
- `Rapm.jsx` still has a local `signed()` (its fix, 38cb542 on branch `claude/gallant-newton-49b1e2`, isn't on main). `draft_prospects.py` still takes MIN(season) as the rookie year.
- **B5 — Ask the Database:** parked on purpose (costs Anthropic API money; needs owner buy-in).
- Minor known issue: `/games/by-date` can still be slow for old historical dates (README Known real gaps).

## How the owner works
- Short messages ("next", "continue", "yea") = pick the next roadmap item and build it **end to end**: check the real data first → pipeline script → Postgres table → endpoint (+ `_source` badge) → `services/api.js` → UI → browser check → smoke test → README update → commit → push. Then report back and wait.
- Owner is a final-year student, hands-off on implementation, wants terse, action-oriented updates and honest disclosure. They asked for "no mistakes": verify claims against real data before writing them anywhere (README numbers, commit messages, UI copy).
- **Never** start money-costing items (B5) or sensitivity-flagged items without an explicit OK in *that* chat. Anything that writes to an external service (Supabase/Layerbase sync, GitHub PRs beyond normal pushes) needs the owner's OK first.
- Commits: one per feature, detailed message on what's real and what was verified; use the attribution line the current session's system instructions give. Grep changed files for secret values from `api/.env` before staging; `api/.env` is gitignored and must stay that way.

## Environment gotchas
- **This machine:** MacBook Air M5, 16 GB, 512 GB SSD, no fan (since 2026-10-02; backup bundle + `RESTORE.md` in `~/Desktop/NBA_Hub_Migration_2026-10-01/`); heavy-job rules under Rules that bite → Machine. Iterate on the tests you need, the full suite once before committing; ask before downloads over a few GB.
- **Local Postgres is Homebrew's `postgresql@18`** (binaries `/opt/homebrew/opt/postgresql@18/bin`, not on PATH; data `/opt/homebrew/var/postgresql@18`; role/password from `api/.env`). `brew services run postgresql@18` (or `start` / `stop`). "Connection refused" = the server is stopped: start it, don't debug the data. **The database must use the `C` locale** (`createdb -T template0 -E UTF8 --locale=C nba_analytics`), like Layerbase (it changes sort order and the manifest's hashes).
- Always use `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3` (plain `python3` is an Anaconda without the deps).
- Whole app: `./start.sh` (Postgres if stopped, 3 backends + frontend; Ctrl+C stops all). Or `cd api` then `uvicorn mvp_api:app --port 8000`, `similarity_api:app --port 8001`, `impact_api:app --port 8002` (always pass `--port`). Frontend: `preview_start` name `nba-frontend` (`autoPort`: read the port from the result); `impact-api` restarts impact_api.
- `DB_TARGET` in `api/.env`: `local` (default), `layerbase` (the mirror, all but LOCAL_ONLY) or `cloud` (Supabase, no `player_shots`/`pbp_events`); an env var overrides it for one command (`DB_TARGET=layerbase python3 -c ...`).
- Gitignored local-only data: `nba_data/kaggle_1947_present/` (Basketball-Reference-derived export), `nba_data/salaries/` (no license; README Known real gaps says where to get them), `live_data/<season>/` (the daily update's caches).
- Season ints are **end year** (`2026` = 2025-26). Label: `f"{season - 1}-{str(season)[-2:]}"`.
- `player_shots` mixes regular season (`game_id` `002…`), playoffs (`004…`) and play-in (`005…`); `shot_zone_basic` is NULL on bulk-loaded rows, so zones come from `api/shots_lib.classify_zone()` (fixed to real court geometry 2026-09-26).
- `pbp_games` has nba_api/ESPN twin games — use `scripts/wpa_lib.PBP_DEDUP_WHERE` when reading play-by-play.
- No `statsmodels` here; clustered-SE regression lives in `scripts/stats_lib.py`.

## Verification pitfalls
- **Several chats may share this working tree and its git index.** Re-read a shared file (`App.jsx`, `navConfig.js`, `api.js`, `theme.css`, `impact_api.py`, `test_smoke.py`, README, this file) before editing it. Right before committing, re-check `git log --oneline -1` and that `git diff --cached` shows only your hunks (else build the file from `git show HEAD:path` + your edits and stage it with `git hash-object -w` + `git update-index --cacheinfo`); never `git add -A`. Finish each edit of a file another chat may be running in one write (half-written imports have crashed pipelines).
- **Check pytest's own exit code** (`pytest ... > out.txt; echo $?`). Piping into `tail` hides failures, and a commit once went out that way.
- Set a viewport with `resize_window` before loading a page (a 0-size viewport used to blank the app).
- If an endpoint that's fast via curl hangs in the browser, check `read_network_requests` for a pile of pending app-shell calls first.
- Sniff-test against real, well-known numbers before shipping: several source datasets had errors found only that way.
- A build whose log the daily update keeps filters pandas' harmless per-query warning (`warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")`).
- `pg_restore -t <table>` (dump: `~/nba_backups/nba_analytics_pre85c_2026-10-06.dump`) restores rows, not constraints: re-add the primary key, then check `paper_manifest.content_hash` against `paper/manifest.json`.
