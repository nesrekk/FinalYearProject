import { createContext } from 'react';

// Shared by the player profile (pages/PlayerProfile.jsx) and the Workbench's
// tool blocks, which show single profile blocks from the same
// GET /player-profile/{id} response.

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;
const span = (c) => `${label(c.from)} to ${label(c.to)}`;

// A profile Section inside a Workbench block: no page anchor, no card of its own.
export const ProfileEmbedContext = createContext(false);

// [2006, 2007, 2009] -> "2005-06 to 2006-07, 2008-09"
export function seasonRanges(seasons) {
    const out = [];
    for (const s of seasons) {
        const last = out[out.length - 1];
        if (last && s === last[1] + 1) last[1] = s;
        else out.push([s, s]);
    }
    return out.map(([a, b]) => (a === b ? label(a) : `${label(a)} to ${label(b)}`)).join(', ');
}

// Why each block is missing for this player, in plain words.
export function missingReasons(d) {
    const p = d.player;
    const cov = d.coverage;
    const endedBefore = (from) => p.last_season < from;
    const career = `${p.player_name}'s career ended in ${label(p.last_season)}`;
    const out = [];
    if (!d.awards.rows.length && !p.greats) {
        out.push(['Awards', `No awards, All-league teams or All-Star selections on record (season awards through ${label(cov.awards.to)}).`]);
    }
    if (!d.shots.seasons.length) {
        out.push(['Shot zones', endedBefore(cov.shots.from)
            ? `Shot locations start in ${label(cov.shots.from)}; ${career}.`
            : 'No regular-season shot locations stored for him.']);
    }
    if (!d.shot_making.rows.length) {
        out.push(['Shot-making', endedBefore(cov.shot_making.from)
            ? `Shot locations start in ${label(cov.shot_making.from)}; ${career}.`
            : 'No regular-season shots on file for him.']);
    }
    if (!d.scouting.seasons.length) {
        out.push(['Scouting report', endedBefore(cov.scouting.from)
            ? `Play-type data starts in ${label(cov.scouting.from)}; ${career}.`
            : 'Needs a season with 1,500+ minutes and play-type data (2012-13 on).']);
    }
    if (!d.defense.rows.length) {
        out.push(['Defense (DAD Index)', endedBefore(cov.defense.from)
            ? `Matchup tracking starts in ${label(cov.defense.from)}; ${career}.`
            : `No tracked matchup season on file (${span(cov.defense)}).`]);
    }
    if (!d.gravity.rows.length) {
        out.push(['Shooting gravity', endedBefore(cov.gravity.from)
            ? `Shooting tracking starts in ${label(cov.gravity.from)}; ${career}.`
            : `Needs a season with 500+ minutes, ${span(cov.gravity)}.`]);
    }
    if (!d.contracts.rows.length) {
        const lastSalary = cov.contracts.included[cov.contracts.included.length - 1];
        let why = `No matched salary in the seasons with reliable salary data (${seasonRanges(cov.contracts.included)}).`;
        if (endedBefore(cov.contracts.from)) why = `Salary data starts in ${label(cov.contracts.from)}; ${career}.`;
        else if (p.first_season > lastSalary) {
            why = `The latest season with reliable salary data is ${label(lastSalary)}; ${p.player_name} started in ${label(p.first_season)}.`;
        }
        out.push(['Contract value', why]);
    }
    if (!d.clutch) {
        out.push(['Clutch', endedBefore(cov.clutch.from)
            ? `Play-by-play covers ${span(cov.clutch)}; ${career}.`
            : `No clutch plays in the play-by-play (${span(cov.clutch)}).`]);
    }
    if (d.projections && !d.projections.rows.length) {
        const windowMinutes = d.seasons.rows.filter((r) => r.season >= cov.seasons.to - 2)
            .reduce((sum, r) => sum + (r.gp || 0) * (r.min || 0), 0);
        out.push(['Next season', p.active
            ? `No projection: ${Math.round(windowMinutes).toLocaleString()} minutes over the last three seasons, under the 250-minute floor (regressing so few minutes toward the league average would invent a role).`
            : `Projections are made for players with a ${label(cov.seasons.to)} season; ${career}.`]);
    }
    if (d.on_off && !d.on_off.rows.length) {
        out.push(['On/off', endedBefore(cov.on_off.from)
            ? `Play-by-play lines cover ${span(cov.on_off)}; ${career}.`
            : `No game with on-court minutes in the play-by-play lines (${span(cov.on_off)}).`]);
    }
    if (d.rim_deterrence && !d.rim_deterrence.rows.length && cov.rim_deterrence?.from) {
        out.push(['Rim deterrence', endedBefore(cov.rim_deterrence.from)
            ? `Five-man stints from play-by-play cover ${span(cov.rim_deterrence)}; ${career}.`
            : `No tracked stint with him on the floor (${span(cov.rim_deterrence)}): his minutes fall in games or stints the play-by-play couldn't place.`]);
    }
    if (d.rapm && !d.rapm.rows.length && cov.rapm?.from) {
        out.push(['RAPM', endedBefore(cov.rapm.from)
            ? `Five-man stints from play-by-play cover ${span(cov.rapm)}; ${career}.`
            : `No tracked stint with him on the floor (${span(cov.rapm)}): his minutes fall in games or stints the play-by-play couldn't place.`]);
    }
    if (d.rating_tracker && !d.rating_tracker.rows.length && cov.rating_tracker?.from) {
        out.push(['Rating Tracker', endedBefore(cov.rating_tracker.from)
            ? `Five-man stints from play-by-play cover ${span(cov.rating_tracker)}; ${career}.`
            : `No tracked stint with him on the floor (${span(cov.rating_tracker)}): the tracker only rates seasons with stints.`]);
    }
    if (d.assists && !d.assists.seasons.length && cov.assists?.from) {
        out.push(['Assists', endedBefore(cov.assists.from)
            ? `Assists by passer and scorer come from play-by-play, ${span(cov.assists)}; ${career}.`
            : `No made shot or assist of his in the play-by-play (${span(cov.assists)}).`]);
    }
    if (d.situational_splits && !d.situational_splits.seasons.length) {
        out.push(['Situational splits', endedBefore(cov.game_lines.from)
            ? `Game-by-game lines start in ${label(cov.game_lines.from)} (rebuilt from ESPN play-by-play); ${career}.`
            : `Needs a season with 3+ games on both sides of a split (home and away, say) in the play-by-play lines (${span(cov.game_lines)}).`]);
    }
    if (d.possessions && !d.possessions.seasons.length && cov.possessions?.from) {
        out.push(['Possessions', endedBefore(cov.possessions.from)
            ? `Possessions from play-by-play cover ${span(cov.possessions)}; ${career}.`
            : `No tracked stint with this player on the floor (${span(cov.possessions)}): the minutes fall in games or stints the play-by-play couldn't place.`]);
    }
    if (!d.similarity.seasons.length) {
        out.push(['Similar seasons', endedBefore(2010)
            ? `Needs usage, net rating, assist % and rebound %, recorded from 2009-10 on; ${career}.`
            : 'No season with all eight similarity inputs.']);
    }
    if (!d.game_log.seasons.length) {
        out.push(['Game log', endedBefore(cov.game_lines.from)
            ? `Game-by-game lines start in ${label(cov.game_lines.from)} (rebuilt from ESPN play-by-play); ${career}.`
            : `No regular-season games in the play-by-play for him (${span(cov.game_lines)}).`]);
    }
    if (!d.breakouts.flags.length) {
        out.push(['Breakouts', p.n_seasons === 1
            ? 'A breakout compares two seasons in a row; this is his first.'
            : `Never in the Breakout Detector's top ${d.breakouts.top} jumps or drops (needs ${d.breakouts.min_gp}+ games and ${d.breakouts.min_mpg}+ minutes in back-to-back seasons).`]);
    }
    return out;
}
