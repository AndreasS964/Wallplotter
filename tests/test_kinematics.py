import math

import pytest

from wallplotter.kinematics import (
    Anchors,
    Gondola,
    Motor,
    WallPlotterKinematics,
    analyze_area,
    default_kinematics,
)

# Anker 1000 mm auseinander, 1000 mm über dem Ursprung
ANCHORS = Anchors(left_x=0.0, right_x=1000.0, y=1000.0)


def kin(**kwargs) -> WallPlotterKinematics:
    return WallPlotterKinematics(ANCHORS, **kwargs)


def test_anchors_must_be_ordered():
    with pytest.raises(ValueError):
        Anchors(left_x=100.0, right_x=100.0, y=0.0)


def test_motor_steps_per_mm():
    motor = Motor()
    # 20 Zähne × 2 mm = 40 mm pro Umdrehung, 200 × 16 = 3200 Schritte
    assert motor.mm_per_rev == 40.0
    assert motor.steps_per_mm == 80.0
    assert motor.step_mm == pytest.approx(0.0125)


def test_motor_force_from_holding_torque():
    motor = Motor()
    # Radius = 40 mm Umfang / 2pi ≈ 6.37 mm → 0.45 Nm / 0.00637 m ≈ 71 N
    assert motor.pulley_radius_mm == pytest.approx(6.366, abs=0.01)
    assert motor.max_force_n == pytest.approx(70.7, abs=0.5)


def test_belt_lengths_are_symmetric_in_the_middle():
    left, right = kin().belt_lengths(500.0, 0.0)
    assert left == pytest.approx(right)


def test_position_inverts_belt_lengths():
    machine = kin()
    for x, y in [(500.0, 0.0), (100.0, 300.0), (900.0, 800.0)]:
        left, right = machine.belt_lengths(x, y)
        back_x, back_y = machine.position(left, right)
        assert back_x == pytest.approx(x, abs=1e-6)
        assert back_y == pytest.approx(y, abs=1e-6)


def test_position_rejects_unreachable_lengths():
    with pytest.raises(ValueError):
        kin().position(100.0, 100.0)  # zusammen kürzer als der Ankerabstand


def test_belt_angle_is_widest_near_the_anchor_line():
    machine = kin()
    high = machine.belt_angle_deg(500.0, 900.0)
    low = machine.belt_angle_deg(500.0, 0.0)
    assert high > low
    assert 0 < low < 180


def test_resolution_is_finer_than_a_pen_tip():
    machine = kin()
    assert machine.resolution_mm(500.0, 0.0) < 0.1


def test_resolution_degrades_towards_the_anchor_line():
    machine = kin()
    assert machine.resolution_mm(500.0, 950.0) > machine.resolution_mm(500.0, 0.0)


def test_tension_is_symmetric_and_carries_the_weight():
    machine = kin(gondola=Gondola(mass_g=300.0))
    left, right = machine.belt_tension_n(500.0, 0.0)
    assert left == pytest.approx(right)
    # vertikale Komponenten beider Riemen tragen zusammen das Gewicht
    (lx, ly), (rx, ry) = machine._unit_vectors(500.0, 0.0)
    assert left * ly + right * ry == pytest.approx(machine.gondola.weight_n)


def test_tension_grows_near_the_anchor_line():
    machine = kin()
    assert max(machine.belt_tension_n(500.0, 950.0)) > max(machine.belt_tension_n(500.0, 0.0))


def test_max_belt_length_is_the_far_corner():
    machine = kin()
    assert machine.max_belt_length(1000.0, 500.0) == pytest.approx(
        math.hypot(1000.0, 1000.0)
    )


def test_area_analysis_flags_the_top_edge():
    analysis = analyze_area(default_kinematics(), samples=11)
    # der kritische Punkt liegt oben, nicht unten
    assert analysis.worst_resolution_at[1] > 1250
    assert analysis.worst_angle_deg > 90
    assert analysis.max_belt_mm > 3000


def test_analysis_verdict_mentions_the_motor_reserve():
    analysis = analyze_area(default_kinematics(), samples=11)
    notes = " ".join(analysis.verdict(Motor()))
    assert "Reserve" in notes
    assert "Riemen" in notes


def test_higher_anchors_improve_the_worst_case():
    low = analyze_area(default_kinematics(anchor_above_mm=100), samples=11)
    high = analyze_area(default_kinematics(anchor_above_mm=400), samples=11)
    assert high.worst_resolution_mm < low.worst_resolution_mm
    assert high.max_tension_n < low.max_tension_n
