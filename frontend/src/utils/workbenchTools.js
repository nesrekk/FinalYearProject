// The app's own tools as Workbench blocks (round 7 step 5). A tool block is
// bound to one member of a set (a player, or a team) and shows an existing
// component of the app with that component's own data loading and "Not on
// file" reasons; nothing here computes a number.
//
// Block settings (utils/workbenchStore.cleanSettings):
//   { tool, setId, member, season, view, games }
//   member  the set member shown (player id or team code); null = the first
//   season  end-year int; null = the latest the tool has
//   view    shot chart 'dots' | 'heatmap', quality map 'expected' | 'league'
//   games   shot chart 'regular' | 'playoffs' | 'all'

import { fetchPlayerFullProfile, fetchTeamProfile } from '../services/api';

// w / h: the block's size when added (12-column grid, 40 px rows).
export const TOOLS = {
    card: { label: 'Player card', icon: 'badge', entity: 'player', w: 6, h: 10 },
    shots: { label: 'Shot chart', icon: 'adjust', entity: 'player', w: 5, h: 16 },
    quality: { label: 'Shot quality map', icon: 'hexagon', entity: 'player', w: 5, h: 18 },
    shotmix: { label: 'Shot mix history', icon: 'stacked_bar_chart', entity: 'player', w: 7, h: 14 },
    gamelog: { label: 'Game log', icon: 'event_note', entity: 'player', w: 8, h: 18 },
    tracker: { label: 'Rating Tracker', icon: 'timeline', entity: 'player', w: 8, h: 16 },
    rapm: { label: 'RAPM', icon: 'functions', entity: 'player', w: 8, h: 11 },
    projections: { label: 'Projection', icon: 'trending_up', entity: 'player', w: 6, h: 11 },
    rotation: { label: 'Rotation chart', icon: 'view_timeline', entity: 'team', w: 8, h: 16 },
    assists: { label: 'Assist network', icon: 'hub', entity: 'team', w: 6, h: 18 },
};
export const TOOL_KEYS = Object.keys(TOOLS);
export const TOOL_VIEWS = { shots: ['dots', 'heatmap'], quality: ['expected', 'league'] };
export const SHOT_GAMES = ['regular', 'playoffs', 'all'];

// One fetch per player (or team-season) per tab, shared by every block
// showing it; a failed fetch is forgotten so the next render can retry.
const CACHE_SIZE = 40;
const profiles = new Map();

function cached(key, load) {
    if (profiles.has(key)) {
        const hit = profiles.get(key);
        profiles.delete(key);
        profiles.set(key, hit);
        return hit;
    }
    const p = load().catch((e) => {
        profiles.delete(key);
        throw e;
    });
    profiles.set(key, p);
    while (profiles.size > CACHE_SIZE) profiles.delete(profiles.keys().next().value);
    return p;
}

export const loadPlayerProfile = (id) => cached(`p:${id}`, () => fetchPlayerFullProfile(id));
export const loadTeamProfile = (abbr, season) => cached(`t:${abbr}:${season ?? ''}`, () => fetchTeamProfile(abbr, season ?? undefined));
