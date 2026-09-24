import React, { useEffect, useRef } from 'react';

const GRID = 22;
const REPEL_RADIUS = 110;
const PARTICLE_CAP = 1800;

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

// Canvas particle field — a jittered grid of dashes that repel + swirl
// around the mouse, spring back to their home position, and settle.
// Ported verbatim from the design spec's physics.
export default function ParticleField({ className = '', density = 1 }) {
    const canvasRef = useRef(null);
    const containerRef = useRef(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        const container = containerRef.current;
        if (!canvas || !container) return undefined;
        const ctx = canvas.getContext('2d');

        let W = 0;
        let H = 0;
        let particles = [];
        const mouse = { x: 0, y: 0, in: false };
        let raf = null;
        let idleFrames = 0;
        let visible = true;
        let isLight = window.matchMedia?.('(prefers-color-scheme: light)').matches;

        function currentIsLight() {
            const attr = document.documentElement.getAttribute('data-theme');
            if (attr === 'light') return true;
            if (attr === 'dark') return false;
            return window.matchMedia?.('(prefers-color-scheme: light)').matches;
        }

        function buildParticles() {
            const rect = container.getBoundingClientRect();
            W = Math.max(1, Math.round(rect.width));
            H = Math.max(1, Math.round(rect.height));
            const dpr = window.devicePixelRatio || 1;
            canvas.width = W * dpr;
            canvas.height = H * dpr;
            canvas.style.width = `${W}px`;
            canvas.style.height = `${H}px`;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

            const spacing = GRID / Math.sqrt(density);
            const next = [];
            for (let y = spacing / 2; y < H && next.length < PARTICLE_CAP; y += spacing) {
                for (let x = spacing / 2; x < W && next.length < PARTICLE_CAP; x += spacing) {
                    const hx = x + (Math.random() - 0.5) * 10;
                    const hy = y + (Math.random() - 0.5) * 10;
                    next.push({
                        hx, hy, x: hx, y: hy, vx: 0, vy: 0,
                        c: gradAt(hx / W),
                        a: 0.15 + Math.random() * 0.35,
                    });
                }
            }
            particles = next;
        }

        function drawFrame() {
            isLight = currentIsLight();
            const alphaMul = isLight ? 1.3 : 1;
            ctx.clearRect(0, 0, W, H);
            let maxSpeed = 0;
            for (const p of particles) {
                const dx = p.x - mouse.x;
                const dy = p.y - mouse.y;
                const d2 = dx * dx + dy * dy;
                if (mouse.in && d2 < REPEL_RADIUS * REPEL_RADIUS) {
                    const d = Math.sqrt(d2) || 1;
                    const f = (1 - d / REPEL_RADIUS) * 2.2;
                    p.vx += (dx / d) * f - (dy / d) * f * 0.6;
                    p.vy += (dy / d) * f + (dx / d) * f * 0.6;
                }
                p.vx += (p.hx - p.x) * 0.04;
                p.vy += (p.hy - p.y) * 0.04;
                p.vx *= 0.86;
                p.vy *= 0.86;
                p.x += p.vx;
                p.y += p.vy;

                const sp = Math.hypot(p.vx, p.vy);
                maxSpeed = Math.max(maxSpeed, sp);
                const ang = sp > 0.05 ? Math.atan2(p.vy, p.vx) : 0.6;
                const len = 3 + Math.min(sp * 2.5, 9);
                ctx.strokeStyle = p.c;
                ctx.globalAlpha = Math.min(1, (p.a + sp * 0.15) * alphaMul);
                ctx.lineWidth = 1.6;
                ctx.lineCap = 'round';
                ctx.beginPath();
                ctx.moveTo(p.x - Math.cos(ang) * len / 2, p.y - Math.sin(ang) * len / 2);
                ctx.lineTo(p.x + Math.cos(ang) * len / 2, p.y + Math.sin(ang) * len / 2);
                ctx.stroke();
            }
            ctx.globalAlpha = 1;
            return maxSpeed;
        }

        function loop() {
            if (!visible) return;
            const maxSpeed = drawFrame();
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
            const rect = canvas.getBoundingClientRect();
            mouse.x = e.clientX - rect.left;
            mouse.y = e.clientY - rect.top;
            mouse.in = mouse.x >= 0 && mouse.x <= W && mouse.y >= 0 && mouse.y <= H;
            ensureRunning();
        }
        function onMouseLeave() {
            mouse.in = false;
        }

        buildParticles();

        if (REDUCED_MOTION) {
            drawFrame();
        } else {
            ensureRunning();
            window.addEventListener('mousemove', onMouseMove);
            container.addEventListener('mouseleave', onMouseLeave);
        }

        const ro = new ResizeObserver(() => {
            buildParticles();
            if (REDUCED_MOTION) drawFrame();
            else ensureRunning();
        });
        ro.observe(container);

        function onVisibilityChange() {
            visible = !document.hidden;
            if (visible) ensureRunning();
            else if (raf) { cancelAnimationFrame(raf); raf = null; }
        }
        document.addEventListener('visibilitychange', onVisibilityChange);

        const io = new IntersectionObserver(([entry]) => {
            visible = entry.isIntersecting && !document.hidden;
            if (visible) ensureRunning();
            else if (raf) { cancelAnimationFrame(raf); raf = null; }
        }, { threshold: 0 });
        io.observe(container);

        return () => {
            if (raf) cancelAnimationFrame(raf);
            window.removeEventListener('mousemove', onMouseMove);
            container.removeEventListener('mouseleave', onMouseLeave);
            document.removeEventListener('visibilitychange', onVisibilityChange);
            ro.disconnect();
            io.disconnect();
        };
    }, [density]);

    return (
        <div ref={containerRef} className={`particle-field ${className}`} aria-hidden="true">
            <canvas ref={canvasRef} />
        </div>
    );
}
