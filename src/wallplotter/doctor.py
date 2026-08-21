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


def check_firmware_config(path: Path, locations_path: Path) -> list[Check]:
    """Beschreibt die config.yaml dieselbe Maschine wie die Software?

    Drei Fragen, in dieser Reihenfolge — jede kann für sich schiefgehen:

    1. Liest FluidNC die Datei überhaupt so, wie sie gemeint ist? Geprüft wird
       mit dessen eigenem Blick auf die Zeilen — ein Kommentar hinter einem
       Zahlenwert etwa reicht für ConfigAlarm, und ein YAML-Parser sieht daran
       nichts. Und kennt FluidNC jeden Schlüssel? Einer zu viel, und das Board
       steht ebenfalls, ohne dass eine Achse zuckt.
    2. Stehen dort die Ankermaße des aktiven Standorts? Das ist die Stelle, an
       der ein Wandbild unbemerkt schief wird: Die Software rechnet mit dem
       Gemessenen, die Firmware mit dem, was jemand vor drei Wochen eintrug.
    3. Ist die Datei noch das, was der Erzeuger schreiben würde — oder hat
       jemand hineingegriffen?
    """
    if not path.exists():
        return [Check("Firmware-Konfiguration", SKIP, f"{path} nicht gefunden")]
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return [Check("Firmware-Konfiguration", SKIP, "PyYAML fehlt, nicht gegengeprüft")]

    from .firmware import FirmwareConfig
    from .fluidnc_schema import ERROR as SCHEMA_ERROR
    from .fluidnc_schema import check_lines, check_mapping
    from .location import LocationBook, LocationError

    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
        wall = data["kinematics"]["WallPlotter"]
    except Exception as exc:
        return [Check("Firmware-Konfiguration", FAIL, f"kein WallPlotter-Block: {exc}")]

    checks: list[Check] = []

    schlimm = [
        str(f)
        for f in list(check_lines(text)) + list(check_mapping(data))
        if f.level == SCHEMA_ERROR
    ]
    if schlimm:
        checks.append(
            Check(
                "config.yaml gegen FluidNC",
                FAIL,
                "; ".join(schlimm[:3]) + (f" (+{len(schlimm) - 3} weitere)" if len(schlimm) > 3 else ""),
                "FluidNC geht damit in ConfigAlarm und fährt nicht — "
                f"Einzelheiten: wallplotter-firmware pruefen {path}",
            )
        )
    else:
        checks.append(
            Check(
                "config.yaml gegen FluidNC",
                OK,
                "jeder Schlüssel bekannt, jede Zeile so lesbar wie gemeint",
            )
        )

    try:
        location = LocationBook.load(locations_path).get()
        anchors = location.anchors()
    except LocationError:
        checks.append(
            Check(
                "Firmware-Konfiguration",
                SKIP,
                "WallPlotter-Block vorhanden, aber kein Standort zum Gegenprüfen",
            )
        )
        erzeugt = FirmwareConfig()
    else:
        pairs = [
            ("left_anchor_x", anchors.left_x),
            ("right_anchor_x", anchors.right_x),
            ("left_anchor_y", anchors.y),
            ("right_anchor_y", anchors.y),
        ]
        try:
            off = [
                f"{key}: Firmware {float(wall.get(key, 0)):.0f} ≠ Standort {value:.0f}"
                for key, value in pairs
                if abs(float(wall.get(key, 0)) - value) > 1.0
            ]
        except (TypeError, ValueError) as exc:
            # Ein Ankermaß in der Datei ist nicht in eine Zahl umzuwandeln
            # (Handbearbeitung, kaputtes YAML) — das soll den ganzen
            # Selbsttest nicht mit einer rohen Ausnahme beenden.
            checks.append(
                Check("Firmware-Konfiguration", FAIL, f"Ankermaß in {path} ist keine Zahl: {exc}")
            )
        else:
            if off:
                checks.append(
                    Check(
                        "Firmware-Konfiguration",
                        WARN,
                        "; ".join(off),
                        f"wallplotter-firmware config --location {location.name} --out {path}",
                    )
                )
            else:
                checks.append(
                    Check(
                        "Firmware-Konfiguration", OK, f"Ankermaße passen zu Standort {location.name}"
                    )
                )
        erzeugt = FirmwareConfig.from_location(location)

    if text == erzeugt.render():
        checks.append(Check("Erzeugt", OK, "die Datei ist das, was das Werkzeug schreibt"))
    else:
        checks.append(
            Check(
                "Erzeugt",
                WARN,
                "die Datei weicht vom Erzeuger ab",
                f"Unterschiede zeigen: wallplotter-firmware diff {path}",
            )
        )
    return checks


def check_board(host: str, timeout: float = 4.0) -> list[Check]:
    from .config import FluidNCConfig
    from .upload import FluidNCClient, FluidNCError

    client = FluidNCClient(FluidNCConfig(host=host, timeout_s=timeout))
    checks: list[Check] = []

    # Zuerst über HTTP nachsehen, ob überhaupt jemand da ist. Vorher begann
    # diese Prüfung mit der Statusabfrage — und die läuft über den TCP-Kanal.
    # War der zu, meldete der Selbsttest „Board noch nicht da" und hörte auf,
    # obwohl ein angeschlossenes Board bereitwillig antwortete. Genau das
    # Werkzeug, das klären soll, warum nichts geht, beruhigte dann.
    try:
        listing = client.list_files("/")
    except FluidNCError as exc:
        return [
            Check(
                "Board erreichbar",
                SKIP,
                f"{host}: {exc}",
                "Solange nichts an der Wand hängt, ist das in Ordnung. "
                "Sonst: IP prüfen, --host angeben",
            )
        ]
    checks.append(Check("Board erreichbar", OK, f"{host} antwortet über HTTP"))
    checks.append(Check("SD-Karte", OK, f"lesbar, {len(listing)} Zeichen Antwort"))

    try:
        machine = client.status()
    except FluidNCError as exc:
        checks.append(
            Check(
                "Kommandokanal",
                FAIL,
                f"Port {client.config.telnet_port}: {exc}",
                "Ohne den Kanal gehen weder Status noch GCode noch Jog. "
                "Im FluidNC-Terminal einschalten: $Telnet/Enable=ON, dann $Bye",
            )
        )
        return checks

    checks.append(
        Check("Kommandokanal", OK, f"Port {client.config.telnet_port}, Zustand {machine.state}")
    )
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
    return checks


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
        ("Firmware", check_firmware_config(firmware, locations)),
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
