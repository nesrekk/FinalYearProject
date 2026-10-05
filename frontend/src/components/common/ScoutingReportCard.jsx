import React, { useEffect, useState } from 'react';
import { fetchScoutingReport } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from './InfoTooltip';
import SourceBadge from './SourceBadge';

const STRENGTH = 'var(--positive)';
const WEAKNESS = 'var(--negative)';

function seasonLabel(season) {
    return `${season - 1}-${String(season).slice(-2)}`;
}

function fmtValue(category, v) {
    if (v == null) return '—';
    return category === 'playtype' ? `${v.toFixed(2)} PPP` : `${(v * 100).toFixed(1)}%`;
}

function fmtP(p) {
    if (p == null) return '—';
    return p < 0.001 ? 'p < 0.001' : `p = ${p.toFixed(3)}`;
}

// Player value vs. baseline, both on the same scale.
function MiniBars({ finding, color }) {
    const scale = Math.max(finding.value, finding.baseline) * 1.1 || 1;
    const baseLabel = finding.category === 'leverage' ? 'his other moments' : 'league';
    return (
        <div style={{ display: 'grid', gridTemplateColumns: '70px 1fr 64px', gap: '2px 6px', alignItems: 'center', fontSize: '0.72rem' }}>
            <span style={{ color: 'var(--text-secondary)' }}>Player</span>
            <div style={{ height: 7, background: 'var(--bg-elevated)', borderRadius: 4 }}>
                <div style={{ width: `${(finding.value / scale) * 100}%`, height: '100%', background: color, borderRadius: 4 }} />
            </div>
            <span style={{ textAlign: 'right', fontWeight: 700 }}>{fmtValue(finding.category, finding.value)}</span>
            <span style={{ color: 'var(--text-muted)' }}>{baseLabel === 'league' ? 'League' : 'Other'}</span>
            <div style={{ height: 7, background: 'var(--bg-elevated)', borderRadius: 4 }}>
                <div style={{ width: `${(finding.baseline / scale) * 100}%`, height: '100%', background: 'var(--text-muted)', borderRadius: 4 }} />
            </div>
            <span style={{ textAlign: 'right', color: 'var(--text-muted)' }}>{fmtValue(finding.category, finding.baseline)}</span>
        </div>
    );
}

function FindingList({ title, items, color, emptyText }) {
    return (
        <div style={{ flex: '1 1 240px', minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: '0.8rem', color, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                {title}
            </div>
            {items.length === 0 && <p className="page-subtitle" style={{ fontSize: '0.78rem', margin: 0 }}>{emptyText}</p>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {items.map((f) => (
                    <div key={`${f.category}-${f.split}`} style={{ borderLeft: `3px solid ${color}`, paddingLeft: 8 }}>
                        <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>
                            {f.label}
                            <span className="page-subtitle" style={{ fontWeight: 400, fontSize: '0.72rem', marginLeft: 6 }}>
                                n = {f.n} {f.n_unit} · {fmtP(f.p)}
                            </span>
                        </div>
                        <MiniBars finding={f} color={color} />
                    </div>
                ))}
            </div>
        </div>
    );
}

// playerId (when the caller knows it) picks the player exactly: two players can share a name.
export default function ScoutingReportCard({ playerName, playerId, season, titleClassName = 'player-modal-section-title' }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        Promise.resolve().then(() => {
            if (!active) return;
            setLoading(true);
            setError('');
            fetchScoutingReport(playerName, season, playerId)
                .then((d) => { if (active) setData(d); })
                .catch((e) => {
                    if (!active) return;
                    setData(null);
                    setError(e?.response?.data?.detail || 'Could not load a scouting report for this player.');
                })
                .finally(() => { if (active) setLoading(false); });
        });
        return () => { active = false; };
    }, [playerName, playerId, season]);

    const persistence = data?.validation?.persistence;

    return (
        <div>
            <div className={titleClassName} style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                Exploit Guide{data ? ` (${seasonLabel(data.season)})` : ''}
                {data && (
                    <InfoTooltip label="How this works" title="Exploit Guide — real splits, significance-filtered">
                        {data.methodology}
                    </InfoTooltip>
                )}
                <SourceBadge source={data?._source} />
            </div>

            {loading && <Loader />}
            {!loading && error && <p className="player-modal-empty page-subtitle">{error}</p>}

            {!loading && data && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    <p className="page-subtitle" style={{ margin: 0, fontSize: '0.78rem' }}>
                        {data.n_tested} real splits tested · {data.n_significant} clear p &lt; 0.05 · about {data.expected_by_chance} would
                        clear it by chance alone. {Math.round(data.minutes).toLocaleString()} real minutes.
                        {persistence?.all && (
                            <> Across all players, findings like these point the same way the next season{' '}
                                {(persistence.all.same_direction_rate * 100).toFixed(0)}% of the time
                                (n = {persistence.all.n_followed_next_season.toLocaleString()}).</>
                        )}
                    </p>

                    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                        <FindingList title="Weaknesses" items={data.weaknesses} color={WEAKNESS}
                            emptyText="No split is significantly below the league average." />
                        <FindingList title="Strengths" items={data.strengths} color={STRENGTH}
                            emptyText="No split is significantly above the league average." />
                    </div>

                    {data.leverage && (
                        <p className="page-subtitle" style={{ margin: 0, fontSize: '0.75rem' }}>
                            <strong>High leverage (reference only):</strong> {fmtValue('leverage', data.leverage.value)} FG vs.{' '}
                            {fmtValue('leverage', data.leverage.baseline)} in other moments (n = {data.leverage.n} FGA, {fmtP(data.leverage.p)}).{' '}
                            {data.leverage_caveat}
                        </p>
                    )}

                    <div>
                        <div style={{ fontWeight: 700, fontSize: '0.8rem', marginBottom: 4 }}>
                            Toughest real defenders against him
                            <InfoTooltip label="About this list" title="Real tracked matchups">{data.best_defenders_note}</InfoTooltip>
                        </div>
                        {data.best_defenders.length === 0 ? (
                            <p className="page-subtitle" style={{ margin: 0, fontSize: '0.78rem' }}>
                                No defender reached the sample threshold this season (real matchup tracking starts 2017-18).
                            </p>
                        ) : (
                            <ul style={{ margin: 0, paddingLeft: '1.1rem', fontSize: '0.8rem' }}>
                                {data.best_defenders.map((d) => (
                                    <li key={d.player_id}>
                                        {d.player_name}: {d.fgm}/{d.fga} ({(d.fg_pct * 100).toFixed(1)}%) vs.{' '}
                                        {d.vs_everyone_else_fg_pct != null ? `${(d.vs_everyone_else_fg_pct * 100).toFixed(1)}%` : '—'} against everyone else
                                        <span className="page-subtitle" style={{ fontSize: '0.72rem' }}> · {d.partial_poss} partial poss. · {fmtP(d.p)}</span>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>

                    <div>
                        <div style={{ fontWeight: 700, fontSize: '0.8rem', marginBottom: 4 }}>What this report can't tell you</div>
                        <ul className="page-subtitle" style={{ margin: 0, paddingLeft: '1.1rem', fontSize: '0.75rem' }}>
                            {data.cant_tell.map((c) => <li key={c}>{c}</li>)}
                        </ul>
                    </div>
                </div>
            )}
        </div>
    );
}
