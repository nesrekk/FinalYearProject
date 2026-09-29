import { useEffect, useRef, useState } from 'react';

// Shared by common/RotationHeatmap.jsx and common/GameRotationChart.jsx.

// Width of a chart's container, for SVGs drawn at their real pixel width.
export function useWidth(initial = 720) {
    const ref = useRef(null);
    const [W, setW] = useState(initial);
    useEffect(() => {
        const el = ref.current;
        if (!el || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(([entry]) => {
            const w = Math.round(entry.contentRect.width);
            if (w > 0) setW(Math.max(280, w));
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    return [ref, W];
}

// "Shai Gilgeous-Alexander" -> "S. Gilgeous-Alexander" when it doesn't fit.
export function shortName(name, max) {
    if (!name) return '?';
    if (name.length <= max) return name;
    const parts = name.split(' ');
    const short = parts.length > 1 ? `${parts[0][0]}. ${parts.slice(1).join(' ')}` : name;
    return short.length <= max ? short : `${short.slice(0, max - 1)}…`;
}
