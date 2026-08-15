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

import os
from dataclasses import replace

from .calibration import CORNERS, AreaCalibration
from .config import WALL_HEIGHT_MM, WALL_WIDTH_MM, FluidNCConfig, PenConfig, PlotConfig
from .gcode import layers_to_gcode, lines_to_gcode, prepare_geometry, stats_for
from .imaging import TECHNIQUES, ImagingError
from .imaging import image_to_lines as image_lines
from .location import DEFAULT_PATH as LOCATIONS_PATH
from .location import Location, LocationBook, LocationError
from .patterns import PATTERNS, build
from .pipeline import VpypeNotAvailable, lines_to_svg, svg_to_layers
from .upload import FluidNCClient, FluidNCError

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
JOG_STEPS = [1, 10, 50, 100]

EMPTY_PREVIEW = (
    '<div style="display:flex;align-items:center;justify-content:center;'
    'height:100%;min-height:60vh;color:#8b8b8b;font-size:0.9rem;text-align:center">'
    "Noch nichts geladen —<br>SVG oder Foto hochladen, oder ein Testmuster wählen</div>"
)


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
        self.book = LocationBook.load(locations_path)

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

    def plot_config(self) -> PlotConfig:
        config = PlotConfig(
            width_mm=self.width.value or WALL_WIDTH_MM,
            height_mm=self.height.value or WALL_HEIGHT_MM,
            margin_mm=self.margin.value or 0,
            draw_feed=self.draw_feed.value or 1500,
            pen=PenConfig(
                down_value=int(self.pen_down.value or 0),
                up_value=int(self.pen_up.value or 0),
                dwell_s=self.pen_dwell.value or 0,
            ),
        )
        if self.calibration.complete and self.use_calibration.value:
            config = self.calibration.to_plot_config(config)
        # Muster sind in Maschinenkoordinaten geschrieben, SVGs nicht
        return replace(config, invert_y=not self.source_is_pattern)

    def client(self, timeout: float = 10.0) -> FluidNCClient:
        return FluidNCClient(FluidNCConfig(host=self.host_input.value, timeout_s=timeout))

    # -- Zeichnung laden --------------------------------------------------

    def load_upload(self, event) -> None:
        self.upload_data = event.content.read()
        self.upload_name = event.name
        self.render_upload()

    def render_upload(self) -> None:
        """Hochgeladene Vorlage (neu) übersetzen — auch beim Verfahrenswechsel."""
        if not getattr(self, "upload_data", None):
            return
        suffix = os.path.splitext(self.upload_name)[1].lower()
        config = self.plot_config()
        try:
            if suffix in IMAGE_SUFFIXES:
                options = {}
                if self.technique.value in ("hatch", "spiral"):
                    options["pitch_mm"] = self.pitch.value
                self.lines = image_lines(
                    self.upload_data,
                    config.width_mm,
                    config.height_mm,
                    self.technique.value,
                    margin_mm=config.margin_mm,
                    image_suffix=suffix,
                    **options,
                )
                # Bildverfahren rechnen selbst in Millimetern
                self.source_is_pattern = False
                self.fit_source = False
                name = f"{self.upload_name} ({self.technique.value})"
            else:
                self.layers = svg_to_layers(self.upload_data)
                self.lines = [line for layer in self.layers for line in layer.lines]
                self.fit_source = True
                self.source_is_pattern = False
                name = self.upload_name
                if len(self.layers) > 1:
                    name += f" ({len(self.layers)} Farben)"
        except (VpypeNotAvailable, ImagingError) as exc:
            self.ui.notify(str(exc), type="negative", multi_line=True)
            return
        if suffix in IMAGE_SUFFIXES:
            self.layers = []
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
        config = self.plot_config()
        fit = getattr(self, "fit_source", True) and not self.source_is_pattern
        self.gcode = lines_to_gcode(
            self.lines, config, fit=fit, header_comment=self.source_name, feeds=self.feeds
        )
        self.info.set_text(
            f"{self.source_name}: {stats_for(prepare_geometry(self.lines, config, fit=fit), config)}"
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
                self.lines, config, fit=fit, invert_y=self.source_is_pattern, apply_origin=False
            ),
            config.width_mm,
            config.height_mm,
            travel_stroke="#d64545",
            style="max-height:68vh;display:block;margin:auto",
        )

    def refresh_layers(self) -> None:
        """Farbebenen auflisten — jede lässt sich einzeln auf die Wand schicken."""
        self.layer_box.clear()
        if len(self.layers) < 2:
            return
        ui = self.ui
        with self.layer_box:
            ui.label("Farbebenen — nacheinander plotten, Stift dazwischen wechseln").classes(
                "text-xs text-grey"
            )
            for position, layer in enumerate(self.layers, start=1):
                with ui.row().classes("items-center gap-2 w-full no-wrap"):
                    ui.html(
                        f'<span style="display:inline-block;width:14px;height:14px;'
                        f'border-radius:3px;border:1px solid #bbb;background:{layer.color}"></span>'
                    )
                    ui.label(f"{position}. {layer.label}").classes("text-xs flex-grow")
                    ui.label(f"{len(layer.lines)} Linien").classes("text-xs text-grey")
                    ui.button(
                        icon="send", on_click=lambda i=position - 1: self.send_layer(i)
                    ).props("flat dense").tooltip("nur diese Farbe plotten")

    def send_layer(self, index: int) -> None:
        if not (0 <= index < len(self.layers)):
            return
        layer = self.layers[index]
        config = self.plot_config()
        # gemeinsame Einpassung über alle Ebenen, sonst passt der Passer nicht
        programs = layers_to_gcode(self.layers, config, separate=True)
        program = programs.get(layer.label)
        if not program:
            self.ui.notify("Ebene ist leer", type="warning")
            return
        try:
            remote = self.client().upload(program, "plot.gcode")
            self.client().run_file(remote)
        except FluidNCError as exc:
            self.ui.notify(str(exc), type="negative", multi_line=True)
            return
        self.ui.notify(f"Ebene {layer.label} gestartet", type="positive")

    # -- Maschine ---------------------------------------------------------

    def send_plot(self) -> None:
        if not self.gcode:
            self.ui.notify("Erst eine Zeichnung oder ein Muster laden", type="warning")
            return
        try:
            remote = self.client().upload(self.gcode, "plot.gcode")
            self.client().run_file(remote)
        except FluidNCError as exc:
            self.ui.notify(str(exc), type="negative", multi_line=True)
            return
        self.ui.notify(f"Plot gestartet: {remote}", type="positive")

    def jog(self, dx: float, dy: float) -> None:
        step = float(self.jog_step.value)
        try:
            self.client(5).jog(dx * step, dy * step, feed=self.jog_feed.value or 1000)
        except (FluidNCError, Exception) as exc:  # noqa: B014 - Netzwerkfehler aller Art
            self.ui.notify(f"Jog fehlgeschlagen: {exc}", type="negative")

    def machine_command(self, name: str, action) -> None:
        try:
            action()
        except Exception as exc:
            self.ui.notify(f"{name} fehlgeschlagen: {exc}", type="negative")
            return
        self.ui.notify(f"{name} gesendet", type="positive")

    def record_corner(self, corner: str) -> None:
        try:
            position = self.client(5).position()
        except Exception as exc:
            self.ui.notify(f"Position nicht lesbar: {exc}", type="negative")
            return
        if self.location is None:
            self.ui.notify("Erst einen Standort anlegen", type="warning")
            return
        self.calibration.record(corner, position)
        self.save_book()
        self.refresh_calibration()
        self.ui.notify(f"{corner} bei X{position[0]:.0f} Y{position[1]:.0f}", type="positive")

    def goto_corner(self, corner: str) -> None:
        if corner not in self.calibration.points:
            self.ui.notify(f"{corner} ist noch nicht kalibriert", type="warning")
            return
        x, y = self.calibration.points[corner]
        self.machine_command(
            f"Anfahrt {corner}", lambda: self.client(5).jog_to(x, y, self.jog_feed.value or 1000)
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

    def poll_status(self) -> None:
        try:
            machine = self.client(3).status()
        except Exception:
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
