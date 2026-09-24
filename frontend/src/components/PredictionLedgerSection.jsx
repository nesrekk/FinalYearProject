import React, { useEffect, useMemo, useState } from 'react';
import { fetchLedgerSummary } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';

const MODELS = [
    { id: 'mvp', label: 'MVP' },
    { id: 'dpoy', label: 'DPOY' },
    { id: 'roy', label: 'ROY' },
    { id: 'all_nba', label: 'All-NBA' },
];

const COLORS = ['#38bdf8', '#f87171', '#facc15', '#a78bfa', '#34d399'];

const CHART_W = 640;
const CHART_H = 220;
const PAD_L = 40;
const PAD_R = 12;
const PAD_T = 12;
const PAD_B = 24;
const PLOT_W = CHART_W - PAD_L - PAD_R;
const PLOT_H = CHART_H - PAD_T - PAD_B;

function fmtTime(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function TrajectoryChart({ candidates }) {
    if (!candidates?.length) return null;

    const allTimes = candidates.flatMap((c) => c.trajectory.map((p) => new Date(p.predicted_at).getTime()));
    const tMin = Math.min(...allTimes);
    const tMax = Math.max(...allTimes);
    const x = (t) => PAD_L + (tMax === tMin ? 0.5 : (t - tMin) / (tMax - tMin)) * PLOT_W;
    const y = (p) => PAD_T + (1 - p) * PLOT_H;

    return (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: '100%', display: 'block' }}>
            <rect x="0" y="0" width={CHART_W} height={CHART_H} fill="#1a2332" rx="8" />
            {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                <React.Fragment key={t}>
                    <line x1={PAD_L} y1={y(t)} x2={CHART_W - PAD_R} y2={y(t)} stroke="#26344a" strokeWidth="1" />
                    <text x={PAD_L - 6} y={y(t) + 3} fill="#64748b" fontSize="9" textAnchor="end">{Math.round(t * 100)}%</text>
                </React.Fragment>
            ))}
            {candidates.map((c, ci) => {
                const color = COLORS[ci % COLORS.length];
                const points = c.trajectory.map((p) => ({ x: x(new Date(p.predicted_at).getTime()), y: y(p.probability) }));
                if (points.length === 1) {
                    return <circle key={c.player_id} cx={points[0].x} cy={points[0].y} r={5} fill={color} stroke="#0f172a" strokeWidth="1.5" />;
                }
                const path = points.map((pt, i) => `${i === 0 ? 'M' : 'L'} ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`).join(' ');
                return (
                    <g key={c.player_id}>
                        <path d={path} fill="none" stroke={color} strokeWidth="2" />
                        <circle cx={points[points.length - 1].x} cy={points[points.length - 1].y} r={4} fill={color} stroke="#0f172a" strokeWidth="1.5" />
                    </g>
                );
            })}
            <text x={PAD_L} y={CHART_H - 4} fill="#64748b" fontSize="9">{fmtTime(new Date(tMin).toISOString())}</text>
            <text x={CHART_W - PAD_R} y={CHART_H - 4} fill="#64748b" fontSize="9" textAnchor="end">{fmtTime(new Date(tMax).toISOString())}</text>
        </svg>
    );
}

export default function PredictionLedgerSection() {
    const [model, setModel] = useState('mvp');
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        Promise.resolve().then(() => {
            if (!active) return;
            setLoading(true);
            setError('');
            fetchLedgerSummary()
                .then((d) => { if (active) setData(d); })
                .catch((e) => {
                    if (!active) return;
                    setError(e?.response?.data?.detail || 'No prediction ledger found. Run scripts/snapshot_predictions.py first.');
                })
                .finally(() => { if (active) setLoading(false); });
        });
        return () => { active = false; };
    }, []);

    const candidates = data?.live?.[model] || [];

    const resolvedByModel = useMemo(() => {
        if (!data?.resolved) return {};
        const grouped = {};
        data.resolved.forEach((r) => {
            (grouped[r.model] = grouped[r.model] || []).push(r);
        });
        return grouped;
    }, [data]);

    return (
        <section className="dashboard-card">
            <h2 className="card-title hb-page-title">
                <span className="card-icon"><Icon name="timeline" /></span>
                Prediction Ledger
                <InfoTooltip label="How this works" title="Real live predictions, logged and graded — not a backtest">
                    Every real logged snapshot (scripts/snapshot_predictions.py) records the live model's real
                    predicted probability for every real candidate at a real point in time. Once a season
                    actually finishes and a real winner/selection is recorded, scripts/resolve_predictions.py
                    grades every logged prediction for it with a real Brier score. This is separate from Model
                    Validation's leave-one-season-out backtests above — those are honest historical re-runs, this
                    is a real, growing record of what the live model actually said, checked against what actually
                    happened. It starts sparse and gets more useful the longer it runs.
                </InfoTooltip>
                <SourceBadge source={data?._source} />
            </h2>

            {data && (
                <p className="page-subtitle">
                    Current season {data.current_season} · {data.resolved.length === 0
                        ? 'no seasons resolved yet — check back once real award winners are recorded'
                        : `${data.resolved.length} resolved (model, season) result${data.resolved.length === 1 ? '' : 's'}`}
                </p>
            )}

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {data && (
                <>
                    <div className="tab-bar" style={{ marginTop: '0.75rem', marginBottom: '1rem' }}>
                        {MODELS.map((m) => (
                            <button
                                key={m.id}
                                className={`tab-btn ${model === m.id ? 'tab-btn--active' : ''}`}
                                onClick={() => setModel(m.id)}
                            >
                                {m.label}
                            </button>
                        ))}
                    </div>

                    <h3 className="section-heading" style={{ marginTop: 0 }}>
                        {MODELS.find((m) => m.id === model)?.label} Favorites — Real Probability Over Time
                    </h3>
                    {candidates.length === 0 && (
                        <p className="page-subtitle">No logged predictions yet for this model this season.</p>
                    )}
                    {candidates.length > 0 && (
                        <>
                            <TrajectoryChart candidates={candidates} />
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem 1.25rem', marginTop: '0.6rem' }}>
                                {candidates.map((c, i) => (
                                    <div key={c.player_id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                        <span style={{ width: 8, height: 8, borderRadius: '50%', background: COLORS[i % COLORS.length] }} />
                                        <PlayerHeadshot playerId={c.player_id} playerName={c.player_name} size={20} />
                                        <span className="page-subtitle" style={{ margin: 0, fontSize: '0.8rem' }}>
                                            {c.player_name} — {Math.round(c.latest_probability * 100)}%
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </>
                    )}

                    <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>Resolved Predictions</h3>
                    {(!resolvedByModel[model] || resolvedByModel[model].length === 0) ? (
                        <p className="page-subtitle">
                            No resolved {MODELS.find((m) => m.id === model)?.label} seasons yet — this fills in once a
                            season's real winner/selections are recorded and scripts/resolve_predictions.py runs.
                        </p>
                    ) : (
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr><th>Season</th><th>Candidates</th><th>Mean Brier Score</th><th>Resolved</th></tr>
                                </thead>
                                <tbody>
                                    {resolvedByModel[model].map((r) => (
                                        <tr key={`${r.model}-${r.season}`}>
                                            <td>{r.season - 1}-{String(r.season).slice(-2)}</td>
                                            <td>{r.n_candidates}</td>
                                            <td>{r.mean_brier_score?.toFixed(4) ?? '—'}</td>
                                            <td>{r.resolved_at ? new Date(r.resolved_at).toLocaleDateString() : '—'}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </>
            )}
        </section>
    );
}
