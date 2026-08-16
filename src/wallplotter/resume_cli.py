"""CLI zum Fortsetzen eines abgebrochenen Plots.

    wallplotter-resume wand.gcode --percent 42 -o rest.gcode
    wallplotter-resume wand.gcode --from-board --host 192.168.1.42 --upload --run

Mit ``--from-board`` wird der Fortschritt nicht getippt, sondern beim Board
erfragt: FluidNC meldet im Status, wie weit es in der SD-Datei gelesen hat.
Das geht nur, solange der Job noch läuft oder gerade angehalten ist — nach
einem Stromausfall ist die Zahl weg, dann bleibt der Blick an die Wand und
eine geschätzte Prozentangabe.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import FluidNCConfig
from .output import OutputError, write_text
from .resume import ResumeError, resume_file, scan_program


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallplotter-resume",
        description="Aus einem abgebrochenen Plot das lauffähige Restprogramm schneiden.",
    )
    parser.add_argument("gcode", type=Path, help="die ursprüngliche GCode-Datei")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--percent", type=float, help="Fortschritt in Prozent (0…100)")
    where.add_argument("--line", type=int, help="Zeilennummer, ab der weitergeplottet wird")
    where.add_argument(
        "--from-board",
        action="store_true",
        help="Fortschritt beim Board erfragen (nur solange der Job dort noch bekannt ist)",
    )
    parser.add_argument("-o", "--out", type=Path, help="Zieldatei (Standard: <input>-rest.gcode)")
    parser.add_argument(
        "--exact",
        action="store_true",
        help="auf den Millimeter genau ansetzen statt den angefangenen Strich neu zu ziehen",
    )
    parser.add_argument("--host", default="fluidnc.local", help="Hostname oder IP")
    parser.add_argument("--upload", action="store_true", help="auf die SD-Karte laden")
    parser.add_argument("--run", action="store_true", help="nach dem Upload direkt starten")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.gcode.exists():
        print(f"Datei nicht gefunden: {args.gcode}", file=sys.stderr)
        return 2

    percent = args.percent
    if args.from_board:
        from .upload import FluidNCClient, FluidNCError  # noqa: PLC0415

        try:
            machine = FluidNCClient(FluidNCConfig(host=args.host)).status()
        except FluidNCError as exc:
            print(str(exc), file=sys.stderr)
            return 5
        if machine.sd_percent is None:
            print(
                "Das Board meldet keinen SD-Fortschritt — der Job ist dort nicht mehr bekannt.\n"
                "Dann hilft nur: an der Wand abschätzen, wie weit es war, und --percent angeben.",
                file=sys.stderr,
            )
            return 4
        percent = machine.sd_percent
        print(f"Board meldet {percent:.1f} % von {machine.sd_file or '?'} ({machine.state})")

    try:
        rest = resume_file(
            args.gcode, line=args.line, percent=percent, whole_stroke=not args.exact
        )
    except ResumeError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    out_path = args.out or args.gcode.with_name(f"{args.gcode.stem}-rest{args.gcode.suffix}")
    try:
        write_text(out_path, rest, what="Restprogramm")
    except OutputError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    done = scan_program(args.gcode.read_text(encoding="utf-8"))[-1].draw_count
    left = scan_program(rest)[-1].draw_count
    print(f"Restprogramm geschrieben: {out_path}")
    print(f"Noch zu zeichnen: {left} von {done} Bewegungen")
    print(
        "Vor dem Start: Nullpunkt herstellen — nach einem Reset ist ein per G92 gesetzter "
        "Nullpunkt weg.\n"
        "  wallplotter-calibrate --host <ip> zero --corner bottom-left"
    )

    if args.upload or args.run:
        from .upload import FluidNCError, upload_and_run  # noqa: PLC0415

        try:
            remote = upload_and_run(
                rest, out_path.name, FluidNCConfig(host=args.host), run=args.run
            )
        except FluidNCError as exc:
            print(str(exc), file=sys.stderr)
            return 5
        print(f"Hochgeladen nach {remote}" + (" und gestartet" if args.run else ""))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
