import React, { useEffect, useRef, useState } from 'react';
import { fetchCurrentMeta, fetchSiteStats } from '../../services/api';
import { TEAM_COLORS, abbrFromTeamName } from '../../utils/teamAssets';

const REDUCED_MOTION = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
}

// A band of 30 team-colored ribbons (canvas 2D sine-wave strips) that ripple
// over time; the ribbon nearest the mouse ripples harder and shows that
// team's real record (from /meta/current) in a frosted tooltip. Draws only
// while the section is on screen.
export default function TeamRibbons() {
    const canvasRef = useRef(null);
    const containerRef = useRef(null);
    const [teams, setTeams] = useState(null);
    const [sinceYear, setSinceYear] = useState(null);
    const [tooltip, setTooltip] = useState(null);

    useEffect(() => {
        let active = true;
        fetchCurrentMeta().then((meta) => {
            if (!active) return;
            const all = [...(meta?.standings?.eastern || []), ...(meta?.standings?.western || [])]
                .map((t) => ({ ...t, abbr: t.abbr || abbrFromTeamName(t.team) }));
            setTeams(all.filter((t) => TEAM_COLORS[t.abbr]));
        }).catch(() => { if (active) setTeams([]); });
        fetchSiteStats().then((s) => { if (active) setSinceYear(s?.season_min ?? null); }).catch(() => {});
        return () => { active = false; };
    }, []);

    useEffect(() => {
        if (!teams || teams.length === 0) return undefined;
        const canvas = canvasRef.current;
        const container = containerRef.current;
        if (!canvas || !container) return undefined;
        const ctx = canvas.getContext('2d');

        let W = 0;
        let H = 0;
        let raf = null;
        let visible = true;
        const mouse = { y: -1000, x: -1000, in: false };
        const rowH = () => H / teams.length;

        function resize() {
            const rect = container.getBoundingClientRect();
            W = Math.max(1, Math.round(rect.width));
            H = Math.max(1, Math.round(rect.height));
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            canvas.width = W * dpr;
            canvas.height = H * dpr;
            canvas.style.width = `${W}px`;
            canvas.style.height = `${H}px`;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        }

        function draw(t) {
            ctx.clearRect(0, 0, W, H);
            const rh = rowH();
            teams.forEach((team, i) => {
                const cy = rh * (i + 0.5);
                const distToMouse = mouse.in ? Math.abs(mouse.y - cy) : Infinity;
                const near = Math.max(0, 1 - distToMouse / (rh * 3));
                const amp = 3 + near * 10;
                const alpha = 0.35 + near * 0.5;
                const rgb = hexToRgb(TEAM_COLORS[team.abbr] || '#888888');
                ctx.strokeStyle = `rgba(${rgb},${alpha})`;
                ctx.lineWidth = Math.max(1.5, rh * 0.28);
                ctx.beginPath();
                for (let x = 0; x <= W; x += 8) {
                    const y = cy + Math.sin(x * 0.015 + t * 0.0012 + i * 0.6) * amp;
                    if (x === 0) ctx.moveTo(x, y);
                    else ctx.lineTo(x, y);
                }
                ctx.stroke();
            });
        }

        function loop(t) {
            if (!visible) return;
            draw(t);
            if (!REDUCED_MOTION) raf = requestAnimationFrame(loop);
        }

        resize();
        if (REDUCED_MOTION) draw(0);
        else raf = requestAnimationFrame(loop);

        const ro = new ResizeObserver(() => { resize(); if (REDUCED_MOTION) draw(0); });
        ro.observe(container);

        const io = new IntersectionObserver(([entry]) => {
            visible = entry.isIntersecting;
            if (visible && !REDUCED_MOTION && raf == null) raf = requestAnimationFrame(loop);
            else if (!visible && raf) { cancelAnimationFrame(raf); raf = null; }
        }, { threshold: 0 });
        io.observe(container);

        function onMouseMove(e) {
            const rect = container.getBoundingClientRect();
            mouse.x = e.clientX - rect.left;
            mouse.y = e.clientY - rect.top;
            mouse.in = mouse.x >= 0 && mouse.x <= W && mouse.y >= 0 && mouse.y <= H;
            if (mouse.in) {
                const idx = Math.min(teams.length - 1, Math.max(0, Math.floor(mouse.y / rowH())));
                const team = teams[idx];
                setTooltip({ team, x: mouse.x, y: mouse.y });
            } else {
                setTooltip(null);
            }
            if (REDUCED_MOTION) draw(performance.now());
        }
        function onMouseLeave() {
            mouse.in = false;
            setTooltip(null);
            if (REDUCED_MOTION) draw(performance.now());
        }
        container.addEventListener('mousemove', onMouseMove);
        container.addEventListener('mouseleave', onMouseLeave);

        return () => {
            if (raf) cancelAnimationFrame(raf);
            ro.disconnect();
            io.disconnect();
            container.removeEventListener('mousemove', onMouseMove);
            container.removeEventListener('mouseleave', onMouseLeave);
        };
    }, [teams]);

    return (
        <div className="landing-section landing-ribbons-section">
            <p className="text-eyebrow">League</p>
            <h2 className="text-display-lg">
                Every team. Every game. <span className="text-gradient">{sinceYear ? `Since ${sinceYear}.` : 'Since day one.'}</span>
            </h2>
            <div className="ribbons-canvas-wrap" ref={containerRef}>
                <canvas ref={canvasRef} aria-hidden="true" />
                {tooltip && (
                    <div
                        className="ribbons-tooltip"
                        style={{ left: tooltip.x, top: tooltip.y }}
                    >
                        <strong>{tooltip.team.team || tooltip.team.abbr}</strong>
                        <span>{tooltip.team.w}-{tooltip.team.l}{tooltip.team.pct != null ? ` · ${tooltip.team.pct}` : ''}</span>
                    </div>
                )}
            </div>
            <p className="ribbons-fallback-text">
                {teams === null && 'Loading real standings…'}
                {teams && teams.length === 0 && 'Standings unavailable right now.'}
            </p>
        </div>
    );
}
