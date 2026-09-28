"""Team identity across seasons: which abbreviation a team uses in which season,
and which franchise it belongs to. Shared by scripts/build_team_seasons.py,
scripts/build_team_zone_mix.py and api/routers/team_profile.py.

The app's tables use two conventions (CLAUDE.md): before 2009-10 the
Basketball-Reference codes (PHO, NJN, SEA, CHH...), from 2009-10 on the
NBA's (PHX, NJN/BKN, CHA...). Basketball-Reference itself keeps its own codes
in every season (PHO, BRK, CHO), so `app_abbr()` translates its codes into
the ones the rest of the database uses for that season.

Franchises follow the NBA's own record-keeping: the Charlotte Hornets of
1988-2002 belong to today's Hornets (CHA), the New Orleans Hornets of
2002-2013 to the Pelicans (NOP), the Seattle SuperSonics to the Thunder (OKC).
"""

# Basketball-Reference code -> the NBA code the app uses from 2009-10 on.
BREF_TO_NBA = {"PHO": "PHX", "BRK": "BKN", "CHO": "CHA"}
NBA_TO_BREF = {v: k for k, v in BREF_TO_NBA.items()}
APP_CODE_SWITCH = 2010   # first season (end year) stored with NBA codes

# Franchise key (today's NBA code) -> every Basketball-Reference code it has played under.
FRANCHISES = {
    "ATL": ["ATL", "STL", "MLH", "TRI"],
    "BOS": ["BOS"],
    "BKN": ["BRK", "NJN", "NYN"],
    "CHA": ["CHO", "CHA", "CHH"],
    "CHI": ["CHI"],
    "CLE": ["CLE"],
    "DAL": ["DAL"],
    "DEN": ["DEN"],
    "DET": ["DET", "FTW"],
    "GSW": ["GSW", "SFW", "PHW"],
    "HOU": ["HOU", "SDR"],
    "IND": ["IND"],
    "LAC": ["LAC", "SDC", "BUF"],
    "LAL": ["LAL", "MNL"],
    "MEM": ["MEM", "VAN"],
    "MIA": ["MIA"],
    "MIL": ["MIL"],
    "MIN": ["MIN"],
    "NOP": ["NOP", "NOH", "NOK"],
    "NYK": ["NYK"],
    "OKC": ["OKC", "SEA"],
    "ORL": ["ORL"],
    "PHI": ["PHI", "SYR"],
    "PHX": ["PHO"],
    "POR": ["POR"],
    "SAC": ["SAC", "KCK", "KCO", "CIN", "ROC"],
    "SAS": ["SAS"],
    "TOR": ["TOR"],
    "UTA": ["UTA", "NOJ"],
    "WAS": ["WAS", "WSB", "CAP", "BAL", "CHZ", "CHP"],
}
FRANCHISE_OF_BREF = {code: fr for fr, codes in FRANCHISES.items() for code in codes}


def app_abbr(bref_code, season):
    """The abbreviation the rest of the database uses for this team in this season."""
    return BREF_TO_NBA.get(bref_code, bref_code) if season >= APP_CODE_SWITCH else bref_code


def franchise_of(bref_code):
    """Franchise key for a Basketball-Reference code; defunct teams are their own franchise."""
    return FRANCHISE_OF_BREF.get(bref_code, bref_code)


def lookup_codes(abbr):
    """Every code a user-supplied abbreviation might mean (PHX and PHO are the same team)."""
    abbr = abbr.upper()
    return {abbr, BREF_TO_NBA.get(abbr, abbr), NBA_TO_BREF.get(abbr, abbr)}
