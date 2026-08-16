"""Testmuster für die Erstinbetriebnahme.

Alle Generatoren liefern Linien direkt in Flächenkoordinaten (0…Breite,
0…Höhe) — sie werden deshalb *nicht* eingepasst, sondern nur um den
Flächenversatz verschoben (``fit=False`` beim GCode-Export). Nur so prüft ein
500-mm-Raster auch wirklich 500 mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .geometry import Line, Lines

__all__ = ["Pattern", "PATTERNS", "build", "describe", "cross"]


@dataclass(frozen=True)
class Pattern:
    """Ein Testmuster samt Erklärung, wonach man beim Anschauen sucht."""

    name: str
    description: str
    lines: Lines
    feeds: list[float] | None = field(default=None)
    """Optionaler Vorschub je Linie — nur der Vorschub-Test nutzt das."""


def _rect(x0: float, y0: float, x1: float, y1: float) -> Line:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def cross(x: float, y: float, size: float) -> Lines:
    """Ein Kreuz — der einzige Punkt, den man an einer Wand genau nachmessen kann."""
    half = size / 2
    return [[(x - half, y), (x + half, y)], [(x, y - half), (x, y + half)]]


_cross = cross


def _circle(cx: float, cy: float, radius: float, segments: int = 72) -> Line:
    return [
        (
            cx + radius * math.cos(2 * math.pi * i / segments),
            cy + radius * math.sin(2 * math.pi * i / segments),
        )
        for i in range(segments + 1)
    ]


def frame(width: float, height: float, margin: float = 0.0, **_) -> Pattern:
    """Rahmen, Diagonalen und Eckkreuze — der erste Plot nach dem Kalibrieren.

    Zeigt sofort, ob die kalibrierte Fläche wirklich passt: Der Rahmen muss
    parallel zu den Wandkanten laufen, und die Diagonalen müssen sich exakt in
    der Mitte kreuzen.
    """
    x0, y0 = margin, margin
    x1, y1 = width - margin, height - margin
    lines: Lines = [_rect(x0, y0, x1, y1), [(x0, y0), (x1, y1)], [(x0, y1), (x1, y0)]]
    # Kreuze nach innen versetzt: mittig auf der Ecke würden sie über den Rand
    # hinausragen — und am Flächenrand hieße das: Stift neben der Wand
    cross_size = min(width - 2 * margin, height - 2 * margin) * 0.05
    inset = cross_size / 2
    for x, y in [
        (x0 + inset, y0 + inset),
        (x1 - inset, y0 + inset),
        (x1 - inset, y1 - inset),
        (x0 + inset, y1 - inset),
    ]:
        lines += _cross(x, y, cross_size)
    return Pattern(
        "frame",
        "Rahmen mit Diagonalen und Eckkreuzen — prüft Fläche, Rechtwinkligkeit "
        "und ob der Stift überall ankommt.",
        lines,
    )


def grid(width: float, height: float, margin: float = 0.0, spacing: float = 250.0, **_) -> Pattern:
    """Raster mit bekanntem Abstand — prüft Maßstab und Verzug.

    Nachmessen mit dem Zollstock: Weichen die Kästchen vom Sollmaß ab, stimmen
    Riemenlänge oder Ankerabstand in der Firmware-Konfiguration nicht.
    """
    if spacing <= 0:
        raise ValueError("spacing muss größer als 0 sein")
    x0, y0 = margin, margin
    x1, y1 = width - margin, height - margin
    lines: Lines = []

    x = x0
    while x <= x1 + 1e-9:
        lines.append([(x, y0), (x, y1)])
        x += spacing
    y = y0
    while y <= y1 + 1e-9:
        lines.append([(x0, y), (x1, y)])
        y += spacing

    return Pattern(
        "grid",
        f"Raster mit {spacing:.0f} mm Teilung — mit dem Zollstock nachmessen, "
        "Abweichungen zeigen falsche Riemen-/Ankermaße.",
        lines,
    )


def circles(width: float, height: float, margin: float = 0.0, spacing: float = 400.0, **_) -> Pattern:
    """Kreisraster — macht Verzerrung sichtbar.

    Kreise sind der ehrlichste Test einer Seilkinematik: Wo die Geometrie
    schlecht konditioniert ist, werden aus Kreisen Eier.
    """
    if spacing <= 0:
        raise ValueError("spacing muss größer als 0 sein")
    radius = spacing * 0.35
    lines: Lines = []
    y = margin + spacing / 2
    while y <= height - margin - spacing / 2 + 1e-9:
        x = margin + spacing / 2
        while x <= width - margin - spacing / 2 + 1e-9:
            lines.append(_circle(x, y, radius))
            x += spacing
        y += spacing
    return Pattern(
        "circles",
        "Kreisraster — aus Kreisen werden Eier, wo die Kinematik schwächelt "
        "(erwartungsgemäß oben am Rand).",
        lines,
    )


def pen_test(width: float, height: float, margin: float = 0.0, strokes: int = 20, **_) -> Pattern:
    """Kurze Striche mit Absetzen dazwischen — Servo- und Anpressdrucktest.

    Alle Striche müssen gleich dunkel anfangen. Fehlende Anfänge heißen: Servo
    zu langsam, also ``PenConfig.dwell_s`` erhöhen.
    """
    if strokes < 2:
        raise ValueError("mindestens 2 Striche")
    x0, x1 = margin, width - margin
    y_top = height - margin
    y_bottom = margin
    lines: Lines = []
    for i in range(strokes):
        y = y_bottom + (y_top - y_bottom) * i / (strokes - 1)
        length = (x1 - x0) / 3
        lines.append([(x0, y), (x0 + length, y)])
        lines.append([(x1 - length, y), (x1, y)])
    return Pattern(
        "pen-test",
        f"{strokes} Strichpaare mit Stiftheben dazwischen — fehlende Strichanfänge "
        "heißen: Servo-Wartezeit erhöhen.",
        lines,
    )


def feed_ramp(
    width: float,
    height: float,
    margin: float = 0.0,
    steps: int = 8,
    min_feed: float = 500.0,
    max_feed: float = 4000.0,
    **_,
) -> Pattern:
    """Linien mit steigendem Vorschub — findet die Grenze vor dem Riemenspringen.

    Von unten nach oben wird jede Linie schneller gezogen. Die erste Linie, die
    wellig wird oder versetzt endet, markiert das Tempo, ab dem die Riemen
    überfordert sind.
    """
    if steps < 2:
        raise ValueError("mindestens 2 Stufen")
    if min_feed <= 0 or max_feed < min_feed:
        raise ValueError("Vorschubbereich unplausibel")
    x0, x1 = margin, width - margin
    y_bottom, y_top = margin, height - margin
    lines: Lines = []
    feeds: list[float] = []
    for i in range(steps):
        share = i / (steps - 1)
        y = y_bottom + (y_top - y_bottom) * share
        # hin und zurück, damit auch die Umkehr belastet wird
        lines.append([(x0, y), (x1, y), (x0, y)])
        feeds.append(min_feed + (max_feed - min_feed) * share)
    return Pattern(
        "feed-ramp",
        f"{steps} Linien von {min_feed:.0f} bis {max_feed:.0f} mm/min — die erste "
        "wellige Linie zeigt die brauchbare Höchstgeschwindigkeit.",
        lines,
        feeds,
    )


PATTERNS = {
    "frame": frame,
    "grid": grid,
    "circles": circles,
    "pen-test": pen_test,
    "feed-ramp": feed_ramp,
}


def build(name: str, width: float, height: float, margin: float = 0.0, **kwargs) -> Pattern:
    """Muster nach Namen erzeugen."""
    if name not in PATTERNS:
        raise KeyError(f"Unbekanntes Muster {name!r}, bekannt: {', '.join(PATTERNS)}")
    if width <= 2 * margin or height <= 2 * margin:
        raise ValueError("Rand ist größer als die Fläche")
    return PATTERNS[name](width, height, margin, **kwargs)


def describe() -> str:
    """Übersicht für ``--list-patterns``."""
    lines = []
    for name in PATTERNS:
        pattern = build(name, 1000.0, 1000.0, 50.0)
        lines.append(f"{name:<10} {pattern.description}")
    return "\n".join(lines)
