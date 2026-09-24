import React from 'react';

export function BentoGrid({ children, className = '' }) {
    return <div className={`bento-grid ${className}`}>{children}</div>;
}

export function Tile({ span = 4, children, className = '', ...rest }) {
    return (
        <div className={`bento-tile bento-tile--span-${span} ${className}`} {...rest}>
            {children}
        </div>
    );
}
