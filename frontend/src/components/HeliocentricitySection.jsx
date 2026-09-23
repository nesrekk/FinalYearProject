import React, { useEffect, useState } from 'react';
import { fetchHeliocentricityLeaderboard } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';
import TeamLogo from './common/TeamLogo';

function indexColor(v) {
    if (v == null) return 'var(--text-muted)';
    if (v >= 90) return '#f87171';
    if (v >= 75) return '#facc15';
    return 'var(--text-secondary)';
}

export default function HeliocentricitySection() {
    const [season, setSeason] = useState(2026);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError('');
        (async () => {
            try {
                const res = await fetchHeliocentricityLeaderboard(season, 25);
                if (active) setData(res);
            } catch (e) {
                if (active) {
                    setData(null);
                    setError(e?.response?.data?.detail || 'Could not load the heliocentricity leaderboard.');
                }
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, [season]);

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Heliocentricity Index
                    <InfoTooltip label="How this works" title="Real tracking data, a disclosed composite">
                        A simple, fully disclosed average of four real percentile ranks within this season's
                        qualified pool (min≥15 mpg, gp≥20): real time-of-possession share of the player's
                        OWN team (live NBA tracking data — what % of the team's total recorded possession time
                        this one player personally holds), real touches per game, real usage%, and real assist%.
                        Equal weights, no fitted model — the same kind of transparent weighted composite this
                        project's own Impact Score already uses. This measures how much a team's real offense
                        runs through one player; it does NOT simulate what happens if that player sits out, since
                        that would mean inventing an effect size with no real data to fit it against.
                    </InfoTooltip>
                </h3>
                <div className="input-row">
                    <input
                        type="number"
                        className="input-field"
                        value={season}
                        onChange={(e) => setSeason(Number(e.target.value))}
                        min={2014}
                        max={2026}
                        style={{ maxWidth: 110 }}
                    />
                </div>
                {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
                {data && (
                    <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                        Qualified pool: {data.pool_size} players · season {data.season}
                    </p>
                )}
            </div>

            {loading && <Loader />}

            {!loading && data && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <div className="hb-table-wrapper table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Rank</th>
                                    <th>Player</th>
                                    <th>TOP Share</th>
                                    <th>Touches</th>
                                    <th>USG%</th>
                                    <th>AST%</th>
                                    <th>Index</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.results.map((r) => (
                                    <tr key={r.player_id}>
                                        <td>{r.rank}</td>
                                        <td>
                                            <div className="entity-row">
                                                <PlayerHeadshot playerId={r.player_id} playerName={r.player_name} size={28} />
                                                {r.player_name}
                                                <TeamLogo abbreviation={r.team_abbreviation} size={16} style={{ marginLeft: 6 }} />
                                            </div>
                                        </td>
                                        <td>{r.time_of_poss_share.toFixed(1)}%</td>
                                        <td>{r.touches.toFixed(1)}</td>
                                        <td>{(r.usg_pct * 100).toFixed(1)}%</td>
                                        <td>{(r.ast_pct * 100).toFixed(1)}%</td>
                                        <td style={{ color: indexColor(r.heliocentricity_index), fontWeight: 700 }}>
                                            {r.heliocentricity_index >= 90 && <Icon name="local_fire_department" size="0.9em" style={{ verticalAlign: 'middle', marginRight: 2 }} />}
                                            {r.heliocentricity_index.toFixed(1)}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}
