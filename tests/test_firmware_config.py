"""Die FluidNC-Konfiguration gegen die Python-Seite gegenprüfen.

Beide beschreiben dieselbe Maschine. Läuft das auseinander, plottet die Wand
etwas anderes als die Vorschau zeigt — deshalb hier festgenagelt.
"""

from pathlib import Path

import pytest

from wallplotter.kinematics import Motor, default_kinematics

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


def test_anchor_span_matches_the_analysed_geometry(config):
    wall = config["kinematics"]["WallPlotter"]
    span = wall["right_anchor_x"] - wall["left_anchor_x"]
    assert span == default_kinematics().anchors.span


def test_pen_lift_runs_on_pwm_not_on_a_laser_mode(config):
    assert config["pwm"]["pwm_hz"] == 50
    assert config["Laser"]["laser_mode"] is False


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
