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

    def get(self, url, params=None, timeout=None):
        self.calls.append(("get", url, params))
        return FakeResponse(self.text, self.status_code)

    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append(("post", url, {"data": data, "files": files}))
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


def test_upload_posts_multipart_and_returns_remote_path():
    api, session = client()
    remote = api.upload("G21\n", "wand.gcode")
    assert remote == "/wand.gcode"
    _, url, payload = session.calls[0]
    assert url == "http://wandplotter.local/upload"
    assert payload["data"]["path"] == "/"
    assert payload["data"]["/wand.gcodeS"] == "4"
    assert "/wand.gcode" in payload["files"]


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


def test_jog_cancel_sends_the_realtime_byte():
    api, session = client()
    api.jog_cancel()
    assert session.calls[0][2] == {"plain": "\x85"}


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
