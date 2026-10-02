import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchCollegePipeline } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import PlayerName from './common/PlayerName';
import '../styles/college.css';
import TableExport from './common/TableExport';
import ChartExport from './common/ChartExport';
import { bySign, signed as signedNum } from '../utils/format';

const RUN_LABEL = {
    Champions: 'Won title', '2ND': 'Runner-up', F4: 'Final Four', E8: 'Elite 8', S16: 'Sweet 16',
    R32: 'Round of 32', R64: 'Round of 64', R68: 'First Four',
};

// Sign and colour follow the value as shown (no "+0.0" or "-0.0"); this page prints a hyphen minus.
const signed = (v, digits = 1) => signedNum(v, digits, '-');

const toneClass = (v, digits = 1) => bySign(v, digits, 'cb-pos', 'cb-neg');

function crossesZero(groups) {
    return groups.every((g) => g.ci_low <= 0 && g.ci_high >= 0);
}

/* Dot-and-whisker chart: group mean with its 95% bootstrap interval. */
function ForestPlot({ groups, ariaLabel }) {
    const svgRef = useRef(null);
    const W = 520, rowH = 36, padL = 170, padR = 20, padT = 10, padB = 34;
    const H = padT + groups.length * rowH + padB;
    const lo = Math.min(-2, ...groups.map((g) => g.ci_low));
    const hi = Math.max(2, ...groups.map((g) => g.ci_high));
    const x = (v) => padL + ((v - lo) / (hi - lo)) * (W - padL - padR);
    const ticks = [];
    for (let t = Math.ceil(lo); t <= Math.floor(hi); t += 1) ticks.push(t);

    return (
        <div>
            <ChartExport svgRef={svgRef} name="college pipeline by round" />
            <svg ref={svgRef} className="cb-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
            {ticks.map((t) => (
                <g key={t}>
                    <line className={t === 0 ? 'cb-zero' : 'cb-axis'} x1={x(t)} x2={x(t)} y1={padT} y2={H - padB} />
                    <text x={x(t)} y={H - padB + 16} textAnchor="middle">{t > 0 ? `+${t}` : t}</text>
                </g>
            ))}
            {groups.map((g, i) => {
                const cy = padT + i * rowH + rowH / 2;
                return (
                    <g key={g.label}>
                        <text className="cb-label" x={0} y={cy + 4}>{g.label}</text>
                        <text x={padL - 10} y={cy + 4} textAnchor="end">n={g.n}</text>
                        <line className="cb-whisker" x1={x(g.ci_low)} x2={x(g.ci_high)} y1={cy} y2={cy} />
                        <circle className="cb-dot" cx={x(g.ws4_vs_expected)} cy={cy} r={6}>
                            <title>{`${g.label}: ${signed(g.ws4_vs_expected, 2)} Win Shares vs. slot (95% range ${signed(g.ci_low, 2)} to ${signed(g.ci_high, 2)}), n=${g.n}, avg pick ${g.mean_pick}`}</title>
                        </circle>
                    </g>
                );
            })}
            <text x={(padL + W - padR) / 2} y={H - 2} textAnchor="middle">Win Shares vs. draft-slot expectation (first 4 NBA seasons)</text>
        </svg>
        </div>
    );
}

function StrengthScatter({ players, highlight }) {
    const svgRef = useRef(null);
    const W = 520, H = 330, padL = 44, padR = 12, padT = 12, padB = 40;
    const xs = players.map((p) => p.adj_margin);
    const ys = players.map((p) => p.ws4_vs_expected);
    const xMin = Math.floor(Math.min(...xs) / 5) * 5, xMax = Math.ceil(Math.max(...xs) / 5) * 5;
    const yMin = Math.floor(Math.min(...ys) / 10) * 10, yMax = Math.ceil(Math.max(...ys) / 10) * 10;
    const x = (v) => padL + ((v - xMin) / (xMax - xMin)) * (W - padL - padR);
    const y = (v) => padT + (1 - (v - yMin) / (yMax - yMin)) * (H - padT - padB);
    const xTicks = [];
    for (let t = xMin; t <= xMax; t += 10) xTicks.push(t);
    const yTicks = [];
    for (let t = yMin; t <= yMax; t += 10) yTicks.push(t);

    return (
        <div>
            <ChartExport svgRef={svgRef} name="college strength vs NBA win shares" />
            <svg ref={svgRef} className="cb-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Scatter of college team strength against NBA Win Shares versus draft-slot expectation, one dot per player">
            {yTicks.map((t) => (
                <g key={`y${t}`}>
                    <line className={t === 0 ? 'cb-zero' : 'cb-axis'} x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} />
                    <text x={padL - 6} y={y(t) + 4} textAnchor="end">{t > 0 ? `+${t}` : t}</text>
                </g>
            ))}
            {xTicks.map((t) => (
                <text key={`x${t}`} x={x(t)} y={H - padB + 16} textAnchor="middle">{t > 0 ? `+${t}` : t}</text>
            ))}
            {players.map((p) => (
                <circle
                    key={`${p.player_name}-${p.draft_year}`}
                    className={highlight && p.college_team === highlight ? 'cb-point cb-point--hi' : 'cb-point'}
                    cx={x(p.adj_margin)} cy={y(p.ws4_vs_expected)} r={highlight && p.college_team === highlight ? 5 : 3.2}
                >
                    <title>{`${p.player_name} (${p.college_team} ${p.college_season}, #${p.overall_pick}): ${signed(p.ws4_vs_expected)} WS vs. slot`}</title>
                </circle>
            ))}
            <text x={(padL + W - padR) / 2} y={H - 4} textAnchor="middle">College team strength (adj. margin per 100 possessions vs. average D1)</text>
        </svg>
        </div>
    );
}

export default function CollegePipelineSection() {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [school, setSchool] = useState('');
    const [query, setQuery] = useState('');
    const [sort, setSort] = useState('recent');

    useEffect(() => {
        let active = true;
        fetchCollegePipeline()
            .then((d) => { if (active) setData(d); })
            .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load the college pipeline.'); })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, []);

    const mature = useMemo(() => (data ? data.players.filter((p) => p.mature) : []), [data]);
    const schoolOptions = useMemo(
        () => (data ? [...new Set(data.players.map((p) => p.college_team))].sort() : []),
        [data]
    );

    const rows = useMemo(() => {
        if (!data) return [];
        const q = query.trim().toLowerCase();
        let list = data.players.filter((p) => (!school || p.college_team === school)
            && (!q || p.player_name.toLowerCase().includes(q)));
        if (sort === 'over') list = list.filter((p) => p.mature).sort((a, b) => b.ws4_vs_expected - a.ws4_vs_expected);
        if (sort === 'under') list = list.filter((p) => p.mature).sort((a, b) => a.ws4_vs_expected - b.ws4_vs_expected);
        if (sort === 'strength') list = [...list].sort((a, b) => b.adj_margin - a.adj_margin);
        return list.slice(0, 60);
    }, [data, school, query, sort]);

    if (loading) return <Loader />;
    if (error) return <div className="dashboard-card"><p className="error-message">{error}</p></div>;
    if (!data) return null;

    const { effect, counts, no_college: nc } = data;
    const noEffect = effect.ci_low <= 0 && effect.ci_high >= 0;

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    College → NBA Pipeline
                    <InfoTooltip label="How this works" title="Draft slot sets the bar; did college team strength move it?">
                        Every NBA pick {data.players.at(-1)?.draft_year}–{data.players[0]?.draft_year} who came from
                        a US college is linked to that college team&apos;s season in Bart Torvik&apos;s ratings. The NBA
                        outcome is Basketball-Reference Win Shares in the first four seasons after the draft. The bar
                        for each slot is {data.expected_curve.formula} (a = {data.expected_curve.a}, b = {data.expected_curve.b}),
                        fitted on all {data.expected_curve.n} picks through {data.mature_class}. Only classes through {data.mature_class} have
                        four NBA seasons, so later classes are listed without a verdict. Intervals are 95% bootstrap ranges.
                    </InfoTooltip>
                    <SourceBadge source={data._source} />
                </h3>
                <p className="cb-verdict">
                    {noEffect ? 'Once you know the pick, the college team barely matters' : 'College team strength moves the needle'}
                </p>
                <p className="cb-lede">
                    A college team 10 points per 100 possessions better goes with {signed(effect.ws4_per_10_margin, 2)} NBA
                    Win Shares over four seasons, after accounting for draft slot (95% range {signed(effect.ci_low, 2)} to{' '}
                    {signed(effect.ci_high, 2)}{noEffect ? ', which includes zero' : ''}). Correlation r = {effect.pearson_r}.
                    {noEffect && ' NBA teams already price in how good a prospect’s college team was.'}
                </p>
                <div className="cb-kpis">
                    <div className="cb-kpi"><b>{counts.analysed}</b><span>players judged (classes through {data.mature_class})</span></div>
                    <div className="cb-kpi"><b>{counts.linked}</b><span>of {counts.with_college} college picks linked</span></div>
                    <div className="cb-kpi"><b>r = {effect.pearson_r}</b><span>team strength vs. value over slot</span></div>
                    <div className="cb-kpi"><b className={toneClass(nc.ws4_vs_expected)}>{signed(nc.ws4_vs_expected, 1)}</b><span>WS vs. slot, picks with no US college</span></div>
                </div>
            </div>

            <div className="cb-grid">
                <div className="dashboard-card">
                    <h3 className="section-heading" style={{ marginTop: 0 }}>By college team rank</h3>
                    <ForestPlot groups={data.tiers} ariaLabel="Win Shares versus draft slot by college team national rank, with 95% intervals" />
                    <p className="cb-note">
                        Rank is the team&apos;s national Torvik rank that season.
                        {crossesZero(data.tiers) ? ' Every interval crosses zero.' : ' At least one interval clears zero.'}
                    </p>
                </div>
                <div className="dashboard-card">
                    <h3 className="section-heading" style={{ marginTop: 0 }}>By March Madness run</h3>
                    <ForestPlot groups={data.runs} ariaLabel="Win Shares versus draft slot by how far the college team went in the NCAA tournament, with 95% intervals" />
                    <p className="cb-note">
                        {crossesZero(data.runs)
                            ? 'Every interval crosses zero: a deep tournament run doesn’t mark a player as under- or over-drafted either.'
                            : 'At least one interval clears zero.'}
                    </p>
                </div>
            </div>

            <div className="cb-grid">
                <div className="dashboard-card">
                    <h3 className="section-heading" style={{ marginTop: 0 }}>Every player</h3>
                    <StrengthScatter players={mature} highlight={school} />
                    <p className="cb-note">Pick a school below to highlight its players.</p>
                </div>
                <div className="dashboard-card">
                    <h3 className="section-heading" style={{ marginTop: 0 }}>Where no-college picks fit</h3>
                    <p className="cb-lede">
                        The {nc.n} picks through {data.mature_class} with no US college (international, G League, straight
                        from high school) come in at <strong className={toneClass(nc.ws4_vs_expected, 2)}>{signed(nc.ws4_vs_expected, 2)}</strong> Win
                        Shares vs. their slot (95% range {signed(nc.ci_low, 2)} to {signed(nc.ci_high, 2)}).
                    </p>
                    <p className="cb-lede" style={{ marginTop: '0.75rem' }}>
                        Most of that gap is the draft-and-stash effect: {Math.round(nc.no_nba_games_share * 100)}% of them played
                        no NBA games in their first four seasons (often staying overseas), against {Math.round(nc.college_no_nba_games_share * 100)}% of
                        college picks. Among players who did play, it&apos;s {signed(nc.played_ws4_vs_expected, 2)} (n={nc.played_n}) vs.{' '}
                        {signed(nc.college_played_ws4_vs_expected, 2)} for college picks (n={nc.college_played_n}).
                    </p>
                </div>
            </div>

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <h3 className="section-heading" style={{ marginTop: 0 }}>Schools</h3>
                <p className="cb-note" style={{ marginTop: 0, marginBottom: '0.75rem' }}>
                    Schools with at least {data.min_school_picks} picks through {data.mature_class}. With {data.schools.length} schools compared,
                    a couple will clear zero by chance alone, so read the ranges, not just the order.
                </p>
                <TableExport />
                <div className="hb-table-wrapper table-wrapper cb-scroll">
                    <table className="data-table">
                        <thead>
                            <tr><th>School</th><th>Picks</th><th>Avg pick</th><th>Total WS (4 yrs)</th><th>WS vs. slot, per player</th><th>95% range</th><th>Best NBA player</th></tr>
                        </thead>
                        <tbody>
                            {data.schools.map((s) => (
                                <tr key={s.label}>
                                    <td>
                                        <button type="button" className="back-btn" style={{ padding: '4px 10px' }} onClick={() => setSchool(s.label === school ? '' : s.label)}>
                                            {s.label}
                                        </button>
                                    </td>
                                    <td className="cb-num">{s.n}</td>
                                    <td className="cb-num">{s.mean_pick}</td>
                                    <td className="cb-num">{s.total_ws4}</td>
                                    <td className={`cb-num ${toneClass(s.ws4_vs_expected, 2)}`}>{signed(s.ws4_vs_expected, 2)}</td>
                                    <td className="cb-num">{signed(s.ci_low, 1)} to {signed(s.ci_high, 1)}</td>
                                    <td>{s.best}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <h3 className="section-heading" style={{ marginTop: 0 }}>Players</h3>
                <div className="cb-toolbar">
                    <input className="search-input" type="search" placeholder="Search a player" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search a player" />
                    <select value={school} onChange={(e) => setSchool(e.target.value)} aria-label="Filter by school">
                        <option value="">All schools</option>
                        {schoolOptions.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                    <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort players">
                        <option value="recent">Newest draft first</option>
                        <option value="over">Most above slot</option>
                        <option value="under">Most below slot</option>
                        <option value="strength">Strongest college team</option>
                    </select>
                </div>
                <TableExport />
                <div className="hb-table-wrapper table-wrapper">
                    <table className="data-table">
                        <thead>
                            <tr><th>Player</th><th>Drafted</th><th>College team</th><th>Team strength</th><th>WS (first 4 yrs)</th><th>Slot bar</th><th>vs. slot</th></tr>
                        </thead>
                        <tbody>
                            {rows.map((p) => (
                                <tr key={`${p.player_name}-${p.draft_year}`}>
                                    <td><PlayerName playerId={p.player_id} name={p.player_name} /></td>
                                    <td className="cb-num">{p.draft_year} · #{p.overall_pick}<span className="cb-sub">{p.nba_team}</span></td>
                                    <td>
                                        {p.college_team} <span className="cb-num">{p.college_season - 1}-{String(p.college_season).slice(-2)}</span>
                                        <span className="cb-sub">{p.postseason ? `${RUN_LABEL[p.postseason] || p.postseason}${p.seed ? `, ${p.seed} seed` : ''}` : 'No NCAA tournament'}</span>
                                    </td>
                                    <td className="cb-num">#{p.barthag_rank} of {p.n_teams}<span className="cb-sub">{signed(p.adj_margin)} per 100</span></td>
                                    <td className="cb-num">{p.ws4.toFixed(1)}<span className="cb-sub">{p.seasons4} NBA season{p.seasons4 === 1 ? '' : 's'}</span></td>
                                    <td className="cb-num">{p.expected_ws4.toFixed(1)}</td>
                                    <td className={`cb-num ${toneClass(p.ws4_vs_expected)}`}>
                                        {p.mature ? signed(p.ws4_vs_expected) : <span className="cb-sub">too early</span>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {rows.length === 60 && <p className="cb-note">Showing the first 60; search or filter by school to narrow it down.</p>}
            </div>
        </div>
    );
}
