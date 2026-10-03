import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchLivePlayerSuggestions, fetchQualityMap } from '../../services/api';
import AutocompleteDropdown from './AutocompleteDropdown';
import ChartExport from './ChartExport';
import InfoTooltip from './InfoTooltip';
import SourceBadge from './SourceBadge';
import TableExport from './TableExport';
import { bySign, signed } from '../../utils/format';
import '../../styles/gamelog.css';
import '../../styles/playfinder.css';
import '../../styles/qualitymap.css';

// Shot quality map (GET /shots/quality-map, routers/shot_quality_map.py; cells
// from scripts/build_shot_making.py). The Shot Charts "Quality map" tab: one
// or two players' shots binned into 2-foot hexagons; colour = FG% above (orange)
// or below (blue) either what an average shooter makes on those shots
// ("expected", the shot-making model) or what the league made from the same
// hexagon that season; size = how many shots. Hand-built SVG with the app's
// court geometry (feet, hoop at the top).

const SCALE = 9.4;
const HOOP_X = 250;
const HOOP_Y = 52;
const VIEW_H = 500;
const px = (x) => HOOP_X + x * SCALE;
const py = (y) => HOOP_Y + y * SCALE;
const SHRINK = 5;        // pseudo-shots at the reference rate added to every cell's gap
const FULL_TINT = 0.12;  // a gap of 12 FG points (after shrinking) is the strongest colour
const MIN_LEAGUE_NOTE = 30;

const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const gapText = (v, d = 1) => (v == null ? '—' : signed(v * 100, d));
// Made shots above the reference, coloured as shown at one decimal (0.0 isn't tinted).
const madeTone = (v) => bySign(v, 1, 'qm-pos', 'qm-neg');

function arc(cx, cy, r, a1, a2, steps = 48) {
    const pts = [];
    for (let i = 0; i <= steps; i += 1) {
        const t = ((a1 + ((a2 - a1) * i) / steps) * Math.PI) / 180;
        pts.push(`${i === 0 ? 'M' : 'L'} ${px(cx + r * Math.cos(t)).toFixed(1)} ${py(cy + r * Math.sin(t)).toFixed(1)}`);
    }
    return pts.join(' ');
}

const COURT = {
    box: { x: px(-25), y: py(-4.75), width: 50 * SCALE, height: (42.25 + 4.75) * SCALE },
    paint: { x: px(-8), y: py(-4.75), width: 16 * SCALE, height: (14.25 + 4.75) * SCALE },
    ra: arc(0, 0, 4, 0, 180),
    ft: arc(0, 14.25, 6, 0, 180),
    three: arc(0, 0, 23.75, 22, 158),
    hoop: { cx: px(0), cy: py(0), r: 0.75 * SCALE },
};

const hexPoints = (cx, cy, r) => Array.from({ length: 6 }, (_, k) => {
    const a = ((30 + 60 * k) * Math.PI) / 180;
    return `${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`;
}).join(' ');

// FG% gap of one cell against the chosen reference, shrunk toward 0.
function cellGap(c, mode) {
    const ref = mode === 'league' ? (c.league_fg_pct == null ? null : c.league_fg_pct * c.fga) : c.xm;
    if (ref == null) return { gap: null, ref: null };
    return { gap: (c.fgm - ref) / (c.fga + SHRINK), ref: ref / c.fga };
}

const tint = (gap) => {
    const t = Math.min(1, Math.abs(gap) / FULL_TINT);
    return `color-mix(in srgb, var(${gap >= 0 ? '--qm-up' : '--qm-down'}) ${Math.round(t * 100)}%, var(--surface-2))`;
};

function where(c) {
    const d = Math.hypot(c.x, c.y);
    const side = Math.abs(c.x) < 1 ? 'in line with the hoop' : `${Math.abs(c.x).toFixed(0)} ft ${c.x < 0 ? 'left' : 'right'}`;
    return `${d.toFixed(0)} ft out, ${side}`;
}

// One player's map. Also a Workbench block (`playerId` there: names aren't unique).
export function QualityPanel({ name, playerId, season, mode, minShots, onSeason }) {
    const svgRef = useRef(null);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [hover, setHover] = useState(null);

    useEffect(() => {
        let live = true;
        const timer = setTimeout(() => {
            setLoading(true);
            setError('');
            fetchQualityMap(name, season ?? undefined, playerId)
                .then((d) => { if (live) setData(d); })
                .catch((e) => {
                    if (!live) return;
                    setData(null);
                    setError(e?.response?.data?.detail || 'The map could not load.');
                })
                .finally(() => { if (live) setLoading(false); });
        }, 100);
        return () => { live = false; clearTimeout(timer); };
    }, [name, season, playerId]);

    const cells = useMemo(() => {
        if (!data) return [];
        const shown = data.cells.filter((c) => c.fga >= minShots);
        const max = Math.max(1, ...shown.map((c) => c.fga));
        const R = data.cell_radius_ft; // circumradius, feet
        return shown.map((c) => {
            const { gap, ref } = cellGap(c, mode);
            return { ...c, gap, ref, r: R * (0.32 + 0.68 * Math.sqrt(c.fga / max)) };
        });
    }, [data, mode, minShots]);

    const extremes = useMemo(() => {
        const q = cells.filter((c) => c.fga >= 10 && c.gap != null && c.ref != null);
        const raw = (c) => c.fgm - c.ref * c.fga;
        const sorted = [...q].sort((a, b) => raw(b) - raw(a));
        return { up: sorted.slice(0, 5).filter((c) => raw(c) > 0), down: sorted.slice(-5).reverse().filter((c) => raw(c) < 0) };
    }, [cells]);

    if (error) return <div className="qm-panel"><p className="error-message">{error}</p></div>;
    if (!data) return <div className="qm-panel"><p className="page-subtitle">{loading ? 'Loading the map…' : ''}</p></div>;

    const refName = mode === 'league' ? 'league FG% from the same hexagon' : 'expected FG%';
    const t = data.totals;
    const hovered = hover != null ? cells.find((c) => c.cell === hover) : null;
    const summary = `Half-court map of ${data.player_name}'s ${data.season_label} shots on hexagons: ${t.fga.toLocaleString()} shots, `
        + `${pct(t.fg_pct)} FG against ${pct(t.expected_fg_pct)} expected; orange cells beat ${refName}, blue cells fall short, bigger cells have more shots.`;

    return (
        <div className={loading ? 'qm-panel qm-panel--stale' : 'qm-panel'} aria-busy={loading}>
            <div className="qm-head">
                <h4>{data.player_name}{data.team ? <span className="qm-team"> · {data.team}</span> : null}</h4>
                <label className="qm-season">
                    <span>Season</span>
                    <select className="input-field" value={data.season} onChange={(e) => onSeason(Number(e.target.value))}>
                        {[...data.seasons].reverse().map((s) => <option key={s.season} value={s.season}>{s.label}</option>)}
                    </select>
                </label>
            </div>
            <p className="qm-stats">
                <strong>{t.fga.toLocaleString()}</strong> mapped shots · FG <strong>{pct(t.fg_pct)}</strong>, expected {pct(t.expected_fg_pct)},
                league {pct(data.league.fg_pct)} <span className="qm-gap">({gapText(t.fg_pct - t.expected_fg_pct)} vs expected)</span>
            </p>
            <div className="qm-chart">
                <ChartExport svgRef={svgRef} name={`${data.player_name} quality map ${data.season_label}`} />
                <svg ref={svgRef} viewBox={`0 0 500 ${VIEW_H}`} role="img" aria-label={summary} onMouseLeave={() => setHover(null)}>
                    <rect x="0" y="0" width="500" height={VIEW_H} className="qm-bg" rx="8" />
                    <rect {...COURT.box} className="qm-line" rx="4" />
                    <rect {...COURT.paint} className="qm-line" />
                    <path d={COURT.ft} className="qm-line" />
                    <path d={COURT.ra} className="qm-line qm-line--dash" />
                    <path d={COURT.three} className="qm-line" />
                    <line x1={px(-22)} x2={px(-22)} y1={py(-4.75)} y2={py(9.25)} className="qm-line" />
                    <line x1={px(22)} x2={px(22)} y1={py(-4.75)} y2={py(9.25)} className="qm-line" />
                    {cells.map((c) => (
                        <polygon key={c.cell} points={hexPoints(px(c.x), py(c.y), c.r * SCALE)}
                            className={c.gap == null ? 'qm-cell qm-cell--none' : 'qm-cell'}
                            style={c.gap == null ? undefined : { fill: tint(c.gap) }}
                            onMouseEnter={() => setHover(c.cell)} />
                    ))}
                    <circle cx={COURT.hoop.cx} cy={COURT.hoop.cy} r={COURT.hoop.r} className="qm-hoop" />
                </svg>
            </div>
            <div className="qm-legend" aria-hidden="true">
                <span>Below</span>
                <span className="qm-ramp" />
                <span>Above {refName}</span>
                <span className="qm-legend-note">bigger cell = more shots</span>
            </div>
            <p className="qm-detail" aria-live="polite">
                {hovered ? (
                    <>
                        <strong>{where(hovered)}:</strong> {hovered.fgm} of {hovered.fga} ({pct(hovered.fgm / hovered.fga, 0)}),
                        expected {pct(hovered.xm / hovered.fga, 0)}
                        {hovered.league_fg_pct != null
                            ? `, league ${pct(hovered.league_fg_pct, 0)} from here (${hovered.league_fga.toLocaleString()} shots)`
                            : `, league: under ${MIN_LEAGUE_NOTE} shots here`}
                        {hovered.gap != null && hovered.fga < 10 ? ' · a small sample' : ''}
                    </>
                ) : 'Hover a cell for its shots, makes and both references.'}
            </p>
            {(extremes.up.length > 0 || extremes.down.length > 0) && (
                <details className="qm-gaps">
                    <summary>Biggest gaps (cells with 10+ shots, makes vs {refName})</summary>
                    <TableExport name={`${data.player_name} quality map gaps ${data.season_label}`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table qm-table">
                            <thead><tr><th>Where</th><th className="lb-num">Shots</th><th className="lb-num">Made</th><th className="lb-num">FG%</th><th className="lb-num">Reference</th><th className="lb-num">Makes vs reference</th></tr></thead>
                            <tbody>
                                {[...extremes.up, ...extremes.down].map((c) => (
                                    <tr key={c.cell}>
                                        <td>{where(c)}</td>
                                        <td className="lb-num">{c.fga}</td>
                                        <td className="lb-num">{c.fgm}</td>
                                        <td className="lb-num">{pct(c.fgm / c.fga, 0)}</td>
                                        <td className="lb-num">{pct(c.ref, 0)}</td>
                                        <td className={`lb-num ${madeTone(c.fgm - c.ref * c.fga)}`}>
                                            {signed(c.fgm - c.ref * c.fga, 1)}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </details>
            )}
            <p className="qm-foot">
                {data.off_map.fga > 0 && <>{data.off_map.fga.toLocaleString()} of his shots are in no cell. {data.off_map.reason} </>}
                <SourceBadge source={data._source} />
            </p>
        </div>
    );
}

function PlayerPicker({ onPick }) {
    const inputRef = useRef(null);
    const [q, setQ] = useState('');
    const [hits, setHits] = useState([]);
    useEffect(() => {
        const query = q.trim();
        if (query.length < 2) return undefined;
        let live = true;
        const timer = setTimeout(() => {
            fetchLivePlayerSuggestions(query, 8)
                .then((d) => { if (live) setHits(d?.results ?? []); })
                .catch(() => { if (live) setHits([]); });
        }, 200);
        return () => { live = false; clearTimeout(timer); };
    }, [q]);
    const shown = q.trim().length >= 2 ? hits : [];
    return (
        <>
            <input ref={inputRef} className="input-field" type="search" value={q} placeholder="Compare with another player"
                aria-label="Compare with another player" autoComplete="off" onChange={(e) => setQ(e.target.value)} />
            <AutocompleteDropdown anchorRef={inputRef} items={shown}
                onPick={(n) => { onPick(n); setQ(''); setHits([]); }} />
        </>
    );
}

export default function ShotQualityMap({ playerName, state, onChange }) {
    const { mode, season, vs, vsSeason, minShots } = state;
    // Another player's seasons differ: forget the season once the player changes (not on first mount, which may carry a linked one).
    const lastName = useRef(playerName);
    useEffect(() => {
        if (lastName.current !== playerName) {
            lastName.current = playerName;
            onChange({ season: null });
        }
        // onChange is a stable state setter wrapper from the parent
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [playerName]);
    return (
        <div className="dashboard-card qm-root" style={{ marginBottom: '1rem' }}>
            <h3 className="section-heading" style={{ marginTop: 0 }}>
                Shot quality map
                <InfoTooltip label="About the quality map" title="How to read it">
                    Each hexagon is a 2-foot patch of the half court. Its colour is the player&apos;s FG% there minus a
                    reference: either the FG% an average shooter would make on the same shots (the shot-making model,
                    which never saw this player&apos;s shots) or what the whole league made from that hexagon that
                    season. Orange is better than the reference, blue worse; the bigger the hexagon, the more shots.
                    Colours are shrunk toward zero as if {SHRINK} more shots had gone in at the reference rate, so a
                    one-shot cell can&apos;t look extreme; the hover shows the raw counts. No shot on file has a defender
                    distance or shot type, so a gap also carries the defence faced and how the shot was created.
                </InfoTooltip>
            </h3>
            <p className="page-subtitle" style={{ marginTop: 0 }}>
                Where a player shoots and how far above or below an average shooter he is from each spot, regular season
                only. Players need 200+ shots in a season to have a map. Before 2010-11 about a quarter of shots, nearly all
                at the rim, have no recorded location and are left off the map.
            </p>
            <div className="qm-controls">
                <div className="qm-toggle" role="group" aria-label="Colour by">
                    <span className="qm-label">Colour</span>
                    <button type="button" className="pf-pill" aria-pressed={mode === 'expected'}
                        onClick={() => onChange({ mode: 'expected' })}>FG% − expected</button>
                    <button type="button" className="pf-pill" aria-pressed={mode === 'league'}
                        onClick={() => onChange({ mode: 'league' })}>FG% − league</button>
                </div>
                <label className="qm-season">
                    <span>Cells with at least</span>
                    <select className="input-field" value={minShots} onChange={(e) => onChange({ minShots: Number(e.target.value) })}>
                        {[1, 2, 3, 5, 10].map((n) => <option key={n} value={n}>{n} shot{n > 1 ? 's' : ''}</option>)}
                    </select>
                </label>
                {vs ? (
                    <span className="gf-player-chip">
                        vs {vs}
                        <button type="button" className="cb-remove" aria-label="Remove the comparison"
                            onClick={() => onChange({ vs: null, vsSeason: null })}>×</button>
                    </span>
                ) : <PlayerPicker onPick={(n) => onChange({ vs: n, vsSeason: null })} />}
            </div>
            <div className={vs ? 'qm-grid qm-grid--two' : 'qm-grid'}>
                <QualityPanel key={playerName} name={playerName} season={season} mode={mode} minShots={minShots}
                    onSeason={(s) => onChange({ season: s })} />
                {vs && <QualityPanel key={vs} name={vs} season={vsSeason} mode={mode} minShots={minShots}
                    onSeason={(s) => onChange({ vsSeason: s })} />}
            </div>
        </div>
    );
}
