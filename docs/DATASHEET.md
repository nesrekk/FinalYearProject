# NBA Hub: datasheet

What the `nba_analytics` database holds, where each part comes from, what is known to be wrong with it, and what this repository does and does not redistribute. Written for round 5 step 9 (2026-09-30). The counts below were read from the database on that date. `paper/manifest.json` (from `scripts/paper_manifest.py`) gives the exact row count and a content hash for every table. `docs/REPRODUCIBILITY.md` says how to rebuild it.

## Motivation and scope

The database backs an NBA analytics web platform and a conference paper that evaluates its models under one protocol (tune 2020-21 to 2023-24, validate 2024-25, test 2025-26). It is built from public feeds and from third-party files. The repository adds nothing by hand except a few static label lists, which are written out inside the scripts with their sources (All-NBA teams, the NBA 75th Anniversary Team, award winners).

The core of the paper is the 2020-21 to 2025-26 regular seasons. Season-level tables reach back further, to 1946-47 for the Basketball-Reference-derived files.

## Composition

The database holds 167 tables and 19.9M rows. `paper_manifest.py` sorts every table into one of five kinds:

| kind | tables | meaning |
|---|---|---|
| source | 31 | loaded from an outside feed or file (below) |
| derived | 112 | built from other tables by a script in `scripts/` |
| paper | 19 | written by a `scripts/paper_*.py` script for the paper |
| cache | 3 | written by the running app (`league_shot_zones`, `player_shots_cache_status`, `prediction_ledger`) |
| legacy | 2 | loaded before the repository's first commit by a script it doesn't contain (`mvp_seasons`, `mvp_winners`) |

Every table's producing script is listed in `PRODUCERS` in `scripts/paper_manifest.py`. The README's "Database schema" section describes the tables themselves.

**Unit of the core data:**
- A play-by-play event (`pbp_events`, 3.61M rows; 3.40M are ESPN events once the nba_api twin copies are dropped).
- A located field-goal attempt (`player_shots`, 6.32M rows, 1996-97 to 2025-26, including playoff and play-in shots: `game_id` 004… / 005…).
- A team-game with its final score (`game_scores`, 40,696 rows, 2009-10 to 2025-26).
- A player-season (`player_season_stats`, 23,408 rows, 1949-50 to 2025-26).

**People in the data:** professional players, coaches' teams and game officials, identified by name and public league ids. There is no personal data beyond what the league and its broadcasters publish: names, public birth dates from Basketball-Reference's player file, heights, weights, draft and combine measurements, salaries.

## Sources (the source tables)

| source | how it's fetched or loaded | tables | coverage | terms / redistribution |
|---|---|---|---|---|
| ESPN play-by-play (sportsdataverse's hosted ESPN release) | `fetch_pbp_espn.py 2021 2026` (network), then `repair_espn_player_ids.py --apply` | `pbp_events`, `pbp_games` (source `espn`) | 7,232 games 2020-21 to 2025-26: every regular-season game plus the three NBA Cup finals (which the NBA doesn't count in the regular season; season-level builds drop them) | ESPN's data; not redistributed here |
| ESPN public scoreboard | `fetch_game_scores.py`, `fetch_postseason_games.py` (network) | `game_scores`, `postseason_games` | every regular-season game 2009-10 to 2025-26; 1,458 play-in and playoff games | ESPN's data; not redistributed here |
| stats.nba.com via `nba_api` | the `fetch_*` scripts tagged `nbaapi` in `rebuild_all.sh` | `team_game_fatigue`, `game_officials`, `game_team_box`, `lineup_stats`, `player_shot_tracking`, `player_matchups`, `defender_dfg`, `player_hustle`, `player_playtypes`, `player_shot_context`, `draft_combine`, `defense_tracking_stats` (0 rows); `pbp_events` / `pbp_games` source `nba_api` (a 420-game 2024-25 sample, all of them ESPN twins, dropped by `wpa_lib.PBP_DEDUP_WHERE`) | per table, from 2009-10 (the schedule) or later, to 2025-26 (the app's Data Coverage page gives each span) | NBA.com's terms apply. The site times out from the author's machine since 2026-09-26, so these tables stay as they were fetched and can't be refreshed from here |
| NBA season tables (stats.nba.com, saved as CSV) | committed in `nba_data/nba_<season>_season.csv` and `_advanced.csv`, 2009-10 to 2025-26 | `player_season_stats` 2009-10 to 2025-26 | 386-457 players a season through 2024-25 (see Legacy), all 582 in 2025-26 | **committed to this repository** (see Distribution) |
| NBA play-by-play with shot coordinates, bulk files | `load_pbp_shots.py` from `shots_data/pbp_shots_1997_2026/pbp<year>.csv` (gitignored, 2.0 GB) | `player_shots` | 1996-97 to 2025-26 | **Owner to record the download page and its terms** (the repository doesn't say where these files came from). Not redistributed |
| Kaggle "NBA Database, 1947-present" (Basketball-Reference-derived) | `nba_data/kaggle_1947_present/` (gitignored): `load_kaggle_historical_seasons.py`, `load_draft_history_bref.py`, `load_bref_bpm_vorp.py`, and the builds tagged `kaggle` | pre-2010 `player_season_stats`, `player_id_map`, `draft_history`, `draft_pick_outcomes`, published BPM/VORP, team seasons, bios, awards | 1946-47 to 2025-26 | Basketball-Reference's terms forbid automated collection (README Known real gaps); not redistributed |
| Kaggle salary uploads (built from HoopsHype and Basketball-Reference by their uploaders) | `nba_data/salaries/` (gitignored): `load_salaries.py` | `player_salaries`, `league_minimum_salary` | 2005-06 to 2019-20 and 2024-25 used (others excluded as inflation-adjusted or incomplete) | no stated licence; not redistributed |
| Kaggle "College Basketball Dataset" (Bart Torvik's T-Rank team ratings) | `nba_data/college_teams/` (gitignored): `load_college_teams.py` | `college_team_seasons` | 2012-13 to 2025-26 | not redistributed |
| CollegeBasketballData.com API | `fetch_cbb_games.py`, `fetch_college_stats.py` (needs `CBBD_API_KEY`) | `cbb_games`, `college_player_season_stats` | games 2012-13 to 2025-26; player stats 2013-14 to 2024-25 | the API's terms; not redistributed |
| Static lists written out in scripts, each with its source in the docstring | `fetch_all_nba_teams.py`, `fetch_dpoy_roy_stats.py`, `load_nba75_team.py`, `build_award_winners_table.py` | `all_nba_seasons`, `dpoy_seasons`, `roy_seasons`, `nba75_team`, `award_winners` | 2009-10 to 2024-25 awards; the 76 players of the 75th Anniversary Team | facts, typed from public announcements |

Live-only sources the platform calls at request time (the Odds API, RSS news, NBA CDN headshots, live stats.nba.com calls) aren't stored for analysis and aren't used by the paper.

## Legacy: what no script in this repository can rebuild

- **`player_season_stats`, 2009-10 to 2024-25 base rows.** They were loaded before the first commit by a script that isn't in the repository. The files they came from are committed (`nba_data/nba_<season>_season.csv`). For each of those 16 seasons the stored rows are exactly the CSV's players with games × minutes per game ≥ 200, with the same games and points; `api/tests/test_reproducibility.py` checks this every run.
  - Later scripts in the load and derived stages add columns to these rows: shooting counts, personal fouls, the BPM reproduction, Basketball-Reference's published BPM/VORP, and impact scores.
  - Pre-2010 rows come from `load_kaggle_historical_seasons.py`; 2025-26 rows come from `load_2025_26_into_db.py`, all rows, no minutes floor.
- **`mvp_seasons` (15 rows) and `mvp_winners` (16 rows).** These are the MVP label tables, also loaded before the first commit. `nba_data/mvp_seasons.csv` holds the first.
- **Anything stats.nba.com served** can't be re-fetched while the site is unreachable (see Sources).

## Collection and processing

- **One lineup parser.** `scripts/pbp_lineups.py` replays every ESPN game once. It drives the player game lines, the five-man stints, the play finder and the other play-by-play tables.
- **Reconciliation.** Each game is checked against its real final score (`game_scores`), its game length and its team totals. Games that don't reconcile are stored but flagged.
- **Matching the two feeds.** ESPN attempts are matched to NBA shot-chart shots by order within (game, shooter, period), never by clock: ESPN's clock trails the chart's by a median 4 s (26 s at the 99th percentile). Missed shots take the chart's two-or-three call.
- **Name matching.** Names are matched between Basketball-Reference and NBA ids by `scripts/bref_nba_ids.py`. 129 pre-2010 players are left unmatched on purpose.
- **Order and seeds.** Every table built from a fit or a shuffle reads its input in a fixed order and seeds its random draws (`docs/REPRODUCIBILITY.md` lists the seeds).

## Known errors

The paper's data-quality audit (`scripts/paper_data_audit.py`, Table *audit* in the paper, tables `paper_data_audit` and `paper_data_audit_classes`) re-measures 17 error classes from the database with the check that finds each one:

- **ESPN play-by-play (11 classes):** twin copies of nba_api games, the NBA Cup finals, same-surname wrong-player tags (fixed), single events whose tag and text name different players, players with no id, team-less substitutions, stale score fields, a last score that isn't the final, missed threes worded as twos, clock errors, and games that don't reconcile.
- **NBA shot chart (3 classes):** four missing 2025-26 games and a lower match rate that season, `shot_distance` = 0 on 11-17% of threes a season, and unlocated shots at (0, 0) before 2010-11.
- **Season tables (3 classes):** `team_game_fatigue.plus_minus` isn't the final margin, a few player-seasons carry a wrong team, and two age conventions are mixed.

The README's "Known real gaps" section describes each class and every other known limit of the platform. The audit's numbers are the current ones; the README's are dated.

## Distribution

**Committed to this repository:**
- The code.
- The trained model files (`models/*.pkl`, `scripts/*.pkl`).
- The static label lists.
- The stats.nba.com season CSVs in `nba_data/` (`nba_<season>_season.csv` / `_advanced.csv`, `mvp_seasons.csv`, `player_seasons_master.csv`, the award training CSVs).
- Two shot-chart JSON files for one player (`shots_data/stephen_curry_shots.json`, `frontend/public/shot_data/stephen_curry_shots.json`).

The owner should decide whether the NBA files stay in a public or review artifact.

**Not redistributed, gitignored:**
- The Basketball-Reference-derived Kaggle export.
- The salary files.
- The Torvik college ratings.
- The bulk shot files (`shots_data/pbp_shots_1997_2026/`, and `shots_data/kaggle_shot_data_2000_2022/`, which nothing reads).

**Not redistributed, never written to disk as files:** the ESPN play-by-play, the scoreboards and the CollegeBasketballData.com data. They exist only in the database.

**What a reader can re-fetch or re-download:**
- Re-fetch with the provided scripts: the ESPN play-by-play and scoreboards.
- Re-fetch while stats.nba.com is reachable: the stats.nba.com tables.
- Download themselves into the gitignored folders: the Kaggle, salary and college files. The README's "Data sources" section names them.
- Can't re-create exactly (see Legacy): the legacy rows.

## Maintenance

After any rebuild:
1. Run `scripts/rebuild_all.sh paper-inputs`. It regenerates the manifest, `numbers.tex`, the figures and `SHA256SUMS`.
2. Check the manifest digest the paper prints.

A new pipeline must add its tables to `PRODUCERS` in `scripts/paper_manifest.py` and its script to `scripts/rebuild_all.sh`; the reproducibility test fails otherwise.

The cloud mirror (Layerbase) is synced by hand with the owner's OK. To compare it with the local database, write its manifest to a separate folder and diff the two `manifest.tsv` files (`DB_TARGET=layerbase python3 scripts/paper_manifest.py --out <dir>`).
