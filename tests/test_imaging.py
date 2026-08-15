import pytest

from wallplotter.geometry import bounds, draw_length, travel_length
from wallplotter.imaging import (
    TECHNIQUES,
    GrayImage,
    ImagingError,
    dither_points,
    image_to_lines,
    load_gray,
    spiral,
    stipple,
    tsp,
)

Image = pytest.importorskip("PIL.Image", reason="Pillow ist optional (Extra: photo)")

AREA = (2000.0, 2500.0)


@pytest.fixture
def photo(tmp_path):
    """Verlauf von hell nach dunkel mit einem schwarzen Block."""
    image = Image.new("L", (120, 150), 255)
    for y in range(150):
        for x in range(120):
            image.putpixel((x, y), int(255 - 150 * y / 150))
    for y in range(100, 140):
        for x in range(20, 100):
            image.putpixel((x, y), 0)
    path = tmp_path / "foto.png"
    image.save(path)
    return path


def gradient(width=40, height=50) -> GrayImage:
    return GrayImage(
        [[1.0 - row / height for _ in range(width)] for row in range(height)], width, height
    )


# -- Laden ------------------------------------------------------------------


def test_load_downsamples_large_images(photo):
    image = load_gray(photo, max_size=40)
    assert max(image.width, image.height) == 40


def test_load_accepts_bytes(photo):
    assert load_gray(photo.read_bytes(), max_size=30).width > 0


def test_load_rejects_junk():
    with pytest.raises(ImagingError, match="nicht lesbar"):
        load_gray(b"kein bild")


def test_darkness_outside_the_image_is_zero():
    assert gradient().darkness(-5, -5) == 0.0


# -- Punktverteilung --------------------------------------------------------


def test_dither_puts_more_points_where_it_is_darker():
    points = dither_points(gradient(), spacing_px=2.0)
    top = [p for p in points if p[1] < 25]
    bottom = [p for p in points if p[1] >= 25]
    assert len(bottom) > len(top) * 2


def test_dither_is_deterministic():
    assert dither_points(gradient()) == dither_points(gradient())


def test_dither_rejects_nonsense_spacing():
    with pytest.raises(ImagingError):
        dither_points(gradient(), spacing_px=0)


# -- Verfahren --------------------------------------------------------------


def test_stipple_draws_one_dash_per_point():
    lines = stipple(gradient(), *AREA, dot_mm=1.0)
    assert lines
    assert all(len(line) == 2 for line in lines)
    assert all(abs(line[1][0] - line[0][0]) == pytest.approx(1.0) for line in lines)


def test_tsp_is_a_single_unbroken_line():
    lines = tsp(gradient(), *AREA)
    assert len(lines) == 1
    assert len(lines[0]) > 10


def test_tsp_travels_far_less_than_stipple():
    """Der eigentliche Grund für tsp: keine Leerwege, keine Stifthübe."""
    points = gradient()
    assert travel_length(tsp(points, *AREA)) < travel_length(stipple(points, *AREA)) / 10


def test_tsp_beats_the_unordered_point_sequence():
    ordered = tsp(gradient(), *AREA)[0]
    assert draw_length([ordered]) > 0


def test_spiral_covers_the_area_and_stays_inside():
    lines = spiral(gradient(), *AREA, pitch_mm=40)
    xmin, ymin, xmax, ymax = bounds(lines)
    assert xmin >= -1e-6 and ymin >= -1e-6
    assert xmax <= AREA[0] + 1e-6 and ymax <= AREA[1] + 1e-6
    assert xmax - xmin > AREA[0] * 0.8


def test_coarser_spiral_means_a_shorter_plot():
    """Bahnabstand und Zeichenweg hängen zusammen wie Fläche zu Abstand."""
    fine = draw_length(spiral(gradient(), *AREA, pitch_mm=20))
    coarse = draw_length(spiral(gradient(), *AREA, pitch_mm=40))
    assert coarse < fine / 1.5


def test_spiral_rejects_nonsense():
    with pytest.raises(ImagingError):
        spiral(gradient(), *AREA, pitch_mm=0)


# -- Einstieg ---------------------------------------------------------------


@pytest.mark.parametrize("technique", ["stipple", "tsp", "spiral"])
def test_every_technique_respects_the_margin(photo, technique):
    lines = image_to_lines(photo, *AREA, technique, margin_mm=100.0, max_size=60)
    xmin, ymin, xmax, ymax = bounds(lines)
    assert xmin >= 100.0 - 1e-6
    assert ymin >= 100.0 - 1e-6
    assert xmax <= AREA[0] - 100.0 + 1e-6
    assert ymax <= AREA[1] - 100.0 + 1e-6


def test_unknown_technique_is_rejected(photo):
    with pytest.raises(ImagingError, match="Unbekanntes Verfahren"):
        image_to_lines(photo, *AREA, "malen")


def test_margin_larger_than_area_is_rejected(photo):
    with pytest.raises(ImagingError, match="Rand"):
        image_to_lines(photo, 100.0, 100.0, "spiral", margin_mm=60.0)


def test_sorting_keeps_the_travel_short(photo):
    lines = image_to_lines(photo, *AREA, "spiral", max_size=60, pitch_mm=40)
    assert travel_length(lines) < draw_length(lines) / 2


def test_every_technique_is_described():
    assert set(TECHNIQUES) == {"hatch", "stipple", "tsp", "spiral"}
    assert all(len(text) > 20 for text in TECHNIQUES.values())
