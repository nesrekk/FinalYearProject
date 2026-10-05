import React, { useEffect, useMemo, useState } from 'react';
import { fetchCoachingDecision, fetchCoachingOptions, fetchCoachingTests } from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import TeamLogo from '../common/TeamLogo';
import { IntervalChart } from '../common/CoachingCharts';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/playfinder.css';
import '../../styles/possessions.css';
import '../../styles/coaching.css';

// Coaching Decisions (?page=coaching): four things coaches act on (a timeout
// after a run, a coach's challenge, fouling up 3 late, the 2-for-1), each
// tested the way the platform tests every popular belief: the decision is
// shuffled within matched moments and the observed gap is compared with the
// shuffles (GET /coaching/*, from scripts/build_coaching_decisions.py).
// Inputs in the link: v (timeout | challenge | foul_up3 | twoforone | tests),
// team, season, sort, dir.

const VIEWS = [
    { key: 'timeout', label: 'Timeouts' },
    { key: 'challenge', label: 'Challenges' },
    { key: 'foul_up3', label: 'Up 3, late' },
    { key: 'twoforone', label: '2-for-1' },
    { key: 'tests', label: 'All tests' },
];
const MINUS = '−';
const CLAIM = { timeout: 'Timeout after an 8-0 run', challenge: 'Challenge won vs lost', foul_up3: 'Fouling up 3 late', twoforone: 'Shooting early for a 2-for-1' };
// Signed, with the sign taken from the rounded value (no "−0.00").
const signed = (v, d) => {
    const r = Number(v.toFixed(d));
    return `${r > 0 ? '+' : r < 0 ? MINUS : ''}${Math.abs(r).toFixed(d)}`;
};
const fmtPts = (v) => (v == null ? '—' : signed(v, 2));
const fmtPp = (v) => (v == null ? '—' : `${signed(v * 100, 1)} pp`);
const fmtPct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const fmtPoss = (v) => (v == null ? '—' : signed(v, 2));
const fmtP = (p) => (p == null ? '—' : p < 0.001 ? '< 0.001' : p.toFixed(3));
const UNIT_FMT = { pts: fmtPts, wp: fmtPp, share: fmtPp, poss: fmtPoss };
const UNIT_WORD = { pts: 'points', wp: 'win-probability points', share: 'percentage points', poss: 'possessions' };
// Secondary outcomes whose unit isn't the decision's own.
const SECONDARY_UNITS = {
    'timeout:outcome_d': 'share', 'challenge:outcome_b': 'pts', 'foul_up3:outcome_b': 'share', 'foul_up3:outcome_c': 'share',
    'twoforone:outcome_b': 'poss', 'twoforone:outcome_c': 'pts', 'twoforone:outcome_d': 'pts',
};

function formFromParams(p, o) {
    const seasons = o.seasons.map((s) => s.season);
    return {
        view: parseParam.oneOf(p, 'v', VIEWS.map((v) => v.key)) ?? 'timeout',
        team: parseParam.oneOf(p, 'team', o.teams),
        season: parseParam.int(p, 'season', { min: seasons[0], max: seasons[seasons.length - 1] }),
        sort: parseParam.str(p, 'sort'),
        dir: parseParam.oneOf(p, 'dir', ['asc', 'desc']),
    };
}

function Stat({ k, v, sub, title, tone }) {
    return (
        <div className={`px-stat${tone ? ` cd-stat--${tone}` : ''}`} title={title}>
            <div className="px-stat-k">{k}</div>
            <div className="px-stat-v">{v}</div>
            {sub && <div className="px-stat-sub">{sub}</div>}
        </div>
    );
}

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;

function verdictOf(test) {
    if (!test) return null;
    if (test.survives) return { word: 'Holds up', tone: 'pos', text: 'survives the false-discovery check across its family' };
    if (test.p != null && test.p < 0.05) return { word: 'Weak', tone: null, text: 'nominally under 5%, but not after the false-discovery check' };
    return { word: 'No evidence', tone: null, text: 'inside what shuffling the decision gives' };
}

function Headline({ d, unit }) {
    const fmt = UNIT_FMT[unit];
    const t = d.scope_test;
    const lv = d.live;
    const def = d.definition;
    const v = verdictOf(t?.level === 'season' ? { ...t, survives: null } : t);
    const statLabel = d.scope_stat;
    const isRate = d.decision === 'challenge' && d.team && !d.season && t;
    const f = isRate ? fmtPp : fmt;
    const fm = unit === 'share' ? fmtPct : f;      // a share's two means are levels, not differences
    return (
        <div className="px-stats">
            <Stat k={statLabel} v={f(t ? t.stat : lv?.stat)}
                sub={t ? (t.ci_lo != null ? `95% interval ${f(t.ci_lo)} to ${f(t.ci_hi)} · p ${fmtP(t.p)}` : `p ${fmtP(t.p)} · ${t.n_perm.toLocaleString()} shuffles`)
                    : 'matched effect, no p-value at this scope'}
                title={d.scope_note} />
            <Stat k={isRate ? 'Won vs league rate' : `${def.treated.split(' (')[0]} vs ${def.control.split(' (')[0]}`}
                v={isRate ? `${fmtPct(t.treated_mean)} vs ${fmtPct(t.control_mean)}` : `${fm(t ? t.treated_mean : lv?.treated_mean)} vs ${fm(t ? t.control_mean : lv?.control_mean)}`}
                sub={isRate ? 'success rate, the league in the same season and quarter group' : 'matched: control averaged within the same strata'} />
            <Stat k="Sample" v={`${(t ?? lv)?.n_treated?.toLocaleString() ?? '—'} / ${(t ?? lv)?.n_control?.toLocaleString() ?? '—'}`}
                sub={isRate ? 'challenges won / lost' : `${def.treated.split(' (')[0]} / ${def.control.split(' (')[0]}, in strata holding both`} />
            {v ? (
                <Stat k="Verdict" v={v.word} tone={v.tone} sub={t.q != null ? `${v.text} (q ${fmtP(t.q)})` : v.text} />
            ) : (
                <Stat k="Verdict" v="—" sub="Too few decisions at this scope for a test; the league and team tests are stored." />
            )}
        </div>
    );
}

function TrendChart({ d, unit }) {
    const fmt = UNIT_FMT[unit];
    const items = d.trend.map((r) => ({
        key: String(r.season), label: r.label, value: r.stat ?? null, lo: r.ci_lo ?? null, hi: r.ci_hi ?? null,
        highlight: r.season === d.season,
        note: `${r.n.toLocaleString()} decision points, ${r.treated.toLocaleString()} ${d.definition.treated.split(' (')[0]}${r.p != null ? ` · p ${fmtP(r.p)}` : ''}`,
    }));
    if (!items.some((x) => x.value != null)) return null;
    return (
        <IntervalChart items={items} refLine={0} refLabel="No effect" fmt={fmt} legend={d.team ? `${d.team}: matched effect (no interval)` : 'Matched effect, 95% interval'}
            name={`coaching ${d.decision} by season ${d.team ?? 'league'}`} ariaLabel={`${d.label}, by season`} />
    );
}

function TeamTable({ d, unit, sort, dir, onSort, onPick }) {
    const challenge = d.decision === 'challenge';
    const fmt = challenge ? fmtPp : UNIT_FMT[unit];
    const tested = d.teams.some((t) => t.stat != null);
    const cols = [
        { key: 'n', label: challenge ? 'Challenges' : 'Decisions', get: (t) => t.n, fmt: (v) => v.toLocaleString() },
        { key: 'treated_share', label: challenge ? 'Won' : 'Share treated', title: challenge ? 'Share of decided challenges won' : `Share that ${d.definition.treated}`, get: (t) => t.treated_share, fmt: (v) => fmtPct(v) },
        ...(tested ? [
            { key: 'stat', label: challenge ? 'Above league' : 'Effect', title: challenge ? 'Success rate minus the league\'s in the same season and quarter group' : 'Matched effect within the team\'s own decisions', get: (t) => t.stat, fmt },
            { key: 'p', label: 'p', get: (t) => t.p, fmt: fmtP },
            { key: 'q', label: 'q (BH)', title: 'Benjamini-Hochberg adjusted p over the 30 teams', get: (t) => t.q, fmt: fmtP },
        ] : []),
    ];
    const col = cols.find((c) => c.key === sort) ?? cols.find((c) => c.key === (tested ? 'stat' : 'n'));
    const s = (dir ?? 'desc') === 'asc' ? 1 : -1;
    const rows = [...d.teams].sort((a, b) => {
        const va = col.get(a);
        const vb = col.get(b);
        if (va == null) return 1;
        if (vb == null) return -1;
        return s * (va - vb) || a.team.localeCompare(b.team);
    });
    return (
        <>
            <TableExport name={`coaching ${d.decision} teams`} />
            <div className="table-wrapper">
                <table className="data-table lb-table px-table cd-table">
                    <thead>
                        <tr>
                            <th>Team</th>
                            {cols.map((c) => {
                                const active = c.key === col.key;
                                return (
                                    <th key={c.key} className="lb-num" title={c.title} aria-sort={active ? (s > 0 ? 'ascending' : 'descending') : 'none'}>
                                        <button type="button" className="oo-sort" onClick={() => onSort(c.key)}>
                                            {c.label}{active && <span aria-hidden="true">{s > 0 ? ' ▲' : ' ▼'}</span>}
                                        </button>
                                    </th>
                                );
                            })}
                            {tested && <th title="Survives Benjamini-Hochberg at 5% over the 30 teams">FDR 5%</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((t) => (
                            <tr key={t.team} className={t.team === d.team ? 'px-row--sel' : undefined}>
                                <td className="oo-team">
                                    <button type="button" className="px-team-btn" onClick={() => onPick(t.team)} title={`Show ${t.team}`}>
                                        <TeamLogo abbreviation={t.team} size={18} /> <span>{t.team}</span>
                                    </button>
                                </td>
                                {cols.map((c) => {
                                    const v = c.get(t);
                                    const tone = c.key === 'stat' && t.survives ? (v > 0 ? ' oo-pos' : ' oo-neg') : '';
                                    return <td key={c.key} className={`lb-num${tone}`}>{v == null ? '—' : c.fmt(v)}</td>;
                                })}
                                {tested && <td>{t.survives ? <strong>Yes</strong> : t.stat == null ? '—' : 'No'}</td>}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle px-foot">
                {tested
                    ? (challenge
                        ? 'Each team over all six seasons: its share of challenges won minus the league\'s in the same season and quarter group; the null shuffles every challenge\'s outcome within those groups. '
                        : 'Each team over all six seasons, its own decision points matched within season, quarter group and a coarser margin or start-time band, and the decision shuffled within them. ')
                    : 'Too few decisions a team for a per-team test: counts only. '}
                Coloured: survives Benjamini-Hochberg at 5% over the 30 teams. Click a team to show it.
            </p>
        </>
    );
}

function TestsTable({ rows, name }) {
    return (
        <>
            <TableExport name={name} />
            <div className="table-wrapper">
                <table className="data-table lb-table px-table cd-table">
                    <thead>
                        <tr>
                            <th>Test</th><th className="lb-num">Effect</th><th className="lb-num">95% interval</th>
                            <th className="lb-num">Treated / control</th><th className="lb-num">p</th><th className="lb-num">q (BH)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map(({ t, unit, text }) => {
                            const f = UNIT_FMT[unit] ?? fmtPts;
                            return (
                                <tr key={`${t.family}:${t.key}`}>
                                    <td>{text}</td>
                                    <td className={`lb-num${t.survives ? (t.stat > 0 ? ' oo-pos' : ' oo-neg') : ''}`}>{f(t.stat)}</td>
                                    <td className="lb-num">{t.ci_lo == null ? '—' : `${f(t.ci_lo)} to ${f(t.ci_hi)}`}</td>
                                    <td className="lb-num">{t.n_treated?.toLocaleString()} / {t.n_control?.toLocaleString()}</td>
                                    <td className="lb-num">{fmtP(t.p)}</td>
                                    <td className="lb-num">{t.q == null ? 'not in a family' : fmtP(t.q)}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </>
    );
}

function DecisionDetail({ d, unit }) {
    const b = d.breakdowns;
    const fmt = UNIT_FMT[unit];
    if (!b || !Object.keys(b).length) return <p className="empty-message">No decisions on file for this choice.</p>;
    const scope = `${d.team ?? 'League'}${d.season ? `, ${label(d.season)}` : ', 2020-21 to 2025-26'}`;
    if (d.decision === 'timeout') {
        const sec = Object.fromEntries((d.secondary ?? []).map((t) => [t.key, t]));
        const stored = !d.team && !d.season;
        const winItems = b.windows.map((w) => {
            const t = stored ? (w.col === 'outcome' ? d.league : sec[w.col]) : null;
            return {
                key: String(w.k), label: `Next ${w.k} possessions`, value: t ? t.stat : w.stat, lo: t?.ci_lo ?? null, hi: t?.ci_hi ?? null,
                note: w.treated_mean != null ? `After a timeout ${fmtPts(w.treated_mean)}, matched no-timeout ${fmtPts(w.control_mean)} (net points for the team on the wrong end of the run)` : null,
            };
        });
        const rowItems = (rows) => rows.map((r) => ({
            key: r.key, label: r.label, value: r.stat,
            note: `${r.n.toLocaleString()} moments, ${fmtPct(r.treated_share, 0)} with a timeout · raw: ${fmtPts(r.treated_raw)} after a timeout, ${fmtPts(r.control_raw)} without`,
        }));
        return (
            <>
                <h3 className="px-h">Timeout minus no timeout, by how far ahead you look ({scope})</h3>
                <IntervalChart items={winItems} refLine={0} refLabel="No effect" fmt={fmtPts} legend={stored ? 'Matched effect on net points, 95% interval' : 'Matched effect on net points'}
                    name={`coaching timeout windows ${scope}`} ariaLabel="Effect of a timeout on net points over the next 2, 6 and 12 possessions" />
                <p className="page-subtitle px-foot">
                    Net points for the team on the wrong end of the run (its points minus the other team&apos;s), timeout minus matched
                    moments without one. Both groups regress after a run: an 8-0 run is followed by about even play either way
                    (raw averages in the hover), so the comparison is with the matched moments, never with the run itself.
                    {b.scored_next && <> It scored on its next possession {Math.abs(b.scored_next.stat * 100).toFixed(1)} percentage points {b.scored_next.stat < 0 ? 'less' : 'more'} often after a timeout ({fmtPct(b.scored_next.treated_mean)} vs {fmtPct(b.scored_next.control_mean)}, matched).</>}
                    {b.later_share != null && <> {fmtPct(b.later_share, 0)} of the no-timeout moments saw a timeout later in the window.</>}
                </p>
                <h3 className="px-h">By run size and quarter</h3>
                <IntervalChart items={[...rowItems(b.by_run), ...rowItems(b.by_period)]} refLine={0} refLabel="No effect" fmt={fmtPts}
                    legend="Matched effect, next 6 possessions" name={`coaching timeout by run and quarter ${scope}`}
                    ariaLabel="Effect of a timeout by run size and by quarter" />
            </>
        );
    }
    if (d.decision === 'challenge') {
        const won = b.by_season.reduce((a, r) => a + r.won, 0);
        const lost = b.by_season.reduce((a, r) => a + r.lost, 0);
        const rate = won / Math.max(1, won + lost);
        const unknown = b.by_season.reduce((a, r) => a + r.unknown, 0);
        const before = b.by_season.find((r) => r.season === 2023);
        const after = b.by_season[b.by_season.length - 1];
        return (
            <>
                <div className="px-stats">
                    <Stat k="Challenges won" v={fmtPct(rate)} sub={`${won.toLocaleString()} of ${(won + lost).toLocaleString()} decided; ${unknown.toLocaleString()} with no outcome logged`} />
                    <Stat k="Won minus lost, two possessions" v={b.value_pts ? `${fmtPts(b.value_pts.stat)} pts` : '—'}
                        sub="challenger's net points over the possession in progress and the next one" />
                    <Stat k="Win probability after" v={`${fmtPp(b.wp_won)} / ${fmtPp(b.wp_lost)}`} sub="average change after a won / a lost challenge" />
                    <Stat k="Value of a point there" v={b.median_lev == null ? '—' : `${(b.median_lev * 100).toFixed(1)} pp`} sub="median win probability per point at the challenges' moments" />
                </div>
                <h3 className="px-h">Share of challenges won, by season ({d.team ?? 'league'})</h3>
                <IntervalChart items={b.by_season.map((r) => ({
                    key: String(r.season), label: r.label, value: r.rate, lo: r.lo, hi: r.hi, highlight: r.season === d.season,
                    note: `${r.won} won, ${r.lost} lost${r.unknown ? `, ${r.unknown} with no outcome` : ''}`,
                }))} refLine={rate} refLabel={`${d.team ?? 'League'}, all seasons`} fmt={(v) => fmtPct(v, 0)} legend="Share won, 95% interval"
                    name={`coaching challenge success by season ${d.team ?? 'league'}`} ariaLabel="Share of coach's challenges won by season" />
                <p className="page-subtitle px-foot">
                    Since 2023-24 a team that wins its first challenge gets a second one (NBA, July 2023).
                    {before && after && <> Decided challenges a season: {(before.won + before.lost).toLocaleString()} in {before.label}, {(after.won + after.lost).toLocaleString()} in {after.label}.</>}
                </p>
                <h3 className="px-h">By quarter ({scope})</h3>
                <IntervalChart items={b.by_period.map((r) => ({
                    key: r.key, label: r.label, value: r.rate, lo: r.lo, hi: r.hi,
                    note: `${r.won} of ${r.n} won · a point was worth ${fmtPp(r.lev)} of win probability there (median)`,
                }))} refLine={rate} refLabel="All quarters" fmt={(v) => fmtPct(v, 0)} legend="Share won, 95% interval"
                    name={`coaching challenge success by quarter ${scope}`} ariaLabel="Share of coach's challenges won by quarter" />
                <p className="page-subtitle px-foot">
                    Late challenges are won less often and matter more: the median point is worth far more win probability in the last two minutes.
                    The quarters overlap: the last-two-minutes row is part of the 4th quarter row.
                </p>
            </>
        );
    }
    if (d.decision === 'foul_up3') {
        return (
            <>
                <div className="px-stats">
                    <Stat k="Leader won after fouling" v={fmtPct(b.win_fouled)} sub={`vs ${fmtPct(b.win_defended)} after defending (raw)`} />
                    <Stat k="Period ended tied" v={b.tied ? fmtPp(b.tied.stat) : '—'} sub={b.tied ? `${fmtPct(b.tied.treated_mean)} after fouling vs ${fmtPct(b.tied.control_mean)}, matched` : ''} />
                    <Stat k="Trailing team made a three" v={b.three ? fmtPp(b.three.stat) : '—'} sub={b.three ? `${fmtPct(b.three.treated_mean)} after fouling vs ${fmtPct(b.three.control_mean)}, matched` : ''} />
                    <Stat k="Situations" v={`${(b.reg + b.ot).toLocaleString()}`} sub={`${b.reg} in the 4th quarter, ${b.ot} in overtime`} />
                </div>
                <h3 className="px-h">By time left when the trailing team got the ball ({scope})</h3>
                <TableExport name={`coaching foul up 3 by time ${scope}`} />
                <div className="table-wrapper">
                    <table className="data-table lb-table px-table px-narrow cd-table">
                        <thead>
                            <tr>
                                <th>Time left</th><th className="lb-num">Situations</th><th className="lb-num">Fouled</th>
                                <th className="lb-num">Won, fouled</th><th className="lb-num">Won, defended</th><th className="lb-num">Matched gap</th>
                            </tr>
                        </thead>
                        <tbody>
                            {b.by_time.map((r) => (
                                <tr key={r.key}>
                                    <td>{r.label}</td><td className="lb-num">{r.n}</td><td className="lb-num">{fmtPct(r.treated_share, 0)}</td>
                                    <td className="lb-num">{fmtPct(r.treated_raw)}</td><td className="lb-num">{fmtPct(r.control_raw)}</td>
                                    <td className="lb-num">{fmtPp(r.stat)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <p className="page-subtitle px-foot">
                    Fouling stops the three ({b.three ? `${Math.abs(b.three.stat * 100).toFixed(1)} percentage points` : '—'} fewer made threes) but gives up free throws and an
                    offensive rebound chance; the win columns show what that trade came to. About {Math.round((b.reg + b.ot) / 6)} such
                    situations a season is a small sample: a few points of win probability either way can&apos;t be told from chance.
                </p>
            </>
        );
    }
    if (d.decision === 'twoforone') {
        const sp = b.split;
        return (
            <>
                <div className="px-stats">
                    <Stat k="On the early possession itself" v={sp.itself ? `${fmtPts(sp.itself.stat)} pts` : '—'} sub="quick shots are often open ones: part opportunity, not only choice" />
                    <Stat k="After it, to the end of the quarter" v={sp.after ? `${fmtPts(sp.after.stat)} pts` : '—'} sub="what the extra possession is worth" />
                    <Stat k="Possessions" v={sp.possessions ? fmtPoss(sp.possessions.stat) : '—'} sub="own minus opponent's possessions to the end of the quarter" />
                </div>
                <h3 className="px-h">By time left at the start of the possession ({scope})</h3>
                <IntervalChart items={b.by_start.map((r) => ({
                    key: r.key, label: r.label, value: r.stat,
                    note: `${r.n.toLocaleString()} possessions, ${fmtPct(r.treated_share, 0)} shot early · raw ${fmtPts(r.treated_raw)} early vs ${fmtPts(r.control_raw)} not`,
                }))} refLine={0} refLabel="No effect" fmt={fmtPts} legend="Matched effect on net points to the end of the quarter"
                    name={`coaching 2-for-1 by start time ${scope}`} ariaLabel="Effect of shooting early by time left at the start of the possession" />
                <p className="page-subtitle px-foot">
                    &ldquo;Early&rdquo; = the first shot (or shooting foul, or turnover) came with more than 24 seconds left, so the other
                    team can&apos;t hold the ball for the last shot. With 38 or more seconds left nearly everyone gets there anyway;
                    under 32 it takes a quick shot.
                </p>
                <TableExport name={`coaching 2-for-1 by start type ${scope}`} />
                <div className="table-wrapper">
                    <table className="data-table lb-table px-table px-narrow cd-table">
                        <thead><tr><th>Possession began</th><th className="lb-num">Possessions</th><th className="lb-num">Shot early</th><th className="lb-num">Matched effect</th></tr></thead>
                        <tbody>
                            {b.by_start_type.map((r) => (
                                <tr key={r.key}><td>{r.label}</td><td className="lb-num">{r.n.toLocaleString()}</td><td className="lb-num">{fmtPct(r.treated_share, 0)}</td><td className="lb-num">{fmt(r.stat)}</td></tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </>
        );
    }
    return null;
}

export default function CoachingDecisions() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [data, setData] = useState(null); // { key, data } | { key, error }
    const [tests, setTests] = useState(null);

    useEffect(() => {
        fetchCoachingOptions()
            .then((o) => { setOptions(o); setForm(formFromParams(params, o)); })
            .catch(() => setOptionsError('Coaching Decisions couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    useUrlSync(form && {
        v: form.view === 'timeout' ? null : form.view, team: form.team, season: form.season, sort: form.sort, dir: form.dir,
    });

    const view = form?.view;
    const team = form?.team;
    const season = form?.season;
    const key = view && view !== 'tests' ? `${view}|${team ?? ''}|${season ?? ''}` : null;
    useEffect(() => {
        if (!key) return undefined;
        let live = true;
        fetchCoachingDecision(view, { team, season })
            .then((d) => { if (live) setData({ key, data: d }); })
            .catch((e) => { if (live) setData({ key, error: e.response?.data?.detail || 'This view couldn\'t load.' }); });
        return () => { live = false; };
    }, [key, view, team, season]);

    useEffect(() => {
        if (view !== 'tests' || tests) return undefined;
        let live = true;
        fetchCoachingTests().then((t) => { if (live) setTests(t); }).catch(() => { if (live) setTests({ error: true }); });
        return () => { live = false; };
    }, [view, tests]);

    const decisions = useMemo(() => (options ? Object.fromEntries(options.decisions.map((x) => [x.key, x])) : {}), [options]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const league = options.families.find((f) => f.family === 'coaching:league');
    const d = data?.key === key ? data.data : null;
    const err = data?.key === key ? data.error : '';
    const unit = view !== 'tests' ? decisions[view]?.units : null;
    const onSort = (k) => setForm((f) => ({ ...f, sort: k, dir: f.sort === k ? (f.dir === 'asc' ? 'desc' : 'asc') : null }));
    const testText = (t) => {
        if (t.family === 'coaching:league') return decisions[t.key]?.label ?? t.key;
        const dec = t.family.split(':')[1];
        if (t.family.startsWith('sensitivity')) return `Sensitivity: ${t.note}`;
        return `${CLAIM[dec] ?? dec}: ${t.note}`;
    };
    const unitOf = (t) => (t.family === 'coaching:league' ? decisions[t.key]?.units
        : SECONDARY_UNITS[`${t.family.split(':')[1]}:${t.key}`] ?? decisions[t.family.split(':')[1]]?.units ?? 'pts');

    return (
        <section className="dashboard-card lb-card px-page cd-page">
            <h2 className="card-title hb-page-title">
                Coaching Decisions
                <InfoTooltip label="How the decisions are tested" title="One test for every decision">
                    {'Each decision is compared with matched moments where it wasn\'t made (same season, quarter, time left, score and more, per decision). '
                        + 'The effect is the average gap over those moments; its null comes from shuffling the decision within them 2,000 times (20,000 when the first pass looks significant), '
                        + 'and Benjamini-Hochberg at 5% is applied within each family (the four league-level beliefs; the 30 teams of a decision). '
                        + 'The interval is a game-clustered bootstrap. Every time is the corrected clock (ESPN stamps made shots a median 14 s late).'}
                </InfoTooltip>
                <SourceBadge source={d?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="coaching" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Four things coaches act on, tested the way this site tests every popular belief: compare the decision with matched
                moments where it wasn&apos;t made, and ask whether the gap is bigger than shuffling the decision gives. At the league level{' '}
                <strong>{league?.survivors ?? '—'} of {league?.units ?? 4}</strong> hold up after the false-discovery check.
                {' '}{VIEWS.filter((v) => options.league?.[v.key]).map((v) => {
                    const t = options.league[v.key];
                    const f = UNIT_FMT[decisions[v.key]?.units] ?? fmtPts;
                    return (
                        <span key={v.key} className="cd-claim">
                            {CLAIM[v.key]}: <strong>{f(t.stat)}{decisions[v.key]?.units === 'pts' ? ' pts' : ''}</strong>{' '}
                            ({t.survives ? 'holds up' : 'no evidence'}, p {fmtP(t.p)}).{' '}
                        </span>
                    );
                })}
            </p>

            <div className="pf-toggles cd-tabs" role="tablist" aria-label="Decision">
                {VIEWS.map((v) => (
                    <button key={v.key} type="button" role="tab" className="pf-pill" aria-selected={view === v.key} aria-pressed={view === v.key}
                        onClick={() => set({ view: v.key, sort: null, dir: null })}>{v.label}</button>
                ))}
            </div>

            {view !== 'tests' && (
                <div className="lb-controls pf-controls">
                    <label>
                        <span>Team</span>
                        <select className="input-field" value={team ?? ''} onChange={(e) => set({ team: e.target.value || null })}>
                            <option value="">League (all 30)</option>
                            {options.teams.map((x) => <option key={x} value={x}>{x}</option>)}
                        </select>
                    </label>
                    <label>
                        <span>Season</span>
                        <select className="input-field" value={season ?? ''} onChange={(e) => set({ season: e.target.value ? Number(e.target.value) : null })}>
                            <option value="">All six seasons</option>
                            {[...options.seasons].reverse().map((s) => <option key={s.season} value={s.season}>{s.label}</option>)}
                        </select>
                    </label>
                </div>
            )}

            {view === 'tests' ? (
                <>
                    <h3 className="px-h">Every family: k of n survive</h3>
                    <TableExport name="coaching families" />
                    <div className="table-wrapper">
                        <table className="data-table lb-table px-table px-narrow cd-table">
                            <thead><tr><th>Family</th><th className="lb-num">Tests</th><th className="lb-num">p &lt; 0.05</th><th className="lb-num">Expected by chance</th><th className="lb-num">Survive FDR 5%</th></tr></thead>
                            <tbody>
                                {[...options.families].sort((a, b) => (b.family === 'coaching:league') - (a.family === 'coaching:league')).map((f) => (
                                    <tr key={f.family}>
                                        <td>{f.label}</td><td className="lb-num">{f.units}</td><td className="lb-num">{f.p05}</td>
                                        <td className="lb-num">{f.expected.toFixed(1)}</td><td className="lb-num"><strong>{f.survivors}</strong></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle px-foot">
                        &ldquo;Expected by chance&rdquo; is 5% of the tests: the nominal rejections a world with no effect would give.
                        In the per-team families each team&apos;s own effect is tested against zero (for challenges: its success rate against the league&apos;s),
                        so where the league effect is real (the 2-for-1) teams survive because the effect is large enough to see in one team&apos;s
                        six seasons, not because teams differ.
                    </p>
                    <h3 className="px-h">League-level tests, secondary outcomes and sensitivity runs</h3>
                    {!tests && <Loader />}
                    {tests?.error && <p className="error-message">The tests couldn&apos;t load.</p>}
                    {tests?.league && (
                        <TestsTable name="coaching league tests"
                            rows={tests.league.map((t) => ({ t, unit: unitOf(t), text: testText(t) }))} />
                    )}
                    <p className="page-subtitle px-foot">
                        Effects in each decision&apos;s own unit: net points, percentage points of win probability or of a share, or possessions.
                        Only the four league-level beliefs form a family (q-values); the rest are reported without a correction.
                    </p>
                </>
            ) : (
                <>
                    {err && <p className="error-message">{err}</p>}
                    {!d && !err && <Loader />}
                    {d && (
                        <>
                            <h3 className="px-h cd-title">{d.definition.title}</h3>
                            <p className="page-subtitle px-note">{d.definition.summary}</p>
                            <Headline d={d} unit={unit} />
                            <p className="page-subtitle px-foot">{d.scope_note} Effect in {UNIT_WORD[unit]}{d.decision === 'challenge' ? ' (shown as percentage points)' : ''}.</p>
                            <DecisionDetail d={d} unit={unit} />
                            <h3 className="px-h">Season by season{d.team ? ` (${d.team})` : ''}</h3>
                            <TrendChart d={d} unit={unit} />
                            <p className="page-subtitle px-foot">
                                {d.team ? 'A team-season is a small sample: dots only, no interval or p-value.' : 'One stored test per season (not in any family).'}
                                {d.team && <>{' '}<TeamLink abbr={d.team} season={form.season ?? undefined} className="px-link">Open the {d.team} team page</TeamLink></>}
                            </p>
                            <h3 className="px-h">Every team</h3>
                            <TeamTable d={d} unit={unit} sort={form.sort} dir={form.dir} onSort={onSort} onPick={(t) => set({ team: t })} />
                            {d.not_on_file && <p className="page-subtitle cd-nof"><strong>Not on file.</strong> {d.not_on_file}</p>}
                        </>
                    )}
                </>
            )}
        </section>
    );
}
