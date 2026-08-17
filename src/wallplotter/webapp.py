"""Lokale Web-UI auf NiceGUI-Basis (Stufen 3 bis 6 der Roadmap).

Start:

    python -m wallplotter.webapp

Danach von jedem Gerät im Heimnetz erreichbar unter ``http://<pc-ip>:8080`` —
Bedienung am Rechner wie am Handy an der Wand.

Aufgeteilt in drei Reiter, weil das die drei Situationen sind, in denen man
vor der Wand steht: etwas plotten, die Fläche einmessen, dem Ding beim
Arbeiten zusehen.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace

from .calibration import CORNERS, AreaCalibration
from .config import WALL_HEIGHT_MM, WALL_WIDTH_MM, FluidNCConfig, PlotConfig
from .gcode import geometry_to_gcode, layers_to_gcode, prepare_geometry, stats_for
from .imaging import IMAGE_SUFFIXES, TECHNIQUES, ImagingError
from .imaging import image_to_lines as image_lines
from .location import DEFAULT_PATH as LOCATIONS_PATH
from .location import Location, LocationBook, LocationError
from .motion import resonance_warning
from .patterns import PATTERNS, build
from .pipeline import VpypeNotAvailable, lines_to_svg, svg_to_layers
from .toolhead import (
    TOOLHEADS,
    LaserToolhead,
    PenToolhead,
    Toolhead,
    ToolheadError,
    toolhead_by_name,
)
from .upload import FluidNCClient, FluidNCError

CORRECTION_PATH = "korrektur.json"
JOG_STEPS = [1, 10, 50, 100]
DEFAULT_PITCH_MM = 25.0
DEFAULT_JOG_STEP_MM = 10

PATTERN_INTRO = "Fünf Muster für die Inbetriebnahme — Fläche, Maßstab, Servo, Tempo."

DRAW_COLOR = "#1a4fd6"
"""Zeichenwege in der Vorschau — und zugleich die Akzentfarbe der Oberfläche.

Kein Zufall: Was der Stift auf die Wand bringt, ist das, worum es geht. Eine
zweite, dekorative Akzentfarbe daneben hätte nichts zu sagen."""

TRAVEL_COLOR = "#d64545"

EMPTY_PREVIEW = (
    '<div class="wp-empty">'
    '<svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M3 5h18v14H3z"/><path d="M3 15l5-5 4 4 3-3 6 6"/><circle cx="8.5" cy="9" r="1.5"/>'
    "</svg>"
    "<p><b>Noch nichts geladen</b></p>"
    "<p>SVG oder Foto hochladen — oder ein Testmuster wählen.</p>"
    "</div>"
)

#: Das ganze Aussehen an einer Stelle. Bewusst als Stylesheet und nicht als
#: Utility-Klassen im Aufbau verteilt: Wer die Farbe der Karten ändern will,
#: soll eine Zeile ändern müssen, nicht vierzig.
#:
#: Keine Schrift von einem CDN. Die Oberfläche soll in einem Keller laufen,
#: in dem das WLAN gerade so bis zum Board reicht — eine Google-Font, die
#: nicht lädt, ist dort kein Schönheitsfehler, sondern ein Layoutsprung.
STYLE = """
<style>
:root {
  --wp-ink: #14181c;
  --wp-ink-soft: #495561;
  --wp-muted: #7b8794;
  --wp-line: #e3e7ec;
  --wp-surface: #ffffff;
  --wp-bg: #f2f4f7;
  --wp-accent: #1a4fd6;
  --wp-accent-soft: #eef2fd;
  --wp-ok: #157f52;
  --wp-warn: #b45309;
  --wp-bad: #c0392b;
  --wp-radius: 12px;
}

body {
  background: var(--wp-bg);
  color: var(--wp-ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "Helvetica Neue", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}

/* Quasar schreibt Knöpfe versal. Das schreit, und eine Werkstattoberfläche
   soll nicht schreien. */
.q-btn { text-transform: none; font-weight: 550; letter-spacing: 0; }
.q-tab { text-transform: none; }

/* -- Kopfzeile ---------------------------------------------------------- */
.wp-header {
  background: var(--wp-ink);
  box-shadow: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.wp-wordmark { font-size: 1.05rem; font-weight: 650; letter-spacing: -0.01em; }
.wp-wordmark span { color: rgba(255, 255, 255, 0.45); font-weight: 400; }

.wp-status {
  display: inline-flex; align-items: center; gap: 0.5rem;
  padding: 0.3rem 0.7rem 0.3rem 0.55rem;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  font-size: 0.82rem; font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.wp-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--wp-muted); flex: none; }
.wp-dot.is-run   { background: #34d399; box-shadow: 0 0 0 3px rgba(52, 211, 153, 0.18); }
.wp-dot.is-hold  { background: #fbbf24; box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.18); }
.wp-dot.is-alarm { background: #f87171; box-shadow: 0 0 0 3px rgba(248, 113, 113, 0.18); }
.wp-dot.is-idle  { background: #9aa5b1; }

/* -- Reiter ------------------------------------------------------------- */
.wp-tabs { background: var(--wp-ink); color: rgba(255,255,255,0.65); }
.wp-tabs .q-tab { min-height: 46px; padding: 0 1.1rem; font-size: 0.9rem; }
.wp-tabs .q-tab--active { color: #fff; }
.wp-tabs .q-tab__content { min-width: 0; }
.wp-tabs .q-tab--active .q-focus-helper,
.wp-tabs .q-tab .q-focus-helper { opacity: 0 !important; }
.wp-tabs .q-tab__icon { font-size: 20px; }

/* -- Karten ------------------------------------------------------------- */
.wp-card {
  background: var(--wp-surface);
  border: 1px solid var(--wp-line);
  border-radius: var(--wp-radius);
  box-shadow: 0 1px 2px rgba(20, 24, 28, 0.04);
  padding: 1.05rem 1.1rem;
  gap: 0.75rem;
}
.wp-card-title {
  font-size: 0.7rem; font-weight: 700; letter-spacing: 0.07em;
  text-transform: uppercase; color: var(--wp-muted);
}
.wp-hint { font-size: 0.78rem; line-height: 1.45; color: var(--wp-muted); }
.wp-readout {
  font-size: 0.8rem; line-height: 1.5; color: var(--wp-ink-soft);
  font-variant-numeric: tabular-nums;
}
.wp-rule { height: 1px; background: var(--wp-line); margin: 0.2rem 0; }

/* -- Vorschau ----------------------------------------------------------- */
.wp-stage {
  background: var(--wp-surface);
  border: 1px solid var(--wp-line);
  border-radius: var(--wp-radius);
  overflow: hidden;
}
.wp-stage-body { padding: 1.1rem; }
.wp-stage-foot {
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;
  padding: 0.6rem 1.1rem;
  border-top: 1px solid var(--wp-line);
  background: #fafbfc;
  font-size: 0.8rem; color: var(--wp-ink-soft);
  font-variant-numeric: tabular-nums;
}
.wp-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 0.4rem; min-height: 52vh; color: var(--wp-muted); text-align: center;
}
.wp-empty p { margin: 0; font-size: 0.86rem; }
.wp-empty b { color: var(--wp-ink-soft); font-weight: 600; }
.wp-key { display: inline-flex; align-items: center; gap: 0.4rem; }
.wp-key i { display: inline-block; width: 16px; height: 0; border-top-width: 2px; }

/* -- Ablagefeld für Dateien --------------------------------------------- */
.wp-drop .q-uploader {
  width: 100%; box-shadow: none; background: transparent;
  border: 1.5px dashed var(--wp-line); border-radius: 10px;
  transition: border-color 0.15s, background 0.15s;
}
.wp-drop .q-uploader:hover { border-color: var(--wp-accent); background: var(--wp-accent-soft); }
.wp-drop .q-uploader__header { background: transparent; color: var(--wp-ink-soft); }
/* Fortschrittsbalken und Byte-Zähler des Uploaders: Bei einer Datei, die aus
   dem Nachbarordner kommt, ist beides in derselben Sekunde vorbei und sieht
   nur nach halbfertiger Oberfläche aus. */
.wp-drop .q-uploader__header .q-linear-progress,
.wp-drop .q-uploader__title + div { display: none; }

/* -- Testmuster --------------------------------------------------------- */
.wp-chip .q-btn {
  border: 1px solid var(--wp-line); border-radius: 8px;
  color: var(--wp-ink-soft) !important; background: var(--wp-surface);
  font-size: 0.82rem; padding: 0.3rem 0.7rem; min-height: 34px;
}
.wp-chip .q-btn:hover { border-color: var(--wp-accent); color: var(--wp-accent) !important; }

/* -- Jog-Feld ----------------------------------------------------------- */
.wp-jog { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.5rem; }
.wp-jog .q-btn {
  min-height: 58px; border-radius: 10px;
  border: 1px solid var(--wp-line); background: var(--wp-surface); color: var(--wp-ink);
}
.wp-jog .q-btn:hover { border-color: var(--wp-accent); color: var(--wp-accent); }
.wp-jog .wp-stop .q-btn { border-color: #f3c6c1; color: var(--wp-bad); background: #fff6f5; }

/* -- Ecken -------------------------------------------------------------- */
.wp-corner {
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0.35rem 0; font-size: 0.88rem;
}
.wp-tick {
  width: 20px; height: 20px; border-radius: 50%; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 0.7rem; font-weight: 700;
  background: #eef1f4; color: var(--wp-muted);
}
.wp-tick.is-set { background: #dff3e8; color: var(--wp-ok); }

/* -- Dateien auf der Karte ---------------------------------------------- */
.wp-file {
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0.3rem 0; font-size: 0.85rem;
  font-variant-numeric: tabular-nums;
  border-bottom: 1px solid var(--wp-line);
}
.wp-file:last-child { border-bottom: none; }

/* -- Laufender Job ------------------------------------------------------ */
.wp-job-state { font-size: 1.5rem; font-weight: 650; letter-spacing: -0.02em; }
.wp-job-file {
  font-size: 0.82rem; color: var(--wp-muted);
  font-variant-numeric: tabular-nums; word-break: break-all;
}
.wp-metric-label {
  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.07em;
  text-transform: uppercase; color: var(--wp-muted);
}
.wp-metric-value {
  font-size: 1.05rem; font-variant-numeric: tabular-nums; color: var(--wp-ink);
}

/* -- Schrittweite ------------------------------------------------------- */
.wp-steps {
  border: 1px solid var(--wp-line); border-radius: 9px; overflow: hidden;
  background: var(--wp-surface);
}
.wp-steps .q-btn { border-radius: 0; min-height: 38px; font-variant-numeric: tabular-nums; }

/* -- Handy -------------------------------------------------------------- */
@media (max-width: 1023px) {
  .wp-card { padding: 0.95rem 1rem; }
  .wp-jog .q-btn { min-height: 64px; }
  .wp-stage-body { padding: 0.6rem; }
}
@media (max-width: 700px) {
  .wp-wordmark { font-size: 0.98rem; }
  .wp-status { font-size: 0.78rem; padding: 0.25rem 0.6rem 0.25rem 0.5rem; }
  .wp-tabs .q-tab { padding: 0 0.7rem; font-size: 0.85rem; }
}
</style>
"""


def _card_entries(listing: str) -> list[tuple[str, int]]:
    """Dateiliste der Karte aus der JSON-Antwort der Firmware.

    Kaputte oder unerwartete Antworten geben eine leere Liste: Die Karte ist
    eine Nebensache der Oberfläche, und ein Traceback vor der Wand hilft
    niemandem.
    """
    import json  # noqa: PLC0415

    try:
        data = json.loads(listing)
        files = data.get("files") or []
    except (ValueError, AttributeError):
        return []
    entries = []
    for entry in files:
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        try:
            size = int(entry.get("size", 0))
        except (TypeError, ValueError):
            size = 0
        # Verzeichnisse meldet die Firmware mit negativer Größe
        if size >= 0:
            entries.append((name, size))
    return sorted(entries)


def _bytes(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size >= 1000:
        return f"{size / 1000:.0f} kB"
    return f"{size} B"


def _key_html(color: str, dash: str, text: str) -> str:
    """Ein Eintrag der Vorschaulegende — Strichprobe und Beschriftung."""
    return f'<span class="wp-key"><i style="border-top:2px {dash} {color}"></i>{text}</span>'


def _positive(value, fallback: float) -> float:
    """Zahlenfeldwert, der garantiert größer als null ist."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _require_nicegui():
    try:
        from nicegui import ui  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("NiceGUI ist nicht installiert — `pip install -e .[web]`") from exc
    return ui


class WallplotterUI:
    """Zustand und Aufbau der Oberfläche.

    Als Klasse statt loser Funktionen, damit Zeichnung, Kalibrierung und
    Verbindung an einer Stelle liegen — die drei Reiter greifen alle darauf zu.
    """

    def __init__(self, ui, host: str = "fluidnc.local", locations_path: str = str(LOCATIONS_PATH)):
        self.ui = ui
        self.host = host
        self.locations_path = locations_path
        self.lines: list = []
        self.feeds: list[float] | None = None
        self.source_name = ""
        self.source_is_pattern = False
        self.gcode: str | None = None
        self.layers: list = []
        # Eine unlesbare Standortdatei darf die Oberfläche nicht am Start
        # hindern — sie ist die einzige Datei, die man vor der Wand von Hand
        # anfasst, und ohne Oberfläche lässt sie sich auch nicht reparieren.
        try:
            self.book = LocationBook.load(locations_path)
            self.book_error = ""
        except LocationError as exc:
            self.book, self.book_error = LocationBook(), str(exc)
        self.correction_path = CORRECTION_PATH
        self._clients: dict = {}

    @property
    def location(self) -> Location | None:
        """Der aktive Standort, falls schon einer angelegt ist."""
        try:
            return self.book.get()
        except LocationError:
            return None

    @property
    def calibration(self) -> AreaCalibration:
        location = self.location
        return location.calibration if location else AreaCalibration()

    def save_book(self) -> None:
        self.book.save(self.locations_path)

    # -- Konfiguration ----------------------------------------------------

    def toolhead(self) -> Toolhead:
        """Der gewählte Kopf, mit den Feldern der Oberfläche nachjustiert.

        Der Katalogeintrag liefert die Startwerte; wer an den Servo-Reglern
        dreht, meint genau diesen Stift. Ein Laser hat keine Servo-Werte —
        dort bleiben die Regler wirkungslos, statt Unsinn zu erzeugen.
        """
        head = toolhead_by_name(self.head_select.value or "fineliner")
        if isinstance(head, LaserToolhead):
            return head
        return replace(
            head,
            down_value=int(self.pen_down.value or head.down_value),
            up_value=int(self.pen_up.value or 0),
            # negative Wartezeit gäbe es an keinem Servo, und ToolheadError
            # mitten im Aufbau der Konfiguration hilft niemandem
            dwell_s=max(0.0, self.pen_dwell.value if self.pen_dwell.value is not None else head.dwell_s),
        )

    def plot_config(self) -> PlotConfig:
        # Ein Zahlenfeld nimmt jede Eingabe an: 2000 mm Rand auf 2000 mm Fläche,
        # eine negative Breite, einen Vorschub von minus tausend. PlotConfig und
        # das Bewegungsmodell werfen dafür zu Recht — aber ein Tippfehler darf
        # die Oberfläche nicht zerlegen, während man vor der Wand steht.
        width = _positive(self.width.value, WALL_WIDTH_MM)
        height = _positive(self.height.value, WALL_HEIGHT_MM)
        margin = min(max(self.margin.value or 0, 0.0), max(min(width, height) / 2 - 1, 0.0))
        config = PlotConfig(
            width_mm=width,
            height_mm=height,
            margin_mm=margin,
            draw_feed=_positive(self.draw_feed.value, 1500.0),
            toolhead=self.toolhead(),
        )
        if self.calibration.complete and self.use_calibration.value:
            config = self.calibration.to_plot_config(config)
        # Muster sind in Maschinenkoordinaten geschrieben, SVGs nicht
        return replace(config, invert_y=not self.source_is_pattern)

    def client(self, timeout: float = 10.0) -> FluidNCClient:
        """Client für das Board — mit wiederverwendeter Verbindung.

        Die Statusabfrage läuft alle zwei Sekunden. Jedes Mal einen neuen
        Client zu bauen hieße jedes Mal eine neue ``requests.Session`` und
        einen neuen WebSocket-Kanal zum ESP32 — von beidem hat er nicht viele,
        und FluidNC schließt beim Verbinden ältere Kanäle derselben Sitzung.

        Deshalb wird pro Zeitlimit **einer** gehalten (Statusabfrage und
        Plotstart haben verschiedene Geduld) und beim Wechsel des Hosts alles
        sauber geschlossen, statt die Verbindungen liegenzulassen.
        """
        host = self.host_input.value
        if self._clients.get("host") != host:
            self.close_clients()
            self._clients = {"host": host, "by_timeout": {}}
        clients = self._clients["by_timeout"]
        if timeout not in clients:
            clients[timeout] = FluidNCClient(FluidNCConfig(host=host, timeout_s=timeout))
        return clients[timeout]

    def close_clients(self) -> None:
        """Offene Kanäle zumachen — beim Hostwechsel und beim Beenden."""
        for existing in self._clients.get("by_timeout", {}).values():
            try:
                existing.close()
            except Exception:  # noqa: BLE001 - beim Aufräumen ist alles verzeihlich
                pass
        self._clients = {}

    def correction(self):
        """Vorverzerrung, falls eine Datei danebenliegt und sie gewollt ist.

        Das Dehnungsmodell braucht die Ankermaße des Standorts; das gemessene
        Polynom kommt ohne aus. Fehlt beides, wird eben nicht vorverzerrt —
        das ist kein Fehler, sondern der Normalfall vor der ersten Messreihe.
        """
        if not getattr(self, "use_correction", None) or not self.use_correction.value:
            return None
        from .correction import CorrectionError, load_correction  # noqa: PLC0415

        location = self.location
        try:
            return load_correction(
                self.correction_path,
                kinematics=location.kinematics() if location else None,
            )
        except CorrectionError:
            return None

    def remote_name(self) -> str:
        """Dateiname auf der Karte, aus dem Namen der Vorlage abgeleitet."""
        stem = os.path.splitext(self.source_name.split(" (")[0])[0].strip()
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stem).strip("-")
        return f"{safe or 'plot'}.gcode"

    def laser_blocked(self) -> str:
        """Meldung, falls ein Laser gewählt, aber nicht scharfgeschaltet ist.

        Das Gegenstück zu ``--laser-verstanden``: In der Oberfläche steht der
        Laser sonst als gewöhnlicher Menüeintrag neben den Stiften, und ein
        Klick erzeugte ein vollständiges Laserprogramm, das sich mit dem
        nächsten Knopf hochladen und starten lässt.
        """
        head = toolhead_by_name(self.head_select.value or "fineliner")
        armed = getattr(self, "laser_armed", None)
        if isinstance(head, LaserToolhead) and not (armed and armed.value):
            return (
                "Laser gewählt, aber nicht scharfgeschaltet. Erst die Warnungen lesen "
                "und den Schalter \u201eLaser scharf\u201c umlegen \u2014 dann entsteht Laser-GCode."
            )
        return ""

    def change_toolhead(self) -> None:
        """Werkzeug gewechselt: Regler auf die Katalogwerte, Warnungen zeigen."""
        head = toolhead_by_name(self.head_select.value or "fineliner")
        if isinstance(head, PenToolhead):
            self.pen_down.set_value(head.down_value)
            self.pen_up.set_value(head.up_value)
            self.pen_dwell.set_value(head.dwell_s)
        self.head_label.set_text(head.describe())
        # Beim Laser darf niemand die Warnungen übersehen — sie stehen deshalb
        # nicht klein im Feld, sondern als Meldung mit langer Standzeit.
        for note in head.check(travel_as_g1=False, draw_feed=self.draw_feed.value or 1500):
            self.ui.notify(note, type="warning", multi_line=True, timeout=10000)
        self.regenerate()

    # -- Zeichnung laden --------------------------------------------------

    async def load_upload(self, event) -> None:
        self.upload_data = event.content.read()
        self.upload_name = event.name
        await self.render_upload()

    def _convert_upload(self, suffix: str, config: PlotConfig):
        """Die eigentliche Umrechnung — rechenintensiv, gehört in einen Thread.

        Ein TSP-Weg über zehntausend Punkte oder eine Spirale über zwei Meter
        Wand rechnet Sekunden. In der Event-Loop stünde die Oberfläche
        währenddessen für alle.
        """
        if suffix in IMAGE_SUFFIXES:
            options = {}
            if self.technique.value in ("hatch", "spiral"):
                # geleertes Zahlenfeld liefert None, und None geht in keine Rechnung
                options["pitch_mm"] = self.pitch.value or DEFAULT_PITCH_MM
            lines = image_lines(
                self.upload_data,
                config.width_mm,
                config.height_mm,
                self.technique.value,
                margin_mm=config.margin_mm,
                image_suffix=suffix,
                **options,
            )
            return lines, [], f"{self.upload_name} ({self.technique.value})", False
        layers = svg_to_layers(self.upload_data)
        name = self.upload_name
        if len(layers) > 1:
            name += f" ({len(layers)} Farben)"
        return [line for layer in layers for line in layer.lines], layers, name, True

    async def render_upload(self) -> None:
        """Hochgeladene Vorlage (neu) übersetzen — auch beim Verfahrenswechsel."""
        if not getattr(self, "upload_data", None):
            return
        suffix = os.path.splitext(self.upload_name)[1].lower()
        config = self.plot_config()
        try:
            lines, layers, name, fit = await asyncio.to_thread(
                self._convert_upload, suffix, config
            )
        except (VpypeNotAvailable, ImagingError) as exc:
            self.ui.notify(str(exc), type="negative", multi_line=True)
            return
        # Bildverfahren rechnen selbst in Millimetern und werden nicht eingepasst
        self.lines, self.layers, self.fit_source = lines, layers, fit
        self.source_is_pattern = False
        self.feeds, self.source_name = None, name
        self.refresh_layers()
        self.regenerate()
        self.ui.notify(f"{len(self.lines)} Linien", type="positive")

    def load_pattern(self, name: str) -> None:
        config = self.plot_config()
        try:
            pattern = build(name, config.width_mm, config.height_mm, config.margin_mm)
        except (KeyError, ValueError) as exc:
            self.ui.notify(str(exc), type="negative")
            return
        self.lines, self.feeds = pattern.lines, pattern.feeds
        self.source_name, self.source_is_pattern = pattern.name, True
        self.fit_source = False
        self.pattern_hint.set_text(pattern.description)
        self.regenerate()

    def regenerate(self) -> None:
        if not self.lines:
            return
        blocked = self.laser_blocked()
        if blocked:
            self.gcode = None
            self.info.set_text(blocked)
            self.preview.content = EMPTY_PREVIEW
            return
        config = self.plot_config()
        fit = getattr(self, "fit_source", True) and not self.source_is_pattern
        correction = self.correction()
        # Einmal in Maschinenkoordinaten rechnen und dabei bleiben: Statistik,
        # GCode und Warnung müssen dieselbe Geometrie sehen.
        machine_lines = prepare_geometry(self.lines, config, fit=fit, correction=correction)
        try:
            self.gcode = geometry_to_gcode(
                machine_lines, config, header=self.source_name, feeds=self.feeds
            )
        except ToolheadError as exc:
            self.gcode = None
            self.ui.notify(str(exc), type="negative", multi_line=True, timeout=10000)
            return

        note = f"{self.source_name}: {stats_for(machine_lines, config)}"
        if correction is not None:
            note += "  ·  vorverzerrt"
        warning = resonance_warning(machine_lines, config.toolhead.feed_for(config.draw_feed))
        if warning is not None and warning.critical:
            # Genau die Zeichnungen, die man am liebsten plottet — dichte
            # Schraffuren —, treffen die Pendelfrequenz der Gondel.
            note += f"\n{warning}"
        self.info.set_text(note)
        self.key_draw.set_content(
            _key_html(config.toolhead.color, "solid", "Stift unten")
        )
        self.area_label.set_text(
            f"{config.width_mm:.0f} × {config.height_mm:.0f} mm"
            + (
                f" ab X{config.origin_x_mm:.0f} Y{config.origin_y_mm:.0f}"
                if config.origin_x_mm or config.origin_y_mm
                else ""
            )
        )
        # Vorschau im SVG-Sinn: Ursprung oben links, ohne Flächenversatz
        self.preview.content = lines_to_svg(
            prepare_geometry(
                self.lines,
                config,
                fit=fit,
                invert_y=self.source_is_pattern,
                apply_origin=False,
                correction=correction,
            ),
            config.width_mm,
            config.height_mm,
            stroke=config.toolhead.color,
            stroke_width_mm=max(0.6, config.toolhead.width_mm),
            travel_stroke=TRAVEL_COLOR,
            style="max-height:66vh;display:block;margin:auto",
            # In Bildschirmpunkten, nicht in Millimetern: Ein halber Millimeter
            # auf zwei Metern Wand wäre hier ein Zehntel Pixel.
            screen_stroke_px=1.4,
        )

    def layer_tools(self) -> dict[str, Toolhead]:
        """Welche Farbebene mit welchem Stift gezeichnet wird."""
        return {
            label: toolhead_by_name(name)
            for label, name in getattr(self, "layer_heads", {}).items()
            if name
        }

    def refresh_layers(self) -> None:
        """Farbebenen auflisten — jede mit eigenem Stift und eigenem Knopf."""
        self.layer_box.clear()
        self.layer_heads: dict[str, str] = {}
        if len(self.layers) < 2:
            return
        ui = self.ui
        with self.layer_box:
            ui.label("Farbebenen — nacheinander plotten, Stift dazwischen wechseln").classes(
                "text-xs text-grey"
            )
            names = {key: head.name for key, head in TOOLHEADS.items()}
            for position, layer in enumerate(self.layers, start=1):
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.html(
                        f'<span style="display:inline-block;width:14px;height:14px;'
                        f'border-radius:3px;border:1px solid #bbb;background:{layer.color}"></span>'
                    )
                    ui.label(f"{position}. {layer.label}").classes("text-xs flex-grow")
                    ui.label(f"{len(layer.lines)} Linien").classes("text-xs text-grey")
                    ui.select(
                        names,
                        value=None,
                        on_change=lambda event, key=layer.label: self.assign_head(key, event.value),
                    ).props("dense outlined options-dense clearable").classes("w-32").tooltip(
                        "Stift für diese Farbe — ohne Auswahl der oben gewählte"
                    )
                    ui.button(
                        icon="send", on_click=lambda i=position - 1: self.send_layer(i)
                    ).props("flat dense").tooltip("nur diese Farbe plotten")

    def assign_head(self, label: str, name: str | None) -> None:
        self.layer_heads[label] = name or ""

    async def send_layer(self, index: int) -> None:
        if not (0 <= index < len(self.layers)):
            return
        layer = self.layers[index]
        config = self.plot_config()
        # gemeinsame Einpassung über alle Ebenen, sonst passt der Passer nicht
        try:
            programs = layers_to_gcode(
                self.layers, config, separate=True, correction=self.correction(),
                tools=self.layer_tools(),
            )
        except ToolheadError as exc:
            self.ui.notify(str(exc), type="negative", multi_line=True, timeout=10000)
            return
        program = programs.get(layer.label)
        if not program:
            self.ui.notify("Ebene ist leer", type="warning")
            return
        # Eigener Dateiname je Ebene: sonst überschreibt Rot das Schwarz auf der
        # Karte, und hinterher ist nicht mehr nachvollziehbar, was dort läuft
        name = f"ebene-{index + 1}-{layer.slug}.gcode"
        remote = await self._send(program, name, f"Ebene {layer.label}")
        if remote:
            self.ui.notify(f"Ebene {layer.label} gestartet: {remote}", type="positive")

    # -- Maschine ---------------------------------------------------------

    async def _send(self, program: str, filename: str, what: str) -> str | None:
        """Hochladen und starten — im Thread, damit die Oberfläche atmet.

        Ein GCode-Programm für eine große Wand ist schnell ein paar Megabyte.
        Liefe der Upload in der Event-Loop, stünde die Oberfläche währenddessen
        für jedes Gerät im Netz, das gerade draufschaut.
        """

        def work() -> str:
            client = self.client(30.0)
            remote = client.upload(program, filename)
            client.run_file(remote)
            return remote

        try:
            return await asyncio.to_thread(work)
        except FluidNCError as exc:
            self.ui.notify(f"{what}: {exc}", type="negative", multi_line=True)
        except Exception as exc:  # Netzwerkfehler aller Art
            self.ui.notify(f"{what} fehlgeschlagen: {exc}", type="negative", multi_line=True)
        return None

    async def send_plot(self) -> None:
        if not self.gcode:
            self.ui.notify("Erst eine Zeichnung oder ein Muster laden", type="warning")
            return
        # Eigener Name je Vorlage: sonst überschreibt jeder Plot den vorigen auf
        # der Karte — und genau die Originaldatei braucht das Fortsetzen später.
        remote = await self._send(self.gcode, self.remote_name(), "Plot")
        if remote:
            self.ui.notify(f"Plot gestartet: {remote}", type="positive")

    async def jog(self, dx: float, dy: float) -> None:
        # Ein Toggle lässt sich auch abwählen — dann steht dort None
        step = float(self.jog_step.value or DEFAULT_JOG_STEP_MM)
        feed = self.jog_feed.value or 1000
        await self.machine_command(
            "Jog", lambda: self.client(5).jog(dx * step, dy * step, feed=feed), quiet=True
        )

    async def machine_command(self, name: str, action, *, quiet: bool = False) -> bool:
        """Ein Kommando ans Board schicken, ohne die Oberfläche anzuhalten."""
        try:
            await asyncio.to_thread(action)
        except Exception as exc:
            self.ui.notify(f"{name} fehlgeschlagen: {exc}", type="negative")
            return False
        if not quiet:
            self.ui.notify(f"{name} gesendet", type="positive")
        return True

    async def record_corner(self, corner: str) -> None:
        if self.location is None:
            self.ui.notify("Erst einen Standort anlegen", type="warning")
            return
        try:
            position = await asyncio.to_thread(self.client(5).position)
        except Exception as exc:
            self.ui.notify(f"Position nicht lesbar: {exc}", type="negative")
            return
        self.calibration.record(corner, position)
        self.save_book()
        self.refresh_calibration()
        self.ui.notify(f"{corner} bei X{position[0]:.0f} Y{position[1]:.0f}", type="positive")

    async def goto_corner(self, corner: str) -> None:
        if corner not in self.calibration.points:
            self.ui.notify(f"{corner} ist noch nicht kalibriert", type="warning")
            return
        x, y = self.calibration.points[corner]
        feed = self.jog_feed.value or 1000
        await self.machine_command(
            f"Anfahrt {corner}", lambda: self.client(5).jog_to(x, y, feed)
        )

    def clear_calibration(self) -> None:
        location = self.location
        if location is None:
            return
        location.calibration = AreaCalibration()
        self.save_book()
        self.refresh_calibration()

    # -- Standorte --------------------------------------------------------

    def add_location(self) -> None:
        try:
            location = Location(
                name=self.new_name.value.strip() or "Standort",
                anchor_span_mm=self.new_span.value or 0,
                left_belt_zero_mm=self.new_left.value or 0,
                right_belt_zero_mm=self.new_right.value or 0,
            )
        except LocationError as exc:
            self.ui.notify(str(exc), type="negative", multi_line=True)
            return
        self.book.add(location)
        self.save_book()
        self.location_select.set_options(sorted(self.book.locations), value=location.name)
        self.refresh_calibration()
        self.ui.notify(f"Standort {location.name} angelegt", type="positive")

    def switch_location(self, name: str | None) -> None:
        if not name:
            return
        try:
            self.book.use(name)
        except LocationError as exc:
            self.ui.notify(str(exc), type="negative")
            return
        self.save_book()
        self.refresh_calibration()

    def refresh_calibration(self) -> None:
        if self.book_error:
            # sichtbar machen statt verschweigen: der Standort ist weg, nicht leer
            self.ui.notify(
                f"Standortdatei nicht lesbar: {self.book_error}",
                type="negative",
                multi_line=True,
                timeout=15000,
            )
            self.book_error = ""
        location = self.location
        self.location_label.set_text(
            location.report().splitlines()[2].strip() if location else "Kein Standort angelegt"
        )
        self.geometry_label.set_text(
            "\n".join(location.analysis().verdict(location.kinematics().motor))
            if location and location.calibration.complete
            else ""
        )
        self.calibration_label.set_text(self.calibration.summary())
        for corner, badge in self.corner_badges.items():
            done = corner in self.calibration.points
            badge.set_content(
                f'<span class="wp-tick{" is-set" if done else ""}">{"✓" if done else "–"}</span>'
            )
        self.use_calibration.set_enabled(self.calibration.complete)
        self.regenerate()

    def _read_status(self):
        """Statusabfrage — blockiert, gehört deshalb in einen Thread."""
        try:
            return self.client(3).status()
        except Exception:
            return None

    # -- SD-Karte ---------------------------------------------------------

    async def refresh_card(self, *, quiet: bool = False) -> None:
        """Verzeichnis der Karte holen und anzeigen.

        Das ist die Gegenprobe zum Hochladen: Wer wissen will, ob die Datei
        wirklich oben ist, sieht hier nach — und kann sie von dort auch wieder
        starten, ohne sie noch einmal zu schicken.

        ``quiet`` ist für den Blick beim Start gedacht: Ist das Board noch gar
        nicht da, ist das keine Fehlermeldung wert — die Zeile „noch nicht
        gelesen" bleibt einfach stehen.
        """

        def work() -> list[tuple[str, int]]:
            return _card_entries(self.client(10).list_files("/"))

        try:
            entries = await asyncio.to_thread(work)
        except Exception as exc:  # FluidNC-Fehler und Netzwerkfehler aller Art
            if not quiet:
                self.ui.notify(f"Karte nicht lesbar: {exc}", type="negative", multi_line=True)
            return

        self.card_list.clear()
        with self.card_list:
            if not entries:
                self.ui.label("Keine Dateien auf der Karte").classes("wp-hint")
                return
            for name, size in entries:
                with self.ui.element("div").classes("wp-file w-full"):
                    self.ui.label(name).classes("flex-grow truncate")
                    self.ui.label(_bytes(size)).classes("wp-hint shrink-0")
                    self.ui.button(
                        icon="play_arrow", on_click=lambda n=name: self.run_from_card(n)
                    ).props("flat dense round color=primary").tooltip("Von der Karte starten")

    async def run_from_card(self, name: str) -> None:
        """Eine Datei starten, die schon auf der Karte liegt."""
        path = name if name.startswith("/") else f"/{name}"
        if await self.machine_command(f"Start {path}", lambda: self.client(10).run_file(path)):
            self.ui.notify(f"{path} gestartet", type="positive")

    async def poll_status(self) -> None:
        """Alle zwei Sekunden den Maschinenstatus holen und anzeigen.

        Die HTTP-Abfrage läuft in einem Thread: NiceGUI arbeitet die Timer in
        derselben Event-Loop ab wie alles andere, und ein nicht erreichbares
        Board (der Normalfall beim Basteln) würde die Oberfläche sonst alle
        zwei Sekunden für die volle Timeout-Dauer einfrieren — für *alle*
        Geräte, die gerade draufschauen.
        """
        machine = await asyncio.to_thread(self._read_status)
        self.job_host.set_text(self.host_input.value or self.host)

        if machine is None:
            self.status_label.set_text("FluidNC nicht erreichbar")
            self.status_detail.set_text("")
            self.status_badge.classes(replace="wp-dot")
            self.progress.set_value(0)
            self.position_label.set_text("")
            self.job_state.set_text("offline")
            self.job_percent.set_text("")
            self.job_file.set_text("Board antwortet nicht")
            self.job_position.set_text("—")
            return

        head = machine.state.split(":")[0]
        kind = {"Idle": "idle", "Run": "run", "Jog": "run", "Hold": "hold"}.get(head, "alarm")
        self.status_label.set_text(machine.state)
        self.status_badge.classes(replace=f"wp-dot is-{kind}")

        percent = machine.sd_percent
        self.progress.set_value((percent or 0) / 100)
        self.job_state.set_text(machine.state)
        self.job_percent.set_text(f"{percent:.0f} %" if percent is not None else "")
        self.job_file.set_text(machine.sd_file or "kein Programm")
        self.status_detail.set_text(
            f"{percent:.0f} %" if percent is not None and machine.is_running else ""
        )

        if machine.position:
            position = f"X {machine.position[0]:.1f} · Y {machine.position[1]:.1f} mm"
            self.position_label.set_text(
                position + (f" · {machine.sd_file}" if machine.sd_file else "")
            )
            self.job_position.set_text(position)
        else:
            self.job_position.set_text("—")

    # -- Bausteine ---------------------------------------------------------

    def _card(self, title: str, hint: str = "", width: str = "w-full"):
        """Karte mit einheitlichem Kopf.

        Damit sehen alle Karten gleich aus, ohne dass an vierzig Stellen
        dieselben Klassen stehen — und eine neue Karte sieht automatisch aus
        wie die anderen, statt fast wie die anderen.
        """
        card = self.ui.column().classes(f"wp-card {width}")
        with card:
            self.ui.label(title).classes("wp-card-title")
            if hint:
                self.ui.label(hint).classes("wp-hint")
        return card

    def _rule(self) -> None:
        self.ui.element("div").classes("wp-rule w-full")

    def _metric(self, label: str, value: str = "—"):
        with self.ui.column().classes("gap-0"):
            self.ui.label(label).classes("wp-metric-label")
            return self.ui.label(value).classes("wp-metric-value")

    def build_ui(self) -> None:
        ui = self.ui
        ui.add_head_html(STYLE)

        with ui.header(elevated=False).classes("wp-header p-0"), ui.column().classes(
            "w-full gap-0"
        ):
            with ui.row().classes(
                "w-full items-center justify-between px-4 py-2 gap-x-3 gap-y-1"
            ):
                with ui.row().classes("items-center gap-3 no-wrap min-w-0"):
                    ui.label("Wandplotter").classes("wp-wordmark")
                    self.location_select = (
                        ui.select(
                            sorted(self.book.locations),
                            value=self.book.active,
                            on_change=lambda e: self.switch_location(e.value),
                        )
                        .props("dense borderless dark options-dense")
                        .classes("w-36 text-sm")
                        .tooltip("Standort — jede Aufhängung hat eigene Ankermaße")
                    )
                with ui.row().classes("items-center gap-3 no-wrap"):
                    with ui.element("div").classes("wp-status"):
                        self.status_badge = ui.element("span").classes("wp-dot is-idle")
                        self.status_label = ui.label("verbinde …").classes("text-white")
                        self.status_detail = ui.label("").classes("text-white opacity-60")
                    self.host_input = (
                        ui.input(value=self.host)
                        .props("dense borderless dark input-class=text-white")
                        .classes("w-40 text-sm")
                        .tooltip("Adresse des Boards")
                    )

            with ui.tabs().props(
                "no-caps align=left indicator-color=white inline-label"
            ).classes("wp-tabs w-full") as tabs:
                tab_plot = ui.tab("Plotten", icon="brush")
                tab_calibrate = ui.tab("Kalibrieren", icon="straighten")
                tab_machine = ui.tab("Maschine", icon="precision_manufacturing")

        with ui.tab_panels(tabs, value=tab_plot).classes(
            "w-full bg-transparent mx-auto max-w-[1500px]"
        ):
            with ui.tab_panel(tab_plot).classes("p-3 md:p-5"):
                self._plot_panel()
            with ui.tab_panel(tab_calibrate).classes("p-3 md:p-5"):
                self._calibration_panel()
            with ui.tab_panel(tab_machine).classes("p-3 md:p-5"):
                self._machine_panel()

        ui.timer(2.0, self.poll_status)
        # Einmal beim Öffnen nachsehen, was auf der Karte liegt — still, denn
        # ein noch nicht eingeschaltetes Board ist beim Start der Normalfall
        # und keine Fehlermeldung wert.
        ui.timer(1.0, lambda: self.refresh_card(quiet=True), once=True)

    def _plot_panel(self) -> None:
        ui = self.ui
        with ui.row().classes("w-full gap-4 items-start no-wrap max-lg:flex-wrap"):
            with ui.column().classes("gap-4 w-[21rem] max-lg:w-full shrink-0"):
                with self._card("Vorlage"):
                    with ui.element("div").classes("wp-drop w-full"):
                        ui.upload(
                            on_upload=self.load_upload,
                            auto_upload=True,
                            label="SVG oder Foto hierher ziehen",
                        ).props('accept=".svg,.png,.jpg,.jpeg" flat').classes("w-full")
                    ui.label("Testmuster").classes("wp-hint")
                    with ui.row().classes("wp-chip gap-2 flex-wrap"):
                        for name in PATTERNS:
                            ui.button(
                                name, on_click=lambda n=name: self.load_pattern(n)
                            ).props("flat no-caps dense")
                    # Bewusst kein Tooltip an den Knöpfen: Auf einem Touchgerät
                    # bleibt der nach dem Tippen stehen, bis man woanders
                    # hintippt. Die Erklärung gehört ohnehin hierher — man liest
                    # sie, *nachdem* man gewählt hat.
                    self.pattern_hint = ui.label(PATTERN_INTRO).classes("wp-hint")

                with self._card("Fläche"):
                    self.area_label = ui.label("—").classes("wp-readout")
                    with ui.row().classes("gap-2 w-full no-wrap"):
                        self.width = (
                            ui.number("Breite mm", value=WALL_WIDTH_MM)
                            .props("dense outlined")
                            .classes("flex-1 min-w-0")
                        )
                        self.height = (
                            ui.number("Höhe mm", value=WALL_HEIGHT_MM)
                            .props("dense outlined")
                            .classes("flex-1 min-w-0")
                        )
                    self.margin = (
                        ui.number("Rand mm", value=50).props("dense outlined").classes("w-full")
                    )
                    self.use_calibration = ui.switch(
                        "Kalibrierte Fläche verwenden", value=True
                    ).props("dense")
                    self.use_correction = (
                        ui.switch("Vorverzerrung verwenden", value=False)
                        .props("dense")
                        .tooltip(
                            f"Rechnet gegen bekannte Maschinenfehler vor, aus {CORRECTION_PATH} "
                            "— erst nach einer Messreihe sinnvoll"
                        )
                    )

                with self._card("Werkzeug"):
                    self.head_select = (
                        ui.select(
                            {key: head.name for key, head in TOOLHEADS.items()},
                            value="fineliner",
                            on_change=lambda _: self.change_toolhead(),
                        )
                        .props("dense outlined")
                        .classes("w-full")
                        .tooltip("Die Werte je Stift sind Startwerte — mit pen-test nachziehen")
                    )
                    self.head_label = ui.label("").classes("wp-readout whitespace-pre-line")
                    self.laser_armed = (
                        ui.switch("Laser scharf", value=False)
                        .props("dense color=negative")
                        .tooltip(
                            "Das Gegenstück zu --laser-verstanden: ohne diesen Schalter "
                            "entsteht kein Laser-GCode"
                        )
                    )
                    self.laser_armed.on_value_change(lambda _: self.regenerate())

                with self._card("Feineinstellung", "Servo-Werte und Bildverfahren."):
                    with ui.row().classes("gap-2 w-full no-wrap"):
                        self.draw_feed = (
                            ui.number("Vorschub mm/min", value=1500)
                            .props("dense outlined")
                            .classes("flex-1 min-w-0")
                        )
                        self.pen_dwell = (
                            ui.number("Wartezeit s", value=0.25, step=0.05)
                            .props("dense outlined")
                            .classes("flex-1 min-w-0")
                        )
                    with ui.row().classes("gap-2 w-full no-wrap"):
                        self.pen_down = (
                            ui.number("Pen unten (S)", value=30)
                            .props("dense outlined")
                            .classes("flex-1 min-w-0")
                        )
                        self.pen_up = (
                            ui.number("Pen oben (S)", value=0)
                            .props("dense outlined")
                            .classes("flex-1 min-w-0")
                        )
                    self._rule()
                    self.technique = (
                        ui.select(
                            {
                                name: f"{name} — {text.split(' — ')[0]}"
                                for name, text in TECHNIQUES.items()
                            },
                            value="spiral",
                            label="Verfahren für Fotos",
                            on_change=lambda _: self.render_upload(),
                        )
                        .props("dense outlined")
                        .classes("w-full")
                        .tooltip("tsp und spiral zeichnen ohne Stiftheben")
                    )
                    self.pitch = (
                        ui.number("Bahnabstand mm", value=25.0, step=1.0)
                        .props("dense outlined")
                        .classes("w-full")
                    )
                    self.pitch.on_value_change(lambda _: self.render_upload())

                for field in (
                    self.width,
                    self.height,
                    self.margin,
                    self.draw_feed,
                    self.pen_down,
                    self.pen_up,
                    self.pen_dwell,
                ):
                    field.on_value_change(lambda _: self.regenerate())
                self.use_calibration.on_value_change(lambda _: self.regenerate())
                self.use_correction.on_value_change(lambda _: self.regenerate())

            # Auf dem Handy zuerst die Vorschau und der Startknopf: Wer vor der
            # Wand steht, will sehen und starten — die Einstellungen hat er
            # vorher am Rechner gemacht.
            with ui.column().classes("flex-grow gap-4 min-w-0 max-lg:order-first"):
                with ui.element("div").classes("wp-stage w-full"):
                    with ui.element("div").classes("wp-stage-body"):
                        self.preview = ui.html(EMPTY_PREVIEW).classes("w-full")
                    with ui.element("div").classes("wp-stage-foot"):
                        # Die Legende führt die Farbe des gewählten Stifts, nicht
                        # eine feste: Sonst steht dort Blau, während die Vorschau
                        # schwarz zeichnet.
                        self.key_draw = ui.html(_key_html(DRAW_COLOR, "solid", "Stift unten"))
                        ui.html(_key_html(TRAVEL_COLOR, "dashed", "Leerweg"))
                        ui.element("div").classes("flex-grow")
                        self.info = ui.label("Noch nichts geladen")

                self.layer_box = ui.column().classes("gap-2 w-full")
                ui.button("Auf Wand plotten", icon="send", on_click=self.send_plot).props(
                    "color=primary unelevated size=lg no-caps"
                ).classes("w-full")

    def _calibration_panel(self) -> None:
        ui = self.ui
        # Jog-Feld zuerst: auf dem Handy stapeln die Karten, und vor der Wand
        # bewegt man die Gondel dauernd — Nullpunkt und Ecken sind Einzelschritte
        with ui.row().classes("w-full gap-4 items-start max-lg:flex-wrap"):
            with self._card(
                "Gondel bewegen", "Schrittweite wählen, dann fahren.", "w-[19rem] max-lg:w-full"
            ):
                self._jog_pad()

            with self._card("Fläche einmessen", width="w-[21rem] max-lg:w-full"):
                ui.label("1 · Nullpunkt").classes("text-sm font-semibold")
                ui.label(
                    "Gondel an den oberen Anschlag fahren, dann Nullpunkt setzen."
                ).classes("wp-hint")
                ui.button(
                    "Nullpunkt setzen (G92)",
                    icon="my_location",
                    on_click=lambda: self.machine_command("G92", self.client(5).set_zero),
                ).props("outline no-caps color=primary").classes("w-full")

                self._rule()
                ui.label("2 · Ecken anfahren und übernehmen").classes("text-sm font-semibold")
                self.corner_badges = {}
                for corner in CORNERS:
                    with ui.element("div").classes("wp-corner w-full"):
                        self.corner_badges[corner] = ui.html(
                            '<span class="wp-tick">–</span>'
                        )
                        ui.label(corner).classes("flex-grow")
                        ui.button(
                            icon="place", on_click=lambda c=corner: self.record_corner(c)
                        ).props("flat dense round").tooltip("Position übernehmen")
                        ui.button(
                            icon="near_me", on_click=lambda c=corner: self.goto_corner(c)
                        ).props("flat dense round").tooltip("Ecke anfahren")

                self._rule()
                self.calibration_label = ui.label("").classes("wp-readout whitespace-pre-line")
                with ui.row().classes("gap-2 w-full"):
                    ui.button(
                        "Rahmen plotten", icon="crop_free",
                        on_click=lambda: self.load_pattern("frame"),
                    ).props("outline no-caps dense color=primary").classes("flex-grow")
                    ui.button("Verwerfen", on_click=self.clear_calibration).props(
                        "flat no-caps dense color=negative"
                    )

            with ui.column().classes("gap-4 flex-grow min-w-[21rem] max-lg:w-full"):
                with self._card("Standort"):
                    self.location_label = ui.label("").classes("wp-readout")
                    self.geometry_label = ui.label("").classes(
                        "wp-readout whitespace-pre-line"
                    )

                with self._card(
                    "Neue Aufhängung eintragen",
                    "Gondel am Referenzpunkt, Nullpunkt gesetzt, dann drei Maße mit dem "
                    "Zollstock nehmen.",
                ):
                    self.new_name = (
                        ui.input("Name").props("dense outlined").classes("w-full")
                    )
                    self.new_span = (
                        ui.number("Abstand der Umlenkpunkte mm")
                        .props("dense outlined")
                        .classes("w-full")
                    )
                    with ui.row().classes("gap-2 w-full"):
                        self.new_left = (
                            ui.number("linker Riemen mm")
                            .props("dense outlined")
                            .classes("flex-1 min-w-[8.5rem]")
                        )
                        self.new_right = (
                            ui.number("rechter Riemen mm")
                            .props("dense outlined")
                            .classes("flex-1 min-w-[8.5rem]")
                        )
                    ui.button(
                        "Standort anlegen", icon="add_location_alt", on_click=self.add_location
                    ).props("outline no-caps color=primary").classes("w-full")

    def _jog_pad(self) -> None:
        ui = self.ui
        self.jog_step = (
            ui.toggle(JOG_STEPS, value=10)
            .props("dense no-caps spread unelevated toggle-color=primary")
            .classes("wp-steps w-full")
        )
        ui.label("Schrittweite in mm").classes("wp-hint")

        with ui.element("div").classes("wp-jog w-full my-1"):
            layout = [
                (None, ("keyboard_arrow_up", 0, 1), None),
                (("keyboard_arrow_left", -1, 0), ("close", 0, 0), ("keyboard_arrow_right", 1, 0)),
                (None, ("keyboard_arrow_down", 0, -1), None),
            ]
            for row in layout:
                for cell in row:
                    if cell is None:
                        ui.element("div")
                        continue
                    icon, dx, dy = cell
                    if (dx, dy) == (0, 0):
                        with ui.element("div").classes("wp-stop"):
                            ui.button(
                                icon=icon,
                                on_click=lambda: self.machine_command(
                                    "Jog-Stopp", self.client(5).jog_cancel
                                ),
                            ).props("flat").classes("w-full").tooltip("Bewegung abbrechen")
                    else:
                        ui.button(
                            icon=icon, on_click=lambda dx=dx, dy=dy: self.jog(dx, dy)
                        ).props("flat").classes("w-full")

        self.jog_feed = (
            ui.number("Jog-Vorschub mm/min", value=1000)
            .props("dense outlined")
            .classes("w-full")
        )
        self.position_label = ui.label("").classes("wp-readout font-mono")

    def _machine_panel(self) -> None:
        ui = self.ui
        with ui.row().classes("w-full gap-4 items-start max-lg:flex-wrap"):
            with self._card("Laufender Job", width="w-[27rem] max-lg:w-full"):
                with ui.row().classes("items-baseline gap-3 w-full no-wrap"):
                    self.job_state = ui.label("—").classes("wp-job-state")
                    ui.element("div").classes("flex-grow")
                    self.job_percent = ui.label("").classes("wp-metric-value")
                self.job_file = ui.label("kein Programm").classes("wp-job-file")
                self.progress = (
                    ui.linear_progress(value=0, show_value=False)
                    .props("rounded size=8px")
                    .classes("w-full")
                )

                with ui.row().classes("gap-6 w-full mt-1"):
                    self.job_position = self._metric("Position")
                    self.job_host = self._metric("Board", self.host)

                self._rule()
                with ui.row().classes("gap-2 w-full no-wrap"):
                    ui.button(
                        "Pause",
                        icon="pause",
                        on_click=lambda: self.machine_command("Pause", self.client(5).pause),
                    ).props("outline no-caps color=primary").classes("flex-1")
                    ui.button(
                        "Weiter",
                        icon="play_arrow",
                        on_click=lambda: self.machine_command("Weiter", self.client(5).resume),
                    ).props("outline no-caps color=primary").classes("flex-1")
                    ui.button(
                        "Stopp",
                        icon="stop",
                        on_click=lambda: self.machine_command("Stopp", self.client(5).stop),
                    ).props("outline no-caps color=negative").classes("flex-1")

            with self._card(
                "Auf der Karte",
                "Was schon oben liegt, lässt sich ohne Rechner wieder starten.",
                width="w-[24rem] max-lg:w-full",
            ):
                self.card_list = ui.column().classes("gap-0 w-full")
                with self.card_list:
                    ui.label("noch nicht gelesen").classes("wp-hint")
                ui.button("Karte lesen", icon="refresh", on_click=self.refresh_card).props(
                    "outline no-caps dense color=primary"
                ).classes("w-full")

            with self._card(
                "Was die drei Knöpfe tun", width="w-[24rem] max-lg:w-full"
            ):
                for title, text in (
                    ("Pause", "Feed Hold — die Maschine bremst kontrolliert ab und bleibt "
                              "auf der Bahn. Dasselbe passiert bei einer M0-Pause zum "
                              "Stiftwechsel."),
                    ("Weiter", "Cycle Start — fährt aus der Pause weiter, auf derselben Bahn."),
                    ("Stopp", "Soft-Reset (Ctrl-X). Bricht den SD-Job ab und kostet die "
                              "Maschinenposition: Danach steht ein Alarm, und der Nullpunkt "
                              "ist neu zu setzen."),
                ):
                    with ui.column().classes("gap-0 w-full"):
                        ui.label(title).classes("text-sm font-semibold")
                        ui.label(text).classes("wp-hint")

                self._rule()
                ui.label(
                    "Alle drei gehen über zwei Wege gleichzeitig zum Board — über den "
                    "Kanal und über den Endpunkt der Firmware. Einer allein könnte "
                    "unbemerkt verlorengehen."
                ).classes("wp-hint")


def create_app(host: str = "fluidnc.local", locations_path: str = str(LOCATIONS_PATH)):
    """UI aufbauen und das ``ui``-Modul zurückgeben (Start über :func:`main`)."""
    ui = _require_nicegui()
    # Quasars Grundfarben auf die eigenen ziehen — sonst bleiben Schalter,
    # Fortschrittsbalken und der Hauptknopf im Standardblau, und die Oberfläche
    # hat zwei Akzentfarben, von denen eine nichts bedeutet.
    ui.colors(primary=DRAW_COLOR, negative=TRAVEL_COLOR, dark="#14181c")
    app = WallplotterUI(ui, host, locations_path)
    app.build_ui()
    app.refresh_calibration()
    return ui


def main(host: str = "0.0.0.0", port: int = 8080) -> None:
    ui = create_app()
    ui.run(host=host, port=port, title="Wandplotter", reload=False, show=False)


if __name__ in {"__main__", "__mp_main__"}:  # NiceGUI startet den Prozess neu
    main()
