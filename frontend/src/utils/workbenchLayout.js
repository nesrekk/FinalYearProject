// The Workbench grid: 12 columns, rows of a fixed height, each block at
// (x, y) spanning w × h cells. After any move or resize the layout is
// compacted like a stack of bricks: blocks fall upward into free space and a
// block that would overlap one already placed is pushed down below it. The
// block being moved is placed first among blocks on its row, so dropping it
// onto another pushes that one down rather than bouncing back.

export const GRID_COLS = 12;
export const ROW_PX = 40;
export const GAP_PX = 12;
export const MIN_W = 2;
export const MIN_H = 2;
export const MAX_H = 40;

const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

export function clampRect({ x, y, w, h }) {
    const cw = Math.max(MIN_W, Math.min(GRID_COLS, Math.round(w)));
    return {
        w: cw,
        h: Math.max(MIN_H, Math.min(MAX_H, Math.round(h))),
        x: Math.max(0, Math.min(GRID_COLS - cw, Math.round(x))),
        y: Math.max(0, Math.round(y)),
    };
}

// Returns the blocks in their original order with resolved positions.
export function compact(blocks, priorityId = null) {
    const order = [...blocks].sort((a, b) => (a.y - b.y)
        || (a.id === priorityId ? -1 : b.id === priorityId ? 1 : 0)
        || (a.x - b.x));
    const placed = [];
    for (const b of order) {
        const r = { ...b, ...clampRect(b) };
        while (r.y > 0 && !placed.some((p) => overlaps({ ...r, y: r.y - 1 }, p))) r.y -= 1;
        while (placed.some((p) => overlaps(r, p))) r.y += 1;
        placed.push(r);
    }
    const byId = new Map(placed.map((p) => [p.id, p]));
    return blocks.map((b) => byId.get(b.id));
}

// The layout with block `id` moved/resized to `rect` (any of x, y, w, h).
export function withRect(blocks, id, rect) {
    return compact(blocks.map((b) => (b.id === id ? { ...b, ...clampRect({ ...b, ...rect }) } : b)), id);
}

// Where a new block of w × h goes: the first free spot reading top to
// bottom, left to right (beside a narrow block if it fits), else at the bottom.
export function placeNew(blocks, w, h) {
    const size = clampRect({ x: 0, y: 0, w, h });
    const bottom = boardRows(blocks);
    for (let y = 0; y <= bottom; y += 1) {
        for (let x = 0; x + size.w <= GRID_COLS; x += 1) {
            const r = { ...size, x, y };
            if (!blocks.some((b) => overlaps(r, b))) return r;
        }
    }
    return { ...size, x: 0, y: bottom };
}

// Reading order (top to bottom, then left to right): the DOM order, so tab
// order and screen readers follow what's on screen, and the order blocks
// stack in on a phone.
export const readingOrder = (blocks) => [...blocks].sort((a, b) => (a.y - b.y) || (a.x - b.x));

export const boardRows = (blocks) => blocks.reduce((m, b) => Math.max(m, b.y + b.h), 0);
