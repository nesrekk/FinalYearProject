import React, { useEffect, useRef } from 'react';

const N = 220;
const R = 96;
const C = N / 2;
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => (v + 0.5) / 16);

function onSeam(nx, ny, nz) {
    return Math.abs(nx) < 0.035 || Math.abs(ny) < 0.035 || Math.abs(Math.sqrt(nx * nx + nz * nz) - 0.72) < 0.04;
}

export default function DitherBall({ className = '' }) {
    const canvasRef = useRef(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        const ctx = canvas.getContext('2d');
        const img = ctx.createImageData(N, N);
        const d = img.data;
        const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        let rot = 0, tilt = 0, targetTilt = 0, spin = 0.012, glitch = 0, hover = false;
        let raf = 0, visible = true;

        function render() {
            rot += spin;
            tilt += (targetTilt - tilt) * 0.06;
            const cr = Math.cos(rot), sr = Math.sin(rot), ct = Math.cos(tilt), st = Math.sin(tilt);
            for (let py = 0; py < N; py++) {
                for (let px = 0; px < N; px++) {
                    const i = (py * N + px) * 4;
                    const dx = (px - C) / R, dy = (py - C) / R, rr = dx * dx + dy * dy;
                    if (rr > 1) { d[i + 3] = 0; continue; }
                    const dz = Math.sqrt(1 - rr);
                    const light = Math.max(0, -0.5 * dx - 0.6 * dy + 0.62 * dz) * 0.95 + 0.08;
                    const y1 = dy * ct - dz * st, z1 = dy * st + dz * ct;
                    const nx = dx * cr + z1 * sr, nz = -dx * sr + z1 * cr;
                    const ink = onSeam(nx, y1, nz) || light <= BAYER[(py % 4) * 4 + (px % 4)];
                    const v = ink ? 13 : 246;
                    d[i] = v; d[i + 1] = ink ? 13 : 240; d[i + 2] = ink ? 13 : 228; d[i + 3] = 255;
                }
            }
            ctx.putImageData(img, 0, 0);
            if (!reduced && (hover ? Math.random() < 0.25 : Math.random() < 0.02)) glitch = 8;
            if (glitch > 0) {
                glitch--;
                for (let k = 0; k < 4; k++) {
                    const y = (Math.random() * N) | 0, h = (4 + Math.random() * 16) | 0, off = ((Math.random() - 0.5) * 30) | 0;
                    ctx.drawImage(canvas, 0, y, N, h, off, y, N, h);
                }
            }
        }
        function loop() {
            render();
            if (visible && !reduced) raf = requestAnimationFrame(loop);
        }
        function onMove(e) {
            const rect = canvas.getBoundingClientRect();
            const x = (e.clientX - (rect.left + rect.width / 2)) / window.innerWidth;
            const y = (e.clientY - (rect.top + rect.height / 2)) / window.innerHeight;
            spin = 0.012 + x * 0.05;
            targetTilt = Math.max(-0.6, Math.min(0.6, y * 1.2));
            hover = Math.abs(e.clientX - (rect.left + rect.width / 2)) < rect.width / 2
                && Math.abs(e.clientY - (rect.top + rect.height / 2)) < rect.height / 2;
        }

        const io = new IntersectionObserver(([entry]) => {
            const was = visible;
            visible = entry.isIntersecting;
            if (visible && !was && !reduced) raf = requestAnimationFrame(loop);
        });
        io.observe(canvas);
        window.addEventListener('pointermove', onMove, { passive: true });
        raf = requestAnimationFrame(loop);
        return () => {
            cancelAnimationFrame(raf);
            io.disconnect();
            window.removeEventListener('pointermove', onMove);
        };
    }, []);

    return <canvas ref={canvasRef} width={N} height={N} className={className} aria-hidden="true" />;
}
