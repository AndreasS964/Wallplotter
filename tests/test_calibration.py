import pytest

from wallplotter.calibration import AreaCalibration, CalibrationError
from wallplotter.config import PlotConfig
from wallplotter.gcode import lines_to_gcode, prepare_geometry

PERFECT = {
    "bottom-left": (100.0, 200.0),
    "bottom-right": (1900.0, 200.0),
    "top-right": (1900.0, 2400.0),
    "top-left": (100.0, 2400.0),
}


def calibration(points=None) -> AreaCalibration:
    return AreaCalibration(points=dict(points or PERFECT))


def test_rectangle_from_four_corners():
    assert calibration().rectangle() == (100.0, 200.0, 1800.0, 2200.0)


def test_rectangle_is_conservative_when_the_wall_is_crooked():
    crooked = dict(PERFECT)
    crooked["bottom-left"] = (120.0, 210.0)  # weiter rechts und höher
    origin_x, origin_y, width, height = calibration(crooked).rectangle()
    assert (origin_x, origin_y) == (120.0, 210.0)
    assert width == 1780.0
    assert height == 2190.0


def test_two_diagonal_corners_are_enough():
    partial = calibration({"bottom-left": (0.0, 0.0), "top-right": (1000.0, 500.0)})
    assert partial.complete
    assert partial.rectangle() == (0.0, 0.0, 1000.0, 500.0)


def test_the_other_diagonal_is_enough_too():
    """complete() prüfte nur die eine Diagonale (bottom-left/top-right) fest
    verdrahtet — die andere (bottom-right/top-left), die rectangle() genauso
    gut aus den vorhandenen Ecken herleiten kann, zählte fälschlich als
    unvollständig."""
    partial = calibration({"bottom-right": (1000.0, 0.0), "top-left": (0.0, 500.0)})
    assert partial.complete
    assert partial.rectangle() == (0.0, 0.0, 1000.0, 500.0)


def test_incomplete_calibration_reports_what_is_missing():
    partial = calibration({"bottom-left": (0.0, 0.0)})
    assert not partial.complete
    assert "top-right" in partial.missing
    with pytest.raises(CalibrationError, match="unvollständig"):
        partial.rectangle()


def test_swapped_corners_are_rejected():
    swapped = dict(PERFECT)
    swapped["bottom-left"], swapped["bottom-right"] = (
        PERFECT["bottom-right"],
        PERFECT["bottom-left"],
    )
    with pytest.raises(CalibrationError, match="keine Fläche"):
        calibration(swapped).rectangle()


def test_unknown_corner_name_is_rejected():
    with pytest.raises(CalibrationError, match="Unbekannte Ecke"):
        AreaCalibration().record("middle", (0.0, 0.0))


def test_skew_detects_a_crooked_mount():
    crooked = dict(PERFECT)
    crooked["bottom-right"] = (1900.0, 215.0)
    horizontal, vertical = calibration(crooked).skew_mm()
    assert horizontal == 15.0
    assert vertical == 0.0


def test_to_plot_config_carries_origin_and_settings():
    base = PlotConfig(draw_feed=900.0, margin_mm=50.0)
    config = calibration().to_plot_config(base)
    assert (config.width_mm, config.height_mm) == (1800.0, 2200.0)
    assert (config.origin_x_mm, config.origin_y_mm) == (100.0, 200.0)
    assert config.draw_feed == 900.0
    assert config.margin_mm == 50.0


def test_margin_shrinks_for_a_tiny_area():
    tiny = calibration({"bottom-left": (0.0, 0.0), "top-right": (100.0, 80.0)})
    assert tiny.to_plot_config(PlotConfig(margin_mm=50.0)).margin_mm == 20.0


def test_gcode_lands_inside_the_calibrated_area():
    config = calibration().to_plot_config(PlotConfig(margin_mm=0.0, invert_y=False))
    square = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]
    geometry = prepare_geometry(square, config)
    xs = [x for line in geometry for x, _ in line]
    ys = [y for line in geometry for _, y in line]
    # Breite begrenzt: 1800 mm Quadrat, in den 2200 mm Höhe zentriert (je 200 mm Luft)
    assert min(xs) == 100.0
    assert max(xs) == 1900.0
    assert min(ys) == 400.0
    assert max(ys) == 2200.0
    # und im GCode taucht der Versatz genauso auf
    assert "X100 Y400" in lines_to_gcode(square, config)


def test_preview_geometry_ignores_the_origin():
    config = calibration().to_plot_config()
    geometry = prepare_geometry(
        [[(0.0, 0.0), (10.0, 10.0)]], config, invert_y=False, apply_origin=False
    )
    assert min(x for line in geometry for x, _ in line) == config.margin_mm


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "calibration.json"
    original = calibration()
    original.note = "erste Aufhängung"
    original.save(path)

    loaded = AreaCalibration.load(path)
    assert loaded.points == PERFECT
    assert loaded.note == "erste Aufhängung"
    assert loaded.rectangle() == original.rectangle()


def test_load_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(CalibrationError, match="Keine Kalibrierung"):
        AreaCalibration.load(tmp_path / "fehlt.json")


def test_load_rejects_garbage(tmp_path):
    path = tmp_path / "kaputt.json"
    path.write_text("kein json", encoding="utf-8")
    with pytest.raises(CalibrationError, match="keine gültige Kalibrierung"):
        AreaCalibration.load(path)


def test_load_rejects_a_points_block_of_the_wrong_shape(tmp_path):
    """Gültiges JSON, aber "points" ist eine Liste statt eines Wörterbuchs —
    load() fing nur ValueError/KeyError/TypeError/IndexError ab, nicht den
    AttributeError von ``["a", "b"].items()``."""
    path = tmp_path / "falsche-form.json"
    path.write_text('{"points": ["a", "b"], "note": ""}', encoding="utf-8")
    with pytest.raises(CalibrationError, match="keine gültige Kalibrierung"):
        AreaCalibration.load(path)


def test_summary_mentions_area_and_skew():
    crooked = dict(PERFECT)
    crooked["bottom-right"] = (1900.0, 212.0)
    text = calibration(crooked).summary()
    assert "1800" in text and "2188" in text
    assert "Schiefstand" in text
