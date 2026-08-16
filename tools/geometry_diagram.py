#!/usr/bin/env python3
"""Geometrie-Skizze für die Dokumentation erzeugen.

Zeichnet die Skizze *aus* den Ankerkoordinaten, die
:mod:`wallplotter.location` aus den drei Maßen berechnet — damit stimmt das
Bild automatisch mit der Rechnung überein, statt daneben zu veralten.

    python tools/geometry_diagram.py --span 2300 --left 1450 --right 1470
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wallplotter.location import Location  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "images" / "geometrie.svg"


def build_svg(location: Location, width_mm: float, height_mm: float, top_gap_mm: float) -> str:
    anchors = location.anchors()
    area_x0 = (anchors.left_x + anchors.right_x) / 2 - width_mm / 2
    area_y1 = anchors.y - top_gap_mm
    area_y0 = area_y1 - height_mm

    pad, pad_top = 300.0, 620.0
    minx, maxx = anchors.left_x - pad, anchors.right_x + pad
    miny, maxy = area_y0 - 220, anchors.y + pad_top
    w, h = maxx - minx, maxy - miny

    def sx(x: float) -> float:
        return x - minx

    def sy(y: float) -> float:
        return maxy - y  # SVG zählt Y nach unten

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="100%" '
        f'style="max-width:720px" font-family="system-ui, sans-serif">',
        f'<rect width="{w:.0f}" height="{h:.0f}" fill="#ffffff"/>',
    ]

    dim_y = sy(anchors.y) - 330
    out += [
        f'<line x1="{sx(anchors.left_x):.0f}" y1="{dim_y:.0f}" x2="{sx(anchors.right_x):.0f}" '
        f'y2="{dim_y:.0f}" stroke="#444" stroke-width="7" marker-start="url(#m)" marker-end="url(#m)"/>',
        f'<line x1="{sx(anchors.left_x):.0f}" y1="{dim_y:.0f}" x2="{sx(anchors.left_x):.0f}" '
        f'y2="{sy(anchors.y):.0f}" stroke="#c8ccd0" stroke-width="4"/>',
        f'<line x1="{sx(anchors.right_x):.0f}" y1="{dim_y:.0f}" x2="{sx(anchors.right_x):.0f}" '
        f'y2="{sy(anchors.y):.0f}" stroke="#c8ccd0" stroke-width="4"/>',
        f'<text x="{sx(0):.0f}" y="{dim_y - 55:.0f}" font-size="110" fill="#222" '
        f'text-anchor="middle">① Spannweite {anchors.span:.0f} mm</text>',
        f'<line x1="{sx(anchors.left_x) - 200:.0f}" y1="{sy(anchors.y):.0f}" '
        f'x2="{sx(anchors.right_x) + 200:.0f}" y2="{sy(anchors.y):.0f}" '
        f'stroke="#9aa0a6" stroke-width="8" stroke-dasharray="34 20"/>',
        f'<rect x="{sx(area_x0):.0f}" y="{sy(area_y1):.0f}" width="{width_mm:.0f}" '
        f'height="{height_mm:.0f}" fill="#f2f6fd" stroke="#1a4fd6" stroke-width="9"/>',
        f'<text x="{sx(area_x0) + width_mm / 2:.0f}" y="{sy(area_y1) + height_mm * 0.62:.0f}" '
        f'font-size="120" fill="#1a4fd6" text-anchor="middle">Zeichenfläche</text>',
        f'<text x="{sx(area_x0) + width_mm / 2:.0f}" y="{sy(area_y1) + height_mm * 0.62 + 145:.0f}" '
        f'font-size="100" fill="#5b7fd6" text-anchor="middle">per Ecken-Anfahrt ausgemessen</text>',
    ]

    belts = (
        (anchors.left_x, location.left_belt_zero_mm, "②", "end", -40),
        (anchors.right_x, location.right_belt_zero_mm, "③", "start", 40),
    )
    for anchor_x, length, number, align, offset in belts:
        out.append(
            f'<line x1="{sx(anchor_x):.0f}" y1="{sy(anchors.y):.0f}" x2="{sx(0):.0f}" '
            f'y2="{sy(0):.0f}" stroke="#d64545" stroke-width="11"/>'
        )
        mid_x = (sx(anchor_x) + sx(0)) / 2 + offset
        mid_y = (sy(anchors.y) + sy(0)) / 2
        out.append(
            f'<text x="{mid_x:.0f}" y="{mid_y:.0f}" font-size="105" fill="#b03030" '
            f'text-anchor="{align}">{number} {length:.0f} mm</text>'
        )

    for anchor_x, label in ((anchors.left_x, "Anker links"), (anchors.right_x, "Anker rechts")):
        out += [
            f'<circle cx="{sx(anchor_x):.0f}" cy="{sy(anchors.y):.0f}" r="54" fill="#222"/>',
            f'<text x="{sx(anchor_x):.0f}" y="{sy(anchors.y) - 105:.0f}" font-size="100" '
            f'fill="#222" text-anchor="middle">{label}</text>',
        ]

    out += [
        f'<rect x="{sx(0) - 88:.0f}" y="{sy(0) - 72:.0f}" width="176" height="144" rx="26" fill="#222"/>',
        f'<text x="{sx(0):.0f}" y="{sy(0) + 230:.0f}" font-size="105" fill="#222" '
        f'text-anchor="middle">Gondel am Nullpunkt</text>',
        '<defs><marker id="m" markerWidth="12" markerHeight="12" refX="6" refY="6" orient="auto">'
        '<circle cx="6" cy="6" r="5" fill="#444"/></marker></defs>',
        f'<text x="50" y="{h - 45:.0f}" font-size="88" fill="#6b7280">'
        f'Drei Maße genügen — die Ankerkoordinaten folgen per Trilateration</text>',
        "</svg>",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--span", type=float, default=2300.0)
    parser.add_argument("--left", type=float, default=1450.0)
    parser.add_argument("--right", type=float, default=1470.0)
    parser.add_argument("--width", type=float, default=2000.0, help="Zeichenfläche")
    parser.add_argument("--height", type=float, default=2500.0)
    parser.add_argument("--top-gap", type=float, default=250.0, help="Fläche unter der Ankerlinie")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    location = Location(
        name="Skizze",
        anchor_span_mm=args.span,
        left_belt_zero_mm=args.left,
        right_belt_zero_mm=args.right,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_svg(location, args.width, args.height, args.top_gap), encoding="utf-8")

    anchors = location.anchors()
    print(f"Geschrieben: {args.out}")
    print(f"Anker links X{anchors.left_x:.1f}, rechts X{anchors.right_x:.1f}, Höhe Y{anchors.y:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
