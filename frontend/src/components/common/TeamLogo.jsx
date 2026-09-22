import React, { useState } from 'react';
import { getTeamLogoUrl } from '../../utils/teamAssets';

export default function TeamLogo({ abbreviation, size = 28, className = '', style = {} }) {
    const [failed, setFailed] = useState(false);
    const url = getTeamLogoUrl(abbreviation);

    if (!url || failed) {
        return (
            <span
                className={`team-logo-fallback ${className}`}
                style={{ width: size, height: size, fontSize: size * 0.32, ...style }}
            >
                {abbreviation || '—'}
            </span>
        );
    }

    return (
        <img
            src={url}
            alt={abbreviation}
            className={`team-logo-img ${className}`}
            style={{ width: size, height: size, ...style }}
            onError={() => setFailed(true)}
            loading="lazy"
        />
    );
}
