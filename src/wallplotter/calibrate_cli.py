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
from .location import DEFAULT_PATH as LOCATION_PATH
from .location import LocationBook, LocationError
from .upload import FluidNCClient, FluidNCError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallplotter-calibrate",
        description="Zeichenfläche durch Anfahren der Ecken ausmessen.",
    )
    parser.add_argument("--host", default="fluidnc.local")
    parser.add_argument(
        "--file",
        type=Path,
        help="Kalibrierdatei — ohne Angabe geht es in den aktiven Standort",
    )
    parser.add_argument("--locations", type=Path, default=LOCATION_PATH)
    parser.add_argument("--location", help="Standort, falls nicht der aktive gemeint ist")
    parser.add_argument("--feed", type=float, default=1000.0, help="Jog-Vorschub in mm/min")

    sub = parser.add_subparsers(dest="command", required=True)

    zero = sub.add_parser("zero", help="aktuelle Position zum Nullpunkt erklären (G92)")
    zero.add_argument(
        "--persistent",
        action="store_true",
        help="als G54-Versatz speichern (übersteht Ausschalten) statt als flüchtiges G92",
    )
    zero.add_argument(
        "--corner",
        choices=CORNERS,
        help="verlorenen Nullpunkt wiederherstellen: diese kalibrierte Ecke anfahren "
        "und ihre Koordinaten setzen",
    )
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


class _Store:
    """Kalibrierung liegt entweder in einer eigenen Datei oder im Standort.

    Der Standort ist der Normalfall — nur so gehören Ankermaße und Fläche
    zusammen. Die Einzeldatei bleibt für schnelle Versuche.
    """

    def __init__(self, args) -> None:
        self.path = args.file
        self.book = None if args.file else LocationBook.load(args.locations)
        self.locations_path = args.locations
        self.location_name = args.location

    def load(self) -> AreaCalibration:
        if self.book is None:
            if not self.path.exists():
                # Der Normalfall vor der ersten Aufnahme — keine Meldung nötig.
                return AreaCalibration()
            # Existiert die Datei aber ist kaputt, soll das auffallen: sonst
            # fängt man hier mit einer leeren Kalibrierung an und überschreibt
            # beim nächsten `record` lautlos, was vorher drinstand.
            return AreaCalibration.load(self.path)
        return self.book.get(self.location_name).calibration

    def save(self, calibration: AreaCalibration) -> str:
        if self.book is None:
            calibration.save(self.path)
            return str(self.path)
        location = self.book.get(self.location_name)
        location.calibration = calibration
        self.book.save(self.locations_path)
        return f"Standort {location.name}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = FluidNCClient(FluidNCConfig(host=args.host))

    try:
        if args.command == "zero":
            if args.corner:
                calibration = _Store(args).load()
                if args.corner not in calibration.points:
                    print(f"{args.corner} ist nicht kalibriert.", file=sys.stderr)
                    return 4
                x, y = calibration.points[args.corner]
            else:
                x = y = 0.0
            if args.persistent:
                client.set_work_offset(x, y)
                where = f" über {args.corner}" if args.corner else ""
                print(f"Nullpunkt{where} als G54-Versatz gespeichert: X{x:.1f} Y{y:.1f}")
                print(
                    "Hinweis: Das hilft nach dem Einschalten nur mit einer "
                    "reproduzierbaren mechanischen Referenz — Gondel an den Anschlag "
                    "und das Board DORT neu starten. Ein $H gibt es nicht: die "
                    "WallPlotter-Kinematik kann nicht referenzieren (canHome() liefert "
                    "false), auch nicht mit StallGuard."
                )
            else:
                client.set_zero(x, y)
                where = f" über {args.corner} wiederhergestellt" if args.corner else " gesetzt"
                print(f"Nullpunkt{where}: X{x:.1f} Y{y:.1f}")
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
            store = _Store(args)
            calibration = store.load()
            position = tuple(args.at) if args.at else client.position()
            calibration.record(args.corner, position)
            target = store.save(calibration)
            print(f"{args.corner} bei X{position[0]:.1f} Y{position[1]:.1f} → {target}")
            if calibration.complete:
                print(calibration.summary())
            else:
                print("Fehlt noch: " + ", ".join(calibration.missing))
            return 0

        if args.command == "goto":
            calibration = _Store(args).load()
            if args.corner not in calibration.points:
                print(f"{args.corner} ist nicht kalibriert.", file=sys.stderr)
                return 4
            x, y = calibration.points[args.corner]
            client.jog_to(x, y, args.feed)
            print(f"Fahre {args.corner} an: X{x:.1f} Y{y:.1f}")
            return 0

        if args.command == "show":
            print(_Store(args).load().summary())
            return 0

        if args.command == "clear":
            store = _Store(args)
            store.save(AreaCalibration())
            print("Kalibrierung verworfen.")
            return 0

    except (CalibrationError, LocationError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except FluidNCError as exc:
        print(str(exc), file=sys.stderr)
        return 5

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
