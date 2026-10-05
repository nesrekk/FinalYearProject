import React, { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { fetchCurrentMeta, fetchTeamComparisonExtra } from '../../services/api';
import TeamLogo from '../common/TeamLogo';
import TeamLink from '../common/TeamLink';
import PlayerName from '../common/PlayerName';
import BigStat from '../ui/BigStat';
import InfoTooltip from '../common/InfoTooltip';
import SourceBadge from '../common/SourceBadge';
import { TEAM_COLORS } from '../../utils/teamAssets';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';
import TableExport from '../common/TableExport';

const statLabels = [
    { key: 'ppg', label: 'Points Per Game', max: 130 },
    { key: 'rpg', label: 'Rebounds Per Game', max: 55 },
    { key: 'apg', label: 'Assists Per Game', max: 35 },
    { key: 'fgPct', label: 'FG %', max: 55 },
    { key: 'threePct', label: '3PT %', max: 45 },
    { key: 'ftPct', label: 'FT %', max: 90 },
    { key: 'spg', label: 'Steals Per Game', max: 12 },
    { key: 'bpg', label: 'Blocks Per Game', max: 8 },
];

const advancedStatLabels = [
    { key: 'offRating', label: 'Offensive Rating', max: 125, digits: 1 },
    { key: 'defRating', label: 'Defensive Rating', max: 125, digits: 1 },
    { key: 'netRating', label: 'Net Rating', max: 15, min: -15, digits: 1 },
    { key: 'tov', label: 'Turnovers Per Game', max: 18, digits: 1 },
];

function CompRow({ label, valA, valB, max, min = 0, abbrA, abbrB, delay, preset, higherIsBetter = true }) {
    const range = max - min;
    const pctA = ((valA - min) / range) * 100;
    const pctB = ((valB - min) / range) * 100;
    const better = higherIsBetter ? valA > valB : valA < valB;
    const worse = higherIsBetter ? valB > valA : valB < valA;
    const winner = valA === valB ? null : better ? 'A' : worse ? 'B' : null;

    return (
        <div className="comparison-row">
            <span className={`comp-value comp-value--left ${winner === 'A' ? 'comp-value--winner' : ''}`}>{valA}</span>
            <div className="comp-bar-group">
                <div className="comp-bar comp-bar--left">
                    <motion.div
                        className="comp-bar-fill comp-bar-fill--a"
                        style={{ background: TEAM_COLORS[abbrA] || 'var(--accent)' }}
                        initial={{ width: 0 }}
                        animate={{ width: `${Math.max(0, Math.min(100, pctA))}%` }}
                        transition={{ ...preset.tableTransition, delay }}
                    />
                </div>
                <span className="comp-label">{label}</span>
                <div className="comp-bar comp-bar--right">
                    <motion.div
                        className="comp-bar-fill comp-bar-fill--b"
                        style={{ background: TEAM_COLORS[abbrB] || 'var(--brand)' }}
                        initial={{ width: 0 }}
                        animate={{ width: `${Math.max(0, Math.min(100, pctB))}%` }}
                        transition={{ ...preset.tableTransition, delay }}
                    />
                </div>
            </div>
            <span className={`comp-value comp-value--right ${winner === 'B' ? 'comp-value--winner' : ''}`}>{valB}</span>
        </div>
    );
}

function FormStreak({ form }) {
    if (!form) return <span className="page-subtitle">No recent-game data.</span>;
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 700 }}>{form.wins}-{form.losses}</span>
            <span className="page-subtitle" style={{ margin: 0 }}>last {form.games_considered}</span>
            <div style={{ display: 'flex', gap: 3 }}>
                {[...form.results].reverse().map((g, i) => (
                    <span
                        key={i}
                        title={`${g.win ? 'W' : 'L'} vs ${g.opponent} (${g.point_diff >= 0 ? '+' : ''}${g.point_diff}) — ${g.date}`}
                        style={{
                            width: 18, height: 18, borderRadius: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: '0.6rem', fontWeight: 700, color: '#0d0d0d', border: '1.5px solid var(--line)',
                            background: g.win ? '#34d399' : '#f87171',
                        }}
                    >
                        {g.win ? 'W' : 'L'}
                    </span>
                ))}
            </div>
        </div>
    );
}

function RosterTable({ roster }) {
    if (!roster?.length) return null;
    return (
        <>
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table">
                    <thead>
                        <tr><th>Player</th><th>GP</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th></tr>
                    </thead>
                    <tbody>
                        {roster.map((p) => (
                            <tr key={p.player_id}>
                                <td>
                                    <PlayerName playerId={p.player_id} name={p.player_name} size={24} />
                                </td>
                                <td>{p.gp}</td>
                                <td>{p.min?.toFixed(1)}</td>
                                <td>{p.pts?.toFixed(1)}</td>
                                <td>{p.reb?.toFixed(1)}</td>
                                <td>{p.ast?.toFixed(1)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

export default function TeamComparison() {
    const [teamStats, setTeamStats] = useState({});
    const [teamKeys, setTeamKeys] = useState([]);
    const [loadError, setLoadError] = useState(false);
    const [teamA, setTeamA] = useState('LAL');
    const [teamB, setTeamB] = useState('BOS');
    const [loading, setLoading] = useState(true);
    const [standings, setStandings] = useState(null);
    const [extra, setExtra] = useState(null);
    const [extraError, setExtraError] = useState('');
    const [extraLoading, setExtraLoading] = useState(false);

    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);

    useEffect(() => {
        let active = true;
        async function loadCurrent() {
            setLoading(true);
            try {
                const meta = await fetchCurrentMeta();
                const incoming = meta?.team_stats;
                if (active && incoming && Object.keys(incoming).length > 0) {
                    const keys = Object.keys(incoming).sort();
                    setTeamStats(incoming);
                    setTeamKeys(keys);
                    // Functional updates read the live pick instead of this
                    // mount-time closure, so the effect stays mount-only (no
                    // refetch of current meta on every team change) without
                    // needing teamA/teamB as dependencies.
                    setTeamA((cur) => (incoming[cur] ? cur : keys[0]));
                    setTeamB((cur) => (incoming[cur] ? cur : keys[1] || keys[0]));
                }
                if (active && meta?.standings) {
                    const combined = [...(meta.standings.eastern || []), ...(meta.standings.western || [])];
                    const byAbbr = {};
                    combined.forEach((t) => { if (t.abbr) byAbbr[t.abbr] = t; });
                    setStandings(byAbbr);
                }
                if (active && !(incoming && Object.keys(incoming).length > 0)) setLoadError(true);
            } catch {
                if (active) setLoadError(true);
            } finally {
                if (active) setLoading(false);
            }
        }
        loadCurrent();
        return () => {
            active = false;
        };
    }, []);

    const statsA = teamStats[teamA];
    const statsB = teamStats[teamB];
    const canRender = useMemo(() => Boolean(statsA && statsB), [statsA, statsB]);
    const matchupKey = `${teamA}-${teamB}-${isAdvanced}`;

    // Advanced stats, roster, head-to-head, and recent form — a separate
    // real-data endpoint (/teams/compare) from the basic per-game stats
    // above, so it can be added/refreshed independently.
    useEffect(() => {
        if (!teamA || !teamB) return;
        let active = true;
        setExtraLoading(true);
        setExtraError('');
        fetchTeamComparisonExtra(teamA, teamB)
            .then((data) => { if (active) setExtra(data); })
            .catch((e) => {
                if (!active) return;
                setExtra(null);
                setExtraError(e?.response?.data?.detail || 'Could not load extended team comparison.');
            })
            .finally(() => { if (active) setExtraLoading(false); });
        return () => { active = false; };
    }, [teamA, teamB]);

    return (
        <div className="page page-teams fade-in">
            {loading && <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>Loading current season team stats...</p>}
            {!loading && loadError && <p className="empty-message">Current team stats couldn&apos;t load right now.</p>}
            {/* Team Selectors */}
            <div className="comparison-selectors">
                <div className="selector-group">
                    <select
                        className="team-select"
                        value={teamA}
                        onChange={(e) => setTeamA(e.target.value)}
                    >
                        {teamKeys.map((t) => (
                            <option key={t} value={t}>{teamStats[t].name}</option>
                        ))}
                    </select>
                </div>

                <span className="vs-divider">VS</span>

                <div className="selector-group">
                    <select
                        className="team-select"
                        value={teamB}
                        onChange={(e) => setTeamB(e.target.value)}
                    >
                        {teamKeys.map((t) => (
                            <option key={t} value={t}>{teamStats[t].name}</option>
                        ))}
                    </select>
                </div>
            </div>

            {canRender && (
                <div className="team-compare-hero">
                    <div
                        className="team-compare-hero-side"
                        style={{ '--team-wash': TEAM_COLORS[statsA.abbr] || 'var(--brand)' }}
                    >
                        <TeamLink abbr={statsA.abbr}>
                            <TeamLogo abbreviation={statsA.abbr} size={40} />
                            <span className="team-compare-hero-name">{statsA.name}</span>
                        </TeamLink>
                        {standings?.[teamA] && (
                            <BigStat
                                label={`${standings[teamA].pct} win pct`}
                                value={`${standings[teamA].w}-${standings[teamA].l}`}
                                className="team-compare-hero-stat"
                            />
                        )}
                    </div>
                    <span className="vs-divider">VS</span>
                    <div
                        className="team-compare-hero-side team-compare-hero-side--right"
                        style={{ '--team-wash': TEAM_COLORS[statsB.abbr] || 'var(--accent)' }}
                    >
                        {standings?.[teamB] && (
                            <BigStat
                                label={`${standings[teamB].pct} win pct`}
                                value={`${standings[teamB].w}-${standings[teamB].l}`}
                                className="team-compare-hero-stat"
                            />
                        )}
                        <TeamLink abbr={statsB.abbr}>
                            <span className="team-compare-hero-name">{statsB.name}</span>
                            <TeamLogo abbreviation={statsB.abbr} size={40} />
                        </TeamLink>
                    </div>
                </div>
            )}

            {/* Comparison Bars */}
            {canRender && (
                <div className="comparison-bars" key={matchupKey}>
                    {statLabels.map(({ key, label, max }, i) => {
                        const valA = statsA[key];
                        const valB = statsB[key];
                        const pctA = (valA / max) * 100;
                        const pctB = (valB / max) * 100;
                        const winner = valA > valB ? 'A' : valB > valA ? 'B' : null;
                        const delay = isAdvanced ? i * 0.04 : 0;

                        return (
                            <div key={key} className="comparison-row">
                                <span className={`comp-value comp-value--left ${winner === 'A' ? 'comp-value--winner' : ''}`}>
                                    {valA}
                                </span>
                                <div className="comp-bar-group">
                                    <div className="comp-bar comp-bar--left">
                                        <motion.div
                                            className="comp-bar-fill comp-bar-fill--a"
                                            style={{ background: TEAM_COLORS[statsA.abbr] || 'var(--accent)' }}
                                            initial={{ width: 0 }}
                                            animate={{ width: `${pctA}%` }}
                                            transition={{ ...preset.tableTransition, delay }}
                                        />
                                    </div>
                                    <span className="comp-label">{label}</span>
                                    <div className="comp-bar comp-bar--right">
                                        <motion.div
                                            className="comp-bar-fill comp-bar-fill--b"
                                            style={{ background: TEAM_COLORS[statsB.abbr] || 'var(--brand)' }}
                                            initial={{ width: 0 }}
                                            animate={{ width: `${pctB}%` }}
                                            transition={{ ...preset.tableTransition, delay }}
                                        />
                                    </div>
                                </div>
                                <span className={`comp-value comp-value--right ${winner === 'B' ? 'comp-value--winner' : ''}`}>
                                    {valB}
                                </span>
                            </div>
                        );
                    })}
                </div>
            )}

            {extraError && <p className="error-message" style={{ marginTop: '1rem' }}>{extraError}</p>}

            {canRender && extra && (
                <>
                    <div className="dashboard-card" style={{ marginTop: '1.5rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Advanced Stats
                            <InfoTooltip label="How this works" title="The team page's numbers">
                                Offensive/Defensive/Net Rating are Basketball-Reference&apos;s team ratings (points per
                                100 possessions), the same numbers as each team&apos;s page. Turnovers per game are the
                                team&apos;s from NBA.com&apos;s box score, team turnovers included (from 2020-21; earlier
                                seasons sum the players&apos; season rows over the team&apos;s games).
                            </InfoTooltip>
                            <SourceBadge source={extra._source} />
                        </h3>
                        <div className="comparison-bars" key={`adv-${matchupKey}`}>
                            {advancedStatLabels.map(({ key, label, max, min, digits }, i) => (
                                <CompRow
                                    key={key}
                                    label={label}
                                    valA={extra.team_a.advanced_stats[key]?.toFixed(digits)}
                                    valB={extra.team_b.advanced_stats[key]?.toFixed(digits)}
                                    max={max}
                                    min={min ?? 0}
                                    abbrA={teamA}
                                    abbrB={teamB}
                                    delay={isAdvanced ? i * 0.04 : 0}
                                    preset={preset}
                                    higherIsBetter={key !== 'defRating' && key !== 'tov'}
                                />
                            ))}
                        </div>
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Head-to-Head
                            <InfoTooltip label="How this works" title="Every real meeting on file">
                                Real results from every game these two teams have actually played against each other
                                (seasons 2010 onward, this project's real schedule-data coverage) — not a prediction.
                            </InfoTooltip>
                        </h3>
                        {extra.head_to_head.games_played === 0 ? (
                            <p className="page-subtitle">These teams haven't played each other in real games on file.</p>
                        ) : (
                            <>
                                <p style={{ fontWeight: 600, marginBottom: '0.75rem' }}>
                                    {extra.head_to_head.games_played} real meetings &middot; {statsA.abbr} {extra.head_to_head.team_a_wins} — {extra.head_to_head.team_b_wins} {statsB.abbr}
                                </p>
                                <TableExport />
                                <div className="table-wrapper">
                                    <table className="data-table">
                                        <thead>
                                            <tr><th>Date</th><th>Winner</th><th>Margin</th></tr>
                                        </thead>
                                        <tbody>
                                            {extra.head_to_head.recent_meetings.map((g) => (
                                                <tr key={g.date}>
                                                    <td>{g.date}</td>
                                                    <td>{g.team_a_won ? statsA.abbr : statsB.abbr}</td>
                                                    <td>{Math.abs(g.team_a_point_diff).toFixed(0)} pts</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </>
                        )}
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Recent Form
                            <InfoTooltip label="How this works" title="Real last-10 results">
                                Each team's real record over its last 10 real games on file, most recent first.
                            </InfoTooltip>
                        </h3>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1rem' }}>
                            <div>
                                <div className="entity-row" style={{ marginBottom: '0.5rem' }}>
                                    <TeamLink abbr={statsA.abbr}><TeamLogo abbreviation={statsA.abbr} size={20} /> <strong>{statsA.name}</strong></TeamLink>
                                </div>
                                <FormStreak form={extra.team_a.recent_form} />
                            </div>
                            <div>
                                <div className="entity-row" style={{ marginBottom: '0.5rem' }}>
                                    <TeamLink abbr={statsB.abbr}><TeamLogo abbreviation={statsB.abbr} size={20} /> <strong>{statsB.name}</strong></TeamLink>
                                </div>
                                <FormStreak form={extra.team_b.recent_form} />
                            </div>
                        </div>
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Rosters
                            <InfoTooltip label="How this works" title="Real current-season stat leaders">
                                Each team's top 8 real players this season, ranked by real points per game.
                            </InfoTooltip>
                        </h3>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.5rem' }}>
                            <div>
                                <div className="entity-row" style={{ marginBottom: '0.5rem' }}>
                                    <TeamLink abbr={statsA.abbr}><TeamLogo abbreviation={statsA.abbr} size={20} /> <strong>{statsA.name}</strong></TeamLink>
                                </div>
                                <RosterTable roster={extra.team_a.roster} />
                            </div>
                            <div>
                                <div className="entity-row" style={{ marginBottom: '0.5rem' }}>
                                    <TeamLink abbr={statsB.abbr}><TeamLogo abbreviation={statsB.abbr} size={20} /> <strong>{statsB.name}</strong></TeamLink>
                                </div>
                                <RosterTable roster={extra.team_b.roster} />
                            </div>
                        </div>
                    </div>
                </>
            )}
            {canRender && extraLoading && !extra && (
                <p className="page-subtitle" style={{ marginTop: '1rem' }}>Loading advanced stats, roster, and head-to-head...</p>
            )}
        </div>
    );
}
