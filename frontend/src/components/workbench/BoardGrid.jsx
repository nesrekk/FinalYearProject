import React, { useEffect, useRef, useState } from 'react';
import Icon from '../common/Icon';
import { GAP_PX, GRID_COLS, ROW_PX, readingOrder, withRect } from '../../utils/workbenchLayout';

// The board's grid. Blocks sit at explicit grid cells (CSS grid, 12 columns);
// a block moves by dragging its handle and resizes by dragging its corner
// (pointer events, so mouse, pen and touch all work), or from the keyboard:
// focus the handle, arrows move it a cell, Shift+arrows resize it. While a
// drag is under way the layout is a preview; it is stored once, on release.
// Below 760 px the blocks stack in reading order and Up/Down buttons replace
// dragging.
export default function BoardGrid({ blocks, narrow, onCommit, renderHeader, renderBody, labelOf }) {
    const gridRef = useRef(null);
    const [drag, setDrag] = useState(null);
    // A layout just committed, shown until the stored board comes back (so a
    // drop doesn't flash the old layout for a frame).
    const [committed, setCommitted] = useState(null);
    const [said, setSaid] = useState('');
    const focusNext = useRef(null);

    const base = committed && committed.basedOn === blocks ? committed.layout : blocks;
    const layout = drag ? drag.preview : base;

    useEffect(() => {
        const want = focusNext.current;
        if (!want) return;
        focusNext.current = null;
        document.querySelector(`[data-wb-${want.kind}="${want.id}"]`)?.focus();
    }, [blocks]);

    const commit = (next, id, kind) => {
        const before = new Map(blocks.map((b) => [b.id, b]));
        const changed = next.some((b) => {
            const o = before.get(b.id);
            return !o || o.x !== b.x || o.y !== b.y || o.w !== b.w || o.h !== b.h;
        });
        if (!changed) return;
        setCommitted({ basedOn: blocks, layout: next });
        if (id) focusNext.current = { id, kind };
        onCommit(next);
    };

    const describe = (next, id, verb) => {
        const b = next.find((x) => x.id === id);
        setSaid(`${labelOf(id)} ${verb}: column ${b.x + 1}, row ${b.y + 1}, ${b.w} columns wide, ${b.h} rows tall.`);
    };

    const start = (e, block, mode) => {
        if (narrow || e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture?.(e.pointerId);
        setDrag({ id: block.id, mode, rect: { x: block.x, y: block.y, w: block.w, h: block.h }, px: e.clientX, py: e.clientY, preview: base });
    };
    const move = (e) => {
        if (!drag || !gridRef.current) return;
        const g = gridRef.current.getBoundingClientRect();
        const dx = Math.round((e.clientX - drag.px) / ((g.width + GAP_PX) / GRID_COLS));
        const dy = Math.round((e.clientY - drag.py) / (ROW_PX + GAP_PX));
        const r = drag.rect;
        const rect = drag.mode === 'move' ? { x: r.x + dx, y: r.y + dy } : { w: r.w + dx, h: r.h + dy };
        const preview = withRect(base, drag.id, rect);
        setDrag((d) => (d ? { ...d, preview } : d));
    };
    const end = () => {
        if (!drag) return;
        const { preview, id, mode } = drag;
        setDrag(null);
        commit(preview, null);
        describe(preview, id, mode === 'move' ? 'moved' : 'resized');
    };

    const onKey = (e, block, mode) => {
        const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
        if (!d) return;
        e.preventDefault();
        const resize = mode === 'resize' || e.shiftKey;
        const rect = resize ? { w: block.w + d[0], h: block.h + d[1] } : { x: block.x + d[0], y: block.y + d[1] };
        const next = withRect(base, block.id, rect);
        commit(next, block.id, mode === 'resize' ? 'resize' : 'handle');
        describe(next, block.id, resize ? 'resized' : 'moved');
    };

    // Phone: swap places with the block before/after it in reading order.
    const step = (block, dir) => {
        const order = readingOrder(base);
        const i = order.findIndex((b) => b.id === block.id);
        const other = order[i + dir];
        if (!other) return;
        const next = dir < 0
            ? withRect(base, block.id, { x: other.x, y: other.y })
            : withRect(base, other.id, { x: block.x, y: block.y });
        commit(next, block.id, `step${dir < 0 ? 'up' : 'down'}`);
        setSaid(`${labelOf(block.id)} moved ${dir < 0 ? 'up' : 'down'}.`);
    };

    // DOM order = reading order of the stored layout (not the preview, so a
    // node being dragged is never moved in the DOM mid-drag).
    const order = readingOrder(base);
    const pos = new Map(layout.map((b) => [b.id, b]));
    return (
        <>
            <p className="wb-sr" aria-live="polite">{said}</p>
            <div ref={gridRef} className={`wb-grid${narrow ? ' wb-grid--stack' : ''}${drag ? ' wb-grid--dragging' : ''}`}
                style={{ '--wb-row': `${ROW_PX}px`, '--wb-gap': `${GAP_PX}px` }}>
                {order.map((b, i) => {
                    const p = pos.get(b.id);
                    const style = narrow ? undefined : { gridColumn: `${p.x + 1} / span ${p.w}`, gridRow: `${p.y + 1} / span ${p.h}` };
                    const label = labelOf(b.id);
                    const handle = narrow ? (
                        <span className="wb-steps">
                            <button type="button" className="wb-icon-btn" disabled={i === 0} data-wb-stepup={b.id}
                                onClick={() => step(b, -1)} aria-label={`Move ${label} up`}><Icon name="arrow_upward" size={16} /></button>
                            <button type="button" className="wb-icon-btn" disabled={i === order.length - 1} data-wb-stepdown={b.id}
                                onClick={() => step(b, 1)} aria-label={`Move ${label} down`}><Icon name="arrow_downward" size={16} /></button>
                        </span>
                    ) : (
                        <button
                            type="button"
                            className="wb-handle"
                            data-wb-handle={b.id}
                            aria-label={`Move ${label}. Drag, or use the arrow keys; Shift with the arrows resizes.`}
                            title="Drag to move (arrow keys move, Shift+arrows resize)"
                            onPointerDown={(e) => start(e, b, 'move')}
                            onPointerMove={move}
                            onPointerUp={end}
                            onPointerCancel={end}
                            onKeyDown={(e) => onKey(e, b, 'move')}
                        >
                            <Icon name="drag_indicator" size={18} />
                        </button>
                    );
                    return (
                        <section
                            key={b.id}
                            className={`wb-block wb-block--${b.type}${drag?.id === b.id ? ' wb-block--active' : ''}`}
                            style={style}
                            aria-label={label}
                            data-wb-block={b.id}
                        >
                            {renderHeader(b, handle)}
                            <div className="wb-block-body">{renderBody(b)}</div>
                            {!narrow && (
                                <button
                                    type="button"
                                    className="wb-resize"
                                    data-wb-resize={b.id}
                                    aria-label={`Resize ${label}. Drag, or use the arrow keys.`}
                                    title="Drag to resize (arrow keys work too)"
                                    onPointerDown={(e) => start(e, b, 'resize')}
                                    onPointerMove={move}
                                    onPointerUp={end}
                                    onPointerCancel={end}
                                    onKeyDown={(e) => onKey(e, b, 'resize')}
                                >
                                    <Icon name="open_in_full" size={14} />
                                </button>
                            )}
                        </section>
                    );
                })}
            </div>
        </>
    );
}
