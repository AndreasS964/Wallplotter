"""Rauchtest der Web-UI: baut sie sich überhaupt auf?

Kein Browser-Test — der prüft nur, dass die Verdrahtung zwischen UI und Kern
hält (falsche Feldnamen, vertauschte Argumente, kaputte Callbacks fallen hier
auf, bevor sie vor der Wand auffallen).
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("nicegui", reason="NiceGUI ist optional (Extra: web)")

from wallplotter.calibration import AreaCalibration  # noqa: E402
from wallplotter.location import Location, LocationBook  # noqa: E402
from wallplotter.upload import FluidNCError  # noqa: E402
from wallplotter.webapp import WallplotterUI, create_app  # noqa: E402


def run_handler(app, coro):
    """Eine Coroutine so ausführen, wie NiceGUI es tut: im Slot ihres Elements.

    NiceGUI hält den Slot über den gesamten ``await`` hinweg offen
    (``events._await_and_handle_in_context``) — ohne ihn findet ``ui.notify``
    keinen Client. Der Slot-Stapel hängt am asyncio-Task, deshalb muss er
    *innerhalb* der neuen Task betreten werden.
    """

    async def wrapper():
        with app.layer_box:
            return await coro

    return asyncio.run(wrapper())


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
    run_handler(instance, instance.record_corner("bottom-left"))  # darf nicht abstürzen
    assert instance.plot_config().width_mm == 2000.0


def test_photo_technique_can_be_switched(app, tmp_path):
    """Verfahrenswechsel muss die Vorlage neu übersetzen, nicht nur den Regler drehen."""
    pytest.importorskip("PIL.Image")
    from PIL import Image

    image = Image.new("L", (60, 75), 128)
    path = tmp_path / "foto.png"
    image.save(path)

    app.upload_data = path.read_bytes()
    app.upload_name = "foto.png"

    app.technique.set_value("spiral")
    run_handler(app, app.render_upload())
    assert len(app.lines) >= 1
    spiral_points = sum(len(line) for line in app.lines)

    app.technique.set_value("tsp")
    run_handler(app, app.render_upload())
    assert len(app.lines) == 1                      # eine durchgehende Linie
    assert sum(len(line) for line in app.lines) != spiral_points
    assert "tsp" in app.source_name


def test_photo_geometry_is_not_fitted_again(app, tmp_path):
    """Bildverfahren liefern schon Millimeter — erneutes Einpassen würde skalieren."""
    pytest.importorskip("PIL.Image")
    from PIL import Image

    path = tmp_path / "foto.png"
    Image.new("L", (60, 75), 100).save(path)
    app.upload_data, app.upload_name = path.read_bytes(), "foto.png"
    run_handler(app, app.render_upload())
    assert app.fit_source is False


def test_colour_layers_are_listed_and_plottable(app, tmp_path):
    pytest.importorskip("vpype_cli")
    svg = tmp_path / "bunt.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
        'viewBox="0 0 100 100">'
        '<rect x="10" y="10" width="80" height="80" fill="none" stroke="#000000"/>'
        '<circle cx="50" cy="50" r="30" fill="none" stroke="#e02020"/></svg>',
        encoding="utf-8",
    )
    app.upload_data, app.upload_name = svg.read_bytes(), "bunt.svg"
    run_handler(app, app.render_upload())

    assert len(app.layers) == 2
    assert {layer.color for layer in app.layers} == {"#000000", "#e02020"}
    assert "2 Farben" in app.source_name
    run_handler(app, app.send_layer(0))   # ohne Board: darf nur nicht abstürzen


def test_photo_upload_clears_previous_layers(app, tmp_path):
    pytest.importorskip("PIL.Image")
    from PIL import Image

    app.layers = ["alt"]
    path = tmp_path / "foto.png"
    Image.new("L", (40, 50), 120).save(path)
    app.upload_data, app.upload_name = path.read_bytes(), "foto.png"
    run_handler(app, app.render_upload())
    assert app.layers == []


def test_empty_number_fields_do_not_crash_the_ui(app, tmp_path):
    """Ein geleertes Zahlenfeld liefert None — das darf keine Rechnung erreichen."""
    pytest.importorskip("PIL.Image")
    from PIL import Image

    path = tmp_path / "foto.png"
    Image.new("L", (40, 50), 120).save(path)
    app.upload_data, app.upload_name = path.read_bytes(), "foto.png"

    app.pitch.set_value(None)
    run_handler(app, app.render_upload())   # früher: TypeError aus imaging.spiral
    assert app.lines

    app.jog_step.set_value(None)
    run_handler(app, app.jog(1, 0))   # ohne Board: darf nur nicht abstürzen


def test_absurd_margin_does_not_crash_the_ui(app):
    """PlotConfig wirft bei zu großem Rand — die Oberfläche darf das nicht."""
    app.margin.set_value(9999)
    config = app.plot_config()
    assert config.drawable_width_mm > 0
    assert config.drawable_height_mm > 0


def test_status_polling_does_not_block_the_event_loop(app):
    """Die Timer-Abfrage läuft in einem Thread — sonst friert die UI alle zwei
    Sekunden für die Timeout-Dauer ein, sobald das Board nicht antwortet."""
    import asyncio

    def slow_status():
        time.sleep(0.3)
        raise FluidNCError("nicht erreichbar")

    app.client = lambda timeout=10.0: SimpleNamespace(status=slow_status)

    async def scenario():
        ticks = 0

        async def counter():
            nonlocal ticks
            for _ in range(30):
                await asyncio.sleep(0.01)
                ticks += 1

        await asyncio.gather(app.poll_status(), counter())
        return ticks

    assert asyncio.run(scenario()) == 30
    assert app.status_label.text == "FluidNC nicht erreichbar"
