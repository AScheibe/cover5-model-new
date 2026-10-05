"""Cover 5 line-movement tracker.

Conventions used everywhere in this package:
  * ``home_spread`` is the sportsbook number next to the home team.
    ``-3.0`` means the home team is favored by 3; ``+3.0`` means the home
    team is a 3 point underdog.
  * ``home_margin`` is home_score - away_score.
  * League scoring for a pick is (margin on the picked side) - (spread on
    the picked side), i.e. the number of points the pick covers by, which
    can be negative.
"""
__version__ = "0.1.0"
