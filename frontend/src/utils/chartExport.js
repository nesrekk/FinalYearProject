// Turns an inline SVG chart into a standalone SVG file or a 2x PNG. This
// app's charts color themselves two ways — CSS custom properties on
// attributes (fill="var(--surface-2)") and plain CSS classes (.rx-grid,
// .smk-bar, ...) — neither of which means anything in a file opened outside
// the app: there's no :root and no <link>/<style> for the app's stylesheet.
// So every element's actually-rendered paint/stroke is read with
// getComputedStyle (which already resolves the full cascade — vars, classes,
// inheritance, all of it) and written back as an explicit attribute,
// walking the real (attached) element tree in parallel with the clone since
// getComputedStyle needs a rendered node.

const STYLE_PROPS = [
    'fill', 'stroke', 'stop-color', 'flood-color', 'lighting-color', 'color',
    'opacity', 'fill-opacity', 'stroke-opacity', 'stroke-width', 'stroke-dasharray', 'stroke-linejoin', 'stroke-linecap',
    'font-family', 'font-size', 'font-weight', 'text-anchor', 'letter-spacing',
];

function inlineComputedStyle(liveRoot, cloneRoot) {
    const liveEls = [liveRoot, ...liveRoot.querySelectorAll('*')];
    const cloneEls = [cloneRoot, ...cloneRoot.querySelectorAll('*')];
    liveEls.forEach((liveEl, i) => {
        const cloneEl = cloneEls[i];
        if (!cloneEl || cloneEl.nodeType !== 1) return;
        const cs = getComputedStyle(liveEl);
        STYLE_PROPS.forEach((prop) => {
            const resolved = cs.getPropertyValue(prop);
            if (resolved) {
                cloneEl.setAttribute(prop, resolved);
                if (cloneEl.style) cloneEl.style.removeProperty(prop);
            }
        });
        // Classes drove the now-inlined presentation; keep them out of the
        // standalone file's markup since there's no stylesheet to match them.
        cloneEl.removeAttribute('class');
    });
}

function serializeSvg(svgEl) {
    const rect = svgEl.getBoundingClientRect();
    const clone = svgEl.cloneNode(true);
    inlineComputedStyle(svgEl, clone);
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    clone.setAttribute('width', Math.max(1, Math.round(rect.width)));
    clone.setAttribute('height', Math.max(1, Math.round(rect.height)));
    clone.style.fontFamily = getComputedStyle(svgEl).fontFamily;
    return { markup: new XMLSerializer().serializeToString(clone), width: rect.width || 1, height: rect.height || 1 };
}

function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function downloadChartSvg(svgEl, filename) {
    const { markup } = serializeSvg(svgEl);
    const blob = new Blob([`<?xml version="1.0" encoding="UTF-8"?>\n${markup}`], { type: 'image/svg+xml;charset=utf-8' });
    triggerDownload(blob, filename);
}

// Renders at `scale`x the chart's on-screen size (default 2, i.e. retina),
// on a canvas pre-filled with the current theme's page background so the
// PNG isn't transparent where the chart itself draws no background rect.
export function downloadChartPng(svgEl, filename, scale = 2) {
    const { markup, width, height } = serializeSvg(svgEl);
    const svgBlob = new Blob([markup], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(svgBlob);
    const img = new Image();
    return new Promise((resolve, reject) => {
        img.onload = () => {
            try {
                const canvas = document.createElement('canvas');
                canvas.width = Math.max(1, Math.round(width * scale));
                canvas.height = Math.max(1, Math.round(height * scale));
                const ctx = canvas.getContext('2d');
                const bg = getComputedStyle(document.documentElement).getPropertyValue('--surface').trim();
                ctx.fillStyle = bg || '#ffffff';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                canvas.toBlob((blob) => {
                    URL.revokeObjectURL(url);
                    if (blob) triggerDownload(blob, filename);
                    resolve();
                }, 'image/png');
            } catch (e) {
                URL.revokeObjectURL(url);
                reject(e);
            }
        };
        img.onerror = (e) => { URL.revokeObjectURL(url); reject(e); };
        img.src = url;
    });
}
