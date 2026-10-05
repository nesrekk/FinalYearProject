import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchBacktestDetail, fetchBacktestComparison, fetchShapCandidates, fetchShapBreakdown, fetchAllNBABacktest, fetchWpaValidation, fetchWpaModelCompare } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import TableExport from './common/TableExport';
import ChartExport from './common/ChartExport';
import { bySign, signed } from '../utils/format';

const ROC_SIZE = 320;
const ROC_PAD = 36;
const ROC_PLOT = ROC_SIZE - 2 * ROC_PAD;

function rocX(fpr) { return ROC_PAD + fpr * ROC_PLOT; }
function rocY(tpr) { return ROC_SIZE - ROC_PAD - tpr * ROC_PLOT; }

function RocCurveChart({ points, auc, color, exportName }) {
    const svgRef = useRef(null);
    if (!points?.length) return null;
    const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${rocX(p.fpr).toFixed(1)} ${rocY(p.tpr).toFixed(1)}`).join(' ');
    const areaPath = `${linePath} L ${rocX(1).toFixed(1)} ${rocY(0).toFixed(1)} L ${rocX(0).toFixed(1)} ${rocY(0).toFixed(1)} Z`;
    const ticks = [0, 0.25, 0.5, 0.75, 1];
    return (
        <div>
            <ChartExport svgRef={svgRef} name={exportName ?? 'ROC curve'} />
            <svg ref={svgRef} viewBox={`0 0 ${ROC_SIZE} ${ROC_SIZE}`} style={{ maxWidth: 320, display: 'block' }} role="img" aria-label={`ROC curve plotting true positive rate against false positive rate across every decision threshold, pooled across held-out seasons, with an AUC of ${auc?.toFixed(3) ?? 'unavailable'}; the dashed diagonal marks what random guessing would trace`}>
            <rect x="0" y="0" width={ROC_SIZE} height={ROC_SIZE} fill="var(--surface-2)" rx="8" />
            {/* Random-guess diagonal baseline */}
            <line x1={rocX(0)} y1={rocY(0)} x2={rocX(1)} y2={rocY(1)} stroke="var(--hairline)" strokeWidth="1" strokeDasharray="4 3" />
            {ticks.map((t) => (
                <React.Fragment key={t}>
                    <line x1={rocX(t)} y1={rocY(0)} x2={rocX(t)} y2={rocY(1)} stroke="var(--hairline)" strokeWidth="1" />
                    <line x1={rocX(0)} y1={rocY(t)} x2={rocX(1)} y2={rocY(t)} stroke="var(--hairline)" strokeWidth="1" />
                    <text x={rocX(t)} y={ROC_SIZE - ROC_PAD + 16} fill="var(--text-3)" fontSize="10" textAnchor="middle">{t}</text>
                    <text x={ROC_PAD - 8} y={rocY(t) + 3} fill="var(--text-3)" fontSize="10" textAnchor="end">{t}</text>
                </React.Fragment>
            ))}
            <path d={areaPath} fill={color} fillOpacity={0.12} stroke="none" />
            <path d={linePath} fill="none" stroke={color} strokeWidth="2.5" />
            <text x={ROC_SIZE / 2} y={ROC_SIZE - 6} fill="var(--text-2)" fontSize="11" textAnchor="middle">False Positive Rate</text>
            <text x="12" y={ROC_SIZE / 2} fill="var(--text-2)" fontSize="11" textAnchor="middle" transform={`rotate(-90 12 ${ROC_SIZE / 2})`}>True Positive Rate</text>
            <text x={ROC_SIZE - ROC_PAD} y={ROC_PAD - 12} fill="var(--text)" fontSize="13" fontWeight="700" textAnchor="end">
                AUC = {auc?.toFixed(3) ?? '—'}
            </text>
        </svg>
        </div>
    );
}

function ReliabilityChart({ bins, color, label }) {
    const svgRef = useRef(null);
    if (!bins?.length) return null;
    const maxN = Math.max(...bins.map((b) => b.n));
    const ticks = [0, 0.25, 0.5, 0.75, 1];
    const linePath = bins
        .map((b, i) => `${i === 0 ? 'M' : 'L'} ${rocX(b.predicted_mid).toFixed(1)} ${rocY(b.observed_rate).toFixed(1)}`)
        .join(' ');
    return (
        <div>
            <ChartExport svgRef={svgRef} name={label ? `calibration ${label}` : 'calibration'} />
            <svg ref={svgRef} viewBox={`0 0 ${ROC_SIZE} ${ROC_SIZE}`} style={{ maxWidth: 320, display: 'block' }} role="img" aria-label={`Reliability (calibration) chart of predicted win probability versus real observed win rate${label ? ` for ${label}` : ''}, with dot size showing sample size per bucket; points on the dashed diagonal are well-calibrated`}>
            <rect x="0" y="0" width={ROC_SIZE} height={ROC_SIZE} fill="var(--surface-2)" rx="8" />
            {/* Perfect-calibration diagonal */}
            <line x1={rocX(0)} y1={rocY(0)} x2={rocX(1)} y2={rocY(1)} stroke="var(--hairline)" strokeWidth="1" strokeDasharray="4 3" />
            {ticks.map((t) => (
                <React.Fragment key={t}>
                    <line x1={rocX(t)} y1={rocY(0)} x2={rocX(t)} y2={rocY(1)} stroke="var(--hairline)" strokeWidth="1" />
                    <line x1={rocX(0)} y1={rocY(t)} x2={rocX(1)} y2={rocY(t)} stroke="var(--hairline)" strokeWidth="1" />
                    <text x={rocX(t)} y={ROC_SIZE - ROC_PAD + 16} fill="var(--text-3)" fontSize="10" textAnchor="middle">{t}</text>
                    <text x={ROC_PAD - 8} y={rocY(t) + 3} fill="var(--text-3)" fontSize="10" textAnchor="end">{t}</text>
                </React.Fragment>
            ))}
            <path d={linePath} fill="none" stroke={color} strokeWidth="2" />
            {bins.map((b) => (
                <circle
                    key={b.bucket_lo}
                    cx={rocX(b.predicted_mid)}
                    cy={rocY(b.observed_rate)}
                    r={3 + 6 * Math.sqrt(b.n / maxN)}
                    fill={color}
                    fillOpacity={0.85}
                />
            ))}
            <text x={ROC_SIZE / 2} y={ROC_SIZE - 6} fill="var(--text-2)" fontSize="11" textAnchor="middle">Predicted Win Probability</text>
            <text x="12" y={ROC_SIZE / 2} fill="var(--text-2)" fontSize="11" textAnchor="middle" transform={`rotate(-90 12 ${ROC_SIZE / 2})`}>Real Observed Win Rate</text>
            {label && (
                <text x={ROC_SIZE - ROC_PAD} y={ROC_PAD - 12} fill="var(--text)" fontSize="12" fontWeight="700" textAnchor="end">
                    {label}
                </text>
            )}
        </svg>
        </div>
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
                <span style={{ fontSize: '0.8rem', color: bySign(shapValue * 100, 1, 'var(--positive)', 'var(--negative)', undefined), flexShrink: 0 }}>
                    {signed(shapValue * 100, 1, '-')}pp
                </span>
            </div>
            <div style={{ position: 'relative', height: 10, background: 'rgba(100,116,139,0.15)', borderRadius: 4 }}>
                <div
                    style={{
                        position: 'absolute', top: 0, bottom: 0,
                        left: positive ? '50%' : `${50 - widthPct / 2}%`,
                        width: `${widthPct / 2}%`,
                        background: positive ? 'var(--positive)' : 'var(--negative)',
                        borderRadius: 3,
                    }}
                />
                <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: 'var(--text-3)' }} />
            </div>
        </div>
    );
}

const AWARDS = [
    { id: 'mvp', label: 'MVP' },
    { id: 'dpoy', label: 'DPOY' },
    { id: 'roy', label: 'ROY' },
    { id: 'allnba', label: 'All-NBA' },
    { id: 'wpa', label: 'Clutch WPA' },
];

const WPA_SCOPE_META = {
    all_events: { label: 'All Held-Out Events', color: 'var(--compare-b)' },
    clutch_only: { label: 'Real Clutch Time Only', color: 'var(--streak)' },
};

function pct(v) {
    return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

function _season_label_client(season) {
    return season ? `${season - 1}-${String(season).slice(-2)}` : '';
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

    const [allNbaDetail, setAllNbaDetail] = useState(null);
    const [allNbaSeason, setAllNbaSeason] = useState(null);

    const [wpaValidation, setWpaValidation] = useState(null);
    const [wpaCompare, setWpaCompare] = useState(null);

    const isAllNba = award === 'allnba';
    const isWpa = award === 'wpa';

    // Comparison table (all models for the current award) — also drives
    // which model types are available to pick from. All-NBA and WPA each
    // have their own dedicated fetch below, so skip this for them.
    useEffect(() => {
        if (isAllNba || isWpa) { setComparison(null); return; }
        let active = true;
        fetchBacktestComparison(award)
            .then((data) => { if (active) setComparison(data); })
            .catch(() => { if (active) setComparison(null); });
        return () => { active = false; };
    }, [award, isAllNba, isWpa]);

    // Detail (per-season table + feature importance) for the selected model.
    useEffect(() => {
        if (isAllNba || isWpa) { setDetail(null); setLoading(false); return; }
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
    }, [award, model, isAllNba, isWpa]);

    // All-NBA's own backtest fetch — different shape (precision@15 across
    // 16 seasons, not rank-of-single-winner), so it doesn't share the
    // detail/comparison effects above.
    useEffect(() => {
        if (!isAllNba) { setAllNbaDetail(null); return; }
        let active = true;
        setLoading(true);
        setError('');
        fetchAllNBABacktest()
            .then((data) => {
                if (!active) return;
                setAllNbaDetail(data);
                setAllNbaSeason(data.summary?.holdout_season?.season ?? null);
            })
            .catch((e) => {
                if (!active) return;
                setAllNbaDetail(null);
                setError(e?.response?.data?.detail || 'No All-NBA backtest results found. Run scripts/build_all_nba_model.py first.');
            })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [isAllNba]);

    // Clutch-Time WPA's own calibration validation fetch — reliability
    // curves and Brier/log-loss, not a rank-of-winner backtest, so it gets
    // its own effect and render branch rather than reusing the award ones.
    useEffect(() => {
        if (!isWpa) { setWpaValidation(null); return; }
        let active = true;
        setLoading(true);
        setError('');
        fetchWpaValidation()
            .then((data) => { if (active) setWpaValidation(data); })
            .catch((e) => {
                if (!active) return;
                setWpaValidation(null);
                setError(e?.response?.data?.detail || 'No WPA validation found. Run scripts/train_wpa_model.py first.');
            })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [isWpa]);

    // WPA model comparison (deployed Logistic Regression vs. an evaluated
    // Gradient Boosting alternative) — separate, non-critical fetch so a
    // failure here doesn't block the main calibration view above.
    useEffect(() => {
        if (!isWpa) { setWpaCompare(null); return; }
        let active = true;
        fetchWpaModelCompare()
            .then((data) => { if (active) setWpaCompare(data); })
            .catch(() => { if (active) setWpaCompare(null); });
        return () => { active = false; };
    }, [isWpa]);

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
            <h2 className="card-title hb-page-title">
                <span className="card-icon"><Icon name="science" /></span>
                Model Validation
                <InfoTooltip label="How this backtest works" title="Leave-one-season-out validation">
                    For each historical season, every model is retrained on the other seasons only and
                    asked to predict the one it has never seen — the same situation as predicting an
                    upcoming season. This is different from (and more honest than) just re-scoring a
                    model against data it already trained on, which tends to look artificially good.
                </InfoTooltip>
                <SourceBadge source={detail?._source || allNbaDetail?._source || wpaValidation?._source} />
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
                <>
                    <TableExport />
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
                                            {m.model_type === bestModelType ? <Icon name="military_tech" size="0.9em" style={{ marginLeft: 4 }} /> : ''}
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
                </>
            )}
            {!isAllNba && !isWpa && (
                <p className="page-subtitle" style={{ marginTop: '-0.75rem', marginBottom: '1rem' }}>
                    Click a row to see that model's per-season detail below. The <Icon name="military_tech" size="0.9em" /> badge marks the best top-5 accuracy for {award.toUpperCase()}.
                </p>
            )}

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {!loading && isAllNba && allNbaDetail && (
                <>
                    <p className="page-subtitle" style={{ marginBottom: '1rem' }}>
                        All-NBA is a 15-winner-per-season award (First/Second/Third Team, 5 each), so
                        "rank of the one true winner" doesn't apply the way it does for MVP/DPOY/ROY — the
                        honest metric here is precision@15: of the 15 players predicted for a held-out
                        season, how many were actually selected that season.
                    </p>
                    <div className="stat-cards-row">
                        <div className="stat-card">
                            <div className="stat-card-label">Mean Precision@15</div>
                            <div className="stat-card-value">{pct(allNbaDetail.summary.mean_precision_at_15)}</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-card-label">ROC-AUC</div>
                            <div className="stat-card-value">{allNbaDetail.summary.roc_auc?.toFixed(3) ?? '—'}</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-card-label">Seasons Evaluated</div>
                            <div className="stat-card-value">{allNbaDetail.summary.n_seasons_evaluated}</div>
                        </div>
                        {allNbaDetail.summary.holdout_season && (
                            <div className="stat-card">
                                <div className="stat-card-label">2024-25 Holdout (real, unseen)</div>
                                <div className="stat-card-value">{allNbaDetail.summary.holdout_season.hits}/15</div>
                            </div>
                        )}
                    </div>

                    {allNbaDetail.summary.roc_curve && (
                        <>
                            <h3 className="section-heading" style={{ marginTop: '1rem' }}>ROC Curve</h3>
                            <p className="page-subtitle" style={{ marginTop: '-0.25rem', marginBottom: '0.75rem' }}>
                                Pooled across every held-out season's out-of-fold predictions — the dashed
                                diagonal is what random guessing would trace.
                            </p>
                            <RocCurveChart points={allNbaDetail.summary.roc_curve} auc={allNbaDetail.summary.roc_auc} color="var(--streak)" exportName="All-NBA ROC curve" />
                        </>
                    )}

                    <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>Per-Season Detail</h3>
                    <p className="page-subtitle" style={{ marginTop: '-0.25rem', marginBottom: '0.75rem' }}>
                        Click a season to see its predicted top 15 below. ✓ marks a player who was actually selected that season.
                    </p>
                    <TableExport />
                    <div className="table-wrapper" style={{ marginBottom: '1rem' }}>
                        <table className="data-table">
                            <thead>
                                <tr><th>Season</th><th>Hits</th><th>Precision@15</th></tr>
                            </thead>
                            <tbody>
                                {allNbaDetail.seasons.map((s) => (
                                    <tr
                                        key={s.season}
                                        onClick={() => setAllNbaSeason(s.season)}
                                        style={{ cursor: 'pointer' }}
                                        className={s.season === allNbaSeason ? 'text-accent' : ''}
                                    >
                                        <td>{s.season === allNbaSeason ? '▶ ' : ''}{s.season_label}</td>
                                        <td>{s.hits}/15</td>
                                        <td>{pct(s.precision_at_15)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    {(() => {
                        const selected = allNbaDetail.summary.holdout_season?.season === allNbaSeason
                            ? allNbaDetail.summary.holdout_season
                            : allNbaDetail.seasons.find((s) => s.season === allNbaSeason);
                        if (!selected) return null;
                        return (
                            <>
                                <h3 className="section-heading">
                                    {_season_label_client(selected.season)} Predicted Top 15
                                    {allNbaDetail.summary.holdout_season?.season === selected.season ? ' (real holdout — never trained on)' : ''}
                                </h3>
                                <TableExport />
                                <div className="table-wrapper" style={{ marginBottom: '1rem' }}>
                                    <table className="data-table">
                                        <thead>
                                            <tr><th>#</th><th>Player</th><th>Probability</th><th>Actual Selection?</th></tr>
                                        </thead>
                                        <tbody>
                                            {selected.top15.map((p, i) => (
                                                <tr key={p.player_name}>
                                                    <td>{i + 1}</td>
                                                    <td>{p.player_name}</td>
                                                    <td>{(p.probability * 100).toFixed(1)}%</td>
                                                    <td className={p.actual_selection ? 'text-accent' : ''}>{p.actual_selection ? '✓' : '—'}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </>
                        );
                    })()}

                    <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>Feature Importance</h3>
                    <p className="page-subtitle" style={{ marginTop: '-0.25rem', marginBottom: '0.75rem' }}>
                        Standardized logistic regression coefficients from the full-data model — sign shows direction, magnitude shows influence.
                    </p>
                    <TableExport />
                    <div className="table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr><th>Feature</th><th>Coefficient</th></tr>
                            </thead>
                            <tbody>
                                {allNbaDetail.summary.feature_importance.map((f) => (
                                    <tr key={f.feature}>
                                        <td>{f.feature}</td>
                                        <td className={bySign(f.value, 3, 'text-accent', 'error-message')}>
                                            {signed(f.value, 3, '-')}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}

            {!loading && isWpa && wpaValidation && (
                <>
                    <p className="page-subtitle" style={{ marginBottom: '1rem' }}>
                        The Clutch-Time WPA model outputs a real win probability at every point in a game, so
                        "rank of the true winner" doesn't apply here either — the honest metric is calibration:
                        when the model says a team has a 70% chance to win, did that real team actually win
                        about 70% of the real time it said so?
                    </p>

                    {wpaCompare && (
                        <div style={{ marginBottom: '1.5rem' }}>
                            <h3 className="section-heading" style={{ marginTop: 0 }}>Model Comparison</h3>
                            <TableExport />
                            <div className="table-wrapper">
                                <table className="data-table">
                                    <thead>
                                        <tr><th>Model</th><th>ROC-AUC (all events)</th><th>ROC-AUC (clutch only)</th><th>Deployed</th></tr>
                                    </thead>
                                    <tbody>
                                        {wpaCompare.models.map((m) => (
                                            <tr key={m.model_type}>
                                                <td>{m.model_label}</td>
                                                <td>{m.scopes.all_events?.roc_auc?.toFixed(4) ?? '—'}</td>
                                                <td>{m.scopes.clutch_only?.roc_auc?.toFixed(4) ?? '—'}</td>
                                                <td>{m.model_type === 'logreg_calibrated' ? 'Yes' : 'No'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                                Same real held-out-by-game test split for both. Gradient Boosting performs
                                essentially identically here (ROC-AUC within ~0.001 in both scopes, not
                                meaningfully better) — the hand-engineered interaction
                                feature (margin / √time-remaining) already captures the nonlinearity a tree
                                model would otherwise need to learn on its own, so there's little real room
                                left for a more complex model to improve on. The deployed model stays Logistic
                                Regression: equal accuracy, and win probability is looked up one event at a
                                time in tight loops when replaying a full game, where its closed-form sigmoid
                                is meaningfully faster than scoring a boosted-tree ensemble per event.
                            </p>
                        </div>
                    )}
                    <div className="stat-cards-row">
                        <div className="stat-card">
                            <div className="stat-card-label">Games Trained On</div>
                            <div className="stat-card-value">{wpaValidation.n_games_train}</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-card-label">Held-Out Games</div>
                            <div className="stat-card-value">{wpaValidation.n_games_test}</div>
                        </div>
                    </div>
                    <p className="page-subtitle" style={{ marginTop: '0.75rem', marginBottom: '1.25rem' }}>
                        {wpaValidation.methodology}
                    </p>

                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2rem' }}>
                        {Object.entries(wpaValidation.scopes).map(([scopeKey, scope]) => {
                            const meta = WPA_SCOPE_META[scopeKey] || { label: scopeKey, color: 'var(--compare-b)' };
                            return (
                                <div key={scopeKey}>
                                    <h3 className="section-heading" style={{ marginTop: 0 }}>{meta.label}</h3>
                                    <div className="stat-cards-row" style={{ marginBottom: '0.75rem' }}>
                                        <div className="stat-card">
                                            <div className="stat-card-label">ROC-AUC</div>
                                            <div className="stat-card-value">{scope.roc_auc?.toFixed(3) ?? '—'}</div>
                                        </div>
                                        <div className="stat-card">
                                            <div className="stat-card-label">Brier Score</div>
                                            <div className="stat-card-value">{scope.brier_score?.toFixed(3) ?? '—'}</div>
                                        </div>
                                        <div className="stat-card">
                                            <div className="stat-card-label">Log Loss</div>
                                            <div className="stat-card-value">{scope.log_loss?.toFixed(3) ?? '—'}</div>
                                        </div>
                                    </div>
                                    <p className="page-subtitle" style={{ marginBottom: '0.5rem' }}>
                                        {scope.n_events.toLocaleString()} real held-out events. Dot size = real
                                        sample size in that bucket; points on the dashed diagonal are well-calibrated.
                                    </p>
                                    <ReliabilityChart bins={scope.reliability_bins} color={meta.color} label={meta.label} />
                                </div>
                            );
                        })}
                    </div>
                    {wpaValidation.scopes.all_events && wpaValidation.scopes.clutch_only && (
                        <p className="page-subtitle" style={{ marginTop: '1.25rem' }}>
                            Brier score and log loss aren't directly comparable between the two scopes above —
                            real clutch-time predictions cluster closer to 50/50 by definition (that's what makes
                            a game close), which is a harder prediction problem and pushes both scores up even
                            for a well-calibrated model. The reliability points themselves, not these two
                            aggregate scores, are the real evidence for calibration quality in each scope.
                        </p>
                    )}
                </>
            )}

            {!loading && !isAllNba && !isWpa && detail && (
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
                            <RocCurveChart points={detail.summary.roc_curve} auc={detail.summary.roc_auc} color="var(--compare-b)" exportName={`${detail.summary.model_label} ROC curve`} />
                        </>
                    )}

                    <TableExport name={`${detail.summary.model_label} per-season detail`} />
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
                    <TableExport />
                    <div className="table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr><th>Feature</th><th>{detail.summary.feature_importance?.[0]?.signed ? 'Coefficient' : 'Importance'}</th></tr>
                            </thead>
                            <tbody>
                                {detail.summary.feature_importance.map((f) => (
                                    <tr key={f.feature}>
                                        <td>{f.feature}</td>
                                        <td className={!f.signed ? 'text-accent' : bySign(f.value, 3, 'text-accent', 'error-message')}>
                                            {f.signed ? signed(f.value, 3, '-') : f.value.toFixed(3)}
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
                                <div style={{ background: 'var(--surface-2)', border: '2px solid var(--line)', borderRadius: 0, padding: '1rem' }}>
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
