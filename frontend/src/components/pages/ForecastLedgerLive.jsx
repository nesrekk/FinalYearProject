import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchLedgerLiveGames } from '../../services/api';
import Loader from '../Loader';
import ChartExport from '../common/ChartExport';
import ChartTooltip from '../common/ChartTooltip';
import InfoTooltip from '../common/InfoTooltip';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import useChartCrosshair from '../../utils/useChartCrosshair';
import { signed as signedNum } from '../../utils/format';

// Forecast Ledger, Live scoring tab (?page=ledger&tab=live&m=&cal=&tv=): the season scored so far
// against the locked forecasts (GET /ledger/live, appended nightly by scripts/ledger_update.py
// with the code at the lock's git tag). Every number comes from the stored log; nothing is
// computed here but formatting.

const VERSION_ORDER = ['roster', 'as_is', 'record', 'roster_pre', 'as_is_pre'];
const SHORT = { roster: 'Roster-aware', as_is: 'As is', record: 'Record only', roster_pre: 'Roster-aware (locked)', as_is_pre: 'As is (locked)' };
const METRIC_LABEL = { log_loss: 'Log loss', brier: 'Brier score' };

const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const signed = (v, d = 4) => signedNum(v, d);
const pct = (v, d = 0) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const fmtUtc = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return `${d.toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric' })}, `
        + `${d.toLocaleTimeString('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit' })} UTC`;
};
const fmtDate = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—');

function StatusCard({ data }) {
    const s = data.status;
    const run = s.last_run;
    const started = s.games_final > 0;
    return (
        <div className="lg-lock">
            <div className="lg-lock-grid">
                <div>
                    <span className="lg-k">Games scored</span>
                    <span className="lg-v">{s.games_scored_all_versions.toLocaleString()} of {s.games_scheduled.toLocaleString()}</span>
                    <span className="lg-sub">
                        {started
                            ? `${s.games_final.toLocaleString()} final; ${(s.games_before_tip ?? 0).toLocaleString()} scored with odds logged before tip-off`
                            : `The season starts ${fmtUtc(s.first_tip_utc)}; nothing to score yet`}
                    </span>
                </div>
                <div>
                    <span className="lg-k">Last update</span>
                    <span className="lg-v">{run ? fmtUtc(run.finished_at) : 'Not run yet'}</span>
                    <span className="lg-sub">
                        {run ? `${run.mode === 'espn' ? 'ESPN read' : 'Stored results'}: ${run.espn_events.toLocaleString()} events, ${run.new_rows} new odds rows${run.late_rows ? ` (${run.late_rows} recomputed after tip)` : ''}` : 'scripts/ledger_update.py has not stored anything'}
                        {run?.waiting ? ` · ${run.waiting}` : ''}
                    </span>
                </div>
                <div>
                    <span className="lg-k">Next games</span>
                    <span className="lg-v">{fmtDate(s.next_date)}</span>
                    <span className="lg-sub">Odds are logged on the morning of each date (US Eastern), from the final scores before it, with the code at tag <code>{run?.code_tag ?? 'ledger-2026-27'}</code></span>
                </div>
            </div>
        </div>
    );
}

function Verdict({ data }) {
    const s = data.status;
    const n = s.games_scored_all_versions;
    if (n < data.min_test_games) {
        return (
            <p className="rx-verdict">
                <strong>Too early to tell.</strong> {n.toLocaleString()} game{n === 1 ? '' : 's'} scored under every version so far; intervals start
                at {data.min_test_games} games (about two weeks of the season). Until then the running numbers below are shown, but no model can be
                said to be ahead.
            </p>
        );
    }
    const find = (a, b) => data.tests.find((t) => t.model_a === a && t.model_b === b && t.metric === 'log_loss' && t.variant === 'all');
    const say = (t, nameA, nameB) => {
        if (!t) return null;
        const outside = t.ci_hi < 0 || t.ci_lo > 0;
        const better = t.diff < 0 ? nameA : nameB;
        return (
            <span>
                {nameA} against {nameB}: log loss {signed(t.diff)} [{signed(t.ci_lo)}, {signed(t.ci_hi)}], {outside ? <strong>{better} ahead, outside the 95% interval</strong> : 'inside the 95% interval: can\'t tell yet'}.{' '}
            </span>
        );
    };
    return (
        <p className="rx-verdict">
            {say(find('roster', 'as_is'), 'Roster-aware', 'as is')}
            {say(find('as_is', 'record'), 'As is', 'record only')}
            {say(find('roster_pre', 'as_is_pre'), 'The locked roster-aware odds', 'the locked as-is odds')}
            <span className="lg-note">({n.toLocaleString()} games, tests as of {fmtDate(data.tests_as_of)}.)</span>
        </p>
    );
}

function Upcoming({ rows }) {
    if (!rows.length) return null;
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">Logged odds for games not yet final: chance the home team wins</h4>
            <TableExport name="forecast ledger logged odds upcoming" />
            <div className="table-wrapper">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Date</th><th>Game</th>
                            <th className="lb-num">Roster-aware</th><th className="lb-num">As is</th><th className="lb-num">Record only</th>
                            <th title="When the odds were computed, and whether that was before ESPN's tip time">Logged</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((g) => (
                            <tr key={g.espn_id}>
                                <td>{fmtDate(g.game_date)}</td>
                                <td>{g.away} @ {g.home}{g.home_b2b ? <span className="lg-note"> {g.home} b2b</span> : null}{g.away_b2b ? <span className="lg-note"> {g.away} b2b</span> : null}</td>
                                <td className="lb-num">{pct(g.roster, 1)}</td>
                                <td className="lb-num">{pct(g.as_is, 1)}</td>
                                <td className="lb-num">{pct(g.record, 1)}</td>
                                <td>{fmtUtc(g.logged_at)} <span className={g.before_tip ? 'lg-ok' : 'lg-note'}>{g.before_tip ? 'before tip' : 'after tip'}</span></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function Scoreboard({ metrics, n }) {
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">Scores so far, on the {n.toLocaleString()} games every version scored (lower is better)</h4>
            <TableExport name="forecast ledger scores so far" />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-model-table">
                    <thead>
                        <tr>
                            <th>Version</th><th className="lb-num">Games</th><th className="lb-num">Log loss</th><th className="lb-num">Brier</th>
                            <th className="lb-num" title="Share of games won by the side the version made the favourite">Favourite won</th>
                        </tr>
                    </thead>
                    <tbody>
                        {metrics.length ? VERSION_ORDER.map((v) => metrics.find((m) => m.version === v)).filter(Boolean).map((m) => (
                            <tr key={m.version}>
                                <td><span className={`lg-swatch lg-v--${m.version}`} aria-hidden="true" />{m.label}</td>
                                <td className="lb-num">{m.n.toLocaleString()}</td>
                                <td className="lb-num">{num(m.log_loss, 4)}</td>
                                <td className="lb-num">{num(m.brier, 4)}</td>
                                <td className="lb-num">{pct(m.favourite_won, 1)}</td>
                            </tr>
                        )) : <tr><td colSpan={5} className="lg-note">No game scored yet.</td></tr>}
                    </tbody>
                </table>
            </div>
            <p className="rp-panel-note">
                A coin flip scores log loss 0.6931 and Brier 0.2500. Record only is log5 of the two teams&apos; win % before the date (no home court,
                win % held to 5-95%, a coin flip until a team has played); early in the season it is often worse than a coin flip.
            </p>
        </div>
    );
}

function Tests({ data, variant, onVariant }) {
    const rows = data.tests.filter((t) => t.variant === variant);
    const hasEarly = data.tests.some((t) => t.variant === 'logged_before_tip');
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">
                Paired differences, A − B (negative: A has the lower error)
                <InfoTooltip label="How the intervals are made" title="Paired tests">
                    Both versions are scored on the same games. The 95% interval resamples games {Number(data.tests[0]?.resamples ?? 10000).toLocaleString()} times
                    (paper_tests&apos; paired bootstrap, as in the paper); p is the bootstrap p, DM the Diebold-Mariano p on the games in date order.
                    Nothing is shown before {data.min_test_games} games.
                </InfoTooltip>
            </h4>
            {data.tests.length > 0 && (
                <div className="ss-date-btns" role="tablist" aria-label="Which games">
                    {[['all', 'All scored games'], ['logged_before_tip', 'Odds logged before tip only']].map(([k, label]) => (
                        <button key={k} type="button" role="tab" aria-selected={variant === k} disabled={k === 'logged_before_tip' && !hasEarly}
                            className={`tab-btn ${variant === k ? 'tab-btn--active' : ''}`} onClick={() => onVariant(k)}>{label}</button>
                    ))}
                </div>
            )}
            <TableExport name={`forecast ledger paired tests ${variant}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-model-table">
                    <thead>
                        <tr>
                            <th>A − B</th><th>Measure</th><th className="lb-num">Games</th><th className="lb-num">A</th><th className="lb-num">B</th>
                            <th className="lb-num">Difference [95% interval]</th><th className="lb-num">p</th><th className="lb-num">DM p</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.length ? rows.map((t) => {
                            const outside = t.ci_hi < 0 || t.ci_lo > 0;
                            return (
                                <tr key={`${t.model_a}-${t.model_b}-${t.metric}`}>
                                    <td>{SHORT[t.model_a]} − {SHORT[t.model_b]}</td>
                                    <td>{METRIC_LABEL[t.metric]}</td>
                                    <td className="lb-num">{t.n.toLocaleString()}</td>
                                    <td className="lb-num">{num(t.value_a, 4)}</td>
                                    <td className="lb-num">{num(t.value_b, 4)}</td>
                                    <td className={`lb-num ${outside ? (t.diff < 0 ? 'lg-ok' : 'lg-bad') : ''}`}>{signed(t.diff)} [{signed(t.ci_lo)}, {signed(t.ci_hi)}]</td>
                                    <td className="lb-num">{num(t.p_boot, 3)}</td>
                                    <td className="lb-num">{num(t.dm_p, 3)}</td>
                                </tr>
                            );
                        }) : (
                            <tr><td colSpan={8} className="lg-note">
                                Too early: {data.status.games_scored_all_versions.toLocaleString()} of the {data.min_test_games} games needed before an interval is worth showing.
                            </td></tr>
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

// Cumulative log loss (or Brier) after each date, one line per version.
const RC_W = 680;
const RC_H = 260;
const RC_PAD = { l: 52, r: 16, t: 14, b: 30 };
const rcX = (i, n) => RC_PAD.l + (n > 1 ? (i / (n - 1)) * (RC_W - RC_PAD.l - RC_PAD.r) : (RC_W - RC_PAD.l - RC_PAD.r) / 2);

function RunningChart({ running, metric, onMetric }) {
    const svgRef = useRef(null);
    const W = RC_W;
    const H = RC_H;
    const pad = RC_PAD;
    const dates = useMemo(() => [...new Set(running.map((r) => r.date))].sort(), [running]);
    // The y-range is set by the model versions; record only's early-season spike (a 1-0 team is a 95%
    // favourite) runs off the top, clipped, and the note under the chart says so.
    const vals = running.filter((r) => r.version !== 'record').map((r) => r[metric]);
    const lo = Math.min(...vals, metric === 'log_loss' ? 0.6 : 0.2);
    const hi = Math.max(...vals, metric === 'log_loss' ? 0.75 : 0.27);
    const offTop = running.filter((r) => r.version === 'record' && r[metric] > hi);
    const x = (i) => rcX(i, dates.length);
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo || 1)) * (H - pad.t - pad.b);
    const di = Object.fromEntries(dates.map((d, i) => [d, i]));
    const coin = metric === 'log_loss' ? Math.log(2) : 0.25;
    const points = useMemo(() => dates.map((d, i) => ({ x: rcX(i, dates.length), date: d })), [dates]);
    const { point, overlayProps } = useChartCrosshair(points, W);
    const at = point ? Object.fromEntries(running.filter((r) => r.date === point.date).map((r) => [r.version, r])) : null;
    const ticks = 5;
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">Running score through the season (lower is better)</h4>
            <div className="ss-date-btns" role="tablist" aria-label="Measure">
                {Object.entries(METRIC_LABEL).map(([k, label]) => (
                    <button key={k} type="button" role="tab" aria-selected={metric === k}
                        className={`tab-btn ${metric === k ? 'tab-btn--active' : ''}`} onClick={() => onMetric(k)}>{label}</button>
                ))}
            </div>
            {dates.length === 0 ? <p className="rp-panel-note">The chart starts with the first night of results.</p> : (
                <div className="ss-chart lg-chart" style={{ position: 'relative' }}>
                    <ChartExport svgRef={svgRef} name={`forecast ledger running ${metric}`} />
                    <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Cumulative ${METRIC_LABEL[metric]} by date for each version`}>
                        {Array.from({ length: ticks + 1 }, (_, k) => lo + (k * (hi - lo)) / ticks).map((v) => (
                            <g key={v}>
                                <line className="rx-grid" x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} />
                                <text className="rx-tick" x={pad.l - 6} y={y(v) + 4} textAnchor="end">{v.toFixed(3)}</text>
                            </g>
                        ))}
                        {coin >= lo && coin <= hi && (
                            <g>
                                <line className="lg-coin" x1={pad.l} x2={W - pad.r} y1={y(coin)} y2={y(coin)} />
                                <text className="rx-tick" x={W - pad.r} y={y(coin) - 4} textAnchor="end">coin flip</text>
                            </g>
                        )}
                        <clipPath id="lg-running-clip"><rect x={pad.l} y={pad.t} width={W - pad.l - pad.r} height={H - pad.t - pad.b} /></clipPath>
                        <g clipPath="url(#lg-running-clip)">
                            {VERSION_ORDER.map((v) => {
                                const pts = running.filter((r) => r.version === v);
                                if (!pts.length) return null;
                                return <polyline key={v} className={`lg-line lg-v--${v}`} points={pts.map((r) => `${x(di[r.date])},${y(r[metric])}`).join(' ')} />;
                            })}
                        </g>
                        {[0, dates.length - 1].filter((i, k, a) => a.indexOf(i) === k).map((i) => (
                            <text key={i} className="rx-tick" x={x(i)} y={H - 8} textAnchor={i ? 'end' : 'start'}>{fmtDate(dates[i])}</text>
                        ))}
                        {point && <line className="rx-grid" x1={point.x} x2={point.x} y1={pad.t} y2={H - pad.b} />}
                        <rect x={pad.l} y={pad.t} width={W - pad.l - pad.r} height={H - pad.t - pad.b} fill="transparent"
                            aria-label="Step through dates with the arrow keys" {...overlayProps} />
                    </svg>
                    {point && at && (
                        <ChartTooltip x={point.x} y={pad.t + 10} chartWidth={W} chartHeight={H} align="below">
                            <strong>Through {fmtDate(point.date)}</strong> ({at[VERSION_ORDER.find((v) => at[v])]?.n} games)
                            {VERSION_ORDER.filter((v) => at[v]).map((v) => <div key={v}>{SHORT[v]}: {num(at[v][metric], 4)}</div>)}
                        </ChartTooltip>
                    )}
                    <div className="ss-legend lg-legend">
                        {VERSION_ORDER.filter((v) => running.some((r) => r.version === v)).map((v) => (
                            <span key={v} className={`lg-key lg-v--${v}`}>{SHORT[v]}</span>
                        ))}
                    </div>
                    {offTop.length > 0 && (
                        <p className="rp-panel-note">
                            Record only runs off the top through {fmtDate(offTop[offTop.length - 1].date)} (up to {num(Math.max(...offTop.map((r) => r[metric])), 3)}):
                            early records are all-or-nothing, and log5 makes a 1-0 team a 95% favourite over an 0-1 team.
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}

function Calibration({ calibration, version, onVersion }) {
    const svgRef = useRef(null);
    const bins = calibration[version] ?? [];
    const W = 360;
    const H = 320;
    const pad = { l: 44, r: 12, t: 12, b: 40 };
    const s = (v) => pad.l + v * (W - pad.l - pad.r);
    const t = (v) => H - pad.b - v * (H - pad.t - pad.b);
    const versions = VERSION_ORDER.filter((v) => calibration[v]);
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">Calibration: predicted chance vs how often the home team won</h4>
            {!versions.length ? <p className="rp-panel-note">Starts with the first night of results.</p> : (
                <>
                    <div className="lb-controls">
                        <label>
                            <span>Version</span>
                            <select className="input-field" value={version} onChange={(e) => onVersion(e.target.value)}>
                                {versions.map((v) => <option key={v} value={v}>{SHORT[v]}</option>)}
                            </select>
                        </label>
                    </div>
                    <div className="ss-chart lg-chart lg-cal">
                        <ChartExport svgRef={svgRef} name={`forecast ledger calibration ${version}`} />
                        <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Calibration of ${SHORT[version]}: home-win rate by bin of predicted chance, with 95% Wilson intervals`}>
                            {[0, 0.2, 0.4, 0.6, 0.8, 1].map((v) => (
                                <g key={v}>
                                    <line className="rx-grid" x1={s(v)} x2={s(v)} y1={t(0)} y2={t(1)} />
                                    <line className="rx-grid" x1={s(0)} x2={s(1)} y1={t(v)} y2={t(v)} />
                                    <text className="rx-tick" x={s(v)} y={H - pad.b + 14} textAnchor="middle">{v * 100}%</text>
                                    <text className="rx-tick" x={pad.l - 6} y={t(v) + 4} textAnchor="end">{v * 100}%</text>
                                </g>
                            ))}
                            <line className="lg-coin" x1={s(0)} y1={t(0)} x2={s(1)} y2={t(1)} />
                            {bins.filter((b) => b.n > 0).map((b) => (
                                <g key={b.lo}>
                                    <line className="lg-ci" x1={s(b.predicted)} x2={s(b.predicted)} y1={t(b.ci_lo)} y2={t(b.ci_hi)} />
                                    <circle className={`lg-caldot lg-v--${version}`} cx={s(b.predicted)} cy={t(b.actual)} r={Math.max(2.5, Math.min(7, Math.sqrt(b.n) / 2))} />
                                </g>
                            ))}
                            <text className="rx-tick" x={(s(0) + s(1)) / 2} y={H - 6} textAnchor="middle">predicted chance the home team wins</text>
                        </svg>
                    </div>
                    <TableExport name={`forecast ledger calibration ${version}`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table">
                            <thead><tr><th>Bin</th><th className="lb-num">Games</th><th className="lb-num">Predicted</th><th className="lb-num">Home won</th><th className="lb-num">95% interval</th></tr></thead>
                            <tbody>
                                {bins.map((b) => (
                                    <tr key={b.lo} className={b.n ? '' : 'lg-muted'}>
                                        <td>{Math.round(b.lo * 100)}-{Math.round(b.hi * 100)}%</td>
                                        <td className="lb-num">{b.n}</td>
                                        <td className="lb-num">{pct(b.predicted, 1)}</td>
                                        <td className="lb-num">{pct(b.actual, 1)}</td>
                                        <td className="lb-num">{b.n ? `${pct(b.ci_lo)}-${pct(b.ci_hi)}` : '—'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="rp-panel-note">Dots sized by games; bars are 95% Wilson intervals on the home-win rate. A well-calibrated version&apos;s bars cross the diagonal.</p>
                </>
            )}
        </div>
    );
}

function rangeStatus(r, f) {
    const x = r[f];
    if (!x || !r.games) return null;
    const pace = (r.wins / r.games) * 82;
    if (x.exp_final_wins > x.locked_p90) return ['above', pace];
    if (x.exp_final_wins < x.locked_p10) return ['below', pace];
    return ['inside', pace];
}

function Standings({ teams, asOf }) {
    const [sort, setSort] = useState('roster');
    if (!teams.length) return null;
    const rows = [...teams].sort((a, b) => (sort === 'team' ? a.team.localeCompare(b.team)
        : sort === 'wins' ? (b.wins - a.wins) || (a.losses - b.losses) : b[sort].exp_final_wins - a[sort].exp_final_wins));
    const count = (f, k) => teams.filter((r) => rangeStatus(r, f)?.[0] === k).length;
    const played = teams.some((r) => r.games > 0);
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">Win totals so far against the locked 80% ranges (as of the morning of {fmtDate(asOf)})</h4>
            <p className="rp-panel-note" style={{ marginTop: 0 }}>
                Expected final wins = wins so far + each remaining game&apos;s win chance from this morning&apos;s ratings (the same frozen code),
                plus the games still owed to reach 82 against an average opponent. It carries no rating uncertainty, so on opening day it sits a
                little further from 41 than the locked simulation&apos;s mean.
                {played ? ` Headed outside the locked range: roster-aware ${count('roster', 'above') + count('roster', 'below')} of 30 teams, as is ${count('as_is', 'above') + count('as_is', 'below')} (an honest 80% range misses about 6).` : ''}
            </p>
            <TableExport name={`forecast ledger standings vs locked ranges ${asOf}`} />
            <div className="table-wrapper lg-games">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            {[['team', 'Team'], ['wins', 'W-L']].map(([k, label]) => (
                                <th key={k}><button type="button" className="rp-sort" onClick={() => setSort(k)}>{label}{sort === k ? ' ▼' : ''}</button></th>
                            ))}
                            <th className="lb-num" title="Wins × 82 / games">Pace</th>
                            {[['roster', 'Roster-aware'], ['as_is', 'As is']].map(([k, label]) => (
                                <th key={k} className="lb-num">
                                    <button type="button" className="rp-sort" onClick={() => setSort(k)}>{label}: expected (locked range){sort === k ? ' ▼' : ''}</button>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.team}>
                                <td><span className="ss-team"><TeamLink abbr={r.team} /></span></td>
                                <td>{r.wins}-{r.losses}</td>
                                <td className="lb-num">{r.games ? num((r.wins / r.games) * 82, 0) : '—'}</td>
                                {['roster', 'as_is'].map((f) => {
                                    const x = r[f];
                                    const st = rangeStatus(r, f);
                                    return (
                                        <td key={f} className="lb-num">
                                            <span className={st?.[0] === 'above' ? 'lg-ok' : st?.[0] === 'below' ? 'lg-bad' : ''}>{num(x.exp_final_wins)}</span>
                                            <span className="ss-range">{num(x.locked_p10, 0)}–{num(x.locked_p90, 0)}</span>
                                            {st && st[0] !== 'inside' ? <span className="lg-note"> {st[0]}</span> : null}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function ResultsLog({ recent, season }) {
    const [all, setAll] = useState(null);
    const [team, setTeam] = useState('');
    useEffect(() => {
        if (all === null) return undefined;
        let active = true;
        fetchLedgerLiveGames({ season, team: team || undefined }).then((d) => { if (active) setAll(d.games); }).catch(() => { if (active) setAll([]); });
        return () => { active = false; };
    }, [team]); // eslint-disable-line react-hooks/exhaustive-deps
    const rows = all ?? recent;
    if (!recent.length) return null;
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">{all ? 'Every scored game' : 'Latest results'}: each version&apos;s chance the home team won</h4>
            <div className="lg-actions">
                {all === null ? (
                    <button type="button" className="tab-btn" onClick={() => { setAll(recent); setTeam(''); fetchLedgerLiveGames({ season }).then((d) => setAll(d.games)).catch(() => {}); }}>Show every scored game</button>
                ) : (
                    <div className="lb-controls">
                        <label>
                            <span>Team</span>
                            <input className="input-field" value={team} maxLength={3} placeholder="e.g. BOS" onChange={(e) => setTeam(e.target.value.toUpperCase())} />
                        </label>
                    </div>
                )}
            </div>
            <TableExport name={`forecast ledger scored games ${team || 'all'}`} />
            <div className="table-wrapper lg-games">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Date</th><th>Result</th>
                            {VERSION_ORDER.map((v) => <th key={v} className="lb-num">{SHORT[v]}</th>)}
                            <th title="Whether the in-season odds were logged before ESPN's tip time">Logged</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((g) => {
                            const homeWon = g.home_pts > g.away_pts;
                            return (
                                <tr key={g.espn_id}>
                                    <td>{fmtDate(g.game_date)}</td>
                                    <td>{g.away} {g.away_pts} @ <strong>{g.home} {g.home_pts}</strong>{g.in_lock ? '' : <span className="lg-note"> not in the lock</span>}</td>
                                    {VERSION_ORDER.map((v) => {
                                        const p = g[v];
                                        const right = p == null ? null : (p >= 0.5) === homeWon;
                                        return <td key={v} className={`lb-num ${right === false ? 'lg-muted-cell' : ''}`}>{pct(p, 0)}</td>;
                                    })}
                                    <td><span className={g.before_tip ? 'lg-ok' : 'lg-note'}>{g.before_tip ? 'before tip' : 'recomputed'}</span></td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            <p className="rp-panel-note">Greyed odds favoured the team that lost. &quot;Recomputed&quot;: the update didn&apos;t run before tip-off that day, so the odds were computed afterwards by the same rule from the same earlier results.</p>
        </div>
    );
}

export default function ForecastLedgerLive({ data, form, set }) {
    if (!data) return <Loader />;
    const metric = form.metric;
    const calVersion = data.calibration[form.cal] ? form.cal : 'roster';
    return (
        <>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                The {data.season_label} season scored against the locked forecasts, game by game. Each morning (US Eastern) the update reads ESPN,
                stores the finals and logs every version&apos;s odds for that day&apos;s games with the time they were computed, using the code frozen
                at the lock&apos;s git tag and only results from earlier dates.
            </p>
            <SourceBadge source={data._source} />
            <StatusCard data={data} />
            <Verdict data={data} />
            <Upcoming rows={data.upcoming} />
            <Scoreboard metrics={data.metrics} n={data.status.games_scored_all_versions} />
            <Tests data={data} variant={form.tv} onVariant={(k) => set({ tv: k })} />
            <RunningChart running={data.running} metric={metric} onMetric={(k) => set({ metric: k })} />
            <div className="ss-model-grid lg-grid">
                <Calibration calibration={data.calibration} version={calVersion} onVersion={(v) => set({ cal: v })} />
                <Standings teams={data.teams} asOf={data.teams_as_of} />
            </div>
            <ResultsLog recent={data.recent} season={data.season} />
        </>
    );
}
