"""CLI-Wrapper (Stufe 2 der Roadmap).

    plot input.svg --out plot.gcode --upload --run

Bleibt auch nach der Web-UI als Debugging- und Scripting-Werkzeug erhalten.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .config import WALL_HEIGHT_MM, WALL_WIDTH_MM, FluidNCConfig, PlotConfig
from .gcode import geometry_to_gcode, layers_to_gcode, prepare_geometry, stats_for
from .imaging import IMAGE_SUFFIXES, TECHNIQUES, ImagingError, image_to_lines
from .imaging import describe as describe_techniques
from .output import OutputError, write_text
from .patterns import PATTERNS, build, describe
from .pipeline import VpypeNotAvailable, lines_to_svg, svg_to_layers, svg_to_lines
from .timing import MotionLimits
from .toolhead import (
    LaserToolhead,
    Toolhead,
    ToolheadError,
    describe_toolheads,
    head_for,
    pen_map,
    toolhead_by_name,
)


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
    tools = parser.add_argument_group("Werkzeug")
    tools.add_argument(
        "--toolhead",
        default="fineliner",
        help="Werkzeugkopf aus dem Katalog (--list-toolheads zeigt ihn)",
    )
    tools.add_argument(
        "--list-toolheads", action="store_true", help="Werkzeugköpfe und ihre Werte zeigen"
    )
    tools.add_argument(
        "--pen-for",
        action="append",
        metavar="EBENE=KOPF",
        help="mit --layers: welche Strichfarbe mit welchem Stift gezeichnet wird "
        "(mehrfach angebbar, z. B. --pen-for '#e02020=marker')",
    )
    tools.add_argument("--pen-down", type=int, help="S-Wert Stift unten, überschreibt den Katalog")
    tools.add_argument("--pen-up", type=int, help="S-Wert Stift oben, überschreibt den Katalog")
    tools.add_argument("--pen-dwell", type=float, help="Servo-Wartezeit in s, überschreibt den Katalog")

    laser = parser.add_argument_group("Laser")
    laser.add_argument("--laser-power", type=float, help="Leistung in Prozent von s_max")
    laser.add_argument(
        "--laser-smax",
        type=int,
        help="S-Wert für volle Leistung — steht in der speed_map der config.yaml",
    )
    laser.add_argument("--laser-passes", type=int, help="Anzahl der Durchgänge")
    laser.add_argument(
        "--laser-constant",
        action="store_true",
        help="konstante Leistung (M3) statt dynamischer (M4) — nur für Fokuspunkte",
    )
    laser.add_argument(
        "--laser-verstanden",
        action="store_true",
        help="bestätigt, dass die Warnungen gelesen sind; ohne das entsteht kein Laser-GCode",
    )

    colors = parser.add_argument_group("Mehrfarbig")
    colors.add_argument(
        "--layers",
        action="store_true",
        help="SVG nach Strichfarben trennen und je Farbe eine GCode-Datei schreiben",
    )
    colors.add_argument(
        "--one-file",
        action="store_true",
        help="mit --layers: eine Datei mit M0-Pausen zum Stiftwechsel statt mehrerer",
    )

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

    quality = parser.add_argument_group("Qualität")
    quality.add_argument(
        "--correction",
        type=Path,
        help="Vorverzerrung gegen bekannte Maschinenfehler (Datei aus wallplotter.correction)",
    )
    quality.add_argument(
        "--adaptive-feed",
        action="store_true",
        help="Vorschub dort drosseln, wo die Kinematik schlecht konditioniert ist "
        "(braucht einen eingemessenen Standort)",
    )
    quality.add_argument(
        "--pen-below-pivot",
        type=float,
        default=100.0,
        help="Abstand Aufhängepunkt→Stiftspitze in mm, für die Resonanzprüfung",
    )
    quality.add_argument(
        "--no-resonance-check",
        action="store_true",
        help="nicht prüfen, ob die Zeichnung die Gondel zum Pendeln bringt",
    )
    quality.add_argument(
        "--acceleration",
        type=float,
        default=200.0,
        help="Beschleunigung der Maschine in mm/s², für die Laufzeitschätzung",
    )
    quality.add_argument(
        "--max-rate",
        type=float,
        default=4000.0,
        help="Höchstgeschwindigkeit der Achsen in mm/min, für die Laufzeitschätzung",
    )

    net = parser.add_argument_group("FluidNC")
    net.add_argument("--host", default="fluidnc.local", help="Hostname oder IP")
    net.add_argument("--upload", action="store_true", help="auf die SD-Karte laden")
    net.add_argument(
        "--run", action="store_true", help="nach dem Upload direkt starten (impliziert --upload)"
    )
    net.add_argument(
        "--trotzdem",
        action="store_true",
        help="auch starten, wenn die Maschine als beschäftigt gemeldet wird",
    )
    net.add_argument("--remote-name", help="Dateiname auf der SD-Karte")

    return parser


def _toolhead_from_args(args) -> Toolhead:
    """Den Kopf aus dem Katalog holen und mit den Schaltern nachjustieren.

    Der Katalogeintrag ist der Startwert, die Schalter sind die Korrektur —
    wer ``--pen-down`` setzt, meint genau diesen Stift, nicht alle.
    """
    head = toolhead_by_name(args.toolhead)
    if isinstance(head, LaserToolhead):
        return tune_laser(head, args)

    changes = {}
    if args.pen_down is not None:
        changes["down_value"] = args.pen_down
    if args.pen_up is not None:
        changes["up_value"] = args.pen_up
    if args.pen_dwell is not None:
        changes["dwell_s"] = args.pen_dwell
    return replace(head, **changes) if changes else head


def tune_laser(head: LaserToolhead, args) -> LaserToolhead:
    """Die ``--laser-*``-Schalter auf einen Laserkopf anwenden.

    Eigene Funktion, weil ein Laser auf zwei Wegen ins Programm kommt: über
    ``--toolhead laser`` und über ``--pen-for 'FARBE=laser'``. Stünde das nur
    im ersten Weg, liefe der zweite still mit den Katalogwerten — bei einer
    Leistungsangabe ist das der Unterschied zwischen Gravur und Brandloch.
    """
    changes = {}
    if args.laser_power is not None:
        changes["power_pct"] = args.laser_power
    if args.laser_smax is not None:
        changes["s_max"] = args.laser_smax
    if args.laser_passes is not None:
        changes["passes"] = args.laser_passes
    if args.laser_constant:
        changes["dynamic"] = False
    return replace(head, **changes) if changes else head


def guard_lasers(heads, args) -> None:
    """Den Riegel über *jeden* Kopf ziehen, der im Programm landet.

    Vorher hing die Bestätigung am Kopf aus ``--toolhead``. Über
    ``--pen-for 'FARBE=laser'`` entstand damit vollständiger Laser-GCode ohne
    jede Rückfrage und ohne eine einzige Warnung — genau die Lücke, gegen die
    die Bestätigung gebaut war.
    """
    lasers = [head for head in heads if isinstance(head, LaserToolhead)]
    if lasers and not args.laser_verstanden:
        raise ToolheadError(
            "Laser-GCode entsteht nur mit --laser-verstanden.\n"
            + "\n".join(
                f"  {note}" for note in lasers[0].check(travel_as_g1=False, draw_feed=0)
            )
            + "\n  Erst mit kleiner Leistung einen Rahmen fahren, dann schneiden."
        )


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(build_parser().parse_args(argv))
    except OutputError as exc:
        # Ein vertippter Ordner ist kein Softwarefehler und soll nicht so aussehen
        print(str(exc), file=sys.stderr)
        return 2


def _run(args) -> int:
    if args.list_patterns:
        print(describe())
        return 0

    if args.list_techniques:
        print(describe_techniques())
        return 0

    if args.list_toolheads:
        print(describe_toolheads())
        return 0

    try:
        head = _toolhead_from_args(args)
    except ToolheadError as exc:
        print(str(exc), file=sys.stderr)
        return 2

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
        toolhead=head,
        limits=MotionLimits(
            acceleration_mm_s2=args.acceleration, max_rate_mm_min=args.max_rate
        ),
    )

    # Der Standort trägt die Ankermaße — ohne die gibt es weder Vorverzerrung
    # nach dem Dehnungsmodell noch einen konditionsabhängigen Vorschub.
    location = None
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

    correction = None
    if args.correction:
        from .correction import CorrectionError, load_correction  # noqa: PLC0415

        try:
            # Das Dehnungsmodell rechnet mit den Ankermaßen; das gemessene
            # Polynom kommt ohne aus.
            correction = load_correction(
                args.correction, kinematics=location.kinematics() if location else None
            )
        except CorrectionError as exc:
            print(str(exc), file=sys.stderr)
            return 6
        print(f"Vorverzerrung aus {args.correction}{': ' + correction.note if correction.note else ''}")

    if args.adaptive_feed and location is None:
        print(
            "--adaptive-feed braucht einen Standort mit Ankermaßen: --location angeben.",
            file=sys.stderr,
        )
        return 2

    tools = None
    if args.pen_for:
        try:
            tools = {
                label: tune_laser(assigned, args) if isinstance(assigned, LaserToolhead) else assigned
                for label, assigned in pen_map(",".join(args.pen_for)).items()
            }
        except ToolheadError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    # Der Riegel gilt für alles, was tatsächlich Programm wird — nicht nur für
    # den Kopf aus --toolhead
    try:
        guard_lasers([head, *(tools or {}).values()], args)
    except ToolheadError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    optimize_kwargs = {
        "simplify_tolerance_mm": args.simplify,
        "merge_tolerance_mm": args.merge,
        "sort": not args.no_sort,
        "reloop": not args.no_reloop,
        "remove_hidden": args.occult,
    }

    feeds: list[float] | None = None
    source_name = args.input.name if args.input else ""

    if args.layers:
        if args.pattern or (args.input and args.input.suffix.lower() in IMAGE_SUFFIXES):
            print("--layers gilt nur für SVG-Vorlagen.", file=sys.stderr)
            return 2
        try:
            layer_list = svg_to_layers(args.input, quantization_mm=args.quantization,
                                       **optimize_kwargs)
        except VpypeNotAvailable as exc:
            print(str(exc), file=sys.stderr)
            return 3
        if not layer_list:
            print("Keine Linien gefunden.", file=sys.stderr)
            return 4

        base = args.out or args.input.with_suffix(".gcode")
        try:
            result = layers_to_gcode(layer_list, plot_config, separate=not args.one_file,
                                     fit=not args.no_fit, correction=correction, tools=tools)
        except ToolheadError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        for layer in layer_list:
            print(f"  {layer.color}  {len(layer.lines):>5} Linien  "
                  f"→ {head_for(layer, tools, plot_config.toolhead).name}")

        if args.one_file:
            write_text(base, result, what="GCode")
            print(f"{len(layer_list)} Ebenen mit Stiftwechsel-Pausen: {base}")
        else:
            slugs = {layer.label: layer.slug for layer in layer_list}
            for position, (label, program) in enumerate(result.items(), start=1):
                target = base.with_name(
                    f"{base.stem}-{position}-{slugs.get(label, 'ebene')}{base.suffix}"
                )
                write_text(target, program, what=f"Ebene {label}")
                print(f"Ebene {label} → {target}")
        # Sonst wartet man vor der Wand darauf, dass etwas hochgeladen wird
        ignored = [
            name
            for name, used in (
                ("--preview", args.preview),
                ("--upload", args.upload),
                ("--run", args.run),
            )
            if used
        ]
        if ignored:
            print(
                f"Hinweis: {', '.join(ignored)} gilt nicht für --layers — "
                "welche Farbe zuerst an die Wand soll, entscheidet der Mensch.",
                file=sys.stderr,
            )
        return 0

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

    # Einmal in Maschinenkoordinaten rechnen und dabei bleiben: Statistik,
    # Vorschübe und Ausgabe müssen dieselbe Geometrie sehen, sonst weicht die
    # Schätzung vom GCode-Header ab und die Vorschübe sitzen auf der falschen
    # Linie.
    machine_lines = prepare_geometry(
        lines, plot_config, fit=not args.no_fit, correction=correction
    )

    if args.adaptive_feed and location is not None:
        from .motion import conditioning_feeds  # noqa: PLC0415

        # Der Vorschub des Kopfes, nicht der aus der Konfiguration: ein Pinsel
        # mit eigenem draw_feed bekäme sonst die Werte eines Fineliners
        feeds = conditioning_feeds(
            machine_lines, location.kinematics(), plot_config.toolhead.feed_for(plot_config.draw_feed)
        )
        if feeds:
            print(
                f"Vorschub angepasst: {min(feeds):.0f} bis {max(feeds):.0f} mm/min "
                "je nach Kondition der Kinematik"
            )

    try:
        gcode = geometry_to_gcode(
            machine_lines,
            plot_config,
            header=f"Quelle: {source_name}",
            feeds=feeds,
        )
    except ToolheadError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    out_path = args.out or (
        Path(f"{args.pattern}.gcode") if args.pattern else args.input.with_suffix(".gcode")
    )
    write_text(out_path, gcode, what="GCode")
    print(f"{stats_for(machine_lines, plot_config)}")
    print(f"GCode geschrieben: {out_path}")

    if not args.no_resonance_check:
        from .motion import resonance_warning  # noqa: PLC0415

        warning = resonance_warning(
            machine_lines,
            plot_config.toolhead.feed_for(plot_config.draw_feed),
            args.pen_below_pivot,
        )
        if warning is not None:
            print(str(warning), file=sys.stderr if warning.critical else sys.stdout)

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
            correction=correction,
        )
        write_text(
            args.preview,
            lines_to_svg(
                preview_lines,
                plot_config.width_mm,
                plot_config.height_mm,
                travel_stroke="#d64545",
            ),
            what="Vorschau",
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
                force=args.trotzdem,
            )
        except FluidNCError as exc:
            print(str(exc), file=sys.stderr)
            return 5
        print(f"Hochgeladen nach {remote_path}" + (" und gestartet" if args.run else ""))

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
