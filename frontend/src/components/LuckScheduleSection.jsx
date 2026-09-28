import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchLuckSchedule, fetchLuckScheduleModel, fetchLuckScheduleTeam } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import TableExport from './common/TableExport';
import ChartExport from './common/ChartExport';
import CopyLinkButton from './common/CopyLinkButton';
import SaveViewButton from './common/SaveViewButton';
import TeamLogo from './common/TeamLogo';
import TeamLink from './common/TeamLink';
import { currentPageParam, parseParam, useInitialParams, useUrlSync } from '../utils/useUrlState';
import '../styles/luck.css';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const signed = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}`);
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const tone = (v, eps = 0.05) => (v == null || Math.abs(v) < eps ? '' : v > 0 ? 'lk-pos' : 'lk-neg');
const wl = (w, l) => `${w}-${l}`;
const fmtDate = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
// Old abbreviations for the same franchise, for the logo only (the label keeps the real one).
const LOGO = { NOH: 'NOP', NJN: 'BKN' };
const franchiseOf = (t) => (t ? LOGO[t] ?? t : null);
const URL_KEYS = ['season', 'asof', 'team', 'sort', 'dir'];
const LUCK_RANGE = 10; // wins from zero to the bar's edge

const FULL_COLS = {
    team_abbreviation: { label: 'Team', text: true },
    wins: { label: 'W-L', title: 'Actual record' },
    exp_wins: { label: 'Exp. W', title: 'Wins a team with these points for and against usually gets (Pythagorean, exponent fitted)' },
    luck: { label: 'Luck', title: 'Actual wins minus expected wins' },
    close3: { label: '≤3 pts', title: 'Record in games decided by 3 or fewer points' },
    close5: { label: '≤5 pts', title: 'Record in games decided by 5 or fewer points' },
    ot: { label: 'OT', title: 'Record in overtime games' },
    mov: { label: 'Margin', title: 'Average point margin per game' },
    srs: { label: 'SRS', title: 'Rating: points per game better than an average team on a neutral floor, adjusted for opponents' },
    sos: { label: 'SOS', title: 'Average SRS of the opponents it played' },
};
const ASOF_COLS = {
    team_abbreviation: { label: 'Team', text: true },
    wins: { label: 'W-L', title: 'Record before that date' },
    exp_wins: { label: 'Exp. W', title: 'Expected wins so far, from points' },
    luck: { label: 'Luck', title: 'Wins so far minus expected wins so far' },
    srs: { label: 'SRS', title: 'Rating from the games played so far' },
    sos: { label: 'SOS', title: 'Average rating of the opponents played so far' },
    rem_games: { label: 'Left', title: 'Games left (home-away)' },
    rem_sos: { label: 'SOS left', title: 'Average current rating of the opponents still to play (rank 1 = hardest)' },
    proj_wins: { label: 'Proj. W', title: 'Wins so far plus the summed win chances of the games left' },
    final_wins: { label: 'Final W', title: 'What actually happened' },
};

function sortValue(r, key) {
    if (key === 'close3') return (r.close3_w + r.close3_l) ? r.close3_w / (r.close3_w + r.close3_l) : null;
    if (key === 'close5') return (r.close5_w + r.close5_l) ? r.close5_w / (r.close5_w + r.close5_l) : null;
    if (key === 'ot') return (r.ot_w + r.ot_l) ? r.ot_w / (r.ot_w + r.ot_l) : null;
    if (key === 'wins') return r.win_pct;
    return r[key];
}

function sortRows(rows, sort, dir, text) {
    const sign = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
        const va = sortValue(a, sort);
        const vb = sortValue(b, sort);
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return text ? sign * String(va).localeCompare(String(vb)) : sign * (va - vb);
    });
}

function formFromParams(p) {
    const asof = parseParam.str(p, 'asof');
    const all = { ...FULL_COLS, ...ASOF_COLS };
    return {
        season: parseParam.int(p, 'season', { min: 2000, max: 2100 }),
        asof: asof && /^\d{4}-\d{2}-\d{2}$/.test(asof) ? asof : null,
        team: parseParam.str(p, 'team')?.toUpperCase() ?? null,
        sort: parseParam.oneOf(p, 'sort', Object.keys(all)) ?? 'srs',
        dir: parseParam.oneOf(p, 'dir', ['asc', 'desc']) ?? 'desc',
    };
}

function useWidth() {
    const ref = useRef(null);
    const [W, setW] = useState(720);
    useEffect(() => {
        const el = ref.current;
        if (!el || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(([entry]) => {
            const w = Math.round(entry.contentRect.width);
            if (w > 0) setW(Math.max(280, w));
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    return [ref, W];
}

// Luck as a bar from a zero line: right = won more than the points said.
function LuckBar({ v }) {
    if (v == null) return null;
    const w = 70;
    const mid = w / 2;
    const len = Math.max(-1, Math.min(1, v / LUCK_RANGE)) * (mid - 2);
    return (
        <svg className="lk-bar" width={w} height={12} viewBox={`0 0 ${w} 12`} aria-hidden="true" data-export-skip>
            <line x1={mid} x2={mid} y1={0} y2={12} stroke="var(--line)" strokeWidth={1} />
            <rect x={len >= 0 ? mid : mid + len} y={3} width={Math.abs(len)} height={6} rx={1}
                fill={v >= 0 ? 'var(--positive)' : 'var(--negative)'} />
        </svg>
    );
}

// Every team-season since 2009-10: expected win% (x) against actual (y).
// On the diagonal = exactly as many wins as the points said; above = lucky.
function FitScatter({ model, season, team }) {
    const [ref, W] = useWidth();
    const svgRef = useRef(null);
    const narrow = W < 520;
    const H = narrow ? 300 : 360;
    const M = { l: 44, r: 12, t: 12, b: 40 };
    const sx = (v) => M.l + ((v - 0.1) / 0.8) * (W - M.l - M.r);
    const sy = (v) => H - M.b - ((v - 0.1) / 0.8) * (H - M.t - M.b);
    const fit = model.fits.find((f) => f.chosen);
    const pts = model.points;
    const inSeason = pts.filter((p) => p.season === season);
    const labelled = [...inSeason].sort((a, b) => Math.abs(b.win_pct - b.exp_win_pct) - Math.abs(a.win_pct - a.exp_win_pct))
        .slice(0, narrow ? 2 : 4);
    const ticks = [0.2, 0.4, 0.6, 0.8];
    const tip = (p) => `${p.team} ${seasonLabel(p.season)}: ${(p.win_pct * 100).toFixed(1)}% won, ${(p.exp_win_pct * 100).toFixed(1)}% expected (${p.games} games, margin ${signed(p.mov)})`;
    return (
        <div className="rx-chart lk-scatter" ref={ref}>
            <ChartExport svgRef={svgRef} name={`luck fit ${seasonLabel(season)}`} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                aria-label={`Expected against actual win% for ${pts.length} team-seasons; ${seasonLabel(season)} highlighted. Fit: Pythagorean exponent ${num(fit.param, 2)}, leave-one-season-out error ${num(fit.loso_rmse_wins)} wins.`}>
                {ticks.map((t) => (
                    <g key={t}>
                        <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <line className="rx-grid" y1={M.t} y2={H - M.b} x1={sx(t)} x2={sx(t)} />
                        <text className="rx-tick" x={M.l - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle">{t.toFixed(1)}</text>
                        <text className="rx-tick" x={sx(t)} y={H - M.b + 14} textAnchor="middle">{t.toFixed(1)}</text>
                    </g>
                ))}
                <line className="lk-diag" x1={sx(0.1)} y1={sy(0.1)} x2={sx(0.9)} y2={sy(0.9)} />
                {pts.filter((p) => p.season !== season).map((p) => (
                    <circle key={`${p.season}-${p.team}`} className="lk-dot" cx={sx(p.exp_win_pct)} cy={sy(p.win_pct)} r={2.2}>
                        <title>{tip(p)}</title>
                    </circle>
                ))}
                {inSeason.map((p) => (
                    <circle key={`s-${p.team}`} className={`lk-dot lk-dot--on ${p.team === team ? 'lk-dot--team' : ''}`}
                        cx={sx(p.exp_win_pct)} cy={sy(p.win_pct)} r={p.team === team ? 6 : 4}>
                        <title>{tip(p)}</title>
                    </circle>
                ))}
                {labelled.map((p) => (
                    <text key={`l-${p.team}`} className="lk-dot-label" x={sx(p.exp_win_pct) + 7} y={sy(p.win_pct)}
                        dominantBaseline="middle">{p.team}</text>
                ))}
                <text className="rx-axis" x={(M.l + W - M.r) / 2} y={H - 4} textAnchor="middle">Expected win% from points</text>
                <text className="rx-axis" transform={`translate(12 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle">Actual win%</text>
            </svg>
            <p className="lk-key">
                <span className="lk-key-item"><span className="lk-swatch lk-swatch--on" />{seasonLabel(season)}</span>
                <span className="lk-key-item"><span className="lk-swatch" />Every other team-season, {seasonLabel(model.seasons[0].season)} on ({pts.length})</span>
                <span className="lk-key-item">Line: as many wins as the points said. Above it = luckier.</span>
            </p>
        </div>
    );
}

// One franchise's luck in every season, as bars from zero.
function TeamHistory({ hist, season, onPick }) {
    const [ref, W] = useWidth();
    const svgRef = useRef(null);
    const rows = hist.seasons;
    const H = 150;
    const M = { l: 34, r: 8, t: 10, b: 26 };
    const max = Math.max(4, ...rows.map((r) => Math.abs(r.luck)));
    const step = (W - M.l - M.r) / rows.length;
    const sy = (v) => M.t + ((max - v) / (2 * max)) * (H - M.t - M.b);
    return (
        <div className="lk-history">
            <ChartExport svgRef={svgRef} name={`${hist.franchise} luck by season`} />
            <div className="rx-chart" ref={ref}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                    aria-label={`${hist.franchise} luck by season: ${rows.map((r) => `${seasonLabel(r.season)} ${signed(r.luck)}`).join(', ')}.`}>
                    {[-max, 0, max].map((t) => (
                        <g key={t}>
                            <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                            <text className="rx-tick" x={M.l - 5} y={sy(t)} textAnchor="end" dominantBaseline="middle">{signed(t, 0)}</text>
                        </g>
                    ))}
                    {rows.map((r, i) => {
                        const x = M.l + i * step + step * 0.15;
                        const y0 = sy(0);
                        const y1 = sy(r.luck);
                        return (
                            <g key={r.season} className="lk-hbar" onClick={() => onPick(r.season)}>
                                <rect x={M.l + i * step} y={M.t} width={step} height={H - M.t - M.b} fill="transparent" />
                                <rect x={x} y={Math.min(y0, y1)} width={step * 0.7} height={Math.max(1, Math.abs(y1 - y0))} rx={1}
                                    className={`${r.luck >= 0 ? 'lk-hbar-pos' : 'lk-hbar-neg'} ${r.season === season ? 'lk-hbar--on' : ''}`} />
                                <title>{`${r.team_abbreviation} ${seasonLabel(r.season)}: ${wl(r.wins, r.losses)}, expected ${num(r.exp_wins)}, luck ${signed(r.luck)}`}</title>
                                {(i % (W < 520 ? 4 : 2) === 0 || r.season === season) && (
                                    <text className="rx-tick" x={M.l + i * step + step / 2} y={H - 8} textAnchor="middle">{`'${String(r.season).slice(-2)}`}</text>
                                )}
                            </g>
                        );
                    })}
                </svg>
            </div>
        </div>
    );
}

function Validation({ model }) {
    const c = model.checks;
    const v = (k, d = 2) => (c[k] ? num(c[k].value, d) : '—');
    const ci = (k, d = 2) => (c[k] && c[k].lo != null ? `${num(c[k].lo, d)} to ${num(c[k].hi, d)}` : '');
    const n = (k) => (c[k]?.n != null ? c[k].n.toLocaleString() : '—');
    return (
        <div className="lk-checks">
            <h3 className="lk-h3">Does luck carry over?</h3>
            <p className="rx-verdict">
                <strong>Mostly not.</strong> A franchise&apos;s luck (wins above expected, per 82 games) correlates{' '}
                <strong>{v('luck_next_luck_r')}</strong> with its luck the next season (95% interval {ci('luck_next_luck_r')},{' '}
                {n('luck_next_luck_r')} season pairs), against {v('mov_next_mov_r')} for point margin. Record in games decided by 3
                or fewer points: {v('close3_next_close3_r')} ({ci('close3_next_close3_r')}, {n('close3_next_close3_r')} pairs with 8+ such
                games both years). The small part that does repeat is clear of zero, so it isn&apos;t pure chance: it may be late-game
                execution, or teams whose points the curve reads badly (coaches who empty the bench early in blowouts). Next
                season&apos;s win% correlates {v('next_winpct_r_record', 3)} with this season&apos;s record and{' '}
                {v('next_winpct_r_expected', 3)} with its expected win%: about the same, a smaller edge for the points than the usual
                story suggests. Holding expected win% fixed, a win of luck is worth {v('next_winpct_coef_luck')} of a win the next
                season (interval {ci('next_winpct_coef_luck')}, which includes zero).
            </p>
            <div className="table-wrapper">
                <table className="data-table lb-table lk-check-table">
                    <thead>
                        <tr><th>Check</th><th className="lb-num">Result</th><th className="lb-num">n</th></tr>
                    </thead>
                    <tbody>
                        {model.fits.map((f) => (
                            <tr key={f.method}>
                                <td>
                                    Expected wins, {f.method === 'pythagorean' ? `Pythagorean (exponent ${num(f.param, 2)})` : f.method === 'normal' ? `normal curve in margin (SD ${num(f.param, 1)})` : `straight line in margin (${num(f.param * 100, 2)} pts of win% per point)`}
                                    {f.chosen ? ' — used' : ''}
                                </td>
                                <td className="lb-num">±{num(f.loso_rmse_wins, 2)} wins</td>
                                <td className="lb-num">{f.n}</td>
                            </tr>
                        ))}
                        <tr>
                            <td>Rest-of-season wins from the halfway date: record so far</td>
                            <td className="lb-num">±{v('midseason_rmse_record')} wins</td><td className="lb-num">{n('midseason_rmse_record')}</td>
                        </tr>
                        <tr>
                            <td>… from expected win% so far</td>
                            <td className="lb-num">±{v('midseason_rmse_expected')} wins</td><td className="lb-num">{n('midseason_rmse_expected')}</td>
                        </tr>
                        <tr>
                            <td>… from ratings so far plus the schedule left (the as-of view)</td>
                            <td className="lb-num">±{v('midseason_rmse_srs_schedule')} wins</td><td className="lb-num">{n('midseason_rmse_srs_schedule')}</td>
                        </tr>
                        <tr>
                            <td>SRS against Basketball-Reference&apos;s published SRS: correlation</td>
                            <td className="lb-num">{v('bref_srs_r', 4)}</td><td className="lb-num">{n('bref_srs_r')}</td>
                        </tr>
                        <tr>
                            <td>… average gap (largest: {v('bref_srs_max_abs_diff')}, 2019-20 with the neutral-site restart)</td>
                            <td className="lb-num">{v('bref_srs_mean_abs_diff', 3)} pts</td><td className="lb-num">{n('bref_srs_mean_abs_diff')}</td>
                        </tr>
                        <tr>
                            <td>Team records differing from Basketball-Reference</td>
                            <td className="lb-num">{v('bref_wins_mismatch', 0)}</td><td className="lb-num">{n('bref_wins_mismatch')}</td>
                        </tr>
                        <tr>
                            <td>Largest gap to Basketball-Reference&apos;s margin per game (theirs is rounded to 0.01)</td>
                            <td className="lb-num">{v('bref_mov_max_abs_diff', 3)} pts</td><td className="lb-num">{n('bref_mov_max_abs_diff')}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle lk-foot">
                Errors are root-mean-square, each season predicted by a curve fitted on the other seasons (expected wins) or by that
                season&apos;s games before its halfway date only (rest of season). Correlations pair each franchise with itself the
                next season (New Jersey→Brooklyn and New Orleans Hornets→Pelicans counted as one).
            </p>
        </div>
    );
}

export default function LuckScheduleSection() {
    const params = useInitialParams();
    const [form, setForm] = useState(() => formFromParams(params));
    const [data, setData] = useState(null);
    const [model, setModel] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [dateDraft, setDateDraft] = useState(form.asof ?? '');

    const shownSeason = form.season ?? data?.season ?? null;
    useUrlSync({
        season: shownSeason, asof: form.asof, team: form.team,
        sort: form.sort === 'srs' ? null : form.sort, dir: form.dir === 'desc' ? null : form.dir,
    });

    // This tool lives inside the Analytics page; drop its inputs from the link when it closes.
    const [page] = useState(currentPageParam);
    useEffect(() => () => {
        const url = new URL(window.location.href);
        if (url.searchParams.get('page') !== page) return;
        URL_KEYS.forEach((k) => url.searchParams.delete(k));
        window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
    }, [page]);

    useEffect(() => {
        fetchLuckScheduleModel().then(setModel).catch(() => setModel(null));
    }, []);

    useEffect(() => {
        let alive = true;
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                const res = await fetchLuckSchedule({ season: form.season ?? undefined, as_of: form.asof ?? undefined });
                if (alive) setData(res);
            } catch (e) {
                if (alive) setError(e?.response?.data?.detail || 'Could not load luck & schedule data. Is the impact API (port 8002) running?');
            } finally {
                if (alive) setLoading(false);
            }
        }, 120);
        return () => { alive = false; clearTimeout(timer); };
    }, [form.season, form.asof]);

    // A franchise's history, kept with the team it belongs to so a stale one never shows.
    const [histData, setHistData] = useState(null);
    useEffect(() => {
        if (!form.team) return undefined;
        let alive = true;
        fetchLuckScheduleTeam(form.team)
            .then((res) => { if (alive) setHistData({ team: form.team, res }); })
            .catch(() => { if (alive) setHistData({ team: form.team, res: null }); });
        return () => { alive = false; };
    }, [form.team]);
    const hist = form.team && histData?.team === form.team ? histData.res : null;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const asOf = Boolean(data?.as_of);
    const cols = asOf ? ASOF_COLS : FULL_COLS;
    const onSort = (key) => setForm((f) => (
        f.sort === key ? { ...f, dir: f.dir === 'asc' ? 'desc' : 'asc' } : { ...f, sort: key, dir: cols[key].text ? 'asc' : 'desc' }
    ));
    const sortKey = cols[form.sort] ? form.sort : 'srs';
    const rows = useMemo(() => (data ? sortRows(data.teams, sortKey, form.dir, cols[sortKey]?.text) : []),
        [data, sortKey, form.dir, cols]);

    if (!data && loading) return <Loader />;
    if (!data) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;

    const season = data.season;
    const info = data.season_info;
    const seasons = [...data.seasons_available].reverse();
    const byLuck = [...data.teams].sort((a, b) => b.luck - a.luck);
    const lucky = byLuck[0];
    const unlucky = byLuck[byLuck.length - 1];
    const minDate = (() => { const d = new Date(`${info.first_date}T12:00:00`); d.setDate(d.getDate() + 1); return d.toISOString().slice(0, 10); })();
    const halfway = info.halfway_date;
    const applyDate = (d) => { setDateDraft(d); set({ asof: d || null }); };
    const pickTeam = (t) => set({ team: form.team === t ? null : t });

    const head = (k, className) => {
        const c = cols[k];
        const active = sortKey === k;
        return (
            <th key={k} className={className ?? (c.text ? undefined : 'lb-num')} title={c.title}
                aria-sort={active ? (form.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                <button type="button" className={`oo-sort ${active ? 'oo-sort--active' : ''}`} onClick={() => onSort(k)}>
                    {c.label}{active && <span aria-hidden="true">{form.dir === 'asc' ? ' ▲' : ' ▼'}</span>}
                </button>
            </th>
        );
    };
    const teamCell = (r) => (
        <td className="oo-team">
            <button type="button" className={`lk-team ${franchiseOf(form.team) === franchiseOf(r.team_abbreviation) ? 'lk-team--on' : ''}`}
                onClick={() => pickTeam(r.team_abbreviation)} title="Show this franchise's luck in every season">
                <TeamLogo abbreviation={LOGO[r.team_abbreviation] ?? r.team_abbreviation} size={20} /> {r.team_abbreviation}
            </button>
        </td>
    );
    const rec = (w, l) => (w + l ? wl(w, l) : '—');

    return (
        <section className="dashboard-card lb-card lk-card">
            <h2 className="card-title hb-page-title">
                Luck &amp; schedule: who won more than their points said
                <InfoTooltip label="How luck and SRS are computed" title="Under the hood">
                    {data.method}
                </InfoTooltip>
                <SourceBadge source={data._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="analytics" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every regular-season game since 2009-10, from real final scores. Expected wins say what a team&apos;s points for and
                against usually buy; luck is the gap. SRS rates each team against the schedule it actually played. Pick a date to
                see the standings, ratings and schedule left as they stood that morning.
            </p>

            <div className="lb-controls lk-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={season}
                        onChange={(e) => { setDateDraft(''); set({ season: Number(e.target.value), asof: null }); }}>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>As of (morning of)</span>
                    <input className="input-field" type="date" min={minDate} max={info.last_date} value={dateDraft}
                        onChange={(e) => setDateDraft(e.target.value)}
                        onBlur={() => dateDraft !== (form.asof ?? '') && applyDate(dateDraft)}
                        onKeyDown={(e) => { if (e.key === 'Enter') applyDate(dateDraft); }} />
                </label>
                <div className="lk-date-btns">
                    <button type="button" className={`tab-btn ${!asOf ? 'tab-btn--active' : ''}`} onClick={() => applyDate('')}>Full season</button>
                    <button type="button" className={`tab-btn ${form.asof === halfway ? 'tab-btn--active' : ''}`} onClick={() => applyDate(halfway)}
                        title="The date of the season's middle game (the view counts the games before it)">Halfway</button>
                </div>
            </div>

            {error && <p className="error-message">{error}</p>}

            <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                {!asOf ? (
                    <p className="rx-verdict">
                        <strong>{seasonLabel(season)}: {info.games.toLocaleString()} games{info.complete ? '' : ` of ${info.scheduled.toLocaleString()} (season not finished)`}.</strong>{' '}
                        {data.summary.luck_within_3} of {data.summary.teams} teams finished within 3 wins of what their points said;{' '}
                        {data.summary.luck_over_5} missed by more than 5. Luckiest: <strong>{lucky.team_abbreviation}</strong>{' '}
                        ({wl(lucky.wins, lucky.losses)}, <span className={tone(lucky.luck)}>{signed(lucky.luck)}</span>); unluckiest:{' '}
                        <strong>{unlucky.team_abbreviation}</strong> ({wl(unlucky.wins, unlucky.losses)},{' '}
                        <span className={tone(unlucky.luck)}>{signed(unlucky.luck)}</span>). Home court was worth{' '}
                        {signed(info.hca)} points a game this season, and a game&apos;s margin typically landed {num(info.sigma)} points
                        from what the two ratings said. {Math.round(info.close3_share * 100)}% of games were decided by 3 or fewer.
                    </p>
                ) : (
                    <p className="rx-verdict">
                        <strong>{seasonLabel(season)} as of the morning of {fmtDate(data.as_of)}: {data.as_of_info.played_games} games
                            played, {data.as_of_info.left_games} left.</strong>{' '}
                        Ratings from the games so far are pulled {Math.round((1 - data.as_of_info.shrink) * 100)}% toward average for the
                        projection (that share of their spread is still noise), then every remaining game becomes a win chance with
                        home court ({signed(data.as_of_info.hca)}) and a game-to-game spread of {num(data.as_of_info.sigma)} points.
                        Final W shows what actually happened; at the halfway date this method misses a team&apos;s rest-of-season wins
                        by about {model ? num(model.checks.midseason_rmse_srs_schedule?.value) : '4.5'} on average (root-mean-square).
                    </p>
                )}

                <TableExport name={`luck schedule ${seasonLabel(season)}${asOf ? ` as of ${data.as_of}` : ''}`} />
                <div className="table-wrapper">
                    <table className="data-table lb-table lk-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                {Object.keys(cols).map((k) => head(k, k === 'luck' ? 'lb-num lb-stat' : undefined))}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r, i) => (asOf ? (
                                <tr key={r.team_abbreviation}>
                                    <td>{i + 1}</td>
                                    {teamCell(r)}
                                    <td className="lb-num">{wl(r.wins, r.losses)}</td>
                                    <td className="lb-num">{num(r.exp_wins)}</td>
                                    <td className={`lb-num lb-stat ${tone(r.luck)}`}>{signed(r.luck)}</td>
                                    <td className={`lb-num ${tone(r.srs)}`}>{signed(r.srs, 2)}</td>
                                    <td className="lb-num">{signed(r.sos, 2)}</td>
                                    <td className="lb-num" title={`${r.rem_home} home, ${r.rem_away} away`}>{r.rem_games} <span className="lk-sub">({r.rem_home}-{r.rem_away})</span></td>
                                    <td className="lb-num">{r.rem_sos == null ? '—' : <>{signed(r.rem_sos, 2)} <span className="lk-sub">#{Math.round(r.rem_sos_rank)}</span></>}</td>
                                    <td className="lb-num lb-stat">{num(r.proj_wins)}</td>
                                    <td className="lb-num">{r.final_wins == null ? '—' : wl(r.final_wins, r.final_losses)}</td>
                                </tr>
                            ) : (
                                <tr key={r.team_abbreviation}>
                                    <td>{i + 1}</td>
                                    {teamCell(r)}
                                    <td className="lb-num">{wl(r.wins, r.losses)}</td>
                                    <td className="lb-num">{num(r.exp_wins)}</td>
                                    <td className={`lb-num lb-stat lk-luck ${tone(r.luck)}`}><LuckBar v={r.luck} />{signed(r.luck)}</td>
                                    <td className="lb-num">{rec(r.close3_w, r.close3_l)}</td>
                                    <td className="lb-num">{rec(r.close5_w, r.close5_l)}</td>
                                    <td className="lb-num">{rec(r.ot_w, r.ot_l)}</td>
                                    <td className={`lb-num ${tone(r.mov)}`}>{signed(r.mov)}</td>
                                    <td className={`lb-num ${tone(r.srs)}`}>{signed(r.srs, 2)} <span className="lk-sub">#{r.srs_rank}</span></td>
                                    <td className="lb-num">{signed(r.sos, 2)}</td>
                                </tr>
                            )))}
                        </tbody>
                    </table>
                </div>
                <p className="page-subtitle lk-foot">
                    Click a team for its luck in every season. Luck is in wins; SRS and SOS in points per game against an average team
                    on a neutral floor (SRS = margin + SOS, give or take home court). Close-game records use the final margin, so an
                    overtime game counts by its final score. With ~{Math.round(info.close3_share * 82)} games a season decided by 3 or
                    fewer, a close-game record is a small sample.
                </p>

                {hist && (
                    <div className="lk-team-block">
                        <h3 className="lk-h3">
                            <TeamLogo abbreviation={hist.franchise} size={22} /> {hist.franchise}
                            {hist.abbreviations.length > 1 ? ` (${hist.abbreviations.join(' / ')})` : ''} in every season since 2009-10
                            <TeamLink abbr={hist.seasons.find((r) => r.season === season)?.team_abbreviation ?? hist.franchise} season={season} className="lk-open"><span>Team page</span></TeamLink>
                            <button type="button" className="lk-close" onClick={() => set({ team: null })} aria-label="Close team history">×</button>
                        </h3>
                        <p className="rx-verdict">
                            Luck added up to <strong className={tone(hist.total_luck)}>{signed(hist.total_luck)}</strong> wins over{' '}
                            {hist.seasons.length} seasons. Best: {(() => { const b = [...hist.seasons].sort((a, c) => c.luck - a.luck)[0]; return `${seasonLabel(b.season)} (${signed(b.luck)})`; })()};
                            worst: {(() => { const w = [...hist.seasons].sort((a, c) => a.luck - c.luck)[0]; return `${seasonLabel(w.season)} (${signed(w.luck)})`; })()}.
                            Click a bar to open that season.
                        </p>
                        <TeamHistory hist={hist} season={season} onPick={(s) => { setDateDraft(''); set({ season: s, asof: null }); }} />
                        <TableExport name={`luck history ${hist.franchise}`} />
                        <div className="table-wrapper">
                            <table className="data-table lb-table lk-table">
                                <thead>
                                    <tr>
                                        <th>Season</th><th>Team</th><th className="lb-num">W-L</th><th className="lb-num">Exp. W</th>
                                        <th className="lb-num">Luck</th><th className="lb-num">≤3 pts</th><th className="lb-num">SRS</th>
                                        <th className="lb-num">SOS</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {[...hist.seasons].reverse().map((r) => (
                                        <tr key={r.season} className={r.season === season ? 'lk-row--on' : undefined}>
                                            <td>{seasonLabel(r.season)}</td>
                                            <td>{r.team_abbreviation}</td>
                                            <td className="lb-num">{wl(r.wins, r.losses)}</td>
                                            <td className="lb-num">{num(r.exp_wins)}</td>
                                            <td className={`lb-num lb-stat ${tone(r.luck)}`}>{signed(r.luck)}</td>
                                            <td className="lb-num">{rec(r.close3_w, r.close3_l)}</td>
                                            <td className={`lb-num ${tone(r.srs)}`}>{signed(r.srs, 2)} <span className="lk-sub">#{r.srs_rank}</span></td>
                                            <td className="lb-num">{signed(r.sos, 2)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}

                {model && (
                    <>
                        <h3 className="lk-h3">The fit: expected against actual win%</h3>
                        <FitScatter model={model} season={season} team={data.teams.find((t) => franchiseOf(t.team_abbreviation) === franchiseOf(form.team))?.team_abbreviation} />
                        <Validation model={model} />
                    </>
                )}
            </div>
        </section>
    );
}
