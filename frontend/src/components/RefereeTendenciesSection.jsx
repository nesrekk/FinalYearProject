import React, { useEffect, useState } from 'react';
import { fetchRefereeCrewTendencies, fetchRefereeTendencies } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import TableExport from './common/TableExport';
import SegmentedControl from './ui/SegmentedControl';
import { plain, shownSign, signed } from '../utils/format';

const MODE_OPTIONS = [
    { value: 'official', label: 'By Official' },
    { value: 'crew', label: 'By Crew' },
];

const SORT_OPTIONS = [
    { value: 'n_games', label: 'Games Worked' },
    { value: 'fouls_diff_pct', label: 'Fouls vs League (|diff|)' },
    { value: 'fta_diff_pct', label: 'FTA vs League (|diff|)' },
    { value: 'pace_diff_pct', label: 'Pace vs League (|diff|)' },
    { value: 'name', label: 'Name' },
];

const CREW_MIN_GAMES_OPTIONS = [1, 2, 3, 4];

function DiffCell({ diffPct, ciLow, ciHigh }) {
    if (diffPct == null) return <td>—</td>;
    const color = shownSign(diffPct, 1) === 0 ? 'var(--text-3)' : diffPct > 0 ? 'var(--compare-a)' : 'var(--compare-b)';
    return (
        <td style={{ color, fontWeight: 600 }}>
            {signed(diffPct, 1, '-')}%
            {ciLow != null && ciHigh != null && (
                <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem', fontWeight: 400 }}>
                    95% CI [{plain(ciLow, 2)}, {plain(ciHigh, 2)}]
                </span>
            )}
        </td>
    );
}

export default function RefereeTendenciesSection() {
    const [mode, setMode] = useState('official'); // 'official' | 'crew'
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [minGames, setMinGames] = useState(10);
    const [crewMinGames, setCrewMinGames] = useState(1);
    const [sort, setSort] = useState('n_games');

    useEffect(() => {
        let active = true;
        Promise.resolve().then(() => {
            if (!active) return;
            setLoading(true);
            const fetcher = mode === 'crew'
                ? fetchRefereeCrewTendencies(crewMinGames, sort)
                : fetchRefereeTendencies(minGames, sort);
            fetcher
                .then((d) => { if (active) { setData(d); setError(''); } })
                .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load referee tendencies.'); })
                .finally(() => { if (active) setLoading(false); });
        });
        return () => { active = false; };
    }, [mode, minGames, crewMinGames, sort]);

    const rows = mode === 'crew' ? data?.crews : data?.officials;

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Referee Tendencies
                    <InfoTooltip label="How this works" title="Descriptive real totals, not a bias claim">
                        {mode === 'crew' ? (
                            <>
                                Real NBA games are worked by a 3-official crew. For each distinct real crew, real
                                total fouls called and real free throws attempted in games they worked together,
                                compared against the real league average for those same real seasons. Real crew
                                assignments are close to random game to game, so most real crews here worked together
                                only once or twice — that's a genuine finding about crew rarity, not a data gap.
                                Crews below {10} real games together are flagged as a small sample, which in practice
                                is nearly every crew; treat any one crew's numbers as a curiosity, not a reliable
                                estimate. Not a claim about intent or bias.
                            </>
                        ) : (
                            <>
                                For each real NBA official, real total fouls called and real free throws attempted in
                                games they worked (BoxScoreSummaryV2 officials, LeagueGameFinder box stats), compared
                                against the real league average for those same real seasons with a 95% confidence
                                interval on the difference. This is a descriptive comparison of real totals — it does
                                not and cannot account for which teams' games an official was assigned to, crew
                                composition, or era, and is not a claim about intent or bias. Officials below 25 real
                                games worked are flagged as a small sample, where ordinary game-to-game variance
                                alone can produce a large-looking difference. Some very recent games are known to be
                                missing official data (a real gap in nba_api's own BoxScoreSummaryV2 for games
                                on/after April 2025).
                            </>
                        )}
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                {data && mode === 'official' && (
                    <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                        {data.total_officials_tracked} real officials tracked across real seasons {data.season_span?.min}–{data.season_span?.max}.
                    </p>
                )}
                {data && mode === 'crew' && (
                    <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                        {data.total_crews_tracked} real 3-official crews tracked across real seasons {data.season_span?.min}–{data.season_span?.max}
                        {' '}— only {data.repeat_crews} worked together more than once.
                    </p>
                )}
            </div>

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center', marginBottom: '1rem' }}>
                    <SegmentedControl
                        options={MODE_OPTIONS}
                        value={mode}
                        onChange={setMode}
                        layoutIdPrefix="referee-mode"
                    />
                    {mode === 'official' ? (
                        <label className="page-subtitle">
                            Min. games worked:{' '}
                            <select value={minGames} onChange={(e) => setMinGames(Number(e.target.value))}>
                                {[1, 5, 10, 25, 50, 100].map((n) => (
                                    <option key={n} value={n}>{n}</option>
                                ))}
                            </select>
                        </label>
                    ) : (
                        <label className="page-subtitle">
                            Min. games together:{' '}
                            <select value={crewMinGames} onChange={(e) => setCrewMinGames(Number(e.target.value))}>
                                {CREW_MIN_GAMES_OPTIONS.map((n) => (
                                    <option key={n} value={n}>{n}</option>
                                ))}
                            </select>
                        </label>
                    )}
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
                    <>
                        <TableExport />
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>{mode === 'crew' ? 'Crew' : 'Official'}</th>
                                        <th>Games</th>
                                        <th>Avg Fouls/G</th>
                                        <th>Fouls vs League</th>
                                        <th>Avg FTA/G</th>
                                        <th>FTA vs League</th>
                                        <th>Pace vs League</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows?.map((row) => {
                                        const key = mode === 'crew' ? row.crew_key : row.official_id;
                                        const label = mode === 'crew' ? row.official_names : row.official_name;
                                        return (
                                            <tr key={key}>
                                                <td>
                                                    {label}
                                                    {row.small_n_warning && (
                                                        <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem' }}>
                                                            small sample
                                                        </span>
                                                    )}
                                                </td>
                                                <td>{row.n_games}</td>
                                                <td>{row.avg_total_fouls?.toFixed(1)}</td>
                                                <DiffCell diffPct={row.fouls_diff_pct} ciLow={row.fouls_ci_low} ciHigh={row.fouls_ci_high} />
                                                <td>{row.avg_total_fta?.toFixed(1)}</td>
                                                <DiffCell diffPct={row.fta_diff_pct} ciLow={row.fta_ci_low} ciHigh={row.fta_ci_high} />
                                                <td>{row.pace_diff_pct != null && signed(row.pace_diff_pct, 1, '-')}%</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                            {rows?.length === 0 && (
                                <p className="empty-message">
                                    {mode === 'crew' ? 'No crews meet that minimum-games threshold yet.' : 'No officials meet that minimum-games threshold yet.'}
                                </p>
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
