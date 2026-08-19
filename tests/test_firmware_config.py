"""Die FluidNC-Konfiguration gegen die Python-Seite gegenprüfen.

Beide beschreiben dieselbe Maschine. Läuft das auseinander, plottet die Wand
etwas anderes als die Vorschau zeigt — deshalb hier festgenagelt.
"""

from pathlib import Path

import pytest

from wallplotter.kinematics import Motor

yaml = pytest.importorskip("yaml")

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "fluidnc-wallplotter.yaml"


@pytest.fixture(scope="module")
def config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_uses_wallplotter_kinematics(config):
    assert "WallPlotter" in config["kinematics"]


def test_both_belts_are_mapped_to_a_motor(config):
    wall = config["kinematics"]["WallPlotter"]
    assert wall["left_axis"] == 0
    assert wall["right_axis"] == 1


def test_steps_per_mm_matches_the_pulley_maths(config):
    for axis in ("x", "y"):
        assert config["axes"][axis]["steps_per_mm"] == Motor().steps_per_mm


def test_microsteps_match_the_assumed_resolution(config):
    for axis in ("x", "y"):
        driver = config["axes"][axis]["motor0"]["tmc_5160"]
        assert driver["microsteps"] == Motor().microsteps


def test_motor_current_stays_at_the_rated_1_2_amps(config):
    for axis in ("x", "y"):
        driver = config["axes"][axis]["motor0"]["tmc_5160"]
        assert driver["run_amps"] == 1.2
        assert driver["hold_amps"] == 1.2


def test_anchors_are_plausible_and_marked_as_location_specific(config):
    wall = config["kinematics"]["WallPlotter"]
    assert wall["right_anchor_x"] > wall["left_anchor_x"]
    # beide Anker gleich hoch und oberhalb des Nullpunkts
    assert wall["left_anchor_y"] == wall["right_anchor_y"] > 0
    assert "STANDORTABHÄNGIG" in CONFIG_PATH.read_text(encoding="utf-8")


def test_pen_lift_runs_on_pwm_not_on_a_laser_mode(config):
    assert config["pwm"]["pwm_hz"] == 50
    # Hier stand `config["Laser"]["laser_mode"] is False` — und damit prüfte der
    # Test genau das, was das Board lahmlegt: `laser_mode` ist kein
    # Konfigschlüssel von FluidNC, und ein unbekannter Schlüssel setzt die
    # Firmware in ConfigAlarm (ParserHandler.h -> log_config_error ->
    # set_state(State::ConfigAlarm)). Ein Abschnitt `Laser:` legte außerdem eine
    # zweite, rate-adjusted Spindel an — genau das, was ein Servo nicht will.
    assert "Laser" not in config


def test_the_servo_speed_map_covers_the_rc_pulse_window(config):
    """S-Werte sind Tastverhältnis, nicht Winkel.

    Bei 50 Hz ist die Periode 20 ms, ein RC-Servo will 1,0 bis 2,0 ms — also
    5 bis 10 %. Bildet die speed_map auf 0..100 % ab, liegt der ganze
    Servoweg zwischen S5 und S10, und der Stiftkatalog (26 bis 40) fährt in
    den Anschlag.
    """
    period_ms = 1000.0 / config["pwm"]["pwm_hz"]
    entries = dict(
        (float(value.rstrip("%")), float(key))
        for key, _, value in (part.partition("=") for part in config["pwm"]["speed_map"].split())
    )
    for percent, s_value in entries.items():
        pulse_ms = period_ms * percent / 100.0
        assert 0.8 <= pulse_ms <= 2.5, (
            f"S{s_value:.0f} ergäbe {pulse_ms:.2f} ms Impuls — außerhalb des Servofensters"
        )


def test_there_is_a_way_to_stop_the_machine(config):
    """Über HTTP geht es nicht, also muss es über Taster gehen.

    `/command?plain=` führt nur $-Kommandos aus; ein Realtime-Byte landet dort
    beim leeren Kommandonamen und meldet Erfolg, ohne etwas zu tun. Bleibt der
    control-Block — die Endstop-Eingänge sind ohnehin frei, weil die
    WallPlotter-Kinematik nicht referenzieren kann.
    """
    control = config["control"]
    for key in ("reset_pin", "feed_hold_pin", "cycle_start_pin"):
        assert control[key] != "NO_PIN"
        assert control[key].startswith("gpio.")


def test_homing_is_done_by_hand_not_by_endstops(config):
    assert config["start"]["must_home"] is False
    for axis in ("x", "y"):
        motor = config["axes"][axis]["motor0"]
        assert motor["limit_pos_pin"] == "NO_PIN"
        assert motor["limit_neg_pin"] == "NO_PIN"


def test_no_wifi_credentials_are_committed():
    text = CONFIG_PATH.read_text(encoding="utf-8").lower()
    assert "password:" not in text
    assert "psk" not in text
