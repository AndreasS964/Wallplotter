"""WallPlotter-Kinematik nachrechnen — offener Punkt 2 aus docs/projektidee.md.

Beantwortet die Fragen, die *vor* dem Drucken der Mechanik feststehen sollten:

* Wie fein löst die Maschine an der ungünstigsten Stelle der Fläche auf?
* Wie lang müssen die Riemen sein?
* Wie viel Zugkraft brauchen die Motoren im schlimmsten Fall — reicht das
  Haltemoment der 17HS4412P1-3 (0,45 Nm)?
* Wo wird die Geometrie schlecht konditioniert (Riemen fast parallel)?

Reine Mathematik, keine Abhängigkeiten. Aufruf: ``python -m wallplotter.kinematics``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import WALL_HEIGHT_MM, WALL_WIDTH_MM

__all__ = [
    "Anchors",
    "Gondola",
    "Motor",
    "WallPlotterKinematics",
    "AreaAnalysis",
    "analyze_area",
    "analysis_report",
    "compare_anchor_positions",
    "default_kinematics",
]


@dataclass(frozen=True)
class Anchors:
    """Die beiden Umlenkpunkte, an denen die Riemen die Wand verlassen.

    Koordinaten in Maschinenkoordinaten (Ursprung unten links der Fläche,
    Y nach oben). Die Anker sitzen typischerweise oberhalb der Fläche.
    """

    left_x: float
    right_x: float
    y: float

    @property
    def span(self) -> float:
        return self.right_x - self.left_x

    def __post_init__(self) -> None:
        if self.span <= 0:
            raise ValueError("rechter Anker muss rechts vom linken sitzen")


@dataclass(frozen=True)
class Gondola:
    mass_g: float = 300.0
    """Masse der selbstgewichteten Gondel inkl. Stift und Servo."""

    @property
    def weight_n(self) -> float:
        return self.mass_g / 1000.0 * 9.81


@dataclass(frozen=True)
class Motor:
    """NEMA17 mit GT2-Pulley."""

    full_steps_per_rev: int = 200
    """1,8°-Motor."""

    microsteps: int = 16
    pulley_teeth: int = 20
    belt_pitch_mm: float = 2.0
    holding_torque_nm: float = 0.45

    @property
    def mm_per_rev(self) -> float:
        return self.pulley_teeth * self.belt_pitch_mm

    @property
    def steps_per_mm(self) -> float:
        return self.full_steps_per_rev * self.microsteps / self.mm_per_rev

    @property
    def step_mm(self) -> float:
        """Riemenlängenänderung pro Mikroschritt."""
        return 1.0 / self.steps_per_mm

    @property
    def pulley_radius_mm(self) -> float:
        """Teilkreisradius: Umfang = Zähne × Teilung."""
        return self.mm_per_rev / (2 * math.pi)

    @property
    def max_force_n(self) -> float:
        """Zugkraft am Riemen bei vollem Haltemoment (statisch, ohne Reserve)."""
        return self.holding_torque_nm / (self.pulley_radius_mm / 1000.0)


class WallPlotterKinematics:
    """Umrechnung zwischen Stiftposition und Riemenlängen."""

    def __init__(
        self,
        anchors: Anchors,
        motor: Motor | None = None,
        gondola: Gondola | None = None,
    ) -> None:
        self.anchors = anchors
        self.motor = motor or Motor()
        self.gondola = gondola or Gondola()

    # -- Vorwärts / rückwärts ---------------------------------------------

    def belt_lengths(self, x: float, y: float) -> tuple[float, float]:
        """Stiftposition → (linke, rechte) Riemenlänge in mm."""
        left = math.hypot(x - self.anchors.left_x, y - self.anchors.y)
        right = math.hypot(x - self.anchors.right_x, y - self.anchors.y)
        return left, right

    def position(self, left: float, right: float) -> tuple[float, float]:
        """Riemenlängen → Stiftposition (die untere der beiden Lösungen)."""
        span = self.anchors.span
        if left + right < span:
            raise ValueError("Riemen zu kurz — Position nicht erreichbar")
        # Schnittpunkt zweier Kreise um die Anker
        a = (left**2 - right**2 + span**2) / (2 * span)
        h_sq = left**2 - a**2
        if h_sq < 0:
            raise ValueError("Riemenlängen ergeben keinen Schnittpunkt")
        return (self.anchors.left_x + a, self.anchors.y - math.sqrt(h_sq))

    # -- Güte der Geometrie an einer Stelle -------------------------------

    def _unit_vectors(self, x: float, y: float) -> tuple[tuple[float, float], tuple[float, float]]:
        """Einheitsvektoren vom Stift zum jeweiligen Anker."""
        left, right = self.belt_lengths(x, y)
        if left == 0 or right == 0:
            raise ValueError("Stift sitzt genau auf einem Anker")
        return (
            ((self.anchors.left_x - x) / left, (self.anchors.y - y) / left),
            ((self.anchors.right_x - x) / right, (self.anchors.y - y) / right),
        )

    def belt_angle_deg(self, x: float, y: float) -> float:
        """Winkel zwischen den beiden Riemen. Klein = schlecht konditioniert."""
        (lx, ly), (rx, ry) = self._unit_vectors(x, y)
        cos = max(-1.0, min(1.0, lx * rx + ly * ry))
        return math.degrees(math.acos(cos))

    def resolution_mm(self, x: float, y: float) -> float:
        """Größte Stiftbewegung, die ein einzelner Mikroschritt auslöst.

        Aus der Jacobi-Matrix der Riemenlängen. Mit den Einheitsvektoren
        :math:`u_l`, :math:`u_r` vom Stift zu den Ankern ist

        .. code-block:: text

            J = [[-lx, -ly],
                 [-rx, -ry]]        det J = lx·ry − ly·rx = sin(Riemenwinkel)

        Die Spalten der inversen Matrix sind :math:`(-r_y, r_x)/\\det` und
        :math:`(l_y, -l_x)/\\det`. Beide haben die Länge 1/|det|, weil
        :math:`u_l` und :math:`u_r` Einheitsvektoren sind — ein Schritt auf
        *jeder* der beiden Achsen verschiebt den Stift also gleich weit:

        .. code-block:: text

            Auflösung = Schrittweite / |sin(Riemenwinkel)|

        Hier stand früher eine Mischung aus Komponenten beider Spalten
        (``hypot(-ry, lx)`` und ``hypot(rx, -ly)``). Die Zahlen sahen plausibel
        aus, wiesen aber die falsche Problemzone aus — und weil
        :func:`wallplotter.motion.conditioning_feeds` genau daraus den
        ortsabhängigen Vorschub ableitet, wurde dort am falschen Ende
        gebremst.
        """
        (lx, ly), (rx, ry) = self._unit_vectors(x, y)
        det = lx * ry - ly * rx
        if abs(det) < 1e-12:
            return math.inf  # Riemen kollinear: Position nicht bestimmbar
        return self.motor.step_mm / abs(det)

    def belt_tension_n(self, x: float, y: float) -> tuple[float, float]:
        """Statische Riemenkräfte, die das Gondelgewicht halten."""
        (lx, ly), (rx, ry) = self._unit_vectors(x, y)
        det = lx * ry - ly * rx
        if abs(det) < 1e-12:
            return (math.inf, math.inf)
        weight = self.gondola.weight_n
        # T_l * u_l + T_r * u_r = (0, +W)  →  Cramer
        t_left = (0 * ry - weight * rx) / det
        t_right = (lx * weight - ly * 0) / det
        return (abs(t_left), abs(t_right))

    def max_belt_length(self, area_width: float, area_height: float) -> float:
        """Längster benötigter Riemen für die Fläche (Ursprung unten links)."""
        corners = [(0.0, 0.0), (area_width, 0.0), (0.0, area_height), (area_width, area_height)]
        return max(max(self.belt_lengths(x, y)) for x, y in corners)


@dataclass(frozen=True)
class AreaAnalysis:
    """Auswertung über die gesamte Zeichenfläche."""

    worst_resolution_mm: float
    worst_resolution_at: tuple[float, float]
    best_resolution_mm: float
    worst_angle_deg: float
    """Der am schlechtesten konditionierte Riemenwinkel.

    Schlecht ist *beides*: nahe 0° (Riemen parallel) und nahe 180° (Riemen in
    einer Linie, direkt unter den Ankern). Maßstab ist deshalb der Sinus, nicht
    der Winkel selbst.
    """

    worst_angle_at: tuple[float, float]
    max_tension_n: float
    max_tension_at: tuple[float, float]
    max_belt_mm: float

    def verdict(self, motor: Motor) -> list[str]:
        """Klartext-Bewertung mit den Grenzwerten aus der Projektidee."""
        notes: list[str] = []
        if self.worst_resolution_mm <= 0.1:
            notes.append(
                f"Auflösung unkritisch: schlimmstenfalls {self.worst_resolution_mm:.3f} mm "
                "pro Mikroschritt — feiner als jede Stiftspitze."
            )
        else:
            notes.append(
                f"Auflösung grenzwertig: {self.worst_resolution_mm:.3f} mm pro Mikroschritt "
                f"bei {self.worst_resolution_at[0]:.0f}/{self.worst_resolution_at[1]:.0f} mm."
            )

        x, y = self.worst_angle_at
        if self.worst_angle_deg > 160:
            notes.append(
                f"Riemen laufen bei {x:.0f}/{y:.0f} mm bis auf {self.worst_angle_deg:.0f}° "
                "auseinander, also fast in eine Linie — dort ist die Höhe schlecht bestimmt "
                "und der Zug steigt. Anker höher setzen hilft mehr als sie zu spreizen."
            )
        elif self.worst_angle_deg < 20:
            notes.append(
                f"Riemenwinkel wird bei {x:.0f}/{y:.0f} mm bis auf {self.worst_angle_deg:.0f}° eng — "
                "dort ist die Position schlecht bestimmt, Anker weiter spreizen."
            )
        else:
            notes.append(
                f"Riemenwinkel bleibt im gutmütigen Bereich (ungünstigster Wert "
                f"{self.worst_angle_deg:.0f}° bei {x:.0f}/{y:.0f} mm)."
            )

        reserve = motor.max_force_n / self.max_tension_n if self.max_tension_n else math.inf
        notes.append(
            f"Maximale Riemenkraft {self.max_tension_n:.1f} N bei "
            f"{self.max_tension_at[0]:.0f}/{self.max_tension_at[1]:.0f} mm; Motor schafft "
            f"rechnerisch {motor.max_force_n:.0f} N → Faktor {reserve:.0f} Reserve."
        )
        if reserve < 3:
            notes.append(
                "Wenig Reserve: Motorstrom, Gondelgewicht oder Ankerabstand überdenken."
            )
        notes.append(
            f"Längster Riemen {self.max_belt_mm / 1000:.2f} m pro Seite — "
            f"mit Umlenkung und Reserve rund {2 * (self.max_belt_mm / 1000 + 0.5):.1f} m Gesamtbedarf."
        )
        return notes


def analyze_area(
    kinematics: WallPlotterKinematics,
    width_mm: float = WALL_WIDTH_MM,
    height_mm: float = WALL_HEIGHT_MM,
    samples: int = 21,
) -> AreaAnalysis:
    """Fläche abrastern und die kritischen Kennwerte einsammeln."""
    if samples < 2:
        raise ValueError("mindestens 2 Stützstellen je Achse")

    worst_res, worst_at = 0.0, (0.0, 0.0)
    best_res = math.inf
    worst_sin, worst_angle, angle_at = math.inf, 90.0, (0.0, 0.0)
    max_tension, tension_at = 0.0, (0.0, 0.0)

    for iy in range(samples):
        y = height_mm * iy / (samples - 1)
        for ix in range(samples):
            x = width_mm * ix / (samples - 1)
            try:
                resolution = kinematics.resolution_mm(x, y)
            except ValueError:
                # Der Stift sitzt an dieser einen Rasterstelle exakt auf einem
                # Anker (etwa bei --overhang 0 --above 0, wo der Anker in eine
                # Ecke der Fläche fällt) — eine einzelne Singularität im
                # Raster, kein Grund, die ganze Auswertung abzubrechen.
                continue
            if resolution > worst_res:
                worst_res, worst_at = resolution, (x, y)
            best_res = min(best_res, resolution)

            angle = kinematics.belt_angle_deg(x, y)
            conditioning = abs(math.sin(math.radians(angle)))
            if conditioning < worst_sin:
                worst_sin, worst_angle, angle_at = conditioning, angle, (x, y)

            tension = max(kinematics.belt_tension_n(x, y))
            if tension > max_tension:
                max_tension, tension_at = tension, (x, y)

    return AreaAnalysis(
        worst_resolution_mm=worst_res,
        worst_resolution_at=worst_at,
        best_resolution_mm=best_res,
        worst_angle_deg=worst_angle,
        worst_angle_at=angle_at,
        max_tension_n=max_tension,
        max_tension_at=tension_at,
        max_belt_mm=kinematics.max_belt_length(width_mm, height_mm),
    )


def analysis_report(
    kinematics: WallPlotterKinematics,
    width_mm: float = WALL_WIDTH_MM,
    height_mm: float = WALL_HEIGHT_MM,
    samples: int = 21,
    table_points: int = 5,
) -> str:
    """Kompletter Textbericht — was `python -m wallplotter.kinematics` ausgibt."""
    motor = kinematics.motor
    anchors = kinematics.anchors
    analysis = analyze_area(kinematics, width_mm, height_mm, samples)

    out = [
        "WallPlotter-Kinematik",
        "=" * 60,
        f"Fläche          {width_mm:.0f} × {height_mm:.0f} mm",
        f"Anker           links x={anchors.left_x:.0f}, rechts x={anchors.right_x:.0f}, "
        f"y={anchors.y:.0f} mm (Spannweite {anchors.span:.0f} mm)",
        f"Motor           {motor.full_steps_per_rev} Vollschritte × {motor.microsteps} Mikroschritte, "
        f"Pulley {motor.pulley_teeth}Z/{motor.belt_pitch_mm:.0f}mm → {motor.mm_per_rev:.0f} mm/Umdrehung",
        f"Riemenauflösung {motor.steps_per_mm:.1f} Schritte/mm ({motor.step_mm * 1000:.1f} µm pro Schritt)",
        f"Gondel          {kinematics.gondola.mass_g:.0f} g = {kinematics.gondola.weight_n:.2f} N",
        "",
        "Stützstellen (X/Y in mm)",
        "-" * 60,
        f"{'Position':>16} {'Riemen l/r':>16} {'Auflösung':>11} {'Winkel':>8} {'Zug max':>9}",
    ]

    for iy in range(table_points):
        y = height_mm * iy / (table_points - 1)
        for ix in range(table_points):
            x = width_mm * ix / (table_points - 1)
            left, right = kinematics.belt_lengths(x, y)
            out.append(
                f"{f'{x:.0f}/{y:.0f}':>16} {f'{left:.0f}/{right:.0f}':>16} "
                f"{kinematics.resolution_mm(x, y) * 1000:>8.0f} µm "
                f"{kinematics.belt_angle_deg(x, y):>6.1f}° "
                f"{max(kinematics.belt_tension_n(x, y)):>7.1f} N"
            )

    out += ["", "Bewertung", "-" * 60]
    out += [f"* {note}" for note in analysis.verdict(motor)]
    return "\n".join(out)


def compare_anchor_positions(
    width_mm: float = WALL_WIDTH_MM,
    height_mm: float = WALL_HEIGHT_MM,
    overhangs: tuple[float, ...] = (0.0, 150.0, 300.0),
    heights: tuple[float, ...] = (100.0, 250.0, 400.0),
    motor: Motor | None = None,
    gondola: Gondola | None = None,
    samples: int = 15,
) -> str:
    """Ankerpositionen gegeneinander stellen — Entscheidungshilfe fürs Aufhängen.

    Der obere Rand der Fläche ist der kritische Bereich; wie viel Luft die Anker
    darüber brauchen, lässt sich so vor der Wandmontage ablesen.
    """
    out = [
        "Ankerposition im Vergleich",
        "=" * 60,
        "Überstand = Anker seitlich neben der Fläche, Höhe = Anker über der Oberkante",
        "",
        f"{'Überstand':>10} {'Höhe':>7} {'Auflösung':>12} {'Winkel':>9} {'Zug max':>9} {'Riemen':>9}",
        "-" * 60,
    ]
    for overhang in overhangs:
        for above in heights:
            kinematics = WallPlotterKinematics(
                Anchors(-overhang, width_mm + overhang, height_mm + above), motor, gondola
            )
            analysis = analyze_area(kinematics, width_mm, height_mm, samples)
            out.append(
                f"{overhang:>7.0f} mm {above:>4.0f} mm "
                f"{analysis.worst_resolution_mm * 1000:>9.0f} µm "
                f"{analysis.worst_angle_deg:>7.0f}° "
                f"{analysis.max_tension_n:>7.1f} N "
                f"{analysis.max_belt_mm / 1000:>6.2f} m"
            )
    out += [
        "",
        "Gelesen wird spaltenweise: kleinere Auflösung ist besser, der Winkel soll "
        "möglichst weit weg von 0° und 180° bleiben,",
        "die Zugkraft klein gegen die rund 70 N, die ein Motor am 20Z-Pulley aufbringt.",
    ]
    return "\n".join(out)


def default_kinematics(
    width_mm: float = WALL_WIDTH_MM,
    height_mm: float = WALL_HEIGHT_MM,
    anchor_overhang_mm: float = 150.0,
    anchor_above_mm: float = 150.0,
) -> WallPlotterKinematics:
    """Sinnvolle Startgeometrie: Anker seitlich versetzt und über der Fläche.

    Der seitliche Überstand hält den Riemenwinkel in den oberen Ecken offen —
    säßen die Anker genau über den Ecken, liefe der Stift dort in die
    Singularität (Riemen und Bewegungsrichtung fallen zusammen).
    """
    return WallPlotterKinematics(
        Anchors(
            left_x=-anchor_overhang_mm,
            right_x=width_mm + anchor_overhang_mm,
            y=height_mm + anchor_above_mm,
        )
    )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - dünner Wrapper
    import argparse

    parser = argparse.ArgumentParser(
        prog="wallplotter-kinematics",
        description="Auflösung, Riemenlängen und Zugkräfte der WallPlotter-Kinematik prüfen.",
    )
    parser.add_argument("--width", type=float, default=WALL_WIDTH_MM)
    parser.add_argument("--height", type=float, default=WALL_HEIGHT_MM)
    parser.add_argument("--overhang", type=float, default=150.0, help="Anker seitlich neben der Fläche, mm")
    parser.add_argument("--above", type=float, default=150.0, help="Anker über der Fläche, mm")
    parser.add_argument("--gondola-g", type=float, default=300.0)
    parser.add_argument("--microsteps", type=int, default=16)
    parser.add_argument("--samples", type=int, default=21)
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Ankerpositionen gegeneinander stellen statt eine einzelne auswerten",
    )
    args = parser.parse_args(argv)

    if args.compare:
        print(
            compare_anchor_positions(
                args.width,
                args.height,
                motor=Motor(microsteps=args.microsteps),
                gondola=Gondola(mass_g=args.gondola_g),
            )
        )
        return 0

    kinematics = WallPlotterKinematics(
        Anchors(
            left_x=-args.overhang,
            right_x=args.width + args.overhang,
            y=args.height + args.above,
        ),
        Motor(microsteps=args.microsteps),
        Gondola(mass_g=args.gondola_g),
    )
    print(analysis_report(kinematics, args.width, args.height, samples=args.samples))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
