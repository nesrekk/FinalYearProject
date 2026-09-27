import React, { useEffect, useState } from 'react';
import { fetchPlayerSuggestions, fetchPlayerMatchups } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';
import SourceBadge from './common/SourceBadge';
import TableExport from './common/TableExport';

function MatchupRow({ row, highlight }) {
    return (
        <tr style={{ opacity: row.reliable ? 1 : 0.45 }}>
            <td>
                <span className="entity-row">
                    <PlayerHeadshot playerId={row.player_id} playerName={row.player_name} size={26} />
                    {row.player_name}
                </span>
            </td>
            <td>{row.gp}</td>
            <td>{row.partial_poss.toFixed(1)}</td>
            <td>{row.matchup_fga}</td>
            <td style={{ color: highlight, fontWeight: 700 }}>
                {row.matchup_fg_pct == null ? '—' : `${(row.matchup_fg_pct * 100).toFixed(0)}%`}
            </td>
            <td>{row.player_pts}</td>
            {!row.reliable && (
                <td>
                    <span className="page-subtitle" style={{ fontSize: '0.7rem' }}>small sample</span>
                </td>
            )}
        </tr>
    );
}

export default function MatchupFinderSection() {
    const [player, setPlayer] = useState('');
    const [role, setRole] = useState('scorer');
    const [season, setSeason] = useState('2026');
    const [suggestions, setSuggestions] = useState([]);
    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        const query = player.trim();
        if (query.length < 2) {
            setSuggestions([]);
            return;
        }
        const timer = setTimeout(async () => {
            try {
                const data = await fetchPlayerSuggestions(query, 10);
                setSuggestions(data?.results ?? []);
            } catch {
                setSuggestions([]);
            }
        }, 200);
        return () => clearTimeout(timer);
    }, [player]);

    const handleSearch = async () => {
        if (!player.trim()) return;
        setLoading(true);
        setError('');
        setResult(null);
        try {
            const data = await fetchPlayerMatchups(player.trim(), role, season || undefined, 10);
            setResult(data);
        } catch (e) {
            setError(e?.response?.data?.detail || 'Could not load matchup data.');
        } finally {
            setLoading(false);
        }
    };

    return (
        <section className="dashboard-card">
            <h2 className="card-title hb-page-title">
                <span className="card-icon"><Icon name="swords" /></span>
                Matchup Finder
                <InfoTooltip label="How this works" title="Real defensive matchup tracking, not a model">
                    Real player-vs-player matchup data (NBA's own real player-tracking cameras, `LeagueSeasonMatchups`)
                    — real partial possessions each pair has actually been matched up for, and the real FG% the
                    offensive player shot in those specific matchups. Pairs below 20 real partial possessions are
                    greyed out and marked "small sample" — a single defended shot is either 0% or 100%, so tiny
                    samples produce noisy, misleading percentages. Real full league-wide coverage starts at the
                    2017-18 season.
                </InfoTooltip>
                <SourceBadge source={result?._source} />
            </h2>
            <p className="page-subtitle">
                Search a player, then choose whether they're the scorer (who guards them toughest?) or the
                defender (who do they shut down?).
            </p>

            <div className="input-row">
                <input
                    type="text"
                    placeholder="Player Name"
                    value={player}
                    onChange={(e) => setPlayer(e.target.value)}
                    className="input-field"
                    list="matchup-player-suggestions"
                />
                <datalist id="matchup-player-suggestions">
                    {suggestions.map((name) => (
                        <option key={name} value={name} />
                    ))}
                </datalist>
                <select className="input-field" value={role} onChange={(e) => setRole(e.target.value)}>
                    <option value="scorer">As scorer — who guards them best?</option>
                    <option value="defender">As defender — who do they shut down?</option>
                </select>
                <input
                    type="number"
                    placeholder="Season (e.g. 2026)"
                    value={season}
                    onChange={(e) => setSeason(e.target.value)}
                    className="input-field"
                    min={2018}
                    max={2026}
                />
                <button
                    className="action-btn"
                    onClick={handleSearch}
                    disabled={loading || !player.trim()}
                >
                    {loading ? 'Searching…' : 'Find Matchups'}
                </button>
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {result && (
                <>
                    <div className="entity-row" style={{ marginTop: '1rem', marginBottom: '0.75rem' }}>
                        <PlayerHeadshot playerId={result.player_id} playerName={result.player_name} size={40} />
                        <span style={{ fontWeight: 700 }}>{result.player_name}</span>
                        <span className="page-subtitle" style={{ margin: 0 }}>
                            {result.season - 1}-{String(result.season).slice(-2)} ·{' '}
                            {result.role === 'scorer' ? 'as the offensive player' : 'as the defender'}
                        </span>
                    </div>

                    <div className="stat-cards-row" style={{ alignItems: 'flex-start', gap: '1.5rem' }}>
                        <div style={{ flex: 1, minWidth: 280 }}>
                            <h3 className="section-heading" style={{ fontSize: '0.95rem' }}>
                                {result.role === 'scorer' ? 'Toughest matchups' : 'Shuts down best'}
                            </h3>
                            <TableExport />
                            <div className="table-wrapper">
                                <table className="data-table">
                                    <thead>
                                        <tr>
                                            <th>{result.role === 'scorer' ? 'Defender' : 'Offensive Player'}</th>
                                            <th>GP</th>
                                            <th>Poss</th>
                                            <th>FGA</th>
                                            <th>FG%</th>
                                            <th>Pts</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {result.toughest.map((row) => (
                                            <MatchupRow key={row.player_id} row={row} highlight="#f87171" />
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <div style={{ flex: 1, minWidth: 280 }}>
                            <h3 className="section-heading" style={{ fontSize: '0.95rem' }}>
                                {result.role === 'scorer' ? 'Easiest matchups' : 'Torched by'}
                            </h3>
                            <TableExport />
                            <div className="table-wrapper">
                                <table className="data-table">
                                    <thead>
                                        <tr>
                                            <th>{result.role === 'scorer' ? 'Defender' : 'Offensive Player'}</th>
                                            <th>GP</th>
                                            <th>Poss</th>
                                            <th>FGA</th>
                                            <th>FG%</th>
                                            <th>Pts</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {result.easiest.map((row) => (
                                            <MatchupRow key={row.player_id} row={row} highlight="#34d399" />
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>{result.methodology}</p>
                </>
            )}
        </section>
    );
}
