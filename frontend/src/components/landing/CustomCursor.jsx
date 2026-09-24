import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const LERP = 0.35;
const RING_BASE = 22;
const RING_MAGNETIC = 40;

const COARSE_POINTER = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(pointer: coarse)').matches
    : false;
const REDUCED_MOTION = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

// A 6px dot exactly at the pointer, a 34px ring that lags behind it, and
// magnetic pull on any [data-magnetic] button within `scopeRef`. Off on
// touch devices and reduced motion — the native cursor stays visible then.
export default function CustomCursor({ scopeRef }) {
    const dotRef = useRef(null);
    const ringRef = useRef(null);
    const [active, setActive] = useState(false);
    const [magnetic, setMagnetic] = useState(false);

    useEffect(() => {
        if (COARSE_POINTER || REDUCED_MOTION) return undefined;
        const scope = scopeRef.current;
        if (!scope) return undefined;

        const dot = dotRef.current;
        const ring = ringRef.current;
        const pointer = { x: -100, y: -100 };
        const ringPos = { x: -100, y: -100 };
        const hasMoved = { current: false };
        let raf = null;

        function onMouseMove(e) {
            pointer.x = e.clientX;
            pointer.y = e.clientY;
            if (!hasMoved.current) {
                hasMoved.current = true;
                setActive(true);
            }
        }
        function onMouseLeave() {
            hasMoved.current = false;
            setActive(false);
        }

        function tick() {
            ringPos.x += (pointer.x - ringPos.x) * LERP;
            ringPos.y += (pointer.y - ringPos.y) * LERP;
            if (dot) dot.style.transform = `translate(${pointer.x}px, ${pointer.y}px)`;
            if (ring) ring.style.transform = `translate(${ringPos.x}px, ${ringPos.y}px)`;
            raf = requestAnimationFrame(tick);
        }
        raf = requestAnimationFrame(tick);

        scope.addEventListener('mousemove', onMouseMove);
        scope.addEventListener('mouseleave', onMouseLeave);

        // Bind (and later unbind) magnetic listeners per element, tracked so
        // content that mounts/unmounts after this effect runs — e.g. a
        // "Start" or "Next" button appearing once a daily game loads its
        // state — still gets the magnetic pull, not just whatever existed
        // at the first paint.
        const bound = new Map();
        function bindMagnetic(el) {
            if (bound.has(el)) return;
            function onEnter() { setMagnetic(true); }
            function onMove(e) {
                const rect = el.getBoundingClientRect();
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;
                const dx = e.clientX - cx;
                const dy = e.clientY - cy;
                el.style.transition = 'none';
                el.style.transform = `translate(${dx * 0.25}px, ${dy * 0.35}px)`;
            }
            function onLeave() {
                setMagnetic(false);
                el.style.transition = 'transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)';
                el.style.transform = 'translate(0, 0)';
            }
            el.addEventListener('mouseenter', onEnter);
            el.addEventListener('mousemove', onMove);
            el.addEventListener('mouseleave', onLeave);
            bound.set(el, () => {
                el.removeEventListener('mouseenter', onEnter);
                el.removeEventListener('mousemove', onMove);
                el.removeEventListener('mouseleave', onLeave);
            });
        }
        function unbindMagnetic(el) {
            bound.get(el)?.();
            bound.delete(el);
        }

        Array.from(scope.querySelectorAll('[data-magnetic]')).forEach(bindMagnetic);

        const observer = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                mutation.addedNodes.forEach((node) => {
                    if (!(node instanceof Element)) return;
                    if (node.matches?.('[data-magnetic]')) bindMagnetic(node);
                    node.querySelectorAll?.('[data-magnetic]').forEach(bindMagnetic);
                });
                mutation.removedNodes.forEach((node) => {
                    if (!(node instanceof Element)) return;
                    if (node.matches?.('[data-magnetic]')) unbindMagnetic(node);
                    node.querySelectorAll?.('[data-magnetic]').forEach(unbindMagnetic);
                });
            }
        });
        observer.observe(scope, { childList: true, subtree: true });

        return () => {
            cancelAnimationFrame(raf);
            scope.removeEventListener('mousemove', onMouseMove);
            scope.removeEventListener('mouseleave', onMouseLeave);
            observer.disconnect();
            bound.forEach((cleanup) => cleanup());
        };
    }, [scopeRef]);

    if (COARSE_POINTER || REDUCED_MOTION) return null;
    if (typeof document === 'undefined') return null;

    const ringSize = magnetic ? RING_MAGNETIC : RING_BASE;

    // Portaled straight to <body> — rendering these inline, nested inside
    // page content, meant they sat inside whatever Framer Motion
    // `motion.div` wraps the current page. Framer keeps an inline
    // `transform` on that div at all times (not just mid-animation), and
    // per the CSS spec `position: fixed` becomes relative to the nearest
    // transformed ancestor instead of the real viewport — so the dot/ring
    // rendered visibly offset from the actual pointer, worse the more the
    // page had scrolled. Portaling to body guarantees no ancestor transform
    // can ever intercept it again.
    return createPortal(
        <>
            <span ref={dotRef} className="custom-cursor-dot" style={{ opacity: active ? 1 : 0 }} />
            <span
                ref={ringRef}
                className={`custom-cursor-ring${magnetic ? ' custom-cursor-ring--magnetic' : ''}`}
                style={{ width: ringSize, height: ringSize, marginLeft: -ringSize / 2, marginTop: -ringSize / 2, opacity: active ? 1 : 0 }}
            />
        </>,
        document.body
    );
}
