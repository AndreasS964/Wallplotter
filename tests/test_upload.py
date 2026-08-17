"""Was der Client wirklich auf die Leitung legt.

Die Erwartungen hier sind gegen den Quelltext der Firmware geschrieben
(FluidNC v3.9.9, v4.0.4 und master), nicht gegen die ESP3D-Dokumentation. Wo
beides auseinandergeht, gewinnt die Firmware — sie ist es, die am Ende
antwortet.
"""

import pytest

from conftest import FakeChannel, FakeSession
from wallplotter.config import FluidNCConfig
from wallplotter.upload import FluidNCClient, FluidNCError, parse_status, upload_and_run


def client(channel=None, **kwargs) -> tuple[FluidNCClient, FakeSession, FakeChannel]:
    session = FakeSession(**kwargs)
    board = channel if channel is not None else FakeChannel()
    return FluidNCClient(FluidNCConfig(host="wandplotter.local"), session, board), session, board


def test_base_url_adds_scheme():
    assert FluidNCConfig(host="1.2.3.4").base_url == "http://1.2.3.4"
    assert FluidNCConfig(host="http://1.2.3.4/").base_url == "http://1.2.3.4"


# -- Dateien: HTTP ----------------------------------------------------------


def test_upload_goes_to_the_sd_card_not_the_flash():
    """In FluidNC ist ``/upload`` die Karte und ``/files`` der Flash.

    Andersherum steht es in der ESP3D-Dokumentation — die beschreibt aber die
    eigenständige ESP3D-Firmware. Der in FluidNC eingebaute Webserver kennt
    ``/sdfiles`` überhaupt nicht; dieser Pfad landet im 404-Handler.
    """
    api, session, _ = client()
    remote = api.upload("G21\n", "wand.gcode")
    assert remote == "/wand.gcode"
    method, url, payload = session.calls[0]
    assert method == "post"
    assert url == "http://wandplotter.local/upload"
    assert payload["params"] == {"path": "/"}
    assert payload["data"]["/wand.gcodeS"] == "4"
    assert "/wand.gcode" in payload["files"]


def test_list_files_asks_the_sd_endpoint_without_an_action():
    """``action`` ist in der Firmware für Löschen/Anlegen/Umbenennen reserviert.

    Gelistet wird immer — es sei denn, ``dontlist=yes`` steht daneben. Ein
    ``action=list`` würde die Firmware als unvollständigen Löschauftrag lesen
    (sie verlangt dazu ``filename``) und still ignorieren.
    """
    api, session, _ = client(text="{}")
    api.list_files("/")
    method, url, params = session.calls[0]
    assert method == "get"
    assert url == "http://wandplotter.local/upload"
    assert params == {"path": "/"}


def test_download_uses_sd_show_not_a_url():
    """``$SD/Show`` gibt es in jeder Fassung, den WebDAV-Mount ``/sd`` erst ab 4.x."""
    api, _, board = client()
    board.lines = ["{}"]
    assert api.download("standorte.json") == "{}"
    assert board.sent == ["$SD/Show=/standorte.json"]


def test_download_reports_a_refusal_instead_of_returning_it():
    api, _, board = client()
    board.lines = ["error:60"]
    with pytest.raises(FluidNCError, match="error:60"):
        api.download("standorte.json")


def test_http_error_raises():
    api, _, _ = client(status_code=500)
    with pytest.raises(FluidNCError):
        api.list_files("/")


def test_blocked_during_motion_says_what_to_do():
    """HTTP 503 heißt hier nie „Server kaputt", sondern immer dasselbe."""
    api, _, _ = client(status_code=503)
    with pytest.raises(FluidNCError, match="BlockDuringMotion"):
        api.list_files("/")


# -- Maschine: Kanal --------------------------------------------------------


def test_gcode_goes_through_the_channel():
    """G-Code über ``/command?plain=`` käme nie beim Parser an.

    Der Handler ruft ``settings_execute_line()``, und die Funktion schneidet
    das erste Zeichen ab: Aus ``G92 X0 Y0`` würde die Suche nach einer
    Einstellung namens ``92 X0 Y0``.
    """
    api, session, board = client()
    api.set_zero()
    assert board.sent == ["G92 X0.000 Y0.000"]
    assert session.calls == []


def test_set_zero_can_restore_a_known_point():
    api, _, board = client()
    api.set_zero(120.5, 340.0)
    assert board.sent == ["G92 X120.500 Y340.000"]


def test_work_offset_is_persistent_unlike_g92():
    api, _, board = client()
    api.set_work_offset(120.5, 340.0)
    assert board.sent == ["G10 L20 P1 X120.500 Y340.000"]


def test_work_offset_rejects_an_unknown_system():
    api, _, _ = client()
    with pytest.raises(FluidNCError, match="1..6"):
        api.set_work_offset(0, 0, system=9)


def test_run_file_sends_sd_run_over_the_channel():
    """Die Firmware hängt den Job an den Kanal, der ihn gestartet hat.

    Über HTTP wäre das ein Wegwerf-Kanal, der mit der Antwort stirbt.
    """
    api, session, board = client()
    api.run_file("/wand.gcode")
    assert board.sent == ["$SD/Run=/wand.gcode"]
    assert session.calls == []


def test_status_comes_from_the_channel():
    board = FakeChannel(status="<Idle|MPos:1.000,2.000,0.000>")
    api, session, _ = client(channel=board)
    assert api.status().position == (1.0, 2.0, 0.0)
    assert session.calls == []


def test_position_returns_xy():
    api, _, _ = client(channel=FakeChannel(status="<Idle|MPos:120.500,340.000,0.000>"))
    assert api.position() == (120.5, 340.0)


def test_position_without_data_raises():
    api, _, _ = client(channel=FakeChannel(status="<Idle|FS:0,0>"))
    with pytest.raises(FluidNCError, match="ohne Position"):
        api.position()


def test_jog_is_relative_and_metric():
    api, _, board = client()
    api.jog(dx=-100, dy=25, feed=800)
    assert board.sent == ["$J=G91 G21 X-100.000 Y25.000 F800"]


def test_jog_to_is_absolute():
    api, _, board = client()
    api.jog_to(1200, 300)
    assert board.sent[0].startswith("$J=G90 G21 X1200.000 Y300.000")


def test_jog_without_movement_is_rejected():
    api, _, _ = client()
    with pytest.raises(FluidNCError, match="ohne Weg"):
        api.jog()


def test_jog_without_feed_is_rejected():
    api, _, _ = client()
    with pytest.raises(FluidNCError, match="Vorschub"):
        api.jog(dx=10, feed=0)


def test_jog_cancel_goes_through_the_channel():
    """Über HTTP gibt es keinen Weg zum Realtime-Zweig der Firmware.

    ``/command?plain=%85`` landet in der Kommandotabelle, nicht bei
    ``execute_realtime_command`` — die Realtime-Zeichen fängt die Firmware im
    Zeichenstrom eines Kanals ab, und den gibt es nur über WebSocket oder
    seriell. Für den Jog-Abbruch gibt es auch keinen eigenen Endpunkt.
    """
    api, session, board = client()
    api.jog_cancel()
    assert board.realtime == [0x85]
    assert session.calls == []


@pytest.mark.parametrize(
    ("action", "byte", "path"),
    [
        ("pause", 0x21, "/feedhold_reload"),
        ("resume", 0x7E, "/cyclestart_reload"),
        ("stop", 0x18, "/restart_reload"),
    ],
)
def test_halt_and_resume_take_both_ways_at_once(action, byte, path):
    """Halt, Weiter und Stopp gehen über beide Wege — einer reicht nicht.

    Der Kanal ist schneller, quittiert aber nichts: Ist die Gegenstelle weg,
    ohne dass es hier schon aufgefallen wäre, gelingt das erste ``send``
    trotzdem, weil die Bytes im Puffer des Betriebssystems landen. Ein
    Not-Halt wäre damit still verschluckt. Der HTTP-Endpunkt antwortet dagegen
    mit einem Statuscode — und die Bewegungssperre fasst ihn nicht an.
    """
    api, session, board = client()
    getattr(api, action)()
    assert board.realtime == [byte]
    method, url, _ = session.calls[0]
    assert method == "get"
    assert url == f"http://wandplotter.local{path}"


@pytest.mark.parametrize(
    ("action", "path"),
    [
        ("pause", "/feedhold_reload"),
        ("resume", "/cyclestart_reload"),
        ("stop", "/restart_reload"),
    ],
)
def test_halt_and_resume_still_work_without_the_channel(action, path):
    api, session, _ = client(channel=FakeChannel(dead=True))
    getattr(api, action)()
    method, url, _ = session.calls[0]
    assert method == "get"
    assert url == f"http://wandplotter.local{path}"


@pytest.mark.parametrize("action", ["pause", "resume", "stop"])
def test_halt_fails_only_when_neither_way_gets_through(action):
    class Dead:
        def get(self, *a, **k):
            raise OSError("Netzwerk weg")

    api = FluidNCClient(FluidNCConfig(host="x"), Dead(), FakeChannel(dead=True))
    with pytest.raises(FluidNCError, match="Kanal.*HTTP|HTTP.*Kanal"):
        getattr(api, action)()


def test_the_second_way_does_not_wait_as_long_as_an_upload():
    """Ein Not-Halt darf nicht 30 Sekunden auf ein stummes Board warten."""
    session = FakeSession()
    api = FluidNCClient(FluidNCConfig(host="x", timeout_s=30.0), session, FakeChannel(dead=True))
    api.stop()
    assert session.timeouts[0] <= 5.0


def test_realtime_rejects_impossible_bytes():
    api, _, _ = client()
    with pytest.raises(FluidNCError, match="zwischen 0 und 255"):
        api.send_realtime(256)


def test_dollar_commands_fall_back_to_http_without_a_channel():
    """``$``-Kommandos sind das Einzige, was der HTTP-Weg richtig überträgt."""
    api, session, _ = client(channel=FakeChannel(dead=True))
    api.send_command("$#")
    method, url, params = session.calls[0]
    assert method == "get"
    assert url == "http://wandplotter.local/command"
    assert params == {"plain": "$#"}


def test_gcode_without_a_channel_says_why():
    api, _, _ = client(channel=FakeChannel(dead=True))
    with pytest.raises(FluidNCError, match="WebSocket-Kanal"):
        api.set_zero()


# -- Statuszeile ------------------------------------------------------------


def test_parse_status_full():
    status = parse_status("<Run|MPos:12.000,3.500,0.000|FS:1500,0|SD:42.30,/wand.gcode>")
    assert status.state == "Run"
    assert status.position == (12.0, 3.5, 0.0)
    assert status.sd_percent == 42.3
    assert status.sd_file == "/wand.gcode"
    assert status.is_running


def test_parse_status_idle_without_sd():
    status = parse_status("<Idle|MPos:0.000,0.000,0.000|FS:0,0>")
    assert status.state == "Idle"
    assert status.sd_percent is None
    assert not status.is_running


def test_parse_status_rejects_garbage():
    with pytest.raises(FluidNCError):
        parse_status("kein status")


# -- Zusammenspiel ----------------------------------------------------------


def test_upload_and_run_does_both():
    api, session, board = client()
    upload_and_run("G21\n", "wand.gcode", client=api)
    assert [call[0] for call in session.calls] == ["post"]
    assert board.sent == ["$SD/Run=/wand.gcode"]


def test_upload_and_run_without_run():
    api, session, board = client()
    upload_and_run("G21\n", "wand.gcode", client=api, run=False)
    assert [call[0] for call in session.calls] == ["post"]
    assert board.sent == []


def test_network_failures_become_fluidnc_errors():
    class Dead:
        def get(self, *a, **k):
            raise OSError("Netzwerk weg")

        def post(self, *a, **k):
            raise OSError("Netzwerk weg")

    api = FluidNCClient(FluidNCConfig(host="x"), Dead(), FakeChannel(dead=True))
    with pytest.raises(FluidNCError, match="kein Kanal"):
        api.status()
    with pytest.raises(FluidNCError, match="Netzwerk weg"):
        api.upload("G21", "x.gcode")


# -- Zwei Plots übereinander ------------------------------------------------


def test_starting_a_plot_over_a_running_one_is_refused():
    """FluidNC schachtelt das neue Programm in das laufende (``Job::nest``).

    Die Firmware wehrt sich dagegen nicht. Auf einer Wand heißt das: zwei
    Zeichnungen übereinander und Stunden verloren.
    """
    board = FakeChannel(status="<Run|MPos:0.000,0.000,0.000|SD:42.30,/wand.gcode>")
    api, _, _ = client(channel=board)
    with pytest.raises(FluidNCError, match="beschäftigt"):
        api.run_file("/neu.gcode")
    assert board.sent == []


def test_the_refusal_names_what_is_running():
    board = FakeChannel(status="<Run|MPos:0.000,0.000,0.000|SD:42.30,/wand.gcode>")
    api, _, _ = client(channel=board)
    with pytest.raises(FluidNCError, match=r"/wand\.gcode, 42 %"):
        api.run_file("/neu.gcode")


def test_a_hold_counts_as_busy_too():
    """Hold heißt: Der Plot läuft noch, er wartet nur — etwa auf den Stiftwechsel."""
    board = FakeChannel(status="<Hold:0|MPos:0.000,0.000,0.000|SD:42.30,/wand.gcode>")
    api, _, _ = client(channel=board)
    with pytest.raises(FluidNCError, match="beschäftigt"):
        api.run_file("/neu.gcode")


def test_force_starts_anyway():
    board = FakeChannel(status="<Run|MPos:0.000,0.000,0.000|SD:42.30,/wand.gcode>")
    api, _, _ = client(channel=board)
    api.run_file("/neu.gcode", force=True)
    assert board.sent == ["$SD/Run=/neu.gcode"]


def test_an_unreadable_status_does_not_block_the_start():
    """Nicht raten: Der Start läuft ohnehin über denselben Kanal.

    Ein Plot, der wegen eines Aussetzers in der Statusabfrage nicht anläuft,
    wäre die schlechtere Antwort — scheitert der Kanal wirklich, scheitert
    auch das Startkommando, und zwar mit der Meldung, die dazu passt.
    """
    board = FakeChannel(status="unlesbar")
    api, _, _ = client(channel=board)
    api.run_file("/neu.gcode")
    assert board.sent == ["$SD/Run=/neu.gcode"]


def test_uploading_during_a_plot_is_still_allowed():
    """Eine zusätzliche Datei auf der Karte stört keinen laufenden Plot."""
    board = FakeChannel(status="<Run|MPos:0.000,0.000,0.000|SD:42.30,/wand.gcode>")
    api, session, _ = client(channel=board)
    upload_and_run("G21\n", "neu.gcode", client=api, run=False)
    assert session.calls[0][0] == "post"
