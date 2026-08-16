import pytest

from wallplotter.config import FluidNCConfig
from wallplotter.upload import FluidNCClient, FluidNCError, parse_status, upload_and_run


class FakeResponse:
    def __init__(self, text="ok", status_code=200):
        self.text = text
        self.status_code = status_code


class FakeSession:
    """Minimaler Ersatz für requests.Session — zeichnet alle Aufrufe auf."""

    def __init__(self, text="ok", status_code=200):
        self.text = text
        self.status_code = status_code
        self.calls = []
        self.timeouts = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(("get", url, params))
        self.timeouts.append(timeout)
        return FakeResponse(self.text, self.status_code)

    # kompatibel zu requests: download() ruft get() ohne params auf

    def post(self, url, params=None, data=None, files=None, timeout=None):
        self.calls.append(("post", url, {"params": params, "data": data, "files": files}))
        return FakeResponse(self.text, self.status_code)


def client(**kwargs) -> tuple[FluidNCClient, FakeSession]:
    session = FakeSession(**kwargs)
    return FluidNCClient(FluidNCConfig(host="wandplotter.local"), session), session


def test_base_url_adds_scheme():
    assert FluidNCConfig(host="1.2.3.4").base_url == "http://1.2.3.4"
    assert FluidNCConfig(host="http://1.2.3.4/").base_url == "http://1.2.3.4"


def test_send_command_uses_plain_parameter():
    api, session = client()
    api.send_command("?")
    method, url, params = session.calls[0]
    assert method == "get"
    assert url == "http://wandplotter.local/command"
    assert params == {"plain": "?"}


def test_upload_goes_to_the_sd_card_not_the_flash():
    """`/upload` schreibt in ESP3D v3 auf den ESP32-Flash, `/sdfiles` auf die Karte."""
    api, session = client()
    remote = api.upload("G21\n", "wand.gcode")
    assert remote == "/wand.gcode"
    _, url, payload = session.calls[0]
    assert url == "http://wandplotter.local/sdfiles"
    assert payload["params"] == {"path": "/", "createPath": "yes"}
    assert payload["data"]["/wand.gcodeS"] == "4"
    assert "/wand.gcode" in payload["files"]


def test_download_reads_from_the_card():
    api, session = client(text="{}")
    api.download("standorte.json")
    assert session.calls[0][1] == "http://wandplotter.local/sd/standorte.json"


def test_list_files_asks_for_a_listing():
    api, session = client(text="{}")
    api.list_files("/")
    method, url, params = session.calls[0]
    assert url == "http://wandplotter.local/sdfiles"
    assert params == {"path": "/", "action": "list"}


def test_work_offset_is_persistent_unlike_g92():
    api, session = client()
    api.set_work_offset(120.5, 340.0)
    assert session.calls[0][2] == {"plain": "G10 L20 P1 X120.500 Y340.000"}


def test_work_offset_rejects_an_unknown_system():
    api, _ = client()
    with pytest.raises(FluidNCError, match="1..6"):
        api.set_work_offset(0, 0, system=9)


def test_http_error_raises():
    api, _ = client(status_code=500)
    with pytest.raises(FluidNCError):
        api.send_command("?")


def test_run_file_sends_sd_run():
    api, session = client()
    api.run_file("/wand.gcode")
    assert session.calls[0][2] == {"plain": "$SD/Run=/wand.gcode"}


def test_upload_and_run_does_both():
    api, session = client()
    upload_and_run("G21\n", "wand.gcode", client=api)
    assert [call[0] for call in session.calls] == ["post", "get"]


def test_upload_and_run_without_run():
    api, session = client()
    upload_and_run("G21\n", "wand.gcode", client=api, run=False)
    assert [call[0] for call in session.calls] == ["post"]


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


def test_status_via_client():
    api, _ = client(text="<Idle|MPos:1.000,2.000,0.000>")
    assert api.status().position == (1.0, 2.0, 0.0)


def test_jog_is_relative_and_metric():
    api, session = client()
    api.jog(dx=-100, dy=25, feed=800)
    assert session.calls[0][2] == {"plain": "$J=G91 G21 X-100.000 Y25.000 F800"}


def test_jog_to_is_absolute():
    api, session = client()
    api.jog_to(1200, 300)
    assert session.calls[0][2]["plain"].startswith("$J=G90 G21 X1200.000 Y300.000")


def test_jog_without_movement_is_rejected():
    api, _ = client()
    with pytest.raises(FluidNCError, match="ohne Weg"):
        api.jog()


def test_jog_without_feed_is_rejected():
    api, _ = client()
    with pytest.raises(FluidNCError, match="Vorschub"):
        api.jog(dx=10, feed=0)


@pytest.mark.parametrize(
    ("action", "byte"),
    [("pause", 0x21), ("resume", 0x7E), ("stop", 0x18), ("jog_cancel", 0x85)],
)
def test_realtime_bytes_go_out_as_percent_escapes(action, byte):
    """Als Escape in der URL, nicht als Zeichen im params-Dict."""
    api, session = client()
    getattr(api, action)()
    method, url, params = session.calls[0]
    assert params is None
    assert url.endswith(f"/command?plain=%{byte:02X}")


def test_realtime_bytes_survive_the_requests_encoder():
    """Der eigentliche Fehler lag nicht im Client, sondern eine Schicht tiefer.

    Über ``params={"plain": "\x85"}`` macht requests aus dem Byte 0x85 die
    UTF-8-Folge 0xC2 0x85 — das Board sieht dann nie das Byte, auf das es
    wartet, und der Jog-Abbruch wirkt nicht. Ein Test gegen das params-Dict
    hätte das nie gemerkt, deshalb geht dieser durch requests hindurch.
    """
    requests = pytest.importorskip("requests")

    api, session = client()
    api.jog_cancel()
    prepared = requests.Request("GET", session.calls[0][1]).prepare()
    assert prepared.url.endswith("plain=%85")

    naiv = requests.Request("GET", "http://x/command", params={"plain": "\x85"}).prepare()
    assert naiv.url.endswith("plain=%C2%85")   # so sah es vorher aus


def test_realtime_uses_a_short_timeout():
    """Ein Not-Halt darf nicht 30 Sekunden auf ein stummes Board warten."""
    session = FakeSession()
    api = FluidNCClient(FluidNCConfig(host="x", timeout_s=30.0), session)
    api.stop()
    assert session.timeouts[0] <= 5.0


def test_realtime_rejects_impossible_bytes():
    api, _ = client()
    with pytest.raises(FluidNCError, match="zwischen 0 und 255"):
        api.send_realtime(256)


def test_set_zero_uses_g92():
    api, session = client()
    api.set_zero()
    assert session.calls[0][2] == {"plain": "G92 X0.000 Y0.000"}


def test_position_returns_xy():
    api, _ = client(text="<Idle|MPos:120.500,340.000,0.000>")
    assert api.position() == (120.5, 340.0)


def test_position_without_data_raises():
    api, _ = client(text="<Idle|FS:0,0>")
    with pytest.raises(FluidNCError, match="ohne Position"):
        api.position()


def test_set_zero_can_restore_a_known_point():
    api, session = client()
    api.set_zero(120.5, 340.0)
    assert session.calls[0][2] == {"plain": "G92 X120.500 Y340.000"}


def test_network_failures_become_fluidnc_errors():
    class Dead:
        def get(self, *a, **k):
            raise OSError("Netzwerk weg")

        def post(self, *a, **k):
            raise OSError("Netzwerk weg")

    api = FluidNCClient(FluidNCConfig(host="x"), Dead())
    with pytest.raises(FluidNCError, match="Netzwerk weg"):
        api.status()
    with pytest.raises(FluidNCError, match="Netzwerk weg"):
        api.upload("G21", "x.gcode")
