import React, { useEffect, useRef, useState } from 'react';
import { fetchAvailabilityGame } from '../../services/api';
import Loader from '../Loader';
import ChartExport from './ChartExport';
import PlayerName from './PlayerName';
import TableExport from './TableExport';
import '../../styles/availability.css';

// Availability-aware pre-game odds on the Season Simulator page
// (GET /pregame/availability/*, scripts/build_pregame_availability.py):
// AvailabilityWhatIf takes rotation players out of a game or puts the ones
// who sat back in; AvailabilitySection shows what knowing who played is
// worth under the paper's protocol, and the biggest upsets with and
// without it.

const pct = (v, d = 0) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const signed = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}`);
const pts = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v * 100).toFixed(d)}`);
const fmtDate = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const PHASE = { tune: 'Tune (2020-21 to 2023-24)', validate: 'Validate (2024-25)', test: 'Test (2025-26, scored once)' };
const pText = (p) => (p == null ? '—' : p < 0.001 ? '<0.001' : p.toFixed(3));

function Diff({ t, d = 4 }) {
    if (!t) return '—';
    const good = t.ci_hi < 0;
    return (
        <span className={good ? 'av-good' : t.ci_lo > 0 ? 'av-bad' : ''}>
            {signed(t.diff, d)} <span className="av-ci av-ci--block">[{signed(t.ci_lo, d)}, {signed(t.ci_hi, d)}]</span>
        </span>
    );
}

// Home win chance on a 0-100% line: before lineups, with who played, the user's lineup (with 80% ranges).
function OddsStrip({ data, name }) {
    const svgRef = useRef(null);
    const W = 900;
    const H = 150;
    const pad = { l: 16, r: 16 };
    const x = (p) => pad.l + p * (W - pad.l - pad.r);
    const g = data.game;
    const rows = [
        { key: 'base', label: 'Before lineups', p: g.p_base, cls: 'av-mark--base' },
        { key: 'actual', label: 'With who played', p: data.actual.p, lo: data.actual.p10, hi: data.actual.p90, cls: 'av-mark--actual' },
    ];
    if (data.whatif.changed) rows.push({ key: 'whatif', label: 'Your lineup', p: data.whatif.p, lo: data.whatif.p10, hi: data.whatif.p90, cls: 'av-mark--whatif' });
    const y = (i) => 18 + i * 30;
    return (
        <div className="av-chart">
            <ChartExport svgRef={svgRef} name={name} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                aria-label={`${g.home} win chance: ${rows.map((r) => `${r.label.toLowerCase()} ${pct(r.p, 1)}`).join(', ')}`}>
                {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                    <g key={t}>
                        <line className={t === 0.5 ? 'av-mid' : 'rx-grid'} x1={x(t)} x2={x(t)} y1={4} y2={H - 38} />
                        <text className="rx-tick" x={x(t)} y={H - 22} textAnchor={t === 0 ? 'start' : t === 1 ? 'end' : 'middle'}>{(t * 100).toFixed(0)}%</text>
                    </g>
                ))}
                {rows.map((r, i) => (
                    <g key={r.key}>
                        {r.lo != null && <line className={`av-range ${r.cls}`} x1={x(r.lo)} x2={x(r.hi)} y1={y(i)} y2={y(i)} />}
                        <circle className={`av-mark ${r.cls}`} cx={x(r.p)} cy={y(i)} r={6} />
                        <text className="ss-label" x={r.p > 0.62 ? x(r.p) - 12 : x(r.p) + 12} y={y(i) + 4}
                            textAnchor={r.p > 0.62 ? 'end' : 'start'}>{r.label} {pct(r.p, 1)}</text>
                    </g>
                ))}
                <text className="rx-tick" x={W / 2} y={H - 4} textAnchor="middle">{g.home} win chance (dot; bar = 80% range)</text>
            </svg>
        </div>
    );
}

function TeamTable({ team, rows, home, onToggle, season }) {
    const minutes = rows.filter((r) => r.in_lineup).reduce((a, r) => a + r.exp_min, 0);
    return (
        <div className="av-team">
            <h4 className="rp-panel-title">{team} {home ? '(home)' : '(away)'}: {rows.filter((r) => r.in_lineup).length} rotation players in, {num(minutes, 0)} of 240 minutes</h4>
            <TableExport name={`what-if lineup ${team} ${seasonLabel(season)}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table av-table">
                    <thead>
                        <tr>
                            <th title="Tick to put a player in this lineup, untick to take him out">In</th>
                            <th>Player</th>
                            <th className="lb-num" title="His average minutes in his earlier games this season (what the model expects, not what he played; the tag shows what he played)">Exp. min</th>
                            <th className="lb-num" title="Rating fixed before the season, points per 100 possessions">Rating</th>
                            <th className="lb-num" title="Home win chance with just this player switched in or out (everything else as it was)">If switched</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.player_id} className={r.in_lineup ? '' : 'av-row--out'}>
                                <td>
                                    <input type="checkbox" checked={r.in_lineup} onChange={() => onToggle(r)}
                                        aria-label={`${r.in_lineup ? 'Take out' : 'Put in'} ${r.player_name}`} />
                                </td>
                                <td>
                                    <PlayerName playerId={r.player_id} name={r.player_name} size={22} />
                                    <span className={`av-tag ${r.played ? 'av-tag--played' : 'av-tag--sat'}`}>{r.played ? `${r.minutes < 1 ? '<1' : num(r.minutes, 0)} min` : 'sat'}</span>
                                </td>
                                <td className="lb-num">{num(r.exp_min)}</td>
                                <td className="lb-num">{signed(r.r)}{!r.rated && <span className="av-unrated" title="No rating on file: replacement level">*</span>}</td>
                                <td className="lb-num">{pct(r.p_if_toggled, 1)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

export function AvailabilityWhatIf({ gameId, out, add, onChange, onClose }) {
    const [res, setRes] = useState(null);
    const key = `${gameId}|${out.join(',')}|${add.join(',')}`;
    useEffect(() => {
        let active = true;
        fetchAvailabilityGame(gameId, { out, add })
            .then((d) => { if (active) setRes({ key, data: d }); })
            .catch((e) => { if (active) setRes({ key, error: e.response?.data?.detail || 'The what-if couldn\'t load. Is the impact API (port 8002) running, and has scripts/build_pregame_availability.py been run?' }); });
        return () => { active = false; };
    }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
    const stale = res && res.key !== key;
    const data = res?.data;
    if (!res) return <div className="av-panel"><Loader /></div>;
    if (res.error && !stale) return <div className="av-panel"><p className="error-message">{res.error}</p></div>;
    if (!data) return <div className="av-panel"><Loader /></div>;
    const g = data.game;
    const head = (
        <div className="av-head">
            <h3 className="rp-panel-title">
                What-if: {g.away} @ {g.home}, {fmtDate(g.game_date)}
                <span className="av-result"> (final {g.away} {g.pts_away} – {g.pts_home} {g.home})</span>
            </h3>
            <button type="button" className="tab-btn" onClick={onClose}>Close</button>
        </div>
    );
    if (!data.available) return <div className="av-panel">{head}<p className="rp-panel-note">{data.reason}</p></div>;
    const toggle = (r) => {
        const id = r.player_id;
        if (r.played) onChange({ out: out.includes(id) ? out.filter((x) => x !== id) : [...out, id].sort((a, b) => a - b), add });
        else onChange({ out, add: add.includes(id) ? add.filter((x) => x !== id) : [...add, id].sort((a, b) => a - b) });
    };
    const side = (home) => data.roster.filter((r) => r.home === home);
    const w = data.whatif;
    const move = w.p - data.actual.p;
    return (
        <div className={`av-panel ${stale ? 'lb-results--stale' : ''}`} aria-busy={stale}>
            {head}
            <p className="rx-verdict">
                <strong>{g.home} win chance: {pct(g.p_base, 1)} before lineups, {pct(data.actual.p, 1)} with who actually played
                    {w.changed ? <>, {pct(w.p, 1)} with your lineup ({pts(move)} points; 80% range {pct(w.p10, 0)}-{pct(w.p90, 0)})</> : ''}.</strong>{' '}
                {g.home_won ? `${g.home} won.` : `${g.away} won.`} Untick a player who played to take him out; tick a player who sat to put him back.
                Minutes are each player&apos;s average in his earlier games; minutes your lineup doesn&apos;t cover go to a replacement-level
                player ({signed(data.fill)}), and a lineup with more than 240 has everyone&apos;s share scaled down.
            </p>
            <OddsStrip data={data} name={`what-if odds ${g.away} at ${g.home} ${g.game_date}`} />
            {w.changed && <p className="rp-panel-note"><button type="button" className="pp-link rp-link" onClick={() => onChange({ out: [], add: [] })}>Reset to who actually played</button></p>}
            <div className="av-teams">
                {[false, true].map((home) => (
                    <TeamTable key={home ? 'h' : 'a'} team={home ? g.home : g.away} home={home} rows={side(home)} onToggle={toggle} season={g.season} />
                ))}
            </div>
            <p className="rp-panel-note">
                Rotation players only (expected to play 10+ minutes). &quot;Sat&quot; = played for the team earlier this season and not for another
                team since, so long injuries are listed every game. Ratings: {data.source_label}; * = no rating on file (rookies, short careers),
                so replacement level. One point of lineup strength (per 100 possessions) moves the log-odds by {num(data.b, 3)} ± {num(data.b_se, 3)}
                (this season&apos;s coefficient, fitted on the other seasons). {data.range_note} {data.caveat}
            </p>
        </div>
    );
}

export function AvailabilitySection({ model, marginBeta, onOpenGame }) {
    const chosen = model.chosen;
    const plat = model.platform[chosen];
    const ph = model.phases;
    const test = ph.find((r) => r.phase === 'test');
    const tt = test?.[`${chosen}_log_loss_test`];
    const label = { bpm: 'BPM projection', rapm: 'Last season\'s RAPM' };
    const ckComplete = model.checks[`log_loss_complete_lineups|${chosen}`];
    const ckShort = model.checks[`log_loss_unidentified_lineups|${chosen}`];
    return (
        <div className="ss-section">
            <h3 className="rp-panel-title">What is knowing who played worth? Every game 2020-21 on, scored like every other model</h3>
            <p className="rx-verdict">
                <strong>On the test season ({tt?.seasons}), adding who played to the pre-game model lowers log loss from {num(test?.base_log_loss, 4)} to {num(test?.[`${chosen}_log_loss`], 4)} ({signed(tt?.diff, 4)}, 95% interval {signed(tt?.ci_lo, 4)} to {signed(tt?.ci_hi, 4)}, p {pText(tt?.p_boot)}).</strong>{' '}
                The gain is about the same in all three phases and with either rating source. It is an upper bound: who played is known at
                tip-off (injury reports, the inactive list), not when a forecast is usually made, and late scratches count as playing.
                {marginBeta ? <> One point of lineup strength is worth {num(plat.b_all / marginBeta, 2)} points of expected margin to the model (coefficient {num(plat.b_all, 3)} against the margin&apos;s {num(marginBeta, 3)}).</> : null}
            </p>
            <div className="table-wrapper">
                <TableExport name="availability odds by phase" />
                <table className="data-table lb-table ss-model-table av-wrap">
                    <thead>
                        <tr>
                            <th>Phase</th><th className="lb-num">Games</th><th className="lb-num">Log loss, before lineups</th>
                            <th className="lb-num">With who played ({label.bpm})</th><th>Change [95% interval]</th>
                            <th className="lb-num">With who played ({label.rapm})</th><th>Change [95% interval]</th>
                            <th className="lb-num" title="Brier score change with the chosen source">Brier change ({chosen.toUpperCase()})</th>
                        </tr>
                    </thead>
                    <tbody>
                        {ph.map((r) => (
                            <tr key={r.phase} className={r.phase === 'test' ? 'ss-chosen' : ''}>
                                <td>{PHASE[r.phase]}</td>
                                <td className="lb-num">{r.n.toLocaleString()}</td>
                                <td className="lb-num">{num(r.base_log_loss, 4)}</td>
                                <td className="lb-num">{num(r.bpm_log_loss, 4)}</td>
                                <td><Diff t={r.bpm_log_loss_test} /></td>
                                <td className="lb-num">{num(r.rapm_log_loss, 4)}{r.rapm_n !== r.n ? <span className="av-ci"> ({r.rapm_n.toLocaleString()} games)</span> : null}</td>
                                <td><Diff t={r.rapm_log_loss_test} /></td>
                                <td><Diff t={r[`${chosen}_brier_test`]} /></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="rp-panel-note">
                Round 5&apos;s protocol: every choice on 2020-21 to 2023-24 (the lineup coefficient leave-one-season-out there), the rating source picked on
                2024-25 ({Object.entries(model.choice.candidates).map(([k, v]) => `${k.toUpperCase()} ${num(v, 4)}`).join(' vs ')}: {chosen.toUpperCase()}), 2025-26 scored once.
                &quot;Before lineups&quot; is the pre-game model as the paper scored it; it is an offset, never refitted, so the change is the lineup term alone. Intervals:
                paired bootstrap by game (paper_tests). RAPM needs a season before, so it starts in 2021-22. BPM vs RAPM on the test season: {signed(test?.bpm_vs_rapm?.diff, 4)} [{signed(test?.bpm_vs_rapm?.ci_lo, 4)}, {signed(test?.bpm_vs_rapm?.ci_hi, 4)}], no real difference.
            </p>
            <div className="ss-model-grid">
                <div className="rp-panel">
                    <h4 className="rp-panel-title">Where it helps (app odds, each season held out; {chosen.toUpperCase()})</h4>
                    <TableExport name="availability odds by bucket" />
                    <div className="table-wrapper">
                        <table className="data-table lb-table ss-model-table av-wrap">
                            <thead><tr><th>Games</th><th className="lb-num">n</th><th className="lb-num">Log loss before</th><th className="lb-num">With who played</th><th className="lb-num">Mean move</th></tr></thead>
                            <tbody>
                                {model.buckets.games_played.map((b) => (
                                    <tr key={`g${b.label}`}><td>{b.label} games played (fewer of the two)</td><td className="lb-num">{b.n.toLocaleString()}</td><td className="lb-num">{num(b.base, 4)}</td><td className="lb-num">{num(b.log_loss, 4)}</td><td className="lb-num">—</td></tr>
                                ))}
                                {model.buckets.abs_avail.map((b) => (
                                    <tr key={`a${b.label}`}><td>Lineup change {b.label} (per 100 poss.)</td><td className="lb-num">{b.n.toLocaleString()}</td><td className="lb-num">{num(b.base, 4)}</td><td className="lb-num">{num(b.log_loss, 4)}</td><td className="lb-num">{pts(b.mean_move)}</td></tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="rp-panel-note">
                        Lineup change = the home side&apos;s lineup strength against what its rating already knew, minus the away side&apos;s (points per 100 possessions); mean move in
                        percentage points of win chance. Early in a season the rating is mostly last season&apos;s, so roster changes are news too.
                    </p>
                </div>
                <div className="rp-panel">
                    <h4 className="rp-panel-title">Checks</h4>
                    <ul className="av-checks">
                        <li>Every player who appeared instead of rotation players only: {Object.entries(test?.all_appearances_vs_rotation ?? {}).map(([k, t]) => `${k.toUpperCase()} ${signed(t.diff, 4)} [${signed(t.ci_lo, 4)}, ${signed(t.ci_hi, 4)}]`).join(', ')} on the test season. Deep-bench players enter blowouts, so &quot;appeared&quot; knows something about the score; it adds nothing here, and the rotation-only version is the one used.</li>
                        {ckComplete && ckShort && (
                            <li>ESPN gives some players no id before 2025-26 (they look absent). Log loss change on games where every player is identified: {signed(ckComplete.value - ckComplete.base, 4)} ({ckComplete.n.toLocaleString()} games); where some aren&apos;t: {signed(ckShort.value - ckShort.base, 4)} ({ckShort.n.toLocaleString()}), because call-ups without ids play when regulars sit. 2025-26, with every player identified, gains as much as any season.</li>
                        )}
                        <li>Coefficient by held-out season: {plat.by_season.map((s) => `${seasonLabel(s.season)} ${num(s.b, 3)}`).join(', ')} (± {num(plat.by_season[0]?.se, 3)}): stable.</li>
                        <li>Constants from the tune seasons ({model.constants_from}): unrated players at {signed(model.constants[`replacement_${chosen}`], 2)} (their own same-season rating, minutes-weighted), {pct(model.constants[`unrated_minute_share_${chosen}`], 0)} of minutes; a player with no history expected to play {num(model.constants.default_minutes, 1)} minutes.</li>
                    </ul>
                </div>
            </div>
            <h4 className="rp-panel-title">The biggest upsets since 2020-21, before and with who played</h4>
            <TableExport name="biggest upsets with lineups" />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-model-table av-wrap">
                    <thead><tr><th>Date</th><th>Game</th><th>Winner</th><th className="lb-num">Winner&apos;s chance before</th><th className="lb-num">With who played</th><th>Winner sat</th><th>Loser sat</th><th /></tr></thead>
                    <tbody>
                        {model.upsets.map((u) => (
                            <tr key={u.game_id}>
                                <td>{fmtDate(u.game_date)}</td>
                                <td>{u.away} @ {u.home}</td>
                                <td>{u.winner}</td>
                                <td className="lb-num">{pct(u.base, 1)}</td>
                                <td className={`lb-num ${u.with_lineups > u.base ? 'av-good' : 'av-bad'}`}>{pct(u.with_lineups, 1)}</td>
                                <td className="lk-sub">{u.sat_winner.slice(0, 3).join(', ') || '—'}{u.sat_winner.length > 3 ? ` +${u.sat_winner.length - 3}` : ''}</td>
                                <td className="lk-sub">{u.sat_loser.slice(0, 3).join(', ') || '—'}{u.sat_loser.length > 3 ? ` +${u.sat_loser.length - 3}` : ''}</td>
                                <td>{onOpenGame && <button type="button" className="pp-link rp-link" onClick={() => onOpenGame(u)}>What-if →</button>}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="rp-panel-note">
                Green: knowing who played made the upset less of a surprise. &quot;Sat&quot; lists rotation players by expected minutes, long injuries included.
            </p>
        </div>
    );
}
