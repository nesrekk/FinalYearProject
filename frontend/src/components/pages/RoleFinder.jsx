import React, { useEffect, useMemo, useState } from 'react';
import { fetchRoleFinder, fetchRoleFinderOptions } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import CopyLinkButton from '../common/CopyLinkButton';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const signed = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}`);
const money = (v) => (v == null ? '—' : `$${(v / 1e6).toFixed(1)}M`);
const TOP_N = [10, 25, 50, 100];
const MAX_CUSTOM = 8;

// How each component's raw value reads in the table. Stored z-scores have
// no other raw value, so only the z is shown for them.
const VALUE_FORMAT = {
    dfg: (v) => `${signed(v * 100, 1)} pp`,
    stl36: (v) => v.toFixed(1),
    blk36: (v) => v.toFixed(1),
    dbpm: (v) => signed(v, 1),
    obpm: (v) => signed(v, 1),
    gravity: (v) => signed(v, 1),
    reb_pct: (v) => `${(v * 100).toFixed(1)}%`,
    oreb_pct: (v) => `${(v * 100).toFixed(1)}%`,
    fg3_pct: (v) => `${(v * 100).toFixed(1)}%`,
    ts_pct: (v) => `${(v * 100).toFixed(1)}%`,
    usg_pct: (v) => `${(v * 100).toFixed(1)}%`,
    ast_pct: (v) => `${(v * 100).toFixed(1)}%`,
    tov_pct: (v) => `${(v * 100).toFixed(1)}%`,
    ft_rate: (v) => v.toFixed(2),
};

const weightsEqual = (a, b) => a.length === b.length && a.every(([k, w], i) => b[i][0] === k && Number(b[i][1]) === Number(w));

// `w=dad_pos:1,cs_pct:1` from a link: known components, once each, weights
// in the slider's range and steps.
function weightsFromParam(raw, byKey) {
    const seen = new Set();
    const out = [];
    for (const part of raw ?? []) {
        const [key, w] = part.split(':');
        const weight = Number(w);
        if (!byKey[key] || seen.has(key) || !Number.isFinite(weight) || Math.abs(weight) > 3 || (weight * 4) % 1 !== 0) continue;
        seen.add(key);
        out.push([key, weight]);
    }
    return out.length ? out.slice(0, MAX_CUSTOM) : null;
}

function initialForm(params, o) {
    const presetKeys = o.presets.map((p) => p.key);
    const byKey = Object.fromEntries(o.components.map((c) => [c.key, c]));
    const presetKey = parseParam.oneOf(params, 'p', [...presetKeys, 'custom']) ?? presetKeys[0];
    const preset = o.presets.find((p) => p.key === presetKey);
    const fromLink = weightsFromParam(parseParam.list(params, 'w'), byKey);
    const weights = fromLink ?? (preset ? preset.weights.map((w) => [w.key, w.weight]) : [['dad_pos', 1], ['cs_pct', 1]]);
    const positions = (parseParam.list(params, 'pos') ?? preset?.positions ?? o.positions).filter((p) => o.positions.includes(p));
    const usg = parseParam.num(params, 'usg', { min: 0, max: 60 });
    return {
        preset: presetKey,
        weights,
        season: parseParam.int(params, 's', { min: o.seasons.from, max: o.seasons.to }) ?? o.seasons.to,
        positions: positions.length ? positions : o.positions,
        relative: parseParam.oneOf(params, 'rel', ['league', 'positions']) ?? preset?.relative ?? 'league',
        maxUsg: usg ?? (params.has('usg') ? '' : (preset?.max_usg ?? '')),
        maxSalary: parseParam.num(params, 'sal', { min: 0, max: 100 }) ?? '',
        topN: TOP_N.includes(parseParam.int(params, 'n')) ? parseParam.int(params, 'n') : 25,
    };
}

export default function RoleFinder() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        fetchRoleFinderOptions()
            .then((o) => {
                setOptions(o);
                setForm(initialForm(params, o));
            })
            .catch(() => setOptionsError('The Role Finder options couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    const byKey = useMemo(() => Object.fromEntries((options?.components ?? []).map((c) => [c.key, c])), [options]);
    const preset = useMemo(() => options?.presets.find((p) => p.key === form?.preset), [options, form?.preset]);
    const presetWeights = useMemo(() => (preset ? preset.weights.map((w) => [w.key, w.weight]) : null), [preset]);
    const edited = !!(form && presetWeights && !weightsEqual(form.weights, presetWeights));
    const active = form ? form.weights.filter(([, w]) => Number(w) !== 0) : [];
    const weightString = active.map(([k, w]) => `${k}:${w}`).join(',');
    const salaryAvailable = !!(options && form && options.salary_seasons.includes(form.season));

    useUrlSync(!form ? null : {
        p: form.preset,
        w: form.preset === 'custom' || edited ? form.weights.map(([k, w]) => `${k}:${w}`) : null,
        s: form.season,
        pos: form.positions,
        rel: form.relative,
        usg: form.maxUsg === '' ? null : form.maxUsg,
        sal: form.maxSalary === '' ? null : form.maxSalary,
        n: form.topN,
    });

    useEffect(() => {
        if (!form) return undefined;
        if (!weightString) {
            setData(null);
            setError('Give at least one component a weight other than 0.');
            return undefined;
        }
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                setData(await fetchRoleFinder({
                    preset: form.preset,
                    weights: form.preset === 'custom' || edited ? weightString : undefined,
                    season: form.season,
                    positions: form.positions.join(','),
                    relative: form.relative,
                    max_usg: form.maxUsg === '' ? undefined : form.maxUsg,
                    max_salary: form.maxSalary === '' ? undefined : form.maxSalary * 1e6,
                    top_n: form.topN,
                }));
            } catch (err) {
                setData(null);
                setError(err.response?.data?.detail || 'Failed to rank the players.');
            } finally {
                setLoading(false);
            }
        }, 350);
        return () => clearTimeout(timer);
    }, [form, weightString, edited]);

    if (optionsError) return <p className="error-message">{optionsError}</p>;
    if (!options || !form) return <Loader />;

    const pickPreset = (key) => {
        const p = options.presets.find((x) => x.key === key);
        setForm((f) => ({
            ...f,
            preset: key,
            weights: p ? p.weights.map((w) => [w.key, w.weight]) : f.weights,
            positions: p ? p.positions : f.positions,
            relative: p ? p.relative : f.relative,
            maxUsg: p ? (p.max_usg ?? '') : f.maxUsg,
        }));
    };
    const setWeight = (i, patch) => setForm((f) => ({
        ...f, weights: f.weights.map((w, j) => (j === i ? [patch.key ?? w[0], patch.w ?? w[1]] : w)),
    }));
    const togglePosition = (pos) => setForm((f) => {
        const next = f.positions.includes(pos) ? f.positions.filter((p) => p !== pos) : [...f.positions, pos];
        return { ...f, positions: options.positions.filter((p) => next.includes(p)) };
    });
    const seasonOptions = [];
    for (let s = options.seasons.to; s >= options.seasons.from; s -= 1) seasonOptions.push(s);
    const groups = [...new Set(options.components.map((c) => c.group))];
    const unused = options.components.find((c) => !form.weights.some(([k]) => k === c.key));
    const shown = data?.weights ?? [];

    return (
        <div className="role-finder">
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Pick the role you need and get a ranked list from the tracking era ({seasonLabel(options.seasons.from)} to{' '}
                {seasonLabel(options.seasons.to)}), with every component&apos;s share of the score. The weights are a
                judgment call, shown in full below; change them and the ranking changes.
                <InfoTooltip label="How the Role Finder scores players" title="Under the hood">
                    {data?.method ?? 'Score = the sum of weight × z-score over the role\'s components, within one season.'}
                </InfoTooltip>
                <SourceBadge source={data?._source ?? options._source} />
                <CopyLinkButton />
            </p>

            <div className="lb-presets" aria-label="Roles">
                {options.presets.map((p) => (
                    <button key={p.key} type="button" aria-pressed={form.preset === p.key} onClick={() => pickPreset(p.key)}>
                        {p.label}
                    </button>
                ))}
                <button type="button" aria-pressed={form.preset === 'custom'} onClick={() => pickPreset('custom')}>Custom</button>
            </div>
            {preset && <p className="rf-blurb">{preset.blurb}</p>}

            <div className="dashboard-card rf-weights-card">
                <div className="rf-weights-head">
                    <h3>The weights{preset ? ` for ${preset.label}` : ''}{edited ? ' (edited)' : ''}</h3>
                    {edited && (
                        <button type="button" className="cb-add" onClick={() => setForm((f) => ({ ...f, weights: presetWeights }))}>
                            Reset to the preset
                        </button>
                    )}
                </div>
                <div className="cb-weights">
                    {form.weights.map(([key, w], i) => (
                        <div className="cb-weight" key={`${key}-${i}`}>
                            <select
                                className="input-field"
                                aria-label={`Component ${i + 1}`}
                                value={key}
                                onChange={(e) => setWeight(i, { key: e.target.value })}
                            >
                                {groups.map((g) => (
                                    <optgroup key={g} label={g}>
                                        {options.components.filter((c) => c.group === g).map((c) => (
                                            <option key={c.key} value={c.key} disabled={c.key !== key && form.weights.some(([k]) => k === c.key)}>
                                                {c.label}{c.higher_is_better ? '' : ' (lower is better)'}
                                            </option>
                                        ))}
                                    </optgroup>
                                ))}
                            </select>
                            <input
                                type="range" min={-3} max={3} step={0.25} value={w}
                                aria-label={`Weight for ${byKey[key]?.label ?? key}`}
                                onChange={(e) => setWeight(i, { w: Number(e.target.value) })}
                            />
                            <span className="cb-weight-value">{w > 0 ? `+${w}` : w}</span>
                            <button
                                type="button" className="cb-remove"
                                aria-label={`Remove ${byKey[key]?.label ?? key}`}
                                onClick={() => setForm((f) => ({ ...f, weights: f.weights.filter((_, j) => j !== i) }))}
                            >
                                ×
                            </button>
                        </div>
                    ))}
                    {form.weights.length < MAX_CUSTOM && unused && (
                        <button type="button" className="cb-add" onClick={() => setForm((f) => ({ ...f, weights: [...f.weights, [unused.key, 1]] }))}>
                            + Add a component
                        </button>
                    )}
                </div>
                {byKey[form.weights[0]?.[0]] && (
                    <ul className="rf-component-notes">
                        {form.weights.map(([key]) => byKey[key] && (
                            <li key={key}><strong>{byKey[key].label}:</strong> {byKey[key].description}</li>
                        ))}
                    </ul>
                )}
            </div>

            <div className="lb-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={form.season} onChange={(e) => setForm((f) => ({ ...f, season: Number(e.target.value) }))}>
                        {seasonOptions.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Compare with</span>
                    <select className="input-field" value={form.relative} onChange={(e) => setForm((f) => ({ ...f, relative: e.target.value }))}>
                        <option value="league">The whole league</option>
                        <option value="positions">The chosen positions</option>
                    </select>
                </label>
                <label>
                    <span>Max. usage %</span>
                    <input className="input-field" type="number" min={0} max={60} step={0.5} value={form.maxUsg} placeholder="none"
                        onChange={(e) => setForm((f) => ({ ...f, maxUsg: e.target.value === '' ? '' : Number(e.target.value) }))} />
                </label>
                <label>
                    <span>Max. salary ($M)</span>
                    <input className="input-field" type="number" min={0} max={100} step={0.5} value={form.maxSalary}
                        placeholder={salaryAvailable ? 'none' : 'no data'} disabled={!salaryAvailable}
                        title={salaryAvailable ? '' : `No contract data for ${seasonLabel(form.season)}`}
                        onChange={(e) => setForm((f) => ({ ...f, maxSalary: e.target.value === '' ? '' : Number(e.target.value) }))} />
                </label>
                <label>
                    <span>Show</span>
                    <select className="input-field" value={form.topN} onChange={(e) => setForm((f) => ({ ...f, topN: Number(e.target.value) }))}>
                        {TOP_N.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
            </div>
            <div className="rf-positions" role="group" aria-label="Positions">
                <span className="rf-positions-label">Positions</span>
                <div className="lb-presets">
                    {options.positions.map((pos) => (
                        <button key={pos} type="button" aria-pressed={form.positions.includes(pos)} onClick={() => togglePosition(pos)}>
                            {pos}
                        </button>
                    ))}
                </div>
            </div>
            {!salaryAvailable && (
                <p className="lb-note rf-salary-note">
                    Salary data covers {options.salary_seasons.map(seasonLabel).join(', ')} only, so there is no salary filter or salary
                    column for {seasonLabel(form.season)}.
                </p>
            )}

            {error && <p className="error-message">{error}</p>}
            {loading && !data && <Loader />}
            {data && (
                <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                    <p className="page-subtitle lb-summary">
                        <strong>{data.preset.label}</strong>, {seasonLabel(data.filters.season)}: {data.pool} of {data.pool_total} players with
                        500+ minutes ({data.filters.positions.join(', ')}
                        {data.filters.max_usg != null ? `, usage ≤ ${data.filters.max_usg}%` : ''}
                        {data.filters.max_salary != null ? `, salary ≤ ${money(data.filters.max_salary)}` : ''}),
                        z-scores {data.filters.relative === 'positions' ? 'within these positions' : 'within the whole league'}.
                        Fit flags come from the Scouting Report; {data.flags_tested} of the {data.results.length} listed had splits tested.
                        {data.notes.map((n) => <span key={n} className="lb-note"> {n}</span>)}
                    </p>
                    {data.results.length === 0 ? (
                        <p className="empty-message">No players pass these filters.</p>
                    ) : (
                        <>
                            <TableExport name={`role finder ${data.preset.label} ${seasonLabel(data.filters.season)}`} />
                            <div className="table-wrapper">
                                <table className="data-table lb-table rf-table">
                                    <thead>
                                        <tr>
                                            <th>#</th>
                                            <th>Player</th>
                                            <th>Team</th>
                                            <th>Pos</th>
                                            <th>Role</th>
                                            <th className="lb-num lb-stat">Score</th>
                                            {shown.map((w) => (
                                                <th key={w.key} className="lb-num" title={`weight ${w.weight > 0 ? '+' : ''}${w.weight}`}>
                                                    {w.label} <span className="cb-z">×{w.weight}</span>
                                                </th>
                                            ))}
                                            <th>Fit flags</th>
                                            <th className="lb-num">MIN</th>
                                            <th className="lb-num">USG%</th>
                                            {data.filters.salary_available && <th className="lb-num">Salary</th>}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.results.map((r) => (
                                            <tr key={r.player_id}>
                                                <td>{r.rank}</td>
                                                <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                                <td>{r.team}</td>
                                                <td>{r.position}</td>
                                                <td className="rf-role">{r.role ?? '—'}</td>
                                                <td className="lb-num lb-stat">{signed(r.score, 2)}</td>
                                                {shown.map((w) => {
                                                    const part = r.parts[w.key];
                                                    const fmt = w.source === 'pool_z' && VALUE_FORMAT[w.key];
                                                    return (
                                                        <td key={w.key} className="lb-num">
                                                            {fmt && part.value != null ? <>{fmt(part.value)} </> : null}
                                                            <span className={fmt ? 'cb-z' : ''}>{fmt ? `(${signed(part.z)})` : `z ${signed(part.z)}`}</span>
                                                            <span className="rf-contrib">{signed(part.contribution, 2)} pts</span>
                                                        </td>
                                                    );
                                                })}
                                                <td className="rf-flags">
                                                    {r.flags.length === 0
                                                        ? <span className="cb-z">{data.flags_tested ? 'none significant' : ''}</span>
                                                        : r.flags.map((f) => (
                                                            <span
                                                                key={f.label}
                                                                className={`rf-flag rf-flag--${f.direction}`}
                                                                title={`${f.direction}: z ${signed(f.z)}, n = ${f.n} ${f.n_unit}`}
                                                            >
                                                                {f.direction === 'strength' ? '▲' : '▼'} {f.label}
                                                            </span>
                                                        ))}
                                                </td>
                                                <td className="lb-num">{r.context.min?.toFixed(1) ?? '—'}</td>
                                                <td className="lb-num">{r.context.usg_pct?.toFixed(1) ?? '—'}</td>
                                                {data.filters.salary_available && <td className="lb-num">{money(r.salary)}</td>}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
