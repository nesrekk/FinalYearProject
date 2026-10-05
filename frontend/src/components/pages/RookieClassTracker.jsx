import React, { useEffect, useState } from 'react';
import { fetchROYPrediction, fetchSeasonSimilarity } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import Icon from '../common/Icon';
import TableExport from '../common/TableExport';
import SourceBadge from '../common/SourceBadge';

// The ROY model scores 2009-10 to the latest finished season.
const LATEST_SEASON = 2026;
const SEASONS = Array.from({ length: LATEST_SEASON - 2010 + 1 }, (_, i) => LATEST_SEASON - i);

function fmt(v, digits = 1) {
    return v == null ? '—' : Number(v).toFixed(digits);
}

export default function RookieClassTracker() {
    const [season, setSeason] = useState(LATEST_SEASON);
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
            // The loaded class's season, not the picker's (it may have changed without a reload).
            const data = await fetchSeasonSimilarity(playerName, rookies?.season ?? season);
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
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="eco" /></span>
                    Rookie Class Tracker
                    <InfoTooltip label="How this works" title="Every rookie in a season">
                        "Rookie" here means the player's first NBA season, taken from Basketball-Reference
                        (so a short earlier stint counts) — the same definition the ROY model uses. The ROY
                        chance is that model's score calibrated so the whole rookie class adds up to 100%. Clicking a rookie shows their closest
                        historical season comp (any player, any season) from the season-similarity engine —
                        useful for "who does this rookie's season actually resemble so far," not a
                        guarantee of a similar career.
                    </InfoTooltip>
                    <SourceBadge source={rookies?._source} />
                </h2>

                <div className="input-row">
                    <select className="input-field" value={season} onChange={(e) => setSeason(Number(e.target.value))}
                        aria-label="Season" style={{ maxWidth: 130 }}>
                        {SEASONS.map((y) => <option key={y} value={y}>{`${y - 1}-${String(y).slice(-2)}`}</option>)}
                    </select>
                    <button type="button" className="action-btn" onClick={loadClass} disabled={loading}>
                        {loading ? 'Loading…' : 'Load Rookie Class'}
                    </button>
                </div>

                {loading && <Loader />}
                {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
                {rookies && !loading && (
                    <>
                        <p className="page-subtitle" style={{ marginTop: '0.75rem', marginBottom: '0.75rem' }}>
                            {rookies.candidate_pool_size} rookies in {rookies.season - 1}-{String(rookies.season).slice(-2)}, sorted by chance to win ROY.
                            Click a row to see their closest historical season comp.
                        </p>
                        <TableExport />
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Rank</th><th>Player</th><th>PTS</th><th>TS%</th>
                                        <th>USG%</th><th>MIN</th><th>Net Rtg</th><th>ROY chance</th>
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
                                            <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                            <td>{fmt(r.pts)}</td>
                                            <td>{fmt(r.ts_pct, 3)}</td>
                                            <td>{fmt(r.usg_pct, 3)}</td>
                                            <td>{fmt(r.min)}</td>
                                            <td>{fmt(r.net_rating)}</td>
                                            <td>{r.roy_chance != null ? `${fmt(r.roy_chance * 100)}%` : '—'}</td>
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
                        <>
                            <TableExport />
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
                                                <td><PlayerName playerId={c.player_id} name={c.player_name} /></td>
                                                <td>{c.season - 1}-{String(c.season).slice(-2)}</td>
                                                <td>{(c.similarity_score * 100).toFixed(1)}%</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
