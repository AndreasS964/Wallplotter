import math

import pytest

from wallplotter.correction import (
    CorrectionError,
    MeasuredCorrection,
    MeasuredPoint,
    StretchCorrection,
    invert,
    load_correction,
)
from wallplotter.kinematics import default_kinematics

AREA = (2000.0, 2500.0)


def grid_points(steps: int = 3):
    for iy in range(steps):
        for ix in range(steps):
            yield (AREA[0] * ix / (steps - 1), AREA[1] * iy / (steps - 1))


def error_of(correction, kinematics, truth, samples: int = 7) -> float:
    """Größter Restfehler, wenn wir mit dieser Korrektur zeichnen.

    Befohlen wird die korrigierte Position, gefahren wird sie durch das echte
    Modell — was rauskommt, sollte wieder die Wunschposition sein.
    """
    worst = 0.0
    for iy in range(samples):
        for ix in range(samples):
            target = (AREA[0] * ix / (samples - 1), AREA[1] * iy / (samples - 1))
            commanded = correction.apply_lines([[target, target]])[0][0]
            actual = truth.forward(commanded)
            worst = max(worst, math.hypot(actual[0] - target[0], actual[1] - target[1]))
    return worst


@pytest.fixture
def kinematics():
    return default_kinematics(anchor_above_mm=400)


@pytest.fixture
def truth(kinematics):
    """Die „echte" Maschine: Glasfaserriemen mit 20 N/mm·m."""
    return StretchCorrection(kinematics, specific_stiffness=20.0)


# -- Umkehrung --------------------------------------------------------------


def test_invert_finds_the_command_for_a_target():
    def forward(point):
        return (point[0] * 1.01 + 3, point[1] * 0.99 - 2)

    command = invert(forward, (1000.0, 500.0))
    result = forward(command)
    assert result[0] == pytest.approx(1000.0, abs=1e-3)
    assert result[1] == pytest.approx(500.0, abs=1e-3)


# -- Dehnungsmodell ---------------------------------------------------------


def test_stretch_displaces_the_pen_by_a_few_tenths(truth):
    assert 0.05 < truth.displacement_mm((1000.0, 1250.0)) < 2.0


def test_stiffer_belt_moves_less(kinematics):
    soft = StretchCorrection(kinematics, 20.0).displacement_mm((1000.0, 2000.0))
    steel = StretchCorrection(kinematics, 100.0).displacement_mm((1000.0, 2000.0))
    assert steel < soft / 4


def test_stretch_correction_removes_almost_all_of_it(kinematics, truth):
    before = max(truth.displacement_mm(p) for p in grid_points(5))
    after = error_of(StretchCorrection(kinematics, 20.0), kinematics, truth)
    assert before > 0.2
    assert after < 0.01
    assert after < before / 20


def test_wrong_stiffness_still_helps_but_less(kinematics, truth):
    # doppelt so steif angenommen wie in Wirklichkeit
    rough = error_of(StretchCorrection(kinematics, 40.0), kinematics, truth)
    uncorrected = max(truth.displacement_mm(p) for p in grid_points(5))
    assert rough < uncorrected


def test_negative_stiffness_is_rejected(kinematics):
    with pytest.raises(CorrectionError):
        StretchCorrection(kinematics, -5.0)


def test_stiffness_can_be_fitted_from_measurements(kinematics, truth):
    points = [MeasuredPoint(nominal=p, measured=truth.forward(p)) for p in grid_points(3)]
    fitted = StretchCorrection.fit_stiffness(kinematics, points)
    assert fitted.specific_stiffness == pytest.approx(20.0, rel=0.1)


def test_fitting_without_points_is_rejected(kinematics):
    with pytest.raises(CorrectionError, match="Ohne Messpunkte"):
        StretchCorrection.fit_stiffness(kinematics, [])


# -- Gemessene Korrektur ----------------------------------------------------


def test_measured_correction_reduces_the_error(kinematics, truth):
    points = [MeasuredPoint(nominal=p, measured=truth.forward(p)) for p in grid_points(5)]
    correction = MeasuredCorrection.fit(points)
    uncorrected = max(truth.displacement_mm(p) for p in grid_points(5))
    assert error_of(correction, kinematics, truth) < uncorrected / 4


def test_higher_degree_follows_the_curved_error_field(kinematics, truth):
    """Das Dehnungsfeld ist gekrümmt — affin bringt kaum etwas, kubisch schon."""
    points = [MeasuredPoint(p, truth.forward(p)) for p in grid_points(5)]
    results = {
        degree: error_of(MeasuredCorrection.fit(points, degree), kinematics, truth)
        for degree in ("affin", "bilinear", "quadratisch", "kubisch")
    }
    assert results["affin"] > results["quadratisch"] > results["kubisch"]
    assert results["kubisch"] < results["affin"] / 3


def test_degree_is_chosen_from_the_number_of_measurements():
    def rectangular(columns, rows):
        return [
            MeasuredPoint((AREA[0] * ix / (columns - 1), AREA[1] * iy / (rows - 1)),
                          (AREA[0] * ix / (columns - 1), AREA[1] * iy / (rows - 1)))
            for iy in range(rows)
            for ix in range(columns)
        ]

    three = [MeasuredPoint(p, p) for p in [(0.0, 0.0), (2000.0, 0.0), (0.0, 2500.0)]]
    assert MeasuredCorrection.fit(three).degree == "affin"
    assert MeasuredCorrection.fit(rectangular(5, 2)).degree == "bilinear"   # 10 Punkte
    assert MeasuredCorrection.fit(rectangular(4, 3)).degree == "quadratisch"  # 12 Punkte
    assert MeasuredCorrection.fit(rectangular(4, 4)).degree == "kubisch"    # 16 Punkte


def test_too_few_points_for_the_requested_degree(kinematics, truth):
    points = [MeasuredPoint(p, truth.forward(p)) for p in grid_points(2)]
    with pytest.raises(CorrectionError, match="mindestens"):
        MeasuredCorrection.fit(points, "kubisch")


def test_unknown_degree_is_rejected(truth):
    points = [MeasuredPoint(p, truth.forward(p)) for p in grid_points(3)]
    with pytest.raises(CorrectionError, match="Unbekannter Grad"):
        MeasuredCorrection.fit(points, "magisch")


def test_the_physical_model_beats_the_polynomial(kinematics, truth):
    """Ein angepasster Materialwert schlägt zehn Polynomkoeffizienten.

    Weil das Modell die Form des Fehlerfelds kennt und das Polynom sie nur
    nachahmt — genau deshalb ist die Dehnungskorrektur der Hauptweg und die
    Messung die Ergänzung.
    """
    points = [MeasuredPoint(p, truth.forward(p)) for p in grid_points(3)]
    polynomial = error_of(MeasuredCorrection.fit(points), kinematics, truth)
    physical = error_of(
        StretchCorrection.fit_stiffness(kinematics, points), kinematics, truth
    )
    assert physical < polynomial / 10


def test_measured_correction_reports_its_own_residual(kinematics, truth):
    points = [MeasuredPoint(nominal=p, measured=truth.forward(p)) for p in grid_points(4)]
    worst, mean = MeasuredCorrection.fit(points).residual_mm(points)
    assert mean <= worst
    assert worst < 0.3


def test_more_measurements_allow_a_better_fit(kinematics, truth):
    coarse = [MeasuredPoint(p, truth.forward(p)) for p in grid_points(3)]
    fine = [MeasuredPoint(p, truth.forward(p)) for p in grid_points(5)]
    assert error_of(MeasuredCorrection.fit(fine), kinematics, truth) < error_of(
        MeasuredCorrection.fit(coarse), kinematics, truth
    )


def test_a_pure_scale_error_is_corrected_exactly():
    # Maschine zeichnet 1 % zu groß
    points = [MeasuredPoint(p, (p[0] * 1.01, p[1] * 1.01)) for p in grid_points(3)]
    correction = MeasuredCorrection.fit(points)
    commanded = correction.apply_lines([[(1000.0, 1000.0)] * 2])[0][0]
    assert commanded[0] * 1.01 == pytest.approx(1000.0, abs=1e-3)


def test_too_few_points_are_rejected():
    with pytest.raises(CorrectionError, match="Mindestens 3"):
        MeasuredCorrection.fit([MeasuredPoint((0.0, 0.0), (0.0, 0.0))])


def test_collinear_points_are_rejected():
    points = [MeasuredPoint((x, 0.0), (x, 1.0)) for x in (0.0, 500.0, 1000.0, 1500.0)]
    with pytest.raises(CorrectionError, match="ungünstig"):
        MeasuredCorrection.fit(points)


def test_perfect_machine_needs_no_correction():
    points = [MeasuredPoint(p, p) for p in grid_points(3)]
    correction = MeasuredCorrection.fit(points)
    moved = correction.apply_lines([[(700.0, 900.0), (100.0, 200.0)]])[0]
    assert moved[0][0] == pytest.approx(700.0, abs=1e-6)
    assert moved[1][1] == pytest.approx(200.0, abs=1e-6)


# -- Speichern und Laden ----------------------------------------------------


def test_measured_correction_roundtrip(tmp_path, truth):
    points = [MeasuredPoint(p, truth.forward(p)) for p in grid_points(3)]
    path = MeasuredCorrection.fit(points, note="Keller").save(tmp_path / "korrektur.json")
    loaded = load_correction(path)
    assert loaded.note == "Keller"
    assert loaded.forward((500.0, 500.0)) == MeasuredCorrection.fit(points).forward((500.0, 500.0))


def test_stretch_correction_roundtrip_needs_kinematics(tmp_path, kinematics):
    path = StretchCorrection(kinematics, 25.0).save(tmp_path / "dehnung.json")
    with pytest.raises(CorrectionError, match="Ankermaßen"):
        load_correction(path)
    assert load_correction(path, kinematics).specific_stiffness == 25.0


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(CorrectionError, match="Keine Korrektur"):
        load_correction(tmp_path / "weg.json")


def test_unknown_type_is_rejected(tmp_path):
    path = tmp_path / "fremd.json"
    path.write_text('{"type": "magie"}', encoding="utf-8")
    with pytest.raises(CorrectionError, match="Unbekannte Korrekturart"):
        load_correction(path)


def test_a_measured_correction_missing_a_coefficient_block_is_a_clean_error(tmp_path):
    """Gültiges JSON, aber ein Schlüssel fehlt — etwa aus einer
    Handbearbeitung oder einem abgebrochenen Schreibvorgang. Vorher flog
    hier ein roher KeyError statt CorrectionError aus der Funktion."""
    path = tmp_path / "unvollstaendig.json"
    path.write_text('{"type": "measured", "coefficients_y": [0, 0, 0]}', encoding="utf-8")
    with pytest.raises(CorrectionError, match="keine gültige Korrektur"):
        load_correction(path)


def test_a_top_level_json_array_is_a_clean_error(tmp_path):
    """``data`` selbst ist keine Abbildung — ``data.get("type")`` schlägt mit
    AttributeError fehl, nicht mit einer der bisher abgefangenen Arten."""
    path = tmp_path / "liste.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(CorrectionError, match="keine gültige Korrektur"):
        load_correction(path)
