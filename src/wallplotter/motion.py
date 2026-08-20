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
    "dominant_run_length_mm",
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


def reversal_frequency_hz(run_length_mm: float, feed_mm_per_min: float) -> float:
    """Grundfrequenz der Anregung: eine Bahn hin, eine Bahn zurück.

    ``run_length_mm`` ist die Länge einer Bahn zwischen zwei Richtungsumkehren
    — bei einer Schraffur also die Strichlänge, **nicht** der Bahnabstand.
    Eine volle Schwingung der Gondel ist „hin und wieder zurück", dauert also
    zweimal die Bahnzeit; daher der Faktor zwei.

    Hier stand früher der Bahnabstand. Das war der falsche Weg: wie oft die
    Gondel umkehrt, hängt daran, wie lang eine Bahn ist, nicht daran, wie eng
    die Bahnen liegen. Bei einer Schraffur über 500 mm mit 3 mm Abstand lagen
    zwischen beiden Zahlen mehr als zwei Größenordnungen.
    """
    if run_length_mm <= 0 or feed_mm_per_min <= 0:
        raise ValueError("Bahnlänge und Vorschub müssen größer als 0 sein")
    return (feed_mm_per_min / 60.0) / (2 * run_length_mm)


def dominant_spacing_mm(lines: Sequence[Line]) -> float | None:
    """Typischen Bahn*abstand* schätzen — für die Meldung, nicht für die Physik.

    Genommen wird der Median des Abstands zwischen aufeinanderfolgenden
    Linienanfängen. Bei einer Schraffur ist das der Bahnabstand; er bestimmt,
    wie fein das Bild wird, aber nicht, wie oft die Gondel umkehrt. Dafür ist
    :func:`dominant_run_length_mm` zuständig.
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


def dominant_run_length_mm(lines: Sequence[Line]) -> float | None:
    """Typische Länge einer Bahn zwischen zwei Richtungsumkehren.

    Das ist die Größe, die die Anregung bestimmt. Gemessen wird innerhalb der
    Linien: eine Polylinie kann selbst schon zickzacken (ein Mäander ist eine
    einzige Linie), und dann liegen die Umkehrpunkte mitten darin. Als
    Umkehr gilt ein Richtungswechsel um mehr als 90°.

    Zurück kommt der Median über alle so gefundenen Abschnitte — der Median,
    weil ein paar lange Anfahrten das Mittel sonst verziehen.
    """
    runs: list[float] = []
    for line in lines:
        if len(line) < 2:
            continue
        run = 0.0
        heading: tuple[float, float] | None = None
        for start, end in zip(line, line[1:], strict=False):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            if length <= 1e-9:
                continue
            direction = (dx / length, dy / length)
            # Verglichen wird mit der Richtung, in der dieser Abschnitt
            # begonnen hat, nicht mit dem Vorgängersegment. Ein Mäander biegt
            # zweimal um 90° ab: gegen den Vorgänger wäre das nie eine Umkehr,
            # gegen die Anfangsrichtung schon — und genau dort kippt die
            # Gondel.
            if heading is not None and heading[0] * direction[0] + heading[1] * direction[1] < 0.0:
                if run > 0:
                    runs.append(run)
                run = 0.0
                heading = direction
            elif heading is None:
                heading = direction
            run += length
        if run > 0:
            runs.append(run)
    runs = sorted(value for value in runs if value > 0.01)
    if not runs:
        return None
    return runs[len(runs) // 2]


@dataclass(frozen=True)
class ResonanceWarning:
    """Ergebnis der Resonanzprüfung."""

    critical: bool
    reversal_hz: float
    pendulum_hz: float
    run_length_mm: float
    """Länge einer Bahn zwischen zwei Umkehren — das ist, was anregt."""

    spacing_mm: float
    """Bahnabstand, nur zur Einordnung in der Meldung."""

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
    run_length = dominant_run_length_mm(lines)
    if run_length is None:
        return None
    spacing = dominant_spacing_mm(lines)

    pendulum = pendulum_frequency_hz(pen_below_pivot_mm)
    reversal = reversal_frequency_hz(run_length, feed_mm_per_min)
    ratio = reversal / pendulum
    critical = abs(ratio - 1.0) <= tolerance

    wo = f"{run_length:.0f} mm Bahnlänge"
    if spacing is not None:
        wo += f", {spacing:.1f} mm Bahnabstand"

    if not critical:
        message = (
            f"Umkehrfrequenz {reversal:.1f} Hz bei {wo}, "
            f"Gondel schwingt mit {pendulum:.1f} Hz — unkritisch."
        )
    else:
        # zwei Auswege: langsamer unter die Resonanz oder schneller darüber
        slower = pendulum * (1 - tolerance - 0.05) * 2 * run_length * 60
        faster = pendulum * (1 + tolerance + 0.05) * 2 * run_length * 60
        message = (
            f"Achtung: Umkehrfrequenz {reversal:.1f} Hz trifft die Pendelfrequenz "
            f"der Gondel ({pendulum:.1f} Hz) — das gibt wellige Linien. "
            f"Ausweg: Vorschub unter {slower:.0f} oder über {faster:.0f} mm/min, "
            f"oder die Bahnen anders legen ({wo})."
        )

    return ResonanceWarning(
        critical=critical,
        reversal_hz=reversal,
        pendulum_hz=pendulum,
        run_length_mm=run_length,
        spacing_mm=spacing if spacing is not None else 0.0,
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
