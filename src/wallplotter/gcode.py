"""Linien → GCode im FluidNC/GRBL-Dialekt.

Bewusst schlank und ohne vpype-gcode-Profil-Datei: Der erzeugte Dialekt ist
klein genug, um ihn direkt zu kontrollieren — ``G0``/``G1`` fürs Fahren,
``M3 S<wert>``/``M5`` fürs Pen-Lift (siehe docs/Projektidee.md, Abschnitt
Laser-Mode).
"""

from __future__ import annotations

from collections.abc import Sequence

from .config import PenConfig, PlotConfig
from .geometry import (
    Line,
    Lines,
    draw_length,
    estimate_duration_s,
    fit_to_area,
    flip_y,
    transform,
    travel_length,
)

__all__ = ["lines_to_gcode", "prepare_geometry", "PlotStats", "stats_for"]


def prepare_geometry(
    lines: Sequence[Line],
    config: PlotConfig | None = None,
    *,
    fit: bool = True,
    invert_y: bool | None = None,
    apply_origin: bool = True,
) -> Lines:
    """Rohlinien in Plot-Geometrie überführen: einpassen, spiegeln, versetzen.

    Damit rechnen GCode-Export, Statistik und Vorschau auf derselben Geometrie
    — sonst weicht z. B. die geschätzte Plotdauer von der tatsächlichen ab.
    Für die Vorschau ``invert_y=False`` und ``apply_origin=False`` setzen: SVG
    hat wie die Zeichnung den Ursprung oben links, und der Versatz der Fläche
    in Maschinenkoordinaten interessiert dort nicht.
    """
    cfg = config or PlotConfig()
    geometry: Lines = [list(line) for line in lines if len(line) >= 2]
    if fit:
        geometry = fit_to_area(
            geometry, cfg.width_mm, cfg.height_mm, cfg.margin_mm, center=True
        )
    if cfg.invert_y if invert_y is None else invert_y:
        geometry = flip_y(geometry, cfg.height_mm)
    if apply_origin and (cfg.origin_x_mm or cfg.origin_y_mm):
        geometry = transform(geometry, 1.0, cfg.origin_x_mm, cfg.origin_y_mm)
    return geometry


def _fmt(value: float, decimals: int = 3) -> str:
    """Koordinate ohne unnötige Nullen ausgeben (``10.500`` → ``10.5``)."""
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _pen_up(pen: PenConfig) -> list[str]:
    cmd = "M5" if pen.use_m5_for_up else f"M3 S{pen.up_value}"
    lines = [cmd]
    if pen.dwell_s > 0:
        lines.append(f"G4 P{_fmt(pen.dwell_s, 2)}")
    return lines


def _pen_down(pen: PenConfig) -> list[str]:
    lines = [f"M3 S{pen.down_value}"]
    if pen.dwell_s > 0:
        lines.append(f"G4 P{_fmt(pen.dwell_s, 2)}")
    return lines


class PlotStats:
    """Kennzahlen eines Plots, für CLI-Ausgabe und Web-UI."""

    def __init__(self, lines: Sequence[Line], config: PlotConfig) -> None:
        self.line_count = len(lines)
        self.point_count = sum(len(line) for line in lines)
        self.draw_mm = draw_length(lines)
        self.travel_mm = travel_length(lines)
        self.duration_s = estimate_duration_s(
            lines, config.draw_feed, config.travel_feed
        )

    def as_dict(self) -> dict[str, float | int]:
        return {
            "line_count": self.line_count,
            "point_count": self.point_count,
            "draw_mm": round(self.draw_mm, 1),
            "travel_mm": round(self.travel_mm, 1),
            "duration_s": round(self.duration_s, 1),
        }

    def __str__(self) -> str:
        minutes = self.duration_s / 60
        return (
            f"{self.line_count} Linien, {self.point_count} Punkte, "
            f"{self.draw_mm / 1000:.1f} m zeichnen + {self.travel_mm / 1000:.1f} m "
            f"Leerweg, geschätzt {minutes:.0f} min"
        )


def stats_for(lines: Sequence[Line], config: PlotConfig | None = None) -> PlotStats:
    return PlotStats(lines, config or PlotConfig())


def lines_to_gcode(
    lines: Sequence[Line],
    config: PlotConfig | None = None,
    *,
    fit: bool = True,
    header_comment: str | None = None,
) -> str:
    """Linien (in mm) in ein vollständiges GCode-Programm übersetzen.

    Mit ``fit=True`` (Standard) wird die Zeichnung proportional in die
    bezeichnete Fläche abzüglich Rand eingepasst. Ist die Geometrie bereits in
    Maschinenkoordinaten, ``fit=False`` setzen.
    """
    cfg = config or PlotConfig()
    geometry = prepare_geometry(lines, cfg, fit=fit)
    stats = PlotStats(geometry, cfg)
    out: list[str] = []

    if header_comment:
        out.append(f"; {header_comment}")
    out.append("; erzeugt mit wallplotter")
    out.append(f"; Flaeche {_fmt(cfg.width_mm)} x {_fmt(cfg.height_mm)} mm, Rand {_fmt(cfg.margin_mm)} mm")
    out.append(f"; {stats}")
    out.append("G21 ; Millimeter")
    out.append("G90 ; absolute Koordinaten")
    out.append("G17 ; XY-Ebene")
    out.extend(_pen_up(cfg.pen))

    travel_cmd = f"G1 F{_fmt(cfg.travel_feed, 1)}" if cfg.travel_as_g1 else "G0"
    pen_is_down = False

    for line in geometry:
        start_x, start_y = line[0]
        if pen_is_down:
            out.extend(_pen_up(cfg.pen))
            pen_is_down = False
        out.append(f"{travel_cmd} X{_fmt(start_x)} Y{_fmt(start_y)}")
        out.extend(_pen_down(cfg.pen))
        pen_is_down = True

        out.append(
            f"G1 X{_fmt(line[1][0])} Y{_fmt(line[1][1])} F{_fmt(cfg.draw_feed, 1)}"
        )
        for x, y in line[2:]:
            out.append(f"G1 X{_fmt(x)} Y{_fmt(y)}")

    if pen_is_down:
        out.extend(_pen_up(cfg.pen))
    out.append("M5 ; Servo/PWM aus")
    out.append(f"{travel_cmd} X0 Y0")
    out.append("M2 ; Programmende")
    return "\n".join(out) + "\n"
