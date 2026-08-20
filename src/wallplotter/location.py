"""Standorte: eine Aufhängung ist nichts Festes, sondern eine Messung.

Der Plotter soll an verschiedenen Wänden hängen — Kellerwand, Werkstatt,
irgendwo zu Besuch. Ankerabstand und -höhe sind damit keine Konstanten,
sondern Eigenschaften des jeweiligen Aufbaus. Dieses Modul hält sie
zusammen mit der zugehörigen Flächenkalibrierung unter einem Namen.

Pro Standort sind genau drei Maße nötig, alle mit dem Zollstock zu nehmen,
nachdem die Gondel am Referenzpunkt hängt und **das Board dort neu gestartet
wurde** — nicht erst nach einem G92 (siehe :class:`Location`):

* Abstand der beiden Umlenkpunkte zueinander
* Länge des linken Riemens vom Umlenkpunkt bis zur Gondel
* dasselbe rechts

Daraus folgt der Rest: Ankerkoordinaten, FluidNC-Konfiguration, erreichbare
Fläche, Auflösung und Zugkräfte.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .calibration import AreaCalibration, CalibrationError
from .config import PlotConfig
from .kinematics import Anchors, Gondola, Motor, WallPlotterKinematics, analyze_area

__all__ = ["Location", "LocationBook", "LocationError", "DEFAULT_PATH"]

DEFAULT_PATH = Path("standorte.json")


class LocationError(RuntimeError):
    """Standort unvollständig oder geometrisch unmöglich."""


@dataclass
class Location:
    """Ein Aufbau an einer bestimmten Wand.

    Alle Koordinaten hier beziehen sich auf den **Maschinennullpunkt**, und
    der entsteht nicht durch ``G92``, sondern beim Einschalten:

    .. code-block:: cpp

        void WallPlotter::init() {
            // We assume the machine starts at cartesian (0, 0, 0).
            xy_to_lengths(0, 0, zero_left, zero_right);

    FluidNC friert die Riemenlängen für kartesisch (0,0) beim Start bzw. beim
    Neuladen der Konfiguration ein — der Nullpunkt ist damit exakt die
    Gondelposition in diesem Augenblick. Ein späteres ``G92`` verschiebt nur
    das *Werkstück*-Koordinatensystem und lässt die Kinematik unberührt.

    Für die drei Maße heißt das: **Gondel an den Referenzpunkt, Board dort neu
    starten, dann messen.** Wer nach dem Booten erst joggt und dann nullt,
    bekommt Ankermaße, die um die Jog-Strecke daneben liegen — und eine
    Zeichnung, die verzerrt ist, ohne dass irgendwo eine Meldung erscheint.
    """

    name: str
    anchor_span_mm: float
    """Abstand der beiden Umlenkpunkte."""

    left_belt_zero_mm: float
    """Linke Riemenlänge am Nullpunkt."""

    right_belt_zero_mm: float
    """Rechte Riemenlänge am Nullpunkt."""

    gondola_mass_g: float = 300.0
    microsteps: int = 16
    note: str = ""
    calibration: AreaCalibration = field(default_factory=AreaCalibration)

    def __post_init__(self) -> None:
        span, left, right = self.anchor_span_mm, self.left_belt_zero_mm, self.right_belt_zero_mm
        if min(span, left, right) <= 0:
            raise LocationError("Alle drei Maße müssen größer als 0 sein")
        # Dreiecksungleichung: sonst treffen sich die Riemen nie
        if left + right <= span or abs(left - right) >= span:
            raise LocationError(
                f"Maße ergeben kein Dreieck: Spannweite {span:.0f} mm, "
                f"Riemen {left:.0f}/{right:.0f} mm — nachmessen"
            )

    # -- Geometrie --------------------------------------------------------

    def anchors(self) -> Anchors:
        """Ankerkoordinaten relativ zum Nullpunkt, per Trilateration.

        Die Gondel sitzt am Nullpunkt auf (0, 0). Aus den beiden Riemenlängen
        und der Spannweite folgt, wo die Umlenkpunkte darüber liegen.

        Angenommen wird, dass beide Anker gleich hoch hängen — der Normalfall,
        wenn sie an einer Deckenkante oder einer Leiste sitzen. Steht einer
        deutlich höher, misst man das an den ungleichen Riemenlängen: die
        Rechnung schiebt den Nullpunkt dann seitlich, nicht in der Höhe.

        Diese Annahme ist keine Bequemlichkeit, sondern deckt sich mit dem,
        was die Firmware rechnet: ``WallPlotter::lengths_to_xy()`` nimmt als
        Abstand der Kreismittelpunkte nur die **X-Differenz** der Anker und
        setzt ``y = left_anchor_y - h``. Bei ungleichen Ankerhöhen wäre die
        Rückrechnung dort falsch — und damit Positionsanzeige und
        Soft-Limit-Grenzen. Also: mit der Wasserwaage aufhängen.
        """
        span = self.anchor_span_mm
        # Abstand vom linken Anker zum Fußpunkt der Gondel auf der Ankerlinie
        offset = (self.left_belt_zero_mm**2 - self.right_belt_zero_mm**2 + span**2) / (2 * span)
        height_sq = self.left_belt_zero_mm**2 - offset**2
        if height_sq <= 0:
            raise LocationError("Riemenlängen ergeben keine Höhe — Maße prüfen")
        return Anchors(left_x=-offset, right_x=span - offset, y=math.sqrt(height_sq))

    def kinematics(self) -> WallPlotterKinematics:
        return WallPlotterKinematics(
            self.anchors(),
            Motor(microsteps=self.microsteps),
            Gondola(mass_g=self.gondola_mass_g),
        )

    def plot_config(self, base: PlotConfig | None = None) -> PlotConfig:
        """Konfiguration inklusive kalibrierter Fläche."""
        if not self.calibration.complete:
            raise LocationError(
                f"Standort {self.name!r} ist noch nicht eingemessen — erst die Ecken anfahren."
            )
        return self.calibration.to_plot_config(base)

    # -- Auswertung -------------------------------------------------------

    def analysis(self, samples: int = 15):
        """Kinematik über die kalibrierte Fläche auswerten.

        Die Fläche liegt im Maschinenkoordinatensystem, die Auswertung erwartet
        eine Fläche ab (0,0) — deshalb wird sie hier um ihren Versatz auf den
        Nullpunkt bezogen, damit Auflösung und Zug an der richtigen Stelle
        gemessen werden.
        """
        origin_x, origin_y, width, height = self.calibration.rectangle()
        anchors = self.anchors()
        shifted = WallPlotterKinematics(
            Anchors(
                left_x=anchors.left_x - origin_x,
                right_x=anchors.right_x - origin_x,
                y=anchors.y - origin_y,
            ),
            self.kinematics().motor,
            self.kinematics().gondola,
        )
        return analyze_area(shifted, width, height, samples)

    def report(self) -> str:
        anchors = self.anchors()
        lines = [
            f"Standort {self.name}",
            f"  Spannweite {self.anchor_span_mm:.0f} mm, Riemen am Nullpunkt "
            f"{self.left_belt_zero_mm:.0f}/{self.right_belt_zero_mm:.0f} mm",
            f"  Anker relativ zum Nullpunkt: links X{anchors.left_x:.1f}, "
            f"rechts X{anchors.right_x:.1f}, beide Y{anchors.y:.1f}",
        ]
        if self.note:
            lines.append(f"  Notiz: {self.note}")
        if not self.calibration.complete:
            lines.append("  Fläche noch nicht eingemessen.")
            return "\n".join(lines)

        lines.append("  " + self.calibration.summary().replace("\n", "\n  "))
        analysis = self.analysis()
        lines += ["  " + note for note in analysis.verdict(self.kinematics().motor)]
        return "\n".join(lines)

    def fluidnc_kinematics_yaml(self, segment_length_mm: float = 1.0) -> str:
        """Der ``kinematics``-Block für die config.yaml dieses Standorts."""
        anchors = self.anchors()
        return "\n".join(
            [
                f"# Standort: {self.name}",
                f"# gemessen: Spannweite {self.anchor_span_mm:.0f} mm, Riemen am Nullpunkt "
                f"{self.left_belt_zero_mm:.0f}/{self.right_belt_zero_mm:.0f} mm",
                "kinematics:",
                "  WallPlotter:",
                "    left_axis: 0",
                "    right_axis: 1",
                f"    left_anchor_x: {anchors.left_x:.3f}",
                f"    left_anchor_y: {anchors.y:.3f}",
                f"    right_anchor_x: {anchors.right_x:.3f}",
                f"    right_anchor_y: {anchors.y:.3f}",
                f"    segment_length: {segment_length_mm:.3f}",
            ]
        )

    # -- Serialisierung ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "anchor_span_mm": self.anchor_span_mm,
            "left_belt_zero_mm": self.left_belt_zero_mm,
            "right_belt_zero_mm": self.right_belt_zero_mm,
            "gondola_mass_g": self.gondola_mass_g,
            "microsteps": self.microsteps,
            "note": self.note,
            "calibration": {
                "points": {k: list(v) for k, v in self.calibration.points.items()},
                "note": self.calibration.note,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> Location:
        try:
            calibration_data = data.get("calibration") or {}
            calibration = AreaCalibration(
                points={
                    key: (float(value[0]), float(value[1]))
                    for key, value in (calibration_data.get("points") or {}).items()
                },
                note=str(calibration_data.get("note", "")),
            )
            return cls(
                name=str(data["name"]),
                anchor_span_mm=float(data["anchor_span_mm"]),
                left_belt_zero_mm=float(data["left_belt_zero_mm"]),
                right_belt_zero_mm=float(data["right_belt_zero_mm"]),
                gondola_mass_g=float(data.get("gondola_mass_g", 300.0)),
                microsteps=int(data.get("microsteps", 16)),
                note=str(data.get("note", "")),
                calibration=calibration,
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise LocationError(f"Standort nicht lesbar: {exc}") from exc


@dataclass
class LocationBook:
    """Alle bekannten Standorte plus der gerade aktive."""

    locations: dict[str, Location] = field(default_factory=dict)
    active: str | None = None

    def add(self, location: Location, activate: bool = True) -> None:
        self.locations[location.name] = location
        if activate or self.active is None:
            self.active = location.name

    def get(self, name: str | None = None) -> Location:
        key = name or self.active
        if key is None:
            raise LocationError("Noch kein Standort angelegt.")
        if key not in self.locations:
            known = ", ".join(sorted(self.locations)) or "keine"
            raise LocationError(f"Standort {key!r} unbekannt. Bekannt: {known}")
        return self.locations[key]

    def use(self, name: str) -> Location:
        location = self.get(name)
        self.active = name
        return location

    def remove(self, name: str) -> None:
        self.get(name)
        del self.locations[name]
        if self.active == name:
            self.active = next(iter(self.locations), None)

    def save(self, path: Path | str = DEFAULT_PATH) -> Path:
        target = Path(path)
        target.write_text(
            json.dumps(
                {
                    "active": self.active,
                    "locations": [loc.to_dict() for loc in self.locations.values()],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return target

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> LocationBook:
        source = Path(path)
        if not source.exists():
            return cls()
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            locations = [Location.from_dict(entry) for entry in data.get("locations", [])]
        except (ValueError, TypeError, CalibrationError) as exc:
            raise LocationError(f"{source} ist nicht lesbar: {exc}") from exc
        book = cls(locations={loc.name: loc for loc in locations})
        active = data.get("active")
        book.active = active if active in book.locations else next(iter(book.locations), None)
        return book
