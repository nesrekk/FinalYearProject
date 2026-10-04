// Usability study sessions (round 7 step 10; docs/USABILITY_STUDY.md).
//
// A moderator opens any page with ?study=1, types a participant code (P1…P8,
// never a name) and the study panel (components/common/StudyPanel.jsx) walks
// through the tasks in utils/studyTasks.json. While a session is open:
//   - the Workbench keeps its boards in a database of the session's own
//     (studyBoardDb()), so every participant starts on an empty Workbench and
//     the moderator's real boards are never touched or shown;
//   - studyEvent() records what the app did (block added, finder run, error
//     shown, page opened…) with a timestamp, for counting afterwards;
//   - at the end of each task the panel stores a copy of the session's boards,
//     which scripts/usability_summary.py checks against the task.
// Everything stays in this browser (IndexedDB) until the moderator exports
// the session as a JSON file. Nothing is sent anywhere. Without an open
// session every function here is a no-op.

const ACTIVE_KEY = 'nba-hub-study-active';
const DB_NAME = 'nba-hub-study';
const STORE = 'sessions';
export const STUDY_FORMAT = 'nba-hub-usability';
export const STUDY_PARAM = 'study';
const BOARD_DB_PREFIX = 'nba-hub-workbench-study-';
const CHANGE_EVENT = 'nba-hub-study-change';

// ── Which session is open (per tab, survives page changes and reloads) ──

export function activeStudy() {
    try {
        const raw = sessionStorage.getItem(ACTIVE_KEY);
        const s = raw ? JSON.parse(raw) : null;
        return s && typeof s.id === 'string' ? s : null;
    } catch {
        return null;
    }
}

function setActive(value) {
    try {
        if (value) sessionStorage.setItem(ACTIVE_KEY, JSON.stringify(value));
        else sessionStorage.removeItem(ACTIVE_KEY);
    } catch { /* storage blocked: the panel says sessions can't be kept */ }
    window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeStudy(cb) {
    window.addEventListener(CHANGE_EVENT, cb);
    return () => window.removeEventListener(CHANGE_EVENT, cb);
}

// The Workbench's database while a session is open (utils/workbenchStore.js).
export function studyBoardDb() {
    const s = activeStudy();
    return s ? `${BOARD_DB_PREFIX}${s.id}` : null;
}

// ── IndexedDB plumbing ─────────────────────────────────────────────────

let dbPromise = null;

function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
        let req;
        try {
            req = indexedDB.open(DB_NAME, 1);
        } catch (e) {
            reject(e);
            return;
        }
        req.onupgradeneeded = () => {
            if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE, { keyPath: 'id' });
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error || new Error('The study log could not be opened.'));
    }).catch((e) => {
        dbPromise = null;
        throw e;
    });
    return dbPromise;
}

async function tx(mode, work) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
        const t = db.transaction(STORE, mode);
        let result;
        t.oncomplete = () => resolve(result);
        t.onerror = () => reject(t.error || new Error('The study log could not be written.'));
        t.onabort = () => reject(t.error || new Error('The study log could not be written.'));
        result = work(t.objectStore(STORE), (v) => { result = v; });
    });
}

export function getSession(id) {
    return tx('readonly', (store, done) => {
        const r = store.get(id);
        r.onsuccess = () => done(r.result || null);
    });
}

export function listSessions() {
    return tx('readonly', (store, done) => {
        const r = store.getAll();
        r.onsuccess = () => done((r.result || []).sort((a, b) => String(a.started).localeCompare(String(b.started))));
    });
}

// Read-modify-write inside one transaction, like workbenchStore.updateBoard.
export function updateSession(id, change) {
    return tx('readwrite', (store, done) => {
        const r = store.get(id);
        r.onsuccess = () => {
            if (!r.result) return;
            const next = change(structuredClone(r.result));
            if (next) {
                store.put(next);
                done(next);
            }
        };
    });
}

export function deleteSession(id) {
    return tx('readwrite', (store) => { store.delete(id); }).then(() => deleteBoardDb(id));
}

function deleteBoardDb(id) {
    return new Promise((resolve) => {
        try {
            const r = indexedDB.deleteDatabase(`${BOARD_DB_PREFIX}${id}`);
            r.onsuccess = r.onerror = r.onblocked = () => resolve();
        } catch {
            resolve();
        }
    });
}

// ── Starting and ending ────────────────────────────────────────────────

const PARTICIPANT_RE = /^[A-Za-z]{1,3}[0-9]{1,3}$/;
export const participantOk = (code) => PARTICIPANT_RE.test(code || '');

function device() {
    const attr = document.documentElement.getAttribute('data-theme');
    const dark = attr === 'dark' || (attr === 'system' && window.matchMedia?.('(prefers-color-scheme: dark)').matches);
    return {
        width: window.innerWidth,
        height: window.innerHeight,
        touch: (navigator.maxTouchPoints || 0) > 0,
        theme: dark ? 'ink' : 'paper',
    };
}

export async function startSession({ participant, pilot, taskSet, taskKeys }) {
    const id = `${participant.toUpperCase()}-${Date.now().toString(36)}`;
    const session = {
        format: STUDY_FORMAT,
        version: 1,
        id,
        participant: participant.toUpperCase(),
        pilot: !!pilot,
        taskSet,
        taskKeys,
        device: device(),
        started: new Date().toISOString(),
        ended: null,
        tasks: [],
        events: [],
        sus: null,
        notes: '',
    };
    await tx('readwrite', (store) => { store.put(session); });
    setActive({ id, participant: session.participant, pilot: session.pilot });
    return session;
}

export async function endSession() {
    const s = activeStudy();
    if (!s) return;
    await flush();
    await updateSession(s.id, (x) => ({ ...x, ended: x.ended || new Date().toISOString() }));
    setActive(null);
}

// Leaves the session open in the log (it can be exported or resumed from the
// panel's list) but stops recording in this tab.
export const leaveSession = () => setActive(null);
export const resumeSession = (session) => setActive({ id: session.id, participant: session.participant, pilot: session.pilot });

// ── Events ─────────────────────────────────────────────────────────────
// Buffered and written at most every second, so typing or dragging never
// waits on IndexedDB. The current task is stamped on each event.

let buffer = [];
let timer = null;
let currentTask = null;

export function setStudyTask(key) {
    currentTask = key;
}

// Detail fields can't replace the event's own `t` and `type`; `task` may be
// given (the panel stamps task_start / task_end before the task is current).
function cleanDetail(detail) {
    const out = {};
    for (const [k, v] of Object.entries(detail || {})) {
        if (k === 't' || k === 'type') continue;
        if (typeof v === 'string') out[k] = v.slice(0, 300);
        else if (typeof v === 'number' || typeof v === 'boolean' || v === null) out[k] = v;
    }
    return out;
}

export function studyEvent(type, detail) {
    const s = activeStudy();
    if (!s) return;
    buffer.push({ task: currentTask, ...cleanDetail(detail), t: Date.now(), type });
    if (!timer) timer = setTimeout(flush, 1000);
}

export async function flush() {
    clearTimeout(timer);
    timer = null;
    const s = activeStudy();
    if (!s || !buffer.length) return;
    const batch = buffer;
    buffer = [];
    try {
        await updateSession(s.id, (x) => ({ ...x, events: [...x.events, ...batch].slice(-20000) }));
    } catch {
        buffer = [...batch, ...buffer];
    }
}

// ── Export ─────────────────────────────────────────────────────────────

export function sessionFileName(session) {
    return `nba-hub-study-${session.participant}${session.pilot ? '-pilot' : ''}-${String(session.started).slice(0, 10)}.json`;
}

export const sessionJson = (session) => JSON.stringify(session, null, 2);
