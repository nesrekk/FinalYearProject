import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchGarbageTime, fetchGarbageTimePlayer } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import PlayerHeadshot from './common/PlayerHeadshot';
import TeamLogo from './common/TeamLogo';
import SourceBadge from './common/SourceBadge';
import AboutModelDrawer from './ui/AboutModelDrawer';
import TableExport from './common/TableExport';
import ChartExport from './common/ChartExport';

const BUCKET_META = {
    // Theme tokens: each reads at 3:1 or better on both Paper and Ink (round 8, R8-050).
    garbage: { label: 'Garbage time', color: 'var(--text-3)' },
    low: { label: 'Low leverage', color: 'var(--compare-b)' },
    medium: { label: 'Medium leverage', color: 'var(--accent)' },
    high: { label: 'High leverage / clutch', color: 'var(--streak)' },
};

const MIN_PPG_OPTIONS = [0, 10, 15, 20];

function seasonLabel(season) {
    return `${season - 1}-${String(season).slice(-2)}`;
}

function pct(v, digits = 1) {
    return v == null ? '—' : `${(v * 100).toFixed(digits)}%`;
}

function ordinal(n) {
    const r = Math.round(n);
    const s = ['th', 'st', 'nd', 'rd'];
    const v = r % 100;
    return `${r}${s[(v - 20) % 10] || s[v] || s[0]}`;
}

// Spread label y-positions so no two are closer than `gap`: push down past
// neighbours, then pull back up from the bottom edge so nothing overflows.
function spreadLabels(items, gap, top, bottom) {
    const sorted = [...items].sort((a, b) => a.y - b.y);
    for (let i = 1; i < sorted.length; i++) {
        sorted[i].y = Math.max(sorted[i].y, sorted[i - 1].y + gap);
    }
    for (let i = sorted.length - 1; i >= 0; i--) {
        const limit = i === sorted.length - 1 ? bottom : sorted[i + 1].y - gap;
        sorted[i].y = Math.min(sorted[i].y, limit);
    }
    sorted.forEach((s) => { s.y = Math.max(top, s.y); });
    return sorted;
}

function SlopeChart({ rows, selectedId, onSelect }) {
    const svgRef = useRef(null);
    const [hoverId, setHoverId] = useState(null);
    const W = 640;
    const H = 500;
    const padTop = 30;
    const padBottom = 20;
    const xL = 130;
    const xR = 380;

    const { yScale, ticks } = useMemo(() => {
        const vals = rows.flatMap((r) => [r.ppg_raw, r.ppg_filtered]);
        const lo = Math.floor(Math.min(...vals) / 2) * 2;
        const hi = Math.ceil(Math.max(...vals) / 2) * 2;
        const scale = (v) => padTop + ((hi - v) / (hi - lo || 1)) * (H - padTop - padBottom);
        const t = [];
        for (let v = lo; v <= hi; v += 2) t.push(v);
        return { yScale: scale, ticks: t };
    }, [rows]);

    const labels = useMemo(
        () => spreadLabels(rows.map((r) => ({ id: r.player_id, y: yScale(r.ppg_filtered), name: r.player_name })), 13, padTop, H - 6),
        [rows, yScale]
    );
    const focusId = hoverId ?? selectedId;
    const focus = rows.find((r) => r.player_id === focusId);

    return (
        <div style={{ width: '100%', overflowX: 'auto' }}>
            <ChartExport svgRef={svgRef} name="raw vs filtered PPG" />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', minWidth: 480, height: 'auto', display: 'block' }} role="img"
                aria-label="Slope chart of raw PPG to filtered PPG for the top 30 scorers">
                {ticks.map((t) => (
                    <g key={t}>
                        <line x1={xL} x2={xR} y1={yScale(t)} y2={yScale(t)} stroke="var(--border)" strokeWidth="1" />
                        <text x={xL - 10} y={yScale(t) + 4} textAnchor="end" fontSize="11" fill="var(--text-muted)">{t}</text>
                    </g>
                ))}
                <text x={xL} y={16} textAnchor="middle" fontSize="12" fontWeight="700" fill="var(--text-secondary)">Raw PPG</text>
                <text x={xR} y={16} textAnchor="middle" fontSize="12" fontWeight="700" fill="var(--text-secondary)">Filtered PPG</text>

                {rows.map((r) => {
                    const isFocus = r.player_id === focusId;
                    return (
                        <g key={r.player_id}
                            onMouseEnter={() => setHoverId(r.player_id)}
                            onMouseLeave={() => setHoverId(null)}
                            onClick={() => onSelect(r.player_id)}
                            style={{ cursor: 'pointer' }}>
                            <line x1={xL} x2={xR} y1={yScale(r.ppg_raw)} y2={yScale(r.ppg_filtered)}
                                stroke="transparent" strokeWidth="10" />
                            <line x1={xL} x2={xR} y1={yScale(r.ppg_raw)} y2={yScale(r.ppg_filtered)}
                                stroke={isFocus ? 'var(--streak)' : 'var(--compare-b)'} strokeOpacity={focusId && !isFocus ? 0.25 : 0.8}
                                strokeWidth={isFocus ? 3 : 1.5} />
                            <circle cx={xL} cy={yScale(r.ppg_raw)} r={isFocus ? 4 : 2.5} fill={isFocus ? 'var(--streak)' : 'var(--compare-b)'} />
                            <circle cx={xR} cy={yScale(r.ppg_filtered)} r={isFocus ? 4 : 2.5} fill={isFocus ? 'var(--streak)' : 'var(--compare-b)'} />
                        </g>
                    );
                })}

                {labels.map((l) => {
                    const isFocus = l.id === focusId;
                    const r = rows.find((x) => x.player_id === l.id);
                    return (
                        <g key={l.id}>
                            <line x1={xR + 4} y1={yScale(r.ppg_filtered)} x2={xR + 44} y2={l.y}
                                stroke={isFocus ? 'var(--streak)' : 'var(--text-muted)'} strokeOpacity={isFocus ? 0.9 : 0.7} strokeWidth="1" />
                            <text x={xR + 48} y={l.y + 4} fontSize="11"
                                fontWeight={isFocus ? 700 : 400}
                                fill={isFocus ? 'var(--streak)' : focusId ? 'var(--text-muted)' : 'var(--text-secondary)'}
                                style={{ cursor: 'pointer' }}
                                onMouseEnter={() => setHoverId(l.id)}
                                onMouseLeave={() => setHoverId(null)}
                                onClick={() => onSelect(l.id)}>
                                {l.name} ({r.ppg_filtered.toFixed(1)})
                            </text>
                        </g>
                    );
                })}

                {focus && (
                    <text x={xL - 38} y={yScale(focus.ppg_raw) + 4} textAnchor="end" fontSize="11" fontWeight="700" fill="var(--streak)">
                        {focus.ppg_raw.toFixed(1)}
                    </text>
                )}
            </svg>
            {focus && (
                <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                    <strong>{focus.player_name}</strong>: {focus.ppg_raw.toFixed(1)} raw PPG → {focus.ppg_filtered.toFixed(1)} filtered
                    (True Production Ratio {focus.true_production_ratio.toFixed(3)}, {focus.games} real games)
                </p>
            )}
        </div>
    );
}

function LeaderTable({ title, tooltip, rows, valueKey, valueLabel, onSelect, showBadge, nQualified }) {
    return (
        <div className="dashboard-card" style={{ flex: '1 1 100%', minWidth: 0 }}>
            <h4 className="section-heading" style={{ marginTop: 0 }}>
                {title}
                <InfoTooltip label="What this ranks" title={title}>{tooltip}</InfoTooltip>
            </h4>
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Player</th>
                            <th>{valueLabel}</th>
                            <th>PPG raw → filtered</th>
                            <th>Games</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r, i) => (
                            <tr key={r.player_id} onClick={() => onSelect(r.player_id)} style={{ cursor: 'pointer' }}>
                                <td>{i + 1}</td>
                                <td>
                                    <div className="entity-row">
                                        <PlayerHeadshot playerId={r.player_id} playerName={r.player_name} size={24} />
                                        {r.player_name}
                                        <TeamLogo abbreviation={r.team_abbreviation} size={14} style={{ marginLeft: 6 }} />
                                    </div>
                                    {showBadge && r.padding_risk && (
                                        <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem', color: 'var(--negative)' }}>
                                            high stat-padding risk
                                        </span>
                                    )}
                                </td>
                                <td style={{ fontWeight: 700 }}>
                                    {pct(r[valueKey])}
                                    {showBadge && r.garbage_share_pctile != null && (
                                        <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem', fontWeight: 400 }}>
                                            {ordinal(r.garbage_share_pctile)} pctile of {nQualified} qualified
                                        </span>
                                    )}
                                </td>
                                <td>{r.ppg_raw.toFixed(1)} → {r.ppg_filtered.toFixed(1)}</td>
                                <td>{r.games}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {rows.length === 0 && <p className="empty-message">No qualified players at that scoring level.</p>}
            </div>
        </div>
    );
}

function PlayerCard({ detail, loading, error }) {
    if (loading) return <Loader />;
    if (error) return <p className="error-message">{error}</p>;
    if (!detail) return null;
    const p = detail.player;
    const maxPts = Math.max(1, ...detail.splits.map((s) => s.pts));
    return (
        <div>
            <div className="entity-row" style={{ gap: '0.75rem', marginBottom: '0.75rem' }}>
                <PlayerHeadshot playerId={p.player_id} playerName={p.player_name} size={48} />
                <div>
                    <div style={{ fontWeight: 700, fontSize: '1.05rem' }}>
                        {p.player_name} <TeamLogo abbreviation={p.team_abbreviation} size={16} style={{ marginLeft: 4 }} />
                    </div>
                    <div className="page-subtitle">
                        {seasonLabel(detail.season)} · {p.games} real games · {p.pts} real points
                        {p.official_ppg != null && ` · official line ${p.official_ppg} PPG over ${p.official_gp} GP`}
                    </div>
                </div>
            </div>

            {detail.small_sample_warning && (
                <p className="page-subtitle" style={{ color: 'var(--warning)' }}>
                    Small sample: under 40 real games, so this player isn't ranked or given a percentile.
                </p>
            )}
            {p.padding_risk && (
                <p style={{ color: 'var(--negative)', fontWeight: 700, margin: '0.25rem 0 0.75rem' }}>
                    High stat-padding risk: {pct(p.garbage_share)} of real points came in garbage time, the{' '}
                    {ordinal(p.garbage_share_pctile)} percentile among {detail.n_qualified_in_season} qualified players.
                </p>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '0.75rem', marginBottom: '1rem' }}>
                {[
                    ['Raw PPG', p.ppg_raw.toFixed(1)],
                    ['PPG without garbage time', p.ppg_ex_garbage.toFixed(1)],
                    ['Filtered PPG', p.ppg_filtered.toFixed(1)],
                    ['True Production Ratio', p.true_production_ratio?.toFixed(3) ?? '—'],
                    ['Leverage-weighted PPG', p.lw_ppg.toFixed(1)],
                    ['Garbage-time share', `${pct(p.garbage_share)}${p.garbage_share_pctile != null ? ` (${ordinal(p.garbage_share_pctile)} pctile)` : ''}`],
                ].map(([label, value]) => (
                    <div key={label} className="dashboard-card" style={{ padding: '0.6rem 0.75rem' }}>
                        <div className="page-subtitle" style={{ fontSize: '0.72rem' }}>{label}</div>
                        <div style={{ fontWeight: 700, fontSize: '1.1rem' }}>{value}</div>
                    </div>
                ))}
            </div>

            <div>
                {detail.splits.map((s) => (
                    <div key={s.bucket} style={{ marginBottom: '0.6rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }}>
                            <span style={{ fontWeight: 600 }}>{BUCKET_META[s.bucket].label}</span>
                            <span className="page-subtitle">
                                {s.pts} pts · {s.fgm}/{s.fga} FG{s.fg_pct != null ? ` (${pct(s.fg_pct)})` : ''} · {s.reb} reb · {s.ast} ast · {s.tov} tov
                            </span>
                        </div>
                        <div style={{ background: 'var(--bg-elevated)', borderRadius: 4, height: 12, overflow: 'hidden' }}>
                            <div style={{ width: `${(s.pts / maxPts) * 100}%`, height: '100%', background: BUCKET_META[s.bucket].color }} />
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}

export default function GarbageTimeSection() {
    const [season, setSeason] = useState(null);
    const [minPpg, setMinPpg] = useState(10);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [selectedId, setSelectedId] = useState(null);
    const [search, setSearch] = useState('');
    const [detail, setDetail] = useState(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [detailError, setDetailError] = useState('');

    useEffect(() => {
        let active = true;
        Promise.resolve().then(() => {
            if (!active) return;
            setLoading(true);
            fetchGarbageTime(season, minPpg)
                .then((d) => {
                    if (!active) return;
                    setData(d);
                    setError('');
                    setSelectedId((cur) => (cur && d.players.some((p) => p.player_id === cur) ? cur : d.top_scorers[0]?.player_id ?? null));
                })
                .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load the Garbage-Time Deflator.'); })
                .finally(() => { if (active) setLoading(false); });
        });
        return () => { active = false; };
    }, [season, minPpg]);

    const activeSeason = data?.season;
    useEffect(() => {
        if (!selectedId || !activeSeason) return undefined;
        let active = true;
        Promise.resolve().then(() => {
            if (!active) return;
            setDetailLoading(true);
            fetchGarbageTimePlayer(selectedId, activeSeason)
                .then((d) => { if (active) { setDetail(d); setDetailError(''); } })
                .catch((e) => { if (active) setDetailError(e?.response?.data?.detail || 'Could not load that player.'); })
                .finally(() => { if (active) setDetailLoading(false); });
        });
        return () => { active = false; };
    }, [selectedId, activeSeason]);

    function handleSearch(value) {
        setSearch(value);
        const match = data?.players.find((p) => p.player_name.toLowerCase() === value.trim().toLowerCase());
        if (match) setSelectedId(match.player_id);
    }

    const v = data?.validation;

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Garbage-Time Deflator
                    <InfoTooltip label="How this works" title="Real production, re-weighted by real leverage">
                        {data?.methodology || 'Loading methodology…'}
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                    A box score counts a basket in a 25-point blowout the same as one in a tied game. This splits every
                    real point by how much the game was actually on the line when it was scored.
                </p>
                <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '0.75rem' }}>
                    <label className="page-subtitle">
                        Season:{' '}
                        <select value={activeSeason ?? ''} onChange={(e) => setSeason(Number(e.target.value))} disabled={!data}>
                            {(data?.seasons_available || []).map((s) => (
                                <option key={s} value={s}>{seasonLabel(s)}</option>
                            ))}
                        </select>
                    </label>
                    <label className="page-subtitle">
                        Leaderboards — min. raw PPG:{' '}
                        <select value={minPpg} onChange={(e) => setMinPpg(Number(e.target.value))}>
                            {MIN_PPG_OPTIONS.map((n) => (
                                <option key={n} value={n}>{n === 0 ? 'Any' : `${n}+`}</option>
                            ))}
                        </select>
                    </label>
                </div>
                {v && (
                    <p className="page-subtitle" style={{ marginTop: '0.75rem', fontSize: '0.8rem' }}>
                        <strong>Validation:</strong> rebuilt raw PPG vs. the official per-game line, r = {v.ppg_vs_official_r.toFixed(4)},
                        mean abs. error {v.ppg_vs_official_mae.toFixed(2)} PPG (n = {v.ppg_vs_official_n} qualified players) ·{' '}
                        {pct(v.points_attribution_rate, 2)} of real points credited to a matched player ·{' '}
                        {pct(v.assist_match_rate)} of assists matched · {v.n_games} real games.
                        {v.note && <span style={{ display: 'block', marginTop: '0.25rem' }}>{v.note}</span>}
                    </p>
                )}
                {error && <p className="error-message">{error}</p>}
            </div>

            {data && (
                <AboutModelDrawer title="Methods & thresholds">
                    <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
                        {['garbage', 'high', 'low', 'medium'].map((b) => (
                            <li key={b}>
                                <strong><span aria-hidden="true" style={{ display: 'inline-block', width: 10, height: 10, marginRight: 6, background: BUCKET_META[b].color }} />{BUCKET_META[b].label}:</strong> {data.thresholds[b]}
                            </li>
                        ))}
                        <li>{data.thresholds.order}</li>
                        <li><strong>Qualified:</strong> {data.thresholds.qualified}. <strong>Stat-padding badge:</strong> {data.thresholds.padding_badge}.</li>
                        <li>
                            <strong>Leverage Index:</strong> the expected absolute win-probability swing of the next play at that
                            game state (seconds left × score margin), from this project's own win-probability model and the real
                            league-wide mix of scoring outcomes, scaled so the average real play is 1.0.
                        </li>
                        {v && (
                            <li>
                                This season: {pct(v.event_share_by_bucket.garbage)} of real plays were garbage time,{' '}
                                {pct(v.event_share_by_bucket.low)} low, {pct(v.event_share_by_bucket.medium)} medium,{' '}
                                {pct(v.event_share_by_bucket.high)} high. {v.clutch_events_reclassified_garbage.toLocaleString()} of{' '}
                                {v.clutch_events.toLocaleString()} real NBA-clutch-time plays were already &gt;99% decided and count as
                                garbage. Missed 3-point attempts are identified from the play text or a listed distance of 23+ ft
                                ({pct(v.three_pa_rule_accuracy, 2)} accurate on real made shots, where the real score change confirms it).
                            </li>
                        )}
                    </ul>
                </AboutModelDrawer>
            )}

            {loading && <Loader />}

            {data && !loading && (
                <>
                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h4 className="section-heading" style={{ marginTop: 0 }}>
                            Top 30 scorers: raw PPG → filtered PPG
                            <InfoTooltip label="Reading this chart" title="Filtered PPG">
                                Each line is one of the 30 highest-scoring qualified players (40+ real games). Filtered PPG
                                removes points scored in garbage time and low-leverage moments. A steeper drop means more of
                                that player's scoring came when the game was already decided. Hover or click a line to see
                                that player's full split below.
                            </InfoTooltip>
                        </h4>
                        <SlopeChart rows={data.top_scorers} selectedId={selectedId} onSelect={setSelectedId} />
                    </div>

                    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginTop: '1rem' }}>
                        <LeaderTable
                            title="Most empty calories"
                            tooltip="Qualified players (40+ real games) with the highest share of their real points scored in garbage time. Deep-bench players dominate at 'Any', because blowouts are when they play — raise the minimum PPG to compare rotation scorers."
                            rows={data.empty_calories}
                            valueKey="garbage_share"
                            valueLabel="Garbage share"
                            onSelect={setSelectedId}
                            showBadge
                            nQualified={v?.n_qualified}
                        />
                        <LeaderTable
                            title="Most clutch-heavy"
                            tooltip="Qualified players (40+ real games) with the highest share of their real points scored in high-leverage moments (Leverage Index 1.5+ or NBA clutch time)."
                            rows={data.clutch_heavy}
                            valueKey="high_share"
                            valueLabel="High-leverage share"
                            onSelect={setSelectedId}
                        />
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h4 className="section-heading" style={{ marginTop: 0 }}>Player split</h4>
                        <div className="input-row" style={{ marginBottom: '1rem' }}>
                            <input
                                type="text"
                                className="input-field"
                                placeholder={`Search any player in ${seasonLabel(data.season)}`}
                                value={search}
                                onChange={(e) => handleSearch(e.target.value)}
                                list="garbage-time-player-suggestions"
                            />
                            <datalist id="garbage-time-player-suggestions">
                                {data.players.map((p) => (
                                    <option key={p.player_id} value={p.player_name} />
                                ))}
                            </datalist>
                        </div>
                        <PlayerCard detail={detail} loading={detailLoading} error={detailError} />
                    </div>
                </>
            )}
        </div>
    );
}
