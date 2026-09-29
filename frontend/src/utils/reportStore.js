// Reports, kept in this browser only. Snapshots hold whole SVG charts and
// table rows, which can pass localStorage's ~5 MB, so they live in IndexedDB
// (one record per report, its items inside it). Nothing is sent to a server.
//
// Every function that touches storage returns a promise that rejects with a
// plain Error when the browser refuses (private window, blocked site data,
// full disk); callers show that message instead of failing silently. The one
// small pointer kept in localStorage (which report new items go to) is wrapped
// in try/catch like utils/watchlist.js and utils/savedViews.js.
//
// Item shapes ('chart' | 'table' | 'text'):
//   { id, type, caption, added, source: { url, page, title },
//     svg: { markup, width, height, bg }      // chart
//     table: { header: [...], rows: [[...]] } // table
//     body }                                  // text (caption = optional heading)

import { useEffect, useState } from 'react';

const DB_NAME = 'nba-hub-reports';
const STORE = 'reports';
const CHANGE_EVENT = 'nba-hub-reports-change';
const ACTIVE_KEY = 'nba-hub-active-report';
export const EXPORT_FORMAT = 'nba-hub-reports';

const STORAGE_MESSAGE = 'This browser is not letting the app store reports (a private window or blocked site data). Nothing was saved.';

// ── IndexedDB plumbing ─────────────────────────────────────────────────

let dbPromise = null;

function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') {
            reject(new Error(STORAGE_MESSAGE));
            return;
        }
        let req;
        try {
            req = indexedDB.open(DB_NAME, 1);
        } catch {
            reject(new Error(STORAGE_MESSAGE));
            return;
        }
        req.onupgradeneeded = () => {
            if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE, { keyPath: 'id' });
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(new Error(STORAGE_MESSAGE));
        req.onblocked = () => reject(new Error(STORAGE_MESSAGE));
    }).catch((e) => {
        dbPromise = null; // let a later call try again
        throw e;
    });
    return dbPromise;
}

// Runs `work(store)` in one transaction and resolves with what `work`
// returns once the transaction has committed (so a quota error surfaces
// here rather than after we've told the user it worked).
async function withStore(mode, work) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
        let result;
        let tx;
        try {
            tx = db.transaction(STORE, mode);
        } catch {
            reject(new Error(STORAGE_MESSAGE));
            return;
        }
        tx.oncomplete = () => resolve(result);
        tx.onerror = () => reject(new Error(STORAGE_MESSAGE));
        tx.onabort = () => reject(new Error(
            tx.error && tx.error.name === 'QuotaExceededError'
                ? 'This browser\'s storage is full, so the report was not saved. Delete a report or an item and try again.'
                : STORAGE_MESSAGE,
        ));
        try {
            result = work(tx.objectStore(STORE), (v) => { result = v; });
        } catch {
            tx.abort();
        }
    });
}

// ── Change notification (this tab + other tabs) ────────────────────────

let channel = null;
try {
    channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel(CHANGE_EVENT) : null;
} catch {
    channel = null;
}

function announce() {
    window.dispatchEvent(new Event(CHANGE_EVENT));
    try { channel?.postMessage('change'); } catch { /* ignore */ }
}

if (channel) channel.onmessage = () => window.dispatchEvent(new Event(CHANGE_EVENT));

export function subscribeReports(callback) {
    window.addEventListener(CHANGE_EVENT, callback);
    return () => window.removeEventListener(CHANGE_EVENT, callback);
}

// ── Small helpers ──────────────────────────────────────────────────────

function makeId() {
    try {
        return crypto.randomUUID();
    } catch {
        return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    }
}

const nowIso = () => new Date().toISOString();
const str = (v, max) => (typeof v === 'string' ? v.slice(0, max) : '');

export function newTextItem(caption = '', body = '') {
    return { id: makeId(), type: 'text', caption, body, added: nowIso(), source: null };
}

// ── Reading ────────────────────────────────────────────────────────────

// Oldest first: a new report goes to the end of the picker.
export async function listReports() {
    const all = await withStore('readonly', (store, done) => {
        const r = store.getAll();
        r.onsuccess = () => done(r.result || []);
    });
    return (all || []).sort((a, b) => String(a.created).localeCompare(String(b.created)));
}

// ── Active report (where "Add to report" puts things) ──────────────────

export function getActiveReportId() {
    try {
        return localStorage.getItem(ACTIVE_KEY);
    } catch {
        return null;
    }
}

export function setActiveReportId(id) {
    try {
        if (id) localStorage.setItem(ACTIVE_KEY, id);
        else localStorage.removeItem(ACTIVE_KEY);
    } catch {
        // ignore — private browsing / blocked storage
    }
    window.dispatchEvent(new Event(CHANGE_EVENT));
}

// ── Writing ────────────────────────────────────────────────────────────

// Reads the latest copy of a report inside the write transaction (not a
// cached copy), so two tabs editing the same report don't overwrite each
// other's items.
async function mutate(reportId, change) {
    let updated = null;
    await withStore('readwrite', (store) => {
        const r = store.get(reportId);
        r.onsuccess = () => {
            const current = r.result;
            if (!current) return;
            updated = change(structuredClone(current));
            if (updated) {
                updated.updated = nowIso();
                store.put(updated);
            }
        };
    });
    announce();
    return updated;
}

export async function createReport(name = 'Untitled report') {
    const report = { id: makeId(), name: str(name, 120).trim() || 'Untitled report', created: nowIso(), updated: nowIso(), items: [] };
    await withStore('readwrite', (store) => { store.put(report); });
    announce();
    return report;
}

export const renameReport = (id, name) => mutate(id, (r) => {
    r.name = str(name, 120).trim() || r.name;
    return r;
});

export async function deleteReport(id) {
    await withStore('readwrite', (store) => { store.delete(id); });
    if (getActiveReportId() === id) setActiveReportId(null);
    announce();
}

// Adds a chart/table snapshot to the report new items are collected in,
// creating "My report" the first time (or when the active one was deleted).
export async function addToActiveReport(item) {
    const reports = await listReports();
    let report = reports.find((r) => r.id === getActiveReportId());
    if (!report) {
        report = reports[0] || await createReport('My report');
        setActiveReportId(report.id);
    }
    const stored = { ...item, id: makeId(), added: nowIso() };
    const updated = await mutate(report.id, (r) => {
        r.items.push(stored);
        return r;
    });
    if (!updated) throw new Error('That report no longer exists.');
    return updated;
}

export const updateItem = (reportId, itemId, patch) => mutate(reportId, (r) => {
    r.items = r.items.map((it) => (it.id === itemId ? { ...it, ...patch } : it));
    return r;
});

export const removeItem = (reportId, itemId) => mutate(reportId, (r) => {
    r.items = r.items.filter((it) => it.id !== itemId);
    return r;
});

// Puts an item back (undo) at `index`, or appends when `index` is null.
export const insertItem = (reportId, item, index = null) => mutate(reportId, (r) => {
    const at = index == null ? r.items.length : Math.max(0, Math.min(index, r.items.length));
    r.items.splice(at, 0, item);
    return r;
});

export const moveItem = (reportId, itemId, direction) => mutate(reportId, (r) => {
    const i = r.items.findIndex((it) => it.id === itemId);
    const j = i + (direction === 'up' ? -1 : 1);
    if (i < 0 || j < 0 || j >= r.items.length) return r;
    [r.items[i], r.items[j]] = [r.items[j], r.items[i]];
    return r;
});

// ── Export / import ────────────────────────────────────────────────────

export function exportReportsJson(reports) {
    return JSON.stringify({ format: EXPORT_FORMAT, version: 1, exported: nowIso(), reports }, null, 2);
}

const COLOR = /^(#[0-9a-f]{3,8}|rgba?\([\d\s.,%/]+\))$/i;
const LIMITS = { markup: 6_000_000, rows: 20_000, cols: 120, cell: 2_000, body: 40_000, caption: 400 };

// A same-app link only: "/?page=…" (a relative path). Anything else in an
// imported file — another host, javascript: — is dropped, so a report someone
// hands you can't send "Open live view" somewhere else.
function cleanUrl(u) {
    return typeof u === 'string' && u.startsWith('/') && !u.startsWith('//') ? u.slice(0, 2000) : '/';
}

function cleanSource(s) {
    if (!s || typeof s !== 'object') return { url: '/', page: '', title: '' };
    return { url: cleanUrl(s.url), page: str(s.page, 120), title: str(s.title, 200) };
}

// Returns a well-formed item, or null if the raw one isn't usable. Chart
// markup is only ever shown as an <img> (scripts never run in one), but it is
// still checked to be an <svg> string of sane size.
export function cleanItem(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const base = {
        id: makeId(),
        caption: str(raw.caption, LIMITS.caption),
        added: typeof raw.added === 'string' && !Number.isNaN(Date.parse(raw.added)) ? raw.added : nowIso(),
        source: cleanSource(raw.source),
    };
    if (raw.type === 'text') return { ...base, type: 'text', body: str(raw.body, LIMITS.body), source: null };
    if (raw.type === 'chart') {
        const s = raw.svg;
        if (!s || typeof s.markup !== 'string' || !/^\s*(<\?xml[^>]*>\s*)?<svg[\s>]/i.test(s.markup) || s.markup.length > LIMITS.markup) return null;
        const w = Number(s.width);
        const h = Number(s.height);
        return {
            ...base,
            type: 'chart',
            svg: {
                markup: s.markup,
                width: Number.isFinite(w) && w > 0 ? Math.min(w, 4000) : 600,
                height: Number.isFinite(h) && h > 0 ? Math.min(h, 4000) : 400,
                bg: typeof s.bg === 'string' && COLOR.test(s.bg.trim()) ? s.bg.trim() : '#ffffff',
            },
        };
    }
    if (raw.type === 'table') {
        const t = raw.table;
        if (!t || !Array.isArray(t.header) || !Array.isArray(t.rows)) return null;
        const header = t.header.slice(0, LIMITS.cols).map((c) => str(String(c ?? ''), LIMITS.cell));
        const rows = t.rows.slice(0, LIMITS.rows).map((r) => (Array.isArray(r) ? r : []).slice(0, header.length).map((c) => str(String(c ?? ''), LIMITS.cell)));
        if (!header.length) return null;
        return { ...base, type: 'table', table: { header, rows } };
    }
    return null;
}

// Merges an exported file into what's stored. Reports are matched by id, so
// importing the same file twice adds nothing the second time. Returns how
// many reports were added, how many were skipped, and how many items were
// dropped as unusable.
export async function importReportsJson(text) {
    let parsed;
    try {
        parsed = JSON.parse(text);
    } catch {
        throw new Error('That file is not JSON exported from this page.');
    }
    if (!parsed || parsed.format !== EXPORT_FORMAT || !Array.isArray(parsed.reports)) {
        throw new Error('That file is not a report export from NBA Hub.');
    }
    const existing = new Set((await listReports()).map((r) => r.id));
    let added = 0;
    let skipped = 0;
    let droppedItems = 0;
    let firstAdded = null;
    for (const raw of parsed.reports) {
        if (!raw || typeof raw.id !== 'string' || existing.has(raw.id)) {
            skipped += 1;
            continue;
        }
        const items = (Array.isArray(raw.items) ? raw.items : []).map(cleanItem);
        droppedItems += items.filter((i) => !i).length;
        // Keeps the original id, so a second import is recognised as a repeat.
        const report = {
            id: raw.id.slice(0, 80),
            name: str(raw.name, 120).trim() || 'Imported report',
            created: typeof raw.created === 'string' && !Number.isNaN(Date.parse(raw.created)) ? raw.created : nowIso(),
            updated: nowIso(),
            items: items.filter(Boolean),
        };
        await withStore('readwrite', (store) => { store.put(report); });
        existing.add(report.id);
        firstAdded = firstAdded || report.id;
        added += 1;
    }
    announce();
    return { added, skipped, droppedItems, firstAdded };
}

// ── React hook ─────────────────────────────────────────────────────────

// { reports, loaded, error }: reloads whenever this tab or another one
// changes anything.
export function useReports() {
    const [state, setState] = useState({ reports: [], loaded: false, error: '' });

    useEffect(() => {
        let alive = true;
        const load = () => listReports().then(
            (reports) => alive && setState({ reports, loaded: true, error: '' }),
            (e) => alive && setState({ reports: [], loaded: true, error: e.message }),
        );
        load();
        const unsubscribe = subscribeReports(load);
        return () => { alive = false; unsubscribe(); };
    }, []);

    return state;
}
