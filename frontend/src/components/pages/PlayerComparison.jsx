import React, { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { fetchCompareProfile } from '../../services/api';
import PlayerHeadshot from '../common/PlayerHeadshot';
import TeamLogo from '../common/TeamLogo';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import AutocompleteDropdown from '../common/AutocompleteDropdown';
import NamesakeNote from '../common/NamesakeNote';
import usePlayerSuggestions from '../../utils/usePlayerSuggestions';
import ScoutingReportCard from '../common/ScoutingReportCard';
import { STAT_GLOSSARY } from '../../utils/statGlossary';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';
import TableExport from '../common/TableExport';
import ChartExport from '../common/ChartExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SourceBadge from '../common/SourceBadge';
import SaveViewButton from '../common/SaveViewButton';
import OpenInWorkbenchButton from '../common/OpenInWorkbenchButton';
import { compareBoard } from '../../utils/openInWorkbench';
import { parseParam, pushPage, useInitialParams, useUrlSync } from '../../utils/useUrlState';

// compare-profile covers 2009-10 to the latest finished season.
const LATEST_SEASON = 2026;
const SEASONS = Array.from({ length: LATEST_SEASON - 2010 + 1 }, (_, i) => LATEST_SEASON - i);

// Theme tokens (styles/tokens.css): the Ink colours were 1.6-2.6:1 as text on Paper.
const COLOR_A = 'var(--compare-a)';
const COLOR_B = 'var(--compare-b)';

const TALE_ROWS = [
    { key: 'pts', label: 'Points', min: 0, max: 35, digits: 1 },
    { key: 'reb', label: 'Rebounds', min: 0, max: 15, digits: 1 },
    { key: 'ast', label: 'Assists', min: 0, max: 12, digits: 1 },
    { key: 'ts_pct', label: 'True Shooting %', min: 0.45, max: 0.70, digits: 3, pct: true },
    { key: 'obpm', label: 'Offensive Impact', min: -5, max: 10, digits: 1, signed: true },
    { key: 'dbpm', label: 'Defensive Impact', min: -3, max: 6, digits: 1, signed: true },
    { key: 'bpm', label: 'Overall Impact', min: -6, max: 13, digits: 1, signed: true },
    { key: 'usg_pct', label: 'Usage Rate', min: 0.10, max: 0.38, digits: 3, pct: true },
];

const RADAR_SIZE = 340;
const RADAR_CENTER = RADAR_SIZE / 2;
const RADAR_MAX_R = RADAR_SIZE / 2 - 56;

function fmtVal(v, digits = 1, pct = false, signed = false) {
    if (v == null) return '—';
    if (pct) return `${(v * 100).toFixed(digits === 3 ? 1 : digits)}%`;
    const s = Number(v).toFixed(digits);
    return signed && v > 0 ? `+${s}` : s;
}

function barFraction(value, min, max) {
    if (value == null) return 0;
    return Math.max(0, Math.min(1, (value - min) / (max - min)));
}

function tierColor(percentile) {
    if (percentile == null) return null;
    if (percentile < 33) return 'var(--negative)';
    if (percentile < 66) return 'var(--streak)';
    return 'var(--positive)';
}

function findDetailStat(profile, key) {
    return profile?.detail_stats?.find((s) => s.key === key) ?? null;
}

// Simplified, openly-labeled heuristic — NOT a fitted or validated synergy
// model. Real on-court chemistry depends on lineup context, defensive
// schemes, and shot-clock situations this project has no play-by-play or
// lineup data to measure. What IS real: each flag below is a plain
// percentile-threshold comparison within the same qualified pool used
// everywhere else on this page (min>=15 mpg, gp>=20 that season) — the
// thresholds themselves (75th/70th/50th/25th) are round, disclosed cutoffs
// chosen for readability, not numbers fit to any outcome data.
function computeFitFlags(profileA, profileB) {
    const usgA = findDetailStat(profileA, 'usg_pct')?.percentile;
    const usgB = findDetailStat(profileB, 'usg_pct')?.percentile;
    const tparA = findDetailStat(profileA, 'tpar')?.percentile;
    const tparB = findDetailStat(profileB, 'tpar')?.percentile;
    const astA = findDetailStat(profileA, 'ast_pct')?.percentile;
    const astB = findDetailStat(profileB, 'ast_pct')?.percentile;

    const flags = [];

    if (usgA != null && usgB != null && usgA >= 75 && usgB >= 75) {
        flags.push({
            kind: 'overlap',
            label: 'Ball-Dominance Overlap',
            detail: `Both players rank in the top quartile for usage rate (${profileA.player_name} ${usgA}th, ${profileB.player_name} ${usgB}th) among this season's qualified pool. Two high-usage players sharing the same touches is a real, commonly-cited source of on-court friction — this project can't measure the actual effect on efficiency, only that the raw usage overlap is real.`,
        });
    }

    if (tparA != null && tparB != null && tparA <= 25 && tparB <= 25) {
        flags.push({
            kind: 'overlap',
            label: 'Spacing Overlap',
            detail: `Both players rank in the bottom quartile for 3-point attempt rate (${profileA.player_name} ${tparA}th, ${profileB.player_name} ${tparB}th). Two low-volume shooters occupying the same driving/post lanes is a common real roster-construction concern, though this project has no shot-location or lineup data to confirm actual floor spacing.`,
        });
    }

    const aIsScorer = usgA != null && astA != null && usgA >= 70 && astA < 50;
    const bIsFacilitator = astB != null && usgB != null && astB >= 70 && usgB < 50;
    const bIsScorer = usgB != null && astB != null && usgB >= 70 && astB < 50;
    const aIsFacilitator = astA != null && usgA != null && astA >= 70 && usgA < 50;
    if ((aIsScorer && bIsFacilitator) || (bIsScorer && aIsFacilitator)) {
        const scorer = aIsScorer ? profileA.player_name : profileB.player_name;
        const facilitator = aIsScorer ? profileB.player_name : profileA.player_name;
        flags.push({
            kind: 'complement',
            label: 'Scorer + Facilitator Split',
            detail: `${scorer} profiles as a high-usage, low-assist scorer while ${facilitator} profiles as a high-assist, lower-usage facilitator — a clean statistical role split rather than an overlap.`,
        });
    }

    return flags;
}

function radarAngle(i, total) {
    return -Math.PI / 2 + (i * 2 * Math.PI) / total;
}
function radarPoint(i, total, value0to100) {
    const angle = radarAngle(i, total);
    const r = (Math.max(0, Math.min(100, value0to100)) / 100) * RADAR_MAX_R;
    return { x: RADAR_CENTER + r * Math.cos(angle), y: RADAR_CENTER + r * Math.sin(angle) };
}
function ringPoints(total, fraction) {
    return Array.from({ length: total }, (_, i) => {
        const p = radarPoint(i, total, fraction * 100);
        return `${p.x},${p.y}`;
    }).join(' ');
}

// A typed name picks the latest player of that name; a suggestion carries the id (two players can share a name).
function SearchBox({ placeholder, value, onChange, resolvedName, onPick, color }) {
    const inputRef = useRef(null);
    const sug = usePlayerSuggestions(value, resolvedName, 6);
    return (
        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, flexShrink: 0 }} />
                <input
                    ref={inputRef}
                    type="text"
                    className="input-field"
                    placeholder={placeholder}
                    aria-label={placeholder.replace('…', '')}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' && value.trim()) { sug.dismiss(); onPick({ name: value.trim(), id: null }); }
                    }}
                    style={{ width: '100%' }}
                />
            </div>
            <AutocompleteDropdown anchorRef={inputRef} items={sug.labels}
                onPick={(label) => { const p = sug.pick(label); sug.dismiss(); if (p) onPick({ name: p.name, id: p.id }); }} />
        </div>
    );
}

function fmtInches(v) {
    if (v == null) return null;
    const feet = Math.floor(v / 12);
    const inches = (v % 12).toFixed(1);
    return `${feet}'${inches}"`;
}

function BioCard({ profile, color }) {
    if (!profile) return null;
    const { bio } = profile;
    const cm = bio.combine_measurements;
    const hasMeasurements = cm && (cm.height_wo_shoes != null || cm.wingspan != null);
    return (
        <div className="hb-compare-bio" style={{ borderColor: color }}>
            <PlayerHeadshot playerId={profile.player_id} playerName={profile.player_name} size={72} />
            <div className="entity-row" style={{ marginTop: '0.5rem', justifyContent: 'center' }}>
                <TeamLogo abbreviation={bio.team_abbreviation} size={18} />
                <span className="page-subtitle" style={{ margin: 0 }}>{bio.team_abbreviation}</span>
            </div>
            <div className="hb-compare-bio-name">{profile.player_name}</div>
            <div className="page-subtitle">{bio.position || '—'} · {bio.age ?? '—'} yrs</div>
            <div className="hb-compare-bio-archetype">
                <Icon name="auto_awesome" size="0.85em" />
                {bio.archetype || 'Unclustered'}
            </div>
            {hasMeasurements && (
                <div className="page-subtitle" style={{ marginTop: '0.4rem', fontSize: '0.75rem' }}>
                    {fmtInches(cm.height_wo_shoes) && <>Ht {fmtInches(cm.height_wo_shoes)}</>}
                    {fmtInches(cm.wingspan) && <> · Wing {fmtInches(cm.wingspan)}</>}
                    {cm.weight != null && <> · {cm.weight.toFixed(0)} lbs</>}
                    <InfoTooltip label="Real NBA Draft Combine measurement" title="Real combine measurement">
                        From the real NBA Draft Combine (only available for players who were actually measured there).
                    </InfoTooltip>
                </div>
            )}
        </div>
    );
}

export default function PlayerComparison() {
    const radarRef = useRef(null);
    // A shared link carries ?season=&a=&aid=&b=&bid= (utils/useUrlState.js): aid/bid (NBA ids) pick
    // the players, a/b keep the link readable and alone open the latest player of that name.
    const params = useInitialParams();
    const [season, setSeason] = useState(() => parseParam.int(params, 'season', { min: 2010, max: LATEST_SEASON }) ?? LATEST_SEASON);
    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);

    const [searchA, setSearchA] = useState('');
    const [searchB, setSearchB] = useState('');
    const [profileA, setProfileA] = useState(null);
    const [profileB, setProfileB] = useState(null);
    const [errorA, setErrorA] = useState('');
    const [errorB, setErrorB] = useState('');

    async function loadProfile({ name, id }, setProfile, setError, setSearch) {
        setError('');
        try {
            const data = await fetchCompareProfile(name, season, id || undefined);
            setProfile(data);
            setSearch?.(data.player_name); // the loaded spelling (Jokić for "jokic"), so no stray suggestions
        } catch (e) {
            setProfile(null);
            setError(e?.response?.data?.detail || 'No data for this player/season.');
        }
    }

    function pickA(p) {
        setSearchA(p.name);
        loadProfile(p, setProfileA, setErrorA, setSearchA);
    }
    function pickB(p) {
        setSearchB(p.name);
        loadProfile(p, setProfileB, setErrorB, setSearchB);
    }

    // Open the players named in the link.
    useEffect(() => {
        const a = parseParam.str(params, 'a');
        const b = parseParam.str(params, 'b');
        const aid = parseParam.int(params, 'aid', { min: 1 });
        const bid = parseParam.int(params, 'bid', { min: 1 });
        if (a || aid) pickA({ name: a ?? String(aid), id: aid });
        if (b || bid) pickB({ name: b ?? String(bid), id: bid });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [params]);

    // A linked name stays in the URL while it loads and is dropped if it fails.
    useUrlSync({
        season,
        a: profileA?.player_name ?? (errorA ? null : parseParam.str(params, 'a')),
        aid: profileA?.player_id ?? (errorA ? null : parseParam.int(params, 'aid', { min: 1 })),
        b: profileB?.player_name ?? (errorB ? null : parseParam.str(params, 'b')),
        bid: profileB?.player_id ?? (errorB ? null : parseParam.int(params, 'bid', { min: 1 })),
    });

    // Re-fetch both slots when season changes.
    useEffect(() => {
        if (profileA?.player_name) loadProfile({ name: profileA.player_name, id: profileA.player_id }, setProfileA, setErrorA);
        if (profileB?.player_name) loadProfile({ name: profileB.player_name, id: profileB.player_id }, setProfileB, setErrorB);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [season]);

    const bothLoaded = profileA && profileB;

    return (
        <div className="page fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="compare_arrows" /></span>
                    Player Comparison
                    <InfoTooltip label="How this works" title="Percentiles, not vibes">
                        Skill Profile and every percentile bar here rank a player against the same qualified pool
                        used by Radar Comparison (min≥15 mpg, gp≥20 that season) — 0-100, where 100 means nobody
                        in the pool beat them. "Archetype" is this project's own statistical clustering, shown in
                        place of a scouted offensive/defensive role (there is no scouted role data). Height,
                        wingspan and weight are the NBA Draft Combine's measurements, shown only for players who
                        were measured there; never guessed.
                    </InfoTooltip>
                    <SourceBadge source={profileA?._source ?? profileB?._source} />
                    <CopyLinkButton />
                    <SaveViewButton pageId="compare" />
                    <OpenInWorkbenchButton disabled={!profileA && !profileB}
                        build={() => compareBoard({ season, players: [profileA, profileB].filter(Boolean) })} />
                </h2>
                <p className="page-subtitle">Pick a season and two players to compare their full statistical profile.</p>

                <div className="input-row">
                    <select className="input-field" value={season} onChange={(e) => setSeason(Number(e.target.value))}
                        aria-label="Season" style={{ maxWidth: 130 }}>
                        {SEASONS.map((y) => <option key={y} value={y}>{`${y - 1}-${String(y).slice(-2)}`}</option>)}
                    </select>
                </div>

                <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginTop: '0.75rem' }}>
                    <SearchBox placeholder="Player A…" value={searchA} onChange={setSearchA} resolvedName={profileA?.player_name} onPick={pickA} color={COLOR_A} />
                    <span className="page-subtitle" style={{ alignSelf: 'center' }}>vs</span>
                    <SearchBox placeholder="Player B…" value={searchB} onChange={setSearchB} resolvedName={profileB?.player_name} onPick={pickB} color={COLOR_B} />
                </div>
                <NamesakeNote name={profileA?.player_name} id={profileA?.player_id} onPick={pickA} />
                <NamesakeNote name={profileB?.player_name} id={profileB?.player_id} onPick={pickB} />
                {errorA && <p className="error-message" style={{ marginTop: '0.5rem' }}>{errorA}</p>}
                {errorB && <p className="error-message" style={{ marginTop: '0.5rem' }}>{errorB}</p>}
            </div>

            {bothLoaded && (
                <motion.div
                    key={`${profileA.player_id}-${profileB.player_id}-${season}`}
                    initial={isAdvanced ? { opacity: 0, y: 10 } : false}
                    animate={{ opacity: 1, y: 0 }}
                    transition={preset.tableTransition}
                >
                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <div style={{ display: 'flex', gap: '1.5rem', justifyContent: 'center', flexWrap: 'wrap' }}>
                            <BioCard profile={profileA} color={COLOR_A} />
                            <div style={{ alignSelf: 'center', fontFamily: 'var(--font)', fontWeight: 700, fontSize: '1.3rem', color: 'var(--text-muted)' }}>VS</div>
                            <BioCard profile={profileB} color={COLOR_B} />
                        </div>
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>Tale of the Tape</h3>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                            {TALE_ROWS.map((row) => {
                                const valA = profileA.tale_of_the_tape[row.key];
                                const valB = profileB.tale_of_the_tape[row.key];
                                const winner = valA != null && valB != null ? (valA > valB ? 'A' : valB > valA ? 'B' : null) : null;
                                return (
                                    <div key={row.key} className="hb-tape-row">
                                        <span className="hb-tape-value" style={{ color: winner === 'A' ? COLOR_A : 'var(--text-secondary)' }}>
                                            {winner === 'A' && <Icon name="emoji_events" size="0.85em" />} {fmtVal(valA, row.digits, row.pct, row.signed)}
                                        </span>
                                        <div className="hb-tape-bars">
                                            <div className="hb-tape-bar hb-tape-bar--left">
                                                <div className="hb-tape-bar-fill" style={{ width: `${barFraction(valA, row.min, row.max) * 100}%`, background: COLOR_A }} />
                                            </div>
                                            <span className="hb-tape-label">{row.label}</span>
                                            <div className="hb-tape-bar hb-tape-bar--right">
                                                <div className="hb-tape-bar-fill" style={{ width: `${barFraction(valB, row.min, row.max) * 100}%`, background: COLOR_B }} />
                                            </div>
                                        </div>
                                        <span className="hb-tape-value" style={{ color: winner === 'B' ? COLOR_B : 'var(--text-secondary)' }}>
                                            {fmtVal(valB, row.digits, row.pct, row.signed)} {winner === 'B' && <Icon name="emoji_events" size="0.85em" />}
                                        </span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>Skill Profile</h3>
                        <p className="page-subtitle" style={{ marginTop: '-0.5rem', marginBottom: '1rem' }}>
                            Percentile rank vs. all qualified players this season · pool size {profileA.pool_size}
                        </p>
                        <ChartExport svgRef={radarRef} name={`${profileA.player_name} vs ${profileB.player_name} skill profile`} />
                        <div style={{ display: 'flex', justifyContent: 'center' }}>
                            <svg ref={radarRef} viewBox={`0 0 ${RADAR_SIZE} ${RADAR_SIZE}`} style={{ maxWidth: 420, width: '100%', height: 'auto' }} role="img" aria-label={`Radar chart comparing ${profileA.player_name} and ${profileB.player_name}'s percentile rank against all qualified players this season across ${profileA.skill_profile.length} skill categories`}>
                                <rect x="0" y="0" width={RADAR_SIZE} height={RADAR_SIZE} fill="var(--surface-2)" rx="8" />
                                {[0.25, 0.5, 0.75, 1].map((f) => (
                                    <polygon key={f} points={ringPoints(profileA.skill_profile.length, f)} fill="none" stroke="var(--hairline)" strokeWidth="1" />
                                ))}
                                {profileA.skill_profile.map((axis, i) => {
                                    const outer = radarPoint(i, profileA.skill_profile.length, 100);
                                    const labelPt = radarPoint(i, profileA.skill_profile.length, 122);
                                    return (
                                        <React.Fragment key={axis.key}>
                                            <line x1={RADAR_CENTER} y1={RADAR_CENTER} x2={outer.x} y2={outer.y} stroke="var(--hairline)" strokeWidth="1" />
                                            <text x={labelPt.x} y={labelPt.y} fill="var(--text-2)" fontSize="12" textAnchor="middle" dominantBaseline="middle">{axis.label}</text>
                                        </React.Fragment>
                                    );
                                })}
                                {[{ data: profileA, color: COLOR_A }, { data: profileB, color: COLOR_B }].map(({ data, color }) => {
                                    const points = data.skill_profile.map((axis, i) => {
                                        const p = radarPoint(i, data.skill_profile.length, axis.percentile ?? 0);
                                        return `${p.x},${p.y}`;
                                    }).join(' ');
                                    return <polygon key={data.player_id} points={points} fill={color} fillOpacity={0.18} stroke={color} strokeWidth="2" />;
                                })}
                            </svg>
                        </div>
                        <div className="hb-compare-legend">
                            <span><span className="hb-legend-dot" style={{ background: COLOR_A }} /> {profileA.player_name}</span>
                            <span><span className="hb-legend-dot" style={{ background: COLOR_B }} /> {profileB.player_name}</span>
                        </div>

                        <div className="hb-percentile-grid">
                            {profileA.skill_profile.map((axisA, i) => {
                                const axisB = profileB.skill_profile[i];
                                return (
                                    <div key={axisA.key} className="hb-percentile-card">
                                        <div className="hb-percentile-label">{axisA.label}</div>
                                        <div className="hb-percentile-row">
                                            <span style={{ color: COLOR_A }}>{axisA.percentile ?? '—'}%</span>
                                            <span className="page-subtitle" style={{ margin: '0 0.4rem' }}>vs</span>
                                            <span style={{ color: COLOR_B }}>{axisB.percentile ?? '—'}%</span>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>Scouting Report</h3>
                        <TableExport />
                        <div className="hb-table-wrapper table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Stat</th>
                                        <th style={{ color: COLOR_A }}>{profileA.player_name}</th>
                                        <th style={{ color: COLOR_B }}>{profileB.player_name}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {profileA.detail_stats.map((statA, i) => {
                                        const statB = profileB.detail_stats[i];
                                        const def = STAT_GLOSSARY[statA.key];
                                        return (
                                            <tr key={statA.key}>
                                                <td>
                                                    {statA.label}
                                                    {def && (
                                                        <InfoTooltip label={`What is ${def.title}?`} title={def.title}>
                                                            {def.formula && <><code className="stat-formula">{def.formula}</code><br /></>}
                                                            {def.body}
                                                        </InfoTooltip>
                                                    )}
                                                </td>
                                                {[statA, statB].map((s, idx) => (
                                                    <td key={idx}>
                                                        {fmtVal(s.value, 3, ['ts_pct', 'efg_pct', 'usg_pct', 'ast_pct', 'reb_pct', 'tov_pct', 'oreb_pct', 'ftr', 'tpar'].includes(s.key), false)}
                                                        {s.percentile != null && (
                                                            <span style={{ marginLeft: 6, fontSize: '0.78em', color: tierColor(s.percentile) || 'var(--text-muted)' }}>
                                                                ({s.percentile}th)
                                                            </span>
                                                        )}
                                                    </td>
                                                ))}
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Fit Analysis
                            <InfoTooltip label="How this works" title="A simple heuristic, not a model">
                                The flags below are plain percentile-threshold comparisons (usage rate, 3PA rate,
                                assist rate) within the same qualified pool used elsewhere on this page: not a
                                trained model, a quick starting point for a basketball conversation. How two players
                                actually did together is on Pair Chemistry, from every play-by-play stint.
                            </InfoTooltip>
                        </h3>

                        {/* Pair Synergy (a ridge model on box-score features) was retired 2026-10-06 (R8-032):
                            it was fitted on the old in-house defensive BPM and its cross-validated R² was low. */}
                        <p className="page-subtitle" style={{ marginTop: 0 }}>
                            For how these two did on the floor together, see{' '}
                            <button type="button" className="oo-link" onClick={() => pushPage('analytics', 'pairs', {
                                season,
                                team: bothLoaded && profileA.bio?.team_abbreviation && profileA.bio.team_abbreviation === profileB.bio?.team_abbreviation
                                    && profileA.bio.team_abbreviation !== 'TOT' ? profileA.bio.team_abbreviation : undefined,
                            })}>Pair Chemistry</button>
                            {' '}(every stint from play-by-play, 2020-21 on; the top 2,000 lineups a season before). The
                            model-based Pair Synergy estimate that used to sit here was retired: it was fitted on an older
                            in-house defensive BPM and explained little of how real pairs did.
                        </p>

                        {(() => {
                            const flags = computeFitFlags(profileA, profileB);
                            if (flags.length === 0) {
                                return (
                                    <p className="page-subtitle" style={{ margin: 0 }}>
                                        No major usage/spacing overlap or clean role split detected between these two profiles.
                                    </p>
                                );
                            }
                            return (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                    {flags.map((f) => (
                                        <div
                                            key={f.label}
                                            style={{
                                                padding: '0.75rem 1rem',
                                                borderRadius: 8,
                                                background: f.kind === 'overlap' ? 'rgba(248,113,113,0.08)' : 'rgba(52,211,153,0.08)',
                                                borderLeft: `3px solid ${f.kind === 'overlap' ? '#f87171' : '#34d399'}`,
                                            }}
                                        >
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, marginBottom: 4 }}>
                                                <Icon name={f.kind === 'overlap' ? 'warning' : 'check_circle'} size="1em" style={{ color: f.kind === 'overlap' ? 'var(--negative)' : 'var(--positive)' }} />
                                                {f.label}
                                            </div>
                                            <p className="page-subtitle" style={{ margin: 0 }}>{f.detail}</p>
                                        </div>
                                    ))}
                                </div>
                            );
                        })()}
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
                            {[[profileA, COLOR_A], [profileB, COLOR_B]].map(([p, color]) => (
                                <div key={p.player_id} style={{ flex: '1 1 320px', minWidth: 0, borderTop: `3px solid ${color}`, paddingTop: '0.75rem' }}>
                                    <div style={{ fontWeight: 700, marginBottom: 4 }}>{p.player_name}</div>
                                    <ScoutingReportCard playerName={p.player_name} playerId={p.player_id} season={season} titleClassName="section-heading" />
                                </div>
                            ))}
                        </div>
                    </div>
                </motion.div>
            )}
        </div>
    );
}
