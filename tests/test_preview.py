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


def test_a_pen_stroke_stays_visible_on_a_whole_wall():
    """Maßstabsgetreu wäre die Vorschau leer.

    Ein Fineliner zieht 0,3 mm. Auf 2000 mm Wand sind das im Browser 0,15
    Bildpunkte — die Zeichnung verschwindet, obwohl alles richtig gerechnet
    ist. Der Strich bekommt deshalb eine Untergrenze.
    """
    import re

    breit = lines_to_svg([[(0.0, 0.0), (1000.0, 1000.0)]], 2000, 2500, stroke_width_mm=0.3)
    dick = float(re.search(r'<polyline[^>]*stroke-width="([\d.]+)"', breit).group(1))
    assert dick >= 4.0

    # Auf einer kleinen Fläche bleibt es beim wahren Maß
    klein = lines_to_svg([[(0.0, 0.0), (10.0, 10.0)]], 50, 50, stroke_width_mm=0.3)
    fein = float(re.search(r'<polyline[^>]*stroke-width="([\d.]+)"', klein).group(1))
    assert fein == 0.3


def test_a_marker_still_draws_thicker_than_a_fineliner():
    """Die Untergrenze darf die Werkzeuge nicht einebnen."""
    import re

    def dicke(width_mm):
        svg = lines_to_svg([[(0.0, 0.0), (100.0, 100.0)]], 400, 400, stroke_width_mm=width_mm)
        return float(re.search(r'<polyline[^>]*stroke-width="([\d.]+)"', svg).group(1))

    assert dicke(3.0) > dicke(0.3)
