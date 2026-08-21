import pytest

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


def test_pattern_spacing_zero_is_not_silently_ignored(tmp_path, capsys):
    """0 ist ein gültiger, expliziter Wert — kein `or`-Fallback auf die
    Vorgabe. `grid` lehnt spacing=0 ab; das darf nicht durch einen
    Wahrheitswert-Test verschwinden, bevor es dort ankommt."""
    ziel = tmp_path / "raster.gcode"
    assert cli.main(["--pattern", "grid", "--pattern-spacing", "0", "-o", str(ziel)]) == 2
    assert "spacing" in capsys.readouterr().err
    assert not ziel.exists()


def test_pitch_zero_reaches_the_technique_instead_of_using_the_default(tmp_path, capsys):
    """`--pitch 0` muss die Validierung von `spiral()` erreichen, nicht auf
    den Vorgabe-Bahnabstand zurückfallen, weil 0 als falsy verschwindet."""
    Image = pytest.importorskip("PIL.Image")
    photo = tmp_path / "foto.png"
    Image.new("L", (20, 20), 128).save(photo)

    assert cli.main([str(photo), "--technique", "spiral", "--pitch", "0",
                     "-o", str(tmp_path / "aus.gcode")]) == 3
    assert "größer als 0" in capsys.readouterr().err


def test_adaptive_feed_without_a_location_does_not_block_layers(tmp_path, capsys):
    """--layers behandelt --adaptive-feed als reinen Hinweis (siehe die
    `ignored`-Liste dort) — der frühe Abbruch für den einfarbigen Zweig darf
    diese Kombination nicht schon vorher mit einer unpassenden Fehlermeldung
    verhindern, nur weil kein --location angegeben ist."""
    pytest.importorskip("vpype_cli")
    svg = tmp_path / "bunt.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
        'viewBox="0 0 100 100">'
        '<rect x="10" y="10" width="80" height="80" fill="none" stroke="#000000"/>'
        '<circle cx="50" cy="50" r="30" fill="none" stroke="#e02020"/></svg>',
        encoding="utf-8",
    )
    result = cli.main([str(svg), "--layers", "--adaptive-feed", "-o", str(tmp_path / "aus.gcode")])
    assert result == 0
    assert "--adaptive-feed" in capsys.readouterr().err
