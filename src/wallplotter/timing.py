"""Wie lange der Plot wirklich dauert — mit Beschleunigung statt Weg durch Tempo.

Die naive Rechnung „Strecke geteilt durch Vorschub" stimmt für lange gerade
Wege und liegt sonst grob daneben. Ein Seilplotter fährt aber vor allem kurze
Stücke: eine Spirale mit 1,2 mm Schrittweite, eine Schraffur mit
Richtungsumkehr alle drei Millimeter, ein Punktraster mit tausend Strichen von
einem Millimeter. Bei 200 mm/s² braucht die Maschine allein 1,25 s, um auf
1500 mm/min zu beschleunigen und wieder anzuhalten — in der Zeit legt sie
15 mm zurück. Wer 1500 mm/min in die Schätzung einsetzt, verspricht also für
ein Punktraster leicht das Dreifache dessen, was die Maschine schafft.

Nachgebildet wird deshalb das, was GRBL/FluidNC tatsächlich tut:

* An jeder Ecke wird nur so weit abgebremst, wie die Krümmung es verlangt
  (*junction deviation*) — eine glatte Spirale läuft nahezu durch, eine
  Kehrtwende geht auf null.
* Rückwärts- und Vorwärtslauf über die Segmente begrenzen die Ecktempos auf
  das, was sich mit der gegebenen Beschleunigung überhaupt erreichen lässt.
* Je Segment kommt dann ein Trapez- oder Dreiecksprofil heraus.

Das ist kein Firmware-Simulator: Segmentierung der Kinematik, Nachlaufzeiten
des Reglers und die Zeit, die FluidNC selbst zum Rechnen braucht, bleiben
außen vor. Die Schätzung fällt damit eher zu kurz als zu lang aus.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .geometry import Line, Point

__all__ = [
    "MotionLimits",
    "profile_duration_s",
    "path_duration_s",
    "plot_duration_s",
]


@dataclass(frozen=True)
class MotionLimits:
    """Die Grenzen, innerhalb derer die Maschine fahren darf.

    Die Vorgabewerte stehen so in ``config/fluidnc-wallplotter.yaml`` — wer
    dort schraubt, sollte sie hier nachziehen, sonst weicht die Schätzung
    wieder ab.
    """

    acceleration_mm_s2: float = 200.0
    max_rate_mm_min: float = 4000.0
    junction_deviation_mm: float = 0.01
    """Wie weit die Bahn in einer Ecke von der Sollbahn abweichen darf.

    GRBLs Maß dafür, wie schnell eine Ecke genommen werden darf. Größer heißt
    schneller und runder, 0,01 mm ist der Vorgabewert von GRBL und FluidNC.
    """

    def __post_init__(self) -> None:
        if self.acceleration_mm_s2 <= 0:
            raise ValueError("Beschleunigung muss größer als 0 sein")
        if self.max_rate_mm_min <= 0:
            raise ValueError("Höchstgeschwindigkeit muss größer als 0 sein")
        if self.junction_deviation_mm <= 0:
            raise ValueError("junction deviation muss größer als 0 sein")

    @property
    def acceleration(self) -> float:
        return self.acceleration_mm_s2

    def speed_mm_s(self, feed_mm_min: float) -> float:
        """Vorschub in mm/s, gedeckelt auf die Höchstgeschwindigkeit der Achse."""
        return min(feed_mm_min, self.max_rate_mm_min) / 60.0


def _junction_speed(
    previous: tuple[float, float], following: tuple[float, float], limits: MotionLimits
) -> float:
    """Tempo, mit dem die Ecke zwischen zwei Segmenten durchfahren werden darf.

    Formel wie in GRBLs Planer: die Ecke wird als Kreisbogen gelesen, dessen
    Stichhöhe die *junction deviation* ist. Geradeaus heißt beliebig schnell,
    Kehrtwende heißt anhalten.
    """
    dot = previous[0] * following[0] + previous[1] * following[1]
    dot = max(-1.0, min(1.0, dot))
    # sin(halber Innenwinkel); 1 = geradeaus, 0 = Kehrtwende
    sin_half = math.sqrt(max(0.0, (1.0 + dot) / 2.0))
    if sin_half >= 1.0 - 1e-12:
        return math.inf
    if sin_half <= 1e-12:
        return 0.0
    return math.sqrt(
        limits.acceleration_mm_s2 * limits.junction_deviation_mm * sin_half / (1.0 - sin_half)
    )


def profile_duration_s(
    length_mm: float, entry_mm_s: float, exit_mm_s: float, cruise_mm_s: float, acceleration: float
) -> float:
    """Dauer eines einzelnen Segments als Trapez- oder Dreiecksprofil."""
    if length_mm <= 0:
        return 0.0
    entry = min(entry_mm_s, cruise_mm_s)
    exit_speed = min(exit_mm_s, cruise_mm_s)
    accel_distance = (cruise_mm_s**2 - entry**2) / (2 * acceleration)
    brake_distance = (cruise_mm_s**2 - exit_speed**2) / (2 * acceleration)

    if accel_distance + brake_distance <= length_mm:
        cruise_distance = length_mm - accel_distance - brake_distance
        return (
            (cruise_mm_s - entry) / acceleration
            + (cruise_mm_s - exit_speed) / acceleration
            + cruise_distance / cruise_mm_s
        )
    # Der Vorschub wird nie erreicht: Dreieck mit einer Spitze irgendwo dazwischen
    peak = math.sqrt(max(0.0, (2 * acceleration * length_mm + entry**2 + exit_speed**2) / 2))
    peak = max(peak, entry, exit_speed)
    return (peak - entry) / acceleration + (peak - exit_speed) / acceleration


def path_duration_s(
    points: Sequence[Point],
    feed_mm_min: float,
    limits: MotionLimits | None = None,
    *,
    entry_mm_s: float = 0.0,
    exit_mm_s: float = 0.0,
) -> float:
    """Fahrzeit für einen zusammenhängenden Streckenzug.

    ``entry_mm_s``/``exit_mm_s`` sind das Tempo an Anfang und Ende; im Plot
    steht die Maschine dort, weil der Stift gehoben oder gesenkt wird.
    """
    bounds = limits or MotionLimits()
    cruise = bounds.speed_mm_s(feed_mm_min)
    if cruise <= 0:
        raise ValueError("Vorschub muss größer als 0 sein")

    lengths: list[float] = []
    directions: list[tuple[float, float]] = []
    for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
        length = math.hypot(x2 - x1, y2 - y1)
        if length <= 0:
            continue  # doppelte Stützpunkte kosten keine Zeit
        lengths.append(length)
        directions.append(((x2 - x1) / length, (y2 - y1) / length))
    if not lengths:
        return 0.0

    # Ecktempos: was die Krümmung erlaubt
    junctions = [min(entry_mm_s, cruise)]
    for index in range(1, len(lengths)):
        junctions.append(
            min(cruise, _junction_speed(directions[index - 1], directions[index], bounds))
        )
    junctions.append(min(exit_mm_s, cruise))

    # Rückwärts: von hinten aufgerollt, damit rechtzeitig gebremst wird
    for index in reversed(range(len(lengths))):
        reachable = math.sqrt(
            junctions[index + 1] ** 2 + 2 * bounds.acceleration * lengths[index]
        )
        junctions[index] = min(junctions[index], reachable)
    # Vorwärts: was sich beschleunigen lässt
    for index in range(len(lengths)):
        reachable = math.sqrt(junctions[index] ** 2 + 2 * bounds.acceleration * lengths[index])
        junctions[index + 1] = min(junctions[index + 1], reachable)

    return sum(
        profile_duration_s(
            lengths[index],
            junctions[index],
            junctions[index + 1],
            cruise,
            bounds.acceleration,
        )
        for index in range(len(lengths))
    )


def plot_duration_s(
    lines: Sequence[Line],
    draw_feed: float,
    travel_feed: float,
    limits: MotionLimits | None = None,
    *,
    start: Point = (0.0, 0.0),
    line_overhead_s: float = 0.0,
    park: bool = True,
) -> float:
    """Gesamtdauer eines Plots: Leerwege, Zeichenwege und Werkzeugzeiten.

    ``line_overhead_s`` ist, was der Kopf je Linie zusätzlich kostet — beim
    Stift die zweimalige Servo-Wartezeit, beim Laser nichts.
    """
    bounds = limits or MotionLimits()
    drawable = [line for line in lines if len(line) >= 2]
    total = 0.0
    position = start
    for line in drawable:
        total += path_duration_s([position, line[0]], travel_feed, bounds)
        total += path_duration_s(line, draw_feed, bounds)
        total += line_overhead_s
        position = line[-1]
    if park and drawable:
        total += path_duration_s([position, start], travel_feed, bounds)
    return total
