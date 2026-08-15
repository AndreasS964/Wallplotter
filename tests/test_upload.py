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
