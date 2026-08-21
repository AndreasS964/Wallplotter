"""Zeichenfläche per Software ausmessen, statt sie zu vermessen.

Wie groß die bemalbare Fläche wirklich ist, steht erst fest, wenn die Motoren
an der Wand hängen — Ankerabstand, Riemenlänge und der Anschlag beim
Referenzieren gehen alle ein. Deshalb: Gondel per Jog in jede Ecke fahren, dort
die Maschinenposition festhalten, und daraus die nutzbare Fläche ableiten.

Ergebnis ist ein :class:`~wallplotter.config.PlotConfig` mit Versatz — alles
Weitere (Einpassen, GCode, Vorschau) rechnet damit unverändert weiter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import PlotConfig

__all__ = ["CORNERS", "AreaCalibration", "CalibrationError", "DEFAULT_PATH"]

CORNERS = ("bottom-left", "bottom-right", "top-right", "top-left")
"""Reihenfolge einmal im Uhrzeigersinn — so, wie man sie auch anfährt."""

DEFAULT_PATH = Path("calibration.json")


class CalibrationError(RuntimeError):
    """Kalibrierung unvollständig oder unbrauchbar."""


@dataclass
class AreaCalibration:
    """Angefahrene Eckpunkte in Maschinenkoordinaten."""

    points: dict[str, tuple[float, float]] = field(default_factory=dict)
    note: str = ""

    # -- Aufnahme ---------------------------------------------------------

    def record(self, corner: str, position: tuple[float, float]) -> None:
        if corner not in CORNERS:
            raise CalibrationError(f"Unbekannte Ecke {corner!r}, erlaubt: {', '.join(CORNERS)}")
        self.points[corner] = (float(position[0]), float(position[1]))

    @property
    def missing(self) -> list[str]:
        return [corner for corner in CORNERS if corner not in self.points]

    @property
    def complete(self) -> bool:
        """Vier Ecken sind ideal, zwei diagonale reichen zur Not — beide
        Diagonalen: ``rectangle()`` bestimmt Breite und Höhe je Seite über
        die jeweils vorhandene Ecke, das funktioniert für beide gleich."""
        if not self.missing:
            return True
        points = set(self.points)
        return {"bottom-left", "top-right"} <= points or {"bottom-right", "top-left"} <= points

    # -- Auswertung -------------------------------------------------------

    def rectangle(self) -> tuple[float, float, float, float]:
        """Größtes achsparalleles Rechteck *innerhalb* der angefahrenen Punkte.

        Gibt ``(origin_x, origin_y, width, height)`` zurück. Bewusst
        konservativ: von zwei linken Ecken zählt die weiter rechts liegende,
        von zwei unteren die höhere. Hängt die Wand leicht schief, wird die
        Fläche dadurch etwas kleiner statt außerhalb zu landen.
        """
        if not self.complete:
            raise CalibrationError(
                "Kalibrierung unvollständig, es fehlen: " + ", ".join(self.missing)
            )

        def x_of(*corners: str) -> list[float]:
            return [self.points[c][0] for c in corners if c in self.points]

        def y_of(*corners: str) -> list[float]:
            return [self.points[c][1] for c in corners if c in self.points]

        left = max(x_of("bottom-left", "top-left"))
        right = min(x_of("bottom-right", "top-right"))
        bottom = max(y_of("bottom-left", "bottom-right"))
        top = min(y_of("top-left", "top-right"))

        width, height = right - left, top - bottom
        if width <= 0 or height <= 0:
            raise CalibrationError(
                f"Ecken ergeben keine Fläche ({width:.1f} × {height:.1f} mm) — "
                "sind links/rechts oder oben/unten vertauscht?"
            )
        return (left, bottom, width, height)

    def skew_mm(self) -> tuple[float, float]:
        """Wie schief die angefahrenen Ecken zueinander stehen.

        ``(horizontal, vertikal)``: Höhenunterschied der beiden unteren bzw.
        oberen Ecken und seitlicher Versatz der linken bzw. rechten. Große
        Werte heißen: Aufhängung prüfen, nicht einfach weiterplotten.
        """
        if self.missing:
            return (0.0, 0.0)
        bl, br = self.points["bottom-left"], self.points["bottom-right"]
        tl, tr = self.points["top-left"], self.points["top-right"]
        horizontal = max(abs(bl[1] - br[1]), abs(tl[1] - tr[1]))
        vertical = max(abs(bl[0] - tl[0]), abs(br[0] - tr[0]))
        return (horizontal, vertical)

    def to_plot_config(self, base: PlotConfig | None = None) -> PlotConfig:
        """Kalibrierung in eine einsatzfertige Konfiguration überführen."""
        origin_x, origin_y, width, height = self.rectangle()
        template = base or PlotConfig()
        margin = min(template.margin_mm, min(width, height) / 4)
        return PlotConfig(
            width_mm=width,
            height_mm=height,
            margin_mm=margin,
            origin_x_mm=origin_x,
            origin_y_mm=origin_y,
            draw_feed=template.draw_feed,
            travel_feed=template.travel_feed,
            travel_as_g1=template.travel_as_g1,
            invert_y=template.invert_y,
            toolhead=template.toolhead,
            limits=template.limits,
        )

    def summary(self) -> str:
        if not self.complete:
            return "Kalibrierung unvollständig, es fehlen: " + ", ".join(self.missing)
        origin_x, origin_y, width, height = self.rectangle()
        lines = [
            f"Fläche {width:.0f} × {height:.0f} mm, "
            f"untere linke Ecke bei X{origin_x:.1f} Y{origin_y:.1f}",
        ]
        horizontal, vertical = self.skew_mm()
        if horizontal or vertical:
            lines.append(f"Schiefstand: {horizontal:.1f} mm horizontal, {vertical:.1f} mm vertikal")
        if self.missing:
            lines.append("Nur über die Diagonale bestimmt — Schiefstand ungeprüft.")
        if self.note:
            lines.append(f"Notiz: {self.note}")
        return "\n".join(lines)

    # -- Persistenz -------------------------------------------------------

    def save(self, path: Path | str = DEFAULT_PATH) -> Path:
        target = Path(path)
        target.write_text(
            json.dumps(
                {"points": {k: list(v) for k, v in self.points.items()}, "note": self.note},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return target

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> AreaCalibration:
        source = Path(path)
        if not source.exists():
            raise CalibrationError(f"Keine Kalibrierung unter {source} — erst Ecken anfahren.")
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            points = {key: (float(v[0]), float(v[1])) for key, v in data["points"].items()}
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            # AttributeError: gültiges JSON, aber "points" ist z. B. eine
            # Liste statt eines Wörterbuchs und hat kein .items().
            raise CalibrationError(f"{source} ist keine gültige Kalibrierung: {exc}") from exc
        unknown = set(points) - set(CORNERS)
        if unknown:
            raise CalibrationError(f"Unbekannte Ecken in {source}: {', '.join(sorted(unknown))}")
        return cls(points=points, note=str(data.get("note", "")))
