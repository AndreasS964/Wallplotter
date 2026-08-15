"""CLI zum Ausmessen der Zeichenfläche.

    wallplotter-calibrate --host 192.168.1.42 zero
    wallplotter-calibrate --host 192.168.1.42 jog --dx -100 --dy -50
    wallplotter-calibrate --host 192.168.1.42 record bottom-left
    wallplotter-calibrate show
    wallplotter-calibrate --host 192.168.1.42 goto top-right

Typischer Ablauf: Gondel an den oberen Anschlag fahren, ``zero``, dann mit
``jog`` in jede Ecke und dort ``record``. ``show`` rechnet daraus die Fläche.
Dieselben Schritte gibt es in der Web-UI mit Knöpfen.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .calibration import CORNERS, AreaCalibration, CalibrationError
from .config import FluidNCConfig
from .upload import FluidNCClient, FluidNCError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallplotter-calibrate",
        description="Zeichenfläche durch Anfahren der Ecken ausmessen.",
    )
    parser.add_argument("--host", default="fluidnc.local")
    parser.add_argument("--file", type=Path, default=Path("calibration.json"))
    parser.add_argument("--feed", type=float, default=1000.0, help="Jog-Vorschub in mm/min")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("zero", help="aktuelle Position zum Nullpunkt erklären (G92)")
    sub.add_parser("status", help="Status und Position abfragen")
    sub.add_parser("show", help="gespeicherte Kalibrierung auswerten")

    jog = sub.add_parser("jog", help="relativ verfahren")
    jog.add_argument("--dx", type=float, default=0.0)
    jog.add_argument("--dy", type=float, default=0.0)

    record = sub.add_parser("record", help="aktuelle Position als Ecke festhalten")
    record.add_argument("corner", choices=CORNERS)
    record.add_argument("--at", type=float, nargs=2, metavar=("X", "Y"),
                        help="Position von Hand angeben statt vom Board zu lesen")

    goto = sub.add_parser("goto", help="gespeicherte Ecke anfahren")
    goto.add_argument("corner", choices=CORNERS)

    sub.add_parser("clear", help="Kalibrierung verwerfen")
    return parser


def _load(path: Path) -> AreaCalibration:
    try:
        return AreaCalibration.load(path)
    except CalibrationError:
        return AreaCalibration()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = FluidNCClient(FluidNCConfig(host=args.host))

    try:
        if args.command == "zero":
            client.set_zero()
            print("Nullpunkt gesetzt.")
            return 0

        if args.command == "status":
            machine = client.status()
            print(f"{machine.state} @ {machine.position}")
            return 0

        if args.command == "jog":
            client.jog(args.dx, args.dy, args.feed)
            print(f"Jog dx={args.dx:.1f} dy={args.dy:.1f} mm")
            return 0

        if args.command == "record":
            calibration = _load(args.file)
            position = tuple(args.at) if args.at else client.position()
            calibration.record(args.corner, position)
            calibration.save(args.file)
            print(f"{args.corner} bei X{position[0]:.1f} Y{position[1]:.1f} gespeichert.")
            if calibration.complete:
                print(calibration.summary())
            else:
                print("Fehlt noch: " + ", ".join(calibration.missing))
            return 0

        if args.command == "goto":
            calibration = AreaCalibration.load(args.file)
            if args.corner not in calibration.points:
                print(f"{args.corner} ist nicht kalibriert.", file=sys.stderr)
                return 4
            x, y = calibration.points[args.corner]
            client.jog_to(x, y, args.feed)
            print(f"Fahre {args.corner} an: X{x:.1f} Y{y:.1f}")
            return 0

        if args.command == "show":
            print(AreaCalibration.load(args.file).summary())
            return 0

        if args.command == "clear":
            args.file.unlink(missing_ok=True)
            print(f"{args.file} gelöscht.")
            return 0

    except CalibrationError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except FluidNCError as exc:
        print(str(exc), file=sys.stderr)
        return 5

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
