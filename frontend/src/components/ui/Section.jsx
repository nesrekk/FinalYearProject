import React from 'react';
import { motion } from 'framer-motion';

export default function Section({ eyebrow, title, subtitle, children, className = '' }) {
    return (
        <motion.section
            className={`kit-section ${className}`}
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-80px' }}
            transition={{ type: 'spring', stiffness: 260, damping: 30 }}
        >
            {(eyebrow || title || subtitle) && (
                <header className="kit-section-header">
                    {eyebrow && <p className="text-eyebrow">{eyebrow}</p>}
                    {title && <h2 className="text-display kit-section-title">{title}</h2>}
                    {subtitle && <p className="kit-section-subtitle">{subtitle}</p>}
                </header>
            )}
            {children}
        </motion.section>
    );
}
