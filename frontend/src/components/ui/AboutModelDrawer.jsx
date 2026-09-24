import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import Icon from '../common/Icon';

// Collapsed-by-default frosted drawer that sits above a tool's content and
// explains, in the project's own words already written for that tool, what
// the model/metric actually is — real methodology, not marketing copy.
export default function AboutModelDrawer({ title = 'About this model', children }) {
    const [open, setOpen] = useState(false);

    return (
        <div className="about-drawer">
            <button
                type="button"
                className="about-drawer-toggle"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
            >
                <span className="about-drawer-toggle-label">
                    <Icon name="info" size="1.05em" />
                    {title}
                </span>
                <Icon name={open ? 'expand_less' : 'expand_more'} />
            </button>
            <AnimatePresence initial={false}>
                {open && (
                    <motion.div
                        className="about-drawer-body-wrap"
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
                    >
                        <div className="about-drawer-body">{children}</div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
