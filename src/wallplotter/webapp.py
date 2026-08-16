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

EMPTY_PREVIEW = (
    '<div style="display:flex;align-items:center;justify-content:center;'
    'height:100%;min-height:60vh;color:#8b8b8b;font-size:0.9rem;text-align:center">'
    "Noch nichts geladen —<br>SVG oder Foto hochladen, oder ein Testmuster wählen</div>"
)


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
            travel_stroke="#d64545",
            style="max-height:68vh;display:block;margin:auto",
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
            badge.set_text("✓" if done else "–")
            badge.classes(replace="text-positive" if done else "text-grey")
        self.use_calibration.set_enabled(self.calibration.complete)
        self.regenerate()

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
            self.position_label.set_text("")
            return
        self.status_label.set_text(machine.state)
        self.status_badge.props(
            "color=" + ("green" if machine.state == "Idle" else "orange" if machine.is_running else "red")
        )
        self.progress.set_value((machine.sd_percent or 0) / 100)
        if machine.position:
            self.position_label.set_text(
                f"X {machine.position[0]:.1f} · Y {machine.position[1]:.1f} mm"
                + (f" · {machine.sd_file}" if machine.sd_file else "")
            )

    # -- Aufbau -----------------------------------------------------------

    def build_ui(self) -> None:
        ui = self.ui
        ui.add_head_html("<style>body{background:#f6f6f7}</style>")

        with ui.header().classes("items-center justify-between px-4 py-2"):
            with ui.row().classes("items-center gap-3"):
                ui.label("Wandplotter").classes("text-xl font-medium")
                self.location_select = (
                    ui.select(
                        sorted(self.book.locations),
                        value=self.book.active,
                        on_change=lambda e: self.switch_location(e.value),
                    )
                    .props("dense outlined dark options-dense label-color=white")
                    .classes("w-40")
                    .tooltip("Standort — jede Aufhängung hat eigene Ankermaße")
                )
            with ui.row().classes("items-center gap-2"):
                self.status_badge = ui.badge("", color="grey").props("rounded")
                self.status_label = ui.label("—").classes("text-sm")
                self.host_input = (
                    ui.input(value=self.host)
                    .props("dense outlined dark input-class=text-white")
                    .classes("w-44")
                )

        with ui.tabs().classes("w-full") as tabs:
            tab_plot = ui.tab("Plotten", icon="brush")
            tab_calibrate = ui.tab("Kalibrieren", icon="straighten")
            tab_machine = ui.tab("Maschine", icon="tune")

        with ui.tab_panels(tabs, value=tab_plot).classes("w-full bg-transparent"):
            with ui.tab_panel(tab_plot):
                self._plot_panel()
            with ui.tab_panel(tab_calibrate):
                self._calibration_panel()
            with ui.tab_panel(tab_machine):
                self._machine_panel()

        ui.timer(2.0, self.poll_status)

    def _plot_panel(self) -> None:
        ui = self.ui
        with ui.row().classes("w-full gap-4 items-start no-wrap max-lg:flex-wrap"):
            with ui.column().classes("gap-3 w-80 max-lg:w-full"):
                with ui.card().classes("w-full"):
                    ui.label("Vorlage").classes("text-sm text-grey-8")
                    ui.upload(
                        on_upload=self.load_upload,
                        auto_upload=True,
                        label="SVG oder Foto hierher",
                    ).props('accept=".svg,.png,.jpg,.jpeg" flat').classes("w-full")
                    with ui.row().classes("gap-1 flex-wrap"):
                        for name in PATTERNS:
                            ui.button(
                                name, on_click=lambda n=name: self.load_pattern(n)
                            ).props("outline size=sm")

                with ui.card().classes("w-full"):
                    ui.label("Fläche").classes("text-sm text-grey-8")
                    self.area_label = ui.label("—").classes("text-xs text-grey")
                    self.width = ui.number("Breite mm", value=WALL_WIDTH_MM).props("dense outlined")
                    self.height = ui.number("Höhe mm", value=WALL_HEIGHT_MM).props("dense outlined")
                    self.margin = ui.number("Rand mm", value=50).props("dense outlined")
                    self.use_calibration = ui.switch("Kalibrierte Fläche verwenden", value=True)
                    self.use_correction = (
                        ui.switch("Vorverzerrung verwenden", value=False)
                        .tooltip(
                            f"Rechnet gegen bekannte Maschinenfehler vor, aus {CORRECTION_PATH} "
                            "— erst nach einer Messreihe sinnvoll"
                        )
                    )

                with ui.card().classes("w-full"):
                    ui.label("Werkzeug").classes("text-sm text-grey-8")
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
                    self.head_label = ui.label("").classes("text-xs text-grey whitespace-pre-line")
                    self.laser_armed = ui.switch("Laser scharf", value=False).tooltip(
                        "Das Gegenstück zu --laser-verstanden: ohne diesen Schalter "
                        "entsteht kein Laser-GCode"
                    )
                    self.laser_armed.on_value_change(lambda _: self.regenerate())

                with ui.expansion("Stift und Tempo").classes("w-full bg-white rounded"):
                    self.draw_feed = ui.number("Vorschub mm/min", value=1500).props("dense outlined")
                    self.pen_down = ui.number("Pen unten (S)", value=30).props("dense outlined")
                    self.pen_up = ui.number("Pen oben (S)", value=0).props("dense outlined")
                    self.pen_dwell = ui.number("Servo-Wartezeit s", value=0.25, step=0.05).props(
                        "dense outlined"
                    )
                    self.technique = (
                        ui.select(
                            {name: f"{name} — {text.split(' — ')[0]}" for name, text in TECHNIQUES.items()},
                            value="spiral",
                            label="Verfahren für Fotos",
                            on_change=lambda _: self.render_upload(),
                        )
                        .props("dense outlined")
                        .tooltip("tsp und spiral zeichnen ohne Stiftheben")
                    )
                    self.pitch = ui.number("Bahnabstand mm", value=25.0, step=1.0).props(
                        "dense outlined"
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

            with ui.column().classes("flex-grow gap-2 min-w-0"):
                with ui.card().classes("w-full"):
                    self.preview = ui.html(EMPTY_PREVIEW).classes("w-full")
                    with ui.row().classes("items-center gap-3 text-xs text-grey-7"):
                        ui.html('<span style="color:#1a4fd6">▬</span> Stift unten')
                        ui.html('<span style="color:#d64545">┅</span> Leerweg')
                self.layer_box = ui.column().classes("gap-1 w-full")
                self.info = ui.label("Noch nichts geladen").classes("text-sm")
                ui.button("Auf Wand plotten", icon="send", on_click=self.send_plot).classes(
                    "w-full"
                ).props("color=primary")

    def _calibration_panel(self) -> None:
        ui = self.ui
        # Jog-Pad zuerst: auf dem Handy stapeln die Karten, und vor der Wand
        # bewegt man die Gondel dauernd — Nullpunkt und Ecken sind Einzelschritte
        with ui.row().classes("w-full gap-4 items-start max-lg:flex-wrap"):
            with ui.card().classes("w-80 max-lg:w-full"):
                self._jog_pad()

            with ui.card().classes("w-80 max-lg:w-full"):
                ui.label("1. Nullpunkt").classes("text-sm text-grey-8")
                ui.label(
                    "Gondel an den oberen Anschlag fahren, dann Nullpunkt setzen."
                ).classes("text-xs text-grey")
                ui.button(
                    "Nullpunkt setzen (G92)",
                    icon="my_location",
                    on_click=lambda: self.machine_command("G92", self.client(5).set_zero),
                ).props("outline").classes("w-full")

                ui.separator()
                ui.label("2. Ecken anfahren").classes("text-sm text-grey-8")
                self.corner_badges = {}
                for corner in CORNERS:
                    with ui.row().classes("items-center gap-2 w-full no-wrap"):
                        self.corner_badges[corner] = ui.label("–").classes("w-4 text-grey")
                        ui.label(corner).classes("flex-grow text-sm")
                        ui.button(
                            icon="place", on_click=lambda c=corner: self.record_corner(c)
                        ).props("flat dense").tooltip("Position übernehmen")
                        ui.button(
                            icon="near_me", on_click=lambda c=corner: self.goto_corner(c)
                        ).props("flat dense").tooltip("Ecke anfahren")

                ui.separator()
                self.calibration_label = ui.label("").classes("text-xs whitespace-pre-line")
                with ui.row().classes("gap-2"):
                    ui.button("Rahmen plotten", on_click=lambda: self.load_pattern("frame")).props(
                        "outline size=sm"
                    )
                    ui.button("Verwerfen", on_click=self.clear_calibration).props(
                        "flat size=sm color=negative"
                    )

            with ui.card().classes("w-80 max-lg:w-full"):
                ui.label("Standort").classes("text-sm text-grey-8")
                self.location_label = ui.label("").classes("text-xs text-grey")
                self.geometry_label = ui.label("").classes(
                    "text-xs whitespace-pre-line text-grey-8"
                )
                ui.separator()
                ui.label("Neue Aufhängung eintragen").classes("text-sm text-grey-8")
                ui.label(
                    "Gondel am Referenzpunkt, Nullpunkt gesetzt, dann drei Maße "
                    "mit dem Zollstock nehmen."
                ).classes("text-xs text-grey")
                self.new_name = ui.input("Name").props("dense outlined")
                self.new_span = ui.number("Abstand der Umlenkpunkte mm").props("dense outlined")
                self.new_left = ui.number("linker Riemen mm").props("dense outlined")
                self.new_right = ui.number("rechter Riemen mm").props("dense outlined")
                ui.button("Standort anlegen", icon="add_location_alt", on_click=self.add_location).props(
                    "outline"
                ).classes("w-full")

    def _jog_pad(self) -> None:
        ui = self.ui
        ui.label("Gondel bewegen").classes("text-sm text-grey-8")
        self.jog_step = ui.toggle(JOG_STEPS, value=10).props("dense")
        ui.label("Schrittweite in mm").classes("text-xs text-grey")

        with ui.grid(columns=3).classes("gap-1 w-44 my-2"):
            layout = [
                (None, ("keyboard_arrow_up", 0, 1), None),
                (("keyboard_arrow_left", -1, 0), ("close", 0, 0), ("keyboard_arrow_right", 1, 0)),
                (None, ("keyboard_arrow_down", 0, -1), None),
            ]
            for row in layout:
                for cell in row:
                    if cell is None:
                        ui.label("")
                        continue
                    icon, dx, dy = cell
                    if (dx, dy) == (0, 0):
                        ui.button(
                            icon=icon,
                            on_click=lambda: self.machine_command(
                                "Jog-Stopp", self.client(5).jog_cancel
                            ),
                        ).props("outline color=negative").tooltip("Bewegung abbrechen")
                    else:
                        ui.button(
                            icon=icon, on_click=lambda dx=dx, dy=dy: self.jog(dx, dy)
                        ).props("outline")

        self.jog_feed = ui.number("Jog-Vorschub mm/min", value=1000).props("dense outlined")
        self.position_label = ui.label("").classes("text-xs text-grey font-mono")

    def _machine_panel(self) -> None:
        ui = self.ui
        with ui.card().classes("w-96 max-lg:w-full"):
            ui.label("Laufender Job").classes("text-sm text-grey-8")
            self.progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            with ui.row().classes("gap-2 mt-2"):
                ui.button(
                    "Pause", icon="pause", on_click=lambda: self.machine_command("Pause", self.client(5).pause)
                ).props("outline")
                ui.button(
                    "Weiter", icon="play_arrow", on_click=lambda: self.machine_command("Resume", self.client(5).resume)
                ).props("outline")
                ui.button(
                    "Stopp", icon="stop", on_click=lambda: self.machine_command("Stopp", self.client(5).stop)
                ).props("outline color=negative")
            ui.separator()
            ui.label(
                "Stopp sendet einen Soft-Reset (Ctrl-X) und bricht den SD-Job ab. "
                "Danach ist der Nullpunkt neu zu setzen."
            ).classes("text-xs text-grey")


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
