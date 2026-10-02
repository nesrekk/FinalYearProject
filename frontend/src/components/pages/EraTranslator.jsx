import React, { useEffect, useRef, useState } from 'react';
import { fetchEraPlayers, fetchEraTranslation } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import ChartExport from '../common/ChartExport';
import ChartTooltip from '../common/ChartTooltip';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import AutocompleteDropdown from '../common/AutocompleteDropdown';
import useChartCrosshair from '../../utils/useChartCrosshair';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { signed as signedNum } from '../../utils/format';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const fmtVal = (format, v) => (v == null ? '—' : format === 'pct' ? `${(v * 100).toFixed(1)}%` : v.toFixed(1));
const signed = (v, d = 2) => signedNum(v, d);
const ordinal = (n) => {
    const s = ['th', 'st', 'nd', 'rd'];
    const v = n % 100;
    return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
};

// Wilt Chamberlain 1961-62: the season everyone wants translated.
const DEFAULT = { playerId: 76375, season: 1962 };
const EXAMPLES = [
    { label: 'Wilt 1961-62', playerId: 76375, season: 1962 },
    { label: 'Oscar 1961-62', playerId: 600015, season: 1962 },
    { label: 'Mikan 1949-50', playerId: 600012, season: 1950 },
    { label: 'Westbrook 2016-17 → 1961-62', playerId: 201566, season: 2017, target: 1962 },
    { label: 'Jokić 2025-26 → 1985-86', playerId: 203999, season: 2026, target: 1986 },
];

// ─── League pace, every season (hand-built SVG like the app's other charts) ──
const M = { l: 44, r: 14, t: 16, b: 30 };

function PaceChart({ series, source, target }) {
    const boxRef = useRef(null);
    const svgRef = useRef(null);
    const [W, setW] = useState(720);
    const H = W < 520 ? 190 : 220;

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

    const s0 = series[0].season;
    const s1 = series[series.length - 1].season;
    const y0 = 85;
    const y1 = 135;
    const sx = (s) => M.l + ((s - s0) / (s1 - s0)) * (W - M.l - M.r);
    const sy = (v) => H - M.b - ((v - y0) / (y1 - y0)) * (H - M.t - M.b);
    const path = (pts) => pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.season).toFixed(1)},${sy(p.pace).toFixed(1)}`).join('');
    // Estimated paces (before 1973-74) dashed; the join season belongs to both.
    const firstMeasured = series.findIndex((p) => p.pace_source === 'bref');
    const est = firstMeasured > 0 ? series.slice(0, firstMeasured + 1) : [];
    const measured = firstMeasured >= 0 ? series.slice(firstMeasured) : [];
    const ticks = series.map((p) => p.season).filter((s) => s % (W < 520 ? 20 : 10) === 0);
    const bySeason = Object.fromEntries(series.map((p) => [p.season, p]));
    const marks = [
        { s: source, cls: 'era-mark era-mark--source', label: seasonLabel(source) },
        ...(target !== source ? [{ s: target, cls: 'era-mark era-mark--target', label: seasonLabel(target) }] : []),
    ].filter((m) => bySeason[m.s]);

    const crosshairPoints = series.map((p) => ({ x: sx(p.season), y: sy(p.pace), p }));
    const { point: hovered, overlayProps } = useChartCrosshair(crosshairPoints, W);

    return (
        <div className="rx-chart era-chart" ref={boxRef}>
            <ChartExport svgRef={svgRef} name="league pace by season" />
            <div style={{ position: 'relative' }}>
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                aria-label={`League pace by season, ${seasonLabel(s0)} to ${seasonLabel(s1)}: ${seasonLabel(source)} ${bySeason[source]?.pace}, ${seasonLabel(target)} ${bySeason[target]?.pace} possessions per 48 minutes.`}>
                {[90, 100, 110, 120, 130].map((t) => (
                    <g key={t}>
                        <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="rx-tick" x={M.l - 6} y={sy(t) + 4} textAnchor="end">{t}</text>
                    </g>
                ))}
                {ticks.map((s) => (
                    <text key={s} className="rx-tick" x={sx(s)} y={H - M.b + 18} textAnchor="middle">{s}</text>
                ))}
                {est.length > 0 && <path className="era-line era-line--est" d={path(est)} />}
                {measured.length > 0 && <path className="era-line" d={path(measured)} />}
                {hovered && <line x1={hovered.x} y1={M.t} x2={hovered.x} y2={H - M.b} className="chart-crosshair-line" />}
                {marks.map((m) => {
                    const x = sx(m.s);
                    const y = sy(bySeason[m.s].pace);
                    const anchor = x > W - 90 ? 'end' : x < M.l + 60 ? 'start' : 'middle';
                    return (
                        <g key={m.cls} className={m.cls}>
                            <circle cx={x} cy={y} r={5.5} />
                            <text className="era-mark-label" x={x} y={y - 11} textAnchor={anchor}>
                                {m.label}: {bySeason[m.s].pace.toFixed(1)}
                            </text>
                        </g>
                    );
                })}
                <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b}
                    className="chart-crosshair-overlay" role="slider" aria-label="League pace by season, use arrow keys to step through"
                    aria-valuetext={hovered ? `${seasonLabel(hovered.p.season)}: ${hovered.p.pace.toFixed(1)} possessions per 48 minutes` : undefined}
                    {...overlayProps} />
            </svg>
            {hovered && (
                <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={W} chartHeight={H}>
                    <div style={{ fontWeight: 600 }}>{seasonLabel(hovered.p.season)}</div>
                    <div>{hovered.p.pace.toFixed(1)} possessions per 48 min</div>
                    {hovered.p.pace_source !== 'bref' && <div style={{ color: 'var(--text-3)' }}>Estimated</div>}
                </ChartTooltip>
            )}
            </div>
            <p className="era-chart-key">
                League pace, possessions per 48 minutes; seasons by the year they ended. <span className="era-key-est">Dashed</span>: estimated
                (before 1973-74 offensive rebounds and turnovers weren&apos;t recorded; 1949-50 estimated by this app).
            </p>
        </div>
    );
}

// ─── Player search (local database, so pre-1996 players are found too) ────
function PlayerSearch({ onPick }) {
    const inputRef = useRef(null);
    const [q, setQ] = useState('');
    const [hits, setHits] = useState([]);

    useEffect(() => {
        const query = q.trim();
        if (query.length < 2) return undefined;
        let live = true;
        const timer = setTimeout(() => {
            fetchEraPlayers(query)
                .then((d) => { if (live) setHits(d.results); })
                .catch(() => { if (live) setHits([]); });
        }, 200);
        return () => { live = false; clearTimeout(timer); };
    }, [q]);

    const shown = q.trim().length >= 2 ? hits : [];
    const label = (p) => `${p.player_name} (${seasonLabel(p.from)}${p.to !== p.from ? ` to ${seasonLabel(p.to)}` : ''})`;
    const labels = shown.map(label);
    return (
        <label className="era-search">
            <span>Player</span>
            <input ref={inputRef} className="input-field" type="search" value={q} placeholder="Search any player since 1949-50"
                autoComplete="off" onChange={(e) => setQ(e.target.value)} />
            <AutocompleteDropdown anchorRef={inputRef} items={labels}
                onPick={(l) => {
                    const p = shown[labels.indexOf(l)];
                    if (p) onPick(p);
                    setQ('');
                    setHits([]);
                }} />
        </label>
    );
}

export default function EraTranslator() {
    const params = useInitialParams();
    const [form, setForm] = useState(() => {
        const playerId = parseParam.int(params, 'player', { min: 1 });
        return {
            playerId: playerId ?? DEFAULT.playerId,
            season: parseParam.int(params, 'season', { min: 1900, max: 2100 }) ?? (playerId ? null : DEFAULT.season),
            target: parseParam.int(params, 'to', { min: 1900, max: 2100 }),
        };
    });
    // The last answer, tagged with the request it answers; loading = the
    // current form hasn't been answered yet (the old answer stays, dimmed).
    const reqKey = JSON.stringify(form);
    const [result, setResult] = useState(null);
    const data = result?.data ?? null;
    const error = result?.key === reqKey ? result.error : '';
    const loading = result?.key !== reqKey;

    // Season and target are written once the API has resolved them, so a
    // link keeps the same view after a newer season is loaded.
    const answered = data?.player.player_id === form.playerId ? data : null;
    useUrlSync({
        player: form.playerId,
        season: form.season ?? answered?.season ?? null,
        to: form.target ?? answered?.target ?? null,
    });

    useEffect(() => {
        let live = true;
        const f = JSON.parse(reqKey);
        fetchEraTranslation({ player_id: f.playerId, season: f.season ?? undefined, target: f.target ?? undefined })
            .then((d) => { if (live) setResult({ key: reqKey, data: d, error: '' }); })
            .catch((err) => {
                if (!live) return;
                setResult({
                    key: reqKey, data: null,
                    error: err.response?.data?.detail || 'The translation couldn\'t load. Is the impact API (port 8002) running?',
                });
            });
        return () => { live = false; };
    }, [reqKey]);

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const targets = data ? Array.from({ length: data.targets.to - data.targets.from + 1 }, (_, i) => data.targets.to - i) : [];
    const env = data?.environment;
    const byKey = Object.fromEntries((data?.rows ?? []).map((r) => [r.key, r]));
    const pts = byKey.pts;
    const p = data?.player;

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Era Translator
                <InfoTooltip label="How the Era Translator works" title="Under the hood">
                    {data?.method ?? 'Counting stats scaled by league pace, and by league averages; percentages shifted by the change in the league average.'}
                </InfoTooltip>
                <SourceBadge source={data?._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="era" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Restate any player-season in another season&apos;s game: its pace, and what an average team scored,
                rebounded and shot. Two answers, because &ldquo;what would that line be today&rdquo; has more than one.
            </p>

            <div className="lb-presets" aria-label="Examples">
                {EXAMPLES.map((ex) => (
                    <button key={ex.label} type="button"
                        onClick={() => setForm({ playerId: ex.playerId, season: ex.season, target: ex.target ?? null })}>
                        {ex.label}
                    </button>
                ))}
            </div>

            <div className="lb-controls">
                <PlayerSearch onPick={(pl) => setForm({ playerId: pl.player_id, season: null, target: form.target })} />
                <label>
                    <span>Season</span>
                    <select className="input-field" value={data?.season ?? ''} disabled={!data}
                        onChange={(e) => set({ season: Number(e.target.value) })}>
                        {(data?.seasons ?? []).slice().reverse().map((s) => (
                            <option key={s.season} value={s.season}>{seasonLabel(s.season)} · {s.team} · {s.gp} g</option>
                        ))}
                    </select>
                </label>
                <label>
                    <span>Translate into</span>
                    <select className="input-field" value={data?.target ?? ''} disabled={!data}
                        onChange={(e) => set({ target: Number(e.target.value) })}>
                        {targets.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
            </div>

            {error && <p className="error-message">{error}</p>}
            {loading && !data && <Loader />}
            {data && (
                <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                    <p className="rx-verdict">
                        <strong><PlayerName playerId={p.player_id} name={p.player_name} /></strong>,{' '}
                        {seasonLabel(data.season)} ({p.team}, {p.gp} games{p.min != null ? `, ${p.min.toFixed(1)} minutes a game` : ''}):{' '}
                        {pts?.original != null && pts.pace_adjusted != null ? (
                            <>
                                {fmtVal('num1', pts.original)} points a game becomes{' '}
                                <strong>{fmtVal('num1', pts.pace_adjusted)}</strong> at {seasonLabel(data.target)}&apos;s pace,
                                or <strong>{fmtVal('num1', pts.league_adjusted)}</strong> at the same share of an average
                                team&apos;s scoring.
                            </>
                        ) : 'no points to translate.'}
                        {pts?.standing && (
                            <> Against that season&apos;s qualified players: {signed(pts.standing.z, 1)} standard deviations from the
                                average scorer ({ordinal(pts.standing.rank)} of {pts.standing.pool}).</>
                        )}
                    </p>
                    {data.season === data.target && (
                        <p className="era-note lb-note">Same season on both sides: pick another season under
                            &ldquo;Translate into&rdquo;.</p>
                    )}
                    {data.notes.map((n) => (
                        <p key={n} className={n.startsWith('Small sample') ? 'era-note era-note--warn' : 'era-note'}>{n}</p>
                    ))}

                    <div className="era-env" role="group" aria-label="The two seasons' league environment">
                        {[
                            ['League pace', env.source.pace.toFixed(1), env.target.pace.toFixed(1), `×${env.pace_factor.toFixed(2)}`],
                            ['League points a team game', env.source.pts.toFixed(1), env.target.pts.toFixed(1),
                                `×${(env.target.pts / env.source.pts).toFixed(2)}`],
                            ['League true shooting', fmtVal('pct', env.source.ts_pct), fmtVal('pct', env.target.ts_pct),
                                `${signed((env.target.ts_pct - env.source.ts_pct) * 100, 1)} pts`],
                        ].map(([label, a, b, f]) => (
                            <div key={label} className="era-env-item">
                                <span className="era-env-label">{label}</span>
                                <span className="era-env-values">{a} → {b}</span>
                                <span className="era-env-factor">{f}</span>
                            </div>
                        ))}
                    </div>

                    <PaceChart series={data.league_series} source={data.season} target={data.target} />

                    <TableExport name={`era translator ${p.player_name} ${seasonLabel(data.season)} to ${seasonLabel(data.target)}`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table era-table">
                            <thead>
                                <tr>
                                    <th>Stat</th>
                                    <th className="lb-num">{seasonLabel(data.season)}</th>
                                    <th className="lb-num lb-stat">Pace only</th>
                                    <th className="lb-num lb-stat">Pace + league</th>
                                    <th className="lb-num">z in {seasonLabel(data.season)}</th>
                                    <th className="lb-num">Better than</th>
                                    <th className="lb-num">Rank</th>
                                    <th>Note</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.rows.map((r) => {
                                    const st = r.standing;
                                    return (
                                        <tr key={r.key} className={r.original == null ? 'sl-short' : undefined}>
                                            <td>{r.label}</td>
                                            <td className="lb-num">{fmtVal(r.format, r.original)}</td>
                                            <td className="lb-num lb-stat">{fmtVal(r.format, r.pace_adjusted)}</td>
                                            <td className="lb-num lb-stat">{fmtVal(r.format, r.league_adjusted)}</td>
                                            <td className="lb-num">{st ? signed(st.z) : '—'}</td>
                                            <td className="lb-num">{st ? `${Math.round(st.percentile)}%` : '—'}</td>
                                            <td className="lb-num">{st ? `${st.rank} of ${st.pool}` : '—'}</td>
                                            <td className="era-row-note">{r.note ?? r.standing_note ?? ''}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>

                    <div className="era-method">
                        <h3>How it&apos;s calculated</h3>
                        <p><strong>Pace only</strong>: stat × (target league pace ÷ source league pace). Same production per
                            possession; percentages don&apos;t change.</p>
                        <p><strong>Pace + league</strong>: stat × (target league average per team game ÷ source league
                            average) for that stat, so the player keeps the same share of an average team&apos;s output;
                            for points this covers pace and scoring efficiency together. Percentages move by the change in the
                            league average (player − source league + target league). Threes stay pace-only.</p>
                        <p><strong>Standing</strong> is the era-free comparison: z-score, percentile and rank within the
                            player&apos;s own season among players with {data.qualified.min_gp}+ games and{' '}
                            {data.qualified.min_mpg}+ minutes a game; z is flipped for turnovers, so + is always better.</p>
                        <p>League pace is used, not the team&apos;s, and minutes aren&apos;t changed. None of this says how
                            the player would have adapted to another era&apos;s rules, spacing or athletes.</p>
                    </div>
                </div>
            )}
        </section>
    );
}
