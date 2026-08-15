"""SVG/Bild → optimierte Linien, auf Basis von vpype.

vpype wird bewusst *lazy* importiert: GCode-Export, Statistik und Upload
funktionieren (und sind testbar) ohne die schwere Geometrie-Toolchain.

Stufe 1 der Roadmap — CLI und Web-UI greifen beide auf genau diese Funktionen
zu, es gibt keine zweite Pipeline.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .geometry import Lines

__all__ = [
    "VpypeNotAvailable",
    "svg_to_lines",
    "image_to_lines",
    "optimize_commands",
    "lines_to_svg",
]

# 1 CSS-Pixel = 1/96 Zoll — vpypes interne Längeneinheit.
_PX_TO_MM = 25.4 / 96.0


class VpypeNotAvailable(RuntimeError):
    """vpype (oder ein benötigtes Plugin) ist nicht installiert."""


def _require_vpype():
    try:
        import vpype_cli  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - abhängig von der Umgebung
        raise VpypeNotAvailable(
            "vpype ist nicht installiert — `pip install -e .[geometry]`"
        ) from exc
    return vpype_cli


def optimize_commands(
    *,
    merge_tolerance_mm: float = 0.5,
    simplify_tolerance_mm: float = 0.1,
    reloop: bool = True,
    sort: bool = True,
    remove_hidden: bool = False,
) -> list[str]:
    """vpype-Kommandos für die Standard-Optimierung zusammenstellen.

    ``remove_hidden`` benötigt das Plugin ``occult`` und entfernt verdeckte
    Linien — bei großen Zeichnungen spürbar langsam, deshalb standardmäßig aus.

    Achtung: ``reloop`` würfelt den Startpunkt geschlossener Kurven (versteckt
    den Ansatzpunkt des Stifts) und kennt keinen Seed — derselbe Input ergibt
    damit nicht denselben GCode. Für vergleichbare Läufe ``reloop=False``.
    """
    cmds: list[str] = []
    if simplify_tolerance_mm > 0:
        cmds.append(f"linesimplify --tolerance {simplify_tolerance_mm}mm")
    if merge_tolerance_mm > 0:
        cmds.append(f"linemerge --tolerance {merge_tolerance_mm}mm")
    if reloop:
        cmds.append("reloop")
    if remove_hidden:
        cmds.append("occult")
    if sort:
        cmds.append("linesort")
    return cmds


def _document_to_lines(document) -> Lines:
    """vpype-Document (CSS-Pixel, komplexe Zahlen) → Linien in mm."""
    lines: Lines = []
    for layer in document.layers.values():
        for line in layer:
            lines.append([(p.real * _PX_TO_MM, p.imag * _PX_TO_MM) for p in line])
    return lines


def _run(commands: Sequence[str]) -> Lines:
    vpype_cli = _require_vpype()
    pipeline = " ".join(commands)
    try:
        document = vpype_cli.execute(pipeline)
    except Exception as exc:  # vpype wirft je nach Fehler sehr unterschiedlich
        raise VpypeNotAvailable(
            f"vpype-Pipeline fehlgeschlagen: {pipeline}\n{exc}"
        ) from exc
    return _document_to_lines(document)


def _as_temp_file(data: bytes | str | os.PathLike, suffix: str) -> tuple[Path, bool]:
    """Pfad zur Eingabe liefern; ``True`` heißt „temporär, bitte löschen"."""
    if isinstance(data, (str, os.PathLike)):
        return Path(data), False
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(data)
    finally:
        handle.close()
    return Path(handle.name), True


def svg_to_lines(
    svg: bytes | str | os.PathLike,
    *,
    quantization_mm: float = 0.2,
    optimize: bool = True,
    extra_commands: Sequence[str] = (),
    **optimize_kwargs,
) -> Lines:
    """SVG einlesen, mit vpype optimieren und als Linien in mm zurückgeben.

    ``svg`` ist entweder ein Dateipfad oder der Dateiinhalt als ``bytes``
    (z. B. direkt aus dem Upload-Feld der Web-UI).

    Skalierung passiert bewusst *nicht* hier, sondern beim GCode-Export
    (:func:`wallplotter.gcode.lines_to_gcode`), damit dieselbe Geometrie ohne
    Neuberechnung für verschiedene Flächen genutzt werden kann.
    """
    path, temporary = _as_temp_file(svg, ".svg")
    try:
        commands = [f"read --quantization {quantization_mm}mm {shlex.quote(str(path))}"]
        if optimize:
            commands += optimize_commands(**optimize_kwargs)
        commands += list(extra_commands)
        return _run(commands)
    finally:
        if temporary:
            path.unlink(missing_ok=True)


def image_to_lines(
    image: bytes | str | os.PathLike,
    *,
    pitch_mm: float = 3.0,
    levels: tuple[int, int, int] = (64, 128, 192),
    blur: int = 0,
    image_suffix: str = ".png",
    optimize: bool = True,
    extra_commands: Sequence[str] = (),
    **optimize_kwargs,
) -> Lines:
    """Foto → Schraffur-Linien über das vpype-Plugin ``hatched``.

    Stufe 5 der Roadmap. Benötigt ``pip install -e .[photo]``; ohne das Plugin
    scheitert der Aufruf mit :class:`VpypeNotAvailable`.
    """
    path, temporary = _as_temp_file(image, image_suffix)
    try:
        levels_arg = " ".join(str(level) for level in levels)
        commands = [
            f"hatched --levels {levels_arg} --pitch {pitch_mm}mm "
            f"--blur {blur} {shlex.quote(str(path))}"
        ]
        if optimize:
            commands += optimize_commands(**optimize_kwargs)
        commands += list(extra_commands)
        return _run(commands)
    finally:
        if temporary:
            path.unlink(missing_ok=True)


def lines_to_svg(
    lines: Sequence[Sequence[tuple[float, float]]],
    width_mm: float,
    height_mm: float,
    *,
    stroke: str = "#1a4fd6",
    stroke_width_mm: float = 1.0,
    travel_stroke: str | None = None,
) -> str:
    """Linien als SVG rendern — für die Vorschau in der Web-UI (Stufe 4).

    Mit ``travel_stroke`` werden zusätzlich die Leerwege (Pen-Up) gestrichelt
    eingezeichnet, damit man vor dem Plot sieht, wie viel Zeit im Leerlauf
    verbracht wird. Braucht kein vpype.
    """
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width_mm} {height_mm}" '
        f'width="100%" preserveAspectRatio="xMidYMid meet">',
        f'<rect x="0" y="0" width="{width_mm}" height="{height_mm}" '
        f'fill="none" stroke="#999" stroke-width="{stroke_width_mm}" />',
    ]

    if travel_stroke:
        pos = (0.0, 0.0)
        travels = []
        for line in lines:
            if not line:
                continue
            travels.append(f"M {pos[0]:.2f},{pos[1]:.2f} L {line[0][0]:.2f},{line[0][1]:.2f}")
            pos = tuple(line[-1])
        if travels:
            parts.append(
                f'<path d="{" ".join(travels)}" fill="none" stroke="{travel_stroke}" '
                f'stroke-width="{stroke_width_mm / 2}" stroke-dasharray="4 4" />'
            )

    for line in lines:
        if len(line) < 2:
            continue
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in line)
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{stroke}" '
            f'stroke-width="{stroke_width_mm}" stroke-linecap="round" '
            f'stroke-linejoin="round" />'
        )

    parts.append("</svg>")
    return "\n".join(parts)
