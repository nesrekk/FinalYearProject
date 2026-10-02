import React, { useEffect, useMemo, useState } from 'react';
import { fetchPossessionLeague, fetchPossessionOptions, fetchPossessionTeam } from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import TeamLogo from '../common/TeamLogo';
import { PppDotChart, TransitionCurve } from '../common/PossessionCharts';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { signed } from '../../utils/format';
import '../../styles/playfinder.css';
import '../../styles/possessions.css';

// Possession Explorer (?page=possessions): points per possession by how the
// possession began (after a make, a defensive rebound, a steal, ...),
// transition, second chances and points off steals, for the league and
// every team, offence and defence, 2020-21 on (GET /possessions/*, from
// scripts/build_possessions.py). Nothing here is a model: counts and rates,
// with a 95% interval and a "how much of the spread is more than chance"
// check per start type.
// Inputs in the link: team, season, start (the start type ranked across
// teams), v (off|def), sort, dir.

const f3 = (v) => (v == null ? '—' : v.toFixed(3));
const f1 = (v) => (v == null ? '—' : v.toFixed(1));
const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const signed3 = (v) => signed(v, 3);
const ordinal = (n) => {
    if (n == null) return '';
    const v = n % 100;
    return `${n}${(v >= 11 && v <= 13) ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] ?? 'th')}`;
};
const TABLE_STARTS = ['made_fg', 'dreb', 'steal', 'dead_tov'];

const cell = (t, key, side) => t.by_start[key]?.[side] ?? null;

// Team-table columns. `get` reads a team row for the side; `better` says which way is good for that side.
function columns(side, short) {
    const off = side === 'off';
    return [
        { key: 'games', label: 'G', title: 'Games counted (those whose possessions add up to the final score and box score)', get: (t) => t.totals.games, fmt: (v) => v },
        { key: 'pace', label: 'Poss/G', title: off ? 'Possessions a game, counted from the play-by-play' : 'Opponent possessions a game', get: (t) => cell(t, 'all', side).poss / t.totals.games, fmt: f1 },
        { key: 'all', label: off ? 'PPP' : 'PPP allowed', title: 'Points per possession, every possession', get: (t) => cell(t, 'all', side)?.ppp, fmt: f3, ppp: 'all' },
        ...TABLE_STARTS.map((k) => ({ key: k, label: short[k], title: `Points per possession ${off ? '' : 'allowed '}${short[k].toLowerCase()}`, get: (t) => cell(t, k, side)?.ppp, fmt: f3, ppp: k })),
        { key: 'trans_share', label: 'Transition', title: 'Share of timed possessions (after makes, made free throws and rebounds) with the first shot within 7 s', get: (t) => cell(t, 'all', side)?.trans_share, fmt: (v) => pct(v) },
        { key: 'trans_ppp', label: 'Trans. PPP', title: 'Points per transition possession', get: (t) => cell(t, 'all', side)?.trans_ppp, fmt: f3 },
        { key: 'second', label: '2nd-chance/G', title: off ? 'Points after an offensive rebound, a game' : 'Opponents’ points after an offensive rebound, a game', get: (t) => (off ? t.totals.second_chance_pg : t.totals.d_second_chance_pg), fmt: f1 },
        { key: 'steal_pts', label: off ? 'Pts off steals/G' : 'Pts after own steals lost/G', title: off ? 'Points on possessions that began with their steal, a game' : 'Opponents’ points on possessions that began with this team’s live-ball turnover, a game', get: (t) => (off ? t.totals.steal_pts_pg : t.totals.d_steal_pts_pg), fmt: f1 },
    ];
}

function formFromParams(p, o) {
    const seasons = o.seasons.map((s) => s.season);
    return {
        season: parseParam.int(p, 'season', { min: seasons[0], max: seasons[seasons.length - 1] }) ?? seasons[seasons.length - 1],
        team: parseParam.oneOf(p, 'team', o.teams),
        start: parseParam.oneOf(p, 'start', ['all', ...o.start_types.filter((s) => s.main).map((s) => s.key)]) ?? 'steal',
        side: parseParam.oneOf(p, 'v', ['off', 'def']) ?? 'off',
        sort: parseParam.str(p, 'sort'),
        dir: parseParam.oneOf(p, 'dir', ['asc', 'desc']),
    };
}

function Stat({ k, v, sub, title }) {
    return (
        <div className="px-stat" title={title}>
            <div className="px-stat-k">{k}</div>
            <div className="px-stat-v">{v}</div>
            {sub && <div className="px-stat-sub">{sub}</div>}
        </div>
    );
}

function SignalTable({ signal, side, labels }) {
    const rows = signal.filter((s) => s.side === side);
    return (
        <>
            <TableExport name={`possessions team signal ${side}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table px-signal">
                    <thead>
                        <tr>
                            <th>Possessions</th>
                            <th className="lb-num" title="Standard deviation of the 30 teams' points per possession, average over the six seasons">Spread between teams</th>
                            <th className="lb-num" title="The spread chance alone would give: the average standard error of a team's value">From chance alone</th>
                            <th className="lb-num" title="1 − chance variance / observed variance, averaged over the six seasons">Share beyond chance</th>
                            <th className="lb-num" title="Correlation of a team's value with its own next season, averaged over the five pairs of seasons">Repeats next season (r)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((s) => (
                            <tr key={s.start_type}>
                                <td>{labels[s.start_type]}</td>
                                <td className="lb-num">±{f3(s.team_sd)}</td>
                                <td className="lb-num">±{f3(s.noise_sd)}</td>
                                <td className="lb-num">{pct(s.real_share, 0)}</td>
                                <td className="lb-num" title={s.yty.map((y) => `${y.from - 1}-${String(y.from).slice(-2)} → ${y.to - 1}-${String(y.to).slice(-2)}: ${y.r.toFixed(2)}`).join('\n')}>
                                    {s.yty_mean == null ? '—' : s.yty_mean.toFixed(2)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

function TeamTable({ data, side, sort, dir, onSort, onPick, short, selected }) {
    const cols = columns(side, short);
    const league = Object.fromEntries(data.league.map((r) => [r.start_type, r.ppp]));
    const col = cols.find((c) => c.key === sort) ?? cols.find((c) => c.key === 'all');
    const sign = (dir ?? (side === 'def' && col.ppp ? 'asc' : 'desc')) === 'asc' ? 1 : -1;
    const rows = [...data.teams].sort((a, b) => {
        const va = col.get(a);
        const vb = col.get(b);
        if (va == null) return 1;
        if (vb == null) return -1;
        return sign * (va - vb) || a.team.localeCompare(b.team);
    });
    const tone = (t, c) => {
        if (!c.ppp) return '';
        const x = cell(t, c.ppp, side);
        const ref = league[c.ppp];
        if (!x || x.lo == null || ref == null) return '';
        const good = side === 'off' ? x.lo > ref : x.hi < ref;
        const bad = side === 'off' ? x.hi < ref : x.lo > ref;
        return good ? ' oo-pos' : bad ? ' oo-neg' : '';
    };
    return (
        <>
            <TableExport name={`possessions teams ${data.label} ${side === 'off' ? 'offence' : 'defence'}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table px-table">
                    <thead>
                        <tr>
                            <th>Team</th>
                            {cols.map((c) => {
                                const active = c.key === col.key;
                                return (
                                    <th key={c.key} className="lb-num" title={c.title} aria-sort={active ? (sign > 0 ? 'ascending' : 'descending') : 'none'}>
                                        <button type="button" className="oo-sort" onClick={() => onSort(c.key)}>
                                            {c.label}{active && <span aria-hidden="true">{sign > 0 ? ' ▲' : ' ▼'}</span>}
                                        </button>
                                    </th>
                                );
                            })}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((t) => (
                            <tr key={t.team} className={t.team === selected ? 'px-row--sel' : undefined}>
                                <td className="oo-team">
                                    <button type="button" className="px-team-btn" onClick={() => onPick(t.team)} title={`Open ${t.team} in the explorer`}>
                                        <TeamLogo abbreviation={t.team} size={18} /> <span>{t.team}</span>
                                    </button>
                                </td>
                                {cols.map((c) => {
                                    const x = c.ppp ? cell(t, c.ppp, side) : null;
                                    return (
                                        <td key={c.key} className={`lb-num${tone(t, c)}`}
                                            title={x ? `${x.poss.toLocaleString()} possessions; 95% interval ${f3(x.lo)} to ${f3(x.hi)}; rank ${x.rank} of ${data.teams.length}` : undefined}>
                                            {c.fmt(c.get(t))}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                        <tr className="px-row--league">
                            <td>League</td>
                            <td className="lb-num">—</td>
                            <td className="lb-num">{f1(data.league[0].poss / data.league[0].games / 2)}</td>
                            <td className="lb-num">{f3(league.all)}</td>
                            {TABLE_STARTS.map((k) => <td key={k} className="lb-num">{f3(league[k])}</td>)}
                            <td className="lb-num">{pct(data.league[0].trans_share)}</td>
                            <td className="lb-num">{f3(data.league[0].trans_ppp)}</td>
                            <td className="lb-num">{f1(data.league[0].second_chance_pts / data.league[0].games / 2)}</td>
                            <td className="lb-num">{f1(data.league.find((r) => r.start_type === 'steal').pts / data.league[0].games / 2)}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle px-foot">
                Click a column to sort, a team to open it. Green or red: the team&apos;s 95% interval clears the league&apos;s
                value for that kind of possession ({side === 'off' ? 'better or worse offence' : 'better or worse defence: fewer or more points allowed'}).
                Hover a value for its possessions, interval and rank.
            </p>
        </>
    );
}

function TeamHistory({ team, side, short }) {
    const cols = columns(side, short).filter((c) => c.key !== 'games');
    return (
        <>
            <TableExport name={`possessions ${team.team} by season ${side === 'off' ? 'offence' : 'defence'}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table px-table">
                    <thead>
                        <tr><th>Season</th><th className="lb-num">G</th>{cols.map((c) => <th key={c.key} className="lb-num" title={c.title}>{c.label}</th>)}</tr>
                    </thead>
                    <tbody>
                        {[...team.seasons].reverse().map((s) => (
                            <tr key={s.season}>
                                <td><TeamLink abbr={team.team} season={s.season}>{s.label}</TeamLink></td>
                                <td className="lb-num">{s.totals.games}</td>
                                {cols.map((c) => {
                                    const x = c.ppp ? cell(s, c.ppp, side) : null;
                                    return (
                                        <td key={c.key} className="lb-num" title={x ? `${x.poss.toLocaleString()} possessions; 95% interval ${f3(x.lo)} to ${f3(x.hi)}` : undefined}>
                                            {c.fmt(c.get(s))}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

function LeagueTrend({ options }) {
    const keys = ['all', ...options.start_types.filter((s) => s.main).map((s) => s.key)];
    const labels = { all: 'Every possession', ...Object.fromEntries(options.start_types.map((s) => [s.key, s.label])) };
    return (
        <>
            <TableExport name="possessions league by season" />
            <div className="table-wrapper">
                <table className="data-table lb-table px-table">
                    <thead>
                        <tr>
                            <th>Points per possession</th>
                            {options.trend.map((t) => <th key={t.season} className="lb-num">{t.label}</th>)}
                            <th className="lb-num" title="Every season pooled">All six</th>
                            <th className="lb-num" title="Share of every possession, all six seasons">Share</th>
                        </tr>
                    </thead>
                    <tbody>
                        {keys.map((k) => (
                            <tr key={k}>
                                <td>{labels[k]}</td>
                                {options.trend.map((t) => {
                                    const r = t.rows.find((x) => x.start_type === k);
                                    return <td key={t.season} className="lb-num" title={r ? `${r.poss.toLocaleString()} possessions` : undefined}>{f3(r?.ppp)}</td>;
                                })}
                                <td className="lb-num"><strong>{f3(options.pooled[k]?.ppp)}</strong></td>
                                <td className="lb-num">{pct(options.pooled[k]?.poss / options.pooled.all.poss)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

export default function PossessionExplorer() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [league, setLeague] = useState(null); // { season, data } | { season, error }
    const [teamData, setTeamData] = useState(null); // { team, data } | { team, error }

    useEffect(() => {
        fetchPossessionOptions()
            .then((o) => { setOptions(o); setForm(formFromParams(params, o)); })
            .catch(() => setOptionsError('The Possession Explorer couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    useUrlSync(form && {
        team: form.team, season: form.season === options.seasons[options.seasons.length - 1].season ? null : form.season,
        start: form.start === 'steal' ? null : form.start, v: form.side === 'off' ? null : form.side,
        sort: form.sort, dir: form.dir,
    });

    const formSeason = form?.season;
    const formTeam = form?.team;
    useEffect(() => {
        if (!formSeason) return undefined;
        let live = true;
        fetchPossessionLeague(formSeason)
            .then((d) => { if (live) setLeague({ season: formSeason, data: d }); })
            .catch((e) => { if (live) setLeague({ season: formSeason, error: e.response?.data?.detail || 'This season couldn\'t load.' }); });
        return () => { live = false; };
    }, [formSeason]);

    useEffect(() => {
        if (!formTeam) return undefined;
        let live = true;
        fetchPossessionTeam(formTeam)
            .then((d) => { if (live) setTeamData({ team: formTeam, data: d }); })
            .catch((e) => { if (live) setTeamData({ team: formTeam, error: e.response?.data?.detail || 'This team couldn\'t load.' }); });
        return () => { live = false; };
    }, [formTeam]);

    const labels = useMemo(() => (options ? { all: 'Every possession', ...Object.fromEntries(options.start_types.map((s) => [s.key, s.label])) } : {}), [options]);
    const short = useMemo(() => (options ? { all: 'All', ...Object.fromEntries(options.start_types.map((s) => [s.key, s.short])) } : {}), [options]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const d = league?.season === form.season ? league.data : null;
    const leagueError = league?.season === form.season ? league.error : '';
    const t = form.team && teamData?.team === form.team ? teamData.data : null;
    const teamError = form.team && teamData?.team === form.team ? teamData.error : '';
    const side = form.side;
    const sideWord = side === 'off' ? 'offence' : 'defence';
    const seasonLabel = options.seasons.find((s) => s.season === form.season)?.label;
    const lg = d ? Object.fromEntries(d.league.map((r) => [r.start_type, r])) : {};
    const teamRow = d && form.team ? d.teams.find((x) => x.team === form.team) : null;
    const ranked = d ? d.teams.map((x) => ({ team: x.team, c: cell(x, form.start, side) })).filter((x) => x.c) : [];
    const pooled = options.pooled;
    const stealGap = pooled.steal.ppp - pooled.made_fg.ppp;
    const onSort = (key) => setForm((f) => ({ ...f, sort: key, dir: f.sort === key ? (f.dir === 'asc' ? 'desc' : 'asc') : null }));
    const mainStarts = options.start_types.filter((s) => s.main);
    const startSignal = d?.signal.find((s) => s.start_type === form.start && s.side === side);
    const transAll = d?.transition.find((x) => x.start_type === 'all');
    const transDreb = d?.transition.find((x) => x.start_type === 'dreb');
    const meanPoss = (k) => (d ? d.teams.reduce((a, x) => a + (cell(x, k, side)?.poss ?? 0), 0) / d.teams.length : 0);
    const best = ranked.length ? [...ranked].sort((a, b) => (side === 'off' ? b.c.ppp - a.c.ppp : a.c.ppp - b.c.ppp)) : [];

    return (
        <section className="dashboard-card lb-card px-page">
            <h2 className="card-title hb-page-title">
                Possession Explorer
                <InfoTooltip label="How possessions are counted" title="Where the numbers come from">
                    {`${options.notes.coverage} ${options.notes.possession} ${options.notes.transition} ${options.notes.intervals}`}
                </InfoTooltip>
                <SourceBadge source={d?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="possessions" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every possession of every regular-season game since {options.seasons[0].label}, by how it began. Across all six
                seasons a possession that starts with a steal scores <strong>{f3(pooled.steal.ppp)}</strong> points, one that starts
                after the other team scores <strong>{f3(pooled.made_fg.ppp)}</strong>: a steal is worth about{' '}
                <strong>{stealGap.toFixed(2)}</strong> points more than the inbound after a make, and{' '}
                {(pooled.steal.ppp - pooled.dead_tov.ppp).toFixed(2)} more than a dead-ball turnover ({f3(pooled.dead_tov.ppp)}).
            </p>

            <div className="lb-controls pf-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={form.season} onChange={(e) => set({ season: Number(e.target.value) })}>
                        {[...options.seasons].reverse().map((s) => <option key={s.season} value={s.season}>{s.label}</option>)}
                    </select>
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={form.team ?? ''} onChange={(e) => set({ team: e.target.value || null })}>
                        <option value="">League (all 30)</option>
                        {options.teams.map((x) => <option key={x} value={x}>{x}</option>)}
                    </select>
                </label>
                <label>
                    <span>Rank teams by</span>
                    <select className="input-field" value={form.start} onChange={(e) => set({ start: e.target.value })}>
                        <option value="all">Every possession</option>
                        {mainStarts.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                    </select>
                </label>
                <div className="pf-toggles px-side" role="group" aria-label="Side of the ball">
                    <button type="button" className="pf-pill" aria-pressed={side === 'off'} onClick={() => set({ side: 'off', sort: null, dir: null })}>Offence</button>
                    <button type="button" className="pf-pill" aria-pressed={side === 'def'} onClick={() => set({ side: 'def', sort: null, dir: null })}>Defence</button>
                </div>
            </div>
            {side === 'def' && <p className="page-subtitle px-note">{options.notes.sides}</p>}

            {leagueError && <p className="error-message">{leagueError}</p>}
            {!d && !leagueError && <Loader />}
            {d && (
                <>
                    {form.team && teamRow ? (
                        <div className="px-stats">
                            <Stat k={`${form.team} ${side === 'off' ? 'points' : 'allowed'} per possession`} v={f3(cell(teamRow, 'all', side)?.ppp)}
                                sub={`${ordinal(cell(teamRow, 'all', side)?.rank)} of 30 · league ${f3(lg.all?.ppp)}`} />
                            <Stat k={side === 'off' ? 'After a steal' : 'After its own live-ball turnover'} v={f3(cell(teamRow, 'steal', side)?.ppp)}
                                sub={`${ordinal(cell(teamRow, 'steal', side)?.rank)} of 30 · league ${f3(lg.steal?.ppp)}`}
                                title={`${cell(teamRow, 'steal', side)?.poss} possessions; 95% interval ${f3(cell(teamRow, 'steal', side)?.lo)} to ${f3(cell(teamRow, 'steal', side)?.hi)}`} />
                            <Stat k="Transition share" v={pct(cell(teamRow, 'all', side)?.trans_share)}
                                sub={`${f3(cell(teamRow, 'all', side)?.trans_ppp)} per transition possession · league ${pct(lg.all?.trans_share)}`} />
                            <Stat k={side === 'off' ? 'Second-chance points a game' : 'Second-chance points allowed a game'}
                                v={f1(side === 'off' ? teamRow.totals.second_chance_pg : teamRow.totals.d_second_chance_pg)}
                                sub={`${pct(side === 'off' ? teamRow.totals.oreb_share : teamRow.totals.d_oreb_share)} of possessions had an offensive rebound`} />
                        </div>
                    ) : (
                        <div className="px-stats">
                            <Stat k="Points per possession" v={f3(lg.all?.ppp)} sub={`${seasonLabel} · ${lg.all?.poss.toLocaleString()} possessions in ${lg.all?.games.toLocaleString()} games`} />
                            <Stat k="After a steal vs after a make" v={`${f3(lg.steal?.ppp)} vs ${f3(lg.made_fg?.ppp)}`}
                                sub={`${signed3(lg.steal?.ppp - lg.made_fg?.ppp)} points a possession; ${pct(lg.steal?.share)} of possessions start with a steal`} />
                            <Stat k="Transition" v={pct(lg.all?.trans_share)}
                                sub={transAll ? `of timed possessions: ${f3(transAll.trans.ppp)} a possession vs ${f3(transAll.settled.ppp)} settled` : ''} />
                            <Stat k="Second-chance points a team-game" v={f1(lg.all ? lg.all.second_chance_pts / lg.all.games / 2 : null)}
                                sub={`${pct(lg.all ? lg.all.oreb_poss / lg.all.poss : null)} of possessions had an offensive rebound`} />
                        </div>
                    )}
                    {form.team && !teamRow && <p className="empty-message">{form.team} has no possessions on file in {seasonLabel}.</p>}

                    <h3 className="px-h">{form.team ? `${form.team}'s ${sideWord} by how the possession began, ${seasonLabel}` : `How the possession began, ${seasonLabel}`}</h3>
                    <PppDotChart
                        name={`points per possession by start ${form.team ?? 'league'} ${seasonLabel} ${sideWord}`}
                        ariaLabel={`Points per possession by how the possession began, ${form.team ?? 'league'}, ${seasonLabel}`}
                        better={side === 'off' ? 'high' : 'low'}
                        refLine={form.team ? undefined : lg.all?.ppp}
                        refLabel="Every possession"
                        items={[...(form.team ? ['all'] : []), ...mainStarts.map((s) => s.key)]
                            .map((k) => {
                                const x = form.team && teamRow ? cell(teamRow, k, side) : lg[k];
                                if (!x) return null;
                                return {
                                    key: k, label: form.team ? short[k] : labels[k], shortLabel: short[k], ppp: x.ppp, lo: x.lo, hi: x.hi, poss: x.poss,
                                    share: form.team ? null : x.share, ref: form.team ? lg[k]?.ppp : undefined,
                                    note: options.start_types.find((s) => s.key === k)?.description,
                                };
                            }).filter(Boolean)}
                    />
                    <p className="page-subtitle px-foot">
                        {form.team
                            ? `Dots: ${form.team}'s points ${side === 'off' ? 'scored' : 'allowed'} per possession with a 95% interval; ticks: the league. Green: clearly better than the league for that kind of possession, red: clearly worse.`
                            : 'Each kind of possession with a 95% interval; the line is every possession together. Hover a row for what the start means and how often it happens.'}
                        {' '}Held balls and a few hundred possessions with a gap in ESPN&apos;s log are left out of the chart (they are in the totals).
                    </p>

                    <h3 className="px-h">
                        {labels[form.start]}: every team&apos;s {sideWord}, {seasonLabel}
                    </h3>
                    {startSignal && (
                        <p className="page-subtitle px-note">
                            {best.length > 0 && <>{side === 'off' ? 'Best' : 'Stingiest'}: <strong>{best[0].team}</strong> {f3(best[0].c.ppp)}; {side === 'off' ? 'worst' : 'leakiest'}: <strong>{best[best.length - 1].team}</strong> {f3(best[best.length - 1].c.ppp)}. </>}
                            How much of this spread is real: about <strong>{pct(startSignal.real_share, 0)}</strong> of it is more than
                            chance (a team plays {Math.round(ranked.reduce((a, x) => a + x.c.poss, 0) / Math.max(1, ranked.length)).toLocaleString()} such
                            possessions a season), and a team&apos;s value repeats the next season with r ={' '}
                            {startSignal.yty_mean == null ? '—' : startSignal.yty_mean.toFixed(2)}.
                            {startSignal.real_share != null && startSignal.real_share < 0.4 && ' Mostly noise: read the ranking with that in mind.'}
                        </p>
                    )}
                    <PppDotChart
                        name={`${labels[form.start]} by team ${seasonLabel} ${sideWord}`}
                        ariaLabel={`${labels[form.start]}: points per possession by team, ${seasonLabel}, ${sideWord}`}
                        better={side === 'off' ? 'high' : 'low'}
                        refLine={lg[form.start]?.ppp}
                        refLabel={`League ${labels[form.start].toLowerCase()}`}
                        rowH={20}
                        onPick={(item) => set({ team: item.key })}
                        items={best.map((x) => ({
                            key: x.team, label: x.team, ppp: x.c.ppp, lo: x.c.lo, hi: x.c.hi, poss: x.c.poss,
                            highlight: x.team === form.team, note: `${ordinal(x.c.rank)} of ${ranked.length} · click to open`,
                        }))}
                    />

                    {form.team ? (
                        <>
                            <h3 className="px-h">{form.team} season by season ({sideWord})</h3>
                            {teamError && <p className="error-message">{teamError}</p>}
                            {!t && !teamError && <Loader />}
                            {t && <TeamHistory team={t} side={side} short={short} />}
                            <p className="page-subtitle px-foot">
                                <TeamLink abbr={form.team} season={form.season} className="px-link">Open the {form.team} team page</TeamLink>
                                {' · '}
                                <button type="button" className="oo-link" onClick={() => set({ team: null })}>Back to the league</button>
                            </p>
                        </>
                    ) : null}

                    <h3 className="px-h">Every team, {seasonLabel} ({sideWord})</h3>
                    <TeamTable data={d} side={side} sort={form.sort} dir={form.dir} onSort={onSort} short={short}
                        selected={form.team} onPick={(team) => set({ team })} />

                    <h3 className="px-h">Transition: shooting early after a stop</h3>
                    <p className="page-subtitle px-note">
                        After a defensive rebound, possessions whose first shot comes in the first seconds score far more than ones
                        that set up. Transition here = the first shot or free throw within {options.transition.window_seconds} s
                        of the start, where the early-shot premium has faded; {pct(options.transition.take_fouls_within_window, 0)} of
                        the {options.transition.take_fouls} &ldquo;transition take fouls&rdquo; on file (2022-23 on) fall inside it.
                        {transDreb && <> In {seasonLabel}, {pct(transDreb.trans.poss / (transDreb.trans.poss + transDreb.settled.poss), 0)} of
                            possessions after a defensive rebound were transition: {f3(transDreb.trans.ppp)} points a possession,
                            against {f3(transDreb.settled.ppp)} when they set up.</>}
                    </p>
                    <TransitionCurve ppp={options.transition.dreb_ppp_by_second} n={options.transition.dreb_n_by_second}
                        windowSec={options.transition.window_seconds} name="points per possession by seconds to first shot after a defensive rebound" />
                    <p className="page-subtitle px-foot">
                        All six seasons, possessions after a player&apos;s defensive rebound; seconds with fewer than 200 possessions are
                        left out. Shooting early is partly a choice and partly an opportunity (a numbers advantage), so this shows what
                        early shots are worth, not what forcing one would gain. {options.notes.transition}
                    </p>
                    {d.transition.length > 0 && (
                        <>
                            <TableExport name={`possessions transition ${seasonLabel}`} />
                            <div className="table-wrapper">
                                <table className="data-table lb-table px-table px-narrow">
                                    <thead>
                                        <tr>
                                            <th>After</th><th className="lb-num">Timed possessions</th><th className="lb-num">Transition</th>
                                            <th className="lb-num">Transition PPP</th><th className="lb-num">Settled PPP</th><th className="lb-num">Gap</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {d.transition.map((x) => (
                                            <tr key={x.start_type}>
                                                <td>{x.start_type === 'all' ? 'Every timed possession (incl. period starts)' : labels[x.start_type].replace(/^After an? /, '').replace(/^a /, '')}</td>
                                                <td className="lb-num">{(x.trans.poss + x.settled.poss).toLocaleString()}</td>
                                                <td className="lb-num">{pct(x.trans.poss / (x.trans.poss + x.settled.poss))}</td>
                                                <td className="lb-num">{f3(x.trans.ppp)}</td>
                                                <td className="lb-num">{f3(x.settled.ppp)}</td>
                                                <td className="lb-num">{signed3(x.trans.ppp - x.settled.ppp)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}

                    <h3 className="px-h">Is it a team trait? ({sideWord}, all six seasons)</h3>
                    <p className="page-subtitle px-note">
                        A team plays about {Math.round(meanPoss('steal')).toLocaleString()} possessions a season after steals and{' '}
                        {Math.round(meanPoss('made_fg')).toLocaleString()} after the other team scores ({seasonLabel}), so part of any gap
                        between teams is luck. &ldquo;Share beyond chance&rdquo; compares the spread between teams with the spread chance
                        alone would give; &ldquo;repeats&rdquo; is how well a team&apos;s value predicts its own next season.
                    </p>
                    <SignalTable signal={d.signal} side={side} labels={labels} />

                    <h3 className="px-h">League by season</h3>
                    <LeagueTrend options={options} />
                    <p className="page-subtitle px-foot">
                        {options.notes.coverage} {options.notes.possession} {options.notes.intervals} Ratings here are per possession
                        ({f3(pooled.all.ppp)} = {(pooled.all.ppp * 100).toFixed(1)} per 100), counted rather than estimated, so they sit a little above On/Off&apos;s.
                    </p>
                </>
            )}
        </section>
    );
}
