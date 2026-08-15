"""Vorschau und Kommando-Bau — beides läuft ohne installiertes vpype."""

from wallplotter.pipeline import lines_to_svg, optimize_commands


def test_optimize_commands_default_order():
    cmds = optimize_commands()
    assert cmds[0].startswith("linesimplify")
    assert cmds[-1] == "linesort"
    assert "occult" not in cmds


def test_optimize_commands_can_disable_everything():
    assert (
        optimize_commands(
            merge_tolerance_mm=0, simplify_tolerance_mm=0, reloop=False, sort=False
        )
        == []
    )


def test_optimize_commands_with_occult():
    assert "occult" in optimize_commands(remove_hidden=True)


def test_lines_to_svg_contains_polyline_and_travel():
    svg = lines_to_svg(
        [[(0.0, 0.0), (10.0, 10.0)], [(20.0, 20.0), (30.0, 30.0)]],
        100,
        100,
        travel_stroke="#d64545",
    )
    assert svg.startswith("<svg")
    assert svg.count("<polyline") == 2
    assert "stroke-dasharray" in svg
    assert svg.rstrip().endswith("</svg>")


def test_lines_to_svg_without_travel_has_no_dashes():
    svg = lines_to_svg([[(0.0, 0.0), (1.0, 1.0)]], 10, 10)
    assert "stroke-dasharray" not in svg
