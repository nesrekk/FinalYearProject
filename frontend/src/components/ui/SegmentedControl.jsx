import React from 'react';
import { motion } from 'framer-motion';

export default function SegmentedControl({ options, value, onChange, layoutIdPrefix = 'segmented', className = '' }) {
    return (
        <div className={`segmented-control ${className}`} role="tablist">
            {options.map((opt) => {
                const active = opt.value === value;
                return (
                    <button
                        key={opt.value}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        className={`segmented-control-btn${active ? ' segmented-control-btn--active' : ''}`}
                        onClick={() => onChange(opt.value)}
                    >
                        {active && (
                            <motion.span
                                layoutId={`${layoutIdPrefix}-pill`}
                                className="segmented-control-pill"
                                transition={{ type: 'spring', stiffness: 260, damping: 30 }}
                            />
                        )}
                        <span className="segmented-control-label">{opt.label}</span>
                    </button>
                );
            })}
        </div>
    );
}
