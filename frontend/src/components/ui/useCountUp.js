import { useEffect, useRef, useState } from 'react';

const REDUCED_MOTION = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

// Counts a number up from its previous value to `target` over `duration`ms.
// Skips the animation entirely under prefers-reduced-motion.
export default function useCountUp(target, duration = 900) {
    const [value, setValue] = useState(target);
    const fromRef = useRef(target);
    const frameRef = useRef(null);

    useEffect(() => {
        const to = Number(target) || 0;
        if (to === fromRef.current) return;
        if (REDUCED_MOTION) {
            fromRef.current = to;
            Promise.resolve().then(() => setValue(to));
            return;
        }
        const from = fromRef.current;
        const start = performance.now();

        function tick(now) {
            const t = Math.min(1, (now - start) / duration);
            const eased = 1 - Math.pow(1 - t, 3);
            setValue(from + (to - from) * eased);
            if (t < 1) {
                frameRef.current = requestAnimationFrame(tick);
            } else {
                fromRef.current = to;
            }
        }
        frameRef.current = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(frameRef.current);
    }, [target, duration]);

    return value;
}
