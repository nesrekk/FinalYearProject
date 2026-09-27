import React, { useEffect, useState } from 'react';
import {
    fetchAllNBABacktest, fetchBacktestOverview, fetchCurrentMeta, fetchMVPPrediction, fetchWpaValidation,
} from '../../services/api';
import Icon from '../common/Icon';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import { CHECKED_ON, OPEN_ISSUES, PRINCIPLES, SECTIONS } from './methodologyContent';
import '../../styles/methodology.css';

const pct = (v, d = 0) => `${(v * 100).toFixed(d)}%`;

function AwardsBacktest({ data, allNba }) {
    if (!data) return <p className="meth-live-note">Loading the live backtest…</p>;
    const rows = (data.awards || []).filter((a) => a.model_type === 'logreg');
    if (!rows.length) return <p className="meth-live-note">Backtest results couldn&apos;t be loaded.</p>;
    return (
        <>
            <TableExport name="award model backtest" />
            <div className="table-wrapper">
                <table className="data-table meth-table">
                    <thead>
                        <tr>
                            <th>Award</th>
                            <th>Seasons</th>
                            <th>Winner ranked 1st</th>
                            <th>Top 3</th>
                            <th>Top 5</th>
                            <th>ROC-AUC</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((a) => (
                            <tr key={a.award}>
                                <td>{a.award}</td>
                                <td>{a.n_seasons_evaluated}</td>
                                <td>{Math.round(a.top1_accuracy * a.n_seasons_evaluated)} of {a.n_seasons_evaluated} ({pct(a.top1_accuracy)})</td>
                                <td>{pct(a.top3_accuracy)}</td>
                                <td>{pct(a.top5_accuracy)}</td>
                                <td>{a.roc_auc.toFixed(3)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            {allNba?.summary && (
                <p className="meth-live-note">
                    All-NBA picks 15 players a season, so it&apos;s scored differently: over{' '}
                    {allNba.summary.n_seasons_evaluated} seasons, {pct(allNba.summary.mean_precision_at_15, 1)} of the
                    model&apos;s top 15 made an All-NBA team (ROC-AUC {allNba.summary.roc_auc.toFixed(3)}).
                </p>
            )}
            <p className="meth-live-note">
                Live from <code>/backtest</code> and <code>/backtest/allnba</code>, logistic models (the ones served).{' '}
                <SourceBadge source={data._source} />
            </p>
        </>
    );
}

function WpValidation({ data }) {
    if (!data) return <p className="meth-live-note">Loading the live validation…</p>;
    const s = data.scopes || {};
    const all = s.all_events;
    const clutch = s.clutch_only;
    if (!all) return <p className="meth-live-note">Validation results couldn&apos;t be loaded.</p>;
    return (
        <div className="meth-stats">
            <div><span>Held-out games</span><strong>{data.n_games_test?.toLocaleString()}</strong></div>
            <div><span>Held-out plays</span><strong>{all.n_events.toLocaleString()}</strong></div>
            <div><span>ROC-AUC, all plays</span><strong>{all.roc_auc.toFixed(3)}</strong></div>
            {clutch && <div><span>ROC-AUC, clutch time</span><strong>{clutch.roc_auc.toFixed(3)}</strong></div>}
            <div><span>Brier score</span><strong>{all.brier_score.toFixed(3)}</strong></div>
            <p className="meth-live-note">
                Live from <code>/validation/wpa</code>; trained on {data.n_games_train?.toLocaleString()} other games.{' '}
                <SourceBadge source={data._source} />
            </p>
        </div>
    );
}

function MvpTop({ data }) {
    const top = (data?.results || []).slice(0, 4);
    if (!top.length) return null;
    return (
        <p className="meth-live-note">
            Right now: {top.map((r, i) => (
                <span key={r.player_name}>{i ? ', ' : ''}{r.player_name} {pct(r.mvp_probability, 2)}</span>
            ))} (live, {data.season - 1}-{String(data.season).slice(-2)}).
        </p>
    );
}

// Plain scroll, not a hash link: the app keeps the URL hash for Analytics tabs.
function jumpTo(id) {
    document.getElementById(`meth-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function ModelCard({ item, live, onNavigate }) {
    const open = () => {
        onNavigate(item.open.page);
        if (item.open.hash) requestAnimationFrame(() => { window.location.hash = item.open.hash; });
    };
    return (
        <article className="meth-card" id={`m-${item.id}`}>
            <header className="meth-card-head">
                <h3>{item.name}</h3>
                <button type="button" className="meth-open" onClick={open}>
                    {item.open.label} <Icon name="arrow_forward" size={16} />
                </button>
            </header>
            <p className="meth-answers">{item.answers}</p>
            <dl className="meth-dl">
                <dt>How it works</dt>
                <dd>{item.method}</dd>
                <dt>How it was checked</dt>
                <dd>{item.checked}</dd>
                <dt>Known limits</dt>
                <dd>
                    <ul>
                        {item.limits.map((l) => <li key={l}>{l}</li>)}
                    </ul>
                </dd>
            </dl>
            {item.live === 'awards' && <AwardsBacktest data={live.backtest} allNba={live.allNba} />}
            {item.live === 'wp' && <WpValidation data={live.wp} />}
        </article>
    );
}

export default function Methodology({ onNavigate }) {
    const [live, setLive] = useState({ backtest: null, allNba: null, wp: null, mvp: null });

    useEffect(() => {
        let alive = true;
        const put = (key) => (value) => { if (alive) setLive((prev) => ({ ...prev, [key]: value })); };
        // Each live block fails on its own; the written content never depends on them.
        fetchBacktestOverview().then(put('backtest')).catch(() => put('backtest')({}));
        fetchAllNBABacktest().then(put('allNba')).catch(() => put('allNba')(null));
        fetchWpaValidation().then(put('wp')).catch(() => put('wp')({}));
        (async () => {
            const meta = await fetchCurrentMeta().catch(() => null);
            const start = meta?.season ?? new Date().getFullYear();
            // /meta/current can run ahead of the loaded data (same walk-back as the landing page).
            for (let s = start; s >= start - 3; s -= 1) {
                const res = await fetchMVPPrediction(s).catch(() => null);
                if (res?.results?.length) { put('mvp')(res); break; }
            }
        })();
        return () => { alive = false; };
    }, []);

    return (
        <div className="meth-page">
            <section className="dashboard-card meth-intro">
                <p className="page-subtitle">
                    How each model and index on this site works, how it was checked, and where it falls short.
                    Written numbers were checked on {CHECKED_ON}; the award and win-probability numbers load live.
                </p>
                <ul className="meth-principles">
                    {PRINCIPLES.map(([t, d]) => (
                        <li key={t}><strong>{t}.</strong> {d}</li>
                    ))}
                </ul>
                <nav className="meth-jump" aria-label="Jump to a section">
                    {[...SECTIONS, { id: 'issues', title: 'Open issues' }].map((sec) => (
                        <button key={sec.id} type="button" onClick={() => jumpTo(sec.id)}>{sec.title}</button>
                    ))}
                </nav>
            </section>

            {SECTIONS.map((s) => (
                <section key={s.id} id={`meth-${s.id}`} className="meth-section">
                    <h2 className="section-heading">{s.title}</h2>
                    <p className="page-subtitle">{s.blurb}</p>
                    <div className="meth-grid">
                        {s.items.map((item) => (
                            <ModelCard key={item.id} item={item} live={live} onNavigate={onNavigate} />
                        ))}
                    </div>
                </section>
            ))}

            <section id="meth-issues" className="meth-section">
                <h2 className="section-heading">Open issues</h2>
                <p className="page-subtitle">Problems found and not fixed yet. Each is removed from this list in the change that fixes it.</p>
                <div className="meth-grid">
                    {OPEN_ISSUES.map((o) => (
                        <article key={o.title} className="meth-card meth-issue">
                            <h3><Icon name="report" size={18} /> {o.title}</h3>
                            <p>{o.body}</p>
                            {o.live === 'mvpTop' && <MvpTop data={live.mvp} />}
                        </article>
                    ))}
                </div>
            </section>
        </div>
    );
}
