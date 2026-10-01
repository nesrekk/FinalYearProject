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

// Requests already in flight for a given cache key, keyed by that key.
// Without this, several components mounting in the same render pass (e.g.
// every section that needs /meta/current) each see a cache miss before the
// first one's response has come back and land, and all fire their own
// duplicate network request. Sharing the in-flight promise collapses those
// into one real request per key per TTL window instead.
const _inFlightRequests = new Map();

async function getWithCache(key, ttlMs, fetcher) {
    const cached = readCache(key, ttlMs);
    if (cached) return cached;

    const pending = _inFlightRequests.get(key);
    if (pending) return pending;

    const request = (async () => {
        try {
            const fresh = await fetcher();
            writeCache(key, fresh);
            return fresh;
        } finally {
            _inFlightRequests.delete(key);
        }
    })();
    _inFlightRequests.set(key, request);
    return request;
}

// ─── Similarity ────────────────────────────────────────────────
export async function fetchSeasonSimilarity(player, season) {
    const response = await axios.get(
        `${SIMILARITY_BASE}/similarity/season/${encodeURIComponent(player)}/${season}`
    );
    return response.data;
}

export async function fetchSeasonSimilarityProfile(
    player, season, { topN = 10, excludeSelf = true, minGp = 20, onePerPlayer = false, playerId } = {}
) {
    // playerId (optional) picks the player exactly; two players can share a name.
    const response = await axios.get(
        `${SIMILARITY_BASE}/similarity/season-profile/${encodeURIComponent(player)}/${season}`,
        { params: { top_n: topN, exclude_self: excludeSelf, min_gp: minGp, one_per_player: onePerPlayer, player_id: playerId } }
    );
    return response.data;
}

export async function fetchStatLineOptions() {
    const response = await axios.get(`${SIMILARITY_BASE}/similarity/stat-line/options`);
    return response.data;
}

// params: { line: 'pts:25,ts_pct:0.6', season, season_from, season_to, min_gp, one_per_player, top_n }
export async function fetchStatLineMatches(params) {
    const response = await axios.get(`${SIMILARITY_BASE}/similarity/stat-line`, { params });
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

export async function fetchAwardCalibration() {
    const response = await axios.get(`${MVP_BASE}/awards/calibration`);
    return response.data;
}

export async function fetchWpaValidation() {
    const response = await axios.get(`${MVP_BASE}/validation/wpa`);
    return response.data;
}

export async function fetchWpaModelCompare() {
    const response = await axios.get(`${MVP_BASE}/validation/wpa/compare`);
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

export async function fetchClutchSplit(minClutchChances = 100) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/clutch-split`,
        { params: { min_clutch_chances: minClutchChances } }
    );
    return response.data;
}

// ─── Game Win-Probability Replay ───────────────────────────────────
export async function fetchWpReplayList(season, gameId) {
    const params = {};
    if (season) params.season = season;
    if (gameId) params.game_id = gameId;
    const response = await axios.get(`${IMPACT_BASE}/games/wp-replay/list`, { params });
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

// Two-player chemistry grid from the stored 5-man lineups.
// params: { season, team, min_minutes, max_players }
export async function fetchPairGrid(params) {
    const response = await axios.get(`${IMPACT_BASE}/lineups/pair-grid`, { params });
    return response.data;
}

// On/off from the play-by-play lines (every minute, 2020-21 on): one team's
// players, or the league's qualified players. params: { season, team, min_minutes }
export async function fetchOnOff(params) {
    const response = await axios.get(`${IMPACT_BASE}/lineups/on-off`, { params });
    return response.data;
}

// Each team's top-usage player: the team with him on vs. off the floor. params: { season }
export async function fetchOnOffStars(params) {
    const response = await axios.get(`${IMPACT_BASE}/lineups/on-off/stars`, { params });
    return response.data;
}

// Rim deterrence: opponents' shots at the rim (and every other distance band)
// with each defender on the floor vs. off. params: { season, team, min_minutes, position }
export async function fetchRimDeterrence(params) {
    const response = await axios.get(`${IMPACT_BASE}/defense/rim-deterrence`, { params });
    return response.data;
}

// ─── Team page ──────────────────────────────────────────────────
// Every block of one team-season (?page=team). abbr: any code the team has
// used, or its franchise's; season: end year (omit for its latest).
export async function fetchTeamProfile(abbr, season) {
    const response = await axios.get(`${IMPACT_BASE}/team-profile/${encodeURIComponent(abbr)}`, { params: { season } });
    return response.data;
}

// ─── Luck & Schedule ────────────────────────────────────────────
// Every team: record, expected wins from points, luck, close games, SRS/SOS.
// params: { season, as_of } (as_of = 'YYYY-MM-DD': standings, ratings and the schedule left that morning)
export async function fetchLuckSchedule(params) {
    const response = await axios.get(`${IMPACT_BASE}/teams/luck-schedule`, { params });
    return response.data;
}

// The expected-win curves, every team-season's point, and the checks (does luck carry over?).
export async function fetchLuckScheduleModel() {
    const response = await axios.get(`${IMPACT_BASE}/teams/luck-schedule/model`);
    return response.data;
}

// One franchise's luck and SRS in every season.
export async function fetchLuckScheduleTeam(abbr) {
    const response = await axios.get(`${IMPACT_BASE}/teams/luck-schedule/team/${encodeURIComponent(abbr)}`);
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

// ─── Garbage-Time Deflator ───────────────────────────────────────
export async function fetchGarbageTime(season, minPpg = 0, topN = 10) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/garbage-time`,
        { params: { min_ppg: minPpg, top_n: topN, ...(season ? { season } : {}) } }
    );
    return response.data;
}

export async function fetchGarbageTimePlayer(playerId, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/garbage-time/player/${playerId}`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

// ─── DAD Index (Defensive Assignment Difficulty) ─────────────────
export async function fetchDadIndex(season) {
    const response = await axios.get(
        `${IMPACT_BASE}/defense/dad`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

// ─── Scouting Report ("Exploit Guide") ───────────────────────────
export async function fetchScoutingReport(playerName, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/players/scouting-report/${encodeURIComponent(playerName)}`,
        { params: season ? { season } : {} }
    );
    return response.data;
}

// ─── Player profile page ─────────────────────────────────────────

export async function fetchPlayerFullProfile(playerId) {
    const response = await axios.get(`${IMPACT_BASE}/player-profile/${playerId}`);
    return response.data;
}

export async function fetchProfileShotZones(playerId, season) {
    const response = await axios.get(`${IMPACT_BASE}/player-profile/${playerId}/shot-zones`, { params: { season } });
    return response.data;
}

// Name -> { player_id, player_name }, for links that only know a name.
export async function resolvePlayerId(name) {
    const response = await axios.get(`${IMPACT_BASE}/player-profile/resolve`, { params: { name } });
    return response.data;
}

// ─── Game Log + Game Finder (player_game_lines, 2020-21 on) ──────
export async function fetchPlayerGameLog(playerId, season) {
    const response = await axios.get(`${IMPACT_BASE}/games/player-log/${playerId}`, { params: season ? { season } : {} });
    return response.data;
}

export async function fetchGameFinderOptions() {
    return getWithCache('game_finder_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/games/finder/options`);
        return response.data;
    });
}

export async function fetchGameFinderPlayers(q) {
    const response = await axios.get(`${IMPACT_BASE}/games/finder/players`, { params: { q } });
    return response.data;
}

// params: { f: 'pts:gte:30,fga:lt:15', mode, season_from, season_to, team, home, result,
//           min_minutes, player_id, sort, order, one_per_player, limit, offset }
export async function fetchGameFinder(params) {
    const response = await axios.get(`${IMPACT_BASE}/games/finder`, { params });
    return response.data;
}

// ─── Hot Streak Checker (hot_streak_persistence) ─────────────────
export async function fetchHotStreakOptions() {
    return getWithCache('hot_streak_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/games/hot-streak/options`);
        return response.data;
    });
}

// params: { season, stat, window, as_of }
export async function fetchHotStreak(playerId, params) {
    const response = await axios.get(`${IMPACT_BASE}/games/hot-streak/${playerId}`, { params });
    return response.data;
}

// params: { season, stat, window, as_of, direction, limit }
export async function fetchHotStreaks(params) {
    const response = await axios.get(`${IMPACT_BASE}/games/hot-streaks`, { params });
    return response.data;
}

// ─── RAPM (player_rapm, rapm_fits, rapm_lambda_cv, rapm_validation) ───
export async function fetchRapmOptions() {
    return getWithCache('rapm_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/rapm/options`);
        return response.data;
    });
}

// params: { version, season, min_poss, team }
export async function fetchRapm(params) {
    const response = await axios.get(`${IMPACT_BASE}/rapm`, { params });
    return response.data;
}

export async function fetchRapmValidation() {
    const response = await axios.get(`${IMPACT_BASE}/rapm/validation`);
    return response.data;
}

// ─── Situational Splits (player_situational_splits, situational_split_league) ───
export async function fetchSituationalOptions() {
    return getWithCache('situational_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/splits/situational/options`);
        return response.data;
    });
}

// params: { season, split, stat, sort, order, team, limit, offset }
export async function fetchSituationalLeaderboard(params) {
    const response = await axios.get(`${IMPACT_BASE}/splits/situational/leaderboard`, { params });
    return response.data;
}

export async function fetchSituationalPlayer(playerId, season) {
    const response = await axios.get(`${IMPACT_BASE}/splits/situational/player/${playerId}`,
        { params: season ? { season } : {} });
    return response.data;
}

// ─── Gravity Index & Spacing Lab ─────────────────────────────────
export async function fetchGravity(season, topN = 25) {
    const response = await axios.get(
        `${IMPACT_BASE}/spacing/gravity`,
        { params: { top_n: topN, ...(season ? { season } : {}) } }
    );
    return response.data;
}

export async function fetchLineupSpacing(playerIds, season) {
    const response = await axios.get(
        `${IMPACT_BASE}/spacing/lineup`,
        { params: { player_ids: playerIds, ...(season ? { season } : {}) } }
    );
    return response.data;
}

// ─── Contract Value ──────────────────────────────────────────────
export async function fetchContractValue(season, topN = 10) {
    const response = await axios.get(
        `${IMPACT_BASE}/contracts/value`,
        { params: { top_n: topN, ...(season ? { season } : {}) } }
    );
    return response.data;
}

export async function fetchPlayerContractValue(playerId, season) {
    const response = await axios.get(`${IMPACT_BASE}/contracts/player/${playerId}`, { params: { season } });
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

export async function fetchRefereeCrewTendencies(minGames = 1, sort = 'n_games') {
    const response = await axios.get(
        `${IMPACT_BASE}/referees/crew-tendencies`,
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

// ─── Trade Impact (wins + spacing + payroll for one trade) ─────
export async function fetchTradeImpact({ season, teamA, playerAId, teamB, playerBId }) {
    const response = await axios.get(`${IMPACT_BASE}/trade/impact`, {
        params: { season, team_a: teamA, player_a_id: playerAId, team_b: teamB, player_b_id: playerBId },
        timeout: 60 * 1000,
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

export async function fetchLeaderboardOptions() {
    return getWithCache('leaderboard_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/leaderboard/options`);
        return response.data;
    });
}

// params: { stat, season_from, season_to, min_gp, min_mpg, min_attempts, team, order, top_n }
// params: { season, stats: 'pts,ts_pct', direction: 'up'|'down', min_gp, min_mpg, top_n }
export async function fetchBreakouts(params) {
    const response = await axios.get(`${IMPACT_BASE}/explore/breakouts`, { params });
    return response.data;
}

// Stat Stability: sample needed per stat (split-half reliability) + year-to-year r.
export async function fetchStatStability() {
    return getWithCache('stat_stability', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/leaderboard/stability`);
        return response.data;
    });
}

// Data Coverage page: live row counts + season span + source/gaps per table.
export async function fetchDataCoverage() {
    return getWithCache('data_coverage', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/meta/coverage`);
        return response.data;
    });
}

// params: { x, y, season_from, season_to, min_gp, min_mpg, within_season }
export async function fetchRegression(params) {
    const response = await axios.get(`${IMPACT_BASE}/explore/regression`, { params });
    return response.data;
}

// params: { weights: 'pts:1,ts_pct:2', season_from, season_to, min_gp, min_mpg, team, top_n }
export async function fetchCompositeLeaderboard(params) {
    const response = await axios.get(`${IMPACT_BASE}/leaderboard/composite`, { params });
    return response.data;
}

export async function fetchRoleFinderOptions() {
    return getWithCache('role_finder_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/roles/finder/options`);
        return response.data;
    });
}

// params: { preset, weights: 'dad_pos:1,cs_pct:1', season, positions: 'G,F', relative, max_usg, max_salary, top_n }
export async function fetchRoleFinder(params) {
    const response = await axios.get(`${IMPACT_BASE}/roles/finder`, { params });
    return response.data;
}

export async function fetchCustomLeaderboard(params) {
    const response = await axios.get(`${IMPACT_BASE}/leaderboard/custom`, { params });
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

export async function fetchTeamComparisonExtra(teamA, teamB, season) {
    const params = season ? { season } : {};
    return getWithCache(`team_compare_extra_${teamA}_${teamB}_${season || 'current'}`, 90 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/teams/compare/${teamA}/${teamB}`, { params });
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

// ─── Landing page ──────────────────────────────────────────────
export async function fetchSiteStats() {
    const response = await axios.get(`${IMPACT_BASE}/meta/site-stats`);
    return response.data;
}

export async function fetchLearnBasics() {
    const response = await axios.get(`${IMPACT_BASE}/learn/basics`);
    return response.data;
}

export async function fetchLeagueShotSample(n = 6000) {
    const response = await axios.get(`${IMPACT_BASE}/shots/league-sample`, { params: { n } });
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

export async function fetchCurrentNews(date, limit = 20, team = null) {
    const key = `news_current:${date || 'latest'}:${limit}:${team || 'all'}`;
    return getWithCache(key, 120 * 1000, async () => {
        const response = await axios.get(
            `${IMPACT_BASE}/news/current`,
            { params: { ...(date ? { date } : {}), limit, ...(team ? { team } : {}) } }
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

// Stored shots only (no live fetch), so it's a normal quick call.
export async function fetchShotZoneHistory(playerName) {
    const response = await axios.get(
        `${IMPACT_BASE}/shots/player/${encodeURIComponent(playerName)}/zone-history`
    );
    return response.data;
}

// Expected FG% / shot-making (routers/shot_making.py, scripts/build_shot_making.py).
export async function fetchShotMaking(playerName) {
    const response = await axios.get(
        `${IMPACT_BASE}/shots/player/${encodeURIComponent(playerName)}/shot-making`
    );
    return response.data;
}

export async function fetchShotMakingLeaderboard(params = {}) {
    const response = await axios.get(`${IMPACT_BASE}/shots/shot-making/leaderboard`, { params });
    return response.data;
}

export async function fetchShotMakingModel() {
    const response = await axios.get(`${IMPACT_BASE}/shots/shot-making/model`);
    return response.data;
}

export async function fetchHofCareerLeaders(stat, limit = 50) {
    return getWithCache(`hof_career_${stat}_${limit}`, 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/hof/career-leaders`, { params: { stat, limit } });
        return response.data;
    });
}

export async function fetchHofGreatestSeasons(stat, limit = 50, minGp = 50) {
    return getWithCache(`hof_seasons_${stat}_${limit}_${minGp}`, 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/hof/greatest-seasons`, { params: { stat, limit, min_gp: minGp } });
        return response.data;
    });
}

export async function fetchHofLongevity(limit = 50) {
    return getWithCache(`hof_longevity_${limit}`, 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/hof/longevity`, { params: { limit } });
        return response.data;
    });
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

// ─── College → NBA pipeline / March Madness ──────────────────────
export async function fetchCollegePipeline() {
    const response = await axios.get(`${IMPACT_BASE}/college/pipeline`);
    return response.data;
}

export async function fetchMarchMadness(season) {
    const response = await axios.get(`${IMPACT_BASE}/college/madness`, { params: season ? { season } : {} });
    return response.data;
}

// ─── Greats of the Game ──────────────────────────────────────────
export async function fetchGreats() {
    const response = await axios.get(`${IMPACT_BASE}/greats`);
    return response.data;
}

// ─── Era Translator ───────────────────────────────────────────────
export async function fetchEraPlayers(q) {
    const response = await axios.get(`${IMPACT_BASE}/era/players`, { params: { q } });
    return response.data;
}

// params: { player_id, season, target }
export async function fetchEraTranslation(params) {
    const response = await axios.get(`${IMPACT_BASE}/era/translate`, { params });
    return response.data;
}

// ─── Aging Curves ─────────────────────────────────────────────────
// params: { stat, era }
export async function fetchAgingCurves(params) {
    const response = await axios.get(`${IMPACT_BASE}/aging/curves`, { params });
    return response.data;
}

// params: { player_id, stat, era }
export async function fetchAgingPlayer(params) {
    const response = await axios.get(`${IMPACT_BASE}/aging/player`, { params });
    return response.data;
}

// ─── Next-season projections ──────────────────────────────────────
// params: { stat }
export async function fetchProjections(params) {
    const response = await axios.get(`${IMPACT_BASE}/projections`, { params });
    return response.data;
}

export async function fetchProjectionBacktest() {
    return getWithCache('projection_backtest', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/projections/backtest`);
        return response.data;
    });
}

export async function fetchPlayerProjections(playerId) {
    const response = await axios.get(`${IMPACT_BASE}/projections/player/${playerId}`);
    return response.data;
}

// ─── Rotations (lineup_stints, rotation_closing_games/stints) ─────
export async function fetchRotationOptions() {
    return getWithCache('rotation_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/rotations/options`);
        return response.data;
    });
}

export async function fetchRotationGames(team, season) {
    const response = await axios.get(`${IMPACT_BASE}/rotations/games`, { params: { team, season } });
    return response.data;
}

export async function fetchRotationGame(gameId) {
    const response = await axios.get(`${IMPACT_BASE}/rotations/game/${encodeURIComponent(gameId)}`);
    return response.data;
}

export async function fetchTeamRotation(team, season) {
    const response = await axios.get(`${IMPACT_BASE}/rotations/team`, { params: { team, season } });
    return response.data;
}

// ─── Assist network (assist_pairs, player_assisted_share, assist_seasons) ─────
export async function fetchAssistOptions() {
    return getWithCache('assist_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/assists/options`);
        return response.data;
    });
}

export async function fetchTeamAssists(team, season) {
    const response = await axios.get(`${IMPACT_BASE}/assists/team`, { params: { team, season } });
    return response.data;
}

export async function fetchAssistPairs(season, sort = 'ast', limit = 50) {
    const response = await axios.get(`${IMPACT_BASE}/assists/pairs`, { params: { season, sort, limit } });
    return response.data;
}

export async function fetchPlayerAssists(playerId, season) {
    const response = await axios.get(`${IMPACT_BASE}/assists/player/${playerId}`, { params: season ? { season } : {} });
    return response.data;
}

// ─── Season Simulator (game_pregame_odds, season_sim_*; 2010-11 on) ────
export async function fetchSeasonSimOptions() {
    return getWithCache('season_sim_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/season-sim/options`);
        return response.data;
    });
}

// params: { season, as_of: 'YYYY-MM-DD' } — 10,000 simulated seasons from that morning.
export async function fetchSeasonSim(params) {
    const response = await axios.get(`${IMPACT_BASE}/season-sim`, { params });
    return response.data;
}

export async function fetchSeasonSimModel() {
    return getWithCache('season_sim_model', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/season-sim/model`);
        return response.data;
    });
}

// ─── Forecast Ledger (ledger_*; the 2026-27 preseason lock) ────────────
export async function fetchLedgerPreseason(season) {
    const response = await axios.get(`${IMPACT_BASE}/ledger/preseason`, { params: season ? { season } : {} });
    return response.data;
}

export async function fetchLedgerGames(params) {
    const response = await axios.get(`${IMPACT_BASE}/ledger/games`, { params });
    return response.data;
}

export async function fetchLedgerRoster(team, season) {
    const response = await axios.get(`${IMPACT_BASE}/ledger/roster/${team}`, { params: season ? { season } : {} });
    return response.data;
}

export async function fetchLedgerHindcast(season) {
    const response = await axios.get(`${IMPACT_BASE}/ledger/hindcast`, { params: season ? { season } : {} });
    return response.data;
}

// Live scoring (ledger_results / ledger_game_log / ...; appended nightly by scripts/ledger_update.py, never cached).
export async function fetchLedgerLive(season) {
    const response = await axios.get(`${IMPACT_BASE}/ledger/live`, { params: season ? { season } : {} });
    return response.data;
}

export async function fetchLedgerLiveGames(params) {
    const response = await axios.get(`${IMPACT_BASE}/ledger/live/games`, { params });
    return response.data;
}

// The canonical CSV the lock's SHA-256 is taken of (a plain download link).
export function ledgerCsvUrl(season) {
    return `${IMPACT_BASE}/ledger/lock.csv${season ? `?season=${season}` : ''}`;
}

// ─── Play Finder (play_finder_events, 2020-21 on) ────────────────
export async function fetchPlayFinderOptions() {
    return getWithCache('play_finder_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/plays/finder/options`);
        return response.data;
    });
}

// params: { player_id, cat: 'made3,ast', season_from, season_to, date_from, date_to, game, team, opp, home,
//           period: '4,ot', clock_min, clock_max, margin_min, margin_max, clutch, dist_min, dist_max,
//           sort, limit, offset }
export async function fetchPlayFinder(params) {
    const response = await axios.get(`${IMPACT_BASE}/plays/finder`, { params });
    return response.data;
}

// ─── Best Games & Upsets (best_games 2020-21 on; game_pregame_odds 2010-11 on) ────
export async function fetchBestGamesOptions() {
    return getWithCache('best_games_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/best-games/options`);
        return response.data;
    });
}

// params: { season, team, sort: 'excitement'|'swing'|'comeback'|'lead_changes'|'close'|'overtime'|'newest',
//           ot, limit, offset }
export async function fetchBestGames(params) {
    const response = await axios.get(`${IMPACT_BASE}/best-games`, { params });
    return response.data;
}

// params: { season, team, side: 'won'|'lost' (needs team), min_games, sort: 'chance'|'newest'|'margin', limit, offset }
export async function fetchUpsets(params) {
    const response = await axios.get(`${IMPACT_BASE}/upsets`, { params });
    return response.data;
}

// ─── Shot quality map (player_shot_hex, shot_hex_league; regular season, 200+ FGA) ────
// One player-season on the 2-foot hexagon grid next to the league's; season defaults to the latest.
export async function fetchQualityMap(player, season) {
    const response = await axios.get(`${IMPACT_BASE}/shots/quality-map`, { params: { player, ...(season ? { season } : {}) } });
    return response.data;
}

// Possession Explorer (GET /possessions/*): points per possession by how the possession began.
export async function fetchPossessionOptions() {
    return getWithCache('possession_options', 10 * 60 * 1000, async () => {
        const response = await axios.get(`${IMPACT_BASE}/possessions/options`);
        return response.data;
    });
}

export async function fetchPossessionLeague(season) {
    const response = await axios.get(`${IMPACT_BASE}/possessions/league`, { params: season ? { season } : {} });
    return response.data;
}

export async function fetchPossessionTeam(abbr) {
    const response = await axios.get(`${IMPACT_BASE}/possessions/team/${encodeURIComponent(abbr)}`);
    return response.data;
}

export async function fetchPlayerPossessions(playerId, season) {
    const response = await axios.get(`${IMPACT_BASE}/possessions/player/${playerId}`, { params: season ? { season } : {} });
    return response.data;
}
