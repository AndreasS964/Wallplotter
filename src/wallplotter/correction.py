"""Vorverzerrung: gegen die bekannten Fehler der Maschine gegenrechnen.

Der begrenzende Fehler eines V-Plotters ist nicht die Schrittweite, sondern
die Riemendehnung — über drei Meter Länge ist GT2 eine Feder, und die Zugkraft
schwankt über die Fläche um den Faktor drei bis vier. Der gleichmäßige Anteil
verschwindet in der Eckkalibrierung; übrig bleibt eine ungleichmäßige
Verzerrung von einigen Zehntelmillimetern.

Zwei Wege dagegen, beide mit derselben Schnittstelle (:meth:`apply_lines`):

* :class:`MeasuredCorrection` — empirisch: Raster plotten, nachmessen, die
  Abweichungen als Polynom anpassen. Fängt auch, was wir nicht modelliert
  haben (schiefe Ankermaße, Pulley-Toleranz).
* :class:`StretchCorrection` — physikalisch: aus den berechneten Zugkräften
  und einer Riemensteifigkeit die Dehnung ausrechnen. Stimmt auch zwischen
  den Stützstellen, hängt aber an einem Materialwert.

Beide beschreiben dasselbe Vorwärtsmodell — „wohin fährt die Maschine
tatsächlich, wenn ich X befehle" — und kehren es zum Zeichnen um.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import Lines, Point

__all__ = [
    "MeasuredPoint",
    "MeasuredCorrection",
    "StretchCorrection",
    "CorrectionError",
    "invert",
    "load_correction",
]

Forward = Callable[[Point], Point]
"""Vorwärtsmodell: befohlene Position → tatsächlich erreichte Position."""


class CorrectionError(RuntimeError):
    """Korrektur nicht bestimmbar oder unbrauchbar."""


def invert(forward: Forward, target: Point, iterations: int = 12, tolerance: float = 1e-4) -> Point:
    """Zu einer Wunschposition die passende Befehlsposition suchen.

    Fixpunktiteration: Wir wissen, wohin die Maschine bei einem Befehl
    tatsächlich fährt, brauchen aber die Umkehrung. Da die Abweichung klein
    und glatt ist, konvergiert das in wenigen Schritten.
    """
    guess = target
    for _ in range(iterations):
        actual = forward(guess)
        error_x = target[0] - actual[0]
        error_y = target[1] - actual[1]
        guess = (guess[0] + error_x, guess[1] + error_y)
        if abs(error_x) < tolerance and abs(error_y) < tolerance:
            break
    return guess


def _apply_lines(forward: Forward, lines: Sequence[Sequence[Point]]) -> Lines:
    return [[invert(forward, point) for point in line] for line in lines]


# ---------------------------------------------------------------------------
# Empirisch: gemessenes Raster
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasuredPoint:
    """Ein Rasterpunkt: wohin er sollte, und wo er gelandet ist.

    Beide in Maschinenkoordinaten (mm). Gemessen wird am einfachsten relativ
    zu zwei Rasterlinien mit dem Zollstock.
    """

    nominal: Point
    measured: Point

    @property
    def error(self) -> Point:
        return (self.measured[0] - self.nominal[0], self.measured[1] - self.nominal[1])


# Ansatzfunktionen nach Grad. Warum das eine Rolle spielt: das Dehnungsfeld
# eines V-Plotters ist deutlich gekrümmt — affin und bilinear nehmen davon nur
# rund ein Drittel weg, erst kubisch kommt auf ein Fünftel des Ausgangsfehlers.
# Dafür braucht es aber 16 Messpunkte. Wer messen will statt zu modellieren,
# muss also gründlich messen.
DEGREES: dict[str, tuple[str, ...]] = {
    "affin": ("1", "x", "y"),
    "bilinear": ("1", "x", "y", "xy"),
    "quadratisch": ("1", "x", "y", "xy", "xx", "yy"),
    "kubisch": ("1", "x", "y", "xy", "xx", "yy", "xxy", "xyy", "xxx", "yyy"),
}


def _basis(point: Point, degree: str) -> list[float]:
    x, y = point
    table = {
        "1": 1.0, "x": x, "y": y, "xy": x * y, "xx": x * x, "yy": y * y,
        "xxy": x * x * y, "xyy": x * y * y, "xxx": x**3, "yyy": y**3,
    }
    return [table[term] for term in DEGREES[degree]]


def _degree_for(count: int) -> str:
    """Höchster Grad, den die Messpunktzahl noch trägt."""
    for degree in ("kubisch", "quadratisch", "bilinear", "affin"):
        if count >= len(DEGREES[degree]) + 6:
            return degree
    return "affin"


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Kleines Gauß-Verfahren mit Spaltenpivot — für 3 bis 4 Unbekannte reicht das."""
    size = len(rhs)
    augmented = [row[:] + [value] for row, value in zip(matrix, rhs, strict=True)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise CorrectionError(
                "Messpunkte liegen zu ungünstig (kollinear?) — über die Fläche verteilt messen"
            )
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / augmented[column][column]
            for c in range(column, size + 1):
                augmented[row][c] -= factor * augmented[column][c]
    result = [0.0] * size
    for row in reversed(range(size)):
        total = sum(augmented[row][c] * result[c] for c in range(row + 1, size))
        result[row] = (augmented[row][size] - total) / augmented[row][row]
    return result


@dataclass
class MeasuredCorrection:
    """Korrektur aus nachgemessenen Rasterpunkten.

    Angepasst wird der *Fehler* als Polynom über der Fläche, nicht die Position
    selbst — so bleibt die Korrektur klein und gutartig, auch außerhalb der
    Messpunkte.
    """

    coefficients_x: list[float]
    coefficients_y: list[float]
    degree: str = "bilinear"
    note: str = ""

    @classmethod
    def fit(cls, points: Sequence[MeasuredPoint], degree: str | None = None, note: str = ""):
        """Polynom an die gemessenen Abweichungen anpassen.

        Ohne Angabe wird der höchste Grad genommen, den die Messpunktzahl mit
        Reserve trägt: 3 Punkte affin, ab 10 quadratisch, ab 16 kubisch.
        """
        if len(points) < 3:
            raise CorrectionError("Mindestens 3 Messpunkte nötig, besser 16 über die Fläche verteilt")
        chosen = degree or _degree_for(len(points))
        if chosen not in DEGREES:
            raise CorrectionError(f"Unbekannter Grad {chosen!r}, möglich: {', '.join(DEGREES)}")
        if len(points) < len(DEGREES[chosen]):
            raise CorrectionError(
                f"{chosen} braucht mindestens {len(DEGREES[chosen])} Messpunkte, "
                f"vorhanden sind {len(points)}"
            )

        size = len(DEGREES[chosen])
        matrix = [[0.0] * size for _ in range(size)]
        rhs_x = [0.0] * size
        rhs_y = [0.0] * size
        for point in points:
            terms = _basis(point.nominal, chosen)
            error_x, error_y = point.error
            for i in range(size):
                rhs_x[i] += terms[i] * error_x
                rhs_y[i] += terms[i] * error_y
                for j in range(size):
                    matrix[i][j] += terms[i] * terms[j]

        return cls(
            coefficients_x=_solve([row[:] for row in matrix], rhs_x),
            coefficients_y=_solve([row[:] for row in matrix], rhs_y),
            degree=chosen,
            note=note,
        )

    def forward(self, point: Point) -> Point:
        terms = _basis(point, self.degree)
        return (
            point[0] + sum(c * t for c, t in zip(self.coefficients_x, terms, strict=True)),
            point[1] + sum(c * t for c, t in zip(self.coefficients_y, terms, strict=True)),
        )

    def apply_lines(self, lines: Sequence[Sequence[Point]]) -> Lines:
        return _apply_lines(self.forward, lines)

    def residual_mm(self, points: Sequence[MeasuredPoint]) -> tuple[float, float]:
        """Was nach der Anpassung übrig bleibt: ``(größter, mittlerer)`` Fehler."""
        errors = []
        for point in points:
            predicted = self.forward(point.nominal)
            errors.append(
                ((predicted[0] - point.measured[0]) ** 2 + (predicted[1] - point.measured[1]) ** 2)
                ** 0.5
            )
        return (max(errors), sum(errors) / len(errors))

    def to_dict(self) -> dict:
        return {
            "type": "measured",
            "degree": self.degree,
            "coefficients_x": self.coefficients_x,
            "coefficients_y": self.coefficients_y,
            "note": self.note,
        }

    def save(self, path: Path | str) -> Path:
        target = Path(path)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return target


# ---------------------------------------------------------------------------
# Physikalisch: Riemendehnung
# ---------------------------------------------------------------------------


@dataclass
class StretchCorrection:
    """Korrektur aus dem Riemendehnungsmodell.

    ``specific_stiffness`` ist die Federsteifigkeit eines *ein Meter langen*
    Riemens in N/mm; für einen kürzeren Riemen steigt sie umgekehrt
    proportional zur Länge. Grobe Anhaltswerte: GT2 6 mm mit Glasfaserkern
    15–30, mit Stahlkern eher 100. Der Wert lässt sich mit
    :meth:`fit_stiffness` aus gemessenen Rasterpunkten bestimmen.
    """

    kinematics: object
    specific_stiffness: float = 20.0
    note: str = ""

    def __post_init__(self) -> None:
        if self.specific_stiffness <= 0:
            raise CorrectionError("Steifigkeit muss größer als 0 sein")

    def forward(self, point: Point) -> Point:
        """Wohin die Gondel wirklich rutscht, wenn die Riemen sich dehnen."""
        left, right = self.kinematics.belt_lengths(*point)
        tension_left, tension_right = self.kinematics.belt_tension_n(*point)
        # Dehnung = Kraft / Steifigkeit, Steifigkeit = spezifisch / Länge[m]
        stretched_left = left + tension_left * (left / 1000.0) / self.specific_stiffness
        stretched_right = right + tension_right * (right / 1000.0) / self.specific_stiffness
        try:
            return self.kinematics.position(stretched_left, stretched_right)
        except ValueError:
            return point

    def apply_lines(self, lines: Sequence[Sequence[Point]]) -> Lines:
        return _apply_lines(self.forward, lines)

    def displacement_mm(self, point: Point) -> float:
        actual = self.forward(point)
        return ((actual[0] - point[0]) ** 2 + (actual[1] - point[1]) ** 2) ** 0.5

    @classmethod
    def fit_stiffness(
        cls,
        kinematics,
        points: Sequence[MeasuredPoint],
        bounds: tuple[float, float] = (2.0, 500.0),
        steps: int = 60,
    ) -> StretchCorrection:
        """Die Steifigkeit suchen, die zu den gemessenen Abweichungen passt.

        Eindimensionale Suche über eine logarithmische Skala — das Modell ist
        in diesem Parameter monoton, ein Gitter reicht also völlig.
        """
        if not points:
            raise CorrectionError("Ohne Messpunkte lässt sich nichts anpassen")

        def error_for(stiffness: float) -> float:
            model = cls(kinematics, stiffness)
            total = 0.0
            for point in points:
                predicted = model.forward(point.nominal)
                total += (predicted[0] - point.measured[0]) ** 2
                total += (predicted[1] - point.measured[1]) ** 2
            return total

        low, high = bounds
        best = low
        for _ in range(3):  # dreimal verfeinern
            ratio = (high / low) ** (1 / (steps - 1))
            candidates = [low * ratio**i for i in range(steps)]
            best = min(candidates, key=error_for)
            low, high = best / ratio**2, best * ratio**2
        return cls(kinematics, best, note="aus Messpunkten angepasst")

    def to_dict(self) -> dict:
        return {
            "type": "stretch",
            "specific_stiffness": self.specific_stiffness,
            "note": self.note,
        }

    def save(self, path: Path | str) -> Path:
        target = Path(path)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return target


@dataclass
class CombinedCorrection:
    """Erst modellieren, dann den Rest empirisch aufräumen."""

    parts: list = field(default_factory=list)

    def forward(self, point: Point) -> Point:
        for part in self.parts:
            point = part.forward(point)
        return point

    def apply_lines(self, lines: Sequence[Sequence[Point]]) -> Lines:
        return _apply_lines(self.forward, lines)


def load_correction(path: Path | str, kinematics=None):
    """Korrektur aus einer Datei laden; das Dehnungsmodell braucht Kinematik."""
    source = Path(path)
    if not source.exists():
        raise CorrectionError(f"Keine Korrektur unter {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CorrectionError(f"{source} ist nicht lesbar: {exc}") from exc

    kind = data.get("type")
    if kind == "measured":
        return MeasuredCorrection(
            coefficients_x=[float(v) for v in data["coefficients_x"]],
            coefficients_y=[float(v) for v in data["coefficients_y"]],
            degree=str(data.get("degree", "bilinear")),
            note=str(data.get("note", "")),
        )
    if kind == "stretch":
        if kinematics is None:
            raise CorrectionError("Das Dehnungsmodell braucht einen Standort mit Ankermaßen")
        return StretchCorrection(
            kinematics, float(data["specific_stiffness"]), str(data.get("note", ""))
        )
    raise CorrectionError(f"Unbekannte Korrekturart: {kind!r}")
