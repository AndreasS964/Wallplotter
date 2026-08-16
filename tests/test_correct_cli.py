"""Der Weg von der Messung zur Vorverzerrung.

Vorher gab es `plot --correction` für ein Dateiformat, das die Toolchain gar
nicht herstellen konnte. Geprüft wird deshalb die ganze Kette: Raster plotten,
Werte eintragen, anpassen, verwenden.
"""

import json

import pytest

from wallplotter import correct_cli
from wallplotter.correction import load_correction


# Eine Verzerrung, wie sie Riemendehnung erzeugt: kissenförmig, nicht affin
def verzerre(x: float, y: float) -> tuple[float, float]:
    return (
        x + 0.0006 * (x - 1000) * (y - 1250) / 1000,
        y - 0.0009 * (y - 1250) ** 2 / 2500 + 0.4,
    )


@pytest.fixture
def measured(tmp_path):
    """Eine ausgefüllte Messwertdatei, wie sie nach dem Nachmessen aussieht."""
    path = tmp_path / "messpunkte.json"
    assert correct_cli.main(["--measurements", str(path), "messen", "--steps", "4"]) == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["punkte"]:
        entry["ist"] = [round(value, 2) for value in verzerre(*entry["soll"])]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_raster_writes_plottable_gcode(tmp_path, capsys):
    target = tmp_path / "raster.gcode"
    assert correct_cli.main(["raster", "--steps", "3", "-o", str(target)]) == 0
    text = target.read_text(encoding="utf-8")
    assert text.strip().endswith("M2 ; Programmende")
    assert text.count("G1 X") == 9 * 2      # neun Kreuze aus je zwei Strichen
    assert "9 Kreuze" in capsys.readouterr().out


def test_template_lists_every_point_with_an_empty_slot(tmp_path):
    path = tmp_path / "m.json"
    assert correct_cli.main(["--measurements", str(path), "messen", "--steps", "3"]) == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["punkte"]) == 9
    assert all(entry["ist"] is None for entry in data["punkte"])
    assert "eintragen" in data["hinweis"]


def test_fitting_removes_the_distortion(measured, tmp_path, capsys):
    out = tmp_path / "korrektur.json"
    assert correct_cli.main(
        ["--measurements", str(measured), "anpassen", "-o", str(out)]
    ) == 0
    report = capsys.readouterr().out
    assert "kubisch" in report               # 16 Punkte tragen den höchsten Grad
    assert "Größter Fehler" in report

    correction = load_correction(out)
    worst = max(
        abs(correction.forward((x, y))[0] - verzerre(x, y)[0])
        for x in (100.0, 800.0, 1500.0)
        for y in (100.0, 1200.0, 2300.0)
    )
    assert worst < 0.05


def test_a_useless_fit_says_so(tmp_path, capsys):
    """Eine Korrektur, die nichts wegnimmt, verschleiert nur — das gehört gesagt."""
    path = tmp_path / "m.json"
    correct_cli.main(["--measurements", str(path), "messen", "--steps", "3"])
    data = json.loads(path.read_text(encoding="utf-8"))
    rauschen = [0.4, -0.3, 0.5, -0.2, 0.3, -0.5, 0.2, -0.4, 0.35]
    for entry, noise in zip(data["punkte"], rauschen, strict=True):
        entry["ist"] = [entry["soll"][0] + noise, entry["soll"][1] - noise]
    path.write_text(json.dumps(data), encoding="utf-8")

    assert correct_cli.main(
        ["--measurements", str(path), "anpassen", "-o", str(tmp_path / "k.json")]
    ) == 0
    assert "nimmt kaum etwas weg" in capsys.readouterr().err


def test_fitting_without_measurements_is_refused(tmp_path, capsys):
    assert correct_cli.main(
        ["--measurements", str(tmp_path / "weg.json"), "anpassen"]
    ) == 3
    assert "messen" in capsys.readouterr().err


def test_empty_measurements_are_refused(tmp_path, capsys):
    path = tmp_path / "m.json"
    correct_cli.main(["--measurements", str(path), "messen", "--steps", "3"])
    assert correct_cli.main(["--measurements", str(path), "anpassen"]) == 3
    assert "keinem Punkt ein Ist-Wert" in capsys.readouterr().err


def test_stretch_model_needs_a_location(measured, capsys):
    assert correct_cli.main(
        ["--measurements", str(measured), "anpassen", "--model", "dehnung"]
    ) == 2
    assert "--location" in capsys.readouterr().err


def test_broken_measurement_file_is_reported(tmp_path, capsys):
    path = tmp_path / "kaputt.json"
    path.write_text("{kein json", encoding="utf-8")
    assert correct_cli.main(["--measurements", str(path), "anpassen"]) == 3
    assert "nicht lesbar" in capsys.readouterr().err


def test_the_result_is_what_plot_consumes(measured, tmp_path, monkeypatch):
    """Der Zweck der Übung: `plot --correction` muss die Datei nehmen."""
    from wallplotter import cli

    out = tmp_path / "korrektur.json"
    correct_cli.main(["--measurements", str(measured), "anpassen", "-o", str(out)])

    monkeypatch.chdir(tmp_path)
    assert cli.main(
        ["--pattern", "frame", "--width", "800", "--height", "800",
         "--correction", str(out), "-o", str(tmp_path / "p.gcode")]
    ) == 0
    assert (tmp_path / "p.gcode").exists()
