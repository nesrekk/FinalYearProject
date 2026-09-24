import React, { useEffect, useRef, useState } from 'react';
import { fetchCurrentMeta, fetchMVPPrediction, fetchShotSeasons, fetchPlayerShots } from '../../services/api';

const REDUCED_MOTION = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

const ARC_MS = 1500;

async function resolveFavorite() {
    const meta = await fetchCurrentMeta().catch(() => null);
    const start = meta?.season ?? new Date().getFullYear();
    for (let season = start; season >= start - 3; season--) {
        const mvp = await fetchMVPPrediction(season).catch(() => null);
        const favorite = mvp?.results?.[0];
        if (favorite) return { season, favorite };
    }
    return null;
}

// Behind the hero headline, real made shots from the current MVP favorite's
// latest logged season briefly re-enact: a dot arcs from the real shot
// location to the rim, fades, then the next one plays (~1 every 1.5s). No
// video asset — this is the "hero" the rest of the page's particle language
// is built from. Silently renders nothing if no real makes are available.
export default function LiveShotHero() {
    const canvasRef = useRef(null);
    const [caption, setCaption] = useState(null);
    const [ready, setReady] = useState(false);
    const shotsRef = useRef([]);

    useEffect(() => {
        if (REDUCED_MOTION) return;
        let active = true;
        (async () => {
            const resolved = await resolveFavorite();
            if (!active || !resolved) return;
            const { favorite } = resolved;
            const seasonsRes = await fetchShotSeasons(favorite.player_name).catch(() => null);
            if (!active || !seasonsRes?.seasons?.length) return;
            const latestSeason = Math.max(...seasonsRes.seasons);
            const shotsRes = await fetchPlayerShots(favorite.player_name, latestSeason).catch(() => null);
            if (!active) return;
            const makes = (shotsRes?.shots || []).filter((s) => s.shot_made_flag && s.loc_x != null && s.loc_y != null);
            if (makes.length === 0) return;
            shotsRef.current = makes.map((s) => ({ ...s, playerName: favorite.player_name }));
            setReady(true);
        })();
        return () => { active = false; };
    }, []);

    useEffect(() => {
        if (!ready || REDUCED_MOTION) return undefined;
        const canvas = canvasRef.current;
        if (!canvas) return undefined;
        const ctx = canvas.getContext('2d');
        let raf = null;
        let idx = 0;
        let start = null;
        let W = 0;
        let H = 0;

        function resize() {
            const rect = canvas.parentElement.getBoundingClientRect();
            W = Math.max(1, Math.round(rect.width));
            H = Math.max(1, Math.round(rect.height));
            const dpr = Math.min(window.devicePixelRatio || 1, 2);
            canvas.width = W * dpr;
            canvas.height = H * dpr;
            canvas.style.width = `${W}px`;
            canvas.style.height = `${H}px`;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        }
        resize();
        const ro = new ResizeObserver(resize);
        ro.observe(canvas.parentElement);

        const rim = { x: W * 0.5, y: H * 0.92 };

        function playNext(now) {
            const shot = shotsRef.current[idx % shotsRef.current.length];
            idx += 1;
            start = now;
            const sx = ((shot.loc_x + 250) / 500) * W;
            const sy = H * 0.15 + (1 - Math.min(1, Math.max(0, shot.shot_distance ?? 15) / 40)) * H * 0.6;
            setCaption(`${shot.playerName} · ${shot.shot_distance != null ? `${Math.round(shot.shot_distance)} ft ` : ''}${shot.shot_type || 'shot'}`);
            return { sx, sy };
        }

        let current = playNext(performance.now());

        function frame(now) {
            const elapsed = now - start;
            ctx.clearRect(0, 0, W, H);
            if (elapsed < ARC_MS) {
                const t = elapsed / ARC_MS;
                const x = current.sx + (rim.x - current.sx) * t;
                const arcLift = Math.sin(t * Math.PI) * -40;
                const y = current.sy + (rim.y - current.sy) * t + arcLift;
                ctx.globalAlpha = 1 - t * 0.3;
                ctx.fillStyle = '#ff6b1a';
                ctx.beginPath();
                ctx.arc(x, y, 4, 0, Math.PI * 2);
                ctx.fill();
            } else {
                current = playNext(now);
            }
            raf = requestAnimationFrame(frame);
        }
        raf = requestAnimationFrame(frame);

        return () => {
            if (raf) cancelAnimationFrame(raf);
            ro.disconnect();
        };
    }, [ready]);

    if (REDUCED_MOTION || !ready) return null;

    return (
        <div className="live-shot-hero" aria-hidden="true">
            <canvas ref={canvasRef} />
            {caption && <p className="live-shot-caption">{caption}</p>}
        </div>
    );
}
