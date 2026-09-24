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

export async function fetchPlayerTrajectory(player, season, topNComps = 5, projectYears = 3) {
    const response = await axios.get(
        `${SIMILARITY_BASE}/players/trajectory/${encodeURIComponent(player)}`,
        { params: { season, top_n_comps: topNComps, project_years: projectYears } }
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

export async function fetchLeagueEvolution() {
    const response = await axios.get(`${SIMILARITY_BASE}/clusters/evolution`);
    return response.data;
}

export async function fetchLivePlayerSuggestions(query, limit = 20) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/search`,
        { params: { q: query, limit } }
    );
    return response.data;
}

export async function fetchPlayersTable(season, minMinutes = 0) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/table/${season}`,
        { params: { min_minutes: minMinutes } }
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

export async function fetchROYPrediction(season, topN) {
    const response = await axios.get(`${MVP_BASE}/roy/predict/${season}`, {
        params: topN ? { top_n: topN } : {},
    });
    return response.data;
}

export async function fetchAllNBAPrediction(season) {
    const response = await axios.get(`${MVP_BASE}/allnba/predict/${season}`);
    return response.data;
}

// ─── Draft Value Analysis ──────────────────────────────────────
export async function fetchDraftClass(draftYear) {
    const response = await axios.get(`${IMPACT_BASE}/draft/${draftYear}`);
    return response.data;
}

export async function fetchDraftValueCurve() {
    const response = await axios.get(`${IMPACT_BASE}/draft/value-curve`);
    return response.data;
}

export async function fetchDraftBestValue(limit = 15, worst = false) {
    const response = await axios.get(`${IMPACT_BASE}/draft/best-value`, { params: { limit, worst } });
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

export async function fetchAllNBABacktest() {
    const response = await axios.get(`${MVP_BASE}/backtest/allnba`);
    return response.data;
}

export async function fetchWpaValidation() {
    const response = await axios.get(`${MVP_BASE}/validation/wpa`);
    return response.data;
}

export async function fetchLedgerSummary(season) {
    const response = await axios.get(
        `${MVP_BASE}/ledger/summary`,
        { params: season ? { season } : {} }
    );
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

// ─── Player Comparison ─────────────────────────────────────────
export async function fetchCompareProfile(playerName, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/compare-profile/${encodeURIComponent(playerName)}`,
        { params: { season } }
    );
    return response.data;
}

export async function fetchPairSynergy(playerA, playerB, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/pair-synergy`,
        { params: { player_a: playerA, player_b: playerB, ...(season ? { season } : {}) } }
    );
    return response.data;
}

// ─── Games: Guess the Player ───────────────────────────────────
export async function fetchGuessDaily(season) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/guess-the-player/daily`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

export async function submitGuessThePlayer(guessPlayerName, season, puzzleDate) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/guess-the-player/guess`,
        { params: { guess_player_name: guessPlayerName, season, puzzle_date: puzzleDate } }
    );
    return response.data;
}

export async function fetchGuessReveal(season, puzzleDate) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/guess-the-player/reveal`,
        { params: { season, puzzle_date: puzzleDate } }
    );
    return response.data;
}

// ─── Games: Blurred Player ──────────────────────────────────────
export async function fetchBlurredDaily(season) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/blurred-player/daily`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

export function getBlurredPlayerImageUrl(season, puzzleDate) {
    const params = new URLSearchParams({ season, puzzle_date: puzzleDate });
    return `${IMPACT_BASE}/games/blurred-player/image?${params.toString()}`;
}

export async function submitBlurredPlayerGuess(guessPlayerName, season, puzzleDate) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/blurred-player/guess`,
        { params: { guess_player_name: guessPlayerName, season, puzzle_date: puzzleDate } }
    );
    return response.data;
}

export async function fetchBlurredReveal(season, puzzleDate) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/blurred-player/reveal`,
        { params: { season, puzzle_date: puzzleDate } }
    );
    return response.data;
}

// ─── Playoff Drop-off Forecaster ─────────────────────────────────
export async function fetchPlayoffComparison(player, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/playoff-comparison/${encodeURIComponent(player)}`,
        { params: { season } }
    );
    return response.data;
}

// ─── Draft Prospect Comp Finder ──────────────────────────────────
export async function fetchDraftProspectComp(player, season, topNComps = 5, includeMeasurements = false) {
    const response = await axios.get(
        `${IMPACT_BASE}/prospects/comp/${encodeURIComponent(player)}`,
        {
            params: {
                ...(season ? { season } : {}),
                top_n_comps: topNComps,
                include_measurements: includeMeasurements,
            },
        }
    );
    return response.data;
}

export async function fetchLengthStudy() {
    const response = await axios.get(`${IMPACT_BASE}/draft/length-study`);
    return response.data;
}

// ─── Heliocentricity Index ────────────────────────────────────────
export async function fetchHeliocentricityLeaderboard(season, topN = 25) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/heliocentricity`,
        { params: season ? { season, top_n: topN } : { top_n: topN } }
    );
    return response.data;
}

// ─── Clutch-Time Win Probability Added (WPA) Tracker ─────────────
export async function fetchClutchWpaLeaderboard(topN = 25) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/clutch-wpa`,
        { params: { top_n: topN } }
    );
    return response.data;
}

// ─── Game Win-Probability Replay ───────────────────────────────────
export async function fetchWpReplayList(season) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/wp-replay/list`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

export async function fetchWpReplay(gameId) {
    const response = await axios.get(`${IMPACT_BASE}/games/wp-replay/${encodeURIComponent(gameId)}`);
    return response.data;
}

export async function fetchWpReplayWhatIf(gameId, eventId) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/wp-replay/${encodeURIComponent(gameId)}/whatif`,
        { params: { event_id: eventId } }
    );
    return response.data;
}

// ─── Lineup Chemistry ─────────────────────────────────────────────
export async function fetchLineupChemistry(order = 'best', minMinutes = 40, topN = 15, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/lineups/chemistry`,
        { params: { order, min_minutes: minMinutes, top_n: topN, ...(season ? { season } : {}) } }
    );
    return response.data;
}

// ─── Vegas vs. Machine: Championship Odds Scanner ───────────────
export async function fetchChampionshipOdds() {
    const response = await axios.get(`${IMPACT_BASE}/odds/championship`);
    return response.data;
}

// ─── Games: Guess the Game ──────────────────────────────────────
export async function fetchGuessTheGameDaily(puzzleDate) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/guess-the-game/daily`,
        { params: puzzleDate ? { puzzle_date: puzzleDate } : {} }
    );
    return response.data;
}

export async function submitGuessTheGame(team, attemptNumber, puzzleDate) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/guess-the-game/guess`,
        { params: { team, attempt_number: attemptNumber, puzzle_date: puzzleDate } }
    );
    return response.data;
}

export async function fetchGuessTheGameReveal(puzzleDate) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/guess-the-game/reveal`,
        { params: { puzzle_date: puzzleDate } }
    );
    return response.data;
}

// ─── Games: Trivia ───────────────────────────────────────────────
export async function fetchTriviaDaily(season) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/trivia/daily`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

export async function submitTriviaGuess(questionId, optionId, season, puzzleDate) {
    const response = await axios.get(
        `${IMPACT_BASE}/games/trivia/guess`,
        { params: { question_id: questionId, option_id: optionId, season, puzzle_date: puzzleDate } }
    );
    return response.data;
}

// ─── Games: Higher or Lower ─────────────────────────────────────
export async function fetchHigherLowerPool() {
    return getWithCache('higher_lower_pool', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/games/higher-lower/pool`);
        return response.data;
    });
}

// A cache miss triggers the same rate-limited live fetch as Shot Charts —
// give it the same generous timeout instead of the axios default.
export async function fetchPlayerShotZones(playerName, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/shots/player/${encodeURIComponent(playerName)}/zones`,
        { params: { season }, timeout: 6 * 60 * 1000 }
    );
    return response.data;
}

export async function fetchLeagueShotZones(season) {
    const response = await axios.get(
        `${IMPACT_BASE}/shots/league-zones/${season}`,
        { timeout: 90 * 1000 }
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

// ─── With vs. Without a Star ────────────────────────────────────
export async function fetchWithWithoutStar(team, season, playerName) {
    const response = await axios.get(
        `${IMPACT_BASE}/teams/with-without/${team}/${season}`,
        { params: { player_name: playerName } }
    );
    return response.data;
}

// ─── Schedule Fatigue ────────────────────────────────────────────
export async function fetchRestStudy(season) {
    const response = await axios.get(
        `${IMPACT_BASE}/schedule/rest-study`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

export async function fetchScheduleDifficulty(season) {
    const response = await axios.get(
        `${IMPACT_BASE}/schedule/difficulty`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

// ─── Play-Type Profiles & Hustle Stats ──────────────────────────
export async function fetchHustleLeaders(stat = 'deflections', season, topN = 15) {
    const response = await axios.get(
        `${IMPACT_BASE}/hustle/leaders`,
        { params: { stat, top_n: topN, ...(season ? { season } : {}) } }
    );
    return response.data;
}

export async function fetchPlaytypeProfile(playerName, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/playtype-profile/${encodeURIComponent(playerName)}`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

export async function fetchPlaytypeArchetypes() {
    const response = await axios.get(`${SIMILARITY_BASE}/clusters/playtype-archetypes`);
    return response.data;
}

export async function fetchPlaytypeSeasonClusters(season) {
    const response = await axios.get(`${SIMILARITY_BASE}/clusters/playtype-season/${season}`);
    return response.data;
}

// ─── Matchup Finder ("Kryptonite" defender/scorer matchups) ─────
export async function fetchPlayerMatchups(playerName, role = 'scorer', season, topN = 15) {
    const response = await axios.get(
        `${IMPACT_BASE}/matchups/player/${encodeURIComponent(playerName)}`,
        { params: { role, top_n: topN, ...(season ? { season } : {}) } }
    );
    return response.data;
}

// ─── Referee Tendencies ──────────────────────────────────────────
export async function fetchRefereeTendencies(minGames = 10, sort = 'n_games') {
    const response = await axios.get(
        `${IMPACT_BASE}/referees/tendencies`,
        { params: { min_games: minGames, sort } }
    );
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

export async function fetchBpmLeaderboard(season) {
    const response = await axios.get(`${IMPACT_BASE}/impact/bpm/${season}`);
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
