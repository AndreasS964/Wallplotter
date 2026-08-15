"""Bildvorlagen in Linien übersetzen — vier Verfahren mit sehr unterschiedlichem Charakter.

Für Fotos entscheidet das Verfahren mehr über das Ergebnis als jede Mechanik:

* ``hatch`` — Schraffur nach Helligkeitsstufen (Plugin ``hatched``). Grafisch,
  technisch, viele kurze Linien und entsprechend viele Stifthübe.
* ``stipple`` — Punktraster, Dichte nach Helligkeit. Weich und fotografisch,
  aber ein Hub pro Punkt: für große Bilder unpraktisch.
* ``tsp`` — dieselben Punkte, aber zu *einer* durchgehenden Linie verbunden.
  Kein einziger Stifthub, also weder Servo-Artefakte noch Pendelstöße durch
  Leerfahrten. Der klassische „TSP-Art"-Look.
* ``spiral`` — eine Spirale von der Mitte nach außen, deren Auslenkung mit der
  Dunkelheit wächst. Ebenfalls eine einzige Linie, sehr gutmütig zu plotten.

Alle liefern Linien in Flächenkoordinaten (0…Breite, 0…Höhe), Ursprung oben
links wie im Bild — die Spiegelung in Maschinenkoordinaten macht wie bei SVGs
der GCode-Export.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from .geometry import Line, Lines, Point, simplify, sort_lines

__all__ = ["TECHNIQUES", "GrayImage", "load_gray", "image_to_lines", "describe"]


class ImagingError(RuntimeError):
    """Bild nicht lesbar oder Verfahren nicht verfügbar."""


@dataclass
class GrayImage:
    """Graustufenbild als Liste von Zeilen, 0 = schwarz, 1 = weiß."""

    pixels: list[list[float]]
    width: int
    height: int

    def darkness(self, x: float, y: float) -> float:
        """Dunkelheit (0…1) an einer Bildposition, außerhalb 0."""
        ix, iy = int(x), int(y)
        if not (0 <= ix < self.width and 0 <= iy < self.height):
            return 0.0
        return 1.0 - self.pixels[iy][ix]

    @property
    def aspect(self) -> float:
        return self.width / self.height


def load_gray(source: bytes | str | os.PathLike, max_size: int = 200) -> GrayImage:
    """Bild laden, in Graustufen wandeln und auf ``max_size`` herunterrechnen.

    Klein rechnen ist Absicht: Ein Wandplotter zeichnet keine Megapixel, und
    alle Verfahren hier arbeiten ohnehin auf Dichten, nicht auf Details.
    """
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ImagingError("Pillow fehlt — `pip install -e .[photo]`") from exc

    import io

    try:
        handle = Image.open(io.BytesIO(source) if isinstance(source, bytes) else source)
        image = handle.convert("L")
    except Exception as exc:
        raise ImagingError(f"Bild nicht lesbar: {exc}") from exc

    scale = min(1.0, max_size / max(image.size))
    if scale < 1.0:
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.LANCZOS,
        )
    # tobytes() statt getdata(): letzteres verschwindet in Pillow 14
    data = image.tobytes()
    pixels = [
        [data[row * image.width + col] / 255.0 for col in range(image.width)]
        for row in range(image.height)
    ]
    return GrayImage(pixels, image.width, image.height)


def _fit_box(image: GrayImage, width_mm: float, height_mm: float) -> tuple[float, float, float]:
    """Skalierung und Versatz, damit das Bild proportional in die Fläche passt."""
    scale = min(width_mm / image.width, height_mm / image.height)
    return (
        scale,
        (width_mm - image.width * scale) / 2,
        (height_mm - image.height * scale) / 2,
    )


# ---------------------------------------------------------------------------
# Punkte aus Helligkeit
# ---------------------------------------------------------------------------


def dither_points(image: GrayImage, spacing_px: float = 2.0, gamma: float = 1.0) -> list[Point]:
    """Punktwolke nach Helligkeit — Fehlerdiffusion statt Zufall.

    Floyd-Steinberg auf einem gröberen Raster: verteilt die Punkte gleichmäßig
    (blaurauschartig) statt sie zu verklumpen, und ist dabei deterministisch —
    derselbe Input ergibt dieselbe Punktwolke.
    """
    if spacing_px <= 0:
        raise ImagingError("spacing_px muss größer als 0 sein")

    cols = max(1, int(image.width / spacing_px))
    rows = max(1, int(image.height / spacing_px))
    # Zielhelligkeit je Zelle, gemittelt über das Originalraster
    cells = [
        [
            _cell_darkness(image, col, row, cols, rows) ** gamma
            for col in range(cols)
        ]
        for row in range(rows)
    ]

    points: list[Point] = []
    for row in range(rows):
        # abwechselnd hin und zurück: sonst kostet der Rücksprung an den
        # Zeilenanfang mehr Leerweg als die ganze Zeichnung lang ist
        columns = range(cols) if row % 2 == 0 else range(cols - 1, -1, -1)
        for col in columns:
            value = cells[row][col]
            keep = value > 0.5
            if keep:
                points.append(((col + 0.5) * image.width / cols, (row + 0.5) * image.height / rows))
            error = value - (1.0 if keep else 0.0)
            # Fehler auf die Nachbarn verteilen (Floyd-Steinberg). Die Richtung
            # folgt der Laufrichtung der Zeile, sonst diffundiert der Fehler
            # gegen die Abarbeitung und das Raster wird streifig.
            step = 1 if row % 2 == 0 else -1
            for dx, dy, weight in (
                (step, 0, 7 / 16), (-step, 1, 3 / 16), (0, 1, 5 / 16), (step, 1, 1 / 16)
            ):
                nx, ny = col + dx, row + dy
                if 0 <= nx < cols and 0 <= ny < rows:
                    cells[ny][nx] += error * weight
    return points


def _cell_darkness(image: GrayImage, col: int, row: int, cols: int, rows: int) -> float:
    x0 = int(col * image.width / cols)
    x1 = max(x0 + 1, int((col + 1) * image.width / cols))
    y0 = int(row * image.height / rows)
    y1 = max(y0 + 1, int((row + 1) * image.height / rows))
    total = 0.0
    count = 0
    for y in range(y0, min(y1, image.height)):
        for x in range(x0, min(x1, image.width)):
            total += 1.0 - image.pixels[y][x]
            count += 1
    return total / count if count else 0.0


# ---------------------------------------------------------------------------
# Verfahren
# ---------------------------------------------------------------------------


def stipple(
    image: GrayImage,
    width_mm: float,
    height_mm: float,
    spacing_px: float = 2.0,
    dot_mm: float = 1.0,
    gamma: float = 1.0,
) -> Lines:
    """Punktraster — jeder Punkt ein kurzer Strich, damit der Stift Farbe abgibt."""
    scale, offset_x, offset_y = _fit_box(image, width_mm, height_mm)
    half = dot_mm / 2
    lines: Lines = []
    for x, y in dither_points(image, spacing_px, gamma):
        cx, cy = offset_x + x * scale, offset_y + y * scale
        lines.append([(cx - half, cy), (cx + half, cy)])
    return lines


def tsp(
    image: GrayImage,
    width_mm: float,
    height_mm: float,
    spacing_px: float = 2.0,
    gamma: float = 1.0,
    improve_rounds: int = 2,
) -> Lines:
    """Alle Punkte zu einer einzigen durchgehenden Linie verbinden.

    Nächster-Nachbar über ein Gitter, danach ein paar 2-opt-Runden gegen die
    schlimmsten Kreuzungen. Kein exaktes TSP — das braucht es auch nicht, der
    Reiz liegt im durchgehenden Strich, nicht im Optimum.
    """
    scale, offset_x, offset_y = _fit_box(image, width_mm, height_mm)
    points = [
        (offset_x + x * scale, offset_y + y * scale)
        for x, y in dither_points(image, spacing_px, gamma)
    ]
    if len(points) < 2:
        return []
    route = _nearest_neighbour(points)
    route = _two_opt(route, rounds=improve_rounds)
    return [route]


def spiral(
    image: GrayImage,
    width_mm: float,
    height_mm: float,
    pitch_mm: float = 25.0,
    amplitude: float = 0.35,
    wobble_period_mm: float | None = None,
    step_mm: float = 1.2,
    simplify_mm: float = 0.15,
) -> Lines:
    """Spirale von innen nach außen, Auslenkung nach Dunkelheit.

    Eine einzige Linie über das ganze Bild: keine Stifthübe, keine Leerwege,
    und die Bewegung bleibt gleichmäßig — für einen Seilplotter das
    gutmütigste Verfahren überhaupt.

    Ohne Angabe folgt die Wellenlänge des Wobbles dem Bahnabstand. Das ist
    kein Detail: bleibt sie fest, während die Amplitude mit dem Bahnabstand
    wächst, wird aus der Welle ein Kamm — die Zeichenwege vervielfachen sich,
    ohne dass das Bild besser wird.

    Der Zeichenweg ist prinzipiell Fläche geteilt durch Bahnabstand, mal einem
    Zuschlag für den Wobble. Auf 2 × 2,5 m sind das auch bei groben
    Einstellungen mehrere Stunden — das zeigt die Statistik vor dem Plot an.
    """
    if pitch_mm <= 0 or step_mm <= 0 or (wobble_period_mm is not None and wobble_period_mm <= 0):
        raise ImagingError("pitch_mm, step_mm und wobble_period_mm müssen größer als 0 sein")

    wavelength = wobble_period_mm if wobble_period_mm else pitch_mm
    scale, offset_x, offset_y = _fit_box(image, width_mm, height_mm)
    center = (offset_x + image.width * scale / 2, offset_y + image.height * scale / 2)
    max_radius = math.hypot(image.width * scale, image.height * scale) / 2
    growth = pitch_mm / (2 * math.pi)  # Radiuszuwachs je Bogenmaß

    lines: Lines = []
    current: Line = []
    angle = 0.0
    arc = 0.0
    while growth * angle <= max_radius:
        radius = growth * angle
        x = center[0] + radius * math.cos(angle)
        y = center[1] + radius * math.sin(angle)
        darkness = image.darkness((x - offset_x) / scale, (y - offset_y) / scale)
        # Wobble mit fester Wellenlänge in mm — bei Winkelbindung würde er
        # außen immer länger und die Zeichenwege explodieren
        wobble = pitch_mm * amplitude * darkness * math.sin(2 * math.pi * arc / wavelength)
        point = (x + wobble * math.cos(angle), y + wobble * math.sin(angle))

        if 0 <= point[0] <= width_mm and 0 <= point[1] <= height_mm:
            current.append(point)
        elif len(current) >= 2:
            lines.append(current)
            current = []
        else:
            current = []

        # Schrittweite so wählen, dass der Bogen etwa step_mm lang wird
        delta = step_mm / max(radius, pitch_mm / 4)
        angle += delta
        arc += step_mm

    if len(current) >= 2:
        lines.append(current)
    # In hellen Bereichen läuft die Spirale glatt — dort sind fast alle
    # Stützpunkte überflüssig und blähen nur die GCode-Datei auf.
    return [reduced for line in lines if len(reduced := simplify(line, simplify_mm)) >= 2]


def hatch(
    source: bytes | str | os.PathLike,
    width_mm: float,
    height_mm: float,
    pitch_mm: float = 3.0,
    levels: tuple[int, int, int] = (64, 128, 192),
    blur: int = 4,
    image_suffix: str = ".png",
) -> Lines:
    """Schraffur über das Paket ``hatched``.

    Angesprochen wird bewusst dessen Python-API und nicht das vpype-Kommando:
    das Plugin registriert je nach Version gar keines.
    """
    try:
        import hatched as hatched_module  # noqa: PLC0415
    except ImportError as exc:
        raise ImagingError("Paket `hatched` fehlt — `pip install -e .[photo]`") from exc

    import tempfile
    from pathlib import Path

    if isinstance(source, bytes):
        handle = tempfile.NamedTemporaryFile(suffix=image_suffix, delete=False)
        handle.write(source)
        handle.close()
        path, temporary = Path(handle.name), True
    else:
        path, temporary = Path(source), False

    try:
        multiline = hatched_module.hatch(
            str(path),
            hatch_pitch=pitch_mm,
            levels=levels,
            blur_radius=blur,
            show_plot=False,
            save_svg=False,
        )
    except Exception as exc:
        raise ImagingError(f"Schraffur fehlgeschlagen: {exc}") from exc
    finally:
        if temporary:
            path.unlink(missing_ok=True)

    lines = [[(float(x), float(y)) for x, y in geom.coords] for geom in multiline.geoms]
    return _scale_into(lines, width_mm, height_mm)


def _scale_into(lines: Lines, width_mm: float, height_mm: float) -> Lines:
    xs = [x for line in lines for x, _ in line]
    ys = [y for line in lines for _, y in line]
    if not xs:
        return []
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    if span_x <= 0 or span_y <= 0:
        return lines
    scale = min(width_mm / span_x, height_mm / span_y)
    dx = (width_mm - span_x * scale) / 2 - min(xs) * scale
    dy = (height_mm - span_y * scale) / 2 - min(ys) * scale
    return [[(x * scale + dx, y * scale + dy) for x, y in line] for line in lines]


# ---------------------------------------------------------------------------
# Wegfindung für tsp
# ---------------------------------------------------------------------------


def _nearest_neighbour(points: list[Point]) -> list[Point]:
    """Immer zum nächsten offenen Punkt, beschleunigt über ein Raster."""
    remaining = set(range(len(points)))
    cell = max(1e-6, math.sqrt(_bounding_area(points) / max(1, len(points))) * 2)
    grid: dict[tuple[int, int], list[int]] = {}
    for index, (x, y) in enumerate(points):
        grid.setdefault((int(x / cell), int(y / cell)), []).append(index)

    current = 0
    remaining.discard(0)
    route = [points[0]]
    while remaining:
        x, y = points[current]
        best, best_distance = None, math.inf
        ring = 1
        while best is None or ring <= 2:
            cx, cy = int(x / cell), int(y / cell)
            for gx in range(cx - ring, cx + ring + 1):
                for gy in range(cy - ring, cy + ring + 1):
                    for index in grid.get((gx, gy), ()):
                        if index not in remaining:
                            continue
                        distance = (points[index][0] - x) ** 2 + (points[index][1] - y) ** 2
                        if distance < best_distance:
                            best, best_distance = index, distance
            ring += 1
            if ring > 64:  # Notbremse: dann eben linear suchen
                best = min(remaining, key=lambda i: (points[i][0] - x) ** 2 + (points[i][1] - y) ** 2)
                break
        remaining.discard(best)
        route.append(points[best])
        current = best
    return route


def _bounding_area(points: list[Point]) -> float:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return max(1e-6, (max(xs) - min(xs)) * (max(ys) - min(ys)))


def _two_opt(route: list[Point], rounds: int = 2, window: int = 30) -> list[Point]:
    """2-opt, aber nur über ein Fenster — global wäre es quadratisch."""
    def distance(a: Point, b: Point) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    for _ in range(max(0, rounds)):
        improved = False
        for i in range(1, len(route) - 2):
            for j in range(i + 1, min(i + window, len(route) - 1)):
                a, b, c, d = route[i - 1], route[i], route[j], route[j + 1]
                if distance(a, c) + distance(b, d) + 1e-9 < distance(a, b) + distance(c, d):
                    route[i : j + 1] = reversed(route[i : j + 1])
                    improved = True
        if not improved:
            break
    return route


# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------

TECHNIQUES = {
    "hatch": "Schraffur nach Helligkeitsstufen — grafisch, viele Stifthübe (Paket hatched)",
    "stipple": "Punktraster nach Dichte — fotografisch, ein Stifthub je Punkt",
    "tsp": "dieselben Punkte als eine durchgehende Linie — kein einziger Stifthub",
    "spiral": "Spirale mit dunkelheitsabhängiger Auslenkung — eine Linie, sehr ruhig zu plotten",
}


def image_to_lines(
    source: bytes | str | os.PathLike,
    width_mm: float,
    height_mm: float,
    technique: str = "spiral",
    *,
    margin_mm: float = 0.0,
    max_size: int = 200,
    image_suffix: str = ".png",
    **options,
) -> Lines:
    """Bild nach dem gewählten Verfahren in Linien übersetzen.

    Das Ergebnis füllt die Fläche abzüglich Rand und wird nicht mehr
    eingepasst — die Verfahren rechnen selbst in Millimetern.
    """
    if technique not in TECHNIQUES:
        raise ImagingError(
            f"Unbekanntes Verfahren {technique!r}, möglich: {', '.join(TECHNIQUES)}"
        )
    inner_width = width_mm - 2 * margin_mm
    inner_height = height_mm - 2 * margin_mm
    if inner_width <= 0 or inner_height <= 0:
        raise ImagingError("Rand ist größer als die Fläche")

    if technique == "hatch":
        lines = hatch(source, inner_width, inner_height, image_suffix=image_suffix, **options)
    else:
        image = load_gray(source, max_size)
        if technique == "stipple":
            lines = stipple(image, inner_width, inner_height, **options)
        elif technique == "tsp":
            lines = tsp(image, inner_width, inner_height, **options)
        else:
            lines = spiral(image, inner_width, inner_height, **options)

    if technique != "tsp" and len(lines) > 1:
        # tsp ist per Definition eine Linie; alles andere gewinnt spürbar,
        # etwa die am Bildrand zerschnittene Spirale
        lines = sort_lines(lines)
    if margin_mm:
        lines = [[(x + margin_mm, y + margin_mm) for x, y in line] for line in lines]
    return lines


def describe() -> str:
    return "\n".join(f"{name:<8} {text}" for name, text in TECHNIQUES.items())
