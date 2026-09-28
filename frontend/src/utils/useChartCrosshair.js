import { useCallback, useState } from 'react';

// Shared "nearest point" hover + keyboard focus for time-series line charts:
// hover (or Tab in, then Left/Right) anywhere over the plot and it snaps to
// the closest x, rather than requiring a precise hit on a 2-3px dot.
//
// `points` must already carry each point's pixel `x` in the SAME chart-space
// units as the SVG's viewBox (i.e. whatever the chart's own `xFor()` uses) —
// this hook only does the nearest-x search and index bookkeeping; rendering
// the highlighted dot/line and the tooltip body stays with the chart, which
// knows its own data. Pair with common/ChartTooltip.jsx for the box.
//
// Usage: spread `overlayProps` onto a transparent full-plot <rect>
// (tabIndex is included so it's keyboard-reachable), then render the
// tooltip when `point` is non-null.
export default function useChartCrosshair(points, chartWidth) {
    const [index, setIndex] = useState(null);

    const nearestIndex = useCallback((chartX) => {
        if (!points.length) return null;
        let best = 0;
        let bestDist = Infinity;
        points.forEach((p, i) => {
            const d = Math.abs(p.x - chartX);
            if (d < bestDist) {
                bestDist = d;
                best = i;
            }
        });
        return best;
    }, [points]);

    const onPointerMove = useCallback((e) => {
        const svg = e.currentTarget.ownerSVGElement || e.currentTarget.closest('svg');
        if (!svg) return;
        const rect = svg.getBoundingClientRect();
        if (!rect.width) return;
        const ratio = (e.clientX - rect.left) / rect.width;
        const chartX = ratio * chartWidth;
        const i = nearestIndex(chartX);
        if (i != null) setIndex(i);
    }, [chartWidth, nearestIndex]);

    const onPointerLeave = useCallback(() => setIndex(null), []);

    const onKeyDown = useCallback((e) => {
        if (!points.length) return;
        if (e.key === 'ArrowRight') {
            e.preventDefault();
            setIndex((i) => (i == null ? 0 : Math.min(points.length - 1, i + 1)));
        } else if (e.key === 'ArrowLeft') {
            e.preventDefault();
            setIndex((i) => (i == null ? points.length - 1 : Math.max(0, i - 1)));
        } else if (e.key === 'Home') {
            e.preventDefault();
            setIndex(0);
        } else if (e.key === 'End') {
            e.preventDefault();
            setIndex(points.length - 1);
        } else if (e.key === 'Escape') {
            setIndex(null);
        }
    }, [points.length]);

    const onFocus = useCallback(() => setIndex((i) => (i == null ? 0 : i)), []);
    const onBlur = useCallback(() => setIndex(null), []);

    return {
        index,
        point: index != null ? points[index] ?? null : null,
        overlayProps: { onPointerMove, onPointerLeave, onKeyDown, onFocus, onBlur, tabIndex: 0 },
    };
}
