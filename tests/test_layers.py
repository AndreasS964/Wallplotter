"""Mehrfarbig plotten — der kritische Punkt ist der Passer zwischen den Ebenen."""

import pytest

from wallplotter.config import PlotConfig
from wallplotter.gcode import layers_to_gcode, prepare_geometry
from wallplotter.geometry import bounds
from wallplotter.pipeline import Layer

CONFIG = PlotConfig(width_mm=1000, height_mm=1000, margin_mm=0, invert_y=False)

# Schwarz füllt die Fläche, Rot ist ein kleines Kästchen in der Ecke
BLACK = Layer(1, "#000000", [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]])
RED = Layer(2, "#e02020", [[(10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)]])


def coordinates(program: str, park=(0.0, 0.0)) -> list[tuple[float, float]]:
    """Angefahrene Punkte ohne die abschließende Parkfahrt.

    Geparkt wird an der unteren linken Ecke der *Zeichenfläche*, nicht auf
    Maschinen-(0,0) — bei kalibrierter Fläche läge der nämlich außerhalb, im
    Bereich der schlechtesten Riemengeometrie.
    """
    points = []
    for line in program.splitlines():
        if not line.startswith(("G0 X", "G1 X")):
            continue
        parts = {p[0]: float(p[1:]) for p in line.split() if p[0] in "XY"}
        if "X" in parts and "Y" in parts:
            points.append((parts["X"], parts["Y"]))
    return points[:-1] if points and points[-1] == park else points


def test_one_file_per_layer():
    result = layers_to_gcode([BLACK, RED], CONFIG)
    assert set(result) == {"#000000", "#e02020"}
    assert "Ebene 1/2: #000000" in result["#000000"]


def test_empty_layers_are_skipped():
    empty = Layer(3, "#00ff00", [[(5.0, 5.0)]])
    assert set(layers_to_gcode([BLACK, empty], CONFIG)) == {"#000000"}


def test_nothing_to_plot():
    assert layers_to_gcode([], CONFIG) == {}
    assert layers_to_gcode([], CONFIG, separate=False) == ""


def test_layers_share_one_scaling():
    """Der Kern der Sache: Rot darf nicht für sich eingepasst werden.

    Sonst würde das kleine rote Kästchen auf die volle Fläche gezogen und läge
    völlig woanders als im Original.
    """
    result = layers_to_gcode([BLACK, RED], CONFIG, fit=True)
    black = coordinates(result["#000000"])
    red = coordinates(result["#e02020"])

    # Schwarz füllt die Fläche
    assert max(x for x, _ in black) == pytest.approx(1000.0, abs=1e-6)
    # Rot behält sein Größenverhältnis: 10 von 100 Einheiten → 100 von 1000 mm
    red_width = max(x for x, _ in red) - min(x for x, _ in red)
    assert red_width == pytest.approx(100.0, abs=1e-6)
    # und liegt an der richtigen Stelle
    assert min(x for x, _ in red) == pytest.approx(100.0, abs=1e-6)


def test_single_file_matches_the_separate_files():
    """Beide Wege müssen dieselbe Geometrie erzeugen, sonst passt der Passer nicht."""
    separate = layers_to_gcode([BLACK, RED], CONFIG)
    combined = layers_to_gcode([BLACK, RED], CONFIG, separate=False)
    for program in separate.values():
        for point in coordinates(program):
            assert point in coordinates(combined)


def test_single_file_pauses_between_layers():
    combined = layers_to_gcode([BLACK, RED], CONFIG, separate=False)
    assert combined.count("M0 ") == 1               # eine Pause bei zwei Ebenen
    assert "Stift wechseln auf: #e02020" in combined
    assert combined.count("M2 ") == 1               # nur ein Programmende
    assert combined.strip().endswith("M2 ; Programmende")


def test_single_file_sets_up_the_machine_only_once():
    combined = layers_to_gcode([BLACK, RED], CONFIG, separate=False)
    assert combined.count("G21 ; Millimeter") == 1
    assert combined.count("G90 ; absolute Koordinaten") == 1


def test_pen_is_up_before_every_pause():
    combined = layers_to_gcode([BLACK, RED], CONFIG, separate=False)
    lines = combined.splitlines()
    pause = next(i for i, line in enumerate(lines) if line.startswith("M0 "))
    assert any(line.startswith("M5") for line in lines[pause - 4 : pause])


def test_no_fit_keeps_the_original_coordinates():
    result = layers_to_gcode([BLACK, RED], CONFIG, fit=False)
    assert (10.0, 10.0) in coordinates(result["#e02020"])


def test_layer_label_falls_back_to_the_colour():
    named = Layer(1, "#000000", BLACK.lines, name="Kontur")
    assert set(layers_to_gcode([named], CONFIG)) == {"Kontur"}


# Wie CONFIG, aber mit Spiegelung und Flächenversatz — also so, wie es nach
# einer Kalibrierung wirklich aussieht.
CALIBRATED = PlotConfig(
    width_mm=1000, height_mm=1000, margin_mm=50, origin_x_mm=300.0, origin_y_mm=200.0
)


def test_layers_land_where_a_single_colour_drawing_lands():
    """Der eigentliche Passer-Test: dieselbe Zeichnung, einmal ein-, einmal
    mehrfarbig — beide Male muss sie an derselben Stelle der Wand liegen.

    Mit Flächenversatz ging das früher schief: der Versatz wurde in X doppelt
    gerechnet und in Y wieder weggekürzt, die Zeichnung landete gut 30 cm
    daneben.
    """
    combined = [line for layer in (BLACK, RED) for line in layer.lines]
    expected = bounds(prepare_geometry(combined, CALIBRATED, fit=True))

    programs = layers_to_gcode([BLACK, RED], CALIBRATED, fit=True)
    park = (CALIBRATED.origin_x_mm, CALIBRATED.origin_y_mm)
    points = [point for program in programs.values() for point in coordinates(program, park)]
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx(expected, abs=1e-6)


def test_layers_keep_the_area_offset_exactly_once():
    programs = layers_to_gcode([BLACK], CALIBRATED, fit=True)
    park = (CALIBRATED.origin_x_mm, CALIBRATED.origin_y_mm)
    xs = [x for x, _ in coordinates(programs["#000000"], park)]
    # 1000 mm Fläche, 50 mm Rand, 300 mm Versatz → 350 … 1250
    assert min(xs) == pytest.approx(350.0, abs=1e-6)
    assert max(xs) == pytest.approx(1250.0, abs=1e-6)


def test_flat_drawing_is_still_scaled_to_the_area():
    """Eine Zeichnung ohne Höhe hat eine entartete Bounding-Box — sie wurde
    dadurch früher gar nicht mehr skaliert und kam winzig aus der Maschine."""
    wide = Layer(1, "#000000", [[(0.0, 0.0), (100.0, 0.0)]])
    half = Layer(2, "#e02020", [[(0.0, 0.0), (50.0, 0.0)]])
    programs = layers_to_gcode([wide, half], CONFIG, fit=True)
    black = [x for x, _ in coordinates(programs["#000000"])]
    red = [x for x, _ in coordinates(programs["#e02020"])]
    assert max(black) - min(black) == pytest.approx(1000.0, abs=1e-6)
    assert max(red) - min(red) == pytest.approx(500.0, abs=1e-6)


def test_no_park_run_across_the_wall_before_a_pen_change():
    """Vor jeder M0-Pause stand die abschließende Parkfahrt.

    Aus dem Programm für sich gesehen harmlos, an der Wand nicht: die Gondel
    fuhr vor jedem Stiftwechsel quer über die Fläche zum Nullpunkt und danach
    wieder zurück. Bei einer kalibrierten Fläche liegt der Nullpunkt obendrein
    außerhalb der Zeichenfläche.
    """
    # Fläche mit Versatz: dort liegt der Nullpunkt garantiert außerhalb der
    # Zeichnung, eine Fahrt dorthin ist also nie Teil des Bildes
    combined = layers_to_gcode([BLACK, RED], CALIBRATED, separate=False)
    lines = combined.splitlines()
    pause = next(index for index, line in enumerate(lines) if line.startswith("M0 "))
    park = f"G0 X{CALIBRATED.origin_x_mm:.0f} Y{CALIBRATED.origin_y_mm:.0f}"
    assert not any(line.startswith(park) for line in lines[:pause])
    # genau eine Parkfahrt, und die steht am Schluss
    assert combined.count(park) == 1
    # und sie geht an die Ecke der Zeichenfläche, nicht auf Maschinen-(0,0)
    assert "G0 X0 Y0" not in combined
    # das Werkzeug ist trotzdem aus, bevor jemand hinfasst
    assert any(line.startswith("M5") for line in lines[pause - 4 : pause])


def test_each_layer_gets_the_tool_preamble_after_a_change():
    """Nach dem Wechsel muss der neue Kopf hochgefahren werden — beim Laser
    wären das Luft an, Vorlauf und Lasermodus."""
    combined = layers_to_gcode([BLACK, RED], CONFIG, separate=False)
    lines = combined.splitlines()
    pause = next(index for index, line in enumerate(lines) if line.startswith("M0 "))
    after = lines[pause + 1 : pause + 8]
    assert any(line.startswith("M3 S0") for line in after)   # Stift oben, definierter Zustand
