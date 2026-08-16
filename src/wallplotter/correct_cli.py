"""CLI für die Vorverzerrung: messen, anpassen, gegenrechnen.

Der Ablauf ist bewusst dreiteilig, weil er es in Wirklichkeit auch ist:

1. ``wallplotter-correct raster --out raster.gcode`` — ein Messraster plotten.
2. Die Kreuze mit dem Zollstock nachmessen und die Werte eintragen
   (``wallplotter-correct messen`` legt eine Vorlage an, in die man die
   Ist-Werte schreibt).
3. ``wallplotter-correct anpassen`` — daraus eine Korrekturdatei rechnen, die
   ``plot --correction`` dann verwendet.

Ohne diesen Weg wäre ``--correction`` ein Schalter für ein Dateiformat, das
sonst niemand herstellen kann.

Wieviel das bringt, sagt der Anpassungsschritt selbst: er zeigt den Fehler vor
und nach der Anpassung. Wird er nicht deutlich kleiner, war entweder zu grob
gemessen oder das Modell passt nicht — dann ist Vorverzerren die falsche
Antwort und die Mechanik die richtige.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import PlotConfig
from .correction import (
    DEGREES,
    CorrectionError,
    MeasuredCorrection,
    MeasuredPoint,
    StretchCorrection,
)
from .gcode import geometry_to_gcode, prepare_geometry
from .location import LocationBook, LocationError
from .patterns import cross

DEFAULT_MEASUREMENTS = Path("messpunkte.json")
DEFAULT_CORRECTION = Path("korrektur.json")


def grid_points(width: float, height: float, margin: float, steps: int) -> list[tuple[float, float]]:
    """Rasterpunkte in Flächenkoordinaten, gleichmäßig verteilt."""
    if steps < 2:
        raise CorrectionError("mindestens 2 Punkte je Achse")
    inner_w, inner_h = width - 2 * margin, height - 2 * margin
    if inner_w <= 0 or inner_h <= 0:
        raise CorrectionError("Rand ist größer als die Fläche")
    return [
        (margin + inner_w * col / (steps - 1), margin + inner_h * row / (steps - 1))
        for row in range(steps)
        for col in range(steps)
    ]


def measurement_pattern(points, size: float = 30.0):
    """Ein Kreuz je Rasterpunkt — messbar ist nur ein Schnittpunkt."""
    lines = []
    for x, y in points:
        lines += cross(x, y, size)
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallplotter-correct",
        description="Maschinenfehler messen und beim Plotten gegenrechnen.",
    )
    parser.add_argument("--location", nargs="?", const="", help="Standort (ohne Namen: der aktive)")
    parser.add_argument("--measurements", type=Path, default=DEFAULT_MEASUREMENTS)
    sub = parser.add_subparsers(dest="command", required=True)

    raster = sub.add_parser("raster", help="Messraster als GCode schreiben")
    raster.add_argument("--steps", type=int, default=4, help="Punkte je Achse (4 → 16 Punkte)")
    raster.add_argument("--size", type=float, default=30.0, help="Kreuzgröße in mm")
    raster.add_argument("-o", "--out", type=Path, default=Path("raster.gcode"))

    measure = sub.add_parser("messen", help="Vorlage zum Eintragen der Messwerte anlegen")
    measure.add_argument("--steps", type=int, default=4)

    fit = sub.add_parser("anpassen", help="aus den Messwerten eine Korrekturdatei rechnen")
    fit.add_argument(
        "--model",
        choices=["polynom", "dehnung"],
        default="polynom",
        help="polynom = empirisch (braucht nur Messpunkte), dehnung = physikalisch "
        "(braucht die Ankermaße des Standorts)",
    )
    fit.add_argument("--degree", choices=sorted(DEGREES), help="Grad des Polynoms")
    fit.add_argument("-o", "--out", type=Path, default=DEFAULT_CORRECTION)

    sub.add_parser("zeigen", help="eine vorhandene Korrekturdatei bewerten")
    return parser


def _plot_config(args) -> tuple[PlotConfig, object | None]:
    """Fläche und Kinematik aus dem Standort, sonst die Vorgabefläche."""
    if args.location is None:
        return PlotConfig(), None
    location = LocationBook.load().get(args.location or None)
    return location.plot_config(), location


def _load_measurements(path: Path) -> list[MeasuredPoint]:
    if not path.exists():
        raise CorrectionError(
            f"Keine Messwerte unter {path} — erst `wallplotter-correct messen` aufrufen."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        points = [
            MeasuredPoint(
                nominal=(float(entry["soll"][0]), float(entry["soll"][1])),
                measured=(float(entry["ist"][0]), float(entry["ist"][1])),
            )
            for entry in data["punkte"]
            if entry.get("ist")
        ]
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise CorrectionError(f"{path} ist nicht lesbar: {exc}") from exc
    if not points:
        raise CorrectionError(
            f"In {path} steht bei keinem Punkt ein Ist-Wert. Nachmessen und eintragen."
        )
    return points


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config, location = _plot_config(args)

        if args.command == "raster":
            points = grid_points(config.width_mm, config.height_mm, config.margin_mm, args.steps)
            geometry = prepare_geometry(
                measurement_pattern(points, args.size), config, fit=False, invert_y=False
            )
            args.out.write_text(
                geometry_to_gcode(
                    geometry, config, header=f"Messraster {args.steps}x{args.steps}"
                ),
                encoding="utf-8",
            )
            print(f"Messraster geschrieben: {args.out} ({len(points)} Kreuze)")
            print("Plotten, dann jedes Kreuz nachmessen und die Ist-Werte eintragen:")
            print("  wallplotter-correct messen")
            return 0

        if args.command == "messen":
            points = grid_points(config.width_mm, config.height_mm, config.margin_mm, args.steps)
            template = {
                "hinweis": "Bei jedem Punkt unter 'ist' die nachgemessene Position "
                "in Maschinenkoordinaten eintragen (mm). Punkte ohne 'ist' werden übergangen.",
                "punkte": [
                    {
                        "soll": [round(x + config.origin_x_mm, 1), round(y + config.origin_y_mm, 1)],
                        "ist": None,
                    }
                    for x, y in points
                ],
            }
            args.measurements.write_text(
                json.dumps(template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"Vorlage geschrieben: {args.measurements} ({len(points)} Punkte)")
            print("Ist-Werte eintragen, dann: wallplotter-correct anpassen")
            return 0

        if args.command == "anpassen":
            points = _load_measurements(args.measurements)
            if args.model == "dehnung":
                if location is None:
                    print(
                        "Das Dehnungsmodell braucht die Ankermaße: --location angeben.",
                        file=sys.stderr,
                    )
                    return 2
                correction = StretchCorrection.fit_stiffness(location.kinematics(), points)
                print(
                    f"Riemensteifigkeit angepasst: {correction.specific_stiffness:.1f} N/mm "
                    "für einen Meter Riemen"
                )
            else:
                correction = MeasuredCorrection.fit(points, args.degree)
                print(f"Polynom vom Grad '{correction.degree}' aus {len(points)} Punkten")

            before = max(
                (point.error[0] ** 2 + point.error[1] ** 2) ** 0.5 for point in points
            )
            after = _residual(correction, points)
            print(f"Größter Fehler vorher {before:.2f} mm, nach der Anpassung {after:.2f} mm")
            if after > before * 0.7:
                # Ehrlich sein: eine Korrektur, die nichts wegnimmt, verschleiert nur
                print(
                    "Das nimmt kaum etwas weg. Entweder wurde zu grob gemessen, oder der "
                    "Fehler ist keiner, den man wegrechnen kann — dann hilft nur die Mechanik.",
                    file=sys.stderr,
                )
            correction.save(args.out)
            print(f"Korrektur geschrieben: {args.out}")
            print(f"Verwenden mit: plot bild.svg --correction {args.out}")
            return 0

        if args.command == "zeigen":
            from .correction import load_correction  # noqa: PLC0415

            correction = load_correction(
                DEFAULT_CORRECTION, kinematics=location.kinematics() if location else None
            )
            print(json.dumps(correction.to_dict(), indent=2, ensure_ascii=False))
            try:
                points = _load_measurements(args.measurements)
            except CorrectionError:
                return 0
            print(f"Restfehler an den Messpunkten: {_residual(correction, points):.2f} mm")
            return 0

    except (CorrectionError, LocationError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


def _residual(correction, points) -> float:
    """Größter Abstand zwischen Modell und Messung, in mm."""
    worst = 0.0
    for point in points:
        predicted = correction.forward(point.nominal)
        worst = max(
            worst,
            (
                (predicted[0] - point.measured[0]) ** 2 + (predicted[1] - point.measured[1]) ** 2
            )
            ** 0.5,
        )
    return worst


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
