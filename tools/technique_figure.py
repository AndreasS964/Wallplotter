#!/usr/bin/env python3
"""Vergleichsbild der vier Bildverfahren für die Dokumentation erzeugen.

Gezeichnet wird *aus* :mod:`wallplotter.imaging` und mit den Zahlen aus
:mod:`wallplotter.gcode` — damit veraltet die Abbildung nicht neben dem Code,
und die Angaben unter den Bildern sind gerechnet statt geschätzt.

    python tools/technique_figure.py

Die Vorlage ist bewusst synthetisch: Verlauf, dunkler Kreis, schwarzer Balken.
An einem echten Foto sähe man den Charakter der Verfahren schlechter, weil das
Motiv den Blick zieht — hier zeigt jedes Feld genau das, was das Verfahren mit
Helligkeit macht.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wallplotter.config import PlotConfig  # noqa: E402
from wallplotter.gcode import stats_for  # noqa: E402
from wallplotter.imaging import image_to_lines  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "images" / "verfahren.png"

AREA_MM = (2000.0, 2500.0)
MARGIN_MM = 50.0

TECHNIQUES = ("hatch", "stipple", "tsp", "spiral")
OPTIONS: dict[str, dict[str, float]] = {
    "hatch": {"pitch_mm": 12.0},
    "stipple": {"spacing_px": 2.0},
    "tsp": {"spacing_px": 2.0},
    "spiral": {"pitch_mm": 25.0},
}

PANEL = 420
GAP = 36
TOP = 96
PAD = 24


def source_image(size: int = 200):
    """Verlauf von hell nach dunkel, dunkler Kreis, schwarzer Balken."""
    from PIL import Image, ImageDraw

    image = Image.new("L", (size, int(size * 1.25)), 255)
    draw = ImageDraw.Draw(image)
    for y in range(image.height):
        draw.line([(0, y), (image.width, y)], fill=int(255 - 150 * y / image.height))
    draw.ellipse(
        [size * 0.22, size * 0.28, size * 0.78, size * 0.84], fill=48
    )
    draw.rectangle([size * 0.14, size * 1.02, size * 0.86, size * 1.13], fill=0)
    return image


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    for name in (
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans-{'Bold' if bold else 'Regular'}.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def build(out: Path) -> Path:
    from PIL import Image, ImageDraw

    source = source_image()
    source_path = out.parent / "_vorlage.png"
    source.save(source_path)

    panels = [("Vorlage", "Graustufenbild", None)]
    try:
        for technique in TECHNIQUES:
            lines = image_to_lines(
                source_path, *AREA_MM, technique, margin_mm=MARGIN_MM, **OPTIONS[technique]
            )
            stats = stats_for(lines, PlotConfig(width_mm=AREA_MM[0], height_mm=AREA_MM[1]))
            caption = (
                f"{len(lines)} Linien · {stats.draw_mm / 1000:.0f} m zeichnen "
                f"· ~{stats.duration_s / 3600:.1f} h"
            )
            panels.append((technique, caption, lines))
    finally:
        source_path.unlink(missing_ok=True)

    width = PAD * 2 + len(panels) * PANEL + (len(panels) - 1) * GAP
    height = TOP + PANEL * AREA_MM[1] / AREA_MM[0] + PAD
    figure = Image.new("RGB", (width, int(height)), "white")
    draw = ImageDraw.Draw(figure)
    title_font, caption_font = _font(26, bold=True), _font(18)

    inner_h = PANEL * AREA_MM[1] / AREA_MM[0]
    for index, (name, caption, lines) in enumerate(panels):
        x0 = PAD + index * (PANEL + GAP)
        draw.text((x0, 18), name, fill=(17, 17, 17), font=title_font)
        draw.text((x0, 56), caption, fill=(120, 120, 120), font=caption_font)
        box = (x0, TOP, x0 + PANEL, TOP + inner_h)
        draw.rectangle(box, outline=(220, 220, 220), width=2)

        if lines is None:
            figure.paste(source.convert("RGB").resize((PANEL - 4, int(inner_h) - 4)),
                         (x0 + 2, TOP + 2))
            continue

        scale = PANEL / AREA_MM[0]
        for line in lines:
            points = [(x0 + x * scale, TOP + y * scale) for x, y in line]
            if len(points) >= 2:
                draw.line(points, fill=(60, 60, 60), width=1)

    out.parent.mkdir(parents=True, exist_ok=True)
    figure.save(out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    print(f"geschrieben: {build(args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
