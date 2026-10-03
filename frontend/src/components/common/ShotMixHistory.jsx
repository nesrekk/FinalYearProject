import React, { useEffect, useRef, useState } from 'react';
import { fetchShotZoneHistory } from '../../services/api';
import SourceBadge from './SourceBadge';
import TableExport from './TableExport';
import ChartExport from './ChartExport';

// Shot mix over a career: one 100%-stacked column per season (regular
// season only), zones from the rim outward, bottom to top. The dashed line
// is where the league's own 3-point share starts that season, so the part of
// a column above it vs. its own 3-point segments reads as "more or fewer
// threes than the league". Hand-built SVG like the app's other charts.

const SHORT = {
    'Restricted Area': 'Rim',
    'In The Paint (Non-RA)': 'Paint',
    'Mid-Range': 'Mid-range',
    'Corner 3': 'Corner 3',
    'Above the Break 3': 'Above-break 3',
};
const THREES = new Set(['Corner 3', 'Above the Break 3']);
const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const shortSeason = (s) => `'${s.slice(-2)}`;
const M = { l: 42, r: 10, t: 10, b: 30 };

function threeShare(zones, key) {
    return zones.filter((z) => THREES.has(z.zone)).reduce((a, z) => a + (z[key] ?? 0), 0);
}

function MixChart({ data, active, onHover, onPick }) {
    const boxRef = useRef(null);
    const svgRef = useRef(null);
    const [W, setW] = useState(720);
    const H = W < 520 ? 240 : 300;

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

    const { seasons } = data;
    const n = seasons.length;
    const band = (W - M.l - M.r) / n;
    const colW = Math.max(3, Math.min(34, band * 0.74));
    const cx = (i) => M.l + band * (i + 0.5);
    const sy = (v) => H - M.b - v * (H - M.t - M.b);
    // Label every season when they fit, otherwise every 2nd/3rd/5th.
    const every = [1, 2, 3, 5, 10].find((k) => band * k >= 34) ?? 10;

    const leaguePts = seasons
        .map((s, i) => {
            const lg3 = threeShare(s.zones, 'league_share');
            return s.zones.every((z) => z.league_share != null) ? [cx(i), sy(1 - lg3)] : null;
        })
        .filter(Boolean);

    const last = seasons[n - 1];
    const summary = `${data.player_name}'s regular-season shot mix by zone, ${seasons[0].season} to ${last.season}: `
        + `${pct(threeShare(seasons[0].zones, 'share'), 0)} of shots were threes in ${seasons[0].season}, `
        + `${pct(threeShare(last.zones, 'share'), 0)} in ${last.season}.`;

    return (
        <div className="sm-chart" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`${data.player_name} shot mix`} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary} onMouseLeave={() => onHover(null)}>
                {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                    <g key={t}>
                        <line className="sm-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="sm-tick" x={M.l - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle">{Math.round(t * 100)}%</text>
                    </g>
                ))}
                {seasons.map((s, i) => {
                    let acc = 0;
                    const cls = ['sm-col', s.small_sample && 'sm-col--small', active === s.season && 'sm-col--active']
                        .filter(Boolean).join(' ');
                    return (
                        <g key={s.season} className={cls}>
                            {s.zones.map((z, zi) => {
                                const y0 = acc;
                                acc += z.share ?? 0;
                                if (!z.share) return null;
                                return (
                                    <rect key={z.zone} x={cx(i) - colW / 2} width={colW}
                                        y={sy(acc)} height={Math.max(0, sy(y0) - sy(acc))}
                                        fill={`var(--zone-${zi + 1})`} />
                                );
                            })}
                        </g>
                    );
                })}
                {leaguePts.length > 1 && (
                    <path className="sm-league" d={leaguePts.map(([x, y], k) => `${k ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')} />
                )}
                {leaguePts.length === 1 && <circle className="sm-league-dot" cx={leaguePts[0][0]} cy={leaguePts[0][1]} r={4} />}
                {seasons.map((s, i) => (
                    i % every === 0 ? (
                        <text key={`t${s.season}`} className="sm-tick" x={cx(i)} y={H - M.b + 16} textAnchor="middle">{shortSeason(s.season)}</text>
                    ) : null
                ))}
                {/* Full-height hit targets, wider than the columns. */}
                {seasons.map((s, i) => (
                    <rect key={`h${s.season}`} className="sm-hit" x={cx(i) - band / 2} width={band} y={M.t} height={H - M.t - M.b}
                        tabIndex={0} aria-label={`${s.season}: ${s.fga} shots, ${pct(threeShare(s.zones, 'share'), 0)} threes`}
                        onMouseEnter={() => onHover(s.season)} onFocus={() => onHover(s.season)}
                        onClick={() => onPick(s.season)} />
                ))}
            </svg>
        </div>
    );
}

// The parent keys this by player (key={playerName}), so hover/pick state
// starts fresh for each player.
// `playerId` (optional, the Workbench): look the player up by id, since names aren't unique.
export default function ShotMixHistory({ playerName, playerId }) {
    const [result, setResult] = useState({ for: null, data: null, error: '' });
    const [hover, setHover] = useState(null);
    const [picked, setPicked] = useState(null);

    useEffect(() => {
        if (!playerName) return undefined;
        let active = true;
        fetchShotZoneHistory(playerName, playerId)
            .then((d) => { if (active) setResult({ for: playerName, data: d, error: '' }); })
            .catch((e) => {
                if (active) {
                    setResult({
                        for: playerName,
                        data: null,
                        error: e?.response?.data?.detail || e?.message || 'Failed to load the shot mix.',
                    });
                }
            });
        return () => { active = false; };
    }, [playerName, playerId]);

    const loading = result.for !== playerName;
    const data = loading ? null : result.data;
    const error = loading ? '' : result.error;
    const seasons = data?.seasons ?? [];
    const shown = seasons.find((s) => s.season === (hover ?? picked)) ?? seasons[seasons.length - 1];

    return (
        <div className="dashboard-card sm-root" style={{ marginTop: '1rem' }}>
            <h3 className="section-heading" style={{ marginTop: 0 }}>
                Shot mix over a career
                <SourceBadge source={data?._source} />
            </h3>
            <p className="page-subtitle" style={{ marginTop: 0 }}>
                Share of each regular season's shots by zone, from the rim (bottom) out to above-the-break threes (top).
                The dashed line marks where the league's 3-point share starts that season.
                {data && ` Seasons under ${data.min_fga} tracked attempts are faded (a few shots swing a share by several points).`}
            </p>

            {loading && <p className="page-subtitle">Loading shot mix…</p>}
            {error && !loading && <p className="page-subtitle">{error}</p>}

            {data && !loading && seasons.length > 0 && (
                <>
                    {data.seasons_before_coverage > 0 && (
                        <p className="page-subtitle">
                            {data.player_name}'s first {data.seasons_before_coverage} season{data.seasons_before_coverage === 1 ? '' : 's'} came
                            before {data.coverage.first}, when shot locations start, so the chart begins partway through the career.
                        </p>
                    )}
                    <ul className="sm-legend">
                        {data.zones.map((z, i) => (
                            <li key={z}><span className="sm-swatch" style={{ background: `var(--zone-${i + 1})` }} />{SHORT[z]}</li>
                        ))}
                        <li><span className="sm-legend-line" />League 3-point share starts here</li>
                    </ul>

                    <MixChart data={data} active={shown?.season} onHover={setHover} onPick={setPicked} />

                    {shown && (
                        <div className="sm-detail" aria-live="polite">
                            <h4>{shown.season} · {shown.fga.toLocaleString()} shots · {pct(shown.fg_pct)} FG</h4>
                            {shown.small_sample && <p>Small sample: fewer than {data.min_fga} tracked attempts.</p>}
                            <div className="table-wrapper">
                                <table className="data-table">
                                    <thead>
                                        <tr><th>Zone</th><th>Shots</th><th>Share</th><th>League share</th><th>FG%</th><th>League FG%</th></tr>
                                    </thead>
                                    <tbody>
                                        {shown.zones.map((z, i) => (
                                            <tr key={z.zone}>
                                                <td><span className="sm-swatch" style={{ display: 'inline-block', marginRight: 6, verticalAlign: '-1px', background: `var(--zone-${i + 1})` }} />{SHORT[z.zone]}</td>
                                                <td>{z.fga}</td>
                                                <td>{pct(z.share)}</td>
                                                <td>{pct(z.league_share)}</td>
                                                <td>{z.fga ? pct(z.fg_pct) : '—'}</td>
                                                <td>{pct(z.league_fg_pct)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    <h4 className="section-heading" style={{ fontSize: '1rem', marginTop: '1.25rem' }}>Every season</h4>
                    <TableExport />
                    <div className="table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Season</th><th>Shots</th>
                                    {data.zones.map((z) => <th key={z}>{SHORT[z]}</th>)}
                                    <th>3-pt share</th><th>League 3-pt share</th>
                                </tr>
                            </thead>
                            <tbody>
                                {seasons.map((s) => (
                                    <tr key={s.season} style={s.small_sample ? { color: 'var(--text-3)' } : undefined}
                                        title={s.small_sample ? `Fewer than ${data.min_fga} tracked attempts` : undefined}>
                                        <td>{s.season}{s.small_sample ? ' *' : ''}</td>
                                        <td>{s.fga}</td>
                                        {s.zones.map((z) => <td key={z.zone}>{pct(z.share)}</td>)}
                                        <td>{pct(threeShare(s.zones, 'share'))}</td>
                                        <td>{pct(threeShare(s.zones, 'league_share'))}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
                        Regular season only. Shot locations exist from {data.coverage.first} to {data.coverage.last}; a season
                        split across teams is one row. * fewer than {data.min_fga} tracked attempts.
                    </p>
                </>
            )}
        </div>
    );
}
