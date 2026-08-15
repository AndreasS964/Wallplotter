from wallplotter.geometry import (
    bounds,
    draw_length,
    estimate_duration_s,
    fit_to_area,
    flip_y,
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
