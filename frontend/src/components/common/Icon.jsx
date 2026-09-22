import React from 'react';

// Material Symbols Outlined — replaces emoji icons app-wide. `name` is a
// Material Symbols icon name (e.g. "dashboard", "sports_basketball").
export default function Icon({ name, size, className = '', style = {}, fill = false }) {
    return (
        <span
            className={`material-symbols-outlined icon ${className}`}
            style={{ fontSize: size, fontVariationSettings: fill ? "'FILL' 1" : undefined, ...style }}
            aria-hidden="true"
        >
            {name}
        </span>
    );
}
