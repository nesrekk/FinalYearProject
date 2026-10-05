/**
 * Calendar date in the user's local timezone (not UTC).
 * Using Date.toISOString().slice(0, 10) breaks "today" for US evenings vs UTC midnight.
 */
export function localDateIso(d = new Date()) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}

/**
 * The NBA's calendar date (US Eastern) for an instant, as YYYY-MM-DD. The scoreboard, the stored
 * results and every date in the database use it: at 8 am in India on 21 October, "today's games"
 * are the 20 October games still being played in the US.
 */
export function nbaDateIso(d = new Date()) {
    try {
        return new Intl.DateTimeFormat('en-CA', {
            timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
        }).format(d);
    } catch {
        return localDateIso(d);
    }
}

/** YYYY-MM-DD shifted by `days` (calendar arithmetic on the string, no timezone involved). */
export function shiftIsoDate(iso, days) {
    const [y, m, d] = iso.split('-').map(Number);
    const t = new Date(Date.UTC(y, m - 1, d + days));
    return `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, '0')}-${String(t.getUTCDate()).padStart(2, '0')}`;
}
