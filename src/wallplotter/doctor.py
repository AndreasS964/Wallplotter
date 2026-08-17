"""Selbsttest: läuft das hier überhaupt, und woran hängt es sonst?

    wallplotter-doctor
    wallplotter-doctor --host 192.168.1.42

Gedacht für den Moment, in dem etwas nicht geht und unklar ist, woran es
liegt — an der Installation, an der Konfiguration oder am Board. Statt zu
raten, geht die Prüfung die Kette einmal von vorn nach hinten durch und sagt
bei jedem Punkt, was der nächste Schritt wäre.

Die Prüfung ist bewusst gestuft: Der Kern muss immer laufen, die Extras nur,
wenn man sie braucht, und das Board ist erst dran, wenn es an der Wand hängt.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__

__all__ = ["Check", "run_checks", "main"]

OK = "ok"
WARN = "warnung"
FAIL = "fehler"
SKIP = "offen"

_MARK = {OK: "✓", WARN: "!", FAIL: "✗", SKIP: "–"}

MINIMUM_PYTHON = (3, 10)
"""Steht so auch in pyproject.toml — hier, damit der Selbsttest es sagen kann."""


@dataclass(frozen=True)
class Check:
    """Ein Prüfpunkt samt Rat, was zu tun wäre."""

    name: str
    status: str
    detail: str = ""
    hint: str = ""

    def __str__(self) -> str:
        text = f"{_MARK[self.status]} {self.name}"
        if self.detail:
            text += f": {self.detail}"
        if self.hint:
            text += f"\n    → {self.hint}"
        return text


def _module(name: str) -> str | None:
    """Version eines Moduls, oder ``None`` wenn es fehlt."""
    try:
        module = importlib.import_module(name)
    except Exception:
        return None
    return str(getattr(module, "__version__", "vorhanden"))


# ---------------------------------------------------------------------------
# Die einzelnen Prüfungen
# ---------------------------------------------------------------------------


def check_python() -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info < MINIMUM_PYTHON:
        return Check(
            "Python", FAIL, version, "Wallplotter braucht 3.10 oder neuer (moderne Typangaben)"
        )
    return Check("Python", OK, f"{version}, wallplotter {__version__}")


def check_extras() -> list[Check]:
    """Die optionalen Zweige — jeder fehlende schaltet genau eine Fähigkeit ab."""
    wanted = [
        ("SVG einlesen", "vpype_cli", "geometry", "SVG-Vorlagen und die vpype-Optimierung"),
        ("Fotos", "PIL", "photo", "stipple, tsp und spiral"),
        ("Schraffur", "hatched", "hatch", "das Verfahren hatch"),
        ("Web-UI", "nicegui", "web", "die Oberfläche im Browser"),
        ("Upload", "requests", "", "alles, was mit dem Board redet"),
    ]
    checks = []
    for label, module, extra, what in wanted:
        version = _module(module)
        if version:
            checks.append(Check(label, OK, f"{module} {version}"))
        elif not extra:
            checks.append(
                Check(label, FAIL, f"{module} fehlt", f"pip install -e . — sonst geht {what} nicht")
            )
        else:
            checks.append(
                Check(label, SKIP, f"{module} fehlt", f'pip install -e ".[{extra}]" für {what}')
            )
    return checks


def check_core() -> list[Check]:
    """Der Kern von der Geometrie bis zum fertigen Programm, ohne jedes Extra.

    Kein Rauchtest um seiner selbst willen: Wenn hier etwas bricht, stimmt
    entweder die Installation nicht oder eine Abhängigkeit hat sich unter der
    Hand geändert — und beides merkt man sonst erst an der Wand.
    """
    from .config import PlotConfig
    from .gcode import prepare_geometry, stats_for
    from .geometry import bounds
    from .patterns import build
    from .resume import scan_program

    checks: list[Check] = []
    config = PlotConfig(width_mm=1000, height_mm=1200, margin_mm=50)
    try:
        pattern = build("frame", config.width_mm, config.height_mm, config.margin_mm)
        geometry = prepare_geometry(pattern.lines, config, fit=False, invert_y=False)
        from .gcode import geometry_to_gcode

        program = geometry_to_gcode(geometry, config, header="Selbsttest")
    except Exception as exc:
        return [Check("GCode erzeugen", FAIL, f"{type(exc).__name__}: {exc}")]

    checks.append(Check("GCode erzeugen", OK, f"{len(program.splitlines())} Zeilen aus 'frame'"))

    state = scan_program(program)[-1]
    if not (state.units_mm and state.absolute):
        checks.append(
            Check("Programmrahmen", FAIL, "keine Millimeter oder keine absoluten Koordinaten")
        )
    elif state.tool_on:
        checks.append(Check("Programmrahmen", FAIL, "das Programm endet mit angesetztem Werkzeug"))
    else:
        checks.append(
            Check("Programmrahmen", OK, f"{state.draw_count} Zeichenbewegungen, Werkzeug am Ende aus")
        )

    xmin, ymin, xmax, ymax = bounds(geometry)
    inside = 0 <= xmin and 0 <= ymin and xmax <= config.width_mm and ymax <= config.height_mm
    checks.append(
        Check(
            "Geometrie in der Fläche",
            OK if inside else FAIL,
            f"X {xmin:.0f}…{xmax:.0f}, Y {ymin:.0f}…{ymax:.0f} mm",
            "" if inside else "Die Zeichnung läge außerhalb der Wand — bitte melden, das ist ein Fehler",
        )
    )

    stats = stats_for(geometry, config)
    checks.append(Check("Laufzeitschätzung", OK, str(stats)))
    return checks


def check_locations(path: Path) -> list[Check]:
    from .location import LocationBook, LocationError

    try:
        book = LocationBook.load(path)
    except LocationError as exc:
        return [Check("Standorte", FAIL, str(exc), f"{path} von Hand prüfen oder löschen")]
    except Exception as exc:
        # etwa eine JSON-Datei, deren oberste Ebene eine Liste ist
        return [
            Check(
                "Standorte",
                FAIL,
                f"{type(exc).__name__}: {exc}",
                f"{path} ist keine Standortdatei — prüfen oder löschen",
            )
        ]

    if not book.locations:
        return [
            Check(
                "Standorte",
                SKIP,
                f"keine in {path}",
                "wallplotter-location new <Name> --span … --left … --right …",
            )
        ]

    checks = [Check("Standorte", OK, f"{len(book.locations)} in {path}, aktiv: {book.active}")]
    try:
        location = book.get()
    except LocationError as exc:
        return checks + [Check("Aktiver Standort", FAIL, str(exc))]

    try:
        anchors = location.anchors()
    except LocationError as exc:
        return checks + [
            Check("Ankermaße", FAIL, str(exc), "die drei Maße noch einmal mit dem Zollstock nehmen")
        ]
    checks.append(
        Check(
            "Ankermaße",
            OK,
            f"links X{anchors.left_x:.0f}, rechts X{anchors.right_x:.0f}, Höhe Y{anchors.y:.0f} mm",
        )
    )

    if not location.calibration.complete:
        missing = ", ".join(location.calibration.missing)
        return checks + [
            Check(
                "Fläche eingemessen",
                SKIP,
                f"es fehlen: {missing}",
                "wallplotter-calibrate --host <ip> record <ecke>",
            )
        ]

    origin_x, origin_y, width, height = location.calibration.rectangle()
    checks.append(
        Check(
            "Fläche eingemessen",
            OK,
            f"{width:.0f} × {height:.0f} mm ab X{origin_x:.0f} Y{origin_y:.0f}",
        )
    )
    analysis = location.analysis()
    worst = analysis.worst_resolution_mm
    checks.append(
        Check(
            "Auflösung",
            OK if worst <= 0.2 else WARN,
            f"schlimmstenfalls {worst * 1000:.0f} µm pro Mikroschritt",
            "" if worst <= 0.2 else "Anker höher oder weiter setzen, siehe wallplotter-location show",
        )
    )
    reserve = location.kinematics().motor.max_force_n / max(analysis.max_tension_n, 1e-9)
    checks.append(
        Check(
            "Zugkraftreserve",
            OK if reserve >= 3 else WARN,
            f"Faktor {reserve:.1f} ({analysis.max_tension_n:.1f} N nötig)",
            "" if reserve >= 3 else "Gondel leichter machen oder die Anker weiter spreizen",
        )
    )
    return checks


def check_firmware_config(path: Path, locations_path: Path) -> Check:
    """Steht in der config.yaml dieselbe Geometrie wie im aktiven Standort?

    Das ist die Stelle, an der ein Wandbild unbemerkt schief wird: Die Software
    rechnet mit den gemessenen Ankermaßen, die Firmware mit denen, die jemand
    vor drei Wochen eingetragen hat.
    """
    if not path.exists():
        return Check("Firmware-Konfiguration", SKIP, f"{path} nicht gefunden")
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return Check("Firmware-Konfiguration", SKIP, "PyYAML fehlt, nicht gegengeprüft")

    from .location import LocationBook, LocationError

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        wall = data["kinematics"]["WallPlotter"]
    except Exception as exc:
        return Check("Firmware-Konfiguration", FAIL, f"kein WallPlotter-Block: {exc}")

    try:
        location = LocationBook.load(locations_path).get()
        anchors = location.anchors()
    except LocationError:
        return Check(
            "Firmware-Konfiguration",
            SKIP,
            "WallPlotter-Block vorhanden, aber kein Standort zum Gegenprüfen",
        )

    pairs = [
        ("left_anchor_x", anchors.left_x),
        ("right_anchor_x", anchors.right_x),
        ("left_anchor_y", anchors.y),
        ("right_anchor_y", anchors.y),
    ]
    off = [
        f"{key}: Firmware {float(wall.get(key, 0)):.0f} ≠ Standort {value:.0f}"
        for key, value in pairs
        if abs(float(wall.get(key, 0)) - value) > 1.0
    ]
    if off:
        return Check(
            "Firmware-Konfiguration",
            WARN,
            "; ".join(off),
            f"wallplotter-location config {location.name} --out kinematics.yaml und übertragen",
        )
    return Check("Firmware-Konfiguration", OK, f"Ankermaße passen zu Standort {location.name}")


def check_board(host: str, timeout: float = 4.0) -> list[Check]:
    from .config import FluidNCConfig
    from .upload import FluidNCClient, FluidNCError

    client = FluidNCClient(FluidNCConfig(host=host, timeout_s=timeout))
    try:
        machine = client.status()
    except FluidNCError as exc:
        return [
            Check(
                "Board erreichbar",
                SKIP,
                f"{host}: {exc}",
                "Solange nichts an der Wand hängt, ist das in Ordnung — üben lässt "
                "sich der ganze Ablauf gegen `wallplotter-sim`. "
                "Sonst: IP prüfen, --host angeben",
            )
        ]

    checks = [Check("Board erreichbar", OK, f"{host}, Zustand {machine.state}")]
    if machine.state.startswith("Alarm"):
        checks.append(
            Check(
                "Maschinenzustand",
                WARN,
                machine.state,
                "Alarm quittieren ($X) und den Nullpunkt neu setzen",
            )
        )
    if machine.position:
        checks.append(
            Check("Position", OK, f"X {machine.position[0]:.1f} Y {machine.position[1]:.1f} mm")
        )
    else:
        checks.append(
            Check("Position", WARN, "der Status meldet keine Position", "Firmware-Version prüfen")
        )

    try:
        listing = client.list_files("/")
    except FluidNCError as exc:
        checks.append(
            Check("SD-Karte", FAIL, str(exc), "Karte gesteckt und formatiert? FAT32 wird erwartet")
        )
    else:
        checks.append(Check("SD-Karte", OK, f"lesbar, {len(listing)} Zeichen Antwort"))

    checks.append(check_motion_block(client))
    return checks


def check_motion_block(client) -> Check:
    """Blockiert das Board HTTP-Kommandos, sobald sich etwas bewegt?

    ``$HTTP/BlockDuringMotion`` steht ab Werk an. Diese Software geht für
    alles Maschinennahe über den WebSocket-Kanal und merkt davon nichts —
    aber jede Diagnose mit ``curl`` oder im Browser läuft dann während eines
    Plots in ein HTTP 503, und das sieht aus wie ein kaputtes Board.
    """
    from .upload import FluidNCError  # noqa: PLC0415

    try:
        answer = client.send_command("$HTTP/BlockDuringMotion").strip()
    except FluidNCError as exc:
        return Check("Bewegungssperre", SKIP, str(exc))

    value = answer.rpartition("=")[2].strip().upper()
    if value in {"OFF", "0", "FALSE"}:
        return Check("Bewegungssperre", OK, "$HTTP/BlockDuringMotion=OFF")
    if value in {"ON", "1", "TRUE"}:
        return Check(
            "Bewegungssperre",
            WARN,
            "$HTTP/BlockDuringMotion=ON",
            "Während der Fahrt antwortet /command mit HTTP 503. "
            "Einmalig `$HTTP/BlockDuringMotion=OFF` setzen",
        )
    return Check("Bewegungssperre", SKIP, f"unerwartete Antwort: {answer!r}")


# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------


def run_checks(
    *,
    host: str | None = None,
    locations_path: Path | None = None,
    firmware_config: Path | None = None,
) -> list[tuple[str, list[Check]]]:
    """Alle Prüfungen, nach Abschnitten gruppiert."""
    from .location import DEFAULT_PATH

    locations = locations_path or DEFAULT_PATH
    firmware = firmware_config or Path("config/fluidnc-wallplotter.yaml")

    sections = [
        ("Installation", [check_python(), *check_extras()]),
        ("Kern", check_core()),
        ("Standort und Fläche", check_locations(locations)),
        ("Firmware", [check_firmware_config(firmware, locations)]),
    ]
    if host:
        sections.append(("Board", check_board(host)))
    return sections


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallplotter-doctor",
        description="Prüfen, ob Installation, Konfiguration und Board zusammenpassen.",
    )
    parser.add_argument("--host", default="fluidnc.local", help="Hostname oder IP des Boards")
    parser.add_argument("--no-board", action="store_true", help="das Board nicht ansprechen")
    parser.add_argument("--locations", type=Path, help="Standortdatei")
    parser.add_argument("--firmware-config", type=Path, help="config.yaml des Boards")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sections = run_checks(
        host=None if args.no_board else args.host,
        locations_path=args.locations,
        firmware_config=args.firmware_config,
    )

    failures = 0
    warnings = 0
    for title, checks in sections:
        print(f"\n{title}")
        print("-" * max(20, len(title)))
        for check in checks:
            print(check)
            failures += check.status == FAIL
            warnings += check.status == WARN

    print()
    if failures:
        print(f"{failures} Fehler — damit plottet hier nichts." if failures > 1 else
              "Ein Fehler — damit plottet hier nichts.")
        return 1
    if warnings:
        print(
            f"Läuft, aber {warnings} Punkte wollen angesehen werden."
            if warnings > 1
            else "Läuft, aber ein Punkt will angesehen werden."
        )
        return 0
    print("Alles, was ohne Wand prüfbar ist, sitzt.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
