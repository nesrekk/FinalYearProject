import React, { useEffect, useState } from 'react';
import { fetchStatLineMatches, fetchStatLineOptions } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const FORMATS = {
    num1: (v) => v.toFixed(1),
    pct: (v) => `${(v * 100).toFixed(1)}%`,
    signed1: (v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}`,
    int: (v) => v.toFixed(0),
};
const fmt = (format, v) => (v == null ? '—' : FORMATS[format](v));
const signed = (v, d = 1) => {
    const r = Number(v.toFixed(d));
    return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(d)}`;
};
const TOP_N = [10, 25, 50];
const ATTEMPT_WORDS = { fga: 'shots', fg3a: 'three-point attempts', fta: 'free throws' };
// Matches with fewer games than this are shown lighter: a real season, but a short one.
const SHORT_SEASON_GP = 40;
// Above this distance nothing in the pool is really close (a typical stat a full SD away).
const FAR = 1;

// Typed values are what people write (60 for 60% TS); the API and the link
// use shares (0.6), as stored.
const toInput = (format, v) => String(format === 'pct' ? +(v * 100).toFixed(1) : v);
const fromInput = (format, s) => (s === '' || !Number.isFinite(Number(s)) ? null
    : format === 'pct' ? Number(s) / 100 : Number(s));

const DEFAULT_LINE = [['pts', 25], ['ts_pct', 0.6], ['ast', 8]];
const EXAMPLES = [
    { label: '25 pts · 60% TS · 8 ast', line: DEFAULT_LINE, season: null },
    { label: 'Rim protector: 3 blk · 12 reb', line: [['blk', 3], ['reb', 12]], season: null },
    { label: 'Movement shooter: 20 pts · 10 3PA · 42% 3P', line: [['pts', 20], ['fg3a', 10], ['fg3_pct', 0.42]], season: null },
    { label: 'Wilt 1961-62: 50.4 pts · 25.7 reb', line: [['pts', 50.4], ['reb', 25.7]], season: 1962 },
];

function rowsFromLine(line, byKey) {
    return line.filter(([k]) => byKey[k]).map(([key, v]) => ({ key, value: toInput(byKey[key].format, v) }));
}

// The form from a shared link (utils/useUrlState.js). season null = latest.
function formFromParams(p, o) {
    const byKey = Object.fromEntries(o.stats.map((s) => [s.key, s]));
    const seen = new Set();
    const line = (parseParam.list(p, 'line') ?? [])
        .map((part) => part.split(':'))
        .filter(([k, v]) => byKey[k] && v !== '' && Number.isFinite(Number(v)) && !seen.has(k) && seen.add(k))
        .map(([k, v]) => [k, Number(v)])
        .slice(0, o.max_stats);
    const n = parseParam.int(p, 'n');
    const range = { min: o.seasons.from, max: o.seasons.to };
    return {
        rows: rowsFromLine(line.length ? line : DEFAULT_LINE, byKey),
        season: parseParam.int(p, 'season', range),
        from: parseParam.int(p, 'from', range),
        to: parseParam.int(p, 'to', range),
        minGp: parseParam.int(p, 'gp', { min: 0, max: 82 }) ?? 20,
        onePerPlayer: p.get('one') !== '0',
        topN: TOP_N.includes(n) ? n : 25,
    };
}

function why(r, line) {
    const keys = line.map((l) => l.key);
    if (keys.length < 2) return '—';
    const byGap = [...keys].sort((a, b) => Math.abs(r.stats[a].gap_z) - Math.abs(r.stats[b].gap_z));
    const label = (k) => line.find((l) => l.key === k).label.toLowerCase();
    const worst = byGap[byGap.length - 1];
    const g = r.stats[worst].gap_z;
    if (Math.abs(g) < 0.05) return 'Matches on every stat';
    return `Closest on ${label(byGap[0])}; ${Math.round(r.share_of_distance[worst] * 100)}% of the gap is `
        + `${label(worst)} (${g > 0 ? 'higher' : 'lower'})`;
}

export default function StatLineFinder() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        fetchStatLineOptions()
            .then((o) => {
                setOptions(o);
                setForm(formFromParams(params, o));
            })
            .catch(() => setOptionsError('The stat list couldn\'t load. Is the similarity API (port 8001) running?'));
    }, [params]);

    const byKey = options ? Object.fromEntries(options.stats.map((s) => [s.key, s])) : {};
    const typed = form ? form.rows.map((r) => [r.key, fromInput(byKey[r.key].format, r.value)]) : [];
    const complete = typed.filter(([, v]) => v != null);
    const lineParam = complete.map(([k, v]) => `${k}:${+v.toFixed(4)}`).join(',');
    const shownSeason = form?.season ?? data?.season ?? null;

    useUrlSync(form && {
        line: lineParam, season: shownSeason, from: form.from, to: form.to,
        gp: form.minGp || 0, one: form.onePerPlayer ? null : 0, n: form.topN,
    });

    useEffect(() => {
        if (!options || !form) return undefined;
        const timer = setTimeout(async () => {
            if (!lineParam) {
                setData(null);
                setError('Type at least one number.');
                return;
            }
            setLoading(true);
            setError('');
            try {
                setData(await fetchStatLineMatches({
                    line: lineParam, season: form.season ?? undefined,
                    season_from: form.from ?? undefined, season_to: form.to ?? undefined,
                    min_gp: form.minGp || 0, one_per_player: form.onePerPlayer, top_n: form.topN,
                }));
            } catch (err) {
                setData(null);
                setError(err.response?.data?.detail || 'Failed to find matches.');
            } finally {
                setLoading(false);
            }
        }, 350);
        return () => clearTimeout(timer);
    }, [options, form, lineParam]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const setRow = (i, patch) => set({ rows: form.rows.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
    const used = new Set(form.rows.map((r) => r.key));
    const unused = options.stats.filter((s) => !used.has(s.key));
    const addStat = (key) => {
        if (!key) return;
        // A stat recorded only from a later season moves the seasons with it.
        const first = byKey[key].first_season;
        const clip = (s) => (s != null && s < first ? null : s);
        set({ rows: [...form.rows, { key, value: '' }], season: clip(form.season), from: clip(form.from), to: clip(form.to) });
    };
    const applyExample = (ex) => set({ rows: rowsFromLine(ex.line, byKey), season: ex.season, from: null, to: null });

    // Seasons a line can be read in / matched over: every typed stat must be recorded.
    const firstUsable = Math.max(options.seasons.from, ...form.rows.map((r) => byKey[r.key].first_season));
    const allSeasons = [];
    for (let s = options.seasons.to; s >= firstUsable; s -= 1) allSeasons.push(s);
    const limiting = form.rows.reduce((a, r) => (!a || byKey[r.key].first_season > byKey[a].first_season ? r.key : a), null);
    const line = data?.line ?? [];
    const best = data?.results?.[0];

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Find the season behind a stat line
                <InfoTooltip label="How the Stat Line Finder works" title="Under the hood">
                    {data?.method ?? 'Each stat is z-scored within its own season; your line is z-scored against the season you pick and matched on the stats you typed.'}
                </InfoTooltip>
                <SourceBadge source={data?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="statline" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Type the numbers you have in mind. They&apos;re read as a standing in the season you pick
                (25 points in 2025-26 is about 2.5 standard deviations above that season&apos;s average), and every
                real player-season is ranked by how close its own standing is, on those stats only.
            </p>

            <div className="lb-presets sl-examples" aria-label="Examples">
                {EXAMPLES.map((ex) => (
                    <button key={ex.label} type="button" onClick={() => applyExample(ex)}>{ex.label}</button>
                ))}
            </div>

            <fieldset className="sl-line">
                <legend>Your stat line ({form.rows.length} of up to {options.max_stats})</legend>
                {form.rows.map((r, i) => {
                    const s = byKey[r.key];
                    return (
                        <div className="sl-row" key={r.key}>
                            <label htmlFor={`sl-${r.key}`}>{s.label}</label>
                            <div className="sl-input">
                                <input id={`sl-${r.key}`} className="input-field" type="number" inputMode="decimal"
                                    step="any" min={s.format === 'pct' ? 0 : undefined} max={s.format === 'pct' ? 100 : undefined}
                                    value={r.value} onChange={(e) => setRow(i, { value: e.target.value })} />
                                <span className="sl-unit">{s.format === 'pct' ? '%' : s.key === 'age' ? 'yrs' : s.format === 'signed1' ? 'pts/100' : '/ game'}</span>
                            </div>
                            <button type="button" className="cb-remove" aria-label={`Remove ${s.label}`}
                                onClick={() => set({ rows: form.rows.filter((_, j) => j !== i) })}
                                disabled={form.rows.length <= 1}>×</button>
                        </div>
                    );
                })}
                {unused.length > 0 && form.rows.length < options.max_stats && (
                    <select className="input-field sl-add" aria-label="Add a stat" value=""
                        onChange={(e) => addStat(e.target.value)}>
                        <option value="">+ Add a stat…</option>
                        {unused.map((s) => (
                            <option key={s.key} value={s.key}>
                                {s.label}{s.first_season > options.seasons.from ? ` (from ${seasonLabel(s.first_season)})` : ''}
                            </option>
                        ))}
                    </select>
                )}
            </fieldset>

            <div className="lb-controls">
                <label>
                    <span>Read the line in</span>
                    <select className="input-field" value={shownSeason ?? ''}
                        onChange={(e) => set({ season: Number(e.target.value) })}>
                        {allSeasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Match seasons from</span>
                    <select className="input-field" value={form.from ?? ''}
                        onChange={(e) => set({ from: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">Earliest ({seasonLabel(firstUsable)})</option>
                        {allSeasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>to</span>
                    <select className="input-field" value={form.to ?? ''}
                        onChange={(e) => set({ to: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">Latest ({seasonLabel(options.seasons.to)})</option>
                        {allSeasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Min. games</span>
                    <input className="input-field" type="number" min={0} max={82} value={form.minGp}
                        onChange={(e) => set({ minGp: e.target.value === '' ? '' : Number(e.target.value) })} />
                </label>
                <label>
                    <span>Show</span>
                    <select className="input-field" value={form.topN} onChange={(e) => set({ topN: Number(e.target.value) })}>
                        {TOP_N.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
            </div>
            <div className="sim-filters">
                <label>
                    <input type="checkbox" checked={form.onePerPlayer} onChange={(e) => set({ onePerPlayer: e.target.checked })} />
                    One season per player
                </label>
            </div>
            {limiting && byKey[limiting].first_season > options.seasons.from && (
                <p className="page-subtitle sl-note">
                    {byKey[limiting].label} is recorded from {seasonLabel(byKey[limiting].first_season)} on, so only
                    seasons from then are searched.
                </p>
            )}

            {error && <p className="error-message">{error}</p>}
            {loading && !data && <Loader />}
            {data && (
                <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                    <div className="table-wrapper">
                        <table className="data-table lb-table sl-read">
                            <caption>Your line, read in {seasonLabel(data.season)}</caption>
                            <thead>
                                <tr>
                                    <th>Stat</th>
                                    <th className="lb-num">You typed</th>
                                    <th className="lb-num">Season average</th>
                                    <th className="lb-num">Standing (SD)</th>
                                    <th className="lb-num">n</th>
                                </tr>
                            </thead>
                            <tbody>
                                {line.map((l) => (
                                    <tr key={l.key}>
                                        <td>{l.label}</td>
                                        <td className="lb-num lb-stat">{fmt(l.format, l.value)}</td>
                                        <td className="lb-num">{fmt(l.format, l.season_mean)}</td>
                                        <td className="lb-num">{signed(l.z, 2)}</td>
                                        <td className="lb-num" title="Player-seasons the average and SD come from">{l.season_n.toLocaleString()}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {line.some((l) => byKey[l.key].attempts) && (
                        <p className="page-subtitle sl-note">
                            Shooting percentages count only players with at least{' '}
                            {line.filter((l) => byKey[l.key].attempts).map((l) => {
                                const s = byKey[l.key];
                                return `${s.min_attempts} ${ATTEMPT_WORDS[s.attempts]} a game for ${s.label}`;
                            }).join(', ')}; below that a season isn&apos;t matched on it.
                        </p>
                    )}

                    {best && best.distance > FAR && (
                        <p className="rx-verdict">
                            <strong>Nothing real is close.</strong> The nearest season is {best.distance.toFixed(2)} standard
                            deviations away on a typical stat; this line is outside anything in the data for {seasonLabel(data.season)}.
                        </p>
                    )}
                    <p className="page-subtitle lb-summary">
                        Closest {data.results.length} of {data.pool.toLocaleString()} player-seasons ({seasonLabel(data.range.from)} to{' '}
                        {seasonLabel(data.range.to)}, {data.filters.min_gp}+ games{data.filters.one_per_player ? ', one per player' : ''}).
                        Distance 0 = the same standing on every stat; 1 = a typical stat one standard deviation away. The
                        number in brackets is how far that stat is from yours, in standard deviations of its own season.
                        {' '}Because standing is what&apos;s matched, a season from another era can show raw numbers
                        unlike yours (league shooting and pace were different), and still be a close match.
                        {' '}Seasons under {SHORT_SEASON_GP} games are lighter.
                    </p>
                    {data.results.length === 0 ? (
                        <p className="empty-message">No player-seasons pass these filters.</p>
                    ) : (
                        <>
                            <TableExport name={`seasons closest to ${lineParam}`} />
                            <div className="table-wrapper">
                                <table className="data-table lb-table">
                                    <thead>
                                        <tr>
                                            <th>#</th>
                                            <th>Player</th>
                                            <th>Season</th>
                                            <th>Team</th>
                                            <th className="lb-num">GP</th>
                                            <th className="lb-num lb-stat">Distance</th>
                                            {line.map((l) => <th key={l.key} className="lb-num">{l.label}</th>)}
                                            <th>What drives it</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.results.map((r) => (
                                            <tr key={`${r.player_id}-${r.season}`} className={r.gp < SHORT_SEASON_GP ? 'sl-short' : undefined}>
                                                <td>{r.rank}</td>
                                                <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                                <td>{seasonLabel(r.season)}</td>
                                                <td>{r.team}</td>
                                                <td className="lb-num">{r.gp}</td>
                                                <td className="lb-num lb-stat">{r.distance.toFixed(2)}</td>
                                                {line.map((l) => (
                                                    <td key={l.key} className="lb-num">
                                                        {fmt(l.format, r.stats[l.key].value)}{' '}
                                                        <span className="cb-z">({signed(r.stats[l.key].gap_z)})</span>
                                                    </td>
                                                ))}
                                                <td className="sim-why">{why(r, line)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            )}
        </section>
    );
}
