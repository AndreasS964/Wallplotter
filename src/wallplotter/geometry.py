"""Geometrie-Grundtypen — bewusst ohne Abhängigkeit zu vpype oder numpy.

Damit lassen sich GCode-Export und Statistik testen, ohne die schwere
Toolchain zu installieren. ``pipeline`` konvertiert vpype-Geometrie in genau
diese Form.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

Point = tuple[float, float]
Line = list[Point]
"""Ein zusammenhängender Polygonzug in mm, Stift bleibt unten."""

Lines = list[Line]
"""Mehrere Linien; zwischen zwei Linien wird der Stift angehoben."""


def bounds(lines: Sequence[Line]) -> tuple[float, float, float, float]:
    """Bounding-Box als ``(xmin, ymin, xmax, ymax)``.

    Leere Eingabe ergibt ``(0, 0, 0, 0)``.
    """
    xs = [x for line in lines for x, _ in line]
    ys = [y for line in lines for _, y in line]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def transform(lines: Sequence[Line], scale: float, dx: float, dy: float) -> Lines:
    """Skalieren und verschieben: ``p * scale + (dx, dy)``."""
    return [[(x * scale + dx, y * scale + dy) for x, y in line] for line in lines]


def fit_to_area(
    lines: Sequence[Line],
    width_mm: float,
    height_mm: float,
    margin_mm: float = 0.0,
    center: bool = True,
) -> Lines:
    """Zeichnung proportional in die Fläche einpassen.

    Seitenverhältnis bleibt erhalten; ohne ``center`` wird linksbündig/oben
    ausgerichtet. Eine leere Zeichnung wird unverändert zurückgegeben.
    """
    inner_w = width_mm - 2 * margin_mm
    inner_h = height_mm - 2 * margin_mm
    if inner_w <= 0 or inner_h <= 0:
        raise ValueError("Rand ist größer als die Fläche")

    xmin, ymin, xmax, ymax = bounds(lines)
    src_w, src_h = xmax - xmin, ymax - ymin
    if src_w <= 0 and src_h <= 0:
        return [list(line) for line in lines]

    scale = min(
        inner_w / src_w if src_w > 0 else math.inf,
        inner_h / src_h if src_h > 0 else math.inf,
    )
    dx = margin_mm - xmin * scale
    dy = margin_mm - ymin * scale
    if center:
        dx += (inner_w - src_w * scale) / 2
        dy += (inner_h - src_h * scale) / 2
    return transform(lines, scale, dx, dy)


def flip_y(lines: Sequence[Line], height_mm: float) -> Lines:
    """Y-Achse spiegeln (SVG-Ursprung oben links → Maschine unten links)."""
    return [[(x, height_mm - y) for x, y in line] for line in lines]


def draw_length(lines: Iterable[Line]) -> float:
    """Summierte Strecke mit Stift unten, in mm."""
    total = 0.0
    for line in lines:
        for (x1, y1), (x2, y2) in zip(line, line[1:], strict=False):
            total += math.hypot(x2 - x1, y2 - y1)
    return total


def travel_length(lines: Sequence[Line], start: Point = (0.0, 0.0)) -> float:
    """Summierte Leerweg-Strecke (Pen-Up), in mm — inklusive An- und Rückfahrt."""
    total = 0.0
    pos = start
    for line in lines:
        if not line:
            continue
        total += math.hypot(line[0][0] - pos[0], line[0][1] - pos[1])
        pos = line[-1]
    total += math.hypot(start[0] - pos[0], start[1] - pos[1])
    return total


def estimate_duration_s(
    lines: Sequence[Line], draw_feed: float, travel_feed: float
) -> float:
    """Grobe Laufzeitschätzung in Sekunden (ohne Beschleunigung und Pen-Hübe)."""
    if draw_feed <= 0 or travel_feed <= 0:
        raise ValueError("Vorschub muss größer als 0 sein")
    return draw_length(lines) / draw_feed * 60 + travel_length(lines) / travel_feed * 60
