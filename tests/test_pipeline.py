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
