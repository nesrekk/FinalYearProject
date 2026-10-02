import React, { useEffect, useState } from 'react';
import { fetchClutchWpaLeaderboard } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';
import TeamLogo from './common/TeamLogo';
import SourceBadge from './common/SourceBadge';
import TableExport from './common/TableExport';
import ClutchSplitSection from './ClutchSplitSection';
import { bySign, signed } from '../utils/format';

function wpaColor(v) {
    if (v == null) return 'var(--text-muted)';
    return bySign(v, 2, 'var(--positive)', 'var(--negative)', 'var(--text-secondary)'); // as shown, so 0.00 isn't tinted
}

export default function ClutchWpaSection() {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        (async () => {
            try {
                const res = await fetchClutchWpaLeaderboard(25);
                if (active) setData(res);
            } catch (e) {
                if (active) setError(e?.response?.data?.detail || 'Could not load the clutch WPA leaderboard.');
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, []);

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Clutch-Time Win Probability Added
                    <InfoTooltip label="How this works" title="A real trained model, not a formula guess">
                        A real Logistic Regression win-probability model, trained on real play-by-play (real
                        running score, real game clock, real final winner) from real games across multiple
                        seasons — same library and same interpretable-coefficients approach this project already
                        uses for MVP/DPOY/ROY. WPA per play is the model's real output after the play minus its
                        real output before the play, credited to whichever player made that play. Clutch time
                        uses the NBA's own real definition: final 5 minutes of regulation/OT with the score
                        within 5 points. The real sample size is shown below rather than implied to be the full
                        historical record.
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                {error && <p className="error-message">{error}</p>}
                {data && (
                    <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                        Trained on {data.sample_size_games} real games
                    </p>
                )}
            </div>

            {loading && <Loader />}

            {!loading && data && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <TableExport />
                    <div className="hb-table-wrapper table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Rank</th>
                                    <th>Player</th>
                                    <th>Games</th>
                                    <th>Clutch Plays</th>
                                    <th>Clutch WPA</th>
                                    <th>Season WPA</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.results.map((r) => (
                                    <tr key={r.player_id}>
                                        <td>{r.rank}</td>
                                        <td>
                                            <div className="entity-row">
                                                <PlayerHeadshot playerId={r.player_id} playerName={r.player_name} size={28} />
                                                {r.player_name}
                                                <TeamLogo abbreviation={r.team_abbreviation} size={16} style={{ marginLeft: 6 }} />
                                            </div>
                                        </td>
                                        <td>{r.n_games}</td>
                                        <td>{r.clutch_plays}</td>
                                        <td style={{ color: wpaColor(r.clutch_wpa), fontWeight: 700 }}>
                                            <Icon
                                                name={bySign(r.clutch_wpa, 2, 'arrow_upward', 'arrow_downward', 'remove')}
                                                size="0.9em"
                                                style={{ verticalAlign: 'middle', marginRight: 2 }}
                                            />
                                            {signed(r.clutch_wpa, 2, '-')}
                                        </td>
                                        <td style={{ color: wpaColor(r.total_wpa) }}>
                                            {signed(r.total_wpa, 2, '-')}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            <ClutchSplitSection />
        </div>
    );
}
