import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchAvailabilityGames, fetchAvailabilityModel, fetchSeasonSim, fetchSeasonSimModel, fetchSeasonSimOptions } from '../../services/api';
import Loader from '../Loader';
import { AvailabilitySection, AvailabilityWhatIf } from '../common/AvailabilityOdds';
import ChartExport from '../common/ChartExport';
import ChartTooltip from '../common/ChartTooltip';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import useChartCrosshair from '../../utils/useChartCrosshair';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { signed } from '../../utils/format';
import '../../styles/rapm.css';
import '../../styles/simulator.css';

// Season Simulator (?page=simulator&season=&asof=&team=&sort=&dir=&game=&out=&add=): 10,000
// simulated seasons from the morning of any date 2010-11 on, on a pre-game
// win model fitted leave-one-season-out (GET /season-sim, /season-sim/model).
// Every season on file is complete, so each team's odds sit next to what
// actually happened. From 2020-21 the day's games also show the odds with who
// played, and `game` opens the lineup what-if (out / add = player ids;
// GET /pregame/availability/*).

const AVAIL_FROM = 2021;
const idList = (params, key) => (parseParam.list(params, key) ?? []).map(Number).filter((n) => Number.isInteger(n) && n > 0).sort((a, b) => a - b);

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const fmtDate = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
const LOGO = { NOH: 'NOP', NJN: 'BKN' };

// Never round to 0% or 100% unless every run agreed.
function pct(v, d = 0) {
    if (v == null) return '—';
    if (v === 0) return '0%';
    if (v === 1) return '100%';
    const x = v * 100;
    if (x < 0.5) return '<1%';
    if (x > 99.5) return '>99%';
    return `${x.toFixed(d)}%`;
}
const pctClass = (v) => (v == null ? '' : v >= 0.995 ? 'ss-pct--sure' : v < 0.005 ? 'ss-pct--out' : '');

const COLS = [
    ['team', 'Team', 'text'],
    ['wins', 'W-L', 'Record before that morning'],
    ['position_now', 'Now', 'Conference position that morning, by the same ranking rule'],
    ['rating', 'Rating', 'Points per game better than average: this season\'s SRS blended with last season\'s (± its uncertainty)'],
    ['games_left', 'Left', 'Games left (home)'],
    ['mean_wins', 'Proj. W', 'Mean simulated win total, with the 10th-90th percentile range'],
    ['p_playoffs', 'Playoffs', 'Share of simulated seasons ending in the playoffs (play-in included from 2020-21)'],
    ['p_top6', 'Top 6', 'Straight into the playoffs, no play-in'],
    ['p_playin', 'Play-in', 'Finishing 7th to 10th'],
    ['p_first', '1st', 'Finishing first in the conference'],
    ['seeds', 'Finish', 'Chance of each conference finish, 1 to 15, before the play-in'],
    ['final', 'What happened', 'Final record, finish and result'],
];

function satText(a, g) {
    if (!a) return '—';
    const one = (team, list) => (list.length ? `${team}: ${list[0].name}${list.length > 1 ? ` +${list.length - 1}` : ''}` : null);
    return [one(g.away, a.sat_away), one(g.home, a.sat_home)].filter(Boolean).join('; ') || 'nobody';
}

function sortRows(rows, key, dir) {
    const sign = dir === 'asc' ? 1 : -1;
    const value = (r) => {
        if (key === 'team') return r.team;
        if (key === 'wins') return (r.wins + r.losses) ? r.wins / (r.wins + r.losses) : 0;
        if (key === 'final') return r.final.wins ?? -1;
        if (key === 'seeds') return -r.position_now;
        return r[key];
    };
    return [...rows].sort((a, b) => {
        const va = value(a);
        const vb = value(b);
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === 'string') return sign * va.localeCompare(vb);
        return sign * (va - vb) || (b.p_playoffs - a.p_playoffs);
    });
}

function Seeds({ p, teamName }) {
    return (
        <span className="ss-seeds" aria-label={`Finish odds for ${teamName}`}>
            {p.map((v, i) => (
                <span key={i} className={`ss-seed ${v >= 0.5 ? 'ss-seed--hot' : ''}`}
                    style={{ '--v': Math.min(1, v).toFixed(2) }}
                    title={`${i + 1}${['st', 'nd', 'rd'][i] ?? 'th'}: ${pct(v, 1)}`}>
                    {v >= 0.1 ? i + 1 : ''}
                </span>
            ))}
        </span>
    );
}

function Outcome({ f, playIn }) {
    if (f.wins == null) return <span className="ss-pct--out">not on file</span>;
    let badge;
    if (f.playoffs) badge = <span className="ss-badge ss-badge--in">{playIn && f.play_in ? 'Playoffs via play-in' : 'Playoffs'}</span>;
    else if (f.play_in) badge = <span className="ss-badge ss-badge--playin">Out in play-in</span>;
    else badge = <span className="ss-badge ss-badge--out">Out</span>;
    return <>{f.wins}-{f.losses} · {f.position}{['st', 'nd', 'rd'][f.position - 1] ?? 'th'} {badge}</>;
}

// Win-total distribution of one team's simulated seasons, with the 80% range
// and what actually happened.
function WinsHistogram({ row, seasonLabelText }) {
    const svgRef = useRef(null);
    const W = 640;
    const H = 260;
    const pad = { l: 44, r: 16, t: 18, b: 34 };
    const hist = row.wins_hist;
    const total = hist.reduce((a, b) => a + b, 0) || 1;
    const lo = Math.max(0, Math.min(Math.floor(row.wins_p10) - 6, hist.findIndex((c) => c > 0)));
    const hiIdx = hist.length - 1 - [...hist].reverse().findIndex((c) => c > 0);
    const hi = Math.min(row.games_final, Math.max(Math.ceil(row.wins_p90) + 6, hiIdx));
    const xs = [];
    for (let w = lo; w <= hi; w += 1) xs.push(w);
    const maxP = Math.max(...xs.map((w) => (hist[w] ?? 0) / total), 0.01);
    const bw = (W - pad.l - pad.r) / xs.length;
    const xFor = (w) => pad.l + (w - lo) * bw;
    const yFor = (p) => pad.t + (H - pad.t - pad.b) * (1 - p / maxP);
    const points = xs.map((w) => ({ x: xFor(w) + bw / 2, w, p: (hist[w] ?? 0) / total }));
    const { index, overlayProps } = useChartCrosshair(points, W);
    const point = index != null ? points[index] : null;
    const ticksY = [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxP);
    const finalW = row.final.wins;
    return (
        <div className="ss-chart">
            <ChartExport svgRef={svgRef} name={`win distribution ${row.team} ${seasonLabelText}`} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                aria-label={`${row.team}: distribution of simulated win totals, 80% range ${num(row.wins_p10, 0)} to ${num(row.wins_p90, 0)}`}>
                {ticksY.map((t) => (
                    <g key={t}>
                        <line className="rx-grid" x1={pad.l} x2={W - pad.r} y1={yFor(t)} y2={yFor(t)} />
                        <text className="rx-tick" x={pad.l - 6} y={yFor(t) + 4} textAnchor="end">{(t * 100).toFixed(0)}%</text>
                    </g>
                ))}
                {xs.map((w) => {
                    const p = (hist[w] ?? 0) / total;
                    const inRange = w >= Math.floor(row.wins_p10) && w <= Math.ceil(row.wins_p90);
                    return (
                        <rect key={w} className={`${inRange ? 'ss-bar' : 'ss-bar ss-bar--out'} ${point && point.w === w ? 'ss-bar--hot' : ''}`}
                            x={xFor(w) + 1} y={yFor(p)} width={Math.max(1, bw - 2)} height={yFor(0) - yFor(p)} />
                    );
                })}
                <line className="ss-mean-line" x1={xFor(row.mean_wins) + bw / 2} x2={xFor(row.mean_wins) + bw / 2} y1={pad.t} y2={yFor(0)} />
                <text className="ss-label" x={xFor(row.mean_wins) + bw / 2 + 4} y={pad.t + 10}>mean {num(row.mean_wins)}</text>
                {finalW != null && finalW >= lo && finalW <= hi && (
                    <>
                        <line className="ss-final-line" x1={xFor(finalW) + bw / 2} x2={xFor(finalW) + bw / 2} y1={pad.t} y2={yFor(0)} />
                        <text className="ss-label ss-label--strong" x={xFor(finalW) + bw / 2 + 4} y={pad.t + 24}>final {finalW}</text>
                    </>
                )}
                {finalW != null && (finalW < lo || finalW > hi) && (
                    <text className="ss-label ss-label--strong" x={W - pad.r} y={pad.t + 10} textAnchor="end">final {finalW} (off the chart)</text>
                )}
                <line className="rx-axis" x1={pad.l} x2={W - pad.r} y1={yFor(0)} y2={yFor(0)} />
                {xs.filter((w) => w % 5 === 0).map((w) => (
                    <text key={w} className="rx-tick" x={xFor(w) + bw / 2} y={H - pad.b + 16} textAnchor="middle">{w}</text>
                ))}
                <text className="rx-tick" x={(pad.l + W - pad.r) / 2} y={H - 4} textAnchor="middle">wins</text>
                <rect {...overlayProps} x={pad.l} y={pad.t} width={W - pad.l - pad.r} height={H - pad.t - pad.b} fill="transparent" />
            </svg>
            {point && (
                <ChartTooltip x={point.x} y={yFor(point.p)} chartWidth={W} chartHeight={H}>
                    <strong>{point.w} wins</strong>: {pct(point.p, 1)} of runs
                </ChartTooltip>
            )}
        </div>
    );
}

// Predicted vs actual rate, one dot per bin; dot area = games or teams in it.
function CalibrationChart({ series, name, xLabel, yLabel }) {
    const svgRef = useRef(null);
    const W = 420;
    const H = 360;
    const pad = { l: 46, r: 14, t: 14, b: 42 };
    const xFor = (v) => pad.l + v * (W - pad.l - pad.r);
    const yFor = (v) => pad.t + (1 - v) * (H - pad.t - pad.b);
    const points = series.flatMap((s) => s.points
        .filter((p) => p.predicted != null)
        .map((p) => ({ ...p, x: xFor(p.predicted), y: yFor(p.actual), series: s }))).sort((a, b) => a.x - b.x);
    const { index, overlayProps } = useChartCrosshair(points, W);
    const point = index != null ? points[index] : null;
    const maxN = Math.max(...points.map((p) => p.n), 1);
    return (
        <div className="ss-chart">
            <ChartExport svgRef={svgRef} name={name} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${name}: predicted against actual`}>
                {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                    <g key={t}>
                        <line className="rx-grid" x1={pad.l} x2={W - pad.r} y1={yFor(t)} y2={yFor(t)} />
                        <line className="rx-grid" y1={pad.t} y2={H - pad.b} x1={xFor(t)} x2={xFor(t)} />
                        <text className="rx-tick" x={pad.l - 6} y={yFor(t) + 4} textAnchor="end">{t * 100}%</text>
                        <text className="rx-tick" x={xFor(t)} y={H - pad.b + 16} textAnchor="middle">{t * 100}%</text>
                    </g>
                ))}
                <line className="ss-diag" x1={xFor(0)} y1={yFor(0)} x2={xFor(1)} y2={yFor(1)} />
                {series.map((s) => (
                    <polyline key={s.id} className={`ss-cal-line ${s.id === 'record' ? 'ss-cal-line--record' : ''}`}
                        points={s.points.filter((p) => p.predicted != null).map((p) => `${xFor(p.predicted)},${yFor(p.actual)}`).join(' ')} />
                ))}
                {points.map((p, i) => (
                    <circle key={`${p.series.id}-${i}`} className={`ss-dot ss-dot--${p.series.id} ${point === p ? 'ss-dot--hot' : ''}`}
                        cx={p.x} cy={p.y} r={3 + 9 * Math.sqrt(p.n / maxN)} />
                ))}
                <text className="rx-tick" x={(pad.l + W - pad.r) / 2} y={H - 6} textAnchor="middle">{xLabel}</text>
                <text className="rx-tick" transform={`translate(12 ${(pad.t + H - pad.b) / 2}) rotate(-90)`} textAnchor="middle">{yLabel}</text>
                <rect {...overlayProps} x={pad.l} y={pad.t} width={W - pad.l - pad.r} height={H - pad.t - pad.b} fill="transparent" />
            </svg>
            {point && (
                <ChartTooltip x={point.x} y={point.y} chartWidth={W} chartHeight={H}>
                    <strong>{point.series.label}</strong>, {pct(point.lo)}-{pct(point.hi)} bin<br />
                    predicted {pct(point.predicted, 1)}, actual {pct(point.actual, 1)}, n = {point.n}
                </ChartTooltip>
            )}
        </div>
    );
}

function ModelSection({ model }) {
    const [cp, setCp] = useState('halfway');
    const chosen = model.chosen;
    const cal = model.calibration[chosen] ?? [];
    const summary = model.backtest.summary;
    const metric = (c, m, k) => summary.find((r) => r.checkpoint === c && r.method === m && r.metric === k)?.value;
    const methods = ['model', 'record', 'standings'];
    const cps = ['opening', 'halfway', 'sixty'];
    const btSeries = methods.filter((m) => !(cp === 'opening' && m !== 'model') && m !== 'standings').map((m) => ({
        id: m, label: model.backtest.methods[m],
        points: model.backtest.calibration.filter((r) => r.checkpoint === cp && r.method === m && r.target === 'playoffs'),
    }));
    const bySeason = useMemo(() => {
        const out = {};
        model.by_season.forEach((r) => { (out[r.season] ??= {})[r.form] = r; });
        return Object.entries(out).sort((a, b) => a[0] - b[0]);
    }, [model]);
    const rest = (h, a) => model.rest.find((r) => r.home_b2b === h && r.away_b2b === a);
    return (
        <div className="ss-section">
            <h3 className="rp-panel-title">How good is the model? Every test is on games or seasons the fit never saw</h3>
            <div className="ss-model-grid">
                <div className="rp-panel">
                    <h4 className="rp-panel-title">Pre-game forms, leave-one-season-out on {model.forms[0].n.toLocaleString()} games</h4>
                    <TableExport name="pre-game model forms" />
                    <div className="table-wrapper">
                        <table className="data-table lb-table ss-model-table">
                            <thead><tr><th>Form</th><th className="lb-num" title="Lower is better; 0.693 = coin flips">Log loss</th><th className="lb-num" title="Lower is better; 0.25 = coin flips">Brier</th><th className="lb-num">Favourite wins</th></tr></thead>
                            <tbody>
                                {model.forms.map((f) => (
                                    <tr key={f.form} className={f.chosen ? 'ss-chosen' : undefined}>
                                        <td>{f.label}{f.chosen ? ' (used)' : ''}</td>
                                        <td className="lb-num">{num(f.loso_log_loss, 4)}</td>
                                        <td className="lb-num">{num(f.loso_brier, 4)}</td>
                                        <td className="lb-num">{pct(f.favourite_win_rate, 1)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="rp-panel-note">
                        Coefficients of the form used (all seasons): {Object.entries(model.forms.find((f) => f.chosen).beta).map(([k, v]) => `${k} ${signed(v, 3)}`).join(', ')}.
                        Expected margin is in points, so +{num(1 / model.forms.find((f) => f.chosen).beta.exp_margin, 1)} points of edge is one unit of log odds.
                        Home win rate {pct(model.era.find((e) => e.era === 'to_2020')?.home_win_rate, 1)} through 2019-20 and {pct(model.era.find((e) => e.era === 'from_2021')?.home_win_rate, 1)} since;
                        favourites win {pct(model.era.find((e) => e.era === 'to_2020')?.favourite_win_rate, 1)} and {pct(model.era.find((e) => e.era === 'from_2021')?.favourite_win_rate, 1)}.
                    </p>
                    <h4 className="rp-panel-title" style={{ marginTop: 'var(--space-4)' }}>Log loss by games played (the fewer of the two teams&apos;)</h4>
                    <TableExport name="pre-game log loss by games played" />
                    <div className="table-wrapper">
                        <table className="data-table lb-table ss-model-table">
                            <thead><tr><th>Games</th><th className="lb-num">n</th><th className="lb-num">Luck page</th><th className="lb-num">This season only</th><th className="lb-num">With prior</th><th className="lb-num">Prior + rest</th></tr></thead>
                            <tbody>
                                {model.by_games_played.map((r) => (
                                    <tr key={r.games_played}>
                                        <td>{r.games_played}</td><td className="lb-num">{r.n.toLocaleString()}</td>
                                        <td className="lb-num">{num(r.baseline, 4)}</td><td className="lb-num">{num(r.current, 4)}</td>
                                        <td className="lb-num">{num(r.prior, 4)}</td><td className="lb-num">{num(r.prior_rest, 4)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <h4 className="rp-panel-title" style={{ marginTop: 'var(--space-4)' }}>Rest: home win rate by who is on a back-to-back</h4>
                    <TableExport name="home win rate by rest" />
                    <div className="table-wrapper">
                        <table className="data-table lb-table ss-model-table">
                            <thead><tr><th>Home team</th><th>Away team</th><th className="lb-num">Games</th><th className="lb-num">Home wins</th><th className="lb-num" title="The form without rest terms">Model without rest</th></tr></thead>
                            <tbody>
                                {[[false, false], [true, false], [false, true], [true, true]].map(([h, a]) => {
                                    const r = rest(h, a);
                                    return r && (
                                        <tr key={`${h}${a}`}>
                                            <td>{h ? 'back-to-back' : 'rested'}</td><td>{a ? 'back-to-back' : 'rested'}</td>
                                            <td className="lb-num">{r.n.toLocaleString()}</td><td className="lb-num">{pct(r.home_win_rate, 1)}</td>
                                            <td className="lb-num">{pct(r.p_prior_mean, 1)}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
                <div className="rp-panel">
                    <h4 className="rp-panel-title">Calibration of the form used, by decile of predicted home win chance</h4>
                    <CalibrationChart name="pre-game calibration" xLabel="predicted home win chance" yLabel="home teams that won"
                        series={[{ id: 'model', label: 'Pre-game model', points: cal }]} />
                    <p className="rp-panel-note">Each dot is one decile of predictions; its size is the number of games. On the dashed line the model is exactly calibrated.</p>
                    <h4 className="rp-panel-title" style={{ marginTop: 'var(--space-4)' }}>Held-out log loss by season</h4>
                    <TableExport name="pre-game log loss by season" />
                    <div className="table-wrapper">
                        <table className="data-table lb-table ss-model-table">
                            <thead><tr><th>Season</th><th className="lb-num">Games</th><th className="lb-num">Luck page</th><th className="lb-num">Prior + rest</th><th className="lb-num">Favourite wins</th></tr></thead>
                            <tbody>
                                {bySeason.map(([s, r]) => (
                                    <tr key={s}>
                                        <td>{seasonLabel(Number(s))}</td><td className="lb-num">{r[chosen]?.n.toLocaleString()}</td>
                                        <td className="lb-num">{num(r.baseline?.log_loss, 4)}</td>
                                        <td className="lb-num">{num(r[chosen]?.log_loss, 4)}</td>
                                        <td className="lb-num">{pct(r[chosen]?.favourite_win_rate, 1)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <h3 className="rp-panel-title" style={{ marginTop: 'var(--space-5)' }}>The simulator&apos;s backtest: every season, three dates, against what happened</h3>
            <p className="page-subtitle" style={{ marginTop: 0 }}>
                Each season was simulated with coefficients fitted without it. Two baselines at the same dates: the standings as they stood
                (a team in a playoff spot gets 100%) and the record carried forward (each remaining game by log5 of the two teams&apos; win%,
                no home court, same tiebreaks). Brier score: 0 is perfect, 0.25 is a coin flip for every team; log loss 0.693 is coin flips.
            </p>
            <TableExport name="season simulator backtest" />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-model-table">
                    <thead>
                        <tr>
                            <th>Date</th><th>Method</th>
                            <th className="lb-num" title="Brier score of the playoff probability">Playoffs Brier</th>
                            <th className="lb-num">Playoffs log loss</th>
                            <th className="lb-num" title="Brier score of a top-6 finish, 2020-21 on">Top-6 Brier</th>
                            <th className="lb-num" title="Mean absolute error of the mean projected win total">Win total MAE</th>
                            <th className="lb-num" title="Share of final win totals inside the 10th-90th percentile range (80% if the ranges are honest)">Inside 80% range</th>
                            <th className="lb-num" title="Brier score of finishing first in the conference">1st Brier</th>
                            <th className="lb-num">Teams</th>
                        </tr>
                    </thead>
                    <tbody>
                        {cps.flatMap((c) => methods.filter((m) => summary.some((r) => r.checkpoint === c && r.method === m)).map((m) => (
                            <tr key={`${c}-${m}`} className={m === 'model' ? 'ss-chosen' : undefined}>
                                <td>{model.backtest.checkpoints[c]}</td><td>{model.backtest.methods[m]}</td>
                                <td className="lb-num">{num(metric(c, m, 'playoffs_brier'), 3)}</td>
                                <td className="lb-num">{num(metric(c, m, 'playoffs_log_loss'), 3)}</td>
                                <td className="lb-num">{num(metric(c, m, 'top6_brier'), 3)}</td>
                                <td className="lb-num">{num(metric(c, m, 'wins_mae'), 2)}</td>
                                <td className="lb-num">{pct(metric(c, m, 'wins_cover80'), 0)}</td>
                                <td className="lb-num">{num(metric(c, m, 'first_brier'), 3)}</td>
                                <td className="lb-num">{summary.find((r) => r.checkpoint === c && r.method === m && r.metric === 'playoffs_brier')?.n}</td>
                            </tr>
                        )))}
                    </tbody>
                </table>
            </div>
            <div className="ss-model-grid">
                <div className="rp-panel">
                    <h4 className="rp-panel-title" style={{ display: 'flex', alignItems: 'center' }}>
                        Playoff probability: predicted vs. actual
                        <label className="ss-inline-select">
                            <span>at</span>
                            <select className="input-field" value={cp} onChange={(e) => setCp(e.target.value)}>
                                {cps.map((c) => <option key={c} value={c}>{model.backtest.checkpoints[c]}</option>)}
                            </select>
                        </label>
                    </h4>
                    <CalibrationChart name={`playoff calibration ${cp}`} xLabel="predicted playoff chance" yLabel="teams that made it" series={btSeries} />
                    <div className="ss-legend">
                        {btSeries.map((s) => <span key={s.id} className={`ss-legend--${s.id}`}>{s.label}</span>)}
                    </div>
                </div>
                <div className="rp-panel">
                    <h4 className="rp-panel-title">Prior constants (fitted on {model.params.pairs?.value} franchise season pairs)</h4>
                    <TableExport name="season simulator constants" />
                    <div className="table-wrapper">
                        <table className="data-table lb-table ss-model-table">
                            <thead><tr><th>Constant</th><th className="lb-num">Value</th><th>Meaning</th></tr></thead>
                            <tbody>
                                {Object.values(model.params).map((p) => (
                                    <tr key={p.name}><td>{p.name}</td><td className="lb-num">{p.value == null ? p.note : num(p.value, 3)}</td><td className="lk-sub">{p.value == null ? '' : p.note}</td></tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    );
}

function ConferenceTable({ name, rows, playIn, sort, dir, onSort, selected, onPick, seasonLabelText, asOf }) {
    const sorted = useMemo(() => sortRows(rows, sort, dir), [rows, sort, dir]);
    const cols = COLS.filter(([k]) => playIn || (k !== 'p_top6' && k !== 'p_playin'));
    const cutAfter = playIn ? 6 : 8;
    return (
        <>
            <h3 className="ss-conf-title">{name}ern Conference <small>click a team for its win distribution</small></h3>
            <TableExport name={`season simulator ${name} ${seasonLabelText} as of ${asOf}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-table">
                    <thead>
                        <tr>
                            {cols.map(([k, label, title]) => (
                                <th key={k} className={k === 'team' || k === 'final' || k === 'seeds' ? '' : 'lb-num'} title={title === 'text' ? undefined : title}>
                                    <button type="button" className="rp-sort" onClick={() => onSort(k)} aria-label={`Sort by ${label}`}>
                                        {label}{sort === k ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}
                                    </button>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {sorted.map((r) => {
                            const rank = sort === 'p_playoffs' && dir === 'desc' ? sorted.indexOf(r) : -1;
                            return (
                                <tr key={r.team} className={`${selected === r.team ? 'ss-row--on' : ''} ${rank === cutAfter ? 'ss-row--cut' : ''}`}
                                    onClick={() => onPick(r.team)} tabIndex={0} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(r.team); } }}
                                    aria-selected={selected === r.team}>
                                    <td><span className="ss-team"><TeamLink abbr={LOGO[r.team] ?? r.team} season={Number(seasonLabelText.slice(0, 4)) + 1}>{r.team}</TeamLink></span></td>
                                    <td className="lb-num">{r.wins}-{r.losses}</td>
                                    <td className="lb-num">{r.position_now}</td>
                                    <td className="lb-num">{signed(r.rating)}<span className="ss-sd">±{num(r.rating_sd)}</span></td>
                                    <td className="lb-num">{r.games_left} <span className="ss-sd">({r.home_left}h)</span></td>
                                    <td className="lb-num">{num(r.mean_wins)}<span className="ss-range">{num(r.wins_p10, 0)}–{num(r.wins_p90, 0)}</span></td>
                                    <td className={`lb-num ${pctClass(r.p_playoffs)}`}>{pct(r.p_playoffs)}</td>
                                    {playIn && <td className={`lb-num ${pctClass(r.p_top6)}`}>{pct(r.p_top6)}</td>}
                                    {playIn && <td className={`lb-num ${pctClass(r.p_playin)}`}>{pct(r.p_playin)}</td>}
                                    <td className={`lb-num ${pctClass(r.p_first)}`}>{pct(r.p_first)}</td>
                                    <td><Seeds p={r.p_seed} teamName={r.team} /></td>
                                    <td><Outcome f={r.final} playIn={playIn} /></td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </>
    );
}

export default function SeasonSimulator({ onNavigate }) {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [res, setRes] = useState(null);   // { key, data } | { key, error }
    const [model, setModel] = useState(null);
    const [availModel, setAvailModel] = useState(null);
    const [availDay, setAvailDay] = useState(null);   // { key, data }
    const [dateDraft, setDateDraft] = useState('');
    const whatIfRef = useRef(null);

    useEffect(() => {
        fetchSeasonSimOptions()
            .then((o) => {
                setOptions(o);
                const season = parseParam.int(params, 'season', { min: 2000, max: 2100 });
                const s = o.seasons.some((x) => x.season === season) ? season : o.default_season;
                const info = o.seasons.find((x) => x.season === s);
                const asof = parseParam.str(params, 'asof');
                const ok = asof && /^\d{4}-\d{2}-\d{2}$/.test(asof) && asof >= info.first_date && asof <= info.last_date;
                setForm({
                    season: s,
                    asof: ok ? asof : info.halfway_date,
                    team: parseParam.str(params, 'team')?.toUpperCase() ?? null,
                    sort: parseParam.oneOf(params, 'sort', COLS.map(([k]) => k)) ?? 'p_playoffs',
                    dir: parseParam.oneOf(params, 'dir', ['asc', 'desc']) ?? 'desc',
                    game: (() => { const g = parseParam.str(params, 'game'); return g && /^\d{10}$/.test(g) ? g : null; })(),
                    out: idList(params, 'out'),
                    add: idList(params, 'add'),
                });
                setDateDraft(ok ? asof : info.halfway_date);
            })
            .catch(() => setOptionsError('The Season Simulator couldn\'t load. Is the impact API (port 8002) running, and has scripts/build_season_sim.py been run?'));
        fetchSeasonSimModel().then(setModel).catch(() => setModel({ error: true }));
        fetchAvailabilityModel().then(setAvailModel).catch(() => setAvailModel({ error: true }));
    }, [params]);

    useUrlSync(form && {
        season: form.season, asof: form.asof, team: form.team, sort: form.sort === 'p_playoffs' ? null : form.sort, dir: form.dir === 'desc' ? null : form.dir,
        game: form.game, out: form.game && form.out.length ? form.out.join(',') : null, add: form.game && form.add.length ? form.add.join(',') : null,
    });

    const key = form ? `${form.season}|${form.asof}` : null;
    useEffect(() => {
        if (!form) return undefined;
        let active = true;
        fetchSeasonSim({ season: form.season, as_of: form.asof })
            .then((d) => { if (active) setRes({ key, data: d }); })
            .catch((e) => { if (active) setRes({ key, error: e.response?.data?.detail || 'The simulation couldn\'t run.' }); });
        return () => { active = false; };
    }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

    const dayKey = form && form.season >= AVAIL_FROM ? form.asof : null;
    useEffect(() => {
        if (!dayKey) return undefined;
        let active = true;
        fetchAvailabilityGames(dayKey)
            .then((d) => { if (active) setAvailDay({ key: dayKey, data: d }); })
            .catch(() => { if (active) setAvailDay({ key: dayKey, data: null }); });
        return () => { active = false; };
    }, [dayKey]);

    useEffect(() => {
        if (form?.game && whatIfRef.current) whatIfRef.current.scrollIntoView({ block: 'nearest' });
    }, [form?.game]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const info = options.seasons.find((x) => x.season === form.season);
    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const data = res?.key === key ? res.data : null;
    const error = res?.key === key ? res.error : '';
    const applyDate = (d) => { if (d && d >= info.first_date && d <= info.last_date) { set({ asof: d, game: null, out: [], add: [] }); setDateDraft(d); } };
    const avail = availDay?.key === dayKey && availDay.data ? Object.fromEntries(availDay.data.games.map((g) => [g.game_id, g])) : null;
    const openGame = (gameId) => set({ game: form.game === gameId ? null : gameId, out: [], add: [] });
    const openUpset = (u) => {
        const s = options.seasons.find((x) => x.season === u.season);
        if (!s) return;
        set({ season: u.season, asof: u.game_date, team: null, game: u.game_id, out: [], add: [] });
        setDateDraft(u.game_date);
    };
    const checkpointOf = (s, d) => (['first_date', 'halfway_date', 'sixty_date', 'last_date'].find((k) => s[k] === d) ?? 'halfway_date');
    const onSort = (k) => set(form.sort === k ? { dir: form.dir === 'asc' ? 'desc' : 'asc' } : { sort: k, dir: k === 'team' || k === 'position_now' ? 'asc' : 'desc' });
    const allRows = data ? [...data.conferences.East, ...data.conferences.West] : [];
    const picked = allRows.find((r) => r.team === form.team) ?? null;
    const buttons = [['first_date', 'Opening day'], ['halfway_date', 'Halfway'], ['sixty_date', '60 games in'], ['last_date', 'Final day']];

    return (
        <section className="dashboard-card lb-card oo-card">
            <h2 className="card-title hb-page-title">
                Season simulator: playoff odds from any date
                <InfoTooltip label="How the simulator works" title="Under the hood">{data?.method ?? options.method}</InfoTooltip>
                <SourceBadge source={data?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="simulator" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Pick a season and a morning. Every game still to play is drawn {options.runs.toLocaleString()} times from a pre-game
                model tested on every game since 2010-11, the standings are ranked with tiebreaks and the play-in is played out.
                Every season on file is over, so the odds sit next to what actually happened. Past dates only: the schedule on file
                ends {fmtDate(options.seasons[options.seasons.length - 1].last_date)}; a new season works once its schedule and games are loaded.
            </p>

            <div className="lb-controls ss-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={form.season} onChange={(e) => {
                        const s = Number(e.target.value);
                        const next = options.seasons.find((x) => x.season === s);
                        const d = next[checkpointOf(info, form.asof)];
                        set({ season: s, asof: d, team: null, game: null, out: [], add: [] });
                        setDateDraft(d);
                    }}>
                        {[...options.seasons].reverse().map((s) => <option key={s.season} value={s.season}>{seasonLabel(s.season)}</option>)}
                    </select>
                </label>
                <label>
                    <span>As of the morning of</span>
                    <input className="input-field" type="date" min={info.first_date} max={info.last_date} value={dateDraft}
                        onChange={(e) => setDateDraft(e.target.value)} onBlur={() => applyDate(dateDraft)}
                        onKeyDown={(e) => { if (e.key === 'Enter') applyDate(dateDraft); }} />
                </label>
                <div className="ss-date-btns">
                    {buttons.map(([k, label]) => (
                        <button key={k} type="button" className={`tab-btn ${form.asof === info[k] ? 'tab-btn--active' : ''}`}
                            onClick={() => applyDate(info[k])} title={fmtDate(info[k])}>{label}</button>
                    ))}
                </div>
            </div>
            {info.note && <p className="ss-note">{seasonLabel(form.season)}: {info.note}</p>}

            <div className={data ? 'lb-results' : 'lb-results lb-results--stale'} aria-busy={!data}>
                {error && <p className="error-message">{error}</p>}
                {!data && !error && <Loader />}
                {data && (
                    <>
                        <p className="rx-verdict">
                            <strong>{data.season_label} as of the morning of {fmtDate(data.as_of)}{data.checkpoint ? ` (${options.checkpoints[data.checkpoint].toLowerCase()})` : ''}: {data.info.played_games.toLocaleString()} games played, {data.info.left_games.toLocaleString()} left.</strong>{' '}
                            {data.info.ratings_from_srs
                                ? <>Ratings are this season&apos;s SRS blended with last season&apos;s final rating (worth about {num(data.info.prior_games_worth, 0)} games), home court {signed(data.info.hca)} points. </>
                                : <>Fewer than 30 games played, so ratings are almost all last season&apos;s (0.6 × final SRS, worth about {num(data.info.prior_games_worth, 0)} games), home court {signed(data.info.hca)} points. </>}
                            {data.info.runs.toLocaleString()} simulated seasons, each drawing every team&apos;s true rating from its uncertainty first. {data.tie_rule}
                            {data.play_in ? ' Seeds 7-10 play the play-in in every run.' : ' Top 8 in each conference make the playoffs.'}
                            {data.info.left_games === 0 && ' Nothing left to simulate: this is the final standings.'}
                        </p>
                        {['East', 'West'].map((c) => (
                            <ConferenceTable key={c} name={c} rows={data.conferences[c]} playIn={data.play_in} sort={form.sort} dir={form.dir}
                                onSort={onSort} selected={form.team} onPick={(t) => set({ team: form.team === t ? null : t })}
                                seasonLabelText={data.season_label} asOf={data.as_of} />
                        ))}
                        <p className="page-subtitle lb-summary">
                            &quot;Finish&quot; shades each conference position by its chance (numbered from 10%). A line under the 8th
                            {data.play_in ? ' (or 6th)' : ''} row marks the cut when sorted by playoff odds. &lt;1% and &gt;99% mean some runs went the other way; 0% and 100% mean none did.
                        </p>
                        {picked && (
                            <>
                                <h3 className="rp-panel-title" style={{ marginTop: 'var(--space-5)' }}>
                                    {picked.team}: simulated win totals from {fmtDate(data.as_of)} ({picked.wins}-{picked.losses} then, {picked.games_left} games left; rating {signed(picked.rating)} ± {num(picked.rating_sd)}, last season&apos;s prior {signed(picked.prior_rating)})
                                </h3>
                                <WinsHistogram row={picked} seasonLabelText={data.season_label} />
                                <p className="rp-panel-note">
                                    Bars in orange are the 80% range ({num(picked.wins_p10, 0)}-{num(picked.wins_p90, 0)} wins); the dashed line is the real final ({picked.final.wins ?? '?'}).
                                    Remaining opponents&apos; average rating {signed(picked.rem_sos)}.
                                </p>
                            </>
                        )}
                        {data.games_on_date.length > 0 && (
                            <div className="ss-games">
                                <h3 className="rp-panel-title">Games on {fmtDate(data.as_of)}: the pre-game odds (held out: coefficients fitted without this season)</h3>
                                <TableExport name={`pre-game odds ${data.as_of}`} />
                                <div className="table-wrapper">
                                    <table className="data-table lb-table">
                                        <thead><tr><th>Game</th><th className="lb-num" title="P(home team wins) as of that morning">Home win chance</th>{avail && <th className="lb-num" title="The same with who played (rotation players), known at tip-off: an upper bound">With who played</th>}<th className="lb-num" title="Expected margin for the home team in points">Exp. margin</th><th>Rest</th>{avail && <th title="Rotation players who sat (by expected minutes)">Sat</th>}<th className="lb-num">Result</th>{avail && <th />}</tr></thead>
                                        <tbody>
                                            {data.games_on_date.map((g) => (
                                                <tr key={g.game_id}>
                                                    <td>{g.away} @ {g.home}{g.venue === 0 ? ' (neutral)' : ''}</td>
                                                    <td className="lb-num">{pct(g.p_home, 1)}</td>
                                                    {avail && <td className="lb-num">{avail[g.game_id]?.p_avail != null ? pct(avail[g.game_id].p_avail, 1) : '—'}</td>}
                                                    <td className="lb-num">{signed(g.exp_margin)}</td>
                                                    <td className={avail ? 'lk-sub av-sat' : 'lk-sub'}>{[g.home_b2b ? `${g.home} back-to-back` : null, g.away_b2b ? `${g.away} back-to-back` : null].filter(Boolean).join(', ') || '—'}</td>
                                                    {avail && <td className="lk-sub av-sat">{satText(avail[g.game_id], g)}</td>}
                                                    <td className={`lb-num ${(g.p_home >= 0.5) === g.home_won ? 'ss-win' : 'ss-loss'}`}>{g.away} {g.pts_away} – {g.pts_home} {g.home}</td>
                                                    {avail && <td>{avail[g.game_id]?.p_avail != null && <button type="button" className="pp-link rp-link" onClick={() => openGame(g.game_id)}>{form.game === g.game_id ? 'Close' : 'What-if'}</button>}</td>}
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                                <p className="rp-panel-note">Green: the favourite won.{avail ? ' "With who played" adds the rotation players who actually played (known at tip-off, so an upper bound on what injury news is worth); "What-if" takes players out or puts them back.' : ''} <button type="button" className="pp-link rp-link" onClick={() => onNavigate('analytics', 'luck', { season: form.season, asof: form.asof })}>Open the same morning on Luck &amp; Schedule →</button></p>
                            </div>
                        )}
                        {form.game && (
                            <div ref={whatIfRef}>
                                <AvailabilityWhatIf key={form.game} gameId={form.game} out={form.out} add={form.add}
                                    onChange={({ out, add }) => set({ out, add })} onClose={() => set({ game: null, out: [], add: [] })} />
                            </div>
                        )}
                    </>
                )}
            </div>

            {model && !model.error && <ModelSection model={model} />}
            {availModel && !availModel.error && <AvailabilitySection model={availModel} marginBeta={options.beta?.exp_margin} onOpenGame={openUpset} />}
        </section>
    );
}
