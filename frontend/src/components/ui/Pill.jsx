import React from 'react';
import Icon from '../common/Icon';

export function Pill({ children, icon, tone = 'neutral', title, className = '' }) {
    return (
        <span className={`kit-pill kit-pill--${tone} ${className}`} title={title}>
            {icon && <Icon name={icon} size="0.9em" />}
            {children}
        </span>
    );
}

export function Badge({ children, tone = 'neutral', className = '' }) {
    return <span className={`kit-badge kit-badge--${tone} ${className}`}>{children}</span>;
}

// "n = 42" sample-size pill — greyed (muted tone) when the sample is small.
export function SamplePill({ n, minReliable = 25, className = '' }) {
    const small = typeof n === 'number' && n < minReliable;
    return (
        <Pill
            tone={small ? 'muted' : 'neutral'}
            title={small ? `Small sample: fewer than ${minReliable} observations` : 'Sample size'}
            className={className}
        >
            n = {n?.toLocaleString?.() ?? n}
        </Pill>
    );
}
