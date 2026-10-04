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
