import React, { useEffect, useMemo, useState } from 'react';
import { fetchClutchSplit } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import PlayerName from './common/PlayerName';
import TeamLogo from './common/TeamLogo';
import SourceBadge from './common/SourceBadge';
import TableExport from './common/TableExport';

const SORT_OPTIONS = [
    { value: 'lift', label: 'Clutch lift' },
    { value: 'chances', label: 'Clutch chances' },
    { value: 'certainty', label: 'Furthest from zero (z)' },
];

const VERDICT_LABEL = { better: 'Better in clutch', worse: 'Worse in clutch', same: 'Can\'t tell apart' };
const VERDICT_COLOR = { better: 'var(--positive)', worse: 'var(--negative)', same: 'var(--text-3)' };

// Shared x-range for every row's interval, so bars are comparable down the table.
const AXIS = 0.6;

function signed(v) {
    const r = Math.round(v * 100) / 100;
    if (r === 0) return '0.00';
    return `${r > 0 ? '+' : ''}${r.toFixed(2)}`;
}

function CiBar({ low, high, lift, verdict }) {
    const w = 140;
    const x = (v) => ((Math.max(-AXIS, Math.min(AXIS, v)) + AXIS) / (2 * AXIS)) * w;
    const color = VERDICT_COLOR[verdict];
    return (
        <svg width={w} height={16} viewBox={`0 0 ${w} 16`} aria-hidden="true" style={{ display: 'block' }}>
            <line x1={x(0)} x2={x(0)} y1={1} y2={15} stroke="var(--line)" strokeWidth={1} />
            <line x1={x(low)} x2={x(high)} y1={8} y2={8} stroke={color} strokeWidth={2} strokeLinecap="round" />
            <circle cx={x(lift)} cy={8} r={3.5} fill={color} />
        </svg>
    );
}

export default function ClutchSplitSection() {
    const [floor, setFloor] = useState(100);
    const [sort, setSort] = useState('lift');
    const [onlyOutside, setOnlyOutside] = useState(false);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError('');
        (async () => {
            try {
                const res = await fetchClutchSplit(floor);
                if (active) setData(res);
            } catch (e) {
                if (active) setError(e?.response?.data?.detail || 'Could not load the clutch split.');
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, [floor]);

    const rows = useMemo(() => {
        if (!data) return [];
        const list = onlyOutside ? data.results.filter((r) => r.verdict !== 'same') : [...data.results];
        const z = (r) => Math.abs(r.clutch_lift) / ((r.ci_high - r.ci_low) / 3.92);
        if (sort === 'chances') list.sort((a, b) => b.clutch_chances - a.clutch_chances);
        else if (sort === 'certainty') list.sort((a, b) => z(b) - z(a));
        else list.sort((a, b) => b.clutch_lift - a.clutch_lift);
        return list;
    }, [data, sort, onlyOutside]);

    const league = data?.league;

    return (
        <div className="dashboard-card" style={{ marginTop: '1rem' }}>
            <h3 className="section-heading" style={{ marginTop: 0 }}>
                Clutch vs. the rest of the game
                <InfoTooltip label="How this works" title="Compared at equal leverage">
                    A clutch play moves win probability about {league?.clutch_leverage_ratio ?? '3.7'}× as much as
                    an average play, so raw per-play WPA would call almost everyone clutch. Each play&apos;s WPA is
                    divided by its leverage (the win-probability value of one point at that moment, from the same
                    model), which puts it in points. Rates are per scoring chance: a shot, free throw or turnover.
                    Rebounds, fouls and substitutions earn almost no WPA here and would mix in how often a player
                    gets the ball. Clutch lift = clutch rate − non-clutch rate − the league&apos;s own change, with a
                    95% interval clustered by game. The model doesn&apos;t know who has the ball, so a miss or
                    turnover costs nothing: this rewards scoring, not defense.
                </InfoTooltip>
                <SourceBadge source={data?._source} />
            </h3>

            {data && league && (
                <>
                    <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                        {data.n_games.toLocaleString()} games, {data.seasons[0] - 1}-{String(data.seasons[0]).slice(-2)} to{' '}
                        {data.seasons[1] - 1}-{String(data.seasons[1]).slice(-2)}. League average per scoring chance at equal
                        leverage: <strong>{league.nonclutch_pts_rate.toFixed(3)}</strong> points outside clutch time
                        ({league.nonclutch_chances.toLocaleString()} chances), <strong>{league.clutch_pts_rate.toFixed(3)}</strong> in
                        it ({league.clutch_chances.toLocaleString()}). Everyone drops by about {Math.abs(league.shift).toFixed(2)};
                        the lift below is measured against that drop.
                    </p>
                    <p style={{ margin: '0.75rem 0 0', fontWeight: 600 }}>
                        Of {data.n_players} players with {data.min_clutch_chances}+ clutch chances,{' '}
                        <span style={{ color: 'var(--positive)' }}>{data.n_better} were better</span> and{' '}
                        <span style={{ color: 'var(--negative)' }}>{data.n_worse} worse</span> in the clutch at 95%.
                        About {Math.round(data.expected_by_chance)} would land outside zero by chance alone, so for
                        nearly every player clutch and non-clutch can&apos;t be told apart.
                    </p>
                </>
            )}

            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center', margin: '1rem 0' }}>
                <label className="page-subtitle">
                    Min. clutch chances:{' '}
                    <select value={floor} onChange={(e) => setFloor(Number(e.target.value))}>
                        {(data?.floors || [50, 100, 200]).map((n) => (
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
                <label className="page-subtitle" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <input type="checkbox" checked={onlyOutside} onChange={(e) => setOnlyOutside(e.target.checked)} />
                    Only players outside zero
                </label>
            </div>

            {error && <p className="error-message">{error}</p>}
            {loading && <Loader />}

            {!loading && data && (
                <>
                    <TableExport />
                    <div className="table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Player</th>
                                    <th>Games</th>
                                    <th>Clutch chances</th>
                                    <th>Clutch pts / chance</th>
                                    <th>Other pts / chance</th>
                                    <th>Clutch lift (95% CI)</th>
                                    <th aria-label="Interval chart">−{AXIS} · 0 · +{AXIS}</th>
                                    <th>Verdict</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((r) => (
                                    <tr key={r.player_id}>
                                        <td>
                                            <PlayerName playerId={r.player_id} name={r.player_name}>
                                                <TeamLogo abbreviation={r.team_abbreviation} size={16} style={{ marginLeft: 6 }} />
                                            </PlayerName>
                                        </td>
                                        <td>{r.n_games}</td>
                                        <td>{r.clutch_chances}</td>
                                        <td>{r.clutch_pts_rate.toFixed(3)}</td>
                                        <td>{r.nonclutch_pts_rate.toFixed(3)}</td>
                                        <td style={{ color: VERDICT_COLOR[r.verdict], fontWeight: r.verdict === 'same' ? 400 : 700, whiteSpace: 'nowrap' }}>
                                            {signed(r.clutch_lift)}{' '}
                                            <span style={{ fontWeight: 400, color: 'var(--text-3)' }}>
                                                ({signed(r.ci_low)} to {signed(r.ci_high)})
                                            </span>
                                        </td>
                                        <td><CiBar low={r.ci_low} high={r.ci_high} lift={r.clutch_lift} verdict={r.verdict} /></td>
                                        <td style={{ color: VERDICT_COLOR[r.verdict], whiteSpace: 'nowrap' }}>{VERDICT_LABEL[r.verdict]}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        {rows.length === 0 && <p className="empty-message">No players at this floor are outside zero.</p>}
                    </div>
                </>
            )}
        </div>
    );
}
