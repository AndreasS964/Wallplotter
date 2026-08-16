from wallplotter import cli
from wallplotter.cli import build_parser, main


def test_defaults_match_the_wall():
    args = build_parser().parse_args(["bild.svg"])
    assert args.width == 2000.0
    assert args.height == 2500.0
    assert args.margin == 50.0
    assert args.upload is False


def test_flags_are_parsed():
    args = build_parser().parse_args(
        ["foto.png", "--pitch", "2.5", "--host", "1.2.3.4", "--run", "--occult"]
    )
    assert args.pitch == 2.5
    assert args.host == "1.2.3.4"
    assert args.run and args.occult


def test_missing_input_returns_exit_code_2(tmp_path, capsys):
    assert main([str(tmp_path / "gibtsnicht.svg")]) == 2
    assert "nicht gefunden" in capsys.readouterr().err


def test_a_missing_output_directory_is_created(tmp_path):
    """`plot -o ausgabe/wand.gcode` meint fast immer genau das."""
    ziel = tmp_path / "ausgabe" / "wand.gcode"
    assert cli.main(["--pattern", "frame", "--width", "500", "--height", "500",
                     "-o", str(ziel), "--preview", str(tmp_path / "v" / "vor.svg")]) == 0
    assert ziel.exists()
    assert (tmp_path / "v" / "vor.svg").exists()


def test_an_unwritable_target_gives_a_message_not_a_traceback(tmp_path, capsys):
    """Ein Tippfehler im Pfad ist kein Softwarefehler und soll nicht so aussehen."""
    verzeichnis = tmp_path / "kein-dateiname.gcode"
    verzeichnis.mkdir()
    assert cli.main(["--pattern", "frame", "-o", str(verzeichnis)]) == 2
    assert "lässt sich nicht schreiben" in capsys.readouterr().err
