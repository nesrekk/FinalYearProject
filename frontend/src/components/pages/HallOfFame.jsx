import React, { useEffect, useState } from 'react';
import { fetchHofCareerLeaders, fetchHofGreatestSeasons, fetchHofLongevity } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import SourceBadge from '../common/SourceBadge';
import Icon from '../common/Icon';
import PlayerHeadshot from '../common/PlayerHeadshot';

const STAT_OPTIONS = [
    { key: 'pts', label: 'Points' },
    { key: 'reb', label: 'Rebounds' },
    { key: 'ast', label: 'Assists' },
    { key: 'stl', label: 'Steals' },
    { key: 'blk', label: 'Blocks' },
];

const TABS = [
    { key: 'career', label: 'Career Leaders' },
    { key: 'seasons', label: 'Greatest Seasons' },
    { key: 'longevity', label: 'Longevity' },
];

function seasonSpan(first, last) {
    return `${first - 1}-${String(first).slice(-2)} to ${last - 1}-${String(last).slice(-2)}`;
}

export default function HallOfFame() {
    const [tab, setTab] = useState('career');
    const [stat, setStat] = useState('pts');
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError('');
        setData(null); // clear the previous tab's data immediately — its shape doesn't match the new tab
        const load = tab === 'career'
            ? fetchHofCareerLeaders(stat, 50)
            : tab === 'seasons'
                ? fetchHofGreatestSeasons(stat, 50, 50)
                : fetchHofLongevity(50);
        load
            .then((res) => { if (active) setData(res); })
            .catch((e) => {
                if (!active) return;
                setData(null);
                setError(e?.response?.data?.detail || 'Could not load this real leaderboard.');
            })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [tab, stat]);

    return (
        <div className="page fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="stars" /></span>
                    Hall of Fame
                    <InfoTooltip label="How this works" title="Real all-time leaderboards, not verified induction">
                        "Hall of Fame" here means real statistical greatness — all-time career totals, single-season
                        records, and career-longevity leaders — computed from every real player-season on file,
                        1950 through the current season. This project has no real Naismith Hall of Fame induction
                        dataset (no API we use provides one), so this page is deliberately about real, verifiable
                        stats rather than a claim about who was actually inducted.
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h2>
                <p className="page-subtitle">Real career and single-season records across 75+ real NBA seasons.</p>

                <div className="hb-rail-chips" style={{ marginTop: '0.75rem' }}>
                    {TABS.map((t) => (
                        <button
                            key={t.key}
                            type="button"
                            className={`hb-rail-item ${tab === t.key ? 'hb-rail-item--active' : ''}`}
                            onClick={() => { setData(null); setTab(t.key); }}
                            style={{ display: 'inline-flex', width: 'auto', textAlign: 'center' }}
                        >
                            {t.label}
                        </button>
                    ))}
                </div>

                {tab !== 'longevity' && (
                    <div className="input-row" style={{ marginTop: '0.75rem' }}>
                        <select className="input-field" value={stat} onChange={(e) => setStat(e.target.value)} style={{ maxWidth: 200 }}>
                            {STAT_OPTIONS.map((s) => (
                                <option key={s.key} value={s.key}>{s.label}</option>
                            ))}
                        </select>
                        {tab === 'seasons' && (
                            <span className="page-subtitle" style={{ alignSelf: 'center', margin: 0 }}>
                                Min. 50 games played that real season
                            </span>
                        )}
                    </div>
                )}
            </div>

            {loading && <Loader />}
            {error && <p className="error-message" style={{ marginTop: '1rem' }}>{error}</p>}

            {!loading && !error && data && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <div className="hb-table-wrapper table-wrapper">
                        <table className="data-table">
                            {tab === 'career' && (
                                <>
                                    <thead>
                                        <tr>
                                            <th>#</th><th>Player</th><th>Career {STAT_OPTIONS.find((s) => s.key === stat)?.label}</th>
                                            <th>Per Game</th><th>Seasons</th><th>GP</th><th>Span</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.leaders.map((l) => (
                                            <tr key={l.player_id}>
                                                <td>{l.rank}</td>
                                                <td>
                                                    <div className="entity-row">
                                                        <PlayerHeadshot playerId={l.player_id} playerName={l.player_name} size={26} />
                                                        {l.player_name}
                                                    </div>
                                                </td>
                                                <td>{l.career_total.toLocaleString()}</td>
                                                <td>{l.per_game}</td>
                                                <td>{l.seasons_played}</td>
                                                <td>{l.career_gp.toLocaleString()}</td>
                                                <td>{seasonSpan(l.first_season, l.last_season)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </>
                            )}
                            {tab === 'seasons' && (
                                <>
                                    <thead>
                                        <tr>
                                            <th>#</th><th>Player</th><th>Season</th><th>Age</th><th>GP</th>
                                            <th>{STAT_OPTIONS.find((s) => s.key === stat)?.label}</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.seasons.map((s) => (
                                            <tr key={`${s.player_id}-${s.season}`}>
                                                <td>{s.rank}</td>
                                                <td>
                                                    <div className="entity-row">
                                                        <PlayerHeadshot playerId={s.player_id} playerName={s.player_name} size={26} />
                                                        {s.player_name}
                                                    </div>
                                                </td>
                                                <td>{s.season_label}</td>
                                                <td>{s.age}</td>
                                                <td>{s.gp}</td>
                                                <td>{s.value}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </>
                            )}
                            {tab === 'longevity' && (
                                <>
                                    <thead>
                                        <tr>
                                            <th>#</th><th>Player</th><th>Seasons Played</th><th>Career GP</th><th>Span</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.leaders.map((l) => (
                                            <tr key={l.player_id}>
                                                <td>{l.rank}</td>
                                                <td>
                                                    <div className="entity-row">
                                                        <PlayerHeadshot playerId={l.player_id} playerName={l.player_name} size={26} />
                                                        {l.player_name}
                                                    </div>
                                                </td>
                                                <td>{l.seasons_played}</td>
                                                <td>{l.career_gp.toLocaleString()}</td>
                                                <td>{seasonSpan(l.first_season, l.last_season)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </>
                            )}
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}
