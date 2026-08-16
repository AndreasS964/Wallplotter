import pytest

from wallplotter.config import PenConfig, PlotConfig
from wallplotter.gcode import lines_to_gcode, stats_for

SQUARE = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]
CONFIG = PlotConfig(width_mm=100, height_mm=100, margin_mm=10, invert_y=False)


def test_header_and_footer():
    gcode = lines_to_gcode(SQUARE, CONFIG)
    lines = gcode.splitlines()
    assert "G21 ; Millimeter" in lines
    assert "G90 ; absolute Koordinaten" in lines
    assert lines[-1] == "M2 ; Programmende"
    assert gcode.endswith("\n")


def test_pen_uses_m3_not_m280():
    gcode = lines_to_gcode(SQUARE, CONFIG)
    assert "M3 S30" in gcode
    assert "M280" not in gcode


def test_fit_scales_into_drawable_area():
    gcode = lines_to_gcode(SQUARE, CONFIG)
    # letzte Bewegung ist die Rückfahrt auf X0 Y0 und gehört nicht zur Zeichnung
    coords = [line for line in gcode.splitlines() if line.startswith(("G0 X", "G1 X"))][:-1]
    xs = [float(part[1:]) for line in coords for part in line.split() if part.startswith("X")]
    assert min(xs) == 10.0
    assert max(xs) == 90.0


def test_no_fit_keeps_coordinates():
    gcode = lines_to_gcode(SQUARE, CONFIG, fit=False)
    assert "G0 X0 Y0" in gcode
    assert "G1 X10 Y0" in gcode


def test_invert_y_mirrors_axis():
    config = PlotConfig(width_mm=100, height_mm=100, margin_mm=0, invert_y=True)
    gcode = lines_to_gcode([[(0.0, 0.0), (0.0, 10.0)]], config, fit=False)
    assert "Y100" in gcode
    assert "Y90" in gcode


def test_pen_lift_between_lines():
    two = [[(0.0, 0.0), (1.0, 0.0)], [(5.0, 0.0), (6.0, 0.0)]]
    gcode = lines_to_gcode(two, CONFIG, fit=False)
    # zweimal absetzen, dazwischen und am Ende abheben
    assert gcode.count("M3 S30") == 2
    assert gcode.count("M3 S0") == 3


def test_pen_up_can_use_m5():
    config = PlotConfig(pen=PenConfig(use_m5_for_up=True, dwell_s=0))
    gcode = lines_to_gcode(SQUARE, config)
    assert "M3 S0" not in gcode
    assert gcode.count("M5") >= 2


def test_degenerate_lines_are_dropped():
    gcode = lines_to_gcode([[(1.0, 1.0)], []], CONFIG, fit=False)
    assert "G1 X" not in gcode


def test_travel_as_g1():
    config = PlotConfig(width_mm=100, height_mm=100, margin_mm=10, travel_as_g1=True)
    gcode = lines_to_gcode(SQUARE, config)
    assert "G0 " not in gcode
    assert "G1 F3000" in gcode


def test_stats():
    stats = stats_for(SQUARE, CONFIG)
    assert stats.line_count == 1
    assert stats.point_count == 5
    assert stats.draw_mm == 40.0
    assert stats.as_dict()["draw_mm"] == 40.0
    assert "Linien" in str(stats)


def test_margin_larger_than_area_is_rejected():
    with pytest.raises(ValueError):
        PlotConfig(width_mm=100, height_mm=100, margin_mm=60)


def test_stats_count_the_servo_dwell():
    """Bei einem Punktraster sind die Stifthübe der Löwenanteil der Laufzeit."""
    dots = [[(float(i), 0.0), (float(i) + 1.0, 0.0)] for i in range(0, 200, 2)]
    config = PlotConfig(pen=PenConfig(dwell_s=0.25))
    stats = stats_for(dots, config)
    assert stats.pen_s == pytest.approx(2 * 100 * 0.25)
    assert stats.duration_s == pytest.approx(stats.motion_s + stats.pen_s)
    assert "Stifthübe" in str(stats)


def test_stats_without_dwell_are_pure_motion():
    config = PlotConfig(pen=PenConfig(dwell_s=0.0))
    stats = stats_for(SQUARE, config)
    assert stats.pen_s == 0.0
    assert stats.duration_s == pytest.approx(stats.motion_s)
    assert "Stifthübe" not in str(stats)
