import { useEffect, useState } from 'react';

// Shareable links. The open page lives in `?page=<id>`; a tool's inputs are
// extra query params next to it (`?page=builder&stat=pts&from=2016`), and
// Analytics tabs keep their `#hash` (`?page=analytics#wpa`).
//
// Page changes push a history entry (so Back works); input changes replace
// the current one (so Back doesn't step through every slider move).

export const PAGE_PARAM = 'page';

const here = () => window.location.pathname + window.location.search + window.location.hash;

export function currentPageParam() {
    return new URLSearchParams(window.location.search).get(PAGE_PARAM);
}

// Go to a page: a fresh URL with only `page` (and the Analytics tab hash),
// so one tool's inputs never leak into another's link. No page = landing.
// `params` ({ key: value }) pre-fills the target page's inputs, for the
// few cases where one tool hands its inputs to another.
export function pushPage(page, hash, params) {
    const search = new URLSearchParams();
    if (page) search.set(PAGE_PARAM, page);
    for (const [k, v] of Object.entries(params || {})) {
        const p = toParam(v);
        if (p != null) search.set(k, p);
    }
    const qs = search.toString() ? `?${search.toString().replace(/%2C/gi, ',').replace(/%3A/gi, ':')}` : '';
    const url = `${window.location.pathname}${qs}${hash ? `#${hash}` : ''}`;
    if (url !== here()) window.history.pushState(null, '', url);
    // pushState doesn't fire hashchange; AnalyticsSection listens for it
    // when it's already open and only the tab changes.
    if (hash) window.dispatchEvent(new Event('hashchange'));
}

// Opening a page that needs its own query params (a player's profile,
// `?page=player&id=203999`) from anywhere, including components that don't
// get onNavigate. App re-reads the URL on this event, as on Back/Forward.
export const NAVIGATE_EVENT = 'nbahub:navigate';

export function pageHref(page, params = {}) {
    return `${window.location.pathname}?${new URLSearchParams({ [PAGE_PARAM]: page, ...params })}`;
}

export function openPage(page, params) {
    const url = pageHref(page, params);
    if (url !== here()) window.history.pushState(null, '', url);
    window.dispatchEvent(new Event(NAVIGATE_EVENT));
}

export const playerProfileHref = (playerId) => pageHref('player', { id: playerId });
export const openPlayerProfile = (playerId) => openPage('player', { id: playerId });
// A team's page (?page=team&abbr=BOS&season=2024); no season = its latest.
const teamParams = (abbr, season) => (season ? { abbr, season } : { abbr });
export const teamProfileHref = (abbr, season) => pageHref('team', teamParams(abbr, season));
export const openTeamProfile = (abbr, season) => openPage('team', teamParams(abbr, season));

// Opens a full saved URL (pathname + search + hash), e.g. from
// utils/savedViews.js — unlike openPage(), it doesn't rebuild the query
// string, so every saved input comes back exactly as it was.
export function openFullUrl(url) {
    if (url !== here()) window.history.pushState(null, '', url);
    window.dispatchEvent(new Event(NAVIGATE_EVENT));
}

// A plain left click (no modifier keys) is handled in-app; cmd/ctrl/shift
// and middle clicks keep the browser's own behaviour (open in a new tab).
export function isPlainClick(e) {
    return e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey;
}

// The query string as it was when the component mounted. Read inputs from
// it once, when the component sets up its initial state.
export function useInitialParams() {
    const [params] = useState(() => new URLSearchParams(window.location.search));
    return params;
}

function toParam(v) {
    if (v == null || v === '' || (Array.isArray(v) && v.length === 0)) return null;
    return Array.isArray(v) ? v.join(',') : String(v);
}

// Mirror `values` ({ key: value }) into the query string. null, '' or an
// empty array removes the key; arrays are comma-joined. Pass null to write
// nothing (e.g. while options are still loading). Writes stop once the URL
// belongs to another page, so a page that is animating out can't write its
// inputs onto the next page's link.
export function useUrlSync(values) {
    const [page] = useState(currentPageParam);
    const serialized = values == null
        ? null
        : JSON.stringify(Object.entries(values).map(([k, v]) => [k, toParam(v)]));

    useEffect(() => {
        if (serialized == null) return;
        const url = new URL(window.location.href);
        if (url.searchParams.get(PAGE_PARAM) !== page) return;
        for (const [k, v] of JSON.parse(serialized)) {
            if (v == null) url.searchParams.delete(k);
            else url.searchParams.set(k, v);
        }
        // Commas and colons are legal in a query string; leaving them
        // unescaped keeps links readable (w=pts:1,ts_pct:2).
        const search = url.search.replace(/%2C/gi, ',').replace(/%3A/gi, ':');
        const next = url.pathname + search + url.hash;
        if (next !== here()) window.history.replaceState(window.history.state, '', next);
    }, [page, serialized]);
}

// Parsers for values read back from a link. Each returns null when the
// value is missing or not usable, so callers fall back to their default.
export const parseParam = {
    int(params, key, { min = -Infinity, max = Infinity } = {}) {
        const raw = params.get(key);
        if (raw == null || raw === '') return null;
        const n = Number(raw);
        return Number.isInteger(n) && n >= min && n <= max ? n : null;
    },
    num(params, key, { min = -Infinity, max = Infinity } = {}) {
        const raw = params.get(key);
        if (raw == null || raw === '') return null;
        const n = Number(raw);
        return Number.isFinite(n) && n >= min && n <= max ? n : null;
    },
    oneOf(params, key, allowed) {
        const raw = params.get(key);
        return raw != null && allowed.includes(raw) ? raw : null;
    },
    str(params, key) {
        const raw = params.get(key);
        return raw && raw.trim() ? raw.trim() : null;
    },
    list(params, key) {
        const raw = params.get(key);
        return raw ? raw.split(',').map((s) => s.trim()).filter(Boolean) : null;
    },
};
