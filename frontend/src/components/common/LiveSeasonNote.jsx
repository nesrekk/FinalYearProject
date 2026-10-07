// "2026-27 so far": the line every page shows under a season that is still being played (round 9 step 5).
// Renders nothing for a complete season. It says how far the season is (through <date>, games played of the
// schedule, games a team) and, unless `early={false}`, which numbers are still mostly noise for a typical rotation
// player (Stat Stability's half-signal samples: reliability = n / (n + M), from GET /meta/season).
import { isLiveSeason, seasonInfo, shortDate } from '../../utils/season';
import { seasonLabel } from '../../utils/format';
import { isPlainClick, openPage, pageHref } from '../../utils/useUrlState';

const NOISY = 0.5;

/** The inline form beside a season picker: "so far, through Oct 22 · 34 games". Nothing for a complete season. */
export function LiveSeasonTag({ season }) {
    if (!isLiveSeason(season)) return null;
    const { live } = seasonInfo();
    return (
        <span className="live-tag" title={`${seasonLabel(live.season)} is still being played: ${live.games} of ${live.scheduled ?? '?'} games through ${live.through}. Numbers are season to date.`}>
            so far, through {shortDate(live.through)} · {live.games.toLocaleString()} games
        </span>
    );
}

export default function LiveSeasonNote({ season, early = true, what, style }) {
    if (!isLiveSeason(season)) return null;
    const { live, early: rel } = seasonInfo();
    const perTeam = live.team_games_min === live.team_games_max
        ? `${live.team_games_max} a team`
        : `${live.team_games_min}-${live.team_games_max} a team`;
    const noisy = early ? (rel || []).filter((r) => r.reliability < NOISY) : [];
    const href = pageHref('stability', {});
    return (
        <p className="live-note" role="note" style={style}>
            <strong>{seasonLabel(live.season)} so far</strong>, through {shortDate(live.through)}:{' '}
            {live.games.toLocaleString()}{live.scheduled ? ` of ${live.scheduled.toLocaleString()}` : ''} games played ({perTeam}).
            {what ? ` ${what}` : ''}
            {noisy.length > 0 && (
                <>
                    {' '}Early season: how much of a typical rotation player&apos;s number is signal so far:{' '}
                    {noisy.map((r, i) => (
                        <span key={r.stat} title={`median ${r.median_n} ${r.unit_label} so far; half signal at ${r.stable_n}`}>
                            {i > 0 ? ', ' : ''}{r.label} {Math.round(r.reliability * 100)}%
                        </span>
                    ))}{' '}(n / (n + M),{' '}
                    <a href={href} onClick={(e) => { if (isPlainClick(e)) { e.preventDefault(); openPage('stability', {}); } }}>
                        Stat Stability
                    </a>).
                </>
            )}
        </p>
    );
}
