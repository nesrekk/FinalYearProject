import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import SaveViewButton from '../common/SaveViewButton';
import BoardGrid from '../workbench/BoardGrid';
import NoteBlock from '../workbench/NoteBlock';
import SetBlock from '../workbench/SetBlock';
import TableBlock from '../workbench/TableBlock';
import { defaultColumns } from '../workbench/tableSpec';
import { BLOCK_INFO, blockLabel } from '../workbench/workbenchShared';
import { fetchWorkbenchCatalogue, workbenchError } from '../../services/api';
import { downloadText, slugify } from '../../utils/tableExport';
import { useAutosave } from '../../utils/useAutosave';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { placeNew } from '../../utils/workbenchLayout';
import {
    LIMITS, MAX_SHARE_CHARS, SHARE_PARAM, cleanSettings, createBoard, decodeShare, deleteBoard, encodeShare,
    exportBoardsJson, importBoardsJson, makeId, renameBoard, saveNewBoard, updateBoard, useBoards,
} from '../../utils/workbenchStore';
import '../../styles/workbench.css';

const NARROW = '(max-width: 760px)';

function useNarrow() {
    return useSyncExternalStore(
        (cb) => {
            const mq = window.matchMedia(NARROW);
            mq.addEventListener('change', cb);
            return () => mq.removeEventListener('change', cb);
        },
        () => window.matchMedia(NARROW).matches,
    );
}

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        const box = document.createElement('textarea');
        box.value = text;
        box.setAttribute('readonly', '');
        box.style.position = 'fixed';
        box.style.opacity = '0';
        document.body.appendChild(box);
        box.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch { ok = false; }
        box.remove();
        return ok;
    }
}

function BoardName({ board, onSave }) {
    const [name, setName, flush] = useAutosave(board.name, (v) => (v.trim() ? onSave(v) : undefined));
    return (
        <input className="wb-board-name" value={name} maxLength={LIMITS.name} aria-label="Board name"
            onChange={(e) => setName(e.target.value)} onBlur={flush} />
    );
}

function SharedPreview({ shared, onAdd, onClose }) {
    if (shared.state === 'loading') return <p className="wb-meta" role="status">Opening the shared board…</p>;
    if (shared.state === 'error') {
        return (
            <div className="wb-shared wb-shared--bad" role="alert">
                <p>{shared.error}</p>
                <button type="button" className="table-export-btn" onClick={onClose}>Close</button>
            </div>
        );
    }
    const { board, dropped } = shared;
    const counts = Object.entries(board.blocks.reduce((m, b) => ({ ...m, [b.type]: (m[b.type] || 0) + 1 }), {}))
        .map(([t, n]) => `${n} ${BLOCK_INFO[t].short.toLowerCase()}${n === 1 ? '' : 's'}`).join(', ');
    return (
        <div className="wb-shared" role="region" aria-label="Shared board">
            <p>
                <strong>Someone shared a board with you: “{board.name}”</strong> ({counts || 'no blocks'}
                {board.sets.length ? `; sets: ${board.sets.map((s) => `${s.name} (${s.members.length})`).join(', ')}` : ''}).
                {dropped ? ` ${dropped} unusable item${dropped === 1 ? ' was' : 's were'} left out.` : ''}
                {' '}Adding it saves a copy in this browser; its numbers load live from the app’s data.
            </p>
            <div className="wb-row">
                <button type="button" className="table-export-btn" onClick={onAdd}><Icon name="add" size={15} /> Add to my boards</button>
                <button type="button" className="table-export-btn" onClick={onClose}>No thanks</button>
            </div>
        </div>
    );
}

export default function Workbench() {
    const { boards, loaded, error } = useBoards();
    const params = useInitialParams();
    const [sel, setSel] = useState(() => parseParam.str(params, 'b'));
    const [askedFor] = useState(() => parseParam.str(params, 'b'));
    const [shareCode] = useState(() => parseParam.str(params, SHARE_PARAM));
    const [shared, setShared] = useState(() => ({ state: shareCode ? 'loading' : 'none' }));
    const [catalogue, setCatalogue] = useState({ data: null, error: '' });
    const [msg, setMsg] = useState({ text: '', bad: false });
    const [undo, setUndo] = useState(null);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [editing, setEditing] = useState(() => new Set());
    const fileInput = useRef(null);
    const scrollTo = useRef(null);
    const narrow = useNarrow();

    useEffect(() => {
        fetchWorkbenchCatalogue().then(
            (data) => setCatalogue({ data, error: '' }),
            (e) => setCatalogue({ data: null, error: workbenchError(e) }),
        );
    }, []);

    useEffect(() => {
        if (!shareCode) return;
        decodeShare(shareCode).then(
            ({ board, dropped }) => setShared({ state: 'ready', board, dropped }),
            (e) => setShared({ state: 'error', error: e.message }),
        );
    }, [shareCode]);

    // The board asked for, else the one edited last.
    const latest = [...boards].sort((a, b) => String(b.updated).localeCompare(String(a.updated)))[0] || null;
    const board = boards.find((b) => b.id === sel) || latest;
    const missing = loaded && askedFor && sel === askedFor && !boards.some((b) => b.id === askedFor);

    useUrlSync(loaded ? { b: board?.id || null, [SHARE_PARAM]: shared.state === 'none' ? null : shareCode } : null);

    useEffect(() => {
        const id = scrollTo.current;
        if (!id) return;
        scrollTo.current = null;
        const el = document.querySelector(`[data-wb-block="${id}"]`);
        el?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
        el?.querySelector('[data-wb-handle], .wb-steps button:not(:disabled)')?.focus({ preventScroll: true });
    }, [boards]);

    const run = async (work, okText) => {
        try {
            const result = await work();
            setMsg({ text: okText || '', bad: false });
            return result;
        } catch (e) {
            setMsg({ text: e.message || 'That did not work.', bad: true });
            return null;
        }
    };
    const change = (fn, okText) => board && run(() => updateBoard(board.id, fn), okText);

    const onNewBoard = async () => {
        const created = await run(() => createBoard(`Board ${boards.length + 1}`));
        if (created) { setSel(created.id); setConfirmDelete(false); setUndo(null); }
    };

    const addBlock = async (type) => {
        if (type === 'table' && !catalogue.data) return;
        const target = board || await run(() => createBoard('My board'));
        if (!target) return;
        setSel(target.id);
        const id = makeId();
        const info = BLOCK_INFO[type];
        const added = await run(() => updateBoard(target.id, (bd) => {
            if (bd.blocks.length >= LIMITS.blocks) throw new Error(`A board holds up to ${LIMITS.blocks} blocks.`);
            let settings = {};
            if (type === 'set') {
                if (bd.sets.length >= LIMITS.sets) throw new Error(`A board holds up to ${LIMITS.sets} sets.`);
                const set = { id: makeId(), name: `Players ${bd.sets.length + 1}`, kind: 'player', members: [] };
                bd.sets.push(set);
                settings = { setId: set.id };
            } else if (type === 'table') {
                // Bound to the newest set that has members, if there is one.
                const set = [...bd.sets].reverse().find((s) => s.members.length);
                const dsKey = set?.kind === 'team' ? 'team_season' : 'player_season';
                const ds = catalogue.data.datasets.find((d) => d.key === dsKey) || catalogue.data.datasets[0];
                settings = cleanSettings('table', { dataset: ds.key, setId: set?.id, columns: defaultColumns(ds) }, new Set(bd.sets.map((s) => s.id)));
            } else {
                settings = { text: '' };
            }
            bd.blocks.push({ id, type, title: '', ...placeNew(bd.blocks, narrow ? 12 : info.w, info.h), settings });
            return bd;
        }));
        if (added) scrollTo.current = id;
    };

    const removeBlock = (block) => {
        const others = board.blocks.filter((b) => b.id !== block.id);
        const sid = block.settings.setId;
        // A set goes with the last Set block showing it, unless a table still reads it.
        const dropSet = block.type === 'set' && sid && !others.some((b) => b.settings.setId === sid)
            ? board.sets.find((s) => s.id === sid) : null;
        change((bd) => {
            bd.blocks = bd.blocks.filter((b) => b.id !== block.id);
            if (dropSet) bd.sets = bd.sets.filter((s) => s.id !== dropSet.id);
            return bd;
        });
        setUndo({ boardId: board.id, block, set: dropSet, label: blockLabel(block, board, catalogue.data) });
    };

    const onUndo = async () => {
        const u = undo;
        setUndo(null);
        await run(() => updateBoard(u.boardId, (bd) => {
            if (u.set && !bd.sets.some((s) => s.id === u.set.id)) bd.sets.push(u.set);
            if (!bd.blocks.some((b) => b.id === u.block.id)) bd.blocks.push(u.block);
            return bd;
        }, u.block.id), 'Block restored.');
    };

    const patchSettings = (blockId, patch) => change((bd) => {
        bd.blocks = bd.blocks.map((b) => (b.id === blockId ? { ...b, settings: { ...b.settings, ...patch } } : b));
        return bd;
    });
    const setTitle = (blockId, title) => change((bd) => {
        bd.blocks = bd.blocks.map((b) => (b.id === blockId ? { ...b, title: title.slice(0, LIMITS.title) } : b));
        return bd;
    });
    const updateSet = (setId, fn) => change((bd) => {
        bd.sets = bd.sets.map((s) => (s.id === setId ? fn(structuredClone(s)) : s));
        return bd;
    });
    const newSetFor = (blockId, kind) => change((bd) => {
        if (bd.sets.length >= LIMITS.sets) throw new Error(`A board holds up to ${LIMITS.sets} sets.`);
        const set = { id: makeId(), name: `${kind === 'team' ? 'Teams' : 'Players'} ${bd.sets.length + 1}`, kind, members: [] };
        bd.sets.push(set);
        bd.blocks = bd.blocks.map((b) => (b.id === blockId ? { ...b, settings: { ...b.settings, setId: set.id } } : b));
        return bd;
    });
    const commitLayout = (next) => change((bd) => {
        const pos = new Map(next.map((b) => [b.id, b]));
        bd.blocks = bd.blocks.map((b) => {
            const p = pos.get(b.id);
            return p ? { ...b, x: p.x, y: p.y, w: p.w, h: p.h } : b;
        });
        return bd;
    });

    const onShare = async () => {
        const code = await encodeShare(board);
        if (code.length > MAX_SHARE_CHARS) {
            setMsg({
                text: `This board is too big for a link (${code.length.toLocaleString()} characters even compressed; links stay under ${MAX_SHARE_CHARS.toLocaleString()} so chat apps and mail don’t cut them). Use Export file and send that instead.`,
                bad: true,
            });
            return;
        }
        const url = `${window.location.origin}${window.location.pathname}?page=workbench&${SHARE_PARAM}=${code}`;
        const ok = await copyText(url);
        setMsg(ok
            ? { text: `Share link copied (${url.length.toLocaleString()} characters). Whoever opens it can add a copy of this board: blocks, sets and settings travel in the link; the numbers load live. Later edits here don’t reach their copy.`, bad: false }
            : { text: 'Couldn’t copy the link. Use Export file instead.', bad: true });
    };

    const doExport = (all) => {
        const list = all ? boards : [board];
        const stamp = new Date().toISOString().slice(0, 10);
        downloadText(exportBoardsJson(list), `${all ? 'nba-hub-boards' : `nba-hub-board-${slugify(board.name)}`}-${stamp}.json`, 'application/json');
    };

    const onImport = async (e) => {
        const file = e.target.files?.[0];
        e.target.value = '';
        if (!file) return;
        const result = await run(async () => importBoardsJson(await file.text()));
        if (!result) return;
        const { added, skipped, dropped, firstAdded } = result;
        setMsg({
            text: `Imported ${added} board${added === 1 ? '' : 's'}${skipped ? ` (${skipped} already here or unusable)` : ''}${dropped ? `; ${dropped} unusable item${dropped === 1 ? ' was' : 's were'} left out` : ''}.`,
            bad: false,
        });
        if (firstAdded) setSel(firstAdded);
    };

    const onAddShared = async () => {
        const saved = await run(() => saveNewBoard({ ...shared.board, id: makeId() }), `Added “${shared.board.name}” to your boards.`);
        if (saved) { setSel(saved.id); setShared({ state: 'none' }); }
    };

    const onDelete = async () => {
        const ok = await run(async () => { await deleteBoard(board.id); return true; }, 'Board deleted.');
        if (ok) { setSel(null); setConfirmDelete(false); setUndo(null); }
    };

    const toggleEditing = (id) => setEditing((s) => {
        const next = new Set(s);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    const labelOf = (id) => {
        const b = board?.blocks.find((x) => x.id === id);
        return b ? blockLabel(b, board, catalogue.data) : 'Block';
    };

    const renderHeader = (b, handle) => {
        const info = BLOCK_INFO[b.type];
        const label = labelOf(b.id);
        return (
            <header className="wb-block-head">
                {handle}
                <h3 className="wb-block-title"><Icon name={info.icon} size={16} /> <span>{label}</span></h3>
                <span className="wb-block-actions">
                    {b.type === 'table' && (
                        <button type="button" className={`wb-icon-btn${editing.has(b.id) ? ' wb-icon-btn--on' : ''}`} onClick={() => toggleEditing(b.id)}
                            aria-expanded={editing.has(b.id)} aria-label={`Settings for ${label}`} title="Settings">
                            <Icon name="tune" size={17} />
                        </button>
                    )}
                    <button type="button" className="wb-icon-btn" onClick={() => removeBlock(b)} aria-label={`Remove ${label}`} title="Remove block">
                        <Icon name="delete" size={17} />
                    </button>
                </span>
            </header>
        );
    };

    const renderBody = (b) => {
        if (b.type === 'set') {
            const set = board.sets.find((s) => s.id === b.settings.setId) || null;
            const usedBy = set ? board.blocks.filter((x) => x.type !== 'set' && x.settings.setId === set.id).map((x) => blockLabel(x, board, catalogue.data)) : [];
            return (
                <SetBlock
                    set={set}
                    sets={board.sets}
                    usedBy={usedBy}
                    onUpdateSet={updateSet}
                    onBindSet={(setId) => patchSettings(b.id, { setId })}
                    onNewSet={(kind) => newSetFor(b.id, kind)}
                />
            );
        }
        if (b.type === 'note') return <NoteBlock block={b} onSave={(text) => patchSettings(b.id, { text: text.slice(0, LIMITS.note) })} />;
        if (catalogue.error) return <p className="wb-error" role="alert">{catalogue.error}</p>;
        if (!catalogue.data) return <p className="wb-meta" role="status">Loading the stat catalogue…</p>;
        return (
            <TableBlock
                block={b}
                board={board}
                catalogue={catalogue.data}
                editing={editing.has(b.id)}
                onSettings={(patch) => patchSettings(b.id, patch)}
                onTitle={(title) => setTitle(b.id, title)}
            />
        );
    };

    const addButtons = (
        <span className="wb-add" role="group" aria-label="Add a block">
            <span className="wb-add-label">Add</span>
            {['set', 'table', 'note'].map((t) => (
                <button key={t} type="button" className="table-export-btn" onClick={() => addBlock(t)}
                    disabled={t === 'table' && !catalogue.data}>
                    <Icon name={BLOCK_INFO[t].icon} size={15} /> {BLOCK_INFO[t].short}
                </button>
            ))}
        </span>
    );

    return (
        <div className="page page-workbench fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="dashboard_customize" fill /></span>
                    Workbench
                    <InfoTooltip label="How this works" title="Building a board">
                        A board is a page of blocks you arrange yourself. Start with a <strong>Set</strong>: a named group of
                        players or teams. Then add a <strong>Table</strong>, choose the data (player seasons, player games,
                        team seasons, team games), the stats and the seasons, and bind it to the set; change the set and
                        every block bound to it follows. Drag a block by its handle to move it and by its corner to resize
                        it, or focus the handle and use the arrow keys (Shift resizes). Every number is fetched live from
                        the app’s data with its sample (n); values from too small a sample are greyed. Boards are stored
                        in this browser (IndexedDB), not on a server: share one as a link or an exported file.
                    </InfoTooltip>
                    {board && <SaveViewButton pageId="workbench" title={`Workbench: ${board.name}`} />}
                </h2>
                <p className="wb-note-line">
                    Boards live in this browser only: they aren’t sent to a server, won’t follow you to another device, and
                    clearing this site’s data deletes them. Export a board to keep a copy.
                </p>

                {shared.state !== 'none' && (
                    <SharedPreview shared={shared} onAdd={onAddShared} onClose={() => setShared({ state: 'none' })} />
                )}
                {error && <p className="wb-error" role="alert">{error}</p>}
                {missing && (
                    <p className="wb-warn" role="status">
                        The board in this link isn’t in this browser (boards are stored per browser; it may have been
                        deleted or saved on another device). {board ? `Showing “${board.name}” instead.` : ''}
                    </p>
                )}

                <div className="wb-picker" role="group" aria-label="Your boards">
                    {boards.map((b) => (
                        <button key={b.id} type="button" className={`wb-pill${board && b.id === board.id ? ' wb-pill--on' : ''}`}
                            aria-pressed={!!board && b.id === board.id}
                            onClick={() => { setSel(b.id); setConfirmDelete(false); setUndo(null); }}>
                            {b.name}
                            <span className="wb-pill-n">{b.blocks.length}</span>
                        </button>
                    ))}
                    <button type="button" className="table-export-btn" onClick={onNewBoard}><Icon name="add" size={15} /> New board</button>
                    <button type="button" className="table-export-btn" onClick={() => fileInput.current?.click()}><Icon name="upload_file" size={15} /> Import file</button>
                    <input ref={fileInput} type="file" accept="application/json,.json" style={{ display: 'none' }} onChange={onImport} />
                    {boards.length > 1 && (
                        <button type="button" className="table-export-btn" onClick={() => doExport(true)}><Icon name="download" size={15} /> Export all</button>
                    )}
                </div>

                <p className="wb-msg" role="status" style={{ color: msg.bad ? 'var(--negative)' : undefined }}>
                    {msg.text}
                    {undo && (
                        <>
                            {' '}Removed “{undo.label}”{undo.set ? ` and its set “${undo.set.name}”` : ''}.{' '}
                            <button type="button" className="wb-link" onClick={onUndo}>Undo</button>
                        </>
                    )}
                </p>

                {!loaded && <p className="empty-message">Loading your boards…</p>}

                {loaded && !error && !board && (
                    <div className="wb-empty">
                        <p className="wb-empty-title">Add your first block</p>
                        <p>
                            Start with a <strong>Set</strong> of players or teams, then add a <strong>Table</strong> and pick
                            its stats. A <strong>Note</strong> holds your own text. Charts and the app’s tools come as blocks next.
                        </p>
                        {addButtons}
                    </div>
                )}

                {board && (
                    <>
                        <div className="wb-board-head">
                            <BoardName key={board.id} board={board} onSave={(v) => run(() => renameBoard(board.id, v))} />
                            <div className="wb-board-actions">
                                {addButtons}
                                <button type="button" className="table-export-btn" onClick={onShare}><Icon name="share" size={15} /> Share link</button>
                                <button type="button" className="table-export-btn" onClick={() => doExport(false)}><Icon name="download" size={15} /> Export file</button>
                                {confirmDelete ? (
                                    <span className="wb-confirm">
                                        Delete “{board.name}” and its {board.blocks.length} block{board.blocks.length === 1 ? '' : 's'}?
                                        <button type="button" className="table-export-btn" onClick={onDelete}>Delete</button>
                                        <button type="button" className="table-export-btn" onClick={() => setConfirmDelete(false)}>Keep</button>
                                    </span>
                                ) : (
                                    <button type="button" className="table-export-btn" onClick={() => setConfirmDelete(true)}><Icon name="delete" size={15} /> Delete board</button>
                                )}
                            </div>
                        </div>
                        {catalogue.error && <p className="wb-error" role="alert">{catalogue.error}</p>}
                        {board.blocks.length === 0 ? (
                            <div className="wb-empty">
                                <p className="wb-empty-title">This board is empty</p>
                                <p>Add a <strong>Set</strong> of players or teams first, then a <strong>Table</strong> bound to it.</p>
                                {addButtons}
                            </div>
                        ) : (
                            <BoardGrid
                                key={board.id}
                                blocks={board.blocks}
                                narrow={narrow}
                                onCommit={commitLayout}
                                renderHeader={renderHeader}
                                renderBody={renderBody}
                                labelOf={labelOf}
                            />
                        )}
                    </>
                )}
            </div>
        </div>
    );
}
