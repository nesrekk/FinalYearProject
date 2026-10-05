import React, { useEffect, useMemo, useState } from 'react';
import { fetchDataCoverage } from '../../services/api';
import Loader from '../Loader';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import '../../styles/coverage.css';
import { NAV_GROUPS } from '../layout/navConfig';
import { ANALYTICS_TABS } from '../analytics/analyticsTabs';

const fmtN = (n) => n.toLocaleString();

// Labels for `used_by` page ids (COVERAGE_MAP in api/routers/meta.py): nav labels, and an Analytics tab
// as 'analytics#<tab>'. A hand-kept list here missed eight ids and showed them raw ("analytics#onoff",
// "team"; R8-061). The player and team pages need an id, so they're named, not linked.
const NAV_LABELS = Object.fromEntries(
    NAV_GROUPS.flatMap((g) => g.items).filter((i) => i.id !== 'analytics').map((i) => [i.id, i.label]),
);
const TOOL_LABELS = Object.fromEntries(ANALYTICS_TABS.map((t) => [`analytics#${t.id}`, t.label]));
const STATIC_PAGES = { player: 'Player Profile', team: 'Team page' };
const pageLabel = (id) => STATIC_PAGES[id] || TOOL_LABELS[id] || NAV_LABELS[id] || id;

function CoverageRow({ row, onNavigate }) {
    return (
        <tr className={row.exists || row.local_only ? undefined : 'cov-missing'}>
            <td>
                <code className="cov-table">{row.table}</code>
                <div className="cov-label">{row.label}</div>
            </td>
            <td className="lb-num">{row.exists ? fmtN(row.n_rows) : '—'}</td>
            <td>
                {row.exists && (row.season_from ? `${row.season_from} → ${row.season_to}` : '—')}
                {/* A table kept in the local database only is absent from the cloud mirror on purpose (api/local_only.py). */}
                {!row.exists && (row.local_only ? <span className="cov-none">{row.note}</span> : 'table not found')}
            </td>
            <td className="cov-source">{row.source}</td>
            <td className="cov-gap">{row.gap || <span className="cov-none">No known gap</span>}</td>
            <td>
                {row.used_by.length === 0 && <span className="cov-none">Internal only</span>}
                {row.used_by.map((pageId) => (
                    STATIC_PAGES[pageId] ? (
                        <span key={pageId} className="cov-page-link cov-page-link--static">{pageLabel(pageId)}</span>
                    ) : (
                        <button
                            key={pageId}
                            type="button"
                            className="cov-page-link"
                            onClick={() => onNavigate(...pageId.split('#'))}
                        >
                            {pageLabel(pageId)}
                        </button>
                    )
                ))}
            </td>
        </tr>
    );
}

export default function DataCoverage({ onNavigate }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [group, setGroup] = useState('all');

    useEffect(() => {
        fetchDataCoverage()
            .then(setData)
            .catch((err) => setError(err.response?.data?.detail
                || 'Data coverage couldn\'t load. Is the impact API (port 8002) running?'));
    }, []);

    const rows = useMemo(() => {
        if (!data) return [];
        return group === 'all' ? data.tables : data.tables.filter((r) => r.group === group);
    }, [data, group]);

    if (error) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;
    if (!data) return <Loader />;

    const totalRows = data.tables.reduce((sum, r) => sum + (r.exists ? r.n_rows : 0), 0);
    const missing = data.tables.filter((r) => !r.exists && !r.local_only);
    const keptLocal = data.tables.filter((r) => !r.exists && r.local_only);

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Data Coverage
                <SourceBadge source={data._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="coverage" />
            </h2>
            <p className="page-subtitle">
                Every important table this app reads, with a live row count and season span (never hand-typed, so
                they can&apos;t drift from what&apos;s actually in the database), plus where the data came from and
                any known gap — the same wording used on the README and the Methodology page.
            </p>

            <div className="cov-summary">
                <div><span>Tables tracked</span><strong>{data.tables.length}</strong></div>
                <div><span>Total rows (tracked tables)</span><strong>{fmtN(totalRows)}</strong></div>
                <div><span>Tables with a disclosed gap</span><strong>{data.tables.filter((r) => r.gap).length}</strong></div>
            </div>

            {missing.length > 0 && (
                <p className="cov-alert">
                    {missing.length} table{missing.length > 1 ? 's' : ''} in this map {missing.length > 1 ? 'don\'t' : 'doesn\'t'} exist
                    in this database right now: {missing.map((r) => r.table).join(', ')}.
                </p>
            )}
            {keptLocal.length > 0 && (
                <p className="page-subtitle">
                    {keptLocal.map((r) => r.table).join(', ')}: {keptLocal[0].note} (the paper&apos;s per-prediction
                    rows; no page reads them).
                </p>
            )}

            <div className="tab-bar cov-tabs" role="tablist" aria-label="Filter by area">
                {['all', ...data.groups].map((g) => (
                    <button
                        key={g}
                        type="button"
                        role="tab"
                        aria-selected={group === g}
                        className={`tab-btn ${group === g ? 'tab-btn--active' : ''}`}
                        onClick={() => setGroup(g)}
                    >
                        {g === 'all' ? 'All' : g}
                    </button>
                ))}
            </div>

            <TableExport name="data coverage" />
            <div className="table-wrapper">
                <table className="data-table lb-table cov-table-el">
                    <thead>
                        <tr>
                            <th>Table</th>
                            <th className="lb-num">Rows</th>
                            <th>Season span</th>
                            <th>Source</th>
                            <th>Known gap</th>
                            <th>Used by</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => (
                            <CoverageRow key={row.table} row={row} onNavigate={onNavigate} />
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="cov-legend">
                Row counts, season spans and which tables exist are computed live against this database every time
                this page loads (cached per backend process — restart impact_api after a data rebuild to refresh
                them). Source and known-gap text are hand-maintained in <code>api/routers/meta.py</code>
                {' '}(<code>COVERAGE_MAP</code>) and kept in the same wording as README &quot;Known real gaps&quot;.
            </p>
        </section>
    );
}
