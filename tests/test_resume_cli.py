"""Die CLI zum Fortsetzen — ohne Board, mit gestelltem Status."""

from types import SimpleNamespace

import pytest

from wallplotter import resume_cli
from wallplotter.config import PlotConfig
from wallplotter.gcode import lines_to_gcode
from wallplotter.upload import FluidNCError, MachineStatus

CONFIG = PlotConfig(width_mm=1000, height_mm=1000, margin_mm=50, invert_y=False)
LINES = [
    [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)],
    [(200.0, 200.0), (300.0, 200.0)],
    [(400.0, 0.0), (400.0, 100.0), (500.0, 100.0)],
]


@pytest.fixture
def gcode_file(tmp_path):
    path = tmp_path / "wand.gcode"
    path.write_text(lines_to_gcode(LINES, CONFIG), encoding="utf-8")
    return path


def test_percent_writes_a_rest_program(gcode_file, capsys):
    assert resume_cli.main([str(gcode_file), "--percent", "70"]) == 0
    rest = gcode_file.with_name("wand-rest.gcode")
    assert rest.exists()
    assert rest.read_text(encoding="utf-8").strip().endswith("M2 ; Programmende")
    out = capsys.readouterr().out
    assert "Noch zu zeichnen" in out
    assert "Nullpunkt herstellen" in out       # der Punkt, den man sonst vergisst


def test_own_output_path(gcode_file, tmp_path):
    target = tmp_path / "weiter.gcode"
    assert resume_cli.main([str(gcode_file), "--line", "16", "-o", str(target)]) == 0
    assert target.exists()


def test_missing_file(tmp_path, capsys):
    assert resume_cli.main([str(tmp_path / "weg.gcode"), "--percent", "50"]) == 2
    assert "nicht gefunden" in capsys.readouterr().err


def test_finished_plot_is_refused(gcode_file, capsys):
    assert resume_cli.main([str(gcode_file), "--percent", "99.9"]) == 4
    assert "schon durch" in capsys.readouterr().err


def test_from_board_uses_the_reported_progress(gcode_file, monkeypatch, capsys):
    import wallplotter.upload as upload

    status = MachineStatus(state="Hold", sd_percent=70.0, sd_file="/wand.gcode")
    monkeypatch.setattr(
        upload, "FluidNCClient", lambda *a, **k: SimpleNamespace(status=lambda: status)
    )
    assert resume_cli.main([str(gcode_file), "--from-board"]) == 0
    assert "Board meldet 70.0 %" in capsys.readouterr().out


def test_from_board_without_a_known_job(gcode_file, monkeypatch, capsys):
    """Nach einem Stromausfall kennt das Board den Job nicht mehr."""
    import wallplotter.upload as upload

    status = MachineStatus(state="Idle")
    monkeypatch.setattr(upload, "FluidNCClient", lambda *a, **k: SimpleNamespace(status=lambda: status))
    assert resume_cli.main([str(gcode_file), "--from-board"]) == 4
    assert "--percent" in capsys.readouterr().err


def test_from_board_when_the_board_is_silent(gcode_file, monkeypatch, capsys):
    import wallplotter.upload as upload

    def boom():
        raise FluidNCError("keine Antwort")

    monkeypatch.setattr(upload, "FluidNCClient", lambda *a, **k: SimpleNamespace(status=boom))
    assert resume_cli.main([str(gcode_file), "--from-board"]) == 5
    assert "keine Antwort" in capsys.readouterr().err


def test_upload_is_offered(gcode_file, monkeypatch, capsys):
    import wallplotter.upload as upload

    seen = {}

    def fake_upload(text, name, config, run=True):
        seen.update(name=name, run=run, size=len(text))
        return f"/{name}"

    monkeypatch.setattr(upload, "upload_and_run", fake_upload)
    assert resume_cli.main([str(gcode_file), "--percent", "70", "--run"]) == 0
    assert seen["name"] == "wand-rest.gcode"
    assert seen["run"] is True
    assert "gestartet" in capsys.readouterr().out
