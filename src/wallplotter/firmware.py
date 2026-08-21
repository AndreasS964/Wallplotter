"""Die ``config.yaml`` des Boards erzeugen, statt sie von Hand zu pflegen.

Bis hierher gab es zwei Beschreibungen derselben Maschine: die Python-Seite
(Anker aus :mod:`wallplotter.location`, Motor und Auflösung aus
:mod:`wallplotter.kinematics`, Grenzen aus :mod:`wallplotter.timing`, Servo-
Werte aus :mod:`wallplotter.toolhead`) und eine 300 Zeilen lange YAML-Datei,
die jemand von Hand nachzog. Zwei Beschreibungen laufen auseinander: Die
Vorschau rechnet mit den gemessenen Ankermaßen, das Board mit denen von vor
drei Wochen, und das Bild an der Wand ist verzerrt.
:func:`wallplotter.doctor.check_firmware_config` merkt das an — aber nur, wenn
jemand den Selbsttest aufruft. Eine Prüfung, die man aufrufen muss, erreicht
nicht den, der nicht daran denkt.

Deshalb ist die Python-Seite jetzt die Quelle und die YAML-Datei das Erzeugnis:

.. code-block:: console

    wallplotter-firmware config --location Keller --out config.yaml
    wallplotter-firmware push   --location Keller --host wandplotter.local

Was dabei **nicht** verloren geht, sind die Kommentare. Der Wert dieser Datei
steckt zur Hälfte in den Begründungen — warum nicht referenziert wird, warum
der Servo an ``gpio.25`` und nicht am Stecker mit der Aufschrift „PWM" hängt,
warum ``speed_map`` bei 5 % anfängt. Der Erzeuger schreibt sie mit und setzt
die gerechneten Zahlen an die passenden Stellen.

Drei Dinge, die der Erzeuger von sich aus richtig macht und die von Hand
schon danebengegangen sind:

* **Schrittweite und Mikroschritte gehören zusammen.** ``steps_per_mm`` folgt
  aus Pulley, Riementeilung und Mikroschritten. Wer im Treiberblock die
  Mikroschritte ändert und oben die Schritte vergisst, fährt um denselben
  Faktor daneben.
* **Der Servoweg ist ein Tastverhältnis, kein Winkel.** Bei 50 Hz ist die
  Periode 20 ms; ein RC-Servo will 1,0 bis 2,0 ms Impuls. Die ``speed_map``
  rechnet der Erzeuger aus dem Impulsfenster aus, statt sie zu raten.
* **Ein Pin gehört genau einem Verbraucher.** FluidNC belegt ihn exklusiv;
  zwei Abschnitte auf demselben GPIO lassen sich nicht einmal parsen.
  :meth:`FirmwareConfig.check` sagt das vorher.

Geprüft wird die Ausgabe gegen :mod:`wallplotter.fluidnc_schema` — die
Schlüsselliste aus dem FluidNC-Quelltext. Ein Schlüssel, den die Firmware
nicht kennt, setzt sie in ConfigAlarm; das soll hier auffallen und nicht an
der Wand.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

from .fluidnc_schema import ERROR, WARN
from .kinematics import Anchors, Motor
from .timing import MotionLimits

__all__ = [
    "BoardProfile",
    "RODENT_V1",
    "BOARDS",
    "ServoSpindle",
    "LaserSpindle",
    "FirmwareConfig",
    "Problem",
    "FirmwareError",
    "board_by_name",
]


class FirmwareError(RuntimeError):
    """Die Konfiguration ist so nicht erzeugbar."""


@dataclass(frozen=True)
class Problem:
    """Eine Beanstandung an der Konfiguration selbst."""

    level: str
    text: str

    def __str__(self) -> str:
        return self.text


def _fmt(value: float, decimals: int = 3) -> str:
    """Zahl mit fester Nachkommastelle, so wie FluidNC sie selbst schreibt."""
    return f"{value:.{decimals}f}"


def _de(value: float, decimals: int = 2, minimum: int = 0) -> str:
    """Zahl für den Fließtext der Kommentare — mit Komma statt Punkt.

    Die Kommentare sind auf Deutsch; ``1.5 ms`` mitten in einem deutschen Satz
    liest sich wie ein Tippfehler. Nullen am Ende fallen weg, aber nicht unter
    ``minimum`` Nachkommastellen: ein Servoimpuls heißt „1,0 ms", nicht „1 ms".
    """
    whole, _, frac = f"{value:.{decimals}f}".partition(".")
    frac = frac.rstrip("0").ljust(minimum, "0")
    return f"{whole},{frac}" if frac else whole


_PLAIN_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/+()-]*$")
"""Zeichen, mit denen ein YAML-Skalar ohne Anführungszeichen auskommt."""

_YAML_KEYWORDS = {"true", "false", "yes", "no", "on", "off", "null", "none", "~"}


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _scalar(value: str) -> str:
    """Freitext so schreiben, dass FluidNC **und** ein YAML-Parser dasselbe lesen.

    Ohne das legt ein Maschinenname mit Doppelpunkt die Datei lahm — ``name:
    Wand: groß`` ist kein gültiges YAML, und ``name: Keller #2`` verliert bei
    jedem YAML-Parser alles ab der Raute.

    Anführungszeichen sind hier enger begrenzt als in YAML, weil FluidNCs
    Tokenizer **keine Maskierung kennt**: ``parseValue()`` liest bis zum ersten
    passenden Zeichen und wirft den Rest der Zeile weg (Tokenizer.cpp:127ff).
    Ein Backslash bleibt dort ein Backslash. Deshalb einfache Anführungszeichen
    zuerst — die deutet auch YAML fast wörtlich —, doppelte nur, wenn kein
    Backslash im Spiel ist, und beides zusammen im Text ist schlicht nicht
    darstellbar.
    """
    text = str(value)
    if "\n" in text or "\r" in text:
        raise FirmwareError(f"Zeilenumbruch in {text!r} — eine YAML-Zeile trägt nur eine Zeile")
    if _PLAIN_SAFE.match(text) and not text.endswith(" ") and text.lower() not in _YAML_KEYWORDS:
        if not _looks_numeric(text):
            return text
    if "'" not in text:
        return f"'{text}'"
    if '"' not in text and "\\" not in text:
        return f'"{text}"'
    raise FirmwareError(
        f"{text!r} enthält beide Anführungszeichen — FluidNCs Tokenizer kennt keine "
        "Maskierung, das lässt sich nicht schreiben"
    )


def _kommentar(text: str) -> str:
    """Freitext, der in einer Kommentarzeile landet, auf eine Zeile zwingen.

    Ein Zeilenumbruch im Standortnamen bräche die Datei auf: Die zweite Zeile
    stünde ohne ``#`` da und wäre für FluidNC ein Schlüssel — also ConfigAlarm
    an einer Stelle, an der niemand einen Schlüssel vermutet.
    """
    return " ".join(str(text).split())


def _kv(indent: str, key: str, value: object, comment: str = "") -> list[str]:
    """Eine Schlüssel-Wert-Zeile, der Kommentar in der Zeile **darüber**.

    Hinter dem Wert darf keiner stehen, und das ist keine Geschmacksfrage:
    FluidNCs Tokenizer schneidet Kommentare am Zeilenende **nicht** ab.
    ``Tokenizer::parseValue()`` nimmt bei einem unquotierten Wert den ganzen
    Rest der Zeile (``_token._value = _line;``); abgeworfen wird nur eine Zeile,
    die *mit* ``#`` beginnt. Was daraus folgt, hängt am Typ des Schlüssels:

    * **Zahl** — ``Parser::intValue()`` verlangt, dass die *ganze* Zeichenkette
      die Zahl ist (``from_decimal`` bricht bei jedem Nicht-Ziffernzeichen ab,
      ``from_float`` prüft ``floatEnd == str + length``). Passt es nicht, ruft
      es ``parseError()`` — und das ist ``set_state(State::ConfigAlarm)``.
      **Das Board fährt nicht mehr.**
    * **Wahrheitswert** — ``boolValue()`` vergleicht auf ``"true"`` und liefert
      sonst stillschweigend ``false``. Ein ``hard_limits: true # …`` wäre damit
      aus, ohne dass irgendetwas gemeldet würde.
    * **Pin** — ``Pin::create()`` bekommt den Kommentar mit, meldet
      ``Setting up pin: … failed`` und liefert einen ErrorPin.
    * **Text** — der Kommentar wird Teil des Werts.

    Deshalb steht in BTTs eigener ``rodent.yaml`` hinter keinem einzigen Wert
    ein Kommentar. Hier jetzt auch nicht mehr.
    """
    lines = [f"{indent}# {comment}"] if comment else []
    lines.append(f"{indent}{key}: {value}")
    return lines


def _wrap(text: str, prefix: str = "# ", width: int = 79) -> list[str]:
    """Fließtext als Kommentarzeilen umbrechen.

    Von Hand umgebrochene Kommentare stehen schief, sobald sich eine Zahl darin
    ändert — und genau das passiert hier bei jedem Erzeugen.
    """
    import textwrap  # noqa: PLC0415

    return textwrap.wrap(
        text,
        width=width,
        initial_indent=prefix,
        subsequent_indent=prefix,
        break_long_words=False,
        break_on_hyphens=False,
    )


# ---------------------------------------------------------------------------
# Das Board
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoardProfile:
    """Was an einem Board festverdrahtet ist und nicht zur Aufhängung gehört.

    Alles hier ist Eigenschaft der Platine, nicht der Maschine — deshalb steht
    es getrennt von :class:`FirmwareConfig`. Ein anderes Board heißt: ein
    zweites Profil, nicht ein zweiter Erzeuger.
    """

    key: str
    """Kurzname für ``--board``."""

    name: str
    """Wie das Board in der Datei unter ``board:`` steht."""

    long_name: str
    """Ausgeschrieben, für die Kopfzeile. FluidNC liest nur ``name``."""

    source: str
    """Woher die Pinbelegung stammt. Gehört in die Datei, damit sie nachprüfbar ist."""

    # -- Schrittmotortreiber ---------------------------------------------
    left_step_pin: str
    left_direction_pin: str
    left_disable_pin: str
    left_cs_pin: str
    left_spi_index: int

    right_step_pin: str
    right_direction_pin: str
    right_disable_pin: str
    right_cs_pin: str
    right_spi_index: int

    driver_section: str = "tmc_5160"
    """Wie der Treiber in FluidNC heißt. Der Rodent trägt TMC2160; BTT
    konfiguriert sie in der eigenen ``rodent.yaml`` als ``tmc_5160``, und für
    den Schrittbetrieb sind die Register identisch."""

    r_sense_ohms: float = 0.022
    stepping_engine: str = "I2S_STREAM"
    shared_stepper_disable_pin: str = "NO_PIN"

    # -- Busse -------------------------------------------------------------
    i2so_bck_pin: str = "gpio.22"
    i2so_data_pin: str = "gpio.21"
    i2so_ws_pin: str = "gpio.17"
    spi_miso_pin: str = "gpio.19"
    spi_mosi_pin: str = "gpio.23"
    spi_sck_pin: str = "gpio.18"
    sd_cs_pin: str = "gpio.0"
    sd_card_detect_pin: str = "NO_PIN"
    sd_frequency_hz: int = 8_000_000

    # -- Statusleuchten ----------------------------------------------------
    status_idle_pin: str = "NO_PIN"
    status_run_pin: str = "NO_PIN"
    status_alarm_pin: str = "NO_PIN"
    status_interval_ms: int = 500

    # -- Werkzeugkopf ------------------------------------------------------
    servo_pin: str = "NO_PIN"
    servo_pin_note: str = ""
    laser_pin: str = "NO_PIN"
    laser_pin_note: str = ""

    # -- Taster ------------------------------------------------------------
    reset_pin: str = "NO_PIN"
    reset_pin_note: str = ""
    feed_hold_pin: str = "NO_PIN"
    feed_hold_pin_note: str = ""
    cycle_start_pin: str = "NO_PIN"
    cycle_start_pin_note: str = ""

    spare_drivers_note: str = ""

    # -- Versorgung --------------------------------------------------------
    input_voltage: str = ""
    """Eingangsspannung der Platine, wie im Handbuch angegeben."""

    five_volt_taps: tuple[str, ...] = ()
    """Wo der **rohe** 5-V-Zweig herauskommt — ohne Vorwiderstand.

    Der +5-V-Pin am Spindelstecker gehört ausdrücklich *nicht* dazu: dort
    liegen 100 Ohm in Reihe. Diese Unterscheidung ist der Grund, warum in
    diesem Projekt eine Zeitlang ein zweites Netzteil empfohlen wurde.
    """

    vmos_ports: tuple[tuple[str, str], ...] = ()
    """Die schaltbaren Leistungsausgänge: (GPIO, Beschreibung).

    Low-Side-MOSFETs an einem eigenen Eingang — kein Logikausgang. Für einen
    Servo also unbrauchbar, für Luft, Absaugung oder Licht genau richtig.
    """

    vmos_voltage: str = ""

    diag_jumpers: tuple[tuple[str, str], ...] = ()
    """Steckbrücken, die einen Treiber-DIAG-Ausgang auf einen GPIO legen.

    Sie sitzen auf denselben Pins wie die Endstop-Eingänge. Wer dort Taster
    betreibt, muss sie abziehen — sonst treibt der Treiber gegen den Taster.
    """

    def output_pins(self) -> dict[str, str]:
        """Alle Pins, auf denen das Board etwas *ausgibt* — für die Kollisionsprüfung."""
        return {
            "linker Treiber step": self.left_step_pin,
            "linker Treiber dir": self.left_direction_pin,
            "linker Treiber disable": self.left_disable_pin,
            "linker Treiber cs": self.left_cs_pin,
            "rechter Treiber step": self.right_step_pin,
            "rechter Treiber dir": self.right_direction_pin,
            "rechter Treiber disable": self.right_disable_pin,
            "rechter Treiber cs": self.right_cs_pin,
            "I2S bck": self.i2so_bck_pin,
            "I2S data": self.i2so_data_pin,
            "I2S ws": self.i2so_ws_pin,
            "SPI miso": self.spi_miso_pin,
            "SPI mosi": self.spi_mosi_pin,
            "SPI sck": self.spi_sck_pin,
            "SD cs": self.sd_cs_pin,
            "Statusleuchte idle": self.status_idle_pin,
            "Statusleuchte run": self.status_run_pin,
            "Statusleuchte alarm": self.status_alarm_pin,
        }


RODENT_V1 = BoardProfile(
    key="rodent",
    name="BTT Rodent V1.0",
    long_name="BIGTREETECH Rodent V1.0",
    source=(
        "Pinbelegung (Stepper, SPI, I2S, SD, Endstops) stammt aus BTTs eigener "
        "rodent.yaml: https://github.com/bigtreetech/Rodent/blob/master/rodent.yaml — "
        "Servo- und Tasterpins aus dem Rodent User Manual V1.03 (Pinbild S. 6/7, "
        "Spindel-Schaltplan S. 12)"
    ),
    left_step_pin="I2SO.2",
    left_direction_pin="I2SO.1",
    left_disable_pin="I2SO.0",
    left_cs_pin="gpio.5",
    left_spi_index=1,
    right_step_pin="I2SO.5",
    right_direction_pin="I2SO.4",
    right_disable_pin="I2SO.7",
    # Daisy-Chain: CS sitzt am ersten Treiber, die weiteren hängen dahinter.
    right_cs_pin="NO_PIN",
    right_spi_index=2,
    status_idle_pin="I2SO.11:low",
    status_run_pin="I2SO.14:low",
    status_alarm_pin="I2SO.3:low",
    servo_pin="gpio.25",
    servo_pin_note="Sp-Enable, CN51 Pin 3",
    laser_pin="gpio.15",
    laser_pin_note="Sp-Direction, CN52 Pin 3 — schließt RS485 aus",
    reset_pin="gpio.35",
    reset_pin_note="X-MAX-Stecker",
    feed_hold_pin="gpio.34",
    feed_hold_pin_note="Y-MAX-Stecker",
    cycle_start_pin="gpio.33",
    cycle_start_pin_note="Z-MAX-Stecker",
    spare_drivers_note=(
        "Die beiden freien Treiber (I2SO.10/9/8 und I2SO.13/12/15, spi_index 3 und 4)"
    ),
    input_voltage="DC24V bis DC56V",
    five_volt_taps=(
        "OLED-Stecker, Pin +5V (neben SDA/SCL/GND)",
        "jeder Endstop-Stecker, Pin V-Lim — wenn die SW_VCC-Brücke (CN44) auf +5V steckt",
    ),
    vmos_ports=(
        ("gpio.12", "V-MOS 1"),
        ("gpio.2", "V-MOS 2"),
        ("gpio.4", "V-MOS 3"),
    ),
    vmos_voltage="DC12V bis DC36V, bis 5 A je Ausgang",
    diag_jumpers=(
        ("DIAGX", "gpio.35"),
        ("DIAGY", "gpio.34"),
        ("DIAGZ", "gpio.33"),
        ("DIAGE", "gpio.32"),
    ),
)

BOARDS: dict[str, BoardProfile] = {"rodent": RODENT_V1}


def board_by_name(name: str) -> BoardProfile:
    try:
        return BOARDS[name.lower()]
    except KeyError:
        known = ", ".join(sorted(BOARDS))
        raise FirmwareError(f"Board {name!r} unbekannt. Bekannt: {known}") from None


# ---------------------------------------------------------------------------
# Werkzeugköpfe als Spindel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServoSpindle:
    """Der Stiftservo an der PWM-Spindel.

    ``speed_map`` bildet S-Werte auf das **Tastverhältnis** ab, nicht auf einen
    Winkel. Deshalb wird sie hier aus dem Impulsfenster gerechnet und nicht
    eingetragen: bei 50 Hz ist die Periode 20 ms, ein RC-Servo will 1,0 bis
    2,0 ms — also 5 bis 10 %. Damit entspricht S<wert> unmittelbar Prozent des
    Servowegs.
    """

    pwm_hz: int = 50
    min_pulse_ms: float = 1.0
    max_pulse_ms: float = 2.0
    s_min: int = 0
    s_max: int = 100
    tool_num: int = 0

    def __post_init__(self) -> None:
        if self.pwm_hz <= 0:
            raise FirmwareError("pwm_hz muss größer als 0 sein")
        if self.min_pulse_ms >= self.max_pulse_ms:
            raise FirmwareError(
                f"Impulsfenster {self.min_pulse_ms}…{self.max_pulse_ms} ms ist verkehrt herum"
            )
        if self.s_min >= self.s_max:
            raise FirmwareError(f"S-Bereich {self.s_min}…{self.s_max} ist verkehrt herum")

    @property
    def period_ms(self) -> float:
        return 1000.0 / self.pwm_hz

    def duty_pct(self, pulse_ms: float) -> float:
        return pulse_ms / self.period_ms * 100.0

    @property
    def speed_map(self) -> str:
        return (
            f"{self.s_min}={_fmt(self.duty_pct(self.min_pulse_ms))}% "
            f"{self.s_max}={_fmt(self.duty_pct(self.max_pulse_ms))}%"
        )


@dataclass(frozen=True)
class LaserSpindle:
    """Der zweite Kopf. Standardmäßig aus — dann steht er auskommentiert in der Datei.

    Zwei Dinge gehen mit dem Servo zusammen nicht, und beide sind hier
    festgehalten statt in einer Fußnote: derselbe Pin (FluidNC belegt ihn
    exklusiv) und dieselbe Frequenz (ein Servo will 50 Hz, die Klasse ``Laser``
    lässt nur 1000 bis 100000 Hz zu).
    """

    enabled: bool = False
    pwm_hz: int = 5000
    s_max: int = 1000
    tool_num: int = 100
    output_pin: str = ""
    """Leer heißt: den Laserpin des Boardprofils nehmen."""

    air_assist: bool = True
    """Air Assist über den Flood-Ausgang (``M8``/``M9``)."""

    def __post_init__(self) -> None:
        if self.s_max <= 0:
            # Ohne diese Prüfung erzeugt _laser_block() eine speed_map, die
            # denselben S-Wert (0) auf zwei widersprüchliche Tastverhältnisse
            # abbildet — check() sah das nicht vor, weil kein Bereich dafür
            # registriert war.
            raise FirmwareError(f"s_max={self.s_max} muss größer als 0 sein")


# ---------------------------------------------------------------------------
# Die Maschine
# ---------------------------------------------------------------------------

DEFAULT_ANCHORS = Anchors(left_x=-1150.0, right_x=1150.0, y=900.0)
"""Beispiel für eine symmetrische Aufhängung — 2,3 m Spannweite, 0,9 m über dem
Nullpunkt. Steht nur da, damit die ausgelieferte Datei vollständig ist; für den
eigenen Aufbau kommt sie aus ``wallplotter-location``."""


@dataclass(frozen=True)
class FirmwareConfig:
    """Alles, was in die ``config.yaml`` geht — und woher es kommt."""

    board: BoardProfile = RODENT_V1
    name: str = "Wandplotter Keller"
    meta: str = "Kletterwand-Projekt"

    anchors: Anchors = DEFAULT_ANCHORS
    location_name: str = ""
    """Name des Standorts, aus dem die Anker stammen. Leer = Beispielwerte."""

    location_measurements: str = ""
    """Die drei gemessenen Maße im Klartext, für die Kopfzeile."""

    segment_length_mm: float = 1.0
    motor: Motor = field(default_factory=Motor)
    limits: MotionLimits = field(default_factory=MotionLimits)

    run_amps: float = 1.2
    hold_amps: float = 1.2
    rated_amps: float = 1.2
    """Nennstrom des verbauten Motors (17HS4412P1-3). Grenze für :meth:`check`."""

    motor_label: str = "17HS4412P1-3"
    max_travel_mm: float = 4000.0
    idle_ms: int = 255

    servo: ServoSpindle = field(default_factory=ServoSpindle)
    laser: LaserSpindle = field(default_factory=LaserSpindle)

    controls: bool = True
    """Not-Halt, Pause und Weiter als Taster. Ohne sie gibt es keinen Halt."""

    control_active_low: bool = True
    """``:low`` an die Tasterpins hängen — Ruhelage HIGH, gedrückt gegen GND."""

    hostname: str = "wandplotter"

    # -- Herkunft ---------------------------------------------------------

    @classmethod
    def from_location(cls, location, base: FirmwareConfig | None = None) -> FirmwareConfig:
        """Konfiguration mit den Ankermaßen eines eingemessenen Standorts.

        Die Anker kommen per Trilateration aus den drei Maßen, die am Standort
        genommen wurden — und damit aus derselben Rechnung, mit der die
        Vorschau arbeitet. Genau das ist der Punkt der Übung.
        """
        base = base or cls()
        return replace(
            base,
            anchors=location.anchors(),
            location_name=location.name,
            location_measurements=(
                f"Spannweite {location.anchor_span_mm:.0f} mm, Riemen am Nullpunkt "
                f"{location.left_belt_zero_mm:.0f}/{location.right_belt_zero_mm:.0f} mm"
            ),
            motor=replace(base.motor, microsteps=location.microsteps),
        )

    # -- Abgeleitetes -----------------------------------------------------

    @property
    def laser_pin(self) -> str:
        return self.laser.output_pin or self.board.laser_pin

    @property
    def air_assist_pin(self) -> str:
        """Der V-MOS-Ausgang für die Luft — der letzte der drei, damit die
        beiden vorderen für Absaugung und Licht frei bleiben."""
        if not self.board.vmos_ports:
            return "NO_PIN"
        return self.board.vmos_ports[-1][0]

    def command_line(self) -> str:
        """Der Aufruf, der genau diese Datei wieder erzeugt.

        Steht in der Kopfzeile der Ausgabe. Ohne ihn ist „erzeugt, nicht von
        Hand ändern" eine Behauptung ohne Anleitung — und wenn er unvollständig
        ist, ist er schlimmer als keiner: dann erzeugt er eine *andere* Datei.
        Ein Test hält ihn deshalb gegen das, was er tatsächlich hervorbringt.
        """
        import shlex  # noqa: PLC0415

        default = FirmwareConfig(board=self.board)
        parts = ["wallplotter-firmware", "config"]

        # Ohne diese Angabe zöge der Erzeuger den aktiven Standort heran und
        # käme damit auf andere Anker als die, die hier drinstehen.
        parts += ["--location", self.location_name] if self.location_name else ["--kein-standort"]

        if self.board.key != "rodent":
            parts += ["--board", self.board.key]
        if self.name != default.name:
            parts += ["--name", self.name]
        if self.meta != default.meta:
            parts += ["--meta", self.meta]
        # Bei gesetztem Standort IMMER mitschreiben: `from_location()` holt die
        # Mikroschritte aus dem Standort, der Vergleich unten geht aber gegen
        # die Klassenvorgabe. Steht im Standort 32 und jemand überschreibt mit
        # `--microsteps 16`, wären beide Werte gleich der Vorgabe — die Option
        # fiele weg, und der Aufruf in der Kopfzeile zöge die 32 zurück.
        # Ergebnis wäre steps_per_mm 160 statt 80: Faktor zwei auf beiden
        # Achsen, ohne dass irgendwo etwas gemeldet würde.
        if self.location_name or self.motor.microsteps != default.motor.microsteps:
            parts += ["--microsteps", str(self.motor.microsteps)]

        # Der Haltestrom folgt in der CLI stillschweigend dem Laufstrom; steht
        # er woanders, muss er ausdrücklich mit.
        run_genannt = self.run_amps != default.run_amps
        if run_genannt:
            parts += ["--run-amps", f"{self.run_amps:g}"]
        if self.hold_amps != (self.run_amps if run_genannt else default.hold_amps):
            parts += ["--hold-amps", f"{self.hold_amps:g}"]

        if self.segment_length_mm != default.segment_length_mm:
            parts += ["--segment-length", f"{self.segment_length_mm:g}"]
        if self.limits.max_rate_mm_min != default.limits.max_rate_mm_min:
            parts += ["--max-rate", f"{self.limits.max_rate_mm_min:g}"]
        if self.limits.acceleration_mm_s2 != default.limits.acceleration_mm_s2:
            parts += ["--acceleration", f"{self.limits.acceleration_mm_s2:g}"]
        if self.max_travel_mm != default.max_travel_mm:
            parts += ["--max-travel", f"{self.max_travel_mm:g}"]
        if self.idle_ms != default.idle_ms:
            parts += ["--idle-ms", str(self.idle_ms)]

        if self.servo.pwm_hz != default.servo.pwm_hz:
            parts += ["--servo-hz", str(self.servo.pwm_hz)]
        if (self.servo.min_pulse_ms, self.servo.max_pulse_ms) != (
            default.servo.min_pulse_ms,
            default.servo.max_pulse_ms,
        ):
            parts += [
                "--servo-impuls",
                f"{self.servo.min_pulse_ms:g}",
                f"{self.servo.max_pulse_ms:g}",
            ]

        if self.laser.enabled:
            parts.append("--laser")
        if self.laser.output_pin:
            parts += ["--laser-pin", self.laser.output_pin]
        if self.laser.pwm_hz != default.laser.pwm_hz:
            parts += ["--laser-hz", str(self.laser.pwm_hz)]
        if self.laser.s_max != default.laser.s_max:
            parts += ["--laser-smax", str(self.laser.s_max)]

        if not self.controls:
            parts.append("--ohne-taster")
        if not self.control_active_low:
            parts.append("--taster-aktiv-high")

        return " ".join(shlex.quote(part) for part in parts)

    def pin_usage(self) -> dict[str, list[str]]:
        """Pin → alles, was darauf liegt. Grundlage der Kollisionsprüfung."""
        usage: dict[str, list[str]] = {}

        def note(what: str, pin: str) -> None:
            bare = pin.split(":")[0].strip()
            if not bare or bare.upper() == "NO_PIN":
                return
            usage.setdefault(bare.lower(), []).append(what)

        for what, pin in self.board.output_pins().items():
            note(what, pin)
        note("Stiftservo", self.board.servo_pin)
        if self.laser.enabled:
            note("Laser", self.laser_pin)
        if self.controls:
            note("Not-Halt", self.board.reset_pin)
            note("Pause", self.board.feed_hold_pin)
            note("Weiter", self.board.cycle_start_pin)
        return usage

    # -- Prüfung ----------------------------------------------------------

    def check(self) -> list[Problem]:
        """Was auffällt, bevor die Datei auf das Board geht.

        Absichtlich nicht als Ausnahme: Eine Konfiguration darf fragwürdig sein
        und trotzdem geschrieben werden — nur soll niemand behaupten können, es
        habe keiner gesagt.
        """
        problems: list[Problem] = []

        # -- Aufhängung
        if self.anchors.span <= 0:
            problems.append(Problem(ERROR, "Der rechte Anker sitzt links vom linken."))
        if self.anchors.y <= 0:
            problems.append(
                Problem(
                    ERROR,
                    f"Ankerhöhe {self.anchors.y:.0f} mm liegt nicht über dem Nullpunkt — "
                    "die Gondel hinge über den Umlenkpunkten.",
                )
            )
        # WallPlotter::lengths_to_xy() nimmt als Abstand der Kreismittelpunkte nur
        # die X-Differenz und setzt y = left_anchor_y - h. Ungleiche Ankerhöhen
        # rechnet die Firmware also falsch zurück. Anchors trägt nur ein y — das
        # ist genau deshalb so und hier festgehalten, damit es so bleibt.

        # -- Motor und Strom
        if self.run_amps > self.rated_amps:
            problems.append(
                Problem(
                    ERROR,
                    f"run_amps {self.run_amps:g} A über dem Nennstrom des "
                    f"{self.motor_label} ({self.rated_amps:g} A) — der Motor wird heiß, "
                    "und heiß heißt Schrittverluste.",
                )
            )
        if self.hold_amps > self.rated_amps:
            problems.append(
                Problem(
                    ERROR,
                    f"hold_amps {self.hold_amps:g} A über dem Nennstrom des "
                    f"{self.motor_label} ({self.rated_amps:g} A) — im Stillstand steht der "
                    "Strom dauerhaft an, das ist der Fall, in dem ein Motor wirklich heiß wird.",
                )
            )
        if self.hold_amps < self.run_amps:
            problems.append(
                Problem(
                    WARN,
                    f"hold_amps {self.hold_amps:g} A unter run_amps {self.run_amps:g} A — "
                    "die Gondel hängt am Riemen und darf im Stillstand nicht absacken.",
                )
            )
        if self.idle_ms != 255:
            problems.append(
                Problem(
                    WARN,
                    f"idle_ms {self.idle_ms} schaltet die Motoren nach dieser Zeit ab. "
                    "255 heißt \u201enie\u201c — alles andere lässt die Gondel irgendwann fallen.",
                )
            )

        # -- Zerlegung gerader Strecken
        if self.segment_length_mm <= 0 or not math.isfinite(self.segment_length_mm):
            problems.append(
                Problem(
                    ERROR,
                    f"segment_length {self.segment_length_mm} ist keine brauchbare Länge — "
                    "FluidNC zerlegt gerade Strecken in Stücke dieser Größe, und bei 0 oder "
                    "negativ wären es unendlich viele.",
                )
            )
        elif self.segment_length_mm > 10:
            problems.append(
                Problem(
                    WARN,
                    f"segment_length {_de(self.segment_length_mm)} mm ist grob: Die Kinematik "
                    "ist nichtlinear, und zwischen zwei Stützstellen fährt die Gondel gerade. "
                    "Auf 2,5 m Wand fällt das als Beule in jeder Diagonalen auf.",
                )
            )

        # -- Servo
        pulse_lo = self.servo.min_pulse_ms
        pulse_hi = self.servo.max_pulse_ms
        if not (0.5 <= pulse_lo and pulse_hi <= 2.5):
            problems.append(
                Problem(
                    ERROR,
                    f"Impulsfenster {_de(pulse_lo, minimum=1)} bis {_de(pulse_hi, minimum=1)} ms "
                    "liegt außerhalb dessen, was ein RC-Servo versteht (0,5 bis 2,5 ms) — "
                    "er fährt in den Anschlag.",
                )
            )
        if self.servo.duty_pct(pulse_hi) > 100.0:
            problems.append(
                Problem(
                    ERROR,
                    f"Bei {self.servo.pwm_hz} Hz ist die Periode nur "
                    f"{_de(self.servo.period_ms)} ms — ein Impuls von "
                    f"{_de(pulse_hi, minimum=1)} ms passt nicht hinein.",
                )
            )
        if not 40 <= self.servo.pwm_hz <= 400:
            problems.append(
                Problem(
                    WARN,
                    f"pwm_hz {self.servo.pwm_hz} für einen RC-Servo ungewöhnlich — "
                    "üblich sind 50 Hz.",
                )
            )
        if self.board.servo_pin.upper() == "NO_PIN":
            problems.append(
                Problem(ERROR, "Kein Servopin im Boardprofil — der Stift hebt dann nie ab.")
            )

        # -- Laser
        if self.laser.enabled:
            if not 1000 <= self.laser.pwm_hz <= 100000:
                problems.append(
                    Problem(
                        ERROR,
                        f"Laser pwm_hz {self.laser.pwm_hz} außerhalb 1000…100000 — die Klasse "
                        "Laser klemmt den Wert (Spindles/LaserSpindle.h:41).",
                    )
                )
            if self.laser.tool_num == self.servo.tool_num:
                problems.append(
                    Problem(
                        ERROR,
                        f"Stift und Laser tragen beide tool_num {self.servo.tool_num} — "
                        "M6 könnte sie nicht auseinanderhalten.",
                    )
                )

        # -- Pins
        for pin, users in sorted(self.pin_usage().items()):
            if len(users) > 1:
                problems.append(
                    Problem(
                        ERROR,
                        f"{pin} ist doppelt belegt ({', '.join(users)}). FluidNC belegt "
                        "einen Pin exklusiv — die Datei ließe sich nicht einmal einlesen.",
                    )
                )

        # gpio.34…39 des ESP32 können nur lesen. Als Tastereingang ist das
        # richtig; ein Ausgang darauf schaltet nie.
        for what, pin in (("Stiftservo", self.board.servo_pin),) + (
            (("Laser", self.laser_pin),) if self.laser.enabled else ()
        ):
            number = _gpio_number(pin)
            if number is not None and 34 <= number <= 39:
                problems.append(
                    Problem(
                        ERROR,
                        f"{what} liegt auf {pin} — gpio.34 bis gpio.39 des ESP32 sind "
                        "reine Eingänge und schalten nichts.",
                    )
                )

        if not self.controls:
            problems.append(
                Problem(
                    WARN,
                    "Ohne den control-Block gibt es keinen Halt: Über HTTP lässt sich die "
                    "Maschine nicht anhalten, und die M0-Pause beim Stiftwechsel löst nur "
                    "cycle_start_pin auf.",
                )
            )

        return problems

    # -- Ausgabe ----------------------------------------------------------

    def render(self) -> str:
        """Die vollständige ``config.yaml`` als Text."""
        blocks = [
            self._header(),
            self._kinematics(),
            self._stepping(),
            self._axes(),
            self._spindle(),
            self._control(),
            self._start(),
            self._buses(),
            self._wifi(),
        ]
        return "\n".join(block.rstrip() + "\n" for block in blocks if block)

    # Die einzelnen Abschnitte. Jeder gibt seinen eigenen Kommentar mit aus —
    # eine Zahl ohne Begründung ist in dieser Datei die Ausnahme.

    def _header(self) -> str:
        lines = [
            f"# FluidNC-Konfiguration für den Wandplotter auf dem {self.board.long_name}",
            "#",
            "# ERZEUGT — nicht von Hand ändern. Wieder herstellen mit:",
            f"#     {_kommentar(self.command_line())}",
            "#",
            "# Wer hier etwas ändern will, ändert es in der Python-Seite: Ankermaße über",
            "# `wallplotter-location`, Motor und Grenzen in wallplotter/firmware.py. Sonst",
            "# ist die Änderung beim nächsten Erzeugen wieder weg — und schlimmer: bis",
            "# dahin beschreiben Software und Board zwei verschiedene Maschinen.",
            "#",
            *_wrap(f"{self.board.source}."),
            "# Jeder Schlüssel dieser Datei ist gegen den FluidNC-Quelltext geprüft —",
            "# siehe docs/firmware-gegenpruefung.md.",
            "#",
            "# Vor dem ersten Einschalten durchgehen:",
            "#   1. Ankermaße: stehen die des eigenen Standorts drin? (siehe unten)",
            f"#   2. run_amps auf {self.run_amps:g} lassen ({self.motor_label}), nicht höher",
            "#   3. Bei einem V1.1-Board r_sense_ohms nachprüfen: BTTs rodent.yaml gilt für",
            f"#      V1.0 ({_fmt(self.board.r_sense_ohms)}), im V1.1-Pinbild steht \"RSENSE 75MR\"",
            "#   4. Nach dem Start ins Log sehen — ein einziges \"[MSG:ERR: Ignored key …]\"",
            "#      bedeutet ConfigAlarm, und dann fährt gar nichts",
            "#",
            "# Prüfen lässt sich beides vorab ohne Board — die Datei selbst und die",
            "# Geometrie dahinter:",
            "#   wallplotter-firmware pruefen config/fluidnc-wallplotter.yaml",
            "#   wallplotter-kinematics --width 2000 --height 2500 --overhang 150 --above 150",
            "",
            f"board: {_scalar(self.board.name)}",
            f"name: {_scalar(self.name)}",
        ]
        if self.meta:
            lines.append(f"meta: {_scalar(self.meta)}")
        return "\n".join(lines)

    def _kinematics(self) -> str:
        anchors = self.anchors
        if self.location_name:
            origin = [
                f"    # Standort: {_kommentar(self.location_name)}",
                f"    # gemessen: {_kommentar(self.location_measurements)}",
                "    #",
                "    # Erzeugt aus `wallplotter-location` — dieselben drei Maße, mit denen",
                "    # auch die Vorschau rechnet. Ändert sich die Aufhängung, wird der",
                "    # Standort neu vermessen und diese Datei neu erzeugt.",
            ]
        else:
            origin = [
                "    # STANDORTABHÄNGIG — diese vier Zeilen gehören nicht ins Repo, sondern zur",
                "    # jeweiligen Aufhängung. Der Plotter soll an verschiedenen Wänden hängen,",
                "    # und damit ändern sich Ankerabstand und -höhe jedes Mal.",
                "    #",
                "    # Gondel an den Referenzpunkt hängen, DORT das Board neu starten (siehe",
                "    # den Homing-Block unten), dann drei Maße nehmen: Abstand der",
                "    # Umlenkpunkte, linke Riemenlänge, rechte Riemenlänge. Dann:",
                "    #",
                "    #   wallplotter-location new <Name> --span 2300 --left 1450 --right 1470",
                "    #   wallplotter-firmware config --location <Name> --out config.yaml",
                "    #",
                "    # Das erzeugt diese Datei mit den passenden Zahlen. Die Werte unten sind",
                "    # nur ein Beispiel für eine symmetrische Aufhängung.",
            ]
        return "\n".join(
            [
                "kinematics:",
                "  WallPlotter:",
                "    # Motorachsen: links hängt an X, rechts an Y",
                "    left_axis: 0",
                "    right_axis: 1",
                "",
                *origin,
                f"    left_anchor_x: {_fmt(anchors.left_x)}",
                f"    left_anchor_y: {_fmt(anchors.y)}",
                f"    right_anchor_x: {_fmt(anchors.right_x)}",
                f"    right_anchor_y: {_fmt(anchors.y)}",
                "",
                "    # Länge, in die FluidNC gerade Strecken zerlegt. Kleiner = runder, aber",
                "    # mehr Rechenlast. 1 mm ist bei 2,5 m Wand ein guter Kompromiss.",
                f"    segment_length: {_fmt(self.segment_length_mm)}",
            ]
        )

    def _stepping(self) -> str:
        return "\n".join(
            [
                "stepping:",
                f"  engine: {self.board.stepping_engine}",
                *_kv(
                    "  ",
                    "idle_ms",
                    self.idle_ms,
                    "Motoren gehalten lassen — die Gondel hängt am Riemen",
                ),
                "  pulse_us: 4",
                "  dir_delay_us: 1",
                "  disable_delay_us: 0",
            ]
        )

    def _axis(self, side: str, first: bool) -> list[str]:
        board, motor = self.board, self.motor
        step, direction, disable, cs, index = (
            (
                board.left_step_pin,
                board.left_direction_pin,
                board.left_disable_pin,
                board.left_cs_pin,
                board.left_spi_index,
            )
            if side == "links"
            else (
                board.right_step_pin,
                board.right_direction_pin,
                board.right_disable_pin,
                board.right_cs_pin,
                board.right_spi_index,
            )
        )
        axis = "x" if side == "links" else "y"

        head = [f"  # {'Linker' if side == 'links' else 'Rechter'} Riemen", f"  {axis}:"]
        if first:
            head += [
                f"    # {motor.pulley_teeth}Z-Pulley an GT{motor.belt_pitch_mm:g} = "
                f"{motor.mm_per_rev:g} mm Riemen pro Umdrehung.",
                f"    # {motor.full_steps_per_rev} Vollschritte * {motor.microsteps} "
                f"Mikroschritte / {motor.mm_per_rev:g} mm = {motor.steps_per_mm:g} Schritte/mm",
            ]
        body = [
            f"    steps_per_mm: {_fmt(motor.steps_per_mm)}",
            f"    max_rate_mm_per_min: {_fmt(self.limits.max_rate_mm_min)}",
            f"    acceleration_mm_per_sec2: {_fmt(self.limits.acceleration_mm_s2)}",
            f"    max_travel_mm: {_fmt(self.max_travel_mm)}",
            "    soft_limits: false",
            "    homing:",
        ]
        if first:
            body += [
                "      # Referenziert wird NICHT. Das ist keine Sparmaßnahme, sondern eine",
                "      # Eigenschaft der Kinematik:",
                "      #",
                "      #   bool WallPlotter::canHome(AxisMask) {",
                "      #       log_error(\"This kinematic system cannot home\");",
                "      #       return false;",
                "      #   }",
                "      #",
                "      # $H endet also in einer Fehlermeldung — mit Endschalter wie ohne, und",
                "      # auch mit sensorlosem StallGuard. (Frühere Fassungen dieser Datei haben",
                "      # StallGuard-Homing empfohlen; das war falsch.)",
                "      #",
                "      # Der Nullpunkt entsteht stattdessen mechanisch, und zwar so:",
                "      # Gondel an den Anschlag, dann das BOARD DORT NEU STARTEN. FluidNC friert",
                "      # beim Start die Riemenlängen für kartesisch (0,0) ein",
                "      # (WallPlotter::init -> xy_to_lengths(0,0,zero_left,zero_right)) — der",
                "      # Maschinennullpunkt ist exakt die Gondelposition in diesem Moment.",
                "      # Ein späteres G92 verschiebt nur das Werkstücksystem, nicht die",
                "      # Kinematik. Wer nach dem Booten erst joggt und dann nullt, bekommt",
                "      # Ankermaße, die um die Jog-Strecke daneben liegen.",
                "      #",
                "      # Was ein Ausschalten übersteht: G54 (G10 L20 P1), das liegt im NVS.",
                "      # G92 nicht — das ist flüchtig. Ein M2 verwirft G92 übrigens NICHT;",
                "      # es setzt nur coord_select auf G54 zurück.",
            ]
        body += [
            "      cycle: 0",
            "    motor0:",
            "      limit_neg_pin: NO_PIN",
            "      limit_pos_pin: NO_PIN",
            "      limit_all_pin: NO_PIN",
            "      hard_limits: false",
            "      pulloff_mm: 1.000",
        ]
        if first:
            body += [
                "      # BTT konfiguriert die TMC2160 des Rodent als tmc_5160 — die Register",
                "      # sind für den Schrittbetrieb identisch, und so ist es von BTT getestet.",
            ]
        body += [
            f"      {board.driver_section}:",
            f"        step_pin: {step}",
            f"        direction_pin: {direction}",
            f"        disable_pin: {disable}",
            *_kv(
                "        ",
                "cs_pin",
                cs,
                "Daisy-Chain: CS sitzt am ersten Treiber" if cs.upper() == "NO_PIN" else "",
            ),
            f"        spi_index: {index}",
            f"        r_sense_ohms: {_fmt(board.r_sense_ohms)}",
            *_kv(
                "        ",
                "run_amps",
                _fmt(self.run_amps),
                f"Nennstrom der {self.motor_label}, nicht erhöhen" if first else "",
            ),
            *_kv(
                "        ",
                "hold_amps",
                _fmt(self.hold_amps),
                "voller Haltestrom: die Gondel darf nicht absacken" if first else "",
            ),
            f"        microsteps: {self.motor.microsteps}",
            "        toff_disable: 0",
            "        toff_stealthchop: 5",
            "        use_enable: false",
            *_kv(
                "        ",
                "run_mode",
                "StealthChop",
                "leise — und Homing gibt es hier ohnehin nicht" if first else "",
            ),
            "        homing_mode: CoolStep",
            "        stallguard: 16",
            "        stallguard_debug: false",
            "        toff_coolstep: 3",
            "        tpfd: 4",
        ]
        return head + body

    def _axes(self) -> str:
        lines = [
            "axes:",
            f"  shared_stepper_disable_pin: {self.board.shared_stepper_disable_pin}",
            "",
            *self._axis("links", first=True),
            "",
            *self._axis("rechts", first=False),
        ]
        if self.board.spare_drivers_note:
            lines += [
                "",
                "  # Z bleibt unbenutzt: der Stift hebt über den Servo, nicht über eine Achse.",
                f"  # {self.board.spare_drivers_note}",
                "  # sind damit Reserve.",
            ]
        return "\n".join(lines)

    def _spindle(self) -> str:
        servo, board = self.servo, self.board
        lines = [
            "# Pen-Lift über Servo an der PWM-fähigen Spindel, angesteuert per M3 S<wert> / M5.",
            "# (Gemeint ist die FluidNC-Sektion `pwm:`, NICHT der Stecker mit der Aufschrift",
            "# \"PWM\" auf dem Board — siehe weiter unten.)",
            "# Bewusst NICHT M280 — das ist Marlin/Makelangelo-Konvention und existiert",
            "# in FluidNC nicht.",
            "#",
            "# Der Pin ist geklärt (Rodent User Manual V1.03, Pinbild S. 6/7 und",
            f"# Spindel-Schaltplan S. 12): {board.servo_pin} liegt als \"Sp-Enable\" auf dem",
            "# 3-poligen Stecker CN51 heraus — VCC / GND / Signal, Raster 2,54 mm.",
            "# Beschaltung dort:",
            "#",
            "#   CN51 Pin 1 --[R25 100R]-- +5V",
            "#   CN51 Pin 2 --------------- GND",
            f"#   CN51 Pin 3 --[R24 100R]-- {board.servo_pin}, ESD-Klemmdiode 3,3 V gegen GND",
            "#",
            "# Also: Signal an Pin 3, Masse an Pin 2 — aber die SERVOVERSORGUNG NICHT vom",
            "# +5-V-Pin dieses Steckers abgreifen. Da liegen ebenfalls 100 Ohm in Reihe, an",
            "# denen ein MG90S (mehrere hundert mA im Anlauf) die Spannung vollständig",
            "# zusammenbrechen lässt.",
            "#",
            "# Ein zweites Netzteil braucht es dafür aber NICHT — das stand hier eine",
            "# Zeitlang und war zu kurz gedacht. Die Platine hat einen eigenen 5-V-Zweig,",
            "# und der kommt an zwei Stellen ohne Vorwiderstand heraus:",
            "#",
            *(f"#   * {tap}" for tap in board.five_volt_taps),
            "#",
            "# Von dort die rote Servoleitung, Masse weiter an CN51 Pin 2. Dazu 470 bis",
            "# 1000 uF Elko plus 100 nF direkt an der Gondel: Ein Stiftheber zieht im Mittel",
            "# fast nichts, aber für rund 100 ms beim Hub einige hundert mA — der Puffer",
            "# nimmt genau diese Spitze auf, statt sie durch die Zuleitung zu holen.",
            "#",
            "# Wie viel der 5-V-Zweig hergibt, steht im Rodent-Handbuch nirgends. Also",
            "# nachmessen: Servo mehrfach heben und senken, dabei die 5 V beobachten. Sackt",
            "# sie unter etwa 4,5 V oder startet der ESP32 neu, kommt ein kleiner",
            "# Abwärtswandler an die Motorklemme (24 V auf 5 V, 2 bis 3 A). Auch das ist",
            "# kein zweites Netzteil, sondern ein Bauteil für wenige Euro am vorhandenen.",
            "#",
            "# Der Stecker mit der Aufschrift \"PWM\" ist NICHT der richtige: das ist ein",
            "# analoger 3-10-V-Drehzahlausgang mit Trimmpoti (VR-10K), für einen VFD",
            "# gedacht.",
            "#",
            "# Ebenfalls richtiggestellt, und diesmal in die andere Richtung: gpio.2, gpio.4",
            "# und gpio.12 sind am Rodent SEHR WOHL herausgeführt — hier stand einmal das",
            "# Gegenteil. Sie schalten die drei V-MOS-Leistungsausgänge",
            f"# ({board.vmos_voltage}). Für den Servo taugen sie trotzdem nicht: das",
            "# sind Low-Side-MOSFETs an einem eigenen Spannungseingang, keine",
            "# Logikausgänge.",
            "# Wofür sie taugen, steht beim Laserblock weiter unten.",
            "pwm:",
            *_kv("  ", "pwm_hz", servo.pwm_hz, "Servo-Takt"),
            *_kv("  ", "output_pin", board.servo_pin, board.servo_pin_note),
            "  enable_pin: NO_PIN",
            "  direction_pin: NO_PIN",
            "  disable_with_s0: false",
            "  s0_with_disable: false",
            *_wrap(
                "speed_map bildet S-Werte auf das TASTVERHÄLTNIS ab, nicht auf einen "
                f"Winkel. Bei {servo.pwm_hz} Hz ist die Periode {_de(servo.period_ms)} ms; "
                f"ein RC-Servo will {_de(servo.min_pulse_ms, minimum=1)} bis "
                f"{_de(servo.max_pulse_ms, minimum=1)} ms Impuls, also "
                f"{_de(servo.duty_pct(servo.min_pulse_ms))} % bis "
                f"{_de(servo.duty_pct(servo.max_pulse_ms))} %. Damit entspricht S<wert> "
                f"direkt Prozent des Servowegs: S{servo.s_min} = "
                f"{_de(servo.min_pulse_ms, minimum=1)} ms, S{(servo.s_min + servo.s_max) // 2} = "
                f"{_de((servo.min_pulse_ms + servo.max_pulse_ms) / 2, minimum=1)} ms, S{servo.s_max} = "
                f"{_de(servo.max_pulse_ms, minimum=1)} ms.",
                prefix="  # ",
            ),
            "  #",
            "  # Hier stand vorher `0=0.000% 100=100.000%`. Das bildete S0..S100 auf 0..20 ms",
            "  # ab — der gesamte nutzbare Servoweg lag zwischen S5 und S10, und die Werte",
            "  # aus dem Stiftkatalog (26 bis 40) fuhren den Servo in den Anschlag.",
            f"  speed_map: {servo.speed_map}",
            f"  tool_num: {servo.tool_num}",
            "",
            "# Hier stand ein Block `Laser:` mit `laser_mode: false`. Beides war falsch:",
            "# `laser_mode` ist überhaupt kein Konfigschlüssel (Lasermodus ist die Klasse",
            "# der aktiven Spindel; $32 ist ein reiner Lese-Proxy auf isRateAdjusted()),",
            "# und ein unbekannter Schlüssel setzt FluidNC in ConfigAlarm — das Board wäre",
            "# gar nicht erst gefahren. Zusätzlich hätte `Laser:` eine zweite Spindel",
            "# angelegt, ebenfalls mit tool_num 0.",
            "#",
            "# Die Spindel oben ist Klasse PWM, und die ist nicht rate-adjusted. Genau das",
            "# ist gewollt: M3 S<wert> setzt konstant, ohne mit dem Vorschub zu skalieren.",
            "",
            "# " + "-" * 75,
            "# Zweiter Werkzeugkopf (Laser)",
            "# " + "-" * 75,
            "#",
            "# FluidNC kann mehrere Spindeln in einer Datei führen; umgeschaltet wird im",
            "# Programm mit `M6 T<nummer>`. Der Wallplotter erzeugt diese Zeile, wenn beim",
            "# Laserkopf `tool_number` gesetzt ist:",
            "#",
            f"#   plot bild.svg --toolhead laser --laser-verstanden --laser-smax {self.laser.s_max}",
            "#",
            "# Zwei Dinge gehen dabei NICHT:",
            "#",
            "#   1. Beide Köpfe am selben GPIO. FluidNC belegt einen Pin exklusiv; zwei",
            f"#      Spindeln auf {board.servo_pin} lassen sich nicht einmal parsen. Der Laser",
            "#      braucht also einen eigenen Ausgang.",
            "#   2. Beide Köpfe an derselben Frequenz. Ein RC-Servo will 50 Hz, ein Laser",
            "#      braucht Kilohertz — FluidNC begrenzt `pwm_hz` einer Laser-Spindel auf",
            "#      1000 bis 100000. Wer den Stift versehentlich an eine `Laser:`-Spindel",
            "#      hängt, bekommt einen Servo, der bei jedem Leerweg in die Nullstellung",
            "#      schnappt und im Betrieb mit dem Vorschub mitflattert: eine",
            "#      rate-adjusted Spindel skaliert S mit der Geschwindigkeit.",
            "#",
            "# Sitzt der Laser physisch auf demselben Pin wie der Servo (Kopf wird",
            "# getauscht), hilft kein M6 — dann braucht es zwei config.yaml und einen",
            "# Neustart dazwischen.",
            "#",
            "# Vor dem Einschalten: `speed_map` gegen das eigene Netzteil prüfen und den",
            "# Endwert als `--laser-smax` übernehmen. Steht dort 255 und die Software",
            "# rechnet mit 1000, kommt viermal zu wenig Leistung heraus — oder umgekehrt.",
            "#",
            "# Die Luft (M8) hängt an einem V-MOS-Ausgang. Genau dafür sind die drei da:",
            f"# {board.vmos_voltage}, geschaltet von",
            "# " + ", ".join(f"{pin} ({label})" for pin, label in board.vmos_ports) + ".",
            "# Eigener Spannungseingang, also Pumpe oder Ventil direkt daran — kein",
            "# Relais, kein weiteres Netzteil. Dasselbe gilt für Absaugung oder Licht.",
        ]
        lines += self._laser_block()
        return "\n".join(lines)

    def _laser_block(self) -> list[str]:
        """Der Laserabschnitt — als YAML, wenn er an ist, sonst auskommentiert.

        Auskommentiert statt weggelassen, weil die Zeilen dann beim Nachrüsten
        schon dastehen. Beide Fassungen sind Zeile für Zeile dieselben: Wer die
        Rautezeichen entfernt, bekommt gültiges YAML — und keine Kommentare
        hinter Werten, die FluidNC in ConfigAlarm schicken würden (siehe
        :func:`_kv`).
        """
        laser = self.laser
        block = [
            "Laser:",
            *_kv("  ", "pwm_hz", laser.pwm_hz, "Klasse Laser erlaubt nur 1000..100000"),
            *_kv("  ", "output_pin", self.laser_pin, self.board.laser_pin_note),
            *_kv("  ", "enable_pin", "NO_PIN", "Freigabe der Laser-PSU, falls vorhanden"),
            *_kv("  ", "disable_with_s0", "true", "S0 zieht auch die Freigabe — sicherer"),
            "  s0_with_disable: true",
            f"  speed_map: 0=0.000% {laser.s_max}=100.000%",
            f"  tool_num: {laser.tool_num}",
        ]
        if laser.air_assist:
            block += [
                "",
                "coolant:",
                "  mist_pin: NO_PIN",
                *_kv(
                    "  ",
                    "flood_pin",
                    self.air_assist_pin,
                    "Air Assist über V-MOS, angesteuert mit M8 (aus: M9)",
                ),
                "  delay_ms: 500",
            ]

        if laser.enabled:
            return ["", *block]

        return [
            "#",
            "# Aus, solange kein Laser dranhängt — einschalten mit `--laser`.",
            "#",
            *(f"# {text}".rstrip() for text in block),
        ]

    def _control(self) -> str:
        if not self.controls:
            return "\n".join(
                [
                    "# Kein control-Block: Not-Halt, Pause und Weiter sind nicht verdrahtet.",
                    "# Damit gibt es keinen Weg, die Maschine anzuhalten — über HTTP geht es",
                    "# nicht (siehe die Begründung in docs/firmware-gegenpruefung.md), und die",
                    "# M0-Pause beim Stiftwechsel bleibt stehen.",
                    "# Erzeugt mit --ohne-taster; ohne diese Option kommt der Block zurück.",
                ]
            )

        board = self.board
        suffix = ":low" if self.control_active_low else ""

        if self.control_active_low:
            polarity = [
                "# Das `:low` unten unterstellt, dass der Eingang im Ruhezustand HIGH liegt und",
                "# der gedrückte Taster ihn nach GND zieht. Die Eingänge des Rodent sind",
                "# optogekoppelt, die Ruhelage hängt damit an der Beschaltung — vor dem ersten",
                "# Lauf mit `?` gegenprüfen: im Statusbericht listet das Feld `Pn:` genau die",
                "# Control-Eingänge, die FluidNC gerade als AKTIV sieht. Steht dort schon im",
                "# Ruhezustand ein R, H oder S, gehört das `:low` weg — dann neu erzeugen mit",
                "# `--taster-aktiv-high`.",
            ]
        else:
            polarity = [
                "# Ohne `:low` gilt ein Eingang als aktiv, sobald er HIGH liest (erzeugt mit",
                "# `--taster-aktiv-high`). Vor dem ersten Lauf mit `?` gegenprüfen: im",
                "# Statusbericht listet das Feld `Pn:` genau die Control-Eingänge, die FluidNC",
                "# gerade als AKTIV sieht. Steht dort schon im Ruhezustand ein R, H oder S,",
                "# gehört das `:low` wieder hin.",
            ]

        return "\n".join(
            [
                "# " + "-" * 75,
                "# Halt, Pause und Weiter als Taster",
                "# " + "-" * 75,
                "#",
                "# Nicht optional. Über HTTP lässt sich die Maschine derzeit nicht anhalten:",
                "# `/command?plain=` führt nur $-Kommandos aus, ein Realtime-Byte landet dort",
                "# beim leeren Kommandonamen (\"Help\") und meldet HTTP 200, ohne etwas zu tun.",
                "# Und während der Fahrt antwortet der Endpunkt ohnehin nur mit 503, solange",
                "# $HTTP/BlockDuringMotion an ist.",
                "#",
                "# Die Eingänge sind frei: weil die WallPlotter-Kinematik nicht referenzieren",
                "# kann, wird kein einziger der fünf optogekoppelten Endstop-Eingänge gebraucht.",
                "# Taster gegen GND, Spannungswahl über die SW_VCC-Brücke (CN44) auf +5V.",
                "#",
                "# ACHTUNG, Steckbrücken abziehen: Auf denselben GPIOs liegen die",
                "# DIAG-Ausgänge der Treiber, wenn die Brücken gesteckt sind —",
                "# " + ", ".join(f"{name} auf {pin}" for name, pin in board.diag_jumpers) + ".",
                "# Bleibt eine davon drin, treibt der Treiber gegen den Taster. Für einen",
                "# Wandplotter sind sie ohnehin nutzlos: StallGuard-Homing kann diese",
                "# Kinematik nicht.",
                "#",
                "# `cycle_start_pin` ist zusätzlich der einzige heute funktionierende Weg, die",
                "# M0-Pause beim Stiftwechsel aufzulösen.",
                "#",
                "# Alle Control-Eingänge müssen beim Start INAKTIV lesen — ein klemmender Taster",
                "# löst sonst gleich beim Einschalten Alarm aus. Das ist gewollt.",
                "#",
                *polarity,
                "#",
                "# Wer die Taster (noch) nicht verdrahtet hat, erzeugt die Datei mit",
                "# `--ohne-taster`; dann fehlt der Halt allerdings ersatzlos.",
                "control:",
                *_kv(
                    "  ",
                    "reset_pin",
                    f"{board.reset_pin}{suffix}",
                    f"{board.reset_pin_note} — Not-Halt, entspricht Ctrl-X",
                ),
                *_kv(
                    "  ",
                    "feed_hold_pin",
                    f"{board.feed_hold_pin}{suffix}",
                    f"{board.feed_hold_pin_note} — Pause, entspricht !",
                ),
                *_kv(
                    "  ",
                    "cycle_start_pin",
                    f"{board.cycle_start_pin}{suffix}",
                    f"{board.cycle_start_pin_note} — Weiter, entspricht ~",
                ),
            ]
        )

    def _start(self) -> str:
        return "\n".join(
            [
                "start:",
                *_kv(
                    "  ",
                    "must_home",
                    "false",
                    "Referenziert wird per Anschlag und G92, nicht per $H",
                ),
                "  deactivate_parking: true",
                "  check_limits: false",
            ]
        )

    def _buses(self) -> str:
        board = self.board
        return "\n".join(
            [
                "# Ab hier unverändert aus BTTs rodent.yaml übernommen",
                "i2so:",
                f"  bck_pin: {board.i2so_bck_pin}",
                f"  data_pin: {board.i2so_data_pin}",
                f"  ws_pin: {board.i2so_ws_pin}",
                "",
                "spi:",
                f"  miso_pin: {board.spi_miso_pin}",
                f"  mosi_pin: {board.spi_mosi_pin}",
                f"  sck_pin: {board.spi_sck_pin}",
                "",
                "sdcard:",
                f"  cs_pin: {board.sd_cs_pin}",
                f"  card_detect_pin: {board.sd_card_detect_pin}",
                f"  frequency_hz: {board.sd_frequency_hz}",
                "",
                "# Statusleuchten des Boards",
                "status_outputs:",
                f"  report_interval_ms: {board.status_interval_ms}",
                f"  idle_pin: {board.status_idle_pin}",
                f"  run_pin: {board.status_run_pin}",
                f"  alarm_pin: {board.status_alarm_pin}",
            ]
        )

    def _wifi(self) -> str:
        return "\n".join(
            [
                "# WLAN dauerhaft im Station-Modus, damit Upload und Web-UI ohne Kabel gehen.",
                "# SSID und Passwort nicht hier eintragen, sondern nach dem Flashen über das",
                "# FluidNC-Terminal setzen — sonst landen sie im Git-Repo:",
                "#   $Sta/SSID=...",
                "#   $Sta/Password=...",
                "#   $WiFi/Mode=STA",
                f"#   $Hostname={self.hostname}",
            ]
        )


def _gpio_number(pin: str) -> int | None:
    """Die Nummer eines ``gpio.N``-Pins, oder ``None`` bei allem anderen."""
    bare = pin.split(":")[0].strip().lower()
    if not bare.startswith("gpio."):
        return None
    try:
        return int(bare[5:])
    except ValueError:
        return None
