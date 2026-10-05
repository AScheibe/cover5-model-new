"""Team name normalisation. Canonical codes follow nflverse (LA for the Rams)."""
from __future__ import annotations

CANON = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
    "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO",
    "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
]

_FULL = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Los Angeles Rams": "LA", "Los Angeles Chargers": "LAC",
    "Las Vegas Raiders": "LV", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "Seattle Seahawks": "SEA", "San Francisco 49ers": "SF", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
    # historical / alternate names
    "Oakland Raiders": "LV", "San Diego Chargers": "LAC", "St. Louis Rams": "LA",
    "Washington Football Team": "WAS", "Washington Redskins": "WAS",
}

_ALIASES = {
    "LAR": "LA", "WSH": "WAS", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LA",
    "GNB": "GB", "KAN": "KC", "NWE": "NE", "NOR": "NO", "SFO": "SF", "TAM": "TB",
    "LVR": "LV", "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU",
}


def normalize(name: str) -> str:
    """Map a full name, nickname-ish string, or abbreviation to a canonical code."""
    if name is None:
        raise ValueError("team name is None")
    s = name.strip()
    if s in _FULL:
        return _FULL[s]
    up = s.upper()
    if up in CANON:
        return up
    if up in _ALIASES:
        return _ALIASES[up]
    # last-word nickname match, e.g. "Chiefs"
    for full, code in _FULL.items():
        if full.split()[-1].lower() == s.lower():
            return code
    raise ValueError(f"unknown team: {name!r}")
