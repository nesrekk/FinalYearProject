import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { fetchCurrentMeta } from '../../services/api';
import TeamLogo from '../common/TeamLogo';
import { abbrFromTeamName } from '../../utils/teamAssets';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

const CONFERENCES = [
    { id: 'eastern', label: 'Eastern' },
    { id: 'western', label: 'Western' },
];

export default function StandingsSection() {
    const [conference, setConference] = useState('eastern');
    const [standings, setStandings] = useState({ eastern: [], western: [] });
    const [loading, setLoading] = useState(false);
    const data = standings[conference] || [];

    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);

    useEffect(() => {
        let active = true;
        async function loadCurrent() {
            setLoading(true);
            try {
                const meta = await fetchCurrentMeta();
                if (active && meta?.standings?.eastern && meta?.standings?.western) {
                    setStandings(meta.standings);
                }
            } catch {
                // keep mock fallback
            } finally {
                if (active) setLoading(false);
            }
        }
        loadCurrent();
        return () => {
            active = false;
        };
    }, []);

    const leader = data[0] || null;

    return (
        <div className="page page-standings fade-in">
            {loading && <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>Loading current season standings...</p>}

            <div className="hb-segmented" style={{ marginBottom: '1rem' }}>
                {CONFERENCES.map((c) => (
                    <button
                        key={c.id}
                        type="button"
                        className={`hb-segmented-btn ${conference === c.id ? 'hb-segmented-btn--active' : ''}`}
                        onClick={() => setConference(c.id)}
                    >
                        {conference === c.id && (
                            <motion.div
                                layoutId="standings-tab-indicator"
                                className="hb-segmented-indicator"
                                transition={preset.segmentedSpring}
                            />
                        )}
                        <span className="hb-segmented-label">{c.label} Conference</span>
                    </button>
                ))}
            </div>

            {leader && (
                <motion.div
                    key={`hero-${conference}-${leader.team}-${isAdvanced}`}
                    className="hb-hero"
                    style={{ marginBottom: '1rem' }}
                    initial={isAdvanced ? { opacity: 0, y: -8 } : false}
                    animate={{ opacity: 1, y: 0 }}
                    transition={preset.spring}
                >
                    <span className="hb-row-avatar">
                        <TeamLogo abbreviation={leader.abbr || abbrFromTeamName(leader.team)} size={40} />
                    </span>
                    <div className="hb-hero-text">
                        <div className="hb-hero-label">#1 Seed — {CONFERENCES.find((c) => c.id === conference)?.label}</div>
                        <div className="hb-hero-name">{leader.team}</div>
                    </div>
                    <div className="hb-hero-value">{leader.pct}</div>
                </motion.div>
            )}

            <div className="hb-table-wrapper table-wrapper">
                <table className="data-table standings-table">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Team</th>
                            <th>W</th>
                            <th>L</th>
                            <th>PCT</th>
                            <th>GB</th>
                            <th>L10</th>
                            <th>Streak</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.map((team) => (
                            <tr key={team.team}>
                                <td className="rank-cell">{team.rank}</td>
                                <td className="team-cell">
                                    <span className="entity-row">
                                        <TeamLogo abbreviation={team.abbr || abbrFromTeamName(team.team)} size={24} />
                                        {team.team}
                                    </span>
                                </td>
                                <td>{team.w}</td>
                                <td>{team.l}</td>
                                <td className="hb-cell-accent">{team.pct}</td>
                                <td>{team.gb}</td>
                                <td>{team.last10}</td>
                                <td>
                                    <span className={`streak-badge ${team.streak.startsWith('W') ? 'streak--win' : 'streak--loss'}`}>
                                        {team.streak}
                                    </span>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            {!loading && data.length === 0 && (
                <p className="empty-message">No live standings available right now. Please refresh in a moment.</p>
            )}
        </div>
    );
}
