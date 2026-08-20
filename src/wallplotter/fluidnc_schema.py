"""Welche Schlüssel FluidNC in der ``config.yaml`` kennt — und in welchem Abschnitt.

Warum diese Tabelle überhaupt existiert: **ein einziger unbekannter Schlüssel
legt das Board still.** FluidNC meldet ihn beim Einlesen als

.. code-block:: text

    [MSG:ERR: Ignored key <name>]

und geht in ``ConfigAlarm`` (``Configuration/ParserHandler.h`` →
``log_config_error`` → ``set_state(State::ConfigAlarm)``). Danach fährt keine
Achse mehr, und der Grund steht in einer Zeile, die längst durchgescrollt ist.
Genau so ist in diesem Projekt schon einmal ein ``laser_mode`` in die Datei
geraten, den es in FluidNC nie gab.

Ein *falscher Wert* ist dagegen harmlos: die Firmware klemmt ihn auf den
erlaubten Bereich und schreibt eine Warnung
(``NutsBolts.h:109 constrain_with_message``). Die Unterscheidung steht deshalb
auch hier drin — ein geklemmter Wert ist ein Hinweis, ein unbekannter Schlüssel
ein Abbruch.

Herkunft der Tabelle
--------------------
Mechanisch aus dem Quelltext gezogen, nicht aus dem Wiki abgeschrieben (das
driftet, und zum Zeitpunkt der Prüfung antwortete es mit 503). Quelle ist
``github.com/bdring/FluidNC``, Stand ``8a0f8c8`` vom 17.08.2026:

.. code-block:: console

    grep -rn 'handler\.item(\|handler\.section(' FluidNC/src --include=*.cpp --include=*.h
    grep -rn 'InstanceBuilder<'                  FluidNC/src --include=*.cpp --include=*.h

Der erste Aufruf liefert Schlüssel samt Wertebereich, der zweite die Namen der
Abschnitte, die über eine Fabrik entstehen (Spindeln, Kinematiken, Treiber,
Module). Jeder Eintrag unten trägt die Fundstelle mit.

Vollständigkeit
---------------
Abgebildet sind der Wurzelabschnitt und alles, was ein Wandplotter braucht.
Abschnitte, die hier fehlen, gelten als **ungeprüft** und nicht als falsch —
:func:`check_mapping` sagt das ausdrücklich, statt eine Vollständigkeit zu
behaupten, die diese Datei nicht hat.

Groß- und Kleinschreibung ist FluidNC egal: ``Parser::is()`` vergleicht mit
``strncasecmp`` (``Configuration/Parser.cpp:27``). Der Vergleich hier ebenso.

Zwei Prüfungen, zwei Blickwinkel
--------------------------------
:func:`check_mapping` sieht die Datei so, wie ein YAML-Parser sie sieht: als
Baum aus Abschnitten und Schlüsseln. Das findet erfundene Schlüssel.

:func:`check_lines` sieht sie so, wie **FluidNCs eigener Tokenizer** sie sieht:
Zeile für Zeile, mit dessen Eigenheiten. Und die weichen von YAML ab — am
folgenreichsten beim Kommentar hinter einem Wert, den FluidNC nicht abschneidet.
Ein YAML-Parser sieht dort nichts Auffälliges; das Board geht in ConfigAlarm.
Deshalb beide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Item",
    "Node",
    "Finding",
    "ERROR",
    "WARN",
    "INFO",
    "NODES",
    "resolve",
    "check_mapping",
    "check_lines",
    "SOURCE_REVISION",
]

SOURCE_REVISION = "bdring/FluidNC 8a0f8c8 (17.08.2026)"

ERROR = "fehler"
"""Unbekannter Schlüssel — FluidNC geht damit in ConfigAlarm."""

WARN = "warnung"
"""Wert außerhalb des Bereichs — die Firmware klemmt ihn und fährt weiter."""

INFO = "hinweis"
"""Abschnitt, den diese Tabelle nicht abbildet. Keine Aussage, kein Vorwurf."""


@dataclass(frozen=True)
class Item:
    """Ein einzelner Schlüssel mit dem Bereich, auf den FluidNC ihn klemmt."""

    source: str
    minimum: float | None = None
    maximum: float | None = None

    def complain(self, value: Any) -> str | None:
        """Beanstandung, falls der Wert außerhalb des Bereichs liegt."""
        if self.minimum is None and self.maximum is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if self.minimum is not None and value < self.minimum:
            return f"{value} liegt unter {self.minimum}"
        if self.maximum is not None and value > self.maximum:
            return f"{value} liegt über {self.maximum}"
        return None


@dataclass(frozen=True)
class Node:
    """Ein Abschnitt: die Schlüssel darin und die Abschnitte darunter."""

    items: dict[str, Item] = field(default_factory=dict)
    children: dict[str, str] = field(default_factory=dict)
    """Abschnittsname → Name des Knotens in :data:`NODES`."""

    pattern_children: tuple[tuple[str, str], ...] = ()
    """Kinder mit laufender Nummer, z. B. ``motor0``/``motor1`` → ``("motor", "motor")``."""

    open_children: bool = False
    """``True``: unbekannte Unterabschnitte sind hier normal (Wurzel, Motor)."""


# ---------------------------------------------------------------------------
# Die Tabelle
# ---------------------------------------------------------------------------

_AXIS_NAMES = ("x", "y", "z", "a", "b", "c", "u", "v", "w")
"""``Machine/Axes.cpp:20`` — mehr Achsnamen kennt FluidNC nicht."""

_SPINDLE_SECTIONS = (
    "PWM",
    "Laser",
    "OnOff",
    "Relay",
    "DAC",
    "BESC",
    "HBridge",
    "10V",
    "NoSpindle",
    "PlasmaSpindle",
    "ModbusVFD",
    "H100",
    "H2A",
    "Huanyang",
    "YL620",
    "NowForever",
    "DeltaMS300",
    "DanfossVLT2800",
    "SiemensV20",
    "FolinnBD600",
    "MollomG70",
)
"""``Spindles/*.cpp`` — ``SpindleFactory::InstanceBuilder<…> registration("…")``."""

_MOTOR_SECTIONS = (
    "tmc_2130",
    "tmc_2160",
    "tmc_2160Pro",
    "tmc_2208",
    "tmc_2209",
    "tmc_5160",
    "tmc_5160Pro",
    "standard_stepper",
    "stepstick",
    "rc_servo",
    "solenoid",
    "dynamixel2",
    "null_motor",
)
"""``Motors/*.cpp`` — ``MotorFactory::InstanceBuilder<…>``."""

_KINEMATICS_SECTIONS = ("Cartesian", "CoreXY", "WallPlotter", "midtbot", "parallel_delta")
"""``Kinematics/*.cpp`` — ``KinematicsFactory::InstanceBuilder<…>``."""

# Was jede Spindel gemeinsam hat: Spindles/Spindle.h:112ff
_SPINDLE_COMMON = {
    "tool_num": Item("Spindles/Spindle.h:112", 0, 100),
    "speed_map": Item("Spindles/Spindle.h:122"),
    "off_on_alarm": Item("Spindles/Spindle.h:129"),
    "atc": Item("Spindles/Spindle.h:137"),
    "m6_macro": Item("Spindles/Spindle.h:145"),
    "s0_with_disable": Item("Spindles/Spindle.h:152"),
    "disable_with_s0": Item("Spindles/Spindle.h:159"),
    "spinup_ms": Item("Spindles/Spindle.h:173", 0, 60000),
    "spindown_ms": Item("Spindles/Spindle.h:179", 0, 60000),
    # OnOffSpindle ist die Basisklasse von PWM und Laser — deren Pins gelten mit
    "output_pin": Item("Spindles/OnOffSpindle.h:28"),
    "enable_pin": Item("Spindles/OnOffSpindle.h:34"),
    "direction_pin": Item("Spindles/OnOffSpindle.h:66"),
}

NODES: dict[str, Node] = {
    "root": Node(
        items={
            "board": Item("Machine/MachineConfig.cpp:44"),
            "name": Item("Machine/MachineConfig.cpp:50"),
            "meta": Item("Machine/MachineConfig.cpp:56"),
            "arc_tolerance_mm": Item("Machine/MachineConfig.cpp:113", 0.001, 1.0),
            "junction_deviation_mm": Item("Machine/MachineConfig.cpp:123", 0.01, 1.0),
            "verbose_errors": Item("Machine/MachineConfig.cpp:130"),
            "report_inches": Item("Machine/MachineConfig.cpp:137"),
            "enable_parking_override_control": Item("Machine/MachineConfig.cpp:147"),
            "use_line_numbers": Item("Machine/MachineConfig.cpp:156"),
            "planner_blocks": Item("Machine/MachineConfig.cpp:163", 10, 120),
        },
        children={
            "stepping": "stepping",
            "i2so": "i2so",
            "spi": "spi",
            "sdcard": "sdcard",
            "kinematics": "kinematics",
            "axes": "axes",
            "control": "control",
            "coolant": "coolant",
            "probe": "probe",
            "start": "start",
            "parking": "parking",
            "status_outputs": "status_outputs",
            "user_outputs": "user_outputs",
            "macros": "macros",
            **{name: "spindle" for name in _SPINDLE_SECTIONS},
        },
        # ethernet, extenders, user_inputs, uart1…, uart_channel1…, oled, rgbled,
        # atc_manual und die Netzwerkmodule sind echte Abschnitte, stehen hier aber
        # nicht im Detail — deshalb offen statt falsch.
        open_children=True,
    ),
    "stepping": Node(
        items={
            "engine": Item("Stepping.cpp:213"),
            "idle_ms": Item("Stepping.cpp:222", 0, 10000000),
            "pulse_us": Item("Stepping.cpp:231", 0, 30),
            "dir_delay_us": Item("Stepping.cpp:238", 0, 10),
            "disable_delay_us": Item("Stepping.cpp:245", 0, 1000000),
            "segments": Item("Stepping.cpp:253", 6, 20),
        }
    ),
    "i2so": Node(
        items={
            "bck_pin": Item("Machine/I2SOBus.cpp:27"),
            "data_pin": Item("Machine/I2SOBus.cpp:33"),
            "ws_pin": Item("Machine/I2SOBus.cpp:40"),
            "min_pulse_us": Item("Machine/I2SOBus.cpp:47"),
            "oe_pin": Item("Machine/I2SOBus.cpp:53"),
        }
    ),
    "spi": Node(
        items={
            "miso_pin": Item("Machine/SPIBus.cpp:59"),
            "mosi_pin": Item("Machine/SPIBus.cpp:66"),
            "sck_pin": Item("Machine/SPIBus.cpp:72"),
        }
    ),
    "sdcard": Node(
        items={
            "cs_pin": Item("SDCard.h:53"),
            "card_detect_pin": Item("SDCard.h:60"),
            "frequency_hz": Item("SDCard.h:67", 400000, 20000000),
        }
    ),
    "kinematics": Node(children={name: "kinematic" for name in _KINEMATICS_SECTIONS}),
    "kinematic": Node(
        items={
            # WallPlotter — Kinematics/WallPlotter.cpp:20ff. Keiner der sechs
            # Werte ist begrenzt; left_axis/right_axis sind int32_t und meinen
            # den Achsindex (0 = X), nicht den Achsbuchstaben.
            "left_axis": Item("Kinematics/WallPlotter.cpp:20"),
            "left_anchor_x": Item("Kinematics/WallPlotter.cpp:26"),
            "left_anchor_y": Item("Kinematics/WallPlotter.cpp:32"),
            "right_axis": Item("Kinematics/WallPlotter.cpp:39"),
            "right_anchor_x": Item("Kinematics/WallPlotter.cpp:45"),
            "right_anchor_y": Item("Kinematics/WallPlotter.cpp:51"),
            "segment_length": Item("Kinematics/WallPlotter.cpp:58"),
            # CoreXY / midtbot
            "x_scaler": Item("Kinematics/CoreXY.cpp:34", 0.1, 10.0),
            # parallel_delta
            "crank_mm": Item("Kinematics/ParallelDelta.cpp:67", 50.0, 500.0),
            "base_triangle_mm": Item("Kinematics/ParallelDelta.cpp:73", 20.0, 500.0),
            "linkage_mm": Item("Kinematics/ParallelDelta.cpp:79", 20.0, 500.0),
            "end_effector_triangle_mm": Item("Kinematics/ParallelDelta.cpp:85", 20.0, 500.0),
            "kinematic_segment_len_mm": Item("Kinematics/ParallelDelta.cpp:94", 0.05, 20.0),
            "use_servos": Item("Kinematics/ParallelDelta.cpp:101"),
            "up_degrees": Item("Kinematics/ParallelDelta.cpp:108", -90, 0),
        }
    ),
    "axes": Node(
        items={
            "shared_stepper_disable_pin": Item("Machine/Axes.cpp:173"),
            "shared_stepper_reset_pin": Item("Machine/Axes.cpp:182"),
            "homing_runs": Item("Machine/Axes.cpp:189", 1, 5),
        },
        children={name: "axis" for name in _AXIS_NAMES},
    ),
    "axis": Node(
        items={
            "steps_per_mm": Item("Machine/Axis.cpp:18", 0.001, 100000.0),
            "max_rate_mm_per_min": Item("Machine/Axis.cpp:24", 0.001, 250000.0),
            "acceleration_mm_per_sec2": Item("Machine/Axis.cpp:30", 0.001, 100000.0),
            "max_travel_mm": Item("Machine/Axis.cpp:39", 0.1, 10000000.0),
            "soft_limits": Item("Machine/Axis.cpp:48"),
            "idle_disable": Item("Machine/Axis.cpp:56"),
        },
        children={"homing": "homing"},
        # MAX_MOTORS_PER_AXIS ist 2 (Machine/Axis.h:28) — motor2 gäbe es nicht.
        pattern_children=(("motor0", "motor"), ("motor1", "motor")),
    ),
    "homing": Node(
        items={
            "cycle": Item("Machine/Homing.h:80"),
            "allow_single_axis": Item("Machine/Homing.h:88"),
            "positive_direction": Item("Machine/Homing.h:95"),
            "mpos_mm": Item("Machine/Homing.h:103"),
            "feed_mm_per_min": Item("Machine/Homing.h:112", 1.0, 100000.0),
            "seek_mm_per_min": Item("Machine/Homing.h:119", 1.0, 100000.0),
            "settle_ms": Item("Machine/Homing.h:126", 0, 1000),
            "seek_scaler": Item("Machine/Homing.h:134", 1.0, 100.0),
            "feed_scaler": Item("Machine/Homing.h:144", 1.0, 100.0),
        }
    ),
    "motor": Node(
        items={
            "limit_neg_pin": Item("Machine/Motor.cpp:22"),
            "limit_pos_pin": Item("Machine/Motor.cpp:29"),
            "limit_all_pin": Item("Machine/Motor.cpp:39"),
            "hard_limits": Item("Machine/Motor.cpp:46"),
            "pulloff_mm": Item("Machine/Motor.cpp:53", 0.1, 100000.0),
        },
        children={name: "driver" for name in _MOTOR_SECTIONS},
    ),
    "driver": Node(
        items={
            # StandardStepper — Basis aller Schritttreiber
            "step_pin": Item("Motors/StandardStepper.h:42"),
            "direction_pin": Item("Motors/StandardStepper.h:48"),
            "disable_pin": Item("Motors/StandardStepper.h:55"),
            # TrinamicBase
            "r_sense_ohms": Item("Motors/TrinamicBase.h:91", 0.0, 1.0),
            "run_amps": Item("Motors/TrinamicBase.h:97", 0.05, 10.0),
            "hold_amps": Item("Motors/TrinamicBase.h:103", 0.05, 10.0),
            "microsteps": Item("Motors/TrinamicBase.h:109", 1, 256),
            "toff_disable": Item("Motors/TrinamicBase.h:114", 0, 15),
            "toff_stealthchop": Item("Motors/TrinamicBase.h:119", 2, 15),
            "use_enable": Item("Motors/TrinamicBase.h:126"),
            # TrinamicSpiDriver
            "cs_pin": Item("Motors/TrinamicSpiDriver.h:64"),
            "spi_index": Item("Motors/TrinamicSpiDriver.h:72", -1, 127),
            "run_mode": Item("Motors/TrinamicSpiDriver.h:79"),
            "homing_mode": Item("Motors/TrinamicSpiDriver.h:85"),
            "stallguard": Item("Motors/TrinamicSpiDriver.h:92", -64, 63),
            "stallguard_debug": Item("Motors/TrinamicSpiDriver.h:98"),
            "toff_coolstep": Item("Motors/TrinamicSpiDriver.h:103", 2, 15),
            "diag0_error": Item("Motors/TrinamicSpiDriver.h:109"),
            "diag0_otpw": Item("Motors/TrinamicSpiDriver.h:115"),
            "diag0_int_pushpull": Item("Motors/TrinamicSpiDriver.h:121"),
            # TMC5160Driver — genau der Treiber, unter dem der Rodent läuft
            "tpfd": Item("Motors/TMC5160Driver.h:34", 0, 15),
            # TrinamicUartDriver
            "addr": Item("Motors/TrinamicUartDriver.h:41"),
            "uart_num": Item("Motors/TrinamicUartDriver.h:57"),
            "homing_amps": Item("Motors/TMC2209Driver.h:57", 0.0, 10.0),
            "shared_address_write_only": Item("Motors/TMC2209Driver.h:89"),
            # StepStick
            "ms1_pin": Item("Motors/StepStick.cpp:33"),
            "ms2_pin": Item("Motors/StepStick.cpp:39"),
            "ms3_pin": Item("Motors/StepStick.cpp:45"),
            "reset_pin": Item("Motors/StepStick.cpp:52"),
            # RcServo / Solenoid
            "output_pin": Item("Motors/RcServo.h:54"),
            "pwm_hz": Item("Motors/RcServo.h:61"),
            "min_pulse_us": Item("Motors/RcServo.h:67", 500, 2500),
            "max_pulse_us": Item("Motors/RcServo.h:74", 500, 2500),
            "timer_ms": Item("Motors/RcServo.h:80", 20, 250),
            "off_percent": Item("Motors/Solenoid.h:66", 0.0, 100.0),
            "pull_percent": Item("Motors/Solenoid.h:73", 0.0, 100.0),
            "hold_percent": Item("Motors/Solenoid.h:81", 0.0, 100.0),
            "pull_ms": Item("Motors/Solenoid.h:88", 0, 3000),
            "direction_invert": Item("Motors/Solenoid.h:94"),
        }
    ),
    "spindle": Node(
        items={
            **_SPINDLE_COMMON,
            # PWMSpindle.h:57 erlaubt 1..20000000, LaserSpindle.h:41 nur
            # 1000..100000. Der engere Bereich steckt in check_mapping(), weil er
            # vom Abschnittsnamen abhängt und nicht vom Schlüssel.
            "pwm_hz": Item("Spindles/PWMSpindle.h:57", 1, 20000000),
            "output_cw_pin": Item("Spindles/HBridgeSpindle.h:77"),
            "output_ccw_pin": Item("Spindles/HBridgeSpindle.h:84"),
            "forward_pin": Item("Spindles/10vSpindle.h:51"),
            "reverse_pin": Item("Spindles/10vSpindle.h:58"),
            "arc_ok_pin": Item("Spindles/PlasmaSpindle.h:53"),
            "arc_wait_ms": Item("Spindles/PlasmaSpindle.h:60", 0, 3000),
            "min_pulse_us": Item("Spindles/BESCSpindle.h:79", 500, 3000),
            "max_pulse_us": Item("Spindles/BESCSpindle.h:86", 500, 3000),
            "uart_num": Item("Spindles/VFDSpindle.cpp:238"),
            "modbus_id": Item("Spindles/VFDSpindle.cpp:254", 0, 247),
            "debug": Item("Spindles/VFDSpindle.cpp:260", 0, 5),
            "poll_ms": Item("Spindles/VFDSpindle.cpp:265", 250, 20000),
            "retries": Item("Spindles/VFDSpindle.cpp:270"),
        },
        children={"uart": "uart"},
    ),
    "uart": Node(
        items={
            "txd_pin": Item("Uart.cpp:301"),
            "rxd_pin": Item("Uart.cpp:310"),
            "rts_pin": Item("Uart.cpp:317"),
            "cts_pin": Item("Uart.cpp:324"),
            "baud": Item("Uart.cpp:335", 2400, 10000000),
            "mode": Item("Uart.cpp:344"),
            "passthrough_baud": Item("Uart.cpp:351", 0, 10000000),
            "passthrough_mode": Item("Uart.cpp:358"),
        }
    ),
    "control": Node(
        items={
            # Control.cpp:29ff — die Reihenfolge ist die der ControlPin-Liste
            "safety_door_pin": Item("Control.cpp:29"),
            "reset_pin": Item("Control.cpp:35"),
            "feed_hold_pin": Item("Control.cpp:42"),
            "cycle_start_pin": Item("Control.cpp:49"),
            "macro0_pin": Item("Control.cpp:56"),
            "macro1_pin": Item("Control.cpp:63"),
            "macro2_pin": Item("Control.cpp:70"),
            "macro3_pin": Item("Control.cpp:77"),
            "fault_pin": Item("Control.cpp:87"),
            "estop_pin": Item("Control.cpp:95"),
            "homing_button_pin": Item("Control.cpp:101"),
        }
    ),
    "coolant": Node(
        items={
            "flood_pin": Item("CoolantControl.cpp:94"),
            "mist_pin": Item("CoolantControl.cpp:100"),
            "delay_ms": Item("CoolantControl.cpp:108", 0, 10000),
        }
    ),
    "probe": Node(
        items={
            "pin": Item("Probe.cpp:40"),
            "toolsetter_pin": Item("Probe.cpp:49"),
            "check_mode_start": Item("Probe.cpp:58"),
            "hard_stop": Item("Probe.cpp:67"),
            "probe_hard_limit": Item("Probe.cpp:76"),
        }
    ),
    "start": Node(
        items={
            "must_home": Item("Machine/MachineConfig.h:66"),
            "deactivate_parking": Item("Machine/MachineConfig.h:75"),
            "check_limits": Item("Machine/MachineConfig.h:82"),
        }
    ),
    "parking": Node(
        items={
            "enable": Item("Parking.cpp:202"),
            "axis": Item("Parking.cpp:210"),
            "target_mpos_mm": Item("Parking.cpp:217"),
            "rate_mm_per_min": Item("Parking.cpp:224"),
            "pullout_distance_mm": Item("Parking.cpp:231"),
            "pullout_rate_mm_per_min": Item("Parking.cpp:238"),
        }
    ),
    "status_outputs": Node(
        items={
            "report_interval_ms": Item("Status_outputs.h:60", 100, 5000),
            "idle_pin": Item("Status_outputs.h:66"),
            "run_pin": Item("Status_outputs.h:72"),
            "hold_pin": Item("Status_outputs.h:78"),
            "alarm_pin": Item("Status_outputs.h:84"),
            "door_pin": Item("Status_outputs.h:90"),
        }
    ),
    "user_outputs": Node(
        items={
            **{
                f"analog{i}_pin": Item(f"Machine/UserOutputs.cpp:{84 + 6 * i}")
                for i in range(4)
            },
            **{
                f"analog{i}_hz": Item(f"Machine/UserOutputs.cpp:{107 + 5 * i}", 1, 20000000)
                for i in range(4)
            },
            **{
                f"digital{i}_pin": Item(f"Machine/UserOutputs.cpp:{128 + 6 * i}")
                for i in range(8)
            },
        }
    ),
    # Sonderfall: die Schlüssel stehen nicht als Zeichenkette im item()-Aufruf,
    # sondern kommen aus dem Makro selbst (`item(_macro[0].name(), …)`,
    # Machine/Macros.h:43ff). Die Namen stehen deshalb in Macros.cpp — und dort
    # mit großem M ("Macro0"), was folgenlos ist: FluidNC vergleicht
    # unabhängig von der Schreibweise.
    "macros": Node(
        items={
            "startup_line0": Item("Machine/Macros.cpp:31"),
            "startup_line1": Item("Machine/Macros.cpp:39"),
            "macro0": Item("Machine/Macros.cpp:48"),
            "macro1": Item("Machine/Macros.cpp:54"),
            "macro2": Item("Machine/Macros.cpp:60"),
            "macro3": Item("Machine/Macros.cpp:66"),
            "after_homing": Item("Machine/Macros.cpp:75"),
            "after_reset": Item("Machine/Macros.cpp:83"),
            "after_unlock": Item("Machine/Macros.cpp:90"),
        }
    ),
}

_LASER_PWM_RANGE = (1000, 100000)
"""``Spindles/LaserSpindle.h:41`` — enger als die 1..20 MHz der PWM-Spindel."""


@dataclass(frozen=True)
class Finding:
    """Eine Beanstandung samt Pfad in der Datei."""

    level: str
    path: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}: {self.text}"


def resolve(node_name: str, section: str) -> str | None:
    """Knotenname des Unterabschnitts ``section``, oder ``None``.

    Der Vergleich ist wie in FluidNC unabhängig von Groß- und Kleinschreibung.
    """
    node = NODES.get(node_name)
    if node is None:
        return None
    lowered = section.lower()
    for name, target in node.children.items():
        if name.lower() == lowered:
            return target
    for name, target in node.pattern_children:
        if name.lower() == lowered:
            return target
    return None


def _known_item(node: Node, key: str) -> Item | None:
    lowered = key.lower()
    for name, item in node.items.items():
        if name.lower() == lowered:
            return item
    return None


def check_mapping(data: Any, node_name: str = "root", path: str = "") -> list[Finding]:
    """Eine eingelesene ``config.yaml`` gegen die Tabelle halten.

    Erwartet das Ergebnis von ``yaml.safe_load``. Zurück kommen Beanstandungen
    in Lesereihenfolge; eine leere Liste heißt: gegen diese Tabelle ist nichts
    einzuwenden — nicht, dass die Maschine damit fährt.
    """
    findings: list[Finding] = []
    if not isinstance(data, dict):
        return findings
    node = NODES.get(node_name)
    if node is None:
        return findings

    for key, value in data.items():
        key = str(key)
        here = f"{path}/{key}" if path else key

        if isinstance(value, dict):
            child = resolve(node_name, key)
            if child is not None:
                findings += check_mapping(value, child, here)
                continue
            # Ein Abschnitt, den die Tabelle nicht führt. In der Wurzel ist das
            # normal (Netzwerkmodule, Extender, UARTs); tiefer unten ist es ein
            # Schlüssel, den FluidNC nicht kennt.
            if node.open_children:
                findings.append(
                    Finding(INFO, here, "Abschnitt nicht in der Tabelle — ungeprüft")
                )
            else:
                findings.append(
                    Finding(
                        ERROR,
                        here,
                        f"unbekannter Abschnitt — FluidNC meldet »Ignored key {key}« "
                        "und geht in ConfigAlarm",
                    )
                )
            continue

        item = _known_item(node, key)
        if item is None:
            findings.append(
                Finding(
                    ERROR,
                    here,
                    f"unbekannter Schlüssel — FluidNC meldet »Ignored key {key}« "
                    "und geht in ConfigAlarm",
                )
            )
            continue

        minimum, maximum = item.minimum, item.maximum
        if key.lower() == "pwm_hz" and path.lower().startswith("laser"):
            minimum, maximum = _LASER_PWM_RANGE
            item = Item(item.source, minimum, maximum)
        complaint = item.complain(value)
        if complaint:
            findings.append(
                Finding(WARN, here, f"{complaint} — FluidNC klemmt den Wert und warnt")
            )

    return findings


# ---------------------------------------------------------------------------
# Wie FluidNC die Zeilen liest — und wo das von YAML abweicht
# ---------------------------------------------------------------------------

_IDENTIFIER = re.compile(r"[A-Za-z0-9_]")


def _is_number(text: str) -> bool:
    """Wie ``string_util::from_decimal``/``from_float``: die *ganze* Zeichenkette."""
    try:
        int(text)
        return True
    except ValueError:
        pass
    try:
        float(text)
        return True
    except ValueError:
        return False


def check_lines(text: str) -> list[Finding]:
    """Die Datei durch FluidNCs eigenen Tokenizer sehen.

    Nachgebaut ist ``Configuration/Tokenizer.cpp`` — ``nextLine()``,
    ``parseKey()``, ``parseValue()`` — und die Wertumwandlung aus
    ``Configuration/Parser.cpp``. Gesucht wird das, was ein YAML-Parser
    **nicht** bemerkt, weil es an FluidNCs Abweichungen von YAML hängt.

    Die wichtigste davon: **Kommentare am Zeilenende werden nicht abgeschnitten.**
    Verworfen wird nur eine Zeile, die mit ``#`` *beginnt* (Tokenizer.cpp:103).
    Ein unquotierter Wert dagegen reicht bis zum Zeilenende
    (``_token._value = _line;``, Tokenizer.cpp:139). Was daraus wird, hängt am
    Typ des Schlüssels — und der schlimmste Fall ist der stille:

    ==========  ==========================  ====================================
    Wert        Umwandlung                  Folge
    ==========  ==========================  ====================================
    Zahl        ``intValue``/``floatValue``  ``parseError`` → **ConfigAlarm**
    true/false  ``boolValue``               stillschweigend ``false``
    Pin         ``Pin::create``             ErrorPin, ``Setting up pin … failed``
    Text        ``stringValue``             Kommentar wird Teil des Werts
    ==========  ==========================  ====================================

    Nicht betroffen ist ein **quotierter** Wert: ``parseValue()`` liest bis zum
    schließenden Anführungszeichen und wirft den Rest der Zeile weg. ``name:
    "Wand" # Notiz`` geht also. Sauberer ist der Kommentar in der Zeile darüber
    — so hält es auch BTTs eigene ``rodent.yaml``, in der hinter keinem einzigen
    Wert ein Kommentar steht.
    """
    findings: list[Finding] = []
    stack: list[tuple[int, str]] = []

    for nummer, roh in enumerate(text.split("\n"), 1):
        zeile = roh.rstrip("\r")
        if not zeile:
            continue

        einzug = len(zeile) - len(zeile.lstrip(" "))
        zeile = zeile[einzug:]
        if not zeile:
            continue
        if zeile.startswith("\t"):
            findings.append(
                Finding(ERROR, f"Zeile {nummer}", "Tabulator als Einzug — FluidNC verlangt Leerzeichen")
            )
            continue
        if zeile.startswith("#"):
            continue

        if not _IDENTIFIER.match(zeile[0]):
            findings.append(
                Finding(ERROR, f"Zeile {nummer}", f"unerwartetes Zeichen {zeile[0]!r} am Zeilenanfang")
            )
            continue

        key, trenner, rest = zeile.partition(":")
        if not trenner:
            findings.append(
                Finding(ERROR, f"Zeile {nummer}", f"»{key.strip()}« ohne Doppelpunkt")
            )
            continue

        key = key.rstrip(" \t\f\r")
        while stack and stack[-1][0] >= einzug:
            stack.pop()
        pfad = "/".join(name for _tiefe, name in stack + [(einzug, key)])

        wert = rest.lstrip(" \t\f\r")
        if not wert:
            stack.append((einzug, key))
            continue
        if wert[0] in "\"'":
            # parseValue() liest bis zum schließenden Zeichen und verwirft den Rest
            if wert.find(wert[0], 1) == -1:
                findings.append(
                    Finding(ERROR, pfad, f"Zeile {nummer}: kein schließendes {wert[0]}")
                )
            continue

        vorn, trenn, _hinten = wert.partition(" #")
        if not trenn:
            continue
        vorn = vorn.strip()
        if _is_number(vorn):
            findings.append(
                Finding(
                    ERROR,
                    pfad,
                    f"Zeile {nummer}: Kommentar hinter dem Wert. FluidNC schneidet ihn nicht ab, "
                    f"liest »{wert.strip()}« als Zahl, scheitert und geht in ConfigAlarm. "
                    "Kommentar in die Zeile darüber.",
                )
            )
        elif vorn.lower() in ("true", "false"):
            findings.append(
                Finding(
                    ERROR,
                    pfad,
                    f"Zeile {nummer}: Kommentar hinter dem Wert. boolValue() vergleicht die "
                    "ganze Zeile mit »true« — mit einem Kommentar dahinter kommt nie true "
                    "heraus, gleich was dasteht, und gemeldet wird es auch nicht. "
                    "Kommentar in die Zeile darüber.",
                )
            )
        else:
            findings.append(
                Finding(
                    WARN,
                    pfad,
                    f"Zeile {nummer}: Kommentar hinter dem Wert wird Teil des Werts "
                    f"(»{wert.strip()}«). Bei einem Pin gibt das »Setting up pin … failed«. "
                    "Kommentar in die Zeile darüber.",
                )
            )

    return findings

