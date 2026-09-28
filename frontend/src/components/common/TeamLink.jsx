import React from 'react';
import TeamLogo from './TeamLogo';
import { isPlainClick, openTeamProfile, teamProfileHref } from '../../utils/useUrlState';
import '../../styles/teamprofile.css';

// Link to a team's page (?page=team&abbr=…&season=…). Wraps whatever it's
// given (a logo, a name); with no children it renders logo + abbreviation.
// `season` (end year) opens that season; without it, the team's latest.
// Codes that aren't a team (league rows, "2TM") render as plain text.
export default function TeamLink({ abbr, season, children, logoSize = 20, className = '' }) {
    const content = children ?? (
        <>
            <TeamLogo abbreviation={abbr} size={logoSize} />
            <span>{abbr}</span>
        </>
    );
    if (!abbr || /^([0-9]TM|TOT)$/.test(abbr)) return <span className={`team-link ${className}`}>{content}</span>;
    const onClick = (e) => {
        e.stopPropagation(); // rows with their own click handler
        if (!isPlainClick(e)) return;
        e.preventDefault();
        openTeamProfile(abbr, season);
    };
    return (
        <a className={`team-link ${className}`} href={teamProfileHref(abbr, season)} onClick={onClick}
            title={`Open the ${abbr} team page${season ? ` for ${season - 1}-${String(season).slice(-2)}` : ''}`}>
            {content}
        </a>
    );
}
