import json
from pathlib import Path

from cover5.providers import parse_oddsapi, parse_espn

FX = Path(__file__).parent / "fixtures"


def test_parse_oddsapi_medians_books_and_skips_empty():
    lines = parse_oddsapi(json.loads((FX / "oddsapi_sample.json").read_text()))
    by = {(l.away, l.home): l for l in lines}
    assert by[("DEN", "KC")].home_spread == -6.5     # median of -6.5, -6.0, -6.5
    assert by[("DEN", "KC")].books == 3
    assert by[("SF", "LA")].home_spread == 2.5
    assert ("DAL", "WAS") not in by                    # no bookmakers -> no line


def test_parse_espn_details_and_even():
    lines = parse_espn(json.loads((FX / "espn_sample.json").read_text()))
    by = {(l.away, l.home): l for l in lines}
    assert by[("DEN", "KC")].home_spread == -6.5
    assert by[("SF", "LA")].home_spread == 2.5        # away favored -> positive home spread
    assert by[("DAL", "WAS")].home_spread == 0.0
    assert ("PHI", "NYG") not in by
