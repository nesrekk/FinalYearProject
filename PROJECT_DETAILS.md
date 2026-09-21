# 🏀 NBA Analytics Project — Models & Methods Details

> **Project Name:** NBA Analytics Platform  
> **Tech Stack:** Python, PostgreSQL, FastAPI, Vite (React), scikit-learn  
> **Data Source:** NBA API (`nba_api` library) → PostgreSQL (`nba_analytics` database)  
> **Training Data:** 2009–10 through 2023–24 seasons (~7,500+ player-season records)  
> **Prediction Target:** 2024–25 season

---

## 📂 Project Folder Structure

```
main nba project build/
├── scripts/               ← Model training & data processing scripts
│   ├── train_mvp_model.py
│   ├── build_dpoy_roy_models.py
│   ├── fetch_dpoy_roy_stats.py
│   ├── build_similarity_engine.py
│   ├── build_league_adjusted_similarity.py
│   ├── precompute_career_similarity.py
│   ├── precompute_league_similarity.py
│   ├── compute_impact_score.py
│   └── upgrade_impact_scores.py
├── api/                   ← FastAPI REST APIs (serves predictions)
│   ├── mvp_api.py
│   ├── impact_api.py
│   └── similarity_api.py
├── models/                ← Saved model artifacts (.pkl files)
│   ├── dpoy_model.pkl
│   ├── dpoy_scaler.pkl
│   ├── roy_model.pkl
│   └── roy_scaler.pkl
├── nba_data/              ← Season CSVs (basic + advanced stats)
├── frontend/              ← Vite + React dashboard UI
└── PROJECT_DETAILS.md     ← This file
```

---

## 🧠 Models Used in This Project

This project uses **four distinct model/method types**:

| # | Model / Method | Type | Purpose |
|---|----------------|------|---------|
| 1 | **Logistic Regression** | Supervised Classification | Predict MVP, DPOY, ROY award winners |
| 2 | **Cosine Similarity** | Unsupervised Similarity Metric | Find similar player-seasons and careers |
| 3 | **League-Adjusted Cosine Similarity** | Unsupervised Similarity (enhanced) | Cross-era player comparison |
| 4 | **Weighted Composite Scoring (Z-Score)** | Statistical Scoring Model | Compute player impact scores |

---

## 📋 Detailed File-by-File Breakdown

---

### 1. `scripts/train_mvp_model.py` — MVP Prediction Model

**Model Used:** `LogisticRegression` (from `scikit-learn`)

**What is Logistic Regression?**  
Logistic Regression is a supervised machine learning algorithm used for **binary classification**. Unlike Linear Regression which predicts continuous values, Logistic Regression predicts the **probability** of a binary outcome (in our case: MVP or Not MVP). It uses the **sigmoid function** to map any real-valued input to a probability between 0 and 1.

**Why Logistic Regression for MVP Prediction?**
- **Binary outcome:** MVP is a yes/no classification problem — only one player wins per season.
- **Probability output:** We need probabilities (not just yes/no), so we can rank players by MVP likelihood.
- **Interpretable coefficients:** Each feature gets a coefficient showing its weight (positive = increases MVP chance, negative = decreases it). This is critical for explaining *why* a player is predicted as MVP.
- **Works well with small positive samples:** Only ~15 MVP winners exist across 15 training seasons. Logistic Regression with `class_weight="balanced"` handles this class imbalance effectively.
- **Lightweight and fast:** Trains instantly, no GPU required.

**Features Used (9 total):**
| Feature | Description |
|---------|-------------|
| `pts` | Points per game |
| `ts_pct` | True Shooting Percentage (shooting efficiency) |
| `usg_pct` | Usage Rate (% of team plays used) |
| `off_rating` | Offensive Rating (points produced per 100 possessions) |
| `def_rating` | Defensive Rating (points allowed per 100 possessions) |
| `net_rating` | Net Rating (off_rating − def_rating) |
| `w_pct` | Team Win Percentage |
| `min` | Minutes Per Game |
| `age` | Player Age |

**How It Works (Pipeline):**
1. Pull data from PostgreSQL (`player_season_stats` LEFT JOIN `mvp_winners`)
2. Label each row: 1 = MVP winner, 0 = not
3. Split: seasons 2010–2024 for training, 2025 for prediction
4. Normalize features using `StandardScaler` (zero mean, unit variance)
5. Train `LogisticRegression(class_weight="balanced", solver="lbfgs")`
6. Predict MVP probabilities for 2025 season using `predict_proba()`
7. Rank players by probability and return top candidates

**Output:** `mvp_model.pkl`, `mvp_scaler.pkl`

---

### 2. `scripts/build_dpoy_roy_models.py` — DPOY & ROY Prediction Models

**Model Used:** `LogisticRegression` (same algorithm as MVP, different features)

**What is DPOY & ROY?**
- **DPOY** = Defensive Player of the Year — best defensive player in the NBA
- **ROY** = Rookie of the Year — best first-year player in the NBA

**Why Logistic Regression (again)?**
- Same reasoning as MVP: binary classification, probability ranking, interpretable, handles class imbalance.
- Different features are selected for each award to capture what makes a DPOY vs ROY winner unique.

**DPOY Features (7 total):**
| Feature | Why? |
|---------|------|
| `def_rating` | Core defensive metric |
| `net_rating` | Overall impact on the game |
| `stl` | Steals per game (proxy for steal rate) |
| `blk` | Blocks per game (proxy for block rate) |
| `reb_pct` | Rebound percentage (defensive rebounding) |
| `min` | Minutes played (must be a starter) |
| `w_pct` | Team record (narrative matters) |

**ROY Features (6 total):**
| Feature | Why? |
|---------|------|
| `pts` | Scoring is the most visible stat for rookies |
| `ts_pct` | Shooting efficiency |
| `usg_pct` | How involved the rookie is in the offense |
| `net_rating` | Overall court impact |
| `min` | Playing time given by coach |
| `age` | Younger rookies stand out more |

**How It Works:**
1. Pull all player-season data from PostgreSQL
2. Label winners using hardcoded award winner lists (2010–2024)
3. Train separate Logistic Regression models for DPOY and ROY
4. Save models + scalers to `models/` folder
5. Predict top candidates for 2025

**Output:** `dpoy_model.pkl`, `dpoy_scaler.pkl`, `roy_model.pkl`, `roy_scaler.pkl`

---

### 3. `scripts/build_similarity_engine.py` — Player Season Similarity (Basic)

**Method Used:** `Cosine Similarity` (from `scikit-learn`)

**What is Cosine Similarity?**  
Cosine Similarity measures the **angle** between two vectors in multi-dimensional space. It returns a value between -1 and +1, where 1 means identical direction (perfectly similar), 0 means orthogonal (no similarity), and -1 means opposite. Unlike Euclidean distance, cosine similarity focuses on the **pattern** of stats rather than their magnitude.

**Why Cosine Similarity?**
- **Scale-invariant:** After StandardScaler normalization, cosine similarity captures the *profile shape* of a player, not just raw numbers.
- **Ideal for comparing player profiles:** Two players with similar stat distributions (e.g., high scorer + low defender) will have high similarity even if their raw numbers differ.
- **Fast and interpretable:** Easy to compute pairwise and explain to non-technical audiences ("Player A's stat profile is 95% similar to Player B's").

**Features Used (8 total):** `pts`, `ts_pct`, `usg_pct`, `net_rating`, `ast_pct`, `reb_pct`, `age`, `min`

**How It Works:**
1. Pull all player-season data from PostgreSQL
2. Normalize all features using `StandardScaler`
3. For a target player-season, compute cosine similarity against ALL other player-seasons
4. Return the top N most similar player-seasons

**Output:** `similarity_scaler.pkl`

---

### 4. `scripts/build_league_adjusted_similarity.py` — League-Adjusted Similarity

**Method Used:** `Cosine Similarity` with **League Adjustment** (season-relative normalization)

**What is League Adjustment?**  
Before computing similarity, each player's stats are adjusted by subtracting the **league average for that season**. This creates "adjusted" stats (e.g., `adj_pts = player_pts - league_avg_pts`) that show how far above or below average the player was *relative to their era*.

**Why League Adjustment?**
- **Fair cross-era comparison:** In the 2010s, average scoring was ~96 PPG per team; by 2024, it's ~114 PPG. Without adjustment, every modern player would appear "better" than older players simply because the league pace and scoring changed.
- **Controls for era inflation:** A player scoring 5 PPG above the 2012 average becomes comparable to a player scoring 5 PPG above the 2024 average.
- **Better similarity results:** Stephen Curry's 2016 season gets matched with historically dominant seasons from any era, not just recent high-scoring seasons.

**Features Used (8 total):**
- 6 league-adjusted: `adj_pts`, `adj_ts_pct`, `adj_usg_pct`, `adj_net_rating`, `adj_ast_pct`, `adj_reb_pct`
- 2 raw (not adjusted): `age`, `min`

**Output:** `league_similarity_scaler.pkl`

---

### 5. `scripts/precompute_league_similarity.py` — Precomputed Season Similarities

**Method Used:** `Cosine Similarity` (batch precomputation)

**What This Does:**  
Instead of computing similarity on-the-fly (which is slow for ~7,500 rows), this script **precomputes** the top-10 most similar seasons for EVERY player-season in the database and stores the results in a PostgreSQL table (`season_similarity`).

**Why Precompute?**
- **Performance:** Computing cosine similarity against 7,500+ rows for every query is expensive. Precomputing once and storing in a database makes API responses near-instant.
- **API-ready:** The FastAPI similarity endpoint just queries the `season_similarity` table — no model computation needed at request time.

**How It Works:**
1. Load all player-seasons, apply league adjustment
2. Normalize with `StandardScaler`
3. For each of ~7,500 rows, compute cosine similarity against all others
4. Take top 10 most similar and insert into `season_similarity` table
5. Total: ~75,000 rows inserted

**PostgreSQL Table Created:** `season_similarity` with columns: `source_player_id`, `source_season`, `similar_player_id`, `similar_season`, `similarity_score`

---

### 6. `scripts/precompute_career_similarity.py` — Precomputed Career Similarities

**Method Used:** `Cosine Similarity` (career-level aggregation)

**What This Does:**  
Aggregates season-level stats into **career averages** per player (AVG of pts, ts_pct, etc., plus COUNT of seasons played and MAX of pts for peak scoring), then computes pairwise cosine similarity and stores the top-10 most similar careers per player.

**Why Career-Level?**
- Answers the question "Who had the most similar overall career to LeBron?" rather than "Who had a similar single season?"
- Uses additional features like `seasons_played` (longevity) and `peak_pts` (peak performance).

**Features Used (9 total):**
`career_pts`, `career_ts_pct`, `career_usg_pct`, `career_net_rating`, `career_ast_pct`, `career_reb_pct`, `career_min`, `seasons_played`, `peak_pts`

**PostgreSQL Table Created:** `career_similarity` with columns: `source_player_id`, `similar_player_id`, `similarity_score`

---

### 7. `scripts/compute_impact_score.py` — Player Impact Score (v1)

**Method Used:** `Weighted Composite Scoring` + `Z-Score Normalization`

**What is a Weighted Composite Score?**  
A custom formula that combines multiple stats with hand-tuned weights to create a single "impact" number. This is NOT a machine learning model — it's a **statistical formula** designed based on domain knowledge of basketball analytics.

**The Formula:**
```
impact_score = (TS% × 2.0) + (NET_RATING × 0.5) + (USG% × 0.3)
             + (AST% × 0.3) + (REB% × 0.2) + (W% × 1.5)
             − (DEF_RATING × 0.2)
```

**Weight Rationale:**
| Stat | Weight | Why? |
|------|--------|------|
| `TS%` | 2.0 (highest) | Shooting efficiency is the most valuable offensive stat |
| `W%` | 1.5 | Winning matters — great players win games |
| `NET_RATING` | 0.5 | Overall point differential when on court |
| `USG%` | 0.3 | Higher usage = more responsibility |
| `AST%` | 0.3 | Playmaking ability |
| `REB%` | 0.2 | Rebounding contribution |
| `DEF_RATING` | −0.2 | *Subtracted* because lower DEF_RATING = better defense |

**Z-Score Normalization:**  
After computing the raw score, it's normalized per season:
```
z = (player_raw_score − season_mean) / season_std
```
This means a score of +2.0 means the player was 2 standard deviations above the season average.

**Why This Method?**
- Simple and explainable — anyone can understand the formula.
- Per-season normalization makes scores comparable across eras.
- Stored directly in PostgreSQL for fast querying.

---

### 8. `scripts/upgrade_impact_scores.py` — Upgraded Impact Scores (v2: Raw + Star)

**Method Used:** `Two-Tier Weighted Composite Scoring` + `Z-Score Normalization`

**What Changed from v1?**  
This upgrade creates **two separate impact scores**:

#### a) `impact_score_raw` — For ALL players
Same formula as v1:
```
raw = (TS% × 2.0) + (NET_RATING × 0.5) + (USG% × 0.3)
    + (AST% × 0.3) + (REB% × 0.2) + (W% × 1.5) − (DEF_RATING × 0.2)
```

#### b) `impact_score_star` — For STAR-CALIBER players only
**Filter:** Only players with `MIN ≥ 28` AND `USG% ≥ 0.22` (high-minutes, high-usage players)

**Different formula — production-weighted:**
```
star = (PTS × 0.6) + (TS% × 1.8) + (NET_RATING × 0.5) + (USG% × 0.5)
     + (AST% × 0.3) + (REB% × 0.2) + (W% × 1.0) − (DEF_RATING × 0.2)
```

**Key Difference:** The star formula adds `PTS × 0.6` (raw scoring volume) and increases `USG% weight to 0.5`, because for star players we care about how much they score, not just efficiency.

---

### 9. `scripts/fetch_dpoy_roy_stats.py` — Data Collection (No Model)

**Model Used:** None — this is a **data pipeline script**.

**What It Does:**  
Builds DPOY and ROY award winner datasets by merging base stats from PostgreSQL with advanced stats from CSV files. Saves as both CSVs and PostgreSQL tables.

**Purpose:** Creates the labeled datasets (`dpoy_seasons.csv`, `roy_seasons.csv`) that feed into model training.

---

## 🌐 API Layer (FastAPI)

The models are served through three FastAPI applications:

### `api/mvp_api.py` — MVP Prediction API
- **Port:** 8001
- **Endpoint:** `GET /mvp/predict/{season}`
- **What It Does:** Loads the trained `mvp_model.pkl` and `mvp_scaler.pkl` at startup. For a given season, pulls all player stats, runs the Logistic Regression model, and returns top 15 MVP candidates with probabilities.

### `api/impact_api.py` — Impact Score API
- **Port:** 8002
- **Endpoints:**
  - `GET /impact/raw/{season}` — Top 20 players by raw impact score
  - `GET /impact/star/{season}` — Top 20 star-qualified players by star impact
  - `GET /impact/player/{name}/{season}` — Both scores for a specific player
- **What It Does:** Reads precomputed `impact_score_raw` and `impact_score_star` values from PostgreSQL. No model inference — just database queries.

### `api/similarity_api.py` — Player Similarity API
- **Port:** 8000
- **Endpoints:**
  - `GET /similarity/season/{name}/{season}` — Top 10 similar seasons
  - `GET /similarity/career/{name}` — Top 10 similar careers
- **What It Does:** Reads precomputed similarity scores from `season_similarity` and `career_similarity` PostgreSQL tables. No real-time computation — just database lookups.

---

## 🔧 Key Libraries Used

| Library | Version Role |
|---------|-------------|
| `scikit-learn` | LogisticRegression, StandardScaler, cosine_similarity, accuracy_score, confusion_matrix |
| `pandas` | Data manipulation, DataFrames, CSV I/O |
| `numpy` | Numerical operations, array handling |
| `psycopg2` | PostgreSQL database connection |
| `FastAPI` | REST API framework |
| `uvicorn` | ASGI web server for FastAPI |
| `pickle` | Model serialization (.pkl files) |
| `nba_api` | Data fetching from NBA stats endpoints |

---

## 💾 Database Tables (PostgreSQL — `nba_analytics`)

| Table | Description |
|-------|-------------|
| `player_season_stats` | Main table — all player stats per season (pts, ts_pct, etc.) + impact scores |
| `mvp_winners` | MVP winner IDs and seasons |
| `dpoy_seasons` | DPOY winner stats per season |
| `roy_seasons` | ROY winner stats per season |
| `season_similarity` | Precomputed top-10 similar player-seasons |
| `career_similarity` | Precomputed top-10 similar player careers |

---

## 📊 Summary for Guide Meeting

**In one sentence:** This project uses **Logistic Regression** to predict NBA award winners (MVP, DPOY, ROY), **Cosine Similarity** to find similar players across eras, and **Weighted Composite Scoring** to rank player impact — all served through FastAPI endpoints and displayed on a React dashboard.

**Key talking points:**
1. **Why Logistic Regression?** — Binary classification, probability output for ranking, interpretable coefficients, handles class imbalance with balanced weights
2. **Why Cosine Similarity?** — Measures stat profile similarity (pattern, not magnitude), scale-invariant, fast computation
3. **Why League Adjustment?** — Fair cross-era comparison by subtracting season averages
4. **Why Weighted Composite?** — Simple, explainable impact formula based on basketball domain knowledge
5. **Why Z-Score Normalization?** — Makes scores comparable across different seasons
6. **Architecture:** Data flows from NBA API → PostgreSQL → Python scripts (train models) → FastAPI (serve predictions) → React frontend (display results)
