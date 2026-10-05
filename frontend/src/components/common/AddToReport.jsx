import React, { useEffect, useRef, useState } from 'react';
import Icon from './Icon';
import { NAV_GROUPS } from '../layout/navConfig';
import { ANALYTICS_TABS } from '../analytics/analyticsTabs';
import { addToActiveReport } from '../../utils/reportStore';
import { openPage, PAGE_PARAM } from '../../utils/useUrlState';

const NAV_LABELS = Object.fromEntries(NAV_GROUPS.flatMap((g) => g.items).map((i) => [i.id, i.label]));
const ANALYTICS_LABELS = Object.fromEntries(ANALYTICS_TABS.map((t) => [t.id, t.label]));

// What the page is called, for the line under a report item ("Source: …").
// Analytics keeps its tab in the #hash, which has no label of its own.
function sourcePageLabel() {
    const params = new URLSearchParams(window.location.search);
    const page = params.get(PAGE_PARAM) || '';
    const hash = window.location.hash.replace('#', '');
    if (page === 'player') return 'Player profile';
    if (page === 'team') return 'Team page';
    // The page header's own title (every page but the two hero pages has one).
    const heading = document.querySelector('.page-header .page-title')?.textContent.trim();
    const label = heading || NAV_LABELS[page] || page || 'NBA Hub';
    const tool = page === 'analytics' ? ANALYTICS_LABELS[hash] : null;
    if (tool) return `Analytics › ${tool}`;
    return page === 'analytics' && hash ? `${label} (${hash} tab)` : label;
}

// Export names are lower-case ("rapm vs bpm 2025-26"); the caption starts a
// sentence, with the acronyms this app uses kept in capitals.
const ACRONYMS = new Set(['rapm', 'bpm', 'vorp', 'dad', 'wpa', 'srs', 'nba', 'ncaa', 'epm']);
function sentenceCase(s) {
    if (!s) return s;
    const [first, ...rest] = s.split(' ');
    const head = ACRONYMS.has(first) ? first.toUpperCase() : first.charAt(0).toUpperCase() + first.slice(1);
    return [head, ...rest].join(' ');
}

// The button in the export toolbars (common/ChartExport.jsx, TableExport.jsx).
// Snapshots the chart or table exactly as rendered right now, with the URL it
// came from, into the report new items are collected in (utils/reportStore.js).
// `getSnapshot()` returns { title, svg } or { title, table }, or null when
// there is nothing to add yet.
export default function AddToReport({ kind, getSnapshot }) {
    const [status, setStatus] = useState(null); // { ok, text, reportId }
    const [busy, setBusy] = useState(false);
    const timer = useRef(null);

    useEffect(() => () => clearTimeout(timer.current), []);

    const say = (next, ms) => {
        setStatus(next);
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setStatus(null), ms);
    };

    const add = async () => {
        let snap;
        try {
            snap = getSnapshot();
        } catch {
            snap = null;
        }
        if (!snap) {
            say({ ok: false, text: 'Nothing to add yet.' }, 4000);
            return;
        }
        setBusy(true);
        try {
            const title = sentenceCase(snap.title);
            const report = await addToActiveReport({
                type: kind,
                caption: title,
                svg: snap.svg,
                table: snap.table,
                source: {
                    url: window.location.pathname + window.location.search + window.location.hash,
                    page: sourcePageLabel(),
                    title,
                },
            });
            say({ ok: true, text: `Added to “${report.name}”.`, reportId: report.id }, 8000);
        } catch (e) {
            say({ ok: false, text: e.message || 'Could not add this to a report.' }, 9000);
        } finally {
            setBusy(false);
        }
    };

    return (
        <>
            {status && (
                <span className={status.ok ? 'report-add-status' : 'table-export-error'} role="status">
                    {status.text}
                    {status.ok && (
                        <>
                            {' '}
                            <button type="button" className="report-add-link" onClick={() => openPage('report', { r: status.reportId })}>
                                Open report
                            </button>
                        </>
                    )}
                </span>
            )}
            <button
                type="button"
                className={kind === 'chart' ? 'chart-export-btn' : 'table-export-btn'}
                onClick={add}
                disabled={busy}
                aria-label={`Add this ${kind} to your report`}
            >
                <Icon name="playlist_add" size={15} /> Report
            </button>
        </>
    );
}
