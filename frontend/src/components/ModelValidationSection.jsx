import React, { useEffect, useMemo, useState } from 'react';
import { fetchBacktestDetail, fetchBacktestComparison, fetchShapCandidates, fetchShapBreakdown } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';

const ROC_SIZE = 320;
const ROC_PAD = 36;
const ROC_PLOT = ROC_SIZE - 2 * ROC_PAD;

function rocX(fpr) { return ROC_PAD + fpr * ROC_PLOT; }
function rocY(tpr) { return ROC_SIZE - ROC_PAD - tpr * ROC_PLOT; }

function RocCurveChart({ points, auc, color }) {
    if (!points?.length) return null;
    const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${rocX(p.fpr).toFixed(1)} ${rocY(p.tpr).toFixed(1)}`).join(' ');
    const areaPath = `${linePath} L ${rocX(1).toFixed(1)} ${rocY(0).toFixed(1)} L ${rocX(0).toFixed(1)} ${rocY(0).toFixed(1)} Z`;
    const ticks = [0, 0.25, 0.5, 0.75, 1];
    return (
        <svg viewBox={`0 0 ${ROC_SIZE} ${ROC_SIZE}`} style={{ maxWidth: 320, display: 'block' }}>
            <rect x="0" y="0" width={ROC_SIZE} height={ROC_SIZE} fill="#1a2332" rx="8" />
            {/* Random-guess diagonal baseline */}
            <line x1={rocX(0)} y1={rocY(0)} x2={rocX(1)} y2={rocY(1)} stroke="#475569" strokeWidth="1" strokeDasharray="4 3" />
            {ticks.map((t) => (
                <React.Fragment key={t}>
                    <line x1={rocX(t)} y1={rocY(0)} x2={rocX(t)} y2={rocY(1)} stroke="#26344a" strokeWidth="1" />
                    <line x1={rocX(0)} y1={rocY(t)} x2={rocX(1)} y2={rocY(t)} stroke="#26344a" strokeWidth="1" />
                    <text x={rocX(t)} y={ROC_SIZE - ROC_PAD + 16} fill="#64748b" fontSize="10" textAnchor="middle">{t}</text>
                    <text x={ROC_PAD - 8} y={rocY(t) + 3} fill="#64748b" fontSize="10" textAnchor="end">{t}</text>
                </React.Fragment>
            ))}
            <path d={areaPath} fill={color} fillOpacity={0.12} stroke="none" />
            <path d={linePath} fill="none" stroke={color} strokeWidth="2.5" />
            <text x={ROC_SIZE / 2} y={ROC_SIZE - 6} fill="#94a3b8" fontSize="11" textAnchor="middle">False Positive Rate</text>
            <text x="12" y={ROC_SIZE / 2} fill="#94a3b8" fontSize="11" textAnchor="middle" transform={`rotate(-90 12 ${ROC_SIZE / 2})`}>True Positive Rate</text>
            <text x={ROC_SIZE - ROC_PAD} y={ROC_PAD - 12} fill={color} fontSize="13" fontWeight="700" textAnchor="end">
                AUC = {auc?.toFixed(3) ?? '—'}
            </text>
        </svg>
    );
}

function ShapBar({ feature, featureValue, shapValue, maxAbs }) {
    // Two stacked rows (label+value on top, bar on its own full-width row)
    // rather than a 3-column layout — a fixed-width label/value column pair
    // can exceed the panel's actual width on narrow viewports, which
    // collapses a flexible middle column to 0 and makes the bar disappear
    // entirely. Stacking guarantees the bar always gets 100% of the width.
    const positive = shapValue >= 0;
    const widthPct = maxAbs ? Math.min(100, (Math.abs(shapValue) / maxAbs) * 100) : 0;
    return (
        <div style={{ marginBottom: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 3 }}>
                <span className="page-subtitle" style={{ fontSize: '0.8rem' }}>{feature} ({featureValue})</span>
                <span style={{ fontSize: '0.8rem', color: positive ? '#34d399' : '#f87171', flexShrink: 0 }}>
                    {positive ? '+' : ''}{(shapValue * 100).toFixed(1)}pp
                </span>
            </div>
            <div style={{ position: 'relative', height: 10, background: 'rgba(100,116,139,0.15)', borderRadius: 4 }}>
                <div
                    style={{
                        position: 'absolute', top: 0, bottom: 0,
                        left: positive ? '50%' : `${50 - widthPct / 2}%`,
                        width: `${widthPct / 2}%`,
                        background: positive ? '#34d399' : '#f87171',
                        borderRadius: 3,
                    }}
                />
                <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: '#475569' }} />
            </div>
        </div>
    );
}

const AWARDS = [
    { id: 'mvp', label: 'MVP' },
    { id: 'dpoy', label: 'DPOY' },
    { id: 'roy', label: 'ROY' },
];

function pct(v) {
    return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

export default function ModelValidationSection() {
    const [award, setAward] = useState('mvp');
    const [comparison, setComparison] = useState(null);
    const [model, setModel] = useState('logreg');
    const [detail, setDetail] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const [shapCandidates, setShapCandidates] = useState(null);
    const [shapPlayer, setShapPlayer] = useState('');
    const [shapDetail, setShapDetail] = useState(null);
    const [shapError, setShapError] = useState('');

    // Comparison table (all models for the current award) — also drives
    // which model types are available to pick from.
    useEffect(() => {
        let active = true;
        fetchBacktestComparison(award)
            .then((data) => { if (active) setComparison(data); })
            .catch(() => { if (active) setComparison(null); });
        return () => { active = false; };
    }, [award]);

    // Detail (per-season table + feature importance) for the selected model.
    useEffect(() => {
        let active = true;
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                const data = await fetchBacktestDetail(award, model);
                if (active) setDetail(data);
            } catch (e) {
                if (!active) return;
                setDetail(null);
                setError(e?.response?.data?.detail || 'No backtest results found. Run scripts/backtest_models.py first.');
            } finally {
                if (active) setLoading(false);
            }
        }, 0);
        return () => {
            active = false;
            clearTimeout(timer);
        };
    }, [award, model]);

    // SHAP candidate list for the current award (Random Forest only —
    // Logistic Regression's coefficients already explain every prediction).
    useEffect(() => {
        setShapPlayer('');
        setShapDetail(null);
        setShapError('');
        if (model !== 'random_forest') { setShapCandidates(null); return; }
        let active = true;
        fetchShapCandidates(award)
            .then((data) => { if (active) setShapCandidates(data); })
            .catch(() => { if (active) setShapCandidates(null); });
        return () => { active = false; };
    }, [award, model]);

    useEffect(() => {
        if (!shapPlayer) { setShapDetail(null); return; }
        let active = true;
        const timer = setTimeout(async () => {
            setShapError('');
            try {
                const data = await fetchShapBreakdown(award, shapPlayer);
                if (active) setShapDetail(data);
            } catch (e) {
                if (!active) return;
                setShapDetail(null);
                setShapError(e?.response?.data?.detail || 'No SHAP breakdown available.');
            }
        }, 0);
        return () => {
            active = false;
            clearTimeout(timer);
        };
    }, [award, shapPlayer]);

    // Default to the top candidate once the list loads.
    useEffect(() => {
        if (shapCandidates?.candidates?.length && !shapPlayer) {
            setShapPlayer(shapCandidates.candidates[0].player_name);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [shapCandidates]);

    const models = useMemo(() => comparison?.models ?? [], [comparison]);

    // If the current model isn't available for this award, fall back to
    // whatever the comparison list actually has (keeps award/model in sync).
    useEffect(() => {
        if (!models.length) return;
        if (!models.some((m) => m.model_type === model)) {
            setModel(models[0].model_type);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [models]);

    const avgCandidates = useMemo(() => {
        const seasons = detail?.seasons ?? [];
        if (!seasons.length) return '—';
        const avg = seasons.reduce((acc, s) => acc + (s.num_candidates || 0), 0) / seasons.length;
        return Math.round(avg);
    }, [detail]);

    const bestModelType = useMemo(() => {
        if (!models.length) return null;
        return models.reduce((best, m) => (
            (m.top5_accuracy ?? -1) > (best.top5_accuracy ?? -1) ? m : best
        ), models[0]).model_type;
    }, [models]);

    return (
        <section className="dashboard-card">
            <h2 className="card-title">
                <span className="card-icon">🧪</span>
                Model Validation
                <InfoTooltip label="How this backtest works" title="Leave-one-season-out validation">
                    For each historical season, every model is retrained on the other seasons only and
                    asked to predict the one it has never seen — the same situation as predicting an
                    upcoming season. This is different from (and more honest than) just re-scoring a
                    model against data it already trained on, which tends to look artificially good.
                </InfoTooltip>
            </h2>

            <div className="tab-bar" style={{ marginBottom: '1rem' }}>
                {AWARDS.map((a) => (
                    <button
                        key={a.id}
                        className={`tab-btn ${award === a.id ? 'tab-btn--active' : ''}`}
                        onClick={() => setAward(a.id)}
                    >
                        {a.label}
                    </button>
                ))}
            </div>

            {models.length > 0 && (
                <div className="table-wrapper" style={{ marginBottom: '1.25rem' }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Model</th>
                                <th>Top-1</th>
                                <th>Top-3</th>
                                <th>Top-5</th>
                                <th>MRR</th>
                                <th>ROC-AUC</th>
                            </tr>
                        </thead>
                        <tbody>
                            {models.map((m) => (
                                <tr
                                    key={m.model_type}
                                    onClick={() => setModel(m.model_type)}
                                    style={{ cursor: 'pointer' }}
                                    className={m.model_type === model ? 'text-accent' : ''}
                                >
                                    <td>
                                        {m.model_type === model ? '▶ ' : ''}{m.model_label}
                                        {m.model_type === bestModelType ? ' 🏅' : ''}
                                    </td>
                                    <td>{pct(m.top1_accuracy)}</td>
                                    <td>{pct(m.top3_accuracy)}</td>
                                    <td>{pct(m.top5_accuracy)}</td>
                                    <td>{m.mean_reciprocal_rank?.toFixed(3) ?? '—'}</td>
                                    <td>{m.roc_auc?.toFixed(3) ?? '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            <p className="page-subtitle" style={{ marginTop: '-0.75rem', marginBottom: '1rem' }}>
                Click a row to see that model's per-season detail below. 🏅 marks the best top-5 accuracy for {award.toUpperCase()}.
            </p>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {!loading && detail && (
                <>
                    <div className="stat-cards-row">
                        <div className="stat-card">
                            <div className="stat-card-label">Top-1 Accuracy</div>
                            <div className="stat-card-value">{pct(detail.summary.top1_accuracy)}</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-card-label">Top-3 Accuracy</div>
                            <div className="stat-card-value">{pct(detail.summary.top3_accuracy)}</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-card-label">Top-5 Accuracy</div>
                            <div className="stat-card-value">{pct(detail.summary.top5_accuracy)}</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-card-label">Mean Reciprocal Rank</div>
                            <div className="stat-card-value">{detail.summary.mean_reciprocal_rank?.toFixed(3) ?? '—'}</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-card-label">ROC-AUC</div>
                            <div className="stat-card-value">{detail.summary.roc_auc?.toFixed(3) ?? '—'}</div>
                        </div>
                    </div>

                    <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
                        {detail.summary.model_label} · {detail.summary.n_seasons_evaluated} seasons evaluated, leave-one-season-out.
                        Confusion matrix @ 0.5 threshold: TP {detail.summary.confusion_matrix.tp} · FN {detail.summary.confusion_matrix.fn} · FP {detail.summary.confusion_matrix.fp} · TN {detail.summary.confusion_matrix.tn}.
                        With roughly 1 winner per {avgCandidates} candidates a season, threshold precision/recall
                        are noisy — rank accuracy and ROC-AUC above are the more reliable signal.
                    </p>

                    {detail.summary.roc_curve && (
                        <>
                            <h3 className="section-heading" style={{ marginTop: '1rem' }}>ROC Curve</h3>
                            <p className="page-subtitle" style={{ marginTop: '-0.25rem', marginBottom: '0.75rem' }}>
                                Traces true-positive rate against false-positive rate across every possible decision
                                threshold, pooled across all held-out seasons — the dashed diagonal is what a model
                                with no real signal (random guessing) would trace. The curve bows toward the
                                top-left corner because the model is discriminating real signal from noise well
                                above chance, matching the {detail.summary.roc_auc?.toFixed(3) ?? '—'} AUC above.
                            </p>
                            <RocCurveChart points={detail.summary.roc_curve} auc={detail.summary.roc_auc} color="#38bdf8" />
                        </>
                    )}

                    <div className="table-wrapper" style={{ marginTop: '1rem' }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Season</th>
                                    <th>Actual Winner</th>
                                    <th>Predicted Rank</th>
                                    <th>Model's #1 Pick</th>
                                </tr>
                            </thead>
                            <tbody>
                                {detail.seasons.map((s) => {
                                    const topPick = s.top5?.[0];
                                    const gotIt = s.predicted_rank === 1;
                                    return (
                                        <tr key={s.season}>
                                            <td>{s.season_label}</td>
                                            <td>{s.actual_winner || '—'}</td>
                                            <td className={gotIt ? 'text-accent' : ''}>
                                                {s.predicted_rank ? `#${s.predicted_rank}` : 'not found'}
                                                {' '}/ {s.num_candidates}
                                            </td>
                                            <td>{gotIt ? '—' : (topPick ? `${topPick.player_name} (${(topPick.probability * 100).toFixed(1)}%)` : '—')}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>Feature Importance</h3>
                    <p className="page-subtitle" style={{ marginTop: '-0.25rem', marginBottom: '0.75rem' }}>
                        {detail.summary.feature_importance?.[0]?.signed
                            ? 'Standardized logistic regression coefficients from the full-data model — sign shows direction, magnitude shows influence.'
                            : "Random Forest feature importances from the full-data model (always positive — trees don't have a notion of direction, just how much a feature helps split the data)."}
                    </p>
                    <div className="table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr><th>Feature</th><th>{detail.summary.feature_importance?.[0]?.signed ? 'Coefficient' : 'Importance'}</th></tr>
                            </thead>
                            <tbody>
                                {detail.summary.feature_importance.map((f) => (
                                    <tr key={f.feature}>
                                        <td>{f.feature}</td>
                                        <td className={!f.signed || f.value >= 0 ? 'text-accent' : 'error-message'}>
                                            {f.signed && f.value >= 0 ? '+' : ''}{f.value.toFixed(3)}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    {model === 'random_forest' && (
                        <>
                            <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>
                                Explain a Prediction (SHAP)
                            </h3>
                            <p className="page-subtitle" style={{ marginTop: '-0.25rem', marginBottom: '0.75rem' }}>
                                Random Forest has no coefficients to point to — its prediction comes from voting
                                across 200 trees. SHAP decomposes one player's predicted probability into how much
                                each feature pushed it up (green) or down (red) from the model's base rate
                                ({shapCandidates ? (shapCandidates.base_value * 100).toFixed(1) : '—'}%),
                                for the {shapCandidates?.season ?? ''} prediction season.
                            </p>

                            {shapCandidates && (
                                <select
                                    className="input-field"
                                    value={shapPlayer}
                                    onChange={(e) => setShapPlayer(e.target.value)}
                                    style={{ marginBottom: '1rem' }}
                                >
                                    {shapCandidates.candidates.map((c) => (
                                        <option key={c.player_id} value={c.player_name}>
                                            {c.player_name} ({(c.predicted_probability * 100).toFixed(1)}%)
                                        </option>
                                    ))}
                                </select>
                            )}

                            {shapError && <p className="error-message">{shapError}</p>}

                            {shapDetail && (
                                <div style={{ background: '#1a2332', border: '1px solid #334155', borderRadius: 8, padding: '1rem' }}>
                                    <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>
                                        {shapDetail.player_name}: base rate {(shapDetail.base_value * 100).toFixed(1)}%
                                        {' '}→ predicted {(shapDetail.predicted_probability * 100).toFixed(1)}%
                                    </p>
                                    {shapDetail.features.map((f) => (
                                        <ShapBar
                                            key={f.feature}
                                            feature={f.feature}
                                            featureValue={f.feature_value}
                                            shapValue={f.shap_value}
                                            maxAbs={Math.max(...shapDetail.features.map((x) => Math.abs(x.shap_value)))}
                                        />
                                    ))}
                                </div>
                            )}
                        </>
                    )}
                </>
            )}
        </section>
    );
}
