// Official team IDs from nba_api's static teams list (bundled with the
// library, not a live fetch — verified against nba_api.stats.static.teams).
// Used to build NBA's own public CDN URLs for logos/headshots — no new
// data pipeline, just image URLs built from data already in the DB
// (team_abbreviation, player_id).
export const TEAM_IDS = {
    ATL: 1610612737, BKN: 1610612751, BOS: 1610612738, CHA: 1610612766,
    CHI: 1610612741, CLE: 1610612739, DAL: 1610612742, DEN: 1610612743,
    DET: 1610612765, GSW: 1610612744, HOU: 1610612745, IND: 1610612754,
    LAC: 1610612746, LAL: 1610612747, MEM: 1610612763, MIA: 1610612748,
    MIL: 1610612749, MIN: 1610612750, NOP: 1610612740, NYK: 1610612752,
    OKC: 1610612760, ORL: 1610612753, PHI: 1610612755, PHX: 1610612756,
    POR: 1610612757, SAC: 1610612758, SAS: 1610612759, TOR: 1610612761,
    UTA: 1610612762, WAS: 1610612764,
};

// Full official name -> abbreviation. Needed because /meta/current's live
// standings fetch (impact_api.py) returns the team's full name reliably
// but an empty "abbr" field (a real, pre-existing gap in that upstream
// data source, not something introduced here) — this recovers the
// abbreviation from the name so logos can still resolve.
export const TEAM_NAME_TO_ABBR = {
    'Atlanta Hawks': 'ATL', 'Boston Celtics': 'BOS', 'Brooklyn Nets': 'BKN',
    'Charlotte Hornets': 'CHA', 'Chicago Bulls': 'CHI', 'Cleveland Cavaliers': 'CLE',
    'Dallas Mavericks': 'DAL', 'Denver Nuggets': 'DEN', 'Detroit Pistons': 'DET',
    'Golden State Warriors': 'GSW', 'Houston Rockets': 'HOU', 'Indiana Pacers': 'IND',
    'Los Angeles Clippers': 'LAC', 'Los Angeles Lakers': 'LAL', 'Memphis Grizzlies': 'MEM',
    'Miami Heat': 'MIA', 'Milwaukee Bucks': 'MIL', 'Minnesota Timberwolves': 'MIN',
    'New Orleans Pelicans': 'NOP', 'New York Knicks': 'NYK', 'Oklahoma City Thunder': 'OKC',
    'Orlando Magic': 'ORL', 'Philadelphia 76ers': 'PHI', 'Phoenix Suns': 'PHX',
    'Portland Trail Blazers': 'POR', 'Sacramento Kings': 'SAC', 'San Antonio Spurs': 'SAS',
    'Toronto Raptors': 'TOR', 'Utah Jazz': 'UTA', 'Washington Wizards': 'WAS',
};

// Official team brand colors (hex). Real, static reference data — not
// fetched, doesn't change season to season.
export const TEAM_COLORS = {
    ATL: '#E03A3E', BOS: '#007A33', BKN: '#000000', CHA: '#1D1160',
    CHI: '#CE1141', CLE: '#860038', DAL: '#00538C', DEN: '#0E2240',
    DET: '#C8102E', GSW: '#1D428A', HOU: '#CE1141', IND: '#002D62',
    LAC: '#C8102E', LAL: '#552583', MEM: '#5D76A9', MIA: '#98002E',
    MIL: '#00471B', MIN: '#0C2340', NOP: '#0C2340', NYK: '#F58426',
    OKC: '#007AC1', ORL: '#0077C0', PHI: '#006BB6', PHX: '#1D1160',
    POR: '#E03A3E', SAC: '#5A2D81', SAS: '#C4CED4', TOR: '#CE1141',
    UTA: '#002B5C', WAS: '#002B5C',
};

export function abbrFromTeamName(fullName) {
    return TEAM_NAME_TO_ABBR[fullName?.trim()] || null;
}

export function getTeamLogoUrl(abbreviation) {
    const id = TEAM_IDS[abbreviation?.toUpperCase()];
    if (!id) return null;
    return `https://cdn.nba.com/logos/nba/${id}/global/L/logo.svg`;
}

export function getPlayerHeadshotUrl(playerId) {
    if (!playerId) return null;
    return `https://cdn.nba.com/headshots/nba/latest/1040x760/${playerId}.png`;
}

export function initials(name) {
    if (!name) return '?';
    const parts = name.trim().split(/\s+/);
    const first = parts[0]?.[0] || '';
    const last = parts.length > 1 ? parts[parts.length - 1][0] : '';
    return (first + last).toUpperCase();
}
