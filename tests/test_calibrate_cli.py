"""Die CLI gegen einen simulierten Client — ohne Board im Netz."""

import sys
from pathlib import Path

import pytest

from wallplotter import calibrate_cli
from wallplotter.calibration import AreaCalibration
from wallplotter.upload import FluidNCClient, TelnetChannel

sys.path.insert(0, str(Path(__file__).parent))

from fluidnc_fake import FakeFluidNCSocket, FakeSession, opener_for  # noqa: E402


class Board:
    """Gegenstelle mit beiden Kanälen: HTTP für Dateien, TCP für Kommandos."""

    def __init__(self, status="<Idle|MPos:120.000,340.000,0.000>"):
        self.session = FakeSession()
        self.socket = FakeFluidNCSocket(status=status)

    @property
    def commands(self) -> list[str]:
        """Alle GCode-/$-Zeilen, die tatsächlich am Board angekommen sind."""
        return self.socket.lines

    def client(self, config) -> FluidNCClient:
        channel = TelnetChannel(config.hostname, config.telnet_port, 5.0, opener_for(self.socket))
        return FluidNCClient(config, self.session, channel)


@pytest.fixture
def board(monkeypatch):
    fake = Board()
    monkeypatch.setattr(calibrate_cli, "FluidNCClient", fake.client)
    return fake


def test_zero_sends_g92(board, capsys):
    assert calibrate_cli.main(["zero"]) == 0
    assert board.commands == ["G92 X0.000 Y0.000"]
    assert "Nullpunkt" in capsys.readouterr().out


def test_jog_passes_the_distance(board):
    assert calibrate_cli.main(["jog", "--dx", "-50", "--dy", "10"]) == 0
    assert board.commands == ["$J=G91 G21 X-50.000 Y10.000 F1000"]


def test_record_reads_the_position_from_the_board(board, tmp_path):
    path = tmp_path / "cal.json"
    assert calibrate_cli.main(["--file", str(path), "record", "bottom-left"]) == 0
    assert AreaCalibration.load(path).points == {"bottom-left": (120.0, 340.0)}


def test_record_accepts_a_manual_position(board, tmp_path):
    path = tmp_path / "cal.json"
    calibrate_cli.main(["--file", str(path), "record", "top-right", "--at", "1900", "2400"])
    assert AreaCalibration.load(path).points["top-right"] == (1900.0, 2400.0)
    assert board.commands == []  # Board wurde gar nicht gefragt


def test_record_reports_what_is_still_missing(board, tmp_path, capsys):
    path = tmp_path / "cal.json"
    calibrate_cli.main(["--file", str(path), "record", "bottom-left"])
    assert "Fehlt noch" in capsys.readouterr().out


def test_two_diagonal_corners_complete_the_calibration(board, tmp_path, capsys):
    path = tmp_path / "cal.json"
    calibrate_cli.main(["--file", str(path), "record", "bottom-left", "--at", "0", "0"])
    capsys.readouterr()
    calibrate_cli.main(["--file", str(path), "record", "top-right", "--at", "1800", "2200"])
    assert "1800 × 2200 mm" in capsys.readouterr().out


def test_goto_jogs_to_the_stored_corner(board, tmp_path):
    path = tmp_path / "cal.json"
    calibrate_cli.main(["--file", str(path), "record", "top-left", "--at", "100", "2400"])
    assert calibrate_cli.main(["--file", str(path), "goto", "top-left"]) == 0
    assert board.commands == ["$J=G90 G21 X100.000 Y2400.000 F1000"]


def test_goto_uncalibrated_corner_fails_cleanly(board, tmp_path, capsys):
    path = tmp_path / "cal.json"
    calibrate_cli.main(["--file", str(path), "record", "top-left", "--at", "1", "2"])
    assert calibrate_cli.main(["--file", str(path), "goto", "bottom-right"]) == 4
    assert "nicht kalibriert" in capsys.readouterr().err


def test_show_without_file_lists_what_is_missing(board, tmp_path, capsys):
    # kein Fehler, sondern die nützlichere Auskunft: es ist noch nichts aufgenommen
    assert calibrate_cli.main(["--file", str(tmp_path / "weg.json"), "show"]) == 0
    out = capsys.readouterr().out
    assert "es fehlen" in out
    assert "bottom-left" in out


def test_clear_empties_the_calibration(board, tmp_path):
    path = tmp_path / "cal.json"
    calibrate_cli.main(["--file", str(path), "record", "top-left", "--at", "1", "2"])
    assert calibrate_cli.main(["--file", str(path), "clear"]) == 0
    assert AreaCalibration.load(path).points == {}


def test_zero_can_be_restored_from_a_calibrated_corner(board, tmp_path):
    path = tmp_path / "cal.json"
    calibrate_cli.main(["--file", str(path), "record", "bottom-left", "--at", "120", "210"])
    assert calibrate_cli.main(["--file", str(path), "zero", "--corner", "bottom-left"]) == 0
    assert board.commands == ["G92 X120.000 Y210.000"]


def test_restoring_from_an_uncalibrated_corner_fails(board, tmp_path):
    path = tmp_path / "cal.json"
    assert calibrate_cli.main(["--file", str(path), "zero", "--corner", "top-right"]) == 4
