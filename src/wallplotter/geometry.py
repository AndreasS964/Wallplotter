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
    source_bounds: tuple[float, float, float, float] | None = None,
) -> Lines:
    """Zeichnung proportional in die Fläche einpassen.

    Seitenverhältnis bleibt erhalten; ohne ``center`` wird linksbündig/oben
    ausgerichtet. Eine leere Zeichnung wird unverändert zurückgegeben.

    ``source_bounds`` setzt die Ausgangs-Bounding-Box von Hand, statt sie aus
    ``lines`` zu nehmen. Genau das braucht mehrfarbiges Plotten: jede Farbebene
    muss mit *derselben* Abbildung eingepasst werden wie alle anderen, sonst
    zöge jede ihre eigene Skalierung und die Ebenen lägen nicht mehr
    übereinander.
    """
    inner_w = width_mm - 2 * margin_mm
    inner_h = height_mm - 2 * margin_mm
    if inner_w <= 0 or inner_h <= 0:
        raise ValueError("Rand ist größer als die Fläche")

    xmin, ymin, xmax, ymax = source_bounds if source_bounds is not None else bounds(lines)
    src_w, src_h = xmax - xmin, ymax - ymin
    if src_w <= 0 and src_h <= 0:
        # Alles auf einem Punkt: skalieren geht nicht, aber die Rohkoordinaten
        # einfach durchzureichen hieße, dass die Zeichnung irgendwo landet —
        # bei einem SVG mit Ursprung weit außerhalb auch neben der Wand.
        # Also wenigstens in die Mitte der Fläche schieben.
        if not any(line for line in lines):
            return [list(line) for line in lines]
        return transform(
            lines,
            1.0,
            margin_mm + inner_w / 2 - xmin,
            margin_mm + inner_h / 2 - ymin,
        )

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


def simplify(line: Sequence[Point], tolerance: float) -> Line:
    """Douglas-Peucker: Stützpunkte ausdünnen, ohne die Form zu ändern.

    Vpype kann das auch, aber die Bildverfahren erzeugen ihre Punkte selbst und
    sollen nicht die halbe Toolchain brauchen, nur um gerade Abschnitte
    loszuwerden.
    """
    if tolerance <= 0 or len(line) < 3:
        return list(line)

    keep = [False] * len(line)
    keep[0] = keep[-1] = True
    stack = [(0, len(line) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        ax, ay = line[start]
        bx, by = line[end]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        worst_index, worst_distance = -1, 0.0
        for index in range(start + 1, end):
            px, py = line[index]
            if length == 0:
                distance = math.hypot(px - ax, py - ay)
            else:
                distance = abs(dy * px - dx * py + bx * ay - by * ax) / length
            if distance > worst_distance:
                worst_index, worst_distance = index, distance
        if worst_distance > tolerance:
            keep[worst_index] = True
            stack += [(start, worst_index), (worst_index, end)]
    return [point for point, keeper in zip(line, keep, strict=True) if keeper]


def sort_lines(lines: Sequence[Line], start: Point = (0.0, 0.0)) -> Lines:
    """Linien greedy nach kürzestem Leerweg ordnen, Richtung frei wählbar.

    vpype kann das mit ``linesort`` besser, aber die Bildverfahren erzeugen
    ihre Geometrie ohne vpype — und ungeordnet kostet eine zerschnittene
    Spirale mehr Leerweg als Zeichenweg.

    Gesucht wird über ein Raster statt über alle Linien: ``stipple`` liefert
    für ein Foto leicht zehntausend kurze Striche, und die paarweise Suche
    wächst quadratisch — das sind dann Minuten statt Sekunden, bei jedem
    Reglerdreh in der Web-UI aufs Neue. Das Ergebnis ist dasselbe, nur der Weg
    dorthin kürzer.
    """
    remaining = [list(line) for line in lines if len(line) >= 2]
    if not remaining:
        return []

    # Beide Enden jeder Linie ins Raster hängen — angefahren werden darf jedes,
    # die Linie wird dann gegebenenfalls umgedreht gezeichnet.
    ends = [(line[0], line[-1]) for line in remaining]
    xs = [point[0] for pair in ends for point in pair]
    ys = [point[1] for pair in ends for point in pair]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    cell = max(span / max(1, math.isqrt(len(remaining))), 1e-9)

    grid: dict[tuple[int, int], list[int]] = {}
    for index, (head, tail) in enumerate(ends):
        for point in (head, tail):
            grid.setdefault((int(point[0] // cell), int(point[1] // cell)), []).append(index)
    gxs = [gx for gx, _ in grid]
    gys = [gy for _, gy in grid]
    grid_box = (min(gxs), max(gxs), min(gys), max(gys))

    open_lines = set(range(len(remaining)))
    ordered: Lines = []
    position = start
    while open_lines:
        best_index, best_flip = _nearest_end(grid, grid_box, ends, open_lines, position, cell)
        open_lines.discard(best_index)
        line = remaining[best_index]
        if best_flip:
            line.reverse()
        ordered.append(line)
        position = line[-1]
    return ordered


def _nearest_end(
    grid: dict[tuple[int, int], list[int]],
    grid_box: tuple[int, int, int, int],
    ends: Sequence[tuple[Point, Point]],
    open_lines: set[int],
    position: Point,
    cell: float,
) -> tuple[int, bool]:
    """Nächstes freies Linienende suchen: ``(Index, umdrehen?)``.

    Ring für Ring nach außen. Aufgehört wird erst, wenn selbst der
    nächstmögliche Punkt des nächsten Rings weiter weg läge als der beste
    bisher gefundene — sonst käme statt der kürzesten nur eine kurze Anfahrt
    heraus.
    """
    cx, cy = int(position[0] // cell), int(position[1] // cell)
    min_gx, max_gx, min_gy, max_gy = grid_box
    # Der Startpunkt kann weit außerhalb der Zeichnung liegen (Maschinennullpunkt
    # in einer Ecke). Leere Ringe davor werden übersprungen statt abgesucht.
    first = max(0, min_gx - cx, cx - max_gx, min_gy - cy, cy - max_gy)
    last = max(abs(cx - min_gx), abs(cx - max_gx), abs(cy - min_gy), abs(cy - max_gy))

    # Verglichen wird das Tripel, nicht nur der Abstand. Zwei Linien, die
    # einen Endpunkt teilen, liegen exakt gleich weit weg — und welche davon
    # zuerst gezogen wird, entscheidet über den *nächsten* Leerweg. Die
    # Rasterreihenfolge ist dabei eine andere als die Listenreihenfolge; ohne
    # festen Gleichstand käme mal der eine, mal der andere Weg heraus, und
    # gelegentlich der längere.
    best = (math.inf, len(ends), False)
    best_index = -1
    for ring in range(first, last + 1):
        for gx, gy in _ring_cells(cx, cy, ring, grid_box):
            for index in grid.get((gx, gy), ()):
                if index not in open_lines:
                    continue
                for flip in (False, True):
                    head = ends[index][1] if flip else ends[index][0]
                    squared = (head[0] - position[0]) ** 2 + (head[1] - position[1]) ** 2
                    if (squared, index, flip) < best:
                        best, best_index = (squared, index, flip), index
        if best_index >= 0 and (ring * cell) ** 2 >= best[0]:
            break
    return best_index, best[2]


def _ring_cells(cx: int, cy: int, ring: int, box: tuple[int, int, int, int]):
    """Belegbare Zellen mit genau ``ring`` Abstand (Schachbrett) um ``(cx, cy)``.

    Auf ``box`` beschnitten: außerhalb liegt garantiert nichts, und ein Ring
    um einen weit entfernten Startpunkt hätte sonst Millionen leerer Zellen.
    """
    min_gx, max_gx, min_gy, max_gy = box
    if ring == 0:
        if min_gx <= cx <= max_gx and min_gy <= cy <= max_gy:
            yield (cx, cy)
        return
    columns = range(max(cx - ring, min_gx), min(cx + ring, max_gx) + 1)
    for gy in (cy - ring, cy + ring):
        if min_gy <= gy <= max_gy:
            for gx in columns:
                yield (gx, gy)
    rows = range(max(cy - ring + 1, min_gy), min(cy + ring - 1, max_gy) + 1)
    for gx in (cx - ring, cx + ring):
        if min_gx <= gx <= max_gx:
            for gy in rows:
                yield (gx, gy)


def draw_length(lines: Iterable[Line]) -> float:
    """Summierte Strecke mit Stift unten, in mm."""
    total = 0.0
    for line in lines:
        for (x1, y1), (x2, y2) in zip(line, line[1:], strict=False):
            total += math.hypot(x2 - x1, y2 - y1)
    return total


def travel_length(
    lines: Sequence[Line], start: Point = (0.0, 0.0), *, return_to_start: bool = True
) -> float:
    """Summierte Leerweg-Strecke (Pen-Up), in mm — inklusive Anfahrt.

    Mit ``return_to_start=False`` (Standard ``True``) fehlt die Rückfahrt: für
    einen Block, dessen GCode gar nicht zum Ausgangspunkt zurückfährt — etwa
    eine Zwischenebene eines zusammenhängenden Mehrfarbenprogramms, die mit
    ``M0`` pausiert, statt vorher zu parken (siehe
    :func:`wallplotter.gcode._program_with_pauses`).
    """
    total = 0.0
    pos = start
    for line in lines:
        if not line:
            continue
        total += math.hypot(line[0][0] - pos[0], line[0][1] - pos[1])
        pos = line[-1]
    if return_to_start:
        total += math.hypot(start[0] - pos[0], start[1] - pos[1])
    return total


def estimate_duration_s(
    lines: Sequence[Line], draw_feed: float, travel_feed: float
) -> float:
    """Grobe Laufzeitschätzung in Sekunden (ohne Beschleunigung und Pen-Hübe)."""
    if draw_feed <= 0 or travel_feed <= 0:
        raise ValueError("Vorschub muss größer als 0 sein")
    return draw_length(lines) / draw_feed * 60 + travel_length(lines) / travel_feed * 60
