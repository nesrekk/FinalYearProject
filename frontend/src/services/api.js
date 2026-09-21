/**
 * NBA Analytics API Service
 *
 * IMPORTANT: FastAPI backend must enable CORS for frontend requests.
 * Add CORSMiddleware to each FastAPI app with:
 *   allow_origins=["http://localhost:5173"]  (or ["*"] for dev)
 *   allow_methods=["*"]
 *   allow_headers=["*"]
 */

import axios from 'axios';
import { localDateIso } from '../utils/date';

const MVP_BASE = 'http://localhost:8000';
const SIMILARITY_BASE = 'http://localhost:8001';
const IMPACT_BASE = 'http://localhost:8002';
const CACHE_PREFIX = 'nbahub_cache_v2:';

function readCache(key, ttlMs) {
    try {
        const raw = localStorage.getItem(`${CACHE_PREFIX}${key}`);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return null;
        if (Date.now() - parsed.ts > ttlMs) return null;
        return parsed.data;
    } catch {
        return null;
    }
}

function writeCache(key, data) {
    try {
        localStorage.setItem(
            `${CACHE_PREFIX}${key}`,
            JSON.stringify({ ts: Date.now(), data })
        );
    } catch {
        // Ignore storage errors in private/incognito or quota limits.
    }
}

async function getWithCache(key, ttlMs, fetcher) {
    const cached = readCache(key, ttlMs);
    if (cached) return cached;
    const fresh = await fetcher();
    writeCache(key, fresh);
    return fresh;
}

// ─── Similarity ────────────────────────────────────────────────
export async function fetchSeasonSimilarity(player, season) {
    const response = await axios.get(
        `${SIMILARITY_BASE}/similarity/season/${encodeURIComponent(player)}/${season}`
    );
    return response.data;
}

export async function fetchPlayerSuggestions(query, limit = 8) {
    const response = await axios.get(
        `${SIMILARITY_BASE}/players/search`,
        { params: { q: query, limit } }
    );
    return response.data;
}

// ─── Player Archetype Clusters ────────────────────────────────
export async function fetchArchetypes() {
    const response = await axios.get(`${SIMILARITY_BASE}/clusters/archetypes`);
    return response.data;
}

export async function fetchSeasonClusters(season) {
    const response = await axios.get(`${SIMILARITY_BASE}/clusters/season/${season}`);
    return response.data;
}

export async function fetchPlayerClusterHistory(playerName) {
    const response = await axios.get(
        `${SIMILARITY_BASE}/clusters/player/${encodeURIComponent(playerName)}`
    );
    return response.data;
}

export async function fetchLivePlayerSuggestions(query, limit = 20) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/search`,
        { params: { q: query, limit } }
    );
    return response.data;
}

// ─── MVP Prediction ────────────────────────────────────────────
export async function fetchMVPPrediction(season) {
    const response = await axios.get(`${MVP_BASE}/mvp/predict/${season}`);
    return response.data;
}

// ─── DPOY / ROY Prediction ─────────────────────────────────────
export async function fetchDPOYPrediction(season) {
    const response = await axios.get(`${MVP_BASE}/dpoy/predict/${season}`);
    return response.data;
}

export async function fetchROYPrediction(season) {
    const response = await axios.get(`${MVP_BASE}/roy/predict/${season}`);
    return response.data;
}

export async function fetchAllNBAPrediction(season) {
    const response = await axios.get(`${MVP_BASE}/allnba/predict/${season}`);
    return response.data;
}

// ─── Model Validation (backtest) ──────────────────────────────
export async function fetchBacktestOverview() {
    const response = await axios.get(`${MVP_BASE}/backtest`);
    return response.data;
}

export async function fetchBacktestDetail(award, model) {
    const response = await axios.get(`${MVP_BASE}/backtest/${award}`, { params: model ? { model } : {} });
    return response.data;
}

export async function fetchBacktestComparison(award) {
    const response = await axios.get(`${MVP_BASE}/backtest/${award}/compare`);
    return response.data;
}

// ─── SHAP Explainability (Random Forest) ──────────────────────
export async function fetchShapCandidates(award) {
    const response = await axios.get(`${MVP_BASE}/explain/${award}`);
    return response.data;
}

export async function fetchShapBreakdown(award, playerName) {
    const response = await axios.get(`${MVP_BASE}/explain/${award}/${encodeURIComponent(playerName)}`);
    return response.data;
}

// ─── Trend Analysis ────────────────────────────────────────────
export async function fetchPlayerHistory(playerName) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/history/${encodeURIComponent(playerName)}`
    );
    return response.data;
}

export async function fetchTeamHistory(teamAbbr) {
    const response = await axios.get(`${IMPACT_BASE}/teams/history/${teamAbbr}`);
    return response.data;
}

// ─── Radar Comparison ──────────────────────────────────────────
export async function fetchRadarProfile(playerName, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/radar/${encodeURIComponent(playerName)}`,
        { params: { season } }
    );
    return response.data;
}

// ─── Trade Analyzer ────────────────────────────────────────────
export async function fetchTradeTeams(season) {
    const response = await axios.get(`${IMPACT_BASE}/trade/teams/${season}`);
    return response.data;
}

export async function fetchTradeRoster(team, season) {
    const response = await axios.get(`${IMPACT_BASE}/trade/roster/${team}/${season}`);
    return response.data;
}

export async function simulateTrade({ season, teamA, playerAId, teamB, playerBId }) {
    const response = await axios.get(`${IMPACT_BASE}/trade/simulate`, {
        params: { season, team_a: teamA, player_a_id: playerAId, team_b: teamB, player_b_id: playerBId },
    });
    return response.data;
}

// ─── Impact Rankings ───────────────────────────────────────────
export async function fetchRawImpact(season) {
    const response = await axios.get(`${IMPACT_BASE}/impact/raw/${season}`);
    return response.data;
}

export async function fetchStarImpact(season) {
    const response = await axios.get(`${IMPACT_BASE}/impact/star/${season}`);
    return response.data;
}

export async function fetchCurrentMeta() {
    return getWithCache('meta_current', 90 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/meta/current`);
        return response.data;
    });
}

export async function fetchPlayerImage(playerName) {
    const response = await axios.get(
        `${IMPACT_BASE}/media/player-image/${encodeURIComponent(playerName)}`
    );
    return response.data;
}

export async function fetchPlayerProfile(playerName, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/profile/${encodeURIComponent(playerName)}`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

export async function fetchGamesByDate(date) {
    const d = date || localDateIso();
    const key = `games_by_date:${d}`;
    return getWithCache(key, 30 * 1000, async () => {
        const response = await axios.get(
            `${IMPACT_BASE}/games/by-date`,
            { params: { date: d } }
        );
        return response.data;
    });
}

export async function fetchCurrentNews(date, limit = 20) {
    const key = `news_current:${date || 'latest'}:${limit}`;
    return getWithCache(key, 120 * 1000, async () => {
        const response = await axios.get(
            `${IMPACT_BASE}/news/current`,
            { params: { ...(date ? { date } : {}), limit } }
        );
        return response.data;
    });
}

export async function fetchGameBoxscore(gameId) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/boxscore/${encodeURIComponent(gameId)}`
    );
    return response.data;
}

export async function fetchStatLeaders(statKey, season, topN = 10) {
    const key = `leaders:${statKey}:${season || 'latest'}:${topN}`;
    return getWithCache(key, 60 * 1000, async () => {
        const response = await axios.get(
            `${IMPACT_BASE}/leaders/${encodeURIComponent(statKey)}`,
            { params: { ...(season ? { season } : {}), top_n: topN } }
        );
        return response.data;
    });
}

// ─── Shot Charts ───────────────────────────────────────────────
// A cache miss triggers a rate-limited live fetch on the backend (see
// api/shots_lib.py) which can take a couple of minutes for a long career —
// give it a generous timeout instead of the axios default.
export async function fetchShotSeasons(playerName) {
    const response = await axios.get(
        `${IMPACT_BASE}/shots/player/${encodeURIComponent(playerName)}/seasons`,
        { timeout: 6 * 60 * 1000 }
    );
    return response.data;
}

export async function fetchPlayerShots(playerName, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/shots/player/${encodeURIComponent(playerName)}`,
        { params: season ? { season } : {}, timeout: 6 * 60 * 1000 }
    );
    return response.data;
}

export async function prefetchCoreData() {
    const today = localDateIso();
    await Promise.allSettled([
        fetchCurrentMeta(),
        fetchCurrentNews(today, 10),
        fetchGamesByDate(today),
        fetchStatLeaders('pts', undefined, 10),
    ]);
}
