import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchLengthStudy } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import ChartExport from './common/ChartExport';

const CHART_W = 480;
const CHART_H = 320;
const PAD_L = 46;
const PAD_R = 16;
const PAD_T = 16;
const PAD_B = 34;
const PLOT_W = CHART_W - PAD_L - PAD_R;
const PLOT_H = CHART_H - PAD_T - PAD_B;

function ScatterChart({ points, xKey, yKey, xLabel, yLabel, color }) {
    const svgRef = useRef(null);
    const xs = points.map((p) => p[xKey]);
    const ys = points.map((p) => p[yKey]);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const x = (v) => PAD_L + ((v - xMin) / ((xMax - xMin) || 1)) * PLOT_W;
    const y = (v) => PAD_T + (1 - (v - yMin) / ((yMax - yMin) || 1)) * PLOT_H;

    return (
        <div>
            <ChartExport svgRef={svgRef} name={`${xLabel} vs ${yLabel}`} />
            <svg ref={svgRef} viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: '100%', display: 'block' }} role="img" aria-label={`Scatter plot of ${xLabel} versus ${yLabel} for every player, showing the real correlation between wingspan-minus-height and defensive production`}>
                <rect x="0" y="0" width={CHART_W} height={CHART_H} fill="var(--surface-2)" rx="8" />
                {points.map((p) => (
                    <circle key={p.player_id} cx={x(p[xKey])} cy={y(p[yKey])} r={3} fill={color} fillOpacity={0.65}>
                        <title>{p.player_name}: {xLabel} {p[xKey]}, {yLabel} {p[yKey]}</title>
                    </circle>
                ))}
                <text x={CHART_W / 2} y={CHART_H - 6} fill="var(--text-2)" fontSize="11" textAnchor="middle">{xLabel}</text>
                <text x="12" y={CHART_H / 2} fill="var(--text-2)" fontSize="11" textAnchor="middle" transform={`rotate(-90 12 ${CHART_H / 2})`}>{yLabel}</text>
            </svg>
        </div>
    );
}

export default function LengthMattersCard() {
    const [metric, setMetric] = useState('avg_dbpm');
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        fetchLengthStudy()
            .then((d) => { if (active) setData(d); })
            .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load the length study.'); })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, []);

    const corr = useMemo(() => {
        if (!data) return null;
        return metric === 'avg_dbpm' ? data.correlations.wingspan_minus_height_vs_dbpm : data.correlations.wingspan_minus_height_vs_stocks_per36;
    }, [data, metric]);

    return (
        <div className="dashboard-card" style={{ marginTop: '1rem' }}>
            <h3 className="section-heading" style={{ marginTop: 0 }}>
                Does Length Matter?
                <InfoTooltip label="How this works" title="A real correlation, not a causal claim">
                    Real wingspan-minus-height (NBA Draft Combine) plotted against each real player's real
                    career-average defensive production (minutes-weighted across their whole real career),
                    for every real player with both real combine data and a real career minutes floor. Pearson
                    r and the real sample size are always shown. This is a real correlation, not a claim that
                    length causes good defense — effort, positioning, and IQ are real factors this doesn't
                    control for.
                </InfoTooltip>
            </h3>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {data && (
                <>
                    <div className="tab-bar" style={{ marginBottom: '0.75rem' }}>
                        <button
                            className={`tab-btn ${metric === 'avg_dbpm' ? 'tab-btn--active' : ''}`}
                            onClick={() => setMetric('avg_dbpm')}
                        >
                            vs. DBPM
                        </button>
                        <button
                            className={`tab-btn ${metric === 'stocks_per36' ? 'tab-btn--active' : ''}`}
                            onClick={() => setMetric('stocks_per36')}
                        >
                            vs. Stocks/36
                        </button>
                    </div>
                    <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>
                        n = {data.n} real players (min {data.min_total_minutes.toLocaleString()} real career minutes) ·
                        Pearson r = <strong style={{ color: 'var(--text-primary)' }}>{corr.r}</strong> (p = {corr.p_value})
                    </p>
                    <ScatterChart
                        points={data.points}
                        xKey="wingspan_minus_height"
                        yKey={metric}
                        xLabel="Wingspan − Height (in)"
                        yLabel={metric === 'avg_dbpm' ? 'Career Avg DBPM' : 'Career Stocks/36'}
                        color={metric === 'avg_dbpm' ? '#38bdf8' : '#facc15'}
                    />
                </>
            )}
        </div>
    );
}
