// The app's current season (round 9 step 5, R9-011): one rule, decided by the server (api/current_season.py,
// GET /meta/season) and read here once before the app renders (main.jsx), so every season picker can open on it
// synchronously. Until the first regular-season final of a new season is stored the app opens on the newest
// complete season; from opening night on the season being played, with "through <date>" and early-season
// warnings (LiveSeasonNote) wherever it is shown.
//
// Read it at render time (currentSeason(), seasonInfo()), never into a module-level constant: modules are
// evaluated before the answer arrives.

const URL = 'http://localhost:8002/meta/season';
const STORE_KEY = 'nbahub_season_info_v1';
const TIMEOUT_MS = 1500;
// The fallback if the server doesn't answer in time and nothing was stored before: the newest complete season
// on 2026-10-07 (the paper's test season).
const FALLBACK = { current: 2026, latest_complete: 2026, live: null, upcoming: null, early: [] };

let INFO = FALLBACK;

function readStored() {
    try {
        const raw = localStorage.getItem(STORE_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}

export async function loadSeasonInfo() {
    const stored = readStored();
    if (stored && Number.isInteger(stored.current)) INFO = stored;
    try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
        const res = await fetch(URL, { signal: ctrl.signal });
        clearTimeout(timer);
        if (!res.ok) return INFO;
        const body = await res.json();
        if (Number.isInteger(body?.current)) {
            const { _source, ...info } = body;
            INFO = info;
            try { localStorage.setItem(STORE_KEY, JSON.stringify(info)); } catch { /* private mode */ }
        }
    } catch {
        // the server is down or slow: keep the stored answer or the fallback
    }
    return INFO;
}

/** The season pickers' default: the live season once it has a final stored, else the newest complete one. */
export function currentSeason() {
    return INFO.current;
}

/** The newest season whose regular season is over (the default for things that need a whole season). */
export function latestCompleteSeason() {
    return INFO.latest_complete;
}

/** The whole answer: {current, latest_complete, live: {season, through, games, scheduled, ...} | null, upcoming, early}. */
export function seasonInfo() {
    return INFO;
}

/** True when `season` (an end year) is the season being played right now. */
export function isLiveSeason(season) {
    return !!INFO.live && Number(season) === INFO.live.season;
}

/** For a tool with a games floor: the current season once its teams have played `minGames` games, else the newest
 *  complete one (a 20-game floor finds nobody in a three-game season). */
export function defaultSeasonFor(minGames) {
    const live = INFO.live;
    if (live && (live.team_games_max ?? 0) < minGames) return INFO.latest_complete;
    return INFO.current;
}

/** [newest, …, from]: the seasons a picker offers, newest first (newest = the current season unless given). */
export function seasonRange(from, to) {
    const out = [];
    for (let s = to ?? INFO.current; s >= from; s--) out.push(s);
    return out;
}

/** "Oct 22" for an ISO date (the dates are the NBA's US calendar dates, so no time zone shift). */
export function shortDate(iso) {
    if (!iso) return '';
    const d = new Date(`${iso}T12:00:00Z`);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
}
