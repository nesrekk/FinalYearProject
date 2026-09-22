import React, { useEffect, useState } from 'react';
import { fetchDraftClass, fetchDraftValueCurve, fetchDraftBestValue } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import Icon from '../common/Icon';

const CHART_W = 640, CHART_H = 260, PAD_L = 56, PAD_R = 16, PAD_T = 16, PAD_B = 40;

function fmt(v, digits = 2) {
    return v == null ? '—' : Number(v).toFixed(digits);
}

function ValueCurveChart({ buckets }) {
    const plotW = CHART_W - PAD_L - PAD_R;
    const plotH = CHART_H - PAD_T - PAD_B;
    const values = buckets.map((b) => b.avg_career_impact_raw).filter((v) => v != null);
    const maxVal = values.length ? Math.max(...values, 0) : 1;
    const minVal = Math.min(0, ...values);
    const span = maxVal - minVal || 1;
    const barW = plotW / buckets.length;

    const yFor = (v) => PAD_T + plotH - ((v - minVal) / span) * plotH;
    const zeroY = yFor(0);

    return (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
            <rect x="0" y="0" width={CHART_W} height={CHART_H} fill="#1a2332" rx="8" />
            {[0, 0.25, 0.5, 0.75, 1].map((t) => {
                const v = minVal + t * span;
                const y = yFor(v);
                return (
                    <React.Fragment key={t}>
                        <line x1={PAD_L} y1={y} x2={CHART_W - PAD_R} y2={y} stroke="#26344a" strokeWidth="1" />
                        <text x={PAD_L - 8} y={y + 3} fill="#64748b" fontSize="10" textAnchor="end">{v.toFixed(1)}</text>
                    </React.Fragment>
                );
            })}
            {buckets.map((b, i) => {
                const x = PAD_L + i * barW + barW * 0.15;
                const w = barW * 0.7;
                const val = b.avg_career_impact_raw ?? 0;
                const y = Math.min(yFor(val), zeroY);
                const h = Math.abs(yFor(val) - zeroY);
                return (
                    <React.Fragment key={b.range}>
                        <rect x={x} y={y} width={w} height={Math.max(h, 1)} fill="#38bdf8" rx="3">
                            <title>{`Picks ${b.range}: ${fmt(b.avg_career_impact_raw)} avg career impact (n=${b.n_players})`}</title>
                        </rect>
                        <text x={x + w / 2} y={CHART_H - PAD_B + 16} fill="#94a3b8" fontSize="11" textAnchor="middle">
                            {b.range}
                        </text>
                        {b.avg_career_impact_raw != null && (
                            <text x={x + w / 2} y={y - 6} fill="#38bdf8" fontSize="10" textAnchor="middle">
                                {fmt(b.avg_career_impact_raw)}
                            </text>
                        )}
                    </React.Fragment>
                );
            })}
            <text x={CHART_W / 2} y={CHART_H - 6} fill="#94a3b8" fontSize="11" textAnchor="middle">Overall Pick Range</text>
        </svg>
    );
}

export default function DraftValueGuide() {
    const [curve, setCurve] = useState(null);
    const [curveError, setCurveError] = useState('');

    const [mode, setMode] = useState('best'); // 'best' | 'worst'
    const [leaderboard, setLeaderboard] = useState(null);
    const [leaderboardError, setLeaderboardError] = useState('');

    const [draftYear, setDraftYear] = useState(2015);
    const [draftClass, setDraftClass] = useState(null);
    const [draftError, setDraftError] = useState('');
    const [loadingClass, setLoadingClass] = useState(false);

    useEffect(() => {
        fetchDraftValueCurve()
            .then(setCurve)
            .catch((e) => setCurveError(e?.response?.data?.detail || 'No draft data loaded yet.'));
    }, []);

    useEffect(() => {
        setLeaderboardError('');
        fetchDraftBestValue(15, mode === 'worst')
            .then(setLeaderboard)
            .catch((e) => { setLeaderboard(null); setLeaderboardError(e?.response?.data?.detail || 'No draft data loaded yet.'); });
    }, [mode]);

    async function loadDraftClass() {
        setLoadingClass(true);
        setDraftError('');
        setDraftClass(null);
        try {
            const data = await fetchDraftClass(draftYear);
            setDraftClass(data);
        } catch (e) {
            setDraftError(e?.response?.data?.detail || 'No data for that draft year.');
        } finally {
            setLoadingClass(false);
        }
    }

    useEffect(() => { loadDraftClass(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

    return (
        <div className="page page-draft fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="school" /></span>
                    Draft Value Analysis
                    <InfoTooltip label="How this works" title="Career value, not scouting">
                        "Value" here is impact_score_raw — the same efficiency-weighted composite
                        (TS%, net rating, usage, assist/rebound %, win%) already used everywhere else
                        in this project (Trade Analyzer sums it per roster) — summed across every
                        season a player has logged in this database. It's a real-outcomes value
                        estimate, not a pre-draft scouting guide: college/international prospect data
                        isn't part of this project, so nothing here evaluates players before they've
                        actually played in the NBA. Charts and leaderboards only include draft classes
                        through 2019-20 so every included player has had a fair number of seasons to
                        accumulate value — recent draft classes are too new to judge fairly.
                    </InfoTooltip>
                </h2>

                {curveError && <p className="error-message">{curveError}</p>}
                {curve && (
                    <>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Career Value by Pick Range
                        </h3>
                        <ValueCurveChart buckets={curve.buckets} />
                    </>
                )}
            </div>

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <h2 className="card-title">
                    <span className="card-icon"><Icon name="diamond" /></span>
                    Best Value / Most Underperformed
                </h2>
                <div className="tab-bar" style={{ marginBottom: '1rem' }}>
                    <button type="button" className={`tab-btn ${mode === 'best' ? 'tab-btn--active' : ''}`} onClick={() => setMode('best')}>Best Value</button>
                    <button type="button" className={`tab-btn ${mode === 'worst' ? 'tab-btn--active' : ''}`} onClick={() => setMode('worst')}>Underperformed Slot</button>
                </div>
                {leaderboardError && <p className="error-message">{leaderboardError}</p>}
                {leaderboard && (
                    <div className="table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Player</th><th>Draft</th><th>Pick</th><th>Team</th>
                                    <th>Career Impact</th><th>Expected (slot avg)</th><th>vs Expectation</th>
                                </tr>
                            </thead>
                            <tbody>
                                {leaderboard.results.map((r) => (
                                    <tr key={r.player_id}>
                                        <td>{r.player_name}</td>
                                        <td>{r.draft_year}</td>
                                        <td>#{r.overall_pick}</td>
                                        <td>{r.team_abbreviation}</td>
                                        <td>{fmt(r.career_impact_raw)}</td>
                                        <td>{fmt(r.expected_impact_raw)}</td>
                                        <td style={{ color: r.value_over_expectation >= 0 ? '#34d399' : '#f87171' }}>
                                            {r.value_over_expectation >= 0 ? '+' : ''}{fmt(r.value_over_expectation)}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <h2 className="card-title">
                    <span className="card-icon"><Icon name="list_alt" /></span>
                    Browse a Draft Class
                </h2>
                <div className="input-row">
                    <input
                        type="number"
                        className="input-field"
                        value={draftYear}
                        onChange={(e) => setDraftYear(Number(e.target.value))}
                        min={1990}
                        max={2025}
                    />
                    <button type="button" className="action-btn" onClick={loadDraftClass} disabled={loadingClass}>
                        {loadingClass ? 'Loading…' : 'Load Draft Class'}
                    </button>
                </div>
                {loadingClass && <Loader />}
                {draftError && <p className="error-message" style={{ marginTop: '0.5rem' }}>{draftError}</p>}
                {draftClass && !loadingClass && (
                    <>
                        <p className="page-subtitle" style={{ marginTop: '0.75rem', marginBottom: '0.75rem' }}>
                            {draftClass.draft_year} Draft ·
                            {' '}rookie season {draftClass.rookie_season_int - 1}-{String(draftClass.rookie_season_int).slice(-2)}
                        </p>
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Pick</th><th>Player</th><th>From</th><th>Team</th>
                                        <th>Seasons</th><th>Career Impact</th><th>Avg/Season</th><th>Star Seasons</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {draftClass.results.map((p) => (
                                        <tr key={p.player_id}>
                                            <td>{p.overall_pick != null ? `#${p.overall_pick}` : '—'}</td>
                                            <td>{p.player_name}</td>
                                            <td>{p.organization || '—'}</td>
                                            <td>{p.team_abbreviation || '—'}</td>
                                            <td>{p.seasons_played}</td>
                                            <td>{fmt(p.career_impact_raw)}</td>
                                            <td>{fmt(p.avg_impact_raw)}</td>
                                            <td>{p.star_seasons}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
