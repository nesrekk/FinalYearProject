import React, { useEffect, useRef, useState } from 'react';
import { fetchShotMaking, fetchShotMakingLeaderboard, fetchShotMakingModel } from '../../services/api';
import InfoTooltip from './InfoTooltip';
import PlayerName from './PlayerName';
import SourceBadge from './SourceBadge';
import TableExport from './TableExport';
import ChartExport from './ChartExport';
import { bySign, signed as signedNum } from '../../utils/format';
import '../../styles/shotmaking.css';

// Expected FG% / shot-making (GET /shots/shot-making/*, routers/shot_making.py,
// scripts/build_shot_making.py). Three pieces, reused by the Shot Charts
// "Shot-making" tab and the player profile:
//   ShotMakingPlayer      one player's seasons: chart + table (fetches by name)
//   ShotMakingTable       the seasons table alone (profile block, rows given)
//   ShotMakingLeaderboard one season's qualified players, ranked
//   ShotMakingModel       how the per-shot model was chosen and checked
// Hand-built SVG like the app's other charts.

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;
const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const pts = (v, d = 1) => (v == null ? '—' : signedNum(v * 100, d));
const signed = (v, d = 0) => signedNum(v, d);
// Colour follows the value as shown, so a cell reading 0.0 (or 0) isn't tinted.
const tone = (v) => bySign(v * 100, 1, 'smk-pos', 'smk-neg'); // eFG points, as pts() shows them
const tonePts = (v) => bySign(v, 0, 'smk-pos', 'smk-neg');    // points, as signed() shows them
const M = { l: 44, r: 10, t: 12, b: 30 };

export function ShotMakingInfo() {
    return (
        <InfoTooltip label="About shot-making" title="Expected FG% and shot-making">
            A model scores every regular-season shot since 1996-97 by how often an average shooter makes a shot like it:
            court location, distance, angle, zone, two or three, period, seconds left and season. Shot quality is the
            expected eFG% of a player&apos;s own shots; shot-making is his actual eFG% minus that, so +5.0 means five eFG
            points better than an average shooter on the same diet. Each player is scored by a model that never saw
            his shots. No shot on file has a defender distance or a shot type (catch-and-shoot vs. pull-up), so
            shot-making also carries the defence he faced and the shots he created himself.
        </InfoTooltip>
    );
}

// ── Chart: one bar per season, 95% whiskers, zero line ───────────────────
function ShotMakingChart({ rows, minFga, name, active, onHover, onPick }) {
    const boxRef = useRef(null);
    const svgRef = useRef(null);
    const [W, setW] = useState(720);
    const H = W < 520 ? 220 : 260;

    useEffect(() => {
        const el = boxRef.current;
        if (!el || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(([entry]) => {
            const w = Math.round(entry.contentRect.width);
            if (w > 0) setW(Math.max(280, w));
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    const n = rows.length;
    const band = (W - M.l - M.r) / n;
    const colW = Math.max(3, Math.min(30, band * 0.7));
    const cx = (i) => M.l + band * (i + 0.5);
    const lim = Math.max(0.05, ...rows.map((r) => Math.abs(r.shot_making) + (r.margin95 ?? 0))) * 1.08;
    const sy = (v) => M.t + (1 - (v + lim) / (2 * lim)) * (H - M.t - M.b);
    const every = [1, 2, 3, 5, 10].find((k) => band * k >= 34) ?? 10;
    const step = lim > 0.16 ? 0.1 : lim > 0.08 ? 0.05 : 0.02;
    const ticks = [];
    for (let t = -Math.floor(lim / step) * step; t <= lim + 1e-9; t += step) ticks.push(Number(t.toFixed(3)));
    const best = rows.filter((r) => r.qualified).sort((a, b) => b.shot_making - a.shot_making)[0];
    const summary = `${name}'s shot-making by season, eFG points above an average shooter on the same shots, `
        + (best ? `best ${label(best.season)} at ${pts(best.shot_making)}.` : 'no qualified season.');

    return (
        <div className="smk-chart" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`${name} shot-making`} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary} onMouseLeave={() => onHover(null)}>
                {ticks.map((t) => (
                    <g key={t}>
                        <line className={t === 0 ? 'smk-zero' : 'smk-grid'} x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="smk-tick" x={M.l - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle">{pts(t, 0)}</text>
                    </g>
                ))}
                {rows.map((r, i) => {
                    const v = r.shot_making ?? 0;
                    const cls = ['smk-bar', v >= 0 ? 'smk-bar--up' : 'smk-bar--down', !r.qualified && 'smk-bar--small',
                        active === r.season && 'smk-bar--active'].filter(Boolean).join(' ');
                    return (
                        <g key={r.season} className={cls}>
                            <rect x={cx(i) - colW / 2} width={colW} y={Math.min(sy(v), sy(0))} height={Math.abs(sy(v) - sy(0))} />
                            {r.margin95 != null && (
                                <line className="smk-whisker" x1={cx(i)} x2={cx(i)} y1={sy(v - r.margin95)} y2={sy(v + r.margin95)} />
                            )}
                        </g>
                    );
                })}
                {rows.map((r, i) => (i % every === 0 ? (
                    <text key={`t${r.season}`} className="smk-tick" x={cx(i)} y={H - M.b + 16} textAnchor="middle">{`'${String(r.season).slice(-2)}`}</text>
                ) : null))}
                {rows.map((r, i) => (
                    <rect key={`h${r.season}`} className="smk-hit" x={cx(i) - band / 2} width={band} y={M.t} height={H - M.t - M.b}
                        tabIndex={0}
                        aria-label={`${label(r.season)}: shot-making ${pts(r.shot_making)} on ${r.fga} shots${r.qualified ? '' : `, under ${minFga} attempts`}`}
                        onMouseEnter={() => onHover(r.season)} onFocus={() => onHover(r.season)} onClick={() => onPick(r.season)} />
                ))}
            </svg>
        </div>
    );
}

// ── Seasons table (also the profile block) ───────────────────────────────
export function ShotMakingTable({ rows, minFga, compact = false }) {
    return (
        <>
            <TableExport name="shot-making by season" />
            <div className="table-wrapper">
                <table className="data-table lb-table smk-table">
                    <thead>
                        <tr>
                            <th>Season</th>
                            {!compact && <th>Team</th>}
                            <th className="lb-num">FGA</th>
                            <th className="lb-num">eFG%</th>
                            <th className="lb-num">Expected eFG% (quality)</th>
                            <th className="lb-num">Shot-making ± 95%</th>
                            <th className="lb-num">Points above</th>
                            <th className="lb-num">Rank</th>
                            <th className="lb-num">Quality rank</th>
                            <th className="lb-num">3P% vs exp.</th>
                            <th className="lb-num">2P% vs exp.</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.season} className={r.qualified ? '' : 'smk-row--small'}
                                title={r.qualified ? undefined : `Fewer than ${minFga} attempts: not ranked`}>
                                <td>{label(r.season)}{r.qualified ? '' : ' *'}</td>
                                {!compact && <td>{r.team_abbreviation ?? '—'}</td>}
                                <td className="lb-num">{r.fga.toLocaleString()}</td>
                                <td className="lb-num">{pct(r.efg_pct)}</td>
                                <td className="lb-num">{pct(r.x_efg_pct)}</td>
                                <td className={`lb-num lb-stat ${tone(r.shot_making)}`}>
                                    {pts(r.shot_making)} <span className="smk-margin">± {pts(r.margin95, 1).replace(/^[+−]/, '')}</span>
                                </td>
                                <td className={`lb-num ${tonePts(r.pts_above)}`}>{signed(r.pts_above)}</td>
                                <td className="lb-num">{r.rank ? `${r.rank} of ${r.pool}` : '—'}</td>
                                <td className="lb-num">{r.quality_rank ? `${r.quality_rank} of ${r.pool}` : '—'}</td>
                                <td className="lb-num">{r.fg3a ? `${pct(r.fg3_pct)} vs ${pct(r.x_fg3_pct)}` : '—'}</td>
                                <td className="lb-num">{r.fga - r.fg3a ? `${pct(r.fg2_pct)} vs ${pct(r.x_fg2_pct)}` : '—'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </>
    );
}

// Attempt-weighted totals over the qualified seasons (a few rows: no memo needed).
function careerTotals(qualified) {
    if (!qualified.length) return null;
    const fga = qualified.reduce((a, r) => a + r.fga, 0);
    const efg = qualified.reduce((a, r) => a + r.efg_pct * r.fga, 0) / fga;
    const x = qualified.reduce((a, r) => a + r.x_efg_pct * r.fga, 0) / fga;
    const ptsAbove = qualified.reduce((a, r) => a + r.pts_above, 0);
    return { fga, efg, x, diff: efg - x, ptsAbove, seasons: qualified.length };
}

// ── One player: fetch + chart + table ────────────────────────────────────
export function ShotMakingPlayer({ playerName }) {
    const [result, setResult] = useState({ for: null, data: null, error: '' });
    const [hover, setHover] = useState(null);
    const [picked, setPicked] = useState(null);

    useEffect(() => {
        if (!playerName) return undefined;
        let active = true;
        fetchShotMaking(playerName)
            .then((d) => { if (active) setResult({ for: playerName, data: d, error: '' }); })
            .catch((e) => {
                if (active) {
                    setResult({ for: playerName, data: null, error: e?.response?.data?.detail || e?.message || 'Failed to load shot-making.' });
                }
            });
        return () => { active = false; };
    }, [playerName]);

    const loading = result.for !== playerName;
    const data = loading ? null : result.data;
    const error = loading ? '' : result.error;
    const rows = data?.rows ?? [];
    const shown = rows.find((r) => r.season === (hover ?? picked)) ?? rows[rows.length - 1];
    const qualified = rows.filter((r) => r.qualified);
    const career = careerTotals(qualified);

    return (
        <div className="dashboard-card smk-root">
            <h3 className="section-heading" style={{ marginTop: 0 }}>
                Shot-making
                <ShotMakingInfo />
                <SourceBadge source={data?._source} />
            </h3>
            <p className="page-subtitle" style={{ marginTop: 0 }}>
                Actual eFG% minus the eFG% an average shooter would post on the same shots (location, two or three, period,
                clock, season). Each bar is one regular season, with its 95% range; seasons under {data?.min_fga ?? 200} attempts
                are faded and not ranked.
            </p>
            {loading && <p className="page-subtitle">Loading shot-making…</p>}
            {error && !loading && <p className="page-subtitle">{error}</p>}
            {data && !loading && rows.length > 0 && (
                <>
                    {career && (
                        <div className="stat-cards-row smk-cards">
                            <div className="stat-card">
                                <div className="stat-card-label">Career shot-making</div>
                                <div className={`stat-card-value ${tone(career.diff)}`}>{pts(career.diff)}</div>
                                <div className="stat-card-sub">eFG points, {career.seasons} qualified season{career.seasons === 1 ? '' : 's'}</div>
                            </div>
                            <div className="stat-card">
                                <div className="stat-card-label">Shot quality</div>
                                <div className="stat-card-value">{pct(career.x)}</div>
                                <div className="stat-card-sub">expected eFG% on his shots</div>
                            </div>
                            <div className="stat-card">
                                <div className="stat-card-label">Points above expected</div>
                                <div className={`stat-card-value ${tonePts(career.ptsAbove)}`}>{signed(career.ptsAbove)}</div>
                                <div className="stat-card-sub">over {career.fga.toLocaleString()} shots</div>
                            </div>
                        </div>
                    )}
                    <ShotMakingChart rows={rows} minFga={data.min_fga} name={data.player_name} active={shown?.season}
                        onHover={setHover} onPick={setPicked} />
                    {shown && (
                        <div className="smk-detail" aria-live="polite">
                            <h4>
                                {label(shown.season)} · {shown.fga.toLocaleString()} shots · eFG {pct(shown.efg_pct)} vs. expected {pct(shown.x_efg_pct)}
                                {' '}→ <span className={tone(shown.shot_making)}>{pts(shown.shot_making)}</span> ± {pts(shown.margin95).replace(/^[+−]/, '')}
                            </h4>
                            <p>
                                {shown.qualified
                                    ? `Rank ${shown.rank} of ${shown.pool} players with ${data.min_fga}+ attempts (quality rank ${shown.quality_rank}). `
                                    : `Fewer than ${data.min_fga} attempts: not ranked. `}
                                {signed(shown.pts_above)} points against an average shooter taking the same shots.
                                {shown.sd_shot_making != null && ` Spread among qualified players that season: ± ${pts(shown.sd_shot_making).replace(/^[+−]/, '')} (one standard deviation).`}
                            </p>
                        </div>
                    )}
                    <ShotMakingTable rows={rows} minFga={data.min_fga} />
                    <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
                        Regular season only, {data.coverage.first} to {data.coverage.last}. {data.not_on_file} * fewer than {data.min_fga} attempts.
                    </p>
                </>
            )}
        </div>
    );
}

// ── League leaderboard for one season ────────────────────────────────────
const SORTS = [['shot_making', 'Shot-making'], ['quality', 'Shot quality'], ['pts_above', 'Points above expected'], ['efg', 'eFG%']];

export function ShotMakingLeaderboard({ season, sort, order, onChange }) {
    const [result, setResult] = useState({ key: null, data: null, error: '' });
    const key = `${season ?? ''}|${sort}|${order}`;

    useEffect(() => {
        let active = true;
        fetchShotMakingLeaderboard({ season: season ?? undefined, sort, order, limit: 50 })
            .then((d) => { if (active) setResult({ key, data: d, error: '' }); })
            .catch((e) => {
                if (active) setResult({ key, data: null, error: e?.response?.data?.detail || e?.message || 'Failed to load the leaderboard.' });
            });
        return () => { active = false; };
    }, [key, season, sort, order]);

    const loading = result.key !== key;
    const data = loading ? null : result.data;
    const error = loading ? '' : result.error;

    return (
        <div className="dashboard-card smk-root" style={{ marginTop: '1rem' }}>
            <h3 className="section-heading" style={{ marginTop: 0 }}>
                League leaderboard
                <SourceBadge source={data?._source} />
            </h3>
            <p className="page-subtitle" style={{ marginTop: 0 }}>
                Every player with {data?.min_fga ?? 200}+ regular-season attempts, ranked by the chosen column. Shot quality is
                the expected eFG% of a player&apos;s own shots (rim-heavy bigs lead it); shot-making is how far above or below it he shot.
            </p>
            <div className="input-row smk-controls">
                <label className="smk-select"><span>Season</span>
                    <select className="input-field" value={data?.season ?? season ?? ''} onChange={(e) => onChange({ season: Number(e.target.value) })}>
                        {(data?.seasons ?? (season ? [season] : [])).slice().reverse().map((s) => <option key={s} value={s}>{label(s)}</option>)}
                    </select>
                </label>
                <label className="smk-select"><span>Rank by</span>
                    <select className="input-field" value={sort} onChange={(e) => onChange({ sort: e.target.value })}>
                        {SORTS.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
                    </select>
                </label>
                <label className="smk-select"><span>Order</span>
                    <select className="input-field" value={order} onChange={(e) => onChange({ order: e.target.value })}>
                        <option value="desc">Best first</option>
                        <option value="asc">Worst first</option>
                    </select>
                </label>
            </div>
            {loading && <p className="page-subtitle">Loading…</p>}
            {error && !loading && <p className="page-subtitle">{error}</p>}
            {data && !loading && (
                <>
                    <p className="page-subtitle">
                        {label(data.season)}: {data.league.n_qualified} qualified players. League eFG {pct(data.league.efg_pct)} on{' '}
                        {data.league.fga.toLocaleString()} shots, expected {pct(data.league.x_efg_pct)}; one standard deviation of
                        shot-making among qualified players is {pts(data.league.sd_shot_making).replace(/^[+−]/, '')} eFG points.
                    </p>
                    <TableExport name={`shot-making leaderboard ${label(data.season)}`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table smk-table">
                            <thead>
                                <tr>
                                    <th className="lb-num">#</th><th>Player</th><th>Team</th>
                                    <th className="lb-num">FGA</th><th className="lb-num">eFG%</th>
                                    <th className="lb-num">Expected eFG%</th><th className="lb-num">Shot-making ± 95%</th>
                                    <th className="lb-num">Points above</th><th className="lb-num">3P% vs exp.</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.rows.map((r, i) => (
                                    <tr key={r.player_id}>
                                        <td className="lb-num">{i + 1}</td>
                                        <td><PlayerName playerId={r.player_id} name={r.player_name} size={28} /></td>
                                        <td>{r.team_abbreviation ?? '—'}</td>
                                        <td className="lb-num">{r.fga.toLocaleString()}</td>
                                        <td className="lb-num">{pct(r.efg_pct)}</td>
                                        <td className="lb-num">{pct(r.x_efg_pct)}</td>
                                        <td className={`lb-num lb-stat ${tone(r.shot_making)}`}>
                                            {pts(r.shot_making)} <span className="smk-margin">± {pts(r.margin95).replace(/^[+−]/, '')}</span>
                                        </td>
                                        <td className={`lb-num ${tonePts(r.pts_above)}`}>{signed(r.pts_above)}</td>
                                        <td className="lb-num">{r.fg3a >= 50 ? `${pct(r.fg3_pct)} vs ${pct(r.x_fg3_pct)}` : '—'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>{data.not_on_file} 3P% shown from 50 attempts.</p>
                </>
            )}
        </div>
    );
}

// ── How the model was checked ────────────────────────────────────────────
const SMALL_BIN = 1000; // a calibration bucket with fewer shots is greyed out
const MODEL_NAMES = {
    constant: 'Constant (last season\'s league FG%)',
    zone_baseline: 'Zone baseline (last season\'s FG% by zone)',
    logreg: 'Logistic regression',
    hgb: 'Gradient boosting (histogram)',
};

export function ShotMakingModelStats({ data, compact = false }) {
    if (!data) return <p className="meth-live-note">Loading the live validation…</p>;
    if (!data.holdout?.length) return <p className="meth-live-note">Validation results couldn&apos;t be loaded.</p>;
    const deployed = data.holdout.find((r) => r.deployed) ?? data.holdout[data.holdout.length - 1];
    const cf = data.crossfit;
    const y2y = cf?.notes?.year_to_year?.[String(cf?.notes?.min_fga ?? 200)];
    return (
        <div className="smk-model">
            <TableExport name="shot-making model check" />
            <div className="table-wrapper">
                <table className="data-table smk-table">
                    <thead>
                        <tr><th>Model</th><th className="lb-num">Log loss</th><th className="lb-num">Brier</th><th className="lb-num">ROC-AUC</th></tr>
                    </thead>
                    <tbody>
                        {data.holdout.map((r) => (
                            <tr key={r.model_type} className={r.deployed ? 'smk-row--chosen' : ''}>
                                <td>{MODEL_NAMES[r.model_type] ?? r.model_type}{r.deployed ? ' — used' : ''}</td>
                                <td className="lb-num">{r.log_loss.toFixed(4)}</td>
                                <td className="lb-num">{r.brier.toFixed(4)}</td>
                                <td className="lb-num">{r.roc_auc.toFixed(3)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="meth-live-note">
                Scored on {data.holdout_season} ({deployed.n_test.toLocaleString()} shots), which none of the models saw; trained on
                the {deployed.n_train.toLocaleString()} shots of every earlier season.
                {cf && ` Cross-fitted by player over all seasons: log loss ${cf.log_loss.toFixed(4)} on ${cf.n_test.toLocaleString()} shots.`}
                {y2y && ` Year to year (players with ${cf.notes.min_fga}+ attempts in both seasons, ${y2y.n_pairs.toLocaleString()} pairs): shot-making r = ${y2y.shot_making}, shot quality r = ${y2y.quality}, raw eFG% r = ${y2y.efg}.`}
            </p>
            {!compact && deployed.reliability_bins?.length > 0 && (
                <>
                    <h4 className="section-heading" style={{ fontSize: '1rem', marginTop: '1rem' }}>Calibration on {data.holdout_season}</h4>
                    <TableExport name="shot-making reliability" />
                    <div className="table-wrapper">
                        <table className="data-table smk-table">
                            <thead><tr><th>Predicted make chance</th><th className="lb-num">Shots</th><th className="lb-num">Predicted</th><th className="lb-num">Actually made</th></tr></thead>
                            <tbody>
                                {deployed.reliability_bins.map((b) => (
                                    <tr key={b.bucket_lo} className={b.n < SMALL_BIN ? 'smk-row--small' : ''}
                                        title={b.n < SMALL_BIN ? `Fewer than ${SMALL_BIN.toLocaleString()} shots: noisy` : undefined}>
                                        <td>{pct(b.bucket_lo, 0)} to {pct(b.bucket_hi, 0)}</td>
                                        <td className="lb-num">{b.n.toLocaleString()}</td>
                                        <td className="lb-num">{pct(b.predicted_mean)}</td>
                                        <td className="lb-num">{pct(b.observed_rate)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
            {!compact && (
                <p className="meth-live-note">
                    Buckets under {SMALL_BIN.toLocaleString()} shots are greyed out: a few dozen heaves can't show calibration.{' '}
                    <SourceBadge source={data._source} />
                </p>
            )}
        </div>
    );
}

export function ShotMakingModel() {
    const [data, setData] = useState(null);
    useEffect(() => {
        let active = true;
        fetchShotMakingModel().then((d) => { if (active) setData(d); }).catch(() => { if (active) setData({}); });
        return () => { active = false; };
    }, []);
    return (
        <div className="dashboard-card smk-root" style={{ marginTop: '1rem' }}>
            <h3 className="section-heading" style={{ marginTop: 0 }}>How good is the model?</h3>
            <p className="page-subtitle" style={{ marginTop: 0 }}>
                Two models and two baselines were trained on every season but the last and scored on the last one by log loss
                (lower is better); the better model scores every player. Calibration: when it says 40%, about 40% should go in.
            </p>
            <ShotMakingModelStats data={data} />
        </div>
    );
}
