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
from .timing import plot_duration_s

__all__ = [
    "lines_to_gcode",
    "layers_to_gcode",
    "geometry_to_gcode",
    "prepare_geometry",
    "PlotStats",
    "stats_for",
]


def prepare_geometry(
    lines: Sequence[Line],
    config: PlotConfig | None = None,
    *,
    fit: bool = True,
    invert_y: bool | None = None,
    apply_origin: bool = True,
    correction=None,
    fit_bounds: tuple[float, float, float, float] | None = None,
) -> Lines:
    """Rohlinien in Plot-Geometrie überführen: einpassen, spiegeln, versetzen.

    Damit rechnen GCode-Export, Statistik und Vorschau auf derselben Geometrie
    — sonst weicht z. B. die geschätzte Plotdauer von der tatsächlichen ab.
    ``correction`` verzerrt zum Schluss gegen bekannte Maschinenfehler vor.

    Für die Vorschau ``invert_y=False`` und ``apply_origin=False`` setzen: SVG
    hat wie die Zeichnung den Ursprung oben links, und der Versatz der Fläche
    in Maschinenkoordinaten interessiert dort nicht.

    ``fit_bounds`` gibt die Ausgangs-Bounding-Box vor, statt sie aus ``lines``
    zu nehmen — so wird jede Farbebene mit derselben Abbildung eingepasst wie
    die Zeichnung als Ganzes (siehe :func:`layers_to_gcode`).
    """
    cfg = config or PlotConfig()
    geometry: Lines = [list(line) for line in lines if len(line) >= 2]
    if fit:
        geometry = fit_to_area(
            geometry,
            cfg.width_mm,
            cfg.height_mm,
            cfg.margin_mm,
            center=True,
            source_bounds=fit_bounds,
        )
    if cfg.invert_y if invert_y is None else invert_y:
        geometry = flip_y(geometry, cfg.height_mm)
    if apply_origin and (cfg.origin_x_mm or cfg.origin_y_mm):
        geometry = transform(geometry, 1.0, cfg.origin_x_mm, cfg.origin_y_mm)
    if correction is not None:
        # zuletzt: die Vorverzerrung rechnet in Maschinenkoordinaten
        geometry = correction.apply_lines(geometry)
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
        # Je Linie hebt und senkt der Servo einmal, und nach jedem Hub wartet
        # das Programm. Bei einem Punktraster ist das nicht die Nachkommastelle,
        # sondern der Löwenanteil: 5000 Punkte × 2 × 0,25 s sind gut 40 Minuten,
        # die eine reine Wegschätzung unterschlägt.
        self.pen_s = 2 * self.line_count * max(0.0, config.pen.dwell_s)
        # Mit Beschleunigungsprofil statt Weg durch Tempo — bei kurzen Strichen
        # ist das der Unterschied zwischen sieben und einundzwanzig Minuten.
        self.motion_s = plot_duration_s(
            lines, config.draw_feed, config.travel_feed, config.limits
        )
        self.naive_motion_s = estimate_duration_s(lines, config.draw_feed, config.travel_feed)
        self.duration_s = self.motion_s + self.pen_s

    def as_dict(self) -> dict[str, float | int]:
        return {
            "line_count": self.line_count,
            "point_count": self.point_count,
            "draw_mm": round(self.draw_mm, 1),
            "travel_mm": round(self.travel_mm, 1),
            "motion_s": round(self.motion_s, 1),
            "pen_s": round(self.pen_s, 1),
            "duration_s": round(self.duration_s, 1),
        }

    def __str__(self) -> str:
        text = (
            f"{self.line_count} Linien, {self.point_count} Punkte, "
            f"{self.draw_mm / 1000:.1f} m zeichnen + {self.travel_mm / 1000:.1f} m "
            f"Leerweg, geschätzt {self.duration_s / 60:.0f} min"
        )
        # nur ausweisen, wenn es weh tut — sonst rauscht die Zeile zu
        if self.pen_s > 0.1 * self.duration_s:
            text += f" (davon {self.pen_s / 60:.0f} min Stifthübe)"
        return text


def stats_for(lines: Sequence[Line], config: PlotConfig | None = None) -> PlotStats:
    return PlotStats(lines, config or PlotConfig())


def lines_to_gcode(
    lines: Sequence[Line],
    config: PlotConfig | None = None,
    *,
    fit: bool = True,
    header_comment: str | None = None,
    feeds: Sequence[float] | None = None,
    correction=None,
) -> str:
    """Linien (in mm) in ein vollständiges GCode-Programm übersetzen.

    Mit ``fit=True`` (Standard) wird die Zeichnung proportional in die
    bezeichnete Fläche abzüglich Rand eingepasst. Ist die Geometrie bereits in
    Flächenkoordinaten (etwa ein Testmuster), ``fit=False`` setzen.

    ``feeds`` setzt den Vorschub je Linie statt für alle gleich — gedacht für
    den Vorschub-Test aus :mod:`wallplotter.patterns`.

    ``correction`` verzerrt die Geometrie zum Schluss gegen die bekannten
    Maschinenfehler vor (siehe :mod:`wallplotter.correction`).
    """
    cfg = config or PlotConfig()
    if feeds is not None and len(feeds) != len([line for line in lines if len(line) >= 2]):
        raise ValueError("feeds muss genauso viele Einträge haben wie es Linien gibt")
    geometry = prepare_geometry(lines, cfg, fit=fit, correction=correction)
    return _program(geometry, cfg, header=header_comment, feeds=feeds)


def geometry_to_gcode(
    geometry: Lines,
    config: PlotConfig | None = None,
    *,
    header: str | None = None,
    feeds: Sequence[float] | None = None,
) -> str:
    """GCode aus bereits fertiger Maschinengeometrie.

    Der Weg für alles, was zwischen Einpassen und Ausgabe noch etwas mit der
    Geometrie vorhat — etwa den Vorschub je Linie an die örtliche Kondition
    der Kinematik anzupassen (:func:`wallplotter.motion.conditioning_feeds`).
    Sonst müsste dieselbe Geometrie zweimal gerechnet werden, einmal für die
    Vorschübe und einmal für die Ausgabe, und beide könnten auseinanderlaufen.
    """
    cfg = config or PlotConfig()
    if feeds is not None and len(feeds) != len(geometry):
        raise ValueError("feeds muss genauso viele Einträge haben wie es Linien gibt")
    return _program(geometry, cfg, header=header, feeds=feeds)


def _program(
    geometry: Lines,
    cfg: PlotConfig,
    *,
    header: str | None = None,
    feeds: Sequence[float] | None = None,
) -> str:
    """GCode-Programm aus fertiger Maschinengeometrie."""
    stats = PlotStats(geometry, cfg)
    out: list[str] = []

    if header:
        out.append(f"; {header}")
    out.append("; erzeugt mit wallplotter")
    out.append(f"; Flaeche {_fmt(cfg.width_mm)} x {_fmt(cfg.height_mm)} mm, Rand {_fmt(cfg.margin_mm)} mm")
    out.append(f"; {stats}")
    out.append("G21 ; Millimeter")
    out.append("G90 ; absolute Koordinaten")
    out.append("G17 ; XY-Ebene")
    out.extend(_pen_up(cfg.pen))

    travel_cmd = f"G1 F{_fmt(cfg.travel_feed, 1)}" if cfg.travel_as_g1 else "G0"
    pen_is_down = False

    for index, line in enumerate(geometry):
        feed = feeds[index] if feeds is not None else cfg.draw_feed
        start_x, start_y = line[0]
        if pen_is_down:
            out.extend(_pen_up(cfg.pen))
            pen_is_down = False
        out.append(f"{travel_cmd} X{_fmt(start_x)} Y{_fmt(start_y)}")
        out.extend(_pen_down(cfg.pen))
        pen_is_down = True

        out.append(f"G1 X{_fmt(line[1][0])} Y{_fmt(line[1][1])} F{_fmt(feed, 1)}")
        for x, y in line[2:]:
            out.append(f"G1 X{_fmt(x)} Y{_fmt(y)}")

    if pen_is_down:
        out.extend(_pen_up(cfg.pen))
    out.append("M5 ; Servo/PWM aus")
    out.append(f"{travel_cmd} X0 Y0")
    out.append("M2 ; Programmende")
    return "\n".join(out) + "\n"


def _program_with_pauses(blocks: Sequence[tuple[str, str, Lines]], cfg: PlotConfig) -> str:
    """Alle Ebenen in einem Programm, dazwischen Halt zum Stiftwechsel."""
    parts = []
    for position, (header, _, geometry) in enumerate(blocks):
        program = _program(geometry, cfg, header=header)
        body = program.splitlines()
        if position > 0:
            # Kopfzeilen der Folgeebenen kürzen, die Grundeinstellungen stehen schon
            body = [line for line in body if not line.startswith(("G21", "G90", "G17"))]
        # Programmende nur ganz am Schluss
        if position < len(blocks) - 1:
            body = [line for line in body if not line.startswith("M2 ")]
            next_label = blocks[position + 1][1]
            body += [f"M0 ; anhalten — Stift wechseln auf: {next_label}"]
        parts.append("\n".join(body))
    return "\n".join(parts) + "\n"


def layers_to_gcode(
    layers: Sequence,
    config: PlotConfig | None = None,
    *,
    separate: bool = True,
    fit: bool = True,
    correction=None,
) -> dict[str, str] | str:
    """Mehrfarbige Zeichnung in GCode übersetzen — eine Ebene je Stiftfarbe.

    Mit ``separate=True`` (Standard) kommt je Ebene eine eigene Datei heraus.
    Das ist für mehrstündige Plots das Vernünftige: man plottet Schwarz heute,
    Rot morgen, und muss nicht danebenstehen.

    Mit ``separate=False`` entsteht ein einziges Programm, das zwischen den
    Farben mit ``M0`` anhält. Bequemer, aber es setzt voraus, dass die
    Firmware ``M0`` als Pause versteht und jemand zum Weiterdrücken da ist.

    Wichtig für die Passgenauigkeit: Alle Ebenen werden *gemeinsam*
    eingepasst, nicht jede für sich — sonst zöge jede Farbe ihre eigene
    Skalierung und die Zeichnung fiele auseinander.
    """
    cfg = config or PlotConfig()
    entries = [layer for layer in layers if any(len(line) >= 2 for line in layer.lines)]
    if not entries:
        return {} if separate else ""

    # Gemeinsame Einpassung: die Grenzen über *alle* Ebenen bestimmen und jeder
    # Ebene als Bezug mitgeben. Die Ebene durchläuft danach dieselbe Kette wie
    # eine einfarbige Zeichnung — nur eben mit fremder Bounding-Box.
    combined = [line for layer in entries for line in layer.lines if len(line) >= 2]
    fit_bounds = _bounds(combined) if fit else None

    def geometry_of(layer):
        return prepare_geometry(
            layer.lines, cfg, fit=fit, correction=correction, fit_bounds=fit_bounds
        )

    if separate:
        result: dict[str, str] = {}
        for position, layer in enumerate(entries, start=1):
            result[layer.label] = _program(
                geometry_of(layer),
                cfg,
                header=f"Ebene {position}/{len(entries)}: {layer.label}",
            )
        return result

    blocks = [
        (f"Ebene {position}/{len(entries)}: {layer.label}", layer.label, geometry_of(layer))
        for position, layer in enumerate(entries, start=1)
    ]
    return _program_with_pauses(blocks, cfg)


def _bounds(lines: Sequence[Line]) -> tuple[float, float, float, float]:
    xs = [x for line in lines for x, _ in line]
    ys = [y for line in lines for _, y in line]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
