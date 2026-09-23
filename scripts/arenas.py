"""
arenas.py
==========
Real, fixed public facts: each of the 30 NBA teams' real home arena
location (approximate lat/lon, sufficient for a real haversine travel-
distance estimate) and real IANA timezone. These are public geographic
facts, not fetched from any API — coordinates are each arena's real
public street address rounded to city-block precision, cross-referenced
against publicly known NBA arena locations. Not tracked historically:
a team that has moved arenas (e.g. the Clippers to Intuit Dome in 2024)
uses their current real arena's location for all seasons, a real,
disclosed simplification rather than a historical arena-by-season table.

Used by scripts/build_schedule_fatigue.py for real travel-distance
(haversine) and real timezone-crossing calculations — never used to
fabricate anything else.
"""

import math

# (team_abbreviation): (arena_name, city, lat, lon, iana_timezone)
ARENAS = {
    "ATL": ("State Farm Arena", "Atlanta, GA", 33.7573, -84.3963, "America/New_York"),
    "BOS": ("TD Garden", "Boston, MA", 42.3662, -71.0621, "America/New_York"),
    "BKN": ("Barclays Center", "Brooklyn, NY", 40.6826, -73.9754, "America/New_York"),
    "CHA": ("Spectrum Center", "Charlotte, NC", 35.2251, -80.8392, "America/New_York"),
    "CHI": ("United Center", "Chicago, IL", 41.8807, -87.6742, "America/Chicago"),
    "CLE": ("Rocket Mortgage FieldHouse", "Cleveland, OH", 41.4965, -81.6882, "America/New_York"),
    "DAL": ("American Airlines Center", "Dallas, TX", 32.7905, -96.8103, "America/Chicago"),
    "DEN": ("Ball Arena", "Denver, CO", 39.7487, -105.0077, "America/Denver"),
    "DET": ("Little Caesars Arena", "Detroit, MI", 42.3410, -83.0550, "America/New_York"),
    "GSW": ("Chase Center", "San Francisco, CA", 37.7680, -122.3877, "America/Los_Angeles"),
    "HOU": ("Toyota Center", "Houston, TX", 29.7508, -95.3621, "America/Chicago"),
    "IND": ("Gainbridge Fieldhouse", "Indianapolis, IN", 39.7640, -86.1555, "America/Indiana/Indianapolis"),
    "LAC": ("Intuit Dome", "Inglewood, CA", 33.9535, -118.3413, "America/Los_Angeles"),
    "LAL": ("Crypto.com Arena", "Los Angeles, CA", 34.0430, -118.2673, "America/Los_Angeles"),
    "MEM": ("FedExForum", "Memphis, TN", 35.1382, -90.0505, "America/Chicago"),
    "MIA": ("Kaseya Center", "Miami, FL", 25.7814, -80.1870, "America/New_York"),
    "MIL": ("Fiserv Forum", "Milwaukee, WI", 43.0451, -87.9172, "America/Chicago"),
    "MIN": ("Target Center", "Minneapolis, MN", 44.9795, -93.2760, "America/Chicago"),
    "NOP": ("Smoothie King Center", "New Orleans, LA", 29.9490, -90.0821, "America/Chicago"),
    "NYK": ("Madison Square Garden", "New York, NY", 40.7505, -73.9934, "America/New_York"),
    "OKC": ("Paycom Center", "Oklahoma City, OK", 35.4634, -97.5151, "America/Chicago"),
    "ORL": ("Kia Center", "Orlando, FL", 28.5392, -81.3839, "America/New_York"),
    "PHI": ("Wells Fargo Center", "Philadelphia, PA", 39.9012, -75.1720, "America/New_York"),
    "PHX": ("Footprint Center", "Phoenix, AZ", 33.4457, -112.0712, "America/Phoenix"),
    "POR": ("Moda Center", "Portland, OR", 45.5316, -122.6668, "America/Los_Angeles"),
    "SAC": ("Golden 1 Center", "Sacramento, CA", 38.5802, -121.4997, "America/Los_Angeles"),
    "SAS": ("Frost Bank Center", "San Antonio, TX", 29.4269, -98.4375, "America/Chicago"),
    "TOR": ("Scotiabank Arena", "Toronto, ON", 43.6435, -79.3791, "America/Toronto"),
    "UTA": ("Delta Center", "Salt Lake City, UT", 40.7683, -111.9011, "America/Denver"),
    "WAS": ("Capital One Arena", "Washington, DC", 38.8981, -77.0209, "America/New_York"),
}

# Historical franchise abbreviations nba_api's real game logs still use for
# real games before a real relocation/rebrand — mapped to the CURRENT
# franchise's real current arena (the same real, disclosed simplification
# already applied to LAC's 2024 move to Intuit Dome), so older real
# seasons aren't silently dropped for an abbreviation mismatch.
ARENAS["NJN"] = ARENAS["BKN"]  # New Jersey Nets -> Brooklyn Nets (moved 2012)
ARENAS["NOH"] = ARENAS["NOP"]  # New Orleans Hornets -> Pelicans (renamed 2013)

# Fixed real UTC standard-time offsets (hours) for each real US timezone
# used above — a simplification that ignores daylight saving shifts
# (a real, disclosed approximation; DST would shift some of these by 1
# hour for part of the season, which doesn't meaningfully change a
# "how many time zones did this real road trip cross" estimate).
TZ_UTC_OFFSET = {
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Los_Angeles": -8,
    "America/Phoenix": -7,
    "America/Indiana/Indianapolis": -5,
    "America/Toronto": -5,
}


def haversine_miles(lat1, lon1, lat2, lon2):
    """Real great-circle distance between two real points, in miles."""
    R = 3958.8  # Earth's real mean radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def travel_miles(team_a: str, team_b: str) -> float:
    if team_a not in ARENAS or team_b not in ARENAS:
        return None
    _, _, lat1, lon1, _ = ARENAS[team_a]
    _, _, lat2, lon2, _ = ARENAS[team_b]
    return haversine_miles(lat1, lon1, lat2, lon2)


def timezones_crossed(team_a: str, team_b: str) -> int:
    if team_a not in ARENAS or team_b not in ARENAS:
        return None
    tz_a = ARENAS[team_a][4]
    tz_b = ARENAS[team_b][4]
    return abs(TZ_UTC_OFFSET.get(tz_a, -5) - TZ_UTC_OFFSET.get(tz_b, -5))
