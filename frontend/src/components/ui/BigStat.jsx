import React from 'react';
import Icon from '../common/Icon';
import useCountUp from './useCountUp';

const compactFormatter = new Intl.NumberFormat('en', { notation: 'compact' });
const plainFormatter = new Intl.NumberFormat('en');

export default function BigStat({ label, value, delta, digits = 0, compact = false, sparkline, className = '' }) {
    const numeric = typeof value === 'number' ? value : Number(value);
    const isNumeric = Number.isFinite(numeric);
    const animated = useCountUp(isNumeric ? numeric : 0);

    let display;
    if (!isNumeric) {
        display = value ?? '—';
    } else if (compact) {
        display = compactFormatter.format(Math.round(animated));
    } else {
        display = digits > 0 ? animated.toFixed(digits) : plainFormatter.format(Math.round(animated));
    }

    const deltaPositive = typeof delta === 'number' && delta > 0;
    const deltaNegative = typeof delta === 'number' && delta < 0;

    return (
        <div className={`big-stat ${className}`}>
            <span className="text-stat big-stat-value" title={isNumeric ? String(value) : undefined}>{display}</span>
            <span className="text-eyebrow big-stat-label">{label}</span>
            {typeof delta === 'number' && (
                <span className={`big-stat-delta${deltaPositive ? ' big-stat-delta--positive' : ''}${deltaNegative ? ' big-stat-delta--negative' : ''}`}>
                    <Icon name={deltaNegative ? 'arrow_downward' : 'arrow_upward'} size="0.9em" />
                    {Math.abs(delta).toFixed(digits > 0 ? digits : 1)}
                </span>
            )}
            {sparkline && sparkline.length > 1 && <Sparkline points={sparkline} />}
        </div>
    );
}

function Sparkline({ points, width = 100, height = 28 }) {
    const min = Math.min(...points);
    const max = Math.max(...points);
    const range = max - min || 1;
    const path = points
        .map((p, i) => {
            const x = (i / (points.length - 1)) * width;
            const y = height - ((p - min) / range) * height;
            return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
        })
        .join(' ');
    return (
        <svg viewBox={`0 0 ${width} ${height}`} className="big-stat-sparkline" aria-hidden="true">
            <path d={path} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
        </svg>
    );
}
