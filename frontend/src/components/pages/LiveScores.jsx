import React, { useEffect, useMemo, useState } from 'react';
import { fetchGameBoxscore, fetchGamesByDate } from '../../services/api';
import { mockLiveScores } from '../../services/mockData';

const TEAM_COLORS = {
    ATL: '#E03A3E', BOS: '#007A33', BKN: '#000000', CHA: '#1D1160',
    CHI: '#CE1141', CLE: '#860038', DAL: '#00538C', DEN: '#0E2240',
    DET: '#C8102E', GSW: '#1D428A', HOU: '#CE1141', IND: '#002D62',
    LAC: '#C8102E', LAL: '#552583', MEM: '#5D76A9', MIA: '#98002E',
    MIL: '#00471B', MIN: '#0C2340', NOP: '#0C2340', NYK: '#F58426',
    OKC: '#007AC1', ORL: '#0077C0', PHI: '#006BB6', PHX: '#1D1160',
    POR: '#E03A3E', SAC: '#5A2D81', SAS: '#C4CED4', TOR: '#CE1141',
    UTA: '#002B5C', WAS: '#002B5C',
};

function toIsoDate(dateObj) {
    const y = dateObj.getFullYear();
    const m = String(dateObj.getMonth() + 1).padStart(2, '0');
    const d = String(dateObj.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
}

function formatMinutes(value) {
    if (value == null) return 'DNP';
    const raw = String(value).trim();
    if (!raw) return 'DNP';

    // Handle NBA live format: PT24M20.00S
    if (raw.startsWith('PT')) {
        const match = raw.match(/^PT(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$/);
        if (!match) return raw;
        const mins = Number(match[1] || 0);
        const secs = Number(match[2] || 0);
        if (mins === 0 && secs === 0) return 'DNP';
        return `${mins}:${String(Math.floor(secs)).padStart(2, '0')}`;
    }

    // Handle clock-like values already provided.
    if (raw === '0' || raw === '0:00' || raw === '00:00') return 'DNP';
    return raw;
}

export default function LiveScores() {
    const [selectedDate, setSelectedDate] = useState(toIsoDate(new Date()));
    const [games, setGames] = useState(mockLiveScores);
    const [loading, setLoading] = useState(false);
    const [selectedGame, setSelectedGame] = useState(null);
    const [boxscore, setBoxscore] = useState(null);
    const [loadingBoxscore, setLoadingBoxscore] = useState(false);
    const [activeTeamSide, setActiveTeamSide] = useState('away');

    const quickDates = useMemo(() => {
        const today = new Date();
        const y = new Date(today);
        y.setDate(today.getDate() - 1);
        const t = new Date(today);
        t.setDate(today.getDate() + 1);
        return {
            yesterday: toIsoDate(y),
            today: toIsoDate(today),
            tomorrow: toIsoDate(t),
        };
    }, []);

    useEffect(() => {
        let active = true;
        async function loadGames() {
            setLoading(true);
            try {
                const data = await fetchGamesByDate(selectedDate);
                if (active && Array.isArray(data?.games) && data.games.length > 0) {
                    const mapped = data.games.map((g) => ({
                        id: g.id,
                        status: g.status === 'SCHEDULED' ? (g.status_text || 'SCHEDULED') : g.status,
                        quarter: g.status === 'LIVE' ? 'LIVE' : '',
                        clock: g.status === 'LIVE' ? '' : '',
                        away: {
                            ...g.away,
                            color: TEAM_COLORS[g.away.abbr] || '#334155',
                        },
                        home: {
                            ...g.home,
                            color: TEAM_COLORS[g.home.abbr] || '#334155',
                        },
                    }));
                    setGames(mapped);
                } else if (active) {
                    setGames([]);
                }
            } catch {
                if (active) {
                    setGames(mockLiveScores);
                }
            } finally {
                if (active) setLoading(false);
            }
        }
        loadGames();
        return () => {
            active = false;
        };
    }, [selectedDate]);

    return (
        <div className="page page-scores fade-in">
            <div className="input-row" style={{ marginBottom: '1rem' }}>
                <button className="action-btn" onClick={() => setSelectedDate(quickDates.yesterday)}>Yesterday</button>
                <button className="action-btn" onClick={() => setSelectedDate(quickDates.today)}>Today</button>
                <button className="action-btn" onClick={() => setSelectedDate(quickDates.tomorrow)}>Tomorrow</button>
                <input
                    type="date"
                    className="input-field"
                    value={selectedDate}
                    onChange={(e) => setSelectedDate(e.target.value)}
                />
            </div>
            {loading && <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>Loading games...</p>}
            <div className="scores-grid">
                {games.map((game) => {
                    const isLive = game.status === 'LIVE';
                    const isFinal = game.status === 'FINAL';
                    const isScheduled = !isLive && !isFinal;

                    return (
                        <div
                            key={game.id}
                            className={`game-card ${isLive ? 'game-card--live' : ''}`}
                            style={{ cursor: 'pointer' }}
                            onClick={async () => {
                                setSelectedGame(game);
                                setActiveTeamSide('away');
                                setLoadingBoxscore(true);
                                try {
                                    const data = await fetchGameBoxscore(game.id);
                                    setBoxscore(data?.boxscore || { away: [], home: [] });
                                } catch {
                                    setBoxscore({ away: [], home: [] });
                                } finally {
                                    setLoadingBoxscore(false);
                                }
                            }}
                        >
                            {/* Status badge */}
                            <div className="game-status-row">
                                {isLive && (
                                    <span className="badge badge--live">
                                        <span className="live-dot"></span> LIVE · {game.quarter} {game.clock}
                                    </span>
                                )}
                                {isFinal && <span className="badge badge--final">FINAL</span>}
                                {isScheduled && <span className="badge badge--scheduled">{game.status}</span>}
                            </div>

                            {/* Teams */}
                            <div className="game-matchup">
                                <div className="game-team">
                                    {game.away.logo ? (
                                        <img
                                            src={game.away.logo}
                                            alt={game.away.abbr}
                                            className="team-badge"
                                            style={{ objectFit: 'cover' }}
                                        />
                                    ) : (
                                        <div className="team-badge" style={{ background: game.away.color }}>{game.away.abbr}</div>
                                    )}
                                    <span className="team-name">{game.away.name}</span>
                                    <span className={`team-score ${!isScheduled ? '' : 'team-score--dim'}`}>
                                        {isScheduled ? '-' : game.away.score}
                                    </span>
                                </div>

                                <span className="game-vs">VS</span>

                                <div className="game-team">
                                    {game.home.logo ? (
                                        <img
                                            src={game.home.logo}
                                            alt={game.home.abbr}
                                            className="team-badge"
                                            style={{ objectFit: 'cover' }}
                                        />
                                    ) : (
                                        <div className="team-badge" style={{ background: game.home.color }}>{game.home.abbr}</div>
                                    )}
                                    <span className="team-name">{game.home.name}</span>
                                    <span className={`team-score ${!isScheduled ? '' : 'team-score--dim'}`}>
                                        {isScheduled ? '-' : game.home.score}
                                    </span>
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>
            {!loading && games.length === 0 && (
                <p className="empty-message">No games found for {selectedDate}.</p>
            )}

            {selectedGame && (
                <div className="bsm-overlay" onClick={() => setSelectedGame(null)}>
                    <div className="bsm-modal" onClick={(e) => e.stopPropagation()}>
                        <button className="bsm-close" onClick={() => setSelectedGame(null)}>X</button>
                        <div className="bsm-header">
                            <div className="bsm-team">
                                <span className="bsm-name">{selectedGame.away.city} {selectedGame.away.name}</span>
                                <span className="bsm-score">{selectedGame.away.score ?? '-'}</span>
                            </div>
                            <div className="bsm-vs-block">
                                <span className="bsm-status">{selectedGame.status}</span>
                            </div>
                            <div className="bsm-team">
                                <span className="bsm-name">{selectedGame.home.city} {selectedGame.home.name}</span>
                                <span className="bsm-score">{selectedGame.home.score ?? '-'}</span>
                            </div>
                        </div>
                        {loadingBoxscore && <p className="empty-message">Loading box score...</p>}
                        {!loadingBoxscore && (
                            <>
                                <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '0.75rem' }}>
                                    <div
                                        style={{
                                            display: 'inline-flex',
                                            background: 'var(--bg-input)',
                                            border: '1px solid var(--border-light)',
                                            borderRadius: '999px',
                                            padding: '0.2rem',
                                            gap: '0.25rem',
                                        }}
                                    >
                                        <button
                                            className="action-btn"
                                            onClick={() => setActiveTeamSide('away')}
                                            style={{
                                                padding: '0.35rem 0.75rem',
                                                borderRadius: '999px',
                                                opacity: activeTeamSide === 'away' ? 1 : 0.65,
                                            }}
                                        >
                                            {selectedGame.away.abbr}
                                        </button>
                                        <button
                                            className="action-btn"
                                            onClick={() => setActiveTeamSide('home')}
                                            style={{
                                                padding: '0.35rem 0.75rem',
                                                borderRadius: '999px',
                                                opacity: activeTeamSide === 'home' ? 1 : 0.65,
                                            }}
                                        >
                                            {selectedGame.home.abbr}
                                        </button>
                                    </div>
                                </div>
                                <div className="table-wrapper">
                                    <table className="data-table">
                                        <thead>
                                            <tr>
                                                <th>Player</th>
                                                <th>MIN</th>
                                                <th>PTS</th>
                                                <th>REB</th>
                                                <th>AST</th>
                                                <th>FG</th>
                                                <th>3PT</th>
                                                <th>FT</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {(activeTeamSide === 'away' ? (boxscore?.away || []) : (boxscore?.home || [])).slice(0, 15).map((p, idx) => (
                                                <tr key={`${activeTeamSide}-${idx}`}>
                                                    <td>{p.name}</td>
                                                    <td>{formatMinutes(p.min)}</td>
                                                    <td>{p.pts}</td>
                                                    <td>{p.reb}</td>
                                                    <td>{p.ast}</td>
                                                    <td>{p.fg}</td>
                                                    <td>{p.three}</td>
                                                    <td>{p.ft}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                                {(activeTeamSide === 'away' ? (boxscore?.away || []) : (boxscore?.home || [])).length === 0 && (
                                    <p className="empty-message" style={{ marginTop: '0.5rem' }}>
                                        No player box score rows returned for {activeTeamSide === 'away' ? selectedGame.away.abbr : selectedGame.home.abbr}.
                                    </p>
                                )}
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
