import React, { useEffect, useRef, useState } from 'react';
import { fetchLearnBasics } from '../../services/api';
import '../../styles/learn.css';

// Court coordinates follow player_shots: tenths of a foot, hoop at (0, 0),
// baseline at y = -47.5, half-court line at y = 422.5 (y grows away from the baseline).
const A3 = Math.acos(220 / 237.5);
const COURT_LINES = [
    'M-250 -47.5 L250 -47.5 L250 422.5 L-250 422.5 Z',
    'M-80 -47.5 L-80 142.5 L80 142.5 L80 -47.5',
    'M-60 -47.5 L-60 142.5 M60 -47.5 L60 142.5',
    arcPath(0, 142.5, 60, 0, Math.PI),
    arcPath(0, 0, 40, 0, Math.PI),
    'M-7.5 0 a7.5 7.5 0 1 0 15 0 a7.5 7.5 0 1 0 -15 0',
    'M-30 -7.5 L30 -7.5',
    'M-220 -47.5 L-220 92.5 M220 -47.5 L220 92.5',
    arcPath(0, 0, 237.5, A3, Math.PI - A3),
    arcPath(0, 422.5, 60, Math.PI, 2 * Math.PI),
];

const INSIDE_ARC = 'M-220 -47.5 L-220 92.5 A237.5 237.5 0 0 0 220 92.5 L220 -47.5 Z';
const RA_CIRCLE = 'M-40 0 a40 40 0 1 0 80 0 a40 40 0 1 0 -80 0';
const ZONE_SHAPES = {
    'Restricted Area': RA_CIRCLE,
    'In The Paint (Non-RA)': `M-80 -47.5 L80 -47.5 L80 142.5 L-80 142.5 Z ${RA_CIRCLE}`,
    'Mid-Range': `${INSIDE_ARC} M-80 -47.5 L80 -47.5 L80 142.5 L-80 142.5 Z`,
    'Corner 3': 'M-250 -47.5 L-220 -47.5 L-220 92.5 L-250 92.5 Z M220 -47.5 L250 -47.5 L250 92.5 L220 92.5 Z',
    'Above the Break 3': 'M-250 -47.5 L250 -47.5 L250 422.5 L-250 422.5 Z M-250 -47.5 L-250 92.5 L-220 92.5 A237.5 237.5 0 0 0 220 92.5 L250 92.5 L250 -47.5 Z',
};
const ZONE_PLAIN = {
    'Restricted Area': 'Right at the rim: layups and dunks.',
    'In The Paint (Non-RA)': 'The painted lane, just outside the rim: floaters and hooks.',
    'Mid-Range': 'Two-pointers from outside the lane: pull-up jumpers.',
    'Corner 3': 'Threes from the corners, the shortest three on the floor.',
    'Above the Break 3': 'Every other three, from the wings and the top of the arc.',
};

const CALLOUTS = [
    { at: [0, 0], tag: [-44, -30], label: 'Hoop: 10 ft off the floor' },
    { at: [0, 142.5], tag: [-100, 175], label: 'Free-throw line: 15 ft from the backboard' },
    { at: [0, 237.5], tag: [0, 290], label: 'Three-point line: 23 ft 9 in from the hoop' },
    { at: [-220, 40], tag: [-176, 20], label: 'Only 22 ft in the corners' },
];

function arcPath(cx, cy, r, a0, a1) {
    const s = [cx + r * Math.cos(a0), cy + r * Math.sin(a0)];
    const e = [cx + r * Math.cos(a1), cy + r * Math.sin(a1)];
    const large = Math.abs(a1 - a0) > Math.PI ? 1 : 0;
    return `M${s[0]} ${s[1]} A${r} ${r} 0 ${large} 1 ${e[0]} ${e[1]}`;
}

const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const num = (v) => (v == null ? '—' : Number(v).toLocaleString());

function useInView(ref) {
    const [seen, setSeen] = useState(false);
    useEffect(() => {
        const el = ref.current;
        if (!el || seen) return undefined;
        const io = new IntersectionObserver(([entry]) => {
            if (entry.isIntersecting) { setSeen(true); io.disconnect(); }
        }, { threshold: 0.25 });
        io.observe(el);
        return () => io.disconnect();
    }, [ref, seen]);
    return seen;
}

function CourtLines({ drawn }) {
    const groupRef = useRef(null);
    useEffect(() => {
        groupRef.current?.querySelectorAll('path').forEach((p) => {
            p.style.setProperty('--l', p.getTotalLength());
        });
    }, []);
    return (
        <g ref={groupRef} className={`lg-lines${drawn ? ' is-drawn' : ''}`}>
            {COURT_LINES.map((d, i) => (
                <path key={i} d={d} style={{ animationDelay: `${i * 0.15}s` }} />
            ))}
        </g>
    );
}

function CourtSpec() {
    const ref = useRef(null);
    const drawn = useInView(ref);
    return (
        <div>
            <svg ref={ref} viewBox="-330 -120 660 560" className="lg-court" role="img" aria-label="Half court with its official dimensions">
                <CourtLines drawn={drawn} />
                <g className={`lg-notes${drawn ? ' is-drawn' : ''}`}>
                    <line className="lg-dim" x1="-250" y1="-85" x2="250" y2="-85" />
                    <text className="lg-dim-label" x="0" y="-95" textAnchor="middle">50 FT WIDE · 47 FT TO HALF COURT</text>
                    <line className="lg-dim" x1="-80" y1="-62" x2="80" y2="-62" />
                    <text className="lg-dim-label" x="0" y="-67" textAnchor="middle">LANE 16 FT</text>
                    {CALLOUTS.map((c, i) => (
                        <g key={c.label} style={{ animationDelay: `${1.8 + i * 0.3}s` }} className="lg-callout">
                            <path d={`M${c.at[0]} ${c.at[1]} L${c.tag[0]} ${c.tag[1]}`} />
                            <circle cx={c.at[0]} cy={c.at[1]} r="4" />
                            <circle className="lg-tag" cx={c.tag[0]} cy={c.tag[1]} r="15" />
                            <text x={c.tag[0]} y={c.tag[1] + 7} textAnchor="middle">{i + 1}</text>
                        </g>
                    ))}
                </g>
            </svg>
            <ol className={`lg-legend${drawn ? ' is-drawn' : ''}`}>
                {CALLOUTS.map((c) => <li key={c.label}>{c.label}</li>)}
            </ol>
        </div>
    );
}

function ZoneCourt({ active, onPick }) {
    const ref = useRef(null);
    const drawn = useInView(ref);
    return (
        <svg ref={ref} viewBox="-270 -70 540 510" className="lg-court lg-court--zones" role="img" aria-label="Half court divided into the five official shot zones">
            <defs>
                <pattern id="lg-hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                    <line x1="0" y1="0" x2="0" y2="10" className="lg-hatch-line" />
                </pattern>
            </defs>
            {Object.entries(ZONE_SHAPES).map(([zone, d]) => (
                <path
                    key={zone}
                    d={d}
                    fillRule="evenodd"
                    className={`lg-zone${active === zone ? ' is-active' : ''}`}
                    onMouseEnter={() => onPick(zone)}
                    onClick={() => onPick(zone)}
                />
            ))}
            <CourtLines drawn={drawn} />
        </svg>
    );
}

export default function LearnTheGame({ onNavigate }) {
    const [data, setData] = useState(null);
    const [failed, setFailed] = useState(false);
    const [active, setActive] = useState('Restricted Area');

    useEffect(() => {
        let alive = true;
        fetchLearnBasics()
            .then((d) => { if (alive) setData(d); })
            .catch(() => { if (alive) setFailed(true); });
        return () => { alive = false; };
    }, []);

    const zones = data?.zones || [];
    const byValue = [...zones].sort((a, b) => (b.points_per_shot ?? 0) - (a.points_per_shot ?? 0));
    const activeZone = zones.find((z) => z.zone === active);
    const maxPps = Math.max(...zones.map((z) => z.points_per_shot || 0), 1.4);
    const season = data?.season_label;
    const p = data?.example_player;
    const perPoss = data?.scoring?.points_per_team_game && data?.pace?.possessions_per_team_game
        ? data.scoring.points_per_team_game / data.pace.possessions_per_team_game
        : null;

    return (
        <div className="lg">
            <div className="lg-sheet">
                <header className="lg-head">
                    <span className="lg-k">Sheet 01 · Learn the game</span>
                    <h1>Basketball,<br />drawn to spec. <span className="lg-hand">in 5 minutes</span></h1>
                    <p className="lg-lede">
                        Everything a first-time fan needs: the court, how scoring works, and why teams shoot where they do.
                        {data ? ` Every league number here is measured from ${num(data.n_shots)} real ${season} shots and games.` : ''}
                    </p>
                    {failed && <p className="lg-warn">The live league numbers couldn&apos;t load right now. The rules below still apply.</p>}
                </header>

                <section className="lg-sec lg-sec--court">
                    <div className="lg-text">
                        <span className="lg-k">01 · The court</span>
                        <h2>Two baskets, five players a side.</h2>
                        <p>Each team defends one basket and attacks the other. This is half the floor: the same markings repeat at the other end.</p>
                        <p>The painted lane, the free-throw line and the three-point arc are the lines you&apos;ll hear about most.</p>
                    </div>
                    <CourtSpec />
                </section>

                <section className="lg-sec">
                    <span className="lg-k">02 · Scoring</span>
                    <h2>Three ways to put up points.</h2>
                    <div className="lg-rules">
                        <div className="lg-rule"><b>2</b><h3>Inside the arc</h3><p>Any made shot with your feet inside the three-point line.</p></div>
                        <div className="lg-rule"><b>3</b><h3>Behind the arc</h3><p>A made shot with both feet behind the three-point line.</p></div>
                        <div className="lg-rule"><b>1</b><h3>Free throws</h3><p>Uncontested shots from the free-throw line, awarded after certain fouls. Each make is one point.</p></div>
                    </div>
                    {data && (
                        <p className="lg-note">In {season}, <b>{pct(data.three_share)}</b> of all {num(data.n_shots)} shots were threes.</p>
                    )}
                </section>

                <section className="lg-sec lg-sec--zones">
                    <div className="lg-text">
                        <span className="lg-k">03 · Where shots come from</span>
                        <h2>Not every shot is worth the same.</h2>
                        <p>
                            Multiply how often a shot goes in by what it&apos;s worth and you get points per shot.
                            That single number explains the modern game. Hover a zone.
                        </p>
                        {activeZone && (
                            <div className="lg-zone-card">
                                <span className="lg-k">{activeZone.zone}</span>
                                <p>{ZONE_PLAIN[activeZone.zone]}</p>
                                <dl>
                                    <div><dt>Made</dt><dd>{pct(activeZone.fg_pct)}</dd></div>
                                    <div><dt>Share of shots</dt><dd>{pct(activeZone.share_of_shots)}</dd></div>
                                    <div><dt>Points per shot</dt><dd>{activeZone.points_per_shot?.toFixed(2)}</dd></div>
                                </dl>
                            </div>
                        )}
                    </div>
                    <ZoneCourt active={active} onPick={setActive} />
                    {zones.length > 0 && (
                        <div className="lg-value">
                            <span className="lg-k">Points per shot, {season} (highest first)</span>
                            {byValue.map((z) => (
                                <button
                                    type="button"
                                    key={z.zone}
                                    className={`lg-bar${active === z.zone ? ' is-active' : ''}`}
                                    onMouseEnter={() => setActive(z.zone)}
                                    onFocus={() => setActive(z.zone)}
                                    onClick={() => setActive(z.zone)}
                                >
                                    <span className="lg-bar-name">{z.zone}</span>
                                    <span className="lg-bar-track"><i style={{ width: `${(z.points_per_shot / maxPps) * 100}%` }} /></span>
                                    <span className="lg-bar-val">{z.points_per_shot?.toFixed(2)}</span>
                                </button>
                            ))}
                            <p className="lg-hand lg-hand--note">That&apos;s why teams hunt layups and threes, and the long two is disappearing.</p>
                            <p className="lg-fine">
                                Zones are assigned by this project&apos;s court-geometry classifier. Checked against the NBA&apos;s own zone
                                totals for {data.zone_classifier_check?.seasons}: shot counts within {pct(data.zone_classifier_check?.max_fga_error)},
                                make rates within {((data.zone_classifier_check?.max_fg_pct_error ?? 0) * 100).toFixed(1)} points.
                            </p>
                        </div>
                    )}
                </section>

                <section className="lg-sec">
                    <span className="lg-k">04 · The clock</span>
                    <h2>48 minutes, 24 seconds at a time.</h2>
                    <div className="lg-specs">
                        <div><b>4 × 12</b><span>Four 12-minute quarters. Ties go to 5-minute overtimes.</span></div>
                        <div><b>24 s</b><span>The shot clock. Shoot and hit the rim within 24 seconds or lose the ball.</span></div>
                        <div><b>{data?.pace?.possessions_per_team_game ?? '—'}</b><span>Possessions per team per game in {season || 'the latest season'}, averaged over {num(data?.pace?.n_games)} real games.</span></div>
                        <div><b>{data?.scoring?.points_per_team_game ? `≈${Math.round(data.scoring.points_per_team_game)}` : '—'}</b><span>Points per team per game (summed from every player&apos;s per-game average, so approximate).</span></div>
                    </div>
                    {perPoss && (
                        <p className="lg-note">So a typical possession is worth about <b>{perPoss.toFixed(2)} points</b>. Compare that with the points-per-shot bars above.</p>
                    )}
                </section>

                {p && (
                    <section className="lg-sec">
                        <span className="lg-k">05 · Reading a stat line</span>
                        <h2>{p.player_name}, {season}.</h2>
                        <p className="lg-sub">The league&apos;s top scorer ({p.team}, {p.gp} games). Here&apos;s what each number means.</p>
                        <div className="lg-statline">
                            <div><b>{p.pts.toFixed(1)}</b><dt>PTS</dt><dd>Points per game.</dd></div>
                            <div><b>{p.reb.toFixed(1)}</b><dt>REB</dt><dd>Rebounds: missed shots grabbed, per game.</dd></div>
                            <div><b>{p.ast.toFixed(1)}</b><dt>AST</dt><dd>Assists: passes that led straight to a teammate&apos;s basket.</dd></div>
                            <div><b>{p.fg3_pct != null ? pct(p.fg3_pct) : '—'}</b><dt>3P%</dt><dd>Share of threes taken that went in.</dd></div>
                            <div><b>{p.ts_pct != null ? pct(p.ts_pct) : '—'}</b><dt>TS%</dt><dd>True shooting: scoring efficiency that counts threes and free throws fairly.</dd></div>
                        </div>
                    </section>
                )}

                <section className="lg-sec lg-sec--go">
                    <span className="lg-k">06 · Now try it</span>
                    <div className="lg-go">
                        <button type="button" onClick={() => onNavigate('shotcharts')}>See any player&apos;s shots →</button>
                        <button type="button" onClick={() => onNavigate('leaders')}>Stat leaders →</button>
                        <button type="button" onClick={() => onNavigate('games')}>Play a daily game →</button>
                    </div>
                </section>

                <div className="lg-titleblock">
                    <div>DRAWING</div><div>NBA HUB · LEARN THE GAME</div>
                    <div>SOURCE</div><div>{data ? data._source.tables.join(', ') : 'postgres / nba_analytics'}</div>
                    <div>RULES</div><div>Official NBA court and game rules</div>
                </div>
            </div>
        </div>
    );
}
