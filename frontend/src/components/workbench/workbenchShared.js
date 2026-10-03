import { plain, signed } from '../../utils/format';
import { PALETTE_SIZE } from '../../utils/workbenchStore';

// Set colours are stored as a palette index; the colours themselves are the
// --wb-series-N tokens in styles/workbench.css (Paper and Ink each have their
// own), so a board looks right in either theme and the charts of step 4 can
// read the same tokens.
export const COLOR_NAMES = ['orange', 'blue', 'green', 'purple', 'gold', 'teal', 'pink', 'grey'];
export const seriesVar = (i) => `var(--wb-series-${((i % PALETTE_SIZE) + PALETTE_SIZE) % PALETTE_SIZE})`;

export const seasonLabel = (s) => (s == null ? '—' : `${s - 1}-${String(s).slice(-2)}`);

export function formatValue(format, v) {
    if (v == null || Number.isNaN(v)) return '—';
    switch (format) {
        case 'int': return Math.round(v).toLocaleString();
        case 'num2': return plain(v, 2);
        case 'pct': return `${plain(v * 100, 1)}%`;
        case 'signed1': return signed(v, 1);
        case 'signed2': return signed(v, 2);
        default: return plain(v, 1);
    }
}

// The first palette colour no member of the set uses yet.
export function nextColor(members) {
    const used = new Set(members.map((m) => m.color));
    for (let i = 0; i < PALETTE_SIZE; i += 1) if (!used.has(i)) return i;
    return members.length % PALETTE_SIZE;
}

export const BLOCK_INFO = {
    set: { label: 'Player or team set', short: 'Set', icon: 'group', w: 4, h: 7 },
    table: { label: 'Table', short: 'Table', icon: 'table_view', w: 8, h: 9 },
    chart: { label: 'Chart', short: 'Chart', icon: 'monitoring', w: 6, h: 11 },
    note: { label: 'Note', short: 'Note', icon: 'sticky_note_2', w: 4, h: 4 },
};

// What a block is called on the board: its own title, else something
// descriptive from its settings ("Table: Player seasons · Players").
export function blockLabel(block, board, catalogue) {
    if (block.title) return block.title;
    const set = board.sets.find((s) => s.id === block.settings.setId);
    if (block.type === 'set') return set ? set.name : 'Set';
    if (block.type === 'note') return 'Note';
    const ds = catalogue?.datasets.find((d) => d.key === block.settings.dataset);
    if (block.type === 'chart') {
        const s = block.settings;
        const lab = (k) => ds?.columns.find((c) => c.key === k)?.label;
        const kind = { scatter: 'Scatter', line: 'Line', bar: 'Bars', histogram: 'Histogram', box: 'Distribution', heatmap: 'Heatmap' }[s.chart] || 'Chart';
        const what = s.chart === 'scatter' ? [lab(s.y), lab(s.x)].filter(Boolean).join(' vs ') : lab(s.y);
        return `${kind}${what ? `: ${what}` : ''}${set ? ` · ${set.name}` : ''}`;
    }
    return `${ds ? ds.label : 'Table'}${set ? ` · ${set.name}` : ''}`;
}

export const entityWord = (kind, plural = true) => (kind === 'team' ? (plural ? 'teams' : 'team') : (plural ? 'players' : 'player'));
