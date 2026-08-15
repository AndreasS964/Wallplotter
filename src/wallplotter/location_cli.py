"""CLI für Standorte — eine Aufhängung anlegen, prüfen, wechseln.

    wallplotter-location new Keller --span 2300 --left 1450 --right 1470
    wallplotter-location list
    wallplotter-location show
    wallplotter-location config --out kinematics.yaml
    wallplotter-location use Werkstatt

Die Flächenecken kommen aus ``wallplotter-calibrate`` und landen im jeweils
aktiven Standort.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .location import DEFAULT_PATH, Location, LocationBook, LocationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallplotter-location",
        description="Aufhängungen an verschiedenen Wänden verwalten.",
    )
    parser.add_argument("--file", type=Path, default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="Standort aus drei Maßen anlegen")
    new.add_argument("name")
    new.add_argument("--span", type=float, required=True, help="Abstand der Umlenkpunkte in mm")
    new.add_argument("--left", type=float, required=True, help="linker Riemen am Nullpunkt in mm")
    new.add_argument("--right", type=float, required=True, help="rechter Riemen am Nullpunkt in mm")
    new.add_argument("--gondola-g", type=float, default=300.0)
    new.add_argument("--microsteps", type=int, default=16)
    new.add_argument("--note", default="")

    sub.add_parser("list", help="bekannte Standorte auflisten")

    show = sub.add_parser("show", help="Standort auswerten")
    show.add_argument("name", nargs="?")

    use = sub.add_parser("use", help="Standort aktivieren")
    use.add_argument("name")

    remove = sub.add_parser("remove", help="Standort löschen")
    remove.add_argument("name")

    config = sub.add_parser("config", help="FluidNC-Kinematikblock ausgeben")
    config.add_argument("name", nargs="?")
    config.add_argument("--out", type=Path, help="in Datei schreiben statt auf die Konsole")
    config.add_argument("--segment-length", type=float, default=1.0)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        book = LocationBook.load(args.file)

        if args.command == "new":
            location = Location(
                name=args.name,
                anchor_span_mm=args.span,
                left_belt_zero_mm=args.left,
                right_belt_zero_mm=args.right,
                gondola_mass_g=args.gondola_g,
                microsteps=args.microsteps,
                note=args.note,
            )
            book.add(location)
            book.save(args.file)
            print(location.report())
            print("\nAls aktiver Standort gesetzt. Jetzt die Ecken anfahren:")
            print("  wallplotter-calibrate --host <ip> zero")
            print("  wallplotter-calibrate --host <ip> record bottom-left   (usw.)")
            return 0

        if args.command == "list":
            if not book.locations:
                print("Noch kein Standort angelegt.")
                return 0
            for name, location in sorted(book.locations.items()):
                marker = "*" if name == book.active else " "
                state = (
                    "eingemessen" if location.calibration.complete else "Fläche fehlt noch"
                )
                print(f"{marker} {name:<16} Spannweite {location.anchor_span_mm:>6.0f} mm  {state}")
            return 0

        if args.command == "show":
            print(book.get(args.name).report())
            return 0

        if args.command == "use":
            book.use(args.name)
            book.save(args.file)
            print(f"Aktiver Standort: {args.name}")
            return 0

        if args.command == "remove":
            book.remove(args.name)
            book.save(args.file)
            print(f"{args.name} gelöscht. Aktiv: {book.active or 'keiner'}")
            return 0

        if args.command == "config":
            block = book.get(args.name).fluidnc_kinematics_yaml(args.segment_length)
            if args.out:
                args.out.write_text(block + "\n", encoding="utf-8")
                print(f"Geschrieben: {args.out}")
            else:
                print(block)
            return 0

    except LocationError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
