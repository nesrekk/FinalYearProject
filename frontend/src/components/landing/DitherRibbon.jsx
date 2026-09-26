import React, { useEffect, useRef } from 'react';

const VERT = 'attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';

// A flowing ribbon that ripples around the cursor, drawn with 4x4 ordered
// dithering in the landing palette (orange ground, ink ribbon, cream highlights).
const FRAG = `precision highp float;
uniform vec2 r;uniform float t;uniform vec2 m;uniform float cell;
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
 return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p){float s=0.,a=.5;for(int i=0;i<4;i++){s+=a*noise(p);p*=2.03;a*=.5;}return s;}
float bayer2(vec2 a){a=floor(a);return fract(a.x/2.+a.y*a.y*.75);}
float bayer4(vec2 a){return bayer2(.5*a)*.25+bayer2(a);}
void main(){
 vec2 px=floor(gl_FragCoord.xy/cell)*cell;
 vec2 uv=(px-.5*r)/r.y;
 vec2 mm=(m-.5)*vec2(r.x/r.y,1.);
 float band=uv.y-.16+.24*sin(uv.x*1.5+t*.22)+.1*sin(uv.x*3.2-t*.35);
 float d=distance(uv,mm);
 band+=.22*exp(-d*d*7.)*sin(d*20.-t*2.4);
 float w=fbm(uv*1.3+vec2(t*.04,band*.6));
 float dist=abs(band-.02+.14*w);
 float body=smoothstep(.36,.02,dist);
 float spec=pow(max(0.,1.-dist*7.),5.);
 float th=bayer4(gl_FragCoord.xy/cell);
 vec3 orange=vec3(1.,.357,.078);
 vec3 ink=vec3(.051);
 vec3 cream=vec3(.965,.941,.894);
 vec3 col=orange;
 if(body*.85>th)col=ink;
 if(spec*.9>th+.25)col=cream;
 gl_FragColor=vec4(col,1.);
}`;

function compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
}

export default function DitherRibbon({ className = '' }) {
    const canvasRef = useRef(null);

    useEffect(() => {
        const canvas = canvasRef.current;
        const gl = canvas?.getContext('webgl', { antialias: false, premultipliedAlpha: false });
        if (!gl) return undefined;
        const vs = compile(gl, gl.VERTEX_SHADER, VERT);
        const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
        if (!vs || !fs) return undefined;
        const prog = gl.createProgram();
        gl.attachShader(prog, vs);
        gl.attachShader(prog, fs);
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return undefined;
        gl.useProgram(prog);

        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
        const loc = gl.getAttribLocation(prog, 'p');
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
        const uR = gl.getUniformLocation(prog, 'r');
        const uT = gl.getUniformLocation(prog, 't');
        const uM = gl.getUniformLocation(prog, 'm');
        const uCell = gl.getUniformLocation(prog, 'cell');

        const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        let raf = 0;
        let visible = true;
        let mx = 0.72, my = 0.45, tx = mx, ty = my;

        function resize() {
            const w = canvas.clientWidth, h = canvas.clientHeight;
            canvas.width = Math.max(1, Math.round(w * dpr));
            canvas.height = Math.max(1, Math.round(h * dpr));
            gl.viewport(0, 0, canvas.width, canvas.height);
        }
        function draw(ms) {
            mx += (tx - mx) * 0.07;
            my += (ty - my) * 0.07;
            gl.uniform2f(uR, canvas.width, canvas.height);
            gl.uniform1f(uT, reduced ? 4 : ms / 1000);
            gl.uniform2f(uM, mx, my);
            gl.uniform1f(uCell, 3 * dpr);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        }
        function loop(ms) {
            draw(ms);
            if (visible && !reduced) raf = requestAnimationFrame(loop);
        }
        function onMove(e) {
            const rect = canvas.getBoundingClientRect();
            tx = (e.clientX - rect.left) / rect.width;
            ty = 1 - (e.clientY - rect.top) / rect.height;
            if (reduced) { mx = tx; my = ty; draw(4000); }
        }

        const ro = new ResizeObserver(() => { resize(); draw(performance.now()); });
        ro.observe(canvas);
        const io = new IntersectionObserver(([entry]) => {
            const was = visible;
            visible = entry.isIntersecting;
            if (visible && !was && !reduced) raf = requestAnimationFrame(loop);
        });
        io.observe(canvas);
        window.addEventListener('pointermove', onMove, { passive: true });
        resize();
        raf = requestAnimationFrame(loop);

        return () => {
            cancelAnimationFrame(raf);
            ro.disconnect();
            io.disconnect();
            window.removeEventListener('pointermove', onMove);
            gl.deleteBuffer(buf);
            gl.deleteProgram(prog);
            gl.deleteShader(vs);
            gl.deleteShader(fs);
        };
    }, []);

    return <canvas ref={canvasRef} className={className} aria-hidden="true" />;
}
