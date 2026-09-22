import React, { useEffect, useState } from 'react';
import { fetchROYPrediction, fetchSeasonSimilarity } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import Icon from '../common/Icon';

function fmt(v, digits = 1) {
    return v == null ? '—' : Number(v).toFixed(digits);
}

export default function RookieClassTracker() {
    const [season, setSeason] = useState(2026);
    const [rookies, setRookies] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const [selected, setSelected] = useState(null);
    const [comps, setComps] = useState(null);
    const [compsLoading, setCompsLoading] = useState(false);
    const [compsError, setCompsError] = useState('');

    async function loadClass() {
        setLoading(true);
        setError('');
        setRookies(null);
        setSelected(null);
        setComps(null);
        try {
            const data = await fetchROYPrediction(season, 100);
            setRookies(data);
        } catch (e) {
            setError(e?.response?.data?.detail || 'No rookie class data for that season.');
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { loadClass(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

    async function loadComps(playerName) {
        setSelected(playerName);
        setCompsLoading(true);
        setCompsError('');
        setComps(null);
        try {
            const data = await fetchSeasonSimilarity(playerName, season);
            setComps(data);
        } catch (e) {
            setCompsError(e?.response?.data?.detail || 'No comps available.');
        } finally {
            setCompsLoading(false);
        }
    }

    return (
        <div className="page page-rookies fade-in">
            <div className="dashboard-card">
                <h2 className="card-title">
                    <span className="card-icon"><Icon name="eco" /></span>
                    Rookie Class Tracker
                    <InfoTooltip label="How this works" title="Every rookie, tracked live">
                        "Rookie" here means the player's first season anywhere in this database — the
                        same definition the ROY model already uses, no separate draft data needed. ROY
                        probability comes straight from that model. Clicking a rookie shows their closest
                        historical season comp (any player, any season) from the season-similarity engine —
                        useful for "who does this rookie's season actually resemble so far," not a
                        guarantee of a similar career.
                    </InfoTooltip>
                </h2>

                <div className="input-row">
                    <input
                        type="number"
                        className="input-field"
                        value={season}
                        onChange={(e) => setSeason(Number(e.target.value))}
                        min={2010}
                        max={2026}
                    />
                    <button type="button" className="action-btn" onClick={loadClass} disabled={loading}>
                        {loading ? 'Loading…' : 'Load Rookie Class'}
                    </button>
                </div>

                {loading && <Loader />}
                {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
                {rookies && !loading && (
                    <>
                        <p className="page-subtitle" style={{ marginTop: '0.75rem', marginBottom: '0.75rem' }}>
                            {rookies.candidate_pool_size} rookies this season, sorted by ROY probability.
                            Click a row to see their closest historical season comp.
                        </p>
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Rank</th><th>Player</th><th>PTS</th><th>TS%</th>
                                        <th>USG%</th><th>MIN</th><th>Net Rtg</th><th>ROY Prob</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rookies.results.map((r) => (
                                        <tr
                                            key={r.player_id}
                                            onClick={() => loadComps(r.player_name)}
                                            style={{ cursor: 'pointer' }}
                                            className={r.player_name === selected ? 'text-accent' : ''}
                                        >
                                            <td>{r.player_name === selected ? '▶ ' : ''}{r.rank}</td>
                                            <td>{r.player_name}</td>
                                            <td>{fmt(r.pts)}</td>
                                            <td>{fmt(r.ts_pct, 3)}</td>
                                            <td>{fmt(r.usg_pct, 3)}</td>
                                            <td>{fmt(r.min)}</td>
                                            <td>{fmt(r.net_rating)}</td>
                                            <td>{fmt(r.roy_probability * 100)}%</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>

            {selected && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <h3 className="section-heading" style={{ marginTop: 0 }}>
                        {selected}'s Closest Historical Comp
                    </h3>
                    {compsLoading && <Loader />}
                    {compsError && <p className="error-message">{compsError}</p>}
                    {comps && (
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr><th>Player</th><th>Season</th><th>Similarity</th></tr>
                                </thead>
                                <tbody>
                                    {comps.results.length === 0 && (
                                        <tr><td colSpan={3} className="empty-message">No comps found yet — needs more games played this season.</td></tr>
                                    )}
                                    {comps.results.slice(0, 5).map((c) => (
                                        <tr key={`${c.player_id}-${c.season}`}>
                                            <td>{c.player_name}</td>
                                            <td>{c.season - 1}-{String(c.season).slice(-2)}</td>
                                            <td>{(c.similarity_score * 100).toFixed(1)}%</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
