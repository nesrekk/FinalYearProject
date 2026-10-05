import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { createPortal } from 'react-dom';
import { fetchPlayerHistory, fetchPlayerClusterHistory, fetchPlaytypeProfile } from '../../services/api';
import Loader from '../Loader';
import Icon from './Icon';
import TeamLogo from './TeamLogo';
import PlayerHeadshot from './PlayerHeadshot';
import ScoutingReportCard from './ScoutingReportCard';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';
import { isPlainClick, openPlayerProfile, playerProfileHref } from '../../utils/useUrlState';
import '../../styles/profile.css';

function fmt(v, digits = 1) {
    if (v == null) return '—';
    return Number(v).toFixed(digits);
}

export default function PlayerDetailModal({ player, onClose }) {
    const { isAdvanced } = useMotionMode();
    const { spring: SPRING } = motionPreset(isAdvanced);
    const [history, setHistory] = useState(null);
    const [clusters, setClusters] = useState(null);
    const [playtypes, setPlaytypes] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;
        Promise.resolve().then(() => {
            if (cancelled) return;
            setLoading(true);
            setError('');
            Promise.allSettled([
                fetchPlayerHistory(player.player_name),
                fetchPlayerClusterHistory(player.player_name),
                fetchPlaytypeProfile(player.player_name),
            ]).then(([historyResult, clusterResult, playtypeResult]) => {
                if (cancelled) return;
                if (historyResult.status === 'fulfilled') {
                    setHistory(historyResult.value);
                } else {
                    setError('Could not load year-by-year stats for this player.');
                }
                if (clusterResult.status === 'fulfilled') {
                    setClusters(clusterResult.value);
                }
                if (playtypeResult.status === 'fulfilled') {
                    setPlaytypes(playtypeResult.value);
                }
                setLoading(false);
            });
        });
        return () => { cancelled = true; };
    }, [player.player_name]);

    useEffect(() => {
        function onKeyDown(e) {
            if (e.key === 'Escape') onClose();
        }
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, [onClose]);

    const seasons = history?.seasons ? [...history.seasons].reverse() : [];
    const latestArchetype = clusters?.seasons?.length
        ? [...clusters.seasons].sort((a, b) => b.season - a.season)[0].archetype
        : null;

    return createPortal(
        <motion.div
            className="modal-overlay"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
        >
            <motion.div
                className="player-modal"
                onClick={(e) => e.stopPropagation()}
                initial={isAdvanced ? { opacity: 0, y: 20, scale: 0.96 } : { opacity: 0 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={isAdvanced ? { opacity: 0, y: 14, scale: 0.97 } : { opacity: 0 }}
                transition={SPRING}
            >
                <div className="player-modal-header">
                    <motion.span layoutId={isAdvanced ? `player-avatar-${player.player_id}` : undefined} transition={SPRING}>
                        <PlayerHeadshot playerId={player.player_id} playerName={player.player_name} size={56} />
                    </motion.span>
                    <div className="player-modal-header-text">
                        <motion.div
                            layoutId={isAdvanced ? `player-name-${player.player_id}` : undefined}
                            transition={SPRING}
                            className="player-modal-name"
                        >
                            {player.player_name}
                        </motion.div>
                        <div className="player-modal-subline">
                            <TeamLogo abbreviation={player.team_abbreviation} size={18} />
                            {player.team_abbreviation}
                            {player.position && <>&nbsp;·&nbsp;Est. {player.position}</>}
                        </div>
                    </div>
                    {player.player_id > 0 && (
                        <a
                            className="player-modal-profile-link"
                            href={playerProfileHref(player.player_id)}
                            onClick={(e) => {
                                if (!isPlainClick(e)) return;
                                e.preventDefault();
                                onClose();
                                openPlayerProfile(player.player_id);
                            }}
                        >
                            Open full profile
                        </a>
                    )}
                    <button type="button" className="player-modal-close" onClick={onClose} aria-label="Close">
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

                    {playtypes && playtypes.play_types.length > 0 && (
                        <div>
                            <div className="player-modal-section-title">Real Play-Type Profile ({playtypes.season})</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                {playtypes.play_types.map((pt) => (
                                    <div key={pt.play_type} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                        <span style={{ width: 110, fontSize: '0.78rem', color: 'var(--text-secondary)', flexShrink: 0 }}>{pt.play_type}</span>
                                        <div style={{ flex: 1, height: 8, background: 'rgba(100,116,139,0.15)', borderRadius: 4, position: 'relative' }}>
                                            <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${Math.min(100, pt.freq * 100 * 3)}%`, background: 'var(--series-1)', borderRadius: 4 }} />
                                        </div>
                                        <span style={{ width: 40, fontSize: '0.75rem', textAlign: 'right', color: 'var(--text-secondary)' }}>{(pt.freq * 100).toFixed(0)}%</span>
                                        <span style={{ width: 48, fontSize: '0.75rem', textAlign: 'right', color: pt.percentile >= 0.6 ? 'var(--positive)' : pt.percentile <= 0.4 ? 'var(--negative)' : 'var(--text-muted)' }}>
                                            {pt.ppp.toFixed(2)} PPP
                                        </span>
                                    </div>
                                ))}
                            </div>
                            <p className="player-modal-empty" style={{ marginTop: 6 }}>
                                Real share of offensive possessions by real play type (NBA Synergy tracking), with real points-per-possession for each.
                            </p>
                        </div>
                    )}

                    <ScoutingReportCard playerName={player.player_name} />

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
            </motion.div>
        </motion.div>,
        document.body
    );
}
