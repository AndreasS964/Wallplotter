"""Konfiguration für Wandfläche, Werkzeug und GCode-Ausgabe.

Alle Maße in Millimetern, alle Geschwindigkeiten in mm/min (GRBL-Konvention).

Was am unteren Ende der Gondel hängt, steht nicht mehr hier, sondern in
:mod:`wallplotter.toolhead` — seit dort auch ein Laser hineinpasst, ist ein
Feld namens ``pen`` nicht mehr die Wahrheit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .timing import MotionLimits
from .toolhead import PenToolhead, Toolhead

#: Alter Name des Stiftkopfes. Er beschreibt weiterhin genau das, was er immer
#: beschrieben hat, und trägt alle *benannten* Aufrufe unverändert. Positional
#: geht nicht mehr: der Kopf hat vorn ein Feld `name` bekommen. Ein alter
#: Aufruf `PenConfig(30, 0, 0.25)` läuft deshalb in einen ToolheadError statt
#: still einen Stift namens „30" anzulegen.
PenConfig = PenToolhead

# Wandfläche im Kletterwand-Keller (siehe docs/Projektidee.md)
WALL_WIDTH_MM = 2000.0
WALL_HEIGHT_MM = 2500.0


@dataclass(frozen=True)
class PlotConfig:
    """Alles, was einen konkreten Plot beschreibt."""

    width_mm: float = WALL_WIDTH_MM
    height_mm: float = WALL_HEIGHT_MM
    margin_mm: float = 50.0
    """Rand, der auf allen vier Seiten frei bleibt."""

    origin_x_mm: float = 0.0
    origin_y_mm: float = 0.0
    """Untere linke Ecke der Zeichenfläche in Maschinenkoordinaten.

    Wo die Fläche tatsächlich liegt, hängt an der Aufhängung und steht erst
    nach dem Kalibrieren fest (siehe :mod:`wallplotter.calibration`). Bis
    dahin fällt sie mit dem Maschinennullpunkt zusammen.
    """

    draw_feed: float = 1500.0
    """Vorschub bei Stift-auf-Wand (G1)."""

    travel_feed: float = 3000.0
    """Vorschub für Leerwege (G0 wird von FluidNC ohnehin mit Rapid gefahren;
    der Wert dient als Fallback, falls ``travel_as_g1`` gesetzt ist)."""

    travel_as_g1: bool = False
    """Leerwege als G1 mit ``travel_feed`` statt G0 ausgeben.

    Sinnvoll, wenn die Riemen bei vollem Rapid zum Springen neigen.
    """

    invert_y: bool = True
    """SVG hat den Ursprung oben links, die Maschine unten links.

    Mit ``True`` wird beim GCode-Export gespiegelt, damit das Bild nicht auf
    dem Kopf steht.
    """

    toolhead: Toolhead = field(default_factory=PenToolhead)
    """Was unten an der Gondel hängt — Stift, Laser, irgendwann anderes.

    Hieß früher ``pen``; die Eigenschaft :attr:`pen` liest weiterhin darauf,
    solange dort wirklich ein Stift sitzt.
    """

    limits: MotionLimits = field(default_factory=MotionLimits)
    """Beschleunigung und Höchsttempo der Maschine.

    Geht nicht in den GCode ein — das steht in der Firmware —, aber ohne diese
    Zahlen ist jede Laufzeitschätzung geraten. Vorgabe wie in
    ``config/fluidnc-wallplotter.yaml``.
    """

    @property
    def pen(self) -> Toolhead:
        """Der alte Name für :attr:`toolhead`, nur lesend."""
        return self.toolhead

    @property
    def drawable_width_mm(self) -> float:
        return self.width_mm - 2 * self.margin_mm

    @property
    def drawable_height_mm(self) -> float:
        return self.height_mm - 2 * self.margin_mm

    def __post_init__(self) -> None:
        if self.drawable_width_mm <= 0 or self.drawable_height_mm <= 0:
            raise ValueError(
                f"margin_mm={self.margin_mm} ist zu groß für die Fläche "
                f"{self.width_mm}×{self.height_mm} mm"
            )


@dataclass(frozen=True)
class FluidNCConfig:
    """Zugang zum FluidNC-Board im Heimnetz."""

    host: str = "fluidnc.local"
    """Hostname oder IP des ESP32 (WLAN-Station-Modus, siehe Projektidee)."""

    remote_dir: str = "/"
    """Zielverzeichnis auf der µSD-Karte."""

    timeout_s: float = 30.0

    @property
    def base_url(self) -> str:
        host = self.host
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        return host.rstrip("/")
