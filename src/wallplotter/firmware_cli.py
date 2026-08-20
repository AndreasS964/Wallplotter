"""CLI für die ``config.yaml`` des Boards — erzeugen, prüfen, übertragen.

.. code-block:: console

    wallplotter-firmware config --location Keller --out config/fluidnc-wallplotter.yaml
    wallplotter-firmware pruefen config/fluidnc-wallplotter.yaml
    wallplotter-firmware pruefen --host wandplotter.local
    wallplotter-firmware diff config/fluidnc-wallplotter.yaml
    wallplotter-firmware push --host wandplotter.local --location Keller

``config`` erzeugt die Datei aus derselben Beschreibung, mit der auch die
Vorschau rechnet — Ankermaße aus dem Standort, Schrittweite aus Pulley und
Mikroschritten, Servofenster aus der PWM-Frequenz.

``pruefen`` hält eine beliebige ``config.yaml`` gegen die Schlüsselliste aus
dem FluidNC-Quelltext. Mit ``--host`` holt es sie vorher vom Board — das ist
der schnellste Weg, herauszufinden, warum ein Board in ConfigAlarm steht.

``diff`` stellt die Datei auf der Platte dem gegenüber, was der Erzeuger heute
schreiben würde — der Weg, um eine von Hand hineingeratene Änderung zu finden.

``push`` schreibt sie in den **Flash** des Boards (nicht auf die SD-Karte,
dort liest FluidNC sie nicht) und startet neu. Die alte Fassung wird vorher
gesichert, außer man sagt ausdrücklich, dass man das nicht will.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import sys
from pathlib import Path

from .firmware import (
    BOARDS,
    FirmwareConfig,
    FirmwareError,
    LaserSpindle,
    ServoSpindle,
    board_by_name,
)
from .fluidnc_schema import ERROR, INFO, SOURCE_REVISION, WARN, check_lines, check_mapping
from .timing import MotionLimits

DEFAULT_TARGET = Path("config/fluidnc-wallplotter.yaml")
REMOTE_PATH = "/config.yaml"

_MARK = {ERROR: "✗", WARN: "!", INFO: "–"}


# ---------------------------------------------------------------------------
# Argumente
# ---------------------------------------------------------------------------


def _machine_options(parser: argparse.ArgumentParser) -> None:
    """Alles, was die Maschine beschreibt. Für jeden Unterbefehl dasselbe."""
    from .location import DEFAULT_PATH  # noqa: PLC0415

    group = parser.add_argument_group("Maschine")
    group.add_argument(
        "--location",
        metavar="NAME",
        help="Ankermaße aus diesem Standort übernehmen (ohne Angabe: der aktive)",
    )
    group.add_argument(
        "--standorte", type=Path, default=DEFAULT_PATH, help="Standortdatei"
    )
    group.add_argument(
        "--kein-standort",
        action="store_true",
        help="die Beispielanker nehmen, auch wenn ein Standort vorliegt",
    )
    group.add_argument("--board", default="rodent", choices=sorted(BOARDS))
    group.add_argument("--name", help="Maschinenname im Kopf der Datei")
    group.add_argument("--meta", help="Freitext im Kopf der Datei")
    group.add_argument("--microsteps", type=int, help="Mikroschritte der Treiber")
    group.add_argument("--run-amps", type=float, help="Motorstrom in Ampere")
    group.add_argument("--hold-amps", type=float, help="Haltestrom in Ampere")
    group.add_argument("--segment-length", type=float, help="Zerlegung gerader Strecken in mm")
    group.add_argument("--max-rate", type=float, help="Höchstgeschwindigkeit in mm/min")
    group.add_argument("--acceleration", type=float, help="Beschleunigung in mm/s²")
    group.add_argument("--max-travel", type=float, help="max_travel_mm je Achse")
    group.add_argument("--idle-ms", type=int, help="Haltezeit der Motoren (255 = nie abschalten)")

    servo = parser.add_argument_group("Stiftservo")
    servo.add_argument("--servo-hz", type=int, help="PWM-Frequenz des Servos")
    servo.add_argument(
        "--servo-impuls",
        nargs=2,
        type=float,
        metavar=("MIN_MS", "MAX_MS"),
        help="Impulsfenster des Servos in Millisekunden (Vorgabe 1.0 2.0)",
    )

    laser = parser.add_argument_group("Laser")
    laser.add_argument("--laser", action="store_true", help="Laserspindel einschalten")
    laser.add_argument("--laser-pin", help="Ausgang des Lasers, z. B. gpio.15")
    laser.add_argument("--laser-hz", type=int, help="PWM-Frequenz des Lasers (1000…100000)")
    laser.add_argument("--laser-smax", type=int, help="S-Wert für volle Leistung")

    taster = parser.add_argument_group("Taster")
    taster.add_argument(
        "--ohne-taster",
        action="store_true",
        help="control-Block weglassen — dann gibt es keinen Halt",
    )
    taster.add_argument(
        "--taster-aktiv-high",
        action="store_true",
        help="Taster gelten als gedrückt, wenn der Eingang HIGH liest (kein :low)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallplotter-firmware",
        description="Die config.yaml des Boards erzeugen, prüfen und übertragen.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("config", help="config.yaml erzeugen")
    config.add_argument("--out", type=Path, help="in Datei schreiben statt auf die Konsole")
    config.add_argument(
        "--trotzdem",
        action="store_true",
        help="auch schreiben, wenn die Prüfung Fehler findet",
    )
    _machine_options(config)

    pruefen = sub.add_parser("pruefen", help="eine config.yaml gegen FluidNC halten")
    pruefen.add_argument("datei", nargs="?", type=Path, default=DEFAULT_TARGET)
    pruefen.add_argument("--host", help="stattdessen die Datei aus dem Flash des Boards holen")

    diff = sub.add_parser("diff", help="Datei gegen das halten, was der Erzeuger schreiben würde")
    diff.add_argument("datei", nargs="?", type=Path, default=DEFAULT_TARGET)
    _machine_options(diff)

    push = sub.add_parser("push", help="config.yaml in den Flash des Boards schreiben")
    push.add_argument("--host", required=True, help="Hostname oder IP des Boards")
    push.add_argument(
        "--sicherung",
        type=Path,
        help="wohin die bisherige Fassung gesichert wird (Vorgabe: config.yaml.bak)",
    )
    push.add_argument(
        "--ohne-sicherung",
        action="store_true",
        help="die bisherige Fassung nicht vorher herunterladen",
    )
    push.add_argument(
        "--kein-neustart",
        action="store_true",
        help="nicht neu starten — die neue Datei wird dann erst beim nächsten Start wirksam",
    )
    push.add_argument(
        "--trotzdem",
        action="store_true",
        help="auch übertragen, wenn die Prüfung Fehler findet",
    )
    _machine_options(push)

    return parser


# ---------------------------------------------------------------------------
# Konfiguration zusammenbauen
# ---------------------------------------------------------------------------


def config_from_args(args: argparse.Namespace) -> tuple[FirmwareConfig, list[str]]:
    """Die Konfiguration aus den Argumenten — samt Hinweisen für die Ausgabe."""
    from .location import LocationBook, LocationError  # noqa: PLC0415

    notes: list[str] = []
    config = FirmwareConfig(board=board_by_name(args.board))

    if not args.kein_standort:
        try:
            location = LocationBook.load(args.standorte).get(args.location)
        except LocationError as exc:
            if args.location:
                # Ausdrücklich verlangt und nicht da: das ist ein Fehler, kein Hinweis.
                raise
            notes.append(f"Kein Standort übernommen ({exc}) — es gelten die Beispielanker.")
        else:
            config = FirmwareConfig.from_location(location, config)
            notes.append(f"Ankermaße aus Standort {location.name}.")

    motor = config.motor
    if args.microsteps is not None:
        motor = dataclasses.replace(motor, microsteps=args.microsteps)

    limits = config.limits
    if args.max_rate is not None or args.acceleration is not None:
        limits = MotionLimits(
            acceleration_mm_s2=args.acceleration or limits.acceleration_mm_s2,
            max_rate_mm_min=args.max_rate or limits.max_rate_mm_min,
            junction_deviation_mm=limits.junction_deviation_mm,
        )

    servo = config.servo
    if args.servo_hz is not None or args.servo_impuls is not None:
        low, high = args.servo_impuls or (servo.min_pulse_ms, servo.max_pulse_ms)
        servo = ServoSpindle(
            pwm_hz=args.servo_hz or servo.pwm_hz,
            min_pulse_ms=low,
            max_pulse_ms=high,
            s_min=servo.s_min,
            s_max=servo.s_max,
            tool_num=servo.tool_num,
        )

    laser = config.laser
    if args.laser or args.laser_pin or args.laser_hz or args.laser_smax:
        laser = LaserSpindle(
            enabled=args.laser or laser.enabled,
            pwm_hz=args.laser_hz or laser.pwm_hz,
            s_max=args.laser_smax or laser.s_max,
            tool_num=laser.tool_num,
            output_pin=args.laser_pin or laser.output_pin,
            air_assist=laser.air_assist,
        )

    changes: dict[str, object] = {"motor": motor, "limits": limits, "servo": servo, "laser": laser}
    for option, field in (
        ("name", "name"),
        ("meta", "meta"),
        ("run_amps", "run_amps"),
        ("hold_amps", "hold_amps"),
        ("segment_length", "segment_length_mm"),
        ("max_travel", "max_travel_mm"),
        ("idle_ms", "idle_ms"),
    ):
        value = getattr(args, option, None)
        if value is not None:
            changes[field] = value
    if args.run_amps is not None and args.hold_amps is None:
        # Der Haltestrom folgt dem Laufstrom, solange niemand etwas anderes sagt:
        # die Gondel hängt am Riemen und darf im Stillstand nicht absacken.
        changes["hold_amps"] = args.run_amps
    if args.ohne_taster:
        changes["controls"] = False
    if args.taster_aktiv_high:
        changes["control_active_low"] = False

    return dataclasses.replace(config, **changes), notes


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------


def report(config: FirmwareConfig, text: str) -> tuple[int, list[str]]:
    """Alle drei Prüfungen zusammen.

    1. :meth:`FirmwareConfig.check` — ist die Maschine so beschrieben sinnvoll?
    2. :func:`check_lines` — wie liest FluidNCs eigener Tokenizer die Zeilen?
    3. :func:`check_mapping` — kennt FluidNC jeden Schlüssel?

    Nur die dritte braucht PyYAML. Fehlt es, fällt sie weg — und das steht dann
    auch da: eine Prüfung, die stillschweigend ausfällt, ist schlimmer als keine.

    Zurück kommen die Zahl der Fehler und die Zeilen für die Ausgabe.
    """
    lines: list[str] = []
    errors = 0

    for problem in config.check():
        lines.append(f"{_MARK[problem.level]} {problem.text}")
        errors += problem.level == ERROR

    for finding in check_lines(text):
        lines.append(f"{_MARK[finding.level]} {finding}")
        errors += finding.level == ERROR

    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        lines.append(
            f"{_MARK[INFO]} PyYAML fehlt — die Schlüssel sind nicht gegen FluidNC "
            "gehalten worden (pip install pyyaml)"
        )
        return errors, lines

    for finding in check_mapping(yaml.safe_load(text)):
        lines.append(f"{_MARK[finding.level]} {finding}")
        errors += finding.level == ERROR

    return errors, lines


def _print(lines: list[str], stream=None) -> None:
    """Hinweise auf die Fehlerausgabe, damit `--out -` nicht verschmutzt wird.

    ``sys.stderr`` wird hier bewusst erst beim Aufruf nachgeschlagen und nicht
    als Vorgabewert eingefroren — sonst schreibt die Funktion an einer
    umgeleiteten Ausgabe vorbei.
    """
    for line in lines:
        print(line, file=stream or sys.stderr)


# ---------------------------------------------------------------------------
# Unterbefehle
# ---------------------------------------------------------------------------


def cmd_config(args: argparse.Namespace) -> int:
    config, notes = config_from_args(args)
    text = config.render()
    errors, lines = report(config, text)

    _print([f"# {note}" for note in notes] + lines)
    if errors and not args.trotzdem:
        print(
            f"{errors} Fehler — nichts geschrieben. Mit --trotzdem trotzdem schreiben.",
            file=sys.stderr,
        )
        return 2

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Geschrieben: {args.out} ({len(text.splitlines())} Zeilen)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_pruefen(args: argparse.Namespace) -> int:
    if args.host:
        from .config import FluidNCConfig  # noqa: PLC0415
        from .upload import FluidNCClient, FluidNCError  # noqa: PLC0415

        try:
            text = FluidNCClient(FluidNCConfig(host=args.host)).download_local(REMOTE_PATH)
        except FluidNCError as exc:
            print(str(exc), file=sys.stderr)
            return 5
        quelle = f"{args.host}{REMOTE_PATH}"
    else:
        if not args.datei.exists():
            print(f"{args.datei} gibt es nicht.", file=sys.stderr)
            return 4
        text = args.datei.read_text(encoding="utf-8")
        quelle = str(args.datei)

    # Zuerst die Sicht des Tokenizers: die braucht keinen YAML-Parser und findet
    # genau das, was ein YAML-Parser übersieht — etwa den Kommentar hinter einem
    # Wert, den FluidNC nicht abschneidet.
    findings = list(check_lines(text))

    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        print(
            "PyYAML fehlt — geprüft wurde nur, wie FluidNCs Tokenizer die Zeilen liest, "
            "nicht ob es die Schlüssel kennt (pip install pyyaml).",
            file=sys.stderr,
        )
    else:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            print(f"{quelle} ist kein gültiges YAML: {exc}", file=sys.stderr)
            return 4
        findings += check_mapping(data)

    for finding in findings:
        print(f"{_MARK[finding.level]} {finding}")

    errors = sum(f.level == ERROR for f in findings)
    print()
    print(f"Geprüft gegen {SOURCE_REVISION}.")
    if errors:
        print(f"{quelle}: {errors} Fehler — mit dieser Datei fährt das Board nicht.")
        return 1
    if findings:
        print(f"{quelle}: nichts Tödliches; die Hinweise oben ansehen.")
    else:
        print(f"{quelle}: FluidNC kennt jeden Schlüssel und liest jede Zeile so, wie sie gemeint ist.")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    config, notes = config_from_args(args)
    neu = config.render()
    _print([f"# {note}" for note in notes])

    alt = args.datei.read_text(encoding="utf-8") if args.datei.exists() else ""
    if alt == neu:
        print(f"{args.datei} ist auf dem Stand des Erzeugers.")
        return 0

    sys.stdout.writelines(
        difflib.unified_diff(
            alt.splitlines(keepends=True),
            neu.splitlines(keepends=True),
            fromfile=str(args.datei),
            tofile="wallplotter-firmware config",
        )
    )
    print(
        f"\n{args.datei} weicht ab — mit `wallplotter-firmware config --out {args.datei}` "
        "neu erzeugen.",
        file=sys.stderr,
    )
    return 1


def cmd_push(args: argparse.Namespace) -> int:
    from .config import FluidNCConfig  # noqa: PLC0415
    from .upload import FluidNCClient, FluidNCError  # noqa: PLC0415

    config, notes = config_from_args(args)
    text = config.render()
    errors, lines = report(config, text)
    _print([f"# {note}" for note in notes] + lines)
    if errors and not args.trotzdem:
        print(
            f"{errors} Fehler — nichts übertragen. Ein Board mit einer solchen Datei "
            "steht in ConfigAlarm und lässt sich nur über die serielle Schnittstelle "
            "wieder einfangen. Mit --trotzdem trotzdem übertragen.",
            file=sys.stderr,
        )
        return 2

    client = FluidNCClient(FluidNCConfig(host=args.host))

    if not args.ohne_sicherung:
        ziel = args.sicherung or Path("config.yaml.bak")
        try:
            ziel.write_text(client.download_local(REMOTE_PATH), encoding="utf-8")
            print(f"Bisherige Fassung gesichert: {ziel}")
        except FluidNCError as exc:
            # Kein Abbruch: Auf einem frisch geflashten Board gibt es noch keine.
            print(f"Keine Sicherung möglich ({exc}) — weiter.", file=sys.stderr)

    try:
        pfad = client.upload_local(text, REMOTE_PATH)
        print(f"Geschrieben: {args.host}{pfad} ({len(text.encode('utf-8'))} Bytes)")
        if args.kein_neustart:
            print("Kein Neustart — die Datei wird erst beim nächsten Einschalten gelesen.")
        else:
            print(client.restart())
    except FluidNCError as exc:
        print(str(exc), file=sys.stderr)
        return 5
    finally:
        client.close()

    print()
    print("Danach am Board nachsehen:")
    print("  1. Log auf »[MSG:ERR: Ignored key …]« prüfen — das hieße ConfigAlarm.")
    print("  2. Die Gondel hängt jetzt an einem neuen Nullpunkt: FluidNC friert die")
    print("     Riemenlängen für (0,0) beim Start ein. Vor dem ersten Plot einmessen.")
    print(f"  3. Gegenprobe: wallplotter-firmware pruefen --host {args.host}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from .location import LocationError  # noqa: PLC0415

    args = build_parser().parse_args(argv)
    handlers = {
        "config": cmd_config,
        "pruefen": cmd_pruefen,
        "diff": cmd_diff,
        "push": cmd_push,
    }
    try:
        return handlers[args.command](args)
    except (FirmwareError, LocationError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ImportError as exc:
        print(f"Dafür fehlt ein Paket: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
