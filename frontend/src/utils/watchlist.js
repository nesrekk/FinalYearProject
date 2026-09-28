// Starred players, kept in this browser only (localStorage — nothing is sent
// to the server). Wrapped in try/catch like utils/theme.js: private browsing
// or blocked storage should degrade to "nothing starred", not an error.
const STORAGE_KEY = 'nba-hub-watchlist';
const CHANGE_EVENT = 'nba-hub-watchlist-change';

function readStorage() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        const list = raw ? JSON.parse(raw) : [];
        return Array.isArray(list) ? list.filter((p) => p && p.playerId != null && p.name) : [];
    } catch {
        return [];
    }
}

// A stable reference, only replaced when the list actually changes — so
// useSyncExternalStore (PlayerName's star, the Watchlist page) doesn't see a
// "new" snapshot, and re-render, on every unrelated render.
let cache = readStorage();

function writeStorage(list) {
    cache = list;
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
    } catch {
        // ignore — private browsing / blocked storage
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function getWatchlist() {
    return cache;
}

export function isWatched(playerId) {
    if (playerId == null) return false;
    return cache.some((p) => String(p.playerId) === String(playerId));
}

// Adds/removes `playerId` and returns the new starred state.
export function toggleWatchlist(playerId, name) {
    const already = cache.some((p) => String(p.playerId) === String(playerId));
    if (already) {
        writeStorage(cache.filter((p) => String(p.playerId) !== String(playerId)));
        return false;
    }
    writeStorage([...cache, { playerId, name }]);
    return true;
}

// Re-renders whatever's showing the watchlist (star buttons, the Watchlist
// page) when it changes anywhere — including another star button for the
// same player elsewhere on the page.
export function subscribeWatchlist(callback) {
    window.addEventListener(CHANGE_EVENT, callback);
    return () => window.removeEventListener(CHANGE_EVENT, callback);
}
