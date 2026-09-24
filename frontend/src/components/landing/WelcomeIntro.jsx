import React, { useEffect, useRef, useState } from 'react';
import { fetchSiteStats } from '../../services/api';

const CONVERGE_MS = 900;
const HOLD_MS = 600;
const BURST_MS = 700;
const TOTAL_MS = CONVERGE_MS + HOLD_MS + BURST_MS; // 2200ms — at the 2.2s cap.
const MAX_POINTS = 900;
const SESSION_KEY = 'nbahub_intro_shown';

const REDUCED_MOTION = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function lerpColor(a, b, t) {
    const ca = hexToRgb(a);
    const cb = hexToRgb(b);
    return `rgb(${Math.round(ca[0] + (cb[0] - ca[0]) * t)},${Math.round(ca[1] + (cb[1] - ca[1]) * t)},${Math.round(ca[2] + (cb[2] - ca[2]) * t)})`;
}
function gradAt(t) {
    if (t <= 0.5) return lerpColor('#ff6b1a', '#ff3d7f', t / 0.5);
    return lerpColor('#ff3d7f', '#2997ff', (t - 0.5) / 0.5);
}

function easeOutCubic(t) {
    return 1 - (1 - t) ** 3;
}

// Sample "NBA HUB" onto an offscreen canvas and return the lit pixel
// centers (~1 every 4px), capped to MAX_POINTS by random subsampling.
function sampleTextPoints(W, H) {
    const off = document.createElement('canvas');
    off.width = W;
    off.height = H;
    const octx = off.getContext('2d');
    const fontSize = Math.min(W * 0.14, 140);
    octx.font = `800 ${fontSize}px system-ui, -apple-system, sans-serif`;
    octx.textAlign = 'center';
    octx.textBaseline = 'middle';
    octx.fillStyle = '#fff';
    octx.fillText('NBA HUB', W / 2, H / 2);
    const { data } = octx.getImageData(0, 0, W, H);
    const points = [];
    for (let y = 0; y < H; y += 4) {
        for (let x = 0; x < W; x += 4) {
            if (data[(y * W + x) * 4 + 3] > 128) points.push({ x, y });
        }
    }
    if (points.length <= MAX_POINTS) return points;
    const out = [];
    const step = points.length / MAX_POINTS;
    for (let i = 0; i < MAX_POINTS; i++) out.push(points[Math.floor(i * step)]);
    return out;
}

function shouldPlay() {
    if (REDUCED_MOTION) return false;
    try {
        if (sessionStorage.getItem(SESSION_KEY)) return false;
        sessionStorage.setItem(SESSION_KEY, '1');
        return true;
    } catch {
        return true;
    }
}

// One-time "NBA HUB" welcome animation: particles converge into the
// wordmark, hold, then burst outward into the hero's normal particle field
// while this overlay fades, revealing the hero underneath. Plays once per
// session, skippable, and never runs under reduced motion.
export default function WelcomeIntro({ onDone }) {
    const canvasRef = useRef(null);
    const [playing] = useState(shouldPlay);
    const [count, setCount] = useState(0);
    const [totalPlayerSeasons, setTotalPlayerSeasons] = useState(null);
    const doneRef = useRef(false);
    const finishRef = useRef(null);

    useEffect(() => {
        if (!playing) {
            onDone();
            return undefined;
        }

        let active = true;
        fetchSiteStats().then((d) => { if (active) setTotalPlayerSeasons(d?.n_player_seasons ?? null); }).catch(() => {});

        const canvas = canvasRef.current;
        const ctx = canvas.getContext('2d');
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const W = window.innerWidth;
        const H = window.innerHeight;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        canvas.style.width = `${W}px`;
        canvas.style.height = `${H}px`;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

        const targets = sampleTextPoints(W, H);
        const cx = W / 2;
        const cy = H / 2;
        const particles = targets.map((t) => {
            const angle = Math.random() * Math.PI * 2;
            const r = Math.max(W, H) * (0.5 + Math.random() * 0.4);
            return {
                sx: cx + Math.cos(angle) * r,
                sy: cy + Math.sin(angle) * r,
                tx: t.x,
                ty: t.y,
                x: 0,
                y: 0,
                c: gradAt(t.x / W),
                burstAngle: Math.atan2(t.y - cy, t.x - cx) || angle,
            };
        });

        let raf = null;
        const start = performance.now();

        function finish() {
            if (doneRef.current) return;
            doneRef.current = true;
            if (raf) cancelAnimationFrame(raf);
            onDone();
        }
        finishRef.current = finish;

        function frame(now) {
            const elapsed = now - start;
            ctx.clearRect(0, 0, W, H);

            let overlayAlpha = 1;
            let burstT = 0;
            if (elapsed < CONVERGE_MS) {
                const t = easeOutCubic(Math.min(1, elapsed / CONVERGE_MS));
                for (const p of particles) {
                    p.x = p.sx + (p.tx - p.sx) * t;
                    p.y = p.sy + (p.ty - p.sy) * t;
                }
            } else if (elapsed < CONVERGE_MS + HOLD_MS) {
                for (const p of particles) { p.x = p.tx; p.y = p.ty; }
            } else {
                burstT = Math.min(1, (elapsed - CONVERGE_MS - HOLD_MS) / BURST_MS);
                const dist = burstT * Math.max(W, H) * 0.7;
                for (const p of particles) {
                    p.x = p.tx + Math.cos(p.burstAngle) * dist;
                    p.y = p.ty + Math.sin(p.burstAngle) * dist;
                }
                overlayAlpha = 1 - easeOutCubic(burstT);
            }

            for (const p of particles) {
                ctx.strokeStyle = p.c;
                ctx.globalAlpha = elapsed < CONVERGE_MS + HOLD_MS ? 0.9 : 0.9 * (1 - burstT);
                ctx.lineWidth = 1.8;
                ctx.lineCap = 'round';
                ctx.beginPath();
                ctx.moveTo(p.x - 2, p.y);
                ctx.lineTo(p.x + 2, p.y);
                ctx.stroke();
            }
            ctx.globalAlpha = 1;

            const progress = Math.min(1, elapsed / TOTAL_MS);
            if (totalPlayerSeasons != null) setCount(Math.round(easeOutCubic(progress) * totalPlayerSeasons));

            const overlay = canvas.parentElement;
            if (overlay) overlay.style.opacity = String(overlayAlpha);

            if (elapsed >= TOTAL_MS) {
                finish();
                return;
            }
            raf = requestAnimationFrame(frame);
        }
        raf = requestAnimationFrame(frame);

        function onSkip(e) {
            if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Escape') return;
            finish();
        }
        window.addEventListener('click', onSkip);
        window.addEventListener('keydown', onSkip);

        return () => {
            active = false;
            if (raf) cancelAnimationFrame(raf);
            window.removeEventListener('click', onSkip);
            window.removeEventListener('keydown', onSkip);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [playing]);

    if (!playing) return null;

    return (
        <div className="landing-intro-overlay" aria-hidden="true">
            <canvas ref={canvasRef} className="landing-intro-canvas" />
            <p className="landing-intro-counter">
                {totalPlayerSeasons == null ? '—' : count.toLocaleString()} player-seasons
            </p>
            <button type="button" className="landing-intro-skip" onClick={() => finishRef.current?.()}>
                Skip
            </button>
        </div>
    );
}
