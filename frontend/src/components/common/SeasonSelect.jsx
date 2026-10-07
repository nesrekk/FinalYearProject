// A labelled season picker ("2025-26" … "2009-10"), newest first. Seasons are end years (2026 = 2025-26).
// Round 8 (R8-037, R8-043): pages used bare number boxes that showed "2025" and opened on an old season;
// pass the range the page's data really covers. Round 9 step 5: the newest season offered defaults to the
// app's current season (utils/season.js), and the season being played reads "2026-27 (so far)".
import { seasonLabel } from '../../utils/format';
import { currentSeason, isLiveSeason } from '../../utils/season';

export default function SeasonSelect({ value, onChange, from, to, label = 'Season', style }) {
    const newest = to ?? currentSeason();
    const seasons = [];
    for (let s = newest; s >= from; s--) seasons.push(s);
    return (
        <select className="input-field" value={value} onChange={(e) => onChange(Number(e.target.value))}
            aria-label={label} title={label} style={{ maxWidth: 150, ...style }}>
            {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}{isLiveSeason(s) ? ' (so far)' : ''}</option>)}
        </select>
    );
}
