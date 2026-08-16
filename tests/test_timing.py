"""Laufzeit mit Bewegungsprofil.

Geprüft wird gegen von Hand nachrechenbare Fälle, nicht gegen den Code selbst
— sonst zementiert der Test nur den Fehler, den er finden soll.
"""

import math

import pytest

from wallplotter.timing import (
    MotionLimits,
    path_duration_s,
    plot_duration_s,
    profile_duration_s,
)

LIMITS = MotionLimits(acceleration_mm_s2=200.0, max_rate_mm_min=4000.0)
V = 25.0  # 1500 mm/min in mm/s


# ---------------------------------------------------------------------------
# Einzelnes Segment
# ---------------------------------------------------------------------------


def test_trapezoid_matches_the_hand_calculation():
    """Aus dem Stand auf 25 mm/s und zurück, über 100 mm.

    Beschleunigen: v/a = 0,125 s über v²/2a = 1,5625 mm. Zweimal, dann bleiben
    96,875 mm mit 25 mm/s = 3,875 s. Zusammen 4,125 s.
    """
    assert profile_duration_s(100.0, 0.0, 0.0, V, 200.0) == pytest.approx(4.125)


def test_triangle_when_the_feed_is_never_reached():
    """Unter 2·v²/2a = 3,125 mm kommt die Maschine gar nicht auf Tempo.

    Bei 1 mm: Spitze v = √(a·L) = √200 mm/s, Dauer 2·v/a.
    """
    peak = math.sqrt(200.0 * 1.0)
    assert profile_duration_s(1.0, 0.0, 0.0, V, 200.0) == pytest.approx(2 * peak / 200.0)


def test_a_segment_entered_at_speed_is_faster():
    voll = profile_duration_s(50.0, 0.0, 0.0, V, 200.0)
    fliegend = profile_duration_s(50.0, V, V, V, 200.0)
    assert fliegend < voll
    assert fliegend == pytest.approx(50.0 / V)


def test_zero_length_costs_nothing():
    assert profile_duration_s(0.0, 0.0, 0.0, V, 200.0) == 0.0


# ---------------------------------------------------------------------------
# Streckenzug
# ---------------------------------------------------------------------------


def test_splitting_a_straight_line_changes_nothing():
    """Die wichtigste Eigenschaft überhaupt.

    Eine Spirale besteht aus tausend kurzen, fast geraden Stücken. Würde jedes
    einzeln beschleunigt und gebremst, käme eine Schätzung heraus, die um den
    Faktor drei danebenliegt — und die Zahl im GCode-Kopf wäre wertlos.
    """
    am_stueck = path_duration_s([(0.0, 0.0), (1000.0, 0.0)], 1500, LIMITS)
    zerlegt = path_duration_s([(float(x), 0.0) for x in range(1001)], 1500, LIMITS)
    assert zerlegt == pytest.approx(am_stueck, rel=1e-9)


def test_a_gentle_curve_is_barely_slower_than_a_straight_line():
    gerade = path_duration_s([(0.0, 0.0), (500.0, 0.0)], 1500, LIMITS)
    bogen = path_duration_s(
        [(500 * i / 200, 20 * math.sin(math.pi * i / 200)) for i in range(201)], 1500, LIMITS
    )
    assert bogen < gerade * 1.35


@pytest.mark.parametrize(
    ("teilung", "mindestens"),
    [(20.0, 1.10), (10.0, 1.25), (3.0, 1.9)],
)
def test_reversals_cost_real_time(teilung, mindestens):
    """Eine Schraffur kehrt alle paar Millimeter um — dort steht die Maschine
    jedes Mal wirklich still, und je enger die Teilung, desto teurer.

    Bei 3 mm Bahnabstand — einer ganz normalen Schraffur — dauert derselbe Weg
    doppelt so lang wie die naive Rechnung verspricht.
    """
    schritte = int(100 / teilung)
    zack = [(0.0, 0.0)]
    for i in range(schritte):
        zack.append((teilung if i % 2 == 0 else 0.0, (i + 1) * teilung / 10))
    laenge = sum(math.dist(zack[k], zack[k + 1]) for k in range(len(zack) - 1))
    dauer = path_duration_s(zack, 1500, LIMITS)
    assert dauer > (laenge / V) * mindestens


def test_a_full_reversal_comes_to_a_standstill():
    """Hin und exakt zurück: dazwischen muss das Tempo auf null."""
    hin_zurueck = path_duration_s([(0.0, 0.0), (50.0, 0.0), (0.0, 0.0)], 1500, LIMITS)
    zweimal_einzeln = 2 * path_duration_s([(0.0, 0.0), (50.0, 0.0)], 1500, LIMITS)
    assert hin_zurueck == pytest.approx(zweimal_einzeln, rel=1e-9)


def test_the_feed_is_capped_by_the_axis_limit():
    """Wer 9999 mm/min einträgt, bekommt trotzdem nur, was die Achse hergibt."""
    schnell = path_duration_s([(0.0, 0.0), (1000.0, 0.0)], 99999, LIMITS)
    am_limit = path_duration_s([(0.0, 0.0), (1000.0, 0.0)], LIMITS.max_rate_mm_min, LIMITS)
    assert schnell == pytest.approx(am_limit)


def test_duplicate_points_cost_nothing():
    mit = path_duration_s([(0.0, 0.0), (0.0, 0.0), (50.0, 0.0), (50.0, 0.0)], 1500, LIMITS)
    ohne = path_duration_s([(0.0, 0.0), (50.0, 0.0)], 1500, LIMITS)
    assert mit == pytest.approx(ohne)


def test_degenerate_input_is_free():
    assert path_duration_s([], 1500, LIMITS) == 0.0
    assert path_duration_s([(1.0, 1.0)], 1500, LIMITS) == 0.0


def test_a_feed_of_zero_is_rejected():
    with pytest.raises(ValueError, match="Vorschub"):
        path_duration_s([(0.0, 0.0), (1.0, 0.0)], 0, LIMITS)


# ---------------------------------------------------------------------------
# Ganzer Plot
# ---------------------------------------------------------------------------


def test_the_estimate_is_never_shorter_than_the_naive_one():
    """Beschleunigung kostet Zeit, sie schenkt keine.

    Gilt so lange, wie der Vorschub unter der Achsengrenze bleibt — sonst
    deckelt das Modell und ist zu Recht schneller.
    """
    from wallplotter.geometry import estimate_duration_s

    lines = [[(0.0, float(y)), (200.0, float(y))] for y in range(0, 200, 20)]
    echt = plot_duration_s(lines, 1500, 3000, LIMITS)
    naiv = estimate_duration_s(lines, 1500, 3000)
    assert echt >= naiv


def test_short_strokes_take_far_longer_than_the_naive_estimate():
    """Der Fall, für den das Modul gebaut wurde: ein Punktraster."""
    from wallplotter.geometry import estimate_duration_s

    punkte = [[(float(i % 50) * 10, float(i // 50) * 10),
               (float(i % 50) * 10 + 1, float(i // 50) * 10)] for i in range(500)]
    echt = plot_duration_s(punkte, 1500, 3000, LIMITS)
    naiv = estimate_duration_s(punkte, 1500, 3000)
    # gemessen 1,84× — die Schranke lässt Luft, die Aussage bleibt dieselbe
    assert echt > naiv * 1.5


def test_tool_overhead_is_counted_per_line():
    lines = [[(0.0, float(y)), (100.0, float(y))] for y in range(10)]
    ohne = plot_duration_s(lines, 1500, 3000, LIMITS)
    mit = plot_duration_s(lines, 1500, 3000, LIMITS, line_overhead_s=0.5)
    assert mit - ohne == pytest.approx(10 * 0.5)


def test_the_park_run_can_be_left_out():
    lines = [[(500.0, 500.0), (600.0, 500.0)]]
    assert plot_duration_s(lines, 1500, 3000, LIMITS, park=False) < plot_duration_s(
        lines, 1500, 3000, LIMITS
    )


def test_nothing_to_plot_takes_no_time():
    assert plot_duration_s([], 1500, 3000, LIMITS) == 0.0
    assert plot_duration_s([[(1.0, 1.0)]], 1500, 3000, LIMITS) == 0.0


# ---------------------------------------------------------------------------
# Grenzwerte
# ---------------------------------------------------------------------------


def test_limits_reject_nonsense():
    for kwargs in ({"acceleration_mm_s2": 0}, {"max_rate_mm_min": -1}, {"junction_deviation_mm": 0}):
        with pytest.raises(ValueError):
            MotionLimits(**kwargs)


def test_limits_are_hashable_so_plotconfig_stays_frozen():
    assert hash(MotionLimits()) == hash(MotionLimits())


def test_more_acceleration_is_faster():
    lines = [[(0.0, float(y)), (20.0, float(y))] for y in range(20)]
    lahm = plot_duration_s(lines, 1500, 3000, MotionLimits(acceleration_mm_s2=50))
    flott = plot_duration_s(lines, 1500, 3000, MotionLimits(acceleration_mm_s2=800))
    assert flott < lahm


def test_a_larger_junction_deviation_rounds_corners_faster():
    ecke = [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0)]
    eng = path_duration_s(ecke, 1500, MotionLimits(junction_deviation_mm=0.001))
    weit = path_duration_s(ecke, 1500, MotionLimits(junction_deviation_mm=0.5))
    assert weit < eng
