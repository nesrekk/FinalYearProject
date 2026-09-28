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
