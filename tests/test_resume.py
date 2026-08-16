"""Einen abgebrochenen Plot fortsetzen.

Der Punkt ist nicht das Schneiden, sondern der modale Zustand: GCode gilt
weiter, bis es jemand ändert. Ein Restprogramm ohne Einheiten, ohne absolute
Koordinaten oder mit angesetztem Stift beim ersten Leerweg zieht einen Strich
quer durch das halbfertige Bild.
"""

import pytest

from wallplotter.config import PenConfig, PlotConfig
from wallplotter.gcode import lines_to_gcode
from wallplotter.resume import (
    ResumeError,
    line_for_percent,
    resume_file,
    resume_program,
    scan_program,
)

CONFIG = PlotConfig(width_mm=1000, height_mm=1000, margin_mm=50, invert_y=False)
LINES = [
    [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)],
    [(200.0, 200.0), (300.0, 200.0)],
    [(400.0, 0.0), (400.0, 100.0), (500.0, 100.0)],
]


@pytest.fixture
def program() -> str:
    return lines_to_gcode(LINES, CONFIG)


def moves(program: str) -> list[str]:
    """Nur echte Fahrbefehle — Vorsicht: "G17"[:2] ist ebenfalls "G1"."""
    return [line for line in program.splitlines() if line.split()[:1] in (["G0"], ["G1"])]


def test_scan_tracks_position_feed_and_tool(program):
    states = scan_program(program)
    final = states[-1]
    assert final.units_mm and final.absolute
    assert final.tool_on is False           # das Programm endet mit M5
    assert final.feed == 1500.0
    assert final.draw_count == 5            # 5 G1-Bewegungen mit Werkzeug an
    assert (final.x, final.y) == (0.0, 0.0)  # Parkfahrt auf den Nullpunkt


def test_percent_maps_to_a_line(program):
    assert line_for_percent(program, 0) == 0
    assert line_for_percent(program, 100) == len(program.splitlines()) - 1
    middle = line_for_percent(program, 50)
    assert 0 < middle < len(program.splitlines())


def test_percent_outside_the_range_is_rejected(program):
    with pytest.raises(ResumeError):
        line_for_percent(program, 101)


def test_resume_rebuilds_the_modal_state(program):
    rest = resume_program(program, percent=70)
    lines = rest.splitlines()
    assert "G21" in lines and "G90" in lines
    # Werkzeug aus, bevor irgendetwas verfahren wird
    tool_off = next(i for i, line in enumerate(lines) if line.startswith("M5"))
    first_move = next(i for i, line in enumerate(lines) if line.split()[:1] in (["G0"], ["G1"]))
    assert tool_off < first_move


def test_resume_starts_a_new_stroke_rather_than_joining_one(program):
    """Mitten im Strich fortzusetzen hinterlässt einen sichtbaren Ansatz."""
    lines = program.splitlines()
    inside = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("G1 X590")          # zweiter Strich, mittendrin
    )
    rest = resume_program(program, line=inside)
    # der Strich wird von seiner Anfahrt an neu gezogen
    assert "G0 X410 Y680" in rest
    assert "M3 S30" in rest.splitlines()[: rest.splitlines().index("G1 X590 Y680 F1500")]


def test_resume_can_also_cut_exactly(program):
    lines = program.splitlines()
    inside = lines.index("G1 X590 Y680 F1500")
    rest = resume_program(program, line=inside, whole_stroke=False)
    assert "G0 X410 Y680 ; an die Abbruchstelle" in rest
    assert any(line.startswith("M3 S30 ;") for line in rest.splitlines())
    # der Rest bringt sein eigenes F mit — dann keine überflüssige Zeile davor
    assert not any(line.startswith("F1500 ;") for line in rest.splitlines())


def test_resume_restores_a_modal_feed_the_rest_does_not_carry():
    """Fremde Dateien setzen F oft nur einmal im Kopf. Ohne Vorschub quittiert
    die Firmware die erste G1-Bewegung mit error:22."""
    fremd = "\n".join([
        "G21", "G90", "F900",
        "G0 X0 Y0", "M3 S30", "G1 X10 Y0", "G1 X10 Y10",
        "M3 S0", "G0 X50 Y50", "M3 S30", "G1 X60 Y50",
        "M5", "M2",
    ])
    rest = resume_program(fremd, line=9, whole_stroke=False)
    assert "F900 ; Vorschub war modal gesetzt" in rest


def test_resume_keeps_the_remaining_work(program):
    rest = resume_program(program, percent=70)
    original = moves(program)
    # alles ab dem Wiedereinstieg steht auch im Restprogramm
    assert original[-3:] == moves(rest)[-3:]
    assert rest.strip().endswith("M2 ; Programmende")


def test_resume_before_the_first_stroke_returns_the_whole_program(program):
    rest = resume_program(program, percent=1)
    assert "unveränderte" in rest
    assert moves(rest) == moves(program)


def test_resume_after_the_last_stroke_is_refused(program):
    with pytest.raises(ResumeError, match="schon durch"):
        resume_program(program, percent=99.9)


def test_resume_needs_exactly_one_anchor(program):
    with pytest.raises(ResumeError):
        resume_program(program)
    with pytest.raises(ResumeError):
        resume_program(program, line=5, percent=50)


def test_resume_rejects_a_line_outside_the_program(program):
    with pytest.raises(ResumeError, match="außerhalb"):
        resume_program(program, line=10_000)


def test_resume_of_an_empty_program():
    with pytest.raises(ResumeError, match="Leeres Programm"):
        resume_program("", line=0)


def test_resume_survives_travel_as_g1(program):
    """Mit travel_as_g1 sind Leerwege ebenfalls G1 — der Schnitt muss halten."""
    config = PlotConfig(
        width_mm=1000, height_mm=1000, margin_mm=50, invert_y=False, travel_as_g1=True
    )
    text = lines_to_gcode(LINES, config)
    rest = resume_program(text, percent=70)
    assert rest.strip().endswith("M2 ; Programmende")
    assert "M5 ; Werkzeug aus, bevor irgendetwas verfahren wird" in rest


def test_resume_survives_m5_style_pen_lift():
    """Mit use_m5_for_up steht statt M3 S0 ein nacktes M5 im Programm."""
    config = PlotConfig(
        width_mm=1000,
        height_mm=1000,
        margin_mm=50,
        invert_y=False,
        toolhead=PenConfig(use_m5_for_up=True),
    )
    text = lines_to_gcode(LINES, config)
    rest = resume_program(text, percent=70)
    assert "M3 S30" in rest
    assert rest.strip().endswith("M2 ; Programmende")


def test_resume_file(tmp_path, program):
    path = tmp_path / "wand.gcode"
    path.write_text(program, encoding="utf-8")
    rest = resume_file(path, percent=70)
    assert "Fortsetzung von wand.gcode" in rest


def test_resume_file_needs_the_file(tmp_path):
    with pytest.raises(ResumeError, match="Keine Datei"):
        resume_file(tmp_path / "weg.gcode", percent=50)


def test_pen_up_is_not_mistaken_for_drawing(program):
    """`M3 S0` hebt den Stift, `M3 S30` senkt ihn — beides ist M3.

    Wer nur auf den Buchstaben sieht, hält jeden Leerweg für einen Strich.
    Das Programm hat drei Linien mit zusammen fünf G1-Bewegungen; alles
    darüber hieße, dass Anfahrten mitgezählt werden.
    """
    assert scan_program(program)[-1].draw_count == 5


def test_travel_as_g1_is_not_counted_as_drawing():
    """Mit travel_as_g1 sind auch Leerwege G1 — der Zähler darf nicht anspringen."""
    config = PlotConfig(
        width_mm=1000, height_mm=1000, margin_mm=50, invert_y=False, travel_as_g1=True
    )
    text = lines_to_gcode(LINES, config)
    assert scan_program(text)[-1].draw_count == 5


def test_laser_power_lines_are_understood():
    """Beim Laser steht die Leistung in nackten S-Zeilen, nicht an M3."""
    from wallplotter.toolhead import LaserToolhead

    config = PlotConfig(
        width_mm=1000,
        height_mm=1000,
        margin_mm=50,
        invert_y=False,
        toolhead=LaserToolhead(power_pct=40),
    )
    text = lines_to_gcode(LINES, config)
    assert scan_program(text)[-1].draw_count == 5
    rest = resume_program(text, percent=70)
    assert rest.strip().endswith("M2 ; Programmende")


def test_no_drawn_segment_is_silently_travelled(program):
    """Der Fund, der diesen Umbau ausgelöst hat.

    Wurde am Werkzeugbefehl statt an der Anfahrt angesetzt, rutschte die letzte
    Zeichenbewegung des vorigen Strichs hinter das eingefügte „Werkzeug aus" —
    und wurde mit gehobenem Stift abgefahren statt gezeichnet. Im Bild fehlte
    lautlos ein Segment.

    Geprüft wird deshalb für *jede* Abbruchstelle: keine Zeichenbewegung darf
    verschwinden, also muss die Summe aus schon Gezeichnetem und Restprogramm
    mindestens das Ganze ergeben.
    """
    total = scan_program(program)[-1].draw_count
    for cut in range(len(program.splitlines())):
        try:
            rest = resume_program(program, line=cut)
        except ResumeError:
            continue
        done = scan_program(program)[cut - 1].draw_count if cut else 0
        left = scan_program(rest)[-1].draw_count
        assert done + left >= total, f"bei Zeile {cut} fehlen Zeichenbewegungen"


def test_resume_names_the_tool_that_has_to_be_in_the_holder():
    """Ein mehrfarbiges Programm hält mit M0 an. Wer mittendrin fortsetzt,
    muss wissen, welcher Stift jetzt stecken muss — sonst wird Rot schwarz."""
    from wallplotter.gcode import layers_to_gcode
    from wallplotter.pipeline import Layer

    layers = [
        Layer(1, "#000000", [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]]),
        Layer(2, "#e02020", [[(20.0, 20.0), (80.0, 20.0), (80.0, 80.0)]]),
    ]
    combined = layers_to_gcode(layers, CONFIG, separate=False)
    lines = combined.splitlines()
    pause = next(index for index, line in enumerate(lines) if line.startswith("M0 "))
    # tief genug hinter die Pause, damit der Wiedereinstieg auch dahinter liegt
    inside = next(
        index for index, line in enumerate(lines) if index > pause and line.startswith("G1 ")
    )
    rest = resume_program(combined, line=inside)
    assert "Im Halter muss stecken" in rest
    assert "#e02020" in rest


def test_a_program_without_any_tool_command_says_so():
    """Ein fremdes Programm, das den Stift über die Z-Achse hebt, lässt sich
    hier nicht rekonstruieren — das gehört gesagt, statt „schon fertig" zu
    melden."""
    z_achse = "\n".join(["G21", "G90", "G0 X10 Y10", "G1 Z-1", "G1 X50 Y10", "G1 Z1", "M2"])
    with pytest.raises(ResumeError, match="keine einzige Zeichenbewegung"):
        resume_program(z_achse, percent=50)
