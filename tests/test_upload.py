"""Anbindung ans Board — gegen Gegenstellen, die auch Nein sagen können.

Die Gegenstellen stehen in ``tests/fluidnc_fake.py`` und bilden nach, was
FluidNC wirklich tut: unbekannte Endpunkte sind 404, ``/command?plain=``
versteht nur ``$``-Kommandos, und Realtime-Zeichen wirken ausschließlich auf
dem TCP-Kanal. Deshalb schlagen diese Tests an, wenn jemand wieder auf den
HTTP-Weg zurückfällt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fluidnc_fake import (  # noqa: E402
    FakeFluidNCSocket,
    FakeResponse,
    FakeSession,
    opener_for,
)
from wallplotter.config import FluidNCConfig  # noqa: E402
from wallplotter.upload import (  # noqa: E402
    FluidNCClient,
    FluidNCError,
    TelnetChannel,
    parse_status,
    upload_and_run,
)


def client(**kwargs) -> tuple[FluidNCClient, FakeSession, FakeFluidNCSocket]:
    session = FakeSession(**kwargs)
    sock = FakeFluidNCSocket()
    config = FluidNCConfig(host="wandplotter.local")
    channel = TelnetChannel(config.hostname, config.telnet_port, 5.0, opener_for(sock))
    return FluidNCClient(config, session, channel), session, sock


# ---------------------------------------------------------------------------
# Adressierung
# ---------------------------------------------------------------------------


def test_base_url_adds_scheme():
    assert FluidNCConfig(host="1.2.3.4").base_url == "http://1.2.3.4"
    assert FluidNCConfig(host="http://1.2.3.4/").base_url == "http://1.2.3.4"


def test_hostname_strips_scheme_and_port():
    assert FluidNCConfig(host="http://1.2.3.4:8080/").hostname == "1.2.3.4"
    assert FluidNCConfig(host="wandplotter.local").hostname == "wandplotter.local"
    assert FluidNCConfig(host="[fe80::1]:80").hostname == "[fe80::1]"


# ---------------------------------------------------------------------------
# Dateien — die Endpunkte, die es wirklich gibt
# ---------------------------------------------------------------------------


def test_upload_goes_to_the_endpoint_that_exists():
    """`/upload` ist die SD-Karte, `/files` der Flash — `/sdfiles` gibt es nicht.

    Die Gegenstelle antwortet auf jeden unbekannten Pfad mit 404; ein Rückfall
    auf `/sdfiles` lässt diesen Test also scheitern statt grün bleiben.
    """
    api, session, _ = client()
    remote = api.upload("G21\n", "wand.gcode")
    assert remote == "/wand.gcode"
    method, url, payload = session.calls[0]
    assert method == "post"
    assert url == "http://wandplotter.local/upload"
    assert payload["params"] == {"path": "/"}
    assert "createPath" not in (payload["params"] or {})
    # Die Datei ist wirklich auf der simulierten Karte gelandet
    assert session.card["/wand.gcode"] == "G21\n"


def test_upload_names_the_full_path_so_the_size_check_matches():
    """Der Multipart-Dateiname trägt den Zielpfad, das Größenfeld denselben.

    Steht im Dateinamen nur `wand.gcode`, legt FluidNC die Datei im
    Wurzelverzeichnis ab und findet das Feld `/gcode/wand.gcodeS` nicht mehr —
    die Platzprüfung fällt dann still aus.
    """
    session = FakeSession()
    sock = FakeFluidNCSocket()
    config = FluidNCConfig(host="x", remote_dir="/gcode")
    api = FluidNCClient(
        config, session, TelnetChannel(config.hostname, 23, 5.0, opener_for(sock))
    )
    api.upload("G21\n", "wand.gcode")
    payload = session.calls[0][2]
    filename = next(iter(payload["files"].values()))[0]
    assert filename == "/gcode/wand.gcode"
    assert payload["data"]["/gcode/wand.gcodeS"] == "4"


def test_upload_to_an_unknown_endpoint_would_fail():
    """Beweis, dass die Gegenstelle nicht alles durchwinkt."""
    session = FakeSession()
    assert session.post("http://x/sdfiles", files={}).status_code == 404


def test_download_reads_from_the_card():
    api, session, _ = client(files={"/standorte.json": "{}"})
    assert api.download("standorte.json") == "{}"
    assert session.calls[0][1] == "http://wandplotter.local/sd/standorte.json"


def test_download_uses_the_configured_remote_dir():
    """Wie beim Upload: ein Dateiname ohne führenden Schrägstrich liegt unter
    remote_dir. Vorher las download() immer ab Kartenwurzel — push (schreibt
    unter remote_dir) und ein folgendes pull trafen sich bei einem remote_dir
    ungleich "/" nie an derselben Stelle."""
    session = FakeSession(files={"/gcode/standorte.json": "{}"})
    sock = FakeFluidNCSocket()
    config = FluidNCConfig(host="x", remote_dir="/gcode")
    api = FluidNCClient(
        config, session, TelnetChannel(config.hostname, 23, 5.0, opener_for(sock))
    )
    assert api.download("standorte.json") == "{}"
    assert session.calls[0][1] == "http://x/sd/gcode/standorte.json"


def test_download_with_a_leading_slash_bypasses_remote_dir():
    session = FakeSession(files={"/anderswo.json": "{}"})
    sock = FakeFluidNCSocket()
    config = FluidNCConfig(host="x", remote_dir="/gcode")
    api = FluidNCClient(
        config, session, TelnetChannel(config.hostname, 23, 5.0, opener_for(sock))
    )
    assert api.download("/anderswo.json") == "{}"
    assert session.calls[0][1] == "http://x/sd/anderswo.json"


def test_list_files_needs_no_action_parameter():
    api, session, _ = client(files={"/wand.gcode": ""})
    listing = api.list_files("/")
    method, url, params = session.calls[0]
    assert url == "http://wandplotter.local/upload"
    assert params == {"path": "/"}
    assert "wand.gcode" in listing


# ---------------------------------------------------------------------------
# Kommandos — über den Kanal, nicht über plain=
# ---------------------------------------------------------------------------


def test_gcode_goes_over_the_channel_not_over_http():
    api, session, sock = client()
    api.set_zero()
    assert sock.lines == ["G92 X0.000 Y0.000"]
    assert session.calls == []


def test_work_offset_is_persistent_unlike_g92():
    api, _, sock = client()
    api.set_work_offset(120.5, 340.0)
    assert sock.lines == ["G10 L20 P1 X120.500 Y340.000"]


def test_work_offset_rejects_an_unknown_system():
    api, _, _ = client()
    with pytest.raises(FluidNCError, match="1..6"):
        api.set_work_offset(0, 0, system=9)


def test_set_zero_can_restore_a_known_point():
    api, _, sock = client()
    api.set_zero(120.5, 340.0)
    assert sock.lines == ["G92 X120.500 Y340.000"]


def test_run_file_sends_sd_run():
    api, _, sock = client()
    api.run_file("/wand.gcode")
    assert sock.lines == ["$SD/Run=/wand.gcode"]


def test_a_rejected_command_raises_instead_of_reporting_success():
    session = FakeSession()
    sock = FakeFluidNCSocket(errors={"G92": "error:2"})
    api = FluidNCClient(
        FluidNCConfig(host="x"), session, TelnetChannel("x", 23, 5.0, opener_for(sock))
    )
    with pytest.raises(FluidNCError, match="error:2"):
        api.set_zero()


def test_settings_fall_back_to_http_when_the_channel_is_dead():
    """Telnet abgeschaltet: `$`-Kommandos gehen weiterhin über HTTP."""

    def dead_opener(address, timeout=None):
        raise OSError("Verbindung abgelehnt")

    session = FakeSession()
    api = FluidNCClient(
        FluidNCConfig(host="x"), session, TelnetChannel("x", 23, 5.0, dead_opener)
    )
    api.run_file("/wand.gcode")
    assert session.calls[0][2] == {"plain": "$SD/Run=/wand.gcode"}


def test_gcode_does_not_fall_back_to_http():
    """Für GCode gibt es keinen HTTP-Weg — lieber laut scheitern.

    Über `plain=` käme `G92 …` als Kommando mit dem Namen „92 X0.000 Y0.000"
    an, träfe nichts und würde je nach Zustand mit 500 oder mit einer
    fröhlichen Hilfezeile quittiert. Beides ist kein gesetzter Nullpunkt.
    """

    def dead_opener(address, timeout=None):
        raise OSError("Verbindung abgelehnt")

    session = FakeSession()
    api = FluidNCClient(
        FluidNCConfig(host="x"), session, TelnetChannel("x", 23, 5.0, dead_opener)
    )
    with pytest.raises(FluidNCError):
        api.set_zero()
    assert session.calls == []


def test_jog_is_relative_and_metric():
    api, _, sock = client()
    api.jog(dx=-100, dy=25, feed=800)
    assert sock.lines == ["$J=G91 G21 X-100.000 Y25.000 F800"]


def test_jog_to_is_absolute():
    api, _, sock = client()
    api.jog_to(1200, 300)
    assert sock.lines[0].startswith("$J=G90 G21 X1200.000 Y300.000")


def test_jog_without_movement_is_rejected():
    api, _, _ = client()
    with pytest.raises(FluidNCError, match="ohne Weg"):
        api.jog()


def test_jog_without_feed_is_rejected():
    api, _, _ = client()
    with pytest.raises(FluidNCError, match="Vorschub"):
        api.jog(dx=10, feed=0)


# ---------------------------------------------------------------------------
# Halt, Pause, Weiter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "endpoint"),
    [
        ("pause", "/feedhold_reload"),
        ("resume", "/cyclestart_reload"),
        ("stop", "/restart_reload"),
    ],
)
def test_hold_and_resume_use_the_endpoints_that_trigger_the_event(action, endpoint):
    """Diese drei Endpunkte rufen direkt `protocol_send_event(...)` auf.

    Sie brauchen keinen Kanal und werden auch nicht von
    `$HTTP/BlockDuringMotion` blockiert — also genau das, was ein Halt können
    muss. Der frühere Weg (`/command?plain=%18`) landete beim namenlosen
    Hilfe-Kommando und meldete HTTP 200, ohne etwas zu tun.
    """
    api, session, _ = client()
    getattr(api, action)()
    method, url, params = session.calls[0]
    assert method == "get"
    assert url.endswith(endpoint)
    assert params is None


def test_a_redirect_is_success_not_failure():
    """Die Ereignis-Endpunkte antworten mit 302 auf `/`."""
    api, session, _ = client()
    api.pause()
    assert session._dispatch("/feedhold_reload", None).status_code == 302


def test_hold_uses_a_short_timeout():
    """Ein Not-Halt darf nicht 30 Sekunden auf ein stummes Board warten."""
    session = FakeSession()
    api = FluidNCClient(FluidNCConfig(host="x", timeout_s=30.0), session, object())
    api.stop()
    assert session.timeouts[0] <= 5.0


def test_the_old_http_path_would_have_been_silently_useless():
    """Warum der Weg über `plain=` weg musste — nachgestellt an der Gegenstelle.

    Ein Realtime-Byte wird von `settings_execute_line()` um sein erstes
    Zeichen gekürzt, trifft das Kommando mit leerem Namen und bekommt die
    Hilfezeile. Antwort: HTTP 200. Ein Client, der nur auf den Statuscode
    sieht, meldet „Stopp ausgeführt".
    """
    session = FakeSession()
    answer = session.get("http://x/command", params={"plain": "\x18"})
    assert answer.status_code == 200
    assert "HLP:" in answer.text


def test_hold_reports_a_blocked_board_instead_of_swallowing_it():
    api, session, _ = client()
    session.blocked = True
    # Die Ereignis-Endpunkte sind von der Sperre nicht betroffen
    api.pause()


def test_jog_cancel_goes_out_as_a_realtime_byte():
    api, _, sock = client()
    api.jog_cancel()
    assert sock.realtime == [0x85]
    assert sock.lines == []


def test_realtime_rejects_impossible_bytes():
    api, _, _ = client()
    with pytest.raises(FluidNCError, match="zwischen 0 und 255"):
        api.send_realtime(256)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_comes_from_the_channel():
    api, session, sock = client()
    status = api.status()
    assert status.state == "Idle"
    assert sock.realtime == [ord("?")]
    assert session.calls == []


def test_status_via_client():
    session = FakeSession()
    sock = FakeFluidNCSocket(status="<Idle|MPos:1.000,2.000,0.000>")
    api = FluidNCClient(
        FluidNCConfig(host="x"), session, TelnetChannel("x", 23, 5.0, opener_for(sock))
    )
    assert api.status().position == (1.0, 2.0, 0.0)


def test_parse_status_full():
    status = parse_status("<Run|MPos:12.000,3.500,0.000|FS:1500,0|SD:42.30,/wand.gcode>")
    assert status.state == "Run"
    assert status.position == (12.0, 3.5, 0.0)
    assert status.sd_percent == 42.3
    assert status.sd_file == "/wand.gcode"
    assert status.is_running
    assert not status.sd_done


def test_parse_status_idle_without_sd():
    status = parse_status("<Idle|MPos:0.000,0.000,0.000|FS:0,0>")
    assert status.state == "Idle"
    assert status.sd_percent is None
    assert not status.is_running


def test_parse_status_converts_machine_to_work_coordinates():
    """Der erzeugte GCode fährt in Werkstückkoordinaten, also muss die
    aufgenommene Position auch eine sein.

    Mit gesetztem G92/G54 liegen MPos und WPos auseinander; wer das eine
    aufnimmt und das andere anfährt, verschiebt die ganze Zeichnung um genau
    den Versatz. `WCO` steht in derselben Zeile — es gibt keinen Grund, das zu
    raten.
    """
    status = parse_status("<Idle|MPos:120.000,340.000,0.000|WCO:20.000,40.000,0.000>")
    assert status.machine_position == (120.0, 340.0, 0.0)
    assert status.position == (100.0, 300.0, 0.0)
    assert status.work_offset == (20.0, 40.0, 0.0)


def test_parse_status_takes_wpos_as_it_is():
    status = parse_status("<Idle|WPos:100.000,300.000,0.000|WCO:20.000,40.000,0.000>")
    assert status.position == (100.0, 300.0, 0.0)
    assert status.machine_position == (120.0, 340.0, 0.0)


def test_parse_status_understands_the_end_marker():
    """`SD: wand.gcode: Sent` heißt fertig, nicht „kein Fortschritt"."""
    status = parse_status("<Idle|MPos:0.000,0.000,0.000|SD: wand.gcode: Sent>")
    assert status.sd_done
    assert status.sd_percent == 100.0
    assert status.sd_file == "wand.gcode"


def test_parse_status_rejects_garbage():
    with pytest.raises(FluidNCError):
        parse_status("kein status")


def test_position_returns_xy():
    session = FakeSession()
    sock = FakeFluidNCSocket(status="<Idle|MPos:120.500,340.000,0.000>")
    api = FluidNCClient(
        FluidNCConfig(host="x"), session, TelnetChannel("x", 23, 5.0, opener_for(sock))
    )
    assert api.position() == (120.5, 340.0)


def test_position_without_data_raises():
    session = FakeSession()
    sock = FakeFluidNCSocket(status="<Idle|FS:0,0>")
    api = FluidNCClient(
        FluidNCConfig(host="x"), session, TelnetChannel("x", 23, 5.0, opener_for(sock))
    )
    with pytest.raises(FluidNCError, match="ohne Position"):
        api.position()


# ---------------------------------------------------------------------------
# Der Kanal selbst
# ---------------------------------------------------------------------------


def test_channel_waits_for_the_acknowledgement():
    sock = FakeFluidNCSocket()
    channel = TelnetChannel("x", 23, 5.0, opener_for(sock))
    assert channel.send_line("G21") == ""
    assert sock.lines == ["G21"]


def test_channel_collects_output_before_the_ok():
    sock = FakeFluidNCSocket(errors={"$#": "[G54:0.000,0.000,0.000]\nok"})
    channel = TelnetChannel("x", 23, 5.0, opener_for(sock))
    assert "G54" in channel.send_line("$#")


def test_channel_times_out_instead_of_hanging():
    class Silent(FakeFluidNCSocket):
        def _answer(self, line: str) -> None:
            pass  # Board antwortet nicht

    channel = TelnetChannel("x", 23, 0.01, opener_for(Silent()))
    with pytest.raises(FluidNCError, match="Keine Quittung"):
        channel.send_line("G21")


def test_channel_reports_an_unreachable_board():
    def dead(address, timeout=None):
        raise OSError("Verbindung abgelehnt")

    with pytest.raises(FluidNCError, match="Kein TCP-Kanal"):
        TelnetChannel("x", 23, 1.0, dead).send_line("G21")


def test_channel_closes_cleanly():
    sock = FakeFluidNCSocket()
    with TelnetChannel("x", 23, 5.0, opener_for(sock)):
        pass
    assert sock.closed


# ---------------------------------------------------------------------------
# Zusammenspiel
# ---------------------------------------------------------------------------


def test_upload_and_run_does_both():
    api, session, sock = client()
    upload_and_run("G21\n", "wand.gcode", client=api)
    assert [call[0] for call in session.calls] == ["post"]
    assert sock.lines == ["$SD/Run=/wand.gcode"]


def test_upload_and_run_without_run():
    api, session, sock = client()
    upload_and_run("G21\n", "wand.gcode", client=api, run=False)
    assert [call[0] for call in session.calls] == ["post"]
    assert sock.lines == []


def test_http_error_raises():
    class Broken(FakeSession):
        def post(self, *a, **k):
            return FakeResponse("kaputt", 500)

    api = FluidNCClient(FluidNCConfig(host="x"), Broken(), object())
    with pytest.raises(FluidNCError, match="HTTP 500"):
        api.upload("G21", "x.gcode")


def test_network_failures_become_fluidnc_errors():
    class Dead:
        def get(self, *a, **k):
            raise OSError("Netzwerk weg")

        def post(self, *a, **k):
            raise OSError("Netzwerk weg")

    def dead_opener(address, timeout=None):
        raise OSError("Netzwerk weg")

    api = FluidNCClient(
        FluidNCConfig(host="x"), Dead(), TelnetChannel("x", 23, 1.0, dead_opener)
    )
    with pytest.raises(FluidNCError, match="Netzwerk weg"):
        api.status()
    with pytest.raises(FluidNCError, match="Netzwerk weg"):
        api.upload("G21", "x.gcode")
