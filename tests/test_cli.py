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
