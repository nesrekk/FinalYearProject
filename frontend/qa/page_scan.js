// Page sweep scanner (round 8 step 2). Run through the browser tool, not shipped with the app.
//
// It drives the app in same-origin iframes, one page at a time, at a given width and theme, waits until
// the page has settled (no request in flight and no "Loading" text, or a cap), then scans the rendered
// page for: console errors, failed or slow (> 3 s) requests, horizontal overflow, text below the WCAG
// contrast floor (4.5:1, 3:1 for large text), chart marks (SVG dots, lines, bars) under 3:1 (`marks`, step 2c),
// "undefined"/"NaN"/"null"/"[object Object]" in visible text, loading text that never went away, empty
// tables, and internal links that can't open a real view.
//
// It needs a page that loads the app with request/console recording. Round 8 used a temporary copy of
// index.html (`zz-harness.html`, deleted before committing) whose inline script, before the app loads:
//   - replaces requestAnimationFrame with setTimeout (a hidden Browser pane never fires rAF, so
//     framer-motion page transitions would never finish), and turns CSS transitions/animations off;
//   - loads the app through a module script that first sets framer-motion's
//     `MotionGlobalConfig.skipAnimations = true`: its opacity fades run on the Web Animations API, whose
//     clock doesn't advance in a hidden pane either, so pages otherwise stay at opacity 0 (`invisible`
//     counts text that is, and is the sign this happened);
//   - copies `?zztheme=light|dark` into localStorage 'nba-hub-theme';
//   - wraps XMLHttpRequest and fetch into window.__zz = { reqs: [{method,url,status,ms}], pending },
//     and console.error / window errors / unhandled rejections into window.__zz.errors;
//   - with `?zzdriver`, doesn't load the app at all (the driver page).
//
// Usage from the browser tool, on `/zz-harness.html?zzdriver`:
//   const qa = await import('/qa/page_scan.js');
//   const r = await qa.sweep([{ page: 'rapm' }, { page: 'player', params: { id: 2544 } }],
//                            { widths: [1280, 375], themes: ['light', 'dark'] });
//   qa.summarise(r)   // a short text report; r has every detail
// One browser-tool call gets ~45 s, so start a long sweep without awaiting it and read window.__zzResults
// in later calls. Editing this file while the driver page is open makes Vite reload it (results lost).
// Also: linkRoundTrip() (change a <select>, open the URL fresh, compare), followLinks() (open one link of
// each kind), followButtons() (click navigation-looking buttons), checkAfter() (act on a page, e.g. click
// "Predict MVP", then scan the filled-in view; helpers typeInto() and clickText()).
// Step 2c's harness also replaces IntersectionObserver with a stub that reports every observed element as on
// screen (a hidden pane never fires the real one), so `whileInView` sections and the Workbench's lazy blocks
// render; without it they stay at opacity 0 / unloaded. Blind spots: a segmented control's active pill is a sibling layer (its label reads as a contrast failure), and a chart
// panel drawn inside a chart counts only when it is a <rect> covering at least half the <svg>.

// "the null" / "a null result" / "null centre" is statistics wording (Coaching Decisions' footnotes, Methodology),
// not a leaked value.
const BAD_TEXT = /\b(undefined|NaN)\b|(?<!\b(?:[Tt]he|[Aa]|[Ii]ts|shuffled|permutation) )\bnull\b(?! (?:centre|center|result|hypothesis|distribution|model))|\[object Object\]|\bInfinity\b/;
const LOADING_TEXT = /\bLoading\b|\bloading…|\bLoading…/;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseColor(str) {
  // color-mix() computes to `color(srgb r g b / a)` with 0-1 channels (step 2b: the Simulator's seed cells
  // were read as transparent before this).
  const s = str && str.match(/color\(srgb ([^)]+)\)/);
  if (s) {
    const p = s[1].split(/[\s/]+/).filter(Boolean).map(Number);
    return { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255, a: p.length > 3 ? p[3] : 1 };
  }
  const m = str && str.match(/rgba?\(([^)]+)\)/);
  if (!m) return null;
  const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
  return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
}

function blend(top, bottom) {
  const a = top.a + bottom.a * (1 - top.a);
  if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
  return {
    r: (top.r * top.a + bottom.r * bottom.a * (1 - top.a)) / a,
    g: (top.g * top.a + bottom.g * bottom.a * (1 - top.a)) / a,
    b: (top.b * top.a + bottom.b * bottom.a * (1 - top.a)) / a,
    a,
  };
}

function luminance(c) {
  const f = (v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
}

export function contrast(a, b) {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// Background behind an element: composite every ancestor's background-color. Returns null when an
// ancestor paints an image or gradient (contrast unknown; reported separately, not as a failure).
function backgroundOf(el, win) {
  const layers = [];
  for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
    const cs = win.getComputedStyle(n);
    if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
    const c = parseColor(cs.backgroundColor);
    if (c && c.a > 0) {
      layers.push(c);
      if (c.a >= 1) break;
    }
  }
  let bg = { r: 255, g: 255, b: 255, a: 1 };
  for (let i = layers.length - 1; i >= 0; i--) bg = blend(layers[i], bg);
  return bg;
}

// A chart's own panel: <rect>s covering at least half the <svg> (e.g. fill var(--surface-2)), composited over
// the page behind the <svg>. Used for SVG text and chart marks.
const backdropCache = new WeakMap();
function svgBackdrop(svg, win, pageBg) {
  if (backdropCache.has(svg)) return backdropCache.get(svg);
  const sr = svg.getBoundingClientRect();
  let bg = pageBg;
  for (const rect of svg.querySelectorAll('rect')) {
    const r = rect.getBoundingClientRect();
    if (r.width * r.height < 0.5 * sr.width * sr.height) continue;
    const cs = win.getComputedStyle(rect);
    const c = parseColor(cs.fill);
    if (!c) continue;
    const a = c.a * Number(cs.fillOpacity || 1) * Number(cs.opacity || 1);
    if (a > 0) bg = blend({ ...c, a }, bg);
  }
  backdropCache.set(svg, bg);
  return bg;
}

function opacityOf(el, win) {
  let o = 1;
  for (let n = el; n && n.nodeType === 1; n = n.parentElement) o *= Number(win.getComputedStyle(n).opacity || 1);
  return o;
}

function visible(el, win) {
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return false;
  const cs = win.getComputedStyle(el);
  if (cs.visibility === 'hidden' || cs.display === 'none') return false;
  if (el.closest('[aria-hidden="true"], .sr-only, .visually-hidden')) return false;
  // clipped-to-nothing helpers
  if (cs.clip === 'rect(0px, 0px, 0px, 0px)' || cs.clipPath === 'inset(50%)') return false;
  return true;
}

function describe(el) {
  let s = el.tagName.toLowerCase();
  if (el.id) s += '#' + el.id;
  const cls = (typeof el.className === 'string' ? el.className : el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3);
  if (cls.length) s += '.' + cls.join('.');
  const p = el.parentElement;
  if (p) {
    const pc = (typeof p.className === 'string' ? p.className : p.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean)[0];
    s = (pc ? p.tagName.toLowerCase() + '.' + pc : p.tagName.toLowerCase()) + ' > ' + s;
  }
  return s;
}

function directText(el) {
  let t = '';
  for (const n of el.childNodes) if (n.nodeType === 3) t += n.nodeValue;
  return t.replace(/\s+/g, ' ').trim();
}

// Scan one rendered document.
export function scan(doc, win, opts = {}) {
  const out = { overflow: null, wide: [], contrast: [], marks: [], unknownBg: 0, invisible: 0, badText: [], loading: [], emptyTables: 0, links: {}, badLinks: [] };
  const vw = doc.documentElement.clientWidth;
  out.overflow = doc.documentElement.scrollWidth > vw + 1 ? doc.documentElement.scrollWidth - vw : 0;

  // Elements sticking out past the viewport that aren't inside a scroll/clip container.
  if (out.overflow) {
    const seen = new Set();
    for (const el of doc.body.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.right <= vw + 1 || r.width < 1) continue;
      let clipped = false;
      for (let n = el.parentElement; n && n !== doc.body; n = n.parentElement) {
        const ox = win.getComputedStyle(n).overflowX;
        if (ox !== 'visible') { clipped = true; break; }
      }
      if (clipped) continue;
      // only report the outermost offender
      if ([...seen].some((s) => s.contains(el))) continue;
      seen.add(el);
      out.wide.push({ el: describe(el), right: Math.round(r.right), width: Math.round(r.width) });
      if (out.wide.length > 8) break;
    }
  }

  // Text contrast and bad text.
  const contrastSeen = new Map();
  for (const el of doc.body.querySelectorAll('*')) {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'OPTION'].includes(el.tagName)) continue;
    const text = directText(el);
    if (!text) continue;
    if (!visible(el, win)) continue;
    const bad = text.match(BAD_TEXT);
    if (bad) out.badText.push({ el: describe(el), text: text.slice(Math.max(0, bad.index - 50), bad.index + 60) });
    if (LOADING_TEXT.test(text)) out.loading.push({ el: describe(el), text: text.slice(0, 80) });
    if (el.closest('[disabled], [aria-disabled="true"]')) continue;
    const cs = win.getComputedStyle(el);
    const isSvg = el instanceof win.SVGElement;
    // Outlined text (-webkit-text-stroke, the landing's title) reads by its stroke.
    const stroked = parseFloat(cs.webkitTextStrokeWidth) >= 1 && parseColor(cs.webkitTextStrokeColor)?.a > 0.5;
    let fg = parseColor(stroked ? cs.webkitTextStrokeColor : isSvg ? cs.fill : cs.color);
    if (!fg) continue;
    const op = opacityOf(el, win);
    if (op < 0.05) { out.invisible++; continue; } // faded out (or a fade-in that never finished: see invisible)
    let bg = backgroundOf(el, win);
    if (!bg) { out.unknownBg++; continue; }
    if (isSvg && el.ownerSVGElement) bg = svgBackdrop(el.ownerSVGElement, win, bg);
    fg = { ...fg, a: fg.a * op * (isSvg ? Number(cs.fillOpacity || 1) : 1) };
    const ratio = contrast(blend(fg, bg), bg);
    const size = parseFloat(cs.fontSize);
    const bold = Number(cs.fontWeight) >= 700;
    const large = size >= 24 || (bold && size >= 18.66);
    const floor = large ? 3 : 4.5;
    if (ratio + 1e-6 < floor) {
      const key = describe(el) + '|' + cs.color;
      const prev = contrastSeen.get(key);
      if (prev) { prev.count++; continue; }
      const rec = { el: describe(el), text: text.slice(0, 50), ratio: Math.round(ratio * 100) / 100, floor, size, fg: isSvg ? cs.fill : cs.color, bg: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`, count: 1 };
      contrastSeen.set(key, rec);
      out.contrast.push(rec);
    }
  }

  // Chart marks (step 2c): SVG dots, lines, bars and outlines should be >= 3:1 against what's behind the chart
  // (WCAG 1.4.11). An element passes if any of its paints (stroke or fill) reads: a hollow dot is its stroke.
  // Skipped: layers under 40% opacity (faded on purpose: gridlines, bands, background population), paints
  // the colour of the background (knockouts, separators), heat-map cells (20+ same-size <rect>s: a colour
  // scale read against its legend), shapes as big as the chart, anything without a visible box.
  const markSeen = new Map();
  const cellSizes = new WeakMap();
  const isHeatCell = (el, svg) => {
    if (el.tagName !== 'rect') return false;
    let sizes = cellSizes.get(svg);
    if (!sizes) {
      sizes = new Map();
      for (const r of svg.querySelectorAll('rect')) { const k = r.getAttribute('width') + 'x' + r.getAttribute('height'); sizes.set(k, (sizes.get(k) || 0) + 1); }
      cellSizes.set(svg, sizes);
    }
    return (sizes.get(el.getAttribute('width') + 'x' + el.getAttribute('height')) || 0) >= 20;
  };
  for (const el of doc.body.querySelectorAll('svg circle, svg path, svg line, svg rect, svg polyline, svg polygon, svg ellipse')) {
    if (!visible(el, win) || el.closest('defs, clipPath, mask, pattern, marker')) continue;
    const svg = el.ownerSVGElement;
    if (!svg) continue;
    const r = el.getBoundingClientRect(), sr = svg.getBoundingClientRect();
    if (r.width * r.height > 0.5 * sr.width * sr.height) continue;
    if (isHeatCell(el, svg)) continue;
    const cs = win.getComputedStyle(el);
    const op = opacityOf(el, win);
    const strokeW = parseFloat(cs.strokeWidth) || 0;
    const paints = [];
    if (cs.stroke && cs.stroke !== 'none' && strokeW >= 1) paints.push([cs.stroke, Number(cs.strokeOpacity || 1)]);
    if (cs.fill && cs.fill !== 'none' && !(el.tagName === 'line' || el.tagName === 'polyline')) paints.push([cs.fill, Number(cs.fillOpacity || 1)]);
    const pageBg = backgroundOf(svg, win);
    if (!pageBg) continue;
    const bg = svgBackdrop(svg, win, pageBg);
    let best = null;
    for (const [paint, po] of paints) {
      const c = parseColor(paint);
      if (!c) continue;
      const a = c.a * po * op;
      if (a < 0.4) continue;
      const ratio = contrast(blend({ ...c, a }, bg), bg);
      if (ratio < 1.1) continue;
      if (!best || ratio > best.ratio) best = { paint, a, ratio };
    }
    if (!best || best.ratio + 1e-6 >= 3) continue;
    const key = describe(svg) + '|' + best.paint + '|' + Math.round(best.a * 100);
    const prev = markSeen.get(key);
    if (prev) { prev.count++; continue; }
    const rec = { svg: describe(svg), el: el.tagName, paint: best.paint, alpha: Math.round(best.a * 100) / 100, ratio: Math.round(best.ratio * 100) / 100, bg: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`, count: 1 };
    markSeen.set(key, rec);
    out.marks.push(rec);
  }
  if (out.marks.length > 12) out.marks = out.marks.slice(0, 12);

  // Tables with a header and no rows.
  for (const t of doc.querySelectorAll('table')) {
    if (!visible(t, win)) continue;
    const rows = t.querySelectorAll('tbody tr');
    if (t.querySelector('thead') && rows.length === 0) out.emptyTables++;
  }

  // Internal links: count by target page, flag ones that can't open a real view.
  for (const a of doc.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    if (!href || /^(https?:|mailto:|blob:|data:)/.test(href) && !href.includes(win.location.host)) continue;
    let u;
    try { u = new URL(href, win.location.href); } catch { out.badLinks.push({ href, why: 'unparseable' }); continue; }
    const page = u.searchParams.get('page');
    if (!page) continue;
    out.links[page] = (out.links[page] || 0) + 1;
    for (const [k, v] of u.searchParams) {
      if (/^(undefined|null|NaN|)$/.test(v) && !['q', 'tab'].includes(k) && !(opts.emptyOk || []).includes(k)) {
        out.badLinks.push({ href: href.slice(0, 160), why: `${k}=${v || '(empty)'}` });
        break;
      }
    }
    if (page === 'player' && !(Number(u.searchParams.get('id')) > 0)) out.badLinks.push({ href: href.slice(0, 160), why: 'player id' });
  }
  if (out.badLinks.length > 10) out.badLinks = out.badLinks.slice(0, 10);
  if (out.badText.length > 10) out.badText = out.badText.slice(0, 10);
  return out;
}

function harnessUrl(base, { page, params = {}, hash = '' }, theme) {
  const u = new URL(base, location.href);
  if (page) { u.search = ''; u.searchParams.set('page', page); } // page null: open `base` exactly (a copied link)
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  u.searchParams.set('zztheme', theme);
  return u.pathname + u.search + (hash ? '#' + hash : u.hash);
}

// Load one page in an iframe and wait for it to settle.
export async function load(target, { width = 1280, height = 900, theme = 'light', base = '/zz-harness.html', cap = 25000, quiet = 1200 } = {}) {
  const frame = document.createElement('iframe');
  frame.style.cssText = `width:${width}px;height:${height}px;border:0;display:block`;
  frame.src = harnessUrl(base, target, theme);
  document.body.innerHTML = '';
  document.body.appendChild(frame);
  const t0 = performance.now();
  await Promise.race([new Promise((r) => frame.addEventListener('load', r, { once: true })), sleep(cap)]);
  let quietSince = null;
  for (;;) {
    await sleep(150);
    const w = frame.contentWindow;
    if (!w) break; // the frame was removed (another sweep started)
    const zz = w.__zz;
    const elapsed = performance.now() - t0;
    if (elapsed > cap) break;
    if (!zz || !w.document.querySelector('#root > *')) { quietSince = null; continue; }
    const loadingText = LOADING_TEXT.test(w.document.body.innerText);
    if (zz.pending > 0 || loadingText) { quietSince = null; continue; }
    if (quietSince == null) quietSince = performance.now();
    if (performance.now() - quietSince > quiet) break;
  }
  return { frame, settledMs: Math.round(performance.now() - t0) };
}

export async function check(target, opts = {}) {
  const { frame, settledMs } = await load(target, opts);
  const w = frame.contentWindow;
  const zz = w.__zz || { reqs: [], errors: [] };
  const s = scan(w.document, w, opts);
  const failed = zz.reqs.filter((r) => !(r.status >= 200 && r.status < 400)).map((r) => `${r.status} ${r.method} ${r.url.replace(/^https?:\/\/[^/]+/, '')}`);
  const slow = zz.reqs.filter((r) => r.ms > 3000).map((r) => `${r.ms}ms ${r.url.replace(/^https?:\/\/[^/]+/, '')}`);
  return {
    page: target.page + (target.hash ? '#' + target.hash : '') + (target.params ? ' ' + JSON.stringify(target.params) : ''),
    width: opts.width, theme: opts.theme, settledMs, url: w.location.pathname + w.location.search + w.location.hash,
    h1: (w.document.querySelector('h1')?.innerText || '').slice(0, 60),
    textLen: w.document.body.innerText.length,
    requests: zz.reqs.length, failed, slow, errors: [...new Set(zz.errors)].slice(0, 8), pending: zz.pending,
    ...s,
  };
}

// Results are also appended to window.__zzResults as each page finishes, so a long sweep can be started
// without awaiting it and read back in pieces (the browser tool gives one call ~45 s).
export async function sweep(targets, { widths = [1280, 375], themes = ['light', 'dark'], ...opts } = {}) {
  const results = [];
  window.__zzResults = window.__zzResults || [];
  for (const t of targets) for (const width of widths) for (const theme of themes) {
    let r;
    try {
      r = await check(t, { ...opts, width, theme });
    } catch (e) {
      r = { page: t.page, width, theme, settledMs: 0, requests: 0, failed: [], slow: [], errors: ['scan failed: ' + e], pending: 0, overflow: 0, wide: [], contrast: [], badText: [], loading: [], emptyTables: 0, badLinks: [], textLen: 0 };
    }
    results.push(r);
    window.__zzResults.push(r);
  }
  return results;
}

// Load a page, run `action(win, doc)` in it (fill a box, click a button), wait until it settles again, then scan:
// for views that only exist after input (step 2c: Awards Race after "Predict MVP", a Workbench board after
// "Load every block now"). `action` may be async; the returned record is check()'s plus `action` (its result).
export async function checkAfter(target, action, opts = {}) {
  const { frame, settledMs } = await load(target, opts);
  const w = frame.contentWindow;
  let acted;
  try { acted = await action(w, w.document); } catch (e) { acted = 'action failed: ' + e; }
  const t0 = performance.now();
  let quietSince = null;
  for (;;) {
    await sleep(150);
    if (performance.now() - t0 > (opts.cap || 25000)) break;
    const busy = w.__zz.pending > 0 || LOADING_TEXT.test(w.document.body.innerText);
    if (busy) { quietSince = null; continue; }
    if (quietSince == null) quietSince = performance.now();
    if (performance.now() - quietSince > (opts.quiet || 1200)) break;
  }
  const zz = w.__zz;
  const s = scan(w.document, w, opts);
  return {
    page: (target.page || 'link') + (target.hash ? '#' + target.hash : '') + (opts.label ? ' [' + opts.label + ']' : ''),
    width: opts.width, theme: opts.theme, settledMs: settledMs + Math.round(performance.now() - t0), url: w.location.pathname + w.location.search + w.location.hash,
    h1: (w.document.querySelector('h1')?.innerText || '').slice(0, 60), textLen: w.document.body.innerText.length,
    requests: zz.reqs.length,
    failed: zz.reqs.filter((r) => !(r.status >= 200 && r.status < 400)).map((r) => `${r.status} ${r.method} ${r.url.replace(/^https?:\/\/[^/]+/, '')}`),
    slow: zz.reqs.filter((r) => r.ms > 3000).map((r) => `${r.ms}ms ${r.url.replace(/^https?:\/\/[^/]+/, '')}`),
    errors: [...new Set(zz.errors)].slice(0, 8), pending: zz.pending, action: acted, ...s,
  };
}

// Helpers for actions: set a box's value the way React sees it, and click the first button whose text matches.
export function typeInto(el, value) { setValue(el, value); }
export function clickText(doc, re, sel = 'button') {
  const b = [...doc.querySelectorAll(sel)].find((x) => re.test((x.innerText || x.getAttribute('aria-label') || '').trim()));
  if (!b) throw new Error('no ' + sel + ' matching ' + re);
  b.click();
  return (b.innerText || '').trim().slice(0, 40);
}

// One line per problem, grouped by page.
export function summarise(results) {
  const lines = [];
  for (const r of results) {
    const p = [];
    if (r.errors.length) p.push('errors: ' + r.errors.join(' | '));
    if (r.failed.length) p.push('failed: ' + r.failed.join(', '));
    if (r.slow.length) p.push('slow: ' + r.slow.join(', '));
    if (r.pending) p.push(`pending ${r.pending}`);
    if (r.overflow) p.push(`overflow ${r.overflow}px: ` + r.wide.map((w) => `${w.el}(${w.right})`).join(', '));
    if (r.contrast.length) p.push('contrast: ' + r.contrast.slice(0, 6).map((c) => `${c.el} "${c.text}" ${c.ratio}<${c.floor} ${c.fg} on ${c.bg} ×${c.count}`).join(' ; ') + (r.contrast.length > 6 ? ` ; +${r.contrast.length - 6} more` : ''));
    if (r.marks?.length) p.push('marks: ' + r.marks.slice(0, 6).map((m) => `${m.svg} ${m.el} ${m.paint}@${m.alpha} ${m.ratio}<3 on ${m.bg} ×${m.count}`).join(' ; ') + (r.marks.length > 6 ? ` ; +${r.marks.length - 6} more` : ''));
    if (r.badText.length) p.push('text: ' + r.badText.map((b) => `${b.el} "${b.text}"`).join(' ; '));
    if (r.loading.length) p.push('loading: ' + r.loading.map((b) => `${b.el} "${b.text}"`).join(' ; '));
    if (r.invisible) p.push(`text at opacity ~0: ${r.invisible} elements`);
    if (r.emptyTables) p.push(`empty tables ${r.emptyTables}`);
    if (r.badLinks.length) p.push('links: ' + r.badLinks.map((b) => `${b.why} ${b.href}`).join(' ; '));
    if (r.textLen < 200) p.push(`little text (${r.textLen})`);
    lines.push(`${r.page} @${r.width} ${r.theme} [${r.settledMs}ms, ${r.requests} req]` + (p.length ? '\n   ' + p.join('\n   ') : ' OK'));
  }
  return lines.join('\n');
}

// What a view shows, for comparing two loads: inputs' values and the first rows of every table.
export function fingerprint(doc) {
  const main = doc.querySelector('main') || doc.body;
  const controls = [...main.querySelectorAll('select, input:not([type=hidden]):not([type=file])')]
    .map((el) => (el.type === 'checkbox' || el.type === 'radio' ? (el.checked ? 1 : 0) : el.value));
  const tables = [...main.querySelectorAll('table')].map((t) => [...t.querySelectorAll('tbody tr')].slice(0, 3).map((tr) => tr.innerText.replace(/\s+/g, ' ').trim()).join(' | '));
  const pressed = [...main.querySelectorAll('[aria-pressed="true"], [aria-selected="true"], .active, [class*="--active"]')].map((el) => (el.innerText ?? el.textContent ?? '').trim().slice(0, 30)); // SVG elements have no innerText
  return JSON.stringify({ controls, tables, pressed });
}

function setValue(el, value) {
  const proto = el.tagName === 'SELECT' ? el.ownerDocument.defaultView.HTMLSelectElement.prototype : el.ownerDocument.defaultView.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  el.dispatchEvent(new el.ownerDocument.defaultView.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }));
}

// Change the n-th <select> on the page to another option, wait, then open the resulting URL in a fresh
// frame: the two views should match (the "Copy link" promise). Returns what differed, if anything.
export async function linkRoundTrip(target, { selectIndex = 0, optionIndex = 1, width = 1280, theme = 'light' } = {}) {
  const { frame } = await load(target, { width, theme });
  const w = frame.contentWindow;
  const selects = [...w.document.querySelectorAll('main select')].filter((s) => s.options.length > 1 && !s.disabled);
  const sel = selects[selectIndex];
  if (!sel) return { page: target.page, skipped: 'no select' };
  const before = w.location.search;
  const pick = [...sel.options].map((o) => o.value).filter((v) => v !== sel.value)[optionIndex - 1] ?? sel.options[0].value;
  setValue(sel, pick);
  for (let i = 0; i < 60; i++) { await sleep(150); if (!w.__zz.pending && !LOADING_TEXT.test(w.document.body.innerText) && i > 8) break; }
  await sleep(600);
  const url = w.location.pathname + w.location.search + w.location.hash;
  const a = fingerprint(w.document);
  const second = await load({ page: null }, { width, theme, base: url });
  await sleep(300);
  const b = fingerprint(second.frame.contentWindow.document);
  const url2 = second.frame.contentWindow.location.search;
  return { page: target.page, changed: `${sel.getAttribute('aria-label') || sel.name || 'select#' + selectIndex} → ${pick}`, urlChanged: before !== w.location.search, url, same: a === b, urlStable: url2 === w.location.search, a: a === b ? undefined : a.slice(0, 600), b: a === b ? undefined : b.slice(0, 600) };
}

// Open the first link of every kind (target page) found on a page, each in a fresh frame, and check it
// opens a real view: a heading, some text, no console errors, no failed request. `perKind` links per kind.
export async function followLinks(target, { perKind = 1, width = 1280, theme = 'light', skip = [] } = {}) {
  const { frame } = await load(target, { width, theme });
  const w = frame.contentWindow;
  const byKind = {};
  for (const a of w.document.querySelectorAll('main a[href]')) {
    let u;
    try { u = new URL(a.getAttribute('href'), w.location.href); } catch { continue; }
    if (u.origin !== w.location.origin) continue;
    const page = u.searchParams.get('page') || (u.hash ? 'hash' : null);
    if (!page || skip.includes(page)) continue;
    const key = page + (u.searchParams.get('v') ? ':' + u.searchParams.get('v') : '') + (page === 'analytics' || page === 'methodology' ? u.hash + (u.searchParams.get('card') || '') : '');
    (byKind[key] = byKind[key] || []);
    if (byKind[key].length < perKind) byKind[key].push({ href: u.pathname + u.search + u.hash, text: a.innerText.trim().slice(0, 40) });
  }
  const out = [];
  for (const [kind, links] of Object.entries(byKind)) {
    for (const l of links) {
      const r = await load({ page: null }, { width, theme, base: l.href });
      const d = r.frame.contentWindow.document;
      const zz = r.frame.contentWindow.__zz || { errors: [], reqs: [] };
      const failed = zz.reqs.filter((q) => !(q.status >= 200 && q.status < 400)).map((q) => `${q.status} ${q.url.replace(/^https?:\/\/[^/]+/, '')}`);
      const h1 = (d.querySelector('h1')?.innerText || '').trim();
      const text = d.body.innerText.length;
      const notFound = /not found|no data|couldn.t|could not|failed/i.test((d.querySelector('main')?.innerText || '').slice(0, 2000));
      const problem = !h1 || text < 400 || zz.errors.length || failed.length;
      out.push({ kind, link: l.text, href: l.href.slice(0, 140), h1: h1.slice(0, 40), text, ok: !problem, notFoundText: notFound, errors: [...new Set(zz.errors)].slice(0, 3), failed });
    }
  }
  return { page: target.page, kinds: Object.keys(byKind).length, results: out };
}

// Buttons that look like navigation ("Open …", "… →", "Methodology", "See …"): click each in a fresh load and
// report where it went and whether that view rendered.
export async function followButtons(target, { width = 1280, theme = 'light', match = /→|open|methodology|see |full |page|workbench|replay/i, limit = 30 } = {}) {
  const first = await load(target, { width, theme });
  const labels = [...first.frame.contentWindow.document.querySelectorAll('main button')]
    .map((b, i) => ({ i, text: (b.innerText || b.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 50) }))
    .filter((b) => match.test(b.text) && !/copy link|save|report|csv|json|png|svg/i.test(b.text));
  const seen = new Set();
  const out = [];
  for (const b of labels.slice(0, limit)) {
    if (seen.has(b.text)) continue;
    seen.add(b.text);
    const r = await load(target, { width, theme });
    const w = r.frame.contentWindow;
    const before = w.location.search + w.location.hash;
    const btn = w.document.querySelectorAll('main button')[b.i];
    if (!btn) { out.push({ button: b.text, went: 'button not found on reload' }); continue; }
    btn.click();
    for (let k = 0; k < 40; k++) { await sleep(150); if (!w.__zz.pending && k > 6) break; }
    await sleep(500);
    const after = w.location.search + w.location.hash;
    const h1 = (w.document.querySelector('h1')?.innerText || '').trim().slice(0, 40);
    out.push({ button: b.text, went: after === before ? '(same page)' : after.replace(/zztheme=\w+&?/, ''), h1, errors: [...new Set(w.__zz.errors)].slice(0, 2), text: w.document.body.innerText.length });
  }
  return out;
}
