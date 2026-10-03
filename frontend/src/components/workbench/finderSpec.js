// The Player Finder block's settings → the spec POST /workbench/finder runs
// (api/routers/workbench_finder.py), checked against the live catalogue.
// Values are kept in the API's units (a percentage as a share: 0.6 = 60%);
// the block shows and takes percentages as 60.

export const FINDER_DATASETS = ['player_season', 'player_game'];
export const VALUE_OPS = [
    ['gte', '≥ at least'], ['gt', '> more than'], ['lte', '≤ at most'], ['lt', '< less than'],
    ['eq', '= exactly'], ['ne', '≠ not'], ['between', 'between'],
];
export const COUNT_OPS = [['gte', 'at least'], ['gt', 'more than'], ['lte', 'at most'], ['lt', 'fewer than'], ['eq', 'exactly']];
export const PER_WORDS = { game: 'per game', total: 'in total', per36: 'per 36 minutes', per100: 'per 100 possessions' };
export const OP_SIGN = { gte: '≥', gt: '>', lte: '≤', lt: '<', eq: '=', ne: '≠' };
export const MAX_CONDITIONS = 8;
export const MAX_TESTS = 4;

export const rowWord = (dsKey, plural = true) => (dsKey === 'player_game' ? (plural ? 'games' : 'game') : (plural ? 'seasons' : 'season'));

export function finderDefaults() {
    return {
        dataset: 'player_season',
        scope: 'season',
        seasonFrom: null,
        seasonTo: null,
        minGames: 40,
        setId: null,
        where: 'all',
        result: 'all',
        opponent: null,
        minMinutes: null,
        conditions: [{ type: 'value', stat: 'pts', op: 'gte', value: 25, value2: null, per: 'game', minN: null }],
        sort: null,
        limit: 100,
    };
}

// A percentage is typed and shown as 60, stored as 0.6.
const SCALE = (fmt) => (fmt === 'pct' ? 100 : 1);
export const shown = (v, fmt) => (v == null ? '' : String(Number((v * SCALE(fmt)).toFixed(6))));
export const stored = (text, fmt) => {
    if (text === '' || text == null) return null;
    const n = Number(text);
    return Number.isFinite(n) ? n / SCALE(fmt) : null;
};

// Rates whose sample is attempts, possessions or plays get a minimum-n box.
export const hasSampleFloor = (col) => !!col && col.kind !== 'count' && col.kind !== 'sum' && !['games', 'seasons'].includes(col.n_unit);

// The Leaderboard's per-game attempt floor (from the season table's entry of
// the same stat) × the games floor: the starting value of a % condition's box.
export function defaultMinN(catalogue, statKey, minGames) {
    const season = catalogue?.datasets.find((d) => d.key === 'player_season');
    const perGame = season?.columns.find((c) => c.key === statKey)?.min_attempts_per_game;
    return perGame ? Math.round(perGame * Math.max(minGames || 0, 20)) : null;
}

export function conditionTitle(c, ds) {
    const col = (k) => ds.columns.find((x) => x.key === k);
    const testText = (t) => {
        const cc = col(t.stat);
        const v = t.op === 'between' ? `${shown(t.value, cc?.format)}-${shown(t.value2, cc?.format)}` : `${OP_SIGN[t.op] || ''}${shown(t.value, cc?.format)}`;
        return `${cc?.short || t.stat} ${v}${cc?.format === 'pct' ? '%' : ''}`;
    };
    if (c.type === 'value') {
        const cc = col(c.stat);
        const per = cc?.kind === 'count' && c.per !== 'game' ? { total: ' total', per36: '/36', per100: '/100' }[c.per] : '';
        return `${cc?.short || c.stat}${per}`;
    }
    const tests = c.tests.map(testText).join(', ');
    return c.type === 'count' ? `${rowWord(ds.key)}: ${tests}` : `in a row: ${tests}`;
}

// Settings → { spec | null, problems: [text] }.
export function buildFinderSpec(s, ds, set) {
    const problems = [];
    const byKey = new Map(ds.columns.map((c) => [c.key, c]));
    if (set && set.kind !== 'player') problems.push(`${set.name} holds teams; the finder looks for players.`);
    if (set && set.kind === 'player' && set.members.length === 0) problems.push(`${set.name} is empty, so there is no one to look through.`);
    const okStat = (k, where) => {
        const c = byKey.get(k);
        if (!c) { problems.push(`${where}: “${k}” isn’t a ${ds.label.toLowerCase()} stat.`); return null; }
        if (c.status !== 'verified') { problems.push(`${where}: ${c.label} isn’t offered (${c.reason})`); return null; }
        return c;
    };
    const okValue = (t, where) => {
        if (t.value == null) { problems.push(`${where}: type a number.`); return false; }
        if (t.op === 'between' && t.value2 == null) { problems.push(`${where}: type both ends of the range.`); return false; }
        return true;
    };
    const val = (t) => (t.op === 'between' ? [Math.min(t.value, t.value2), Math.max(t.value, t.value2)] : t.value);
    const conditions = [];
    s.conditions.forEach((c, i) => {
        const where = `Condition ${i + 1}`;
        if (c.type === 'value') {
            const col = okStat(c.stat, where);
            if (!col || !okValue(c, where)) return;
            if (col.kind === 'none' && !(ds.key === 'player_season' && s.scope === 'season')) {
                problems.push(`${where}: ${col.label} is one value per season and can’t be combined; use it “in a single season” with season stats, or in a count.`);
                return;
            }
            const per = col.kind === 'count' && (col.per_modes || []).includes(c.per) ? c.per : 'game';
            conditions.push({
                type: 'value', stat: c.stat, op: c.op, value: val(c), per,
                ...(hasSampleFloor(col) && c.minN ? { min_n: c.minN } : {}),
            });
            return;
        }
        const tests = [];
        c.tests.forEach((t, j) => {
            const w = `${where}, test ${j + 1}`;
            if (okStat(t.stat, w) && okValue(t, w)) tests.push({ stat: t.stat, op: t.op, value: val(t) });
        });
        if (tests.length !== c.tests.length) return;
        conditions.push({ type: c.type, tests, count: c.count, ...(c.type === 'count' ? { count_op: c.countOp } : {}) });
    });
    if (!s.conditions.length) problems.push('Add a condition.');
    if (problems.length || !conditions.length) return { spec: null, problems };

    const { from, to } = ds.seasons;
    const clamp = (x) => Math.max(from, Math.min(to, x));
    const seasonTo = clamp(s.seasonTo ?? to);
    const seasonFrom = Math.min(seasonTo, clamp(s.seasonFrom ?? from));
    const filters = [];
    if (ds.key === 'player_game') {
        if (s.where !== 'all') filters.push({ key: 'home', op: 'eq', value: s.where === 'home' });
        if (s.result !== 'all') filters.push({ key: 'result', op: 'eq', value: s.result === 'W' });
        if (s.opponent) filters.push({ key: 'opponent', op: 'eq', value: s.opponent });
    }
    if (s.minMinutes) filters.push({ key: 'min', op: 'gte', value: s.minMinutes });
    const sortOk = s.sort && (/^c(\d)$/.test(s.sort.key) ? Number(s.sort.key.slice(1)) < conditions.length : s.sort.key !== 'season' || s.scope === 'season');
    const spec = {
        dataset: ds.key,
        scope: s.scope,
        season_from: seasonFrom,
        season_to: seasonTo,
        entities: set && set.kind === 'player' ? set.members.map((m) => m.id) : 'all',
        filters,
        conditions,
        limit: s.limit,
        ...(s.minGames ? { min_games: s.minGames } : {}),
        ...(sortOk ? { sort: s.sort } : {}),
    };
    return { spec, problems };
}

// The stats a finder's conditions use, for a table made from its result.
export function finderStats(s) {
    const keys = [];
    for (const c of s.conditions) {
        for (const k of c.type === 'value' ? [c.stat] : c.tests.map((t) => t.stat)) if (k && !keys.includes(k)) keys.push(k);
    }
    return keys;
}
