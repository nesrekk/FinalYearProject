// Workbench boards, kept in this browser only, like utils/reportStore.js:
// IndexedDB (one record per board), BroadcastChannel so two tabs see each
// other's changes, and every write re-reads the stored board inside its own
// transaction so two tabs editing one board don't overwrite each other.
// Nothing is sent to a server; a board is a description (which blocks, which
// players, which stats), and every block fetches its numbers live.
//
// Board shape:
//   { id, name, created, updated, version: 1,
//     sets:   [{ id, name, kind: 'player' | 'team',
//                members: [{ id, name, color, team? }] }]   // color = palette index 0-7
//     blocks: [{ id, type: 'set' | 'table' | 'chart' | 'note' | 'tool' | 'finder', title,
//                x, y, w, h,                                 // grid cells (12 columns)
//                settings: { … per type, see cleanSettings } }] }
//
// Anything read back from a file or a share link goes through cleanBoard(),
// which rebuilds the board field by field (like reportStore.cleanItem), so a
// hand-edited file can't put markup, foreign links or odd types on the page.

import { useEffect, useState } from 'react';
import { GRID_COLS, MAX_H, MIN_H, compact } from './workbenchLayout';
import { SHOT_GAMES, TOOL_KEYS, TOOL_VIEWS } from './workbenchTools';

const DB_NAME = 'nba-hub-workbench';
const STORE = 'boards';
const CHANGE_EVENT = 'nba-hub-workbench-change';
export const EXPORT_FORMAT = 'nba-hub-workbench';
export const SHARE_PARAM = 'share';

export const PALETTE_SIZE = 8;
export const BLOCK_TYPES = ['set', 'table', 'chart', 'note', 'tool', 'finder'];
export const LIMITS = { boards: 200, blocks: 40, sets: 20, members: 500, columns: 40, note: 20000, name: 120, title: 80 };
// A share link carries the whole board; past this many characters it goes
// into a file instead (chat apps and some mail clients cut longer links).
export const MAX_SHARE_CHARS = 2000;

const STORAGE_MESSAGE = 'This browser is not letting the app store boards (a private window or blocked site data). Nothing was saved.';

// ── IndexedDB plumbing (same pattern as reportStore.js) ────────────────

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
        dbPromise = null;
        throw e;
    });
    return dbPromise;
}

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
                ? 'This browser\'s storage is full, so the board was not saved. Delete a board and try again.'
                : STORAGE_MESSAGE,
        ));
        try {
            result = work(tx.objectStore(STORE), (v) => { result = v; });
        } catch {
            tx.abort();
        }
    });
}

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

export function subscribeBoards(callback) {
    window.addEventListener(CHANGE_EVENT, callback);
    return () => window.removeEventListener(CHANGE_EVENT, callback);
}

// ── Small helpers ──────────────────────────────────────────────────────

export function makeId() {
    try {
        return crypto.randomUUID();
    } catch {
        return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    }
}

const nowIso = () => new Date().toISOString();
const str = (v, max) => (typeof v === 'string' ? v.slice(0, max) : '');
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const KEY_RE = /^[a-z][a-z0-9_]{0,39}$/;
const TEAM_RE = /^[A-Z]{2,4}$/;
const isInt = (v) => Number.isInteger(v);
const clampInt = (v, lo, hi, fallback) => (isInt(v) ? Math.max(lo, Math.min(hi, v)) : fallback);
const dateOk = (v) => typeof v === 'string' && !Number.isNaN(Date.parse(v));

// ── Reading ────────────────────────────────────────────────────────────

export async function listBoards() {
    const all = await withStore('readonly', (store, done) => {
        const r = store.getAll();
        r.onsuccess = () => done(r.result || []);
    });
    return (all || []).sort((a, b) => String(a.created).localeCompare(String(b.created)));
}

// ── Writing ────────────────────────────────────────────────────────────

// `change` gets a copy of the stored board and returns the new one (or null
// to leave it). Layout is re-compacted on every write, so no two blocks can
// ever be stored on top of each other.
// A `change` that throws (e.g. "a board holds up to 40 blocks") writes
// nothing, and its error is what the caller gets. `priorityId` names a block
// that keeps its spot when blocks collide (one being put back by Undo).
export async function updateBoard(boardId, change, priorityId = null) {
    let updated = null;
    let failure = null;
    await withStore('readwrite', (store) => {
        const r = store.get(boardId);
        r.onsuccess = () => {
            const current = r.result;
            if (!current) return;
            try {
                updated = change(structuredClone(current));
            } catch (e) {
                failure = e;
                updated = null;
                return;
            }
            if (updated) {
                updated.blocks = compact(updated.blocks, priorityId);
                updated.updated = nowIso();
                store.put(updated);
            }
        };
    });
    if (failure) throw failure;
    announce();
    return updated;
}

export function emptyBoard(name = 'Untitled board') {
    return { id: makeId(), name: str(name, LIMITS.name).trim() || 'Untitled board', created: nowIso(), updated: nowIso(), version: 1, sets: [], blocks: [] };
}

export async function saveNewBoard(board) {
    const count = (await listBoards()).length;
    if (count >= LIMITS.boards) throw new Error(`This browser already holds ${LIMITS.boards} boards. Delete one first.`);
    await withStore('readwrite', (store) => { store.put(board); });
    announce();
    return board;
}

export const createBoard = (name) => saveNewBoard(emptyBoard(name));

export const renameBoard = (id, name) => updateBoard(id, (b) => {
    b.name = str(name, LIMITS.name).trim() || b.name;
    return b;
});

export async function deleteBoard(id) {
    await withStore('readwrite', (store) => { store.delete(id); });
    announce();
}

// ── Validation ─────────────────────────────────────────────────────────

function cleanMember(raw, kind) {
    if (!raw || typeof raw !== 'object') return null;
    const color = clampInt(raw.color, 0, PALETTE_SIZE - 1, 0);
    if (kind === 'player') {
        if (!isInt(raw.id) || raw.id <= 0 || raw.id > 1e9) return null;
        const team = typeof raw.team === 'string' && TEAM_RE.test(raw.team) ? raw.team : null;
        return { id: raw.id, name: str(raw.name, 80) || `Player ${raw.id}`, color, ...(team ? { team } : {}) };
    }
    if (typeof raw.id !== 'string' || !TEAM_RE.test(raw.id)) return null;
    return { id: raw.id, name: str(raw.name, 80) || raw.id, color };
}

export function cleanSet(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const kind = raw.kind === 'team' ? 'team' : 'player';
    const seen = new Set();
    const members = [];
    for (const m of (Array.isArray(raw.members) ? raw.members : []).slice(0, LIMITS.members)) {
        const c = cleanMember(m, kind);
        if (c && !seen.has(c.id)) {
            seen.add(c.id);
            members.push(c);
        }
    }
    return {
        id: typeof raw.id === 'string' && ID_RE.test(raw.id) ? raw.id : makeId(),
        name: str(raw.name, 60).trim() || (kind === 'team' ? 'Teams' : 'Players'),
        kind,
        members,
    };
}

const PER = ['game', 'total', 'per36', 'per100'];
const LIMIT_CHOICES = [25, 50, 100, 250, 500, 1000];
const CHARTS = ['scatter', 'line', 'bar', 'histogram', 'box', 'heatmap'];
const BINS = [0, 10, 20, 40];

// One block type's settings, rebuilt from scratch. Catalogue keys are only
// checked for shape here; the Table block checks them against the live
// catalogue (and says which it dropped), and the server checks them again.
export function cleanSettings(type, raw, setIds) {
    const s = raw && typeof raw === 'object' ? raw : {};
    const setId = typeof s.setId === 'string' && setIds.has(s.setId) ? s.setId : null;
    if (type === 'set') return { setId };
    if (type === 'note') return { text: str(s.text, LIMITS.note) };
    const key = (v) => (typeof v === 'string' && KEY_RE.test(v) ? v : null);
    const season = (v) => (isInt(v) && v >= 1940 && v <= 2100 ? v : null);
    if (type === 'tool') {
        // One of the app's own tools (utils/workbenchTools.js), shown for one
        // set member: a player id or a team code, checked for shape only.
        const tool = TOOL_KEYS.includes(s.tool) ? s.tool : 'card';
        const memberOk = (isInt(s.member) && s.member > 0 && s.member <= 1e9) || (typeof s.member === 'string' && TEAM_RE.test(s.member));
        return {
            tool,
            setId,
            member: memberOk ? s.member : null,
            season: season(s.season),
            view: (TOOL_VIEWS[tool] || []).includes(s.view) ? s.view : null,
            games: tool === 'shots' && SHOT_GAMES.includes(s.games) ? s.games : null,
        };
    }
    if (type === 'finder') return cleanFinder(s, setId, key, season);
    let seasonFrom = season(s.seasonFrom);
    let seasonTo = season(s.seasonTo);
    if (seasonFrom && seasonTo && seasonFrom > seasonTo) [seasonFrom, seasonTo] = [seasonTo, seasonFrom];
    const dataset = key(s.dataset) || 'player_season';
    const minGames = typeof s.minGames === 'number' && Number.isFinite(s.minGames) && s.minGames > 0 ? Math.min(Math.round(s.minGames), 5000) : null;
    // Possessions floor (lineups, pairs) and how a player set picks their rows.
    const minPoss = typeof s.minPoss === 'number' && Number.isFinite(s.minPoss) && s.minPoss > 0 ? Math.min(Math.round(s.minPoss), 100000) : null;
    const playersMatch = s.playersMatch === 'all' ? 'all' : 'any';
    if (type === 'chart') {
        return {
            dataset,
            setId,
            seasonFrom,
            seasonTo,
            per: PER.includes(s.per) ? s.per : 'game',
            minGames,
            minPoss,
            playersMatch,
            chart: CHARTS.includes(s.chart) ? s.chart : 'scatter',
            x: key(s.x),
            y: key(s.y),
            size: key(s.size),
            color: key(s.color) || 'member',
            facet: key(s.facet) || 'none',
            groupBy: key(s.groupBy) || 'none',
            lineX: ['date', 'age'].includes(s.lineX) ? s.lineX : 'season',
            agingEra: key(s.agingEra) || 'all',
            split: key(s.split),
            bins: BINS.includes(s.bins) ? s.bins : 0,
            style: s.style === 'dots' ? 'dots' : 'box',
            context: s.context !== false,
            trend: s.trend === true,
            ci: s.ci !== false,
        };
    }
    // table
    const columns = [...new Set((Array.isArray(s.columns) ? s.columns : []).filter((k) => typeof k === 'string' && KEY_RE.test(k)))]
        .slice(0, LIMITS.columns);
    const sort = (Array.isArray(s.sort) ? s.sort : [])
        .filter((k) => k && typeof k.key === 'string' && KEY_RE.test(k.key))
        .slice(0, 1)
        .map((k) => ({ key: k.key, dir: k.dir === 'asc' ? 'asc' : 'desc' }));
    return {
        dataset,
        setId,
        columns,
        seasonFrom,
        seasonTo,
        groupBy: typeof s.groupBy === 'string' && KEY_RE.test(s.groupBy) ? s.groupBy : 'none',
        per: PER.includes(s.per) ? s.per : 'game',
        sort,
        limit: LIMIT_CHOICES.includes(s.limit) ? s.limit : 50,
        minGames,
        minPoss,
        playersMatch,
        showN: s.showN === true,
        showCi: s.showCi !== false,
    };
}

// The Player Finder (step 6): a sentence of conditions, components/workbench/finderSpec.js.
const FINDER_OPS = ['gte', 'gt', 'lte', 'lt', 'eq', 'ne', 'between'];
const COUNT_OPS = ['gte', 'gt', 'lte', 'lt', 'eq'];
const FINDER_SORT = /^(c[0-7]|n_games|season)$/;

function cleanFinder(s, setId, key, season) {
    const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
    const test = (t) => (t && key(t.stat) ? {
        stat: t.stat,
        op: FINDER_OPS.includes(t.op) ? t.op : 'gte',
        value: num(t.value),
        value2: num(t.value2),
    } : null);
    const conditions = (Array.isArray(s.conditions) ? s.conditions : []).slice(0, 8).map((c) => {
        if (!c || typeof c !== 'object') return null;
        if (c.type === 'count' || c.type === 'streak') {
            const tests = (Array.isArray(c.tests) ? c.tests : []).slice(0, 4).map(test).filter(Boolean);
            if (!tests.length) return null;
            return { type: c.type, tests, countOp: COUNT_OPS.includes(c.countOp) ? c.countOp : 'gte', count: clampInt(c.count, 1, 100000, 5) };
        }
        const t = test(c);
        if (!t) return null;
        const minN = num(c.minN);
        return { type: 'value', ...t, per: PER.includes(c.per) ? c.per : 'game', minN: minN && minN > 0 ? Math.min(Math.round(minN), 1e6) : null };
    }).filter(Boolean);
    let seasonFrom = season(s.seasonFrom);
    let seasonTo = season(s.seasonTo);
    if (seasonFrom && seasonTo && seasonFrom > seasonTo) [seasonFrom, seasonTo] = [seasonTo, seasonFrom];
    const minGames = num(s.minGames);
    const minMinutes = num(s.minMinutes);
    return {
        dataset: s.dataset === 'player_game' ? 'player_game' : 'player_season',
        scope: s.scope === 'span' ? 'span' : 'season',
        seasonFrom,
        seasonTo,
        minGames: minGames && minGames > 0 ? Math.min(Math.round(minGames), 5000) : null,
        setId,
        where: ['home', 'away'].includes(s.where) ? s.where : 'all',
        result: ['W', 'L'].includes(s.result) ? s.result : 'all',
        opponent: typeof s.opponent === 'string' && TEAM_RE.test(s.opponent) ? s.opponent : null,
        minMinutes: minMinutes && minMinutes > 0 ? Math.min(minMinutes, 60) : null,
        conditions: conditions.length ? conditions : [{ type: 'value', stat: 'pts', op: 'gte', value: 25, value2: null, per: 'game', minN: null }],
        sort: s.sort && typeof s.sort.key === 'string' && FINDER_SORT.test(s.sort.key) ? { key: s.sort.key, dir: s.sort.dir === 'asc' ? 'asc' : 'desc' } : null,
        limit: LIMIT_CHOICES.includes(s.limit) ? s.limit : 100,
    };
}

export function cleanBlock(raw, setIds) {
    if (!raw || typeof raw !== 'object' || !BLOCK_TYPES.includes(raw.type)) return null;
    if (raw.type === 'tool' && !TOOL_KEYS.includes(raw.settings?.tool)) return null;
    const w = clampInt(raw.w, 2, GRID_COLS, 6);
    return {
        id: typeof raw.id === 'string' && ID_RE.test(raw.id) ? raw.id : makeId(),
        type: raw.type,
        title: str(raw.title, LIMITS.title),
        x: clampInt(raw.x, 0, GRID_COLS - w, 0),
        y: clampInt(raw.y, 0, 2000, 0),
        w,
        h: clampInt(raw.h, MIN_H, MAX_H, 6),
        settings: cleanSettings(raw.type, raw.settings, setIds),
    };
}

// Returns { board, dropped } — the rebuilt board (a new id unless keepId)
// and how many blocks/sets/members were unusable and left out.
export function cleanBoard(raw, { keepId = false } = {}) {
    if (!raw || typeof raw !== 'object') throw new Error('That is not a Workbench board.');
    const rawSets = (Array.isArray(raw.sets) ? raw.sets : []);
    const sets = [];
    let dropped = 0;
    for (const s of rawSets.slice(0, LIMITS.sets)) {
        const c = cleanSet(s);
        if (!c || sets.some((x) => x.id === c.id)) { dropped += 1; continue; }
        const rawCount = Array.isArray(s.members) ? Math.min(s.members.length, LIMITS.members) : 0;
        dropped += rawCount - c.members.length;
        sets.push(c);
    }
    dropped += Math.max(0, rawSets.length - LIMITS.sets);
    const setIds = new Set(sets.map((s) => s.id));
    const rawBlocks = Array.isArray(raw.blocks) ? raw.blocks : [];
    const blocks = [];
    for (const b of rawBlocks.slice(0, LIMITS.blocks)) {
        const c = cleanBlock(b, setIds);
        if (!c || blocks.some((x) => x.id === c.id)) { dropped += 1; continue; }
        blocks.push(c);
    }
    dropped += Math.max(0, rawBlocks.length - LIMITS.blocks);
    const board = {
        id: keepId && typeof raw.id === 'string' && ID_RE.test(raw.id) ? raw.id : makeId(),
        name: str(raw.name, LIMITS.name).trim() || 'Imported board',
        created: keepId && dateOk(raw.created) ? raw.created : nowIso(),
        updated: nowIso(),
        version: 1,
        sets,
        blocks: compact(blocks),
    };
    return { board, dropped };
}

// ── Export / import (files) ────────────────────────────────────────────

const portable = (b) => ({ id: b.id, name: b.name, created: b.created, version: 1, sets: b.sets, blocks: b.blocks });

export function exportBoardsJson(boards) {
    return JSON.stringify({ format: EXPORT_FORMAT, version: 1, exported: nowIso(), boards: boards.map(portable) }, null, 2);
}

// Boards keep their id, so importing the same file twice adds nothing the
// second time.
export async function importBoardsJson(text) {
    let parsed;
    try {
        parsed = JSON.parse(text);
    } catch {
        throw new Error('That file is not JSON exported from the Workbench.');
    }
    if (!parsed || parsed.format !== EXPORT_FORMAT || !Array.isArray(parsed.boards)) {
        throw new Error('That file is not a Workbench export from NBA Hub.');
    }
    const existing = new Set((await listBoards()).map((b) => b.id));
    let added = 0;
    let skipped = 0;
    let dropped = 0;
    let firstAdded = null;
    for (const raw of parsed.boards) {
        if (!raw || typeof raw.id !== 'string' || existing.has(raw.id)) {
            skipped += 1;
            continue;
        }
        let cleaned;
        try {
            cleaned = cleanBoard(raw, { keepId: true });
        } catch {
            skipped += 1;
            continue;
        }
        await saveNewBoard(cleaned.board);
        existing.add(cleaned.board.id);
        dropped += cleaned.dropped;
        firstAdded = firstAdded || cleaned.board.id;
        added += 1;
    }
    return { added, skipped, dropped, firstAdded };
}

// ── Share links ────────────────────────────────────────────────────────
// The board goes into the link itself (no server): JSON, deflated when the
// browser can (CompressionStream), base64url. "z." = deflated, "j." = plain.

function toBase64Url(bytes) {
    let bin = '';
    for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(text) {
    const bin = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
    return out;
}

async function pipe(bytes, stream) {
    const out = new Response(new Blob([bytes]).stream().pipeThrough(stream));
    return new Uint8Array(await out.arrayBuffer());
}

// Random ids don't compress, so a link carries none: blocks get new ids when
// it's opened and sets are renumbered s0, s1, … (blocks point at those).
export async function encodeShare(board) {
    const short = new Map(board.sets.map((s, i) => [s.id, `s${i}`]));
    const sets = board.sets.map((s) => ({ ...s, id: short.get(s.id) }));
    const blocks = board.blocks.map((b) => {
        const copy = { ...b, settings: { ...b.settings } };
        delete copy.id;
        if (copy.settings.setId) copy.settings.setId = short.get(copy.settings.setId) || null;
        return copy;
    });
    const json = new TextEncoder().encode(JSON.stringify({ format: EXPORT_FORMAT, version: 1, board: { name: board.name, sets, blocks } }));
    if (typeof CompressionStream !== 'undefined') {
        try {
            return `z.${toBase64Url(await pipe(json, new CompressionStream('deflate-raw')))}`;
        } catch { /* fall through to plain */ }
    }
    return `j.${toBase64Url(json)}`;
}

const BAD_LINK = 'That share link is damaged or incomplete (it may have been cut off). Ask for the board as an exported file instead.';

export async function decodeShare(param) {
    if (typeof param !== 'string' || param.length > 200_000 || !/^[zj]\.[A-Za-z0-9_-]+$/.test(param)) throw new Error(BAD_LINK);
    if (param[0] === 'z' && typeof DecompressionStream === 'undefined') {
        throw new Error('This browser can\'t open compressed board links. Ask for the board as an exported file instead.');
    }
    let parsed;
    try {
        let bytes = fromBase64Url(param.slice(2));
        if (param[0] === 'z') bytes = await pipe(bytes, new DecompressionStream('deflate-raw'));
        parsed = bytes.length <= 2_000_000 ? JSON.parse(new TextDecoder().decode(bytes)) : null;
    } catch {
        parsed = null;
    }
    if (!parsed || parsed.format !== EXPORT_FORMAT || !parsed.board) throw new Error(BAD_LINK);
    return cleanBoard(parsed.board);
}

// ── React hook ─────────────────────────────────────────────────────────

// { boards, loaded, error }: reloads whenever this tab or another one changes anything.
export function useBoards() {
    const [state, setState] = useState({ boards: [], loaded: false, error: '' });
    useEffect(() => {
        let alive = true;
        const load = () => listBoards().then(
            (boards) => alive && setState({ boards, loaded: true, error: '' }),
            (e) => alive && setState({ boards: [], loaded: true, error: e.message }),
        );
        load();
        const unsubscribe = subscribeBoards(load);
        return () => { alive = false; unsubscribe(); };
    }, []);
    return state;
}
