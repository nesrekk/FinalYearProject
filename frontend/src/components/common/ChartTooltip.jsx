import React from 'react';

// Floating tooltip anchored to a point in chart-space (the same 0..chartWidth
// / 0..chartHeight coordinates the SVG's viewBox uses), positioned as a
// percentage so it tracks the chart as it scales to fit its container. Pair
// with utils/useChartCrosshair.js for the nearest-point hover/keyboard
// logic; this component only renders the box. The chart must wrap its
// <svg> in a `position: relative` element.
export default function ChartTooltip({ x, y, chartWidth, chartHeight, children, align = 'above' }) {
    const left = `${(x / chartWidth) * 100}%`;
    const top = `${(y / chartHeight) * 100}%`;
    const transform = align === 'above' ? 'translate(-50%, -120%)' : 'translate(-50%, 14%)';
    return (
        <div
            role="status"
            style={{
                position: 'absolute',
                left,
                top,
                transform,
                background: 'var(--surface)',
                border: '1px solid var(--hairline)',
                borderRadius: 8,
                padding: '0.5rem 0.65rem',
                fontSize: '0.75rem',
                color: 'var(--text)',
                whiteSpace: 'nowrap',
                pointerEvents: 'none',
                boxShadow: '0 8px 24px rgba(0,0,0,0.35)',
                zIndex: 5,
            }}
        >
            {children}
        </div>
    );
}
