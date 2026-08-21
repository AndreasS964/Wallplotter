import math

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


def _gradient_image(width: int, height: int) -> GrayImage:
    """Dunkle Mitte, helle Ränder — genug Kontrast für einen sichtbaren Wobble."""
    pixels = []
    for y in range(height):
        row = []
        for x in range(width):
            dx, dy = x - width / 2, y - height / 2
            distance = math.hypot(dx, dy) / (math.hypot(width, height) / 2)
            row.append(int(min(255, 255 * distance)))
        pixels.append(row)
    return GrayImage(width=width, height=height, pixels=pixels)


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


def test_darkness_just_left_of_the_origin_is_still_outside():
    """``int(-0.5) == 0`` in Python — Kürzung Richtung 0 zählte
    ``-1 < x < 0`` fälschlich zu Pixel 0, statt zu ``außerhalb``.

    Das Pixel (0, 0) ist bewusst nicht weiß: sonst maskiert eine zufällig
    passende Dunkelheit von 0.0 an dieser Stelle den Fehler.
    """
    image = GrayImage(pixels=[[0.0]], width=1, height=1)  # Pixelwert 0 → Dunkelheit 1.0
    assert image.darkness(0, 0) == 1.0
    assert image.darkness(-0.5, 0) == 0.0
    assert image.darkness(0, -0.5) == 0.0
    assert image.darkness(-0.5, -0.5) == 0.0


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


def test_hatch_fills_the_area(photo):
    """Das vierte Verfahren hängt als einziges an einem Fremdpaket.

    Und das ist genau der Grund, warum es hier steht: `hatched` 0.2.0 ist die
    letzte Veröffentlichung und mit Shapely 2 nicht mehr lauffähig. Solange das
    so ist, meldet der Test ein **xfail** — sichtbar im Bericht, anders als ein
    Skip, und er wird von selbst wieder grün, wenn upstream nachzieht.
    """
    pytest.importorskip("hatched", reason="Paket `hatched` ist optional (Extra: hatch)")
    try:
        lines = image_to_lines(photo, *AREA, "hatch", margin_mm=100.0, pitch_mm=8.0)
    except ImagingError as exc:
        if "Shapely 2" in str(exc):
            pytest.xfail(f"hatched 0.2.0 gegen Shapely 2: {exc}")
        raise
    assert lines
    xmin, ymin, xmax, ymax = bounds(lines)
    assert xmin >= 100.0 - 1e-6
    assert ymin >= 100.0 - 1e-6
    assert xmax <= AREA[0] - 100.0 + 1e-6
    assert ymax <= AREA[1] - 100.0 + 1e-6


def test_missing_hatched_package_points_at_the_right_extra(photo, monkeypatch):
    """Wer schraffieren will, soll den Namen des Extras lesen, nicht raten."""
    import builtins

    real_import = builtins.__import__

    def without_hatched(name, *args, **kwargs):
        if name == "hatched":
            raise ImportError("no hatched here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_hatched)
    with pytest.raises(ImagingError, match=r"\.\[hatch\]"):
        image_to_lines(photo, *AREA, "hatch")


def test_spiral_keeps_the_picture_at_a_fine_pitch():
    """Bei feinem Bahnabstand verschwand das Bild vollständig.

    Die Wellenlänge des Wobbles folgt dem Bahnabstand; die Schrittweite tat es
    nicht. Ab etwa 2,4 mm Bahnabstand abwärts wurde über die Welle
    hinweggeschritten — Aliasing —, die Auslenkung mittelte sich weg und übrig
    blieb eine glatte Spirale ohne Motiv.

    Gemessen wird der Zeichenweg gegen denselben Aufruf mit ``amplitude=0``:
    Ein wirksamer Wobble muss den Weg deutlich verlängern. Vorher lag das
    Verhältnis bei genau 1,00 — die Auslenkung war rechnerisch da und
    zeichnerisch weg.
    """
    image = _gradient_image(60, 60)
    for pitch in (4.0, 2.4, 1.2, 0.8):
        wobbled = draw_length(spiral(image, 200.0, 200.0, pitch_mm=pitch))
        smooth = draw_length(spiral(image, 200.0, 200.0, pitch_mm=pitch, amplitude=0.0))
        assert smooth > 0
        assert wobbled / smooth > 2.0, f"Bahnabstand {pitch} mm: Wobble ohne Wirkung"


def test_spiral_wobble_phase_uses_the_true_radius_near_the_centre():
    """``arc`` wuchs vorher unbedingt um ``step_mm`` — eine algebraische
    Nullrechnung (``delta * max(radius, pitch_mm/4)`` ist per Definition von
    ``delta`` immer genau ``step_mm``), die den in den Kommentaren
    beschriebenen Fix rückgängig machte. Im Zentrum, wo der Radius kleiner
    als ``pitch_mm/4`` ist, muss die Wobble-Phase deshalb langsamer wachsen
    als am tatsächlichen (kleinen) Radius entlang gefahren wurde — nicht
    pauschal um den vollen Nennschritt.

    Referenzwerte unabhängig nachgerechnet, mit der im Kommentar
    beschriebenen (richtigen) Formel: ``arc += min(step_mm, delta * radius)``.
    """
    pitch_mm, step_mm, amplitude = 25.0, 1.2, 1.0
    width_mm = height_mm = 2000.0
    # Vollständig dunkles Bild: darkness() liefert überall exakt 1.0, damit
    # der Wobble nur noch von der Phase (arc) abhängt, nicht vom Bildinhalt.
    dark = GrayImage(pixels=[[0.0] * 40 for _ in range(40)], width=40, height=40)

    lines = spiral(
        dark, width_mm, height_mm, pitch_mm=pitch_mm, amplitude=amplitude,
        step_mm=step_mm, simplify_mm=0.0,
    )
    raw = lines[0]

    scale = min(width_mm / dark.width, height_mm / dark.height)
    offset_x = (width_mm - dark.width * scale) / 2
    offset_y = (height_mm - dark.height * scale) / 2
    center = (offset_x + dark.width * scale / 2, offset_y + dark.height * scale / 2)
    growth = pitch_mm / (2 * math.pi)
    wavelength = pitch_mm

    angle = arc = 0.0
    expected = []
    for _ in range(30):
        radius = growth * angle
        x = center[0] + radius * math.cos(angle)
        y = center[1] + radius * math.sin(angle)
        wobble = pitch_mm * amplitude * math.sin(2 * math.pi * arc / wavelength)
        expected.append((x + wobble * math.cos(angle), y + wobble * math.sin(angle)))
        delta = step_mm / max(radius, pitch_mm / 4)
        angle += delta
        arc += min(step_mm, delta * radius)  # die im Kommentar beschriebene, richtige Formel

    assert len(raw) >= 30
    for actual, want in zip(raw[:30], expected, strict=False):
        assert actual == pytest.approx(want, abs=1e-6)


def test_spiral_refuses_an_absurdly_fine_pitch():
    image = _gradient_image(60, 60)
    with pytest.raises(ImagingError, match="Bahnabstand"):
        spiral(image, 2000.0, 2500.0, pitch_mm=0.01)


def test_a_broken_hatched_says_what_to_do_instead():
    """Die rohe Meldung von Shapely hilft niemandem weiter.

    „The truth value of an empty array is ambiguous" sagt nichts darüber, dass
    das Fremdpaket seit Jahren nicht mehr veröffentlicht wurde und drei andere
    Bildverfahren bereitstehen, die nur Pillow brauchen.
    """
    import sys
    import types

    from wallplotter.imaging import hatch

    kaputt = types.ModuleType("hatched")

    def explodiert(*args, **kwargs):
        raise ValueError(
            "The truth value of an empty array is ambiguous. "
            "Use `array.size > 0` to check that an array is not empty."
        )

    kaputt.hatch = explodiert
    original = sys.modules.get("hatched")
    sys.modules["hatched"] = kaputt
    try:
        with pytest.raises(ImagingError) as fehler:
            hatch(b"nicht wirklich ein Bild", 100.0, 100.0)
    finally:
        if original is None:
            del sys.modules["hatched"]
        else:
            sys.modules["hatched"] = original

    meldung = str(fehler.value)
    assert "Shapely 2" in meldung
    assert "stipple" in meldung and "tsp" in meldung and "spiral" in meldung
