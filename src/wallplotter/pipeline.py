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
from dataclasses import dataclass
from pathlib import Path

from .geometry import Lines

__all__ = [
    "VpypeNotAvailable",
    "Layer",
    "svg_to_lines",
    "svg_to_layers",
    "optimize_commands",
    "lines_to_svg",
]

@dataclass
class Layer:
    """Eine Ebene der Zeichnung — in der Praxis: eine Stiftfarbe."""

    index: int
    color: str
    """Hex-Farbe aus dem SVG, für Vorschau und Beschriftung."""

    lines: Lines
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.color

    @property
    def slug(self) -> str:
        """Beschriftung als Dateinamensteil (``#e02020`` → ``e02020``)."""
        return "".join(ch for ch in self.label if ch.isalnum()) or "ebene"


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


def _collection_to_lines(collection) -> Lines:
    """vpype-LineCollection (CSS-Pixel, komplexe Zahlen) → Linien in mm."""
    return [[(p.real * _PX_TO_MM, p.imag * _PX_TO_MM) for p in line] for line in collection]


def _document_to_lines(document) -> Lines:
    return [line for collection in document.layers.values() for line in _collection_to_lines(collection)]


def _document_to_layers(document) -> list[Layer]:
    """Ebenen herausziehen, samt Farbe aus dem SVG — gleiche Beschriftung zusammen.

    vpype trennt nach dem *Attributtext* des Strichs: ``black``, ``#000000``
    und ``rgb(0,0,0)`` sind für vpype drei Ebenen, für den Stiftkasten aber
    eine. Zusammengefasst wird deshalb nach der Beschriftung, und das aus zwei
    Gründen:

    * Getrennt gelassen kollidierten sie später als Schlüssel des
      Ergebnis-Wörterbuchs, und alle bis auf die letzte Ebene verschwanden
      lautlos — bei einem SVG aus Inkscape mit gemischter Farbschreibweise
      fehlten also einfach Linien.
    * Selbst ohne Kollision wäre es falsch: drei Ebenen derselben Farbe heißen
      drei Dateien und zwei ``M0``-Pausen für einen Stift, der gar nicht
      gewechselt wird.

    Eine eigene Beschriftung (``vp_name``, in Inkscape der Ebenenname) trennt
    weiterhin — wer seine Ebenen benannt hat, meint sie auch getrennt.
    """
    merged: dict[str, Layer] = {}
    for index, collection in sorted(document.layers.items()):
        lines = _collection_to_lines(collection)
        if not lines:
            continue
        color = collection.metadata.get("vp_color")
        stroke = collection.metadata.get("svg_stroke")
        layer = Layer(
            index=index,
            color=_as_hex(color) or (stroke if isinstance(stroke, str) else "#000000"),
            lines=lines,
            name=str(collection.metadata.get("vp_name", "")),
        )
        existing = merged.get(layer.label)
        if existing is None:
            merged[layer.label] = layer
        else:
            existing.lines.extend(layer.lines)
    return list(merged.values())


def _as_hex(color) -> str | None:
    if color is None:
        return None
    try:
        return f"#{color.red:02x}{color.green:02x}{color.blue:02x}"
    except AttributeError:
        return str(color)


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


def svg_to_layers(
    svg: bytes | str | os.PathLike,
    *,
    quantization_mm: float = 0.2,
    split_by: str = "stroke",
    optimize: bool = True,
    **optimize_kwargs,
) -> list[Layer]:
    """SVG ebenenweise einlesen — eine Ebene je Strichfarbe.

    Damit lässt sich mehrfarbig plotten: erst alle schwarzen Linien, Stift
    wechseln, dann alle roten. Die Trennung übernimmt vpype über
    ``read --attr stroke``; ``split_by=""`` liefert die SVG-eigenen Ebenen.

    Die Optimierung läuft je Ebene — sonst würde ``linesort`` Wege zwischen
    Farben optimieren, die nie hintereinander gezeichnet werden.
    """
    path, temporary = _as_temp_file(svg, ".svg")
    try:
        attr = f" --attr {split_by}" if split_by else ""
        commands = [f"read{attr} --quantization {quantization_mm}mm {shlex.quote(str(path))}"]
        if optimize:
            commands += optimize_commands(**optimize_kwargs)
        vpype_cli = _require_vpype()
        try:
            document = vpype_cli.execute(" ".join(commands))
        except Exception as exc:
            raise VpypeNotAvailable(f"vpype-Pipeline fehlgeschlagen: {exc}") from exc
        return _document_to_layers(document)
    finally:
        if temporary:
            path.unlink(missing_ok=True)


def image_to_lines(*args, **kwargs):
    """Weggezogen nach :mod:`wallplotter.imaging`.

    Der frühere Weg über das vpype-Kommando ``hatched`` funktionierte nicht:
    das Paket registriert je nach Version gar kein Kommando, sondern nur eine
    Python-API. Und Schraffur ist ohnehin nur eins von vier Verfahren.
    """
    raise NotImplementedError(
        "Bildverfahren stehen jetzt in wallplotter.imaging.image_to_lines"
    )


def lines_to_svg(
    lines: Sequence[Sequence[tuple[float, float]]],
    width_mm: float,
    height_mm: float,
    *,
    stroke: str = "#1a4fd6",
    stroke_width_mm: float = 1.0,
    travel_stroke: str | None = None,
    style: str = "",
) -> str:
    """Linien als SVG rendern — für die Vorschau in der Web-UI (Stufe 4).

    Mit ``travel_stroke`` werden zusätzlich die Leerwege (Pen-Up) gestrichelt
    eingezeichnet, damit man vor dem Plot sieht, wie viel Zeit im Leerlauf
    verbracht wird. Braucht kein vpype.

    ``style`` landet als CSS am ``<svg>``. Ohne Höhenbegrenzung wächst eine
    2,5 m hohe Wand im Browser über den ganzen Bildschirm hinaus — die Web-UI
    setzt dort ``max-height``.
    """
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width_mm} {height_mm}" '
        f'width="100%" preserveAspectRatio="xMidYMid meet"'
        + (f' style="{style}"' if style else "")
        + ">",
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
