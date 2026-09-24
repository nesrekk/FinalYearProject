import React, { useEffect, useImperativeHandle, useRef, forwardRef } from 'react';

const HERO_GRID = 22;
const REST_GRID = 34;
const REST_ALPHA_MUL = 0.6;
const REPEL_RADIUS = 110;
const PARTICLE_CAP_DESKTOP = 2500;
const PARTICLE_CAP_MOBILE = 900;
const MOBILE_BREAKPOINT = 768;
const MORPH_STAGGER_MS = 450;
const MORPH_DIM_ALPHA = 0.35;

function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function lerpColor(a, b, t) {
    const ca = hexToRgb(a);
    const cb = hexToRgb(b);
    const r = Math.round(ca[0] + (cb[0] - ca[0]) * t);
    const g = Math.round(ca[1] + (cb[1] - ca[1]) * t);
    const bch = Math.round(ca[2] + (cb[2] - ca[2]) * t);
    return `rgb(${r},${g},${bch})`;
}

// 0→.5 blends brand orange→pink, .5→1 blends pink→blue.
function gradAt(t) {
    if (t <= 0.5) return lerpColor('#ff6b1a', '#ff3d7f', t / 0.5);
    return lerpColor('#ff3d7f', '#2997ff', (t - 0.5) / 0.5);
}

const REDUCED_MOTION = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

// Single canvas particle field, fixed to the viewport but seeded across the
// whole scrollable page in world space (particle.y is a document-space
// coordinate; each frame subtracts window.scrollY to get screen space). That
// lets the same field run behind every section instead of one instance per
// section. Particles above `heroHeight` (world y) use a denser grid; below
// it they use a lighter one so body text stays readable. Ported physics
// (repel + swirl + spring-home) is otherwise unchanged from the hero-only
// version.
//
// Morph engine (ref API): setTargets(points | null) reassigns the nearest
// available particles to `points` (an array of {x, y} in the SAME
// document/world-space coordinates as everything else — callers compute a
// section's shape from its real bounding rect + window.scrollY so it scrolls
// naturally with that section). Each assigned particle springs to its target
// instead of its home position, staggered by the target's position in the
// array so it reads as a flow rather than a jump; particles left over keep
// drifting as dim background. setTargets(null) releases every particle back
// to its home position. Mouse repel keeps working on morphed particles too,
// since it's applied on top of whichever target (home or morph) is active.
const ParticleField = forwardRef(function ParticleField({ className = '', heroHeight = 720 }, ref) {
    const canvasRef = useRef(null);
    const containerRef = useRef(null);
    const heroHeightRef = useRef(heroHeight);
    const setTargetsRef = useRef(() => {});

    useImperativeHandle(ref, () => ({
        setTargets: (points) => setTargetsRef.current(points),
    }), []);

    useEffect(() => {
        heroHeightRef.current = heroHeight;
    }, [heroHeight]);

    useEffect(() => {
        const canvas = canvasRef.current;
        const container = containerRef.current;
        if (!canvas || !container) return undefined;
        const ctx = canvas.getContext('2d');

        let W = 0;
        let H = 0;
        let worldH = 0;
        let particles = [];
        const mouse = { x: 0, y: 0, in: false };
        let raf = null;
        let idleFrames = 0;
        let visible = true;
        let hasActiveMorph = false;
        let isLight = window.matchMedia?.('(prefers-color-scheme: light)').matches;

        function currentIsLight() {
            const attr = document.documentElement.getAttribute('data-theme');
            if (attr === 'light') return true;
            if (attr === 'dark') return false;
            return window.matchMedia?.('(prefers-color-scheme: light)').matches;
        }

        function particleCap() {
            return W < MOBILE_BREAKPOINT ? PARTICLE_CAP_MOBILE : PARTICLE_CAP_DESKTOP;
        }

        function buildParticles() {
            W = Math.max(1, Math.round(window.innerWidth));
            H = Math.max(1, Math.round(window.innerHeight));
            worldH = Math.max(H, Math.round(document.documentElement.scrollHeight));
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            canvas.width = W * dpr;
            canvas.height = H * dpr;
            canvas.style.width = `${W}px`;
            canvas.style.height = `${H}px`;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

            const cap = particleCap();
            const hero = Math.min(heroHeightRef.current, worldH);
            const restHeight = Math.max(0, worldH - hero);

            // Budget particles by area rather than truncating the row scan at
            // the cap: a tall (or, on a narrow pane, even a hero-only-height)
            // page needs more than `cap` particles at the nominal spacings,
            // and stopping mid-scan left a hard cutoff line partway down with
            // nothing below it. Instead: reserve a minimum slice of the
            // budget for the rest-of-page zone so it's never literally empty,
            // give the hero whatever's left (widening its spacing past the
            // ~22px nominal only if it wouldn't otherwise fit), then spread
            // the rest zone's own budget across the *whole* remaining height.
            const heroCountNominal = (W * hero) / (HERO_GRID * HERO_GRID);
            const minRestBudget = restHeight > 0 ? Math.max(20, cap * 0.05) : 0;
            const heroBudget = Math.min(heroCountNominal, Math.max(1, cap - minRestBudget));
            const heroSpacing = heroCountNominal > heroBudget
                ? Math.sqrt((W * hero) / heroBudget)
                : HERO_GRID;
            const restBudget = Math.max(minRestBudget, cap - heroBudget);
            const restSpacing = restHeight > 0
                ? Math.max(REST_GRID, Math.sqrt((W * restHeight) / restBudget))
                : Infinity;

            const next = [];
            for (let y = heroSpacing / 2; y < worldH; y += (y < hero ? heroSpacing : restSpacing)) {
                if (y >= hero && !Number.isFinite(restSpacing)) break;
                const spacing = y < hero ? heroSpacing : restSpacing;
                for (let x = spacing / 2; x < W && next.length < cap; x += spacing) {
                    const hx = x + (Math.random() - 0.5) * 10;
                    const hy = y + (Math.random() - 0.5) * 10;
                    const inRest = hy >= hero;
                    next.push({
                        hx, hy, x: hx, y: hy, vx: 0, vy: 0,
                        c: gradAt(hx / W),
                        a: (0.15 + Math.random() * 0.35) * (inRest ? REST_ALPHA_MUL : 1),
                        mtx: null, mty: null, morphAt: 0,
                    });
                }
            }
            particles = next;
        }

        function drawFrame(now) {
            isLight = currentIsLight();
            const alphaMul = isLight ? 1.3 : 1;
            const scrollY = window.scrollY || 0;
            ctx.clearRect(0, 0, W, H);
            let maxSpeed = 0;
            for (const p of particles) {
                const morphed = p.mtx != null && now >= p.morphAt;
                const tgx = morphed ? p.mtx : p.hx;
                const tgy = morphed ? p.mty : p.hy;

                const dx = p.x - mouse.x;
                const dy = p.y - mouse.y;
                const d2 = dx * dx + dy * dy;
                if (mouse.in && d2 < REPEL_RADIUS * REPEL_RADIUS) {
                    const d = Math.sqrt(d2) || 1;
                    const f = (1 - d / REPEL_RADIUS) * 2.2;
                    p.vx += (dx / d) * f - (dy / d) * f * 0.6;
                    p.vy += (dy / d) * f + (dx / d) * f * 0.6;
                }
                p.vx += (tgx - p.x) * 0.04;
                p.vy += (tgy - p.y) * 0.04;
                p.vx *= 0.86;
                p.vy *= 0.86;
                p.x += p.vx;
                p.y += p.vy;

                const sp = Math.hypot(p.vx, p.vy);
                maxSpeed = Math.max(maxSpeed, sp);

                const screenY = p.y - scrollY;
                if (screenY < -20 || screenY > H + 20) continue;

                const ang = sp > 0.05 ? Math.atan2(p.vy, p.vx) : 0.6;
                const len = 3 + Math.min(sp * 2.5, 9);
                const dim = hasActiveMorph && p.mtx == null ? MORPH_DIM_ALPHA : 1;
                ctx.strokeStyle = p.c;
                ctx.globalAlpha = Math.min(1, (p.a + sp * 0.15) * alphaMul * dim);
                ctx.lineWidth = 1.6;
                ctx.lineCap = 'round';
                ctx.beginPath();
                ctx.moveTo(p.x - Math.cos(ang) * len / 2, screenY - Math.sin(ang) * len / 2);
                ctx.lineTo(p.x + Math.cos(ang) * len / 2, screenY + Math.sin(ang) * len / 2);
                ctx.stroke();
            }
            ctx.globalAlpha = 1;
            return maxSpeed;
        }

        function loop(now) {
            if (!visible) return;
            const maxSpeed = drawFrame(now);
            if (!mouse.in && maxSpeed < 0.02) {
                idleFrames += 1;
            } else {
                idleFrames = 0;
            }
            if (idleFrames < 30) {
                raf = requestAnimationFrame(loop);
            } else {
                raf = null;
            }
        }

        function ensureRunning() {
            if (raf == null && visible) {
                idleFrames = 0;
                raf = requestAnimationFrame(loop);
            }
        }

        function onMouseMove(e) {
            mouse.x = e.clientX;
            mouse.y = e.clientY + (window.scrollY || 0);
            mouse.in = e.clientX >= 0 && e.clientX <= W && e.clientY >= 0 && e.clientY <= H;
            ensureRunning();
        }
        function onMouseLeave() {
            mouse.in = false;
        }
        function onScroll() {
            ensureRunning();
        }

        function setTargets(points) {
            // Release every particle back to its home position.
            if (!points || points.length === 0) {
                for (const p of particles) { p.mtx = null; p.mty = null; }
                hasActiveMorph = false;
                if (REDUCED_MOTION) drawFrame(performance.now());
                else ensureRunning();
                return;
            }

            // Re-pick from the whole field each time (not just currently
            // unmorphed particles) so a new shape can grow out of whichever
            // particles already ended up near it, including ones still
            // mid-flight from the previous shape.
            const pool = particles.slice();
            for (const p of particles) { p.mtx = null; p.mty = null; }

            const n = Math.min(points.length, pool.length);
            const now = performance.now();
            for (let i = 0; i < n; i++) {
                const target = points[i];
                // Nearest-remaining-particle assignment so a new shape grows
                // out of whichever particles are already close to it instead
                // of a random reshuffle.
                let bestIdx = -1;
                let bestD = Infinity;
                for (let j = 0; j < pool.length; j++) {
                    const cand = pool[j];
                    if (cand._taken) continue;
                    const d = (cand.x - target.x) ** 2 + (cand.y - target.y) ** 2;
                    if (d < bestD) { bestD = d; bestIdx = j; }
                }
                if (bestIdx === -1) break;
                const chosen = pool[bestIdx];
                chosen._taken = true;
                chosen.mtx = target.x;
                chosen.mty = target.y;
                chosen.morphAt = now + (i / n) * MORPH_STAGGER_MS;
                if (REDUCED_MOTION) { chosen.x = target.x; chosen.y = target.y; }
            }
            for (const p of pool) delete p._taken;
            hasActiveMorph = true;
            if (REDUCED_MOTION) drawFrame(performance.now());
            else ensureRunning();
        }
        setTargetsRef.current = setTargets;

        buildParticles();

        if (REDUCED_MOTION) {
            drawFrame(performance.now());
        } else {
            ensureRunning();
            window.addEventListener('mousemove', onMouseMove);
            window.addEventListener('mouseleave', onMouseLeave);
            window.addEventListener('scroll', onScroll, { passive: true });
        }

        const ro = new ResizeObserver(() => {
            buildParticles();
            if (REDUCED_MOTION) drawFrame(performance.now());
            else ensureRunning();
        });
        ro.observe(document.body);

        function onVisibilityChange() {
            visible = !document.hidden;
            if (visible) ensureRunning();
            else if (raf) { cancelAnimationFrame(raf); raf = null; }
        }
        document.addEventListener('visibilitychange', onVisibilityChange);

        return () => {
            setTargetsRef.current = () => {};
            if (raf) cancelAnimationFrame(raf);
            window.removeEventListener('mousemove', onMouseMove);
            window.removeEventListener('mouseleave', onMouseLeave);
            window.removeEventListener('scroll', onScroll);
            document.removeEventListener('visibilitychange', onVisibilityChange);
            ro.disconnect();
        };
    }, []);

    return (
        <div ref={containerRef} className={`particle-field-fixed ${className}`} aria-hidden="true">
            <canvas ref={canvasRef} />
        </div>
    );
});

export default ParticleField;
