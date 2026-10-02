import React, { useEffect, useMemo, useState } from 'react';
import { fetchLineupPrediction, fetchLineupPredictorOptions, fetchLineupPredictorTeam } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from './InfoTooltip';
import PlayerName from './PlayerName';
import SourceBadge from './SourceBadge';
import TableExport from './TableExport';
import { IntervalChart } from './CoachingCharts';
import { bySign, signed } from '../../utils/format';
import '../../styles/lineuppredictor.css';

// "Try a lineup" on Teams › Rotations (GET /lineup-predictor/*): pick any five
// of a team-season's players and see the model's net rating for them before
// the season and with the season so far, the parts of the prediction, an 80%
// range, and, if the five actually played, what they did and what the model
// said before their first game. Below: the team's most-used lineups predicted
// vs actual, and the model's held-out scores. The five live in the URL as
// lu=<ids> (Rotations' useUrlSync).

const tone = (v, d = 1) => bySign(v, d, 'oo-pos', 'oo-neg');
const pct = (v, d = 0) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const PHASES = [['test', '2025-26 (test)'], ['validate', '2024-25 (validate)'], ['tune', '2021-22 to 2023-24 (tune, out-of-fold)']];
const CHART_MODELS = ['zero', 'team', 'sum', 'scaled', 'fit', 'full'];

function ModelScores({ options }) {
    const [phase, setPhase] = useState('test');
    const [subset, setSubset] = useState('later');
    const later = options.later_after;
    const items = CHART_MODELS.map((m) => {
        const t = options.tests.find((r) => r.metric === 'r2_true' && r.phase === phase && r.variant === subset && r.model_a === m && !r.model_b);
        return t && { key: m, label: options.models[m], shortLabel: m, value: t.value_a, lo: t.ci_lo, hi: t.ci_hi, highlight: m === 'full' };
    }).filter(Boolean);
    const pair = (a, b) => options.tests.find((r) => r.metric === 'wmse' && r.phase === phase && r.variant === subset && r.model_a === a && r.model_b === b);
    const r2pair = (a, b) => options.tests.find((r) => r.metric === 'r2_true' && r.phase === phase && r.variant === subset && r.model_a === a && r.model_b === b);
    const rows = [['fit', 'scaled', 'Spacing, roles and usage on top of the ratings'], ['full', 'fit', 'The season so far on top of that'],
        ['scaled', 'sum', 'Rescaling the plain sum'], ['scaled', 'team', 'The five players vs the team so far'],
        ['scaled_rapm', 'scaled_bpm', 'Last season\'s RAPM vs the BPM projection'], ['scaled_tracker', 'scaled_bpm', 'Rating Tracker vs the BPM projection']];
    const nc = options.noise_check;
    return (
        <div className="lp-scores">
            <h3 className="rp-panel-title">How good is it?</h3>
            <p className="page-subtitle" style={{ marginTop: 0 }}>
                Share of the real spread in lineup net ratings each model explains (noise taken out), with 95% intervals over team-seasons.
                Chosen and fitted on {options.protocol.tune}, never on the seasons it is scored on.
            </p>
            <div className="lb-controls lp-score-controls">
                <label>
                    <span>Seasons</span>
                    <select className="input-field" value={phase} onChange={(e) => setPhase(e.target.value)}>
                        {PHASES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                    </select>
                </label>
                <label>
                    <span>Lineups first used</span>
                    <select className="input-field" value={subset} onChange={(e) => setSubset(e.target.value)}>
                        <option value="later">After game {later}</option>
                        <option value="all">Any time</option>
                    </select>
                </label>
            </div>
            <IntervalChart items={items} refLine={0} refLabel="Explains nothing" fmt={(v) => pct(v)} tickFmt={(v) => pct(v)}
                legend="Real spread explained (95% interval)" name={`lineup predictor scores ${phase} ${subset}`}
                ariaLabel="Share of the real spread in lineup net ratings explained, by model" />
            <div className="table-wrapper">
                <TableExport name={`lineup predictor tests ${phase} ${subset}`} />
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Question</th>
                            <th className="lb-num" title="Difference in the share of the real spread explained">Δ explained</th>
                            <th className="lb-num" title="95% interval of that difference (paired bootstrap over team-seasons)">95% interval</th>
                            <th className="lb-num" title="Two-sided p of the difference in possession-weighted squared error (bootstrap)">p</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map(([a, b, q]) => {
                            const t = pair(a, b);
                            const r = r2pair(a, b);
                            if (!t || !r) return null;
                            return (
                                <tr key={`${a}-${b}`}>
                                    <td>{q}</td>
                                    <td className={`lb-num ${tone(r.diff * 100)}`}>{signed(r.diff * 100, 1)} pts</td>
                                    <td className="lb-num">{signed(r.ci_lo * 100, 1)} to {signed(r.ci_hi * 100, 1)}</td>
                                    <td className="lb-num">{t.p_boot < 0.001 ? '<0.001' : t.p_boot.toFixed(3)}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            <p className="rot-five-meta">
                Δ explained is in percentage points of the real spread. The noise model (possessions independent) was checked against a split-half
                estimate that assumes nothing about noise: real variance {num(nc.model, 0)} vs {num(nc.split_half, 0)} (points per 100, squared;
                {' '}{(nc.units ?? 0).toLocaleString()} lineups), so the shares may be read about {Math.round((1 - nc.model / nc.split_half) * 100)}% high;
                the differences between models don&apos;t depend on it.
            </p>
        </div>
    );
}

function Band({ title, band, note, poss }) {
    return (
        <div className="lp-band">
            <h4 className="lp-band-title">{title}</h4>
            <div className={`lp-band-value ${tone(band.pred)}`}>{signed(band.pred, 1)}</div>
            <div className="lp-band-range">80% range of its real net: {signed(band.lo, 1)} to {signed(band.hi, 1)}</div>
            <div className="lp-band-range">What {poss} possessions a side would show: {signed(band.obs_lo, 1)} to {signed(band.obs_hi, 1)}</div>
            {note && <p className="rot-five-meta">{note}</p>}
        </div>
    );
}

function Prediction({ res }) {
    if (!res) return <Loader />;
    if (res.error) return <p className="error-message">{res.error}</p>;
    const d = res.data;
    const a = d.actual;
    return (
        <div className="lp-result">
            <div className="lp-bands">
                <Band title="Before the season" band={d.preseason} poss={d.panel_poss}
                    note={`Ratings fixed before ${d.season_label}, Gravity and roles from ${d.season - 2}-${String(d.season - 1).slice(-2)}.`} />
                <Band title="With the whole season" band={d.season_so_far} poss={d.panel_poss}
                    note={`Adds ${d.team}'s and these five's on-court net through ${d.last_date} (${d.games} games), shrunk by ${Number(d.k).toLocaleString()} possessions; includes this five's own minutes if it played.`} />
                <div className="lp-band">
                    <h4 className="lp-band-title">What it actually did</h4>
                    {a ? (
                        <>
                            <div className={`lp-band-value ${tone(a.net)}`}>{signed(a.net, 1)}</div>
                            <div className="lp-band-range">{num(a.poss, 0)} possessions a side (average), {a.games} games; noise ±{num(a.noise_sd, 1)} (1 SD)</div>
                            <div className="lp-band-range">
                                First used {a.first_date} (game {a.first_game_no}). The model then said {signed(a.pred_fit_then, 1)} before the season,
                                {' '}{signed(a.pred_full_then, 1)} with the season up to that game{a.phase !== 'tune' ? '' : ' (tune season: out-of-fold)'}.
                            </div>
                        </>
                    ) : (
                        <p className="rot-five-meta">These five never shared the floor for {d.team} in {d.season_label}.</p>
                    )}
                </div>
            </div>
            <div className="table-wrapper">
                <TableExport name={`lineup prediction ${d.team} ${d.season_label}`} />
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Player</th>
                            <th className="lb-num" title={d.source_label}>Rating</th>
                            <th title="player_roles family, the season before">Role before</th>
                            <th className="lb-num" title="Gravity (spacing), the season before">Gravity</th>
                            <th className="lb-num" title="Projected usage">Usage</th>
                            <th className="lb-num" title="His on-court net rating for this team this season (points per 100)">On-court net</th>
                        </tr>
                    </thead>
                    <tbody>
                        {d.players.map((p) => (
                            <tr key={p.player_id}>
                                <td><PlayerName playerId={p.player_id} name={p.player_name ?? `#${p.player_id}`} size={22} /></td>
                                <td className={`lb-num ${tone(p.rating)}`}>{signed(p.rating, 1)}{!p.rating_known && <span className="lp-flag" title="No rating before the season: replacement level"> *</span>}</td>
                                <td>{p.role ?? <span className="lp-muted">none on file</span>}</td>
                                <td className="lb-num">{signed(p.gravity, 1)}{!p.gravity_known && <span className="lp-flag" title="No Gravity the season before: replacement level"> *</span>}</td>
                                <td className="lb-num">{pct(p.usage, 1)}{!p.usage_known && <span className="lp-flag" title="No projection: replacement level"> *</span>}</td>
                                <td className={`lb-num ${tone(p.on_net)}`}>{signed(p.on_net, 1)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <details className="rot-excluded lp-parts">
                <summary>How the prediction adds up</summary>
                <div className="table-wrapper">
                    <table className="data-table lb-table">
                        <thead><tr><th>Part</th><th className="lb-num">Value</th><th className="lb-num">Weight</th><th className="lb-num">Adds</th></tr></thead>
                        <tbody>
                            <tr><td>Starting point (intercept)</td><td className="lb-num">—</td><td className="lb-num">—</td><td className="lb-num">{signed(d.intercept, 2)}</td></tr>
                            {d.parts.map((p) => (
                                <tr key={p.feature}><td>{p.label}</td><td className="lb-num">{num(p.value, 2)}</td><td className="lb-num">{num(p.coef, 3)}</td>
                                    <td className={`lb-num ${tone(p.contribution, 2)}`}>{signed(p.contribution, 2)}</td></tr>
                            ))}
                            <tr><td><strong>Before the season</strong></td><td /><td /><td className="lb-num"><strong>{signed(d.preseason.pred, 1)}</strong></td></tr>
                        </tbody>
                    </table>
                </div>
                <p className="rot-five-meta">
                    The plain sum of the five ratings is {signed(d.sum, 1)}; rescaled alone, {signed(d.scaled, 1)}. The whole-season number is a separate
                    fit with two more parts: the team&apos;s net so far ({num(d.parts_now[0].value, 1)}, adds {signed(d.parts_now[0].contribution, 2)})
                    and the five&apos;s mean on-court net so far ({num(d.parts_now[1].value, 1)}, adds {signed(d.parts_now[1].contribution, 2)}).
                    Weights fitted on all five seasons, for this page; the scores below come from the held-out fits.
                </p>
            </details>
        </div>
    );
}

export default function LineupPredictorPanel({ team, season, initialIds, onIdsChange }) {
    const [options, setOptions] = useState(null);
    const [optError, setOptError] = useState('');
    const [teamRes, setTeamRes] = useState(null);
    const [pick, setPick] = useState(null);           // { key, ids }
    const [predRes, setPredRes] = useState(null);     // { key, data } | { key, error }
    const key = `${team}-${season}`;

    useEffect(() => {
        fetchLineupPredictorOptions().then(setOptions)
            .catch(() => setOptError('The Lineup Predictor couldn\'t load. Has scripts/build_lineup_predictor.py been run, and was impact_api restarted?'));
    }, []);

    const covered = options ? options.seasons.includes(season) && (options.teams[String(season)] ?? []).includes(team) : false;

    useEffect(() => {
        if (!covered) return undefined;
        let active = true;
        fetchLineupPredictorTeam(team, season)
            .then((d) => {
                if (!active) return;
                setTeamRes({ key, data: d });
                const roster = new Set(d.players.map((p) => p.player_id));
                const wanted = (initialIds ?? []).filter((i) => roster.has(i));
                const ids = wanted.length === 5 && new Set(wanted).size === 5 ? wanted : (d.lineups[0]?.player_ids ?? d.players.slice(0, 5).map((p) => p.player_id));
                setPick({ key, ids });
            })
            .catch((e) => { if (active) setTeamRes({ key, error: e.response?.data?.detail || 'The team\'s players couldn\'t load.' }); });
        return () => { active = false; };
    }, [key, covered]); // eslint-disable-line react-hooks/exhaustive-deps

    const ids = pick?.key === key ? pick.ids : null;
    const idsKey = ids ? `${key}:${ids.join(',')}` : null;
    const valid = ids && ids.every((i) => i != null) && new Set(ids).size === 5;
    useEffect(() => {
        if (!valid) return undefined;
        let active = true;
        fetchLineupPrediction(team, season, ids)
            .then((d) => { if (active) setPredRes({ key: idsKey, data: d }); })
            .catch((e) => { if (active) setPredRes({ key: idsKey, error: e.response?.data?.detail || 'The prediction couldn\'t load.' }); });
        return () => { active = false; };
    }, [idsKey, valid]); // eslint-disable-line react-hooks/exhaustive-deps

    const data = teamRes?.key === key ? teamRes.data : null;
    const teamError = teamRes?.key === key ? teamRes.error : '';
    const players = useMemo(() => data?.players ?? [], [data]);

    if (optError) return <section className="lp-panel"><p className="error-message">{optError}</p></section>;
    if (!options) return <section className="lp-panel"><Loader /></section>;

    const head = (
        <h3 className="card-title lp-title">
            Try a lineup
            <InfoTooltip label="How the Lineup Predictor works" title="Under the hood">{options.method}</InfoTooltip>
            <SourceBadge source={options._source} />
        </h3>
    );
    if (!covered) {
        return (
            <section className="lp-panel">
                {head}
                <p className="page-subtitle">
                    The Lineup Predictor covers {options.seasons[0] - 1}-{String(options.seasons[0]).slice(-2)} to {options.seasons.at(-1) - 1}-{String(options.seasons.at(-1)).slice(-2)}:
                    2020-21 has no RAPM or Rating Tracker season before it.
                </p>
            </section>
        );
    }
    const choose = (next) => {
        setPick({ key, ids: next });
        if (next.every((i) => i != null) && new Set(next).size === 5) onIdsChange?.(next);
    };
    const setSlot = (i, v) => choose(ids.map((x, j) => (j === i ? v : x)));
    const predRight = predRes && predRes.key === idsKey ? predRes : null;
    return (
        <section className="lp-panel">
            {head}
            <p className="page-subtitle" style={{ marginTop: 0 }}>
                Pick any five {team} players from {data?.season_label ?? ''}. The model predicts their net rating (points per 100 possessions) from
                what was known before they shared the floor: the five ratings summed ({options.source_short ?? options.source}),
                spacing, roles and usage, and how the team and the five had played so far.
            </p>
            {teamError && <p className="error-message">{teamError}</p>}
            {!data && !teamError && <Loader />}
            {data && ids && (
                <>
                    <div className="lb-controls lp-picks">
                        {ids.map((id, i) => (
                            <label key={i}>
                                <span>Player {i + 1}</span>
                                <select className="input-field" value={id ?? ''} onChange={(e) => setSlot(i, e.target.value ? Number(e.target.value) : null)}>
                                    <option value="">—</option>
                                    {players.map((p) => (
                                        <option key={p.player_id} value={p.player_id} disabled={ids.includes(p.player_id) && p.player_id !== id}>
                                            {p.player_name ?? `#${p.player_id}`} ({num(p.poss, 0)} poss)
                                        </option>
                                    ))}
                                </select>
                            </label>
                        ))}
                    </div>
                    {!valid && <p className="rot-five-meta">Pick five different players.</p>}
                    {valid && <Prediction res={predRight} />}

                    <h3 className="rp-panel-title" style={{ marginTop: 'var(--space-5)' }}>{team}&apos;s most-used lineups: predicted vs actual</h3>
                    <p className="page-subtitle" style={{ marginTop: 0 }}>
                        {data.n_lineups.toLocaleString()} five-man lineups played for {team} in {data.season_label} ({data.n_later.toLocaleString()} first used after game {options.later_after}).
                        Each prediction was made before the lineup&apos;s first game. The noise column is one standard deviation of the record from chance alone.
                    </p>
                    <div className="table-wrapper">
                        <TableExport name={`lineup predictions ${team} ${data.season_label}`} />
                        <table className="data-table lb-table">
                            <thead>
                                <tr>
                                    <th>Lineup</th>
                                    <th className="lb-num" title="Date and team game number of the first game">First used</th>
                                    <th className="lb-num" title="Possessions a side (average of offence and defence)">Poss</th>
                                    <th className="lb-num" title="Points per 100 possessions, for minus against">Actual</th>
                                    <th className="lb-num" title="One standard deviation of the actual from chance alone">± noise</th>
                                    <th className="lb-num" title="The model before the season">Pre-season</th>
                                    <th className="lb-num" title="The model with the season up to the lineup's first game">At first game</th>
                                    <th />
                                </tr>
                            </thead>
                            <tbody>
                                {data.lineups.map((l) => (
                                    <tr key={l.player_ids.join('-')}>
                                        <td className="rot-five-cell">{l.player_ids.map((pid, i) => <PlayerName key={pid} playerId={pid} name={l.names[i] ?? `#${pid}`} size={20} />)}</td>
                                        <td className="lb-num">{l.first_date} (#{l.first_game_no})</td>
                                        <td className="lb-num">{num(l.poss, 0)}</td>
                                        <td className={`lb-num ${tone(l.net)}`}>{signed(l.net, 1)}</td>
                                        <td className="lb-num">{num(l.noise_sd, 1)}</td>
                                        <td className={`lb-num ${tone(l.pred_fit)}`}>{signed(l.pred_fit, 1)}</td>
                                        <td className={`lb-num ${tone(l.pred_full)}`}>{signed(l.pred_full, 1)}</td>
                                        <td><button type="button" className="pp-link rp-link" onClick={() => choose(l.player_ids)}>Try</button></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
            <ModelScores options={options} />
        </section>
    );
}
