import pytest

from wallplotter.pipeline import svg_to_lines

pytest.importorskip("vpype_cli", reason="vpype ist optional (Extra: geometry)")

SQUARE_SVG = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm"
     viewBox="0 0 100 100">
  <rect x="10" y="10" width="80" height="80" fill="none" stroke="black"/>
</svg>
"""


def test_svg_to_lines_returns_millimetres():
    lines = svg_to_lines(SQUARE_SVG)
    assert lines
    xs = [x for line in lines for x, _ in line]
    ys = [y for line in lines for _, y in line]
    # Das SVG ist in mm bemaßt, das Rechteck liegt bei 10..90 mm
    assert min(xs) == pytest.approx(10.0, abs=0.5)
    assert max(xs) == pytest.approx(90.0, abs=0.5)
    assert min(ys) == pytest.approx(10.0, abs=0.5)
    assert max(ys) == pytest.approx(90.0, abs=0.5)


def test_layers_with_the_same_colour_are_merged_not_lost(tmp_path):
    """Inkscape mischt Farbschreibweisen — für den Stiftkasten ist das eine Farbe.

    `black`, `#000000` und `rgb(0,0,0)` sind für vpype drei Ebenen. Als
    Schlüssel des Ergebnis-Wörterbuchs kollidierten sie, und alle bis auf die
    letzte verschwanden lautlos: aus dem Plot fehlten Linien, ohne Meldung.
    """
    pytest.importorskip("vpype")
    from wallplotter.pipeline import svg_to_layers

    svg = tmp_path / "gemischt.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" '
        'viewBox="0 0 100 100">'
        '<path d="M0,0 L10,0" stroke="black" fill="none"/>'
        '<path d="M0,10 L10,10" stroke="#000000" fill="none"/>'
        '<path d="M0,20 L10,20" stroke="rgb(0,0,0)" fill="none"/>'
        '<path d="M0,30 L10,30" stroke="#e02020" fill="none"/>'
        "</svg>",
        encoding="utf-8",
    )
    layers = svg_to_layers(svg)
    by_label = {layer.label: layer for layer in layers}
    assert set(by_label) == {"#000000", "#e02020"}
    # alle drei schwarzen Striche sind noch da
    assert len(by_label["#000000"].lines) == 3
