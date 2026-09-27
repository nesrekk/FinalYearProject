// Turns a rendered <table> into CSV or JSON: exactly what the reader sees,
// minus decoration. Icon ligatures (aria-hidden), info tooltips and
// [data-export-skip] nodes are left out, [data-export-as] swaps a badge for
// plain words, and non-inline children (badges, pills) are kept apart by a
// space. Each <th>/<td> spanning N columns fills N cells.

function cellText(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent;
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    if (node.getAttribute('aria-hidden') === 'true' || node.hasAttribute('data-export-skip')) return '';
    // InfoTooltip's "i" button and its popover text are help, not data.
    if (node.classList.contains('it-wrap')) return '';
    const tag = node.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'svg' || tag === 'SVG') return '';
    if (tag === 'IMG') return '';
    if (tag === 'BR') return ' ';
    if (node.hasAttribute('data-export-as')) return ` ${node.getAttribute('data-export-as')} `;
    const display = getComputedStyle(node).display;
    if (display === 'none') return '';
    let out = '';
    for (const child of node.childNodes) out += cellText(child);
    return display === 'inline' || display === 'contents' ? out : ` ${out} `;
}

const clean = (s) => s.replace(/\s+/g, ' ').trim();

function rowCells(tr) {
    const cells = [];
    for (const cell of tr.cells) {
        const text = clean(cellText(cell));
        const span = Math.max(1, cell.colSpan || 1);
        cells.push(text);
        for (let i = 1; i < span; i += 1) cells.push('');
    }
    return cells;
}

export function tableToRows(table) {
    const headRows = table.tHead ? [...table.tHead.rows] : [];
    const bodyRows = [...table.tBodies].flatMap((b) => [...b.rows]);
    let header = headRows.length ? rowCells(headRows[headRows.length - 1]) : null;
    const width = header ? header.length : Math.max(0, ...bodyRows.map((r) => rowCells(r).length));
    if (!header) header = Array.from({ length: width }, (_, i) => `Column ${i + 1}`);
    // Blank header cells (e.g. a rank column) still need a unique key.
    header = header.map((h, i) => h || `Column ${i + 1}`);
    const seen = new Map();
    header = header.map((h) => {
        const n = (seen.get(h) || 0) + 1;
        seen.set(h, n);
        return n > 1 ? `${h} (${n})` : h;
    });

    const rows = [];
    for (const tr of bodyRows) {
        // A single cell spanning the whole row is a message or an expanded
        // detail panel, not data.
        if (tr.cells.length === 1 && width > 1) continue;
        const cells = rowCells(tr);
        while (cells.length < width) cells.push('');
        rows.push(cells.slice(0, width));
    }
    return { header, rows };
}

const csvField = (v) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);

export function toCsv({ header, rows }) {
    return [header, ...rows].map((r) => r.map(csvField).join(',')).join('\r\n');
}

// "1,234" -> 1234, "−3.2" -> -3.2, "+0.8" -> 0.8. Anything else (percentages,
// records like "52-30", names, dashes for missing) stays a string.
const NUMERIC = /^[+\-−]?(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?$/;
function jsonValue(v) {
    if (v === '' || v === '—' || v === '-') return null;
    if (NUMERIC.test(v) && /\d/.test(v)) return Number(v.replace(/,/g, '').replace('−', '-'));
    return v;
}

export function toJson({ header, rows }) {
    return JSON.stringify(
        rows.map((r) => Object.fromEntries(header.map((h, i) => [h, jsonValue(r[i])]))),
        null,
        2,
    );
}

export function slugify(s) {
    // Strip accents first so "Jokić" becomes "jokic", not "joki".
    const ascii = clean(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    return ascii.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'table';
}

export function downloadText(text, filename, mime) {
    // BOM so Excel opens UTF-8 names (Dončić, Jokić) correctly.
    const blob = new Blob([mime === 'text/csv' ? '﻿' + text : text], { type: `${mime};charset=utf-8` });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
}
