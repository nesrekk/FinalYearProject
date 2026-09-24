import React, { useEffect, useRef, useState } from 'react';
import { getPlayerHeadshotUrl, TEAM_COLORS } from '../../utils/teamAssets';

const REDUCED_MOTION = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;

function lerp(a, b, t) {
    return a + (b - a) * t;
}
// Piecewise-linear interpolation through `stops` ([x0,x1,...], [y0,y1,...]) —
// framer-motion's useTransform does this, but framer's useScroll(target)
// never re-fired here (its IntersectionObserver-based scroll tracking
// didn't update position on scroll in this layout), so this is driven by a
// plain scroll listener instead, consistent with the rest of the landing
// page's hand-rolled scroll/rAF code (ParticleField, FeatureChapters).
function interp(t, xs, ys) {
    if (t <= xs[0]) return ys[0];
    if (t >= xs[xs.length - 1]) return ys[ys.length - 1];
    for (let i = 0; i < xs.length - 1; i++) {
        if (t >= xs[i] && t <= xs[i + 1]) {
            const local = (t - xs[i]) / (xs[i + 1] - xs[i]);
            return lerp(ys[i], ys[i + 1], local);
        }
    }
    return ys[ys.length - 1];
}

// A glassy 3D trading card for the real MVP favorite (from the awards
// chapter's own fetch — no separate lookup). It travels with scroll across
// chapters 1–3 (the DOM range `rangeRef` spans) and tilts toward the mouse
// on top of that. CSS3D only (perspective + preserve-3d), no three.js.
// Hidden under 768px and static (no scroll travel, no mouse tilt) under
// reduced motion.
export default function FloatingPlayerCard({ rangeRef, player }) {
    const cardRef = useRef(null);
    const [tilt, setTilt] = useState({ rx: 0, ry: 0 });
    const [motionStyle, setMotionStyle] = useState(null);

    useEffect(() => {
        if (REDUCED_MOTION) return undefined;
        let raf = null;

        function computeProgress() {
            const el = rangeRef.current;
            if (!el) return 0;
            const rect = el.getBoundingClientRect();
            const total = rect.height - window.innerHeight;
            if (total <= 0) return rect.top <= 0 ? 1 : 0;
            return Math.min(1, Math.max(0, -rect.top / total));
        }

        function update() {
            const t = computeProgress();
            setMotionStyle({
                x: interp(t, [0, 0.5, 1], [28, -28, 0]),
                y: interp(t, [0, 0.5, 1], [-8, 4, -4]),
                rotateY: interp(t, [0, 0.5, 1], [-20, 20, -8]),
                rotateX: interp(t, [0, 1], [6, -6]),
                opacity: interp(t, [0, 0.05, 0.95, 1], [0, 1, 1, 0]),
            });
            raf = null;
        }

        function onScroll() {
            if (raf == null) raf = requestAnimationFrame(update);
        }

        update();
        window.addEventListener('scroll', onScroll, { passive: true });
        window.addEventListener('resize', onScroll);
        return () => {
            if (raf) cancelAnimationFrame(raf);
            window.removeEventListener('scroll', onScroll);
            window.removeEventListener('resize', onScroll);
        };
    }, [rangeRef]);

    function onMouseMove(e) {
        if (REDUCED_MOTION) return;
        const rect = cardRef.current?.getBoundingClientRect();
        if (!rect) return;
        const px = (e.clientX - rect.left) / rect.width - 0.5;
        const py = (e.clientY - rect.top) / rect.height - 0.5;
        setTilt({ rx: py * -8, ry: px * 8 });
    }
    function onMouseLeave() {
        setTilt({ rx: 0, ry: 0 });
    }

    if (!player) return null;

    const teamColor = TEAM_COLORS[player.team_abbreviation] || '#2997ff';
    const stats = [
        { label: 'PTS', value: player.pts != null ? player.pts.toFixed(1) : '—' },
        { label: 'TS%', value: player.ts_pct != null ? `${Math.round(player.ts_pct * 100)}%` : '—' },
        { label: 'W%', value: player.w_pct != null ? `${Math.round(player.w_pct * 100)}%` : '—' },
    ];

    const style = REDUCED_MOTION || !motionStyle
        ? { '--team-color': teamColor, opacity: REDUCED_MOTION ? 1 : 0 }
        : {
            '--team-color': teamColor,
            opacity: motionStyle.opacity,
            transform: `translate(${motionStyle.x}vw, ${motionStyle.y}vh) rotateY(${motionStyle.rotateY + tilt.ry}deg) rotateX(${motionStyle.rotateX + tilt.rx}deg)`,
        };

    return (
        <div className="floating-card-layer" aria-hidden="true">
            <div
                ref={cardRef}
                className="floating-card"
                style={style}
                onMouseMove={onMouseMove}
                onMouseLeave={onMouseLeave}
            >
                <div className="floating-card-sheen" />
                <img
                    className="floating-card-headshot"
                    src={getPlayerHeadshotUrl(player.player_id)}
                    alt=""
                    loading="lazy"
                    onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }}
                />
                <p className="floating-card-name">{player.player_name}</p>
                <p className="floating-card-team">{player.team_abbreviation} &middot; MVP favorite</p>
                <div className="floating-card-stats">
                    {stats.map((s) => (
                        <div key={s.label}>
                            <span className="floating-card-stat-value">{s.value}</span>
                            <span className="floating-card-stat-label">{s.label}</span>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
