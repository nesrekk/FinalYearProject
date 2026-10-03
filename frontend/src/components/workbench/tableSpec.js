import { entityWord } from './workbenchShared';

// The Table block's settings → the spec POST /workbench/query runs, checked
// against the live catalogue. Shared by the block and the page (defaults for
// a new table).

const DEFAULT_COLUMNS = {
    player_season: ['pts', 'reb', 'ast', 'ts_pct'],
    player_game: ['min', 'pts', 'reb', 'ast', 'plus_minus'],
    team_season: ['w', 'l', 'n_rtg', 'pace'],
    team_game: ['pts', 'opp_pts', 'margin'],
};
export const LIMIT_CHOICES = [25, 50, 100, 250, 500, 1000];

export const one = (rowLabel) => rowLabel.replace(/s$/, '');

export function groupLabel(key, ds) {
    const unit = `${ds.entity}-${one(ds.row_label)}`; // player-season, team-game
    return {
        none: `Each ${unit} on its own`,
        entity: `Each ${entityWord(ds.entity, false)}, combined`,
        season: 'Each season, combined',
        team: 'Each team code, combined',
        opponent: 'Each opponent, combined',
        home: 'Home and away, combined',
        result: 'Wins and losses, combined',
        all: 'Everything in one row',
    }[key] || key;
}

export function defaultColumns(ds) {
    const ok = new Set(ds.columns.filter((c) => c.status === 'verified').map((c) => c.key));
    return (DEFAULT_COLUMNS[ds.key] || []).filter((k) => ok.has(k));
}

// The spec the server runs, from the block's settings checked against the
// live catalogue. Returns { spec | null, problem, dropped }.
export function buildSpec(settings, ds, set) {
    const byKey = new Map(ds.columns.map((c) => [c.key, c]));
    const columns = settings.columns.filter((k) => byKey.get(k)?.status === 'verified');
    const dropped = settings.columns.filter((k) => !columns.includes(k));
    if (set && set.kind !== ds.entity) return { spec: null, problem: `${ds.label} are about ${entityWord(ds.entity)}, but ${set.name} holds ${entityWord(set.kind)}. Choose another set or dataset in Settings.`, dropped };
    if (set && set.members.length === 0) return { spec: null, problem: `${set.name} is empty. Add ${entityWord(set.kind)} to it in its Set block.`, dropped };
    if (!columns.length) return { spec: null, problem: 'Choose at least one stat in Settings.', dropped };
    const { from, to } = ds.seasons;
    const clamp = (s) => Math.max(from, Math.min(to, s));
    const seasonTo = clamp(settings.seasonTo ?? to);
    const seasonFrom = Math.min(seasonTo, clamp(settings.seasonFrom ?? (set ? Math.max(from, seasonTo - 4) : seasonTo)));
    const groupBy = ds.group_by.includes(settings.groupBy) ? settings.groupBy : 'none';
    const per = ds.per_modes.some((p) => p.key === settings.per) ? settings.per : 'game';
    const sortable = new Set([...columns, 'n_rows', 'n_games', 'season']);
    const sort = settings.sort.filter((s) => sortable.has(s.key) && (s.key !== 'season' || groupBy === 'none' || groupBy === 'season'));
    const spec = {
        dataset: ds.key,
        entities: set ? set.members.map((m) => m.id) : 'all',
        columns,
        season_from: seasonFrom,
        season_to: seasonTo,
        group_by: groupBy,
        per,
        sort,
        limit: settings.limit,
        ...(settings.minGames ? { min_games: settings.minGames } : {}),
    };
    return { spec, problem: '', dropped };
}
