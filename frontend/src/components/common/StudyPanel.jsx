import React, { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import Icon from './Icon';
import STUDY from '../../utils/studyTasks.json';
import {
    STUDY_PARAM, activeStudy, deleteSession, endSession, flush, getSession, leaveSession, listSessions, participantOk,
    resumeSession, sessionFileName, sessionJson, setStudyTask, startSession, studyEvent, subscribeStudy, updateSession,
} from '../../utils/studyLog';
import { listBoards } from '../../utils/workbenchStore';
import { downloadText } from '../../utils/tableExport';
import '../../styles/study.css';

// The moderator's panel for the Workbench usability study (round 7 step 10,
// docs/USABILITY_STUDY.md). Opened with ?study=1 on any page; shown only
// while setting up or running a session. The participant reads the task in
// the panel; the moderator presses the buttons and types what was said.

const TASKS = STUDY.tasks;
const TAGS = STUDY.stuckTags;
const SEQ_LABELS = ['Very difficult', '', '', '', '', '', 'Very easy'];
const SUS_LABELS = ['Strongly disagree', '', '', '', 'Strongly agree'];

const clock = (ms) => {
    const s = Math.max(0, Math.round(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

function useActive() {
    return useSyncExternalStore(subscribeStudy, () => sessionStorage.getItem('nba-hub-study-active'), () => null);
}

function wantsStudy() {
    try {
        return new URLSearchParams(window.location.search).has(STUDY_PARAM);
    } catch {
        return false;
    }
}

function dropStudyParam() {
    const url = new URL(window.location.href);
    url.searchParams.delete(STUDY_PARAM);
    window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
}

async function snapshotBoards() {
    try {
        const boards = await listBoards();
        return boards.map((b) => ({ id: b.id, name: b.name, sets: b.sets, blocks: b.blocks }));
    } catch {
        return null;
    }
}

function exportSession(session) {
    downloadText(sessionJson(session), sessionFileName(session), 'application/json');
}

// ── Setup: participant code, earlier sessions ──────────────────────────

function Setup({ onClose }) {
    const [code, setCode] = useState('');
    const [pilot, setPilot] = useState(false);
    const [optional, setOptional] = useState(true);
    const [sessions, setSessions] = useState(null);
    const [msg, setMsg] = useState('');
    const reload = useCallback(() => listSessions().then(setSessions, (e) => setMsg(e.message)), []);
    useEffect(() => { reload(); }, [reload]);

    const start = async (e) => {
        e.preventDefault();
        if (!participantOk(code)) {
            setMsg('Use a code like P3, never a name.');
            return;
        }
        const keys = TASKS.filter((t) => optional || !t.optional).map((t) => t.key);
        try {
            await startSession({ participant: code, pilot, taskSet: STUDY.taskSet, taskKeys: keys });
        } catch (err) {
            setMsg(`Could not start: ${err.message}`);
        }
    };

    return (
        <section className="study-panel study-panel--setup" aria-label="Usability study">
            <header className="study-head">
                <strong><Icon name="science" size={16} /> Usability study</strong>
                <button type="button" className="study-x" onClick={onClose} aria-label="Close the study panel">
                    <Icon name="close" size={16} />
                </button>
            </header>
            <form className="study-body" onSubmit={start}>
                <p className="study-small">
                    Task set <code>{STUDY.taskSet}</code>. The participant gets an empty Workbench of their own; your boards
                    aren’t shown or changed. Everything stays in this browser until you export it.
                </p>
                <label className="study-field">
                    <span>Participant code</span>
                    <input className="study-input" value={code} maxLength={6} placeholder="P1" autoComplete="off"
                        onChange={(e) => setCode(e.target.value.trim())} />
                </label>
                <label className="study-check">
                    <input type="checkbox" checked={pilot} onChange={(e) => setPilot(e.target.checked)} />
                    Pilot run (left out of the results)
                </label>
                <label className="study-check">
                    <input type="checkbox" checked={optional} onChange={(e) => setOptional(e.target.checked)} />
                    Include the optional type-in task (needs the internet and the Gemini key)
                </label>
                <button type="submit" className="table-export-btn study-primary">Start session</button>
                {msg && <p className="study-msg" role="alert">{msg}</p>}
            </form>
            {sessions && sessions.length > 0 && (
                <div className="study-body study-list">
                    <p className="study-small"><strong>Sessions in this browser</strong></p>
                    <ul>
                        {sessions.map((s) => (
                            <li key={s.id}>
                                <span>
                                    {s.participant}{s.pilot ? ' (pilot)' : ''} · {String(s.started).slice(0, 16).replace('T', ' ')} ·{' '}
                                    {s.tasks.length}/{s.taskKeys.length} tasks{s.ended ? '' : ' · not finished'}
                                </span>
                                <span className="study-row">
                                    <button type="button" className="study-link" onClick={() => exportSession(s)}>Export</button>
                                    {!s.ended && <button type="button" className="study-link" onClick={() => resumeSession(s)}>Resume</button>}
                                    <button type="button" className="study-link" onClick={() => {
                                        if (window.confirm(`Delete ${s.participant}'s session and its boards? Export it first.`)) deleteSession(s.id).then(reload);
                                    }}>Delete</button>
                                </span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </section>
    );
}

// ── Running a session ──────────────────────────────────────────────────

function Running({ id, activePage }) {
    const [session, setSession] = useState(null);
    const [now, setNow] = useState(() => Date.now());
    const [open, setOpen] = useState(true);
    const [tag, setTag] = useState('');
    const [note, setNote] = useState('');
    const [answer, setAnswer] = useState('');
    const [outcome, setOutcome] = useState('');
    const [seq, setSeq] = useState(null);
    const [sus, setSus] = useState(() => Array(10).fill(null));
    const [msg, setMsg] = useState('');

    useEffect(() => {
        let alive = true;
        getSession(id).then((s) => alive && setSession(s), (e) => alive && setMsg(e.message));
        return () => { alive = false; };
    }, [id]);

    const cur = session?.current || null;
    const doneKeys = new Set((session?.tasks || []).map((t) => t.key));
    const nextKey = session?.taskKeys.find((k) => !doneKeys.has(k)) || null;
    const taskKey = cur?.key || nextKey;
    const task = TASKS.find((t) => t.key === taskKey) || null;
    const index = session ? session.taskKeys.indexOf(taskKey) : -1;

    useEffect(() => { setStudyTask(cur?.phase === 'running' ? cur.key : null); }, [cur?.phase, cur?.key]);
    useEffect(() => {
        if (cur?.phase !== 'running') return undefined;
        const t = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(t);
    }, [cur?.phase]);
    useEffect(() => { studyEvent('page', { page: activePage }); }, [activePage]);

    const save = async (change) => {
        try {
            const next = await updateSession(id, change);
            if (next) setSession(next);
            return next;
        } catch (e) {
            setMsg(`Not saved: ${e.message}`);
            return null;
        }
    };

    if (!session) return msg ? <p className="study-msg">{msg}</p> : null;

    const startTask = () => {
        studyEvent('task_start', { task: taskKey });
        save((s) => ({ ...s, current: { key: taskKey, phase: 'running', started: Date.now(), hints: 0, stuck: [] } }));
    };
    const skipTask = () => save((s) => ({ ...s, current: null, tasks: [...s.tasks, { key: taskKey, outcome: 'skipped', started: null, ended: null }] }));
    const addHint = () => {
        studyEvent('hint', {});
        save((s) => ({ ...s, current: { ...s.current, hints: s.current.hints + 1 } }));
    };
    const addStuck = () => {
        if (!tag) return;
        studyEvent('stuck', { tag });
        save((s) => ({ ...s, current: { ...s.current, stuck: [...s.current.stuck, { t: Date.now(), tag, note: note.trim().slice(0, 300) }] } }));
        setTag('');
        setNote('');
    };
    const stopTask = async (endedBy) => {
        studyEvent('task_end', { task: taskKey, endedBy });
        await flush();
        const boards = await snapshotBoards();
        setOutcome(endedBy === 'gave_up' ? 'fail' : '');
        save((s) => ({ ...s, current: { ...s.current, phase: 'review', ended: Date.now(), endedBy, boards } }));
    };
    const finishTask = () => {
        if (!outcome || !seq) {
            setMsg('Pick the outcome and the ease rating first.');
            return;
        }
        setMsg('');
        save((s) => ({
            ...s,
            current: null,
            tasks: [...s.tasks, {
                key: s.current.key, started: s.current.started, ended: s.current.ended, endedBy: s.current.endedBy,
                seconds: Math.round((s.current.ended - s.current.started) / 1000), hints: s.current.hints, stuck: s.current.stuck,
                answer: answer.trim().slice(0, 200), outcome, seq, boards: s.current.boards,
            }],
        }));
        setAnswer('');
        setOutcome('');
        setSeq(null);
    };
    const finishSession = async () => {
        if (sus.some((v) => v === null)) {
            setMsg('Every statement needs an answer (or press Finish without the questionnaire).');
            return;
        }
        await save((s) => ({ ...s, sus }));
        await finishQuietly();
    };
    const finishQuietly = async () => {
        await endSession();
        const final = await getSession(id);
        if (final) exportSession(final);
    };

    const phase = cur?.phase || (nextKey ? 'ready' : 'sus');
    const label = `${session.participant}${session.pilot ? ' · pilot' : ''}`;

    if (!open) {
        return (
            <button type="button" className="study-pill" onClick={() => setOpen(true)} aria-label="Show the study panel">
                <Icon name="science" size={15} /> {label}{phase === 'running' ? ` · ${clock(now - cur.started)}` : ''}
            </button>
        );
    }

    return (
        <section className="study-panel" aria-label="Usability study task">
            <header className="study-head">
                <strong>
                    <Icon name="science" size={16} /> {label}
                    {task && phase !== 'sus' ? ` · Task ${index + 1} of ${session.taskKeys.length}` : ''}
                </strong>
                <span className="study-row">
                    {phase === 'running' && <span className="study-clock" aria-label="Time on task">{clock(now - cur.started)}</span>}
                    <button type="button" className="study-x" onClick={() => setOpen(false)} aria-label="Hide the study panel">
                        <Icon name="remove" size={16} />
                    </button>
                </span>
            </header>

            {task && phase !== 'sus' && (
                <div className="study-body">
                    <p className="study-task-title">{task.title}</p>
                    <p className="study-task">{task.text}</p>
                    {task.question && <p className="study-task study-q">{task.question}</p>}
                </div>
            )}

            {phase === 'ready' && (
                <div className="study-body study-actions">
                    <button type="button" className="table-export-btn study-primary" onClick={startTask}>
                        <Icon name="play_arrow" size={15} /> Start task
                    </button>
                    {task?.optional && <button type="button" className="table-export-btn" onClick={skipTask}>Skip this task</button>}
                </div>
            )}

            {phase === 'running' && (
                <div className="study-body">
                    <div className="study-actions">
                        <button type="button" className="table-export-btn study-primary" onClick={() => stopTask('done')}>
                            <Icon name="check" size={15} /> Participant is done
                        </button>
                        <button type="button" className="table-export-btn" onClick={() => stopTask('gave_up')}>Gave up</button>
                    </div>
                    <details className="study-mod">
                        <summary>Moderator: hint ({cur.hints}) · stuck points ({cur.stuck.length})</summary>
                        <p className="study-small">Standard hint: “{task.hint}”</p>
                        <button type="button" className="table-export-btn" onClick={addHint}>Hint given</button>
                        <div className="study-stuck">
                            <select className="study-input" value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Where they got stuck">
                                <option value="">Stuck on…</option>
                                {TAGS.map(([k, text]) => <option key={k} value={k}>{text}</option>)}
                            </select>
                            <input className="study-input" value={note} maxLength={300} placeholder="What happened (no names)"
                                aria-label="Stuck note" onChange={(e) => setNote(e.target.value)} />
                            <button type="button" className="table-export-btn" onClick={addStuck} disabled={!tag}>Log it</button>
                        </div>
                        {cur.stuck.length > 0 && (
                            <ul className="study-small">
                                {cur.stuck.map((x) => <li key={x.t}>{clock(x.t - cur.started)} {x.tag}{x.note ? `: ${x.note}` : ''}</li>)}
                            </ul>
                        )}
                    </details>
                </div>
            )}

            {phase === 'review' && (
                <div className="study-body">
                    <p className="study-small">
                        {cur.endedBy === 'gave_up' ? 'Gave up' : 'Done'} after {clock(cur.ended - cur.started)}
                        {cur.hints ? `, ${cur.hints} hint${cur.hints === 1 ? '' : 's'}` : ''}.
                        {cur.boards ? ` Board saved for checking (${cur.boards.length} board${cur.boards.length === 1 ? '' : 's'}).` : ' The boards could not be read.'}
                    </p>
                    {task.question && (
                        <label className="study-field">
                            <span>Their answer, as said</span>
                            <input className="study-input" value={answer} maxLength={200} onChange={(e) => setAnswer(e.target.value)} />
                        </label>
                    )}
                    <fieldset className="study-scale">
                        <legend>Outcome (moderator)</legend>
                        {[['success', 'Success'], ['help', 'Success after a hint'], ['fail', 'Failed']].map(([k, text]) => (
                            <label key={k}><input type="radio" name="study-outcome" checked={outcome === k} onChange={() => setOutcome(k)} /> {text}</label>
                        ))}
                    </fieldset>
                    <p className="study-small study-success">Counts as success: {task.success}</p>
                    <fieldset className="study-scale study-seq">
                        <legend>Ask: “Overall, how difficult or easy was this task?”</legend>
                        {SEQ_LABELS.map((l, i) => (
                            <label key={i} title={l || undefined}>
                                <input type="radio" name="study-seq" checked={seq === i + 1} onChange={() => setSeq(i + 1)} />
                                <span>{i + 1}</span>
                            </label>
                        ))}
                        <span className="study-small study-ends"><span>1 very difficult</span><span>7 very easy</span></span>
                    </fieldset>
                    <button type="button" className="table-export-btn study-primary" onClick={finishTask}>Save and go on</button>
                </div>
            )}

            {phase === 'sus' && (
                <div className="study-body">
                    <p className="study-task-title">Last: ten statements</p>
                    <p className="study-small">The participant answers each from 1 (strongly disagree) to 5 (strongly agree), about the Workbench.</p>
                    <ol className="study-sus">
                        {STUDY.sus.map((q, qi) => (
                            <li key={q}>
                                <span>{q}</span>
                                <span className="study-row" role="radiogroup" aria-label={q}>
                                    {SUS_LABELS.map((l, i) => (
                                        <label key={i} title={l || undefined}>
                                            <input type="radio" name={`sus-${qi}`} checked={sus[qi] === i + 1}
                                                onChange={() => setSus((v) => v.map((x, j) => (j === qi ? i + 1 : x)))} />
                                            <span>{i + 1}</span>
                                        </label>
                                    ))}
                                </span>
                            </li>
                        ))}
                    </ol>
                    <div className="study-actions">
                        <button type="button" className="table-export-btn study-primary" onClick={finishSession}>Finish and export</button>
                        <button type="button" className="table-export-btn" onClick={finishQuietly}>Finish without it</button>
                    </div>
                </div>
            )}

            {msg && <p className="study-msg" role="alert">{msg}</p>}
            <footer className="study-foot">
                <button type="button" className="study-link" onClick={() => { flush(); leaveSession(); }}>
                    Pause (resume later from ?study=1)
                </button>
            </footer>
        </section>
    );
}

export default function StudyPanel({ activePage }) {
    const raw = useActive();
    const active = raw ? activeStudy() : null;
    const [setup, setSetup] = useState(wantsStudy);

    // Errors anywhere in the app count as a sign of trouble during a task.
    useEffect(() => {
        if (!active) return undefined;
        const onError = (e) => studyEvent('js_error', { message: String(e.message || e.reason || 'error') });
        window.addEventListener('error', onError);
        window.addEventListener('unhandledrejection', onError);
        return () => {
            window.removeEventListener('error', onError);
            window.removeEventListener('unhandledrejection', onError);
        };
    }, [active?.id]); // eslint-disable-line react-hooks/exhaustive-deps

    if (!active && !setup) return null;
    const close = () => { dropStudyParam(); setSetup(false); };
    return createPortal(
        <div className="study-root">
            {active ? <Running key={active.id} id={active.id} activePage={activePage} /> : <Setup onClose={close} />}
        </div>,
        document.body,
    );
}
