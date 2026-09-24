import React, { useEffect, useState } from 'react';
import { fetchRefereeTendencies } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';

const SORT_OPTIONS = [
    { value: 'n_games', label: 'Games Worked' },
    { value: 'fouls_diff_pct', label: 'Fouls vs League (|diff|)' },
    { value: 'fta_diff_pct', label: 'FTA vs League (|diff|)' },
    { value: 'pace_diff_pct', label: 'Pace vs League (|diff|)' },
    { value: 'name', label: 'Name' },
];

function DiffCell({ diffPct, ciLow, ciHigh }) {
    if (diffPct == null) return <td>—</td>;
    const color = Math.abs(diffPct) < 0.01 ? '#94a3b8' : diffPct > 0 ? '#f87171' : '#38bdf8';
    return (
        <td style={{ color, fontWeight: 600 }}>
            {diffPct > 0 ? '+' : ''}{diffPct.toFixed(1)}%
            {ciLow != null && ciHigh != null && (
                <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem', fontWeight: 400 }}>
                    95% CI [{ciLow.toFixed(2)}, {ciHigh.toFixed(2)}]
                </span>
            )}
        </td>
    );
}

export default function RefereeTendenciesSection() {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [minGames, setMinGames] = useState(10);
    const [sort, setSort] = useState('n_games');

    useEffect(() => {
        let active = true;
        setLoading(true);
        fetchRefereeTendencies(minGames, sort)
            .then((d) => { if (active) { setData(d); setError(''); } })
            .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load referee tendencies.'); })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [minGames, sort]);

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Referee Tendencies
                    <InfoTooltip label="How this works" title="Descriptive real totals, not a bias claim">
                        For each real NBA official, real total fouls called and real free throws attempted in games
                        they worked (BoxScoreSummaryV2 officials, LeagueGameFinder box stats), compared against the
                        real league average for those same real seasons with a 95% confidence interval on the
                        difference. This is a descriptive comparison of real totals — it does not and cannot account
                        for which teams' games an official was assigned to, crew composition, or era, and is not a
                        claim about intent or bias. Officials below 25 real games worked are flagged as a small
                        sample, where ordinary game-to-game variance alone can produce a large-looking difference.
                        Some very recent games are known to be missing official data (a real gap in nba_api's own
                        BoxScoreSummaryV2 for games on/after April 2025).
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                {data && (
                    <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                        {data.total_officials_tracked} real officials tracked across real seasons {data.season_span?.min}–{data.season_span?.max}.
                    </p>
                )}
            </div>

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center', marginBottom: '1rem' }}>
                    <label className="page-subtitle">
                        Min. games worked:{' '}
                        <select value={minGames} onChange={(e) => setMinGames(Number(e.target.value))}>
                            {[1, 5, 10, 25, 50, 100].map((n) => (
                                <option key={n} value={n}>{n}</option>
                            ))}
                        </select>
                    </label>
                    <label className="page-subtitle">
                        Sort by:{' '}
                        <select value={sort} onChange={(e) => setSort(e.target.value)}>
                            {SORT_OPTIONS.map((o) => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                        </select>
                    </label>
                </div>

                {loading && <Loader />}
                {error && <p className="error-message">{error}</p>}

                {data && !loading && (
                    <div className="table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Official</th>
                                    <th>Games</th>
                                    <th>Avg Fouls/G</th>
                                    <th>Fouls vs League</th>
                                    <th>Avg FTA/G</th>
                                    <th>FTA vs League</th>
                                    <th>Pace vs League</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.officials.map((o) => (
                                    <tr key={o.official_id} style={{ opacity: o.small_n_warning ? 0.55 : 1 }}>
                                        <td>
                                            {o.official_name}
                                            {o.small_n_warning && (
                                                <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem' }}>
                                                    small sample
                                                </span>
                                            )}
                                        </td>
                                        <td>{o.n_games}</td>
                                        <td>{o.avg_total_fouls?.toFixed(1)}</td>
                                        <DiffCell diffPct={o.fouls_diff_pct} ciLow={o.fouls_ci_low} ciHigh={o.fouls_ci_high} />
                                        <td>{o.avg_total_fta?.toFixed(1)}</td>
                                        <DiffCell diffPct={o.fta_diff_pct} ciLow={o.fta_ci_low} ciHigh={o.fta_ci_high} />
                                        <td>{o.pace_diff_pct > 0 ? '+' : ''}{o.pace_diff_pct?.toFixed(1)}%</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        {data.officials.length === 0 && (
                            <p className="empty-message">No officials meet that minimum-games threshold yet.</p>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}
