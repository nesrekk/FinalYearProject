import { entityWord, setFits } from './workbenchShared';

// The Table block's settings → the spec POST /workbench/query runs, checked
// against the live catalogue. Shared by the block and the page (defaults for
// a new table).

const DEFAULT_COLUMNS = {
    player_season: ['pts', 'reb', 'ast', 'ts_pct'],
    player_game: ['min', 'pts', 'reb', 'ast', 'plus_minus'],
    team_season: ['w', 'l', 'n_rtg', 'pace'],
    team_game: ['pts', 'opp_pts', 'margin'],
    player_onoff: ['minutes_on', 'net_on', 'net_off', 'on_off_net'],
    lineup_season: ['minutes', 'poss', 'off_rating', 'def_rating', 'net_rating'],
    pair_season: ['minutes', 'poss', 'net_rating'],
    team_possessions: ['ppp', 'ppp_steal', 'ppp_made_fg', 'trans_share', 'd_ppp'],
    player_projection: ['proj_pts', 'proj_reb', 'proj_ast', 'proj_ts_pct', 'proj_bpm'],
};
export const LIMIT_CHOICES = [25, 50, 100, 250, 500, 1000];

export const one = (rowLabel) => rowLabel.replace(/s$/, '');
// What one row is: "player-season", "team-game"; datasets whose rows are
// something else say so in row_label ("lineup-seasons", "player-team-seasons").
export const rowUnit = (ds, plural = false) => {
    const label = ds.row_label.includes('-') ? ds.row_label : `${ds.entity}-${ds.row_label}`;
    return plural ? label : one(label);
};

export function groupLabel(key, ds) {
    const unit = rowUnit(ds); // player-season, team-game, lineup-season
    return {
        none: `Each ${unit} on its own`,
        entity: `Each ${entityWord(ds.entity, false)}, combined`,
        season: 'Each season, combined',
        team: 'Each team code, combined',
        opponent: 'Each opponent, combined',
        home: 'Home and away, combined',
        result: 'Wins and losses, combined',
        lineup: 'Each lineup, seasons combined',
        pair: 'Each pair, seasons combined',
        all: 'Everything in one row',
    }[key] || key;
}

// Which rows a set picks: its members as the dataset's entities, or, for a
// player set on lineups/pairs, the units with any (or all) of its players.
export function entitySpec(settings, ds, set) {
    if (!set) return { entities: 'all' };
    const ids = set.members.map((m) => m.id);
    if (set.kind !== ds.entity) return { entities: 'all', players: ids, players_match: settings.playersMatch === 'all' ? 'all' : 'any' };
    return { entities: ids };
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
    if (set && !setFits(set, ds)) return { spec: null, problem: `${ds.label} are about ${entityWord(ds.entity)}, but ${set.name} holds ${entityWord(set.kind)}. Choose another set or dataset in Settings.`, dropped };
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
        ...entitySpec(settings, ds, set),
        columns,
        season_from: seasonFrom,
        season_to: seasonTo,
        group_by: groupBy,
        per,
        sort,
        limit: settings.limit,
        ...(settings.minGames ? { min_games: settings.minGames } : {}),
        ...(ds.poss_floor != null && settings.minPoss ? { min_poss: settings.minPoss } : {}),
    };
    return { spec, problem: '', dropped };
}
