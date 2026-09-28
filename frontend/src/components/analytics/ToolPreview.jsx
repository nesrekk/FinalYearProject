import React from 'react';

// Small decorative motif per chart archetype, shown on an index tile so the
// grid isn't just text — purely illustrative (fixed shapes, not real
// numbers), never labelled or usable as a data readout.
const MOTIFS = {
    bar: (
        <svg viewBox="0 0 120 48" className="tool-preview-svg" aria-hidden="true">
            {[14, 30, 22, 40, 16, 34].map((h, i) => (
                <rect key={i} x={4 + i * 19} y={44 - h} width="12" height={h} rx="2" className="tool-preview-shape" style={{ opacity: 0.35 + i * 0.09 }} />
            ))}
        </svg>
    ),
    line: (
        <svg viewBox="0 0 120 48" className="tool-preview-svg" aria-hidden="true">
            <polyline points="4,36 22,26 40,32 58,14 76,20 94,8 116,18" fill="none" className="tool-preview-line" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="116" cy="18" r="3.5" className="tool-preview-dot" />
        </svg>
    ),
    radar: (
        <svg viewBox="0 0 48 48" className="tool-preview-svg tool-preview-svg--square" aria-hidden="true">
            <polygon points="24,6 40,18 34,40 14,40 8,18" fill="none" className="tool-preview-line" strokeWidth="1.5" opacity="0.35" />
            <polygon points="24,12 34,20 30,36 18,36 14,20" className="tool-preview-shape" opacity="0.5" />
        </svg>
    ),
    scatter: (
        <svg viewBox="0 0 120 48" className="tool-preview-svg" aria-hidden="true">
            {[[10, 30], [26, 18], [40, 34], [55, 12], [68, 26], [82, 8], [96, 22], [110, 16]].map(([x, y], i) => (
                <circle key={i} cx={x} cy={y} r="3.5" className="tool-preview-dot" style={{ opacity: 0.4 + (i % 3) * 0.15 }} />
            ))}
        </svg>
    ),
    radial: (
        <svg viewBox="0 0 48 48" className="tool-preview-svg tool-preview-svg--square" aria-hidden="true">
            <circle cx="24" cy="24" r="18" fill="none" className="tool-preview-line" strokeWidth="1.5" opacity="0.3" />
            <circle cx="24" cy="24" r="4" className="tool-preview-dot" />
            {[0, 60, 120, 180, 240, 300].map((deg, i) => {
                const r = 18;
                const x = 24 + r * Math.cos((deg * Math.PI) / 180);
                const y = 24 + r * Math.sin((deg * Math.PI) / 180);
                return <circle key={i} cx={x} cy={y} r="2.5" className="tool-preview-dot" style={{ opacity: 0.5 }} />;
            })}
        </svg>
    ),
    network: (
        <svg viewBox="0 0 120 48" className="tool-preview-svg" aria-hidden="true">
            <line x1="20" y1="24" x2="60" y2="12" className="tool-preview-line" strokeWidth="1.5" opacity="0.4" />
            <line x1="20" y1="24" x2="60" y2="36" className="tool-preview-line" strokeWidth="1.5" opacity="0.4" />
            <line x1="60" y1="12" x2="100" y2="24" className="tool-preview-line" strokeWidth="1.5" opacity="0.4" />
            <line x1="60" y1="36" x2="100" y2="24" className="tool-preview-line" strokeWidth="1.5" opacity="0.4" />
            {[[20, 24], [60, 12], [60, 36], [100, 24]].map(([x, y], i) => (
                <circle key={i} cx={x} cy={y} r="5" className="tool-preview-dot" />
            ))}
        </svg>
    ),
    heatmap: (
        <svg viewBox="0 0 120 48" className="tool-preview-svg" aria-hidden="true">
            {[0, 1, 2, 3].flatMap((r) => [0, 1, 2, 3, 4, 5].map((c) => (
                <rect key={`${r}-${c}`} x={6 + c * 18} y={2 + r * 11} width="16" height="9" rx="1.5"
                    className="tool-preview-shape" style={{ opacity: 0.15 + (((r * 7 + c * 5) % 6) * 0.13) }} />
            )))}
        </svg>
    ),
    bracket: (
        <svg viewBox="0 0 120 48" className="tool-preview-svg" aria-hidden="true">
            <path d="M6,8 H30 M6,24 H30 M6,40 H30 M30,8 V24 M30,40 V24 M30,24 H60 M60,16 H84 M60,32 V16 M84,16 V24 M84,24 H110" fill="none" className="tool-preview-line" strokeWidth="1.5" opacity="0.5" />
        </svg>
    ),
};

export default function ToolPreview({ variant = 'bar' }) {
    return <div className="tool-preview">{MOTIFS[variant] || MOTIFS.bar}</div>;
}
