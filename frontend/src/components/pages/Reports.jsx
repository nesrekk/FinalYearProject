import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import {
    createReport, deleteReport, exportReportsJson, getActiveReportId, importReportsJson, insertItem, moveItem,
    newTextItem, removeItem, renameReport, setActiveReportId, subscribeReports, updateItem, useReports,
} from '../../utils/reportStore';
import { downloadText, slugify } from '../../utils/tableExport';
import { isPlainClick, openFullUrl, parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/report.css';

function fmtDate(iso) {
    try {
        return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    } catch {
        return iso;
    }
}

// Text that saves itself: when the box loses focus, and a moment after typing
// stops, so a click on Print or a nav link right after typing keeps the text.
function useAutosave(value, save) {
    const [draft, setDraft] = useState(value);
    const timer = useRef(null);
    const latest = useRef({ draft, value, save });
    useLayoutEffect(() => { latest.current = { draft, value, save }; });

    const flush = () => {
        clearTimeout(timer.current);
        const { draft: d, value: v, save: s } = latest.current;
        if (d !== v) s(d);
    };
    const change = (next) => {
        setDraft(next);
        clearTimeout(timer.current);
        timer.current = setTimeout(flush, 700);
    };
    useEffect(() => () => flush(), []);
    return [draft, change, flush];
}

// A chart is only ever shown through <img>: a stored (or imported) SVG can't
// run script there. The snapshot's own background sits behind it.
function ChartSnapshot({ svg, label }) {
    const src = useMemo(() => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg.markup)}`, [svg.markup]);
    return (
        <div className="report-chart" style={{ background: svg.bg }}>
            <img src={src} width={svg.width} height={svg.height} alt={label || 'Chart snapshot'} />
        </div>
    );
}

function TableSnapshot({ table }) {
    return (
        <div className="report-table-wrap">
            <table className="report-table">
                <thead>
                    <tr>{table.header.map((h, i) => <th key={i}>{h}</th>)}</tr>
                </thead>
                <tbody>
                    {table.rows.map((row, r) => (
                        <tr key={r}>{row.map((c, i) => <td key={i}>{c}</td>)}</tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function ItemBar({ item, index, count, label, onMove, onRemove, onAddText }) {
    const kind = item.type === 'chart' ? 'Chart' : item.type === 'table' ? 'Table' : 'Text';
    return (
        <div className="report-item-bar report-noprint">
            <span className="report-item-kind">{kind} {index + 1} of {count}</span>
            <span className="report-item-buttons">
                <button type="button" className="table-export-btn" data-item-id={item.id} data-move="up"
                    onClick={() => onMove('up')} disabled={index === 0} aria-label={`Move “${label}” up`}>
                    <Icon name="arrow_upward" size={15} /> Up
                </button>
                <button type="button" className="table-export-btn" data-item-id={item.id} data-move="down"
                    onClick={() => onMove('down')} disabled={index === count - 1} aria-label={`Move “${label}” down`}>
                    <Icon name="arrow_downward" size={15} /> Down
                </button>
                <button type="button" className="table-export-btn" onClick={onAddText} aria-label={`Add a text section after “${label}”`}>
                    <Icon name="notes" size={15} /> Text below
                </button>
                <button type="button" className="table-export-btn" onClick={onRemove} aria-label={`Remove “${label}” from the report`}>
                    <Icon name="delete" size={15} /> Remove
                </button>
            </span>
        </div>
    );
}

function DataItem({ item, ...bar }) {
    const [caption, setCaption, flushCaption] = useAutosave(item.caption, (v) => bar.onSave({ caption: v }));
    const source = item.source || { url: '/', page: '', title: '' };
    return (
        <article className={`report-item report-item--data report-item--${item.type}`}>
            <ItemBar item={item} label={caption || source.title || 'item'} {...bar} />
            <input
                className="report-caption-input report-noprint"
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
                onBlur={flushCaption}
                aria-label={`Caption for ${item.type} ${bar.index + 1}`}
                placeholder="Caption"
                maxLength={400}
            />
            {caption && <h3 className="report-caption-print">{caption}</h3>}
            {item.type === 'chart'
                ? <ChartSnapshot svg={item.svg} label={caption || source.title} />
                : <TableSnapshot table={item.table} />}
            <p className="report-source">
                <span>Source: {source.page || 'NBA Hub'}</span>
                <span className="report-source-sep"> · </span>
                <span>captured {fmtDate(item.added)}</span>
                <a
                    className="report-source-link report-noprint"
                    href={source.url}
                    onClick={(e) => { if (isPlainClick(e)) { e.preventDefault(); openFullUrl(source.url); } }}
                >
                    <Icon name="open_in_new" size={14} /> Open live view
                </a>
                <span className="report-source-url report-printonly">{window.location.origin}{source.url}</span>
            </p>
        </article>
    );
}

function TextItem({ item, ...bar }) {
    const [heading, setHeading, flushHeading] = useAutosave(item.caption, (v) => bar.onSave({ caption: v }));
    const [body, setBody, flushBody] = useAutosave(item.body, (v) => bar.onSave({ body: v }));
    return (
        <article className="report-item report-item--text">
            <ItemBar item={item} label={heading || 'text section'} {...bar} />
            <input
                className="report-caption-input report-noprint"
                value={heading}
                onChange={(e) => setHeading(e.target.value)}
                onBlur={flushHeading}
                aria-label={`Heading for text section ${bar.index + 1}`}
                placeholder="Heading (optional)"
                maxLength={400}
            />
            <textarea
                className="report-text-input report-noprint"
                value={body}
                onChange={(e) => setBody(e.target.value)}
                onBlur={flushBody}
                aria-label={`Text for section ${bar.index + 1}`}
                placeholder="Write your own notes here: what the charts above and below show, and what you conclude from them."
                rows={4}
                maxLength={40000}
            />
            {heading && <h3 className="report-caption-print">{heading}</h3>}
            {body && <p className="report-text-print">{body}</p>}
        </article>
    );
}

export default function Reports() {
    const { reports, loaded, error } = useReports();
    const params = useInitialParams();
    const [sel, setSel] = useState(() => parseParam.str(params, 'r'));
    const [active, setActive] = useState(getActiveReportId);
    const [msg, setMsg] = useState({ text: '', bad: false });
    const [undo, setUndo] = useState(null);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const fileInput = useRef(null);
    const pendingFocus = useRef(null);

    useEffect(() => subscribeReports(() => setActive(getActiveReportId())), []);

    const report = reports.find((r) => r.id === sel) || reports.find((r) => r.id === active) || reports[0] || null;
    const isActive = report && report.id === active;
    const items = report ? report.items : [];

    useUrlSync(loaded && report ? { r: report.id } : null);

    // After Up/Down the button that was pressed has moved: put keyboard
    // focus back on it (or on its partner when it hit the top/bottom).
    useEffect(() => {
        const want = pendingFocus.current;
        if (!want) return;
        pendingFocus.current = null;
        const pick = (dir) => document.querySelector(`[data-item-id="${want.id}"][data-move="${dir}"]:not(:disabled)`);
        (pick(want.dir) || pick(want.dir === 'up' ? 'down' : 'up'))?.focus();
    }, [reports]);

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

    const onNew = async () => {
        const created = await run(() => createReport(`Report ${reports.length + 1}`));
        if (created) {
            setSel(created.id);
            setConfirmDelete(false);
            if (!getActiveReportId()) { setActiveReportId(created.id); }
        }
    };

    const onDelete = async () => {
        const id = report.id;
        const ok = await run(async () => { await deleteReport(id); return true; }, 'Report deleted.');
        if (ok) {
            setSel(null);
            setConfirmDelete(false);
            setUndo(null);
        }
    };

    const onImport = async (e) => {
        const file = e.target.files?.[0];
        e.target.value = '';
        if (!file) return;
        const result = await run(async () => importReportsJson(await file.text()));
        if (!result) return;
        const { added, skipped, droppedItems, firstAdded } = result;
        setMsg({
            text: `Imported ${added} report${added === 1 ? '' : 's'}${skipped ? ` (${skipped} already here or invalid)` : ''}${droppedItems ? `; ${droppedItems} unusable item${droppedItems === 1 ? ' was' : 's were'} left out` : ''}.`,
            bad: false,
        });
        if (firstAdded) setSel(firstAdded);
    };

    const doExport = (all) => {
        const list = all ? reports : [report];
        const stamp = new Date().toISOString().slice(0, 10);
        const base = all ? 'nba-hub-reports' : `nba-hub-report-${slugify(report.name)}`;
        downloadText(exportReportsJson(list), `${base}-${stamp}.json`, 'application/json');
    };

    const itemHandlers = (item, index) => ({
        index,
        count: items.length,
        onSave: (patch) => run(() => updateItem(report.id, item.id, patch)),
        onMove: (dir) => {
            pendingFocus.current = { id: item.id, dir };
            run(() => moveItem(report.id, item.id, dir));
        },
        onRemove: async () => {
            const saved = await run(() => removeItem(report.id, item.id));
            if (saved) setUndo({ reportId: report.id, item, index, label: item.caption || (item.type === 'text' ? 'text section' : item.type) });
        },
        onAddText: () => run(() => insertItem(report.id, newTextItem(), index + 1)),
    });

    const onUndo = async () => {
        const u = undo;
        setUndo(null);
        await run(() => insertItem(u.reportId, u.item, u.index), 'Item restored.');
    };

    return (
        <div className="page page-report fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title report-noprint">
                    <span className="card-icon"><Icon name="description" fill /></span>
                    Report builder
                    <InfoTooltip label="How this works" title="Building a report">
                        Click <strong>Report</strong> next to the PNG/SVG buttons on any chart, or the CSV/JSON buttons
                        on any table, to add a snapshot of it here: the chart or rows as they were when you clicked,
                        with the page it came from and the date. Charts are always captured in the light (Paper)
                        theme so they print cleanly. Give each item a caption, put your own text between them, reorder
                        with the Up/Down buttons, then Print / Save as PDF. Snapshots don't update when the data
                        does; "Open live view" goes to the current version. Everything is stored in this browser
                        (IndexedDB), not on a server: export to JSON to back a report up or move it.
                    </InfoTooltip>
                </h2>
                <p className="report-note report-noprint">
                    Reports live in this browser only. They are not sent to a server, won't follow you to another
                    device, and clearing this site's data deletes them. Export to JSON to keep a copy.
                </p>

                {error && <p className="table-export-error" role="alert">{error}</p>}

                <div className="report-picker report-noprint" role="group" aria-label="Your reports">
                    {reports.map((r) => (
                        <button
                            key={r.id}
                            type="button"
                            className={`report-pill${report && r.id === report.id ? ' report-pill--on' : ''}`}
                            aria-pressed={report && r.id === report.id}
                            onClick={() => { setSel(r.id); setConfirmDelete(false); setUndo(null); }}
                        >
                            {r.name}
                            <span className="report-pill-n">{r.items.length}</span>
                            {r.id === active && <span className="report-pill-tag">collecting</span>}
                        </button>
                    ))}
                    <button type="button" className="table-export-btn" onClick={onNew}>
                        <Icon name="add" size={15} /> New report
                    </button>
                    <button type="button" className="table-export-btn" onClick={() => fileInput.current?.click()}>
                        <Icon name="upload_file" size={15} /> Import JSON
                    </button>
                    <input ref={fileInput} type="file" accept="application/json,.json" style={{ display: 'none' }} onChange={onImport} />
                    {reports.length > 1 && (
                        <button type="button" className="table-export-btn" onClick={() => doExport(true)}>
                            <Icon name="download" size={15} /> Export all
                        </button>
                    )}
                </div>

                <p className="report-msg report-noprint" role="status" style={{ color: msg.bad ? 'var(--negative)' : undefined }}>
                    {msg.text}
                    {undo && (
                        <>
                            {' '}Removed “{undo.label}”.{' '}
                            <button type="button" className="report-add-link" onClick={onUndo}>Undo</button>
                        </>
                    )}
                </p>

                {!loaded && <p className="empty-message">Loading your reports…</p>}

                {loaded && !error && reports.length === 0 && (
                    <p className="empty-message report-noprint">
                        No reports yet. Click <strong>Report</strong> next to the export buttons on any chart or table
                        (Rotations, RAPM, Season Simulator, Shot Charts, the player profile and most other pages) and
                        the first report is created for you, or press <strong>New report</strong>.
                    </p>
                )}

                {report && (
                    <section className="report-doc" aria-label={`Report: ${report.name}`}>
                        <div className="report-head report-noprint">
                            <ReportName key={report.id} report={report} onSave={(v) => run(() => renameReport(report.id, v))} />
                            <div className="report-head-actions">
                                {isActive ? (
                                    <span className="report-collecting"><Icon name="check_circle" size={15} /> New items are added here</span>
                                ) : (
                                    <button type="button" className="table-export-btn"
                                        onClick={() => setActiveReportId(report.id)}>
                                        <Icon name="playlist_add" size={15} /> Add new items here
                                    </button>
                                )}
                                <button type="button" className="table-export-btn" onClick={() => window.print()} disabled={items.length === 0}>
                                    <Icon name="print" size={15} /> Print / Save as PDF
                                </button>
                                <button type="button" className="table-export-btn" onClick={() => doExport(false)}>
                                    <Icon name="download" size={15} /> Export JSON
                                </button>
                                <button type="button" className="table-export-btn" onClick={() => run(() => insertItem(report.id, newTextItem()))}>
                                    <Icon name="notes" size={15} /> Add text
                                </button>
                                {confirmDelete ? (
                                    <span className="report-confirm">
                                        Delete this report and its {items.length} item{items.length === 1 ? '' : 's'}?
                                        <button type="button" className="table-export-btn" onClick={onDelete}>Delete</button>
                                        <button type="button" className="table-export-btn" onClick={() => setConfirmDelete(false)}>Keep</button>
                                    </span>
                                ) : (
                                    <button type="button" className="table-export-btn" onClick={() => setConfirmDelete(true)}>
                                        <Icon name="delete" size={15} /> Delete report
                                    </button>
                                )}
                            </div>
                        </div>

                        <header className="report-print-title report-printonly">
                            <h1>{report.name}</h1>
                            <p>NBA Hub · {fmtDate(new Date().toISOString())} · {items.length} item{items.length === 1 ? '' : 's'}</p>
                        </header>

                        {items.length === 0 ? (
                            <p className="empty-message report-noprint">
                                This report is empty. Go to any chart or table and click <strong>Report</strong> next to its export buttons
                                {isActive ? '' : ' (after choosing “Add new items here” for this report)'}.
                            </p>
                        ) : (
                            <div className="report-items">
                                {items.map((item, i) => (
                                    item.type === 'text'
                                        ? <TextItem key={item.id} item={item} {...itemHandlers(item, i)} />
                                        : <DataItem key={item.id} item={item} {...itemHandlers(item, i)} />
                                ))}
                            </div>
                        )}
                    </section>
                )}
            </div>
        </div>
    );
}

function ReportName({ report, onSave }) {
    const [name, setName, flush] = useAutosave(report.name, (v) => (v.trim() ? onSave(v) : undefined));
    return (
        <input
            className="report-name-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={flush}
            aria-label="Report name"
            maxLength={120}
        />
    );
}
