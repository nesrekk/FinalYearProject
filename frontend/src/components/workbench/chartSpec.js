import { entityWord, setFits } from './workbenchShared';
import { entitySpec, rowUnit } from './tableSpec';

// The Chart block's settings → the requests it makes, checked against the
// live catalogue:
//   main     POST /workbench/query    the set's rows (the coloured marks)
//   context  POST /workbench/query    every entity's rows (grey dots, scatter only)
//         or POST /workbench/context  quantiles / a histogram over every
//                                    matching row (grey bands, boxes, bars)
//   trend    POST /workbench/trend    built later, on whichever rows were drawn
// Every request uses the same seasons, games floor and "per", so the grey
// population is always the one the coloured marks are compared with.

export const CHART_TYPES = [
    { key: 'scatter', label: 'Scatter', icon: 'scatter_plot', needsSet: false },
    { key: 'line', label: 'Line', icon: 'show_chart', needsSet: true },
    { key: 'bar', label: 'Bar', icon: 'bar_chart', needsSet: true },
    { key: 'histogram', label: 'Histogram', icon: 'equalizer', needsSet: false },
    { key: 'box', label: 'Distribution', icon: 'candlestick_chart', needsSet: true },
    { key: 'heatmap', label: 'Heatmap', icon: 'grid_on', needsSet: true },
];
export const CHART_KEYS = CHART_TYPES.map((c) => c.key);
export const BIN_CHOICES = [0, 10, 20, 40];
export const ROW_CAP = 5000;

const SCATTER_DEFAULTS = {
    player_season: ['usg_pct', 'ts_pct'],
    player_game: ['min', 'pts'],
    team_season: ['o_rtg', 'd_rtg'],
    team_game: ['pace', 'margin'],
    player_onoff: ['net_on', 'on_off_net'],
    lineup_season: ['off_rating', 'def_rating'],
    pair_season: ['poss', 'net_rating'],
    team_possessions: ['share_steal', 'ppp_steal'],
    player_projection: ['proj_pts', 'act_pts'],
};
const Y_DEFAULTS = {
    player_season: 'pts', player_game: 'pts', team_season: 'n_rtg', team_game: 'margin', player_onoff: 'on_off_net',
    lineup_season: 'net_rating', pair_season: 'net_rating', team_possessions: 'ppp', player_projection: 'proj_pts',
};

const CATEGORY_LABELS = { season: 'Season', team: 'Team code', opponent: 'Opponent', home: 'Home or away', result: 'Win or loss' };

const verified = (ds) => ds.columns.filter((c) => c.status === 'verified');
const isStat = (ds, k) => verified(ds).some((c) => c.key === k);
export const isGameData = (ds) => ds.row_label === 'games';
const isSeasonData = (ds) => !isGameData(ds);

// Group keys a chart can split or facet by (not the entity itself), with the
// row field each one fills in.
export function categories(ds) {
    return ds.group_by
        .filter((g) => !['none', 'all', 'entity', 'lineup', 'pair'].includes(g))
        .map((g) => ({ key: g, field: ds.fields.group[g]?.[0] || g, label: CATEGORY_LABELS[g] || g }));
}
const fieldOf = (ds, g) => (g === 'entity' ? null : ds.fields.group[g]?.[0] || g);

export function chartDefaults(ds, chart = 'scatter') {
    const [x, y] = (SCATTER_DEFAULTS[ds.key] || []).filter((k) => isStat(ds, k));
    const yOne = isStat(ds, Y_DEFAULTS[ds.key]) ? Y_DEFAULTS[ds.key] : verified(ds)[0]?.key;
    return chart === 'scatter' ? { x: x || null, y: y || null } : { y: yOne };
}

// What each type's slots mean, for the settings form and the caption.
export const SLOTS = {
    scatter: { x: 'Across (x)', y: 'Up (y)' },
    line: { y: 'Stat (y)' },
    bar: { y: 'Stat' },
    histogram: { y: 'Stat' },
    box: { y: 'Stat' },
    heatmap: { y: 'Colour by' },
};

// The stat keys the chart reads, in order (x, y, size, colour).
function statKeys(s, ds) {
    const keys = [];
    const push = (k) => { if (k && isStat(ds, k) && !keys.includes(k)) keys.push(k); };
    if (s.chart === 'scatter') { push(s.x); push(s.y); push(s.size); if (!['member', 'none'].includes(s.color)) push(s.color); }
    else push(s.y);
    return keys;
}

function seasonsOf(s, ds, set) {
    const { from, to } = ds.seasons;
    const clamp = (v) => Math.max(from, Math.min(to, v));
    const seasonTo = clamp(s.seasonTo ?? to);
    const seasonFrom = Math.min(seasonTo, clamp(s.seasonFrom ?? (set ? Math.max(from, seasonTo - 4) : seasonTo)));
    return { seasonFrom, seasonTo };
}

// Stats with a typical aging curve (the Aging Curves page's), for a line
// chart across ages; `aging` is the catalogue's aging block.
export const agingStat = (aging, ds, key) => !!aging && aging.dataset === ds.key && aging.stats.some((x) => x.key === key);
export const MAX_AGING_PLAYERS = 12;

// Returns { problem, main, context, contextKind, enc } where enc says how to draw.
export function buildChart(settings, ds, set, aging = null) {
    const s = settings;
    const type = CHART_TYPES.find((c) => c.key === s.chart) || CHART_TYPES[0];
    const out = { problem: '', main: null, context: null, contextKind: null, enc: null, type: type.key };
    if (set && !setFits(set, ds)) {
        out.problem = `${ds.label} are about ${entityWord(ds.entity)}, but ${set.name} holds ${entityWord(set.kind)}. Choose another set or dataset in Settings.`;
        return out;
    }
    if (type.needsSet && !set) {
        out.problem = `A ${type.label.toLowerCase()} chart draws one ${entityWord(ds.entity, false)} at a time: choose a set in Settings (or add a Set block first).`;
        return out;
    }
    if (set && set.members.length === 0) {
        out.problem = `${set.name} is empty. Add ${entityWord(set.kind)} to it in its Set block.`;
        return out;
    }
    if (set && set.kind !== ds.entity && !['scatter', 'histogram'].includes(type.key)) {
        out.problem = `${set.name} picks the ${ds.label.toLowerCase()} its players are in, which belong to no one player: use a scatter, a histogram or a table for them, or a set of teams here.`;
        return out;
    }
    const keys = statKeys(s, ds);
    const need = type.key === 'scatter' ? [s.x, s.y] : [s.y];
    if (type.key === 'line' && s.lineX === 'age' && isStat(ds, s.y) && !agingStat(aging, ds, s.y)) {
        out.problem = aging && aging.dataset === ds.key
            ? `There is no typical aging curve for this stat. Curves exist for: ${aging.stats.map((x) => x.label).join(', ')}.`
            : `Across ages needs ${aging ? 'player seasons' : 'the aging curves'} as the data.`;
        return out;
    }
    if (need.some((k) => !isStat(ds, k))) {
        out.problem = type.key === 'scatter' ? 'Choose a stat for each axis in Settings.' : 'Choose a stat in Settings.';
        return out;
    }
    if (type.key === 'scatter' && s.x === s.y) {
        out.problem = 'Choose two different stats for the two axes.';
        return out;
    }
    const { seasonFrom, seasonTo } = seasonsOf(s, ds, set);
    const cats = categories(ds);
    const catOk = (g) => cats.some((c) => c.key === g);
    const per = ds.per_modes.some((p) => p.key === s.per) ? s.per : 'game';
    const base = {
        dataset: ds.key,
        season_from: seasonFrom,
        season_to: seasonTo,
        per,
        limit: ROW_CAP,
        ...(s.minGames ? { min_games: s.minGames } : {}),
        ...(ds.poss_floor != null && s.minPoss ? { min_poss: s.minPoss } : {}),
    };
    // A player set on lineups or pairs picks the units its players are in;
    // those rows belong to no one member, so they aren't coloured by member.
    const bySet = set && set.kind === ds.entity;
    const pick = entitySpec(s, ds, set);
    const ids = pick.entities;
    const extra = pick.players ? { players: pick.players, players_match: pick.players_match } : {};
    const one1 = seasonFrom === seasonTo;
    let group = 'none';
    const enc = { x: null, y: null, size: null, color: bySet ? 'member' : 'none', facet: null, category: null, ci: s.ci !== false };

    if (type.key === 'scatter') {
        const g = ds.group_by.includes(s.groupBy) && s.groupBy !== 'all' ? s.groupBy : 'none';
        let facet = s.facet && s.facet !== 'none' ? s.facet : null;
        if (facet === 'member' && (!set || (g !== 'none' && g !== 'entity'))) facet = null;
        if (facet && facet !== 'member' && !catOk(facet)) facet = null;
        if (g === 'none') group = 'none';
        else group = [...new Set([g, ...(facet && facet !== 'member' ? [facet] : [])])];
        if (Array.isArray(group) && group.length === 1) [group] = group;
        // Rows grouped without the entity have nobody to colour.
        const hasEntity = group === 'none' || group === 'entity' || (Array.isArray(group) && group.includes('entity'));
        enc.x = s.x;
        enc.y = s.y;
        enc.size = isStat(ds, s.size) ? s.size : null;
        enc.color = isStat(ds, s.color) ? s.color : (s.color === 'none' || !bySet || !hasEntity ? 'none' : 'member');
        if (facet === 'member' && !bySet) facet = null;
        enc.facet = facet === 'member' ? 'member' : facet ? fieldOf(ds, facet) : null;
        enc.groupBy = group;
        enc.hasEntity = hasEntity;
        out.main = { ...base, entities: ids, ...extra, columns: keys, group_by: group };
        if (set && s.context) {
            out.context = { ...base, entities: 'all', columns: keys, group_by: group };
            out.contextKind = 'rows';
        }
        enc.trend = !!s.trend && !enc.facet;
    } else if (type.key === 'line' && s.lineX === 'age' && agingStat(aging, ds, s.y) && bySet) {
        // Across ages: the set's seasons against the typical aging curve
        // (POST /workbench/aging, the Aging Curves page's numbers).
        const era = aging.eras.some((e) => e.key === s.agingEra) ? s.agingEra : 'all';
        out.aging = { stat: s.y, player_ids: set.members.slice(0, MAX_AGING_PLAYERS).map((m) => m.id), era };
        out.agingCut = Math.max(0, set.members.length - MAX_AGING_PLAYERS);
        enc.x = 'age';
        enc.y = s.y;
        enc.era = era;
        out.enc = enc;
        out.seasonFrom = seasonFrom;
        out.seasonTo = seasonTo;
        return out;
    } else if (type.key === 'line') {
        const x = s.lineX === 'date' && isGameData(ds) ? 'date' : 'season';
        group = x === 'date' || isSeasonData(ds) ? 'none' : ['entity', 'season'];
        enc.x = x;
        enc.y = s.y;
        out.main = { ...base, entities: ids, ...extra, columns: keys, group_by: group };
        if (s.context && x === 'season') {
            out.context = { spec: { ...base, entities: 'all', columns: keys, group_by: group }, column: s.y, by: 'season' };
            out.contextKind = 'summary';
        }
    } else if (type.key === 'bar') {
        const split = s.split && catOk(s.split) ? s.split : null;
        // One season of season data: a row already is the entity's value, so
        // rates the table can't pool (team four factors) still work.
        if (isSeasonData(ds) && (!split || split === 'season') && one1) group = 'none';
        else group = split ? ['entity', split] : 'entity';
        enc.y = s.y;
        enc.category = split ? fieldOf(ds, split) : null;
        out.main = { ...base, entities: ids, ...extra, columns: keys, group_by: group };
        if (s.context) {
            out.context = { spec: { ...base, entities: 'all', columns: keys, group_by: group }, column: s.y, by: enc.category };
            out.contextKind = 'summary';
        }
    } else if (type.key === 'box') {
        enc.y = s.y;
        enc.style = s.style === 'dots' ? 'dots' : 'box';
        out.main = { ...base, entities: ids, ...extra, columns: keys, group_by: 'none' };
        if (s.context) {
            out.context = { spec: { ...base, entities: 'all', columns: keys, group_by: 'none' }, column: s.y, by: null };
            out.contextKind = 'summary';
        }
    } else if (type.key === 'histogram') {
        enc.x = s.y;
        enc.bins = BIN_CHOICES.includes(s.bins) && s.bins ? s.bins : 20;
        if (set) out.main = { ...base, entities: ids, ...extra, columns: keys, group_by: 'none' };
        if (s.context || !set) {
            out.context = { spec: { ...base, entities: 'all', columns: keys, group_by: 'none' }, column: s.y, by: null, bins: enc.bins };
            out.contextKind = 'summary';
        }
    } else if (type.key === 'heatmap') {
        const xg = s.split && catOk(s.split) ? s.split : 'season';
        group = isSeasonData(ds) && xg === 'season' ? 'none' : ['entity', xg];
        enc.y = s.y;
        enc.category = fieldOf(ds, xg);
        out.main = { ...base, entities: ids, ...extra, columns: keys, group_by: group };
        if (s.context) {
            out.context = { spec: { ...base, entities: 'all', columns: keys, group_by: group }, column: s.y, by: enc.category };
            out.contextKind = 'summary';
        }
    }
    // A games floor means nothing where each row is one game (it would empty
    // the chart), so it applies only where rows combine games.
    const single = (g) => isGameData(ds) && g === 'none';
    for (const k of ['main', 'context']) {
        const spec = k === 'context' && out.contextKind === 'summary' ? out.context?.spec : out[k];
        if (spec && single(spec.group_by)) delete spec.min_games;
    }
    out.floorApplies = !!s.minGames && !single((out.main || out.context?.spec || {}).group_by);
    out.enc = enc;
    out.seasonFrom = seasonFrom;
    out.seasonTo = seasonTo;
    return out;
}

// "player-seasons", "team-games", "players (combined)" … for the n line.
export function unitWord(ds, group, n = 2) {
    const plural = n !== 1;
    if (group === 'none') return rowUnit(ds, plural);
    const g = Array.isArray(group) ? group : [group];
    if (g.length === 1 && g[0] === 'entity') return entityWord(ds.entity, plural);
    if (g.length === 1 && g[0] === 'season') return plural ? 'seasons' : 'season';
    if (g.length === 2 && g.includes('entity') && g.includes('season')) return `${ds.entity}-season${plural ? 's' : ''}`;
    return plural ? 'rows' : 'row';
}

export function categoryLabel(ds, field) {
    const c = categories(ds).find((x) => x.field === field || x.key === field);
    return c ? c.label : field;
}
