import math

import pytest

from wallplotter.calibration import AreaCalibration
from wallplotter.location import Location, LocationBook, LocationError

# Gleichschenklige Aufhängung: Gondel hängt mittig unter den Ankern
SYMMETRIC = dict(anchor_span_mm=2300.0, left_belt_zero_mm=1500.0, right_belt_zero_mm=1500.0)

CORNERS = {
    "bottom-left": (-800.0, -2200.0),
    "bottom-right": (800.0, -2200.0),
    "top-right": (800.0, -300.0),
    "top-left": (-800.0, -300.0),
}


def location(**kwargs) -> Location:
    return Location(name=kwargs.pop("name", "Keller"), **{**SYMMETRIC, **kwargs})


def test_symmetric_setup_puts_the_gondola_in_the_middle():
    anchors = location().anchors()
    assert anchors.left_x == pytest.approx(-1150.0)
    assert anchors.right_x == pytest.approx(1150.0)
    assert anchors.span == pytest.approx(2300.0)
    # Höhe aus Pythagoras: sqrt(1500² - 1150²)
    assert anchors.y == pytest.approx(math.sqrt(1500**2 - 1150**2))


def test_unequal_belts_shift_the_gondola_sideways():
    anchors = location(left_belt_zero_mm=1300.0, right_belt_zero_mm=1700.0).anchors()
    # kürzerer linker Riemen: Gondel hängt näher am linken Anker
    assert abs(anchors.left_x) < abs(anchors.right_x)
    assert anchors.span == pytest.approx(2300.0)


def test_belt_lengths_are_reproduced_from_the_derived_anchors():
    setup = location(left_belt_zero_mm=1400.0, right_belt_zero_mm=1600.0)
    left, right = setup.kinematics().belt_lengths(0.0, 0.0)
    assert left == pytest.approx(1400.0)
    assert right == pytest.approx(1600.0)


def test_impossible_triangle_is_rejected():
    with pytest.raises(LocationError, match="kein Dreieck"):
        Location(name="Unfug", anchor_span_mm=2300.0, left_belt_zero_mm=500.0, right_belt_zero_mm=500.0)


def test_negative_measurements_are_rejected():
    with pytest.raises(LocationError, match="größer als 0"):
        Location(name="Unfug", anchor_span_mm=0.0, left_belt_zero_mm=1500.0, right_belt_zero_mm=1500.0)


def test_plot_config_needs_a_calibrated_area():
    with pytest.raises(LocationError, match="noch nicht eingemessen"):
        location().plot_config()


def test_plot_config_uses_the_calibrated_area():
    setup = location(calibration=AreaCalibration(points=dict(CORNERS)))
    config = setup.plot_config()
    assert (config.width_mm, config.height_mm) == (1600.0, 1900.0)
    assert (config.origin_x_mm, config.origin_y_mm) == (-800.0, -2200.0)


def test_analysis_runs_over_the_calibrated_area():
    setup = location(calibration=AreaCalibration(points=dict(CORNERS)))
    analysis = setup.analysis(samples=7)
    assert analysis.worst_resolution_mm < 1.0
    assert analysis.max_tension_n > 0


def test_report_mentions_anchors_and_verdict():
    setup = location(calibration=AreaCalibration(points=dict(CORNERS)), note="Kellerwand")
    text = setup.report()
    assert "Anker relativ zum Nullpunkt" in text
    assert "Kellerwand" in text
    assert "Reserve" in text


def test_report_without_calibration_says_so():
    assert "noch nicht eingemessen" in location().report()


def test_fluidnc_block_matches_the_derived_anchors():
    setup = location()
    block = setup.fluidnc_kinematics_yaml()
    anchors = setup.anchors()
    assert "WallPlotter:" in block
    assert f"left_anchor_x: {anchors.left_x:.3f}" in block
    assert f"right_anchor_y: {anchors.y:.3f}" in block
    assert "Standort: Keller" in block


def test_fluidnc_block_is_valid_yaml():
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(location().fluidnc_kinematics_yaml())
    wall = parsed["kinematics"]["WallPlotter"]
    assert wall["right_anchor_x"] - wall["left_anchor_x"] == pytest.approx(2300.0)


def test_book_roundtrip(tmp_path):
    path = tmp_path / "standorte.json"
    book = LocationBook()
    book.add(location(name="Keller", calibration=AreaCalibration(points=dict(CORNERS))))
    book.add(location(name="Werkstatt", anchor_span_mm=1800.0))
    book.save(path)

    loaded = LocationBook.load(path)
    assert set(loaded.locations) == {"Keller", "Werkstatt"}
    assert loaded.active == "Werkstatt"
    assert loaded.get("Keller").calibration.points == CORNERS
    assert loaded.get("Werkstatt").anchor_span_mm == 1800.0


def test_loading_a_missing_book_is_empty(tmp_path):
    assert LocationBook.load(tmp_path / "nichts.json").locations == {}


def test_a_malformed_calibration_block_is_a_clean_error():
    """"calibration.points" als Liste statt Wörterbuch — gültiges JSON, aber
    die falsche Form. from_dict() fing nur KeyError/TypeError/ValueError/
    IndexError ab, nicht den AttributeError von ``["x", "y"].items()``."""
    from wallplotter.location import Location

    with pytest.raises(LocationError, match="nicht lesbar"):
        Location.from_dict({
            "name": "Keller",
            "anchor_span_mm": 2300.0,
            "left_belt_zero_mm": 1500.0,
            "right_belt_zero_mm": 1500.0,
            "calibration": {"points": ["x", "y"], "note": ""},
        })


def test_unknown_location_lists_the_known_ones():
    book = LocationBook()
    book.add(location(name="Keller"))
    with pytest.raises(LocationError, match="Keller"):
        book.get("Garage")


def test_empty_book_says_nothing_is_set_up():
    with pytest.raises(LocationError, match="Noch kein Standort"):
        LocationBook().get()


def test_removing_the_active_location_picks_another():
    book = LocationBook()
    book.add(location(name="Keller"))
    book.add(location(name="Werkstatt"))
    book.remove("Werkstatt")
    assert book.active == "Keller"
