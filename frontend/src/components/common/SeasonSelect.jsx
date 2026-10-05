// A labelled season picker ("2025-26" … "2009-10"), newest first. Seasons are end years (2026 = 2025-26).
// Round 8 (R8-037, R8-043): pages used bare number boxes that showed "2025" and opened on an old season;
// pass the range the page's data really covers.
import { seasonLabel } from '../../utils/format';

export default function SeasonSelect({ value, onChange, from, to = 2026, label = 'Season', style }) {
    const seasons = [];
    for (let s = to; s >= from; s--) seasons.push(s);
    return (
        <select className="input-field" value={value} onChange={(e) => onChange(Number(e.target.value))}
            aria-label={label} title={label} style={{ maxWidth: 130, ...style }}>
            {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
        </select>
    );
}
