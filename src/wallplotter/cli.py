"""CLI-Wrapper (Stufe 2 der Roadmap).

    plot input.svg --out plot.gcode --upload --run

Bleibt auch nach der Web-UI als Debugging- und Scripting-Werkzeug erhalten.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .config import WALL_HEIGHT_MM, WALL_WIDTH_MM, FluidNCConfig, PenConfig, PlotConfig
from .gcode import lines_to_gcode, prepare_geometry, stats_for
from .imaging import TECHNIQUES, ImagingError, image_to_lines
from .imaging import describe as describe_techniques
from .patterns import PATTERNS, build, describe
from .pipeline import VpypeNotAvailable, lines_to_svg, svg_to_lines

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plot",
        description="SVG oder Foto in FluidNC-GCode für den Wandplotter übersetzen.",
    )
    parser.add_argument("input", type=Path, nargs="?", help="SVG- oder Bilddatei")
    parser.add_argument(
        "--pattern",
        choices=sorted(PATTERNS),
        help="Testmuster statt einer Datei plotten (Maße 1:1, ohne Einpassen)",
    )
    parser.add_argument(
        "--pattern-spacing", type=float, help="Teilung für grid/circles in mm"
    )
    parser.add_argument(
        "--list-patterns", action="store_true", help="verfügbare Testmuster zeigen"
    )
    parser.add_argument(
        "-o", "--out", type=Path, help="Zieldatei (Standard: <input>.gcode)"
    )
    parser.add_argument("--preview", type=Path, help="Vorschau-SVG zusätzlich schreiben")

    area = parser.add_argument_group("Fläche")
    area.add_argument("--width", type=float, default=WALL_WIDTH_MM, help="mm")
    area.add_argument("--height", type=float, default=WALL_HEIGHT_MM, help="mm")
    area.add_argument("--margin", type=float, default=50.0, help="mm")
    area.add_argument(
        "--no-fit",
        action="store_true",
        help="Geometrie ist bereits in Maschinenkoordinaten, nicht einpassen",
    )
    area.add_argument(
        "--calibration",
        type=Path,
        help="Fläche aus einer Kalibrierdatei nehmen (überschreibt --width/--height)",
    )
    area.add_argument(
        "--location",
        nargs="?",
        const="",
        help="Fläche aus einem Standort nehmen (ohne Namen: der aktive)",
    )
    area.add_argument(
        "--no-invert-y",
        action="store_true",
        help="Y-Achse nicht spiegeln (Standard: SVG oben links → Maschine unten links)",
    )

    motion = parser.add_argument_group("Bewegung")
    motion.add_argument("--draw-feed", type=float, default=1500.0, help="mm/min")
    motion.add_argument("--travel-feed", type=float, default=3000.0, help="mm/min")
    motion.add_argument(
        "--travel-as-g1",
        action="store_true",
        help="Leerwege als G1 statt G0 (schont die Riemen)",
    )
    motion.add_argument("--pen-down", type=int, default=30, help="S-Wert Stift unten")
    motion.add_argument("--pen-up", type=int, default=0, help="S-Wert Stift oben")
    motion.add_argument("--pen-dwell", type=float, default=0.25, help="Sekunden")

    geo = parser.add_argument_group("Optimierung")
    geo.add_argument("--quantization", type=float, default=0.2, help="mm")
    geo.add_argument("--simplify", type=float, default=0.1, help="mm, 0 = aus")
    geo.add_argument("--merge", type=float, default=0.5, help="mm, 0 = aus")
    geo.add_argument("--no-sort", action="store_true", help="linesort überspringen")
    geo.add_argument(
        "--no-reloop",
        action="store_true",
        help="Startpunkt geschlossener Kurven nicht würfeln (reproduzierbarer GCode)",
    )
    geo.add_argument(
        "--occult", action="store_true", help="verdeckte Linien entfernen (Plugin occult)"
    )

    photo = parser.add_argument_group("Foto-Zweig")
    photo.add_argument(
        "--technique",
        choices=sorted(TECHNIQUES),
        default="spiral",
        help="Verfahren für Bildvorlagen (Standard: spiral)",
    )
    photo.add_argument(
        "--list-techniques", action="store_true", help="Bildverfahren erklären"
    )
    photo.add_argument("--pitch", type=float, help="Bahnabstand in mm (hatch/spiral)")
    photo.add_argument("--spacing", type=float, help="Punktabstand in Pixeln (stipple/tsp)")
    photo.add_argument("--dot", type=float, help="Punktlänge in mm (stipple)")

    net = parser.add_argument_group("FluidNC")
    net.add_argument("--host", default="fluidnc.local", help="Hostname oder IP")
    net.add_argument("--upload", action="store_true", help="auf die SD-Karte laden")
    net.add_argument(
        "--run", action="store_true", help="nach dem Upload direkt starten (impliziert --upload)"
    )
    net.add_argument("--remote-name", help="Dateiname auf der SD-Karte")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_patterns:
        print(describe())
        return 0

    if args.list_techniques:
        print(describe_techniques())
        return 0

    if not args.pattern:
        if args.input is None:
            print("Entweder eine Datei oder --pattern angeben.", file=sys.stderr)
            return 2
        if not args.input.exists():
            print(f"Datei nicht gefunden: {args.input}", file=sys.stderr)
            return 2

    plot_config = PlotConfig(
        width_mm=args.width,
        height_mm=args.height,
        margin_mm=args.margin,
        draw_feed=args.draw_feed,
        travel_feed=args.travel_feed,
        travel_as_g1=args.travel_as_g1,
        invert_y=not args.no_invert_y,
        pen=PenConfig(
            down_value=args.pen_down, up_value=args.pen_up, dwell_s=args.pen_dwell
        ),
    )

    if args.calibration or args.location is not None:
        from .calibration import AreaCalibration, CalibrationError  # noqa: PLC0415
        from .location import LocationBook, LocationError  # noqa: PLC0415

        try:
            if args.calibration:
                plot_config = AreaCalibration.load(args.calibration).to_plot_config(plot_config)
            else:
                location = LocationBook.load().get(args.location or None)
                plot_config = location.plot_config(plot_config)
                print(f"Standort {location.name}")
        except (CalibrationError, LocationError) as exc:
            print(str(exc), file=sys.stderr)
            return 6
        print(
            f"Kalibrierte Fläche: {plot_config.width_mm:.0f} × {plot_config.height_mm:.0f} mm "
            f"ab X{plot_config.origin_x_mm:.1f} Y{plot_config.origin_y_mm:.1f}"
        )

    optimize_kwargs = {
        "simplify_tolerance_mm": args.simplify,
        "merge_tolerance_mm": args.merge,
        "sort": not args.no_sort,
        "reloop": not args.no_reloop,
        "remove_hidden": args.occult,
    }

    feeds: list[float] | None = None
    source_name = args.input.name if args.input else ""

    if args.pattern:
        # Testmuster stehen schon in Flächenkoordinaten und werden nicht eingepasst
        extra = {"spacing": args.pattern_spacing} if args.pattern_spacing else {}
        try:
            pattern = build(
                args.pattern,
                plot_config.width_mm,
                plot_config.height_mm,
                plot_config.margin_mm,
                **extra,
            )
        except (KeyError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        lines, feeds, source_name = pattern.lines, pattern.feeds, pattern.name
        args.no_fit = True
        # Muster sind bereits in Maschinenkoordinaten (Y nach oben) geschrieben,
        # anders als SVGs — sonst läge beim Vorschub-Test die langsamste Linie oben
        plot_config = replace(plot_config, invert_y=False)
        print(pattern.description)
    else:
        try:
            if args.input.suffix.lower() in IMAGE_SUFFIXES:
                options = {}
                if args.pitch and args.technique in ("hatch", "spiral"):
                    options["pitch_mm"] = args.pitch
                if args.spacing and args.technique in ("stipple", "tsp"):
                    options["spacing_px"] = args.spacing
                if args.dot and args.technique == "stipple":
                    options["dot_mm"] = args.dot
                lines = image_to_lines(
                    args.input,
                    plot_config.width_mm,
                    plot_config.height_mm,
                    args.technique,
                    margin_mm=plot_config.margin_mm,
                    image_suffix=args.input.suffix.lower(),
                    **options,
                )
                # Bildverfahren rechnen selbst in Millimetern
                args.no_fit = True
                source_name = f"{args.input.name} ({args.technique})"
            else:
                lines = svg_to_lines(
                    args.input, quantization_mm=args.quantization, **optimize_kwargs
                )
        except (VpypeNotAvailable, ImagingError) as exc:
            print(str(exc), file=sys.stderr)
            return 3

    if not lines:
        print("Keine Linien gefunden — falsche Datei oder leeres SVG?", file=sys.stderr)
        return 4

    gcode = lines_to_gcode(
        lines,
        plot_config,
        fit=not args.no_fit,
        header_comment=f"Quelle: {source_name}",
        feeds=feeds,
    )

    # Statistik auf der Maschinen-Geometrie: die Leerwege zum Nullpunkt hängen
    # an der Spiegelung, sonst weicht die Schätzung vom GCode-Header ab
    machine_lines = prepare_geometry(lines, plot_config, fit=not args.no_fit)

    out_path = args.out or (
        Path(f"{args.pattern}.gcode") if args.pattern else args.input.with_suffix(".gcode")
    )
    out_path.write_text(gcode, encoding="utf-8")
    print(f"{stats_for(machine_lines, plot_config)}")
    print(f"GCode geschrieben: {out_path}")

    if args.preview:
        # Vorschau-SVG hat den Ursprung oben links: eine SVG-Vorlage bleibt also
        # unangetastet, ein Muster in Maschinenkoordinaten muss dafür gespiegelt
        # werden. Der Flächenversatz hilft beim Ansehen nicht.
        preview_lines = prepare_geometry(
            lines,
            plot_config,
            fit=not args.no_fit,
            invert_y=bool(args.pattern),
            apply_origin=False,
        )
        args.preview.write_text(
            lines_to_svg(
                preview_lines,
                plot_config.width_mm,
                plot_config.height_mm,
                travel_stroke="#d64545",
            ),
            encoding="utf-8",
        )
        print(f"Vorschau geschrieben: {args.preview}")

    if args.upload or args.run:
        from .upload import FluidNCError, upload_and_run  # noqa: PLC0415

        remote_name = args.remote_name or out_path.name
        try:
            remote_path = upload_and_run(
                gcode,
                remote_name,
                FluidNCConfig(host=args.host),
                run=args.run,
            )
        except FluidNCError as exc:
            print(str(exc), file=sys.stderr)
            return 5
        print(f"Hochgeladen nach {remote_path}" + (" und gestartet" if args.run else ""))

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
