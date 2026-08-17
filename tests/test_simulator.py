"""Die ganze Kette gegen den Simulator — über eine echte Netzwerkverbindung.

Der Unterschied zu ``test_upload.py`` ist der Punkt: Dort prüfen Attrappen,
*was* der Client sagt. Hier redet er mit einer Gegenstelle, die sich wie die
Firmware verhält — samt Handshake, Rahmen, Quittungen und den Eigenheiten, an
denen es sonst still schiefgeht. Was hier grün ist, ist nicht nur richtig
gemeint, sondern hat einmal funktioniert.
"""

from __future__ import annotations

import json
import time

import pytest

from wallplotter.channel import BoardChannel, ChannelError
from wallplotter.config import FluidNCConfig
from wallplotter.simulator import BoardSimulator, serve
from wallplotter.upload import FluidNCClient, FluidNCError

PROGRAM = "\n".join(
    ["G21", "G90"] + [f"G1 X{i * 10} Y{i * 5} F1500" for i in range(40)] + ["M5", ""]
)


@pytest.fixture
def board():
    """Ein Simulator je Test — Zustand aus einem anderen wäre die halbe Miete weg."""
    server = serve(port=0)
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def api(board):
    client = FluidNCClient(FluidNCConfig(host=board.host, timeout_s=5.0))
    try:
        yield client
    finally:
        client.close()


def wait_until(predicate, timeout_s=5.0, step_s=0.05):
    """Auf einen Zustand warten statt auf eine Uhr — Tests mit ``sleep`` lügen."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step_s)
    return None


# -- Dateien ----------------------------------------------------------------


def test_upload_lands_on_the_card_and_can_be_read_back(api, board):
    api.upload(PROGRAM, "wand.gcode")
    assert board.board.card["/wand.gcode"] == PROGRAM
    assert api.download("wand.gcode").strip() == PROGRAM.strip()


def test_upload_declares_its_size_so_the_firmware_can_check_it(api, board):
    """Die Firmware vergleicht die angekündigte Größe mit der angekommenen.

    Findet sie das Feld nicht, nimmt sie 0 an und prüft gar nicht — ein
    abgebrochener Upload fiele dann erst an der Wand auf.
    """
    api.upload(PROGRAM, "wand.gcode")
    assert board.board.card["/wand.gcode"] == PROGRAM

    # Eine falsch angekündigte Größe muss die Gegenstelle ablehnen.
    board.board.card.clear()
    session = api.session
    payload = PROGRAM.encode()
    session.post(
        f"http://{board.host}/upload",
        params={"path": "/"},
        data={"/wand.gcodeS": str(len(payload) + 10)},
        files={"/wand.gcode": ("/wand.gcode", payload, "text/plain")},
        timeout=5,
    )
    assert board.board.card == {}


def test_file_listing_is_json_with_the_keys_the_firmware_uses(api):
    api.upload(PROGRAM, "wand.gcode")
    listing = json.loads(api.list_files("/"))
    assert listing["status"] == "Ok"
    assert [entry["name"] for entry in listing["files"]] == ["wand.gcode"]
    assert listing["files"][0]["size"] == len(PROGRAM.encode())


def test_reading_a_missing_file_is_an_error_not_an_empty_string(api):
    with pytest.raises(FluidNCError, match="error:60"):
        api.download("gibtsnicht.gcode")


# -- Maschine ---------------------------------------------------------------


def test_status_and_position_come_back_over_the_channel(api):
    assert api.status().state == "Idle"
    api.jog(dx=120, dy=340)
    assert api.position() == (120.0, 340.0)


def test_zero_and_work_offset_arrive_as_gcode(api, board):
    api.set_zero(10, 20)
    api.set_work_offset(30, 40)
    assert "G92 X10.000 Y20.000" in board.board.log
    assert "G10 L20 P1 X30.000 Y40.000" in board.board.log


def test_a_job_reports_progress_and_ends_by_itself(api, board):
    board.board.bytes_per_s = 600
    api.upload(PROGRAM, "wand.gcode")
    api.run_file("/wand.gcode")

    running = wait_until(lambda: api.status().state == "Run")
    assert running, "der Job ist nie angelaufen"
    midway = wait_until(lambda: (api.status().sd_percent or 0) > 20)
    assert midway, "der Fortschritt bewegt sich nicht"

    done = wait_until(lambda: api.status().state == "Idle", timeout_s=10)
    assert done
    assert api.status().sd_file is None


def test_hold_and_cycle_start_do_what_the_buttons_promise(api, board):
    board.board.bytes_per_s = 400
    api.upload(PROGRAM, "wand.gcode")
    api.run_file("/wand.gcode")
    assert wait_until(lambda: api.status().state == "Run")

    api.pause()
    assert wait_until(lambda: api.status().state == "Hold")
    api.resume()
    assert wait_until(lambda: api.status().state == "Run")


def test_stop_during_motion_leaves_an_alarm_not_a_tidy_idle(api, board):
    """Ein Soft-Reset in der Fahrt kostet die Maschinenposition.

    GRBL geht dafür in Alarm — und das ist keine Formalie: Der Nullpunkt ist
    danach weg, und weiterplotten heißt erst wieder eine Ecke anfahren.
    """
    board.board.bytes_per_s = 400
    api.upload(PROGRAM, "wand.gcode")
    api.run_file("/wand.gcode")
    assert wait_until(lambda: api.status().state == "Run")

    api.stop()
    state = wait_until(lambda: api.status().state.startswith("Alarm") and api.status().state)
    assert state, "nach dem Stopp müsste ein Alarm stehen"
    assert api.status().sd_file is None

    api.send_command("$X")
    assert wait_until(lambda: api.status().state == "Idle")


def test_m0_pauses_for_the_pen_change(api, board):
    """``--layers --one-file`` hängt daran, dass FluidNC ``M0`` als Pause kennt.

    Es kennt sie (GCode.cpp: ``ProgramFlow::Paused`` löst einen Feed Hold aus),
    und heraus kommt man mit Cycle Start — also mit demselben „Weiter".
    """
    board.board.bytes_per_s = 400
    api.upload("G21\nG1 X10 Y10 F1000\nM0\nG1 X20 Y20 F1000\n", "farben.gcode")
    api.run_file("/farben.gcode")

    assert wait_until(lambda: api.status().state == "Hold"), "M0 hat nicht angehalten"
    api.resume()
    assert wait_until(lambda: api.status().state == "Idle")


# -- Die Eigenheiten, an denen es sonst schiefgeht --------------------------


def test_http_commands_are_refused_while_the_machine_moves(api, board):
    """``$HTTP/BlockDuringMotion`` steht ab Werk an — und blockiert dann alles."""
    board.board.bytes_per_s = 300
    api.upload(PROGRAM, "wand.gcode")
    api.run_file("/wand.gcode")
    assert wait_until(lambda: api.status().state == "Run")

    with pytest.raises(FluidNCError, match="503"):
        api.send_http_command("$SD/Status")

    # Der Kanal kommt trotzdem durch — deshalb läuft alles Wichtige dort.
    assert api.status().state == "Run"


def test_the_channel_keeps_working_when_http_is_blocked(api, board):
    board.board.bytes_per_s = 300
    api.upload(PROGRAM, "wand.gcode")
    api.run_file("/wand.gcode")
    assert wait_until(lambda: api.status().state == "Run")
    api.pause()
    assert wait_until(lambda: api.status().state == "Hold")


def test_gcode_over_http_does_not_reach_the_parser(board):
    """Der Beweis für die Arbeitsteilung — hier absichtlich falsch herum.

    ``settings_execute_line()`` schneidet das erste Zeichen ab. Aus
    ``G92 X0 Y0`` wird die Suche nach einer Einstellung ``92 X0 Y0``: kein
    Fehler, den man sieht, sondern ein Nullpunkt, der nie gesetzt wurde.
    """
    api = FluidNCClient(FluidNCConfig(host=board.host, timeout_s=5.0))
    answer = api.send_http_command("G92 X0.000 Y0.000")
    assert "Invalid statement" in answer
    assert board.board.log == []
    api.close()


def test_status_over_http_is_not_a_status_report(board):
    api = FluidNCClient(FluidNCConfig(host=board.host, timeout_s=5.0))
    answer = api.send_http_command("?")
    assert not answer.startswith("<")
    api.close()


def test_the_old_sdfiles_path_is_a_404(board):
    """Der Pfad aus der ESP3D-Doku. In FluidNC gibt es ihn nicht."""
    import requests

    response = requests.get(f"http://{board.host}/sdfiles", params={"path": "/"}, timeout=5)
    assert response.status_code == 404


def test_reading_a_file_while_plotting_is_refused(api, board):
    """``$SD/Show`` verlangt Idle oder Alarm — mitten im Plot liest niemand."""
    board.board.bytes_per_s = 300
    api.upload(PROGRAM, "wand.gcode")
    api.run_file("/wand.gcode")
    assert wait_until(lambda: api.status().state == "Run")
    with pytest.raises(FluidNCError, match="Idle error"):
        api.download("wand.gcode")


# -- Kanal ------------------------------------------------------------------


def test_the_channel_reports_by_itself_without_being_asked(board):
    """Alle 200 ms von selbst — daran hängt der Fortschrittsbalken."""
    with BoardChannel(board.host) as channel:
        lines = channel.poll(1.0)
        assert any(line.startswith("<") for line in lines)


def test_a_closed_port_is_a_clear_message_not_a_traceback():
    channel = BoardChannel("127.0.0.1:1", connect_timeout_s=1.0)
    with pytest.raises(ChannelError, match="antwortet nicht"):
        channel.connect()


def test_a_server_that_is_not_fluidnc_says_so(board):
    """Ein HTTP-Server ohne WebSocket antwortet mit 200 statt mit 101."""
    channel = BoardChannel(board.host)
    channel.connect()
    channel.close()

    # Derselbe Port, aber ohne Upgrade-Kopfzeile: die Startseite kommt zurück.
    import requests

    assert requests.get(f"http://{board.host}/", timeout=5).status_code == 200


# -- Selbsttest -------------------------------------------------------------


def test_doctor_sees_the_simulated_board(board):
    from wallplotter.doctor import OK, check_board

    checks = {check.name: check for check in check_board(board.host)}
    assert checks["Board erreichbar"].status == OK
    assert checks["Position"].status == OK
    assert checks["SD-Karte"].status == OK


def test_the_simulator_runs_a_pattern_from_end_to_end(api, board):
    """Der Ablauf, der am Tag der Inbetriebnahme zählt: erzeugen, hoch, los."""
    from wallplotter.config import PlotConfig
    from wallplotter.gcode import geometry_to_gcode, prepare_geometry
    from wallplotter.patterns import build

    board.board.bytes_per_s = 20000
    config = PlotConfig(width_mm=1000, height_mm=1200, margin_mm=50)
    pattern = build("frame", config.width_mm, config.height_mm, config.margin_mm)
    geometry = prepare_geometry(pattern.lines, config, fit=False, invert_y=False)
    program = geometry_to_gcode(geometry, config, header="Simulatorlauf")

    remote = api.upload(program, "rahmen.gcode")
    api.run_file(remote)
    assert wait_until(lambda: api.status().state == "Idle" and api.status().sd_file is None,
                      timeout_s=15)
    assert board.board.card[remote] == program


def test_simulator_state_is_self_contained():
    """Ohne Server benutzbar — für alles, was keinen Socket braucht."""
    sim = BoardSimulator()
    sim.store("/a.gcode", "G21\nG1 X5 Y5 F1000\n")
    assert sim.start_job("/a.gcode").startswith("[MSG:Running")
    sim.tick(10.0)
    assert sim.state == "Idle"
    assert (sim.x, sim.y) == (5.0, 5.0)


def test_doctor_warns_about_the_motion_block(board):
    """Die Sperre steht ab Werk an — und sieht später aus wie ein kaputtes Board."""
    from wallplotter.config import FluidNCConfig
    from wallplotter.doctor import OK, WARN, check_motion_block
    from wallplotter.upload import FluidNCClient

    api = FluidNCClient(FluidNCConfig(host=board.host, timeout_s=5.0))
    assert check_motion_block(api).status == WARN

    api.send_command("$HTTP/BlockDuringMotion=OFF")
    assert check_motion_block(api).status == OK
    api.close()


# -- Über Stunden ------------------------------------------------------------


def _free_port() -> int:
    import socket as socket_module

    probe = socket_module.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def _serve_on(port: int):
    import threading

    from wallplotter.simulator import SimulatorServer

    server = SimulatorServer(BoardSimulator(), port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_the_channel_comes_back_after_the_board_was_away():
    """Ein Wandbild läuft Stunden — das WLAN nicht unbedingt.

    Der Plot selbst überlebt einen Aussetzer, weil er von der Karte läuft. Die
    Oberfläche muss ihn danach wiederfinden, ohne dass jemand sie neu startet.
    """
    port = _free_port()
    server = _serve_on(port)
    api = FluidNCClient(FluidNCConfig(host=f"127.0.0.1:{port}", timeout_s=2.0))
    try:
        assert api.status().state == "Idle"

        server.shutdown()
        server.server_close()
        with pytest.raises(FluidNCError):
            wait_until(lambda: api.status(), timeout_s=1.0)

        server = _serve_on(port)
        assert wait_until(lambda: api.status().state == "Idle", timeout_s=5.0)
    finally:
        api.close()
        server.shutdown()
        server.server_close()


def test_a_dead_board_says_so_instead_of_reporting_a_stale_state(board):
    """Der gefährlichste Fehler wäre ein Status, der einfach stehen bleibt.

    Wer vor der Wand steht und „Run" liest, während in Wahrheit nichts mehr
    antwortet, wartet auf ein Bild, das nie fertig wird.
    """
    api = FluidNCClient(FluidNCConfig(host=board.host, timeout_s=2.0))
    assert api.status().state == "Idle"
    board.shutdown()
    board.server_close()
    with pytest.raises(FluidNCError):
        wait_until(lambda: api.status(), timeout_s=1.0)
    api.close()


def test_halt_still_works_when_only_http_is_left(board):
    """Der Not-Halt hängt nicht am Kanal — das ist der Sinn des zweiten Wegs."""
    api = FluidNCClient(FluidNCConfig(host=board.host, timeout_s=2.0))
    board.board.bytes_per_s = 300
    api.upload(PROGRAM, "wand.gcode")
    api.run_file("/wand.gcode")
    assert wait_until(lambda: api.status().state == "Run")

    # Kanal abwürgen, HTTP bleibt: genau die Lage, in der man den Halt braucht
    for channel in list(board.channels):
        channel.stop()

    api.pause()
    assert wait_until(lambda: board.board.state == "Hold", timeout_s=3.0)
    api.close()


def test_a_second_plot_does_not_overwrite_a_running_one(api, board):
    """Die Probe aufs Exempel: Der laufende Job bleibt unangetastet."""
    board.board.bytes_per_s = 300
    api.upload(PROGRAM, "wand.gcode")
    api.upload("G21\nG1 X1 Y1 F100\n", "zweit.gcode")
    api.run_file("/wand.gcode")
    assert wait_until(lambda: api.status().state == "Run")

    with pytest.raises(FluidNCError, match="beschäftigt"):
        api.run_file("/zweit.gcode")

    laufend = api.status()
    assert laufend.sd_file == "/wand.gcode"
    assert laufend.state == "Run"


# -- Laser -------------------------------------------------------------------


def _laser_program() -> str:
    from wallplotter.config import PlotConfig
    from wallplotter.gcode import geometry_to_gcode, prepare_geometry
    from wallplotter.patterns import build
    from wallplotter.toolhead import toolhead_by_name

    config = PlotConfig(
        width_mm=1000, height_mm=1200, margin_mm=50, toolhead=toolhead_by_name("laser")
    )
    pattern = build("frame", 1000, 1200, 50)
    geometry = prepare_geometry(pattern.lines, config, fit=False, invert_y=False)
    return geometry_to_gcode(geometry, config, header="Lasertest")


def test_the_laser_program_never_travels_with_the_beam_on():
    """Die Laserinvariante am *abgespielten* Programm, nicht am Text.

    Die Zeilenprüfungen in `test_gcode.py` lesen, was dasteht. Hier wird es
    ausgeführt: modaler Spindelzustand über das ganze Programm hinweg, und bei
    jedem Leerweg die Frage, ob der Strahl gerade Leistung abgibt. Ein `S0`,
    das an der falschen Stelle steht, fällt nur so auf.
    """
    sim = BoardSimulator(laser_mode=True)
    sim.store("/laser.gcode", _laser_program())
    sim.start_job("/laser.gcode")
    sim.tick(1000.0)

    assert sim.state == "Idle"
    assert sim.laser_violations == []
    assert sim.spindle_mode == "M5"
    assert sim.spindle_s == 0.0


def test_a_wrong_laser_program_is_caught():
    """Gegenprobe — sonst prüft die Prüfung nur sich selbst."""
    sim = BoardSimulator(laser_mode=True)
    sim.store("/bad.gcode", "M4 S500\nG1 X10 Y10 F600\nG0 X50 Y50\nG4 P0.5\nM5\n")
    sim.start_job("/bad.gcode")
    sim.tick(1000.0)

    assert len(sim.laser_violations) == 2
    assert "Leerweg" in sim.laser_violations[0]
    assert "Warten" in sim.laser_violations[1]


def test_the_same_lines_are_harmless_with_a_pen():
    """Warum es den Schalter überhaupt gibt.

    Am Stift *hält* ein stehendes `S` den Servo oben — während eines Leerwegs
    ist das nicht nur erlaubt, sondern nötig. Dieselbe Zeile, entgegengesetzte
    Bedeutung.
    """
    sim = BoardSimulator(laser_mode=False)
    sim.store("/stift.gcode", "M3 S30\nG1 X10 Y10 F1500\nG0 X50 Y50\nG4 P0.25\nM5\n")
    sim.start_job("/stift.gcode")
    sim.tick(1000.0)
    assert sim.laser_violations == []


# -- Ohne Rechner starten ---------------------------------------------------


def test_a_macro_homes_and_starts_the_plot():
    """Was ein Taster an der Wand auslöst — ohne Rechner, ohne Handy.

    Der Plot läuft ohnehin autark von der Karte; was ohne Rechner fehlt, ist
    das Starten. Ein Makro ist genau das: ``$H`` referenzieren, dann die Datei
    abspielen. Das ``$H`` ist dabei kein Beiwerk — ohne reproduzierbaren
    Nullpunkt begänne der Plot dort, wo die Gondel gerade zufällig hängt.
    """
    sim = BoardSimulator(macros={0: "$H&$SD/Run=/wand.gcode"})
    sim.store("/wand.gcode", "G21\n" + "\n".join(f"G1 X{i} Y{i} F1000" for i in range(20)))
    sim.x, sim.y = 543.0, 21.0

    assert sim.execute_line("$Macros/Run=0") == ""
    assert sim.state == "Run"
    assert sim.job is not None and sim.job.path == "/wand.gcode"

    sim.tick(1000.0)
    assert sim.state == "Idle"


def test_a_macro_stops_at_the_first_error():
    """Sonst liefe nach einem gescheiterten ``$H`` fröhlich der Plot los."""
    sim = BoardSimulator(macros={0: "$SD/Run=/gibtsnicht.gcode&G1 X10 Y10 F100"})
    answer = sim.execute_line("$Macros/Run=0")
    assert answer.startswith("error:")
    assert "G1 X10 Y10 F100" not in sim.log


def test_an_empty_macro_says_so():
    sim = BoardSimulator()
    assert sim.execute_line("$Macros/Run=2").startswith("error:")
    assert sim.execute_line("$Macros/Run=").startswith("error:")
