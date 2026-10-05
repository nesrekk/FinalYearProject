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

Counts (2026-10-05, after Step 3): **67 entries**, 33 open, 34 fixed: 6 broken, 20 wrong number,
5 slow, 36 looks wrong. 6 are from Step 3's cross-page checks (R8-062 to R8-067, all fixed in its
commit). 17 are new from Step 1 (R8-001 to R8-017); 15 are known gaps already written
down in README "Known real gaps" / Methodology open issues, listed so a step owns each (R8-018 to
R8-032); 11 are from Step 2a's sweep of the Players pages (R8-033 to R8-043, 10 fixed in its commit);
6 are from Step 2b's sweep of the Teams, Games and Today pages (R8-044 to R8-049, 5 fixed in its commit,
which also fixed R8-016 and the 2b parts of R8-004, R8-014 and R8-043); 12 are from Step 2c's sweep of
Analytics, Shot Charts, the Workbench and the rest (R8-050 to R8-061, 10 fixed in its commit, which also
fixed R8-015 and R8-043 and the 2c part of R8-014).

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

## Step 2a page sweep: Players pages (2026-10-05)

`frontend/qa/page_scan.js` (README "Testing and QA") through the desktop app's browser tool, on a temporary
harness page (deleted before the commit). Pages: every Players menu entry except Shot Charts (2c): Player
Stats, Player Comparison (empty and Jokić vs Murray 2025-26), Stat Leaders, Leaderboard Builder, Regression
Explorer, Breakout Detector, Stat Stability, RAPM, Role Player Finder, Era Translator, Aging Curves,
Projections, Stat Line Finder, Game Finder, Play Finder, Hot Streak Checker, Situational Splits, Draft
Value Guide, Rookie Class Tracker, Greats of the Game (and its card), Hall of Fame; player profiles of
LeBron James (2544), Michael Jordan (893, pre-2000) and Cooper Flagg (1642843, rookie).

| Check | Result |
|---|---|
| Scan at 1280 and 375 px, Paper and Ink (100 renders) | Before: 1 console error on every profile with play-by-play blocks (R8-034), contrast failures on 4 pages (R8-033, R8-035, R8-042), nothing else. **After the fixes: 100 of 100 clean** (no console error, failed request, request over 3 s apart from `/news/current` on a cold app-shell prefetch (R8-011), overflow, contrast failure, "undefined"/"NaN" text, stuck loading text or header-only table; slowest page settled in 4.0 s). |
| By eye (screenshots, 1280 Paper/Ink, 375) | R8-036 (phone search button), R8-037 (stale seasons, raw years), R8-041 (tooltip), the empty Stat Leaders table (R8-001, Step 4). |
| Copy link → open fresh = same view | 15 of 15 pages that keep their inputs in the link match after changing a control (Stat Line Finder by changing a value: its "Add a stat" picker adds an empty row, which isn't in the link by design). Player Stats, Stat Leaders, Draft, Rookies, Greats and Hall of Fame have no link state and no Copy link button. Aging Curves had Copy link without Save (fixed). |
| Links out | One of each kind per page opened fresh (player, team, Stat Stability, Replay): all open a real view; the 185 Hall of Fame and 83 Greats ids all have a profile. Profile buttons (16 on LeBron's, 4 on Jordan's, 13 on Flagg's: Workbench, Greats, Shot Charts, DAD, Spacing, Contracts, Clutch, Projections, On/Off, RAPM, Rating Tracker, Rim, Assists, Splits, Possessions, Breakouts): all open the page they name; On/Off opened the wrong team (R8-040). |
| Back / Forward | Leaderboard Builder, RAPM, Game Finder → profile → Back → Forward, and profile → profile: each returns the same view and inputs. |
| Empty results | Game Finder, Leaderboard Builder, Stat Line Finder, Play Finder say what happened; Breakout Detector and Regression Explorer didn't (R8-038). |

## Step 2b page sweep: Teams, Games, Live Scores and the Today pages (2026-10-05)

Same scanner and harness as 2a (the harness deleted again before the commit). The scanner learned two
things: `color-mix()` backgrounds compute to `color(srgb …)`, which it read as transparent (so the
Simulator's seed cells passed when they didn't, R8-044), and "the null" / "a null" is statistics wording,
not a leaked value (Coaching Decisions' footnotes). Pages: Dashboard, Live Scores, News, Standings (the
Today group, which no other sweep lists); Team Comparison, Trade Analyzer (empty and a run trade), Trade
Impact, Rotations (league, BOS season, a LAL game in "own" measure), Assist Network (default, DEN duos),
Possession Explorer (default, OKC defence from every start), Coaching Decisions (all five views, BOS),
Season Simulator (today and 2024-25 mid-season with a game's what-if), Forecast Ledger (both tabs), Best
Games & Upsets (both views, GSW 2022-23); team pages of LAL (latest), OKC 2004-05 (relocated: opens as
the Seattle SuperSonics), BKN 2009-10 (as the New Jersey Nets), MEM 1998-99 (as the Vancouver Grizzlies),
GSW 2025-26; the Games hub and all five games (Trivia and Higher or Lower played one move).

| Check | Result |
|---|---|
| Scan at 1280 and 375 px, Paper and Ink (about 190 renders of 40 views: 19 default, 16 filled in, 5 games; then the 40 renders of the changed pages again) | Before: the Simulator's seed cells in Ink (R8-044), Live Scores' fallback badge (R8-045), and two false positives (Standings' active segment, whose dark pill is a sibling layer; Coaching's "the null"). After the fixes every render is clean apart from: the Dashboard's 404 probe (R8-049), its scroll-in sections at opacity 0 (`whileInView`: IntersectionObserver never fires in a hidden pane, a harness artefact), the Standings false positive, and `/news/current` over 3 s on a cold app-shell prefetch (R8-011). |
| By eye (screenshots, 1280 Paper/Ink, 375) | R8-045 (Live Scores: no team names), R8-004 (Standings' 0-0 "#1 seed"), seed digits after the fix, the trade cards, a rotation chart at 375 in Ink, Seattle's 2004-05 page. |
| Copy link → open fresh = same view | 9 of 9 pages with link state match after changing a control (Trade Analyzer, Trade Impact, Rotations, Assist Network, Possession Explorer, Coaching Decisions, Season Simulator, Best Games, team page); a full trade link reruns the trade. The Forecast Ledger has no select on its default view (its tabs and views are buttons, kept in the link). The Games hub had no link state (R8-048, fixed). Live Scores, Standings, News, Team Comparison and the Dashboard keep nothing in the link. |
| Links out | One of each kind per page opened fresh (team, player, Replay): all real views. Team page buttons (7 on LAL's: Workbench, Luck & Schedule, Pair Chemistry, Lineup Chemistry, Rotations, Assist Network, On/Off) and the Simulator's (Opening day, Luck & Schedule, What-if) open the page they name, with the team selected; Lineup Chemistry lost the season (R8-047). Possession Explorer and Coaching rows pick a team on the page (by design); Coaching had no way on to the team page (R8-047). Player names on three pages weren't links (R8-046). |
| Back / Forward | Rotations → profile, team page → Rotations, Best Games → team page, each Back then Forward: same URL and view. |
| Empty and pre-season states | Standings and the Dashboard before opening night (R8-004, fixed for the page); Live Scores for a date without games says so (future dates: R8-002, Step 4); the Forecast Ledger's Live tab before any game is final says so. |

## Step 2c page sweep: Analytics, Shot Charts, Workbench and the rest (2026-10-05)

Same scanner and harness as 2a/2b (the harness deleted again before the commit). Pages: the Analytics index and
all 30 tools, Shot Charts (all five views), the Workbench (empty and the six starter boards), Learn,
Methodology, Data Coverage, Data Quality (three views), Model Report Card, Saved analyses, Report builder,
Watchlist and the landing page: 48 default views, then 35 filled in (Awards Race's four predictions, Impact
Rankings' three tables, Season Similarity, Radar with three players, Matchup Finder, With/Without a Star,
Trend Analysis by team, Referees by crew, Prediction Ledger, Model Validation's WPA and All-NBA, Vegas
Scanner expanded, a Game Replay what-if, Shot Charts for Wembanyama, Jordan 1997-98 with every game, a
quality map against Klay Thompson and Jokić's shot-making, Data Quality's "why" and a game-count link, Report
Card's tasks, a Methodology card link, the six starter boards with every block loaded, and Saved analyses /
Report builder with real items saved and added through the UI).

The scanner learned four things: a **chart-mark check** (SVG dots, lines and bars under 3:1, WCAG 1.4.11: an
element passes if its stroke or fill reads; faded layers under 40% opacity, background-coloured knockouts and
heat-map grids are skipped), **SVG text and marks are measured against the chart's own panel** (a `<rect>`
covering half the chart, usually `--surface-2`), **outlined text** reads by its `-webkit-text-stroke`, and
"a null result" / "null centre" are statistics wording. The harness now also replaces IntersectionObserver
with a stub that reports everything as on screen, so scroll-in sections (the Analytics index, Learn, the
Dashboard) and the Workbench's lazy blocks render in a hidden pane.

| Check | Result |
|---|---|
| Scan at 1280 and 375 px, Paper and Ink (about 430 renders of 83 views in the final passes, after earlier passes before the fixes) | Before: dark-theme pastels in 18 chart components (R8-050), faded small-sample rows (R8-051), Shot value's sorted header (R8-052), a browser-blue link in Ink (R8-053), Data Quality's links on tints (R8-054), Methodology overflowing at 375 px (R8-055), the landing footer on orange (R8-056). **After the fixes every render is clean of console errors, failed requests (except R8-049's 404 probe, on the landing page too), overflow, text contrast, "undefined"/"NaN" text, stuck loading and header-only tables.** Left, reviewed: chart marks in brand orange at 2.8-2.9:1 on Paper (R8-059, owner's call); Shot mix's three lighter zone colours (2.0-2.65:1 on Paper, a documented choice: the legend, hover panel and table carry the values); the quality map's hexagons and other heat maps (colour scales read against a legend); the Workbench's grey context population drawn at 40-55% opacity behind the coloured set; the Referees segmented control (the known sibling-layer false positive). Slow: `/news/current` on a cold prefetch (R8-011), With/Without's live call 21 s (R8-008), Data Quality's first load (R8-060). |
| By eye (screenshots, 1280 Paper/Ink) | Shot Charts' made/missed dots in both themes, the Garbage-Time slope chart, Saved analyses and the Report builder with items. Found R8-058 (saved titles) by reading the Saved page. |
| Copy link → open fresh = same view | 14 of 14 after the fix: Shot Charts (season and games, on all five views), Data Quality, Model Report Card, Pair Chemistry, Lineup Chemistry, Luck & Schedule, On/Off, Rim Deterrence, Game Replay (R8-057, fixed), the Workbench (`b=` after adding a block). Pairs, Lineups and On/Off reopen with the same parameters in a different order (same view). |
| Links out | One of each kind per page opened fresh: player and team links (Clutch WPA, Rim, Lineups, Pairs, On/Off, Fatigue, College → NBA, Shot-making, Shot value, Watchlist), Data Quality's Replay links: all real views. Buttons: Saved analyses' Open, Learn's three, Report Card's task tabs; Data Coverage's "used by" showed raw ids (R8-061, fixed). |
| Back / Forward | Rim → profile, Shot value → team page, Data Quality → Game Replay, Data Coverage → Player Stats: each Back then Forward returns the same URL and view. |
| Data writes | None left behind. A 2c test made `/shots/league-zones/2026` fetch and insert 5 rows (R8-007); they were deleted and the test changed. `player_shots` unchanged after every run. |

## Step 3: the same number agrees everywhere (2026-10-05)

`api/tests/test_consistency.py` (13 tests, ~8 s) reads every quantity the app shows on more than one page
through every route that shows it, on 8 player-seasons and 6 team-seasons drawn with a fixed seed
(20261005), and asserts they agree to the rounding each route applies; where two pages differ by
definition the difference is stated on both and pinned with its size. Before that, a probe of the same
routes on a 12-player / 8-team sample found the problems below.

| Quantity | Routes compared | Result |
|---|---|---|
| Player season (games, points, TS%, minutes) | profile, Player Stats table, Leaderboard Builder, Player Comparison, Stat Line Finder, Workbench `player_season` | Equal everywhere (all read `player_season_stats`). |
| Team wins, losses, games, margin | team page (summary, games, luck blocks), Luck & Schedule, Season Simulator, Workbench `team_season` / `team_game`, the Standings fallback | Equal; the Simulator's as-of view is "that morning" (wins + losses + games left = games). **The Standings / Team Comparison stored fallback was wrong for all 30 teams** (R8-062). |
| Team ratings, turnovers (Team Comparison's advanced block) | `/teams/compare` vs the team page | **Different numbers** (R8-063): a games-weighted mean of the players' on-court ratings, 0.9 off the team's net rating on average, 4.0 at worst. |
| RAPM, Rating Tracker (value, interval, rank) | RAPM page, profile, Workbench, every version | Equal to each route's rounding; **ranks of tied players differed by one** (R8-064). |
| Per-game lines | Game Log, Game Finder, Play Finder, Workbench `player_game`, profile season counts | Equal in every column; a game's plays add up to its line. Games vs NBA.com's GP: at most 2 apart (the page shows both). Minutes vs the stints: the 9 phantom games (R8-023). |
| On/off (net, interval) | On/Off page, profile, team page, Workbench `player_onoff` | Equal; the Workbench interval is closed-form, half-width within 25% of the page's bootstrap. |
| Shot totals (regular-season FGA, FGM) | Shot Charts, profile zones, zones route, shot-making, quality map, shot value, zone history | Equal, except **the zones route counted playoff shots** (R8-065). Chart vs season table: within the per-game rounding every season but 2025-26 (R8-028, re-measured). |
| Postseason game dates | `postseason_games` vs Wikipedia's Finals and play-in dates | **A day late for every evening game** (R8-067). |
| Live standings | stats.nba.com rows vs the team list | **The Clippers' row had no team code** (R8-066). |

Fixed in the Step 3 commit: R8-062, R8-063, R8-064, R8-065, R8-066, R8-067 (every fix has a test in
`test_consistency.py` or `test_known_facts.py`). Also 14 new outside-sourced facts in
`test_known_facts.py` (48 tests now): 2022-23 to 2024-25 award winners, the 2025 draft's top five, the
2025 Finals and play-in game by game, 2024-25 records with home/road splits, the annual three-point
leaders 2020-21 to 2024-25 from the play-by-play, Jokić's 2024-25 line and 34 triple-doubles (Finder and
Game Finder), Trae Young's 880 assists (lines and Assist Network), Wembanyama's block titles, the Kings'
176-175 (scores, Best Games, Play Finder points), the Clippers' 35-point comeback (Best Games), the 2024
NBA Cup final kept out of the regular season, Curry's 2020-21 scoring title.

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
- **Step 2b:** the empty `name` and the null/third-party logos did show (bare logos, no team names, a white-on-orange fallback at 2.6:1); the page now uses the app's names and NBA.com logos (R8-045). The route's own fields stay Step 4's.

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
- **Step 2b (Standings, Dashboard tile):** Standings now names the season ("2026-27 standings: no games played yet, so every team is 0-0 until the first tip-off"), shows no "#1 Seed" hero and no "W 0" streaks before a game is played; the Dashboard tile is "Best record" over both conferences (it was the West's first row) and says "no games played yet" before the season. Test: `test_standings_say_when_no_game_has_been_played`. Left for Step 4: `top_scorer` and `team_stats` from 2025-26 without a season label, and the 2026-27 standings coming only from stats.nba.com.

### R8-005 · Live box score shows "nan" as +/- for players who didn't play
- **Severity:** looks wrong · **Step:** 4 · **Status:** open
- **Where:** Live Scores box score, `GET /games/boxscore/{game_id}` (`impact_core.fetch_boxscore`).
- **Reproduce:** `/games/boxscore/0022400062` → 9 of 28 rows (DNPs, `min: "0"`) have `pm: "nan"`. Every other row has plus-minus as text with a decimal (`"19.0"`).
- **Found by:** crawl (placeholder-text check).
- **Step 2b:** not visible on the page: the Live Scores box score has no +/- column (Player, MIN, PTS, REB, AST, FG, 3PT, FT). Still worth fixing in the route (Step 4).

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
- **Step 2c:** the same happens to `league_shot_zones` ("cached forever after the first fetch"): it has 2023-24 and 2024-25 only, so the first `GET /shots/league-zones/2026` (Player Comparison's shot zones for 2025-26, or a test) fetches 2025-26 from stats.nba.com and inserts 5 rows. A 2c test did exactly that on 2026-10-05; the 5 rows were deleted again (the table is back to its 10 rows) and the test now asks for a cached season. `player_shots` and `player_shots_cache_status` were checked unchanged after every 2c run (6,318,078 rows; 2,842 done, last update 2026-09-25): every player opened in the sweep (Curry, Wembanyama, Jordan, Jokić, Thompson) was already cached.

### R8-008 · With/Without a Star and Pair Synergy can wait 30-60 s on a live call
- **Severity:** slow · **Step:** 4 · **Status:** open
- **Where:** `/teams/with-without/{team}/{season}` (two leaguegamefinder calls, 30 s timeout each), `/players/pair-synergy` (leaguedashlineups, 45 s), `/players/playoff-comparison` and `/players/profile` (45 s), `/players/heliocentricity` (30 s).
- **Reproduce:** in the full pytest run the With/Without test took 41.0 s and Pair Synergy 18.8 s. The same routes took 2.5 s and 0.7 s in the crawl a few minutes later. stats.nba.com's first answer is sometimes very slow, and the timeouts let a page hang for up to a minute before a 502.
- **Found by:** pytest durations + crawl. The plan's target is a clear state within 3 s.
- **Step 2c (browser):** With/Without a Star for DEN 2025-26 / Jokić took 21.0 s in the sweep (`/teams/with-without/DEN/2026`).

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
- **Step 2c (browser):** Referee Tendencies › By Crew renders every crew at once: 490,899 characters of page text at 1280 px (the By Official view is 9,127). Paging or a server-side filter would fix both the payload and the page.

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
- **Step 2a (Players pages):** `/players/table/{season}` (Player Stats) and `/players/compare-profile/{name}` (Player Comparison) now return `_source`, and both pages show the badge; Rookie Class Tracker now shows the one `/roy/predict` already returned. Left: `/leaders/{stat}` (Stat Leaders, with R8-001 in Step 4), `/players/pair-synergy` (a live call, Step 4), and the profile's sub-routes (`/player-profile/{id}/shot-zones`, `/contracts/player/{id}`, `/players/playtype-profile/{name}`), which sit under the profile's own badge for `/player-profile/{id}`. Shot routes: 2c.
- **Step 2b (Teams, Games, Today):** `/trade/teams/{season}`, `/trade/roster/{team}/{season}` and `/trade/simulate` now return `_source`, shown on the Trade Analyzer's result (test `test_trade_analyzer_carries_a_source`). Left, all live and Step 4's: `/games/by-date`, `/games/boxscore/{id}` (Live Scores), `/meta/current` (Dashboard, Standings, Team Comparison), `/news/current` (News). `/games/wp-replay/*` is 2c's (Analytics › Game Replay).
- **Step 2c (Analytics, Shot Charts, the rest):** `/shots/player/{name}`, `/shots/player/{name}/seasons` and `/zones` now return `_source` (`player_shots`; `live` true when the shots were fetched just now) and `/shots/league-zones/{season}` too (`league_shot_zones`); Shot Charts shows the badge on its dots and heat-map views (the other three views already had theirs). Test `test_shot_charts_carry_a_source`. The rest of the 2c routes sit under their page's own badge: `/games/wp-replay/list` and `/{id}/whatif` under the replay's, `/backtest` under Model Validation's, `/explain/*` under Awards Race's, `/clusters/player/{name}` under Player Archetypes'. Unused by any page (R8-017): `/players/profile/{name}`, `/similarity/career/{name}`, `/impact/player/{name}/{season}`. Left, all Step 4's: `/leaders/{stat}`, `/hustle/leaders` (Stat Leaders), `/players/pair-synergy`, `/games/by-date`, `/games/boxscore/{id}`, `/meta/current`, `/news/current`.

### R8-015 · Season shown as a raw end year ("2027") in labels
- **Severity:** looks wrong · **Step:** 2 (2a Stat Leaders, 2c Prediction Ledger) · **Status:** fixed (2a: Stat Leaders; 2c: Prediction Ledger)
- **Where:** Stat Leaders subtitle "Top 10 · 2027", Analytics › Prediction Ledger "Current season 2027". The app's label is "2026-27".
- **Found by:** reading the components behind the crawl's empty answers. The sweeps should look for others.
- **Step 2a:** Stat Leaders now reads "Top 10 · 2026-27" (test `test_stat_leaders_season_label`). The same kind of raw-year box was on Player Stats, Player Comparison and Rookie Class Tracker (R8-037, fixed). Prediction Ledger is 2c's; still open for it.
- **Step 2c:** Prediction Ledger reads "Current season 2026-27" (`seasonLabel` in `utils/format.js`, test `test_prediction_ledger_season_label`). The sweep found no other raw year in a label on its pages (the season boxes were R8-043).

### R8-016 · Trivia asks about "this season" from last season's numbers
- **Severity:** looks wrong · **Step:** 2b · **Status:** fixed in the Step 2b commit
- **Where:** Games › Trivia, `GET /games/trivia/daily`.
- **Reproduce:** on 2026-10-05 the answer's `season` is 2026 (2025-26), and every question says "this season" ("Who leads the league in points per game this season?").
- **Found by:** reading the crawl answer.
- **Fix:** every question names the pool's season in the past tense ("Who led the league in points per game in 2025-26?"; `impact_core._trivia_season`); Trivia, Guess the Player and Blurred Player headers read "2025-26 season · …" instead of "Season 2026 · …", and their tooltips say "the latest loaded season's" pool. Tests: `test_trivia_questions_name_the_season`, `test_puzzles_name_their_season`.

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
- **Step 3 (re-measured 2026-10-05):** per game, the chart is short of the lines' FGA in 776 of 1,225 games of 2025-26 (1,079 attempts in all, at most 6 a game, spread one or two a game) against 18 games in 2024-25; 805 games have at least one player-game where the two disagree (2024-25: 98 player-games). Per player-season the chart still lands within the per-game rounding of NBA.com's FGA × GP once the four games with no rows are added back (`test_shot_chart_fga_matches_the_season_table` pins both). So the whole 2025-26 chart is thin, not just four games: the re-fetch should be the full season.

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

### R8-033 · Muted text on the page rails was just under 4.5:1 in Paper
- **Severity:** looks wrong · **Step:** 2a · **Status:** fixed in the Step 2a commit
- **Where:** `--text-3` (#6b6458) on `--surface-2` (#e8e1d3): the rail items of Player Stats, Stat Leaders and Hall of Fame (and anything else muted on a `--surface-2` panel).
- **Reproduce:** contrast 4.497:1 (floor 4.5). Ink passes.
- **Found by:** the scanner. **Fix:** `--text-3` → #665f53 in Paper (4.85:1 on `--surface-2`, 5.36 on `--bg`, 5.95 on `--surface`); a hair darker everywhere it is used. Test: `test_muted_text_reads_on_the_rails`.

### R8-034 · Every profile with play-by-play blocks logged React's duplicate-key error
- **Severity:** broken (console error) · **Step:** 2a · **Status:** fixed in the Step 2a commit
- **Where:** `PlayerProfile.jsx`: Game Log, Rating Tracker, Rim, Assists, Situational Splits and Possessions blocks were siblings all keyed `d.player.player_id` ("Encountered two children with the same key"; React may then duplicate or drop children). LeBron and Flagg had it, Jordan (no such blocks) didn't.
- **Found by:** the scanner's console capture. **Fix:** each block's key gets its own prefix (`log-…`, `tracker-…`, …); the reset-per-player purpose is kept. Test: `test_profile_blocks_have_distinct_keys`.

### R8-035 · Player Comparison's player colours were unreadable on Paper
- **Severity:** looks wrong · **Step:** 2a · **Status:** fixed in the Step 2a commit
- **Where:** `PlayerComparison.jsx` `COLOR_A` #f87171 / `COLOR_B` #38bdf8, fixed hex in both themes: the winning Tale of the Tape values, the skill-profile percentages and the table headers.
- **Reproduce:** Jokić vs Murray 2025-26 in Paper: 2.61:1, 2.02:1 on `--surface`; 2.13:1, 1.65:1 on `--surface-2`.
- **Found by:** the scanner (filled comparison). **Fix:** theme tokens `--compare-a` / `--compare-b` (Paper #b91c1c / #1d4ed8, ≥ 4.97:1 on both surfaces; Ink keeps the old colours, which pass there). Test: `test_comparison_colours_read_as_text`.

### R8-036 · Below 480 px the top bar's search button was an empty box
- **Severity:** looks wrong · **Step:** 2a (app shell, every page) · **Status:** fixed in the Step 2a commit
- **Where:** `styles/shell.css` `.nav-search-pill span { display: none }` hid the "Search" label and also the icon (`Icon` renders a span). The button had no accessible name either.
- **Found by:** 375 px screenshots. **Fix:** the rule skips `.icon`; `aria-label="Search"`. Test: `test_phone_search_button_shows_its_icon`.

### R8-037 · Player Stats and Player Comparison opened on 2024-25; season boxes showed a raw year
- **Severity:** wrong number (stale default) · **Step:** 2a · **Status:** fixed in the Step 2a commit
- **Where:** Player Stats (`useState(2025)`) and Player Comparison (`?? 2025`) opened on 2024-25 although 2025-26 is loaded; both, and Rookie Class Tracker, had a number box showing "2025". Player Stats' line "min ≥ N MPG" and its team links used the inputs before Load was pressed; Rookie Class Tracker's comps used the picker's season instead of the loaded class's, its tooltip said "tracked live" (a stored model) and its count said "this season" for any season.
- **Found by:** screenshots. **Fix:** a season picker labelled 2025-26 … 2009-10 defaulting to 2025-26 on all three; the loaded table's season and floor in the text and links. Test: `test_season_pickers_default_to_the_latest_season`. Other pages with the same pattern: R8-043.

### R8-038 · Two empty results didn't say why
- **Severity:** looks wrong · **Step:** 2a · **Status:** fixed in the Step 2a commit
- **Where:** `/explore/breakouts` with floors nobody meets (e.g. 82+ games, 44+ minutes, 2025-26) said "Not enough seasons with these stats."; `/explore/regression` said "Only 0 player-seasons pass these filters".
- **Found by:** running each finder with filters that match nothing (Game Finder, Leaderboard Builder, Stat Line Finder and Play Finder already explain themselves). **Fix:** Breakouts names the floors and says to lower them (a season before the stats start keeps its own message); Regression says "No player-seasons pass these filters (a fit needs 30)". Test: `test_empty_results_say_why`.

### R8-039 · Hall of Fame names weren't links; a Greats card had no way to the profile
- **Severity:** looks wrong · **Step:** 2a · **Status:** fixed in the Step 2a commit
- **Where:** `HallOfFame.jsx` built its own headshot + name (no link, no watchlist star); `GreatsOfTheGame.jsx`'s detail card had no profile link.
- **Found by:** following links (0 player links on both pages). Checked first: all 185 Hall of Fame ids (five career-leader stats, longevity, greatest seasons) and all 83 Greats ids open a profile. **Fix:** `PlayerName` in the three Hall of Fame tables (the 75 badge kept); "Open full profile →" in the Greats card.

### R8-040 · The profile's "Open On/Off" opened Atlanta
- **Severity:** broken (link to the wrong view) · **Step:** 2a · **Status:** fixed in the Step 2a commit
- **Where:** `PlayerProfile.jsx` On/off block called `onNavigate('analytics', 'onoff')` with no team, so the On/Off page opened on its default team (ATL) for every player.
- **Found by:** clicking every navigation button on the profiles. **Fix:** passes the latest season and team (`?season=2026&team=LAL#onoff` for LeBron, DAL for Flagg); checked in the browser.

### R8-041 · Player Comparison's tooltip said there is no height or wingspan data
- **Severity:** looks wrong · **Step:** 2a · **Status:** fixed in the Step 2a commit
- **Where:** the page's "How this works" tooltip, while the bio cards show NBA Draft Combine height and wingspan when a player was measured (`draft_combine`, said in the route's docstring and the card's own tooltip).
- **Found by:** reading the page. **Fix:** the tooltip says the measurements are the Combine's, shown only for measured players.

### R8-042 · Greats' "From the data" / "Source" labels were 4.49:1 in Ink
- **Severity:** looks wrong · **Step:** 2a · **Status:** fixed in the Step 2a commit
- **Where:** `greats.css` `.gg-dyk a, .gg-dyk .gg-from` #0d0d0d on Ink's orange `--hi` (#ff5b14).
- **Found by:** the scanner with the card open. **Fix:** #000 (4.85:1; higher on Paper's lime).

### R8-043 · Other season pickers still open on 2024-25 or show a raw year
- **Severity:** looks wrong · **Step:** 2b / 2c · **Status:** fixed (2b: the trade pages; 2c: ten Analytics sections)
- **Where:** `useState(2025)` in Analytics sections `WithWithoutStarSection`, `OffensiveStyleSection`, `RadarCompareSection`, `PlayerArchetypesSection`, `PlayoffForecasterSection` (2c); Trade Impact's number box (2b). Check each page's data range before changing the default (Contract Value has no 2025-26).
- **Found by:** grep during Step 2a (same pattern as R8-037).
- **Step 2b:** Trade Analyzer opened on 2023-24 (`?? 2024`) although its rosters and win model cover 2025-26 (checked: `/trade/simulate?season=2026` runs); it now opens on 2025-26. Both trade pages have a labelled season picker (2025-26 … 2009-10) instead of the number box; Trade Impact keeps its 2024-25 default (the last season with all three blocks, said on the page). Test: `test_trade_pages_use_season_pickers`. Left: the five Analytics sections (2c).
- **Step 2c:** ten Analytics sections had a bare number box ("2025"): With/Without a Star, Offensive Style, Radar Compare, Player Archetypes, Playoff Forecaster (all opened on 2024-25), Heliocentricity, Career Trajectory, Awards Race (opened on 2024-25 too), Impact Rankings and Matchup Finder. All now use `common/SeasonSelect.jsx` ("2025-26" … the first season each one's data covers, checked against its route: Awards Race 2009-10, Impact 1949-50 (BPM/VORP from 1973-74, said in the label), Matchup Finder 2017-18, Offensive Style 2012-13, Heliocentricity 2013-14, the rest 2009-10) and open on 2025-26, except Career Trajectory, which keeps 2021-22 on purpose (later seasons exist to check the projection against). Playoff Forecaster's default player is now Shai Gilgeous-Alexander (15 playoff games in 2025-26; Jayson Tatum played 6 after his injury). Test: `test_analytics_season_pickers`.

### R8-044 · The Simulator's finish-odds cells were unreadable in Ink
- **Severity:** looks wrong · **Step:** 2b · **Status:** fixed in the Step 2b commit
- **Where:** Season Simulator, the 1st-15th "Finish" cells (`SeasonSimulator.jsx` `Seeds`, `simulator.css`). Each cell is tinted with the brand orange in proportion to its odds; from 50% the digit turned `--on-brand` (#0d0d0d).
- **Reproduce:** Ink, any team with a likely seed: dark digits on a darkened orange, 1.1:1 at 50% and 3.4:1 at 80%; light digits under 50% fell to 4.4:1. Paper was fine.
- **Found by:** the scanner, once it could read `color-mix()` backgrounds (it had passed the cells before). **Fix:** digits use `--text` in both themes and Ink caps the tint at 65% of the cell (`--seed-max`, the Data Quality heat-map pattern): ≥ 4.68:1 at every tint in both themes, on both surfaces. Test: `test_simulator_seed_cells_read_in_both_themes` (computes every 5% step).

### R8-045 · Live Scores showed no team names, mixed logos and an unreadable fallback badge
- **Severity:** looks wrong · **Step:** 2b · **Status:** fixed in the Step 2b commit
- **Where:** `LiveScores.jsx` game cards and box-score header. `/games/by-date` returns `name: ""` and a thesportsdb logo or null (R8-002), so each card showed a logo with no name, and for a null logo a white abbreviation on the team colour (NYK: 2.56:1). The cards weren't reachable from the keyboard, nothing linked to a team, and preseason games weren't marked as such.
- **Found by:** the scanner (badge contrast) and screenshots. **Fix:** the card uses the app's full team name (`TEAM_NAME_TO_ABBR` inverted) and NBA.com logo (`TeamLogo`, the same as every other page) wrapped in `TeamLink`; the card is a keyboard button with a label; a Preseason / Playoffs / Play-in badge from the NBA game id. Test: `test_live_scores_names_and_logos_come_from_the_app`.

### R8-046 · Player names on three Teams pages weren't links
- **Severity:** looks wrong · **Step:** 2b · **Status:** fixed in the Step 2b commit
- **Where:** Trade Analyzer and Trade Impact (the four player cards; Trade Impact's likely five) and Team Comparison (the two top-8 rosters) drew headshot + name by hand: no profile link, no watchlist star (the same pattern as Hall of Fame in R8-039).
- **Found by:** following links (Trade Analyzer: 0 player links on a run trade). **Fix:** `PlayerName`. Test: `test_team_pages_link_player_names`.

### R8-047 · The team page's Lineup Chemistry link lost the season; Coaching Decisions had no way to the team page
- **Severity:** broken (link to the wrong view) · **Step:** 2b · **Status:** fixed in the Step 2b commit
- **Where:** `TeamProfile.jsx` "Open Lineup Chemistry →" called `onNavigate('analytics', 'lineups')` with nothing, so a 2004-05 Seattle page opened Lineup Chemistry on 2025-26. Lineup Chemistry has no team filter, which the label didn't say. Coaching Decisions with a team picked had no link to that team's page (Possession Explorer has one).
- **Found by:** clicking every navigation button on the team pages. **Fix:** the link passes the season and reads "Open Lineup Chemistry (every team's best and worst fives, 2004-05) →"; Coaching gains "Open the BOS team page". Test: `test_team_page_lineup_link_keeps_the_season`.

### R8-048 · The Games hub didn't keep the open game in the link
- **Severity:** looks wrong · **Step:** 2b · **Status:** fixed in the Step 2b commit
- **Where:** `GamesHub.jsx`: the chosen game was component state only, so a copied or saved link (or a reload) always opened the hub.
- **Found by:** the copy-link round trip. **Fix:** `?page=games&g=trivia` (`guess`, `blurred`, `higherlower`, `trivia`, `guessgame`) through `useInitialParams` / `useUrlSync`; checked in the browser (a fresh load of the link opens Trivia). Test: `test_games_hub_keeps_the_open_game_in_the_link`.

### R8-049 · The Dashboard requests next season's MVP prediction and gets a 404 on every load
- **Severity:** looks wrong · **Step:** 4 · **Status:** open
- **Where:** `DashboardHome.jsx` `resolveSeasonWithData()` starts at `/meta/current`'s season (2027) and walks back on failure, so every Dashboard load logs `404 GET /mvp/predict/2027` in the browser's network panel before 2025-26's answer. The page itself is right ("MVP Favorite · 2025-26").
- **Found by:** the scanner's failed-request list. Fix with R8-004's season work in Step 4 (e.g. a route that says which seasons the award models cover), not a guess in the page.
- **Step 2c:** the landing page asks for it too (`404 GET /mvp/predict/2027`, twice in dev's StrictMode, on every load).

### R8-050 · Older charts drew in dark-theme pastels: 1.2-2.6:1 on Paper
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** hard-coded Tailwind-400 colours (`#38bdf8`, `#f87171`, `#facc15`, `#a78bfa`, `#34d399`, `#f59e0b`, `#94a3b8`, `#00e5ff`, ...) made for a dark page, in 16 Analytics components and two shared ones: Model Validation (ROC/calibration lines and their AUC/label text, SHAP bars), Garbage-Time Deflator (slope chart, focus label 2.0:1, bucket bars and labels), Contract Value ("fair value" line and text 2.0:1, dots), DAD Index (position dots), Draft Prospects (projected-outcome numbers as text, 2.6:1), Career Trajectory, Player Archetypes (10-colour palette, sparklines), Offensive Style, Radar Compare (also the player names in its table), Trend Analysis, Referee Tendencies (diff cells as text: 2.0-2.6:1 in Paper), Game Replay (win-probability dots, what-if markers), Matchup Finder (FG% text), Spacing Lab, Length Matters, Prediction Ledger; `common/ShotCourt.jsx` (every shot chart: made shots `#00e5ff` 1.2:1 on Paper's court, misses at 45% opacity 2.1:1), `PlayerDetailModal` bars; Awards Race's streak badge (`dashboard.css`, `#facc15` text).
- **Reproduce (before):** Paper theme, Analytics › Model Validation: "AUC = 0.996" in `#38bdf8` on cream, 2.02:1; Shot Charts: made-shot dots almost invisible on Paper.
- **Found by:** the scanner's text check (5 pages), then its new chart-mark check (3:1 for dots, lines, bars, WCAG 1.4.11), then grep for the same hexes.
- **Fix:** ten chart series tokens `--series-0..9` in `tokens.css` (Paper and Ink values, each ≥ 3:1 on `--bg`, `--surface` and `--surface-2` in its theme; 0-7 equal the Workbench's `--wb-series-N`); text uses `--text`, `--streak`, `--compare-a/-b` (≥ 4.5:1) or `--positive`/`--negative`. Shot dots: made `--series-5` (teal), missed `--series-8` (red) at 65% opacity (≥ 3:1 on the court in both themes); the Workbench shot block's caption now says "teal dots went in". Scatter dots at 80-90% opacity where needed (Archetypes, Offensive Style, Length Matters). Tests: `test_chart_series_read_in_both_themes`, `test_no_dark_theme_pastels_left_in_charts`. The same hexes remain only as backgrounds under dark text or decorative borders on 2a/2b pages (News tags, Team Comparison W/L boxes, Hall of Fame's 75 badge, Trivia/Comparison borders), which scanned clean.

### R8-051 · Small-sample rows and labels were faded below 4.5:1
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** DAD Index (rows at 55% opacity, detail card at 75%), Referee Tendencies (rows at 55%), Matchup Finder (rows at 45%, plus an extra "small sample" cell the header didn't have), Garbage-Time Deflator (player detail at 70%, non-focus labels at 50%).
- **Reproduce (before):** DAD Index in Paper: a small-sample row's numbers at 4.23:1 and its "± 6.4 · n = 230 · small sample" line at 2.81:1; Matchup Finder (Curry as scorer): "100%" at 2.08:1.
- **Found by:** the scanner (default views and, for Matchup Finder, after "Find Matchups"). **Fix:** no opacity on text; every such row already says "small sample" in words (Matchup Finder now under the name, which is a `PlayerName` link). Test: `test_small_sample_rows_are_not_faded`.

### R8-052 · Shot value's sorted column header was 1.2-2.0:1
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** Shot Charts › Shot value, the sorted header ("SVA"): `.sv-sort--active` set `--brand-text` on the header cell that theme.css paints brand orange.
- **Found by:** the scanner. **Fix:** the button inherits the sorted header's dark text. Test: `test_2c_contrast_fixes_in_css`.

### R8-053 · The Workbench Finder's "How it was measured" link was browser blue in Ink (1.9:1)
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** Finder block, the line under the type-in box (`.wb-fd-ask-about a` had no colour). **Found by:** the scanner. **Fix:** `--brand-text`. Test: `test_2c_contrast_fixes_in_css`.

### R8-054 · Data Quality's game-count links were 4.3:1 on the heat-map tints
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** Data Quality › classes and seasons tables, `.dq-link` (brand text on the tinted cells: 4.32:1 Paper, 4.35:1 Ink). **Found by:** the scanner. **Fix:** `--text`, still underlined. Test: `test_2c_contrast_fixes_in_css`.

### R8-055 · Methodology ran 37 px off a 375 px screen
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** the win-probability card's live note: its source badge ("NBA_API + ESPN VIA SPORTSDATAVERSE (PLAY-BY-PLAY)") doesn't wrap. **Found by:** the scanner (overflow). **Fix:** `.meth-live-note .pill-badge` wraps (the Quality map's pattern). Test: `test_2c_contrast_fixes_in_css`.

### R8-056 · The landing page's footer was 1.1-2.0:1 on orange
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** `.lp .app-footer p` took shell.css's `--text-3` on the landing's orange (Paper 2.03:1, Ink 1.06:1). **Found by:** the scanner. **Fix:** inherits the landing's ink. Test: `test_2c_contrast_fixes_in_css`. (The outlined title "number." was a scanner false positive: it reads by its 3 px ink stroke; the scanner now reads `-webkit-text-stroke`.)

### R8-057 · Game Replay didn't keep a picked game in the link
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** Analytics › Game Replay wrote `?game=` only when opened from a link; a game picked from its list was lost on Copy link, Save or reload, and the tool had no Copy link / Save buttons. **Found by:** the copy-link round trip. **Fix:** a picked game goes into the link (the list's first game stays out of it until something else is picked); Copy link and Save added; checked in the browser (reopening the link shows the picked game). Test: `test_game_replay_keeps_a_picked_game_in_the_link`.

### R8-058 · Saved analyses titled every Analytics tool "College & Draft"
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** `SaveViewButton.autoTitle()` looked the page up by nav id, and the four Analytics nav entries share the id `analytics`, so the last one won: saving Rim Deterrence gave "College & Draft (rim, season: 2026)". A report item's source line read "ANALYTICS & PREDICTIONS (rim tab)".
- **Found by:** saving a view in the browser and reading the Saved page. **Fix:** the tab list moved to `components/analytics/analyticsTabs.js` (small, so the shell can import it); titles read "Analytics › Rim Deterrence (season: 2026)" and report sources "Analytics › Rim Deterrence". Views saved before keep their old title (editable on the Saved page). Test: `test_saved_titles_name_the_analytics_tool`.

### R8-059 · Brand-orange chart marks are 2.8-2.9:1 on Paper
- **Severity:** looks wrong · **Step:** 10 (owner's call) · **Status:** open
- **Where:** charts that draw data in `--brand` (#ff5b14): Rim Deterrence's on-court bars, Luck & Schedule's team dots, College → NBA's highlighted picks, Model Report Card's dots, Data Quality's "does it matter" dot (2.81-2.93:1 on Paper's `--surface`; fine in Ink). About 100 places use `var(--brand)` as a fill, most of them buttons and badges with dark text (fine).
- **Found by:** the scanner's new chart-mark check. Each of these charts also prints the value as text, so nothing is lost; still under WCAG 1.4.11's 3:1. A chart-only Paper orange (e.g. #e04a0d, 3.1-3.8:1 on the three Paper backgrounds) would fix it without touching buttons: a brand decision, so the owner's.

### R8-060 · Data Quality's first load waits 3-4 s on two live checks
- **Severity:** slow · **Step:** 8 · **Status:** open
- **Where:** `?page=quality` fires one `/data-quality/check/{key}` per class (14, twice in dev's StrictMode); cold, `score_fields` takes 3.2-4.3 s and `clock_offset` 3.0 s (fast once cached).
- **Found by:** the scanner's slow-request list.

### R8-061 · Data Coverage showed raw page ids ("analytics#onoff", "team") in "Used by"
- **Severity:** looks wrong · **Step:** 2c · **Status:** fixed in the Step 2c commit
- **Where:** `DataCoverage.jsx` labelled `used_by` ids from a hand-kept list that missed eight of the 30 ids in `COVERAGE_MAP`: `analytics#onoff`, `analytics#replay`, `assists`, `bestgames`, `plays`, `rapm`, `simulator`, `team` showed as raw ids, and "team" was a button to a team page with no team.
- **Found by:** clicking every navigation button on the page. **Fix:** labels come from the nav (`navConfig.js`) and the Analytics tab list; the player and team pages are named, not linked (they need an id); the page also gets Save next to Copy link. Test: `test_coverage_names_every_page_it_links`.

### R8-062 · The Dashboard / Standings / Team Comparison stored fallback was wrong for every team, and the live team block never loaded
- **Severity:** wrong number · **Step:** 3 · **Status:** fixed in the Step 3 commit
- **Where:** `GET /meta/current` (`api/routers/meta.py`), the basic per-game block of Team Comparison and the Standings page when stats.nba.com has nothing; `impact_core.fetch_nba_api_team_stats`.
- **Reproduce (before):** `/meta/current` → `team_stats.CLE.ppg` 147.5 (the real 2025-26 figure is 119.5): the fallback summed every player's season row under his last team and divided by the roster's most games played; every team was off, by up to 28 points a game. The standings fallback took each team's record as its best player's `w_pct` × 82: CLE 82-0, HOU 82-0, POR 70-12 for the real 52-30 / 52-30 / 42-40 (all 30 teams wrong, up to 40 wins). And the live block never worked: stats.nba.com's LeagueDashTeamStats has no `TEAM_ABBREVIATION` column, so the parser skipped every row and returned None, which means **Team Comparison has always shown the wrong fallback** (R8-004 noted the missing season label, not the numbers).
- **Found by:** Step 3's probe of team records across pages; then calling the live endpoint directly.
- **Fix:** `db_standings()` reads `team_seasons` (the real record); `db_team_stats()` takes points per game from the final scores (`game_scores`) and rebounds, assists, steals, blocks and the shooting percentages from the play-by-play lines summed per team (within 0.5 rebounds, 0.2 assists and 0.4 FG% points of NBA.com's own 2025-26 team block, checked live on 2026-10-05; team rebounds belong to nobody in the lines). The live parser (`parse_team_stats_rows`) derives the code from the team name and returns None when nothing has been played (every GP 0), so the stored season shows instead of a table of zeros; the response says `team_stats_source` and `team_stats_season`. Old → new on Team Comparison today: CLE 147.5 → 119.5 points, 52.6 → 44.4 rebounds, 36.9 → 28.3 assists. Tests: `test_standings_and_team_stats_fallback_are_the_real_season`, `test_2024_25_records_and_home_road_splits`. Step 4 still owns the season label (R8-004).

### R8-063 · Team Comparison's advanced block disagreed with the team page
- **Severity:** wrong number · **Step:** 3 · **Status:** fixed in the Step 3 commit
- **Where:** `GET /teams/compare/{a}/{b}` (`api/routers/team_comparison.py`), the Offensive / Defensive / Net Rating and Turnovers rows.
- **Reproduce (before):** CHI 2019-20: `/teams/compare` 104.2 / 107.1 / −2.9, the team page 106.7 / 109.8 / −3.1. The route averaged the players' own on-court ratings weighted by games (a traded player's whole season under his last team): over all 510 team-seasons 0.9 points from Basketball-Reference's team net rating on average, 4.0 at worst. Turnovers summed the players' rows and divided by the roster's most games.
- **Found by:** Step 3's probe.
- **Fix:** the ratings are `team_seasons`' (Basketball-Reference, the team page's numbers); turnovers per game are NBA.com's team box score (`game_team_box`, team turnovers included, from 2020-21; earlier seasons sum the players' rows over the team's games, said in `tovSource`). The page's tooltip says so. Test: `test_team_comparison_advanced_block_is_the_team_page`.

### R8-064 · RAPM page ranks: tied players ranked one apart from the profile
- **Severity:** looks wrong · **Step:** 3 · **Status:** fixed in the Step 3 commit
- **Where:** `GET /rapm` (`api/routers/rapm.py`), `rapm_rank` / `orapm_rank` / `drapm_rank`; the profile ranks with `RANK()`.
- **Reproduce (before):** the stored ratings carry three decimals, so 5-19 pairs a season tie; the page numbered them 1, 2, 3 … in sorted order, the profile gave both the same rank (Finney-Smith, three-season RAPM 2023-24: page 194, profile 193).
- **Found by:** Step 3's probe (one of 56 rank comparisons). **Fix:** competition ranking on the page. Test: `test_rapm_page_ties_share_a_rank`.

### R8-065 · The shot-zones route counted playoff and play-in shots
- **Severity:** wrong number · **Step:** 3 · **Status:** fixed in the Step 3 commit
- **Where:** `GET /shots/player/{name}/zones?season=` (`api/routers/shot_charts.py`). No page calls it today (R8-017's `fetchPlayerShotZones`); the profile uses `/player-profile/{id}/shot-zones`.
- **Reproduce (before):** Bane 2024-25: 1,117 attempts (regular season 1,018), so its zone FG% included 99 playoff shots while the league zones it is meant to be compared with, the profile's zones, shot-making, the quality map, shot value and the Shot Charts page all count the regular season.
- **Found by:** Step 3's probe. **Fix:** regular season only (`games: "regular season"` in the response). Tests: `test_shot_zones_route_counts_the_regular_season`, `test_shot_totals_everywhere`.

### R8-066 · The live standings had no team code for the Clippers
- **Severity:** looks wrong · **Step:** 3 (Step 4's page) · **Status:** fixed in the Step 3 commit
- **Where:** `impact_core._fetch_nba_api_standings_uncached`: stats.nba.com names the team "LA Clippers", which wasn't in `TEAM_NAME_TO_ABBR`, so the row had `abbr: ""` (no logo, no team link on Standings; Team Comparison's record look-up by code found nothing).
- **Found by:** comparing the live 2025-26 standings with `team_seasons` (29 of 30 matched; the Clippers' row had no code). **Fix:** the alias. Test: `test_standings_and_team_stats_fallback_are_the_real_season` (the parser's name mapping).

### R8-067 · `postseason_games` dated every evening game a day late
- **Severity:** wrong number · **Step:** 3 · **Status:** fixed in the Step 3 commit
- **Where:** `scripts/fetch_postseason_games.py` stored ESPN's UTC stamp (`e["date"][:10]`): a 8:30 pm ET tip on June 5 is "2025-06-06T00:30Z". 909 of 1,458 games (every evening game 2009-10 to 2024-25) were a day late: the 2025 Finals read June 6-23 for June 5-22, the 2025 play-in's late games April 16/17/19 for 15/16/18. Nothing reads the dates yet (`build_season_sim.py` uses stage and teams, Best Games' rounds join by id), so no page showed them.
- **Found by:** `test_2025_finals_games_and_champion` against Wikipedia's dates.
- **Fix:** the script converts to US Eastern (`local_date()`, like every other date in the database) and skips ESPN's placeholder events; re-fetched 2026-10-05 and diffed against a snapshot: 909 dates moved one day earlier, no other column changed, 1,458 rows (one 2010-11 first-round game ESPN's scoreboard dropped on the first pass was fetched again). **Not on Layerbase** (for the Step 10 sync).
