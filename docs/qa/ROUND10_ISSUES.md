# Round 10 issue list (the live game companion + ask in English everywhere)

Round 10 adds a live game companion (a real game's score, clock, win probability, swings and box score while it is on)
and one English box that turns a sentence into something the app already does (plan pasted by the owner per step;
design of part A in `docs/LIVE_GAME.md`). This file is its spine, in the format of `docs/qa/ROUND9_ISSUES.md`: every
problem or decision found gets one numbered entry here, and every later step reads it first and updates it in its own
commit. **Never delete an entry; mark it.**

- **Severity:** `broken` (a page, route or run doesn't work) · `wrong number` (a value is wrong, stale or at risk of
  being wrong) · `slow` · `looks wrong` (wording, labels, missing badge) · `design` (a decision a later step must take,
  written down so it isn't lost).
- **Status:** `open` · `fixed in <commit>` · `won't fix` + why · `decided` (+ the decision) for design entries.
- **Step:** the round-10 step expected to take it (10-1 live spike and design, 10-2 the live engine, 10-3 the optional
  pre-game-aware model, 10-4 the Live page, 10-5 real games, 10-6 the test sentences, 10-7 the intent engine, 10-8 the
  box in the app, 10-9 close-out).

**Round 10 step 1 (2026-10-09): 15 entries in all; step 1 logged R10-001 to R10-008 and R10-013 to R10-015** (what the
live spike and the harness found, each open or decided and saying which step takes it) **and R10-012** (a daily-update
test failing on the day's NBA.com data, for 9-7); **step 6, run beside it in another chat, logged R10-009 to R10-011**
(decided, in `api/ask_sentences.json`).

**Round 10 step 7 (2026-10-10): 21 entries in all; step 7 logged R10-016 to R10-021** (the intent engine's held-out
score cut short by Google's daily quota and how it is completed, the retry that made it worse (fixed), the misses kept
for later, and three design decisions the box in step 10-8 must respect).

## Step 1: the live spike and the replay-as-live harness (2026-10-09)

### R10-001 · The harness cannot cut the box score
- **Severity:** design · **Found by:** step 1 · **Step:** 10-2 / 10-4 · **Status:** open (decided for the harness:
  no numbers before the final)
- **Where:** `scripts/live_replay.py`. ESPN's final summary carries one box score, the final one; the harness serves
  the first n plays as if live but has no box score as it stood after play n. It keeps every athlete's id and name
  (`scripts/espn_summary.py` names the plays' participants from the box score) with no statistics, and the final cut
  is the stored summary exactly. **For 10-2 / 10-4:** on the harness the box score is empty until the final, so a
  test of the live box score runs on the recorded live summaries (`live_data/live_spike/401898395/summary/`, which
  carry ESPN's live box after every poll) or on a real game; a box score derived from the plays (points, rebounds,
  assists, fouls, turnovers, shooting splits are in the plays; minutes and +/- are not) would need a second lineup
  reader and was not built (CLAUDE.md: `pbp_lineups` is the one parser). Confirmed on the game: ESPN serves
  `boxscoreSource` "full" and every athlete's live `stats` from the first play (`MIN` "--" before a minute is played),
  so the engine reads both shapes: ESPN's "full" and the harness's "none"; `espn_live.parse_boxscore` reads the
  halftime answer as it is.

### R10-002 · The latest play's win probability sits on a different clock from the stored one
- **Severity:** wrong number (small, transient) · **Found by:** step 1 · **Step:** 10-2 · **Status:** open
- **Where:** `pbp_events.seconds_remaining` of a play is the *next* play's start (hoopR's rule, `espn_summary.
  end_seconds`), so Game Replay scores play i at the moment play i+1 begins. Live, the latest play has no next play
  yet: its only clock is its own start. Scoring every play on its own start instead moves the path by 0.002 on average
  but up to 0.17 in the last seconds of a close game (OKC-HOU 2025-10-21, 595 plays; LAL-GSW 0.0007 / 0.017; LAL-SAC
  2026-10-08 0.0005 / 0.014). **For 10-2:** score every play but the latest with the stored rule (so the live path is
  the replay's path as plays arrive) and the latest on its own start, marked provisional until the next play arrives;
  the test "same path as Game Replay's" then compares all plays but the last, on ESPN's clock (next entry).

### R10-003 · The corrected clock is not available live
- **Severity:** wrong number (known, disclosed) · **Found by:** step 1 · **Step:** 10-2 / 10-4 · **Status:** open
- **Where:** Game Replay reads `pbp_event_clock` (ESPN's made shots re-timed from the NBA's shot chart, built by
  `scripts/build_event_clock.py` after the daily update); a live game has only ESPN's own times, which stamp made shots
  a median 14 s late. **For 10-2:** the live path runs on ESPN's clock and says so (the Methodology `wp` card's live
  section); the equality test against Game Replay must fetch the replay on ESPN's clock (`_fetch_game_events` without
  `corrected_clock`), not the page's corrected one. **For 10-4:** "ESPN's clock" in the last-updated line.

### R10-004 · ESPN's own win probability is in the feed and starts from team strength
- **Severity:** design · **Found by:** step 1 · **Step:** 10-2 / 10-4 · **Status:** open
- **Where:** the summary's `winprobability` array (one entry per play: `homeWinPercentage`, `tiePercentage`,
  `playId`), present on every 2025-26 and 2026-27 summary read (checked 2026-10-09; the live feed's behaviour is in
  `docs/LIVE_GAME.md` § 1). Its first value is not the home-court rate: 0.600 for OKC-HOU on 2025-10-21 (our model
  0.558), 0.564 for LAL-GSW, 0.490 for LAL-SAC in the 2026-27 preseason with LAL a 10.5-point favourite (so the
  preseason value is not the betting line either). Mean absolute gap to our model over a game 0.025-0.051. **For
  10-2:** carry it in the answer as `espn_wp`, labelled, never mixed into ours; **for 10-4:** a second, labelled
  line, off by default or dashed. It is also the comparison line step 10-3 would score against.

### R10-005 · Pre-game odds: the ledger has none for the preseason, ESPN carries a betting line
- **Severity:** design · **Found by:** step 1 · **Step:** 10-2 · **Status:** open
- **Where:** `ledger_forecasts` (`kind = 'game'`, `forecast = 'as_is'`, key = ESPN event id) holds `p_home` and
  `exp_margin` for every 2026-27 regular-season game (locked 2026-09-30); nothing for the preseason, play-in or
  playoffs. The summary's `pickcenter` carries a DraftKings line before tip (`spread`, `overUnder`, `moneyLine`;
  DAL -1.5 / 227.5 for HOU-DAL on 2026-10-09). **For 10-2:** the pre-game line on the page is the ledger's as-is odds
  when the game has them (read-only, never the frozen model files), else none; the betting line is not shown (the
  owner's standing decision: no betting products; `Decisions.md` 2026-10-03).

### R10-006 · Before tip the summary has no clock, period or box score, and a neutral-site game is flagged
- **Severity:** looks wrong (if unhandled) · **Found by:** step 1 · **Step:** 10-2 / 10-4 · **Status:** open
- **Where:** the pre-game summary (HOU-DAL, read from 15:48 IST on 2026-10-09): `status.type` only (no `period`,
  `displayClock`), `competitors[].score` null, no `boxscore.players` key (only `teams`), no `plays` key,
  `winprobability` `[]`, `liveAvailable` false, `playByPlaySource` / `boxscoreSource` "none", `neutralSite` true
  (Venetian Arena, Macao). `espn_live._status()` already reads a missing period as 0 and a missing clock as "". The
  harness's pre-tip cut has the same shape (no `plays`, no `boxscore.players`). **For 10-2:** a
  scheduled game answers with the tip time, the teams, the pre-game line and empty plays; **for 10-4:** the card shows
  the tip and "neutral site" from `neutral_site`.

### R10-007 · A summary is 97 kB before tip and ~460 kB final; two thirds of it is not the game
- **Severity:** slow (if passed through) · **Found by:** step 1 · **Step:** 10-2 · **Status:** open
- **Where:** `news`, `videos`, `standings`, `seasonseries`, `injuries`, `leaders` and the headers' logos and links
  make up ~120 kB of a 465 kB final; `plays` 290 kB, `winprobability` 41 kB, `boxscore` 30 kB. **For 10-2:** the
  engine keeps the plays, the box score and the odds in memory and answers the browser with its own compact shape
  (the plays mapped, the WP path, the box rows), never the raw summary; a poll's answer should stay under ~150 kB.

### R10-008 · The harness is reached by monkeypatching two module constants
- **Severity:** design · **Found by:** step 1 · **Step:** 10-2 · **Status:** open
- **Where:** `api/espn_live.py` builds `SCOREBOARD_URL` / `SUMMARY_URL` at import and `scripts/espn_summary.py` has
  `SUMMARY_URL`; a test points them at `ReplayServer.scoreboard_url` / `summary_url` (`api/tests/test_live_replay.py`
  does for `espn_live`). The page's `?simulate=` mode (10-4) needs the running backend to read the harness without a
  monkeypatch. **For 10-2:** one environment variable (`NBA_HUB_ESPN_SITE`, default ESPN's host) read at call time by
  the live engine's fetches, or a source object the engine is built with; never a URL from the browser.

### R10-013 · The summary's header and `winprobability` run ahead of its `plays`
- **Severity:** design · **Found by:** step 1 · **Step:** 10-2 / 10-4 · **Status:** decided (the header is the
  headline, the plays are the chart; `as_of_play` says where the chart ends)
- **Where:** HOU-DAL 2026-10-09, one answer at a time: the header's `displayClock` ran ahead of the last play's clock
  by a median 11 s (p90 28 s, max 36 s, same period) and its score by up to 6 points (11 of 40 in-game polls, never
  behind); `winprobability` held entries for plays not yet in `plays` on 14 of 62 in-game answers (never fewer), and
  the scoreboard's score was ahead of the summary's on 7 polls. One summary is three feeds stitched at different
  moments. **For 10-2:** the score, clock and status shown come from `header.competitions[0]`; the WP path, the
  swings and the leverage from `plays`; the answer names the play the path ends on and the header's lead over it;
  ESPN's WP entries whose `playId` is not in `plays` are dropped. **For 10-4:** the score never has to agree with the
  last play on the feed, and the page doesn't pretend it does.

### R10-014 · ESPN inserts, deletes and rewrites plays after the fact; ids and positions are not stable
- **Severity:** design · **Found by:** step 1 · **Step:** 10-2 / 10-4 · **Status:** decided (plays keyed by `id`,
  replaced whole every fetch, diffed by id)
- **Where:** HOU-DAL 2026-10-09, halftime to final: two first-half plays removed (a blocked shot and its rebound at
  3:31 of the 2nd), three inserted at their place in the array with higher `sequenceNumber`s (a missed tip and its
  rebound at 11:11 of the 2nd, inserted 45 min later; a turnover), four rewritten (`type`, `text`, `participants`:
  the shot distance, the shooter); no score changed. The play count fell during the break (260 → 258 → 259 → 261); the
  array is in game order, not `sequenceNumber` order (205 gaps in 507 at the final). CLAUDE.md already notes the
  rewrite after an overturned challenge; this is the everyday version. **For 10-2:** every fetch replaces the plays
  whole; a play's identity is its `id`; "new since last time" is a set difference by id; the swings list is
  recomputed from the current plays only, never carried. **For 10-4:** a plays feed keyed by `id` re-renders a
  rewritten line in place and drops a deleted one (never "play 261 of 260").

### R10-015 · The spike's recorder lost most of the game to the Mac's idle sleep
- **Severity:** wrong number (coverage) · **Found by:** step 1 · **Step:** 10-1 (recorder fixed), 10-5 (re-measure)
  · **Status:** fixed in the step-1 commit for the recorder; the re-measure is on the owner's to-do
- **Where:** `scripts/live_spike.py record`, 2026-10-09: the Mac entered "Idle Sleep" at 17:49 IST (`pmset -g log`),
  six minutes after the tip, and dark-woke for a poll or two every 5-20 min; 24 in-game polls came at the 15 s
  cadence (the tip and the first six minutes, two pairs later), 14 gaps of up to 22 min hold the rest, and the two
  quarter breaks (`STATUS_END_PERIOD`) were never seen. A wake-up's first poll also carries a stale system clock (one
  play's wallclock sat 5 min in the future). **Fixed:** `record` runs `caffeinate -i -w <its pid>` on a Mac (the lid
  must still stay open) and `analyze` uses only polls that came on time twice in a row for the cadence and delay
  numbers. **Re-measure:** DAL vs HOU in Macao, 2026-10-11 10:00 UTC (15:30 IST), event 401898400 (`docs/LIVE_GAME.md`
  § 5); 10-5 reruns it on opening week.

### R10-012 · `test_daily_update.py` fails on the day's preseason data: one official with no name outside the run window
- **Severity:** broken (one test; the public tables are untouched) · **Found by:** step 1's full-suite run (2026-10-09,
  651 passed, 1 failed) · **Step:** 9-7 (the daily update's real week) · **Status:** open
- **Where:** `test_preseason_run_stores_every_feed` asserts no `game_officials` row with an empty name after the real
  `daily_update.py` runs the 2026-10-02..10-05 preseason window into `zz_daily_update`. The officials step fetches
  BoxScoreSummaryV3 for **every** game LeagueGameFinder lists by the run day (23 on 2026-10-09; R9-028), and for
  `0012600001` (MIA vs NOP, played 2026-10-08: the preseason ids are not in date order) V3 names official 196700035 as
  "" ; `fill_official_names` can take the name from ESPN's summary only for a final inside the run's window
  (`espn_of`), so the row stays unnamed and the test's `unnamed == 0` fails. The same answer for `0012600009`
  (official 8834, 2026-10-03, inside the window) is filled, as the docstring says. Nothing in step 1 touches this code;
  the test passed on 2026-10-07 because the game had not been played. **For 9-7:** either bound the officials step by
  `--date` too (R9-028), or look up the ESPN summary of any final the scoreboard of that game's date has (the fill then
  works for every game), and keep the test's premise; until then the test fails on any day NBA.com answers an unnamed
  official for a game outside the window.

**Round 10 step 6 (2026-10-09): 11 entries in all; step 6 logged R10-009 to R10-011** (design decisions written into
`api/ask_sentences.json` for step 10-7 to build to; no code changed, commit `c0e2d50` is the file alone). Step 6 ran
beside step 1 in another chat; both steps' entries are in this file.

## Step 6: the English box's test sentences (2026-10-09)

### R10-009 · "This season" must mean the same thing on both sides of opening night
- **Severity:** design · **Found by:** step 6 · **Step:** 10-7 · **Status:** decided (tokens in the file)
- **Where:** `api/ask_sentences.json`. The sentences were written on 2026-10-09; the engine will be tuned and scored
  around or after 2026-10-20, when `api/current_season.py` moves the app's default from 2025-26 to 2026-27 (the first
  stored regular-season final). A literal 2026 in an expected action would be wrong from that day. **Decided:** an
  expected season may be the token `current` (= `status()["current"]`), `last` (= `status()["latest_complete"]`) or
  `current-N`; a date may be `yesterday` (the NBA date before today's, US Eastern); the evaluation resolves them on its
  own day, and on Shot Charts writes the season as its label ("2015-16"). The Finder box's older file fixed the date
  instead (`workbench_parse_lib.latest_seasons()`); 10-7's eval must resolve the tokens for this file.

### R10-010 · Several pages read nothing from the link, so an English ask cannot land on them with its details
- **Severity:** design · **Found by:** step 6 · **Step:** 10-7 (10-8 if a page is to gain inputs) · **Status:** decided
  (routing rules in the file); whether to give Stat Leaders / Team Comparison URL inputs is the owner's call
- **Where:** Stat Leaders (`leaders`), Team Comparison (`teams`), Player Stats (`players`), Standings, News, Draft
  Value, Hall of Fame, Greats, Rookies have no `useInitialParams` (checked 2026-10-09). **Decided for the file:** a
  stat's leaders go to the Leaderboard Builder (`builder`: stat, from, to, team, order, n); two teams compared become
  a Workbench board (a team set and a team-season table); two players compared go to Player Comparison (`compare`:
  aid, bid, season; the names `a` / `b` are free); the rest open bare. Also pinned: Shot Charts takes `pid` and a
  season **label**, not an end year; the Player Profile has no hash, so a player's game log, on/off or projection
  opens the profile whole; Game Finder conditions travel as `f = stat:op:value,…`, Stat Line items as `stat:value`
  with percentages as shares.

### R10-011 · A board can be right in many shapes, so boards are scored more loosely than pages
- **Severity:** design · **Found by:** step 6 · **Step:** 10-7 · **Status:** decided (scoring in the file)
- **Where:** "compare LeBron, Durant and Giannis this season" names no columns: any table on the three is a fair
  answer. **Decided:** a board scores right when its sets match exactly (kinds, ids / codes as sets) and every expected
  block is matched by a produced block of the same type that agrees on every key the expected block names (named
  stats must be among a table's columns; a chart's x / y exact; a season range only when named); unasked extra blocks
  (notes, set blocks, more tables or charts on the same sets) are not penalised. Pages score exactly in every key
  except `a` / `b` beside ids on `compare`, the name beside `pid` on `shotcharts`, and paging. The file states this,
  so the measured score means one defined thing.

**Round 10 step 7 (2026-10-10): 21 entries in all; step 7 logged R10-016 to R10-021.** The engine is `api/ask_lib.py`
+ `api/ask_pages.py` + `api/routers/ask.py`; the score is `api/ask_eval.json` (`scripts/ask_eval.py`); the card is
Methodology `ask`. Dev tuning reached 60 of 60; the held-out test is in R10-016.

## Step 7: the intent engine (2026-10-10)

### R10-016 · The held-out score was cut short by Google's daily free quota: 108 of 270 sentence-runs unanswered
- **Severity:** wrong number (the stored score is partial, and says so) · **Found by:** step 7 · **Step:** 10-7 (the
  resume: any chat, or the owner, once the quota is back) · **Status:** open until `scripts/ask_eval.py --resume` has
  answered the 108 sentence-runs; then the card's and README's numbers are rewritten from the file
- **Where:** `api/ask_eval.json`. The protocol is `--split test --runs 3 --write` once, with the prompt frozen after
  dev tuning. Run 1 answered all 90 held-out sentences (84 right). The day had also carried five dev runs of 60 and the
  Finder's second calls, and at sentence 73 of run 2 Google answered 429 "the free quota for today is used up": run 2
  ended with 72 answered (68 right), run 3 got no answer at all. **Decided:** an unanswered sentence-run is recorded as
  `failed` with its error, never as wrong (`failed_by_run` = [0, 18, 90]); `GET /ask/status` reports `answered`,
  `failed_by_run` and `complete` beside `right_by_run`; and `ask_eval.py --resume` asks only the unanswered sentence-runs,
  refusing when the prompt (hashed as it was on the evaluation day), `ask_lib.py` / `ask_pages.py` (hash), the model or
  the seasons changed, so a resume can complete the evaluation but never retune it; every resume is listed in the file's
  `resumed`. Google resets the quota at midnight Pacific (12:30 IST while the US is on daylight time). The engine's
  code is therefore frozen until the resume has run: a fix to `ask_lib.py` before it would force a full re-score.
- **Numbers so far:** 152 of the 162 answered sentence-runs right; run 1 by action: open_page 58/62, build_board 5/5,
  run_finder 7/7, open_live_game 4/4, ask 2/2, refuse 8/10; median answer 1.65 s.

### R10-017 · The per-minute retry turned a dead daily quota into six hours of waiting
- **Severity:** broken (the eval, not the app) · **Found by:** step 7 · **Step:** 10-7 · **Status:** fixed in the
  step-7 commit (2026-10-10)
- **Where:** `scripts/ask_eval.py run_once()` retried every 429 three times with 65 s waits, which is right for the
  per-minute limit and wrong for the daily one: 108 unanswered sentences × 195 s. **Fixed:** a 429 whose message says
  the day's quota is used up (`workbench_parse_lib._quota_message`) ends the run at once, marks the remaining sentences
  unanswered without a call, and the later runs are recorded unanswered too; the file then says what `--resume` is for.
  The app's own box is unaffected: `POST /ask` answers that 429 with the same message and the pages work by hand.

### R10-018 · Held-out misses, kept for later and not tuned on
- **Severity:** looks wrong · **Found by:** step 7 · **Step:** 10-8 / 10-9 (new, dated dev sentences first; the test set
  is scored once) · **Status:** open
- **Where:** `api/ask_eval.json`, runs 1-2. t19 "Giannis Antetokumpo" (misspelt) → refused as `unknown_player`: the model
  kept the misspelling and the server's search is exact (folded) on the name; t23 the bare "Cooper Flagg" → Projections
  (DAL) once, `no_data` once, expected his profile (a 2025-26 rookie the model doesn't know); t62 "what if the Suns traded
  Devin Booker to the Rockets" → Trade Impact (`tradeimpact`), expected the Trade Analyzer (`trade`): the two pages'
  descriptions need a sharper line; t74 "LeBron's game log in 2012-13" → his profile, expected `no_data` (game logs
  start 2020-21; the profile has no season input, so the edge is lost on the way); t81 "turn on dark mode" →
  `would_change_data`, expected `off_topic`; t84 "guess the player game" → Games `g=guessgame` (Guess the Game),
  expected `g=guess` (Guess the Player); t06 "chart Lebron's points per game season by season" → his profile once (a
  board with a line chart in run 1). A fix means new dated dev sentences for each case in `api/ask_sentences.json`
  (never an edit of these), a prompt change, dev to 60 of 60 again, then a new `--write`.

### R10-019 · A Finder sentence costs two Gemini calls, and the two boxes' per-minute caps are separate
- **Severity:** design · **Found by:** step 7 · **Step:** 10-8 · **Status:** decided
- **Where:** `api/ask_lib.py _finder_action()` sends the sentence to the Finder box's own `workbench_parse_lib.parse()`
  so a Finder answer is exactly what the Finder box would fill (one parser, one scoring); a `run_finder` answer or a
  Finder block in a board therefore costs two calls, counted as two in `/ask`'s `CallBudget` (10 a minute). The Finder
  box's own `/workbench/parse` keeps its own 10 a minute. **For 10-8:** one shared daily quota per Google project, so the
  box should show the same "used up" message as the Finder box and never retry on its own.

### R10-020 · The engine never writes a page's default input, and never moves a season into the data
- **Severity:** design · **Found by:** step 7 · **Step:** 10-8 · **Status:** decided
- **Where:** `api/ask_pages.py` marks each enum's page default (Builder order `high`, Game Finder mode `games`, Ledger
  tab `live`, …) and `ask_lib._read_value` drops a value equal to it silently, so a link carries only what the sentence
  asked for and a page's own default can change without the engine disagreeing. A season before a data set's first
  (`first_season` per page; shots 1996-97, play-by-play tools 2020-21, Player Comparison 2009-10, salaries through
  2024-25) or after the current one is a `no_data` refusal with the span named, never the nearest season. Careers and
  "several seasons" of one player are a board, not Player Comparison. **For 10-8:** replay `href` exactly
  (`openFullUrl()`), show `notes` (what was left out) and `not_understood` with the preview, and treat `refuse` as an
  answer, not an error.

### R10-021 · The model lists "guesses" that aren't; the server filters them
- **Severity:** looks wrong · **Found by:** step 7 · **Step:** 10-7 · **Status:** decided (filtered in
  `ask_lib._real_guesses`)
- **Where:** asked to list what it guessed, `gemini-3.5-flash-lite` names the page choice itself, "this season", the
  default order or the player's team as guesses in most answers. `_real_guesses()` drops items matching `_NOT_A_GUESS`
  (the page, the season when the sentence named none and the page has a default, the entity names it resolved) and keeps
  the rest (a stat it picked for "scoring", a year it inferred). **For 10-8:** show `guessed` only when non-empty, as
  "I took … to mean …".
