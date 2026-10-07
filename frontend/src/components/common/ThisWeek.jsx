// The Dashboard's "This week" (round 9 step 5, GET /dashboard/week): the seven days of finals ending on the current
// season's last stored date: the latest night's results, the biggest upset by the held-out pre-game odds (the Best
// Games & Upsets page's rule) and the best game by excitement, each with its Game Replay. Before a season starts it
// shows the latest complete season's last week, labelled as such.
import { useEffect, useState } from 'react';
import { fetchDashboardWeek } from '../../services/api';
import { isPlainClick, pageHref } from '../../utils/useUrlState';
import { shortDate } from '../../utils/season';
import SourceBadge from './SourceBadge';
import TeamLink from './TeamLink';
import TeamLogo from './TeamLogo';
import '../../styles/thisweek.css';

function Link({ page, params = {}, hash, onNavigate, children, className = 'tw-link' }) {
    const href = `${pageHref(page, params)}${hash ? `#${hash}` : ''}`;
    return (
        <a className={className} href={href}
            onClick={(e) => { if (isPlainClick(e) && onNavigate) { e.preventDefault(); onNavigate(page, hash, params); } }}>
            {children}
        </a>
    );
}

function Score({ g }) {
    const homeWon = g.pts_home > g.pts_away;
    return (
        <span className="tw-score">
            <span className="tw-team"><TeamLogo abbreviation={g.away} size={20} /><TeamLink abbr={g.away}>{g.away}</TeamLink></span>
            <span className={`tw-pts${homeWon ? '' : ' tw-win'}`}>{g.pts_away}</span>
            <span className="tw-dash">–</span>
            <span className={`tw-pts${homeWon ? ' tw-win' : ''}`}>{g.pts_home}</span>
            <span className="tw-team"><TeamLink abbr={g.home}>{g.home}</TeamLink><TeamLogo abbreviation={g.home} size={20} /></span>
        </span>
    );
}

const replayParams = (id, g) => {
    const p = { game: id };
    if (g?.peak_t != null && g?.peak_event_id != null) { p.t = g.peak_t; p.ev = g.peak_event_id; }
    return p;
};

export default function ThisWeek({ onNavigate }) {
    const [data, setData] = useState(null);
    const [failed, setFailed] = useState(false);

    useEffect(() => {
        let active = true;
        fetchDashboardWeek().then((d) => { if (active) setData(d); }).catch(() => { if (active) setFailed(true); });
        return () => { active = false; };
    }, []);

    if (failed) return <p className="tw-empty">This week&apos;s games couldn&apos;t load right now.</p>;
    if (!data) return <p className="tw-empty">Loading this week…</p>;
    if (!data.games) return <p className="tw-empty">No finals stored yet.</p>;

    const lastDate = data.results[0]?.date;
    const lastNight = data.results.filter((g) => g.date === lastDate);
    const up = data.biggest_upset;
    const best = data.best_game;
    return (
        <div className="tw">
            <p className="tw-note">
                {data.live ? `${data.label} so far:` : `The last week of ${data.label}'s regular season${data.upcoming ? ` (${data.upcoming.label} starts ${shortDate(data.upcoming.first_date)})` : ''}:`}{' '}
                {data.games.toLocaleString()} game{data.games === 1 ? '' : 's'} from {shortDate(data.from)} to {shortDate(data.through)}.
                <SourceBadge source={data._source} />
            </p>
            <div className="tw-grid">
                <div className="tw-card">
                    <p className="text-eyebrow">Results · {shortDate(lastDate)}</p>
                    <ul className="tw-list">
                        {lastNight.slice(0, 6).map((g) => (
                            <li key={g.game_id}>
                                <Score g={g} />
                                {g.periods > 4 && <span className="tw-ot">{g.periods === 5 ? 'OT' : `${g.periods - 4}OT`}</span>}
                                {g.replay_id && <Link page="analytics" hash="replay" params={{ game: g.replay_id }} onNavigate={onNavigate}>Replay</Link>}
                            </li>
                        ))}
                    </ul>
                    <Link page="scores" params={{ date: lastDate }} onNavigate={onNavigate}>
                        {lastNight.length > 6 ? `All ${lastNight.length} games and box scores` : 'Box scores'}
                    </Link>
                </div>
                <div className="tw-card">
                    <p className="text-eyebrow">Biggest upset</p>
                    {up ? (
                        <>
                            <Score g={up} />
                            <p className="tw-fact">
                                <strong>{up.winner}</strong> won with a {Math.round(up.winner_chance * 100)}% chance before tip-off
                                ({shortDate(up.date)}; the Season Simulator&apos;s pre-game odds, which don&apos;t know who sat).
                            </p>
                            <span className="tw-actions">
                                {up.replay_id && <Link page="analytics" hash="replay" params={{ game: up.replay_id }} onNavigate={onNavigate}>Replay</Link>}
                                <Link page="bestgames" params={{ v: 'upsets', season: data.season }} onNavigate={onNavigate}>All upsets</Link>
                            </span>
                        </>
                    ) : <p className="tw-fact">No favourite lost this week (or no pre-game odds yet).</p>}
                </div>
                <div className="tw-card">
                    <p className="text-eyebrow">Best game</p>
                    {best ? (
                        <>
                            <Score g={best} />
                            <p className="tw-fact">
                                Excitement {best.excitement.toFixed(1)} ({shortDate(best.date)}): {best.lead_changes} lead changes,
                                win-probability swing {best.swing.toFixed(1)}{best.comeback ? `, a ${best.comeback}-point comeback` : ''}.
                            </p>
                            <span className="tw-actions">
                                {best.replay_id && <Link page="analytics" hash="replay" params={replayParams(best.replay_id, best)} onNavigate={onNavigate}>Replay the big moment</Link>}
                                <Link page="bestgames" params={{ season: data.season }} onNavigate={onNavigate}>All games</Link>
                            </span>
                        </>
                    ) : <p className="tw-fact">No game scored for excitement yet.</p>}
                </div>
            </div>
        </div>
    );
}
