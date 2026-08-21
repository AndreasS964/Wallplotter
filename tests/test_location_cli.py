"""Die CLI für Standorte — ohne Board im Netz, mit einer simulierten Karte."""

import sys
from pathlib import Path

import pytest

import wallplotter.upload as upload_module
from wallplotter import location_cli
from wallplotter.location import LocationBook
from wallplotter.upload import FluidNCClient

sys.path.insert(0, str(Path(__file__).parent))

from fluidnc_fake import FakeSession  # noqa: E402


class FakeCard(FakeSession):
    """Simulierte Karte, wie in ``tests/test_sdstore.py``."""

    def __init__(self, content: str | None = None):
        from wallplotter.sdstore import REMOTE_LOCATIONS  # noqa: PLC0415

        super().__init__(files={f"/{REMOTE_LOCATIONS}": content} if content is not None else None)


@pytest.fixture
def card(monkeypatch) -> FakeCard:
    """Ersetzt ``FluidNCClient`` durch eine Fassung mit simulierter Karte.

    ``location_cli`` importiert ``FluidNCClient`` erst innerhalb von ``main()``
    (``from .upload import FluidNCClient``) — das Ersetzen des Modulattributs
    vorher greift deshalb genauso wie eine echte Injektion.
    """
    fake = FakeCard()
    monkeypatch.setattr(upload_module, "FluidNCClient", lambda config: FluidNCClient(config, fake))
    return fake


def test_new_creates_and_activates_a_location(tmp_path, capsys):
    path = tmp_path / "standorte.json"
    rc = location_cli.main(
        ["--file", str(path), "new", "Keller", "--span", "2300", "--left", "1450", "--right", "1470"]
    )
    assert rc == 0
    book = LocationBook.load(path)
    assert book.active == "Keller"
    assert book.get("Keller").anchor_span_mm == 2300.0
    out = capsys.readouterr().out
    assert "Keller" in out
    assert "wallplotter-calibrate" in out


def test_new_rejects_impossible_measurements(tmp_path, capsys):
    path = tmp_path / "standorte.json"
    # Dreiecksungleichung verletzt: die Riemen sind viel zu kurz für die Spannweite
    rc = location_cli.main(
        ["--file", str(path), "new", "Keller", "--span", "2300", "--left", "10", "--right", "10"]
    )
    assert rc == 3
    assert "Dreieck" in capsys.readouterr().err
    assert not path.exists()


def test_list_without_any_location(tmp_path, capsys):
    path = tmp_path / "standorte.json"
    assert location_cli.main(["--file", str(path), "list"]) == 0
    assert "kein Standort" in capsys.readouterr().out


def test_list_marks_the_active_location_and_calibration_state(tmp_path, capsys):
    path = tmp_path / "standorte.json"
    location_cli.main(
        ["--file", str(path), "new", "Keller", "--span", "2300", "--left", "1450", "--right", "1470"]
    )
    location_cli.main(
        ["--file", str(path), "new", "Werkstatt", "--span", "2000", "--left", "1400", "--right", "1400"]
    )
    capsys.readouterr()
    assert location_cli.main(["--file", str(path), "list"]) == 0
    out = capsys.readouterr().out
    assert "* Werkstatt" in out  # zuletzt angelegt, also aktiv
    assert "  Keller" in out
    assert "Fläche fehlt noch" in out


def test_show_defaults_to_the_active_location(tmp_path, capsys):
    path = tmp_path / "standorte.json"
    location_cli.main(
        ["--file", str(path), "new", "Keller", "--span", "2300", "--left", "1450", "--right", "1470"]
    )
    capsys.readouterr()
    assert location_cli.main(["--file", str(path), "show"]) == 0
    assert "Keller" in capsys.readouterr().out


def test_show_unknown_location_is_a_clean_error(tmp_path, capsys):
    path = tmp_path / "standorte.json"
    assert location_cli.main(["--file", str(path), "show", "Nirgendwo"]) == 3
    assert "unbekannt" in capsys.readouterr().err


def test_use_switches_the_active_location(tmp_path, capsys):
    path = tmp_path / "standorte.json"
    location_cli.main(
        ["--file", str(path), "new", "Keller", "--span", "2300", "--left", "1450", "--right", "1470"]
    )
    location_cli.main(
        ["--file", str(path), "new", "Werkstatt", "--span", "2000", "--left", "1400", "--right", "1400"]
    )
    capsys.readouterr()
    assert location_cli.main(["--file", str(path), "use", "Keller"]) == 0
    assert "Keller" in capsys.readouterr().out
    assert LocationBook.load(path).active == "Keller"


def test_remove_falls_back_to_another_location_when_the_active_one_goes(tmp_path):
    path = tmp_path / "standorte.json"
    location_cli.main(
        ["--file", str(path), "new", "Keller", "--span", "2300", "--left", "1450", "--right", "1470"]
    )
    location_cli.main(
        ["--file", str(path), "new", "Werkstatt", "--span", "2000", "--left", "1400", "--right", "1400"]
    )
    assert location_cli.main(["--file", str(path), "remove", "Werkstatt"]) == 0
    book = LocationBook.load(path)
    assert set(book.locations) == {"Keller"}
    assert book.active == "Keller"


def test_remove_unknown_location_is_a_clean_error(tmp_path, capsys):
    path = tmp_path / "standorte.json"
    assert location_cli.main(["--file", str(path), "remove", "Nirgendwo"]) == 3
    assert "unbekannt" in capsys.readouterr().err


def test_config_prints_only_the_kinematics_block(tmp_path, capsys):
    path = tmp_path / "standorte.json"
    location_cli.main(
        ["--file", str(path), "new", "Keller", "--span", "2300", "--left", "1450", "--right", "1470"]
    )
    capsys.readouterr()
    assert location_cli.main(["--file", str(path), "config"]) == 0
    captured = capsys.readouterr()
    assert "kinematics:" in captured.out
    assert "left_anchor_x" in captured.out
    assert "config.yaml" not in captured.out  # der Hinweis auf `wallplotter-firmware` geht nach stderr
    assert "wallplotter-firmware config" in captured.err


def test_config_can_write_to_a_file(tmp_path):
    path = tmp_path / "standorte.json"
    out_path = tmp_path / "kinematics.yaml"
    location_cli.main(
        ["--file", str(path), "new", "Keller", "--span", "2300", "--left", "1450", "--right", "1470"]
    )
    assert location_cli.main(["--file", str(path), "config", "--out", str(out_path)]) == 0
    assert "kinematics:" in out_path.read_text(encoding="utf-8")


def test_push_and_pull_roundtrip(card, tmp_path):
    path = tmp_path / "standorte.json"
    location_cli.main(
        ["--file", str(path), "new", "Keller", "--span", "2300", "--left", "1450", "--right", "1470"]
    )
    assert location_cli.main(["--file", str(path), "push"]) == 0

    other = tmp_path / "leer.json"
    LocationBook().save(other)
    assert location_cli.main(["--file", str(other), "pull"]) == 0
    assert set(LocationBook.load(other).locations) == {"Keller"}


def test_pull_from_an_empty_board_is_reported_as_an_error(card, tmp_path, capsys):
    path = tmp_path / "standorte.json"
    LocationBook().save(path)
    assert location_cli.main(["--file", str(path), "pull"]) == 5
    err = capsys.readouterr().err
    assert "404" in err or "fehlgeschlagen" in err
