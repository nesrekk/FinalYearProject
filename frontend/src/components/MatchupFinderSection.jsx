import React, { useRef, useState } from 'react';
import { fetchPlayerMatchups } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';
import PlayerName from './common/PlayerName';
import SourceBadge from './common/SourceBadge';
import TableExport from './common/TableExport';
import SeasonSelect from './common/SeasonSelect';
import AutocompleteDropdown from './common/AutocompleteDropdown';
import NamesakeNote from './common/NamesakeNote';
import usePlayerSuggestions from '../utils/usePlayerSuggestions';
import { latestCompleteSeason } from '../utils/season';

// Small samples were faded with opacity (text at 2.1-4.1:1, R8-051) and got an extra cell the header
// didn't have; they now say "small sample" under the name.
function MatchupRow({ row, highlight }) {
    return (
        <tr>
            <td>
                <PlayerName playerId={row.player_id} name={row.player_name} size={26}>
                    {!row.reliable && <span className="page-subtitle" style={{ display: 'block', fontSize: '0.7rem', margin: 0 }}>small sample</span>}
                </PlayerName>
            </td>
            <td>{row.gp}</td>
            <td>{row.partial_poss.toFixed(1)}</td>
            <td>{row.matchup_fga}</td>
            <td style={{ color: highlight, fontWeight: 700 }}>
                {row.matchup_fg_pct == null ? '—' : `${(row.matchup_fg_pct * 100).toFixed(0)}%`}
            </td>
            <td>{row.player_pts}</td>
        </tr>
    );
}

export default function MatchupFinderSection() {
    const [player, setPlayer] = useState('');
    const [role, setRole] = useState('scorer');
    const [season, setSeason] = useState(() => latestCompleteSeason());
    const [picked, setPicked] = useState(null); // { name, id } from a suggestion
    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    // A suggestion carries the NBA id (two players can share a name); typed text opens the latest of that name.
    const sug = usePlayerSuggestions(player, picked?.name);
    const inputRef = useRef(null);

    const handleSearch = async (choice) => {
        const target = choice ?? (picked && picked.name === player.trim() ? picked : { name: player.trim(), id: null });
        if (!target.name) return;
        sug.dismiss();
        setLoading(true);
        setError('');
        setResult(null);
        try {
            const data = await fetchPlayerMatchups(target.name, role, season || undefined, 10, target.id || undefined);
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
                    ref={inputRef}
                    type="text"
                    placeholder="Player Name"
                    aria-label="Player"
                    value={player}
                    onChange={(e) => setPlayer(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
                    className="input-field"
                />
                <AutocompleteDropdown anchorRef={inputRef} items={sug.labels}
                    onPick={(label) => {
                        const p = sug.pick(label);
                        if (!p) return;
                        setPlayer(p.name);
                        setPicked({ name: p.name, id: p.id });
                        sug.dismiss();
                    }} />
                <select className="input-field" value={role} onChange={(e) => setRole(e.target.value)}>
                    <option value="scorer">As scorer — who guards them best?</option>
                    <option value="defender">As defender — who do they shut down?</option>
                </select>
                <SeasonSelect value={season} onChange={setSeason} from={2018} to={latestCompleteSeason()} label="Season" />
                <button
                    className="action-btn"
                    onClick={() => handleSearch()}
                    disabled={loading || !player.trim()}
                >
                    {loading ? 'Searching…' : 'Find Matchups'}
                </button>
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {result && (
                <>
                    <NamesakeNote name={result.player_name} id={result.player_id}
                        onPick={(p) => { setPlayer(p.name); setPicked(p); handleSearch(p); }} />
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
                                            <MatchupRow key={row.player_id} row={row} highlight="var(--negative)" />
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
                                            <MatchupRow key={row.player_id} row={row} highlight="var(--positive)" />
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
