"""Rauchtest der Web-UI: baut sie sich überhaupt auf?

Kein Browser-Test — der prüft nur, dass die Verdrahtung zwischen UI und Kern
hält (falsche Feldnamen, vertauschte Argumente, kaputte Callbacks fallen hier
auf, bevor sie vor der Wand auffallen).
"""

import pytest

pytest.importorskip("nicegui", reason="NiceGUI ist optional (Extra: web)")

from wallplotter.calibration import AreaCalibration  # noqa: E402
from wallplotter.webapp import WallplotterUI, create_app  # noqa: E402

CORNERS = {
    "bottom-left": (100.0, 200.0),
    "bottom-right": (1900.0, 200.0),
    "top-right": (1900.0, 2400.0),
    "top-left": (100.0, 2400.0),
}


@pytest.fixture
def app(tmp_path):
    from nicegui import ui

    instance = WallplotterUI(ui, host="127.0.0.1", calibration_path=str(tmp_path / "cal.json"))
    instance.build_ui()
    return instance


def test_app_builds(app):
    assert app.preview is not None
    assert set(app.corner_badges) == set(CORNERS)


def test_pattern_loads_and_produces_gcode(app):
    app.load_pattern("frame")
    assert app.lines
    assert app.gcode.startswith("; frame")
    assert "M3 S30" in app.gcode
    assert app.source_is_pattern


def test_pattern_geometry_is_not_mirrored(app):
    app.load_pattern("feed-ramp")
    assert app.plot_config().invert_y is False


def test_calibration_switches_the_area(app, tmp_path):
    app.calibration = AreaCalibration(points=dict(CORNERS))
    app.refresh_calibration()
    config = app.plot_config()
    assert (config.width_mm, config.height_mm) == (1800.0, 2200.0)
    assert (config.origin_x_mm, config.origin_y_mm) == (100.0, 200.0)

    app.use_calibration.set_value(False)
    assert app.plot_config().width_mm == 2000.0


def test_recorded_corner_survives_a_restart(app, tmp_path):
    app.calibration.record("bottom-left", (10.0, 20.0))
    app.calibration.save(app.calibration_path)

    from nicegui import ui

    revived = WallplotterUI(ui, calibration_path=app.calibration_path)
    assert revived.calibration.points == {"bottom-left": (10.0, 20.0)}


def test_unknown_pattern_does_not_crash_the_ui(app):
    app.load_pattern("gibtsnicht")
    assert app.gcode is None


def test_create_app_returns_the_ui_module(tmp_path):
    ui = create_app(calibration_path=str(tmp_path / "cal.json"))
    assert hasattr(ui, "run")
