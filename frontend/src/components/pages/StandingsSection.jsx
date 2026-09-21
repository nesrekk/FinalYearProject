import React, { useEffect, useState } from 'react';
import { fetchCurrentMeta } from '../../services/api';

export default function StandingsSection() {
    const [conference, setConference] = useState('eastern');
    const [standings, setStandings] = useState({ eastern: [], western: [] });
    const [loading, setLoading] = useState(false);
    const data = standings[conference] || [];

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

    return (
        <div className="page page-standings fade-in">
            {loading && <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>Loading current season standings...</p>}
            {/* Conference Tabs */}
            <div className="tab-bar">
                <button
                    className={`tab-btn ${conference === 'eastern' ? 'tab-btn--active' : ''}`}
                    onClick={() => setConference('eastern')}
                >
                    Eastern Conference
                </button>
                <button
                    className={`tab-btn ${conference === 'western' ? 'tab-btn--active' : ''}`}
                    onClick={() => setConference('western')}
                >
                    Western Conference
                </button>
            </div>

            {/* Table */}
            <div className="table-wrapper">
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
                            <tr key={team.abbr}>
                                <td className="rank-cell">{team.rank}</td>
                                <td className="team-cell">
                                    {team.logo ? (
                                        <img
                                            src={team.logo}
                                            alt={team.abbr}
                                            style={{ width: '28px', height: '28px', objectFit: 'contain' }}
                                        />
                                    ) : (
                                        <span className="team-abbr-badge">{team.abbr}</span>
                                    )}
                                    {team.team}
                                </td>
                                <td>{team.w}</td>
                                <td>{team.l}</td>
                                <td className="text-accent">{team.pct}</td>
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
