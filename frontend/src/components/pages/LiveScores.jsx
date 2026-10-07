import React, { useEffect, useMemo, useState } from 'react';
import { fetchGameBoxscore, fetchGamesByDate } from '../../services/api';
import { TEAM_NAME_TO_ABBR } from '../../utils/teamAssets';
import { nbaDateIso, shiftIsoDate } from '../../utils/date';
import SourceBadge from '../common/SourceBadge';
import { signed } from '../../utils/format';
import TeamLink from '../common/TeamLink';
import TeamLogo from '../common/TeamLogo';
import '../../styles/thisweek.css';
import { isPlainClick, pageHref, parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';

// abbr -> "Boston Celtics". The scoreboard's own nickname field comes back empty and its logo is a
// third-party image or null (round 8 step 2b), so the card uses the app's names and NBA.com logos.
const TEAM_FULL_NAME = Object.fromEntries(Object.entries(TEAM_NAME_TO_ABBR).map(([name, abbr]) => [abbr, name]));
const teamName = (t) => TEAM_FULL_NAME[t.abbr] || [t.city, t.name].filter(Boolean).join(' ') || t.abbr;

function GameTeam({ team, isScheduled }) {
    const rest = team.rest || {};
    return (
        <div className="game-team">
            <TeamLink abbr={team.abbr}>
                <TeamLogo abbreviation={team.abbr} size={48} className="team-badge" />
            </TeamLink>
            <span className="team-name">{teamName(team)}</span>
            {rest.is_b2b && (
                <span className="badge" title="Back-to-back — no real rest day before this game" style={{ background: 'rgba(248,113,113,0.15)', color: 'var(--negative)', fontSize: '0.65rem', padding: '2px 6px', marginLeft: 4 }}>B2B</span>
            )}
            {!rest.is_b2b && rest.rest_disadvantage && (
                <span className="badge" title={`${rest.rest_days} real rest days vs. the other team's more`} style={{ background: 'rgba(250,204,21,0.15)', color: 'var(--streak)', fontSize: '0.65rem', padding: '2px 6px', marginLeft: 4 }}>REST DISADV.</span>
            )}
            <span className={`team-score ${!isScheduled ? '' : 'team-score--dim'}`}>
                {isScheduled ? '-' : team.score}
            </span>
        </div>
    );
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

// Dates are the NBA's calendar dates (US Eastern): at 8 am in India on 21 October "today" is still
// 20 October in the US, when that night's games are being played.
const STATUS_LABEL = { POSTPONED: 'Postponed', CANCELED: 'Canceled', SUSPENDED: 'Suspended' };

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

// Game Replay for a final whose play-by-play the daily update has stored (round 9 step 5).
function ReplayLink({ replayId, onNavigate }) {
    if (!replayId) return null;
    const params = { game: replayId };
    return (
        <a className="action-btn ls-replay" href={`${pageHref('analytics', params)}#replay`}
            onClick={(e) => { e.stopPropagation(); if (isPlainClick(e) && onNavigate) { e.preventDefault(); onNavigate('analytics', 'replay', params); } }}
            onKeyDown={(e) => e.stopPropagation()}>
            Game Replay
        </a>
    );
}

export default function LiveScores({ onNavigate }) {
    // ?page=scores&date=YYYY-MM-DD opens a date (the Dashboard's "This week" links here).
    const params = useInitialParams();
    const [selectedDate, setSelectedDate] = useState(() => {
        const d = parseParam.str(params, 'date');
        return d && ISO_DATE.test(d) ? d : nbaDateIso();
    });
    useUrlSync({ date: selectedDate === nbaDateIso() ? null : selectedDate });
    const [games, setGames] = useState([]);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState(false);
    const [feed, setFeed] = useState(null); // { source, status, message, _source } from the route
    const [selectedGame, setSelectedGame] = useState(null);
    const [boxscore, setBoxscore] = useState(null);
    const [loadingBoxscore, setLoadingBoxscore] = useState(false);
    const [activeTeamSide, setActiveTeamSide] = useState('away');

    const quickDates = useMemo(() => {
        const today = nbaDateIso();
        return { yesterday: shiftIsoDate(today, -1), today, tomorrow: shiftIsoDate(today, 1) };
    }, []);

    useEffect(() => {
        let active = true;
        async function loadGames() {
            setLoading(true);
            setLoadError(false);
            try {
                const data = await fetchGamesByDate(selectedDate);
                if (!active) return;
                setFeed({ source: data?.source, status: data?.status, message: data?.message, _source: data?._source });
                const mapped = (Array.isArray(data?.games) ? data.games : []).map((g) => ({
                    id: g.id,
                    status: g.status,
                    // "Final", "Final/OT", "Q3 5:12", "7:00 PM ET", "Postponed"...
                    statusText: g.status_text || STATUS_LABEL[g.status] || g.status,
                    kind: g.kind,
                    note: g.note,
                    replayId: g.replay_id || null,
                    away: g.away,
                    home: g.home,
                }));
                setGames(mapped);
            } catch {
                if (active) {
                    setGames([]);
                    setFeed(null);
                    setLoadError(true);
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

    const [boxMessage, setBoxMessage] = useState('');

    async function openGame(game) {
        setSelectedGame(game);
        setActiveTeamSide('away');
        setLoadingBoxscore(true);
        setBoxMessage('');
        try {
            const data = await fetchGameBoxscore(game.id);
            setBoxscore(data?.boxscore || { away: [], home: [] });
            setBoxMessage(data?.message || '');
        } catch {
            setBoxscore({ away: [], home: [] });
            setBoxMessage('The box score couldn\'t load right now.');
        } finally {
            setLoadingBoxscore(false);
        }
    }

    const feedLine = !loading && !loadError && feed && games.length > 0 ? (
        feed.source === 'stored'
            ? 'Final scores from the stored results (ESPN, matched to the schedule).'
            : 'ESPN scoreboard, live; refreshed about once a minute.'
    ) : null;

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
            {feedLine && (
                <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>
                    {feedLine}
                    <SourceBadge source={feed._source} />
                </p>
            )}
            <div className="scores-grid">
                {games.map((game) => {
                    const isLive = game.status === 'LIVE';
                    const isFinal = game.status === 'FINAL';
                    const isScheduled = !isLive && !isFinal;
                    const kind = game.kind && game.kind !== 'Regular season' ? game.kind : null;

                    return (
                        <div
                            key={game.id}
                            className={`game-card ${isLive ? 'game-card--live' : ''}`}
                            style={{ cursor: 'pointer' }}
                            role="button"
                            tabIndex={0}
                            aria-label={`${teamName(game.away)} at ${teamName(game.home)}: open the box score`}
                            onClick={() => openGame(game)}
                            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openGame(game); } }}
                        >
                            {/* Status badge */}
                            <div className="game-status-row">
                                {isLive && (
                                    <span className="badge badge--live">
                                        <span className="live-dot"></span> LIVE · {game.statusText}
                                    </span>
                                )}
                                {isFinal && <span className="badge badge--final">{game.statusText || 'FINAL'}</span>}
                                {isScheduled && <span className="badge badge--scheduled">{game.statusText}</span>}
                                {kind && <span className="badge badge--scheduled">{kind}</span>}
                                {game.note && <span className="badge badge--scheduled" title={game.note}>{game.note}</span>}
                            </div>

                            {/* Teams */}
                            <div className="game-matchup">
                                <GameTeam team={game.away} isScheduled={isScheduled} />

                                <span className="game-vs">VS</span>

                                <GameTeam team={game.home} isScheduled={isScheduled} />
                            </div>
                            {isFinal && (
                                <div className="ls-links">
                                    <span className="ls-link-hint">Box score</span>
                                    <ReplayLink replayId={game.replayId} onNavigate={onNavigate} />
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
            {!loading && loadError && (
                <p className="empty-message">Scores for {selectedDate} couldn&apos;t load right now.</p>
            )}
            {!loading && !loadError && games.length === 0 && (
                <p className="empty-message">
                    {feed?.status === 'unreachable'
                        ? (feed.message || 'ESPN\'s scoreboard didn\'t answer. Try again in a moment.')
                        : `No NBA games on ${selectedDate} (ESPN's schedule).`}
                </p>
            )}

            {selectedGame && (
                <div className="bsm-overlay" onClick={() => setSelectedGame(null)}>
                    <div className="bsm-modal" onClick={(e) => e.stopPropagation()}>
                        <button className="bsm-close" onClick={() => setSelectedGame(null)}>X</button>
                        <div className="bsm-header">
                            <div className="bsm-team">
                                <span className="bsm-name">{teamName(selectedGame.away)}</span>
                                <span className="bsm-score">{selectedGame.away.score ?? '-'}</span>
                            </div>
                            <div className="bsm-vs-block">
                                <span className="bsm-status">{selectedGame.statusText || selectedGame.status}</span>
                                <ReplayLink replayId={selectedGame.replayId} onNavigate={onNavigate} />
                            </div>
                            <div className="bsm-team">
                                <span className="bsm-name">{teamName(selectedGame.home)}</span>
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
                                                <th>+/-</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {(activeTeamSide === 'away' ? (boxscore?.away || []) : (boxscore?.home || [])).slice(0, 15).map((p, idx) => (
                                                <tr key={`${activeTeamSide}-${idx}`}>
                                                    <td>{p.name}</td>
                                                    <td title={p.dnp_reason || undefined}>{formatMinutes(p.min)}</td>
                                                    <td>{p.pts}</td>
                                                    <td>{p.reb}</td>
                                                    <td>{p.ast}</td>
                                                    <td>{p.fg}</td>
                                                    <td>{p.three}</td>
                                                    <td>{p.ft}</td>
                                                    <td>{p.pm == null ? '—' : signed(p.pm, 0)}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                                {(activeTeamSide === 'away' ? (boxscore?.away || []) : (boxscore?.home || [])).length === 0 && (
                                    <p className="empty-message" style={{ marginTop: '0.5rem' }}>
                                        {boxMessage || `No player box score rows returned for ${activeTeamSide === 'away' ? selectedGame.away.abbr : selectedGame.home.abbr}.`}
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
