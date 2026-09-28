// Saved analyses, kept in this browser only (localStorage — nothing is sent
// to the server). Wrapped in try/catch like utils/watchlist.js: private
// browsing or blocked storage should degrade to "nothing saved", not an
// error.
const STORAGE_KEY = 'nba-hub-saved-views';
const CHANGE_EVENT = 'nba-hub-saved-views-change';

function isView(v) {
    return v && typeof v.id === 'string' && typeof v.url === 'string' && typeof v.title === 'string';
}

function readStorage() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        const list = raw ? JSON.parse(raw) : [];
        return Array.isArray(list) ? list.filter(isView) : [];
    } catch {
        return [];
    }
}

// A stable reference, only replaced when the list actually changes — so
// useSyncExternalStore (the Saved page) doesn't re-render on every
// unrelated render. Same pattern as utils/watchlist.js.
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

function makeId() {
    try {
        return crypto.randomUUID();
    } catch {
        return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    }
}

export function getSavedViews() {
    return cache;
}

export function findSavedViewByUrl(url) {
    return cache.find((v) => v.url === url) || null;
}

// Adds a new saved view (or returns the existing one if this exact URL is
// already saved, rather than creating a duplicate). `page` is the page id,
// `title` is auto-filled by the caller from the page label and its current
// inputs.
export function addSavedView({ url, page, title, note = '' }) {
    const existing = findSavedViewByUrl(url);
    if (existing) return existing;
    const view = { id: makeId(), url, page, title, note, date: new Date().toISOString() };
    writeStorage([view, ...cache]);
    return view;
}

export function updateSavedView(id, patch) {
    writeStorage(cache.map((v) => (v.id === id ? { ...v, ...patch } : v)));
}

export function deleteSavedView(id) {
    writeStorage(cache.filter((v) => v.id !== id));
}

export function subscribeSavedViews(callback) {
    window.addEventListener(CHANGE_EVENT, callback);
    return () => window.removeEventListener(CHANGE_EVENT, callback);
}

export function exportSavedViews() {
    return JSON.stringify(cache, null, 2);
}

// Merges parsed JSON (an array of saved-view-shaped objects, e.g. from
// exportSavedViews on another device) into the current list. Entries are
// matched by URL, not id, so re-importing the same export twice doesn't
// duplicate anything; a title/note edited locally since is kept. Returns
// how many were added vs. already present.
export function importSavedViews(parsed) {
    if (!Array.isArray(parsed)) throw new Error('Expected a JSON array of saved views.');
    let added = 0;
    let skipped = 0;
    const next = [...cache];
    for (const raw of parsed) {
        if (!raw || typeof raw.url !== 'string' || typeof raw.title !== 'string') {
            skipped += 1;
            continue;
        }
        if (next.some((v) => v.url === raw.url)) {
            skipped += 1;
            continue;
        }
        next.unshift({
            id: makeId(),
            url: raw.url,
            page: typeof raw.page === 'string' ? raw.page : null,
            title: raw.title,
            note: typeof raw.note === 'string' ? raw.note : '',
            date: typeof raw.date === 'string' ? raw.date : new Date().toISOString(),
        });
        added += 1;
    }
    writeStorage(next);
    return { added, skipped };
}
