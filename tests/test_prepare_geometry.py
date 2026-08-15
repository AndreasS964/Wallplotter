from wallplotter.config import PlotConfig
from wallplotter.gcode import prepare_geometry, stats_for
from wallplotter.geometry import bounds

SQUARE = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]
CONFIG = PlotConfig(width_mm=100, height_mm=100, margin_mm=10)


def test_fit_and_invert_by_default():
    geometry = prepare_geometry(SQUARE, CONFIG)
    assert bounds(geometry) == (10.0, 10.0, 90.0, 90.0)
    # invert_y=True: die erste Ecke liegt jetzt oben statt unten
    assert geometry[0][0] == (10.0, 90.0)


def test_invert_can_be_overridden_for_preview():
    geometry = prepare_geometry(SQUARE, CONFIG, invert_y=False)
    assert geometry[0][0] == (10.0, 10.0)


def test_no_fit_keeps_raw_coordinates():
    geometry = prepare_geometry(SQUARE, CONFIG, fit=False, invert_y=False)
    assert bounds(geometry) == (0.0, 0.0, 10.0, 10.0)


def test_stats_match_the_generated_gcode_header():
    # was die CLI meldet, muss dem entsprechen, was im GCode-Header steht
    stats = stats_for(prepare_geometry(SQUARE, CONFIG), CONFIG)
    from wallplotter.gcode import lines_to_gcode

    header = [
        line for line in lines_to_gcode(SQUARE, CONFIG).splitlines() if "Linien" in line
    ]
    assert header and str(stats) in header[0]


def test_degenerate_lines_are_dropped():
    assert prepare_geometry([[(1.0, 1.0)], []], CONFIG) == []
