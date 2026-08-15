"""Bewegungsqualität: Pendel meiden, Tempo an die Geometrie anpassen.

Die Gondel hängt an zwei Riemen und ist damit ein Pendel mit rund 1,3 bis 2 Hz
Eigenfrequenz. Jede Richtungsumkehr gibt ihr einen Stoß. Trifft die
Umkehrfrequenz einer Schraffur die Eigenfrequenz, schaukelt sich das auf und
die Linien werden wellig — der typische Polargraph-Look, den man nicht will.

Zwei Werkzeuge dagegen:

* :func:`resonance_warning` rechnet vor dem Plot aus, ob Bahnabstand und
  Vorschub in die Resonanz laufen, und schlägt Auswege vor.
* :func:`conditioning_feeds` drosselt dort, wo die Kinematik schlecht
  konditioniert ist — nahe der Ankerlinie wirkt sich jeder Schrittfehler
  stärker aus, und die Zugkräfte sind dort am größten.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .geometry import Line

__all__ = [
    "pendulum_frequency_hz",
    "reversal_frequency_hz",
    "dominant_spacing_mm",
    "resonance_warning",
    "conditioning_feeds",
]


def pendulum_frequency_hz(pen_below_pivot_mm: float = 100.0) -> float:
    """Eigenfrequenz der Gondel als mathematisches Pendel.

    ``pen_below_pivot_mm`` ist der Abstand vom Aufhängepunkt der Riemen zum
    Schwerpunkt bzw. zur Stiftspitze — bei den üblichen Gondeln 60 bis 150 mm.
    """
    if pen_below_pivot_mm <= 0:
        raise ValueError("Pendellänge muss größer als 0 sein")
    return math.sqrt(9810.0 / pen_below_pivot_mm) / (2 * math.pi)


def reversal_frequency_hz(spacing_mm: float, feed_mm_per_min: float) -> float:
    """Wie oft pro Sekunde die Richtung wechselt.

    Bei einer Schraffur mit ``spacing_mm`` Bahnabstand liegen zwei Umkehrungen
    pro Doppelbahn — daher der Faktor zwei.
    """
    if spacing_mm <= 0 or feed_mm_per_min <= 0:
        raise ValueError("Abstand und Vorschub müssen größer als 0 sein")
    return (feed_mm_per_min / 60.0) / (2 * spacing_mm)


def dominant_spacing_mm(lines: Sequence[Line]) -> float | None:
    """Typischen Bahnabstand einer Zeichnung schätzen.

    Genommen wird der Median des Abstands zwischen aufeinanderfolgenden
    Linienanfängen — bei einer Schraffur ist das genau der Bahnabstand, bei
    freier Geometrie ein grober, aber brauchbarer Anhaltspunkt.
    """
    starts = [line[0] for line in lines if len(line) >= 2]
    if len(starts) < 3:
        return None
    gaps = sorted(
        math.hypot(b[0] - a[0], b[1] - a[1])
        for a, b in zip(starts, starts[1:], strict=False)
    )
    gaps = [gap for gap in gaps if gap > 0.01]
    if not gaps:
        return None
    return gaps[len(gaps) // 2]


@dataclass(frozen=True)
class ResonanceWarning:
    """Ergebnis der Resonanzprüfung."""

    critical: bool
    reversal_hz: float
    pendulum_hz: float
    spacing_mm: float
    feed_mm_per_min: float
    message: str

    def __str__(self) -> str:
        return self.message


def resonance_warning(
    lines: Sequence[Line],
    feed_mm_per_min: float,
    pen_below_pivot_mm: float = 100.0,
    tolerance: float = 0.35,
) -> ResonanceWarning | None:
    """Prüfen, ob die Zeichnung die Gondel zum Schwingen bringt.

    ``tolerance`` ist die relative Bandbreite um die Eigenfrequenz, die noch
    als kritisch gilt — ein Pendel mit wenig Dämpfung reagiert breit, deshalb
    großzügig angesetzt.

    Gibt ``None`` zurück, wenn sich kein Bahnabstand bestimmen lässt.
    """
    spacing = dominant_spacing_mm(lines)
    if spacing is None:
        return None

    pendulum = pendulum_frequency_hz(pen_below_pivot_mm)
    reversal = reversal_frequency_hz(spacing, feed_mm_per_min)
    ratio = reversal / pendulum
    critical = abs(ratio - 1.0) <= tolerance

    if not critical:
        message = (
            f"Umkehrfrequenz {reversal:.1f} Hz bei {spacing:.1f} mm Bahnabstand, "
            f"Gondel schwingt mit {pendulum:.1f} Hz — unkritisch."
        )
    else:
        # zwei Auswege: langsamer unter die Resonanz oder schneller darüber
        slower = pendulum * (1 - tolerance - 0.05) * 2 * spacing * 60
        faster = pendulum * (1 + tolerance + 0.05) * 2 * spacing * 60
        message = (
            f"Achtung: Umkehrfrequenz {reversal:.1f} Hz trifft die Pendelfrequenz "
            f"der Gondel ({pendulum:.1f} Hz) — das gibt wellige Linien. "
            f"Ausweg: Vorschub unter {slower:.0f} oder über {faster:.0f} mm/min, "
            f"oder den Bahnabstand ändern."
        )

    return ResonanceWarning(
        critical=critical,
        reversal_hz=reversal,
        pendulum_hz=pendulum,
        spacing_mm=spacing,
        feed_mm_per_min=feed_mm_per_min,
        message=message,
    )


def conditioning_feeds(
    lines: Sequence[Line],
    kinematics,
    base_feed: float,
    min_factor: float = 0.4,
    reference_resolution_mm: float | None = None,
) -> list[float]:
    """Vorschub je Linie an die örtliche Kondition der Kinematik anpassen.

    Nahe der Ankerlinie schlägt ein Schrittfehler stärker auf die Position
    durch und die Riemenkräfte steigen; dort langsamer zu fahren kostet wenig
    Zeit (es sind wenige Linien) und bringt sichtbar ruhigere Striche.

    Der Faktor ergibt sich aus dem Verhältnis der örtlichen Auflösung zur
    besten Auflösung der Zeichnung, begrenzt durch ``min_factor``.
    """
    if base_feed <= 0:
        raise ValueError("Vorschub muss größer als 0 sein")
    if not 0 < min_factor <= 1:
        raise ValueError("min_factor muss zwischen 0 und 1 liegen")

    drawable = [line for line in lines if len(line) >= 2]
    if not drawable:
        return []

    worst_per_line = []
    for line in drawable:
        worst = 0.0
        for x, y in line:
            try:
                worst = max(worst, kinematics.resolution_mm(x, y))
            except ValueError:
                worst = max(worst, float("inf"))
        worst_per_line.append(worst)

    finite = [value for value in worst_per_line if math.isfinite(value) and value > 0]
    reference = reference_resolution_mm or (min(finite) if finite else 1.0)

    feeds = []
    for worst in worst_per_line:
        if not math.isfinite(worst) or worst <= 0:
            factor = min_factor
        else:
            factor = max(min_factor, min(1.0, reference / worst))
        feeds.append(base_feed * factor)
    return feeds
