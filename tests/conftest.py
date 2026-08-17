"""Gemeinsame Doppelgänger für Board-Tests.

Die beiden Wege ins Board sind verschieden genug, dass sie auch verschiedene
Attrappen brauchen: ``FakeSession`` steht für den HTTP-Teil (Dateien),
``FakeChannel`` für den WebSocket-Kanal (Status, G-Code, Realtime).

Wer die echte Verdrahtung sehen will, statt einer Attrappe zu glauben, findet
sie in ``test_simulator.py`` — dort läuft derselbe Client gegen den Simulator
aus dem Paket, über eine echte Netzwerkverbindung.
"""

from __future__ import annotations


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

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        self.calls.append(("get", url, params))
        self.timeouts.append(timeout)
        return FakeResponse(self.text, self.status_code)

    def post(self, url, params=None, data=None, files=None, timeout=None):
        self.calls.append(("post", url, {"params": params, "data": data, "files": files}))
        return FakeResponse(self.text, self.status_code)


class FakeChannel:
    """Ersatz für BoardChannel — merkt sich Zeilen und Realtime-Bytes."""

    def __init__(self, status="<Idle|MPos:0.000,0.000,0.000|FS:0,0>", lines=None, dead=False):
        self.status_line = status
        self.lines = list(lines or [])
        self.sent: list[str] = []
        self.realtime: list[int] = []
        self.dead = dead
        self._connected = False
        self.closed = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        if self.dead:
            raise OSError("kein Kanal")
        self._connected = True

    def close(self) -> None:
        self._connected = False
        self.closed = True

    def send_line(self, text: str) -> None:
        self.sent.append(text)

    def send_realtime(self, byte: int) -> None:
        if self.dead:
            raise OSError("kein Kanal")
        self.realtime.append(byte)

    def poll(self, timeout_s: float = 0.0) -> list[str]:
        lines, self.lines = self.lines, []
        return lines

    def request_status(self, timeout_s: float = 2.0) -> str:
        return self.status_line
