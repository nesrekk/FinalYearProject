# NBA Hub

A full-stack NBA analytics platform built as a final-year project: real historical data (2009-10 through the current season), trained and backtested prediction models, era-normalized player comparison engines, a live betting-market cross-reference, real play-by-play win-probability modeling, and a suite of stat-driven daily games — all built on top of one PostgreSQL database and served through three FastAPI microservices to a React dashboard.

The project's guiding rule, followed throughout: **never fabricate data**. Where a real number exists, it's used and its source is traceable to a real endpoint or a real database column. Where the project doesn't have real data for something (player height/wingspan, a trade-history dataset, a real referee-bias claim), that gap is disclosed in the UI rather than invented — several features explicitly say what they *aren't*, and small real samples get an explicit warning instead of being presented as comprehensive.

![Dashboard](docs/screenshots/dashboard.png)

---

## For an AI assistant picking this project up

Read this whole section before touching anything. It exists so you don't need the owner to re-explain context that's already been established over many previous sessions.

**Environment gotchas — get these wrong and everything else breaks:**
- Plain `python3` on this machine resolves to an Anaconda install missing this project's dependencies. Always use `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3` explicitly, for every script run and every `pip install`.
- Each of the three API files has its own `if __name__ == "__main__"` uvicorn fallback port, but **always pass `--port` explicitly anyway** — don't rely on the fallback, and don't assume the fallback is correct (it has been wrong before and gets fixed reactively; the ports the frontend actually calls are the ones in `frontend/src/services/api.js` — currently 8000/mvp, 8001/similarity, 8002/impact).
- DB credentials live in `api/.env` (gitignored) as `DB_HOST`/`DB_PORT`/`DB_USER`/`DB_PASSWORD`/`DB_NAME`, read through `api/db_config.py` and `scripts/db_config.py` (two separate small modules, each resolving `api/.env` by an absolute path so they work regardless of current working directory). Every script and service does `from db_config import DB_CONFIG`. **Never hardcode the password back into a file** — it used to be hardcoded everywhere and was deliberately pulled out; the old password is still in git history from before that cleanup, so treat it as not-really-secret and don't reuse it for anything real.
- Postgres DB name: `nba_analytics`.
- Scripts are normally run as `cd scripts && python3 whatever.py` (their docstrings say so), but don't assume — check the top of the script; some things (like the test suite) are written to work from any cwd.
- `nba_api` hits the real stats.nba.com over the network. It's rate-limit-sensitive: existing fetch scripts use `time.sleep()` between calls (0.4–1.0s for reliable endpoints like `PlayByPlayV3`/`LeagueGameFinder`, more conservative 5–30s backoff for the flakier `shotchartdetail` endpoint) plus retry-with-backoff. Match the existing pattern in whichever script is closest to what you're building; don't invent a new rate-limiting scheme.
- Season encoding: `player_season_stats.season` and most other tables use the **end year as an int** (e.g. `2026` means the 2025-26 season). Convert to the NBA's own label format with `f"{season - 1}-{str(season)[-2:]}"` when calling `nba_api` — this exact expression appears throughout the codebase, copy it rather than reinventing it. `pbp_games`/`pbp_events` now span real seasons 2021-2026 (previously just one sampled season, see C8 below) and agree with `player_season_stats`'s latest season — but still query each table's own `MAX(season)` rather than assuming any two tables agree on "the latest season" (this exact bug was found and fixed once already in `/games/wp-replay/list`, back when they didn't).

**The "never fabricate data" rule in practice — how to apply it to new work:**
- A composite/index metric (Heliocentricity Index, Impact Score) is fine as a simple, fully-disclosed equal-weighted average of real percentile ranks. It is never presented as a trained/validated model unless it actually is one.
- A predictive/simulated feature (e.g. "what would this trade do to team chemistry") needs either a real trained model with real validation metrics, or it gets scoped down to something that only uses real data that actually happened (see Lineup Chemistry below — the honest substitute for a "Trade Chemistry Simulator" that would have needed an invented usage-redistribution formula).
- Every "what if" / counterfactual feature (see Game Replay) must say in its own API response and its own UI copy that it's a counterfactual, not a prediction of what actually would have happened.
- Small real samples get a disclosed warning/cutoff (e.g. Playoff Drop-off Forecaster's `small_sample_warning` under 10 games, Lineup Chemistry's `min_minutes` cutoff with `lineups_qualified`/`lineups_total` both returned) rather than being silently included or silently hidden.
- If a feature needs data the project doesn't have (a real API key, a real dataset), say so to the owner directly and ask, rather than fabricating placeholder data or silently declining. Registering third-party accounts / submitting signup forms with the owner's info is an action that needs the owner's explicit go-ahead, not something to do autonomously.

**Verification discipline expected on every feature before it's considered done:**
1. Test the actual data source (nba_api endpoint, SQL query, whatever) directly with real inputs *before* writing application code around it — this has caught real bugs early several times (empty-string score fields that needed forward-filling, a `CalibratedClassifierCV` API quirk, non-unique `action_number` values).
2. Run the backend, `curl` the new endpoint(s), confirm real data and the expected JSON shape.
3. Start the frontend dev server, actually click through the new UI in the browser pane (`preview_start` with name `nba-frontend`), check the browser console for errors, screenshot if it's visual.
4. Run the smoke tests: `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests` (works from repo root or `api/`). Add a test for any new endpoint.
5. Do a real "sniff test": does a leaderboard/ranking match genuine basketball knowledge? (e.g. DeMar DeRozan's real reputation as a clutch scorer landing near the top of the Clutch WPA leaderboard was treated as evidence the whole WPA pipeline was working.)
6. `git status --short` and a `grep` for any literal secret values across changed files, every time, before staging — `api/.env` must never get committed (it's gitignored; verify with `git check-ignore -v api/.env` if in doubt).

**Commit style:** one commit per feature, detailed message explaining what's real about it and what was verified, ending with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` (or whichever model actually did the work — check the current session's own attribution instructions rather than assuming). Only commit when the owner asks; `git status` and read the diff before staging.

**Current status as of this README's last update:** everything in "What's in it" below is built, verified, and pushed. The project is being extended feature-by-feature from a longer roadmap — see **Roadmap / what's next** near the bottom for the exact remaining list in priority order, including which items are already partly done and any scope notes discovered while building adjacent features (e.g. Lineup Chemistry already covers part of what a later "real lineups + on/off" item was going to build).

---

## What's in it

**Core stats & browsing**
- Live scores, standings, news, team comparison, full player-stats browser with sortable/filterable tables
- Shot charts (real shot-location data, live-fetched and cached per player/season)
- Stat leaders, Draft Value Guide (career value by draft slot), Rookie Class Tracker (this season's rookies ranked by real ROY probability). Stat Leaders also includes a real **Hustle** stat group (deflections, contested shots, screen assists, loose balls, charges drawn, box outs — NBA's own real hustle-stat tracking, `scripts/fetch_hustle_stats.py`) alongside the traditional box-score stats.
- **Hall of Fame** — real all-time career leaders, single-season records, and career-longevity leaders, computed live from `player_season_stats`' full 1950-2026 coverage (`api/routers/hall_of_fame.py`). Named for "statistical greatness," not literal induction — this project has no real Naismith Hall of Fame membership dataset from any source it uses, and says so directly in the page's own `InfoTooltip` rather than implying verified induction data it doesn't have.

**Player Comparison**
- Head-to-head statistical profiles with percentile ranks against a real qualified-player pool
- Any player's detail view (opened from Players, Stat Leaders, etc.) shows a real **Play-Type Profile**: real share of offensive possessions by real play type (NBA Synergy tracking, `scripts/fetch_playtypes.py`) with real points-per-possession for each, color-coded by percentile
- Fit Analysis: **Real Pair Synergy** — a real ridge regression (`scripts/train_pair_synergy.py`), trained on 23,381 real 2-man lineup pairs across all 17 real seasons and validated with real season-grouped cross-validation (real R² = 0.0148, disclosed honestly even though it's low — a real finding that pair chemistry isn't well predicted by box-score features alone, not a bug). Predicts a pair's expected real net rating above what their two individual real net ratings alone would suggest, and shows the pair's real observed net rating live-fetched from the NBA's own data when the two players have actually shared the floor. The older percentile-threshold heuristic (usage/spacing/role-overlap flags) is kept underneath as a simpler, unvalidated fallback.

![Player Comparison](docs/screenshots/player_comparison.png)

**Analytics** (grouped into four tab groups — Models, Player Analysis, Teams & Markets, Prospects — with the active tab kept in sync with the URL hash, e.g. `#wpa`, `#lineups`, `#replay`, so any tab is directly linkable)

*Models*
- Awards Race, Impact Rankings, Model Validation — ROC curves, SHAP feature breakdowns, leave-one-season-out backtests against real historical winners for MVP/DPOY/ROY/All-NBA, **plus a Clutch WPA calibration tab**: real Brier score, log loss, and a 10-bucket reliability curve (predicted probability vs. real observed outcome) for the win-probability model, computed on real held-out games and shown for both "all events" and "real clutch time only" scopes, with an explicit note that the two scopes' aggregate scores aren't directly comparable (clutch predictions cluster near 50/50, which is a harder problem, not worse calibration — the reliability points are the real evidence).
- **Prediction Ledger** — real live model predictions (MVP/DPOY/ROY/All-NBA) logged with a real timestamp (`scripts/snapshot_predictions.py`), graded with a real Brier score once a season actually finishes and gets a real recorded winner/selection (`scripts/resolve_predictions.py`). Shows each award's current real favorites' probability trajectory over real logged snapshots, kept strictly separate from the honest historical backtest tables above — this is a live, growing record of what the model actually said and when, not a re-run.

*Player Analysis*
- Season Similarity & Player Archetypes — cosine-similarity and K-Means clustering, both **era-normalized**: every feature is z-scored within its own season before comparison. Player Archetypes includes a **League Evolution** panel: a real stacked-area chart of each archetype's share of the qualified pool across all 17 seasons, plus minutes-weighted real league-average trend lines for 3PA rate, True Shooting %, and a disclosed real pace proxy — cleanly reproduces the real, well-documented 3-point revolution and pace revival as a sanity check.
- **Offensive Style** (`scripts/cluster_playtypes.py`) — a second, separate K-Means clustering of the qualified pool by real offensive play-type frequency mix (NBA Synergy tracking: real isolation, pick-and-roll, spot-up, post-up, transition, and roll-man possession shares, era-normalized the same way as the stat archetypes), answering "how does this player's offense get generated" rather than "what does their production look like." Real silhouette score (0.21) is disclosed as lower than the stat archetypes' — play-type mixes genuinely overlap more than production profiles do. Sits right below Player Archetypes with its own scatter plot and table.
- Radar Compare, Trend Analysis, Career Trajectory Forecaster (CARMELO-style: real closest-age comps, weighted by similarity, overlaid against real future data when it exists)

![Career Trajectory Forecaster](docs/screenshots/trajectory.png)

- **Heliocentricity Index** — real touch/possession-share data (`nba_api`'s `LeagueDashPtStats`) combined with real usage%/assist% into a disclosed, equal-weighted percentile-rank composite (modeled on the Impact Score's own precedent). Explicitly not a predictive "slider" — just an honest index of who the real offense actually runs through.
- **Clutch-Time Win Probability Added (WPA) Tracker** — a real trained Logistic Regression win-probability model (same library/approach as the MVP/DPOY/ROY models), fit on real play-by-play from two real sources (`scripts/fetch_play_by_play.py` for nba_api, `scripts/fetch_pbp_espn.py` for real full-season ESPN coverage via sportsdataverse → `train_wpa_model.py` → `compute_wpa.py`), isotonic-calibrated, held out by *game* (not by row) to avoid leakage — 7,652 real games across seasons 2021-2026, real held-out ROC-AUC 0.835 (0.859 clutch-time-only). WPA per play = the model's real win-probability output after the play minus before it, attributed to whichever player made the play, summed over the NBA's own real "clutch time" definition (final 5 min of regulation/OT, score within 5). The real sample size is always shown, not implied to be the full historical record.
- **Matchup Finder ("Kryptonite" defender finder)** — real player-vs-player defensive matchup data (`nba_api`'s `LeagueSeasonMatchups`, `scripts/fetch_matchups.py`: real partial possessions matched up, real FG% allowed, 571,608 real rows 2017-18 through 2025-26). Search any player as either the scorer (which real defenders have actually held them to the lowest FG%, and which have they torched) or the defender (which real offensive players do they actually shut down, and which torch them), reversible either direction. Disclosed sample-size guardrail: pairs under 20 real partial possessions are greyed out and marked "small sample" rather than silently ranked alongside reliable ones — a single defended shot is either 0% or 100%.

*Teams & Markets*
- Vegas vs. Machine — real live championship-winner odds across multiple real sportsbooks, de-vigged with Shin's method, with per-book breakdown and a consensus-disagreement signal (coefficient of variation across books).

![Vegas Scanner](docs/screenshots/vegas_scanner.png)

- Playoff Drop-off Forecaster — real regular-season vs. real playoff advanced stats for the same player-season, with a small-sample warning under 10 real playoff games.
- **Lineup Chemistry** — real 5-man lineup combinations that have actually shared the floor this season (`nba_api`'s `LeagueDashLineups`: real Off/Def/Net Rating, minutes, AST%, TS%), Best/Worst toggle, with a disclosed minimum-shared-minutes cutoff (`lineups_qualified` of `lineups_total` always shown) since tiny real samples produce real but extremely noisy net ratings. This is the honest substitute for a "Trade Chemistry Simulator" — instead of inventing a usage-redistribution formula for a lineup that's never played together, it shows how real lineups that *have* actually played together have actually performed.
- **Game Win-Probability Replay** — replays one real game's entire real play-by-play through the same real WPA model, charting home win probability over the whole game (top 5 real plays by |WPA| marked, hover for details), plus a clickable "what if this real missed shot had gone in" counterfactual (dashed line) that recomputes the rest of the game's win probability assuming the shot's points landed and every later real play happened exactly as it did — clearly labeled as a counterfactual, not a re-simulation.
- **With vs. Without a Star** — pick a real team, season, and player: real record, win%, and average point differential split by whether that real player actually played in each real game that season, live-fetched from the NBA's own full-season game logs. Labeled explicitly as a real association, not a causal claim (other absences in the same games aren't controlled for) — a real sniff test on the 2023-24 Sixers/Embiid split (79.5% win rate with him vs. 37.2% without, across 39 and 43 real games) reproduces that season's well-documented real storyline exactly.
- **Schedule Fatigue** — real rest days, real back-to-backs, real travel miles (haversine between each real consecutive game's real arena location, `scripts/arenas.py`), and real time zones crossed for every real team-game 2010-2026 (`scripts/build_schedule_fatigue.py`, 40,696 real rows). A real win%-by-rest-bucket study (44.4% on 0 real rest days vs. 51-53% on 1-3, a clean real reproduction of the NBA's well-documented back-to-back fatigue effect) and a real team schedule-difficulty ranking by total real travel miles. Live Scores also gets real "B2B" / rest-disadvantage tags per team, computed from this same real stored schedule.
- **Referee Tendencies** — real officials (`BoxScoreSummaryV2`) matched to the real fouls/FTA/pace each of their real games produced (`LeagueGameFinder`), season-adjusted against the real league average for those same real seasons, with a real 95% confidence interval on the difference (`scripts/fetch_referee_officials.py`, `scripts/build_referee_tendencies.py`). Deliberately descriptive, not diagnostic: it compares real games-called totals to a real baseline and says nothing about cause — crew assignment, opponent style, and era are real, unmeasured confounders — and officials under 25 real games worked are explicitly flagged as a small sample rather than ranked alongside officials with a real full season on file. `fetch_referee_officials.py` is a real one-call-per-game pipeline (nba_api has no bulk officials endpoint) — resumable and safe to re-run; it skips every game_id it has already attempted, so real coverage grows each time it's run rather than needing to finish in one sitting.

*Prospects*
- Draft Prospect Comp Finder — ~105,000 real D1 college player-seasons (CollegeBasketballData.com), era-normalized, matched by name to real NBA rookie outcomes wherever a real match exists (~55% of NBA rookies since 2015 — international/G-League/draft-and-stash players never appear in US college data). Optional "include measurements" toggle adds real wingspan-minus-height and real standing reach (NBA Draft Combine, `scripts/fetch_draft_combine.py`, 1133 real player-measurements 2010-2026) to the comp vector, narrowing to real combine attendees only. A **"Does Length Matter?"** card real-correlates wingspan-minus-height against real career-average DBPM and real steals+blocks per 36 (n=565, Pearson r shown, scatter plot) — a real, weak-to-moderate positive correlation, not a causal claim. Real body measurements also show in the Player Comparison profile when a player has them on file.

![Draft Prospect Comp Finder](docs/screenshots/draft_prospects.png)

**Games** (daily, stateless, all built on the same real qualified-player pool)
- Guess the Player, Blurred Player (real headshot proxied through the backend so the URL can't just be read off the page), Higher or Lower, Trivia (5 daily questions, decoys always other real players)
- **Guess the Game** — a real completed game's real win-probability curve (from the same WPA model as Clutch WPA/Game Replay), no teams or date shown; up to 3 guesses at either team, each wrong guess revealing the next real clue (season → final margin → one team)

![Games hub](docs/screenshots/games_hub.png)

**Dashboard**
- Awards Race cards show real seed and "won it before" streak badges — informational context from real standings/award history, never folded into the model's own probability number

---

## Architecture

```
                       ┌─────────────────────┐
                       │   React / Vite SPA   │  localhost:5173
                       └──────────┬───────────┘
                                  │ axios
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   mvp_api.py     │    │ similarity_api.py│    │  impact_api.py    │
│  localhost:8000  │    │  localhost:8001  │    │  localhost:8002   │
│  awards models,  │    │  similarity,      │    │  everything else: │
│  backtests, SHAP,│    │  clustering,      │    │  games, dashboard,│
│  WPA calibration │    │  trajectory       │    │  odds, live data, │
│                  │    │                   │    │  WPA/lineups/replay│
└────────┬─────────┘    └────────┬──────────┘    └────────┬─────────┘
         └────────────────────────┼────────────────────────┘
                                  ▼
                     ┌─────────────────────────┐
                     │  PostgreSQL: nba_analytics│
                     └─────────────────────────┘
```

Each service is independent (its own CORS setup, its own DB connection pool built from the shared `db_config.py`) and can be restarted without touching the others. The frontend's `services/api.js` hardcodes which base URL each feature calls. `scripts/wpa_lib.py` is a small shared module (model loading, win-probability scoring, the clutch-time constants, a game-clock-elapsed conversion) imported by both `scripts/compute_wpa.py` and `api/impact_api.py`'s Game Replay endpoints, so there's exactly one real implementation of the win-probability math rather than two that could drift apart.

## Database schema (all tables in `nba_analytics`)

| Table | What it holds |
|---|---|
| `player_season_stats` | Core real per-player-season stat line, every season 2009-10–present |
| `all_nba_seasons`, `mvp_seasons`, `dpoy_seasons`, `roy_seasons`, `award_winners` | Real historical award ballots/winners |
| `model_backtest_summary`, `model_backtest_seasons`, `all_nba_backtest_summary`, `all_nba_backtest_seasons`, `win_model_backtest` | Leave-one-season-out backtest results (honest, historical — not live predictions) |
| `shap_explanations` | Cached SHAP breakdowns for the Random Forest award models |
| `season_similarity`, `career_similarity` | Precomputed cosine-similarity pairs |
| `player_clusters`, `cluster_archetypes` | K-Means archetype assignments and labels |
| `draft_history` | Real draft-pick outcomes |
| `defense_tracking_stats` | Real defensive tracking stats (DPOY features) |
| `player_shots`, `player_shots_cache_status`, `league_shot_zones` | Live-fetched, cached shot-location data |
| `college_player_season_stats` | ~105k real D1 college player-seasons (CollegeBasketballData.com) |
| `pbp_games`, `pbp_events` | Real play-by-play, 7,652 real games across seasons 2021-2026 — a real sampled subset from `fetch_play_by_play.py` (nba_api, `source='nba_api'`) plus real full-season coverage from `fetch_pbp_espn.py` (ESPN via sportsdataverse, `source='espn'`); both real sources verified to share the identical seconds-remaining/score-margin convention before being combined |
| `player_wpa_totals` | Aggregated real Win Probability Added per player (`compute_wpa.py` output) |
| `wpa_model_validation` | Real calibration validation (Brier/log-loss/reliability bins) for the WPA model, two scopes: all events and clutch-only |
| `prediction_ledger` | Real live-logged MVP/DPOY/ROY/All-NBA predictions, timestamped, graded once real outcomes exist (`snapshot_predictions.py` / `resolve_predictions.py`) |
| `draft_combine` | Real NBA Draft Combine measurements, 2010-2026 (`fetch_draft_combine.py`) — NULL for any test a player skipped, no row at all for players who never attended |
| `pair_synergy_validation` | Real season-grouped cross-validated R² for the 2-man pair-synergy ridge model (`train_pair_synergy.py`) |
| `team_game_fatigue` | Real per-team-game rest/B2B/travel-miles/timezone data, 2010-2026, 40,696 rows (`build_schedule_fatigue.py`) |
| `player_playtypes` | Real per-player-season play-type frequency/PPP/percentile, NBA Synergy tracking, 2013-2026, 43,681 rows (`fetch_playtypes.py`) — mid-season-traded players poss-weighted-averaged across teams |
| `player_hustle` | Real per-player-season hustle stats (deflections, contested shots, screen assists, loose balls, charges, box outs), 2016-2026, 5,602 rows (`fetch_hustle_stats.py`) |
| `playtype_clusters`, `playtype_cluster_archetypes` | K-Means "Offensive Style" cluster assignments and labels, from real play-type frequency mix (`cluster_playtypes.py`) |
| `player_matchups` | Real player-vs-player defensive matchup data (partial possessions, FG% allowed, etc.), 2017-18–2025-26, 571,608 rows (`fetch_matchups.py`) — rows below 5 real partial possessions dropped at fetch time as noise |
| `game_team_box` | Real per-team-game FTA/PF/FGA/OREB/TOV plus a derived possessions estimate, seasons 2021-2026 (`fetch_referee_officials.py`, bulk via `LeagueGameFinder`) |
| `game_officials`, `game_officials_fetch_log` | Real official assignments per real game (`BoxScoreSummaryV2`, one real call per game) and a fetch-attempt log so re-runs skip games already tried |
| `referee_tendencies` | Real per-official fouls/FTA/pace averages vs. the real season league average, with a real 95% CI and small-n warning (`build_referee_tendencies.py`) |

## Tech stack

- **Backend:** Python, FastAPI, psycopg2, pandas, scikit-learn, scipy, `nba_api`, requests, python-dotenv, pytest + httpx (smoke tests)
- **Database:** PostgreSQL (`nba_analytics`)
- **Frontend:** React 19, Vite, Framer Motion, axios — no charting library; every chart (radar profiles, ROC curves, reliability diagrams, win-probability replay, trajectory cones, archetype scatter plots) is a hand-built SVG component
- **Models:** Logistic Regression (MVP/DPOY/ROY, ROC/SHAP-validated; win-probability model, isotonic-calibrated), K-Means clustering (archetypes), cosine similarity (season/career comps)

## Running it locally

### Prerequisites
- PostgreSQL running locally with a `nba_analytics` database already populated (see `scripts/` for the fetch/build pipeline)
- Node.js for the frontend

### Python interpreter — read this first
```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pip install fastapi uvicorn psycopg2-binary pandas numpy scikit-learn scipy nba_api requests python-dotenv pytest httpx certifi sportsdataverse rapidfuzz
```
- **macOS only, one-time:** `sportsdataverse` transitively imports `xgboost`, which needs the OpenMP runtime: `brew install libomp`. Without it, `import sportsdataverse` (or anything under `sportsdataverse.nba`) fails with `XGBoostError: Library not loaded: @rpath/libomp.dylib` even though nothing in this project actually calls xgboost.
- **macOS python.org/Python.framework builds only, one-time:** if any live HTTPS fetch (`sportsdataverse`, `nba_api`, the odds/CBBD APIs) fails with `SSL: CERTIFICATE_VERIFY_FAILED`, run `/Applications/Python\ 3.14/Install\ Certificates.command` — the framework build doesn't wire itself up to the system/certifi trust store by default.

### `.env` setup
Create `api/.env` (gitignored):
```bash
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=your_local_postgres_password
DB_NAME=nba_analytics

# Optional — see "Optional live data" below
ODDS_API_KEY=your_odds_api_key_here
CBBD_API_KEY=your_cbbd_api_key_here
```

### Start the three backend services
Always pass `--port` explicitly:
```bash
cd api
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn mvp_api:app        --port 8000 --reload
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn similarity_api:app --port 8001 --reload
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn impact_api:app     --port 8002 --reload
```

### Optional: live championship odds and college data
Two Analytics tabs need their own free API keys. Without them, every other feature still works — those two tabs just report the key is missing.
- **ODDS_API_KEY** (Vegas Scanner) — [The Odds API](https://the-odds-api.com/), free tier ~500 requests/month.
- **CBBD_API_KEY** (Draft Prospects) — [CollegeBasketballData.com](https://collegebasketballdata.com/key), free, one-field email signup. Only needed to re-run `scripts/fetch_college_stats.py`; the Draft Prospects tab itself just reads from Postgres once that script has run.

### Start the frontend
```bash
cd frontend
npm install
npm run dev
```
Opens at `http://localhost:5173`.

### Run the backend smoke tests
```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests -v
```
Real integration tests (FastAPI `TestClient` against the live local DB, no mocks) — skipped with a clear reason if Postgres isn't reachable. Add a test here for every new endpoint.

## Project structure

```
api/
  .env                  DB creds + API keys — gitignored, never committed
  db_config.py           Shared DB_CONFIG dict, read from api/.env
  mvp_api.py             Awards models, backtests, SHAP, WPA calibration (:8000)
  similarity_api.py      Similarity, clustering, trajectory (:8001)
  impact_api.py          Everything else — games, dashboard, odds, WPA/lineups/replay (:8002).
                          Thin app entrypoint only (FastAPI app + CORS + mounts every router) —
                          route bodies live in routers/, shared setup in impact_core.py (A6).
  impact_core.py          DB pool, loaded models, every fetch_*/find_player/get_db-style helper
                          and module-level constant shared across impact_api's routers.
  routers/                One file per impact_api feature (e.g. referee_tendencies.py,
                          matchup_finder.py) — each just its @router.get route(s), moved verbatim
                          out of the old single impact_api.py so paths stayed byte-for-byte
                          identical; verified via the full smoke suite plus a live sweep of all
                          59 real endpoints after the split (zero 5xx, only pre-existing gaps).
  shots_lib.py           Shared shot-chart fetch/cache logic
  tests/test_smoke.py    Real integration smoke tests, one per shipped endpoint
scripts/
  db_config.py           Shared DB_CONFIG dict for scripts/, resolves api/.env by absolute path
  wpa_lib.py              Shared WPA model loading/scoring, used by compute_wpa.py and impact_api.py
  fetch_*.py, build_*.py, train_*.py, compute_*.py, precompute_*.py, upgrade_*.py
                          The data pipeline: fetch from nba_api/CBBD → build/write Postgres tables →
                          train/backtest models → precompute derived tables. Each script's own
                          docstring says what real data it uses and how to run it.
  *.pkl                   Saved trained model artifacts (committed — matches project convention)
frontend/
  src/
    components/
      pages/              Top-level routed pages (Dashboard, Player Comparison, Games hub, ...)
      common/              Shared components (PlayerHeadshot, TeamLogo, InfoTooltip, Icon, ...)
      *.jsx                Analytics sub-tab components (one per feature)
      pages/AnalyticsSection.jsx   Tab-group registry — add new Analytics tabs here (TAB_GROUPS array)
    services/api.js        Every backend call, one file, grouped by feature, one fetch function each
    styles/dashboard.css   Single stylesheet for the whole app
docs/screenshots/          README images (not all newer features have one yet)
PROJECT_DETAILS.md         Deep-dive on the ML methodology (predates several features in this README —
                          the modeling-methodology sections are still accurate; treat the feature list
                          in THIS file as the current source of truth)
```

## Established conventions & patterns (follow these, don't reinvent)

- **Era-normalization**: any feature comparing players across seasons z-scores each stat within its own season's qualified pool first (`cluster_players.py`, the similarity engines, the Draft Prospect bridge pool). Never pool raw stat values across eras.
- **Headshots/logos**: `<PlayerHeadshot playerId={} playerName={} size={} />` and `<TeamLogo abbreviation={} size={} />` (both in `components/common/`) — they fail gracefully to initials/a fallback, never a broken image icon.
- **Tooltips**: `<InfoTooltip label="..." title="...">` for any "how does this work" disclosure — the project's standard place to put honest methodology explanations rather than burying them in a modal or leaving them unexplained.
- **Source badges**: every main Analytics endpoint's JSON includes a `_source` object (`api/source_badge.py`'s `make_source(tables, upstream_api, as_of=None, live=False)`) disclosing which real Postgres table(s) backed the response, which real upstream API/dataset those tables came from, and — for the handful of endpoints that live-fetch rather than read a precomputed table — that it's live. The frontend renders it with `<SourceBadge source={data?._source} />` (`components/common/SourceBadge.jsx`) next to the section's `InfoTooltip`; it renders nothing if `_source` is absent, so it's safe to add to a new endpoint at any time without touching the frontend contract.
- **Analytics tabs**: registered in `TAB_GROUPS` in `frontend/src/components/pages/AnalyticsSection.jsx`, grouped into Models / Player Analysis / Teams & Markets / Prospects. The active tab syncs to `location.hash` via `history.replaceState` (no back-button spam) so any tab is directly linkable, e.g. `http://localhost:5173/#wpa`.
- **Live external fetches** in the API layer are cached in an in-process `_CACHE` dict with a TTL (see `impact_api.py`'s `_CACHE`/`_CACHE_TTL_SECONDS`) rather than hitting `nba_api`/external APIs on every request.
- **New feature checklist**: pipeline script in `scripts/` (if new data needed) → Postgres table → endpoint in the right API file (awards/validation → `mvp_api.py`; similarity/clustering → `similarity_api.py`; everything else → a new or existing file under `api/routers/`, sharing helpers from `api/impact_core.py`), with a `_source` object on the response → `fetchXyz()` function in `services/api.js` → `XyzSection.jsx` component with a `<SourceBadge>` → registered in `AnalyticsSection.jsx`'s `TAB_GROUPS` → smoke test → verified in browser → committed.

## Known real gaps (disclosed, not hidden)

- Real height/weight/wingspan/draft-combine data now exists (`draft_combine` table, `scripts/fetch_draft_combine.py`) but only covers players who were actually invited to and measured at a real NBA Draft Combine — not every drafted player attends (e.g. Zion Williamson and Victor Wembanyama both have a real combine row but every measurement field is NULL, since they didn't complete physical testing), and undrafted/international players who skipped it entirely have no row at all. NULL is stored, never guessed, for any test a player skipped. Fit Analysis and archetypes still use statistical clustering as a stand-in for a scouted role, since combine data doesn't cover skill/role.
- No real trade-transaction-history dataset exists for a genuine "Trade Chemistry Simulator" — Lineup Chemistry is the honest, real-data substitute (see above); a true trade simulator remains blocked on data, not effort.
- No real MVP/DPOY/ROY betting-futures market exists on the odds provider used here — checked directly against the live API before building the Vegas Scanner, which is why that feature compares championship odds instead.
- The odds API free tier is quota-limited (~500 req/month); the scanner caches live odds for 6 hours rather than fetching on every page load.
- The WPA model is trained on 7,652 real games across seasons 2021-2026 (real full-season coverage for most of that span via ESPN/sportsdataverse, plus a real ~420-game nba_api sample) — always disclosed via `sample_size_games`/`n_games_train`/`n_games_test` in the relevant API responses rather than implied to be comprehensive. 42,570 of ~3.15M real ESPN-sourced player-attributed events (1.4%) couldn't be matched to a real nba_api `person_id` by name and so carry no per-player WPA attribution — a real, disclosed gap in player-level totals only, not in the model itself (training never uses `person_id`).
- Live `nba_api`/`stats.nba.com` calls are occasionally flaky (shot-chart data, live player search) — external network reliability, handled with retries/backoff, not a code bug.
- `/games/by-date` (`fetch_nba_games_by_date`) is built around the live scoreboard endpoint, which works fast for today's real games but can take 45+ real seconds and still return an empty list for an older historical date — a real, pre-existing limitation of that endpoint discovered while building Schedule Fatigue (which doesn't depend on it — Live Scores' rest tags come from the separately-computed `team_game_fatigue` table). Not yet fixed; worth a look if historical date browsing on Live Scores becomes a priority.
- Referee Tendencies' real officials data comes from `BoxScoreSummaryV2`, which has no bulk endpoint (one real call per real game) — `scripts/fetch_referee_officials.py` is resumable and grows real coverage each time it's re-run rather than fetching every real game in one sitting, so `n_games`/season span shown in the UI reflects real coverage as of whenever it was last run, not necessarily every game in scope. Separately, nba_api's own `BoxScoreSummaryV2` is confirmed (checked directly against the live endpoint) to be missing officials for some real games on/after 2025-04-10 — those games are logged as attempted so they aren't retried forever, but contribute no officials rows, a real gap in very recent coverage specifically.

## Roadmap / what's next

This project is being built from a longer plan, executed feature-by-feature with full verification each time. Everything above is done, verified, and pushed. In priority order, what's left (originally scoped by a planning session, adjusted where building earlier items changed the scope of later ones):

1. **Model diversity — advisor feedback (2026-09-25), due next week — Gradient Boosting shipped, WPA still open**: advisor's reaction to the models table was "it's all Logistic Regression and Random Forest, use different ones."
   - ✅ **Shipped**: `sklearn.ensemble.GradientBoostingClassifier` added as a third model type in `scripts/backtest_models.py`'s `MODEL_CONFIGS` (zero new dependency — neither xgboost nor lightgbm was already installed). Reused the exact real LOSO backtest pipeline unchanged; the only real code change needed was applying balanced class weighting manually via `sample_weight` (`GradientBoostingClassifier` has no `class_weight` param, unlike the other two). `/backtest/{award}/compare` and `ModelValidationSection.jsx` both already generalized over model count — no API or frontend changes needed at all, confirmed live. Honest real result, not cherry-picked: Gradient Boosting does **not** clearly beat the other two on this dataset — Logistic Regression actually has the highest real ROC-AUC on MVP (0.996), DPOY (0.939), and ROY (0.967) backtests; Gradient Boosting's real ROC-AUC came in lowest of the three on all three awards (0.928 / 0.768 / 0.925). Disclosed as a real finding (simpler models holding up on a small, high-class-imbalance real dataset), not hidden to make the new model look better. Found and fixed one real pre-existing bug while rerunning the backtest: a NumPy 2.0 compatibility issue (`np.float64.__repr__` changed to `"np.float64(0.99...)"`) broke writing `roc_auc` to Postgres entirely, unrelated to this change but blocking it — fixed with an explicit `float()` cast. 42/42 smoke tests passing (1 new).
   - ⬜ **Still open**: a boosted-tree alternative to the WPA model's current calibrated Logistic Regression — bigger lift (that model isn't part of the generic `MODEL_CONFIGS` comparison), not started.
2. **B5 — Ask the Database in plain English** (optional, costs Anthropic API spend, needs owner buy-in first): natural-language question → LLM-generated single read-only `SELECT` → real safety checks (reject anything but `SELECT`/`WITH...SELECT`, read-only transaction, statement timeout, row limit, ideally a dedicated read-only Postgres role) → results table shown alongside the exact SQL that ran, so the answer is verifiably real.

Everything else in the original roadmap, including A6, is now shipped — B5 stays parked on purpose (real API spend, needs owner buy-in before starting). Item 1's WPA half is new, added straight from advisor feedback.

**Just shipped:** A6 — Split `impact_api.py` into routers. The single 5,569-line file is now a ~85-line app entrypoint (`impact_api.py`: builds the FastAPI app, wires CORS, mounts every router) plus `impact_core.py` (the shared DB pool, loaded models, and every helper function/constant more than one route needs) plus 35 files under `api/routers/`, one per feature, each holding just its own `@router.get` route(s) moved verbatim out of the original file. Built with a small one-off AST-based script (not committed) that sliced the original file by exact top-level statement boundaries so route bodies transferred byte-for-byte, rather than hand-copying 5,500 lines. Verified two ways beyond the smoke suite: every one of the real 59 endpoints was hit live post-split (0 real 5xx responses — the only non-200s were pre-existing, real data gaps like an unpopulated `draft_history` table, not regressions) and `/openapi.json` was diffed to confirm all 59 real paths are still registered, byte-identical to before. One real internal-only break found and fixed: a smoke test reached into `impact_api._attach_rest_tags` directly (a private helper, now in `impact_core` since it's shared, not a route) — updated the test's import, no route or contract changed. 33/33 smoke tests passing.

**Just shipped (previously):** C6 — Referee Tendencies (owner sign-off given for this session). Real officials per game (`BoxScoreSummaryV2`, no bulk endpoint — one real call per real game) matched to real fouls/FTA/pace from `LeagueGameFinder`, season-adjusted against the real league average, real 95% CI on the difference, small-n warning below 25 real games worked (`scripts/fetch_referee_officials.py` → `scripts/build_referee_tendencies.py` → `GET /referees/tendencies`). The one-call-per-game officials fetch is real but slow, so it's built resume-if-interrupted/skip-already-fetched by design — it doesn't need to finish in one run, real coverage just grows each time it's re-run, and the UI always discloses the real current `n_games`/season span rather than implying full coverage. A known real gap, verified directly against the live endpoint: nba_api's own `BoxScoreSummaryV2` is missing officials for some real games on/after 2025-04-10, logged as attempted (not retried) and simply contributing no officials rows. Wording throughout (methodology text, `InfoTooltip`, this entry) is deliberately descriptive, not diagnostic — a real comparison of totals against a real baseline, not a claim about cause, intent, or bias. 33/33 smoke tests passing (2 new).

**Just shipped (previously):** A2 — Source badges. Every main Analytics endpoint (~20 across all three services — Awards Race, Impact Rankings, Model Validation, Prediction Ledger, Season Similarity, Player Archetypes + Offensive Style, Radar Compare, Trend Analysis, Career Trajectory, Heliocentricity, Clutch WPA, Matchup Finder, Vegas Scanner, Playoff Forecaster, Lineup Chemistry, Game Replay, With/Without a Star, Schedule Fatigue, Draft Prospects) now returns a `_source` object (`api/source_badge.py`) disclosing which real Postgres table(s) backed the response and which real upstream API/dataset those tables came from — live-fetch endpoints (Heliocentricity, Vegas Scanner, Lineup Chemistry, Playoff Forecaster, With/Without a Star) are flagged `live: true` rather than implying a static precomputed table. Rendered as a small `SourceBadge` chip (`components/common/SourceBadge.jsx`) next to each section's existing `InfoTooltip`, with the real table list and live/precomputed status in its native hover tooltip. Purely additive (a new JSON key, a component that renders nothing without it) — no existing endpoint contract changed, all 28 pre-existing smoke tests kept passing untouched, plus 3 new ones spot-checking `_source`'s shape.

**Just shipped (previously):** C8 — Full-season play-by-play via sportsdataverse (ESPN), retrained WPA (real 7,652 games, up from ~420 — see "What's in it" above and the `pbp_games`/`pbp_events` schema row). `scripts/fetch_pbp_espn.py` pulls a real FULL season in one ~30s call via `sportsdataverse.nba.load_nba_pbp` (vs. nba_api's one-call-per-game `PlayByPlayV3`), real ESPN team abbreviations mapped to nba_api tricodes (6 real mismatches: GS/NO/NY/SA/UTAH/WSH → GSW/NOP/NYK/SAS/UTA/WAS), real player names matched to existing `person_id`s by accent-normalized exact match then rapidfuzz fallback (98.6% real match rate). `pbp_games.source` ('nba_api' or 'espn') keeps the two real sources disclosed and never silently mixed, though both were verified to share the identical seconds-remaining/score-margin convention first. Retrained model: real held-out ROC-AUC 0.835 (0.859 clutch-time-only) on 1,531 real held-out games. Found and fixed three real bugs surfaced by the bigger dataset (unbatched `compute_wpa.py` still running after an hour, fixed to ~13s; Game Replay's missed-shot/3PT detection only understanding nba_api's own text vocabulary; ESPN mislabeling the 2024 All-Star Game as "regular season") — see git history for the full writeup.

**Operational note on the Prediction Ledger:** `snapshot_predictions.py` only logs a real point-in-time snapshot when it's actually run — nothing runs it automatically. Run it daily or weekly (manually, or via a cron/launchd entry you set up yourself; see the script's own docstring for an example cron line — nothing is auto-installed) so real trajectory history builds up over the season, and run `resolve_predictions.py` periodically to grade any season that's actually finished.

Throughout: update this README's "What's in it" and "Known real gaps" sections as each item ships, add a smoke test alongside each new endpoint rather than batching test-writing to the end, and keep screenshots reasonably current (not strictly required for every feature, but nice to have for the ones with real visual payoff).

---

## Advisor demo — status check (5-min read)

*Last checked: 2026-09-25.*

**Launch (3 backends + frontend), from repo root:**
```bash
cd api
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn mvp_api:app        --port 8000 --reload
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn similarity_api:app --port 8001 --reload
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn impact_api:app     --port 8002 --reload
cd ../frontend && npm run dev
```

**Checked just now — all green:**
- All 3 backends + frontend healthy (200 on every root endpoint).
- Full smoke suite: **34/34 passing**.
- `git status`: clean, pushed to `origin/main` (up to date through commit `5d44a25`).
- No console errors on the pages exercised below.

**Since the last demo, what changed:**
- Historical depth: `player_season_stats` now spans **1950–2026** (was 2010–2026), via a verified Kaggle import — Trend Analysis, career pages, etc. now show real full-career/full-franchise history instead of stopping at 2010.
- Shot charts: **6.3M real shots bulk-loaded** for 2,842 players (1996-97 through 2025-26). This was the fix for the recurring "impact_api freezes on a shot-chart search" bug from earlier — most real player searches now serve instantly from Postgres instead of a live, flaky stats.nba.com scrape.
- Fixed today: a player-search dropdown that was visually clipping/overlapping content on Player Comparison, Career Trajectory Forecaster, and Playoff Forecaster; missing hover tooltips on the Career Trajectory band/projection line, Game Replay's win-probability line, and the Schedule Fatigue chart; two garbled rotated axis labels; and a real regression the historical import introduced (League Evolution's trend charts had silently drifted out of sync with its archetype chart — caught by the smoke suite, fixed, verified).

**Safe to demo confidently:** MVP/DPOY/ROY prediction + backtesting, Season Similarity, Player Archetypes + League Evolution, Trend Analysis (player and team), Career Trajectory Forecaster, Game Replay (win-probability, now fully hoverable), Playoff Drop-off Forecaster, shot charts for any well-known player (LeBron, Jokić, Giannis, etc. — all bulk-cached).

**One thing to avoid, same as last time:** a shot-chart search for an obscure player who isn't among the 2,842 bulk-loaded (rare now, but possible for a very marginal role player) can still fall back to a live stats.nba.com fetch and be slow. If in doubt, search a recognizable name first.

**Not demoed/verified end-to-end:** the Supabase cloud DB path (`DB_TARGET=cloud` in `api/.env`) — wired and ready, but connection testing was blocked by network policy (university wifi blocks outbound Postgres ports 5432/6543). The demo should run on the local DB (`DB_TARGET=local`, the default) unless that's been separately verified since.
