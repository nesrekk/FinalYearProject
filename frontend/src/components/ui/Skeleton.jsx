import React from 'react';

// Shimmer placeholder shaped like the content it stands in for. Use
// `variant="text"` for a line, "circle" for an avatar, "rect" (default)
// for a card/image block.
export default function Skeleton({ variant = 'rect', width, height, className = '' }) {
    const style = {
        width: width ?? (variant === 'text' ? '100%' : undefined),
        height: height ?? (variant === 'text' ? '1em' : variant === 'circle' ? width : undefined),
    };
    return <span className={`skeleton skeleton--${variant} ${className}`} style={style} aria-hidden="true" />;
}

export function SkeletonGroup({ lines = 3, className = '' }) {
    return (
        <div className={`skeleton-group ${className}`}>
            {Array.from({ length: lines }).map((_, i) => (
                <Skeleton key={i} variant="text" width={i === lines - 1 ? '60%' : '100%'} />
            ))}
        </div>
    );
}
