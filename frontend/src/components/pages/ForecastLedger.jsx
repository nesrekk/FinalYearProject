import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchLedgerGames, fetchLedgerLive, fetchLedgerPreseason, fetchLedgerRoster, ledgerCsvUrl } from '../../services/api';
import Loader from '../Loader';
import ChartExport from '../common/ChartExport';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import ForecastLedgerLive from './ForecastLedgerLive';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { signed } from '../../utils/format';
import '../../styles/rapm.css';
import '../../styles/simulator.css';
import '../../styles/ledger.css';

// Forecast Ledger (?page=ledger&tab=preseason|live&v=both|as_is|roster&team=&sort=&dir=): the
// 2026-27 preseason forecasts locked before the first tip (GET /ledger/*,
// written once by scripts/ledger_lock.py), with the SHA-256 that proves they
// haven't changed and the exact CSV it is taken of; and the Live scoring tab
// (ForecastLedgerLive.jsx, &m=&cal=&tv=), scored nightly by scripts/ledger_update.py.

const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const fmtUtc = (iso) => {
    const d = new Date(iso);
    return `${d.toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' })}, `
        + `${d.toLocaleTimeString('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit' })} UTC`;
};
const fmtDate = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });

function pct(v, d = 0) {
    if (v == null) return '—';
    if (v === 0) return '0%';
    if (v === 1) return '100%';
    const x = v * 100;
    if (x < 0.5) return '<1%';
    if (x > 99.5) return '>99%';
    return `${x.toFixed(d)}%`;
}

const VIEWS = [['both', 'Both side by side'], ['roster', 'Roster-aware in full'], ['as_is', 'As is in full']];
const TABS = [['live', 'Live scoring'], ['preseason', 'Preseason lock']];
const CAL_VERSIONS = ['roster', 'as_is', 'record', 'roster_pre', 'as_is_pre'];
const FULL_COLS = [
    ['team', 'Team', null],
    ['prior_mean', 'Rating', 'Opening-day rating: points per game better than an average team (± its uncertainty)'],
    ['mean_wins', 'Proj. W', 'Mean simulated win total, with the 10th-90th percentile range'],
    ['p_playoffs', 'Playoffs', 'Share of simulated seasons ending in the playoffs (through the play-in included)'],
    ['p_top6', 'Top 6', 'Straight into the playoffs'],
    ['p_playin', 'Play-in', 'Finishing 7th to 10th'],
    ['p_first', '1st seed', 'Finishing first in the conference'],
    ['p_round2', '2nd round', 'Winning a first-round series'],
    ['p_conf_finals', 'Conf. finals', 'Reaching the conference finals'],
    ['p_finals', 'Finals', 'Reaching the Finals'],
    ['p_title', 'Title', 'Winning the title'],
];
const BOTH_COLS = [
    ['team', 'Team'], ['rating_as_is', 'Rating (as is)'], ['rating_roster', 'Rating (roster)'],
    ['wins_as_is', 'Wins (as is)'], ['wins_roster', 'Wins (roster)'], ['playoffs_as_is', 'Playoffs (as is)'],
    ['playoffs_roster', 'Playoffs (roster)'], ['title_as_is', 'Title (as is)'], ['title_roster', 'Title (roster)'],
];
const BOTH_FIELD = {
    rating_as_is: ['as_is', 'prior_mean'], rating_roster: ['roster', 'prior_mean'], wins_as_is: ['as_is', 'mean_wins'],
    wins_roster: ['roster', 'mean_wins'], playoffs_as_is: ['as_is', 'p_playoffs'], playoffs_roster: ['roster', 'p_playoffs'],
    title_as_is: ['as_is', 'p_title'], title_roster: ['roster', 'p_title'],
};
const SORT_KEYS = [...new Set([...FULL_COLS.map(([k]) => k), ...BOTH_COLS.map(([k]) => k)])];

function sortBy(rows, value, dir) {
    const sign = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
        const va = value(a);
        const vb = value(b);
        if (typeof va === 'string') return sign * va.localeCompare(vb);
        return sign * ((va ?? -1e9) - (vb ?? -1e9));
    });
}

function HashLine({ hash }) {
    const [copied, setCopied] = useState(false);
    const copy = () => {
        navigator.clipboard?.writeText(hash).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }).catch(() => {});
    };
    return (
        <div className="lg-hash">
            <code>{hash}</code>
            <button type="button" className="tab-btn" onClick={copy} aria-label="Copy the SHA-256">{copied ? 'Copied' : 'Copy'}</button>
        </div>
    );
}

function LockCard({ data }) {
    const { lock, meta } = data;
    const lead = (new Date(lock.first_tip_utc) - new Date(lock.locked_at)) / 86400000;
    const kb = (lock.csv_bytes / 1e6).toFixed(1);
    return (
        <div className="lg-lock">
            <div className="lg-lock-grid">
                <div>
                    <span className="lg-k">Locked</span>
                    <span className="lg-v">{fmtUtc(lock.locked_at)}</span>
                    <span className="lg-sub">{num(lead, 1)} days before the first tip ({fmtUtc(lock.first_tip_utc)}, ESPN&apos;s schedule)</span>
                </div>
                <div>
                    <span className="lg-k">Checked just now</span>
                    <span className={`lg-v ${lock.hash_reproduced ? 'lg-ok' : 'lg-bad'}`}>
                        {lock.hash_reproduced ? 'The stored rows reproduce the hash' : 'The stored rows no longer match the hash'}
                    </span>
                    <span className="lg-sub">{lock.csv_lines.toLocaleString()} lines re-exported from the database for this page</span>
                </div>
                <div>
                    <span className="lg-k">Code</span>
                    <span className="lg-v"><code>{lock.code_commit.slice(0, 7)}</code> · tag <code>{lock.code_tag}</code></span>
                    <span className="lg-sub">In-season odds will be computed with the code at this tag</span>
                </div>
            </div>
            <span className="lg-k">SHA-256 of the locked forecasts</span>
            <HashLine hash={lock.lock_sha256} />
            <div className="lg-actions">
                <a className="tab-btn tab-btn--active" href={ledgerCsvUrl(data.season)} download={lock.csv_name}>Download the locked CSV ({kb} MB)</a>
            </div>
            <details className="lg-howto">
                <summary>How to check this hash yourself</summary>
                <ol>
                    <li>Download the CSV above. It is rebuilt from the stored rows on every download: one line per stored value
                        (<code>table,row,field,value</code>) of the five locked tables, in a fixed order.</li>
                    <li>In a terminal: <code>shasum -a 256 {lock.csv_name}</code> (macOS, Linux) or <code>certutil -hashfile {lock.csv_name} SHA256</code> (Windows).</li>
                    <li>The result must equal the hash above, and the one sent by email before {fmtUtc(lock.first_tip_utc)}. Any change to any forecast,
                        roster, rule or number changes it.</li>
                </ol>
                <p className="lg-sub">Roster read {fmtUtc(`${meta.roster_fetched_utc.value}`)}; schedule read {fmtUtc(`${meta.schedule_fetched_utc.value}`)}.</p>
            </details>
        </div>
    );
}

// Mean projected wins with the 80% range, both forecasts, one row per team.
function WinsChart({ rows, labelText }) {
    const svgRef = useRef(null);
    const W = 680;
    const rowH = 19;
    const pad = { l: 46, r: 16, t: 26, b: 30 };
    const H = pad.t + pad.b + rows.length * rowH;
    const lo = 10;
    const hi = 75;
    const x = (w) => pad.l + ((Math.max(lo, Math.min(hi, w)) - lo) / (hi - lo)) * (W - pad.l - pad.r);
    const ticks = [10, 20, 30, 41, 50, 60, 70];
    return (
        <div className="ss-chart lg-chart">
            <ChartExport svgRef={svgRef} name={`forecast ledger projected wins ${labelText}`} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                aria-label={`Projected wins for every team, ${labelText}: as-is and roster-aware forecasts with 80% ranges`}>
                {ticks.map((t) => (
                    <g key={t}>
                        <line className="rx-grid" x1={x(t)} x2={x(t)} y1={pad.t - 6} y2={H - pad.b} />
                        <text className="rx-tick" x={x(t)} y={H - pad.b + 16} textAnchor="middle">{t}</text>
                    </g>
                ))}
                <text className="rx-tick" x={x(41)} y={pad.t - 12} textAnchor="middle">.500</text>
                {rows.map((r, i) => {
                    const y = pad.t + i * rowH + rowH / 2;
                    return (
                        <g key={r.team}>
                            <text className="rx-tick" x={pad.l - 8} y={y + 4} textAnchor="end">{r.team}</text>
                            <line className="lg-range lg-range--asis" x1={x(r.as_is.wins_p10)} x2={x(r.as_is.wins_p90)} y1={y - 3} y2={y - 3} />
                            <line className="lg-range lg-range--roster" x1={x(r.roster.wins_p10)} x2={x(r.roster.wins_p90)} y1={y + 3} y2={y + 3} />
                            <circle className="lg-dot lg-dot--asis" cx={x(r.as_is.mean_wins)} cy={y - 3} r={4} />
                            <circle className="lg-dot lg-dot--roster" cx={x(r.roster.mean_wins)} cy={y + 3} r={4} />
                        </g>
                    );
                })}
                <text className="rx-tick" x={W - pad.r} y={H - 4} textAnchor="end">projected wins (of 82)</text>
            </svg>
            <div className="ss-legend lg-legend">
                <span className="lg-legend--asis">As is (hollow): mean and 80% range</span>
                <span className="lg-legend--roster">Roster-aware (filled): mean and 80% range</span>
            </div>
        </div>
    );
}

function BothTable({ conf, rows, sort, dir, onSort, selected, onPick, labelText }) {
    const value = (r) => (sort === 'team' ? r.team : BOTH_FIELD[sort] ? r[BOTH_FIELD[sort][0]][BOTH_FIELD[sort][1]] : r.roster.mean_wins);
    const sorted = sortBy(rows, value, dir);
    return (
        <>
            <h3 className="ss-conf-title">{conf}ern Conference <small>click a team for its locked roster and games</small></h3>
            <TableExport name={`forecast ledger ${conf} ${labelText} both forecasts`} />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-table">
                    <thead>
                        <tr>
                            {BOTH_COLS.map(([k, label]) => (
                                <th key={k} className={k === 'team' ? '' : 'lb-num'}>
                                    <button type="button" className="rp-sort" onClick={() => onSort(k)} aria-label={`Sort by ${label}`}>
                                        {label}{sort === k ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}
                                    </button>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {sorted.map((r) => (
                            <tr key={r.team} className={selected === r.team ? 'ss-row--on' : ''} tabIndex={0} aria-selected={selected === r.team}
                                onClick={() => onPick(r.team)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(r.team); } }}>
                                <td><span className="ss-team"><TeamLink abbr={r.team} /></span></td>
                                <td className="lb-num">{signed(r.as_is.prior_mean)}</td>
                                <td className="lb-num">{signed(r.roster.prior_mean)}</td>
                                <td className="lb-num">{num(r.as_is.mean_wins)}<span className="ss-range">{num(r.as_is.wins_p10, 0)}–{num(r.as_is.wins_p90, 0)}</span></td>
                                <td className="lb-num">{num(r.roster.mean_wins)}<span className="ss-range">{num(r.roster.wins_p10, 0)}–{num(r.roster.wins_p90, 0)}</span></td>
                                <td className="lb-num">{pct(r.as_is.p_playoffs)}</td>
                                <td className="lb-num">{pct(r.roster.p_playoffs)}</td>
                                <td className="lb-num">{pct(r.as_is.p_title, 1)}</td>
                                <td className="lb-num">{pct(r.roster.p_title, 1)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

function FullTable({ conf, rows, forecast, sort, dir, onSort, selected, onPick, labelText }) {
    const value = (r) => (sort === 'team' ? r.team : r[forecast][FULL_COLS.some(([k]) => k === sort) ? sort : 'mean_wins']);
    const sorted = sortBy(rows, value, dir);
    return (
        <>
            <h3 className="ss-conf-title">{conf}ern Conference <small>click a team for its locked roster and games</small></h3>
            <TableExport name={`forecast ledger ${conf} ${labelText} ${forecast}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-table">
                    <thead>
                        <tr>
                            {FULL_COLS.map(([k, label, title]) => (
                                <th key={k} className={k === 'team' ? '' : 'lb-num'} title={title ?? undefined}>
                                    <button type="button" className="rp-sort" onClick={() => onSort(k)} aria-label={`Sort by ${label}`}>
                                        {label}{sort === k ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}
                                    </button>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {sorted.map((row) => {
                            const r = row[forecast];
                            return (
                                <tr key={row.team} className={selected === row.team ? 'ss-row--on' : ''} tabIndex={0} aria-selected={selected === row.team}
                                    onClick={() => onPick(row.team)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(row.team); } }}>
                                    <td><span className="ss-team"><TeamLink abbr={row.team} /></span></td>
                                    <td className="lb-num">{signed(r.prior_mean)}<span className="ss-sd">±{num(r.prior_sd)}</span></td>
                                    <td className="lb-num">{num(r.mean_wins)}<span className="ss-range">{num(r.wins_p10, 0)}–{num(r.wins_p90, 0)}</span></td>
                                    {['p_playoffs', 'p_top6', 'p_playin', 'p_first', 'p_round2', 'p_conf_finals', 'p_finals'].map((k) => (
                                        <td key={k} className="lb-num">{pct(r[k])}</td>
                                    ))}
                                    <td className="lb-num">{pct(r.p_title, 1)}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </>
    );
}

function Hindcast({ meta }) {
    const h = meta.hindcast.value;
    const rows = [
        ['srs_rmse', 'Final SRS: root-mean-square error', 2, 'points'],
        ['wins82_mae', 'Win total (per 82 games): mean absolute error', 2, 'wins'],
        ['game_log_loss', 'Every game from opening-day ratings: log loss', 4, ''],
        ['game_brier', 'Every game from opening-day ratings: Brier score', 4, ''],
    ];
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">What to expect: the same two opening-day priors on {h.seasons.replace('-', ' to ').replace(/(\d{4})/g, (y) => `${Number(y) - 1}-${y.slice(-2)}`)}</h4>
            <TableExport name="forecast ledger hindcast" />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-model-table">
                    <thead><tr><th>Measure</th><th className="lb-num">Roster-aware</th><th className="lb-num">As is</th><th className="lb-num">Difference [95% interval]</th></tr></thead>
                    <tbody>
                        {rows.map(([k, label, d]) => (
                            <tr key={k}>
                                <td>{label}</td>
                                <td className="lb-num">{num(h[k].a, d)}</td>
                                <td className="lb-num">{num(h[k].b, d)}</td>
                                <td className="lb-num">{signed(h[k].diff, d)} [{signed(h[k].ci_lo, d)}, {signed(h[k].ci_hi, d)}]</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="rp-panel-note">
                {h.team_seasons} team-seasons and {h.games.toLocaleString()} games; the roster-aware numbers are leave-one-season-out (each season predicted
                with a and c fitted on the other {Object.keys(meta.hindcast_by_season.value).length - 1}); intervals resample whole seasons. Locked fit: prior
                mean = {num(h.roster_a, 3)} × centred team BPM + {num(h.roster_c, 3)} × last season&apos;s SRS, prior SD {num(Math.sqrt(h.tau2_roster), 2)} points
                (as is: 0.604 × last season&apos;s SRS, SD {num(Math.sqrt(Number(meta.tau2.value)), 2)}).
                {' '}<strong>This flatters the roster-aware forecast:</strong> the hindcast rosters are the players who actually played for each team (a
                player who missed the whole season isn&apos;t on them, and nobody traded in mid-season is). 2026-27 is the real test.
            </p>
        </div>
    );
}

function RosterPanel({ team, season, forecasts }) {
    const [got, setGot] = useState(null);   // { key, data }
    const key = `${season}|${team}`;
    useEffect(() => {
        let active = true;
        fetchLedgerRoster(team, season).then((d) => { if (active) setGot({ key, data: d }); })
            .catch(() => { if (active) setGot({ key, data: { error: true } }); });
        return () => { active = false; };
    }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
    const res = got?.key === key ? got.data : null;
    if (!res) return <Loader />;
    if (res.error) return <p className="error-message">The locked roster couldn&apos;t load.</p>;
    const r = forecasts.roster.find((x) => x.team === team);
    const a = forecasts.as_is.find((x) => x.team === team);
    const used = res.players.filter((p) => p.counted);
    const minutes = used.reduce((s, p) => s + p.minutes, 0);
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">{team}: the roster as ESPN listed it at lock time</h4>
            <p className="rp-panel-note" style={{ marginTop: 0 }}>
                {res.players.length} players listed, {used.length} given minutes ({num(minutes, 0)} of 240 a game{minutes < 239.99 ? `; the other ${num(240 - minutes, 0)} go to a replacement-level player` : ''}).
                Team BPM {signed(r.team_bpm)} against the league average → roster-aware rating {signed(r.prior_mean)} (as is {signed(a.prior_mean)},
                from last season&apos;s SRS {signed(a.srs_prev)}).
            </p>
            <TableExport name={`forecast ledger ${team} locked roster`} />
            <div className="table-wrapper">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Player</th><th>Pos</th><th className="lb-num" title="ESPN's years of experience, this season included">Exp.</th>
                            <th className="lb-num" title="Projected minutes a game (Marcel, build_projections.py)">Proj. min</th>
                            <th className="lb-num" title="Projected BPM (Marcel)">Proj. BPM</th>
                            <th className="lb-num" title="Minutes a game the depth-chart rule gave him">Minutes</th>
                            <th className="lb-num" title="Minutes × BPM / 48: points per 100 possessions he adds to the team">Adds</th>
                            <th title="ESPN's injury status at lock time (stored, not used)">Injury</th>
                        </tr>
                    </thead>
                    <tbody>
                        {res.players.map((p) => (
                            <tr key={p.espn_athlete_id} className={p.counted ? '' : 'lg-muted'}>
                                <td>{p.player_id ? <PlayerName playerId={p.player_id} name={p.player_name}>{p.player_name}</PlayerName> : p.player_name}
                                    {!p.player_id && <span className="lg-note"> no NBA record on file</span>}
                                    {p.player_id && p.proj_min == null && <span className="lg-note"> no projection</span>}</td>
                                <td>{p.position ?? '—'}</td>
                                <td className="lb-num">{p.experience ?? '—'}</td>
                                <td className="lb-num">{num(p.proj_min)}</td>
                                <td className="lb-num">{signed(p.proj_bpm)}</td>
                                <td className="lb-num">{p.counted ? num(p.minutes) : '—'}</td>
                                <td className="lb-num">{p.counted ? signed(p.contribution, 2) : '—'}</td>
                                <td>{p.injury_status ?? ''}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function GamesPanel({ team, season }) {
    const [got, setGot] = useState(null);   // { key, data }
    const key = `${season}|${team ?? ''}`;
    useEffect(() => {
        let active = true;
        fetchLedgerGames({ season, team: team || undefined }).then((d) => { if (active) setGot({ key, data: d }); })
            .catch(() => { if (active) setGot({ key, data: { error: true } }); });
        return () => { active = false; };
    }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
    const res = got?.key === key ? got.data : null;
    if (!res) return <Loader />;
    if (res.error) return <p className="error-message">The locked game odds couldn&apos;t load.</p>;
    // Without a team: the first seven days of the schedule.
    const week = res.games.length ? new Date(Date.parse(`${res.games[0].game_date}T12:00:00Z`) + 6 * 86400000).toISOString().slice(0, 10) : '';
    const shown = team ? res.games : res.games.filter((g) => g.game_date <= week);
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">{team ? `${team}: every scheduled game` : 'Opening week'}: locked chance the home team wins</h4>
            <TableExport name={`forecast ledger game odds ${team || 'opening week'}`} />
            <div className="table-wrapper lg-games">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Date</th><th>Game</th>
                            <th className="lb-num" title="P(home wins) from opening-day ratings, as-is forecast">Home win (as is)</th>
                            <th className="lb-num" title="P(home wins) from opening-day ratings, roster-aware forecast">Home win (roster)</th>
                            <th className="lb-num" title="Expected home margin, roster-aware">Exp. margin</th>
                            <th>Notes</th>
                        </tr>
                    </thead>
                    <tbody>
                        {shown.map((g) => (
                            <tr key={g.espn_id}>
                                <td>{fmtDate(g.game_date)}</td>
                                <td>{g.away} @ {g.home}{g.venue === 0 ? ` (${g.city})` : ''}</td>
                                <td className="lb-num">{pct(g.p_home_as_is, 1)}</td>
                                <td className="lb-num">{pct(g.p_home_roster, 1)}</td>
                                <td className="lb-num">{signed(g.exp_margin_roster)}</td>
                                <td className="lk-sub">{[g.home_b2b ? `${g.home} back-to-back` : null, g.away_b2b ? `${g.away} back-to-back` : null, g.note || null].filter(Boolean).join(' · ')}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="rp-panel-note">
                {team ? `${res.games.length} games on ESPN's schedule at lock time; the two the NBA adds after the NBA Cup group stage are simulated against a league-average opponent (one home, one away).`
                    : `Pick a team (click a row above) for its whole schedule. ${res.not_forecast.length} NBA Cup knockout games had no teams at lock time and are not forecast.`}
                {' '}The odds use only opening-day ratings; the in-season odds, updated with each night&apos;s results by the same locked code, come with live scoring.
            </p>
        </div>
    );
}

export default function ForecastLedger() {
    const params = useInitialParams();
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [live, setLive] = useState(null);
    const [form, setForm] = useState(() => ({
        tab: parseParam.oneOf(params, 'tab', TABS.map(([k]) => k)) ?? null,
        metric: parseParam.oneOf(params, 'm', ['log_loss', 'brier']) ?? 'log_loss',
        cal: parseParam.oneOf(params, 'cal', CAL_VERSIONS) ?? 'roster',
        tv: parseParam.oneOf(params, 'tv', ['all', 'logged_before_tip']) ?? 'all',
        view: parseParam.oneOf(params, 'v', VIEWS.map(([k]) => k)) ?? 'both',
        team: parseParam.str(params, 'team')?.toUpperCase() ?? null,
        sort: parseParam.oneOf(params, 'sort', SORT_KEYS) ?? null,
        dir: parseParam.oneOf(params, 'dir', ['asc', 'desc']) ?? 'desc',
    }));

    useEffect(() => {
        fetchLedgerPreseason()
            .then(setData)
            .catch((e) => setError(e.response?.data?.detail
                || 'The Forecast Ledger couldn\'t load. Is the impact API (port 8002) running?'));
        fetchLedgerLive()
            .then(setLive)
            .catch(() => setLive({ error: true }));
    }, []);

    // Until a game has been scored the page opens on the lock; after that, on the live scoring.
    const tab = form.tab ?? (live && !live.error && live.status.games_final > 0 ? 'live' : 'preseason');
    useUrlSync({
        tab: form.tab, v: form.view === 'both' ? null : form.view, team: form.team, sort: form.sort, dir: form.dir === 'desc' ? null : form.dir,
        m: form.metric === 'log_loss' ? null : form.metric, cal: form.cal === 'roster' ? null : form.cal, tv: form.tv === 'all' ? null : form.tv,
    });

    const byTeam = useMemo(() => {
        if (!data) return [];
        const a = Object.fromEntries(data.forecasts.as_is.map((r) => [r.team, r]));
        return data.forecasts.roster.map((r) => ({ team: r.team, conference: r.conference, roster: r, as_is: a[r.team] }));
    }, [data]);

    if (error) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;
    if (!data) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const defaultSort = form.view === 'both' ? 'wins_roster' : 'mean_wins';
    const validSort = form.view === 'both' ? BOTH_COLS.some(([k]) => k === form.sort) : FULL_COLS.some(([k]) => k === form.sort);
    const sort = validSort ? form.sort : defaultSort;
    const onSort = (k) => set(sort === k ? { sort: k, dir: form.dir === 'asc' ? 'desc' : 'asc' } : { sort: k, dir: k === 'team' ? 'asc' : 'desc' });
    const onPick = (t) => set({ team: form.team === t ? null : t });
    const chartRows = sortBy(byTeam, (r) => r.roster.mean_wins, 'desc');
    const { meta } = data;
    const title = [...data.forecasts.roster].sort((x, y) => y.p_title - x.p_title)[0];
    const titleA = [...data.forecasts.as_is].sort((x, y) => y.p_title - x.p_title)[0];
    const gaps = [...byTeam].sort((x, y) => Math.abs(y.roster.prior_mean - y.as_is.prior_mean) - Math.abs(x.roster.prior_mean - x.as_is.prior_mean)).slice(0, 3);
    const sd = (xs) => Math.sqrt(xs.reduce((s, x) => s + x * x, 0) / xs.length);   // ratings average zero
    const rules = Object.entries(meta).filter(([k]) => k.startsWith('rule_') && k !== 'rule_minutes_alternative');

    return (
        <section className="dashboard-card lb-card oo-card">
            <h2 className="card-title hb-page-title">
                Forecast Ledger: {data.season_label}, locked before opening night
                <InfoTooltip label="How the forecasts were made" title="Under the hood">
                    Two forecasts, fixed before the first tip and stored with a SHA-256 of their exact contents. As is: the Season
                    Simulator&apos;s opening day (0.60 × last season&apos;s SRS), the model the paper evaluated. Roster-aware: the Marcel projections
                    (BPM, minutes) of the players on each team&apos;s ESPN roster at lock time, given minutes by a depth chart, blended with last
                    season&apos;s SRS by weights fitted on 2010-11 to 2025-26. Each is played out {Number(meta.runs.value).toLocaleString()} times with
                    the simulator&apos;s pre-game model, play-in and playoffs included.
                </InfoTooltip>
                <SourceBadge source={data._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="ledger" />
            </h2>
            <div className="ss-date-btns lg-tabs" role="tablist" aria-label="Forecast Ledger section">
                {TABS.map(([k, label]) => (
                    <button key={k} type="button" role="tab" aria-selected={tab === k}
                        className={`tab-btn ${tab === k ? 'tab-btn--active' : ''}`} onClick={() => set({ tab: k })}>{label}</button>
                ))}
            </div>
            {tab === 'live' ? (
                live?.error ? <p className="error-message">The live scoring couldn&apos;t load.</p>
                    : <ForecastLedgerLive data={live} form={form} set={set} />
            ) : (<>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                A forecast only counts as a test if it provably existed before the games. These are the {data.season_label} predictions as they were
                locked on {fmtUtc(data.lock.locked_at)}: win totals with 80% ranges, playoff and title odds, and a win chance for every scheduled game,
                from two models. Nothing here can change after the lock without changing the hash; the season will be scored against them as it is played.
            </p>

            <LockCard data={data} />

            <p className="rx-verdict">
                <strong>Title favourite: {title.team}{titleA.team === title.team ? ' in both forecasts' : ' (roster-aware)'}</strong> ({pct(title.p_title)} roster-aware, {num(title.mean_wins)} wins with an 80% range
                of {num(title.wins_p10, 0)}-{num(title.wins_p90, 0)}; {pct(titleA.p_title)} as is{titleA.team !== title.team ? ` for ${titleA.team}` : ''}).
                {' '}Largest disagreements: {gaps.map((r, i) => (
                    <span key={r.team}>{i ? '; ' : ''}{r.team} {signed(r.roster.prior_mean)} roster-aware against {signed(r.as_is.prior_mean)} as is</span>
                ))}. The roster-aware ratings spread wider (SD {num(sd(byTeam.map((r) => r.roster.prior_mean)), 1)} points
                against {num(sd(byTeam.map((r) => r.as_is.prior_mean)), 1)}): the as-is prior keeps {num(Number(meta.carry.value) * 100, 0)}% of last
                season&apos;s rating, while the roster-aware one rests mostly on the current roster&apos;s projections.
            </p>

            <WinsChart rows={chartRows} labelText={data.season_label} />

            <div className="ss-date-btns lg-views" role="tablist" aria-label="Forecast view">
                {VIEWS.map(([k, label]) => (
                    <button key={k} type="button" role="tab" aria-selected={form.view === k}
                        className={`tab-btn ${form.view === k ? 'tab-btn--active' : ''}`} onClick={() => set({ view: k, sort: null })}>{label}</button>
                ))}
            </div>
            {['East', 'West'].map((c) => {
                const rows = byTeam.filter((r) => r.conference === c);
                return form.view === 'both'
                    ? <BothTable key={c} conf={c} rows={rows} sort={sort} dir={form.dir} onSort={onSort} selected={form.team} onPick={onPick} labelText={data.season_label} />
                    : <FullTable key={c} conf={c} rows={rows} forecast={form.view} sort={sort} dir={form.dir} onSort={onSort} selected={form.team} onPick={onPick} labelText={data.season_label} />;
            })}
            <p className="page-subtitle lb-summary">
                Ratings are points per game better than an average team on opening day. &lt;1% and &gt;99% mean some of the
                {' '}{Number(meta.runs.value).toLocaleString()} runs went the other way; 0% and 100% mean none did. Home court {signed(Number(meta.hca_prev.value))} points (2025-26&apos;s).
            </p>

            <div className="ss-model-grid lg-grid">
                {form.team ? <RosterPanel team={form.team} season={data.season} forecasts={data.forecasts} />
                    : (
                        <div className="rp-panel">
                            <h4 className="rp-panel-title">Rosters at lock time</h4>
                            <p className="rp-panel-note" style={{ marginTop: 0 }}>
                                Click a team in either table for the roster ESPN listed when the forecasts were locked ({meta.roster_players.value} players on 30
                                teams, {meta.roster_projected.value} with a projection), the minutes the depth chart gave each player and what he adds.
                            </p>
                        </div>
                    )}
                <Hindcast meta={meta} />
            </div>

            <GamesPanel team={form.team} season={data.season} />

            <div className="rp-panel lg-rules">
                <h4 className="rp-panel-title">The rules, as locked</h4>
                <ul>
                    {rules.map(([k, v]) => (
                        <li key={k}><strong>{k.replace('rule_', '').replace('_', ' ')}.</strong> {v.value}{v.note ? <span className="lg-note"> ({v.note})</span> : null}</li>
                    ))}
                    <li><strong>minutes, the alternative.</strong> {meta.rule_minutes_alternative.note} Its hindcast RMSE: {num(Number(meta.rule_minutes_alternative.value), 3)}
                        {' '}against {num(meta.hindcast.value.srs_rmse.a, 3)} for the rule used.</li>
                </ul>
            </div>
            </>)}
        </section>
    );
}
