# NBA Hub

A full-stack NBA analytics platform built as a final-year project: real historical data (2009-10 through the current season), trained and backtested prediction models, era-normalized player comparison engines, a live betting-market cross-reference, and a suite of stat-driven daily games — all built on top of one PostgreSQL database and served through three FastAPI microservices to a React dashboard.

The project's guiding rule, followed throughout: **never fabricate data**. Where a real number exists, it's used and its source is traceable to a real endpoint or a real database column. Where the project doesn't have real data for something (player height/wingspan, a trained championship-probability model, MVP betting odds), that gap is disclosed in the UI rather than invented — several features below explicitly say what they *aren't* for exactly this reason.

## What's in it

**Core stats & browsing**
- Live scores, standings, news, team comparison, full player-stats browser with sortable/filterable tables
- Shot charts (real shot-location data, live-fetched and cached per player/season)
- Stat leaders, Draft Value Guide (career value by draft slot), Rookie Class Tracker (this season's rookies ranked by real ROY probability)

**Player Comparison**
- Head-to-head statistical profiles with percentile ranks against a real qualified-player pool
- Fit Analysis: usage/spacing/role-overlap flags computed from real percentile thresholds — explicitly labeled as a simplified heuristic, not a trained synergy model, since no real lineup/play-by-play data exists to validate one

**Analytics** (multi-tab)
- Season Similarity & Player Archetypes — cosine-similarity and K-Means clustering, both **era-normalized**: every feature is z-scored within its own season before comparison, so a 2011 stat line and a 2025 stat line are judged relative to their own era instead of the raw numbers (which drift a lot over 16 seasons of pace/3PT-volume change)
- Awards Race, Impact Rankings, Model Validation (ROC curves, SHAP feature breakdowns, leave-one-season-out backtests against real historical winners)
- Career Trajectory Forecaster — CARMELO-style projection: finds a player's closest real statistical comps at the same age, plots what those comps *actually* did in their following seasons, weighted by similarity. If the player already has real future data, it's overlaid so the projection can be checked against what really happened.
- Vegas vs. Machine — real live NBA championship-winner odds (multiple real sportsbooks), de-vigged with Shin's (1992) method, compared against each team's real win percentage. Explicitly disclosed as a naive proxy, not a trained model.

**Games** (daily, stateless, all built on the same real qualified-player pool)
- Guess the Player — Wordle-style deduction with team/position/archetype/stat clues
- Blurred Player — a real headshot that sharpens with each wrong guess (proxied through the backend so the image URL can't just be read off the page)
- Higher or Lower — chain guesses on real career totals
- Trivia — 5 daily multiple-choice questions; the correct answer is always today's real value, decoys are always other real players

**Dashboard**
- Awards Race cards show real seed and "won it before" streak badges next to each prediction — informational context pulled from real standings and real award history, never folded into the model's own probability number

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
│  backtests,      │    │  clustering,      │    │  games, dashboard,│
│  SHAP            │    │  trajectory       │    │  odds, live data  │
└────────┬─────────┘    └────────┬──────────┘    └────────┬─────────┘
         └────────────────────────┼────────────────────────┘
                                  ▼
                     ┌─────────────────────────┐
                     │  PostgreSQL: nba_analytics│
                     └─────────────────────────┘
```

Each service is independent (its own CORS setup, its own DB pool) and can be restarted without touching the others. The frontend's `services/api.js` hardcodes which base URL each feature calls.

## Tech stack

- **Backend:** Python, FastAPI, psycopg2, pandas, scikit-learn, scipy, `nba_api`, requests
- **Database:** PostgreSQL (`nba_analytics`)
- **Frontend:** React 19, Vite, Framer Motion, axios — no charting library; every chart on this project (radar profiles, ROC curves, trajectory cones, archetype scatter plots) is a hand-built SVG component
- **Models:** Logistic Regression (MVP/DPOY/ROY, ROC/SHAP-validated), K-Means clustering (archetypes), cosine similarity (season/career comps)

## Running it locally

### Prerequisites
- PostgreSQL running locally with a `nba_analytics` database already populated (see `scripts/` for the fetch/build pipeline that builds it from `nba_api`)
- Node.js for the frontend

### Python interpreter — read this first
The plain `python3` on this machine resolves to an Anaconda install that's **missing this project's dependencies**. Use the real interpreter explicitly:
```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pip install fastapi uvicorn psycopg2-binary pandas numpy scikit-learn scipy nba_api requests python-dotenv
```

### Start the three backend services
Each file's own `if __name__ == "__main__"` fallback port **does not match** what the frontend actually expects — always pass `--port` explicitly:
```bash
cd api
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn mvp_api:app        --port 8000 --reload
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn similarity_api:app --port 8001 --reload
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m uvicorn impact_api:app     --port 8002 --reload
```

### Optional: live championship odds
The Vegas Scanner needs a free [The Odds API](https://the-odds-api.com/) key (free tier: ~500 requests/month). Without it, every other feature still works — that one tab just reports the key is missing.
```bash
echo "ODDS_API_KEY=your_key_here" > api/.env
```

### Start the frontend
```bash
cd frontend
npm install
npm run dev
```
Opens at `http://localhost:5173`.

## Project structure

```
api/                    FastAPI services (mvp_api, similarity_api, impact_api, shots_lib)
  .env                  ODDS_API_KEY — gitignored, not committed
scripts/                Data pipeline: fetch from nba_api → build DB tables → train/backtest models
models/                 Saved model artifacts (.pkl)
nba_data/               Season CSVs
frontend/
  src/
    components/
      pages/            Top-level routed pages (Dashboard, Player Comparison, Games hub, ...)
      common/            Shared components (PlayerHeadshot, TeamLogo, InfoTooltip, ...)
      *.jsx              Analytics sub-tabs (SimilaritySection, TrajectoryForecasterSection, ...)
    services/api.js      Every backend call, one file, grouped by feature
    styles/dashboard.css Single stylesheet for the whole app
PROJECT_DETAILS.md       Deep-dive on the ML methodology (predates several features in this README —
                          the models section is still accurate, treat feature lists here as current)
```

## Known real gaps (disclosed, not hidden)

- No real player height/weight/wingspan/draft-combine data anywhere in the pipeline — features that would want it (Fit Analysis, archetypes) use "archetype" (statistical clustering) as a disclosed stand-in for a scouted role instead.
- No real MVP/DPOY/ROY betting-futures market exists on the odds provider used here — checked directly against the live API before building the Vegas Scanner, which is why that feature compares championship odds instead.
- The odds API free tier is quota-limited (~500 req/month); the scanner caches live odds for 6 hours rather than fetching on every page load.
- Live `nba_api`/`stats.nba.com` calls are occasionally flaky (used for shot-chart data and live player search) — this is an external network reliability issue outside the project's control, not a code bug, and is handled with retries/backoff rather than silently failing.
