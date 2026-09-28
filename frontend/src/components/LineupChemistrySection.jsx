import React, { useEffect, useState } from 'react';
import { fetchLineupChemistry } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';
import TeamLogo from './common/TeamLogo';
import TeamLink from './common/TeamLink';
import TableExport from './common/TableExport';

function netRatingColor(v) {
    if (v == null) return 'var(--text-secondary)';
    if (v > 0) return 'var(--positive)';
    if (v < 0) return 'var(--negative)';
    return 'var(--text-secondary)';
}

export default function LineupChemistrySection() {
    const [order, setOrder] = useState('best');
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError('');
        (async () => {
            try {
                const res = await fetchLineupChemistry(order, 40, 15);
                if (active) setData(res);
            } catch (e) {
                if (active) setError(e?.response?.data?.detail || 'Could not load lineup chemistry data.');
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, [order]);

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Lineup Chemistry
                    <InfoTooltip label="How this works" title="Real 5-man lineups, not a trade simulation">
                        Real 5-man lineup combinations that have actually shared the floor this season, fetched
                        live from the NBA's own real lineup data. Rather than simulating a hypothetical trade by
                        inventing a usage-redistribution formula, this shows how real lineups that HAVE actually
                        played together have actually performed — real minutes, real possessions, real net
                        rating. A minimum shared-minutes cutoff is applied to filter out tiny, noisy samples,
                        shown below rather than hidden.
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                {data && (
                    <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                        {data.lineups_qualified} of {data.lineups_total} real lineups this season have at least{' '}
                        {data.min_minutes.toFixed(0)} shared minutes
                    </p>
                )}
                <div className="tab-bar" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
                    <button
                        className={`tab-btn ${order === 'best' ? 'tab-btn--active' : ''}`}
                        onClick={() => setOrder('best')}
                    >
                        Best Chemistry
                    </button>
                    <button
                        className={`tab-btn ${order === 'worst' ? 'tab-btn--active' : ''}`}
                        onClick={() => setOrder('worst')}
                    >
                        Worst Chemistry
                    </button>
                </div>
                {error && <p className="error-message">{error}</p>}
            </div>

            {loading && <Loader />}

            {!loading && data && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <TableExport />
                    <div className="hb-table-wrapper table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Rank</th>
                                    <th>Lineup</th>
                                    <th>Team</th>
                                    <th>GP</th>
                                    <th>Min</th>
                                    <th>Off Rtg</th>
                                    <th>Def Rtg</th>
                                    <th>Net Rtg</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.results.map((r) => (
                                    <tr key={r.rank}>
                                        <td>{r.rank}</td>
                                        <td>
                                            <div className="entity-row" style={{ gap: 4 }}>
                                                {r.players.map((p) => (
                                                    <PlayerHeadshot
                                                        key={p.player_id}
                                                        playerId={p.player_id}
                                                        playerName={p.player_name}
                                                        size={26}
                                                    />
                                                ))}
                                            </div>
                                            <div style={{ fontSize: '0.8em', color: 'var(--text-secondary)', marginTop: 2 }}>
                                                {r.players.map((p) => p.player_name).join(' · ')}
                                            </div>
                                        </td>
                                        <td><TeamLink abbr={r.team_abbreviation}><TeamLogo abbreviation={r.team_abbreviation} size={20} /></TeamLink></td>
                                        <td>{r.gp}</td>
                                        <td>{r.min.toFixed(0)}</td>
                                        <td>{r.off_rating.toFixed(1)}</td>
                                        <td>{r.def_rating.toFixed(1)}</td>
                                        <td style={{ color: netRatingColor(r.net_rating), fontWeight: 700 }}>
                                            <Icon
                                                name={r.net_rating > 0 ? 'arrow_upward' : r.net_rating < 0 ? 'arrow_downward' : 'remove'}
                                                size="0.9em"
                                                style={{ verticalAlign: 'middle', marginRight: 2 }}
                                            />
                                            {r.net_rating > 0 ? '+' : ''}{r.net_rating.toFixed(1)}
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
