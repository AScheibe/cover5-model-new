"""League scoring rule and edge arithmetic."""
from __future__ import annotations

HOME, AWAY = "HOME", "AWAY"


def pick_score(home_margin: float, league_home_spread: float, side: str) -> float:
    """Points scored by a pick under the league rule.

    Picking the home team at home_spread -3 when the home team wins by 10 scores
    10 + (-3) = +7. Picking the away team in that game scores -10 - (-3) = -7.
    """
    if side == HOME:
        return home_margin + league_home_spread
    if side == AWAY:
        return -home_margin - league_home_spread
    raise ValueError(side)


def home_edge(league_home_spread: float, market_home_spread: float) -> float:
    """Expected league points from picking the HOME side.

    The market's expected home margin is -market_home_spread. The league pays
    (home_margin + league_home_spread), so the expectation is
    league_home_spread - market_home_spread.

    Example: league has KC(home) -3, market has moved to KC -6. Picking KC is
    worth -3 - (-6) = +3 expected points.
    """
    return league_home_spread - market_home_spread


def best_side(league_home_spread: float, market_home_spread: float) -> tuple[str, float]:
    """Return (side, edge) for the better side of a game. Edge is >= 0."""
    e = home_edge(league_home_spread, market_home_spread)
    return (HOME, e) if e >= 0 else (AWAY, -e)


def side_edge(side: str, league_home_spread: float, market_home_spread: float) -> float:
    e = home_edge(league_home_spread, market_home_spread)
    return e if side == HOME else -e


def fmt_spread(away: str, home: str, home_spread: float) -> str:
    """Render a home spread the way a league sheet would: 'KC -3' or 'PK'."""
    if home_spread is None or home_spread != home_spread:  # NaN
        return "n/a"
    if abs(home_spread) < 1e-9:
        return "PK"
    if home_spread < 0:
        return f"{home} {home_spread:g}"
    return f"{away} {-home_spread:g}"


def fmt_pick(team: str, opponent: str, home_spread: float, team_is_home: bool) -> str:
    """Render a pick the way the league app does: 'IND -3.5 @ WAS' or 'CHI -3 vs NYJ'."""
    if home_spread is None or home_spread != home_spread:
        return f"{team} (n/a) {'vs' if team_is_home else '@'} {opponent}"
    team_spread = home_spread if team_is_home else -home_spread
    num = "PK" if abs(team_spread) < 1e-9 else f"{team_spread:+g}"
    return f"{team} {num} {'vs' if team_is_home else '@'} {opponent}"
