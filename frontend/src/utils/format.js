// Signed numbers as they read on the page. The sign comes from the value after rounding, so −0.04 at
// one decimal reads "0.0" (not "−0.0") and 0.03 reads "0.0" (not "+0.0"). Colour a cell with
// shownSign() at the same decimals, so a cell that reads 0.0 isn't tinted.

export const MINUS = '−'; // U+2212

// 1, −1 or 0: the sign of v once rounded to d decimals (0 for null).
export function shownSign(v, d = 1) {
    if (v == null) return 0;
    const x = Number(v);
    if (Number(Math.abs(x).toFixed(d)) === 0) return 0;
    return x > 0 ? 1 : x < 0 ? -1 : 0;
}

// '+1.2', '−1.2', '0.0'; '—' for null. Pass minus '-' where a page has always printed a hyphen.
export function signed(v, d = 1, minus = MINUS) {
    if (v == null) return '—';
    const s = shownSign(v, d);
    return `${s > 0 ? '+' : s < 0 ? minus : ''}${Math.abs(Number(v)).toFixed(d)}`;
}

// v's sign in front of its magnitude already formatted (`body`, e.g. "$1.2M" or "4.0 pts"), and no
// sign when that magnitude reads zero (no digit 1-9 in it).
export function withSign(v, body, minus = MINUS) {
    if (v == null) return '—';
    if (!/[1-9]/.test(body)) return body;
    return `${v > 0 ? '+' : v < 0 ? minus : ''}${body}`;
}

// pos, neg or zero by the sign v shows at d decimals: a class name, a colour or an icon.
export function bySign(v, d, pos, neg, zero = '') {
    const s = shownSign(v, d);
    return s > 0 ? pos : s < 0 ? neg : zero;
}

// toFixed without a minus on a value that rounds to zero ("0.00", not "-0.00"); other values as toFixed.
export function plain(v, d = 1) {
    const t = Number(v).toFixed(d);
    return Number(t) === 0 ? t.replace('-', '') : t;
}

// A season end year as the app writes it: 2026 → "2025-26".
export function seasonLabel(s) {
    return `${s - 1}-${String(s).slice(-2)}`;
}
