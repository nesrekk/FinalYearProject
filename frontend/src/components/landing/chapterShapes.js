// Pure functions that turn real API data into an array of {x, y, c?}
// morph targets in world-space (document) coordinates, given a chapter's
// visual-pane rect (already converted to world space by the caller: its
// viewport rect + window.scrollY). Every point here traces back to a real
// number passed in — nothing here invents data, only lays it out.

const MADE_COLOR = 'rgb(255, 107, 26)'; // brand orange
const MISS_COLOR = 'rgba(41, 151, 255, 0.55)'; // brand blue, dim

function inset(rect, pad = 24) {
    return {
        left: rect.left + pad,
        top: rect.top + pad,
        width: Math.max(1, rect.width - pad * 2),
        height: Math.max(1, rect.height - pad * 2),
    };
}

// Sample `count` points evenly along a polyline through `pts` (each {x,y}
// already normalized 0..1), then map into `rect`.
function sampleLine(pts, rect, count) {
    if (pts.length === 0) return [];
    if (pts.length === 1) {
        return [{ x: rect.left + pts[0].x * rect.width, y: rect.top + pts[0].y * rect.height }];
    }
    const segLens = [];
    let total = 0;
    for (let i = 0; i < pts.length - 1; i++) {
        const d = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y);
        segLens.push(d);
        total += d;
    }
    const out = [];
    for (let i = 0; i < count; i++) {
        const target = (i / (count - 1)) * total;
        let acc = 0;
        let seg = 0;
        while (seg < segLens.length - 1 && acc + segLens[seg] < target) { acc += segLens[seg]; seg++; }
        const segLen = segLens[seg] || 1;
        const t = segLen > 0 ? (target - acc) / segLen : 0;
        const a = pts[seg];
        const b = pts[seg + 1];
        const nx = a.x + (b.x - a.x) * t;
        const ny = a.y + (b.y - a.y) * t;
        out.push({ x: rect.left + nx * rect.width, y: rect.top + ny * rect.height });
    }
    return out;
}

// Chapter 1 — horizontal bars for top-N MVP probabilities (0..1 values).
export function barsShape(rect, values) {
    const r = inset(rect, 28);
    const points = [];
    const rows = values.length;
    const rowH = r.height / rows;
    values.forEach((v, i) => {
        const barLen = Math.max(6, v * r.width);
        const y = r.top + rowH * (i + 0.5);
        const density = Math.max(2, Math.round(barLen / 6));
        for (let j = 0; j < density; j++) {
            const x = r.left + (barLen * j) / Math.max(1, density - 1);
            // A few stacked points per column so the bar reads with real
            // thickness rather than a single hairline.
            points.push({ x, y: y - 3 });
            points.push({ x, y });
            points.push({ x, y: y + 3 });
        }
    });
    return points;
}

// Chapter 2 — simplified half-court outline + real shot locations.
// loc_x/loc_y are NBA.com court units (tenths of a foot): x in
// roughly [-250, 250], y in roughly [-50, 470] with the hoop near y=0.
export function courtShotsShape(rect, shots, maxShots = 260) {
    const r = inset(rect, 16);
    const points = [];
    const toXY = (locX, locY) => ({
        x: r.left + ((locX + 250) / 500) * r.width,
        y: r.top + (1 - Math.min(1, Math.max(0, locY / 420))) * r.height,
    });

    // Court outline: baseline, two sidelines near the baseline, the key, and
    // a 3pt-arc approximation — sampled as dashes, not a full accurate
    // court diagram, since it's a background motif for the real shots.
    const outline = [];
    for (let x = -250; x <= 250; x += 14) outline.push([x, 420]); // far edge of the shown area
    for (let y = 0; y <= 420; y += 14) { outline.push([-250, y]); outline.push([250, y]); }
    for (let x = -80; x <= 80; x += 10) { outline.push([x, 0]); outline.push([x, 190]); }
    for (let y = 0; y <= 190; y += 14) { outline.push([-80, y]); outline.push([80, y]); }
    for (let a = -90; a <= 90; a += 6) {
        const rad = (a * Math.PI) / 180;
        outline.push([Math.sin(rad) * 237.5, Math.cos(rad) * 237.5]);
    }
    for (const [x, y] of outline) {
        const p = toXY(x, y);
        points.push({ x: p.x, y: p.y, c: 'rgba(255,255,255,0.18)' });
    }

    const sample = shots.length > maxShots
        ? shots.filter((_, i) => i % Math.ceil(shots.length / maxShots) === 0)
        : shots;
    for (const s of sample) {
        if (s.loc_x == null || s.loc_y == null) continue;
        const p = toXY(s.loc_x, s.loc_y);
        points.push({ x: p.x, y: p.y, c: s.shot_made_flag ? MADE_COLOR : MISS_COLOR });
    }
    return points;
}

// Chapter 3 — win-probability line over the course of a game (0..1 = away
// win 100% .. home win 100%, matching the API's home_wp field).
export function wpLineShape(rect, wpPoints, count = 220) {
    const r = inset(rect, 20);
    if (wpPoints.length === 0) return [];
    const maxElapsed = wpPoints[wpPoints.length - 1].seconds_elapsed || 1;
    const normPts = wpPoints.map((p) => ({
        x: (p.seconds_elapsed || 0) / maxElapsed,
        y: 1 - Math.min(1, Math.max(0, p.home_wp)),
    }));
    return sampleLine(normPts, r, Math.min(count, Math.max(20, wpPoints.length)));
}

// Chapter 4 — a career-trajectory uncertainty cone: real floor/ceiling bands
// per age, filled with a scatter, plus the median projected line.
export function trajectoryConeShape(rect, projection) {
    const r = inset(rect, 24);
    if (projection.length === 0) return [];
    const ages = projection.map((p) => p.age);
    const minAge = Math.min(...ages);
    const maxAge = Math.max(...ages) || minAge + 1;
    const allPts = projection.flatMap((p) => [p.ceiling_pts, p.floor_pts, p.projected_pts]).filter((v) => v != null);
    const minV = Math.min(...allPts, 0);
    const maxV = Math.max(...allPts, 1);
    const span = Math.max(1, maxV - minV);
    const nx = (age) => (age - minAge) / Math.max(1, maxAge - minAge);
    const ny = (v) => 1 - (v - minV) / span;

    const points = [];
    const FILL_ROWS = 5;
    for (const p of projection) {
        const x = r.left + nx(p.age) * r.width;
        const yCeil = r.top + ny(p.ceiling_pts ?? p.projected_pts) * r.height;
        const yFloor = r.top + ny(p.floor_pts ?? p.projected_pts) * r.height;
        for (let i = 0; i < FILL_ROWS; i++) {
            const y = yCeil + ((yFloor - yCeil) * i) / Math.max(1, FILL_ROWS - 1);
            points.push({ x, y, c: 'rgba(41, 151, 255, 0.35)' });
        }
        points.push({ x, y: r.top + ny(p.projected_pts) * r.height, c: 'rgb(255, 61, 127)' });
    }
    return points;
}

// Chapter 6 — real reliability curve: predicted probability bucket vs.
// observed win rate (a perfectly calibrated model sits on the diagonal,
// drawn separately as a DOM/SVG reference line by the caller).
export function calibrationShape(rect, bins) {
    const r = inset(rect, 24);
    return bins.map((b) => ({
        x: r.left + b.predicted_mid * r.width,
        y: r.top + (1 - b.observed_rate) * r.height,
        c: 'rgb(255, 107, 26)',
    }));
}
