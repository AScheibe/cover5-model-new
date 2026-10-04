from cover5.scoring import pick_score, home_edge, best_side, side_edge, fmt_spread, HOME, AWAY


def test_pick_score_matches_league_rule():
    # KC home -3, wins by 10: cover by 7
    assert pick_score(10, -3, HOME) == 7
    assert pick_score(10, -3, AWAY) == -7
    # KC home -3, wins by 1: lose by 2
    assert pick_score(1, -3, HOME) == -2
    # home dog +4 loses by 6: picking home scores -2
    assert pick_score(-6, 4, HOME) == -2


def test_home_edge_is_league_minus_market():
    # league KC -3, market KC -6: KC worth +3
    assert home_edge(-3, -6) == 3
    # market moved the other way: KC -3 -> KC -1: KC worth -2, opponent +2
    assert home_edge(-3, -1) == -2
    assert side_edge(AWAY, -3, -1) == 2


def test_best_side():
    assert best_side(-3, -6) == (HOME, 3)
    assert best_side(-3, -1) == (AWAY, 2)
    assert best_side(3, 3) == (HOME, 0)


def test_fmt_spread():
    assert fmt_spread("DEN", "KC", -3) == "KC -3"
    assert fmt_spread("DEN", "KC", 3.5) == "DEN -3.5"
    assert fmt_spread("DEN", "KC", 0) == "PK"


def test_matches_league_app_week4_2026():
    # Real week 4 2026 picks and the scores the league app displayed for them.
    # (team, side, home_spread on league sheet, final home margin, app score)
    cases = [
        ("IND", AWAY, 3.5, -17, 13.5),   # IND -3.5 @ WAS, IND won 30-13
        ("CHI", HOME, -3.0, 11, 8.0),    # CHI -3 vs NYJ, CHI won 23-12
        ("LAR", AWAY, 2.5, -4, 1.5),     # LAR -2.5 @ PHI, LAR won 24-20
        ("TEN", AWAY, -11.5, 6, 5.5),    # TEN +11.5 @ BAL, TEN lost 18-24
        ("JAX", AWAY, -2.5, -5, 7.5),    # JAX +2.5 @ CIN, JAX won 22-17
    ]
    for team, side, hs, margin, expected in cases:
        assert pick_score(margin, hs, side) == expected, team
    assert sum(c[-1] for c in cases) == 36


def test_fmt_pick_matches_app_style():
    from cover5.scoring import fmt_pick
    assert fmt_pick("IND", "WAS", 3.5, team_is_home=False) == "IND -3.5 @ WAS"
    assert fmt_pick("CHI", "NYJ", -3.0, team_is_home=True) == "CHI -3 vs NYJ"
    assert fmt_pick("TEN", "BAL", -11.5, team_is_home=False) == "TEN +11.5 @ BAL"
