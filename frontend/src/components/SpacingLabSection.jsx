import React, { useEffect, useMemo, useState } from 'react';
import { fetchGravity, fetchLineupSpacing } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import PlayerHeadshot from './common/PlayerHeadshot';
import TeamLogo from './common/TeamLogo';
import SourceBadge from './common/SourceBadge';

const WARM = [249, 115, 22]; // congested
const COOL = [56, 189, 248]; // open

function seasonLabel(season) {
    return `${season - 1}-${String(season).slice(-2)}`;
}

function signed(v, digits = 2) {
    if (v == null) return '—';
    return `${v > 0 ? '+' : ''}${v.toFixed(digits)}`;
}

function ordinal(n) {
    const r = Math.round(n);
    const suffixes = ['th', 'st', 'nd', 'rd'];
    const v = r % 100;
    return `${r}${suffixes[(v - 20) % 10] || suffixes[v] || suffixes[0]}`;
}

function tint(percentile) {
    const t = Math.max(0, Math.min(1, (percentile ?? 50) / 100));
    const c = WARM.map((w, i) => Math.round(w + (COOL[i] - w) * t));
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

// Half court in NBA shot-chart units (tenths of a foot, hoop at 0,0,
// baseline 52.5 behind it), drawn with the baseline at the top.
function HalfCourt({ percentile }) {
    const Y = (y) => y + 52.5;
    const arcY = Math.sqrt(237.5 ** 2 - 220 ** 2);
    const fill = percentile == null ? 'var(--bg-elevated)' : tint(percentile);
    const line = 'var(--text-secondary)';
    return (
        <svg viewBox="-260 -10 520 480" style={{ width: '100%', maxWidth: 420, height: 'auto', display: 'block' }}
            role="img" aria-label="Half court tinted by this lineup's spacing percentile">
            <rect x={-250} y={0} width={500} height={470} fill={fill} fillOpacity={percentile == null ? 1 : 0.35} stroke={line} strokeWidth={3} />
            <rect x={-80} y={0} width={160} height={Y(137.5)} fill="none" stroke={line} strokeWidth={2} />
            <circle cx={0} cy={Y(137.5)} r={60} fill="none" stroke={line} strokeWidth={2} />
            <path d={`M -40 ${Y(0)} A 40 40 0 0 0 40 ${Y(0)}`} fill="none" stroke={line} strokeWidth={2} />
            <line x1={-220} y1={0} x2={-220} y2={Y(arcY)} stroke={line} strokeWidth={2} />
            <line x1={220} y1={0} x2={220} y2={Y(arcY)} stroke={line} strokeWidth={2} />
            <path d={`M -220 ${Y(arcY)} A 237.5 237.5 0 0 0 220 ${Y(arcY)}`} fill="none" stroke={line} strokeWidth={2} />
            <line x1={-30} y1={Y(-7.5)} x2={30} y2={Y(-7.5)} stroke={line} strokeWidth={3} />
            <circle cx={0} cy={Y(0)} r={7.5} fill="none" stroke="#f97316" strokeWidth={2.5} />
        </svg>
    );
}

function LineupBuilder({ data }) {
    const [names, setNames] = useState(['', '', '', '', '']);
    const [result, setResult] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const byName = useMemo(() => new Map(data.players.map((p) => [p.player_name.toLowerCase(), p])), [data]);
    const picked = names.map((n) => byName.get(n.trim().toLowerCase()) || null);
    const ids = picked.every(Boolean) ? picked.map((p) => p.player_id) : null;
    const idKey = ids ? ids.join(',') : '';
    const duplicate = ids && new Set(ids).size !== 5;

    useEffect(() => {
        if (!idKey || duplicate) return undefined;
        let active = true;
        Promise.resolve().then(() => {
            if (!active) return;
            setLoading(true);
            fetchLineupSpacing(idKey, data.season)
                .then((d) => { if (active) { setResult(d); setError(''); } })
                .catch((e) => { if (active) { setResult(null); setError(e?.response?.data?.detail || 'Could not score that lineup.'); } })
                .finally(() => { if (active) setLoading(false); });
        });
        return () => { active = false; };
    }, [idKey, duplicate, data.season]);

    const shown = ids && !duplicate ? result : null;
    const pred = shown?.predicted_ortg_change;

    return (
        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <div style={{ flex: '1 1 300px', minWidth: 0 }}>
                <datalist id="spacing-player-suggestions">
                    {data.players.map((p) => <option key={p.player_id} value={p.player_name} />)}
                </datalist>
                {names.map((n, i) => (
                    <div key={i} className="entity-row" style={{ marginBottom: 6, gap: 8 }}>
                        <input
                            type="text"
                            className="input-field"
                            style={{ flex: 1 }}
                            placeholder={`Player ${i + 1}`}
                            value={n}
                            list="spacing-player-suggestions"
                            onChange={(e) => setNames((cur) => cur.map((x, j) => (j === i ? e.target.value : x)))}
                        />
                        <span style={{ width: 70, textAlign: 'right', fontSize: '0.8rem', fontWeight: 700 }}>
                            {picked[i] ? signed(picked[i].gravity) : ''}
                        </span>
                    </div>
                ))}
                <button type="button" className="action-btn" style={{ fontSize: '0.8rem', marginTop: 4 }}
                    onClick={() => setNames(data.leaderboard.slice(0, 5).map((p) => p.player_name))}>
                    Try the five highest-Gravity players
                </button>
                {duplicate && <p className="error-message">Pick five different players.</p>}
                {error && <p className="error-message">{error}</p>}
                {!ids && <p className="page-subtitle" style={{ fontSize: '0.78rem' }}>Pick five players (500+ real minutes this season) to score a lineup.</p>}
            </div>

            <div style={{ flex: '1 1 320px', minWidth: 0 }}>
                <HalfCourt percentile={shown?.percentile_vs_real_lineups} />
                <p className="page-subtitle" style={{ fontSize: '0.72rem', marginTop: 4 }}>
                    Court tint shows this lineup's spacing percentile among real lineups (warm = congested, cool = open).
                    It visualizes the index; it is not where the players stand.
                </p>
                {loading && <Loader />}
                {shown && !loading && (
                    <div style={{ fontSize: '0.85rem' }}>
                        <div>
                            <strong>Lineup spacing {signed(shown.spacing)}</strong> — {ordinal(shown.percentile_vs_real_lineups)} percentile of{' '}
                            {shown.n_real_lineups} real {seasonLabel(shown.season)} lineups with 100+ possessions (median {signed(shown.median_real_spacing)}).
                        </div>
                        {pred ? (
                            <div style={{ marginTop: 6 }}>
                                <strong>Predicted ORtg change vs. a median-spacing lineup: {signed(pred.vs_median_lineup, 1)}</strong>{' '}
                                (95% CI {signed(pred.ci_low, 1)} to {signed(pred.ci_high, 1)}) points per 100 possessions.
                                <span className="page-subtitle" style={{ display: 'block', fontSize: '0.72rem' }}>{pred.note}</span>
                            </div>
                        ) : (
                            <div style={{ marginTop: 6 }}>{shown.no_effect_message}</div>
                        )}
                        {shown.real_lineup && (
                            <div style={{ marginTop: 6 }}>
                                These five really played together: <strong>{shown.real_lineup.off_rating.toFixed(1)} ORtg</strong> over{' '}
                                {shown.real_lineup.poss.toLocaleString()} real possessions.
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

export default function SpacingLabSection() {
    const [season, setSeason] = useState(null);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        Promise.resolve().then(() => {
            if (!active) return;
            setLoading(true);
            fetchGravity(season)
                .then((d) => { if (active) { setData(d); setError(''); } })
                .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load the Spacing Lab.'); })
                .finally(() => { if (active) setLoading(false); });
        });
        return () => { active = false; };
    }, [season]);

    const v = data?.validation;

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Gravity Index &amp; Spacing Lab
                    <InfoTooltip label="How this works" title="A disclosed composite proxy, not tracking gravity">
                        {data?.methodology || 'Loading methodology…'}
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                    How much a player's shooting pulls defenders, from real volume, real catch-and-shoot accuracy, and how
                    tightly defenders stay attached to his threes — and what that adds up to for a five-man lineup.
                </p>
                <label className="page-subtitle" style={{ display: 'inline-block', marginTop: '0.5rem' }}>
                    Season:{' '}
                    <select value={data?.season ?? ''} onChange={(e) => setSeason(Number(e.target.value))} disabled={!data}>
                        {(data?.seasons_available || []).map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                {v && (
                    <p className="page-subtitle" style={{ marginTop: '0.75rem', fontSize: '0.8rem' }}>
                        <strong>Validation:</strong> across {v.n_lineups.toLocaleString()} real lineups with {v.min_lineup_poss}+ possessions
                        ({seasonLabel(v.season_min)} to {seasonLabel(v.season_max)}), each point of lineup spacing goes with{' '}
                        {signed(v.coef_spacing, 3)} ORtg (95% CI {v.ci_low.toFixed(3)} to {v.ci_high.toFixed(3)},{' '}
                        {v.p_spacing < 0.001 ? 'p < 0.001' : `p = ${v.p_spacing.toFixed(3)}`}), holding the five players' summed OBPM and the season fixed;
                        standard errors clustered by team-season ({v.n_clusters} clusters). R² {v.r2.toFixed(3)} vs.{' '}
                        {v.r2_without_spacing.toFixed(3)} without spacing —{' '}
                        {v.p_spacing >= 0.05
                            ? 'no clear effect once lineup quality is accounted for.'
                            : v.r2 - v.r2_without_spacing < 0.005
                                ? 'a small effect: spacing adds little to what the five players’ OBPM already explains.'
                                : 'a real but modest effect.'}
                        {data.tracking_coverage?.share != null && (
                            <> Tracking covers {(data.tracking_coverage.share * 100).toFixed(1)}% of this season's real 3PA.</>
                        )}
                    </p>
                )}
                {error && <p className="error-message">{error}</p>}
            </div>

            {loading && <Loader />}

            {data && !loading && (
                <>
                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h4 className="section-heading" style={{ marginTop: 0 }}>Lineup builder</h4>
                        <LineupBuilder key={data.season} data={data} />
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h4 className="section-heading" style={{ marginTop: 0 }}>
                            Gravity leaders — {seasonLabel(data.season)} ({data.n_pool} players with 500+ minutes)
                        </h4>
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>#</th>
                                        <th>Player</th>
                                        <th>Gravity</th>
                                        <th>3PA / 100 poss</th>
                                        <th>C&amp;S 3P% (n)</th>
                                        <th>3PA with defender ≤ 6 ft (n)</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.leaderboard.map((r) => (
                                        <tr key={r.player_id}>
                                            <td>{r.rank}</td>
                                            <td>
                                                <div className="entity-row">
                                                    <PlayerHeadshot playerId={r.player_id} playerName={r.player_name} size={24} />
                                                    {r.player_name}
                                                    <TeamLogo abbreviation={r.team_abbreviation} size={14} style={{ marginLeft: 6 }} />
                                                </div>
                                            </td>
                                            <td style={{ fontWeight: 700 }}>{signed(r.gravity)}</td>
                                            <td>{r.three_rate.toFixed(1)} <span className="page-subtitle" style={{ fontSize: '0.7rem' }}>z {signed(r.z_three_rate)}</span></td>
                                            <td>
                                                {r.cs_pct_raw != null ? `${(r.cs_pct_raw * 100).toFixed(1)}%` : '—'} ({r.cs_fg3a})
                                                <span className="page-subtitle" style={{ display: 'block', fontSize: '0.7rem' }}>
                                                    shrunk {(r.cs_pct_shrunk * 100).toFixed(1)}% · z {signed(r.z_cs_pct)}
                                                </span>
                                            </td>
                                            <td>
                                                {r.def_fg3a > 0 ? `${((r.contested_fg3a / r.def_fg3a) * 100).toFixed(1)}%` : '—'} ({r.def_fg3a})
                                                <span className="page-subtitle" style={{ display: 'block', fontSize: '0.7rem' }}>
                                                    shrunk {(r.contested_share_shrunk * 100).toFixed(1)}% · z {signed(r.z_contested)}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
