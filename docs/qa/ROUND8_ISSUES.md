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

**Round 8.5 (2026-10-06, Step A): 86 entries: 77 fixed, 1 won't fix, 8 open** (R8-086 added, for round 9 step 1).

**Final counts (2026-10-06, Step 10, round 8 closed): 85 entries: 77 fixed, 1 won't fix, 7 open.** By severity: 8 broken (8 fixed), 27 wrong number (25 fixed, 2 open: R8-028, R8-032), 8 slow (6 fixed, 1 won't fix: R8-013, 1 open: R8-012), 42 looks wrong (38 fixed, 4 open: R8-010, R8-059, R8-071, R8-082). Every open one is an owner's call or waits for data (each entry says which). Step 10 closed R8-014 (every route without `_source` accounted for) and found no new app defect; the round's one-page summary for the guide is at the end of the Step 10 section.

Counts (2026-10-06, after Step 8): **85 entries**, 8 open, 76 fixed, 1 won't fix: 8 broken, 27 wrong number,
8 slow, 42 looks wrong. Step 8 fixed R8-011, R8-017 and R8-060 and three it found (R8-083 to R8-085), measured R8-013
(won't fix: it doesn't delay the first paint) and left R8-012 open (a page decision). After Step 7: 82 entries, 12 open,
70 fixed: 8 broken, 27 wrong number, 5 slow, 42 looks wrong. Step 7 fixed R8-009 (its outside facts; the profile part is R8-082, open), R8-029, R8-030,
R8-068, R8-073 and two it found (R8-080, R8-081), fixed part of R8-071, and left R8-010, R8-028 and R8-032 open with
the reason. After Step 6c: 79 entries, 16 open, 63 fixed: 8 broken, 27 wrong number,
5 slow, 39 looks wrong. Step 6c fixed R8-074 and R8-076 and two it found (R8-078, R8-079). After Step 6b: 77 entries, 18 open, 59 fixed: 8 broken, 26 wrong number,
5 slow, 38 looks wrong. Step 6b fixed R8-026 (the models fitted on the stints) and two it found (R8-075, R8-077), logged R8-076 (6c) and did R8-074's rerun (its rewrite is 6c's). After Step 6a: 74 entries, 18 open, 56 fixed: 7 broken, 24 wrong number,
5 slow, 38 looks wrong. Step 6a fixed R8-022 to R8-025 and R8-027, the stints' part of R8-026 (open for 6b's
refits) and one it found (R8-072); it logged R8-073 (Step 7) and R8-074 (6b/6c). After Step 5 it was 71 entries,
21 open, 50 fixed (recounted from the entries; the Step 4 line said 22 open / 46 fixed, one off).
Step 5 fixed R8-018, R8-019, R8-020 and two it found (R8-069, R8-070), and logged R8-071 (open). Step 4 fixed 12: R8-001 to R8-008, R8-021,
R8-031 (Step 7's, taken with R8-002), R8-049, plus the Step 4 parts of R8-014 and R8-017; it found R8-068 (open). Before that: 6 from Step 3's
cross-page checks (R8-062 to R8-067, all fixed in its commit); 17 new from Step 1 (R8-001 to R8-017); 15 known
gaps already written down in README "Known real gaps" / Methodology open issues, listed so a step owns each
(R8-018 to R8-032); 11 from Step 2a's sweep of the Players pages (R8-033 to R8-043, 10 fixed in its commit);
6 from Step 2b's sweep of the Teams, Games and Today pages (R8-044 to R8-049, 5 fixed in its commit, which also
fixed R8-016 and the 2b parts of R8-004, R8-014 and R8-043); 12 from Step 2c's sweep of Analytics, Shot Charts,
the Workbench and the rest (R8-050 to R8-061, 10 fixed in its commit, which also fixed R8-015 and R8-043 and
the 2c part of R8-014).

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

## Step 4: live pages without stats.nba.com (2026-10-05)

Every page that called stats.nba.com live (the Step 1 table above) got one of the plan's three answers.
New module **`api/espn_live.py`** (scoreboard of a date, one game's box score, regular-season standings;
3 s timeout, short in-memory cache, None when ESPN doesn't answer); new composites in `impact_core.py`
(`games_by_date`, `game_boxscore`, `current_standings`, `standings_win_pct`); every remaining nba_api call
uses `_LIVE_REQUEST_TIMEOUT_SECONDS` = 3 (was 6, 30 or 45). Dates are US Eastern everywhere (the pages
compute "today" in America/New_York: `utils/date.js` `nbaDateIso`). 19 tests in
**`api/tests/test_round8_live.py`** (monkeypatched ESPN for the offline ones; the four that read ESPN for
real skip when it doesn't answer).

| Route (pages) | Decision | Before → after |
|---|---|---|
| `/games/by-date` (Live Scores, Dashboard, landing) | **(a) stored first** (`game_scores`, `postseason_games`) **then (b) ESPN**; (c) `status: unreachable` + message | 2026-10-20: 0 games in 6.2 s → 3 openers in 1.1 s; 2025-01-02: live call → stored in 0.04 s; ESPN down: "No games found" → "didn't answer within 3 s" |
| `/games/boxscore/{id}` (Live Scores) | **(b) ESPN summary**, ESPN or NBA id | `pm: "nan"` for DNPs → int / null with the reason; +/- column on the page |
| `/meta/current` standings (Dashboard, Standings, Team Comparison, Vegas proxy) | **(b) ESPN**, regular-season type; (a) `team_seasons` when ESPN doesn't answer; every block labelled with its season and source | 2026-27 rows ranked 0 → 1-15; "2027" over 2025-26 numbers → "Top Scorer · 2025-26", "record: 2026-27 (ESPN, live)" |
| `/meta/current` team block and scoring leader | **(c)** stats.nba.com with a 3 s timeout, else (a) stored, labelled | unchanged numbers, now named |
| `/leaders/{stat}` (Stat Leaders, app-shell prefetch) | **(a) stored** for stored seasons; (c) live only for a newer season, else the latest stored season with a note | empty "Top 10 · 2027" → "Top 10 · 2025-26" + note + badge |
| `/players/search` (CommandPalette, Comparison, Trend, Radar, Archetypes, Playoff Forecaster, Draft Prospects) | **(a) stored names only**, accent-insensitive, most recent first | up to 6 s a keystroke → ~10 ms |
| `/players/profile/{name}` (no page) | **(a)** DB only | 45 s live path gone |
| `/teams/with-without/{team}/{season}` (With/Without a Star) | **(a) stored** 2020-21 on (`game_scores` + `player_game_lines`, optional `player_id`); (c) live before 2020-21 with a 3 s timeout | DEN 2025-26 / Jokić 21 s → 36 ms; margins = real final margins (R8-021) |
| `/players/pair-synergy` (Player Comparison) | **(a)** observed pair from `pair_seasons` (2020-21 on); null with a note before | 45 s live two-man lineups → 80 ms |
| `/players/playoff-comparison/{name}`, `/players/heliocentricity` | **(c)** keep the live call (nothing stored), 3 s timeout, 503 "didn't answer within 3 s" | 45 s / 30 s hangs; a failed fetch read as "didn't make the playoffs" |
| `/odds/championship` (Vegas Scanner) | proxy win% from the same ESPN/stored standings, named (`win_pct_season`) | stats.nba.com standings |
| `/shots/player/*`, `/shots/league-zones/*` | live fetch **off by default** (`ENABLE_LIVE_SHOT_FETCH`), 404 with the reason | a page view could write `player_shots` (R8-007) |
| `/news/current` | unchanged (RSS; R8-011 is Step 8's), `_source` added | — |

Deleted from `impact_core.py` (723 lines): the cdn.nba.com liveData readers (403), balldontlie, the
thesportsdb team badges, the stats.nba.com scoreboard, box score, standings, player search, player
profile and two-man lineup fetches. Browser check (desktop app, harness deleted before the commit):
Live Scores today (5 preseason games, tip times, ESPN badge), yesterday (2 preseason finals, box score
with +/-), 2026-10-20; Standings, Dashboard (no 404 probe, labelled tiles), Team Comparison (labelled),
Stat Leaders (note + badge), With/Without DEN / Jokić; 375 px Ink Live Scores: no overflow, no console
error on any of them. Full suite 483 passed + 1 skipped (101 s), eslint 0, vite build clean. Nothing
written to any table; nothing to sync. Found on the way: R8-068 (percentage leaders without an attempts
floor, Step 7).

## Step 6a: the play-by-play lines and stints rebuilt (2026-10-05, the owner's OK)

One pass of the shared parser (`scripts/pbp_lineups.py`, no second parser) with five changes, then
`player_game_lines` → `team_game_totals` → `lineup_stints` (+ `lineup_stint_games`, `lineup_stint_seasons`,
`lineup_seasons`, `pair_seasons`) → `player_game_onfloor` (+ `_meta`) rebuilt in `rebuild_all.sh` order. All nine
tables were snapshotted (`zz_r86a_*`), diffed, and the snapshots dropped.

- **Names (R8-022):** after the in-game names, `player_season_stats` and the unique-since-2010 names (unchanged:
  every name they matched before still resolves the same way; all 433 name-only matches are active that season),
  `load_season_names()` adds a `player_bio` index: an exact normalised name that one player active that season has,
  only for one of his teams where he changed teams (`player_team_stints`). Plus five aliases ESPN's own feed states
  (the row's `player_name` field and its text name the same player): Jeenathan/Nate Williams, Kenny/Kenneth
  Lofton Jr., Charles/Charlie Brown Jr., Anthony/Cat Barber, Marcos Louzada Silva/Didi Louzada. 262
  name-team-seasons matched; 9 left (Matt Hurt, Rondae Hollis Jefferson, RJ Nembhard Jr., Yongxi Cui and five
  one-off typos; 262 occurrences), printed by `build_player_game_lines.py`.
- **Team-less substitutions (R8-023):** 12 in six seasons; the 11 naming nobody leaving duplicate the next, tagged
  substitution and are ignored; the one naming both players (Clarke for Allen, MEM 2023-24) goes to their team.
- **No team (R8-024):** a NaN team hint no longer becomes a player's team; the lines stop on a row without one.
- **On-court points (R8-025, R8-026):** free throws (attempt, make, points) are credited to the five on the floor
  at the foul in the stints and the lines (`is_foul_anchor()`, the rule `build_player_game_onfloor.py` validated);
  the lines' points use the stints' per-game pick (`points_method()`); 76,444 free throws move to an earlier stint,
  listed in the new `lineup_stints.foul_ft_actions` (and counted in `lineup_stint_games.fts_at_foul`).
- **3PA (R8-027):** the stints take the shot chart's two-or-three call on misses (`miss_three_calls()`), +7,152 3PA.

| Check | Result |
|---|---|
| Lines vs NBA.com season totals (players with 20+ games) | unchanged: GP 1.0001, minutes 1.0003, points 1.0001 of NBA.com's; mean minute error 0.30%; player-seasons off by > 5 min 430 → 427 |
| Lines vs Basketball-Reference (the 234 player-seasons that gained rows) | teams 234 of 234 on BRef's list; GP exact 228, within 1 for 233 (the other: Matt Hurt, still unmatched) |
| Stints reconcile per game | 7,220 of 7,232 games, as before (same 12 reasons) |
| Lines = stint sums (the build stops otherwise) | seconds and all 14 on-court columns, 154,345 of 154,345 player-games |
| Stints = `player_game_onfloor`'s own replay (the build stops otherwise) | 154,334 player-games, 0 differ; every full-five team-game sums to 5 × the margin (14,383 of 14,383) |
| ESPN box-score +/- (300 random games, seed 7, 6,453 player-games) | on-floor 98.2% exact (unchanged); stints 42.5% → 98.2%; lines' `tm_pts - op_pts` 36.6% → 98.2% |
| Full-minute team-games whose summed lines margin is 5 × the final | 9,698 of 12,882 (75%) → 14,389 of 14,401 |
| Tracked minutes (five identified a side, reconciled game) | 2020-21 93.7% → 99.98%, 93.9 → 99.81, 97.2 → 99.68, 96.0 → 99.60, 97.8 → 99.82, 99.84 → 99.84; fully tracked games 5,891 → 7,164 |

Diff: `player_game_lines` 152,469 → 154,345 rows (+1,878 for players matched by the fallback, 234 player-seasons;
−2 zero-second rows with no stats that existed only because a free throw at the shot credited them); 15 kept rows'
seconds changed (8 of the 9 phantom-minute player-games, e.g. Dončić 2022-01-30 45.4 → 37.1 min, the ninth being Kevin Pangos, who had no id and so no row before; Nate Williams' five
2022-23 Portland games and a teammate's, where ESPN's text called him "Jeenathan"; Ron Holland 2024-10-23
31.9 → 14.9 min, R8-072); own stats changed in 4 rows (3 assists, 2 steals, 1 block now credited to the right
player); one team ('NaN' → UTA). On-court FTA changed in 107,150 rows and points in 104,998 (the foul rule).
`team_game_totals`: OREB +776 in 840 team-games (rebounds by newly identified players), tracked seconds in 1,530;
FGA/FTA/TOV/points unchanged. `lineup_stints` 294,772 → 294,027; `lineup_seasons` 114,497 → 120,628 (minutes
674,387 → 697,609); `pair_seasons` 29,112 → 32,220; `player_game_onfloor` 152,461 → 154,334 rows, plus-minus
changed in 8. Tests: **`api/tests/test_round8_rebuild.py`** (11: the parser rules on made-up games, then the
tables), Embiid's 70-point box score gains Terquavion Smith (read from ESPN 2026-10-05), the 9-phantom-minute
pins in `test_consistency.py` / `test_smoke.py` now 0, coverage floors 0.93 → 0.995.

**Changed tables for the Layerbase sync** (207 MB): `lineup_stints` 102 MB, `player_game_lines` 38 MB,
`lineup_seasons` 26 MB, `player_game_onfloor` 21 MB, `pair_seasons` 5.9 MB, `team_game_totals` 2.8 MB,
`lineup_stint_games` 1.5 MB, `player_game_onfloor_meta`, `lineup_stint_seasons`.

**For Step 6b (stale until rebuilt; the tests that fail now say so):** rerun in `rebuild_all.sh` order
`build_event_clock.py` (its chart matching uses the parser's names), `build_player_on_off.py`,
`build_possessions.py`, `build_stat_stability.py` (its plus-minus/rating rows read `tm_pts`/`op_pts`),
`build_hot_streak_persistence.py`, `build_situational_splits.py`, `build_projections.py`, `build_rapm.py`,
`build_shot_value.py`, `build_rating_tracker.py`, `build_rotations.py`, `build_rim_deterrence.py`,
`build_assist_network.py`, `build_play_finder.py`, `build_best_games.py`, `build_team_zone_mix.py`,
`build_coaching_decisions.py`, then the paper stage (`paper_xrapm.py`, `paper_eval.py`, `paper_tests.py`,
`build_pregame_availability.py`, `build_lineup_predictor.py`, `build_report_card.py`, `paper_data_audit.py`,
`build_data_quality.py`, `paper_beliefs.py`, `paper_ablations.py`); restart impact_api. **`paper_xrapm.py` maps
free throws to stints by action range:** it must move the ones in `foul_ft_actions` (some lie in no stored
range: logged during a zero-second stint that was credited nothing). Failing now, all stale downstream (13 + 5
errors): on/off ×3 (`test_on_off_everywhere`, `test_on_plus_off_equals_team_total_over_games_played`,
`test_on_off_table_equals_the_workbench`), assist network ×2, possessions, shot value, xRAPM, data quality ×2,
the paper audit ×2, the manifest and the paper generators (Step 6c). Methodology cards and README numbers on
the rebuilt models (RAPM's "93.7 to 99.8% of minutes", rim, assists, On/Off, Play Finder, xRAPM) are 6b's to
re-read. **For Step 6c:** R8-074.

## Step 6b: everything downstream of the lines and stints rebuilt (2026-10-05)

28 scripts rerun in `rebuild_all.sh` order, one at a time: `build_event_clock.py` (its chart matching uses the
parser's names), `build_player_on_off.py`, `build_possessions.py`, `build_stat_stability.py`,
`build_hot_streak_persistence.py`, `build_situational_splits.py`, `build_projections.py`, `build_rapm.py`,
`build_shot_value.py`, `build_rating_tracker.py`, `build_rotations.py`, `build_rim_deterrence.py`,
`build_assist_network.py`, `build_play_finder.py`, `build_best_games.py`, `build_team_zone_mix.py`, `compute_wpa.py`
(the event clock moved), `build_coaching_decisions.py`, then the paper stage (`paper_xrapm.py`, `paper_eval.py`,
`paper_tests.py`, `build_pregame_availability.py`, `build_lineup_predictor.py`, `build_report_card.py`,
`paper_data_audit.py`, `build_data_quality.py`, `paper_beliefs.py`, `paper_ablations.py`). The ledger was not touched.
89 tables were snapshotted (`zz_r86b_*`), every one diffed by content hash, the snapshots dropped. 82 changed;
identical: `stat_year_to_year`, `shot_value_shots` (every price: the location fits don't read the rebuilt tables),
`play_finder_games`, `team_zone_mix`, `coaching_decision_summary`, `paper_data_audit_classes`,
`paper_ablation_shot_games`. Every build's own checks passed (paper_eval reproduces the tracker; the report card
reproduces paper_eval; Data Quality reproduces all 267 audit rows and the stored tests; ablations reproduce
paper_eval).

**Code changed (each needed by 6a's free-throw rule or the emptied audit class):**
- `scripts/paper_xrapm.py`: `map_to_stints()` moves each free throw to the stint whose `foul_ft_actions` lists it
  (asserts every listed action is a parsed free throw and every one is found: 76,444). Tracked stints whose attempts
  differ from the stored FGA/FTA: 0 and 0, as before 6a; unmapped attempts 0; residual 309 → 338 points (192 → 214
  stints) over 1.24M attempts.
- `scripts/build_rotations.py`: passes `miss_threes` (R8-075).
- `scripts/paper_data_audit.py`: the final print handled a None value (the team-less class's min/max excess, now
  empty) by crashing after writing; `api/data_quality_lib.py` words that class and sub-1% shares (`_pct_auto`).

**What moved** (old → new; every card/README number re-read, the rest unchanged at the precision shown):

| Result | Before | After |
|---|---|---|
| Tracked minutes / attempts on the stints | 93.7-99.8% | 99.6-99.98% (on/off 99.98-100%) |
| On/off rows; Jokić 2023-24 / 2024-25 on−off | 3,665; +23.7 / +24.2 | 3,920; +23.8 / +24.1 |
| RAPM next-season RMSE 2025-26 (BPM / 3-season / prior / one-season) | 15.33 / 15.46 / 15.53 / 15.91 | 15.31 / 15.44 / 15.52 / 15.87 |
| Jokić one-season RAPM 2020-21 | 69th (+1.8 ± 1.0) | 38th (+2.5 ± 1.1) |
| Rating Tracker settings (λ₀, λq, λb, k, φ) | 4,918, 3,424, 11,050, 0.76, 0.89 | 421, 576, 10,443, 0.58, 0.80 (R8-076) |
| Tracker vs BPM, protocol test season | −0.01 [−0.13, +0.10] | **+0.22 [+0.05, +0.40]** (BPM ahead; tune −0.29, validate −0.26 still tracker) |
| RAPM + prior vs BPM (tune / validate / test) | −0.10 / −0.26 / +0.30 | −0.16 / −0.21 / +0.29 (reversal holds) |
| Shot-aware xRAPM vs actual-points one-season | +0.11 / +0.66 / −0.12 | +0.14 / +0.58 / −0.08 |
| Report Card: tracker vs BPM per possession | 4 of 4 seasons (p 0.02) | 3 of 4 (p 0.47) |
| Lineup Predictor: season so far adds (test) | +4.3 [0.7, 9.8] | +4.4 [0.6, 10.2]; units 78,205 → 81,816 |
| Availability odds (test log loss) | −0.0173 [−0.0276, −0.0068] | −0.0173 [−0.0276, −0.0069] |
| Data Quality: flagged games | 1,569 (1,327 unidentified player) | 315 (56); no conclusion flips in 430 cells |
| Ablations: possession weights, one-season RAPM, test | +0.18 [0.03, 0.33] | +0.08 [−0.07, 0.23] |
| Rim: rim-attempts gap clear of zero | 15-20% a season | 17-22%; Gobert 2nd, 1st, 1st, 3rd, 1st, 20th |
| Assists with an unidentified passer | 1,149 (0.31%) | 18 |
| Coaching: 2-for-1 | +0.42 [0.32, 0.50] | +0.43 [0.34, 0.52] (others identical) |
| Possessions | 1,442,608; clock_ok 72.5% | 1,442,590; 73.0% (steal 1.306 → 1.305 per possession) |
| Beliefs: split tests at p < 0.05; per-player streak FDR | 3,929 of 73,319; 406 | 3,924 of 73,330; 412 (clutch, luck, referees identical) |

Unchanged at the precision shown: stability M values on the card, hot-streak shares and their null centres, split
league effects, projection backtest (501 current projections moved ≤ 0.0015; the locked ledger's minutes and BPM
projections not at all), Shot Value prices (free-throw log loss 0.5262 → 0.5266), Best Games sniff tests (178
games' excitement moved slightly), Clutch WPA (12 of 184), simulator and pre-game numbers, the Report Card's
pre-game/simulator/shot rows.

Tests: full suite 505 passed, 1 skipped (ledger), 2 failed + 5 errors, all Step 6c's: the manifest
(`test_manifest_is_current_and_tampering_is_caught`) and `paper_numbers.py` / the figures
(`test_printed_rows_have_known_formats_and_defined_macros`, test_paper_numbers ×3, test_paper_figures ×2), which
now stop on the audit's None value for the emptied team-less class before reaching their claims (R8-074). The 13 + 5
of Step 6a's list pass except those. Restated tests (the results changed, not the checks): the audit's re-derived
counts (`miss_threes_as_twos` 8,688 → 8,708: more names now line up with the chart; the team-less class adds 0
player-games), the tracker's next-season scale bound (0.9-1.1 → 0.8-1.1, 2025-26 at 0.85), and Rotations'
glue-back exceptions pinned by game id (R8-075).

**Changed tables for the Layerbase sync** (82 tables, ~1.57 GB, on top of 6a's 207 MB): `paper_eval_predictions`
391 MB, `possessions` 295 MB, `pbp_event_clock` 287 MB, `play_finder_events` 211 MB, `paper_xrapm_stints` 80 MB,
`paper_ablation_predictions` 57 MB, `projection_backtest_rows` 37 MB, `pregame_availability_players` 36 MB,
`player_situational_splits` 30 MB, `lineup_predictor_units` 28 MB, `report_card_units` 27 MB, `paper_beliefs` 20 MB,
`report_card_game_sums` 14 MB, `rotation_closing_stints` 8 MB, `coaching_decisions` 7 MB, `assist_pairs` 6 MB,
`data_quality_game_flags` 5 MB, and 65 smaller ones (every other table the 28 scripts write except the 7 identical
ones above).

**For Step 6c:** `scripts/rebuild_all.sh paper-inputs`; `paper_numbers.py` must first decide how the paper words the
team-less class now that no game line carries its error (it crashes on the None excess); the claims that break are
the paper's sentences on the stints (R8-074), the Rating Tracker (R8-076: "never behind BPM out of sample" no longer
holds on the test season) and the ablation on possession weights.

## Step 6c: the paper on the rebuilt data (2026-10-06)

`scripts/rebuild_all.sh paper-inputs` passes again (manifest digest `6bd7540fca2384ca`, 1,353 macros in `paper/numbers.tex`,
270 plotted numbers checked); `paper_numbers.py --check --paper` passes for the 8- and 6-page versions; `scripts/paper_build.sh`:
long 18 pages, 8p 8, 6p 6, each in both review states, 0 overfull boxes, 0 undefined references. Copies of the three papers
before the step: `paper/versions/nba_hub_paper{,_8p,_6p}_2026-10-06_r8.tex` (and `numbers_2026-10-06_r8_before.tex`).

**Generators (R8-074):** `paper_data_audit.py` stores the team-less class's min/max excess (NULL since 6a) without a macro,
prints the on-court class as a count (`DqOnCourtOff`, 12 of 14,399 team-games; `paper_numbers.py` claims all 12 are in
unreconciled games), and its table now calls the team-less class **repaired** ("ignored, or given the players' team; none
left in the game lines": `paper_numbers.py` claims 0 player-games and 0 lines without a team) and the unidentified class
"matched by exact name; the rest's stints left out" with the minutes left untracked printed to two decimals (0.02-0.16%, was
2-6%). Data Quality follows: a team-less substitution no longer makes a game "flagged" (`data_quality_lib.LEVEL_OF` →
worked around): 315 → 306 flagged games. Reran `paper_data_audit.py`, `build_data_quality.py` (all 268 audit rows
reproduce) and `paper_ablations.py` (R8-078; full models reproduce paper_eval exactly).

**Claims:** 18 of `paper_numbers.py`'s claims failed on the rebuilt data. Each sentence was rewritten in every length that
carries it, and each claim now states what the data say (no number typed, no model re-tuned on the test season); one claim
that guarded no sentence (the three-season window ahead of RAPM + prior on the test season) was dropped; new claims guard
the new sentences (the tracker's one losing season in the report card is the protocol's test season; the flagged games are
mostly chart gaps; the lineup shares may read up to `LpNoiseOverPct` = 15% high).

**Conclusions that moved (old → new):**
- Rating Tracker vs BPM, test season: level, −0.01 [−0.13, +0.10] → **behind, +0.22 [+0.05, +0.40]** (p 0.009); still ahead
  on tune (−0.29) and validate (−0.26). Abstract, contribution 3, the tracker section and the conclusion said it "ties BPM" /
  is "the first RAPM version never behind BPM out of sample": now "trails BPM on the test season by more than its interval".
- Lowest test-season next-season error in Table rapm: the tracker (15.31) → **BPM** (15.30; tracker 15.53, fourth). Bold moved.
- Tracker vs RAPM + prior: ahead on tune and test → ahead on tune only, level on validate and test; vs the three-season window:
  ahead in every phase → level in every phase; year to year: more reliable than BPM → **less** reliable than BPM (0.71 vs
  0.74, p 0.04), still more than every RAPM version.
- Report card: BPM vs RAPM + prior split 2-2 → 1-3 (pooled +0.06 [−0.33, +0.44], τ 0.22: still a property of the seasons);
  tracker per possession ahead of BPM and RAPM + prior in every season, outside the interval → 3 and 4 of 4, both inside
  (p 0.47, 0.17); by game the tracker loses only 2025-26.
- Ablations: prior at scale 1 ahead on the test season outside its interval → at its edge (−0.09 [−0.17, 0.00], p 0.05);
  the free minimum the rule sets aside λ 12,000 → 8,000 (same pattern: ahead on test, level on validate); possession weights
  help one-season RAPM in every phase outside the interval → on tune and validate only (test +0.08 [−0.07, +0.23]); RAPM +
  prior without weights worse on tune and validate → on tune only. "The best setting moves by more than one season's
  interval" → "differs from season to season".
- Data quality: flagged games 1,569 → 306, now mostly chart gaps (200), not unidentified players (56). Without them the
  RAPM + prior − BPM gap: test 0.30 → 0.13 inside random drops → 0.29 → **0.21 [0.01, 0.40], past every random drop**
  (0.24-0.37, p 0.03 = the floor of 30 draws), tune lead −0.16 → −0.19 (p 0.03), validate's interval now reaches zero; no
  sign changes. The one effect beyond random drops was the who-played gain halving without the flagged games: now the same
  (−0.0175 → −0.0173, p 0.68). Controlled cells beyond random drops 35 → 26 (13 expected).
- Data section: minutes without ten identified players 2-6% → 0.02-0.16% a season; points: "a free throw counts for the five
  on the court at the foul" added (long version).
- Smaller: lineup shares may read "about a tenth" → "up to about 15%" too high; xRAPM + prior vs RAPM + prior on the test
  season −0.04 → 0.00 (still level); xRAPM + prior vs BPM on test +0.25 (p 0.07) → +0.30 (p 0.04), "never ahead of BPM" holds.
- Unchanged: RAPM + prior vs BPM reversal (−0.16 / −0.21 / +0.29, each outside), on/off worse than zero, xRAPM's negative
  result, the shooter-aware price, shot quality vs shot-making, pre-game and simulator results, beliefs (0 clutch, 0 split
  survivors; referees, luck), coaching (2-for-1 +0.43, won challenge), who-played odds (−0.0173 test), lineups (15.9% →
  15.9%; fit adds nothing).

**Page counts:** with the rebuilt numbers (and before any sentence changed) the 8p and 6p ran one page over (R8-079): trimmed in
8p the platform sentence of "Limitations and Conclusion" and the ablations' "best setting" sentence, in 6p a clause each on
identity errors and the pre-game favourite. The long version's ablation table moved to the top of its section and its caption
lost the Tune column's RAPM clause (a float overran a column by 26 pt). The stats.nba.com sentence of Data and Code
Availability now says the site stopped answering "for part of the time" (R8-006).

Tests: full suite **513 passed, 1 skipped** (ledger), exit 0, 126 s; eslint 0. The 2 failed + 5 errors Step 6b left pass.

## Step 7: the smaller open issues (2026-10-06)

Taken: every open entry owned by Step 7 and every open **wrong number** not owned by Steps 8-10.

- **Hot Streak null centre (R8-029):** the shuffle machinery of `paper_beliefs.py`'s hot-streak family moved verbatim into
  `api/hot_streaks.py` (`SeasonMatrix`, `row_perms`, `null_rng`); the paper imports it (its `streak()` output with 50 + 300
  shuffles is byte-identical old vs new, checked by pickling both `Out` objects) and `build_hot_streak_persistence.py` (~5 min
  now) stores per stat and window `null_slope` (+ 2.5/97.5%), `net_share` = slope − null (+ range) and the same for the
  season-only baseline, from the same 2,000 shuffles as the paper: **equal to `paper_beliefs_summary` exactly** (test). Every
  earlier column byte-identical. The card, the league list and the verdict show the share, the shuffled share and the run's
  own part (e.g. 2024-25 3P% over 10 games: "12% ... but 13% with games shuffled, so the run itself carries on 0% (−3% to
  +2%)"). Methodology OPEN_ISSUES entry removed; the card's sniff test re-read (stale since 6a: 11/10/9 significant against
  9.6/10.6/10.8 → 10/10/8 against 9.7/10.7/10.7; hottest shooters kept 10-19% → 10-18%).
- **Deflator clock (R8-030):** measured first (`build_leverage_splits.py --clock espn --dry-run` reproduces the stored tables
  exactly; `--dry-run` compares): on the corrected clock filtered PPG moves in 501 of 2,111 qualified player-seasons at one
  decimal (max 0.31: Brandon Ingram 2022-23 20.58 → 20.89, Curry 2023-24 23.80 → 24.11), garbage share by up to 4.9 points,
  4 padding badges, clutch plays already decided 17% → 16% (e.g. 2025-26 4,085 of 24,528 → 3,883 of 24,249). Switched;
  `build_scouting_reports.py` rerun (only its leverage rows changed: 34,282 → 34,276 splits; leverage still fails its
  persistence check, 0.60 same direction). README / Data Coverage / Methodology say which clock.
- **Stat Leaders (R8-068):** one rule, `routers/leaders.qualifying()` = the Leaderboard Builder's defaults (30+ games,
  20+ minutes, 5 FGA / 2 3PA / 2 FTA a game for percentages; `DEFAULT_MIN_GP/MPG` named in `leaderboard.py`), on the stored
  path, the live path (games floor 70% of the most games played while a season is young), the hustle leaders and the
  Dashboard's top scorer; the page states it. 2025-26 3P% top: Mark Williams 100% (1-for-1) → Luke Kennard 47.8%; FT%
  Hayden Gray 100% (1 game) → Stephen Curry 92.3%; FG% Harrison Ingram 83.3% (7 games) → Jakob Poeltl 70.0%; steals Kadary
  Richmond 2.7 (3 games) → Kevin Porter Jr. 2.2; +/- Colby Jones +12 (1 game) → Shai Gilgeous-Alexander +11.6; charges drawn
  Brandon Clarke 0.5 (2 games) → Jalen Brunson 0.39. Equal to the Leaderboard Builder's default top 10 (test).
- **Lineup minutes (R8-073):** `lineup_seasons` / `pair_seasons` gained `seconds` (the exact sum); their seconds and
  possessions are summed as numeric, so a rebuild gives the same digits (`build_lineup_stints.py --aggregates-only` rebuilds
  the two from the stored stints, two runs content-hash identical). Against the snapshot: 5 lineup rows' minutes and 42
  rows' ratings, 58 / 56 / 173 pair rows' minutes / possessions / ratings moved by one last digit (0.1 min, 0.1 poss, 0.01
  rating): the float sums' order flips CLAUDE.md mentioned, now gone. Pair Chemistry and the Workbench's lineup/pair minutes
  add `seconds`: Mikal Bridges 2022-23 2,965.8 → 2,962.9 (on the floor 2,962.85).
- **2025-26 awards (R8-009):** read 2026-10-06 from Wikipedia (the 2025-26 NBA season page's Awards, and the MVP, DPOY and
  ROY award pages, which agree): MVP Shai Gilgeous-Alexander, DPOY Victor Wembanyama (unanimous), ROY Cooper Flagg, All-NBA
  three teams. In the label lists (`build_dpoy_roy_models.py`, `fetch_all_nba_teams.py`, `build_award_winners_table.py`'s
  `MVP_AFTER_LEGACY`, since `mvp_winners` is a legacy table with no loader); every model trains on explicit seasons ending
  2024-25, so no model, backtest or calibration changes. `resolve_predictions.py` graded the 60 2025-26 Prediction Ledger
  rows (mean Brier on the raw output: MVP 0.495, DPOY 0.342, ROY 0.284, All-NBA 0.175), and the ledger now says they were
  logged 2026-09-23, after the season (R8-080).
- **Also:** Game Log per-game dots 0.55 → 0.8 opacity (R8-071's part); Stat Leaders' active chip 4.0:1 in Paper (R8-081).
- **Left open, with the reason:** R8-010 (the 2026 draft: `draft_history` comes from the Basketball-Reference export, which
  ends at 2025; the NBA's endpoint would make a second writer of the table: with the round-9 live-season work or a refreshed
  export), R8-028 (re-fetching the 2025-26 shot chart rewrites `player_shots` and the shot chain: owner's OK), R8-032 (Pair
  Synergy retrain: owner's call), R8-071's remaining marks (owner's call with R8-059), R8-082 (profile award lists).

Checks: browser sweep of Stat Leaders, Hot Streaks (default and 2024-25 3P% as of 2025-01-15), SGA's profile (Game Log + hot
streak card), Methodology, Analytics Prediction Ledger / Garbage-Time / Pair Chemistry, and the three other pages with rail
chips (Player Stats, Hall of Fame, News), 1280/375 × Paper/Ink: clean except the known profile marks (R8-059/R8-071) and
News' slow feed (R8-011). `scripts/rebuild_all.sh paper-inputs` exit 0 (manifest digest `0ccd328c11140933`; numbers.tex 1,359
macros, every macro the long, 8p and 6p papers use defined; no paper number moved; figures unchanged). Tests: full suite
**522 passed, 1 skipped**, exit 0, 110 s (new `api/tests/test_round8_step7.py`, 9); eslint 0; vite build passes.

**Changed tables for the Layerbase sync** (~43 MB, all small except the two aggregates): `lineup_seasons` 27 MB (new column
`seconds`), `pair_seasons` 6.3 MB (same), `scouting_splits` 6.8 MB, `player_leverage_splits` 1.6 MB, `player_leverage_summary`,
`leverage_index_grid`, `leverage_validation`, `hot_streak_persistence` (13 new columns), `scouting_validation`, `award_winners`,
`all_nba_seasons`, `prediction_ledger` (60 rows resolved).

## Step 8: speed (2026-10-06)

Measured on the local database with the three backends started fresh (cold = the first call after a restart, warm = the
second), the crawl re-run (`docs/qa/crawl_2026-10-06.tsv`: 210 of 210 answered 2xx, 0 over 3 s; every route's status,
bytes and rows equal to the morning's crawl except `/news/current`, a live feed), and 158 responses of the touched routes
saved before any change and compared **byte for byte** after (all 158 identical; only the `seconds` / `checked_at` timing
fields of the live checks were masked). No index was added and no table changed (nothing to sync, paper untouched).

**Routes** (the crawl's slowest, plus what the before/after snapshots found):

| Route | Before | After | What changed |
|---|---|---|---|
| `/plays/finder?season_from=2024&season_to=2024` | 49.8 s | 0.07 s | R8-083: rows picked from the plays first, sorted by `game_no + 0`; counts in one cached GROUP BY |
| `/plays/finder?season_to=2021` | 118 s | 0.06 s | R8-083 |
| `/plays/finder` (the page's default) | 0.93 s cold / 0.88 warm | 0.36 / 0.17 s | R8-083 |
| `/news/current` | 5.93 s cold | 2.17 s | R8-011: the six RSS feeds at once, joined in list order |
| `/data-quality/check/score_fields` | 2.96 s cold | 1.40 s | R8-060: its two passes on two connections; one run at a time |
| `/data-quality/check/clock_offset` | 1.22 s cold | 0.79 s | R8-060 |
| `/games/guess-the-game/daily` | 1.2 s every call | 1.2 s first, then 0.00 | R8-085: pool and puzzle kept per process |
| `/games/guess-the-game/guess`, `/reveal` | 0.7 s every call | 0.00 s | R8-085 |
| `/games/wp-replay/list` | 0.65-0.77 s every call | first per season, then 0.01 s | R8-085: the unchanged query, kept per season |
| `/meta/current` | 1.43 s cold / 0.26-0.48 warm | 1.07 / 0.03 s | R8-084: stats.nba.com's empty pre-season answer cached |
| `/games/hot-streaks` | 1.05-1.20 s cold | 0.92-1.14 s | later games split once, not masked per player. ~0.6 s is the 4,000-draw shuffle per player (`argpartition`); a different draw changes the p-values, so not touched. Cached after the first call |
| `/workbench/parse` 2.27 s, `/odds/championship` 2.20 s, `/games/boxscore` 1.30 s, `/leaders/pts` 1.45 s cold, `/players/heliocentricity` 0.68 s, `/media/player-image` 0.60 s, `/games/by-date` 0.55-2.0 s | | unchanged | outside services (Gemini, the Odds API, ESPN, stats.nba.com), each already cached after the first call; nothing to do on this side |

EXPLAIN ANALYZE (local): Play Finder rows for one season, before `Nested Loop` over `play_finder_games_pkey` backwards ×
`Materialize` of 558,797 plays (not run to completion: ~50 s through the route), after `Hash Join` + top-N sort 35.9 ms;
unfiltered rows 566 → 159 ms, counts 169 + 155 → 206 ms (one pass). The last-score scan behind R8-085: 754 ms (unchanged;
now once per process).

**First page loads** (the harness, Vite dev server, 1280 px Paper, every page of `App.jsx`'s `PAGES` plus the landing, the
app's localStorage cache cleared before each page and the backends restarted before the run; time = the last request's
end, from the frame's navigation start). The five slowest:

| Page | Before | After | Waiting on |
|---|---|---|---|
| Landing | 5.63 s | 2.06 s | `/news/current` (the app-shell prefetch; the page itself is drawn at 0.1 s, first contentful paint 104 ms) |
| News | 4.95 s | 2.29 s | `/news/current` |
| Data Quality | 3.33 s | 2.32 s | `score_fields` (R8-060) |
| Play Finder | 1.66 s | 1.11 s | `/plays/finder` (R8-083) |
| Hot Streaks | 1.44 s | 1.35 s | `/games/hot-streaks` (the shuffle) |

Next in line, unchanged: the player profile (1.3 s, 21 requests), Learn (1.2 s, ESPN's scoreboard in the prefetch). Pages
from 0.4 to 1.1 s move by ±0.3 s between runs with the dev server's module loading and ESPN.

**Bundle:** the Dashboard was the only page `App.jsx` imported eagerly; it is lazy now like the other 48, so the landing page
no longer downloads it: first load (what `dist/index.html` names) 573.3 kB raw / 171.1 kB gzip in 10 files → 549.7 kB /
163.5 kB in 5 (the Dashboard is a 15.3 kB chunk of its own). The rest of the first load is React DOM, framer-motion (page
transitions), Lenis (the landing's scroll), `services/api.js` and the shared CSS. The landing page loads no image files.
The 532 kB three.js chunk doesn't delay the first paint (R8-013). `frontend/public/shot_data/stephen_curry_shots.json`
(5.4 MB, copied into every build) is referenced by no page; it is a dataset file listed in `docs/DATASHEET.md`, so left
for the owner.

Checks: `api/tests/test_round8_speed.py` (13 tests: the old queries against the new route, the counts, the feeds' order,
the empty-answer cache, the pool read once under concurrency, the two-connection checks, the per-player mask); full suite
**535 passed, 1 skipped**, exit 0, 119 s; eslint 0; vite build passes; the landing → Dashboard link works with the lazy Dashboard; no console error on
any of the 50 pages.

## Step 10: close-out, the Layerbase sync and slimming (2026-10-06)

Step 9 (usability fixes) waits for the owner's study sessions (`docs/USABILITY_STUDY.md`); it can run any time and
doesn't block this close-out.

| Check | Result |
|---|---|
| `pytest api/tests` (local) | before any change **535 passed, 1 skipped**, exit 0, 117 s; after this step's changes **541 passed, 1 skipped**, exit 0, 119 s |
| `npx eslint src` / `npx vite build` | exit 0 / passes (before and after this step's changes) |
| `scripts/rebuild_all.sh paper-inputs` | exit 0, 35 s: 218 tables, 26,879,962 rows, digest `0ccd328c11140933` (the Step 8 digest); `numbers.tex` byte-identical (1,359 macros), 270 plotted numbers match |
| Route crawl (`docs/qa/crawl_2026-10-06_close.tsv`) | 210 routes, **210 called, 0 not 2xx, 0 over 3 s, 0 with placeholder text**, 1 empty (`/ledger/live/games`: nothing logged before 2026-10-20, expected), 33 JSON answers without `_source` (all accounted for: R8-014, now closed). Every route's status, rows and `_source` equal to Step 8's crawl; only `/news/current`'s bytes moved (a live feed) |
| Page sweep of what this step touched | Data Coverage only: opened in the browser with impact_api on `DB_TARGET=layerbase` after the sync (1280 px, Paper): `report_card_units` reads "kept local, not on the cloud mirror" in `--text-3`, no "table not found" alert, no console error; pinned by `test_meta_coverage` (both databases) and `test_data_coverage_marks_a_local_only_table_that_is_absent`. No other page changed in Step 10 |

**Layerbase slimming (the owner's OK, 2026-10-05).** Re-grepped `api/` and `frontend/src` first: no route or page reads
any of the six tables (only Data Coverage's map names `report_card_units`), so all six stay local:
`paper_eval_predictions`, `shot_xfg`, `paper_ablation_predictions`, `report_card_units`, `report_card_game_sums`,
`paper_ablation_shot_games`. The list lives in **`api/local_only.py`** (`LOCAL_ONLY`) and is used by
`scripts/migrate_to_layerbase.py` (never copied; `--tables` refuses them; `--check` doesn't count them;
`--drop-local-only` drops them on Layerbase; new `--reindex [tables]` re-packs indexes the way this step did), `scripts/paper_manifest.py` (`--compare DIR`: a mirror's manifest checked
table by table, LOCAL_ONLY skipped and named; `stale_reasons()` doesn't call them stale on the mirror),
`api/routers/meta.py` (Data Coverage marks them `local_only` and, where absent, "kept local, not on the cloud mirror"
instead of "table not found") and **`api/tests/conftest.py`** (on `DB_TARGET=layerbase` a test or fixture stopped by one
of them not existing is skipped with the table named; any other error still fails). Tests:
`api/tests/test_round8_closeout.py` (6).

**The sync, in order** (sizes in decimal MB from `pg_database_size` / `pg_table_size` / `pg_indexes_size`; the plan's
4,008 is the same database in MiB):

| | Database | Indexes | Tables |
|---|---|---|---|
| Before (2026-10-06, still the 2026-10-04 sync: digest `e5e7bae46a7ccf7a`) | 4,203 MB | 1,086 MB | 218 |
| Six LOCAL_ONLY tables dropped (`--drop-local-only`: 588 MB) | 3,614 MB | 843 MB | 212 |
| 98 tables copied (`--tables`; 10,255,718 rows, 1,299 MB locally) | 3,630 MB | 849 MB | 212 |
| `REINDEX TABLE`, one at a time, biggest first (189 tables: 842 → 698 MB) | **3,487 MB** | **705 MB** | 212 |

**1,513 MB free of the 5 GB tier** (was 797). The 98 changed tables were found by content hash, not row count:
`migrate_to_layerbase.py --check` saw only 28 with a different count. `player_shots` and `pbp_events` hashed equal and
were not re-sent; no `ledger_*` table differed or was touched (nor re-indexed). The re-pack mattered most where the key
index had been built while rows were loaded (the script creates the primary key before the COPY): `pbp_events`
181 → 133, `pbp_event_clock` 114 → 76 (fresh copy), `possessions` 107 → 79 (fresh copy), `player_shots` unchanged.
The plan expected ~765 MB freed (its MiB, measured on the old tables); freed 716 MB (683 MiB) while taking in the round's ~1.3 GB of rebuilt tables.

**Verify:** `DB_TARGET=layerbase python3 paper_manifest.py --out $TMPDIR/lb` then `paper_manifest.py --compare $TMPDIR/lb`:
**212 of 212 tables equal (schema, rows, content), mirror digest `27175bbdea7992e1` on both** (UTC session); the six
LOCAL_ONLY skipped and named. Tests with `DB_TARGET=layerbase` in batches: 542 tests in four batches (smoke 25 min; Workbench + usability 12 min; round 8 + consistency + known facts 29 min; the rest 12 min): **510 passed, 31 skipped, 1 failed**. Skipped: 30 that read a LOCAL_ONLY table (each names it) and the ledger test until the first game. Four failed in the batches with `SSL connection has been closed unexpectedly` (Layerbase's connection drop, seen at every sync) or a 502 caused by it, and passed rerun alone (`test_shot_endpoints_take_a_player_id`, `test_live_shot_fetch_is_off_by_default`, `test_player_season_line_everywhere`, `test_shot_totals_everywhere`). The one failure is the known one: `test_workbench.py::test_every_verified_column_runs` hits the Workbench's 8 s `statement_timeout` on Layerbase only (504 "narrow the query", as designed). The first run of the last batch also showed that a module fixture's cached error skipped only its first test, so `conftest.py` now converts the test report itself (3 errors → skips).

**Found on the way (not app defects, for the owner):**
- **Two orphaned Layerbase sessions** from the 2026-10-04 sync's test run (`idle in transaction` since 08:51 UTC that
  day, client gone) still hold read locks on `game_scores`, `team_game_fatigue`, `game_pregame_odds`,
  `season_sim_params` and the `ledger_*` tables. Reads aren't blocked, but `REINDEX` waited on `game_scores` (cancelled;
  those four tables were skipped with a 20 s lock timeout) and a future copy of any of them (`DROP TABLE`) would hang.
  Ending them changes no data: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in
  transaction' AND xact_start < now() - interval '1 day';` (left for the owner).
- **No pre-6a dump exists:** `~/nba_backups/` was never created and there is no `pre-6a` tag; Step 6a snapshotted its
  nine tables as `zz_r86a_*` instead (diffed, then dropped). The only full dump on this Mac is the migration bundle's
  `~/Desktop/NBA_Hub_Migration_2026-10-01/nba_analytics.dump` (369 MB, 2026-10-01, before round 8). Nothing to delete for
  Step 6; the owner decides about the bundle (already on the owner's to-do list).

## For the guide: round 8 in one page (2026-10-05 to 2026-10-06)

**What was tested.** Every API route (210, crawled with real arguments at the start, after Step 8 and at the close),
every page (49 pages plus profiles and team pages; ~720 renders at 1280 and 375 px in light and dark themes, with a
scripted scan for console errors, slow requests, overflow, contrast, placeholder text and broken links), every
quantity the app shows on more than one page (one consistency test file over a seeded sample), 14 new outside facts,
the live pages without stats.nba.com, the play-by-play chain rebuilt end to end, the paper regenerated on it, and the
speed of the slowest routes and pages. Tests went from 410 to 542.

**Counts.** 85 problems logged: 8 broken, 27 wrong number, 8 slow, 42 looks wrong. **77 fixed, 1 won't fix (measured
harmless), 7 open**, none of them broken: two need the owner's call (a chart orange that is 2.8:1 on the light theme, a
Pair Synergy retrain), one is a page decision (paging Referee crews), two wait for refreshed source data (the 2026 draft,
2025-26 award lists on profiles), one is a shot-chart gap in four 2025-26 games, one a leftover of a contrast issue.

**Biggest fixes.** The play-by-play lines and five-man stints were rebuilt: players ESPN gives no id now matched by name
(minutes not tracked: 2-6% → 0.02-0.16% a season), free throws credited to the five on the floor at the foul (box-score
+/- exact in 98.2% of games, was 42.5%), phantom minutes gone; 82 downstream tables refitted on it. Live Scores,
standings and box scores read ESPN, not the unreliable stats.nba.com. Same-name players picked by id everywhere. A
stored team stats fallback that was wrong for all 30 teams (Cleveland 147.5 points a game). Play Finder with a season
filter 50-118 s → 0.07 s.

**Conclusions that moved (Step 6c).** The Rating Tracker now trails BPM on the test season (+0.22 [+0.05, +0.40], was
level), and BPM has the lowest test-season error in the RAPM table; dropping flagged games now moves the RAPM + prior vs
BPM gap past every random drop (0.21 [0.01, 0.40]); two ablations' test-season edges moved to or inside their intervals (the prior at scale 1, possession weights). Unchanged: RAPM + prior vs
BPM reversal, on/off worse than zero, xRAPM's negative result, pre-game and simulator results, the popular-belief tests.

**Next.** Step 9 after the usability sessions; round 9 (live 2026-27 season from 2026-10-20, own-team data).

## Round 8.5 Step A: Forecast Ledger opening-night check (2026-10-06)

Round 8.5 closes what round 8 left; it keeps using this list. Step A checks the locked Forecast Ledger before the
season tips off (2026-10-20 19:00 UTC) and writes the game-day runbook, **`docs/LEDGER_RUNBOOK.md`**. No table changed;
the real `ledger_*` tables were not written (only dry runs and copies in a dropped `zz_` schema).

| Check | Result |
|---|---|
| `ledger_lock.py --verify` | sha256 `c1a48402…3d17` reproduced |
| Tag on GitHub | `git ls-remote --tags origin ledger-2026-27` = `c9071a8b…` (the lock's code commit). Emailing the hash to the guide is the owner's (not recordable here) |
| `ledger_update.py --dry-run --today 2026-10-20` | exit 0; 1,206 events (1,200 count, 6 NBA Cup knockout games with teams TBD), 9 rows for opening night's 3 games, all before tip, standings 0-0 (OKC 67.9 expected wins on the roster forecast) |
| `--dry-run --today 2026-10-21` | exit 0; the 10-20 rows again, then 10-21 **waits** for opening night's three games to go final, as the rule says |
| ESPN's live schedule vs `ledger_schedule` | identical: same 1,206 ids, and 0 differences in date, tip time, time_valid, home, away, neutral site, venue, city, note; every event STATUS_SCHEDULED. So nothing needs a rule today; the runbook lists the rule for each kind of change the season can bring (postponed, suspended/cancelled, Cup knockout teams, games added after group play, tip time moved, score corrected) |
| Simulated opening week | `api/tests/test_ledger_gameday.py` (new, ~10 s): the real script, six runs, on copies of the six locked tables in `zz_ledger_gameday` (search_path through `PGOPTIONS`), ESPN's scoreboard replaced by events built from the locked schedule, the clock set per run. Opening night's odds before tip = the locked odds (< 2e-6), record-only 0.5; the morning after (3 fake finals, one game postponed): 30 rows for the other 10 games, each equal to a recompute through the tag's code (< 1e-12), none for the postponed one; a missed day: 10-22's rows labelled recomputed (`before_tip` false) and 10-23 waiting for a game still in progress, no standings that morning; then 10-23 with the postponed game on its new date and new tip; every rerun adds no game-log row and identical standings; 15 games scored under all five versions; the schema dropped and the real ledger tables' row counts and content hashes unchanged |
| Timing | the ESPN read takes 45-70 s (was ~20 s on 2026-09-30); README said ~20 s, corrected |

Code: `api/ledger_live.live_tables_exist()` now checks `to_regclass('ledger_game_log')` without the `public.` prefix
(same answer under the default search_path; it lets the update run on a `zz_` copy). Nothing frozen touched: the three
tagged files' blob ids still equal the tag's. Found: R8-086 (every real ledger run changes the paper's inputs).

## Constraints (not defects)

- **Layerbase:** 3,487 of 5,000 MB used (2026-10-06, after the Step 10 sync and slimming; was 4,203). Six paper-only tables stay local (`api/local_only.py`); any sync needs the owner's OK.
- **Locked forecast:** no step touches the `ledger_*` tables, the `ledger-2026-27` tag or `api/ledger_lib.py` / `season_sim_lib.py` / `luck_lib.py`; from 2026-10-20 the owner runs `scripts/ledger_update.py` by hand on game days.

## Issues

### R8-001 · Stat Leaders is empty between seasons
- **Severity:** broken · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** Stat Leaders (`StatLeaders.jsx`), `GET /leaders/{stat_key}` (`api/routers/leaders.py`), also the app-shell prefetch (`prefetchCoreData`).
- **Reproduce:** `curl 127.0.0.1:8002/leaders/pts` → `{"season":2027,"results":[]}`. The page calls it without a season, so the season defaults to `max(latest DB season, current NBA season)` = 2026-27. The live call returns nothing (no games yet) and the DB fallback queries the same season, so it's empty too. The page shows "Top 10 · 2027" and nothing else, with no reason given.
- **Found by:** crawl (empty answer). It stays empty until stats.nba.com has 2026-27 leaders, and goes empty again whenever the live call fails, since the DB never has the current season.

- **Step 4:** without `season`, `/leaders/{stat}` fetches the season in progress live only when it is newer than the stored ones (3 s timeout) and otherwise shows the latest stored season, saying so (`season` 2026, `requested_season` 2027, `fallback`, `note`); a stored season never goes to the network. The page shows the note and a source badge. Old → new today: an empty "Top 10 · 2027" → "Top 10 · 2025-26" with Dončić 33.5. Test `test_leaders_default_falls_back_to_the_latest_stored_season`.
### R8-002 · Live Scores says "No games found" for future dates
- **Severity:** wrong number · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** Live Scores, `GET /games/by-date?date=` (`impact_core.fetch_nba_games_by_date`).
- **Reproduce:** `/games/by-date?date=2026-10-20` → 0 games in 6.2 s, and the page reads "No games found for 2026-10-20". `ledger_schedule` (ESPN) has 3 games that day. Today (2026-10-05) returns 5 preseason games. The team objects carry `name: ""`, and some have `logo: null` (MEM); Step 2b should check whether that shows.
- **Found by:** crawl + manual calls. Opening night itself works only if stats.nba.com answers on the day; there is no ESPN or stored fallback (cdn.nba.com is 403, R8-003).
- **Step 2b:** the empty `name` and the null/third-party logos did show (bare logos, no team names, a white-on-orange fallback at 2.6:1); the page now uses the app's names and NBA.com logos (R8-045). The route's own fields stay Step 4's.

- **Step 4:** `/games/by-date` reads the stored results first (`game_scores`, `postseason_games`) and ESPN's scoreboard for every other date (`api/espn_live.py`, 3 s timeout, cached 60 s for today and the future): 2026-10-20 lists the three openers (BOS-DET 3:00 PM ET, PHI-NYK, OKC-SAS) with their kind, and a date ESPN can't answer for says `status: unreachable` with a message instead of "No games found". The page shows tip times in ET, the quarter and clock for live games, "Final/OT", a Preseason/Play-in/Playoffs badge from ESPN's season type, and the ESPN badge; "today" is the US Eastern date (`utils/date.js` `nbaDateIso`). Tests `test_by_date_lists_opening_night_from_espn`, `test_by_date_says_when_espn_is_unreachable`.
### R8-003 · The cdn.nba.com fallbacks are dead (403)
- **Severity:** broken · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** `impact_core._fetch_nba_cdn_standings_uncached`, `fetch_nba_cdn_games_by_date`, `fetch_boxscore_from_cdn` (used by `/meta/current`, `/games/by-date`, `/games/boxscore`).
- **Reproduce:** `curl -A Mozilla/5.0 https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json` → 403, and the same for `standings/leagueStandings.json` and `boxscore/boxscore_<id>.json`. Headshots and logos on the same host are 200.
- **Found by:** probing the hosts the code calls. When stats.nba.com fails, these fallbacks fail too, so Live Scores and the box score have nothing behind them. Step 4 picks ESPN or stored data.

- **Step 4:** the three cdn.nba.com readers, the balldontlie reader (needed a key nobody set), the thesportsdb badges and the stats.nba.com scoreboard, box score, standings, player search and player profile fetches are deleted from `impact_core.py` (723 lines). Standings: ESPN (regular-season type; its default counted preseason games, TOR 0-1 on 2026-10-05) → `team_seasons`. Box score: ESPN's summary by ESPN id or NBA id (mapped through `game_scores.espn_id`). Test `test_dead_fallbacks_and_live_search_are_gone`.
### R8-004 · The dashboard mixes 2026-27 standings with 2025-26 numbers before the season
- **Severity:** looks wrong · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** Dashboard (`DashboardHome.jsx`), Team Comparison, Standings; `GET /meta/current`.
- **Reproduce:** `/meta/current` → `season: 2027` and every team 0-0 from stats.nba.com (streak "W 0"), but `top_scorer` = Luka Dončić 33.5 (2025-26 `player_season_stats`) and `team_stats` = 2025-26 DB values. None of these carries a season label. The "#1 Seed" tile takes `standings.western[0]`, i.e. only the West's first row (never the East), which is a 0-0 team before the season.
- **Found by:** reading the crawl answer and the components.
- **Step 2b (Standings, Dashboard tile):** Standings now names the season ("2026-27 standings: no games played yet, so every team is 0-0 until the first tip-off"), shows no "#1 Seed" hero and no "W 0" streaks before a game is played; the Dashboard tile is "Best record" over both conferences (it was the West's first row) and says "no games played yet" before the season. Test: `test_standings_say_when_no_game_has_been_played`. Left for Step 4: `top_scorer` and `team_stats` from 2025-26 without a season label, and the 2026-27 standings coming only from stats.nba.com.

- **Step 4:** `/meta/current` labels every block: `standings_season` / `standings_source` (ESPN 2026-27, 0-0 until opening night, numbered 1-15; stored 2025-26 when ESPN doesn't answer), `team_stats_season` / `team_stats_source`, `top_scorer_season` / `top_scorer_source`, plus `stored_season`. The Dashboard's tiles read "Top Scorer · 2025-26" and "Best record · 2026-27", Standings says "2026-27 standings … (ESPN, live)", Team Comparison says "Per-game stats: 2025-26 (stored …) · record: 2026-27 (ESPN, live)". Tests `test_meta_current_labels_every_block_with_its_season`, `test_meta_current_standings_fall_back_to_the_stored_record`.
### R8-005 · Live box score shows "nan" as +/- for players who didn't play
- **Severity:** looks wrong · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** Live Scores box score, `GET /games/boxscore/{game_id}` (`impact_core.fetch_boxscore`).
- **Reproduce:** `/games/boxscore/0022400062` → 9 of 28 rows (DNPs, `min: "0"`) have `pm: "nan"`. Every other row has plus-minus as text with a decimal (`"19.0"`).
- **Found by:** crawl (placeholder-text check).
- **Step 2b:** not visible on the page: the Live Scores box score has no +/- column (Player, MIN, PTS, REB, AST, FG, 3PT, FT). Still worth fixing in the route (Step 4).

- **Step 4:** the box score comes from ESPN: `pm` is an integer (None for a player who didn't play, with `dnp_reason`), and the page gained a +/- column. Test `test_boxscore_takes_both_ids_and_has_no_nan`.
### R8-006 · stats.nba.com is reachable again, but the docs and code comments say it isn't
- **Severity:** looks wrong · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** CLAUDE.md ("unreachable from this machine since 2026-09-26"), README Known real gaps, `scripts/rebuild_all.sh` help ("nbaapi = stats.nba.com (times out …)"), Methodology open issue "Some models can't be retrained right now", the round-8 plan's facts.
- **Reproduce:** `python3 -c "from nba_api.stats.endpoints import leaguestandingsv3 as s; print(s.LeagueStandingsV3(season='2025-26', timeout=30).get_data_frames()[0].shape)"` → (30, 92) in 0.5 s. `curl` against the same URL with browser headers still gets no answer in 20-25 s (so a curl check says "down").
- **Found by:** the With/Without smoke test passing with real data (41 s), then direct calls. Step 4 decides which live pages keep a live call. Any `nbaapi` fetch script (R8-028, R8-032) can be tried again, and the wording should change wherever it says unreachable.

- **Step 4:** CLAUDE.md, README (Known real gaps), `scripts/rebuild_all.sh`'s help, `docs/DATASHEET.md` and the Methodology open issue now say it answers `nba_api` again since 2026-10-05 (plain curl still times out). The retrain itself is R8-032 (Step 7).
### R8-007 · Opening a shot chart can write rows into `player_shots`
- **Severity:** wrong number (risk) · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** Shot Charts and Player Comparison, `GET /shots/player/{name}` (+ `/seasons`, `/zones`), `shots_lib.ensure_player_shots_cached` / `ensure_season_shots_cached`.
- **Reproduce:** read the code. For a player whose `player_shots_cache_status` isn't `done`, a GET fetches his career from stats.nba.com and stores it in `player_shots`. `ENABLE_LIVE_SHOT_FETCH` defaults to true. Today nothing has been written since 2026-09-25 (2,842 players `done`, `player_shots` 6,318,078 rows). With stats.nba.com answering again (R8-006), the first view of an uncached player after 2026-10-20 (e.g. a rookie) would add 2026-27 shots to a table the paper manifest hashes, and that shot-making, xRAPM and Layerbase all read.
- **Found by:** mapping the live callers. Step 4 decides: turn live fetching off, or keep it and exclude live-fetched rows.
- **Step 2c:** the same happens to `league_shot_zones` ("cached forever after the first fetch"): it has 2023-24 and 2024-25 only, so the first `GET /shots/league-zones/2026` (Player Comparison's shot zones for 2025-26, or a test) fetches 2025-26 from stats.nba.com and inserts 5 rows. A 2c test did exactly that on 2026-10-05; the 5 rows were deleted again (the table is back to its 10 rows) and the test now asks for a cached season. `player_shots` and `player_shots_cache_status` were checked unchanged after every 2c run (6,318,078 rows; 2,842 done, last update 2026-09-25): every player opened in the sweep (Curry, Wembanyama, Jordan, Jokić, Thompson) was already cached.

- **Step 4:** `ENABLE_LIVE_SHOT_FETCH` defaults to **off** (`shots_lib.LIVE_FETCH_ENABLED` is true only when `api/.env` sets it to true/1/yes); an uncached player or an unstored league-zone season gets a 404 that says so and how to turn fetching on; `scripts/prewarm_shots.py` turns it on for itself. Test `test_live_shot_fetch_is_off_by_default` (also checks nothing was written).
### R8-008 · With/Without a Star and Pair Synergy can wait 30-60 s on a live call
- **Severity:** slow · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** `/teams/with-without/{team}/{season}` (two leaguegamefinder calls, 30 s timeout each), `/players/pair-synergy` (leaguedashlineups, 45 s), `/players/playoff-comparison` and `/players/profile` (45 s), `/players/heliocentricity` (30 s).
- **Reproduce:** in the full pytest run the With/Without test took 41.0 s and Pair Synergy 18.8 s. The same routes took 2.5 s and 0.7 s in the crawl a few minutes later. stats.nba.com's first answer is sometimes very slow, and the timeouts let a page hang for up to a minute before a 502.
- **Found by:** pytest durations + crawl. The plan's target is a clear state within 3 s.
- **Step 2c (browser):** With/Without a Star for DEN 2025-26 / Jokić took 21.0 s in the sweep (`/teams/with-without/DEN/2026`).

- **Step 4:** With/Without reads stored data from 2020-21 (36 ms for DEN 2025-26 / Jokić in the browser; 21 s before); Pair Synergy's observed pair comes from `pair_seasons` (no live call); Playoff Forecaster and Heliocentricity keep their live call (no stored equivalent) with `_LIVE_REQUEST_TIMEOUT_SECONDS` = 3 and answer 503 "stats.nba.com didn't answer within 3 s" instead of hanging (a failed playoff fetch used to read as "didn't make the playoffs"); With/Without before 2020-21 the same (GSW 2015-16 answered 503 in 3.0 s on 2026-10-05: LeagueGameFinder for old seasons times out). `/players/profile/{name}`'s 45 s live path is gone. Tests `test_every_live_call_fails_within_three_seconds`, `test_with_without_before_2020_21_says_when_the_source_is_unreachable`, `test_playoff_comparison_distinguishes_unreachable_from_missed_playoffs`, `test_heliocentricity_unreachable_is_a_503_with_the_reason`.
### R8-009 · The 2025-26 awards were never loaded
- **Severity:** wrong number (stale) · **Step:** 7 · **Status:** fixed in the Step 7 commit (the profile award lists: R8-082, open)
- **Where:** `award_winners` and `mvp_winners` (max season 2025 = 2024-25), `player_awards` (2026: All-Star only). Affects: Analytics › Prediction Ledger (60 logged 2025-26 prediction rows in `prediction_ledger` can't resolve: "no seasons resolved yet"), award backtests and history, profile award lists (no 2025-26 MVP, DPOY, ROY, All-NBA).
- **Reproduce:** `SELECT award, MAX(season) FROM award_winners GROUP BY 1` → MVP/DPOY/ROY 2025. `scripts/resolve_predictions.py` reads `award_winners`.
- **Found by:** following the empty `/ledger/summary`. Fixing it needs the 2025-26 winners read from an outside page (NBA.com, ESPN or Wikipedia, URL and date in the commit). The 2026-27 award ledger stays empty unless `scripts/snapshot_predictions.py` is run during the season (nothing schedules it).

- **Step 7:** 2025-26's MVP (Shai Gilgeous-Alexander), DPOY (Victor Wembanyama), ROY (Cooper Flagg) and All-NBA teams read 2026-10-06 from Wikipedia (season page + award pages) into the label lists; `award_winners` 2025 → 2026, `all_nba_seasons` +15; no model changes (explicit training seasons). The 60 Prediction Ledger rows resolved (R8-080 for how the page says it). Test `test_2025_26_award_winners`.
### R8-010 · The 2026 draft isn't loaded
- **Severity:** looks wrong (stale) · **Step:** 7 → round 9 · **Status:** open
- **Where:** `draft_history` (max `draft_year` 2025); Draft pages, profile bios of 2026 rookies.
- **Reproduce:** `SELECT MAX(draft_year) FROM draft_history` → 2025.
- **Found by:** checking each core table's latest season. Draft Value's outcome classes stop at 2021 by design; this only concerns the draft list itself.

- **Step 7 (left open):** `draft_history` is built from the Basketball-Reference export (Kaggle, ends at the 2025 draft); `fetch_draft_history.py` (the NBA's endpoint, answering again) replaces the whole table. Adding 2026 needs either a refreshed export or a one-year append path with its own producer entry: with the round-9 live 2026-27 season. No page shows a wrong number meanwhile (Draft Value's outcome classes stop at 2021 by design).
### R8-011 · `/news/current` is the slowest route (5.8 s) and runs on every app load
- **Severity:** slow · **Step:** 8 · **Status:** fixed in the Step 8 commit
- **Where:** News, Dashboard; `prefetchCoreData()` calls it on load.
- **Reproduce:** crawl 5.8 s cold (5.2 s in the first run, 6.3 s in pytest). No RapidAPI key is set, so it reads RSS feeds.
- **Found by:** crawl timing.
- **Step 8:** the six feeds were read one after another (ESPN 0.7 s, NBA.com 404 in 0.6 s, Google 0.3 s, Yahoo 1.9 s, CBS 0.2 s, Sports Illustrated 404 in 2.2 s). `impact_core._fetch_current_news_uncached` now reads them at the same time and joins them in the list's order, so the answer is the loop's (checked on the recorded feeds with scrambled finishing order: identical; `test_news_feeds_combine_in_list_order`). Cold 5.93 → 2.17 s in the crawl (what's left is the slowest feed). The two dead feeds (NBA.com, SI: 404 since at least 2026-10-06) add nothing and cost nothing extra now; left in the list.

### R8-012 · `/referees/crew-tendencies` sends 2.8 MB
- **Severity:** slow · **Step:** 8 · **Status:** open
- **Where:** Analytics › Referee Tendencies, By Crew. Also large: `/defense/rim-deterrence` 510 kB, `/shots/shot-value` 380 kB, `/games/higher-lower/pool` 332 kB.
- **Reproduce:** crawl `bytes` column.
- **Found by:** crawl. 5,373 crews, most of which worked one game together; the page could page through them or filter on the server.
- **Step 2c (browser):** Referee Tendencies › By Crew renders every crew at once: 490,899 characters of page text at 1280 px (the By Official view is 9,127). Paging or a server-side filter would fix both the payload and the page.
- **Step 8 (left open):** the route itself takes 0.09 s; the cost is the page drawing 5,373 rows, because By Crew opens on "1+ games together" (4,859 of the crews worked one game). Gzip would shrink the transfer, not the rendering. Paging, or opening on 2+ games (514 crews), changes what the page shows: a page decision (Step 9/10 or the owner), not a speed fix that keeps every answer the same.

### R8-013 · The landing page's 3D court chunk is 532 kB
- **Severity:** slow · **Step:** 8 · **Status:** won't fix (measured: it doesn't delay the first paint)
- **Where:** `ShotCourtFlight` (three.js, lazy-loaded on the landing page). It's the build's only chunk over 500 kB.
- **Reproduce:** `npx vite build` warning.
- **Found by:** build output. It's lazy, so it doesn't count toward the first load (558 kB raw / 166 kB gzip). Step 8 measures whether the landing page's first paint waits for it.
- **Step 8:** it doesn't. Resource and paint timings of the landing page in the harness: first contentful paint at 104 ms, the chunk (and three.js) requested at 344 ms, after `/shots/league-sample` answers; it renders below the hero inside a `Suspense` with no fallback. The landing page has no image files (the ribbon and ball are drawn on canvas/WebGL), so there are no image sizes to cut.

### R8-014 · 28 data routes answer without a `_source` badge
- **Severity:** looks wrong · **Step:** 2 (2a/2b/2c, per page) · **Status:** fixed (2a, 2b, 2c and the Step 4 commit; closed in the Step 10 commit)
- **Where:** mvp `/backtest`, `/explain/{award}`, `/explain/{award}/{name}`; similarity `/clusters/player/{name}`, `/similarity/career/{name}`; impact `/contracts/player/{id}`, `/games/boxscore/{id}`, `/games/by-date`, `/games/wp-replay/list`, `/games/wp-replay/{id}/whatif`, `/hustle/leaders`, `/impact/player/{name}/{season}`, `/leaders/{stat}`, `/meta/current`, `/news/current`, `/player-profile/{id}/shot-zones`, `/players/compare-profile/{name}`, `/players/pair-synergy`, `/players/playtype-profile/{name}`, `/players/profile/{name}`, `/players/table/{season}`, `/shots/league-zones/{season}`, `/shots/player/{name}` (+ `/seasons`, `/zones`), `/trade/roster/{team}/{season}`, `/trade/simulate`, `/trade/teams/{season}`.
- **Reproduce:** crawl column `source` = `no`.
- **Found by:** crawl. The convention asks for a badge on the main Analytics endpoints. Each page sweep decides per page (add one, or note why the page doesn't need one).
- **Step 2a (Players pages):** `/players/table/{season}` (Player Stats) and `/players/compare-profile/{name}` (Player Comparison) now return `_source`, and both pages show the badge; Rookie Class Tracker now shows the one `/roy/predict` already returned. Left: `/leaders/{stat}` (Stat Leaders, with R8-001 in Step 4), `/players/pair-synergy` (a live call, Step 4), and the profile's sub-routes (`/player-profile/{id}/shot-zones`, `/contracts/player/{id}`, `/players/playtype-profile/{name}`), which sit under the profile's own badge for `/player-profile/{id}`. Shot routes: 2c.
- **Step 2b (Teams, Games, Today):** `/trade/teams/{season}`, `/trade/roster/{team}/{season}` and `/trade/simulate` now return `_source`, shown on the Trade Analyzer's result (test `test_trade_analyzer_carries_a_source`). Left, all live and Step 4's: `/games/by-date`, `/games/boxscore/{id}` (Live Scores), `/meta/current` (Dashboard, Standings, Team Comparison), `/news/current` (News). `/games/wp-replay/*` is 2c's (Analytics › Game Replay).
- **Step 2c (Analytics, Shot Charts, the rest):** `/shots/player/{name}`, `/shots/player/{name}/seasons` and `/zones` now return `_source` (`player_shots`; `live` true when the shots were fetched just now) and `/shots/league-zones/{season}` too (`league_shot_zones`); Shot Charts shows the badge on its dots and heat-map views (the other three views already had theirs). Test `test_shot_charts_carry_a_source`. The rest of the 2c routes sit under their page's own badge: `/games/wp-replay/list` and `/{id}/whatif` under the replay's, `/backtest` under Model Validation's, `/explain/*` under Awards Race's, `/clusters/player/{name}` under Player Archetypes'. Unused by any page (R8-017): `/players/profile/{name}`, `/similarity/career/{name}`, `/impact/player/{name}/{season}`. Left, all Step 4's: `/leaders/{stat}`, `/hustle/leaders` (Stat Leaders), `/players/pair-synergy`, `/games/by-date`, `/games/boxscore/{id}`, `/meta/current`, `/news/current`.

- **Step 4:** `/leaders/{stat}`, `/hustle/leaders`, `/players/pair-synergy`, `/games/by-date`, `/games/boxscore/{id}`, `/meta/current` and `/news/current` now return `_source` (Stat Leaders and Live Scores show the badge). Left: nothing from this entry's Step 4 list.
- **Step 10 (close-out crawl, `docs/qa/crawl_2026-10-06_close.tsv`):** 33 JSON answers carry no `_source`, every one accounted for above: the three services' roots, searches and resolve (`/players/search`, `/era/players`, `/games/finder/players`, `/player-profile/resolve`), the games' quiz routes (Blurred Player, Guess the Game, Guess the Player, Higher or Lower, Trivia; Step 1 counted these with the roots and searches as routes that don't need one), routes under their page's own badge (`/backtest`, `/explain/*`, `/clusters/player/{name}`, `/games/wp-replay/*`, the profile's `/contracts/player/{id}`, `/player-profile/{id}/shot-zones`, `/players/playtype-profile/{name}`), an image URL (`/media/player-image/{name}`) and the routes no page calls (R8-017: `/players/profile/{name}`, `/similarity/career/{name}`, `/impact/player/{name}/{season}`). No data route a page reads without a badge is left.
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
- **Severity:** looks wrong (code hygiene) · **Step:** 8 · **Status:** fixed in the Step 8 commit (wrappers removed; the three routes kept)
- **Where:** routes no frontend file names: similarity `/similarity/career/{name}`, impact `/impact/player/{name}/{season}`, `/shots/quality-map/options`. `services/api.js` wrappers no component calls (9 of 202): `fetchPlayerShotZones`, `fetchLeagueShotZones`, `fetchRapmValidation`, `fetchPlayerImage`, `fetchPlayerProfile`, `fetchShotSeasons`, `fetchProjectionBacktest`, `fetchPlayerProjections`, `fetchLedgerHindcast` (their routes may still be called another way).
- **Found by:** comparing the crawl's route list with `frontend/src`. Keep (API-only, tested) or remove, with a reason either way.

- **Step 4:** `/players/profile/{name}` is DB-only now (its live path is gone); `/shots/league-zones/{season}` no longer fetches by default (R8-007). The unused routes themselves are still Step 10's call.
- **Step 8:** 11 wrappers were unused by then (the 9 above plus `fetchPlayerSuggestions` and `resolvePlayerId`, left behind by Step 5's id pickers): removed from `services/api.js`. The production bundle is byte-identical (the build already dropped unused exports), so this is tidiness, not speed. The three routes stay: they are API-only, cheap, answered by the crawl and covered by tests (`/impact/player` by R8-069's).
### R8-018 · Typed-name tools pick the first of two same-name players
- **Severity:** wrong number · **Step:** 5 · **Status:** fixed in the Step 5 commit
- **Where:** every caller of `impact_core.find_player()`: Player Comparison (+ `/shots/player/{name}/zones`), Trend Analysis, Radar, Scouting Report, With/Without a Star, and others. 19 names belong to two players.
- **Found by:** known gap (README Known real gaps, round 7 step 5). Fix: `resolve_player` + optional `player_id`, the Shot Charts pattern.
- **Step 5:** worse than written: the lookup was an unordered `SELECT DISTINCT ... LIMIT 1`, which returned the lower id, so typing "Brandon Williams", "Nate Williams" or "Johnny Davis" opened the *retired* player. `find_player()` (and similarity_api's `find_player_id()`) now take the latest career of an exact name (last season, then career minutes: the frontend's `namesakes()` order), deterministically. Every route that takes a typed name takes an optional `player_id` (14 in impact_api incl. pair synergy's `player_a_id/player_b_id`, 4 in similarity_api); only `/player-profile/resolve` (name → id for links that only know a name) still calls `find_player`, and a test checks that. Frontend: one id-aware picker (`utils/usePlayerSuggestions.js`, suggestions "Name · 2021-22 to 2025-26" from `/workbench/entities` in an `AutocompleteDropdown`) and `common/NamesakeNote.jsx` ("Another player is also called …: the one of …") on Player Comparison (`aid=`/`bid=` in the link), Trend Analysis, Radar, Playoff Forecaster, Matchup Finder, Season Similarity, Career Trajectory and Player Archetypes; ids passed from the scouting cards, the player modal, Watchlist, Rookie Class Tracker and Ctrl+K (opens the profile by id, no name round trip). The Draft Prospect headshot lookup takes the NBA player whose career starts after the college season. Tests: `api/tests/test_round8_ids.py` (all 19 names by name and by id; 8 season routes and 4 similarity routes on both Brandon Williamses and both Mike Jameses).

### R8-019 · Game Log and Game Finder show no +/-
- **Severity:** looks wrong · **Step:** 5 · **Status:** fixed in the Step 5 commit
- **Where:** profile Game Log, `?page=gamefinder`. The right per-game numbers exist in `player_game_onfloor` (98.2% exact against ESPN's box score), and the Workbench already reads them.
- **Found by:** known gap (README).
- **Step 5:** the catalogue's `plus_minus` column (player_game) now also has page `game_finder` and the join is `workbench_catalogue.ONFLOOR_JOIN`, so the Game Finder gains the stat (condition, sort, streak average) and every row/log carries `plus_minus` (None in the 12 games whose play-by-play doesn't reconcile, said in `notes.plus_minus`); Game Log averages it over the games that have one. Jokić 2025-26: 65 games, +8.5. Tests: rows equal `player_game_onfloor`; Finder count of `plus_minus >= 40` (131 player-games) equals SQL; one game (DEN at IND, 2022-11-09) against ESPN's live box score (skips offline). `test_workbench.py`'s frozen Game Finder literals now add exactly this stat and join.

### R8-020 · 3-5 player-seasons a year carry a team the player never played for
- **Severity:** wrong number · **Step:** 5 · **Status:** fixed in the Step 5 commit
- **Where:** `player_season_stats.team_abbreviation` from 2020-21 (2024-25: Bane ORL, Anthony and Caldwell-Pope MEM, Ingram TOR, Kleber LAL); shown by Player Stats, Role Player Finder, leaderboards. Methodology open issue.
- **Found by:** known gap (README, Methodology).
- **Step 5:** measured 22 rows 2020-21 to 2025-26 (4, 3, 3, 3, 5, 4 a season; 2025-26 adds Anthony Davis and D'Angelo Russell under WAS, who played only for DAL). Ten tables copied the same team from the season rows (`defender_dad` 21 of the 22, `defender_dfg`, `player_gravity`, `player_hustle`, `player_shot_making`, `player_shot_tracking`, `shot_value_added` 22, `player_clusters` 15, `player_roles` 14, `contract_value` 5). **The table is left as loaded** (the lineup parser and the paper's data audit read it; changing it moves paper numbers = owner's call): `api/season_team.py` gives the team as shown (the last team he played for in `player_game_lines` where the row's team isn't one of his) as a SQL `CASE` on player_id + season, so it works on every table above, cached per process (restart after rebuilding the lines). Applied in Player Stats, Leaderboard Builder (shown and filtered on, team list), Composite, Regression Explorer, Breakouts, Role Player Finder, Stat Leaders, Player Comparison, the player profile (seasons, DAD, contracts), Trade Analyzer and Trade Impact rosters and contracts, Guess the Player's team clue, Playoff Forecaster, Era Translator, Aging Curves, Heliocentricity, Impact Rankings, Learn, Higher/Lower, DAD Index, Spacing Lab / Gravity, Hustle leaders, Shot-making, Shot value, Contract Value, the awards service (MVP/DPOY/ROY/All-NBA lists), Season Similarity / Stat Line Finder, Archetypes, and the Workbench (`player_season` team field, grouping and filter through `SEASON_TEAM_JOIN`, a subquery Postgres drops when nothing reads the team; the player search's latest team). The team page already used the play-by-play. Methodology open issue removed. Before 2020-21 there are no per-game teams to check.

### R8-021 · With/Without a Star's point differential comes from stats.nba.com's summed plus-minus
- **Severity:** wrong number · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** `/teams/with-without/...`: live `PLUS_MINUS` from leaguegamefinder, which differs from the final margin in some games (160 of 20,348 in the stored copy). Its Methodology card says so.
- **Found by:** known gap (README). Fix: stored data (`game_scores` + `player_game_lines`, 2020-21 on).

- **Step 4:** `/teams/with-without/{team}/{season}` reads `game_scores` (result and the real final margin) and `player_game_lines` (who had minutes, joined on team + date like the Game Log) for every season with lines; takes an optional `player_id`, which the page passes from the roster. PHI 2023-24 / Embiid: 31-8 and 16-27 as before, +10.36 / −3.58 average margin (the live version's summed-plus-minus margins are gone for these seasons). Methodology card and README updated. Tests `test_with_without_reads_stored_data_from_2020_21`, the With/Without smoke test.
### R8-022 · ESPN's no-id players get no line and break their stints
- **Severity:** wrong number · **Step:** 6a (owner's OK) · **Status:** fixed in the Step 6a commit
- **Where:** `scripts/pbp_lineups.py` resolves names only through `player_season_stats`. 100-155 names a season get no `player_game_lines` row, and their stints aren't `tracked_ok` (2-6% of minutes before 2025-26).
- **Found by:** known gap (README). Fix: an exact name + team + season fallback through `player_bio` / `player_id_map`.
- **Step 6a:** measured first: 274 name-team-seasons failed every lookup (34-97 a season, 1 in 2025-26). `load_season_names()` now carries a `player_bio` index (an exact normalised name one player active that season has; for a player who changed teams, only his teams per `player_team_stints`), tried after every existing step, so no name that matched before matches differently. The team can't be checked inside the build for single-team seasons (no stored source has a team for the players `player_season_stats` leaves out), so it was checked outside it: all 258 matches then had the team Basketball-Reference lists (Kaggle export), and the 234 player-seasons that gained lines all sit on BRef's teams, GP exact for 228. Five more spellings ESPN's own rows pair with the official name went into `ALIASES` (Jeenathan/Nate Williams and four others; not guesses: the same event carries both). 9 left on purpose (listed in the Step 6a section). Tracked minutes 93.7-99.8% → 99.6-99.98% a season. Test `test_round8_rebuild.py`.
### R8-023 · Phantom minutes in 9 player-games (team-less substitution)
- **Severity:** wrong number · **Step:** 6a · **Status:** fixed in the Step 6a commit
- **Where:** `Game.run()`, `player_game_lines` (e.g. Dončić 45.4 min on 2022-01-30 instead of 37.1). Methodology open issue. The smoke test pins the 9.
- **Found by:** known gap (README, Methodology).
- **Step 6a:** 12 team-less substitutions in six seasons: the 11 that name nobody leaving each duplicate the next, tagged substitution and are ignored; the one naming both players (Brandon Clarke for Timmy Allen, MEM 2023-24) goes to the team both play for. `run()` counts only the two teams' floors (like the stints). Dončić 37.05 min; lines = stints in every player-game; the Methodology open issue is removed; smoke and consistency pins now 0.
### R8-024 · One `player_game_lines` row has team 'NaN'
- **Severity:** looks wrong · **Step:** 6a · **Status:** fixed in the Step 6a commit
- **Where:** Miye Oni, 2021-11-20 (`espn_401360071`), 195 s, no stats.
- **Found by:** known gap (README).
- **Step 6a:** the cause was a team-less substitution: its NaN team field was taken as his team (`if team_hint:` is true for NaN). Now only a real team of the game is a team hint, and the team-less duplicate is ignored: UTA, 89 s. The column is `NOT NULL` and the build stops on a row without a team.
### R8-025 · `player_game_lines.tm_pts/op_pts` double-count where ESPN's score field is stale
- **Severity:** wrong number · **Step:** 6a · **Status:** fixed in the Step 6a commit
- **Where:** on-court points in the lines (25% of full-minute team-games don't sum to 5× the margin). Nothing shown reads them since round 7. Fix: points from the made shots like the stints, or drop the columns.
- **Found by:** known gap (README).
- **Step 6a:** kept (Stat Stability's plus-minus and rating rows and the paper audit read them) and recomputed with the stints' rules: made shots and free throws, or the running maximum of the score fields in the 170 games where only that adds up (`points_method()`, the stints' own pick), free throws at the foul. `tm_pts - op_pts` = `player_game_onfloor.plus_minus` in all 154,073 reconciled player-games; full-minute team-games adding up to 5 × the margin 75% → 99.9% (14,389 of 14,401); 98.2% exact against ESPN's box-score +/- (36.6% before). The paper's audit class that measured this (R8-074) needs Step 6b/6c.
### R8-026 · `lineup_stints` credits a free throw at the shot, not at the foul
- **Severity:** wrong number · **Step:** 6a + 6b · **Status:** fixed (the stints in the Step 6a commit, everything fitted on them in the Step 6b commit)
- **Where:** the stints and everything on them (RAPM, Rating Tracker, lineups, pairs, xRAPM, rim deterrence, rotations, lineup predictor, report card, the paper). The box-score rule (`build_player_game_onfloor.py`) is 98.2% exact vs ESPN's +/-; at the shot, 42.5%.
- **Found by:** known gap (README, round 7 step 2).
- **Step 6a:** `Game.stints()` credits a free throw (attempt, make, points, a score step at it) to the stint on the floor at the foul (`is_foul_anchor()`, moved into `pbp_lineups.py` from the on-floor build), keeps a zero-second stint that receives one, and lists the moved ones in `lineup_stints.foul_ft_actions` (76,444; the action range is unchanged in meaning). Stints' plus-minus vs ESPN's box score 42.5% → 98.2% exact; `player_game_onfloor`'s independent replay now checks the stints directly (0 of 154,334 differ). `lineup_seasons` / `pair_seasons` are rebuilt on them; RAPM and everything else fitted on the stints is Step 6b's.
- **Step 6b:** every table on the stints refitted (RAPM, the Rating Tracker, xRAPM, rim, rotations, possessions, the lineup predictor, the report card, the paper's evaluation, tests, audit, Data Quality, beliefs, ablations); `paper_xrapm.py` now moves the listed free throws to the stint at the foul (all 76,444; tracked stints' attempts still equal their stored FGA/FTA). What moved is in the Step 6b section.

### R8-027 · `lineup_stints` takes ESPN's text two-or-three call on misses
- **Severity:** wrong number (not shown anywhere yet) · **Step:** 6a · **Status:** fixed in the Step 6a commit
- **Where:** `home_fg3a`/`away_fg3a` in the stints. `player_game_lines` and the Play Finder use the shot chart's call (`miss_three_calls()`).
- **Found by:** known gap (README).
- **Step 6a:** `build_lineup_stints.py` passes `miss_three_calls()` like the lines: stint 3PA 508,206 → 515,358; per team-game equal to the lines' wherever both count the same attempts (test).
### R8-028 · Four 2025-26 games are missing from the shot chart; 2025-26 match rate 97.5%
- **Severity:** wrong number · **Step:** 6 (owner's OK: changes `player_shots` and the shot chain) · **Status:** open
- **Where:** `player_shots` has no rows for 0022500259-0022500261 and 0022500265 (2025-11-19/20). 2025-26's ESPN-to-chart match rate is 97.5% vs ≥ 99.8% elsewhere. Affects shot-making, quality map, shot value and xRAPM.
- **Found by:** known gap (README: "re-fetch when stats.nba.com is reachable again"). It is reachable now (R8-006).
- **Step 3 (re-measured 2026-10-05):** per game, the chart is short of the lines' FGA in 776 of 1,225 games of 2025-26 (1,079 attempts in all, at most 6 a game, spread one or two a game) against 18 games in 2024-25; 805 games have at least one player-game where the two disagree (2024-25: 98 player-games). Per player-season the chart still lands within the per-game rounding of NBA.com's FGA × GP once the four games with no rows are added back (`test_shot_chart_fga_matches_the_season_table` pins both). So the whole 2025-26 chart is thin, not just four games: the re-fetch should be the full season.

### R8-029 · Hot Streak Checker's "carries on" share includes the shuffled-null centre
- **Severity:** wrong number · **Step:** 7 · **Status:** fixed in the Step 7 commit
- **Where:** `hot_streak_persistence.slope` (7-86% even with games shuffled). Methodology open issue. `paper_beliefs_summary` holds the null centre.
- **Found by:** known gap (README, Methodology).

- **Step 7:** `hot_streak_persistence` stores `null_slope` and `net_share` (+ ranges, both baselines) from the paper's own 2,000 shuffles (`api/hot_streaks.SeasonMatrix`, shared, equal to `paper_beliefs_summary` exactly); the card, list and verdict show both; the Methodology open issue is gone. Shooting runs: share 8/12/28% (3P%, 5/10/20 games) → beyond the null 1/0/4%; points 34/48/66% → 19/19/7%. Tests in `test_round8_step7.py`.
### R8-030 · Garbage-Time Deflator still reads ESPN's raw clock
- **Severity:** wrong number (not measured) · **Step:** 7 · **Status:** fixed in the Step 7 commit
- **Where:** `build_leverage_splits.py` (win probability and clutch labels). Measure how much it moves on `pbp_event_clock` first.
- **Found by:** known gap (README).

- **Step 7:** measured, then switched (Step 7 section above): filtered PPG moved in 501 of 2,111 qualified player-seasons (max 0.31), garbage share up to 4.9 points, 4 badges. Test `test_deflator_reads_the_corrected_clock` (the stored clutch count = the corrected clock's, ≠ ESPN's).
### R8-031 · `/games/by-date` has no stored fallback for past dates
- **Severity:** broken · **Step:** 7 · **Status:** fixed in the Step 4 commit (taken with R8-002)
- **Where:** Live Scores date browsing. README: "returns an empty list for an older historical date".
- **Re-measured 2026-10-05:** `/games/by-date?date=2025-01-02` → 6 final games with scores in 1.0 s, equal to `game_scores` (6). It works because stats.nba.com answers scoreboardv2 again; it would be empty again if that stops (R8-003). Fix: past dates from `game_scores` (or ESPN by date).
- **Found by:** known gap (README) + crawl.

- **Step 4:** stored dates answer from `game_scores` / `postseason_games` without any network (2025-01-02: 6 games in 0.04 s; 2025-06-05: Finals game 1 as "Playoffs"); other past dates (preseason) from ESPN. Test `test_by_date_reads_stored_results_first`.
### R8-032 · Pair Synergy's model still uses the old in-house defensive BPM
- **Severity:** wrong number · **Step:** 7 (owner's call: a retrain) · **Status:** open
- **Where:** Pair Synergy reads `dbpm_repro` until retrained. Methodology open issue "Some models can't be retrained right now" says stats.nba.com is unreachable, which is no longer true (R8-006).
- **Found by:** known gap (CLAUDE.md Open items, Methodology).

- **Step 7 (left open):** the retrain changes Pair Synergy's model and needs the owner's OK; the Methodology wording about stats.nba.com was already corrected in Step 4.
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
- **Severity:** looks wrong · **Step:** 4 · **Status:** fixed in the Step 4 commit
- **Where:** `DashboardHome.jsx` `resolveSeasonWithData()` starts at `/meta/current`'s season (2027) and walks back on failure, so every Dashboard load logs `404 GET /mvp/predict/2027` in the browser's network panel before 2025-26's answer. The page itself is right ("MVP Favorite · 2025-26").
- **Found by:** the scanner's failed-request list. Fix with R8-004's season work in Step 4 (e.g. a route that says which seasons the award models cover), not a guess in the page.
- **Step 2c:** the landing page asks for it too (`404 GET /mvp/predict/2027`, twice in dev's StrictMode, on every load).

- **Step 4:** `/meta/current` returns `stored_season` (the latest stored season) and the Dashboard and landing page start their award-season walk there: the Dashboard's network list on 2026-10-05 is `/mvp/predict/2026` 200 and no 404. Test `test_pages_use_nba_dates_and_the_routes_season_labels`.
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
- **Severity:** slow · **Step:** 8 · **Status:** fixed in the Step 8 commit (3.3 → 2.3 s; what's left is the check itself)
- **Where:** `?page=quality` fires one `/data-quality/check/{key}` per class (14, twice in dev's StrictMode); cold, `score_fields` takes 3.2-4.3 s and `clock_offset` 3.0 s (fast once cached).
- **Found by:** the scanner's slow-request list.
- **Step 8:** each of the two is two independent full passes over the 3.4M ESPN events (window functions). `data_quality_lib.live_score_fields` / `live_clock_offset` take an optional second cursor and the router runs the two passes on two connections at once (`TWO_PASS`; the build still runs them one after the other; same queries, `test_two_pass_checks_equal_one_connection`), and a check runs once at a time (`_LIVE_LOCKS`: the StrictMode duplicate waits and reads the cached answer instead of running the same scan beside it). Alone, cold: `score_fields` 2.96 → 1.40 s, `clock_offset` 1.22 → 0.79 s; the page settles 3.3 → 2.3 s. Faster would need an index on `pbp_events (game_id, action_number, id)` (~100 MB, too big for Layerbase) for a check that is cached after its first run.

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

### R8-068 · Stat Leaders ranks shooting percentages with no attempts floor
- **Severity:** wrong number · **Step:** 7 · **Status:** fixed in the Step 7 commit
- **Where:** Stat Leaders (`StatLeaders.jsx`), `GET /leaders/fg_pct|fg3_pct|ft_pct` (`api/routers/leaders.py`).
- **Reproduce:** `/leaders/fg3_pct?season=2025` → Dru Smith 53.3% first (a handful of attempts). The stored path and the old live path both rank every player with a non-null percentage; the Leaderboard Builder applies an attempts floor for the same stats (`ATTEMPT_DEFAULTS`).
- **Found by:** Step 4, while exercising the route after the fallback change. Fix: reuse the Leaderboard's attempt floors (catalogue) in `_stored_leaders`, and state the floor on the page.

- **Step 7:** `routers/leaders.qualifying()` (Leaderboard Builder defaults) on the stored and live paths, the hustle leaders and the Dashboard's top scorer; stated on the page. 2025-26 3P% leader 100% (Mark Williams, 1-for-1) → 47.8% (Luke Kennard); the other moves are in the Step 7 section. Tests `test_stat_leaders_rank_only_qualified_players`, `test_live_leaders_apply_a_season_in_progress_floor`.
### R8-069 · `/impact/player/{name}/{season}` answers 500 for a season with missing stats
- **Severity:** broken · **Step:** 5 · **Status:** fixed in the Step 5 commit
- **Where:** `api/routers/player_impact.py`: `round(float(None))` on usage / net rating / win % (pre-2010 rows have none), e.g. the 1997-98 to 2002-03 Brandon Williams in 2002-03.
- **Found by:** Step 5's shared-name tests. No page calls the route today. Fix: None for a missing stat.

### R8-070 · Player Comparison's archetype chip is 3.9:1 in Paper
- **Severity:** looks wrong · **Step:** 5 · **Status:** fixed in the Step 5 commit
- **Where:** `.hb-compare-bio-archetype` (brand orange on the orange tint), both bios. Step 2a's sweep compared players whose bios hadn't rendered.
- **Found by:** Step 5's page scan. Fix: `--text` on the tint (Ink unchanged in look, passes).

### R8-071 · Profile charts: Game Log per-game dots and shot-zone tints under 3:1
- **Severity:** looks wrong · **Step:** 7 (or owner's call, like R8-059) · **Status:** open (the dots fixed in the Step 7 commit)
- **Where:** player profile (e.g. `?page=player&id=1630217`): the Game Log rolling chart's per-game dots (`--text-3` at 55% opacity, 2.3:1 Paper / 2.5:1 Ink; they are context for the rolling line), its brand-orange line (2.9:1, = R8-059), and the shot-zone map's tinted cells (1.3-1.5:1 against the court).
- **Found by:** Step 5's page scan with the `marks` check, which step 2a's sweep of the profile predated. Not caused by Step 5.

- **Step 7:** the per-game dots now 0.8 opacity (3.3:1 or more on every Paper/Ink surface; was 2.2-2.6:1). Left for the owner with R8-059: the orange rolling line (2.9:1), the shot-zone tints (1.3-1.5:1 against the court; area fills whose numbers are printed), and, found in the same scan, the profile's shot-mix bars (`.sm-chart`, green/amber/pink 2.0-2.7:1 in Paper; stacked segments).
### R8-072 · ESPN's text spells some players differently from its own player_name field, so their substitutions out weren't applied
- **Severity:** wrong number · **Step:** 6a · **Status:** fixed in the Step 6a commit
- **Where:** `player_game_lines` minutes and the stints' lineups in the games concerned: e.g. DET-IND 2024-10-23, where the rows carry player_name "Ronald Holland II" (with his id) and the text "Ron Holland II", so "Tim Hardaway Jr. enters the game for Ron Holland II" removed nobody (Holland 31.9 minutes instead of 14.9; his two steals credited to no one); POR's last five games of 2022-23, where ESPN writes "Jeenathan Williams" for Nate Williams (48 minutes on 2023-04-09).
- **Found by:** Step 6a's diff (every changed seconds value was traced to its cause). Fix: the `player_bio` fallback answers "Ron Holland" (one player of that name active in 2024-25); five spellings ESPN's own rows pair with the official name in the same event went into `ALIASES`.

### R8-073 · Pair Chemistry adds up lineup minutes rounded to 0.1 with ties going up
- **Severity:** wrong number · **Step:** 7 (or 8) · **Status:** fixed in the Step 7 commit
- **Where:** `/lineups/pair-grid` (`api/routers/pair_chemistry.py`) sums `lineup_seasons.minutes`, each `ROUND(…, 1)` of integer-second totals, which land on a .05 tie about one time in six and round up: about +0.008 minutes a lineup. Mikal Bridges 2022-23 (BKN + PHX): 2,965.8 grid minutes for 2,962.8 on the floor (NBA.com 2,963.1).
- **Found by:** `test_pair_chemistry_grid_known_team` after Step 6a's rebuild tracked all of his minutes (the gap hid it before; the test's bound now allows 0.2%). Fix: sum seconds (or unrounded minutes) and round once.

- **Step 7:** `seconds` (exact) in `lineup_seasons` / `pair_seasons`, summed as numeric with the possessions (reproducible digits); Pair Chemistry and the Workbench add seconds. Bridges 2,965.8 → 2,962.9. Test `test_lineup_and_pair_minutes_add_up_to_the_seconds`.
### R8-074 · The paper's audit classes on the lines and stints measure what Step 6a fixed
- **Severity:** wrong number (paper) · **Step:** 6b (rerun) + 6c (rewrite) · **Status:** fixed in the Step 6c commit (the rerun in the Step 6b commit)
- **Where:** `paper_data_audit.py`: `oncourt_off_share` (the paper's "on-court margin isn't 5 × the final in 25% of full-minute team-games") reads `player_game_lines.tm_pts - op_pts`, now 0.1%; `unidentified` (untracked minutes, 2-6% a season) is now 0.02-0.4%; `teamless_sub` and the NaN team are no longer in the lines. The feed's own errors are still there (`score_steps_miss` measures the stale score fields directly; ESPN still sends the no-id names and the team-less substitutions).
- **Found by:** Step 6a (`test_audit_matches_the_database_now`, `test_paper_data_audit.py`). Fix: Step 6b reruns the audit and `build_data_quality.py`; Step 6c rewrites the sentences (what the feed gets wrong vs what the platform now corrects), e.g. measure the on-court class from the score steps if the paper keeps it.
- **Step 6b:** the audit and Data Quality reran. The audit now measures `oncourt_off_share` 0.08% (was 25%), unidentified minutes 0.02-0.16% a season (2-6%), the team-less class 12 substitutions in 9 games adding 0 player-game seconds (`teamless_extra_min/max` are None), 0 'NaN' lines, missed threes worded as twos 8,708 (8,688). The script crashed after writing on that None (fixed: the print), and `api/data_quality_lib.py` words the empty class and sub-1% shares; `paper_numbers.py` still stops on it (`float(None)`), so Step 6c decides the paper's wording for the class before regenerating.
- **Step 6c:** the class is printed as what the parser now does: team-less substitutions 'repaired' (none left in the game lines; claimed 0), unidentified minutes 0.02-0.16% with two decimals and the exact-name match named in the handling, the on-court class a count (12 of 14,399 team-games, claimed to be in unreconciled games); the NULL excess is stored without a macro. The paper's Data section says how the no-id players are matched and that a free throw counts for the five at the foul. Data Quality: a team-less substitution no longer flags a game (306 flagged).

### R8-075 · Rotations' glue-back check failed in 5,189 games after Step 6a
- **Severity:** broken (a build check) · **Step:** 6b · **Status:** fixed in the Step 6b commit
- **Where:** `scripts/build_rotations.py` replays every game with the shared parser to cut it at 5:00 left in the fourth, then checks the glued pieces equal `lineup_stints`. Since Step 6a the stints take the NBA shot chart's two-or-three call on misses (`miss_threes`); the rotations replay didn't, so its 3PA differed and only 2,043 of 7,232 games "glued back" (`rotation_closing_games.matches_stints`). No stored output counts threes, so no page number was wrong.
- **Found by:** Step 6b's rebuild (the build's own printed check; the smoke test reads the stored flag and would have failed). Fix: pass `miss_three_calls()` like `build_lineup_stints.py`. Now 7,229 of 7,232: the known espn_401468511 and two new ones the 6a free-throw rule exposed (espn_401468743, espn_401704644: substitutions between two free throws that ESPN's clock puts 12-15 s apart and the corrected clock at the trip's first free throw; with the second free throw credited at the foul the lineup in between has neither time nor anything credited on the corrected clock, while the stints, on ESPN's clock, keep its 12-15 s). The smoke test now pins the three game ids; README Known real gaps and the build's docstring say why.

### R8-076 · The Rating Tracker re-chose its settings on the rebuilt stints and now trails BPM on the test season
- **Severity:** wrong number (paper) · **Step:** 6c (rewrite) · **Status:** fixed in the Step 6c commit (the paper says it plainly in all its places; README's round-6 note annotated)
- **Where:** `rating_tracker_fit` / `paper_eval` model `rapm_tracker`. The same rule (pooled next-season RMSE over the three tune pairs, Powell from two starts, both agreeing) picks λ₀ 421, λq 576, λb 10,443, k 0.58, φ 0.80 (was 4,918, 3,424, 11,050, 0.76, 0.89): much weaker carry-over. On the rebuilt stints the earlier settings score 14.416 on the tune pairs against 14.384, so the rule's choice is right by its own criterion; on 2025-26 they would have scored 15.30 against the new 15.53. Under the protocol the tracker − BPM difference is −0.29 (tune), −0.26 (validate) and **+0.22 [+0.05, +0.40] on the test season** (was −0.01 [−0.13, +0.10]); year-to-year r 0.80 → 0.71 (BPM 0.74); the platform's next-season scale for 2025-26 is 0.85.
- **Found by:** Step 6b. Not changed (re-picking after seeing the test season would be peeking). The app's cards and README say it; the paper's "the first RAPM version never behind BPM out of sample" (README Round 6 step 7 and the paper's tracker paragraph) is Step 6c's to rewrite. `test_rating_tracker.py`'s scale bound restated (0.9-1.1 → 0.8-1.1).
- **Step 6c:** the paper's abstract, contribution 3, the tracker subsection, the report-card paragraph and the conclusion now say the tracker trails BPM on the test season by more than its interval (+0.22 [0.05, 0.40]), that BPM has the lowest test-season error, that the tracker is level with RAPM + prior (validate, test) and the three-season window (every phase) and less reliable than BPM year to year; the 8p/6p versions never carried the tracker. Claims in `paper_numbers.py` pin each sentence.

### R8-077 · Card and README numbers that were already stale before the rebuild
- **Severity:** wrong number · **Step:** 6b · **Status:** fixed in the Step 6b commit
- **Where / what:** found while re-reading every number on the rebuilt tables. Assist Network card: "0 of 3,172 player-seasons differ" from the Game Log (4 differ by one assist, the known-facts suite pins them; now "all but 4 of 3,761 player-team-seasons"). Rim Deterrence card: year-to-year "948 player pairs, 0.34 / 0.17 / 0.36" could not be reproduced by any pairing tried; restated with its definition (913 pairs, one row per player-season, 0.34 / 0.19 / 0.37). RAPM card and README: "the Nuggets were +0.5 without him" in 2020-21 (on/off before the 2026-10-03 rebuild; −1.4 since). Pair Chemistry card and README: the 2023-24 Nuggets' 97% tracked and Jokić–Murray 1,410 minutes at +14.6 (Step 6a's rebuild: 100%, 1,424 at +15.8). README Possessions: "after a defensive rebound 1.16" (1.155 rounded; now 1.15). README Situational Splits: "73,306 qualified" (73,319 before, 73,330 now). Data Coverage: the Rating Tracker's settings "estimated by marginal likelihood" (they are chosen by next-season RMSE; the likelihood estimate is stored, not used).

### R8-078 · The ablation table named the protocol's free minimum λ = 12,000 by hand
- **Severity:** wrong number (paper) · **Step:** 6c · **Status:** fixed in the Step 6c commit
- **Where:** `scripts/paper_ablations.py` `TABLE_ROWS` row `lambda=12000`, `paper_numbers.py` (`spec["lambda=12000"]`) and `paper_figures.py` (`AbLamHighLambda` expected 12000). The paper calls that row "the tuning grid's own minimum, which the rule sets aside"; after Step 6b the protocol's free minimum (`paper_eval_choices` impact rapm_prior free_minimum) is λ 8,000, so the row would have described a different setting.
- **Found by:** the claim "lambda = 12,000 ... is the tuning grid's free minimum" failing in 6c. Fix: the row is λ 8,000 (comment says why); `paper_numbers.py` reads the row's λ from `table:rows` and claims it equals the stored free minimum; `paper_figures.py` reads it from `paper_eval_choices`. Same pattern as before: ahead on the test season (−0.14 [−0.23, −0.06]), level on validate.

### R8-079 · The 8- and 6-page papers ran a page over with the rebuilt numbers
- **Severity:** looks wrong (paper) · **Step:** 6c · **Status:** fixed in the Step 6c commit
- **Where:** `paper/nba_hub_paper_8p.tex` (9 pages), `_6p.tex` (7 pages) with the regenerated numbers and tables, before any sentence changed; the long version's ablation table overran a column by 26 pt.
- **Found by:** `scripts/paper_build.sh`. Fix: trimmed sentences that carry no result in the short versions (list in the Step 6c section); moved the long version's ablation table and shortened its caption. Now 18 / 8 / 6 pages, no overfull box.

### R8-080 · The Prediction Ledger graded 2025-26 rows logged after the season without saying so
- **Severity:** looks wrong · **Step:** 7 · **Status:** fixed in the Step 7 commit
- **Where:** Analytics › Prediction Ledger, `GET /ledger/summary` (mvp_api). Every 2025-26 row was logged on 2026-09-23 (one snapshot, after the awards), so its Brier score is not a forecast's; and the scores grade the raw model output, which runs near 1.0 for several players (MVP 0.495).
- **Found by:** Step 7, resolving the rows (R8-009). **Fix:** each resolved season carries `first_logged`, `last_logged`, `season_ended` and `logged_after_season`; the table's new Logged column says "after the season ended …: not a forecast"; the methodology text says the raw output is graded (the tooltip already did). Grading the calibrated chance instead is the owner's call (snapshots since 2026-09-27 store it). Test `test_prediction_ledger_says_when_rows_were_logged`.

### R8-081 · Stat Leaders' active stat chip was 4.0:1 in Paper
- **Severity:** looks wrong · **Step:** 7 · **Status:** fixed in the Step 7 commit
- **Where:** `.hb-rail-chips .hb-rail-item--active` (Stat Leaders, Player Stats, Hall of Fame, News): `--brand-text` on the orange tint.
- **Found by:** Step 7's sweep. **Fix:** `--text` on the tint (the accent border marks the active chip), the R8-070 pattern; all four pages clean in both themes.

### R8-082 · Profile award lists have no 2025-26 awards
- **Severity:** looks wrong (stale) · **Step:** 10 or round 9 · **Status:** open
- **Where:** `player_awards` (profiles' award lists), built by `build_player_profile_data.py` from the Basketball-Reference export (Kaggle, gitignored), which ends at 2024-25 except All-Star.
- **Found by:** Step 7 (R8-009's remainder). Fix: refresh the export (owner) and rerun the script; adding hand rows would break the table's one-source rebuild.

### R8-083 · Play Finder took 45 s to 2 minutes once a season or date range was picked
- **Severity:** slow (the page looked broken) · **Step:** 8 · **Status:** fixed in the Step 8 commit
- **Where:** Players › Play Finder, `GET /plays/finder` with `season_from`/`season_to`/`date_from`/`date_to` (`api/routers/play_finder.py`).
- **Reproduce (before):** `/plays/finder?season_from=2024&season_to=2024` 49.8 s, `?season_to=2021` 118 s, `?date_from=2025-01-01&date_to=2025-01-31` 6.6 s. EXPLAIN: for `ORDER BY p.game_no DESC ... LIMIT 50` Postgres walked `play_finder_games`' primary key backwards and, for every one of the 7,229 games, rescanned the filter's materialised plays in a nested loop (2,459 empty games × 558,797 plays for one season); the unfiltered default page took 0.6 s of 0.9 s the same way.
- **Found by:** Step 8's before/after snapshots (the crawl calls the route with its defaults only; Step 2a's sweep didn't pick a season).
- **Fix:** the page of plays is picked from `play_finder_events` (joined to the games only for the filters) ordered by `p.game_no + 0` (the same order; no index can supply it, so the planner sorts the filtered rows), then joined to the games and `pbp_events` for the 50 rows; `(event_id, cat)` is unique and every play has its game and event, so nothing can move (test). The category counts and top players come from one `GROUP BY cat, player_id` instead of two scans, cached per filter so paging doesn't recount. One season 49.8 → 0.07 s, up to 2020-21 118 → 0.06 s, the default page 0.93 → 0.36 s cold (0.88 → 0.17 s warm). All 39 snapshot queries byte-identical. Tests: `test_play_finder_*` in `api/tests/test_round8_speed.py`.

### R8-084 · `/meta/current` asked stats.nba.com for the team block on every call before opening night
- **Severity:** slow · **Step:** 8 · **Status:** fixed in the Step 8 commit
- **Where:** `impact_core.fetch_nba_api_team_stats` (Dashboard, Standings, Team Comparison, every app load through `prefetchCoreData()`).
- **Reproduce (before):** a warm `/meta/current` took 0.26-0.48 s: LeagueDashTeamStats answers, every GP is 0 (2026-27 not started), the parser returns None, and None wasn't cached, so the next call asked again.
- **Fix:** an answer with nothing in it is cached for the same 5 minutes as a full one; a failure still isn't. Warm 0.26 → 0.03 s; cold unchanged (~1.1 s, ESPN's standings). Test: `test_team_stats_cache_an_empty_answer_not_a_failure`.

### R8-085 · Guess the Game and Game Replay's game list re-read the last score of every game on each call
- **Severity:** slow · **Step:** 8 · **Status:** fixed in the Step 8 commit
- **Where:** `GET /games/guess-the-game/daily|guess|reveal` (`impact_core._guess_the_game_pool`), `GET /games/wp-replay/list` (`routers/wp_replay.py`).
- **Reproduce (before):** daily 1.2 s, guess 0.7 s, reveal 0.7 s on every call; the replay list 0.65-0.77 s per call. All four run `DISTINCT ON (game_id) ... ORDER BY game_id, action_number DESC` over the 3.6M `pbp_events` rows (0.75 s in EXPLAIN ANALYZE).
- **Fix:** kept per process after the first read, like the app's other play-by-play tables (restart impact_api after a rebuild): the pool (with a lock, read once under concurrent first calls), the daily puzzle per date (its ~470 win-probability calls were the other 0.4 s), and the replay list per season through the **unchanged** query, so games of one date keep the order it gives (that order isn't defined by the SQL; a rewritten query would reorder them). First call unchanged, then 0.00-0.01 s. Tests: `test_guess_the_game_*`, `test_replay_list_is_the_unchanged_query`.

### R8-086 · Every real `ledger_update.py` run changes the paper's inputs
- **Severity:** wrong number (the paper isn't byte-reproducible during the season) · **Step:** round 9 step 1 (paper freeze guard) · **Status:** open
- **Where:** `scripts/paper_numbers.py` `ledger()` prints `\pnLgAsOf` = `max(ledger_runs.started_at)` (UTC date), and the paper prints it and `\pnManDigest`; `paper_manifest.py`'s database digest includes the five LIVE ledger tables (`ledger_results.fetched_at` and `ledger_runs` change on every run, even with no game scored).
- **Reproduce:** run `ledger_update.py` (not a dry run), then `scripts/rebuild_all.sh paper-inputs`: `numbers.tex` changes in `\pnLgAsOf` (on a new date) and `\pnManDigest`, plus `paper/manifest.json`/`.tsv` and `SHA256SUMS`. Once the first game is scored, the claim `LgScored == 0` stops the run on purpose (round 9 step 6).
- **Found by:** Round 8.5 Step A, reading what the daily runs touch. Round 9's rule "paper-inputs byte-identical through the round" can't hold unless step 1 decides: e.g. leave the LIVE tables out of the printed digest (or print a frozen-tables digest) and pin `LgAsOf` to the paper's freeze date, or accept these two macros changing and say so.

