import React, { useEffect, useRef, useState } from 'react';

const REDUCED_MOTION = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

// A soft radial glow that follows the mouse over the hero, fading out on
// mouseleave. Sits between the particle canvas and the hero text.
export default function CursorGlow({ scopeRef }) {
    const glowRef = useRef(null);
    const [visible, setVisible] = useState(false);

    useEffect(() => {
        if (REDUCED_MOTION) return undefined;
        const scope = scopeRef.current;
        if (!scope) return undefined;
        let raf = null;
        const pos = { x: 0, y: 0 };

        function onMouseMove(e) {
            const rect = scope.getBoundingClientRect();
            pos.x = e.clientX - rect.left;
            pos.y = e.clientY - rect.top;
            setVisible(true);
            if (raf == null) {
                raf = requestAnimationFrame(() => {
                    if (glowRef.current) {
                        glowRef.current.style.background =
                            `radial-gradient(360px circle at ${pos.x}px ${pos.y}px, rgba(255,107,26,.16), rgba(41,151,255,.10) 45%, transparent 70%)`;
                    }
                    raf = null;
                });
            }
        }
        function onMouseLeave() {
            setVisible(false);
        }

        scope.addEventListener('mousemove', onMouseMove);
        scope.addEventListener('mouseleave', onMouseLeave);
        return () => {
            if (raf) cancelAnimationFrame(raf);
            scope.removeEventListener('mousemove', onMouseMove);
            scope.removeEventListener('mouseleave', onMouseLeave);
        };
    }, [scopeRef]);

    if (REDUCED_MOTION) return null;

    return <div ref={glowRef} className="cursor-glow" style={{ opacity: visible ? 1 : 0 }} aria-hidden="true" />;
}
