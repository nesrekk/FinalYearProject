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
