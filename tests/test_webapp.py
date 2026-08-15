"""Rauchtest der Web-UI: baut sie sich überhaupt auf?

Kein Browser-Test — der prüft nur, dass die Verdrahtung zwischen UI und Kern
hält (falsche Feldnamen, vertauschte Argumente, kaputte Callbacks fallen hier
auf, bevor sie vor der Wand auffallen).
"""

import pytest

pytest.importorskip("nicegui", reason="NiceGUI ist optional (Extra: web)")

from wallplotter.calibration import AreaCalibration  # noqa: E402
from wallplotter.location import Location, LocationBook  # noqa: E402
from wallplotter.webapp import WallplotterUI, create_app  # noqa: E402

CORNERS = {
    "bottom-left": (100.0, 200.0),
    "bottom-right": (1900.0, 200.0),
    "top-right": (1900.0, 2400.0),
    "top-left": (100.0, 2400.0),
}


def make_location(**kwargs) -> Location:
    return Location(
        name=kwargs.pop("name", "Keller"),
        anchor_span_mm=2300.0,
        left_belt_zero_mm=1500.0,
        right_belt_zero_mm=1500.0,
        **kwargs,
    )


@pytest.fixture
def book_path(tmp_path):
    """Ein Standort ohne Flächenkalibrierung, wie nach dem Aufhängen."""
    path = tmp_path / "standorte.json"
    book = LocationBook()
    book.add(make_location())
    book.save(path)
    return path


@pytest.fixture
def app(book_path):
    from nicegui import ui

    instance = WallplotterUI(ui, host="127.0.0.1", locations_path=str(book_path))
    instance.build_ui()
    instance.refresh_calibration()
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


def test_calibration_switches_the_area(app):
    app.location.calibration = AreaCalibration(points=dict(CORNERS))
    app.refresh_calibration()
    config = app.plot_config()
    assert (config.width_mm, config.height_mm) == (1800.0, 2200.0)
    assert (config.origin_x_mm, config.origin_y_mm) == (100.0, 200.0)

    app.use_calibration.set_value(False)
    assert app.plot_config().width_mm == 2000.0


def test_recorded_corner_survives_a_restart(app):
    app.calibration.record("bottom-left", (10.0, 20.0))
    app.save_book()

    from nicegui import ui

    revived = WallplotterUI(ui, locations_path=app.locations_path)
    assert revived.calibration.points == {"bottom-left": (10.0, 20.0)}


def test_new_location_can_be_added_from_the_ui(app):
    app.new_name.set_value("Werkstatt")
    app.new_span.set_value(1800.0)
    app.new_left.set_value(1200.0)
    app.new_right.set_value(1200.0)
    app.add_location()
    assert "Werkstatt" in app.book.locations
    assert app.location.name == "Werkstatt"


def test_impossible_measurements_do_not_create_a_location(app):
    app.new_name.set_value("Unfug")
    app.new_span.set_value(2300.0)
    app.new_left.set_value(400.0)   # Riemen zusammen kürzer als die Spannweite
    app.new_right.set_value(400.0)
    app.add_location()
    assert "Unfug" not in app.book.locations


def test_switching_location_changes_the_area(app):
    app.location.calibration = AreaCalibration(points=dict(CORNERS))
    app.book.add(make_location(name="Werkstatt"), activate=False)
    app.save_book()

    app.switch_location("Werkstatt")
    assert app.location.name == "Werkstatt"
    assert not app.calibration.complete   # eigener Standort, eigene Fläche
    app.switch_location("Keller")
    assert app.plot_config().width_mm == 1800.0


def test_unknown_pattern_does_not_crash_the_ui(app):
    app.load_pattern("gibtsnicht")
    assert app.gcode is None


def test_create_app_returns_the_ui_module(tmp_path):
    ui = create_app(locations_path=str(tmp_path / "standorte.json"))
    assert hasattr(ui, "run")


def test_ui_survives_having_no_location_at_all(tmp_path):
    from nicegui import ui

    instance = WallplotterUI(ui, locations_path=str(tmp_path / "leer.json"))
    instance.build_ui()
    instance.refresh_calibration()
    assert instance.location is None
    instance.record_corner("bottom-left")   # darf nicht abstürzen
    assert instance.plot_config().width_mm == 2000.0
