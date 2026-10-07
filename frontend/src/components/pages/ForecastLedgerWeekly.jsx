import React, { useEffect, useState } from 'react';
import { fetchLedgerWeekly } from '../../services/api';
import Loader from '../Loader';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import { NAVIGATE_EVENT, isPlainClick, pageHref } from '../../utils/useUrlState';
import { signed as signedNum } from '../../utils/format';

// Forecast Ledger, Weekly report tab (?page=ledger&tab=weekly&wk=<week's Sunday>): the guide's weekly page, "what the
// models said vs what happened" (GET /ledger/weekly; api/weekly_report_lib.py, the same numbers as
// scripts/weekly_report.py's docs/weekly/<date>.md). Printable: the print button hides the app around it.

const SHORT = { roster: 'Roster, nightly', as_is: 'As is, nightly', record: 'Record only', roster_pre: 'Roster, locked', as_is_pre: 'As is, locked' };
const f4 = (v) => (v == null ? '—' : Number(v).toFixed(4));
const signed = (v, d = 4) => (v == null ? '—' : signedNum(v, d));
const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`);
const ci = (lo, hi, d = 4) => (lo == null ? '' : ` [${Number(lo).toFixed(d)}, ${Number(hi).toFixed(d)}]`);
const pv = (p) => (p == null ? '—' : p < 0.001 ? '< 0.001' : p.toFixed(3));
const fmtDate = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—');
const fmtShort = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '—');

function Scores({ rows, title, name }) {
    if (!rows.length) return <p className="rp-panel-note">No game scored in this span.</p>;
    return (
        <>
            <h4 className="rp-panel-title">{title}</h4>
            <TableExport name={name} />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-model-table">
                    <thead>
                        <tr>
                            <th>Version</th><th className="lb-num">Games</th>
                            <th className="lb-num">Brier [95% interval]</th><th className="lb-num">Log loss [95% interval]</th>
                            <th className="lb-num">Favourite won</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.version}>
                                <td>{r.label}</td>
                                <td className="lb-num">{r.n}</td>
                                <td className="lb-num">{f4(r.brier)}<span className="lg-note">{ci(r.brier_lo, r.brier_hi)}</span></td>
                                <td className="lb-num">{f4(r.log_loss)}<span className="lg-note">{ci(r.log_loss_lo, r.log_loss_hi)}</span></td>
                                <td className="lb-num">{pct(r.favourite_won)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

function Tests({ lg, minGames }) {
    const all = lg.tests.filter((t) => t.variant === 'all');
    if (!all.length) {
        return (
            <p className="rp-panel-note">
                No paired test yet: the ledger stores them from {minGames} games scored under every version ({lg.common_season} by the end of this week).
            </p>
        );
    }
    const early = lg.tests.find((t) => t.variant === 'logged_before_tip');
    return (
        <>
            <h4 className="rp-panel-title">Paired tests stored by the run of {fmtDate(lg.tests_as_of)} (A minus B: negative = A had the lower error)</h4>
            <TableExport name={`weekly report paired tests ${lg.tests_as_of}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-model-table">
                    <thead>
                        <tr><th>A − B</th><th>Metric</th><th className="lb-num">Games</th><th className="lb-num">Difference [95% interval]</th><th className="lb-num">p (bootstrap)</th></tr>
                    </thead>
                    <tbody>
                        {all.map((t) => (
                            <tr key={`${t.model_a}-${t.model_b}-${t.metric}`}>
                                <td>{SHORT[t.model_a]} − {SHORT[t.model_b]}{t.headline ? <strong className="lg-note"> headline</strong> : null}</td>
                                <td>{t.metric === 'log_loss' ? 'Log loss' : 'Brier'}</td>
                                <td className="lb-num">{t.n}</td>
                                <td className="lb-num">{signed(t.diff, 5)}<span className="lg-note">{ci(t.ci_lo, t.ci_hi, 5)}</span></td>
                                <td className="lb-num">{pv(t.p_boot)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            {early ? <p className="rp-panel-note">Only the games whose odds were all logged before tip-off: {early.n} games; the same tests are stored for them (Live scoring tab).</p> : null}
        </>
    );
}

function Misses({ rows }) {
    if (!rows.length) return null;
    const wc = (m, v) => (m[v] == null ? '—' : pct(m.winner === m.home ? m[v] : 1 - m[v]));
    return (
        <>
            <h4 className="rp-panel-title">Biggest misses this week: the nightly roster-aware odds, by log loss</h4>
            <TableExport name="weekly report biggest misses" />
            <div className="table-wrapper">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Date</th><th>Game</th><th className="lb-num">Final</th>
                            <th className="lb-num">Winner&apos;s chance: roster, nightly</th><th className="lb-num">As is</th>
                            <th className="lb-num">Record only</th><th className="lb-num">Roster, locked</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((m) => (
                            <tr key={m.espn_id}>
                                <td>{fmtShort(m.date)}</td>
                                <td>{m.away} at {m.home}{m.before_tip ? null : <span className="lg-note"> recomputed</span>}</td>
                                <td className="lb-num">{m.pts_away}-{m.pts_home}</td>
                                <td className="lb-num">{pct(m.winner_chance_roster)}</td>
                                <td className="lb-num">{wc(m, 'as_is')}</td>
                                <td className="lb-num">{wc(m, 'record')}</td>
                                <td className="lb-num">{wc(m, 'roster_pre')}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

function Standings({ st, label }) {
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">Standings against the locked forecast</h4>
            <p className="rp-panel-note" style={{ marginTop: 0 }}>
                Expected final wins on the morning of {fmtDate(st.as_of)} (record so far plus each remaining game&apos;s chance), against the locked
                opening-day mean and 80% range; the week&apos;s change is against {st.was_as_of ? fmtDate(st.was_as_of) : 'opening day'}. * = outside the locked 80% range.
            </p>
            {['East', 'West'].map((conf) => (
                <React.Fragment key={conf}>
                    <TableExport name={`weekly report standings ${conf} ${label}`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table">
                            <caption className="lg-caption">{conf}</caption>
                            <thead>
                                <tr>
                                    <th>Team</th><th className="lb-num">W-L</th><th className="lb-num">Roster: now</th>
                                    <th className="lb-num">Locked (80%)</th><th className="lb-num">Week</th>
                                    <th className="lb-num">As is: now</th><th className="lb-num">Locked</th>
                                </tr>
                            </thead>
                            <tbody>
                                {st.teams.filter((t) => t.conference === conf).map((t) => (
                                    <tr key={t.team}>
                                        <td><span className="ss-team"><TeamLink abbr={t.team} /></span></td>
                                        <td className="lb-num">{t.wins}-{t.losses}</td>
                                        <td className="lb-num">{t.roster.exp_final_wins == null ? '—' : t.roster.exp_final_wins.toFixed(1)}{t.roster.outside_range ? '*' : ''}</td>
                                        <td className="lb-num">{t.roster.locked_mean.toFixed(1)} <span className="lg-note">({t.roster.locked_p10.toFixed(0)}-{t.roster.locked_p90.toFixed(0)})</span></td>
                                        <td className="lb-num">{t.roster.week_change == null ? '—' : signed(t.roster.week_change, 1)}</td>
                                        <td className="lb-num">{t.as_is.exp_final_wins == null ? '—' : t.as_is.exp_final_wins.toFixed(1)}{t.as_is.outside_range ? '*' : ''}</td>
                                        <td className="lb-num">{t.as_is.locked_mean.toFixed(1)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </React.Fragment>
            ))}
        </div>
    );
}

// Game Replay lives on Analytics (#replay), as the Dashboard's "This week" links it
function ReplayLink({ g }) {
    const params = { game: g.replay_id };
    if (g.peak_t != null && g.peak_event_id != null) { params.t = g.peak_t; params.ev = g.peak_event_id; }
    const href = `${pageHref('analytics', params)}#replay`;
    const go = (e) => {
        if (!isPlainClick(e)) return;
        e.preventDefault();
        window.history.pushState(null, '', href);
        window.dispatchEvent(new Event(NAVIGATE_EVENT));
    };
    return <a className="lg-noprint" href={href} onClick={go}>Replay</a>;
}

function Notable({ nt }) {
    const bg = nt.best_game;
    const up = nt.biggest_upset;
    return (
        <div className="rp-panel">
            <h4 className="rp-panel-title">Notable this week</h4>
            <ul className="lg-weekly-list">
                {nt.movers.length ? (
                    <li>Biggest moves in expected final wins (roster-aware): {nt.movers.map((m, i) => (
                        <span key={m.team}>{i ? '; ' : ''}{m.team} {signed(m.change, 1)} to {m.exp_final_wins.toFixed(1)} ({m.wins}-{m.losses})</span>
                    ))}.</li>
                ) : null}
                {bg ? (
                    <li>Best game by excitement: {bg.away} at {bg.home}, {bg.pts_away}-{bg.pts_home}{bg.periods > 4 ? ' (OT)' : ''} on {fmtShort(bg.date)},
                        excitement {bg.excitement.toFixed(2)}, {bg.lead_changes} lead changes.{bg.replay_id ? <> <ReplayLink g={bg} /></> : null}</li>
                ) : null}
                {up ? (
                    <li>Biggest upset by the app&apos;s held-out pre-game odds: {up.winner} beat {up.loser} ({up.pts_away}-{up.pts_home}) on {fmtShort(up.date)} with
                        a {pct(up.winner_chance)} chance.{up.replay_id ? <> <ReplayLink g={up} /></> : null}</li>
                ) : null}
                {!bg && !up ? <li>The app&apos;s own game tables (Best Games, held-out pre-game odds) have no game of this week yet: they come from the daily update&apos;s season rebuild and models.</li> : null}
            </ul>
            {nt.tracker.length ? (
                <>
                    <h4 className="rp-panel-title">Rating Tracker, top {nt.tracker.length} so far (points per 100 possessions, 95% interval)</h4>
                    <TableExport name="weekly report rating tracker top" />
                    <div className="table-wrapper">
                        <table className="data-table lb-table">
                            <thead><tr><th>Player</th><th>Team</th><th className="lb-num">Games</th><th className="lb-num">Rating [95% interval]</th></tr></thead>
                            <tbody>
                                {nt.tracker.map((p) => (
                                    <tr key={p.player_id}>
                                        <td><PlayerName playerId={p.player_id} name={p.player} size={22} /></td>
                                        <td>{p.teams}</td>
                                        <td className="lb-num">{p.games}</td>
                                        <td className="lb-num">{signed(p.rating, 1)}<span className="lg-note">{ci(p.ci_lo, p.ci_hi, 1)}</span></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="rp-panel-note">Early-season ratings lean on last season&apos;s (the tracker carries each rating over); intervals narrow as games come in.</p>
                </>
            ) : null}
        </div>
    );
}

export default function ForecastLedgerWeekly({ week, onWeek }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');

    useEffect(() => {
        let alive = true;
        fetchLedgerWeekly(week)
            .then((d) => { if (alive) { setData(d); setError(''); } })
            .catch((e) => { if (alive) setError(e.response?.data?.detail || 'The weekly report couldn\'t load.'); });
        return () => { alive = false; };
    }, [week]);

    if (error) return <p className="error-message">{error}</p>;
    if (!data) return <Loader />;
    const rep = data.report;
    const lg = rep.ledger;
    const started = rep.games_season > 0;
    const weeks = data.weeks;
    const idx = weeks.indexOf(rep.end);

    return (
        <div className="lg-weekly">
            <div className="lg-weekly-bar lg-noprint">
                <label>
                    <span>Week</span>
                    <select className="input-field" value={idx >= 0 ? rep.end : ''} onChange={(e) => onWeek(e.target.value || null)} disabled={!weeks.length}>
                        {idx < 0 ? <option value="">{fmtShort(rep.start)} to {fmtShort(rep.end)}</option> : null}
                        {[...weeks].reverse().map((w) => {
                            const s = new Date(`${w}T12:00:00`);
                            s.setDate(s.getDate() - 6);
                            return <option key={w} value={w}>{fmtShort(s.toISOString().slice(0, 10))} to {fmtShort(w)}</option>;
                        })}
                    </select>
                </label>
                <button type="button" className="tab-btn" onClick={() => window.print()}>Print this report</button>
            </div>
            <div className="lg-weekly-head">
                <h3 className="lg-weekly-title">Weekly report: {rep.label}, {fmtDate(rep.start)} to {fmtDate(rep.end)} <span className="lg-noprint"><SourceBadge source={data._source} /></span></h3>
                <p className="lg-weekly-lede">
                    What the models said against what happened. {started
                        ? <><strong>{rep.games_week} games this week, {rep.games_season} so far</strong> (finals through {fmtDate(rep.last_game)}, US Eastern dates). </>
                        : weeks.length ? 'No game of the season had been played by the end of this week, so there is nothing to score. '
                            : 'No game of the season has been scored yet: this is opening week, and its report fills in from the morning after opening night. '}
                    The forecasts were locked on {rep.lock ? fmtDate(rep.lock.locked_at) : '—'}; each game&apos;s odds are logged on the morning of its date
                    by the code at the lock&apos;s git tag. The same page is written to docs/weekly/{rep.end}.md by scripts/weekly_report.py.
                </p>
                {rep.last_run?.waiting ? <p className="rp-panel-note">The last ledger run of the week was waiting: {rep.last_run.waiting}.</p> : null}
                {lg?.recomputed_week ? <p className="rp-panel-note">{lg.recomputed_week} of this week&apos;s games have odds logged after tip-off (a missed run, recomputed by the same rule).</p> : null}
            </div>
            {started ? (
                <>
                    <div className="rp-panel">
                        <p className="rp-panel-note" style={{ marginTop: 0 }}>
                            Lower is better for both scores; a coin flip scores Brier 0.25 and log loss 0.693. Games scored under all five versions only
                            (season {lg.common_season}, this week {lg.common_week}); intervals resample games ({rep.resamples.toLocaleString()} times).
                        </p>
                        <Scores rows={lg.season} title="How the forecasts are doing: season so far" name={`weekly report scores season ${rep.end}`} />
                        <Scores rows={lg.week} title="This week" name={`weekly report scores week ${rep.end}`} />
                        <Tests lg={lg} minGames={rep.min_test_games} />
                        <Misses rows={lg.misses} />
                    </div>
                    <Standings st={rep.standings} label={rep.end} />
                    <Notable nt={rep.notable} />
                </>
            ) : null}
        </div>
    );
}
