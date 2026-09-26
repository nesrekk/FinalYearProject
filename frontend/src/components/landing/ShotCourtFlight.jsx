import React, { useEffect, useRef } from 'react';
import {
    BufferAttribute, BufferGeometry, Color, Fog, Line, LineBasicMaterial,
    PerspectiveCamera, Points, PointsMaterial, Scene, Vector3, WebGLRenderer,
} from 'three';

const ORANGE = 0xff5b14;
const INK = new Color(0x0d0d0d);
const CREAM = new Color(0xf6f0e4);

// Court coordinates follow player_shots: tenths of a foot, hoop at (0, 0),
// baseline at y = -47.5, half-court line at y = 422.5. World z = -y.
const KEYS = [
    { pos: [0, 250, -470], look: [0, 0, -20] },
    { pos: [0, 600, -40], look: [0, 0, -175] },
    { pos: [-140, 60, 110], look: [0, 0, -30] },
];

function courtSegments() {
    const segs = [];
    const line = (pts) => segs.push(pts);
    const arc = (cx, cy, rad, a0, a1, n = 64) => {
        const pts = [];
        for (let i = 0; i <= n; i++) {
            const a = a0 + ((a1 - a0) * i) / n;
            pts.push([cx + rad * Math.cos(a), cy + rad * Math.sin(a)]);
        }
        line(pts);
    };
    line([[-250, -47.5], [250, -47.5], [250, 422.5], [-250, 422.5], [-250, -47.5]]);
    line([[-80, -47.5], [-80, 142.5], [80, 142.5], [80, -47.5]]);
    line([[-60, -47.5], [-60, 142.5]]);
    line([[60, -47.5], [60, 142.5]]);
    arc(0, 142.5, 60, 0, Math.PI);
    arc(0, 0, 40, 0, Math.PI);
    arc(0, 0, 7.5, 0, Math.PI * 2, 32);
    line([[-30, -7.5], [30, -7.5]]);
    line([[-220, -47.5], [-220, 92.5]]);
    line([[220, -47.5], [220, 92.5]]);
    const a3 = Math.acos(220 / 237.5);
    arc(0, 0, 237.5, a3, Math.PI - a3, 96);
    arc(0, 422.5, 60, Math.PI, Math.PI * 2);
    return segs;
}

const lerp = (a, b, t) => a + (b - a) * t;
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export default function ShotCourtFlight({ points, sectionRef }) {
    const canvasRef = useRef(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        const section = sectionRef.current;
        if (!canvas || !section || !points?.length) return undefined;

        let renderer;
        try {
            renderer = new WebGLRenderer({ canvas, antialias: true });
        } catch {
            return undefined;
        }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setClearColor(ORANGE, 1);

        const scene = new Scene();
        scene.fog = new Fog(ORANGE, 500, 1300);
        const camera = new PerspectiveCamera(42, 1, 1, 4000);

        const n = points.length;
        const pos = new Float32Array(n * 3);
        const col = new Float32Array(n * 3);
        points.forEach(([x, y, made], i) => {
            pos[i * 3] = x;
            pos[i * 3 + 1] = made ? 1.5 + ((i * 7919) % 97) / 14 : 0.6;
            pos[i * 3 + 2] = -y;
            const c = made ? INK : CREAM;
            col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
        });
        const geo = new BufferGeometry();
        geo.setAttribute('position', new BufferAttribute(pos, 3));
        geo.setAttribute('color', new BufferAttribute(col, 3));
        const mat = new PointsMaterial({ size: 3.4, vertexColors: true });
        scene.add(new Points(geo, mat));

        const lineMat = new LineBasicMaterial({ color: 0x0d0d0d });
        const lineGeos = courtSegments().map((pts) => {
            const g = new BufferGeometry().setFromPoints(pts.map(([x, y]) => new Vector3(x, 0, -y)));
            scene.add(new Line(g, lineMat));
            return g;
        });

        const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const camPos = new Vector3();
        const camLook = new Vector3();
        let progress = 0, shown = 0, mx = 0, my = 0, raf = 0, visible = true;

        function resize() {
            const w = canvas.clientWidth, h = canvas.clientHeight;
            renderer.setSize(w, h, false);
            camera.aspect = w / Math.max(h, 1);
            // Portrait screens pin the chapter card to the bottom, so lift the court into the top half.
            if (camera.aspect < 1) camera.setViewOffset(w, h, 0, h * 0.22, w, h);
            else camera.clearViewOffset();
            camera.updateProjectionMatrix();
        }
        function readProgress() {
            const rect = section.getBoundingClientRect();
            const travel = rect.height - window.innerHeight;
            progress = travel > 0 ? Math.min(Math.max(-rect.top / travel, 0), 1) : 0;
        }
        function frame() {
            readProgress();
            shown = reduced ? 0 : shown + (progress - shown) * 0.12;
            const p = shown * (KEYS.length - 1);
            const i = Math.min(Math.floor(p), KEYS.length - 2);
            const f = ease(p - i);
            const a = KEYS[i], b = KEYS[i + 1];
            camPos.set(
                lerp(a.pos[0], b.pos[0], f) + mx * 40,
                lerp(a.pos[1], b.pos[1], f) - my * 25,
                lerp(a.pos[2], b.pos[2], f),
            );
            camLook.set(lerp(a.look[0], b.look[0], f), lerp(a.look[1], b.look[1], f), lerp(a.look[2], b.look[2], f));
            camera.position.copy(camPos);
            camera.lookAt(camLook);
            renderer.render(scene, camera);
            if (visible) raf = requestAnimationFrame(frame);
        }
        function onMove(e) {
            mx = e.clientX / window.innerWidth - 0.5;
            my = e.clientY / window.innerHeight - 0.5;
        }

        const ro = new ResizeObserver(resize);
        ro.observe(canvas);
        const io = new IntersectionObserver(([entry]) => {
            const was = visible;
            visible = entry.isIntersecting;
            if (visible && !was) raf = requestAnimationFrame(frame);
        });
        io.observe(canvas);
        if (!reduced) window.addEventListener('pointermove', onMove, { passive: true });
        resize();
        raf = requestAnimationFrame(frame);

        return () => {
            cancelAnimationFrame(raf);
            ro.disconnect();
            io.disconnect();
            window.removeEventListener('pointermove', onMove);
            geo.dispose();
            mat.dispose();
            lineMat.dispose();
            lineGeos.forEach((g) => g.dispose());
            renderer.dispose();
        };
    }, [points, sectionRef]);

    return <canvas ref={canvasRef} className="lp-flight-canvas" aria-hidden="true" />;
}
