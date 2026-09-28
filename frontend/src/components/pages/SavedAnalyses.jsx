import React, { useRef, useState, useSyncExternalStore } from 'react';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import {
    deleteSavedView, exportSavedViews, getSavedViews, importSavedViews,
    subscribeSavedViews, updateSavedView,
} from '../../utils/savedViews';
import { downloadText } from '../../utils/tableExport';
import { openFullUrl } from '../../utils/useUrlState';

function fmtDate(iso) {
    try {
        return new Date(iso).toLocaleString(undefined, {
            year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
        });
    } catch {
        return iso;
    }
}

// One saved analysis: its title and note are edited in place; "Open" loads
// the exact URL that was saved (page + every input it had at the time).
function SavedRow({ view }) {
    const [editing, setEditing] = useState(false);
    const [title, setTitle] = useState(view.title);
    const [note, setNote] = useState(view.note);

    const save = () => {
        updateSavedView(view.id, { title: title.trim() || view.title, note });
        setEditing(false);
    };

    return (
        <div className="saved-row">
            <div className="saved-row-main">
                {editing ? (
                    <input
                        className="saved-row-title-input"
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                        autoFocus
                    />
                ) : (
                    <button type="button" className="saved-row-title" onClick={() => openFullUrl(view.url)}>
                        {view.title}
                    </button>
                )}
                <span className="saved-row-date">{fmtDate(view.date)}</span>
            </div>
            {editing ? (
                <textarea
                    className="saved-row-note-input"
                    placeholder="Note (optional)"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                />
            ) : (
                view.note && <p className="saved-row-note">{view.note}</p>
            )}
            <div className="saved-row-actions">
                {editing ? (
                    <button type="button" className="table-export-btn" onClick={save}>
                        <Icon name="check" size={15} /> Done
                    </button>
                ) : (
                    <>
                        <button type="button" className="table-export-btn" onClick={() => openFullUrl(view.url)}>
                            <Icon name="open_in_new" size={15} /> Open
                        </button>
                        <button type="button" className="table-export-btn" onClick={() => setEditing(true)}>
                            <Icon name="edit" size={15} /> Edit
                        </button>
                        <button type="button" className="table-export-btn" onClick={() => deleteSavedView(view.id)}>
                            <Icon name="delete" size={15} /> Delete
                        </button>
                    </>
                )}
            </div>
        </div>
    );
}

export default function SavedAnalyses() {
    const views = useSyncExternalStore(subscribeSavedViews, getSavedViews);
    const fileInput = useRef(null);
    const [importMsg, setImportMsg] = useState('');

    const doExport = () => {
        const stamp = new Date().toISOString().slice(0, 10);
        downloadText(exportSavedViews(), `nba-hub-saved-analyses-${stamp}.json`, 'application/json');
    };

    const doImport = async (e) => {
        const file = e.target.files?.[0];
        e.target.value = '';
        if (!file) return;
        try {
            const parsed = JSON.parse(await file.text());
            const { added, skipped } = importSavedViews(parsed);
            setImportMsg(`Imported ${added} new saved view${added === 1 ? '' : 's'}${skipped ? ` (${skipped} already saved or invalid)` : ''}.`);
        } catch {
            setImportMsg('Could not read that file — expected JSON exported from this page.');
        }
    };

    return (
        <div className="page page-saved fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="bookmark" fill /></span>
                    Saved analyses
                    <InfoTooltip label="How this works" title="Your saved views">
                        Click "Save" next to any tool's Copy link button to keep an exact view here — the
                        page, its tab and every filter or input it had at the time. Saves live only in
                        this browser (localStorage), not on the server, so they won't follow you to
                        another device and clearing site data clears them. Export to JSON to back them
                        up or move them to another browser; importing merges in whatever's new.
                    </InfoTooltip>
                </h2>

                <div className="saved-toolbar">
                    <button type="button" className="table-export-btn" onClick={doExport} disabled={views.length === 0}>
                        <Icon name="download" size={15} /> Export JSON
                    </button>
                    <button type="button" className="table-export-btn" onClick={() => fileInput.current?.click()}>
                        <Icon name="upload_file" size={15} /> Import JSON
                    </button>
                    <input ref={fileInput} type="file" accept="application/json" style={{ display: 'none' }} onChange={doImport} />
                </div>
                {importMsg && <p className="saved-import-msg" role="status">{importMsg}</p>}

                {views.length === 0 ? (
                    <p className="empty-message">
                        Nothing saved yet. Click <strong>Save</strong> next to the Copy link button on any tool
                        (Leaderboard Builder, Trade Analyzer, Shot Charts, and more) to keep that exact view here.
                    </p>
                ) : (
                    <>
                        <p className="page-subtitle" style={{ marginTop: '0.25rem', marginBottom: '0.75rem' }}>
                            {views.length} saved view{views.length === 1 ? '' : 's'}.
                        </p>
                        <div className="saved-list">
                            {views.map((v) => <SavedRow key={v.id} view={v} />)}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
