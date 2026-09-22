import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchPlayerHistory, fetchPlayerClusterHistory } from '../../services/api';
import Loader from '../Loader';
import Icon from './Icon';
import TeamLogo from './TeamLogo';
import PlayerHeadshot from './PlayerHeadshot';

function fmt(v, digits = 1) {
    if (v == null) return '—';
    return Number(v).toFixed(digits);
}

const CLOSE_DURATION = 240;

export default function PlayerDetailModal({ player, onClose }) {
    const [history, setHistory] = useState(null);
    const [clusters, setClusters] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [closing, setClosing] = useState(false);
    const closingRef = useRef(false);

    function handleClose() {
        if (closingRef.current) return;
        closingRef.current = true;
        setClosing(true);
        setTimeout(onClose, CLOSE_DURATION);
    }

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError('');
        Promise.allSettled([
            fetchPlayerHistory(player.player_name),
            fetchPlayerClusterHistory(player.player_name),
        ]).then(([historyResult, clusterResult]) => {
            if (cancelled) return;
            if (historyResult.status === 'fulfilled') {
                setHistory(historyResult.value);
            } else {
                setError('Could not load year-by-year stats for this player.');
            }
            if (clusterResult.status === 'fulfilled') {
                setClusters(clusterResult.value);
            }
            setLoading(false);
        });
        return () => { cancelled = true; };
    }, [player.player_name]);

    useEffect(() => {
        function onKeyDown(e) {
            if (e.key === 'Escape') handleClose();
        }
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const seasons = history?.seasons ? [...history.seasons].reverse() : [];
    const latestArchetype = clusters?.seasons?.length
        ? [...clusters.seasons].sort((a, b) => b.season - a.season)[0].archetype
        : null;

    return createPortal(
        <div
            className={`modal-overlay${closing ? ' modal-overlay--closing' : ''}`}
            onClick={handleClose}
        >
            <div
                className={`player-modal${closing ? ' player-modal--closing' : ''}`}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="player-modal-header">
                    <PlayerHeadshot playerId={player.player_id} playerName={player.player_name} size={56} />
                    <div className="player-modal-header-text">
                        <div className="player-modal-name">{player.player_name}</div>
                        <div className="player-modal-subline">
                            <TeamLogo abbreviation={player.team_abbreviation} size={18} />
                            {player.team_abbreviation}
                            {player.position && <>&nbsp;·&nbsp;Est. {player.position}</>}
                        </div>
                    </div>
                    <button type="button" className="player-modal-close" onClick={handleClose} aria-label="Close">
                        <Icon name="close" />
                    </button>
                </div>

                <div className="player-modal-body">
                    <div>
                        <div className="player-modal-section-title">Player Info</div>
                        <div className="player-modal-info-grid">
                            <div className="player-modal-info-item">
                                <span className="player-modal-info-label">Date of Birth</span>
                                <span className="player-modal-info-value player-modal-info-value--muted">Not available</span>
                            </div>
                            <div className="player-modal-info-item">
                                <span className="player-modal-info-label">College</span>
                                <span className="player-modal-info-value player-modal-info-value--muted">Not available</span>
                            </div>
                            <div className="player-modal-info-item">
                                <span className="player-modal-info-label">Draft Year</span>
                                <span className="player-modal-info-value player-modal-info-value--muted">Not available</span>
                            </div>
                            <div className="player-modal-info-item">
                                <span className="player-modal-info-label">Draft Pick</span>
                                <span className="player-modal-info-value player-modal-info-value--muted">Not available</span>
                            </div>
                        </div>
                    </div>

                    <div>
                        <div className="player-modal-section-title">Archetype</div>
                        {latestArchetype ? (
                            <div className="player-modal-archetype">
                                <span className="pill-badge">
                                    <Icon name="auto_awesome" size="0.9em" />
                                    {latestArchetype}
                                </span>
                                <span className="player-modal-empty">Based on statistical clustering of recent seasons.</span>
                            </div>
                        ) : (
                            <p className="player-modal-empty">
                                {loading ? 'Loading…' : 'Not enough qualified minutes to assign an archetype.'}
                            </p>
                        )}
                    </div>

                    <div>
                        <div className="player-modal-section-title">Year-by-Year Stats</div>
                        {loading && <Loader />}
                        {!loading && error && <p className="player-modal-empty">{error}</p>}
                        {!loading && !error && seasons.length > 0 && (
                            <div className="player-modal-stats-wrapper">
                                <table className="data-table">
                                    <thead>
                                        <tr>
                                            <th>Season</th>
                                            <th>Age</th>
                                            <th>GP</th>
                                            <th>MIN</th>
                                            <th>PTS</th>
                                            <th>REB</th>
                                            <th>AST</th>
                                            <th>TS%</th>
                                            <th>USG%</th>
                                            <th>Net Rtg</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {seasons.map((s) => (
                                            <tr key={s.season}>
                                                <td>{s.season}</td>
                                                <td>{s.age ?? '—'}</td>
                                                <td>{s.gp ?? '—'}</td>
                                                <td>{fmt(s.min)}</td>
                                                <td>{fmt(s.pts)}</td>
                                                <td>{fmt(s.reb)}</td>
                                                <td>{fmt(s.ast)}</td>
                                                <td>{fmt(s.ts_pct, 3)}</td>
                                                <td>{fmt(s.usg_pct, 3)}</td>
                                                <td>{fmt(s.net_rating)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                        {!loading && !error && seasons.length === 0 && (
                            <p className="player-modal-empty">No season data found.</p>
                        )}
                    </div>
                </div>
            </div>
        </div>,
        document.body
    );
}
