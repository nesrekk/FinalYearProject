import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import Icon from '../common/Icon';

// Frosted modal, centered on desktop, a bottom sheet on mobile (CSS
// media query swaps the layout, same component/markup either way).
export default function Modal({ open, onClose, title, children, className = '' }) {
    useEffect(() => {
        if (!open) return undefined;
        function onKeyDown(e) {
            if (e.key === 'Escape') onClose?.();
        }
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, [open, onClose]);

    if (typeof document === 'undefined') return null;

    return createPortal(
        <AnimatePresence>
            {open && (
                <motion.div
                    className="kit-modal-overlay"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.18 }}
                    onClick={onClose}
                >
                    <motion.div
                        className={`kit-modal ${className}`}
                        initial={{ opacity: 0, y: 24 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 24 }}
                        transition={{ type: 'spring', stiffness: 260, damping: 30 }}
                        onClick={(e) => e.stopPropagation()}
                    >
                        {title && (
                            <div className="kit-modal-header">
                                <h3 className="text-headline">{title}</h3>
                                <button type="button" className="kit-modal-close" onClick={onClose} aria-label="Close">
                                    <Icon name="close" />
                                </button>
                            </div>
                        )}
                        <div className="kit-modal-body">{children}</div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>,
        document.body
    );
}
