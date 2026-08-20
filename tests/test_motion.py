import pytest

from wallplotter.kinematics import default_kinematics
from wallplotter.motion import (
    conditioning_feeds,
    dominant_run_length_mm,
    dominant_spacing_mm,
    pendulum_frequency_hz,
    resonance_warning,
    reversal_frequency_hz,
)
from wallplotter.patterns import build


def hatching(spacing: float, width: float = 1000.0, count: int = 20):
    """Waagerechte Bahnen mit festem Abstand.

    Lang und ruhig: Bei 1000 mm Bahnlänge kehrt die Gondel alle paar Minuten
    um — das regt nichts an. Gefährlich sind kurze Bahnen, siehe
    :func:`short_strokes`.
    """
    return [[(0.0, i * spacing), (width, i * spacing)] for i in range(count)]


def short_strokes(length: float, spacing: float = 3.0, count: int = 20):
    """Kurze Striche — das wirklich kritische Muster.

    Wie oft die Gondel umkehrt, hängt an der *Bahnlänge*, nicht am
    Bahnabstand. Ein Punktraster oder eine feine Kurzstrichschraffur schaukelt
    sie auf, eine meterlange Schraffurbahn nicht.
    """
    return [[(0.0, i * spacing), (length, i * spacing)] for i in range(count)]


def test_pendulum_frequency_is_in_the_expected_range():
    # 60 bis 150 mm Pendellänge → rund 1,3 bis 2 Hz
    assert pendulum_frequency_hz(60) == pytest.approx(2.04, abs=0.05)
    assert pendulum_frequency_hz(150) == pytest.approx(1.29, abs=0.05)


def test_longer_pendulum_swings_slower():
    assert pendulum_frequency_hz(150) < pendulum_frequency_hz(60)


def test_pendulum_rejects_nonsense():
    with pytest.raises(ValueError):
        pendulum_frequency_hz(0)


def test_reversal_frequency():
    # 600 mm/min = 10 mm/s; eine volle Schwingung ist 5 mm hin und 5 mm zurück
    assert reversal_frequency_hz(5.0, 600.0) == pytest.approx(1.0)


def test_run_length_is_the_stroke_not_the_spacing():
    """Die Größe, die anregt, ist die Bahnlänge — nicht der Bahnabstand.

    Bei einer Schraffur über 1000 mm mit 3 mm Abstand lagen zwischen beiden
    Zahlen mehr als zwei Größenordnungen; die alte Rechnung nahm die falsche
    und schlug damit bei genau den Mustern Alarm, die harmlos sind.
    """
    assert dominant_run_length_mm(hatching(3.0)) == pytest.approx(1000.0)
    assert dominant_spacing_mm(hatching(3.0)) == pytest.approx(3.0)


def test_run_length_sees_reversals_inside_a_polyline():
    """Ein Mäander ist eine einzige Linie — die Umkehren liegen mittendrin."""
    meander = [[(0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0), (0.0, 4.0), (10.0, 4.0)]]
    assert dominant_run_length_mm(meander) == pytest.approx(12.0)


def test_run_length_is_none_without_geometry():
    assert dominant_run_length_mm([]) is None
    assert dominant_run_length_mm([[(0.0, 0.0)]]) is None


def test_dominant_spacing_finds_the_hatch_pitch():
    assert dominant_spacing_mm(hatching(3.0)) == pytest.approx(3.0)


def test_dominant_spacing_is_none_for_too_few_lines():
    assert dominant_spacing_mm([[(0.0, 0.0), (1.0, 1.0)]]) is None


def test_resonance_is_flagged_for_a_critical_combination():
    # 3 mm lange Striche bei 600 mm/min = 10 mm/s → 10/(2·3) = 1,7 Hz,
    # direkt auf der Pendelfrequenz
    warning = resonance_warning(short_strokes(3.0), feed_mm_per_min=600.0)
    assert warning.critical
    assert "wellige Linien" in warning.message
    assert warning.reversal_hz == pytest.approx(1.67, abs=0.05)


def test_a_long_hatch_is_not_flagged_however_tight_the_spacing():
    """Der Fall, den die alte Rechnung falsch herum beurteilt hat.

    1000 mm lange Bahnen mit 3 mm Abstand: die Gondel kehrt alle 100 Sekunden
    um. Das regt ein Pendel mit 1,6 Hz nicht an — die alte Fassung rechnete
    mit dem Abstand und meldete hier Alarm.
    """
    warning = resonance_warning(hatching(3.0), feed_mm_per_min=600.0)
    assert not warning.critical
    assert warning.reversal_hz < 0.1


def test_uncritical_combination_is_not_flagged():
    warning = resonance_warning(short_strokes(3.0), feed_mm_per_min=3000.0)
    assert not warning.critical
    assert "unkritisch" in warning.message


def test_the_suggested_way_out_actually_works():
    """Der Vorschlag muss aus der Resonanz herausführen, nicht wieder hinein."""
    warning = resonance_warning(short_strokes(3.0), feed_mm_per_min=600.0)
    slower = float(warning.message.split("unter ")[1].split(" ")[0])
    faster = float(warning.message.split("über ")[1].split(" ")[0])
    assert not resonance_warning(short_strokes(3.0), slower).critical
    assert not resonance_warning(short_strokes(3.0), faster).critical


def test_resonance_check_on_a_real_pattern():
    pattern = build("grid", 2000.0, 2500.0, 50.0, spacing=250.0)
    warning = resonance_warning(pattern.lines, feed_mm_per_min=1500.0)
    assert warning is not None
    assert warning.spacing_mm > 0


def test_no_warning_without_geometry():
    assert resonance_warning([], 1500.0) is None
    assert resonance_warning([[(0.0, 0.0)]], 1500.0) is None


# -- Positionsabhängiger Vorschub -------------------------------------------


def test_feeds_follow_the_local_conditioning():
    """Wo die Auflösung schlechter ist, muss der Vorschub kleiner sein.

    Welche Zone das ist, hängt an der Aufhängung: bei hohen Ankern wird der
    untere Rand zum Problemfall, bei flachen der obere. Die Funktion soll das
    selbst herausfinden, statt eine Richtung anzunehmen.
    """
    kinematics = default_kinematics(anchor_above_mm=400)
    bottom = [(500.0, 100.0), (1500.0, 100.0)]
    top = [(500.0, 2450.0), (1500.0, 2450.0)]
    feeds = conditioning_feeds([bottom, top], kinematics, 1500.0)

    worse_is_bottom = kinematics.resolution_mm(*bottom[0]) > kinematics.resolution_mm(*top[0])
    assert (feeds[0] < feeds[1]) is worse_is_bottom


def test_the_better_line_keeps_the_full_feed():
    kinematics = default_kinematics(anchor_above_mm=400)
    feeds = conditioning_feeds(
        [[(500.0, 100.0), (1500.0, 100.0)], [(500.0, 2450.0), (1500.0, 2450.0)]],
        kinematics,
        1500.0,
    )
    assert max(feeds) == pytest.approx(1500.0)
    assert min(feeds) < 1500.0


def test_feeds_never_fall_below_the_floor():
    kinematics = default_kinematics(anchor_above_mm=400)
    lines = [[(0.0, 2500.0), (2000.0, 2500.0)]]
    feeds = conditioning_feeds(lines, kinematics, 1500.0, min_factor=0.4)
    assert feeds[0] >= 1500.0 * 0.4


def test_feeds_never_exceed_the_base_feed():
    kinematics = default_kinematics(anchor_above_mm=400)
    pattern = build("grid", 2000.0, 2500.0, 50.0, spacing=500.0)
    feeds = conditioning_feeds(pattern.lines, kinematics, 1500.0)
    assert max(feeds) <= 1500.0
    assert len(feeds) == len(pattern.lines)


def test_feeds_match_the_number_of_drawable_lines():
    kinematics = default_kinematics()
    lines = [[(0.0, 0.0), (100.0, 0.0)], [(5.0, 5.0)], []]
    assert len(conditioning_feeds(lines, kinematics, 1000.0)) == 1


def test_invalid_arguments_are_rejected():
    kinematics = default_kinematics()
    with pytest.raises(ValueError):
        conditioning_feeds([[(0.0, 0.0), (1.0, 1.0)]], kinematics, 0)
    with pytest.raises(ValueError):
        conditioning_feeds([[(0.0, 0.0), (1.0, 1.0)]], kinematics, 1000.0, min_factor=0)
