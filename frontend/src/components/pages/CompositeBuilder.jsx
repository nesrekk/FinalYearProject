import React, { useEffect, useState } from 'react';
import { fetchCompositeLeaderboard } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const FORMATS = {
    num1: (v) => v.toFixed(1),
    num2: (v) => v.toFixed(2),
    pct: (v) => `${(v * 100).toFixed(1)}%`,
    signed1: (v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}`,
    int: (v) => v.toFixed(0),
};
const fmt = (format, v) => (v == null ? '—' : FORMATS[format](v));
const signed = (v, d = 2) => `${v > 0 ? '+' : ''}${v.toFixed(d)}`;
const MAX_STATS = 8;
const TOP_N = [10, 25, 50, 100];

const PRESETS = [
    { label: 'Efficient scorer', weights: [['pts', 1], ['ts_pct', 1]] },
    { label: 'Do-everything', weights: [['pts', 1], ['reb', 1], ['ast', 1]] },
    { label: 'Defensive anchor', weights: [['blk', 1], ['stl', 1], ['dbpm', 2], ['reb_pct', 1]] },
    { label: 'Floor spacer', weights: [['fg3m', 1], ['fg3_pct', 1]] },
    { label: 'Careful playmaker', weights: [['ast_pct', 2], ['tov_pct', 1]] },
];

// `w=pts:1,ts_pct:2` from a shared link: known stats, once each, weights
// in the slider's range and steps. null if nothing usable is left.
function weightsFromParam(raw, byKey) {
    const seen = new Set();
    const out = [];
    for (const part of raw ?? []) {
        const [key, w] = part.split(':');
        const weight = Number(w);
        if (!byKey[key] || seen.has(key) || !Number.isFinite(weight) || Math.abs(weight) > 3 || (weight * 2) % 1 !== 0) continue;
        seen.add(key);
        out.push([key, weight]);
    }
    return out.length ? out.slice(0, MAX_STATS) : null;
}

// stats: the catalogue from /leaderboard/options; seasons: { from, to }.
export default function CompositeBuilder({ stats, seasons, teams }) {
    const usable = stats.filter((s) => s.key !== 'age');
    const byKey = Object.fromEntries(usable.map((s) => [s.key, s]));
    // Inputs start from the link (utils/useUrlState.js); switching over from
    // the single-stat mode carries its seasons, floors, team and size along.
    const params = useInitialParams();
    const season = (key) => parseParam.int(params, key, { min: seasons.from, max: seasons.to }) ?? seasons.to;
    const [weights, setWeights] = useState(() => weightsFromParam(parseParam.list(params, 'w'), byKey) ?? PRESETS[0].weights);
    const [range, setRange] = useState(() => ({ from: season('from'), to: season('to') }));
    const [minGp, setMinGp] = useState(() => parseParam.int(params, 'gp', { min: 0, max: 82 }) ?? 30);
    const [minMpg, setMinMpg] = useState(() => parseParam.num(params, 'mpg', { min: 0, max: 48 }) ?? 20);
    const [team, setTeam] = useState(() => parseParam.oneOf(params, 'team', teams.map((t) => t.team)) ?? '');
    const [topN, setTopN] = useState(() => {
        const n = parseParam.int(params, 'n');
        return TOP_N.includes(n) ? n : 25;
    });
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const active = weights.filter(([, w]) => Number(w) !== 0);
    const weightString = active.map(([k, w]) => `${k}:${w}`).join(',');

    useUrlSync({
        w: weights.map(([k, w]) => `${k}:${w}`), from: range.from, to: range.to,
        gp: minGp || 0, mpg: minMpg || 0, team, n: topN,
    });

    useEffect(() => {
        if (!weightString) {
            setData(null);
            setError('Give at least one stat a weight other than 0.');
            return undefined;
        }
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                setData(await fetchCompositeLeaderboard({
                    weights: weightString, season_from: range.from, season_to: range.to,
                    min_gp: minGp || 0, min_mpg: minMpg || 0, team: team || undefined, top_n: topN,
                }));
            } catch (err) {
                setData(null);
                setError(err.response?.data?.detail || 'Failed to build the metric.');
            } finally {
                setLoading(false);
            }
        }, 350);
        return () => clearTimeout(timer);
    }, [weightString, range, minGp, minMpg, team, topN]);

    const seasonOptions = [];
    for (let s = seasons.to; s >= seasons.from; s -= 1) seasonOptions.push(s);
    const groups = [...new Set(usable.map((s) => s.group))];
    const setWeight = (i, patch) => setWeights((ws) => ws.map((w, j) => (j === i ? [patch.key ?? w[0], patch.w ?? w[1]] : w)));
    const unused = usable.find((s) => !weights.some(([k]) => k === s.key));
    const shown = data?.weights ?? [];
    const range_ = data && (data.filters.season_from === data.filters.season_to
        ? seasonLabel(data.filters.season_to)
        : `${seasonLabel(data.filters.season_from)} to ${seasonLabel(data.filters.season_to)}`);

    return (
        <>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Pick stats and weights. Each stat becomes a z-score within its season&apos;s qualified players,
                so your metric reads in standard deviations and seasons from different eras compare fairly.
                <InfoTooltip label="How the composite score works" title="Under the hood">
                    {data?.method ?? 'Score = the sum of weight × z-score, each stat z-scored within its own season.'}
                </InfoTooltip>
                <SourceBadge source={data?._source} />
            </p>

            <div className="lb-presets" aria-label="Presets">
                {PRESETS.map((p) => (
                    <button key={p.label} type="button" onClick={() => setWeights(p.weights)}>{p.label}</button>
                ))}
            </div>

            <div className="cb-weights">
                {weights.map(([key, w], i) => (
                    <div className="cb-weight" key={`${key}-${i}`}>
                        <select
                            className="input-field"
                            aria-label={`Stat ${i + 1}`}
                            value={key}
                            onChange={(e) => setWeight(i, { key: e.target.value })}
                        >
                            {groups.map((g) => (
                                <optgroup key={g} label={g}>
                                    {usable.filter((s) => s.group === g).map((s) => (
                                        <option key={s.key} value={s.key} disabled={s.key !== key && weights.some(([k]) => k === s.key)}>
                                            {s.label}{s.higher_is_better ? '' : ' (lower is better)'}
                                        </option>
                                    ))}
                                </optgroup>
                            ))}
                        </select>
                        <input
                            type="range" min={-3} max={3} step={0.5} value={w}
                            aria-label={`Weight for ${byKey[key]?.label ?? key}`}
                            onChange={(e) => setWeight(i, { w: Number(e.target.value) })}
                        />
                        <span className="cb-weight-value">{w > 0 ? `+${w}` : w}</span>
                        <button
                            type="button" className="cb-remove"
                            aria-label={`Remove ${byKey[key]?.label ?? key}`}
                            onClick={() => setWeights((ws) => ws.filter((_, j) => j !== i))}
                        >
                            ×
                        </button>
                    </div>
                ))}
                {weights.length < MAX_STATS && unused && (
                    <button type="button" className="cb-add" onClick={() => setWeights((ws) => [...ws, [unused.key, 1]])}>
                        + Add a stat
                    </button>
                )}
            </div>

            <div className="lb-controls">
                <label>
                    <span>From</span>
                    <select className="input-field" value={range.from} onChange={(e) => setRange((r) => ({ ...r, from: Number(e.target.value) }))}>
                        {seasonOptions.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>To</span>
                    <select className="input-field" value={range.to} onChange={(e) => setRange((r) => ({ ...r, to: Number(e.target.value) }))}>
                        {seasonOptions.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Min. games</span>
                    <input className="input-field" type="number" min={0} max={82} value={minGp}
                        onChange={(e) => setMinGp(e.target.value === '' ? '' : Number(e.target.value))} />
                </label>
                <label>
                    <span>Min. minutes a game</span>
                    <input className="input-field" type="number" min={0} max={48} value={minMpg}
                        onChange={(e) => setMinMpg(e.target.value === '' ? '' : Number(e.target.value))} />
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={team} onChange={(e) => setTeam(e.target.value)}>
                        <option value="">All teams</option>
                        {teams.filter((t) => t.to >= Math.min(range.from, range.to) && t.from <= Math.max(range.from, range.to))
                            .map((t) => <option key={t.team} value={t.team}>{t.team}</option>)}
                    </select>
                </label>
                <label>
                    <span>Show</span>
                    <select className="input-field" value={topN} onChange={(e) => setTopN(Number(e.target.value))}>
                        {TOP_N.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
            </div>

            {error && <p className="error-message">{error}</p>}
            {loading && !data && <Loader />}
            {data && (
                <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                    <p className="page-subtitle lb-summary">
                        <strong>
                            {shown.map((w) => `${w.weight > 0 ? '+' : ''}${w.weight} × ${w.label}`).join('  ')}
                        </strong>
                        , {range_}: {data.pool.toLocaleString()} player-seasons ({data.filters.min_gp}+ games,{' '}
                        {data.filters.min_mpg}+ minutes{data.filters.team ? `, ${data.filters.team}` : ''}).
                        {data.notes.map((n) => <span key={n} className="lb-note"> {n}</span>)}
                    </p>
                    {data.results.length === 0 ? (
                        <p className="empty-message">No player-seasons pass these filters.</p>
                    ) : (
                        <>
                            <TableExport name={`custom metric ${range_}`} />
                            <div className="table-wrapper">
                                <table className="data-table lb-table">
                                    <thead>
                                        <tr>
                                            <th>#</th>
                                            <th>Player</th>
                                            <th>Season</th>
                                            <th>Team</th>
                                            <th className="lb-num lb-stat">Score</th>
                                            {shown.map((w) => (
                                                <th key={w.key} className="lb-num">{w.label} (z)</th>
                                            ))}
                                            <th className="lb-num">GP</th>
                                            <th className="lb-num">MIN</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.results.map((r) => (
                                            <tr key={`${r.player_id}-${r.season}`}>
                                                <td>{r.rank}</td>
                                                <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                                <td>{seasonLabel(r.season)}</td>
                                                <td>{r.team}</td>
                                                <td className="lb-num lb-stat">{signed(r.score)}</td>
                                                {shown.map((w) => (
                                                    <td key={w.key} className="lb-num">
                                                        {fmt(w.format, r.parts[w.key].value)}{' '}
                                                        <span className="cb-z">({signed(r.parts[w.key].z, 1)})</span>
                                                    </td>
                                                ))}
                                                <td className="lb-num">{r.gp}</td>
                                                <td className="lb-num">{fmt('num1', r.min)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            )}
        </>
    );
}
