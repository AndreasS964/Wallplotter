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

#: Deutsche Namen und Reihenfolge der Ecken. ``CORNERS`` trägt die Bezeichner,
#: unter denen die Kalibrierung sie speichert — die stehen in Dateien und in
#: der CLI und bleiben englisch. Vor der Wand liest niemand „bottom-left".
CORNER_NAMES = {
    "top-left": "oben links",
    "top-right": "oben rechts",
    "bottom-left": "unten links",
    "bottom-right": "unten rechts",
}

#: Testmuster mit Symbol und Zweck — die nackten Bezeichner sagen nichts.
PATTERN_INFO = {
    "frame": ("crop_free", "Rahmen der Fläche — zeigt, ob die Kalibrierung stimmt"),
    "grid": ("grid_on", "Raster — zeigt Verzug über die ganze Fläche"),
    "circles": ("circle", "Kreise — zeigen Rundlauf und Spiel"),
    "pen-test": ("edit", "Stifttest — Andruck und Servo-Werte einstellen"),
    "feed-ramp": ("speed", "Vorschub-Rampe — findet das Tempo, bei dem es unsauber wird"),
}

EMPTY_PREVIEW = (
    '<div class="wp-empty">'
    '<svg viewBox="0 0 48 48" width="52" height="52" fill="none" stroke="currentColor" '
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="7" y="6" width="34" height="36" rx="2"/>'
    '<path d="M7 30l9-9 7 7 5-5 10 10"/><circle cx="18" cy="16" r="3"/></svg>'
    "<div><b>Noch nichts geladen</b><br>SVG oder Foto hochladen — oder unten ein "
    "Testmuster wählen</div></div>"
)

LOGO = (
    '<svg class="wp-logo" viewBox="0 0 32 26" width="30" height="24" fill="none" '
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round">'
    '<path d="M3 3h26"/><circle cx="4" cy="3" r="1.6" fill="currentColor" stroke="none"/>'
    '<circle cx="28" cy="3" r="1.6" fill="currentColor" stroke="none"/>'
    '<path d="M4 3l12 13M28 3L16 16"/>'
    '<rect x="12" y="16" width="8" height="6" rx="1.5"/>'
    '<path d="M16 22v2"/></svg>'
)

#: Farben und Abstände an einer Stelle. Quasar bringt eine eigene Palette mit;
#: die hier gesetzten Variablen sind die, an denen sich alles Eigene bedient.
THEME = """
<style>
:root {
  --wp-bg: #eceff5;
  --wp-card: #ffffff;
  --wp-ink: #16203a;
  --wp-soft: #626c82;
  --wp-line: #dde1ea;
  --wp-accent: #1a4fd6;
  --wp-shadow: 0 1px 2px rgba(22,32,58,.06), 0 4px 16px rgba(22,32,58,.06);
}
body, .q-page, .nicegui-content { background: var(--wp-bg); color: var(--wp-ink); }
.nicegui-content { padding: 0; gap: 0; }
.wp-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }

/* Kopfzeile: dunkel, damit die Reiter darunter als Blatt wirken */
.wp-header { background: linear-gradient(100deg,#1c2334 0%,#2f3d59 100%); }
.wp-logo { opacity: .9; }
.wp-title { font-weight: 600; letter-spacing: .01em; line-height: 1.1; }
.wp-place .q-field__native { color: #fff; font-weight: 500; }
.wp-place .q-icon { color: rgba(255,255,255,.7); }
.wp-state { background: rgba(255,255,255,.1); border-radius: 999px; padding: .2rem .6rem .2rem .5rem; }
.wp-state .q-badge { min-height: 9px; min-width: 9px; padding: 0; }

.wp-tabs { background: var(--wp-card); border-bottom: 1px solid var(--wp-line);
           color: var(--wp-ink); position: sticky; top: 0; z-index: 10; }
/* Am Handy müssen alle drei Reiter nebeneinander passen, sonst rutscht der
   dritte hinter einen Pfeil und wird nie gefunden */
@media (max-width: 480px) {
  .wp-tabs .q-tab { padding: 0 .5rem; font-size: .78rem; }
  .wp-tabs .q-tab__icon { font-size: 1.1rem; }
}

/* Karten: flache Kante statt Schlagschatten, eine Überschrift je Karte */
.q-card { border-radius: 12px; box-shadow: var(--wp-shadow); border: 1px solid var(--wp-line); }
.wp-cap { font-size: .72rem; font-weight: 600; letter-spacing: .07em;
          text-transform: uppercase; color: var(--wp-soft); }
.wp-hint { font-size: .75rem; color: var(--wp-soft); line-height: 1.35; }

/* Zwei Spalten am Rechner, gestapelt am Handy — und dort die Vorschau zuerst */
.wp-cols { display: grid; grid-template-columns: 21rem minmax(0,1fr);
           gap: 1rem; align-items: start; width: 100%; }
.wp-cols > .wp-side { order: 1; min-width: 0; }
.wp-cols > .wp-main { order: 2; min-width: 0; }
@media (max-width: 1023px) {
  .wp-cols { grid-template-columns: minmax(0,1fr); }
  .wp-cols > .wp-main { order: 1; }
  .wp-cols > .wp-side { order: 2; }
}

/* Vorschau als Hauptsache: heller Grund, damit das Blatt darauf liegt */
.wp-canvas { background: #f6f7fa; border-radius: 10px; padding: .75rem;
             border: 1px solid var(--wp-line); }
.wp-empty { display: flex; flex-direction: column; align-items: center; justify-content: center;
            gap: .9rem; min-height: 46vh; color: #9aa1b1; font-size: .85rem;
            text-align: center; line-height: 1.5; }
.wp-empty.wp-flat { min-height: 8rem; }

/* Ein Kommandozeilenaufruf darf nicht mitten im Schalter umbrechen —
   „--from-boa / rd" ist zum Abtippen unbrauchbar */
.wp-code { white-space: pre; overflow-x: auto; }

/* Kennzahlen als Reihe, nicht als Fließtext */
.wp-stats { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: .25rem; }
.wp-stat { text-align: center; padding: .35rem .2rem; border-radius: 8px; background: #f4f6fa; }
.wp-stat-v { font-size: 1.05rem; font-weight: 600; line-height: 1.2;
             font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.wp-stat-k { font-size: .66rem; letter-spacing: .06em; text-transform: uppercase;
             color: var(--wp-soft); }

/* Der Hauptknopf bleibt stehen, auch wenn die Seite scrollt */
.wp-act { position: sticky; bottom: .5rem; z-index: 6; }
.wp-act .q-btn { height: 3rem; font-size: 1rem; font-weight: 600;
                 box-shadow: 0 4px 14px rgba(26,79,214,.3); }

/* Schmale Ablage statt Upload-Kasten mit Fortschrittsbalken. Quasar zeigt dort
   „0.0B / 0.00%" an — eine Zahl über eine Datei, die es noch gar nicht gibt */
.wp-drop .q-uploader { width: 100%; box-shadow: none; border: 1.5px dashed #c2c8d6;
                       border-radius: 10px; background: #fafbfd; }
.wp-drop .q-uploader__header { background: transparent; color: var(--wp-soft); }
.wp-drop .q-uploader__list, .wp-drop .q-uploader__subtitle { display: none; }
.wp-drop .q-uploader__title { font-size: .85rem; font-weight: 400; }

/* Zwei gleichrangige Karten nebeneinander, solange Platz ist */
.wp-pair { display: flex; flex-wrap: wrap; gap: .75rem; align-items: start; }
.wp-pair > * { flex: 1 1 20rem; }

/* Fahrkreuz: an der Wand wird es mit dem Daumen bedient */
.wp-jog { display: grid; grid-template-columns: repeat(3, 1fr); gap: .4rem;
          width: 100%; max-width: 15rem; margin: .25rem auto; }
.wp-jog .q-btn { height: 3.4rem; border-radius: 10px; }
.wp-jog .q-icon { font-size: 1.7rem; }

/* Eckentabelle: Häkchen, Name, zwei Knöpfe — als Raster, damit nichts springt */
.wp-corner { display: grid; grid-template-columns: 1.2rem 1fr auto auto;
             align-items: center; gap: .4rem; width: 100%; max-width: 26rem; }
.wp-corner + .wp-corner { border-top: 1px solid var(--wp-line); }

.wp-verdict { font-size: .75rem; line-height: 1.45; }
.wp-verdict > div { padding-left: .9rem; text-indent: -.9rem; }

/* Maschinenreiter: der Zustand ist die Nachricht, nicht die Beschriftung */
.wp-big { font-size: 2rem; font-weight: 600; line-height: 1.1;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
</style>
"""


def _positive(value, fallback: float) -> float:
    """Zahlenfeldwert, der garantiert größer als null ist."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def _duration(seconds: float) -> str:
    """Sekunden als das, was man vor der Wand sagen würde: „2 h 05" oder „14 min".

    Unter anderthalb Minuten in Sekunden, danach in Minuten, ab einer Stunde in
    Stunden — eine Zahl wie „218 min" muss sonst jeder selbst umrechnen.
    """
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


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
        # Geometrie der letzten Vorschau, für die Fortschrittsanzeige
        self._view: tuple | None = None
        self._live_percent = -1
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
        damit eine neue TCP-Verbindung zum ESP32 — der hat davon nicht viele.
        """
        host = self.host_input.value
        key = (host, timeout)
        if self._clients.get("key") != key:
            self._clients = {"key": key, "client": FluidNCClient(
                FluidNCConfig(host=host, timeout_s=timeout)
            )}
        return self._clients["client"]

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
        self.regenerate()
        self.ui.notify(pattern.description, multi_line=True)

    def regenerate(self) -> None:
        if not self.lines:
            return
        blocked = self.laser_blocked()
        if blocked:
            self.gcode = None
            self.info.set_text(blocked)
            self.show_stats(None)
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

        stats = stats_for(machine_lines, config)
        self.show_stats(stats)

        note = self.source_name
        if stats.passes > 1:
            note += f"  ·  {stats.passes} Durchgänge"
        if correction is not None:
            note += "  ·  vorverzerrt"
        if stats.pen_s > 0.1 * stats.duration_s:
            note += f"  ·  davon {_duration(stats.pen_s)} Werkzeughübe"
        warning = resonance_warning(machine_lines, config.toolhead.feed_for(config.draw_feed))
        if warning is not None and warning.critical:
            # Genau die Zeichnungen, die man am liebsten plottet — dichte
            # Schraffuren —, treffen die Pendelfrequenz der Gondel.
            note += f"\n{warning}"
        self.info.set_text(note)
        self.area_label.set_text(
            f"{config.width_mm:.0f} × {config.height_mm:.0f} mm"
            + (
                f"  ab X{config.origin_x_mm:.0f} Y{config.origin_y_mm:.0f}"
                if config.origin_x_mm or config.origin_y_mm
                else ""
            )
        )
        self.legend_draw.content = (
            f'<span style="color:{config.toolhead.color}">&#9644;</span> '
            f"{config.toolhead.name}"
        )
        # Vorschau im SVG-Sinn: Ursprung oben links, ohne Flächenversatz
        view_lines = prepare_geometry(
            self.lines,
            config,
            fit=fit,
            invert_y=self.source_is_pattern,
            apply_origin=False,
            correction=correction,
        )
        self._view = (view_lines, config)
        self._live_percent = -1
        self.preview.content = lines_to_svg(
            view_lines,
            config.width_mm,
            config.height_mm,
            stroke=config.toolhead.color,
            stroke_width_mm=config.toolhead.width_mm,
            travel_stroke="#d64545" if self.show_travels.value else None,
            style="max-height:56vh;display:block;margin:auto",
        )

    def show_stats(self, stats) -> None:
        """Kennzahlen in die Zahlenreihe schreiben — oder sie leeren."""
        if stats is None:
            for label in self.stat_values.values():
                label.set_text("–")
            return
        self.stat_values["lines"].set_text(f"{stats.line_count}")
        self.stat_values["draw"].set_text(f"{stats.draw_mm / 1000:.1f} m")
        self.stat_values["travel"].set_text(f"{stats.travel_mm / 1000:.1f} m")
        self.stat_values["time"].set_text(_duration(stats.duration_s))

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
        self.show_verdict(
            location.analysis().verdict(location.kinematics().motor)
            if location and location.calibration.complete
            else []
        )
        self.calibration_label.set_text(self.calibration.summary())
        done_count = 0
        for corner, badge in self.corner_badges.items():
            done = corner in self.calibration.points
            done_count += done
            badge.set_text("✓" if done else "○")
            badge.classes(replace="wp-mono text-positive" if done else "wp-mono text-grey-5")
        self.corner_progress.set_text(f"{done_count} von {len(CORNERS)} Ecken angefahren")
        self.use_calibration.set_enabled(self.calibration.complete)
        self.regenerate()

    def show_verdict(self, notes: list[str]) -> None:
        """Die Bewertung der Aufhängung als Liste statt als Textwand.

        Jeder Satz beginnt mit einem Stichwort vor dem Doppelpunkt; das wird
        fett gesetzt, damit die drei Befunde beim Überfliegen auseinanderfallen.
        """
        self.geometry_box.clear()
        if not notes:
            return
        with self.geometry_box:
            for note in notes:
                head, sep, rest = note.partition(": ")
                body = (
                    f"<b>{head}</b>{sep}{rest}" if sep and len(head) < 40 else note
                )
                self.ui.html(f"<div>· {body}</div>").classes("wp-verdict")

    def _read_status(self):
        """Statusabfrage — blockiert, gehört deshalb in einen Thread."""
        try:
            return self.client(3).status()
        except Exception:
            return None

    async def poll_status(self) -> None:
        """Alle zwei Sekunden den Maschinenstatus holen und anzeigen.

        Die HTTP-Abfrage läuft in einem Thread: NiceGUI arbeitet die Timer in
        derselben Event-Loop ab wie alles andere, und ein nicht erreichbares
        Board (der Normalfall beim Basteln) würde die Oberfläche sonst alle
        zwei Sekunden für die volle Timeout-Dauer einfrieren — für *alle*
        Geräte, die gerade draufschauen.
        """
        machine = await asyncio.to_thread(self._read_status)
        if machine is None:
            self.status_label.set_text("FluidNC nicht erreichbar")
            self.status_badge.props("color=grey")
            self.progress.set_value(0)
            self.state_big.set_text("offline")
            self.percent_big.set_text("")
            self.job_label.set_text("Keine Verbindung zum Board")
            for label in (self.position_label, self.position_hint):
                label.set_text("")
            return
        self.status_label.set_text(machine.state)
        self.status_badge.props(
            "color="
            + ("green" if machine.state == "Idle" else "orange" if machine.is_running else "red")
        )
        self.progress.set_value((machine.sd_percent or 0) / 100)
        self.state_big.set_text(machine.state)
        percent = f"{machine.sd_percent:.0f} %" if machine.sd_percent is not None else "—"
        self.percent_big.set_text(percent if machine.sd_percent is not None else "")
        self.job_label.set_text(
            f"{machine.sd_file} · {percent}" if machine.sd_file else "Kein Job auf der Karte"
        )
        if machine.position:
            place = f"X {machine.position[0]:.1f}  Y {machine.position[1]:.1f} mm"
            self.position_label.set_text(place)
            self.position_hint.set_text(place)
        self.show_progress(machine.sd_percent)

    def show_progress(self, percent: float | None) -> None:
        """Die Vorschau mit abgegrautem Anteil nachziehen — nur bei neuem Prozent.

        Ein großes SVG alle zwei Sekunden neu zu bauen und über die Leitung zu
        schieben, wäre für eine Zahl mit einer Nachkommastelle zu teuer; ein
        ganzes Prozent ist die feinste Stufe, die man an der Wand ohnehin sieht.
        """
        if self._view is None or percent is None:
            return
        step = int(percent)
        if step == self._live_percent:
            return
        self._live_percent = step
        lines, config = self._view
        self.live_preview.content = lines_to_svg(
            lines,
            config.width_mm,
            config.height_mm,
            stroke=config.toolhead.color,
            stroke_width_mm=config.toolhead.width_mm,
            style="max-height:46vh;display:block;margin:auto",
            done_fraction=step / 100,
        )

    # -- Aufbau -----------------------------------------------------------

    def build_ui(self) -> None:
        ui = self.ui
        ui.add_head_html(THEME)

        with ui.header().classes("wp-header items-center justify-between px-3 py-2 gap-2"):
            with ui.row().classes("items-center gap-3 no-wrap min-w-0"):
                ui.html(LOGO)
                with ui.column().classes("gap-0 min-w-0"):
                    ui.label("Wandplotter").classes("wp-title text-base")
                    self.location_select = (
                        ui.select(
                            sorted(self.book.locations),
                            value=self.book.active,
                            on_change=lambda e: self.switch_location(e.value),
                        )
                        .props("dense borderless dark options-dense")
                        .classes("wp-place text-xs -mt-1")
                        .tooltip("Standort — jede Aufhängung hat eigene Ankermaße")
                    )
            with ui.row().classes("items-center gap-1 no-wrap"):
                with ui.row().classes("wp-state items-center gap-2 no-wrap"):
                    self.status_badge = ui.badge("", color="grey").props("rounded")
                    self.status_label = ui.label("—").classes("text-xs font-medium")
                with (
                    ui.button(icon="lan")
                    .props("flat dense round color=white")
                    .tooltip("Verbindung zum Board")
                ):
                    with ui.menu().classes("p-3"), ui.column().classes("gap-2 w-64"):
                        ui.label("FluidNC").classes("wp-cap")
                        self.host_input = (
                            ui.input("Hostname oder IP", value=self.host)
                            .props("dense outlined")
                            .classes("w-full")
                        )
                        self.position_hint = ui.label("").classes("wp-mono text-xs text-grey-7")

        # Reiter direkt unter der Kopfzeile und beim Scrollen stehenbleibend:
        # drei Situationen, nicht ein Menü — am Handy ohne Scrollen erreichbar
        with ui.tabs().props("align=left dense inline-label").classes("wp-tabs w-full") as tabs:
            tab_plot = ui.tab("Plotten", icon="brush")
            tab_calibrate = ui.tab("Kalibrieren", icon="straighten")
            tab_machine = ui.tab("Maschine", icon="precision_manufacturing")

        with ui.tab_panels(tabs, value=tab_plot).classes("w-full bg-transparent"):
            with ui.tab_panel(tab_plot).classes("p-3"):
                self._plot_panel()
            with ui.tab_panel(tab_calibrate).classes("p-3"):
                self._calibration_panel()
            with ui.tab_panel(tab_machine).classes("p-3"):
                self._machine_panel()

        ui.timer(2.0, self.poll_status)

    # Die Reiter sind absichtlich kleinteilig aufgeteilt: jeder Abschnitt ist
    # eine Karte mit einer Überschrift, und jede Karte hat eine Methode.

    def _plot_panel(self) -> None:
        ui = self.ui
        with ui.element("div").classes("wp-cols"):
            with ui.element("div").classes("wp-main flex flex-col gap-3"):
                self._preview_card()
                self.layer_box = ui.column().classes("gap-1 w-full")
                with ui.element("div").classes("wp-act"):
                    ui.button("Auf Wand plotten", icon="send", on_click=self.send_plot).props(
                        "color=primary unelevated"
                    ).classes("w-full")
            with ui.element("div").classes("wp-side flex flex-col gap-3"):
                self._source_card()
                self._area_card()
                self._tool_card()
                self._detail_card()

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
        for switch in (self.use_calibration, self.use_correction, self.show_travels):
            switch.on_value_change(lambda _: self.regenerate())

    def _preview_card(self) -> None:
        """Die Vorschau ist die Hauptsache — Blatt, Maße und Kennzahlen."""
        ui = self.ui
        with ui.card().classes("w-full gap-2 p-3"):
            with ui.element("div").classes("wp-canvas w-full"):
                self.preview = ui.html(EMPTY_PREVIEW).classes("w-full")
            with ui.row().classes("items-center justify-between w-full gap-2"):
                with ui.row().classes("items-center gap-3 text-xs text-grey-7 no-wrap"):
                    self.legend_draw = ui.html(
                        '<span style="color:#1a4fd6">&#9644;</span> Stift unten'
                    )
                    ui.html('<span style="color:#d64545">&#9476;</span> Leerweg')
                self.show_travels = (
                    ui.switch("Leerwege", value=True)
                    .props("dense size=xs")
                    .classes("text-xs")
                    .tooltip("Anfahrten einblenden — sie kosten Zeit, aber keine Farbe")
                )
            with ui.element("div").classes("wp-stats w-full"):
                self.stat_values = {}
                for key, caption in (
                    ("lines", "Linien"),
                    ("draw", "Zeichnen"),
                    ("travel", "Leerweg"),
                    ("time", "Dauer"),
                ):
                    with ui.element("div").classes("wp-stat"):
                        self.stat_values[key] = ui.label("–").classes("wp-stat-v")
                        ui.label(caption).classes("wp-stat-k")
            self.info = ui.label("Noch nichts geladen").classes(
                "wp-hint w-full whitespace-pre-line"
            )

    def _source_card(self) -> None:
        ui = self.ui
        with ui.card().classes("w-full gap-2 p-3"):
            ui.label("Vorlage").classes("wp-cap")
            with ui.element("div").classes("wp-drop w-full"):
                ui.upload(
                    on_upload=self.load_upload,
                    auto_upload=True,
                    label="SVG oder Foto ablegen",
                ).props('accept=".svg,.png,.jpg,.jpeg" flat').classes("w-full")
            ui.label("Testmuster").classes("wp-cap pt-1")
            with ui.row().classes("gap-1 w-full"):
                for name in PATTERNS:
                    icon, purpose = PATTERN_INFO.get(name, ("category", name))
                    ui.button(
                        name, icon=icon, on_click=lambda n=name: self.load_pattern(n)
                    ).props("outline size=sm no-caps").tooltip(purpose)

    def _area_card(self) -> None:
        ui = self.ui
        with ui.card().classes("w-full gap-2 p-3"):
            with ui.row().classes("items-baseline justify-between w-full no-wrap"):
                ui.label("Fläche").classes("wp-cap")
                self.area_label = ui.label("—").classes("wp-mono text-xs text-grey-7")
            # Breite und Höhe gehören nebeneinander; zu dritt wird jedes Feld so
            # schmal, dass „2000" hinter der Einheit abgeschnitten wird
            with ui.row().classes("gap-2 w-full no-wrap"):
                self.width = (
                    ui.number("Breite", value=WALL_WIDTH_MM)
                    .props("dense outlined suffix=mm")
                    .classes("flex-1 min-w-0")
                )
                self.height = (
                    ui.number("Höhe", value=WALL_HEIGHT_MM)
                    .props("dense outlined suffix=mm")
                    .classes("flex-1 min-w-0")
                )
            self.margin = (
                ui.number("Rand", value=50)
                .props("dense outlined suffix=mm")
                .classes("w-full")
                .tooltip("bleibt ringsum frei — die Zeichnung wird kleiner eingepasst")
            )
            self.use_calibration = (
                ui.switch("Kalibrierte Fläche", value=True)
                .props("dense")
                .classes("text-sm")
                .tooltip("Die vier angefahrenen Ecken statt der Maße oben")
            )
            self.use_correction = (
                ui.switch("Vorverzerrung", value=False)
                .props("dense")
                .classes("text-sm")
                .tooltip(
                    f"Rechnet gegen bekannte Maschinenfehler vor, aus {CORRECTION_PATH} "
                    "— erst nach einer Messreihe sinnvoll"
                )
            )

    def _tool_card(self) -> None:
        ui = self.ui
        with ui.card().classes("w-full gap-2 p-3"):
            ui.label("Werkzeug").classes("wp-cap")
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
            self.head_label = ui.label("").classes("wp-hint whitespace-pre-line")
            self.laser_armed = (
                ui.switch("Laser scharf", value=False)
                .props("dense color=negative")
                .classes("text-sm")
                .tooltip(
                    "Das Gegenstück zu --laser-verstanden: ohne diesen Schalter "
                    "entsteht kein Laser-GCode"
                )
            )
            self.laser_armed.on_value_change(lambda _: self.regenerate())

    def _detail_card(self) -> None:
        """Was man einmal einstellt und dann in Ruhe lässt — zugeklappt."""
        ui = self.ui
        with ui.expansion("Feineinstellung", icon="tune").props("dense").classes(
            "w-full bg-white rounded-xl border"
        ), ui.column().classes("gap-2 w-full p-1"):
            self.draw_feed = ui.number("Vorschub", value=1500).props(
                'dense outlined suffix="mm/min"'
            ).classes("w-full")
            with ui.row().classes("gap-2 w-full no-wrap"):
                self.pen_down = (
                    ui.number("Stift unten (S)", value=30)
                    .props("dense outlined")
                    .classes("flex-1 min-w-0")
                )
                self.pen_up = (
                    ui.number("Stift oben (S)", value=0)
                    .props("dense outlined")
                    .classes("flex-1 min-w-0")
                )
            self.pen_dwell = (
                ui.number("Servo-Wartezeit", value=0.25, step=0.05)
                .props("dense outlined suffix=s")
                .classes("w-full")
            )
            ui.label("Fotos").classes("wp-cap pt-1")
            self.technique = (
                ui.select(
                    {name: text.split(" — ")[0] for name, text in TECHNIQUES.items()},
                    value="spiral",
                    label="Verfahren",
                    on_change=lambda _: self.render_upload(),
                )
                .props("dense outlined")
                .classes("w-full")
                .tooltip("tsp und spiral zeichnen ohne Stiftheben")
            )
            self.pitch = (
                ui.number("Bahnabstand", value=25.0, step=1.0)
                .props("dense outlined suffix=mm")
                .classes("w-full")
            )
            self.pitch.on_value_change(lambda _: self.render_upload())

    def _calibration_panel(self) -> None:
        ui = self.ui
        # Fahrkreuz zuerst: am Handy stapeln die Karten, und vor der Wand
        # bewegt man die Gondel dauernd — Nullpunkt und Ecken sind Einzelschritte
        with ui.element("div").classes("wp-cols"):
            with (
                ui.element("div").classes("wp-main flex flex-col gap-3"),
                ui.element("div").classes("wp-pair w-full"),
            ):
                with ui.card().classes("gap-2 p-3"):
                    self._jog_pad()
                with ui.card().classes("gap-2 p-3"):
                    self._corner_card()
            with ui.element("div").classes("wp-side flex flex-col gap-3"):
                self._location_card()

    def _corner_card(self) -> None:
        ui = self.ui
        with ui.row().classes("items-baseline justify-between w-full no-wrap"):
            ui.label("Fläche einmessen").classes("wp-cap")
            self.corner_progress = ui.label("").classes("text-xs text-grey-7")
        ui.label(
            "Erst den Nullpunkt setzen, dann jede Ecke anfahren und übernehmen."
        ).classes("wp-hint")
        ui.button(
            "Nullpunkt setzen (G92)",
            icon="my_location",
            on_click=lambda: self.machine_command("G92", self.client(5).set_zero),
        ).props("outline no-caps").classes("w-full")

        self.corner_badges = {}
        with ui.element("div").classes("w-full pt-1"):
            for corner in CORNERS:
                with ui.element("div").classes("wp-corner py-1"):
                    self.corner_badges[corner] = ui.label("○").classes("wp-mono text-grey-5")
                    ui.label(CORNER_NAMES.get(corner, corner)).classes("text-sm")
                    ui.button(
                        "übernehmen",
                        icon="place",
                        on_click=lambda c=corner: self.record_corner(c),
                    ).props("flat dense size=sm no-caps").tooltip(
                        "aktuelle Position als diese Ecke merken"
                    )
                    ui.button(
                        icon="near_me", on_click=lambda c=corner: self.goto_corner(c)
                    ).props("flat dense").tooltip("Ecke anfahren")

        ui.separator()
        self.calibration_label = ui.label("").classes("wp-hint whitespace-pre-line")
        with ui.row().classes("gap-2 w-full"):
            ui.button(
                "Rahmen plotten", icon="crop_free", on_click=lambda: self.load_pattern("frame")
            ).props("outline size=sm no-caps").tooltip("Probe aufs Exempel: zeichnet die Fläche")
            ui.space()
            ui.button("Verwerfen", icon="delete_outline", on_click=self.clear_calibration).props(
                "flat size=sm no-caps color=negative"
            )

    def _location_card(self) -> None:
        ui = self.ui
        with ui.card().classes("w-full gap-2 p-3"):
            ui.label("Standort").classes("wp-cap")
            self.location_label = ui.label("").classes("wp-hint")
            self.geometry_box = ui.column().classes("gap-1 w-full pt-1")
        with ui.card().classes("w-full gap-2 p-3"):
            ui.label("Neue Aufhängung").classes("wp-cap")
            ui.label(
                "Gondel am Referenzpunkt, Nullpunkt gesetzt, dann drei Maße "
                "mit dem Zollstock nehmen."
            ).classes("wp-hint")
            self.new_name = ui.input("Name").props("dense outlined").classes("w-full")
            self.new_span = (
                ui.number("Abstand der Umlenkpunkte")
                .props("dense outlined suffix=mm")
                .classes("w-full")
                .tooltip("waagerecht von Rolle zu Rolle gemessen")
            )
            with ui.row().classes("gap-2 w-full no-wrap"):
                self.new_left = (
                    ui.number("linker Riemen")
                    .props("dense outlined suffix=mm")
                    .classes("flex-1 min-w-0")
                )
                self.new_right = (
                    ui.number("rechter Riemen")
                    .props("dense outlined suffix=mm")
                    .classes("flex-1 min-w-0")
                )
            ui.button(
                "Standort anlegen", icon="add_location_alt", on_click=self.add_location
            ).props("outline no-caps").classes("w-full")

    def _jog_pad(self) -> None:
        ui = self.ui
        with ui.row().classes("items-baseline justify-between w-full no-wrap"):
            ui.label("Gondel bewegen").classes("wp-cap")
            self.position_label = ui.label("").classes("wp-mono text-xs text-grey-7")
        with ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.label("Schritt").classes("wp-hint")
            self.jog_step = ui.toggle(JOG_STEPS, value=DEFAULT_JOG_STEP_MM).props("dense no-caps")
            ui.label("mm").classes("wp-hint")

        with ui.element("div").classes("wp-jog my-1"):
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
                        ui.button(
                            icon=icon,
                            on_click=lambda: self.machine_command(
                                "Jog-Stopp", self.client(5).jog_cancel
                            ),
                        ).props("unelevated color=negative").tooltip("Bewegung abbrechen")
                    else:
                        ui.button(
                            icon=icon, on_click=lambda dx=dx, dy=dy: self.jog(dx, dy)
                        ).props("outline")

        self.jog_feed = (
            ui.number("Jog-Vorschub", value=1000)
            .props('dense outlined suffix="mm/min"')
            .classes("w-full max-w-60")
        )

    def _machine_panel(self) -> None:
        ui = self.ui
        with ui.element("div").classes("wp-cols"):
            with ui.element("div").classes("wp-main flex flex-col gap-3"):
                self._job_card()
                self._live_card()
            with ui.element("div").classes("wp-side flex flex-col gap-3"):
                with ui.card().classes("w-full gap-2 p-3"):
                    ui.label("Verbindung").classes("wp-cap")
                    ui.label(
                        "Der Status kommt alle zwei Sekunden vom Board. Steht dort "
                        "„nicht erreichbar“, stimmt der Hostname oben rechts nicht "
                        "oder das Board hängt nicht im selben Netz."
                    ).classes("wp-hint")
                with ui.card().classes("w-full gap-2 p-3"):
                    ui.label("Abgebrochen?").classes("wp-cap")
                    ui.label(
                        "Solange das Board den Job noch kennt, schneidet die "
                        "Kommandozeile das Restprogramm heraus — angesetzt wird am "
                        "Anfang des angefangenen Strichs:"
                    ).classes("wp-hint")
                    ui.label(
                        "wallplotter-resume plot.gcode --from-board --host <ip> --upload --run"
                    ).classes("wp-mono wp-code text-xs bg-grey-2 rounded p-2 w-full")

    def _job_card(self) -> None:
        ui = self.ui
        with ui.card().classes("w-full gap-2 p-3"):
            with ui.row().classes("items-baseline justify-between w-full no-wrap"):
                ui.label("Laufender Job").classes("wp-cap")
                self.job_label = ui.label("").classes("wp-mono text-xs text-grey-7")
            with ui.row().classes("items-end justify-between w-full no-wrap gap-2"):
                self.state_big = ui.label("—").classes("wp-big")
                self.percent_big = ui.label("").classes("wp-mono text-lg text-grey-7")
            self.progress = (
                ui.linear_progress(value=0, show_value=False)
                .props("size=14px rounded")
                .classes("w-full")
            )
            with ui.row().classes("gap-2 w-full pt-1"):
                ui.button(
                    "Pause",
                    icon="pause",
                    on_click=lambda: self.machine_command("Pause", self.client(5).pause),
                ).props("outline no-caps").classes("flex-1")
                ui.button(
                    "Weiter",
                    icon="play_arrow",
                    on_click=lambda: self.machine_command("Resume", self.client(5).resume),
                ).props("outline no-caps").classes("flex-1")
                ui.button(
                    "Stopp",
                    icon="stop",
                    on_click=lambda: self.machine_command("Stopp", self.client(5).stop),
                ).props("unelevated color=negative no-caps").classes("flex-1")
            ui.label(
                "Stopp sendet einen Soft-Reset (Ctrl-X) und bricht den SD-Job ab. "
                "Danach ist der Nullpunkt neu zu setzen."
            ).classes("wp-hint")

    def _live_card(self) -> None:
        """Dieselbe Vorschau, aber mit abgegrautem Anteil — Fortschritt zum Ansehen.

        Ein Balken sagt „43 %"; er sagt nicht, ob die Maschine gerade im Gesicht
        des Porträts steht oder noch im Hintergrund. Die Prozentzahl vom Board
        zählt gelesene Bytes, nicht Millimeter — die Grenze im Bild ist deshalb
        ungefähr, und genau als Ungefähres ist sie gemeint.
        """
        ui = self.ui
        with ui.card().classes("w-full gap-2 p-3"):
            ui.label("Fortschritt an der Wand").classes("wp-cap")
            with ui.element("div").classes("wp-canvas w-full"):
                self.live_preview = ui.html(
                    '<div class="wp-empty wp-flat">Sobald ein Plot läuft, steht hier, '
                    "wie weit er ist</div>"
                ).classes("w-full")


def create_app(host: str = "fluidnc.local", locations_path: str = str(LOCATIONS_PATH)):
    """UI aufbauen und das ``ui``-Modul zurückgeben (Start über :func:`main`)."""
    ui = _require_nicegui()
    app = WallplotterUI(ui, host, locations_path)
    app.build_ui()
    app.refresh_calibration()
    return ui


def main(host: str = "0.0.0.0", port: int = 8080) -> None:
    ui = create_app()
    ui.run(host=host, port=port, title="Wandplotter", reload=False, show=False)


if __name__ in {"__main__", "__mp_main__"}:  # NiceGUI startet den Prozess neu
    main()
