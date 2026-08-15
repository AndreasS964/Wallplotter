"""Lokale Web-UI auf NiceGUI-Basis (Stufen 3, 4 und 6 der Roadmap).

Start:

    python -m wallplotter.webapp

Danach von jedem Gerät im Heimnetz erreichbar unter ``http://<pc-ip>:8080`` —
auch vom Handy an der Kletterwand.
"""

from __future__ import annotations

import os

from .config import WALL_HEIGHT_MM, WALL_WIDTH_MM, FluidNCConfig, PenConfig, PlotConfig
from .gcode import lines_to_gcode, prepare_geometry, stats_for
from .pipeline import VpypeNotAvailable, image_to_lines, lines_to_svg, svg_to_lines
from .upload import FluidNCClient, FluidNCError

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def _require_nicegui():
    try:
        from nicegui import ui  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "NiceGUI ist nicht installiert — `pip install -e .[web]`"
        ) from exc
    return ui


def create_app(host: str = "fluidnc.local"):
    """UI aufbauen und das ``ui``-Modul zurückgeben (Start über :func:`main`)."""
    ui = _require_nicegui()
    state: dict = {"lines": [], "gcode": None, "name": "plot.gcode"}

    def current_config() -> PlotConfig:
        return PlotConfig(
            width_mm=width.value,
            height_mm=height.value,
            margin_mm=margin.value,
            draw_feed=draw_feed.value,
            pen=PenConfig(down_value=int(pen_down.value), up_value=int(pen_up.value)),
        )

    def regenerate() -> None:
        lines = state["lines"]
        if not lines:
            return
        config = current_config()
        state["gcode"] = lines_to_gcode(lines, config, header_comment=state["name"])
        info.set_text(str(stats_for(prepare_geometry(lines, config), config)))
        # Vorschau ungespiegelt: SVG hat wie die Zeichnung den Ursprung oben links
        preview.content = lines_to_svg(
            prepare_geometry(lines, config, invert_y=False),
            config.width_mm,
            config.height_mm,
            travel_stroke="#d64545",
        )

    def handle_upload(event) -> None:
        data = event.content.read()
        state["name"] = event.name
        suffix = os.path.splitext(event.name)[1].lower()
        try:
            if suffix in IMAGE_SUFFIXES:
                state["lines"] = image_to_lines(
                    data, pitch_mm=pitch.value, image_suffix=suffix
                )
            else:
                state["lines"] = svg_to_lines(data)
        except VpypeNotAvailable as exc:
            ui.notify(str(exc), type="negative")
            return
        regenerate()
        ui.notify(f"{len(state['lines'])} Linien optimiert")

    def plot() -> None:
        if not state["gcode"]:
            ui.notify("Erst eine Datei hochladen", type="warning")
            return
        client = FluidNCClient(FluidNCConfig(host=host_input.value))
        try:
            remote = client.upload(state["gcode"], "plot.gcode")
            client.run_file(remote)
        except FluidNCError as exc:
            ui.notify(str(exc), type="negative")
            return
        ui.notify(f"Plot gestartet: {remote}", type="positive")

    def poll_status() -> None:
        client = FluidNCClient(FluidNCConfig(host=host_input.value, timeout_s=3))
        try:
            machine = client.status()
        except Exception:  # Board offline/aus ist hier der Normalfall, kein Grund für Lärm
            status.set_text("FluidNC nicht erreichbar")
            progress.set_value(0)
            return
        status.set_text(f"{machine.state} — {machine.sd_file or 'kein Job'}")
        progress.set_value((machine.sd_percent or 0) / 100)

    ui.label("Wandplotter").classes("text-2xl font-bold")

    with ui.row().classes("w-full items-start gap-8"):
        with ui.column().classes("w-96"):
            ui.upload(on_upload=handle_upload, label="SVG oder Foto hochladen").props(
                "accept=.svg,.png,.jpg,.jpeg"
            ).classes("w-full")

            width = ui.number("Breite (mm)", value=WALL_WIDTH_MM, on_change=regenerate)
            height = ui.number("Höhe (mm)", value=WALL_HEIGHT_MM, on_change=regenerate)
            margin = ui.number("Rand (mm)", value=50, on_change=regenerate)
            draw_feed = ui.number("Vorschub (mm/min)", value=1500, on_change=regenerate)
            pen_down = ui.number("Pen unten (S)", value=30, on_change=regenerate)
            pen_up = ui.number("Pen oben (S)", value=0, on_change=regenerate)
            pitch = ui.number("Schraffur-Pitch (mm)", value=3.0)
            host_input = ui.input("FluidNC-Host", value=host)

            info = ui.label("Noch nichts geladen").classes("text-sm text-gray-600")
            ui.button("Auf Wand plotten", on_click=plot).classes("w-full")

            status = ui.label("—")
            progress = ui.linear_progress(value=0, show_value=False)
            ui.timer(2.0, poll_status)

        preview = ui.html("").classes("flex-grow border rounded p-2")

    return ui


def main(host: str = "0.0.0.0", port: int = 8080) -> None:
    ui = create_app()
    ui.run(host=host, port=port, title="Wandplotter", reload=False)


if __name__ in {"__main__", "__mp_main__"}:  # NiceGUI startet den Prozess neu
    main()
