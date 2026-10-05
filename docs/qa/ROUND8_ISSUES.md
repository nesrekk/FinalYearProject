# Round 8 issue list

Round 8 is a test-and-fix round (no new features). This file is its spine: every problem found gets one
numbered entry here, and every later step reads it first and updates it in its own commit. **Never delete
an entry; mark it.**

- **Severity:** `broken` (a page or route doesn't work) · `wrong number` (a value is wrong, stale or at
  risk of being wrong) · `slow` · `looks wrong` (wording, labels, missing badge, placeholder text).
- **Status:** `open` · `fixed in <commit>` · `won't fix` + why.
- **Step:** the round-8 step expected to take it (2a/2b/2c page sweeps, 3 consistency, 4 live pages,
  5 ids / +/- / teams, 6a-6c the play-by-play rebuild (owner's OK), 7 smaller issues, 8 speed,
  9 usability, 10 close-out).
- A fix needs a test where one can be written, and says old → new for any number it moves.

Counts (2026-10-05, after Step 1): **32 entries**, 32 open: 3 broken, 15 wrong number, 4 slow,
10 looks wrong. 17 are new from Step 1 (R8-001 to R8-017); 15 are known gaps already written down in
README "Known real gaps" / Methodology open issues, listed so a step owns each (R8-018 to R8-032).

## Step 1 health check (2026-10-05)

| Check | Result |
|---|---|
| `pytest api/tests` | **409 passed, 1 skipped, exit 0, 156 s** (skipped: `test_ledger_live.py`, nothing logged before 2026-10-20). Slowest: `test_impact_with_without_star` 41.0 s and `test_impact_pair_synergy` 18.8 s (both live stats.nba.com, R8-008), `test_every_verified_column_runs` 10.6 s, `test_news_current_shape` 6.3 s (R8-011). 43 warnings, all pandas' "only supports SQLAlchemy" notice from `pd.read_sql` on a psycopg2 connection (`coaching.py`, `ledger_live.py`, ...) plus two Starlette deprecations; not user-facing. |
| `npx eslint src` | exit 0 (8.9 s) |
| `npx vite build` | passes (0.5 s, Vite 8 / rolldown). 92 JS chunks, 3.06 MB of assets. **First load 558 kB raw / 166 kB gzip**: `index` 332 kB, `proxy` (framer-motion) 123 kB, CSS 99 kB, five small shared chunks. Largest lazy chunks: `ShotCourtFlight` 532 kB (three.js, landing; the only >500 kB warning, R8-013), `AnalyticsSection` 333 kB, `PlotChart` 300 kB (Workbench), `Methodology` 139 kB, `Workbench` 125 kB. |
| `scripts/rebuild_all.sh --dry-run` (and per stage) | exit 0 every stage; plan = 107 steps (fetch 19, load 12, derived 61, paper 10, paper-inputs 5); every gitignored input present (Kaggle export, shot CSVs, salaries, Torvik). |
| `scripts/rebuild_all.sh paper-inputs` | exit 0, 34 s. Manifest: 218 tables, 26,853,999 rows, **digest `e5e7bae46a7ccf7a`, the same as at the 2026-10-04 Layerbase sync**. `numbers.tex` byte-identical (1,352 macros, every macro the paper uses defined), 270 plotted numbers match their macros, refs.bib 40 entries all cited. Only `manifest.json`'s `git_commit` field changed. |
| Route crawl (`scripts/qa_route_crawl.py`) | 210 routes (mvp 15, similarity 14, impact 181; 204 GET + 6 POST), **210 called, 0 not 2xx**, 3 empty, 1 over 3 s, 1 with placeholder text, 49 JSON answers without `_source`; the whole crawl takes ~30 s. Table: `docs/qa/crawl_2026-10-05.tsv`; summary below. |
| Outside sources from this Mac | **stats.nba.com answers through `nba_api`** (LeagueGameFinder 1.3 s, LeagueStandingsV3 0.5 s; every live route answered in the crawl) **but times out to plain curl** even with browser headers (25 s, no answer): the plan's "unreachable" was measured the curl way (R8-006). cdn.nba.com: headshots and logos 200, liveData JSON (scoreboard, standings, box score) 403. ESPN site API 200. The Odds API 200. Gemini 200. |

## Crawl 2026-10-05

`scripts/qa_route_crawl.py` (re-runnable; Step 10 runs it again) reads the three services' OpenAPI schemas
and calls every route once, cold (services restarted just before), with real arguments: LeBron James
(2544), LAL, 2024-25, LAL's opening game in each id format (`espn_401704628` / `0022400062`), the route's own
defaults for everything optional, `OVERRIDES` where a route needs more (each named in the script), one
valid body per Workbench POST. Per route: status, seconds, bytes, rows, empty, `_source`
(top/nested/no) with its `live` flag and upstream text, placeholder strings ("nan", "None", "undefined"
as values), note.

- **Not 2xx:** none. (The first run's two 404s were the crawl's own arguments, `/data-quality/check/dup_games` and `/prospects/comp/LeBron James`; corrected to `twin_copies` and Cooper Flagg.)
- **Empty answers (3):** `/leaders/pts` (R8-001, a real problem); `/ledger/live/games` and mvp's `/ledger/summary` (expected before the season: nothing logged for 2026-27 yet; both pages say so).
- **Over 3 s (1):** `/news/current` 5.8 s (R8-011). Next: `/teams/with-without/LAL/2025` 2.5 s, `/workbench/parse` 2.1 s (Gemini), `/odds/championship` 1.7 s, `/games/by-date` 1.5 s, `/games/guess-the-game/daily` 1.4 s, `/games/hot-streaks` 1.1 s; everything else under 1 s. `/games/boxscore` varies 0.7-3.2 s between runs (live).
- **Largest answers:** `/ledger/lock.csv` 5.9 MB (a download, by design), `/referees/crew-tendencies` 2.8 MB (R8-012), `/defense/rim-deterrence` 510 kB, `/shots/shot-value` 380 kB, `/ledger/games` 334 kB, `/games/higher-lower/pool` 332 kB.
- **Placeholder text (1):** `/games/boxscore/{id}`: `pm = "nan"` (R8-005).
- **No `_source` (49):** 21 are root, search, resolve and quiz routes that don't need one; 28 are data routes (R8-014).
- **Routes and wrappers nothing calls:** R8-017.

### Live calls to outside services (for Step 4)

Found by grep (`nba_api`, `urlopen`, `requests.get` under `api/`) and confirmed in the crawl. None timed out
today because stats.nba.com answers `nba_api`; the "if it fails" column is what the code does.

| Route | Pages | Live call (host) | Timeout | If it fails |
|---|---|---|---|---|
| `/meta/current` | Dashboard, Landing, Standings, Team Comparison (app-shell prefetch) | standings, team stats, points leader (stats.nba.com); standings (cdn.nba.com liveData, **403**); balldontlie (no key, skipped); team badges (thesportsdb) | 6 s each, in parallel | local DB (2025-26 values) |
| `/leaders/{stat}` | Stat Leaders (+ prefetch) | leaguedashplayerstats (stats.nba.com) | 6 s | DB for the same season → empty between seasons (R8-001) |
| `/players/search` (impact) | live player search: CommandPalette, Player Comparison, Trend, Radar, Archetypes, Playoff Forecaster, Draft Prospects | leaguedashplayerstats (stats.nba.com) | 6 s | DB names |
| `/players/profile/{name}` | none (R8-017) | leaguedashplayerstats | 45 s | DB |
| `/players/playoff-comparison/{name}` | Analytics › Playoff Forecaster | leaguedashplayerstats (playoffs) | 45 s | silently empty |
| `/players/heliocentricity` | Analytics › Heliocentricity | leaguedashptstats | 30 s | silently empty |
| `/players/pair-synergy` | Player Comparison | leaguedashlineups (2-man) | 45 s | 502 |
| `/teams/with-without/{team}/{season}` | Analytics › With/Without a Star | leaguegamefinder ×2 (team, player) | 30 s each | 502 (R8-008, R8-021) |
| `/games/by-date` | Live Scores, Dashboard, Landing | scoreboardv2 (stats.nba.com), then cdn.nba.com (**403**) | 6 s | empty list (R8-002, R8-031) |
| `/games/boxscore/{id}` | Live Scores | boxscoretraditionalv2, then cdn.nba.com (**403**) | 45 s | R8-003 |
| `/shots/player/{name}` (+ `/seasons`, `/zones`) | Shot Charts, Player Comparison | shotchartdetail + playercareerstats, **only for a player not yet cached; writes `player_shots`** | per call | 502-style message (R8-007) |
| `/shots/league-zones/{season}` | none (R8-017) | leaguedashteamshotlocations, only if not cached | — | — |
| `/odds/championship` | Analytics › Vegas Scanner | The Odds API (key, ~500 calls/month, cached 6 h) + standings (stats.nba.com) | 15 s | 502 / 503 without key |
| `/news/current` | News, Dashboard (prefetch) | RSS feeds (RapidAPI only with a key; none set) | 25 s | — (R8-011) |
| `/media/player-image/{name}` | none (R8-017) | thesportsdb | 20 s | null |
| `/games/blurred-player/image` | Blurred Player | cdn.nba.com headshot (200) | 10 s | 502 |
| `/workbench/parse` (POST) | Workbench English box | Gemini (key, free tier) | — | says so; the boxes still work |

Frontend direct: player photos and team logos from cdn.nba.com (`utils/teamAssets.js`,
`GreatsOfTheGame.jsx`), both 200.

Fetch scripts tagged `nbaapi` in `rebuild_all.sh` (fetch_play_by_play, fetch_2025_26_season_data,
build_schedule_fatigue, referee officials, spacing, matchups, defend dashboard, hustle, play types) may run
again now; not tried in Step 1 (R8-006).

## Constraints (not defects)

- **Layerbase:** 4,202 of 5,000 MB used (2026-10-04). A Step 6 rebuild rewrites tables of about the same size; any sync needs the owner's OK (Step 10 decides whether the biggest tables stay local).
- **Locked forecast:** no step touches the `ledger_*` tables, the `ledger-2026-27` tag or `api/ledger_lib.py` / `season_sim_lib.py` / `luck_lib.py`; from 2026-10-20 the owner runs `scripts/ledger_update.py` by hand on game days.

## Issues

### R8-001 · Stat Leaders is empty between seasons
- **Severity:** broken · **Step:** 4 · **Status:** open
- **Where:** Stat Leaders (`StatLeaders.jsx`), `GET /leaders/{stat_key}` (`api/routers/leaders.py`), also the app-shell prefetch (`prefetchCoreData`).
- **Reproduce:** `curl 127.0.0.1:8002/leaders/pts` → `{"season":2027,"results":[]}`. The page calls it without a season, so the season defaults to `max(latest DB season, current NBA season)` = 2026-27. The live call returns nothing (no games yet) and the DB fallback queries the same season, so it's empty too. The page shows "Top 10 · 2027" and nothing else, with no reason given.
- **Found by:** crawl (empty answer). It stays empty until stats.nba.com has 2026-27 leaders, and goes empty again whenever the live call fails, since the DB never has the current season.

### R8-002 · Live Scores says "No games found" for future dates
- **Severity:** wrong number · **Step:** 4 · **Status:** open
- **Where:** Live Scores, `GET /games/by-date?date=` (`impact_core.fetch_nba_games_by_date`).
- **Reproduce:** `/games/by-date?date=2026-10-20` → 0 games in 6.2 s, and the page reads "No games found for 2026-10-20". `ledger_schedule` (ESPN) has 3 games that day. Today (2026-10-05) returns 5 preseason games. The team objects carry `name: ""`, and some have `logo: null` (MEM); Step 2b should check whether that shows.
- **Found by:** crawl + manual calls. Opening night itself works only if stats.nba.com answers on the day; there is no ESPN or stored fallback (cdn.nba.com is 403, R8-003).

### R8-003 · The cdn.nba.com fallbacks are dead (403)
- **Severity:** broken · **Step:** 4 · **Status:** open
- **Where:** `impact_core._fetch_nba_cdn_standings_uncached`, `fetch_nba_cdn_games_by_date`, `fetch_boxscore_from_cdn` (used by `/meta/current`, `/games/by-date`, `/games/boxscore`).
- **Reproduce:** `curl -A Mozilla/5.0 https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json` → 403, and the same for `standings/leagueStandings.json` and `boxscore/boxscore_<id>.json`. Headshots and logos on the same host are 200.
- **Found by:** probing the hosts the code calls. When stats.nba.com fails, these fallbacks fail too, so Live Scores and the box score have nothing behind them. Step 4 picks ESPN or stored data.

### R8-004 · The dashboard mixes 2026-27 standings with 2025-26 numbers before the season
- **Severity:** looks wrong · **Step:** 4 · **Status:** open
- **Where:** Dashboard (`DashboardHome.jsx`), Team Comparison, Standings; `GET /meta/current`.
- **Reproduce:** `/meta/current` → `season: 2027` and every team 0-0 from stats.nba.com (streak "W 0"), but `top_scorer` = Luka Dončić 33.5 (2025-26 `player_season_stats`) and `team_stats` = 2025-26 DB values. None of these carries a season label. The "#1 Seed" tile takes `standings.western[0]`, i.e. only the West's first row (never the East), which is a 0-0 team before the season.
- **Found by:** reading the crawl answer and the components.

### R8-005 · Live box score shows "nan" as +/- for players who didn't play
- **Severity:** looks wrong · **Step:** 4 · **Status:** open
- **Where:** Live Scores box score, `GET /games/boxscore/{game_id}` (`impact_core.fetch_boxscore`).
- **Reproduce:** `/games/boxscore/0022400062` → 9 of 28 rows (DNPs, `min: "0"`) have `pm: "nan"`. Every other row has plus-minus as text with a decimal (`"19.0"`).
- **Found by:** crawl (placeholder-text check).

### R8-006 · stats.nba.com is reachable again, but the docs and code comments say it isn't
- **Severity:** looks wrong · **Step:** 4 · **Status:** open
- **Where:** CLAUDE.md ("unreachable from this machine since 2026-09-26"), README Known real gaps, `scripts/rebuild_all.sh` help ("nbaapi = stats.nba.com (times out …)"), Methodology open issue "Some models can't be retrained right now", the round-8 plan's facts.
- **Reproduce:** `python3 -c "from nba_api.stats.endpoints import leaguestandingsv3 as s; print(s.LeagueStandingsV3(season='2025-26', timeout=30).get_data_frames()[0].shape)"` → (30, 92) in 0.5 s. `curl` against the same URL with browser headers still gets no answer in 20-25 s (so a curl check says "down").
- **Found by:** the With/Without smoke test passing with real data (41 s), then direct calls. Step 4 decides which live pages keep a live call. Any `nbaapi` fetch script (R8-028, R8-032) can be tried again, and the wording should change wherever it says unreachable.

### R8-007 · Opening a shot chart can write rows into `player_shots`
- **Severity:** wrong number (risk) · **Step:** 4 · **Status:** open
- **Where:** Shot Charts and Player Comparison, `GET /shots/player/{name}` (+ `/seasons`, `/zones`), `shots_lib.ensure_player_shots_cached` / `ensure_season_shots_cached`.
- **Reproduce:** read the code. For a player whose `player_shots_cache_status` isn't `done`, a GET fetches his career from stats.nba.com and stores it in `player_shots`. `ENABLE_LIVE_SHOT_FETCH` defaults to true. Today nothing has been written since 2026-09-25 (2,842 players `done`, `player_shots` 6,318,078 rows). With stats.nba.com answering again (R8-006), the first view of an uncached player after 2026-10-20 (e.g. a rookie) would add 2026-27 shots to a table the paper manifest hashes, and that shot-making, xRAPM and Layerbase all read.
- **Found by:** mapping the live callers. Step 4 decides: turn live fetching off, or keep it and exclude live-fetched rows.

### R8-008 · With/Without a Star and Pair Synergy can wait 30-60 s on a live call
- **Severity:** slow · **Step:** 4 · **Status:** open
- **Where:** `/teams/with-without/{team}/{season}` (two leaguegamefinder calls, 30 s timeout each), `/players/pair-synergy` (leaguedashlineups, 45 s), `/players/playoff-comparison` and `/players/profile` (45 s), `/players/heliocentricity` (30 s).
- **Reproduce:** in the full pytest run the With/Without test took 41.0 s and Pair Synergy 18.8 s. The same routes took 2.5 s and 0.7 s in the crawl a few minutes later. stats.nba.com's first answer is sometimes very slow, and the timeouts let a page hang for up to a minute before a 502.
- **Found by:** pytest durations + crawl. The plan's target is a clear state within 3 s.

### R8-009 · The 2025-26 awards were never loaded
- **Severity:** wrong number (stale) · **Step:** 7 · **Status:** open
- **Where:** `award_winners` and `mvp_winners` (max season 2025 = 2024-25), `player_awards` (2026: All-Star only). Affects: Analytics › Prediction Ledger (60 logged 2025-26 prediction rows in `prediction_ledger` can't resolve: "no seasons resolved yet"), award backtests and history, profile award lists (no 2025-26 MVP, DPOY, ROY, All-NBA).
- **Reproduce:** `SELECT award, MAX(season) FROM award_winners GROUP BY 1` → MVP/DPOY/ROY 2025. `scripts/resolve_predictions.py` reads `award_winners`.
- **Found by:** following the empty `/ledger/summary`. Fixing it needs the 2025-26 winners read from an outside page (NBA.com, ESPN or Wikipedia, URL and date in the commit). The 2026-27 award ledger stays empty unless `scripts/snapshot_predictions.py` is run during the season (nothing schedules it).

### R8-010 · The 2026 draft isn't loaded
- **Severity:** looks wrong (stale) · **Step:** 7 · **Status:** open
- **Where:** `draft_history` (max `draft_year` 2025); Draft pages, profile bios of 2026 rookies.
- **Reproduce:** `SELECT MAX(draft_year) FROM draft_history` → 2025.
- **Found by:** checking each core table's latest season. Draft Value's outcome classes stop at 2021 by design; this only concerns the draft list itself.

### R8-011 · `/news/current` is the slowest route (5.8 s) and runs on every app load
- **Severity:** slow · **Step:** 8 · **Status:** open
- **Where:** News, Dashboard; `prefetchCoreData()` calls it on load.
- **Reproduce:** crawl 5.8 s cold (5.2 s in the first run, 6.3 s in pytest). No RapidAPI key is set, so it reads RSS feeds.
- **Found by:** crawl timing.

### R8-012 · `/referees/crew-tendencies` sends 2.8 MB
- **Severity:** slow · **Step:** 8 · **Status:** open
- **Where:** Analytics › Referee Tendencies, By Crew. Also large: `/defense/rim-deterrence` 510 kB, `/shots/shot-value` 380 kB, `/games/higher-lower/pool` 332 kB.
- **Reproduce:** crawl `bytes` column.
- **Found by:** crawl. 5,373 crews, most of which worked one game together; the page could page through them or filter on the server.

### R8-013 · The landing page's 3D court chunk is 532 kB
- **Severity:** slow · **Step:** 8 · **Status:** open
- **Where:** `ShotCourtFlight` (three.js, lazy-loaded on the landing page). It's the build's only chunk over 500 kB.
- **Reproduce:** `npx vite build` warning.
- **Found by:** build output. It's lazy, so it doesn't count toward the first load (558 kB raw / 166 kB gzip). Step 8 measures whether the landing page's first paint waits for it.

### R8-014 · 28 data routes answer without a `_source` badge
- **Severity:** looks wrong · **Step:** 2 (2a/2b/2c, per page) · **Status:** open
- **Where:** mvp `/backtest`, `/explain/{award}`, `/explain/{award}/{name}`; similarity `/clusters/player/{name}`, `/similarity/career/{name}`; impact `/contracts/player/{id}`, `/games/boxscore/{id}`, `/games/by-date`, `/games/wp-replay/list`, `/games/wp-replay/{id}/whatif`, `/hustle/leaders`, `/impact/player/{name}/{season}`, `/leaders/{stat}`, `/meta/current`, `/news/current`, `/player-profile/{id}/shot-zones`, `/players/compare-profile/{name}`, `/players/pair-synergy`, `/players/playtype-profile/{name}`, `/players/profile/{name}`, `/players/table/{season}`, `/shots/league-zones/{season}`, `/shots/player/{name}` (+ `/seasons`, `/zones`), `/trade/roster/{team}/{season}`, `/trade/simulate`, `/trade/teams/{season}`.
- **Reproduce:** crawl column `source` = `no`.
- **Found by:** crawl. The convention asks for a badge on the main Analytics endpoints. Each page sweep decides per page (add one, or note why the page doesn't need one).

### R8-015 · Season shown as a raw end year ("2027") in labels
- **Severity:** looks wrong · **Step:** 2 (2a Stat Leaders, 2c Prediction Ledger) · **Status:** open
- **Where:** Stat Leaders subtitle "Top 10 · 2027", Analytics › Prediction Ledger "Current season 2027". The app's label is "2026-27".
- **Found by:** reading the components behind the crawl's empty answers. The sweeps should look for others.

### R8-016 · Trivia asks about "this season" from last season's numbers
- **Severity:** looks wrong · **Step:** 2b · **Status:** open
- **Where:** Games › Trivia, `GET /games/trivia/daily`.
- **Reproduce:** on 2026-10-05 the answer's `season` is 2026 (2025-26), and every question says "this season" ("Who leads the league in points per game this season?").
- **Found by:** reading the crawl answer.

### R8-017 · Routes and API wrappers nothing calls
- **Severity:** looks wrong (code hygiene) · **Step:** 8 · **Status:** open
- **Where:** routes no frontend file names: similarity `/similarity/career/{name}`, impact `/impact/player/{name}/{season}`, `/shots/quality-map/options`. `services/api.js` wrappers no component calls (9 of 202): `fetchPlayerShotZones`, `fetchLeagueShotZones`, `fetchRapmValidation`, `fetchPlayerImage`, `fetchPlayerProfile`, `fetchShotSeasons`, `fetchProjectionBacktest`, `fetchPlayerProjections`, `fetchLedgerHindcast` (their routes may still be called another way).
- **Found by:** comparing the crawl's route list with `frontend/src`. Keep (API-only, tested) or remove, with a reason either way.

### R8-018 · Typed-name tools pick the first of two same-name players
- **Severity:** wrong number · **Step:** 5 · **Status:** open
- **Where:** every caller of `impact_core.find_player()`: Player Comparison (+ `/shots/player/{name}/zones`), Trend Analysis, Radar, Scouting Report, With/Without a Star, and others. 19 names belong to two players.
- **Found by:** known gap (README Known real gaps, round 7 step 5). Fix: `resolve_player` + optional `player_id`, the Shot Charts pattern.

### R8-019 · Game Log and Game Finder show no +/-
- **Severity:** looks wrong · **Step:** 5 · **Status:** open
- **Where:** profile Game Log, `?page=gamefinder`. The right per-game numbers exist in `player_game_onfloor` (98.2% exact against ESPN's box score), and the Workbench already reads them.
- **Found by:** known gap (README).

### R8-020 · 3-5 player-seasons a year carry a team the player never played for
- **Severity:** wrong number · **Step:** 5 · **Status:** open
- **Where:** `player_season_stats.team_abbreviation` from 2020-21 (2024-25: Bane ORL, Anthony and Caldwell-Pope MEM, Ingram TOR, Kleber LAL); shown by Player Stats, Role Player Finder, leaderboards. Methodology open issue.
- **Found by:** known gap (README, Methodology).

### R8-021 · With/Without a Star's point differential comes from stats.nba.com's summed plus-minus
- **Severity:** wrong number · **Step:** 4 · **Status:** open
- **Where:** `/teams/with-without/...`: live `PLUS_MINUS` from leaguegamefinder, which differs from the final margin in some games (160 of 20,348 in the stored copy). Its Methodology card says so.
- **Found by:** known gap (README). Fix: stored data (`game_scores` + `player_game_lines`, 2020-21 on).

### R8-022 · ESPN's no-id players get no line and break their stints
- **Severity:** wrong number · **Step:** 6a (owner's OK) · **Status:** open
- **Where:** `scripts/pbp_lineups.py` resolves names only through `player_season_stats`. 100-155 names a season get no `player_game_lines` row, and their stints aren't `tracked_ok` (2-6% of minutes before 2025-26).
- **Found by:** known gap (README). Fix: an exact name + team + season fallback through `player_bio` / `player_id_map`.

### R8-023 · Phantom minutes in 9 player-games (team-less substitution)
- **Severity:** wrong number · **Step:** 6a · **Status:** open
- **Where:** `Game.run()`, `player_game_lines` (e.g. Dončić 45.4 min on 2022-01-30 instead of 37.1). Methodology open issue. The smoke test pins the 9.
- **Found by:** known gap (README, Methodology).

### R8-024 · One `player_game_lines` row has team 'NaN'
- **Severity:** looks wrong · **Step:** 6a · **Status:** open
- **Where:** Miye Oni, 2021-11-20 (`espn_401360071`), 195 s, no stats.
- **Found by:** known gap (README).

### R8-025 · `player_game_lines.tm_pts/op_pts` double-count where ESPN's score field is stale
- **Severity:** wrong number · **Step:** 6a · **Status:** open
- **Where:** on-court points in the lines (25% of full-minute team-games don't sum to 5× the margin). Nothing shown reads them since round 7. Fix: points from the made shots like the stints, or drop the columns.
- **Found by:** known gap (README).

### R8-026 · `lineup_stints` credits a free throw at the shot, not at the foul
- **Severity:** wrong number · **Step:** 6a + 6b · **Status:** open
- **Where:** the stints and everything on them (RAPM, Rating Tracker, lineups, pairs, xRAPM, rim deterrence, rotations, lineup predictor, report card, the paper). The box-score rule (`build_player_game_onfloor.py`) is 98.2% exact vs ESPN's +/-; at the shot, 42.5%.
- **Found by:** known gap (README, round 7 step 2).

### R8-027 · `lineup_stints` takes ESPN's text two-or-three call on misses
- **Severity:** wrong number (not shown anywhere yet) · **Step:** 6a · **Status:** open
- **Where:** `home_fg3a`/`away_fg3a` in the stints. `player_game_lines` and the Play Finder use the shot chart's call (`miss_three_calls()`).
- **Found by:** known gap (README).

### R8-028 · Four 2025-26 games are missing from the shot chart; 2025-26 match rate 97.5%
- **Severity:** wrong number · **Step:** 6 (owner's OK: changes `player_shots` and the shot chain) · **Status:** open
- **Where:** `player_shots` has no rows for 0022500259-0022500261 and 0022500265 (2025-11-19/20). 2025-26's ESPN-to-chart match rate is 97.5% vs ≥ 99.8% elsewhere. Affects shot-making, quality map, shot value and xRAPM.
- **Found by:** known gap (README: "re-fetch when stats.nba.com is reachable again"). It is reachable now (R8-006).

### R8-029 · Hot Streak Checker's "carries on" share includes the shuffled-null centre
- **Severity:** wrong number · **Step:** 7 · **Status:** open
- **Where:** `hot_streak_persistence.slope` (7-86% even with games shuffled). Methodology open issue. `paper_beliefs_summary` holds the null centre.
- **Found by:** known gap (README, Methodology).

### R8-030 · Garbage-Time Deflator still reads ESPN's raw clock
- **Severity:** wrong number (not measured) · **Step:** 7 · **Status:** open
- **Where:** `build_leverage_splits.py` (win probability and clutch labels). Measure how much it moves on `pbp_event_clock` first.
- **Found by:** known gap (README).

### R8-031 · `/games/by-date` has no stored fallback for past dates
- **Severity:** broken · **Step:** 7 · **Status:** open
- **Where:** Live Scores date browsing. README: "returns an empty list for an older historical date".
- **Re-measured 2026-10-05:** `/games/by-date?date=2025-01-02` → 6 final games with scores in 1.0 s, equal to `game_scores` (6). It works because stats.nba.com answers scoreboardv2 again; it would be empty again if that stops (R8-003). Fix: past dates from `game_scores` (or ESPN by date).
- **Found by:** known gap (README) + crawl.

### R8-032 · Pair Synergy's model still uses the old in-house defensive BPM
- **Severity:** wrong number · **Step:** 7 (owner's call: a retrain) · **Status:** open
- **Where:** Pair Synergy reads `dbpm_repro` until retrained. Methodology open issue "Some models can't be retrained right now" says stats.nba.com is unreachable, which is no longer true (R8-006).
- **Found by:** known gap (CLAUDE.md Open items, Methodology).
