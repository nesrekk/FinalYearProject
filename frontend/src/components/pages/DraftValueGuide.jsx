import React, { useEffect, useRef, useState } from 'react';
import { fetchDraftClass, fetchDraftValueCurve, fetchDraftBestValue } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import Icon from '../common/Icon';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import ChartExport from '../common/ChartExport';

const CHART_W = 640, CHART_H = 260, PAD_L = 56, PAD_R = 16, PAD_T = 16, PAD_B = 40;

function fmt(v, digits = 1) {
    return v == null ? '—' : Number(v).toFixed(digits);
}

function ValueCurveChart({ buckets }) {
    const svgRef = useRef(null);
    const plotW = CHART_W - PAD_L - PAD_R;
    const plotH = CHART_H - PAD_T - PAD_B;
    const values = buckets.map((b) => b.avg_ws_first5).filter((v) => v != null);
    const maxVal = values.length ? Math.max(...values, 0) : 1;
    const minVal = Math.min(0, ...values);
    const span = maxVal - minVal || 1;
    const barW = plotW / buckets.length;

    const yFor = (v) => PAD_T + plotH - ((v - minVal) / span) * plotH;
    const zeroY = yFor(0);

    return (
        <div>
            <ChartExport svgRef={svgRef} name="draft value curve" />
            <svg ref={svgRef} viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label="Bar chart of average Win Shares in a player's first five NBA seasons by draft-pick range, falling as the pick gets later">
            <rect x="0" y="0" width={CHART_W} height={CHART_H} fill="var(--surface-2)" rx="8" />
            {[0, 0.25, 0.5, 0.75, 1].map((t) => {
                const v = minVal + t * span;
                const y = yFor(v);
                return (
                    <React.Fragment key={t}>
                        <line x1={PAD_L} y1={y} x2={CHART_W - PAD_R} y2={y} stroke="var(--hairline)" strokeWidth="1" />
                        <text x={PAD_L - 8} y={y + 3} fill="var(--text-3)" fontSize="10" textAnchor="end">{v.toFixed(1)}</text>
                    </React.Fragment>
                );
            })}
            {buckets.map((b, i) => {
                const x = PAD_L + i * barW + barW * 0.15;
                const w = barW * 0.7;
                const val = b.avg_ws_first5 ?? 0;
                const y = Math.min(yFor(val), zeroY);
                const h = Math.abs(yFor(val) - zeroY);
                return (
                    <React.Fragment key={b.range}>
                        <rect x={x} y={y} width={w} height={Math.max(h, 1)} fill="var(--accent)" rx="3">
                            <title>{`Picks ${b.range}: ${fmt(b.avg_ws_first5)} Win Shares in first 5 seasons on average (n=${b.n_players}; ${Math.round(b.share_zero * 100)}% at zero or less)`}</title>
                        </rect>
                        <text x={x + w / 2} y={CHART_H - PAD_B + 16} fill="var(--text-2)" fontSize="11" textAnchor="middle">
                            {b.range}
                        </text>
                        {b.avg_ws_first5 != null && (
                            <text x={x + w / 2} y={y - 6} fill="var(--text)" fontSize="11" textAnchor="middle">
                                {fmt(b.avg_ws_first5)}
                            </text>
                        )}
                    </React.Fragment>
                );
            })}
            <text x={CHART_W / 2} y={CHART_H - 6} fill="var(--text-2)" fontSize="11" textAnchor="middle">Overall Pick Range</text>
        </svg>
        </div>
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
                    <InfoTooltip label="How this works" title="What each pick actually produced">
                        Value is Basketball-Reference Win Shares in a player&apos;s first five NBA seasons
                        after the draft: the same window for every class, so old and recent picks compare
                        fairly. Picks who never played in the NBA count as zero. Charts and leaderboards use
                        the {curve ? `${curve.first_draft_year}–${curve.maturity_cutoff_draft_year}` : ''} draft
                        classes, the ones that have all had five seasons. Draft picks, Win Shares and All-Star
                        selections come from Basketball-Reference; photos need an NBA player id, which a few
                        older players couldn&apos;t be matched to safely.
                    </InfoTooltip>
                    <SourceBadge source={curve?._source} />
                </h2>

                {curveError && <p className="error-message">{curveError}</p>}
                {curve && (
                    <>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Win Shares in the First Five Seasons, by Pick
                        </h3>
                        <p className="page-subtitle" style={{ marginTop: 0 }}>
                            {curve.first_draft_year}–{curve.maturity_cutoff_draft_year} draft classes ·{' '}
                            {curve.buckets.map((b) => `${b.range}: ${Math.round(b.share_zero * 100)}% at zero or less`).join(' · ')}
                        </p>
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
                    <>
                        <TableExport />
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Player</th><th>Draft</th><th>Pick</th><th>Team</th>
                                        <th>WS, first 5 seasons</th><th>Slot average</th><th>vs Slot</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {leaderboard.results.map((r) => (
                                        <tr key={`${r.draft_year}-${r.overall_pick}`}>
                                            <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                            <td>{r.draft_year}</td>
                                            <td>#{r.overall_pick}</td>
                                            <td>{r.team_abbreviation}</td>
                                            <td>{fmt(r.ws_first5)}</td>
                                            <td>{fmt(r.expected_ws_first5)}</td>
                                            <td style={{ color: r.value_over_expectation >= 0 ? 'var(--positive)' : 'var(--negative)' }}>
                                                {r.value_over_expectation >= 0 ? '+' : ''}{fmt(r.value_over_expectation)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
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
                        min={1947}
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
                            {!draftClass.five_seasons_played && ' · * fewer than five seasons played so far'}
                        </p>
                        <TableExport />
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Pick</th><th>Player</th><th>From</th><th>Team</th>
                                        <th>NBA Seasons</th><th>WS, first 5</th><th>Career WS</th><th>All-Star</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {draftClass.results.map((p) => (
                                        <tr key={`${p.overall_pick ?? 'x'}-${p.player_name}`}>
                                            <td>{p.overall_pick != null ? `#${p.overall_pick}` : '—'}</td>
                                            <td><PlayerName playerId={p.player_id} name={p.player_name} /></td>
                                            <td>{p.organization || '—'}</td>
                                            <td>{p.team_abbreviation || '—'}</td>
                                            <td>{p.nba_seasons ?? '—'}</td>
                                            <td>{draftClass.five_seasons_played ? fmt(p.ws_first5) : `${fmt(p.ws_first5)}*`}</td>
                                            <td>{fmt(p.ws_career)}</td>
                                            <td>{p.all_star_selections || '—'}</td>
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
