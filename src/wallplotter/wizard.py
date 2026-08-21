"""Geführte Einrichtung: von der leeren Wand bis zum ersten Strich.

Alles, was dieser Wizard tut, geht auch einzeln — `wallplotter-location`,
`wallplotter-firmware`, `wallplotter-calibrate`, `plot`. Nur muss man dafür
wissen, **in welcher Reihenfolge**, und genau daran scheitert es. Drei Stellen
verzeihen eine falsche Reihenfolge nicht:

* **Der Nullpunkt entsteht beim Einschalten, nicht durch ein Kommando.**
  FluidNC friert die Riemenlängen für kartesisch (0,0) in ``WallPlotter::init()``
  ein. Wer erst joggt und dann nullt, misst Ankermaße, die um die Jog-Strecke
  danebenliegen — und bekommt eine verzerrte Zeichnung ohne jede Fehlermeldung.
* **Die Ankermaße müssen vor dem Einmessen der Fläche im Board stehen.** Sonst
  fährt die Kalibrierung in einem anderen Koordinatensystem als der spätere
  Plot.
* **Die Servowerte lassen sich erst nach dem Einmessen ermitteln**, weil das
  Testmuster eine Fläche braucht.

Der Wizard kennt diese Reihenfolge, prüft nach jedem Schritt nach, und lässt
sich jederzeit abbrechen und fortsetzen: Jeder Schritt sagt selbst, ob er schon
erledigt ist.

Die Schritte wissen nichts über die Oberfläche, über die sie fragen — das läuft
über :class:`Dialog`. So kann derselbe Ablauf im Terminal laufen
(:mod:`wallplotter.wizard_cli`), später in der Web-UI, und im Test gegen ein
Skript aus vorbereiteten Antworten. Ein Wizard, der nur mit einem echten
Menschen davor prüfbar ist, wird nicht geprüft.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from .calibration import CORNERS
from .config import FluidNCConfig
from .location import DEFAULT_PATH as LOCATION_PATH
from .location import Location, LocationBook, LocationError, ServoSettings

__all__ = [
    "Abbruch",
    "Dialog",
    "Kontext",
    "Schritt",
    "SCHRITTE",
    "OFFEN",
    "ERLEDIGT",
    "UNBEKANNT",
    "lauf",
    "schritt_nach_name",
]

OFFEN = "offen"
ERLEDIGT = "erledigt"
UNBEKANNT = "nicht prüfbar"
"""Zustände eines Schrittes. ``UNBEKANNT`` ist kein Ausweichen: Ob die Gondel am
Anschlag hängt, kann keine Software wissen — dann wird gefragt statt geraten."""

ECKEN_NAMEN = {
    "bottom-left": "unten links",
    "bottom-right": "unten rechts",
    "top-right": "oben rechts",
    "top-left": "oben links",
}


class Abbruch(Exception):
    """Der Mensch möchte aufhören. Kein Fehler, ein Ergebnis."""


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------


class Dialog(Protocol):
    """Wie der Wizard mit dem Menschen redet.

    Absichtlich klein gehalten: Was hier nicht drinsteht, kann ein Schritt auch
    nicht verlangen — und bleibt damit auch in einer Oberfläche baubar, die
    kein Terminal ist.
    """

    def sagen(self, text: str) -> None:
        """Etwas mitteilen, worauf keine Antwort nötig ist."""

    def warnen(self, text: str) -> None:
        """Etwas mitteilen, das schiefgehen kann."""

    def frage(self, text: str, vorgabe: str = "") -> str:
        """Freitext erfragen."""

    def zahl(self, text: str, vorgabe: float | None = None, einheit: str = "mm") -> float:
        """Eine Zahl erfragen."""

    def ja(self, text: str, vorgabe: bool = True) -> bool:
        """Ja/Nein."""

    def auswahl(self, text: str, optionen: Sequence[tuple[str, str]]) -> str:
        """Eine von mehreren Möglichkeiten; ``optionen`` sind (Schlüssel, Text)."""

    def weiter(self, text: str) -> None:
        """Warten, bis der Mensch etwas an der Maschine erledigt hat."""


# ---------------------------------------------------------------------------
# Kontext
# ---------------------------------------------------------------------------


@dataclass
class Kontext:
    """Was der Wizard über den Aufbau weiß und wo er es ablegt."""

    host: str = "fluidnc.local"
    standorte: Path = LOCATION_PATH
    firmware_datei: Path = Path("config/fluidnc-wallplotter.yaml")
    arbeitsverzeichnis: Path = Path(".")

    client_factory: Callable[[FluidNCConfig], Any] | None = None
    """Nur für Tests: liefert einen Client statt eines echten
    :class:`~wallplotter.upload.FluidNCClient`."""

    board_erreichbar: bool | None = None
    """``None`` = noch nicht versucht. Einmal ``False``, überspringen die
    Board-Schritte von selbst, statt in jeden Timeout einzeln zu laufen."""

    notizen: list[str] = field(default_factory=list)
    """Was am Ende noch zu tun bleibt."""

    def buch(self) -> LocationBook:
        return LocationBook.load(self.standorte)

    def standort(self) -> Location | None:
        try:
            return self.buch().get()
        except LocationError:
            return None

    def client(self):
        """Verbindung zum Board — jedes Mal frisch, damit nichts hängen bleibt."""
        config = FluidNCConfig(host=self.host)
        if self.client_factory is not None:
            return self.client_factory(config)
        from .upload import FluidNCClient  # noqa: PLC0415

        return FluidNCClient(config)


# ---------------------------------------------------------------------------
# Schritte
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Schritt:
    """Ein Abschnitt der Einrichtung.

    ``warum`` ist kein Schmuck: Der halbe Nutzen eines geführten Ablaufs steckt
    darin, dass an Ort und Stelle steht, warum dieser Schritt hier kommt und
    nicht später.
    """

    key: str
    titel: str
    warum: str
    braucht_board: bool
    _zustand: Callable[[Kontext], str]
    _ausfuehren: Callable[[Kontext, Dialog], bool]

    def zustand(self, ctx: Kontext) -> str:
        try:
            return self._zustand(ctx)
        except Exception:  # noqa: BLE001 - ein kaputter Zustand ist „offen"
            return OFFEN

    def ausfuehren(self, ctx: Kontext, dialog: Dialog) -> bool:
        return self._ausfuehren(ctx, dialog)


# -- 1. Installation --------------------------------------------------------


def _zustand_installation(ctx: Kontext) -> str:
    from .doctor import FAIL, check_core, check_python  # noqa: PLC0415

    checks = [check_python(), *check_core()]
    return OFFEN if any(c.status == FAIL for c in checks) else ERLEDIGT


def _tue_installation(ctx: Kontext, dialog: Dialog) -> bool:
    from .doctor import FAIL, OK, check_core, check_extras, check_python  # noqa: PLC0415

    kern = [check_python(), *check_core()]
    for check in kern:
        (dialog.warnen if check.status == FAIL else dialog.sagen)(str(check))
    if any(c.status == FAIL for c in kern):
        dialog.warnen(
            "Der Kern läuft noch nicht. Erst `pip install -e \".[geometry,web,photo,dev]\"`, "
            "dann hier weiter."
        )
        return False

    fehlend = [c for c in check_extras() if c.status != OK]
    if fehlend:
        dialog.sagen("")
        dialog.sagen("Optionale Zweige, die noch fehlen — jeder schaltet genau eine Fähigkeit ab:")
        for check in fehlend:
            dialog.sagen(f"  {check}")
    return True


# -- 2. Board ---------------------------------------------------------------


def _zustand_board(ctx: Kontext) -> str:
    return ERLEDIGT if ctx.board_erreichbar else UNBEKANNT


def _tue_board(ctx: Kontext, dialog: Dialog) -> bool:
    ctx.host = dialog.frage("Hostname oder IP des Boards", ctx.host) or ctx.host

    from .upload import FluidNCError  # noqa: PLC0415

    client = ctx.client()
    try:
        status = client.status()
    except FluidNCError as exc:
        dialog.warnen(f"Keine Verbindung: {exc}")
        dialog.sagen("")
        dialog.sagen("Der Reihe nach prüfen:")
        dialog.sagen(f"  1. Antwortet {ctx.host} überhaupt? (ping, oder die IP im Router nachsehen)")
        dialog.sagen("  2. Steht das WLAN? Nach dem Flashen einmal $Sta/SSID und $Sta/Password setzen.")
        dialog.sagen("  3. Ist der Telnet-Kanal an? `$Telnet/Enable=ON`, Port 23.")
        ctx.board_erreichbar = False
        if dialog.ja("Ohne Board weitermachen? Die Vorbereitung geht auch offline.", True):
            ctx.notizen.append("Board war nicht erreichbar — die Board-Schritte fehlen noch.")
            return True
        raise Abbruch("Board nicht erreichbar") from exc
    finally:
        client.close()

    ctx.board_erreichbar = True
    dialog.sagen(f"Board antwortet. Zustand: {status.state}, Position {status.position}")
    if status.state.lower().startswith("alarm"):
        dialog.warnen(
            "Das Board steht in Alarm. Häufigster Grund direkt nach dem Flashen: ein "
            "Fehler in der config.yaml. Der nächste Schritt kümmert sich darum; "
            "`wallplotter-firmware pruefen --host <ip>` sagt genau, welcher Schlüssel es ist."
        )
    return True


# -- 3. Standort ------------------------------------------------------------


def _zustand_standort(ctx: Kontext) -> str:
    return ERLEDIGT if ctx.standort() is not None else OFFEN


def _tue_standort(ctx: Kontext, dialog: Dialog) -> bool:
    vorhanden = ctx.standort()
    if vorhanden is not None and not dialog.ja(
        f"Standort {vorhanden.name!r} ist schon angelegt. Neu vermessen?", False
    ):
        return True

    dialog.sagen("")
    dialog.sagen("Drei Maße mit dem Zollstock — aber erst in dieser Reihenfolge:")
    dialog.sagen("")
    dialog.sagen("  1. Gondel an den Referenzpunkt hängen (oberer Anschlag).")
    dialog.sagen("  2. DORT das Board neu starten.")
    dialog.sagen("  3. Erst dann messen.")
    dialog.sagen("")
    dialog.sagen(
        "Der Neustart ist der Punkt, an dem es sonst schiefgeht: FluidNC friert die "
        "Riemenlängen für den Nullpunkt beim Start ein (WallPlotter::init). Wer erst "
        "joggt und dann nullt, bekommt Ankermaße, die um die Jog-Strecke danebenliegen — "
        "und eine verzerrte Zeichnung, ohne dass irgendwo eine Meldung erscheint."
    )
    dialog.weiter("Gondel hängt am Referenzpunkt, Board dort neu gestartet?")

    name = dialog.frage("Name für diesen Standort", vorhanden.name if vorhanden else "Keller")
    while True:
        span = dialog.zahl("Abstand der beiden Umlenkpunkte zueinander")
        links = dialog.zahl("Linke Riemenlänge, Umlenkpunkt bis Gondel")
        rechts = dialog.zahl("Rechte Riemenlänge, Umlenkpunkt bis Gondel")
        try:
            standort = Location(
                name=name,
                anchor_span_mm=span,
                left_belt_zero_mm=links,
                right_belt_zero_mm=rechts,
                servo=vorhanden.servo if vorhanden else None,
            )
        except LocationError as exc:
            dialog.warnen(str(exc))
            if not dialog.ja("Noch einmal messen?", True):
                raise Abbruch("Maße ergeben kein Dreieck") from exc
            continue
        break

    buch = ctx.buch()
    buch.add(standort)
    buch.save(ctx.standorte)

    dialog.sagen("")
    for zeile in standort.report().splitlines():
        dialog.sagen(zeile)

    urteil = standort.analysis(samples=11) if standort.calibration.complete else None
    if urteil is None:
        # Ohne Fläche lässt sich die Geometrie nur grob abschätzen — über die
        # Riemenlängen selbst, die ja schon eine Aussage über den Aufbau sind.
        anker = standort.anchors()
        if anker.y < span * 0.25:
            dialog.warnen(
                f"Die Anker hängen nur {anker.y:.0f} mm über dem Nullpunkt, bei "
                f"{span:.0f} mm Spannweite. Das wird oben flach: Die Riemen laufen dort "
                "fast in eine Linie, die Höhe ist schlecht bestimmt und der Zug steigt. "
                "Höher aufhängen hilft mehr als weiter spreizen."
            )
    return True


# -- 4. Firmware ------------------------------------------------------------


def _zustand_firmware(ctx: Kontext) -> str:
    from .firmware import FirmwareConfig  # noqa: PLC0415

    standort = ctx.standort()
    if standort is None:
        return OFFEN
    if not ctx.firmware_datei.exists():
        return OFFEN
    erwartet = FirmwareConfig.from_location(standort).render()
    if ctx.firmware_datei.read_text(encoding="utf-8") != erwartet:
        return OFFEN
    # Die Datei stimmt — ob sie auch auf dem Board liegt, weiß nur das Board.
    return UNBEKANNT if ctx.board_erreichbar else ERLEDIGT


def _tue_firmware(ctx: Kontext, dialog: Dialog) -> bool:
    from .firmware import FirmwareConfig  # noqa: PLC0415
    from .firmware_cli import report  # noqa: PLC0415

    standort = ctx.standort()
    if standort is None:
        dialog.warnen("Ohne Standort keine Ankermaße — erst den Schritt davor.")
        return False

    config = FirmwareConfig.from_location(standort)
    text = config.render()
    fehler, zeilen = report(config, text)
    for zeile in zeilen:
        dialog.warnen(zeile)
    if fehler:
        dialog.warnen(
            f"{fehler} Fehler in der erzeugten Datei — die geht so nicht auf ein Board."
        )
        return False

    ctx.firmware_datei.parent.mkdir(parents=True, exist_ok=True)
    ctx.firmware_datei.write_text(text, encoding="utf-8")
    dialog.sagen(f"Geschrieben: {ctx.firmware_datei} ({len(text.splitlines())} Zeilen)")
    dialog.sagen(f"Ankermaße aus Standort {standort.name}, Schrittweite und Servofenster gerechnet.")

    if not ctx.board_erreichbar:
        ctx.notizen.append(
            f"config.yaml liegt in {ctx.firmware_datei}, ist aber noch nicht auf dem Board: "
            f"wallplotter-firmware push --host <ip> --location {standort.name}"
        )
        return True

    if not dialog.ja("Jetzt auf das Board schreiben und neu starten?", True):
        ctx.notizen.append(
            f"config.yaml noch nicht übertragen: wallplotter-firmware push --host {ctx.host}"
        )
        return True

    from .upload import FluidNCError  # noqa: PLC0415

    client = ctx.client()
    try:
        sicherung = ctx.arbeitsverzeichnis / "config.yaml.bak"
        try:
            sicherung.write_text(client.download_local("/config.yaml"), encoding="utf-8")
            dialog.sagen(f"Bisherige Fassung gesichert: {sicherung}")
        except FluidNCError:
            dialog.sagen("Keine bisherige Fassung zum Sichern — frisch geflashtes Board?")
        client.upload_local(text, "/config.yaml")
        dialog.sagen("In den Flash geschrieben (nicht auf die SD-Karte — dort liest FluidNC nichts).")
        dialog.sagen(client.restart())
    except FluidNCError as exc:
        dialog.warnen(f"Übertragen fehlgeschlagen: {exc}")
        return False
    finally:
        client.close()

    dialog.weiter("Board neu gestartet? Im Log darf kein »Ignored key« stehen.")
    return True


# -- 5. Nullpunkt -----------------------------------------------------------


def _zustand_nullpunkt(ctx: Kontext) -> str:
    return UNBEKANNT


def _tue_nullpunkt(ctx: Kontext, dialog: Dialog) -> bool:
    dialog.sagen("")
    dialog.sagen(
        "Jetzt entsteht der Maschinennullpunkt — und zwar mechanisch, nicht per Kommando. "
        "$H gibt es hier nicht: WallPlotter::canHome() liefert false, mit Endschalter wie ohne."
    )
    dialog.sagen("")
    dialog.sagen("  1. Gondel an den Referenzpunkt (denselben wie beim Messen).")
    dialog.sagen("  2. Board DORT neu starten.")
    dialog.sagen("")
    dialog.sagen(
        "Nach dem Neustart ist die Gondelposition exakt (0,0). Ein G92 danach verschiebt "
        "nur das Werkstücksystem und lässt die Kinematik unberührt — es ersetzt den "
        "Neustart also nicht."
    )
    dialog.weiter("Gondel am Referenzpunkt, Board dort neu gestartet?")

    if not ctx.board_erreichbar:
        ctx.notizen.append("Nullpunkt nicht gegengeprüft — kein Board erreichbar.")
        return True

    from .upload import FluidNCError  # noqa: PLC0415

    client = ctx.client()
    try:
        status = client.status()
    except FluidNCError as exc:
        dialog.warnen(f"Status nicht lesbar: {exc}")
        return False
    finally:
        client.close()

    x, y = status.machine_position[:2] if status.machine_position else (0.0, 0.0)
    dialog.sagen(f"Zustand {status.state}, Maschinenposition {x:.1f} / {y:.1f} mm")
    if max(abs(x), abs(y)) > 1.0:
        dialog.warnen(
            f"Die Maschinenposition steht auf {x:.1f}/{y:.1f} statt auf 0/0. Dann ist das "
            "Board nicht an dieser Stelle neu gestartet worden, sondern vorher — und die "
            "Ankermaße passen nicht zu dem, was die Firmware rechnet."
        )
        return dialog.ja("Trotzdem weiter?", False)
    return True


# -- 6. Fläche --------------------------------------------------------------


def _zustand_flaeche(ctx: Kontext) -> str:
    standort = ctx.standort()
    if standort is None:
        return OFFEN
    return ERLEDIGT if standort.calibration.complete else OFFEN


def _tue_flaeche(ctx: Kontext, dialog: Dialog) -> bool:
    standort = ctx.standort()
    if standort is None:
        dialog.warnen("Ohne Standort keine Fläche — erst den Schritt davor.")
        return False
    if not ctx.board_erreichbar:
        dialog.warnen("Zum Einmessen muss die Gondel fahren — dafür braucht es das Board.")
        ctx.notizen.append(
            "Fläche noch nicht eingemessen: wallplotter-calibrate --host <ip> record <ecke>"
        )
        return True

    dialog.sagen("")
    dialog.sagen(
        "Jetzt die Fläche abstecken: In jede Ecke fahren und die Position festhalten. "
        "Vier Ecken sind besser als zwei — nur dann fällt eine schiefe Aufhängung auf. "
        "Zwei diagonale reichen zur Not."
    )
    dialog.sagen(
        "Das Ergebnis ist bewusst das größte Rechteck INNERHALB der angefahrenen Punkte: "
        "lieber etwas kleiner als neben der Wand."
    )

    from .upload import FluidNCError  # noqa: PLC0415

    client = ctx.client()
    aufgenommen = dict(standort.calibration.points)
    fehler: FluidNCError | None = None
    try:
        for ecke in CORNERS:
            name = ECKEN_NAMEN[ecke]
            if ecke in aufgenommen and not dialog.ja(f"{name} ist schon aufgenommen. Neu?", False):
                continue
            wahl = dialog.auswahl(
                f"Ecke {name}",
                [
                    ("fahren", "jetzt hinfahren (Jog) und aufnehmen"),
                    ("hier", "Gondel steht schon dort — aufnehmen"),
                    ("weiter", "diese Ecke auslassen"),
                    ("ende", "Einmessen beenden"),
                ],
            )
            if wahl == "ende":
                break
            if wahl == "weiter":
                continue
            if wahl == "fahren":
                while True:
                    dx = dialog.zahl("Verfahren in X (negativ = links)", 0.0)
                    dy = dialog.zahl("Verfahren in Y (negativ = runter)", 0.0)
                    if dx or dy:
                        client.jog(dx, dy)
                    x, y = client.position()
                    dialog.sagen(f"  Position {x:.1f} / {y:.1f} mm")
                    if dialog.ja(f"Steht die Gondel jetzt {name}?", True):
                        break
            x, y = client.position()
            aufgenommen[ecke] = (x, y)
            dialog.sagen(f"  {name} aufgenommen: {x:.1f} / {y:.1f} mm")
    except FluidNCError as exc:
        # Nicht sofort zurückspringen: Was bis hierhin erfolgreich
        # aufgenommen wurde, soll trotzdem gespeichert werden — sonst kostet
        # ein Verbindungsabbruch bei der dritten Ecke auch die ersten beiden.
        fehler = exc
    finally:
        client.close()

    standort = replace(
        standort, calibration=replace(standort.calibration, points=aufgenommen)
    )
    buch = ctx.buch()
    buch.add(standort)
    buch.save(ctx.standorte)

    if fehler is not None:
        dialog.warnen(f"Verbindung verloren: {fehler}")
        return False

    if not standort.calibration.complete:
        fehlen = ", ".join(ECKEN_NAMEN[e] for e in standort.calibration.missing)
        dialog.warnen(f"Noch nicht vollständig — es fehlen: {fehlen}")
        return False

    dialog.sagen("")
    for zeile in standort.calibration.summary().splitlines():
        dialog.sagen(zeile)
    for hinweis in standort.analysis().verdict(standort.kinematics().motor):
        dialog.sagen(f"  {hinweis}")
    return True


# -- 7. Servo ---------------------------------------------------------------


def _zustand_servo(ctx: Kontext) -> str:
    standort = ctx.standort()
    if standort is None:
        return OFFEN
    return ERLEDIGT if standort.servo is not None else OFFEN


def _tue_servo(ctx: Kontext, dialog: Dialog) -> bool:
    standort = ctx.standort()
    if standort is None or not standort.calibration.complete:
        dialog.warnen("Der Stifttest braucht eine eingemessene Fläche — erst den Schritt davor.")
        return False

    from .gcode import lines_to_gcode  # noqa: PLC0415
    from .patterns import build  # noqa: PLC0415
    from .toolhead import PenToolhead  # noqa: PLC0415

    dialog.sagen("")
    dialog.sagen(
        "Die Werte im Stiftkatalog sind Schätzungen, keine Messwerte: Servohebel, "
        "Federweg und Stifthalter sind an jedem Aufbau anders. Also einmal messen."
    )
    dialog.sagen("")
    dialog.sagen("S ist dabei kein Winkel, sondern Prozent des Servowegs:")
    dialog.sagen("  S0 = 1,0 ms Impuls, S50 = 1,5 ms, S100 = 2,0 ms.")

    start = standort.servo or ServoSettings(down_value=30, up_value=0, dwell_s=0.25)
    while True:
        unten = int(dialog.zahl("S-Wert für Stift AUF der Wand", start.down_value, "S"))
        oben = int(dialog.zahl("S-Wert für Stift ABGEHOBEN", start.up_value, "S"))
        warten = dialog.zahl("Wartezeit nach jedem Hub", start.dwell_s, "s")
        try:
            servo = ServoSettings(down_value=unten, up_value=oben, dwell_s=warten)
        except LocationError as exc:
            dialog.warnen(str(exc))
            continue

        basis = standort.plot_config()
        config = replace(
            basis,
            toolhead=PenToolhead(down_value=unten, up_value=oben, dwell_s=warten),
        )
        muster = build("pen-test", config.width_mm, config.height_mm, config.margin_mm)
        gcode = lines_to_gcode(
            muster.lines,
            replace(config, invert_y=False),
            fit=False,
            header_comment=muster.description,
        )
        ziel = ctx.arbeitsverzeichnis / "stifttest.gcode"
        ziel.write_text(gcode, encoding="utf-8")
        dialog.sagen(f"Testmuster geschrieben: {ziel}")

        if ctx.board_erreichbar and dialog.ja("Jetzt plotten?", True):
            from .upload import FluidNCError, upload_and_run  # noqa: PLC0415

            client = ctx.client()
            try:
                upload_and_run(gcode, "stifttest.gcode", client=client)
                dialog.weiter("Läuft. Enter, wenn das Muster fertig ist.")
            except FluidNCError as exc:
                dialog.warnen(f"Start fehlgeschlagen: {exc}")
            finally:
                client.close()
        elif not ctx.board_erreichbar:
            ctx.notizen.append(f"Stifttest noch nicht geplottet: {ziel}")

        dialog.sagen("")
        dialog.sagen("Was das Muster verrät:")
        dialog.sagen("  * Strichanfänge fehlen        → Wartezeit zu kurz")
        dialog.sagen("  * Enden ausgefranst, Papier   → down_value zu hoch (Stift drückt)")
        dialog.sagen("  * Striche zu blass oder lückig → down_value zu niedrig")
        dialog.sagen("  * Servo brummt dauerhaft      → mechanischer Anschlag, Hebel umsetzen")

        if dialog.ja("Passt das so?", True):
            buch = ctx.buch()
            buch.add(replace(standort, servo=servo))
            buch.save(ctx.standorte)
            dialog.sagen(f"Gespeichert im Standort {standort.name}.")
            dialog.sagen(f"Für plot:  {servo.as_plot_options()}")
            return True
        start = servo


# -- 8. Erster Plot ---------------------------------------------------------


def _zustand_probe(ctx: Kontext) -> str:
    return UNBEKANNT


def _tue_probe(ctx: Kontext, dialog: Dialog) -> bool:
    standort = ctx.standort()
    if standort is None or not standort.calibration.complete:
        dialog.warnen("Erst die Fläche einmessen.")
        return False

    from .gcode import lines_to_gcode, prepare_geometry, stats_for  # noqa: PLC0415
    from .patterns import build  # noqa: PLC0415

    config = standort.plot_config()
    if standort.servo:
        from .toolhead import PenToolhead  # noqa: PLC0415

        config = replace(
            config,
            toolhead=PenToolhead(
                down_value=standort.servo.down_value,
                up_value=standort.servo.up_value,
                dwell_s=standort.servo.dwell_s,
            ),
        )

    muster = build("frame", config.width_mm, config.height_mm, config.margin_mm)
    gcode = lines_to_gcode(
        muster.lines,
        replace(config, invert_y=False),
        fit=False,
        header_comment=muster.description,
    )
    ziel = ctx.arbeitsverzeichnis / "rahmen.gcode"
    ziel.write_text(gcode, encoding="utf-8")

    dialog.sagen("")
    dialog.sagen(
        f"Der Rahmen ist der erste ehrliche Test: Er fährt die Grenzen der eingemessenen "
        f"Fläche ab ({config.drawable_width_mm:.0f} × {config.drawable_height_mm:.0f} mm). "
        "Was hier danebenliegt, liegt bei jedem Bild daneben."
    )
    geometrie = prepare_geometry(muster.lines, replace(config, invert_y=False), fit=False)
    dialog.sagen(f"Geschätzt: {stats_for(geometrie, config)}")
    dialog.sagen(f"Geschrieben: {ziel}")

    if not ctx.board_erreichbar:
        ctx.notizen.append(f"Rahmen noch nicht geplottet: {ziel}")
        return True
    if not dialog.ja("Jetzt plotten? Niemand unter der Gondel.", True):
        ctx.notizen.append(f"Rahmen liegt bereit: {ziel}")
        return True

    from .upload import FluidNCError, upload_and_run  # noqa: PLC0415

    client = ctx.client()
    try:
        upload_and_run(gcode, "rahmen.gcode", client=client)
        dialog.sagen("Läuft. Der Not-Halt-Taster ist ab jetzt der wichtigste Knopf am Aufbau.")
    except FluidNCError as exc:
        dialog.warnen(f"Start fehlgeschlagen: {exc}")
        return False
    finally:
        client.close()
    return True


SCHRITTE: tuple[Schritt, ...] = (
    Schritt(
        "installation",
        "Installation",
        "Ohne lauffähigen Kern nützt der schönste Aufbau nichts — und es ist der einzige "
        "Schritt, der ganz ohne Hardware geht.",
        False,
        _zustand_installation,
        _tue_installation,
    ),
    Schritt(
        "board",
        "Board erreichen",
        "Alles Weitere hängt daran. Und es sagt gleich, ob das Board in ConfigAlarm steht.",
        True,
        _zustand_board,
        _tue_board,
    ),
    Schritt(
        "standort",
        "Aufhängung einmessen",
        "Die drei Maße sind die Grundlage für alles danach. Sie müssen NACH einem Neustart "
        "am Referenzpunkt genommen werden — sonst stimmt der Bezug nicht.",
        False,
        _zustand_standort,
        _tue_standort,
    ),
    Schritt(
        "firmware",
        "config.yaml erzeugen und übertragen",
        "Erst wenn die Ankermaße im Board stehen, rechnet die Firmware dasselbe wie die "
        "Vorschau. Vor dem Einmessen der Fläche, nicht danach.",
        False,
        _zustand_firmware,
        _tue_firmware,
    ),
    Schritt(
        "nullpunkt",
        "Nullpunkt setzen",
        "Der Nullpunkt entsteht beim Einschalten, nicht durch ein Kommando — und die neue "
        "config.yaml wird ohnehin erst mit einem Neustart wirksam.",
        True,
        _zustand_nullpunkt,
        _tue_nullpunkt,
    ),
    Schritt(
        "flaeche",
        "Fläche abstecken",
        "Jetzt fährt die Maschine im richtigen Koordinatensystem — vorher wäre jede Ecke "
        "an der falschen Stelle gelandet.",
        True,
        _zustand_flaeche,
        _tue_flaeche,
    ),
    Schritt(
        "servo",
        "Stiftheber einstellen",
        "Der Stifttest braucht eine Fläche, auf die er passt. Die Katalogwerte sind "
        "Schätzungen und liegen an jedem Aufbau anders.",
        True,
        _zustand_servo,
        _tue_servo,
    ),
    Schritt(
        "probe",
        "Erster Plot",
        "Der Rahmen fährt genau die Grenzen ab, die gerade eingemessen wurden. Was hier "
        "danebenliegt, liegt bei jedem Bild daneben.",
        True,
        _zustand_probe,
        _tue_probe,
    ),
)


def schritt_nach_name(key: str) -> Schritt:
    for schritt in SCHRITTE:
        if schritt.key == key:
            return schritt
    bekannt = ", ".join(s.key for s in SCHRITTE)
    raise KeyError(f"Schritt {key!r} unbekannt. Bekannt: {bekannt}")


def lauf(
    ctx: Kontext,
    dialog: Dialog,
    *,
    ab: str | None = None,
    nur: str | None = None,
    alle: bool = False,
) -> int:
    """Den Ablauf durchgehen. Rückgabe wie ein Exit-Code: 0 = durch.

    ``alle=False`` überspringt, was schon erledigt ist — das ist der Normalfall
    beim Fortsetzen. ``nur`` führt genau einen Schritt aus, ``ab`` beginnt bei
    einem bestimmten.
    """
    if nur:
        schritte = [schritt_nach_name(nur)]
        alle = True
    else:
        schritte = list(SCHRITTE)
        if ab:
            start = [s.key for s in schritte].index(schritt_nach_name(ab).key)
            schritte = schritte[start:]

    for nummer, schritt in enumerate(schritte, 1):
        zustand = schritt.zustand(ctx)
        if zustand == ERLEDIGT and not alle:
            dialog.sagen(f"[{nummer}/{len(schritte)}] {schritt.titel}: erledigt, übersprungen")
            continue

        # Ein Schritt, der die Maschine fahren lässt, hat ohne Board nichts zu
        # tun. Ihn trotzdem zu starten, endet in einer Fehlermeldung pro Schritt
        # — und am Ende weiß niemand mehr, was davon wirklich fehlt.
        if schritt.braucht_board and ctx.board_erreichbar is False:
            dialog.sagen(
                f"[{nummer}/{len(schritte)}] {schritt.titel}: übersprungen, kein Board"
            )
            ctx.notizen.append(
                f"{schritt.titel} braucht die Maschine: wallplotter-setup --ab {schritt.key}"
            )
            continue

        dialog.sagen("")
        dialog.sagen(f"[{nummer}/{len(schritte)}] {schritt.titel}")
        dialog.sagen(f"    {schritt.warum}")
        try:
            if not schritt.ausfuehren(ctx, dialog):
                dialog.warnen("")
                dialog.warnen(
                    f"Bei »{schritt.titel}« hängt es. Fortsetzen mit: "
                    f"wallplotter-setup --ab {schritt.key}"
                )
                return 1
        except Abbruch as exc:
            dialog.sagen("")
            dialog.sagen(f"Abgebrochen: {exc}")
            dialog.sagen(f"Fortsetzen mit: wallplotter-setup --ab {schritt.key}")
            return 2

    dialog.sagen("")
    if ctx.notizen:
        dialog.sagen("Noch offen:")
        for notiz in ctx.notizen:
            dialog.sagen(f"  * {notiz}")
    else:
        dialog.sagen("Durch. Ab hier ist es ein Plotter.")
    return 0
