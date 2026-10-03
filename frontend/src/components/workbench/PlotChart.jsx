import React, { useLayoutEffect, useRef } from 'react';
import * as Plot from '@observablehq/plot';
import { formatValue, intervalText, seasonLabel, seriesVar } from './workbenchShared';
import { categoryLabel, unitWord } from './chartSpec';

// Draws a Chart block with Observable Plot. This is the only file that
// imports Plot, and the Chart block loads it lazily, so no other page (and
// no board without a chart) downloads it.
//
// Colours come from CSS variables (set members: --wb-series-N; the grey
// population: --wb-context; axes and text: currentColor), so a chart follows
// Paper/Ink without redrawing, and chartExport.js resolves them to plain
// colours in an exported file. Only colour *scales* (a stat as colour, the
// heatmap) use fixed schemes (viridis / red-blue), which read on both themes.
//
// Everything the chart needs to be read on its own (legend, n, the trend's r
// and interval) is drawn inside the one <svg>, so a PNG/SVG export or a
// report snapshot carries it.

const CONTEXT = 'var(--wb-context)';
const FONT = 11;
const CHAR = 6.1; // rough width of one character at 11 px, for layout
const QS = [0.1, 0.25, 0.5, 0.75, 0.9];

// Same definition as numpy's default (linear between order statistics), as
// /workbench/context uses, so a set's box and the population's box match.
function quantiles(values) {
    const v = values.filter(Number.isFinite).sort((a, b) => a - b);
    if (!v.length) return null;
    const at = (p) => {
        const h = (v.length - 1) * p;
        const lo = Math.floor(h);
        return v[lo] + (h - lo) * ((v[Math.min(lo + 1, v.length - 1)]) - v[lo]);
    };
    const [p10, p25, p50, p75, p90] = QS.map(at);
    return { n: v.length, p10, p25, p50, p75, p90 };
}

function tickFormatter(format) {
    if (format === 'pct') return (d) => `${+(d * 100).toFixed(1)}%`;
    if (format === 'signed1' || format === 'signed2') return (d) => (d > 0 ? `+${+d.toFixed(2)}` : d < 0 ? `−${+(-d).toFixed(2)}` : '0');
    return (d) => (Math.abs(d) >= 1000 ? d.toLocaleString() : `${+d.toFixed(2)}`.replace('-', '−'));
}

function axisLabel(col) {
    if (!col) return '';
    return `${col.label}${col.kind === 'count' && col.per ? ` (${col.per})` : ''}`;
}

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

// A stat as colour: red-blue around zero for signed stats (blue = better),
// viridis otherwise (reversed when lower is better, so bright = better).
function colorScale(col) {
    const lowerBetter = col?.higher_is_better === false;
    if (col?.format?.startsWith('signed')) return { type: 'diverging', scheme: 'rdbu', pivot: 0, symmetric: true, reverse: lowerBetter };
    return { type: 'linear', scheme: 'viridis', reverse: lowerBetter };
}

// r and its interval with a real minus sign.
const r2 = (v) => formatValue('num2', v).replace('-', '−');

function categoryText(field, v) {
    if (v == null) return '—';
    if (field === 'season') return seasonLabel(v);
    if (field === 'home') return v ? 'Home' : 'Away';
    if (field === 'win') return v ? 'Wins' : 'Losses';
    return String(v);
}

// Relative luminance of "#rrggbb" or "rgb(r, g, b)", for text on heatmap cells.
function luminance(color) {
    let r; let g; let b;
    const m = /^#([0-9a-f]{6})$/i.exec(color || '');
    if (m) {
        const n = parseInt(m[1], 16);
        [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    } else {
        const p = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(color || '');
        if (!p) return 0.5;
        [r, g, b] = p.slice(1, 4).map(Number);
    }
    const lin = (c) => {
        const x = c / 255;
        return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

// Lays legend items out in rows that fit the width.
function layoutLegend(items, width) {
    const rows = [];
    let row = [];
    let x = 0;
    for (const it of items) {
        const w = 18 + it.label.length * CHAR + 14;
        if (row.length && x + w > width) {
            rows.push(row);
            row = [];
            x = 0;
        }
        row.push({ ...it, x });
        x += w;
    }
    if (row.length) rows.push(row);
    return rows;
}

function Swatch({ it }) {
    if (it.shape === 'line') return <line x1={0} x2={14} y1={0} y2={0} stroke={it.color} strokeWidth={2.5} strokeDasharray={it.dash || null} />;
    if (it.shape === 'band') return <rect x={0} y={-5} width={14} height={10} fill={it.color} fillOpacity={0.45} />;
    if (it.shape === 'hollow') return <circle cx={7} cy={0} r={4} fill="none" stroke={it.color} strokeWidth={1.5} />;
    if (it.shape === 'square') return <rect x={1} y={-6} width={12} height={12} fill={it.color} />;
    return <circle cx={7} cy={0} r={4.5} fill={it.color} />;
}

// ── data from the responses ─────────────────────────────────────────────

function prep({ main, ctx, members, entityKey }) {
    const memberOf = new Map(members.map((m, i) => [m.id, { ...m, order: i }]));
    const colOf = (resp, key) => resp?.columns?.find((c) => c.key === key);
    const nameOf = (row) => {
        const m = memberOf.get(row[entityKey]);
        return m?.name || row.player_name || row.team_name || row.franchise || '';
    };
    const colorOf = (row) => {
        const m = memberOf.get(row[entityKey]);
        return m ? seriesVar(m.color) : 'var(--brand)';
    };
    const noisy = (row, keys) => keys.some((k) => k && row.reliability?.[k]?.noisy);
    return { memberOf, colOf: (k) => colOf(main, k) || colOf(ctx, k), nameOf, colorOf, noisy };
}

function rowSub(row) {
    const bits = [];
    if (row.date) bits.push(row.date);
    else if (row.season != null) bits.push(seasonLabel(row.season));
    if (row.opponent) bits.push(`${row.home === false ? '@' : 'vs'} ${row.opponent}`);
    else if (row.home != null) bits.push(row.home ? 'Home' : 'Away');
    if (row.win != null && !row.date) bits.push(row.win ? 'Wins' : 'Losses');
    else if (row.win != null) bits.push(row.win ? 'W' : 'L');
    if (row.team && !row.opponent && row.franchise == null) bits.push(row.team);
    return bits.join(' · ');
}

// " · 95% interval +4.1 to +8.7" when the row carries one for the column.
function ciLine(row, key, col) {
    const t = intervalText(col?.format, row.ci?.[key]);
    return t ? ` · ${col.interval} ${t}` : '';
}
const MAX_WHISKERS = 60; // intervals drawn for at most this many points; more would hide the points

function nLine(row, key, col) {
    const n = row.n?.[key];
    if (n == null) return '';
    const unit = Math.round(n) === 1 && col?.n_unit === 'games' ? 'game' : col?.n_unit || '';
    return `n ${Math.round(n).toLocaleString()} ${unit}${row.reliability?.[key]?.noisy ? ' (small sample)' : ''}`;
}

// ── the charts ──────────────────────────────────────────────────────────

function scatter(o) {
    const { enc, main, ctx, trend, h, ds } = o;
    const p = prep(o);
    const cx = p.colOf(enc.x);
    const cy = p.colOf(enc.y);
    const cs = enc.size && p.colOf(enc.size);
    const byStat = enc.color !== 'member' && enc.color !== 'none';
    const cc = byStat && p.colOf(enc.color);
    const fx = tickFormatter(cx?.format);
    const fy = tickFormatter(cy?.format);
    const keys = [enc.x, enc.y];
    const facetKey = (row) => (enc.facet === 'member' ? p.nameOf(row) : enc.facet ? categoryText(enc.facet, row[enc.facet]) : null);
    const point = (row, grey) => ({
        x: row[enc.x], y: row[enc.y], s: cs ? row[enc.size] : null, c: byStat ? row[enc.color] : null,
        fill: grey ? CONTEXT : enc.color === 'none' ? 'var(--brand)' : p.colorOf(row),
        noisy: p.noisy(row, keys), f: facetKey(row), mine: grey && p.memberOf.has(row[o.entityKey]),
        xlo: row.ci?.[enc.x]?.[0], xhi: row.ci?.[enc.x]?.[1], ylo: row.ci?.[enc.y]?.[0], yhi: row.ci?.[enc.y]?.[1],
        t: [grey ? null : p.nameOf(row) || null, rowSub(row) || null,
            `${cx.label}: ${formatValue(cx.format, row[enc.x])}${ciLine(row, enc.x, cx)} (${nLine(row, enc.x, cx)})`,
            `${cy.label}: ${formatValue(cy.format, row[enc.y])}${ciLine(row, enc.y, cy)} (${nLine(row, enc.y, cy)})`,
            cs ? `${cs.label}: ${formatValue(cs.format, row[enc.size])}` : null,
            cc ? `${cc.label}: ${formatValue(cc.format, row[enc.color])}` : null,
            grey ? `(${p.nameOf(row)})` : null].filter(Boolean).join('\n'),
    });
    const ok = (d) => isNum(d.x) && isNum(d.y);
    const set = (main?.rows || []).map((r) => point(r, false)).filter(ok);
    const grey = (ctx && !ctx.truncated ? ctx.rows : []).map((r) => point(r, true)).filter(ok);

    let facets = [];
    let fxOf = null;
    let fyOf = null;
    if (enc.facet) {
        const keysSeen = [];
        const order = enc.facet === 'member'
            ? o.members.map((m) => m.name).filter((nm) => set.some((d) => d.f === nm))
            : [...set, ...grey].map((d) => d.f).sort();
        for (const k of order) if (k != null && !keysSeen.includes(k)) keysSeen.push(k);
        if (enc.facet === 'season') keysSeen.sort();
        const cols = Math.max(1, Math.min(keysSeen.length, Math.floor(o.w / 210)));
        const at = new Map(keysSeen.map((k, i) => [k, i]));
        fxOf = (d) => at.get(d.f) % cols;
        fyOf = (d) => Math.floor(at.get(d.f) / cols);
        facets = keysSeen.map((k) => ({ f: k }));
    }
    const facetCh = enc.facet ? { fx: fxOf, fy: fyOf } : {};
    // The grey population repeats in every panel when panels are members.
    const greyFacet = enc.facet && enc.facet !== 'member' ? facetCh : {};
    const r = cs ? 's' : 3.6;
    const marks = [Plot.gridX({ strokeOpacity: 0.08 }), Plot.gridY({ strokeOpacity: 0.08 })];
    if (grey.length) {
        marks.push(Plot.dot(grey.filter((d) => !d.noisy), { x: 'x', y: 'y', r: cs ? 's' : 2.4, fill: CONTEXT, fillOpacity: cs ? 0.3 : 0.55, ...greyFacet }));
        marks.push(Plot.dot(grey.filter((d) => d.noisy), { x: 'x', y: 'y', r: cs ? 's' : 2.4, fill: 'none', stroke: CONTEXT, strokeOpacity: 0.6, strokeWidth: 0.8, ...greyFacet }));
    }
    if (trend?.fit && enc.trend) {
        marks.push(Plot.areaY(trend.fit.line, { x: 'x', y1: 'lo', y2: 'hi', fill: 'currentColor', fillOpacity: 0.12 }));
        marks.push(Plot.line(trend.fit.line, { x: 'x', y: 'y', stroke: 'currentColor', strokeWidth: 1.6 }));
    }
    const fill = byStat ? 'c' : 'fill';
    // The model's interval on each of the set's points, as thin whiskers under the dots.
    const whisk = enc.ci && set.length <= MAX_WHISKERS;
    const xw = whisk ? set.filter((d) => isNum(d.xlo) && isNum(d.xhi)) : [];
    const yw = whisk ? set.filter((d) => isNum(d.ylo) && isNum(d.yhi)) : [];
    const wStroke = byStat || enc.color === 'none' ? 'currentColor' : 'fill';
    if (xw.length) marks.push(Plot.ruleY(xw, { y: 'y', x1: 'xlo', x2: 'xhi', stroke: wStroke, strokeOpacity: 0.55, strokeWidth: 1.2, ...facetCh }));
    if (yw.length) marks.push(Plot.ruleX(yw, { x: 'x', y1: 'ylo', y2: 'yhi', stroke: wStroke, strokeOpacity: 0.55, strokeWidth: 1.2, ...facetCh }));
    marks.push(Plot.dot(set.filter((d) => !d.noisy), { x: 'x', y: 'y', r, fill, stroke: 'var(--surface)', strokeWidth: 0.8, ...facetCh }));
    marks.push(Plot.dot(set.filter((d) => d.noisy), { x: 'x', y: 'y', r, fill: 'none', stroke: fill, strokeWidth: 1.6, ...facetCh }));
    // Names on the points when each member has just one.
    const perMember = new Map();
    for (const d of set) perMember.set(d.t.split('\n')[0], (perMember.get(d.t.split('\n')[0]) || 0) + 1);
    if (set.length && set.length <= 15 && [...perMember.values()].every((n) => n === 1) && enc.color === 'member') {
        marks.push(Plot.text(set, { x: 'x', y: 'y', text: (d) => d.t.split('\n')[0], dy: -9, fill: 'currentColor', fontSize: 10, ...facetCh }));
    }
    if (enc.facet) {
        marks.push(Plot.frame({ strokeOpacity: 0.2 }));
        marks.push(Plot.text(facets, { fx: fxOf, fy: fyOf, text: 'f', frameAnchor: 'top-left', dx: 4, dy: 4, fontWeight: 600, fill: 'currentColor' }));
    }
    // Panels per member: the grey points belong to no panel, so only the set's points get a tooltip there.
    // A set member's own rows are also in the grey population (drawn under
    // the coloured dot); the tooltip names the coloured one.
    marks.push(Plot.tip(enc.facet === 'member' ? set : [...set, ...grey.filter((d) => !d.mine)], Plot.pointer({ x: 'x', y: 'y', title: 't', ...(enc.facet ? facetCh : {}) })));
    const options = {
        x: { label: axisLabel(cx), tickFormat: fx, nice: true },
        y: { label: axisLabel(cy), tickFormat: fy, nice: true },
        color: byStat ? { ...colorScale(cc), legend: false } : { type: 'identity' },
        r: cs ? { range: [1, 7] } : undefined,
        marks,
    };
    if (enc.facet) {
        options.fx = { axis: null };
        options.fy = { axis: null };
    }
    const legend = [];
    if (enc.color === 'member') for (const m of o.members) legend.push({ label: m.name, color: seriesVar(m.color) });
    if (grey.length) legend.push({ label: `All ${o.populationWord}`, color: CONTEXT });
    if ([...set, ...grey].some((d) => d.noisy)) legend.push({ label: 'small sample (hollow)', color: 'currentColor', shape: 'hollow' });
    if (trend?.fit && enc.trend) legend.push({ label: 'straight-line fit, 95% band', color: 'currentColor', shape: 'line' });
    const intervals = [...new Set([xw.length ? cx.interval : null, yw.length ? cy.interval : null].filter(Boolean))];
    if (intervals.length) legend.push({ label: `whiskers: ${intervals.join('; ')}`, color: 'currentColor', shape: 'line' });
    const n = [`n = ${set.length.toLocaleString()} ${unitWord(ds, enc.groupBy, set.length)}${o.setName ? ` in ${o.setName}` : ''}`];
    if (grey.length) n.push(`${grey.length.toLocaleString()} in grey (every ${o.populationOne} matching the same filters)`);
    const notes = [`${n.join('; ')}.`];
    if (enc.ci && !whisk && [cx, cy].some((c) => c?.interval) && set.some((d) => isNum(d.ylo) || isNum(d.xlo))) {
        notes.push(`Intervals are drawn for up to ${MAX_WHISKERS} points (hover a dot for its own); choose a set to see them.`);
    }
    if (cs) notes.push(`Dot size: ${axisLabel(cs)}.`);
    if (cc) notes.push(`Colour: ${axisLabel(cc)}${cc.higher_is_better === false ? ' (lower is better)' : ''}.`);
    if (trend?.fit && enc.trend) {
        const f = trend.fit;
        notes.push(`Fit over the ${trend.n.toLocaleString()}${grey.length ? ' grey' : ''} points: r = ${r2(f.r)} (95% interval ${r2(f.r_ci[0])} to ${r2(f.r_ci[1])}, resampling the ${trend.n_clusters.toLocaleString()} ${trend.cluster_by}s); the line’s band counts each ${trend.cluster_by} once.`);
    } else if (trend && !trend.fit && enc.trend) {
        notes.push(`No fit: ${trend.reason}`);
    }
    return { options, legend, notes, height: h };
}

function line(o) {
    const { enc, main, ctx } = o;
    const p = prep(o);
    const cy = p.colOf(enc.y);
    const isDate = enc.x === 'date';
    const pts = (main?.rows || []).filter((r) => isNum(r[enc.y])).map((r) => ({
        x: isDate ? new Date(`${r.date}T12:00:00`) : r.season,
        y: r[enc.y], id: r[o.entityKey], stroke: p.colorOf(r), noisy: p.noisy(r, [enc.y]),
        lo: r.ci?.[enc.y]?.[0], hi: r.ci?.[enc.y]?.[1],
        t: [p.nameOf(r), rowSub(r), `${cy.label}: ${formatValue(cy.format, r[enc.y])}${ciLine(r, enc.y, cy)}`, nLine(r, enc.y, cy)].filter(Boolean).join('\n'),
    })).sort((a, b) => a.x - b.x);
    const banded = enc.ci && !isDate ? pts.filter((d) => isNum(d.lo) && isNum(d.hi)) : [];
    const band = ctx?.groups?.filter((g) => isNum(g.p50)) || [];
    const marks = [Plot.gridY({ strokeOpacity: 0.08 })];
    if (band.length) {
        marks.push(Plot.areaY(band, { x: 'key', y1: 'p10', y2: 'p90', fill: CONTEXT, fillOpacity: 0.28, curve: 'linear' }));
        marks.push(Plot.areaY(band, { x: 'key', y1: 'p25', y2: 'p75', fill: CONTEXT, fillOpacity: 0.4 }));
        marks.push(Plot.line(band, { x: 'key', y: 'p50', stroke: CONTEXT, strokeWidth: 1.5, strokeDasharray: '4 3' }));
    }
    if (cy?.format?.startsWith('signed')) marks.push(Plot.ruleY([0], { strokeOpacity: 0.3 }));
    if (banded.length) {
        marks.push(Plot.areaY(banded, { x: 'x', y1: 'lo', y2: 'hi', z: 'id', fill: 'stroke', fillOpacity: 0.14 }));
        marks.push(Plot.ruleX(banded, { x: 'x', y1: 'lo', y2: 'hi', stroke: 'stroke', strokeOpacity: 0.45, strokeWidth: 1 }));
    }
    marks.push(Plot.line(pts, { x: 'x', y: 'y', z: 'id', stroke: 'stroke', strokeWidth: isDate ? 1.2 : 2 }));
    marks.push(Plot.dot(pts.filter((d) => !d.noisy), { x: 'x', y: 'y', r: isDate ? 2 : 3.2, fill: 'stroke' }));
    marks.push(Plot.dot(pts.filter((d) => d.noisy), { x: 'x', y: 'y', r: isDate ? 2 : 3.2, fill: 'var(--surface)', stroke: 'stroke', strokeWidth: 1.5 }));
    marks.push(Plot.tip(pts, Plot.pointer({ x: 'x', y: 'y', title: 't' })));
    const options = {
        x: isDate ? { label: 'Date', type: 'utc' } : { label: 'Season', tickFormat: (d) => (Number.isInteger(d) ? seasonLabel(d) : ''), nice: false },
        y: { label: axisLabel(cy), tickFormat: tickFormatter(cy?.format), nice: true },
        color: { type: 'identity' },
        marks,
    };
    const legend = o.members.map((m) => ({ label: m.name, color: seriesVar(m.color), shape: 'line' }));
    if (band.length) {
        legend.push({ label: `All ${o.populationWord}: median`, color: CONTEXT, shape: 'line', dash: '4 3' });
        legend.push({ label: 'middle half / middle 80%', color: CONTEXT, shape: 'band' });
    }
    if (pts.some((d) => d.noisy)) legend.push({ label: 'small sample (hollow)', color: 'currentColor', shape: 'hollow' });
    if (banded.length) legend.push({ label: `shaded: ${cy.interval}`, color: 'currentColor', shape: 'band' });
    const notes = [`n = ${pts.length.toLocaleString()} points${o.setName ? ` from ${o.setName}` : ''}${band.length ? `; grey: ${ctx.overall?.n?.toLocaleString() || 0} ${o.rowWord} matching the same filters` : ''}.`];
    return { options, legend, notes };
}

function bar(o) {
    const { enc, main, ctx } = o;
    const p = prep(o);
    const cy = p.colOf(enc.y);
    const cat = enc.category;
    const rows = (main?.rows || []).filter((r) => isNum(r[enc.y]));
    const data = rows.map((r) => ({
        name: p.nameOf(r), y: r[enc.y], fill: p.colorOf(r), noisy: p.noisy(r, [enc.y]),
        c: cat ? categoryText(cat, r[cat]) : null, label: formatValue(cy.format, r[enc.y]),
        lo: r.ci?.[enc.y]?.[0], hi: r.ci?.[enc.y]?.[1],
        t: [p.nameOf(r), cat ? categoryText(cat, r[cat]) : rowSub(r), `${cy.label}: ${formatValue(cy.format, r[enc.y])}${ciLine(r, enc.y, cy)}`, nLine(r, enc.y, cy)].filter(Boolean).join('\n'),
    }));
    const barCi = enc.ci ? data.filter((d) => isNum(d.lo) && isNum(d.hi)) : [];
    const names = o.members.map((m) => m.name).filter((n) => data.some((d) => d.name === n));
    const cats = cat ? [...new Set(data.map((d) => d.c))].sort() : null;
    const fxCh = cat ? { fx: 'c' } : {};
    const band = ctx ? (cat ? ctx.groups.map((g) => ({ ...g, c: categoryText(cat, g.key) })) : (ctx.overall ? [ctx.overall] : [])) : [];
    const marks = [Plot.gridY({ strokeOpacity: 0.08 })];
    if (band.length) {
        marks.push(Plot.rect(band, { y1: 'p25', y2: 'p75', fill: CONTEXT, fillOpacity: 0.35, ...(cat ? { fx: 'c' } : {}) }));
        marks.push(Plot.ruleY(band, { y: 'p50', stroke: CONTEXT, strokeWidth: 1.5, strokeDasharray: '4 3', ...(cat ? { fx: 'c' } : {}) }));
    }
    marks.push(Plot.barY(data, { x: 'name', y: 'y', fill: 'fill', fillOpacity: (d) => (d.noisy ? 0.4 : 0.92), ...fxCh, insetLeft: 2, insetRight: 2 }));
    marks.push(Plot.ruleY([0], { strokeOpacity: 0.5 }));
    if (barCi.length) marks.push(Plot.ruleX(barCi, { x: 'name', y1: 'lo', y2: 'hi', stroke: 'currentColor', strokeWidth: 1.4, strokeOpacity: 0.7, ...fxCh }));
    marks.push(Plot.text(data.filter((d) => d.y >= 0), { x: 'name', y: 'y', text: (d) => `${d.label}${d.noisy ? '*' : ''}`, dy: -6, fill: 'currentColor', fontSize: 10, ...fxCh }));
    marks.push(Plot.text(data.filter((d) => d.y < 0), { x: 'name', y: 'y', text: (d) => `${d.label}${d.noisy ? '*' : ''}`, dy: 8, fill: 'currentColor', fontSize: 10, ...fxCh }));
    marks.push(Plot.tip(data, Plot.pointer({ x: 'name', y: 'y', title: 't', ...fxCh })));
    const options = {
        x: { label: null, domain: names, tickFormat: (d) => (d.length > 14 ? `${d.slice(0, 13)}…` : d), axis: cat ? null : 'bottom' },
        y: { label: axisLabel(cy), tickFormat: tickFormatter(cy?.format), nice: true, insetTop: 16 },
        color: { type: 'identity' },
        opacity: { type: 'identity' },
        marginTop: 22,
        marks,
    };
    if (cat) options.fx = { label: null, domain: cats, padding: 0.12 };
    const legend = o.members.filter((m) => names.includes(m.name)).map((m) => ({ label: m.name, color: seriesVar(m.color), shape: 'square' }));
    if (band.length) {
        legend.push({ label: `All ${o.populationWord}: median`, color: CONTEXT, shape: 'line', dash: '4 3' });
        legend.push({ label: 'middle half', color: CONTEXT, shape: 'band' });
    }
    const games = rows.reduce((a, r) => a + (r.n_games || 0), 0);
    const notes = [`Each bar combines the seasons ${seasonLabel(o.seasonFrom)}${o.seasonTo !== o.seasonFrom ? ` to ${seasonLabel(o.seasonTo)}` : ''}${cat ? `, split by ${categoryLabel(o.ds, cat).toLowerCase()}` : ''}; n ${games ? `${games.toLocaleString()} games` : `${rows.length.toLocaleString()} ${o.rowWord}`}${band.length ? `; grey: ${band.reduce((a, g) => a + g.n, 0).toLocaleString()} ${o.populationWord} combined the same way` : ''}.`];
    if (data.some((d) => d.noisy)) notes.push('* faded: sample too small to say much (Stat Stability reliability under 0.5).');
    if (barCi.length) legend.push({ label: `whisker: ${cy.interval}`, color: 'currentColor', shape: 'line' });
    return { options, legend, notes };
}

// A set's seasons by age against the Aging Curves page's typical curve
// (POST /workbench/aging): everything measured against that season's league
// average, as on that page.
function agingChart(o) {
    const a = o.aging;
    const sm = a.summary;
    const pct = sm.kind === 'pct' || sm.kind === 'rate';
    const fmt = pct ? 'pct' : 'signed1';
    const show = (v) => (pct && v > 0 ? `+${formatValue(fmt, v)}` : formatValue(fmt, v));
    const colorOf = new Map(o.members.map((m) => [m.id, seriesVar(m.color)]));
    const seasons = [];
    const paths = [];
    for (const p of a.players) {
        const color = colorOf.get(p.player_id) || 'var(--brand)';
        for (const x of p.seasons) {
            if (x.age == null || !isNum(x.vs_league)) continue;
            seasons.push({ x: x.age, y: x.vs_league, id: p.player_id, color, q: x.qualified,
                t: [p.player_name, `${seasonLabel(x.season)} · age ${x.age}${x.team ? ` · ${x.team}` : ''}`,
                    `${sm.label}: ${formatValue(pct ? 'pct' : 'num1', x.value)} (league ${formatValue(pct ? 'pct' : 'num1', x.league_average)})`,
                    `vs league: ${show(x.vs_league)}`, x.qualified ? null : `not on the curve: ${x.note}`].filter(Boolean).join('\n') });
        }
        for (const q of p.path) paths.push({ x: q.age, y: q.level, id: p.player_id, color });
    }
    const curve = a.curve.filter((c) => isNum(c.level));
    const band = curve.filter((c) => isNum(c.lo) && isNum(c.hi));
    const marks = [Plot.gridY({ strokeOpacity: 0.08 }), Plot.ruleY([0], { strokeOpacity: 0.3 })];
    marks.push(Plot.areaY(band, { x: 'age', y1: 'lo', y2: 'hi', fill: CONTEXT, fillOpacity: 0.45 }));
    marks.push(Plot.line(curve, { x: 'age', y: 'level', stroke: CONTEXT, strokeWidth: 2 }));
    marks.push(Plot.line(paths, { x: 'x', y: 'y', z: 'id', stroke: 'color', strokeWidth: 1.4, strokeDasharray: '5 3', strokeOpacity: 0.9 }));
    const onCurve = seasons.filter((d) => d.q);
    marks.push(Plot.line(onCurve, { x: 'x', y: 'y', z: 'id', stroke: 'color', strokeWidth: 2 }));
    marks.push(Plot.dot(onCurve, { x: 'x', y: 'y', r: 3.2, fill: 'color' }));
    marks.push(Plot.dot(seasons.filter((d) => !d.q), { x: 'x', y: 'y', r: 3.2, fill: 'var(--surface)', stroke: 'color', strokeWidth: 1.4 }));
    marks.push(Plot.tip([...seasons, ...curve.map((c) => ({ x: c.age, y: c.level,
        t: `Typical player, age ${c.age}\n${show(c.level)} vs league${isNum(c.lo) ? `\n95% range ${show(c.lo)} to ${show(c.hi)}` : ''}${c.pairs ? `\n${c.pairs.toLocaleString()} pairs of seasons` : ''}${c.thin ? ' (thin)' : ''}` }))],
    Plot.pointer({ x: 'x', y: 'y', title: 't' })));
    const options = {
        x: { label: 'Age (on February 1 of the season)', nice: false, tickFormat: (d) => (Number.isInteger(d) ? String(d) : '') },
        y: { label: `${sm.label}, minus that season’s league average`, tickFormat: tickFormatter(fmt), nice: true },
        color: { type: 'identity' },
        marks,
    };
    const legend = a.players.map((p) => ({ label: p.player_name, color: colorOf.get(p.player_id) || 'var(--brand)', shape: 'line' }));
    legend.push({ label: 'typical player (curve), 95% range', color: CONTEXT, shape: 'band' });
    legend.push({ label: 'dashed: the curve moved to his level', color: 'currentColor', shape: 'line', dash: '5 3' });
    if (seasons.some((d) => !d.q)) legend.push({ label: `not on the curve (under ${sm.min_minutes} minutes${sm.min_attempts ? ` or ${sm.min_attempts} attempts` : ''})`, color: 'currentColor', shape: 'hollow' });
    const notes = [`n = ${onCurve.length.toLocaleString()} seasons on the curve${seasons.length > onCurve.length ? ` (+${(seasons.length - onCurve.length).toLocaleString()} hollow)` : ''}; the curve: ${sm.pairs.toLocaleString()} pairs of consecutive seasons from ${sm.players.toLocaleString()} players, ${sm.era_label.toLowerCase()}, peak at ${sm.peak_age}.`,
        'The dashed line is how a typical player at his level ages, not a forecast for him.'];
    return { options, legend, notes };
}

function box(o) {
    const { enc, main, ctx } = o;
    const p = prep(o);
    const cy = p.colOf(enc.y);
    const cats = [];
    if (ctx?.overall) cats.push({ name: `All ${o.populationWord}`, color: CONTEXT, stats: ctx.overall, grey: true });
    const rows = (main?.rows || []).filter((r) => isNum(r[enc.y]));
    for (const m of o.members) {
        const mine = rows.filter((r) => r[o.entityKey] === m.id);
        if (!mine.length) continue;
        cats.push({ name: m.name, color: seriesVar(m.color), stats: quantiles(mine.map((r) => r[enc.y])), rows: mine });
    }
    const stats = cats.map((c, i) => ({ i, ...c.stats, color: c.color, name: c.name,
        t: `${c.name}\n${c.stats.n.toLocaleString()} ${o.rowWord}\nmedian ${formatValue(cy.format, c.stats.p50)}\nmiddle half ${formatValue(cy.format, c.stats.p25)} to ${formatValue(cy.format, c.stats.p75)}\n10th-90th ${formatValue(cy.format, c.stats.p10)} to ${formatValue(cy.format, c.stats.p90)}` }));
    // A fixed jitter per row (from its position), so the dots don't move on redraw.
    const jitter = (k) => (((Math.sin(k * 12.9898) * 43758.5453) % 1) + 1) % 1 - 0.5;
    const dots = [];
    cats.forEach((c, i) => (c.rows || []).forEach((r, k) => dots.push({
        x: i + (o.enc.style === 'dots' ? 0.5 : 0.18) * jitter(k + i * 7919), y: r[enc.y], color: c.color, noisy: p.noisy(r, [enc.y]),
        t: [c.name, rowSub(r), `${cy.label}: ${formatValue(cy.format, r[enc.y])}`, nLine(r, enc.y, cy)].filter(Boolean).join('\n'),
    })));
    const wBox = o.enc.style === 'dots' ? 0.18 : 0.3;
    const marks = [Plot.gridY({ strokeOpacity: 0.08 })];
    marks.push(Plot.ruleX(stats, { x: 'i', y1: 'p10', y2: 'p90', stroke: 'color', strokeWidth: 1.4 }));
    marks.push(Plot.rect(stats, { x1: (d) => d.i - wBox, x2: (d) => d.i + wBox, y1: 'p25', y2: 'p75', fill: 'color', fillOpacity: 0.28, stroke: 'color', strokeWidth: 1.2 }));
    marks.push(Plot.ruleY(stats, { x1: (d) => d.i - wBox, x2: (d) => d.i + wBox, y: 'p50', stroke: 'color', strokeWidth: 2.5 }));
    const showDots = o.enc.style === 'dots' || dots.length <= 400;
    if (showDots) {
        marks.push(Plot.dot(dots.filter((d) => !d.noisy), { x: 'x', y: 'y', r: 2.2, fill: 'color', fillOpacity: 0.7 }));
        marks.push(Plot.dot(dots.filter((d) => d.noisy), { x: 'x', y: 'y', r: 2.2, fill: 'none', stroke: 'color', strokeWidth: 1 }));
    }
    marks.push(Plot.tip([...stats.map((s) => ({ x: s.i, y: s.p50, t: s.t })), ...(showDots ? dots : [])], Plot.pointer({ x: 'x', y: 'y', title: 't' })));
    const names = cats.map((c) => c.name);
    const options = {
        x: { label: null, domain: [-0.6, Math.max(0.6, cats.length - 0.4)], ticks: cats.map((_, i) => i), tickFormat: (i) => { const s = names[i] || ''; return s.length > 14 ? `${s.slice(0, 13)}…` : s; }, grid: false },
        y: { label: axisLabel(cy), tickFormat: tickFormatter(cy?.format), nice: true },
        color: { type: 'identity' },
        marks,
    };
    const legend = [
        { label: 'median', color: 'currentColor', shape: 'line' },
        { label: 'middle half (box)', color: 'currentColor', shape: 'band' },
        { label: 'whiskers: 10th to 90th percentile', color: 'currentColor', shape: 'line' },
    ];
    if (showDots && dots.some((d) => d.noisy)) legend.push({ label: 'small sample (hollow)', color: 'currentColor', shape: 'hollow' });
    const notes = [`Each dot is one ${o.rowOne}; n = ${dots.length.toLocaleString()}${ctx?.overall ? `; grey box: all ${ctx.overall.n.toLocaleString()} ${o.rowWord} matching the same filters` : ''}.${showDots ? '' : ' Dots hidden over 400; choose Dots to show them.'}`];
    return { options, legend, notes };
}

function histogram(o) {
    const { enc, main, ctx } = o;
    const p = prep(o);
    const cx = p.colOf(enc.x);
    const fx = tickFormatter(cx?.format);
    const marks = [Plot.gridY({ strokeOpacity: 0.08 })];
    const hist = ctx?.histogram;
    const bins = hist ? hist.counts.map((c, i) => ({ x1: hist.edges[i], x2: hist.edges[i + 1], y: c,
        t: `${formatValue(cx.format, hist.edges[i])} to ${formatValue(cx.format, hist.edges[i + 1])}\n${c.toLocaleString()} ${o.rowWord}` })) : [];
    const rows = (main?.rows || []).filter((r) => isNum(r[enc.x]));
    const pts = rows.map((r) => ({ x: r[enc.x], color: p.colorOf(r), noisy: p.noisy(r, [enc.x]),
        t: [p.nameOf(r), rowSub(r), `${cx.label}: ${formatValue(cx.format, r[enc.x])}`, nLine(r, enc.x, cx)].filter(Boolean).join('\n') }));
    const greyFill = o.members.length ? CONTEXT : 'var(--brand)';
    if (bins.length) {
        marks.push(Plot.rectY(bins, { x1: 'x1', x2: 'x2', y: 'y', fill: greyFill, fillOpacity: o.members.length ? 0.7 : 0.85, insetLeft: 0.5, insetRight: 0.5 }));
        if (pts.length) {
            const top = Math.max(...bins.map((b) => b.y));
            const tickH = top * 0.14;
            marks.push(Plot.ruleX(pts, { x: 'x', y1: 0, y2: tickH, stroke: 'color', strokeWidth: 2, strokeOpacity: (d) => (d.noisy ? 0.45 : 0.95) }));
            marks.push(Plot.dot(pts, { x: 'x', y: tickH, r: 2.6, fill: (d) => (d.noisy ? 'var(--surface)' : d.color), stroke: 'color', strokeWidth: 1.2 }));
            marks.push(Plot.tip(pts, Plot.pointer({ x: 'x', y: tickH, title: 't' })));
        }
        marks.push(Plot.tip(bins, Plot.pointerX({ x1: 'x1', x2: 'x2', y: 'y', title: 't' })));
    } else if (pts.length) {
        marks.push(Plot.rectY(pts, Plot.binX({ y: 'count' }, { x: 'x', fill: 'color', thresholds: enc.bins, insetLeft: 0.5, insetRight: 0.5 })));
    }
    marks.push(Plot.ruleY([0], { strokeOpacity: 0.5 }));
    const options = {
        x: { label: axisLabel(cx), tickFormat: fx, nice: !bins.length },
        y: { label: o.rowWord, grid: false },
        color: { type: 'identity' },
        opacity: { type: 'identity' },
        marks,
    };
    const legend = [];
    if (bins.length && o.members.length) legend.push({ label: `All ${o.populationWord}`, color: CONTEXT, shape: 'square' });
    for (const m of o.members) if (pts.some((d) => d.color === seriesVar(m.color))) legend.push({ label: m.name, color: seriesVar(m.color), shape: bins.length ? 'line' : 'square' });
    const notes = [];
    if (bins.length) notes.push(`Bars: ${ctx.overall?.n?.toLocaleString() || 0} ${o.rowWord} matching the filters (median ${formatValue(cx.format, ctx.overall?.p50)}${ctx.n_missing ? `; ${ctx.n_missing.toLocaleString()} without a value left out` : ''}).${pts.length ? ` Ticks: the ${pts.length.toLocaleString()} ${o.rowWord} in ${o.setName}.` : ''}`);
    else notes.push(`n = ${pts.length.toLocaleString()} ${o.rowWord} from ${o.setName} (stacked by member).`);
    return { options, legend, notes };
}

function heatmap(o) {
    const { enc, main, ctx } = o;
    const p = prep(o);
    const cv = p.colOf(enc.y);
    const cat = enc.category;
    const cells = (main?.rows || []).filter((r) => isNum(r[enc.y])).map((r) => ({
        x: categoryText(cat, r[cat]), xs: r[cat], y: p.nameOf(r), v: r[enc.y], noisy: p.noisy(r, [enc.y]),
        t: [p.nameOf(r), categoryText(cat, r[cat]), `${cv.label}: ${formatValue(cv.format, r[enc.y])}`, nLine(r, enc.y, cv)].filter(Boolean).join('\n'),
    }));
    const allName = `All ${o.populationWord} (median)`;
    if (ctx?.groups) {
        for (const g of ctx.groups) if (isNum(g.p50)) {
            cells.push({ x: categoryText(cat, g.key), xs: g.key, y: allName, v: g.p50, noisy: false, grey: true,
                t: `${allName}\n${categoryText(cat, g.key)}\nmedian ${formatValue(cv.format, g.p50)} of ${g.n.toLocaleString()}\nmiddle half ${formatValue(cv.format, g.p25)} to ${formatValue(cv.format, g.p75)}` });
        }
    }
    const xs = [...new Map(cells.map((c) => [c.x, c.xs])).entries()].sort((a, b) => (a[1] > b[1] ? 1 : a[1] < b[1] ? -1 : 0)).map(([k]) => k);
    const ys = [...(ctx?.groups ? [allName] : []), ...o.members.map((m) => m.name).filter((n) => cells.some((c) => c.y === n))];
    const vals = cells.map((c) => c.v);
    const colorOpts = { ...colorScale(cv), domain: [Math.min(...vals), Math.max(...vals)] };
    const scale = cells.length ? Plot.scale({ color: colorOpts }) : null;
    const ink = (d) => (luminance(scale?.apply(d.v)) > 0.33 ? '#111111' : '#ffffff');
    const marks = [
        Plot.cell(cells, { x: 'x', y: 'y', fill: 'v', fillOpacity: (d) => (d.noisy ? 0.45 : 1), inset: 0.5 }),
        Plot.text(cells, { x: 'x', y: 'y', text: (d) => `${formatValue(cv.format, d.v)}${d.noisy ? '*' : ''}`, fill: ink, fontSize: 10 }),
        Plot.tip(cells, Plot.pointer({ x: 'x', y: 'y', title: 't' })),
    ];
    const longest = Math.max(4, ...ys.map((y) => y.length));
    const options = {
        x: { label: null, domain: xs, tickRotate: xs.length > 8 ? -40 : 0 },
        y: { label: null, domain: ys },
        color: colorOpts,
        opacity: { type: 'identity' },
        marginLeft: Math.min(200, 12 + longest * CHAR),
        marginBottom: xs.length > 8 ? 52 : 30,
        marks,
    };
    const legend = [];
    const notes = [`Colour and number: ${axisLabel(cv)}${cv?.higher_is_better === false ? ' (lower is better)' : ''}; n = ${cells.filter((c) => !c.grey).length.toLocaleString()} cells.${cells.some((c) => c.noisy) ? ' * faded: sample too small to say much.' : ''}`];
    return { options, legend, notes };
}

const DRAW = { scatter, line, bar, box, histogram, heatmap };

export default function PlotChart(props) {
    const { width, height, svgRef, title } = props;
    const holder = useRef(null);
    const built = (props.aging ? agingChart : DRAW[props.type])({ ...props, w: width });
    const legendRows = layoutLegend(built.legend, width - 8);
    const legendH = legendRows.length ? legendRows.length * 18 + 6 : 0;
    const notesRows = [];
    for (const n of built.notes) {
        // wrap each note to the width
        const words = n.split(' ');
        let line1 = '';
        for (const w of words) {
            if ((line1 + w).length * CHAR > width - 8 && line1) {
                notesRows.push(line1.trim());
                line1 = '';
            }
            line1 += `${w} `;
        }
        if (line1.trim()) notesRows.push(line1.trim());
    }
    const notesH = notesRows.length * 15 + 6;
    const plotH = Math.max(140, height - legendH - notesH);
    const total = legendH + plotH + notesH;

    useLayoutEffect(() => {
        const g = holder.current;
        if (!g) return undefined;
        const { options } = built;
        let el;
        try {
            el = Plot.plot({
                width,
                height: plotH,
                marginLeft: 48,
                marginRight: 16,
                marginTop: 20,
                marginBottom: 34,
                ...options,
                style: { fontSize: `${FONT}px`, overflow: 'visible', background: 'transparent' },
            });
        } catch (e) {
            const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            t.setAttribute('x', '8');
            t.setAttribute('y', '20');
            t.textContent = `Couldn’t draw this chart (${e.message}).`;
            g.replaceChildren(t);
            return undefined;
        }
        // Plot puts a <style> with class selectors in the svg; the same rules
        // live in workbench.css, so an exported file carries no classes.
        el.querySelectorAll('style').forEach((s) => s.remove());
        g.replaceChildren(el);
        return () => { el.remove(); };
    });

    return (
        <svg ref={svgRef} className="wb-chart-svg" width={width} height={total} viewBox={`0 0 ${width} ${total}`} role="img" aria-label={`${title}. ${built.notes.join(' ')}`}
            fontFamily="Space Grotesk, Inter, system-ui, sans-serif" fontSize={FONT}>
            {legendRows.map((row, ri) => (
                <g key={ri} transform={`translate(4 ${ri * 18 + 11})`}>
                    {row.map((it) => (
                        <g key={it.label} transform={`translate(${it.x} 0)`}>
                            <Swatch it={it} />
                            <text x={18} y={4} fill="currentColor">{it.label}</text>
                        </g>
                    ))}
                </g>
            ))}
            <g ref={holder} transform={`translate(0 ${legendH})`} />
            {notesRows.map((t, i) => (
                <text key={i} x={4} y={legendH + plotH + 14 + i * 15} fill="currentColor" className="wb-chart-note">{t}</text>
            ))}
        </svg>
    );
}
