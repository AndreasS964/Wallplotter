"""Werkzeugköpfe — Stifte heute, Laser später.

Der Laserteil ist bewusst streng: an dem Kopf hängt kein Strich, der schief
wird, sondern eine Wand, die brennt. Geprüft wird deshalb nicht nur, dass der
richtige GCode herauskommt, sondern auch, dass der falsche gar nicht erst
entsteht.
"""

import pytest

from wallplotter.config import PenConfig, PlotConfig
from wallplotter.gcode import lines_to_gcode
from wallplotter.pipeline import Layer
from wallplotter.toolhead import (
    PENS,
    TOOLHEADS,
    LaserToolhead,
    PenToolhead,
    Toolhead,
    ToolheadError,
    describe_toolheads,
    head_for,
    pen_map,
    toolhead_by_name,
)

SQUARE = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]]
AREA = PlotConfig(width_mm=100, height_mm=100, margin_mm=10, invert_y=False)


# ---------------------------------------------------------------------------
# Schnittstelle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(TOOLHEADS))
def test_every_catalogue_head_fulfils_the_protocol(key):
    head = TOOLHEADS[key]
    assert isinstance(head, Toolhead)
    assert head.describe()
    assert head.program_start() and head.program_end()
    assert head.engage() and head.retract()
    assert head.cycle_time_s() >= 0
    assert head.passes >= 1
    assert head.feed_for(1500) > 0
    assert head.change_prompt("#000000")


def test_gcode_module_knows_nothing_about_servos_anymore():
    """Der Sinn der Trennung: was das Werkzeug tut, steht beim Werkzeug.

    Bliebe ein ``M3`` in gcode.py stehen, wäre das der Anfang der nächsten
    Sonderbehandlung — und ein Laser bekäme sie mitgeliefert.
    """
    import ast
    import inspect

    from wallplotter import gcode

    tree = ast.parse(inspect.getsource(gcode))
    # Docstrings erklären den Dialekt und dürfen ihn nennen; erzeugen darf ihn
    # nur der Kopf. Geprüft werden deshalb die Zeichenketten im Code.
    docstrings = {
        text
        for parent in ast.walk(tree)
        if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef))
        for text in [ast.get_docstring(parent, clean=False)]
        if text
    }
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]
    for text in literals:
        for forbidden in ("M3", "M5", "G4"):
            assert forbidden not in text, (
                f"{text!r} gehört zum Werkzeug, nicht in gcode.py"
            )


def test_unknown_head_names_the_known_ones():
    with pytest.raises(ToolheadError, match="fineliner"):
        toolhead_by_name("bleistift")


def test_catalogue_is_described_with_a_caveat():
    text = describe_toolheads()
    assert "laser" in text
    # Die Stiftwerte sind geschätzt — das muss dranstehen, sonst misst niemand nach
    assert "Startwerte" in text


# ---------------------------------------------------------------------------
# Stift
# ---------------------------------------------------------------------------


def test_pen_emits_the_same_gcode_as_before():
    """Der Umbau darf am Stiftprogramm nichts geändert haben."""
    pen = PenToolhead(down_value=30, up_value=0, dwell_s=0.25)
    assert pen.engage() == ["M3 S30", "G4 P0.25"]
    assert pen.retract() == ["M3 S0", "G4 P0.25"]
    assert pen.program_end() == ["M5 ; Servo/PWM aus"]


def test_pen_can_lift_with_m5():
    pen = PenToolhead(use_m5_for_up=True, dwell_s=0)
    assert pen.retract() == ["M5"]


def test_pen_config_is_still_the_old_name():
    assert PenConfig is PenToolhead


def test_pen_cycle_time_is_two_dwells():
    assert PenToolhead(dwell_s=0.3).cycle_time_s() == pytest.approx(0.6)


def test_pen_feed_overrides_the_configured_one():
    assert PenToolhead(draw_feed=700).feed_for(1500) == 700
    assert PenToolhead().feed_for(1500) == 1500


def test_pen_without_dwell_is_flagged():
    notes = PenToolhead(dwell_s=0).check(travel_as_g1=False, draw_feed=1500)
    assert any("Wartezeit" in note for note in notes)


def test_impossible_pen_values_are_rejected():
    with pytest.raises(ToolheadError):
        PenToolhead(dwell_s=-1)
    with pytest.raises(ToolheadError):
        PenToolhead(width_mm=0)
    with pytest.raises(ToolheadError):
        PenToolhead(draw_feed=0)


def test_catalogue_pens_differ_in_more_than_the_name():
    """Ein Katalog, in dem alle Stifte gleich sind, wäre nur Dekoration."""
    widths = {pen.width_mm for pen in PENS.values()}
    dwells = {pen.dwell_s for pen in PENS.values()}
    assert len(widths) >= 4
    assert len(dwells) >= 3


# ---------------------------------------------------------------------------
# Laser
# ---------------------------------------------------------------------------


def test_laser_scales_power_to_the_configured_maximum():
    """Der häufigste Laserfehler: ein hart verdrahtetes S-Maximum.

    Welcher S-Wert volle Leistung bedeutet, steht in der ``speed_map`` der
    config.yaml und ist je nach Aufbau 255 oder 1000 — der Faktor vier
    dazwischen ist der Unterschied zwischen Gravur und Brandloch.
    """
    assert LaserToolhead(power_pct=20, s_max=1000).s_value == 200
    assert LaserToolhead(power_pct=20, s_max=255).s_value == 51
    assert LaserToolhead(power_pct=100, s_max=255).s_value == 255


def test_laser_defaults_to_dynamic_power():
    """M4 regelt die Leistung mit dem Vorschub und schaltet im Stillstand ab.

    Ein Seilplotter beschleunigt weich und lange — mit konstanter Leistung
    brennt jede Ecke durch.
    """
    assert LaserToolhead().mode == "M4"
    assert LaserToolhead(dynamic=False).mode == "M3"
    assert any("M4 S0" in line for line in LaserToolhead().program_start())


def test_laser_never_dwells():
    """Was dem Servo Zeit gibt, gibt dem Laser Gelegenheit, ein Loch zu bohren."""
    laser = LaserToolhead()
    assert not any(line.startswith("G4") for line in laser.engage())
    assert not any(line.startswith("G4") for line in laser.retract())
    assert laser.cycle_time_s() == 0.0


def test_laser_turns_the_power_off_before_every_travel():
    assert LaserToolhead().retract() == ["S0"]


def test_laser_refuses_travel_as_g1():
    """Der einzige harte Riegel: ein G1-Leerweg fährt mit eingeschaltetem Strahl."""
    with pytest.raises(ToolheadError, match="Brandsatz"):
        LaserToolhead().check(travel_as_g1=True, draw_feed=600)


def test_laser_gcode_is_refused_at_the_source():
    """Nicht erst beim Ausführen — es darf gar keine Datei entstehen."""
    config = PlotConfig(width_mm=100, height_mm=100, margin_mm=10, travel_as_g1=True,
                        toolhead=LaserToolhead())
    with pytest.raises(ToolheadError):
        lines_to_gcode(SQUARE, config)


def test_laser_power_above_the_limit_is_refused_not_clamped():
    """Zu viel Leistung ist kein Rundungsfehler, den man stillschweigend kürzt."""
    with pytest.raises(ToolheadError, match="außerhalb"):
        LaserToolhead(power_pct=120)
    with pytest.raises(ToolheadError):
        LaserToolhead(power_pct=60, max_power_pct=50)
    # bewusst angehoben ist in Ordnung
    assert LaserToolhead(power_pct=60, max_power_pct=80).s_value > 0


def test_laser_rejects_nonsense_configuration():
    with pytest.raises(ToolheadError):
        LaserToolhead(s_max=0)
    with pytest.raises(ToolheadError):
        LaserToolhead(passes=0)
    with pytest.raises(ToolheadError, match="air_assist"):
        LaserToolhead(air_assist="M42")


def test_laser_warns_about_the_open_wall():
    notes = LaserToolhead().check(travel_as_g1=False, draw_feed=600)
    assert any("Schutzbrille" in note for note in notes)
    assert any("senkrechte" in note for note in notes)


def test_constant_power_is_warned_about():
    notes = LaserToolhead(dynamic=False).check(travel_as_g1=False, draw_feed=600)
    assert any("M3" in note and "brennen" in note.lower() for note in notes)


def test_air_assist_goes_on_before_and_off_after():
    laser = LaserToolhead(air_assist="M8", air_delay_s=0.5)
    start, end = laser.program_start(), laser.program_end()
    assert any(line.startswith("M8") for line in start)
    assert any(line.startswith("G4 P0.5") for line in start)   # Luft aufbauen lassen
    assert any(line.startswith("M9") for line in end)
    assert not any(line.startswith("M8") for line in LaserToolhead(air_assist=None).program_start())


def test_laser_program_is_safe_from_end_to_end():
    """Die Invarianten am fertigen Programm, nicht an den Bausteinen."""
    config = PlotConfig(
        width_mm=100, height_mm=100, margin_mm=10, invert_y=False,
        toolhead=LaserToolhead(power_pct=50, s_max=1000),
    )
    from wallplotter.resume import scan_program

    program = lines_to_gcode(SQUARE, config)
    lines = program.splitlines()
    states = scan_program(program)

    # Die eigentliche Invariante: bei keiner Eilfahrt darf Leistung anliegen.
    for index, line in enumerate(lines):
        if line.startswith("G0 "):
            assert not states[index].drawing, f"Leerweg mit Strahl in Zeile {index + 1}: {line}"

    # Leerwege sind G0, niemals G1 — im Lasermodus zündet GRBL bei G0 nie
    assert not any(line.startswith("G1 F") for line in lines)

    # vor der ersten Bewegung ist der Laser nachweislich aus
    first_move = next(i for i, line in enumerate(lines) if line.startswith(("G0 ", "G1 ")))
    assert any(line.startswith("M5") for line in lines[:first_move])

    # am Ende: Leistung 0, Spindel aus, Luft aus
    tail = "\n".join(lines[-6:])
    assert "S0" in tail and "M5" in tail and "M9" in tail


def test_passes_are_unrolled_because_grbl_has_no_loops():
    config = PlotConfig(
        width_mm=100, height_mm=100, margin_mm=10, invert_y=False,
        toolhead=LaserToolhead(passes=3),
    )
    text = lines_to_gcode(SQUARE, config)
    assert text.count("; Durchgang") == 3
    assert text.count("G1 X") == 3 * 4        # vier Zeichenbewegungen je Durchgang


def test_passes_multiply_the_estimate():
    from wallplotter.gcode import prepare_geometry, stats_for

    one = PlotConfig(width_mm=100, height_mm=100, margin_mm=10, toolhead=LaserToolhead())
    three = PlotConfig(width_mm=100, height_mm=100, margin_mm=10,
                       toolhead=LaserToolhead(passes=3))
    geometry = prepare_geometry(SQUARE, one, fit=True)
    assert stats_for(geometry, three).draw_mm == pytest.approx(
        3 * stats_for(geometry, one).draw_mm
    )


# ---------------------------------------------------------------------------
# Zuordnung Farbebene → Kopf
# ---------------------------------------------------------------------------


def test_pen_map_parses_the_assignment():
    mapping = pen_map("#000000=fineliner,#e02020=marker")
    assert mapping["#000000"].name == "Fineliner schwarz"
    assert mapping["#e02020"].name == "Marker"


def test_pen_map_rejects_nonsense():
    with pytest.raises(ToolheadError, match="Ebene=Kopf"):
        pen_map("#000000")
    with pytest.raises(ToolheadError):
        pen_map("#000000=filzstift")


def test_head_for_prefers_the_label_then_the_colour():
    layer = Layer(1, "#e02020", [], name="Kontur")
    default = PenToolhead(name="Standard")
    assert head_for(layer, None, default) is default
    assert head_for(layer, {"#e02020": PENS["marker"]}, default).name == "Marker"
    assert head_for(layer, {"Kontur": PENS["kreide"]}, default).name == "Kreidemarker"


def test_every_layer_gets_its_own_pen():
    from wallplotter.gcode import layers_to_gcode

    layers = [
        Layer(1, "#000000", [[(0.0, 0.0), (50.0, 0.0)]]),
        Layer(2, "#e02020", [[(0.0, 10.0), (50.0, 10.0)]]),
    ]
    programs = layers_to_gcode(
        layers, AREA, tools={"#000000": PENS["fineliner"], "#e02020": PENS["pinsel"]}
    )
    assert "S30" in programs["#000000"]          # Fineliner
    assert "S26" in programs["#e02020"]          # Pinselstift drückt weniger auf
    assert "F700" in programs["#e02020"]         # und fährt langsamer


def test_the_pause_text_names_the_next_pen():
    from wallplotter.gcode import layers_to_gcode

    layers = [
        Layer(1, "#000000", [[(0.0, 0.0), (50.0, 0.0)]]),
        Layer(2, "#e02020", [[(0.0, 10.0), (50.0, 10.0)]]),
    ]
    combined = layers_to_gcode(
        layers, AREA, separate=False, tools={"#e02020": PENS["marker"]}
    )
    pause = next(line for line in combined.splitlines() if line.startswith("M0 "))
    assert "#e02020" in pause and "Marker" in pause


@pytest.mark.parametrize(
    "laser",
    [
        LaserToolhead(),
        LaserToolhead(passes=3, pass_pause_s=1.0),
        LaserToolhead(air_assist=None),
        LaserToolhead(air_assist="M7", air_delay_s=0.0),
        LaserToolhead(dynamic=False),
        LaserToolhead(tool_number=100),
        LaserToolhead(power_pct=100, s_max=255),
        LaserToolhead(power_pct=0.5, s_max=1000),
    ],
    ids=["vorgabe", "drei-durchgaenge", "ohne-luft", "m7-ohne-vorlauf",
         "konstant", "mit-werkzeugnummer", "volle-leistung", "kleinste-leistung"],
)
@pytest.mark.parametrize(
    "geometry",
    [
        SQUARE,
        [[(0.0, 0.0), (10.0, 10.0)]],                      # eine einzelne Linie
        [[(0.0, 0.0), (5.0, 0.0)], [(8.0, 8.0), (9.0, 9.0)]],
    ],
    ids=["quadrat", "eine-linie", "zwei-linien"],
)
def test_no_laser_configuration_moves_while_the_beam_is_on(laser, geometry):
    """Die eine Invariante, die über allen anderen steht.

    Egal wie der Kopf eingestellt ist: an keiner Stelle des Programms darf eine
    Eilfahrt stattfinden, während Leistung anliegt. Geprüft wird am fertigen
    Programm, nicht an den Bausteinen — dazwischen liegt die Schleife, die die
    Reihenfolge herstellt.
    """
    from wallplotter.resume import scan_program

    config = PlotConfig(
        width_mm=100, height_mm=100, margin_mm=10, invert_y=False, toolhead=laser
    )
    program = lines_to_gcode(geometry, config)
    lines = program.splitlines()
    states = scan_program(program)

    for index, line in enumerate(lines):
        if line.startswith("G0 "):
            assert not states[index].drawing, (
                f"Eilfahrt mit Strahl in Zeile {index + 1}: {line}"
            )
    # kein G1-Leerweg, keine Wartezeit mit Strahl
    assert not any(line.startswith("G1 F") for line in lines)
    for index, line in enumerate(lines):
        if line.startswith("G4"):
            assert not states[index].drawing, f"Wartezeit mit Strahl in Zeile {index + 1}"
    # und am Ende ist alles aus
    assert not states[-1].drawing
