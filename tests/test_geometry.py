import math
import random

from wallplotter.geometry import (
    bounds,
    draw_length,
    estimate_duration_s,
    fit_to_area,
    flip_y,
    sort_lines,
    travel_length,
)

SQUARE = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]


def test_bounds_of_empty_input():
    assert bounds([]) == (0.0, 0.0, 0.0, 0.0)


def test_bounds_square():
    assert bounds(SQUARE) == (0.0, 0.0, 10.0, 10.0)


def test_fit_to_area_keeps_aspect_ratio_and_centers():
    fitted = fit_to_area(SQUARE, width_mm=2000, height_mm=2500, margin_mm=50)
    xmin, ymin, xmax, ymax = bounds(fitted)
    # Breite ist die begrenzende Achse: 2000 - 2*50 = 1900
    assert round(xmax - xmin, 6) == 1900.0
    assert round(ymax - ymin, 6) == 1900.0
    assert round(xmin, 6) == 50.0
    # vertikal zentriert innerhalb 2500 - 2*50 = 2400
    assert round(ymin, 6) == round(50 + (2400 - 1900) / 2, 6)


def test_fit_to_area_respects_margin_without_centering():
    fitted = fit_to_area(SQUARE, 100, 100, margin_mm=10, center=False)
    assert bounds(fitted) == (10.0, 10.0, 90.0, 90.0)


def test_flip_y():
    assert flip_y([[(0.0, 0.0), (1.0, 2.0)]], height_mm=10) == [[(0.0, 10.0), (1.0, 8.0)]]


def test_draw_and_travel_length():
    assert draw_length(SQUARE) == 40.0
    # Start (0,0) -> Linienstart (0,0) -> Linienende (0,0) -> zurück: 0
    assert travel_length(SQUARE) == 0.0

    two_lines = [[(0.0, 0.0), (1.0, 0.0)], [(5.0, 0.0), (6.0, 0.0)]]
    # 0->0 anfahren (0) + 1->5 (4) + Rückweg 6->0 (6)
    assert travel_length(two_lines) == 10.0


def test_estimate_duration():
    seconds = estimate_duration_s(SQUARE, draw_feed=60, travel_feed=60)
    assert round(seconds, 6) == 40.0


def test_fit_to_area_can_use_a_foreign_bounding_box():
    """Für mehrfarbige Zeichnungen: eine Ebene, aber die Grenzen aller Ebenen."""
    corner = [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]]
    fitted = fit_to_area(corner, 100, 100, source_bounds=(0.0, 0.0, 10.0, 10.0))
    # Maßstab kommt aus der fremden Box (10 → 100), nicht aus der eigenen (1)
    assert bounds(fitted) == (0.0, 0.0, 10.0, 10.0)


def _brute_force_sort(lines, start=(0.0, 0.0)):
    """Die naive Fassung — Maßstab für die gerasterte Suche."""
    remaining = [list(line) for line in lines if len(line) >= 2]
    ordered, position = [], start
    while remaining:
        best_index, best_distance, best_flip = 0, math.inf, False
        for index, line in enumerate(remaining):
            for flip in (False, True):
                head = line[-1] if flip else line[0]
                distance = math.hypot(head[0] - position[0], head[1] - position[1])
                if distance < best_distance:
                    best_index, best_distance, best_flip = index, distance, flip
        line = remaining.pop(best_index)
        if best_flip:
            line.reverse()
        ordered.append(line)
        position = line[-1]
    return ordered


def test_sort_lines_orders_by_shortest_travel_and_may_flip():
    lines = [[(100.0, 0.0), (90.0, 0.0)], [(1.0, 0.0), (5.0, 0.0)]]
    assert sort_lines(lines) == [[(1.0, 0.0), (5.0, 0.0)], [(90.0, 0.0), (100.0, 0.0)]]


def test_sort_lines_drops_degenerate_lines():
    assert sort_lines([[(1.0, 1.0)], []]) == []
    assert sort_lines([]) == []


def test_sort_lines_matches_the_brute_force_result():
    """Die Rastersuche darf schneller sein, aber nicht anders entscheiden."""
    rng = random.Random(20260816)
    for _ in range(20):
        lines = [
            [
                (rng.random() * 2000, rng.random() * 2500),
                (rng.random() * 2000, rng.random() * 2500),
            ]
            for _ in range(rng.randint(2, 40))
        ]
        assert sort_lines([list(line) for line in lines]) == _brute_force_sort(lines)


def test_sort_lines_handles_exact_ties_like_the_brute_force_version():
    """Ein Punktraster erzeugt lauter gleich weite Nachbarn — genau der Fall,
    in dem eine Rastersuche gern eine andere Wahl trifft als die naive."""
    grid = [[(x * 10.0, y * 10.0), (x * 10.0 + 4.0, y * 10.0)] for y in range(8) for x in range(8)]
    assert sort_lines([list(line) for line in grid]) == _brute_force_sort(grid)


def test_lines_sharing_an_endpoint_are_ordered_the_same_way():
    """Der Fall, den Zufallszahlen nie treffen und eine Zeichnung ständig.

    Teilen zwei Linien einen Endpunkt, liegen sie exakt gleich weit weg. Welche
    zuerst gezogen wird, entscheidet über den *nächsten* Leerweg — und die
    Rasterreihenfolge ist eine andere als die Listenreihenfolge. Ohne festen
    Gleichstand kam mal der eine, mal der andere Weg heraus, gelegentlich der
    längere: für ``[[(2,2),(3,2)], [(1,3),(2,2)]]`` 4,24 mm statt 3,83 mm.
    """
    gemeldet = [[(2.0, 2.0), (3.0, 2.0)], [(1.0, 3.0), (2.0, 2.0)]]
    assert sort_lines([list(line) for line in gemeldet]) == _brute_force_sort(gemeldet)

    # und breit: Linien auf einem groben Gitter teilen sich dauernd Endpunkte
    rng = random.Random(4711)
    for _ in range(200):
        knoten = [(float(rng.randint(0, 6)), float(rng.randint(0, 6))) for _ in range(10)]
        lines = []
        for _ in range(rng.randint(2, 12)):
            a, b = rng.sample(range(len(knoten)), 2)
            if knoten[a] != knoten[b]:
                lines.append([knoten[a], knoten[b]])
        if len(lines) < 2:
            continue
        assert sort_lines([list(line) for line in lines]) == _brute_force_sort(lines)


def test_sort_lines_finds_geometry_far_from_the_start():
    """Der Nullpunkt kann weit außerhalb der Zeichnung liegen."""
    far = [[(1_000_000.0, 500_000.0), (1_000_010.0, 500_000.0)]]
    assert sort_lines(far) == far
