# The live game companion: design (round 10 step 1, 2026-10-09)

Round 10 part A shows a real game while it is on: the score and clock, a win-probability chart that grows play by
play through the app's own model, the biggest swings, the box score and the pre-game odds, then a pointer to Game
Replay once the next daily update has stored the game. This file is step 1's design: what ESPN's feed does during a
live game (measured on 2026-10-09), the replay-as-live harness every later step tests against, the polling and cache
rules, what the page shows on each status, and the failure modes. Steps 10-2 (the engine), 10-4 (the page) and 10-5
(real games) build on it; the issue list is `docs/qa/ROUND10_ISSUES.md` (R10-001 to R10-008 and R10-012 to R10-015 are
this step's).

**Decided here**

1. **Read-only, one hop.** The browser polls *our* server (`GET /live/games?date=`, `GET /live/game/{espn_id}`,
   step 10-2); our server reads ESPN's summary and scoreboard, the same two endpoints `api/espn_live.py` and
   `scripts/espn_summary.py` already read. Nothing live is written to Postgres: the daily update stays the only writer
   of game data, and Game Replay reads the stored game the next morning.
2. **One fetch per game per 20 s, whatever the number of viewers.** A background refresher per watched game (started
   by the first request, stopped two minutes after the last) fetches the summary every 20 s into an in-memory cache
   keyed by ESPN event id; a browser request is answered from the cache at once, with the feed's age, and never waits
   on ESPN. The scoreboard is one fetch per 20 s for the day. Fetch timeouts: 3 s to connect, 5 s in all (a live
   summary is 94-425 kB; the slowest answer of 523 took 5.6 s, the median 0.56 s). Measured (§ 1): the plays change
   at most every 15 s and a play reaches the feed a median 40 s after it happened, so a 20 s poll adds at most half
   to a lag ESPN already has, and polling faster finds nothing new.
3. **The browser polls every 25 s**, pauses in a hidden tab (`document.visibilityState`), and backs off to 60 s while
   the feed is marked unreachable.
4. **The win probability is Game Replay's model on ESPN's clock.** `scripts/wpa_lib.py` (`wpa_model.pkl`,
   `wpa_scaler.pkl`, validation in `wpa_model_validation`), fed the plays mapped by `scripts/espn_summary.py`'s rule
   (`end_seconds`: a play's clock is the next play's start). Every play but the latest is scored exactly as the stored
   game will be; the latest sits on its own start until the next play arrives and is marked provisional (R10-002). The
   corrected clock (`pbp_event_clock`) does not exist live, so the live path and the page say "ESPN's clock, made
   shots a median 14 s late" (R10-003). ESPN's own win probability is in the feed (`winprobability`, one entry per
   play) and is carried beside ours, labelled, never mixed (R10-004).
5. **Swings and leverage.** A play's swing is the model's home win probability after it minus before it (Game
   Replay's `wpa`); the five largest so far by absolute swing are the "biggest plays". Leverage right now is
   `leverage_index_grid` (`time_bin` = minutes left in regulation, `margin_before`), the Garbage-Time Deflator's stored
   lattice, read-only; overtime uses its last minutes' bins as the deflator does.
6. **Pre-game odds** come only from what exists: for a 2026-27 regular-season game the locked ledger's as-is odds
   (`ledger_forecasts`, `kind = 'game'`, `forecast = 'as_is'`, key = the ESPN event id, `p_home` and `exp_margin`),
   read as a table, never through the frozen model files; a preseason, play-in or playoff game has none and the page
   says so. ESPN's betting line (`pickcenter`) is not shown (no betting products; `Decisions.md` 2026-10-03; R10-005).
7. **Players by id** through the daily update's own matcher (`fetch_pbp_espn.PlayerMatcher`, the one
   `espn_summary.event_rows` takes: the season's `player_season_stats` names, then any season's unique names, then
   fuzzy at 90): a rookie or two-way player who has no season row yet keeps ESPN's name with no id, as the stored rows
   do. The box score's `espn_athlete_id` is kept for the page's keys.
8. **The harness** (`scripts/live_replay.py`, § 2) is the test bed for 10-2, 10-4 and the browser checks: a finished
   game cut play by play, served on ESPN's own paths at any speed. The engine reaches it through one environment
   variable read at call time (R10-008), never a URL from the browser.
9. **The header is the headline, the plays are the chart.** In one answer the header's clock runs ahead of the last
   play's by a median 11 s (max 36 s) and its score by up to 6 points, and `winprobability` can hold entries for plays
   not yet in `plays` (R10-013). So the score and clock shown come from `header.competitions[0]`, the WP path, swings
   and leverage from `plays`, and the answer says which play they end on (`as_of_play`); nothing pairs a header score
   with a play. ESPN also edits plays after the fact, inserting, deleting and rewriting them with new ids in the
   middle of the array (R10-014): every fetch replaces the plays whole, plays are keyed by `id`, and a "new plays"
   feed or swing list is a diff by id against the previous answer, never the array's tail.
10. **Step 10-3 (a pre-game-aware live model) is the owner's call**, asked at the end of this step and recorded in the
   vault's `Decisions.md` (§ 6 has the facts the call rests on).

## 1. What ESPN's feed does during a live game (measured 2026-10-09)

Recorded with `scripts/live_spike.py` (`record --event 401898395 --date 20261009 --interval 15`): HOU at DAL,
2026-27 preseason, neutral site (Venetian Arena, Macao), listed tip 12:00 UTC (17:30 IST, 8:00 AM EDT), polled
every 15 s from 15:48 IST (1 h 42 min before tip) to 20:14 IST, 1 h 37 min after the final (523 polls; final HOU 135,
DAL 117, 507 plays). Every answer is saved under `live_data/live_spike/401898395/` (gitignored:
`summary/<HHMMSS>.json.gz`, `scoreboard/<HHMMSS>.json.gz`, `log.jsonl`); `live_spike.py analyze <folder>` prints the
numbers below. **Coverage caveat:** the Mac idle-slept from 17:49 IST, six minutes into the game, and woke for a poll
or two every 5-20 min (`pmset -g log`: "Idle Sleep"), so the 15 s cadence holds for the pre-game, the tip and the
first six minutes of play (24 on-time in-game polls in all) and 14 gaps of up to 22 min hold the rest; the halftime
and final transitions were caught, the two quarter breaks (`STATUS_END_PERIOD`) were not. The recorder now holds the
Mac awake itself (§ 5, R10-015). The numbers that depend on the cadence (refresh, delay) come from the on-time polls
only; the shapes, sizes, corrections and transitions come from all 523.

| What | Measured on 2026-10-09 |
|---|---|
| Answers | summary 523 of 523 (no failure, no bad shape); scoreboard 521 of 523 (one `ReadTimeout`, one `ConnectionError`, both on the first poll after a wake, i.e. this Mac's network, not ESPN's) |
| Latency | summary median 0.56 s, p90 0.73 s, max 5.56 s (a first poll after a wake); scoreboard 0.53 / 0.62 / 3.07 s |
| Size | summary 93.8 kB before tip, 108.8 kB after the first play, 278 kB at halftime, 425 kB final; the scoreboard 28-30 kB for the day's two games |
| ESPN's refresh | with 15 s polls the plays changed on 14 of 24 on-time in-game polls: 2 plays per change (median; max 5), 15 s between changes (median; p90 and max 45 s) |
| Delay | a play is in the summary a median 40 s after its own `wallclock` (p10 33 s, p90 58 s, min 28 s, max 62 s; n 14): ESPN's own lag is ~30 s before any poll interval is added |
| The tip | the jump ball's `wallclock` is 17:43:03 IST (13 min after the listed tip); the scoreboard said in progress ("11:47 - 1st") on the poll 20 s later, while the summary still said scheduled with no `plays` key; the summary flipped to in progress together with its first play on the next poll (35 s after the jump ball) |
| The header vs the plays | in one answer the header's clock ran ahead of the last play's by a median 11 s (p90 28 s, max 36 s) and its score by up to 6 points (11 of 40 in-game polls; never behind); `winprobability` had more entries than `plays` on 14 of 62 in-game answers (never fewer); the scoreboard's score was ahead of the summary's on 7 polls, never behind |
| Corrections | ESPN edits the log after the fact: between halftime and the final two first-half plays were removed (a blocked shot and its rebound), three inserted at their place in the array with higher sequence numbers (a missed tip and its rebound at 11:11 of the 2nd, inserted 45 min later; a turnover), four had `type` / `text` / `participants` rewritten (shot distance, the shooter); no score changed. The array is in game order, not `sequenceNumber` order (205 gaps in 507 at the final), and the play count fell during halftime (260 → 258 → 259 → 261) |
| Halftime | `STATUS_HALFTIME` on both feeds (`period` 2, `displayClock` "0.0", `statusPrimary` "Halftime"); the last play "End Period"; the feed kept changing during the break (the corrections above) |
| Final | the summary's status is `type` only (`STATUS_FINAL`, state `post`, `completed` true, `shortDetail` "Final"; the scoreboard's adds `period` 4, `clock` 0.0); `winner` on the competitors; the last play "End Game"; `liveAvailable` false, `boxscoreSource` "full", `recent` true; `meta.gameState` "post"; byte-identical on the 27 polls after it |
| ESPN's win probability | one entry per play from the jump ball: 0.490 at tip (the pre-tip DraftKings line was DAL -1.5, 227.5; in-game HOU -3.5, 231.5: `pickcenter` moves during the game), 0.040 at halftime (DAL down 20), 0.0 at the end; the scoreboard's `situation.lastPlay.probability` carries the same number |

**The summary in each state** (the keys 10-2 reads; everything else in the 97-465 kB answer is news, videos,
standings, injuries, season series, leaders and logos, ~120 kB of a final; R10-007):

| State | `header.competitions[0]` | `plays` / `winprobability` | `boxscore.players` | `meta` |
|---|---|---|---|---|
| before tip | `status.type` only (`STATUS_SCHEDULED`, state `pre`, `detail` "Fri, October 9th at 8:00 AM EDT", `shortDetail` "10/9 - 8:00 AM EDT"; no `period`, `displayClock`); `competitors[].score` null, no `linescores`; `liveAvailable` false; `playByPlaySource` / `boxscoreSource` "none", `boxscoreAvailable` false; `neutralSite` true | no `plays` key / `[]` | no `players` key (`boxscore` has only `teams`); `leaders` per team with `leaders: []`; `lastFiveGames` is served only in this state | `gameState` "pre" (no wallclock keys) |
| in progress | `status` = `displayClock` "11:39", `period` 1, `displayPeriod` "1st", `type` (`STATUS_IN_PROGRESS`, state `in`, `detail` "11:39 - 1st Quarter", `shortDetail` "11:39 - 1st", `statusPrimary` "11:39", `statusSecondary` "1st"); no numeric `clock` (the scoreboard's status has one); `competitors[].score` as strings, `linescores` [{`displayValue`}] per period played, `record`; `liveAvailable` true, `playByPlaySource` / `boxscoreSource` "full", `boxscoreAvailable` true, `wallclockAvailable` true | the plays so far, each with `id`, `sequenceNumber`, `type.text`, `text`, `period.number`, `clock.displayValue` ("7:32", "45.3" under a minute), `homeScore` / `awayScore`, `team.id`, `participants[].athlete.id`, `wallclock`, `scoringPlay`, `scoreValue`, `shootingPlay`, `coordinate`; one `winprobability` entry per play (`homeWinPercentage`, `tiePercentage`, `playId`) from the jump ball, at times for a play not yet in `plays` | every athlete with live `stats` in `names` order from the first play (`MIN` "--" before a minute is played), `starter`, `active`, `didNotPlay`; team `totals`; `boxscore.teams` splits; `leaders` per team filled from it | `gameState` "in"; `lastUpdatedAt` = `lastPlayWallClock` (the last play's wallclock, not the fetch time); `firstPlayWallClock` |
| final | `status.type` `STATUS_FINAL` (state `post`, `completed` true, `shortDetail` "Final" / "Final/OT" / "Final/2OT"); scores and `winner`; `linescores` per period | all plays (477-595 in the three 2025-26 / 2026-27 games read), the last one "End Game"; `winprobability` as long as `plays` (its last value 1.0 or 0.0) | every athlete with `stats` in `names` order (MIN, PTS, FG, 3PT, FT, REB, AST, TO, STL, BLK, OREB, DREB, PF, +/-) | `gameState` "post", `lastUpdatedAt`, `firstPlayWallClock`, `lastPlayWallClock` |

**The scoreboard in each state** (`events[].competitions[0]`): `status` always has `clock` (a number), `displayClock`,
`period` and `type`; before tip `STATUS_SCHEDULED` with `shortDetail` "10/9 - 8:00 AM EDT", `period` 0, scores "0";
in progress `STATUS_IN_PROGRESS` "11:39 - 1st" with `linescores` [{`value`, `period`}], `playByPlayAvailable` true
and `situation.lastPlay` (id, type, text, team, `probability`, `athletesInvolved`); halftime `STATUS_HALFTIME`,
`period` 2, `clock` 0.0; final `STATUS_FINAL`, `period` 4, `clock` 0.0, `winner`, `situation` null.
`api/espn_live.parse_scoreboard` on the recorded answers: SCHEDULED "8:00 AM ET" → LIVE "Q1 11:47" → LIVE
"Halftime" → FINAL "Final", `neutral_site` true throughout, so 10-4's card needs no new parser. Not seen on the day
(two games, both played): `STATUS_END_PERIOD` (the Mac slept through both quarter breaks; the harness serves it as
"End of 1st" from ESPN's status catalogue, id 22, unverified on a real answer) and POSTPONED / CANCELED / SUSPENDED /
DELAYED, which `espn_live._status` maps by name.

**ESPN's own win probability** (R10-004) is not the model's: on OKC-HOU 2025-10-21 (595 plays) it starts at 0.600
(ours 0.558, the home-court rate) and ends 1.0; the mean absolute gap to ours over the game is 0.051, and the largest
gaps are where ESPN knows the possession and the free throws to come: with 2.3 s left in the second overtime, OKC
down 1 and Gilgeous-Alexander fouled on the shot, ESPN says 0.728 and ours 0.069 (the Methodology card's stated
limit: possession, timeouts and fouls are not inputs). LAL-GSW 2025-10-21: 0.564 at tip, mean gap 0.027, largest
0.108. LAL-SAC 2026-10-08 (preseason, LAL a 10.5-point favourite): 0.490 at tip, so the preseason value is neither the
line nor a strength rating; mean gap 0.025. HOU-DAL 2026-10-09 (the recorded game, 507 plays): 0.490 at tip again,
mean gap 0.042, largest 0.117 (9:17 of the 2nd, DAL down 8, a shooting foul with the free throws to come: ESPN
0.227, ours 0.344). The clock rule matters only at the very end: scoring every play on its own
start instead of the next play's start moves the path by 0.002 on average and by up to 0.17 on OKC-HOU's last seconds
(0.0007 / 0.017 and 0.0005 / 0.014 on the other two), which is why only the latest play is provisional (R10-002).

## 2. The replay-as-live harness

`scripts/live_replay.py` serves a finished game's summary as if the game were on (`api/tests/test_live_replay.py`,
11 tests, ~1.6 s, offline on a made-up game plus OKC-HOU 2025-10-21 from the cache). Its cuts follow the shapes
recorded in § 1 (the status keys, the pre-tip answer, the per-team `leaders` shell).

```
cd scripts && python3 live_replay.py --event 401809243 --speed 20          # 127.0.0.1:8765, a game in ~7 min
cd scripts && python3 live_replay.py --event 401809243 --start-play 400 --paused   # manual control
cd scripts && python3 live_replay.py --event 401809243 --cut 120 > cut.json        # one cut
```

- **Paths:** ESPN's own, so a client only changes its base URL: `/apis/site/v2/sports/basketball/nba/summary?event=`
  and `.../scoreboard?dates=YYYYMMDD` (every replayed game tipping on that US date). `api/espn_live.py` reads it
  unchanged once `SCOREBOARD_URL` / `SUMMARY_URL` point at it (the test does); `scripts/espn_summary.event_rows`
  maps a cut to the same rows as the whole game for every play but the latest (which has no next play yet; the first
  play of the game and of a period keeps hoopR's fixed end and maps at once).
- **Control:** `/__replay/status`; `/__replay/control?event=&set=N | &advance=K | &pause=1 | &resume=1 | &speed=X`;
  `/__replay/fail?mode=none|timeout|500[&seconds=6]` makes every ESPN path time out or answer 500 (the "unreachable
  within 3 s" test of 10-2). In Python: `Replay(summary, speed=, start_play=, paused=)`, `at(n)`, `current()`,
  `scoreboard_event()`, `serve([replay], port=0)` (`base_url`, `summary_url`, `scoreboard_url`, `stop()`).
- **A cut after n plays:** the first n plays and their `winprobability` entries; the header's status, period, clock
  and scores from the n-th play (scheduled before the first, in progress with "7:32 - 3rd" plus `statusPrimary` /
  `statusSecondary` / `displayPeriod` as the live header carries them, halftime after the 2nd period's "End Period",
  end of period after the others, final after the last; `espn_live._status` reads each the way it reads ESPN's); the
  line scores from the plays (equal to ESPN's own at the final); `meta.lastUpdatedAt` = the last play's wallclock;
  before the first play no `plays` key and no `boxscore.players`, as ESPN serves it; each team's `leaders` emptied
  (ESPN fills them live from the box score, which the harness can't cut) and `againstTheSpread` left as the empty
  per-team shell ESPN serves in every state; the pre-game keys (`pickcenter`, `gameInfo`, `format`, `injuries`,
  `news`, `standings`, `seasonseries`, `videos`) as stored. The final cut is the stored summary byte for byte. Two
  things a cut does not reproduce: ESPN's header running ahead of its plays (a cut is consistent), and ESPN's
  after-the-fact corrections (`--cut` of a stored game never deletes a play); 10-2 tests those two on the recorded
  live summaries of § 1 (R10-013, R10-014).
- **Timing:** on the plays' own `wallclock` stamps scaled by `--speed` (every play of the three games read has
  one), so at speed 1 a replay takes as long as the game did, halftime (≈ 15.7 min on LAL-SAC 2026-10-08) and
  quarter breaks (≈ 3.7 min) included; a summary without wallclocks advances one play every 17 s. `--paused` /
  `set(n)` put it under manual control, which is what a test wants.
- **Limit:** the box score cannot be cut (R10-001): before the final every athlete keeps his id and name (the
  mapping names the plays' participants from the box score) with no statistics and `boxscoreSource` says "none",
  where ESPN says "full" from the first play; the live box score is tested on the recorded live summaries of § 1 or
  on a real game (10-5).
- **Where a game comes from** (`summary_source(id)`): the daily update's cache `live_data/<season>/espn_summary/`,
  then `live_data/replay/<id>.json.gz`, else ESPN once (saved there). ESPN's JSON is never committed.

## 3. The data flow (for 10-2 and 10-4)

```
ESPN summary?event=<id>  ──20 s──▶  refresher thread per watched game  ──▶  in-memory cache {espn_id: LiveGame}
ESPN scoreboard?dates=   ──20 s──▶  the day's games (espn_live.scoreboard, TTL 60 s today)        │
                                                                                                   ▼
browser ──25 s──▶ GET /live/game/<id>  ◀── answered from the cache: score, clock, status, feed age, plays (mapped),
                  GET /live/games        WP path (ours + ESPN's), swings, leverage, box score, pre-game odds, _source
```

- **The engine (`api/live_game.py`, step 10-2)** keeps one `LiveGame` per event: the raw summary's `plays`,
  `winprobability`, `boxscore`, `header` and the fetch's time; it maps plays through `espn_summary.end_seconds` /
  `start_seconds` (the clock rule), names participants from the box score (`athlete_names`) and ids them with
  `PlayerMatcher` for the season, scores each play with `wpa_lib.win_prob` (home margin, seconds left), and answers
  the browser with its own compact shape (never the raw summary: R10-007), the `_source` badge saying "ESPN summary
  (live)", the feed's age and the clock note. Every fetch replaces the plays whole and keys them by `id` (R10-014);
  the headline score and clock are the header's, the path's last play is named (`as_of_play`) and the answer says how
  far the header is ahead of it (R10-013). The box score goes through `espn_live.parse_boxscore` (plus-minus as an
  int, DNP reasons), which already reads the live shape (tested on the halftime answer).
- **`GET /live/games?date=`** is `espn_live.scoreboard` plus, per game, whether the engine is watching it and the
  last WP for a small sparkline. **`GET /live/game/{id}`** for a scheduled game answers the tip time, the teams,
  the odds and empty plays; for a final one the full path plus `replay_id` when `pbp_games` has the game (the next
  morning) and "Replay will be ready after the next daily update" until then.
- **The cache's rules:** a failed fetch never replaces the last good state; the answer carries `feed_status`
  ("ok" / "stale" / "unreachable") and `feed_age_s`; stale = the last good fetch is older than 45 s, unreachable =
  older than 90 s or never. A final game is kept for 10 minutes after its last request, then dropped (a page that
  returns re-fetches it once).
- **No database writes** in any live path; 10-2's test reads `pg_stat_user_tables` (`n_tup_ins + n_tup_upd +
  n_tup_del` over every table) before and after a replayed game and expects equality.

**What the page shows on each status** (10-4; the labels `espn_live._status` already produces):

| Status (scoreboard / summary) | Card | Game view |
|---|---|---|
| `STATUS_SCHEDULED` (state `pre`) | tip time in ET and local, "neutral site" when flagged, the pre-game odds when the ledger has them | the same plus "Live from tip-off"; no chart |
| `STATUS_IN_PROGRESS` (`in`) | LIVE dot, "Q3 7:32", score, WP sparkline | the growing WP chart (pre-game line, swings marked, ESPN's line labelled and dashed), the plays feed newest first with swing badges, the box score, "updated N s ago · ESPN's clock" |
| `STATUS_END_PERIOD` (`in`) | "End of 3rd" | as in progress; the chart's period divider |
| `STATUS_HALFTIME` (`in`) | "Halftime" | as in progress; polling continues (the box score and the second-half plays arrive on the same feed) |
| `STATUS_FINAL` (`post`) | "Final" / "Final/OT", winner marked | the whole path; "Replay will be ready after the next daily update", then the Game Replay link once `pbp_games` has the game |
| `STATUS_POSTPONED` / `CANCELED` / `SUSPENDED` / `DELAYED` | the word, no score | the word and the reason ESPN gives (`status.type.description`); a suspended game keeps the path so far |
| feed `stale` | the last state, "updated N s ago" in the warning colour | the same, a banner "ESPN hasn't answered for N s; showing the last state" |
| feed `unreachable` | "ESPN unreachable" within one poll (≤ 3 s after the engine knows) | the banner; polling backs off to 60 s; nothing invented |

## 4. Failure modes

| What | What happens | Who handles it |
|---|---|---|
| ESPN down or slow | the refresher's fetch fails (5 s); the cache keeps the last good state; `feed_status` stale → unreachable; the page says so and backs off | 10-2 (engine), 10-4 (page) |
| a summary shorter than before, or with plays inserted, deleted or rewritten in the middle (measured at halftime on 2026-10-09; CLAUDE.md: ESPN rewrites the log after an overturned challenge) | the engine replaces its plays with the new list every fetch (never appends), keys them by `id`, recomputes the path, and the swings list and the "new plays" feed are a diff by id (R10-014) | 10-2 |
| the header runs ahead of the plays (clock up to 36 s, score up to 6 points; `winprobability` longer than `plays`) | the header is the headline, the plays the chart, `as_of_play` says where the chart ends; ESPN's WP entries without a play are dropped (R10-013) | 10-2 / 10-4 |
| the first minute after tip: the scoreboard says in progress ~15 s before the summary, which flips together with its first play | the card goes LIVE from the scoreboard; the game view shows the pre-game line until the first play arrives (no answer with status in progress and no plays was seen) | 10-2 / 10-4 |
| a player with no id (rookie, two-way, name mismatch) | the play keeps ESPN's name and no link, as the stored rows do; counted in `_source.unmatched` | 10-2 |
| overtime | periods 5+ at 300 s each (`end_seconds`' rule; `wpa_lib.seconds_elapsed`); "OT" / "2OT" labels | 10-2 / 10-4 |
| halftime, end of period | status words; the clock reads 0.0; the feed keeps changing at halftime (corrections), so polling continues | 10-4 |
| postponed, cancelled, suspended, delayed | the scoreboard's status word; no refresher for a game not in progress | 10-2 |
| the NBA Cup final (no `game_scores` link) and neutral sites | the engine doesn't need `game_scores`; the card flags `neutral_site` | 10-4 |
| ESPN's clock lags (made shots a median 14 s late) | said on the page; the path is what the stored game will be; the corrected clock exists only after the daily update | 10-4 |
| the day boundary (games at 05:00-10:00 IST are the previous US date) | `/live/games` defaults to `espn_live.eastern_today()`; the page's "today" is `utils/date.js` `nbaDateIso()` | 10-4 |
| the daily update runs while a game is on (14:00 IST never overlaps a US evening game; a Macao-style morning game can) | the engine never writes; the update only stores finals, so a live game is not touched | nothing to do |

## 5. How to re-measure

`cd scripts && python3 live_spike.py record --event <id> --date <YYYYMMDD> --interval 15 --minutes 200` during a
game, then `python3 live_spike.py analyze live_data/live_spike/<id>`. `record` now runs `caffeinate -i` for its own
lifetime (R10-015: the Mac's idle sleep ate most of the 2026-10-09 game); the lid must stay open. The analysis
counts a poll only when it and the one before came on time, because a wake-up's first poll carries a stale system
clock (one play's wallclock sat 5 min in the future). The next daytime-IST chance is the second Macao game, DAL vs
HOU on 2026-10-11 at 10:00 UTC (15:30 IST), ESPN event 401898400:

```
cd scripts && /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 live_spike.py record --event 401898400 --date 20261011 --interval 15 --minutes 200
```

started by 15:15 IST; its `analyze` should confirm the refresh, delay and header-lead rows of § 1 and add the two
quarter breaks. Step 10-5 reruns it on opening week and writes the median and worst lag between the feed and the
page here.

## 6. Step 10-3: the owner's call

The plain model starts every game at the home-court rate (0.558) because it knows nothing about the teams; ESPN's
own line starts from team strength (0.600 for OKC-HOU) and knows the possession. Step 10-3 would fit a second model
with the same three inputs plus the pre-game expected margin (`game_pregame_odds.exp_margin`, 19,118 games
2010-11 to 2025-26), frozen before 2026-10-20 and scored only forward on 2026-27 games against the plain model and
ESPN's line. What it would and wouldn't do:

- **Would:** make the first quarter's probabilities honest for a lopsided game (a 10-point favourite at 0.56 at tip
  is wrong on its face); give the paper's forward test a second, pre-registered model; be scored on the same
  recorded games as ESPN's line.
- **Wouldn't:** change the paper (no paper table; `paper-inputs` byte-identical), change Game Replay or Clutch WPA
  (they keep the plain model), know the possession (the biggest gaps to ESPN stay), or be on the Live page unless the
  owner picks it after enough games.
- **Cost:** a medium job (one logistic fit on ~2.8 M events, 2020-21 to 2024-25, chosen on 2025-26's 0.6 M; ~10 min),
  frozen in a tagged commit before opening night; the daily scoring is a few seconds.

Recommendation: yes, if there is a CLI slot before 2026-10-20; the forward test is the part a reviewer can't argue
with, and the live page keeps the plain model either way.
