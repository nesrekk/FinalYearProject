// Shared formatting for Situational Splits (profile block + page).
// Values: counting stats per 36 (1 decimal; steals and blocks 2), minutes per
// game, shooting and usage as %. Gaps in % stats are percentage points.

// Short side names for table headers (the full ones go in the title).
export const SHORT_SIDES = {
    home: ['Home', 'Away'],
    rest: ['B2B', 'Rested'],
    travel: ['1,000+ mi', '<300 mi'],
    opp: ['vs. top 10', 'vs. bottom 10'],
};

export const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;

const DECIMALS = { stl: 2, blk: 2 };
const sign = (v) => (v > 0 ? '+' : v < 0 ? '−' : '');

export function fmtValue(v, stat, format) {
    if (v == null) return '—';
    if (format === 'pct') return `${(v * 100).toFixed(1)}%`;
    return v.toFixed(DECIMALS[stat] ?? 1);
}

export function fmtGap(v, stat, format, { unit = true } = {}) {
    if (v == null) return '—';
    const digits = format === 'pct' ? 1 : (DECIMALS[stat] ?? 1);
    const shown = Math.abs(format === 'pct' ? v * 100 : v).toFixed(digits);
    // No sign on a gap that rounds to zero ("0.0", not "−0.0").
    const s = Number(shown) === 0 ? '' : sign(v);
    return `${s}${shown}${format === 'pct' && unit ? ' pp' : ''}`;
}

export const fmtR = (r) => (r == null ? '—' : `${r < 0 && Math.abs(r) >= 0.005 ? '−' : ''}${Math.abs(r).toFixed(2)}`);

// How to read a year-to-year r for a split effect.
export function repeatWords(r) {
    if (r == null) return 'too few repeat players to say';
    if (r < 0.1) return 'essentially no carry-over';
    if (r < 0.2) return 'a little carry-over';
    return 'some carry-over';
}
