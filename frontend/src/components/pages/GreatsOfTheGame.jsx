import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { fetchGreats } from '../../services/api';
import { initials } from '../../utils/teamAssets';
import Loader from '../Loader';
import SourceBadge from '../common/SourceBadge';
import '../../styles/greats.css';

const ROW_SPEEDS = [95, 120, 80, 110, 90]; // seconds per loop
const TILES_PER_ROW = 28;
const REVEAL_RADIUS = 230; // px from the cursor at which a face starts to colour in

const photo = (id, size) => `https://cdn.nba.com/headshots/nba/latest/${size}/${id}.png`;
const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;

const SORTS = {
    mvps: { label: 'MVPs', fn: (a, b) => b.mvps - a.mvps || b.all_nba - a.all_nba || b.win_shares - a.win_shares },
    all_nba: { label: 'All-NBA', fn: (a, b) => b.all_nba - a.all_nba || b.win_shares - a.win_shares },
    pts: { label: 'Career points', fn: (a, b) => b.pts - a.pts },
    win_shares: { label: 'Win Shares', fn: (a, b) => b.win_shares - a.win_shares },
    era: { label: 'Era (oldest first)', fn: (a, b) => a.first_season - b.first_season },
};

// Deterministic shuffle so the wall looks the same on every visit.
function shuffled(list, seed) {
    let s = seed;
    const rnd = () => (s = (s * 9301 + 49297) % 233280) / 233280;
    return [...list].map((g) => [rnd(), g]).sort((a, b) => a[0] - b[0]).map(([, g]) => g);
}

function Face({ g, size }) {
    return g.has_photo
        ? <img alt="" loading="lazy" src={photo(g.player_id, size)} />
        : <span className="gg-initials">{initials(g.player_name)}</span>;
}

function Wall({ greats, counts, onOpen }) {
    const wallRef = useRef(null);
    const hoverRef = useRef(null);
    const [hovered, setHovered] = useState(null);

    const rows = useMemo(
        () => ROW_SPEEDS.map((_, r) => shuffled(greats, 7 + r).slice(0, TILES_PER_ROW)),
        [greats]
    );
    const ticker = useMemo(() => greats.map((g) => ({ name: g.player_name, fact: g.facts[0] })), [greats]);

    // Cursor reveal: each tile's --lit (0..1) follows its distance to the cursor.
    // Without a hover-capable pointer, a slow spotlight sweeps the wall instead.
    useEffect(() => {
        const wall = wallRef.current;
        if (!wall) return undefined;
        const tiles = [...wall.querySelectorAll('.gg-tile')];
        const sweep = !window.matchMedia('(hover: hover)').matches;
        let mx = -9999;
        let my = -9999;
        let visible = true;
        let frame = 0;
        const t0 = performance.now();
        const onMove = (e) => {
            mx = e.clientX;
            my = e.clientY;
            if (hoverRef.current) {
                hoverRef.current.style.left = `${e.clientX + 16}px`;
                hoverRef.current.style.top = `${e.clientY + 16}px`;
            }
        };
        const onLeave = () => { mx = -9999; my = -9999; };
        const io = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; });
        io.observe(wall);
        const tick = (now) => {
            if (visible) {
                if (sweep) {
                    const r = wall.getBoundingClientRect();
                    const k = (now - t0) / 4000;
                    mx = r.left + r.width * (0.5 + 0.4 * Math.sin(k));
                    my = r.top + r.height * (0.5 + 0.3 * Math.sin(k * 1.7));
                }
                for (const t of tiles) {
                    const b = t.getBoundingClientRect();
                    const lit = Math.max(0, 1 - Math.hypot(b.left + b.width / 2 - mx, b.top + b.height / 2 - my) / REVEAL_RADIUS);
                    t.style.setProperty('--lit', lit.toFixed(3));
                    t.classList.toggle('is-lit', lit > 0.6);
                }
            }
            frame = requestAnimationFrame(tick);
        };
        wall.addEventListener('pointermove', onMove);
        wall.addEventListener('pointerleave', onLeave);
        frame = requestAnimationFrame(tick);
        return () => {
            cancelAnimationFrame(frame);
            io.disconnect();
            wall.removeEventListener('pointermove', onMove);
            wall.removeEventListener('pointerleave', onLeave);
        };
    }, [rows]);

    return (
        <section className="gg-wall" ref={wallRef} aria-label="Wall of the game's greats">
            <div className="gg-rows">
                {rows.map((row, r) => (
                    <div
                        key={r}
                        className={`gg-row${r % 2 ? ' gg-row--rev' : ''}`}
                        style={{ animationDuration: `${ROW_SPEEDS[r]}s` }}
                    >
                        {[...row, ...row].map((g, i) => (
                            <button
                                type="button"
                                key={`${g.player_id}-${i}`}
                                className="gg-tile"
                                aria-label={g.player_name}
                                tabIndex={i < row.length ? 0 : -1}
                                aria-hidden={i >= row.length || undefined}
                                onPointerEnter={() => setHovered(g)}
                                onPointerLeave={() => setHovered(null)}
                                onFocus={() => setHovered(null)}
                                onClick={() => onOpen(g)}
                            >
                                <Face g={g} size="260x190" />
                            </button>
                        ))}
                    </div>
                ))}
            </div>
            <div className="gg-shade" />
            <div className="gg-title">
                <div className="gg-eyebrow">NBA Hub · Players · All-time</div>
                <h1>Greats<br />of the <span>game</span></h1>
                <p>
                    The {counts.team75} players on the NBA&apos;s 75th Anniversary Team, plus {counts.stars} of today&apos;s
                    stars. Move across the wall to light up a face; click one to open it.
                </p>
                <div className="gg-stickers">
                    <div className="gg-sticker"><b>{counts.total}</b>greats</div>
                    <div className="gg-sticker"><b>{counts.mvps}</b>MVP awards between them</div>
                    <div className="gg-sticker"><b>{counts.all_star.toLocaleString()}</b>All-Star selections</div>
                </div>
            </div>
            <div className="gg-ticker" aria-hidden="true">
                <div className="gg-ticker-track">
                    {[...ticker, ...ticker].map((t, i) => (
                        <span key={i}><b>{t.name}</b>{t.fact}</span>
                    ))}
                </div>
            </div>
            {hovered && createPortal(
                <div className="gg-hovercard" ref={hoverRef}>
                    <b>{hovered.player_name}</b>
                    <small>
                        {seasonLabel(hovered.first_season)} – {seasonLabel(hovered.last_season)} ·{' '}
                        {hovered.mvps ? `${hovered.mvps}× MVP · ` : ''}{hovered.all_star}× All-Star
                    </small>
                </div>,
                document.body
            )}
        </section>
    );
}

function Detail({ g, onClose }) {
    const closeRef = useRef(null);
    useEffect(() => {
        closeRef.current?.focus();
        const onKey = (e) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);

    // Portalled to <body>: the page's fade-in transform would otherwise make
    // this fixed overlay position itself against the page, off screen.
    return createPortal(
        <div className="gg-scrim" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
            <div className="gg-detail" role="dialog" aria-modal="true" aria-label={g.player_name}>
                <div className="gg-detail-photo"><Face g={g} size="1040x760" /></div>
                <div className="gg-detail-info">
                    <button type="button" className="gg-close" ref={closeRef} onClick={onClose}>Close</button>
                    <div className="gg-years" style={{ textTransform: 'uppercase' }}>
                        {g.group}{g.hall_of_fame ? ' · Hall of Fame' : ''}
                    </div>
                    <h2>{g.player_name}</h2>
                    <div className="gg-years">
                        {seasonLabel(g.first_season)} – {seasonLabel(g.last_season)} · {g.position} · {g.games.toLocaleString()} games
                    </div>
                    <dl>
                        <div><dt>Career points</dt><dd>{g.pts.toLocaleString()}</dd></div>
                        <div><dt>Rebounds / assists</dt><dd>{g.reb.toLocaleString()} / {g.ast.toLocaleString()}</dd></div>
                        <div><dt>MVPs</dt><dd>{g.mvps}</dd></div>
                        <div><dt>All-NBA (1st team)</dt><dd>{g.all_nba} ({g.all_nba_first})</dd></div>
                        <div><dt>All-Star</dt><dd>{g.all_star}</dd></div>
                        <div><dt>Win Shares</dt><dd>{g.win_shares}</dd></div>
                        <div><dt>Best season (Win Shares)</dt><dd>{g.peak_season ? `${seasonLabel(g.peak_season)} · ${g.peak_ws}` : '—'}</dd></div>
                        <div><dt>Per game</dt><dd>{g.ppg} / {g.rpg} / {g.apg}</dd></div>
                    </dl>
                    {g.trivia.length > 0 && (
                        <div className="gg-dyk">
                            <h4>Did you know?</h4>
                            <ul>
                                {g.trivia.map((t) => (
                                    <li key={t.text}>
                                        {t.text}
                                        {t.basis === 'source'
                                            ? <a href={t.url} target="_blank" rel="noopener noreferrer">Source</a>
                                            : <span className="gg-from">From the data</span>}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    )}
                    <h4>By the numbers</h4>
                    <ul className="gg-facts">
                        {g.facts.map((f) => <li key={f}>{f}</li>)}
                    </ul>
                    <p className="gg-note">
                        Numbers and &ldquo;From the data&rdquo; trivia are computed from Basketball-Reference: NBA/BAA
                        regular seasons only (ABA seasons not counted), league-leading counts use season totals, and pick
                        numbers are left off before 1966 (the territorial-pick era). Trivia marked Source was checked
                        against the linked article. No championship data is used.
                    </p>
                </div>
            </div>
        </div>,
        document.body
    );
}

export default function GreatsOfTheGame() {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [filter, setFilter] = useState('all');
    const [sort, setSort] = useState('mvps');
    const [open, setOpen] = useState(null);
    const closeDetail = useCallback(() => setOpen(null), []);

    useEffect(() => {
        let active = true;
        fetchGreats()
            .then((d) => { if (active) setData(d); })
            .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load the greats.'); });
        return () => { active = false; };
    }, []);

    const list = useMemo(() => {
        if (!data) return [];
        return data.greats
            .filter((g) => filter === 'all' || g.group === filter)
            .sort(SORTS[sort].fn);
    }, [data, filter, sort]);

    if (error) return <div className="dashboard-card"><p className="error-message">{error}</p></div>;
    if (!data) return <Loader />;
    const { counts } = data;
    const chips = [
        ['all', `All ${counts.total}`],
        ['75th Anniversary Team', `75th Anniversary Team ${counts.team75}`],
        ["Today's star", `Today's stars ${counts.stars}`],
    ];

    return (
        <div className="page fade-in">
            <Wall greats={data.greats} counts={counts} onOpen={setOpen} />
            <div className="gg-body">
                <div className="gg-toolbar">
                    <div className="gg-chips" role="group" aria-label="Show">
                        {chips.map(([key, label]) => (
                            <button
                                type="button"
                                key={key}
                                className={`gg-chip${filter === key ? ' is-on' : ''}`}
                                aria-pressed={filter === key}
                                onClick={() => setFilter(key)}
                            >
                                {label}
                            </button>
                        ))}
                    </div>
                    <label>
                        Sort
                        <select value={sort} onChange={(e) => setSort(e.target.value)}>
                            {Object.entries(SORTS).map(([key, s]) => <option key={key} value={key}>{s.label}</option>)}
                        </select>
                    </label>
                </div>
                <p className="gg-rule">
                    Every number, fact and &ldquo;From the data&rdquo; trivia item is computed from Basketball-Reference data.
                    Today&apos;s stars: {data.stars_rule} <SourceBadge source={data._source} />
                </p>
                <div className="gg-grid">
                    {list.map((g) => (
                        <button type="button" key={g.player_id} className="gg-card" onClick={() => setOpen(g)}>
                            <div className="gg-card-photo">
                                <Face g={g} size="260x190" />
                                <span className="gg-tag">{g.group === "Today's star" ? "Today's star" : '75th team'}</span>
                                {g.trivia.length > 0 && <span className="gg-dyk-flag">Did you know?</span>}
                            </div>
                            <div className="gg-card-body">
                                <h3>{g.player_name}</h3>
                                <div className="gg-years">
                                    {seasonLabel(g.first_season)} – {seasonLabel(g.last_season)} · {g.position} · {g.seasons} seasons
                                </div>
                                <p className="gg-fact">{g.facts[0]}</p>
                                <div className="gg-badges">
                                    {g.mvps > 0 && <span className="gg-badge gg-badge--mvp">{g.mvps}× MVP</span>}
                                    {g.all_nba > 0 && <span className="gg-badge">{g.all_nba}× All-NBA</span>}
                                    <span className="gg-badge">{g.all_star}× All-Star</span>
                                    {g.hall_of_fame && <span className="gg-badge gg-badge--hof">Hall of Fame</span>}
                                </div>
                                <div className="gg-line">
                                    <div><b>{g.ppg}</b><span>PTS</span></div>
                                    <div><b>{g.rpg}</b><span>REB</span></div>
                                    <div><b>{g.apg}</b><span>AST</span></div>
                                    <div><b>{g.win_shares}</b><span>WIN SH.</span></div>
                                </div>
                            </div>
                        </button>
                    ))}
                </div>
            </div>
            {open && <Detail g={open} onClose={closeDetail} />}
        </div>
    );
}
