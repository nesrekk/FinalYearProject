import React, { useEffect, useState } from 'react';
import {
    fetchCurrentMeta, fetchMVPPrediction, fetchBacktestOverview,
    fetchShotSeasons, fetchPlayerShots, fetchWpReplayList, fetchWpReplay,
    fetchPlayerTrajectory, fetchWpaValidation,
} from '../../services/api';

// Walk backwards from `startSeason` to find one the award models can
// actually predict on — mirrors DashboardHome's resolveSeasonWithData, since
// /meta/current can report a season ahead of what's been loaded locally.
async function resolveSeasonWithData(startSeason) {
    for (let season = startSeason; season >= startSeason - 3; season--) {
        try {
            const data = await fetchMVPPrediction(season);
            if (data?.results?.length > 0) return { season, mvp: data };
        } catch {
            // try the previous season
        }
    }
    return null;
}

function pct(v) {
    return v == null ? null : `${Math.round(v * 100)}%`;
}

// Fetches everything each chapter needs and picks its real, non-hardcoded
// example (the current MVP favorite, their real shots and trajectory, the
// most recent real replayed game) — a chapter whose data never resolves
// (or whose fetch fails) simply renders its text with no particle shape,
// per the page's data rule. Each chapter's data is merged into state as
// soon as ITS OWN fetch resolves rather than behind one Promise.all: the
// shot-chart and prospect endpoints can fall back to a slow live scrape on
// a cache miss, and that must not hold up the awards/calibration chapters
// which are usually instant.
function useChapterData() {
    const [state, setState] = useState({ status: 'loading' });

    useEffect(() => {
        let active = true;
        const patch = (fields) => { if (active) setState((s) => ({ ...s, ...fields })); };

        (async () => {
            const meta = await fetchCurrentMeta().catch(() => null);
            const startSeason = meta?.season ?? new Date().getFullYear();
            const resolved = await resolveSeasonWithData(startSeason);
            if (!active) return;
            if (!resolved) { patch({ status: 'done' }); return; }

            const { season, mvp } = resolved;
            const top5 = (mvp.results || []).slice(0, 5);
            const favorite = top5[0] || null;
            patch({ status: 'done', season, favorite, top5 });

            fetchBacktestOverview().then((backtest) => {
                const mvpAccuracy = backtest?.awards?.find((a) => a.award?.toLowerCase() === 'mvp')?.top1_accuracy ?? null;
                patch({ mvpAccuracy });
            }).catch(() => {});

            fetchWpaValidation().then((res) => {
                const scopes = res?.scopes || {};
                const calRow = scopes.all_events || Object.values(scopes)[0] || null;
                patch({ calibration: calRow });
            }).catch(() => {});

            fetchWpReplayList(season).then(async (wpList) => {
                const latest = wpList?.games?.length
                    ? [...wpList.games].sort((a, b) => (b.game_date || '').localeCompare(a.game_date || ''))[0]
                    : null;
                if (!latest) return;
                const wpGame = await fetchWpReplay(latest.game_id).catch(() => null);
                patch({ wpGame });
            }).catch(() => {});

            if (favorite) {
                fetchPlayerTrajectory(favorite.player_name, season).then((trajectory) => {
                    patch({ trajectory });
                }).catch(() => {});

                fetchShotSeasons(favorite.player_name).then(async (shots) => {
                    if (!shots?.seasons?.length) return;
                    const latestSeason = Math.max(...shots.seasons);
                    const shotsData = await fetchPlayerShots(favorite.player_name, latestSeason).catch(() => null);
                    if (shotsData) patch({ shots: shotsData });
                }).catch(() => {});
            }
        })();
        return () => { active = false; };
    }, []);

    return state;
}

function Chapter({ id, eyebrow, title, gradientWord, copy, exploreLabel, onExplore, align, children }) {
    return (
        <div className={`chapter chapter--${align}`} data-chapter-id={id}>
            <div className="chapter-text">
                <p className="text-eyebrow">{eyebrow}</p>
                <h2 className="text-display-lg chapter-title">
                    {title}{' '}<span className="text-gradient">{gradientWord}</span>
                </h2>
                <p className="chapter-copy">{copy}</p>
                {onExplore && (
                    <button type="button" className="landing-feature-link chapter-link" data-magnetic onClick={onExplore}>
                        {exploreLabel} &rarr;
                    </button>
                )}
                {children}
            </div>
        </div>
    );
}

export default function FeatureChapters({ onNavigate }) {
    const data = useChapterData();
    const d = data;

    return (
        <div className="chapters">
            <Chapter
                id="awards"
                align="left"
                eyebrow="Models"
                title="Predicts the"
                gradientWord="awards."
                exploreLabel="Explore Awards Race"
                onExplore={() => onNavigate('analytics', 'mvp')}
                copy={
                    d.status === 'done' && d.top5.length
                        ? `Real logistic-regression MVP probability for ${d.season}${d.mvpAccuracy != null ? ` — the model's real top-1 backtest accuracy is ${pct(d.mvpAccuracy)}` : ''}.`
                        : 'Real logistic-regression probability for MVP, DPOY, ROY and All-NBA — validated against every real past season, not eyeballed.'
                }
            >
                {d.status === 'done' && d.top5.length > 0 && (
                    <ol className="chapter-legend">
                        {d.top5.map((r) => (
                            <li key={r.player_id}>
                                <span>{r.rank}. {r.player_name}</span>
                                <span>{pct(r.mvp_probability)}</span>
                            </li>
                        ))}
                    </ol>
                )}
            </Chapter>

            <Chapter
                id="shots"
                align="right"
                eyebrow="Player Analysis"
                title="Sees every"
                gradientWord="shot."
                exploreLabel="Explore Shot Charts"
                onExplore={() => onNavigate('analytics', 'shots')}
                copy={
                    d.status === 'done' && d.shots?.shots?.length
                        ? `${d.favorite?.player_name}'s real ${d.shots.season} shot locations — made in orange, missed in blue.`
                        : "A featured star's real shot locations for their current season — made in orange, missed in blue."
                }
            >
                {d.status === 'done' && d.shots?.shots?.length > 0 && (
                    <p className="chapter-legend-inline">{d.shots.shots.length.toLocaleString()} real logged shots</p>
                )}
            </Chapter>

            <Chapter
                id="wpa"
                align="left"
                eyebrow="Player Analysis"
                title="Feels the"
                gradientWord="pressure."
                exploreLabel="Explore Win-Probability Replay"
                onExplore={() => onNavigate('analytics', 'wpa')}
                copy={
                    d.status === 'done' && d.wpGame
                        ? `${d.wpGame.away_team} at ${d.wpGame.home_team}, ${d.wpGame.game_date} — every real play replayed through the real win-probability model.`
                        : 'Every real play of a real game, replayed through the same real model — click any missed shot for a real counterfactual.'
                }
            >
                {d.status === 'done' && d.wpGame?.top_plays?.[0] && (
                    <p className="chapter-legend-inline">
                        Biggest swing: {d.wpGame.top_plays[0].description} ({d.wpGame.top_plays[0].wpa > 0 ? '+' : ''}{pct(d.wpGame.top_plays[0].wpa)} win prob)
                    </p>
                )}
            </Chapter>

            <Chapter
                id="trajectory"
                align="right"
                eyebrow="Player Analysis"
                title="Knows the future isn't"
                gradientWord="certain."
                exploreLabel="Explore Career Trajectory"
                onExplore={() => onNavigate('analytics', 'trajectory')}
                copy={
                    d.status === 'done' && d.trajectory
                        ? `${d.favorite?.player_name}'s real aging curve, projected from ${d.trajectory.comps?.length ?? 0} real similar-player comps — shown as an honest ceiling/floor range, not a single confident line.`
                        : 'Real similar-player comps project a real aging curve, shown as an honest uncertainty cone rather than a single confident line.'
                }
            />

            <Chapter
                id="prospects"
                align="left"
                eyebrow="Prospects"
                title="Finds the next"
                gradientWord="star."
                exploreLabel="Explore Draft Prospect Comps"
                onExplore={() => onNavigate('analytics', 'prospects')}
                copy="Real D1 college production matched to real NBA rookie outcomes across ~105,000 real college player-seasons — search any real draft prospect."
            />

            <Chapter
                id="calibration"
                align="right"
                eyebrow="Model Validation"
                title="Grades"
                gradientWord="itself."
                exploreLabel="Explore Model Validation"
                onExplore={() => onNavigate('analytics', 'validation')}
                copy={
                    d.status === 'done' && d.calibration
                        ? `Real held-out win-probability calibration — ${d.calibration.n_events?.toLocaleString()} real events, ROC-AUC ${d.calibration.roc_auc?.toFixed(2)}. A well-calibrated model's points sit near the diagonal.`
                        : "Every predictive model is backtested against real held-out seasons — a well-calibrated model's points sit near the diagonal."
                }
            />
        </div>
    );
}
