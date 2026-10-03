// "Open in Workbench" (round 7 step 5): a page hands its players, teams and
// stats to a new board, saved in this browser like any other board, and the
// Workbench opens on it. Every board here goes through cleanBoard() like an
// imported file, and every number on it is fetched live by its blocks, so a
// board says only what the page's inputs were; it never copies a number.

import { openPage } from './useUrlState';
import { placeNew } from './workbenchLayout';
import { cleanBoard, saveNewBoard } from './workbenchStore';
import { TOOLS } from './workbenchTools';

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;
const range = (a, b) => (a === b ? label(a) : `${label(a)} to ${label(b)}`);

// Blocks listed in reading order, each placed like a block added by hand.
function board(name, sets, list) {
    const blocks = [];
    list.filter(Boolean).forEach((b, i) => {
        const w = b.w ?? (b.type === 'tool' ? TOOLS[b.settings.tool].w : 6);
        const h = b.h ?? (b.type === 'tool' ? TOOLS[b.settings.tool].h : 8);
        blocks.push({ id: `b${i}`, type: b.type, title: b.title || '', ...placeNew(blocks, w, h), settings: b.settings });
    });
    return { name, sets, blocks };
}

const note = (text) => ({ type: 'note', w: 12, h: 3, settings: { text } });
const setBlock = (setId) => ({ type: 'set', w: 4, h: 7, settings: { setId } });
// A tool block (utils/workbenchTools.js) for one member; `size` overrides the tool's own { w, h }.
const tool = (name, setId, member, settings = {}, size = {}) => ({ type: 'tool', ...size, settings: { tool: name, setId, member, ...settings } });

export async function openInWorkbench(raw) {
    const { board: b } = cleanBoard(raw);
    await saveNewBoard(b);
    openPage('workbench', { b: b.id });
    return b;
}

// The Leaderboard Builder's single-stat ranking: the players in its shown
// rows (in rank order), a table of their seasons in its range with its stat
// first, and a chart of that stat.
export function leaderboardBoard({ stat, statLabel, from, to, minGp, minMpg, minAttempts, attemptsLabel, team, order, results }) {
    const members = [];
    for (const r of results) {
        if (!members.some((m) => m.id === r.player_id)) members.push({ id: r.player_id, name: r.player_name, color: members.length % 8 });
    }
    // The season table's stored age has two conventions; the Workbench offers age on Feb 1 instead.
    const key = stat === 'age' ? 'age_feb1' : stat;
    const columns = [...new Set([key, 'gp', 'min', 'pts', 'reb', 'ast', 'ts_pct'])];
    const s = { id: 's0', name: `${statLabel} leaders`.slice(0, 60), kind: 'player', members };
    const floors = [`${minGp || 0}+ games`, `${minMpg || 0}+ minutes a game`, minAttempts ? `${minAttempts}+ ${attemptsLabel} a game` : null, team || null]
        .filter(Boolean).join(', ');
    const text = `From the Leaderboard Builder: the top ${results.length} player-seasons by ${statLabel}, ${range(from, to)} (${floors}), `
        + `${order === 'low' ? 'lowest' : 'highest'} first. The set holds the ${members.length} players in them. The table lists every season `
        + `of theirs in that range with ${minGp || 0}+ games${minMpg || minAttempts || team ? '; its minutes, attempts and team floors aren’t table settings, so it can also list seasons the leaderboard left out' : ''}.`
        + (stat === 'age' ? ' Age here is age on February 1 (birth dates); the leaderboard ranks the season table’s stored age, which mixes two conventions.' : '');
    return board(`${statLabel} leaders, ${range(from, to)}`.slice(0, 120), [s], [
        note(text),
        setBlock('s0'),
        {
            type: 'table', w: 8, h: 10,
            settings: {
                dataset: 'player_season', setId: 's0', columns, seasonFrom: from, seasonTo: to, groupBy: 'none', per: 'game',
                sort: [{ key, dir: order === 'low' ? 'asc' : 'desc' }], limit: 100, minGames: minGp || null,
            },
        },
        {
            type: 'chart', w: 12, h: 11,
            settings: {
                dataset: 'player_season', setId: 's0', chart: from === to ? 'bar' : 'line', y: key, seasonFrom: from, seasonTo: to,
                per: 'game', minGames: minGp || null, context: true,
            },
        },
    ]);
}

// Player Comparison: the two players, a card each, their season side by side
// and their BPM over the ten seasons up to it.
export function compareBoard({ season, players }) {
    const members = players.map((p, i) => ({ id: p.player_id, name: p.player_name, color: i }));
    const s = { id: 's0', name: members.map((m) => m.name.split(' ').slice(-1)[0]).join(' vs ').slice(0, 60), kind: 'player', members };
    return board(`${members.map((m) => m.name).join(' vs ')}, ${label(season)}`.slice(0, 120), [s], [
        setBlock('s0'),
        ...members.map((m) => tool('card', 's0', m.id, { season }, { w: 4, h: 13 })),
        {
            type: 'table', w: 12, h: 5,
            settings: {
                dataset: 'player_season', setId: 's0', columns: ['gp', 'min', 'pts', 'reb', 'ast', 'stl', 'blk', 'tov', 'ts_pct', 'usg_pct', 'bpm'],
                seasonFrom: season, seasonTo: season, groupBy: 'none', per: 'game', sort: [], limit: 25,
            },
        },
        { type: 'chart', w: 12, h: 11, settings: { dataset: 'player_season', setId: 's0', chart: 'line', y: 'bpm', seasonFrom: season - 9, seasonTo: season, per: 'game', context: true } },
    ]);
}

// The player profile: his card, his seasons, the tools he has data for.
export function playerBoard(d) {
    const p = d.player;
    const s = { id: 's0', name: p.player_name.slice(0, 60), kind: 'player', members: [{ id: p.player_id, name: p.player_name, color: 0 }] };
    const has = {
        shots: d.shots.seasons.length > 0,
        gamelog: d.game_log.seasons.length > 0,
        rapm: (d.rapm?.rows.length ?? 0) > 0,
        projections: (d.projections?.rows.length ?? 0) > 0,
    };
    return board(p.player_name.slice(0, 120), [s], [
        tool('card', 's0', p.player_id, {}, { w: 8 }),
        setBlock('s0'),
        {
            type: 'table', w: 12, h: 9,
            settings: {
                dataset: 'player_season', setId: 's0', columns: ['gp', 'min', 'pts', 'reb', 'ast', 'ts_pct', 'usg_pct', 'bpm'],
                seasonFrom: p.first_season, seasonTo: p.last_season, groupBy: 'none', per: 'game', sort: [], limit: 50,
            },
        },
        { type: 'chart', w: has.shots ? 7 : 12, h: 11, settings: { dataset: 'player_season', setId: 's0', chart: 'line', y: 'pts', seasonFrom: p.first_season, seasonTo: p.last_season, per: 'game', context: true } },
        has.shots && tool('shots', 's0', p.player_id),
        has.gamelog && tool('gamelog', 's0', p.player_id, {}, { w: 12 }),
        has.rapm && tool('rapm', 's0', p.player_id, {}, { w: 12 }),
        has.projections && tool('projections', 's0', p.player_id),
    ]);
}

// The team page: the franchise, its seasons, and that season's rotation and
// assist network where the play-by-play has them.
export function teamBoard(d) {
    const s = { id: 's0', name: d.team_name.slice(0, 60), kind: 'team', members: [{ id: d.franchise, name: d.team_name, color: 0 }] };
    return board(`${d.team_name}, ${label(d.season)}`.slice(0, 120), [s], [
        setBlock('s0'),
        {
            type: 'table', w: 8, h: 9,
            settings: {
                dataset: 'team_season', setId: 's0', columns: ['w', 'l', 'srs', 'o_rtg', 'd_rtg', 'n_rtg', 'pace'],
                seasonFrom: Math.max(1947, d.season - 9), seasonTo: d.season, groupBy: 'none', per: 'game', sort: [], limit: 25,
            },
        },
        { type: 'chart', w: 12, h: 11, settings: { dataset: 'team_season', setId: 's0', chart: 'line', y: 'n_rtg', seasonFrom: Math.max(1947, d.season - 9), seasonTo: d.season, per: 'game', context: true } },
        d.rotations?.available && tool('rotation', 's0', d.franchise, { season: d.season }),
        d.assists?.available && tool('assists', 's0', d.franchise, { season: d.season }),
    ]);
}
