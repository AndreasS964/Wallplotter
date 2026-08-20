"""Der Selbsttest muss selbst geprüft sein — sonst beruhigt er grundlos."""

import pytest

from wallplotter.calibration import AreaCalibration
from wallplotter.doctor import (
    FAIL,
    OK,
    SKIP,
    WARN,
    Check,
    check_core,
    check_firmware_config,
    check_locations,
    main,
    run_checks,
)
from wallplotter.location import Location, LocationBook

CORNERS = {
    "bottom-left": (100.0, -2400.0),
    "bottom-right": (1900.0, -2400.0),
    "top-right": (1900.0, -300.0),
    "top-left": (100.0, -300.0),
}


def status_of(checks, name: str) -> str:
    return next(check.status for check in checks if check.name == name)


@pytest.fixture
def book_path(tmp_path):
    book = LocationBook()
    location = Location(
        name="Keller",
        anchor_span_mm=2300.0,
        left_belt_zero_mm=1500.0,
        right_belt_zero_mm=1500.0,
        calibration=AreaCalibration(points=dict(CORNERS)),
    )
    book.add(location)
    path = tmp_path / "standorte.json"
    book.save(path)
    return path


def test_core_check_passes_on_a_healthy_install():
    checks = check_core()
    assert all(check.status == OK for check in checks), [str(c) for c in checks]


def test_core_check_covers_the_whole_chain():
    names = {check.name for check in check_core()}
    assert {"GCode erzeugen", "Programmrahmen", "Geometrie in der Fläche"} <= names


def test_missing_locations_are_an_open_point_not_an_error(tmp_path):
    checks = check_locations(tmp_path / "gibtsnicht.json")
    assert status_of(checks, "Standorte") == SKIP
    assert "wallplotter-location new" in checks[0].hint


def test_a_calibrated_location_checks_out(book_path):
    checks = check_locations(book_path)
    assert status_of(checks, "Standorte") == OK
    assert status_of(checks, "Ankermaße") == OK
    assert status_of(checks, "Fläche eingemessen") == OK
    assert status_of(checks, "Auflösung") == OK


def test_an_uncalibrated_location_names_the_missing_corners(tmp_path):
    book = LocationBook()
    book.add(Location("Werkstatt", 1800.0, 1200.0, 1200.0))
    path = tmp_path / "st.json"
    book.save(path)
    checks = check_locations(path)
    assert status_of(checks, "Fläche eingemessen") == SKIP
    assert "bottom-left" in next(c for c in checks if c.name == "Fläche eingemessen").detail


def test_a_broken_locations_file_is_an_error(tmp_path):
    path = tmp_path / "kaputt.json"
    path.write_text("{kein json", encoding="utf-8")
    assert status_of(check_locations(path), "Standorte") == FAIL


def test_firmware_config_mismatch_is_reported(tmp_path, book_path):
    """Der Fall, der ein Wandbild unbemerkt schief macht: Firmware und
    Standort rechnen mit verschiedenen Ankern."""
    pytest.importorskip("yaml")
    config = tmp_path / "config.yaml"
    config.write_text(
        "kinematics:\n"
        "  WallPlotter:\n"
        "    left_anchor_x: -1150.0\n"
        "    right_anchor_x: 1150.0\n"
        "    left_anchor_y: 500.0\n"          # daneben: der Standort sagt 963
        "    right_anchor_y: 500.0\n",
        encoding="utf-8",
    )
    checks = check_firmware_config(config, book_path)
    check = next(c for c in checks if c.name == "Firmware-Konfiguration")
    assert check.status == WARN
    assert "left_anchor_y" in check.detail
    assert "wallplotter-firmware config" in check.hint


def test_matching_firmware_config_is_fine(tmp_path, book_path):
    pytest.importorskip("yaml")
    location = LocationBook.load(book_path).get()
    config = tmp_path / "config.yaml"
    config.write_text(location.fluidnc_kinematics_yaml(), encoding="utf-8")
    assert status_of(check_firmware_config(config, book_path), "Firmware-Konfiguration") == OK


def test_a_config_written_by_the_generator_is_reported_as_generated(tmp_path, book_path):
    """Der ganze Sinn der Sache: die Datei stammt aus dem Werkzeug."""
    pytest.importorskip("yaml")
    from wallplotter.firmware import FirmwareConfig

    location = LocationBook.load(book_path).get()
    config = tmp_path / "config.yaml"
    config.write_text(FirmwareConfig.from_location(location).render(), encoding="utf-8")

    checks = check_firmware_config(config, book_path)
    assert status_of(checks, "Erzeugt") == OK
    assert status_of(checks, "Firmware-Konfiguration") == OK
    assert status_of(checks, "config.yaml gegen FluidNC") == OK


def test_a_hand_written_config_is_flagged_as_drifted(tmp_path, book_path):
    pytest.importorskip("yaml")
    from wallplotter.firmware import FirmwareConfig

    location = LocationBook.load(book_path).get()
    config = tmp_path / "config.yaml"
    config.write_text(
        FirmwareConfig.from_location(location).render().replace("pulse_us: 4", "pulse_us: 5"),
        encoding="utf-8",
    )
    checks = check_firmware_config(config, book_path)
    assert status_of(checks, "Erzeugt") == WARN
    assert "diff" in next(c for c in checks if c.name == "Erzeugt").hint


def test_a_key_fluidnc_does_not_know_is_a_failure(tmp_path, book_path):
    """`laser_mode` stand einmal wirklich in dieser Datei — das Board wäre
    damit gar nicht erst gefahren."""
    pytest.importorskip("yaml")
    config = tmp_path / "config.yaml"
    config.write_text(
        "kinematics:\n  WallPlotter:\n    left_anchor_x: -1150.0\n"
        "Laser:\n  laser_mode: false\n",
        encoding="utf-8",
    )
    checks = check_firmware_config(config, book_path)
    check = next(c for c in checks if c.name == "config.yaml gegen FluidNC")
    assert check.status == FAIL
    assert "laser_mode" in check.detail
    assert "ConfigAlarm" in check.hint


def test_missing_firmware_config_is_an_open_point(tmp_path, book_path):
    assert status_of(check_firmware_config(tmp_path / "weg.yaml", book_path), "Firmware-Konfiguration") == SKIP


def test_run_checks_without_a_board_skips_the_board_section(book_path):
    sections = dict(run_checks(host=None, locations_path=book_path))
    assert "Board" not in sections
    assert {"Installation", "Kern", "Standort und Fläche", "Firmware"} <= set(sections)


def test_unreachable_board_is_an_open_point_not_a_failure(book_path):
    """Solange nichts an der Wand hängt, ist ein stummes Board kein Fehler."""
    sections = dict(run_checks(host="192.0.2.1", locations_path=book_path))
    assert status_of(sections["Board"], "Board erreichbar") == SKIP


def test_main_returns_zero_when_nothing_is_broken(book_path, capsys):
    assert main(["--no-board", "--locations", str(book_path)]) == 0
    assert "Kern" in capsys.readouterr().out


def test_check_renders_mark_detail_and_hint():
    assert str(Check("Sache", OK, "geht")) == "✓ Sache: geht"
    assert "→ tu dies" in str(Check("Sache", FAIL, "geht nicht", "tu dies"))


def test_a_locations_file_of_the_wrong_shape_is_reported(tmp_path):
    """Eine JSON-Datei, deren oberste Ebene keine Abbildung ist, warf einen
    AttributeError aus dem Selbsttest heraus — ausgerechnet dort."""
    path = tmp_path / "falsch.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    checks = check_locations(path)
    assert status_of(checks, "Standorte") == FAIL
    assert "keine Standortdatei" in checks[0].hint


def test_a_comment_behind_a_number_is_a_failure(tmp_path, book_path):
    """FluidNC schneidet Kommentare am Zeilenende nicht ab — ein YAML-Parser
    sieht daran nichts, das Board geht in ConfigAlarm."""
    pytest.importorskip("yaml")
    config = tmp_path / "config.yaml"
    config.write_text(
        "kinematics:\n  WallPlotter:\n    left_anchor_x: -1150.0\n"
        "stepping:\n  idle_ms: 255 # Motoren gehalten lassen\n",
        encoding="utf-8",
    )
    checks = check_firmware_config(config, book_path)
    check = next(c for c in checks if c.name == "config.yaml gegen FluidNC")
    assert check.status == FAIL
    assert "ConfigAlarm" in check.detail or "ConfigAlarm" in check.hint
