import pytest

from wallplotter.config import PlotConfig
from wallplotter.gcode import lines_to_gcode
from wallplotter.geometry import bounds
from wallplotter.patterns import PATTERNS, build, describe

AREA = (2000.0, 2500.0, 50.0)  # Breite, Höhe, Rand


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_every_pattern_stays_inside_the_margin(name):
    pattern = build(name, *AREA)
    xmin, ymin, xmax, ymax = bounds(pattern.lines)
    assert xmin >= 50.0 - 1e-9
    assert ymin >= 50.0 - 1e-9
    assert xmax <= 1950.0 + 1e-9
    assert ymax <= 2450.0 + 1e-9


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_every_pattern_has_drawable_lines_and_a_description(name):
    pattern = build(name, *AREA)
    assert pattern.lines
    assert all(len(line) >= 2 for line in pattern.lines)
    assert len(pattern.description) > 20


def test_unknown_pattern_is_rejected():
    with pytest.raises(KeyError, match="Unbekanntes Muster"):
        build("gibtsnicht", *AREA)


def test_margin_larger_than_area_is_rejected():
    with pytest.raises(ValueError, match="Rand"):
        build("frame", 100.0, 100.0, 60.0)


def test_frame_touches_all_four_corners():
    pattern = build("frame", *AREA)
    assert bounds(pattern.lines) == (50.0, 50.0, 1950.0, 2450.0)
    # die Eckkreuze reichen bis an den Rahmen, aber nicht darüber hinaus
    crosses = pattern.lines[3:]
    assert bounds(crosses) == (50.0, 50.0, 1950.0, 2450.0)
    assert min(y for line in crosses for _, y in line) >= 50.0
    # Rahmen, zwei Diagonalen, vier Kreuze aus je zwei Strichen
    assert len(pattern.lines) == 11


def test_grid_spacing_is_exact():
    pattern = build("grid", 1000.0, 1000.0, 0.0, spacing=250.0)
    verticals = sorted({line[0][0] for line in pattern.lines if line[0][0] == line[1][0]})
    assert verticals == [0.0, 250.0, 500.0, 750.0, 1000.0]


def test_grid_rejects_nonsense_spacing():
    with pytest.raises(ValueError):
        build("grid", *AREA, spacing=0)


def test_circles_are_round():
    pattern = build("circles", 1000.0, 1000.0, 0.0, spacing=500.0)
    first = pattern.lines[0]
    xmin, ymin, xmax, ymax = bounds([first])
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    radii = [((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in first]
    assert max(radii) - min(radii) < 1e-9
    assert max(radii) == pytest.approx(175.0)  # spacing * 0.35


def test_pen_test_lifts_between_every_stroke():
    pattern = build("pen-test", *AREA, strokes=5)
    assert len(pattern.lines) == 10  # zwei Striche je Höhe
    gcode = lines_to_gcode(pattern.lines, PlotConfig(), fit=False)
    assert gcode.count("M3 S30") == 10


def test_feed_ramp_gets_faster_towards_the_top():
    pattern = build("feed-ramp", *AREA, steps=4)
    assert pattern.feeds == pytest.approx([500.0, 1666.667, 2833.333, 4000.0], abs=0.01)
    heights = [line[0][1] for line in pattern.lines]
    assert heights == sorted(heights)  # langsam unten, schnell oben


def test_feed_ramp_writes_one_feed_per_line():
    pattern = build("feed-ramp", *AREA, steps=3)
    gcode = lines_to_gcode(
        pattern.lines, PlotConfig(invert_y=False), fit=False, feeds=pattern.feeds
    )
    assert "F500" in gcode and "F2250" in gcode and "F4000" in gcode


def test_wrong_number_of_feeds_is_rejected():
    with pytest.raises(ValueError, match="feeds"):
        lines_to_gcode([[(0.0, 0.0), (1.0, 1.0)]], feeds=[100.0, 200.0])


def test_describe_lists_every_pattern():
    text = describe()
    assert all(name in text for name in PATTERNS)
