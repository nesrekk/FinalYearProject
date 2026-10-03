import React, { useEffect, useState } from 'react';
import GameLogBlock from '../common/GameLogBlock';
import RatingTrackerBlock from '../common/RatingTrackerBlock';
import ShotCourt, { HeatmapLegend } from '../common/ShotCourt';
import ShotMixHistory from '../common/ShotMixHistory';
import { QualityPanel } from '../common/ShotQualityMap';
import SourceBadge from '../common/SourceBadge';
import { Glance, Hero, NextSeason, RapmBlock, Section } from '../pages/PlayerProfile';
import { Assists, Rotations } from '../pages/TeamProfile';
import { ProfileEmbedContext, missingReasons } from '../pages/playerProfileShared';
import { fetchPlayerShots } from '../../services/api';
import shotTotals, { SHOT_GAMES as GAMES } from '../../utils/shotTotals';
import { openPlayerProfile, openTeamProfile } from '../../utils/useUrlState';
import { TOOLS, loadPlayerProfile, loadTeamProfile } from '../../utils/workbenchTools';
import { entityWord, seasonLabel, seriesVar, toolMember } from './workbenchShared';
import '../../styles/profile.css';
import '../../styles/teamprofile.css';

// The app's own tools as Workbench blocks (round 7 step 5): each shows one
// member of a set with the component the app already uses for it (player
// profile blocks, the Shot Charts court, the quality map, the team page's
// rotation and assist blocks), which keeps its own data loading. A player's
// blocks read GET /player-profile/{id} once per tab (shared by every block
// showing him) and, where he has no data, show the profile's own "Not on
// file" reason; team blocks read GET /team-profile/{code}?season= the same way.

const loadError = (e, what) => e?.response?.data?.detail || `The ${what} couldn’t load. Is the impact API (port 8002) running?`;

function usePlayerProfile(id) {
    const [res, setRes] = useState(null); // { id, data } | { id, error }
    useEffect(() => {
        let live = true;
        loadPlayerProfile(id).then(
            (data) => live && setRes({ id, data }),
            (e) => live && setRes({ id, error: loadError(e, 'player profile') }),
        );
        return () => { live = false; };
    }, [id]);
    return res?.id === id ? res : null;
}

function useTeamProfile(abbr, season) {
    const key = `${abbr}-${season ?? ''}`;
    const [res, setRes] = useState(null); // { key, data } | { key, error }
    useEffect(() => {
        let live = true;
        loadTeamProfile(abbr, season).then(
            (data) => live && setRes({ key, data }),
            (e) => live && setRes({ key, error: loadError(e, 'team page') }),
        );
        return () => { live = false; };
    }, [abbr, season, key]);
    return res?.key === key ? res : null;
}

function NotOnFile({ who, what, why }) {
    return (
        <div className="wb-tool-missing" role="note">
            <p className="wb-tool-missing-title">Not on file for {who}: {what}</p>
            <p className="wb-meta">{why}</p>
        </div>
    );
}

// ── Shot chart (the Shot Charts page's court) ──────────────────────────

function ShotTool({ d, settings, onSettings }) {
    const p = d.player;
    const have = d.shots.seasons.map((s) => s.season);
    const season = have.includes(settings.season) ? settings.season : have[have.length - 1];
    const view = settings.view || 'dots';
    const games = settings.games || 'regular';
    const key = `${p.player_id}-${season}`;
    const [res, setRes] = useState(null); // { key, data } | { key, error }

    useEffect(() => {
        let live = true;
        fetchPlayerShots(p.player_name, seasonLabel(season), p.player_id).then(
            (data) => live && setRes({ key, data }),
            (e) => live && setRes({ key, error: loadError(e, 'shot chart') }),
        );
        return () => { live = false; };
    }, [p.player_id, p.player_name, season, key]);

    const cur = res?.key === key ? res : null;
    const all = cur?.data?.shots || [];
    const shots = all.filter((s) => GAMES[games][1](String(s.game_id)));
    const t = shotTotals(shots);
    const left = all.length - shots.length;
    const pct = (v) => `${(v * 100).toFixed(1)}%`;
    return (
        <div className="wb-tool-body">
            <div className="wb-tool-controls">
                <label className="wb-field">
                    <span>Season</span>
                    <select className="wb-select" value={season} onChange={(e) => onSettings({ season: Number(e.target.value) })}>
                        {[...d.shots.seasons].reverse().map((s) => (
                            <option key={s.season} value={s.season}>{seasonLabel(s.season)} ({s.fga.toLocaleString()} regular-season shots)</option>
                        ))}
                    </select>
                </label>
                <label className="wb-field">
                    <span>Games</span>
                    <select className="wb-select" value={games} onChange={(e) => onSettings({ games: e.target.value })}>
                        {Object.entries(GAMES).map(([k, [text]]) => <option key={k} value={k}>{text}</option>)}
                    </select>
                </label>
                <div className="wb-field">
                    <span id={`shots-view-${key}`}>Show</span>
                    <div className="wb-seg" role="group" aria-labelledby={`shots-view-${key}`}>
                        {[['dots', 'Every shot'], ['heatmap', 'Heat map']].map(([k, text]) => (
                            <button key={k} type="button" className={`wb-seg-btn${view === k ? ' wb-seg-btn--on' : ''}`} aria-pressed={view === k} onClick={() => onSettings({ view: k })}>{text}</button>
                        ))}
                    </div>
                </div>
            </div>
            {cur?.error && <p className="wb-error" role="alert">{cur.error}</p>}
            {!cur && <p className="wb-meta" role="status">Loading shots…</p>}
            {cur?.data && (
                <>
                    <p className="wb-summary">
                        <strong>{t.attempts.toLocaleString()}</strong> shots ({GAMES[games][0].toLowerCase()}, {seasonLabel(season)}) ·
                        FG {t.attempts ? pct(t.fgPct) : '—'} ({t.makes.toLocaleString()} made) ·
                        3P {t.threeAtt ? pct(t.threePct) : '—'} ({t.threeMake} of {t.threeAtt})
                        {left > 0 && ` · ${left.toLocaleString()} other shots this season left out`}
                        {view === 'dots' && ` · light blue dots went in, red missed${t.attempts > 5000 ? '; the first 5,000 are drawn' : ''}`}
                    </p>
                    {t.attempts === 0 ? (
                        <p className="wb-hint">No {GAMES[games][0].toLowerCase()} shots on file for {p.player_name} in {seasonLabel(season)}.</p>
                    ) : (
                        <>
                            <ShotCourt shots={shots} viewMode={view} playerName={p.player_name} season={seasonLabel(season)} />
                            {view === 'heatmap' && <HeatmapLegend />}
                        </>
                    )}
                </>
            )}
        </div>
    );
}

// ── Quality map (Shot Charts' quality panel) ───────────────────────────

const QUALITY_MIN_SHOTS = 2;

function QualityTool({ d, settings, onSettings }) {
    const p = d.player;
    const min = d.shots.season_min_fga;
    const have = d.shots.seasons.filter((s) => s.fga >= min).map((s) => s.season);
    if (!have.length) {
        return <NotOnFile who={p.player_name} what="Shot quality map" why={`A map needs a season with ${min}+ regular-season shots on file; his most in a season is ${Math.max(...d.shots.seasons.map((s) => s.fga)).toLocaleString()}.`} />;
    }
    const mode = settings.view || 'expected';
    return (
        <div className="wb-tool-body">
            <div className="wb-tool-controls">
                <div className="wb-field">
                    <span id={`qm-mode-${p.player_id}`}>Colour</span>
                    <div className="wb-seg" role="group" aria-labelledby={`qm-mode-${p.player_id}`}>
                        {[['expected', 'FG% − expected'], ['league', 'FG% − league']].map(([k, text]) => (
                            <button key={k} type="button" className={`wb-seg-btn${mode === k ? ' wb-seg-btn--on' : ''}`} aria-pressed={mode === k} onClick={() => onSettings({ view: k })}>{text}</button>
                        ))}
                    </div>
                </div>
            </div>
            <p className="wb-meta">
                Each hexagon is a 2-foot patch of the half court: orange beats the reference, blue falls short, bigger cells
                have more shots (cells with {QUALITY_MIN_SHOTS}+ shots). Regular season; seasons with {min}+ shots.
            </p>
            <QualityPanel name={p.player_name} playerId={p.player_id} season={have.includes(settings.season) ? settings.season : null}
                mode={mode} minShots={QUALITY_MIN_SHOTS} onSeason={(s) => onSettings({ season: s })} />
        </div>
    );
}

// ── One player's tool ──────────────────────────────────────────────────

// The profile's "Not on file" names, per tool.
const MISSING_NAME = { shots: 'Shot zones', quality: 'Shot zones', shotmix: 'Shot zones', gamelog: 'Game log', tracker: 'Rating Tracker', rapm: 'RAPM', projections: 'Next season' };

function hasData(tool, d) {
    switch (tool) {
        case 'shots': case 'quality': case 'shotmix': return d.shots.seasons.length > 0;
        case 'gamelog': return d.game_log.seasons.length > 0;
        case 'tracker': return (d.rating_tracker?.rows.length ?? 0) > 0;
        case 'rapm': return (d.rapm?.rows.length ?? 0) > 0;
        case 'projections': return (d.projections?.rows.length ?? 0) > 0;
        default: return true;
    }
}

function PlayerTool({ tool, member, settings, onSettings, onNavigate }) {
    const res = usePlayerProfile(member.id);
    if (!res) return <p className="wb-meta" role="status">Loading {member.name}…</p>;
    if (res.error) return <p className="wb-error" role="alert">{res.error}</p>;
    const d = res.data;
    const p = d.player;
    if (!hasData(tool, d)) {
        const why = missingReasons(d).find(([what]) => what === MISSING_NAME[tool])?.[1]
            || `Nothing on file for ${p.player_name} here.`;
        return <NotOnFile who={p.player_name} what={TOOLS[tool].label} why={why} />;
    }
    switch (tool) {
        case 'card': {
            // The profile's "at a glance" row for the chosen season (else his latest).
            const rows = d.seasons.rows;
            const row = rows.find((r) => r.season === settings.season) || rows[rows.length - 1];
            return (
                <div className="wb-tool-body wb-tool-card">
                    <Hero data={d} onOpen={() => openPlayerProfile(p.player_id)} />
                    {row && (
                        <>
                            {rows.length > 1 && (
                                <div className="wb-tool-controls">
                                    <label className="wb-field">
                                        <span>Season at a glance</span>
                                        <select className="wb-select" value={row.season} onChange={(e) => onSettings({ season: Number(e.target.value) })}>
                                            {[...new Set(rows.map((r) => r.season))].reverse().map((x) => <option key={x} value={x}>{seasonLabel(x)}</option>)}
                                        </select>
                                    </label>
                                </div>
                            )}
                            <Glance row={row} />
                        </>
                    )}
                </div>
            );
        }
        case 'shots': return <ShotTool d={d} settings={settings} onSettings={onSettings} />;
        case 'quality': return <QualityTool d={d} settings={settings} onSettings={onSettings} />;
        case 'shotmix': return <div className="wb-tool-body"><ShotMixHistory playerName={p.player_name} playerId={p.player_id} /></div>;
        case 'gamelog':
            return (
                <GameLogBlock playerId={p.player_id} seasons={d.game_log.seasons}
                    nbaGp={Object.fromEntries(d.seasons.rows.map((r) => [r.season, r.gp]))}
                    initialSeason={settings.season} onSeasonChange={(s) => onSettings({ season: s })} />
            );
        case 'tracker': return <RatingTrackerBlock block={d.rating_tracker} coverage={d.coverage.rating_tracker} onNavigate={onNavigate} Section={Section} />;
        case 'rapm': return <RapmBlock block={d.rapm} coverage={d.coverage.rapm} onNavigate={onNavigate} />;
        case 'projections': return <NextSeason block={d.projections} player={p} onNavigate={onNavigate} />;
        default: return null;
    }
}

// ── One team's tool (the team page's blocks) ───────────────────────────

const TEAM_PART = { rotation: 'rotations', assists: 'assists' };

function TeamTool({ tool, member, settings, onSettings, onNavigate }) {
    const res = useTeamProfile(member.id, settings.season);
    if (!res) return <p className="wb-meta" role="status">Loading {member.name}…</p>;
    if (res.error) return <p className="wb-error" role="alert">{res.error}</p>;
    const d = res.data;
    const block = d[TEAM_PART[tool]];
    const shown = { season: d.season, abbr: d.abbreviation, onNavigate };
    return (
        <div className="wb-tool-body">
            <div className="wb-tool-controls">
                <label className="wb-field">
                    <span>Season</span>
                    <select className="wb-select" value={d.season} onChange={(e) => onSettings({ season: Number(e.target.value) })}>
                        {[...d.franchise_history].reverse().map((r) => (
                            <option key={r.season} value={r.season}>{seasonLabel(r.season)} · {r.abbreviation}</option>
                        ))}
                    </select>
                </label>
                <button type="button" className="pp-link wb-tool-open" onClick={() => openTeamProfile(d.abbreviation, d.season)}>
                    Open the {seasonLabel(d.season)} {d.team_name} page
                </button>
            </div>
            {block?.available
                ? (tool === 'rotation' ? <Rotations r={block} {...shown} /> : <Assists a={block} {...shown} />)
                : <NotOnFile who={`the ${seasonLabel(d.season)} ${d.team_name}`} what={TOOLS[tool].label} why={block?.reason || 'Not on file for this season.'} />}
            <p className="wb-meta"><SourceBadge source={d._source} /></p>
        </div>
    );
}

// ── The block ──────────────────────────────────────────────────────────

export default function ToolBlock({ block, board, onSettings, onNavigate }) {
    const s = block.settings;
    const info = TOOLS[s.tool];
    const set = board.sets.find((x) => x.id === s.setId) || null;
    const usable = board.sets.filter((x) => x.kind === info.entity);
    const member = toolMember(block, set);
    const pickSet = (id) => onSettings({ setId: id || null, member: null, season: null });

    let problem = '';
    if (!set) problem = usable.length ? `Choose a set of ${entityWord(info.entity)} for this block.` : `Add a Set block with ${entityWord(info.entity)} first; this block shows one of them.`;
    else if (set.kind !== info.entity) problem = `${info.label} shows ${entityWord(info.entity)}, but ${set.name} holds ${entityWord(set.kind)}. Choose another set.`;
    else if (!member) problem = `${set.name} is empty. Add ${entityWord(info.entity)} to it in its Set block.`;

    const Tool = info.entity === 'team' ? TeamTool : PlayerTool;
    return (
        <div className="wb-tool">
            <div className="wb-tool-bar">
                <label className="wb-field">
                    <span>Set</span>
                    <select className="wb-select" value={set?.id || ''} onChange={(e) => pickSet(e.target.value)}>
                        {!set && <option value="">Choose a set</option>}
                        {set && set.kind !== info.entity && <option value={set.id}>{set.name} ({entityWord(set.kind)})</option>}
                        {usable.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.members.length})</option>)}
                    </select>
                </label>
                {set && set.kind === info.entity && set.members.length > 0 && (
                    <label className="wb-field wb-tool-member">
                        <span>{info.entity === 'team' ? 'Team' : 'Player'}</span>
                        <span className="wb-row">
                            <span className="wb-dot" style={{ '--wb-c': seriesVar(member.color) }} aria-hidden="true" />
                            <select className="wb-select" value={String(member.id)}
                                onChange={(e) => onSettings({ member: info.entity === 'team' ? e.target.value : Number(e.target.value), season: null })}>
                                {set.members.map((m) => <option key={m.id} value={String(m.id)}>{m.name}</option>)}
                            </select>
                        </span>
                    </label>
                )}
            </div>
            {problem ? <p className="wb-hint">{problem}</p> : (
                <ProfileEmbedContext.Provider value>
                    <Tool key={`${info.entity}-${member.id}`} tool={s.tool} member={member} settings={s} onSettings={onSettings} onNavigate={onNavigate} />
                </ProfileEmbedContext.Provider>
            )}
        </div>
    );
}
