"""Test-Gegenstellen für FluidNC — absichtlich keine Ja-Sager.

Die alten Attrappen quittierten *jede* URL und *jedes* Kommando mit HTTP 200.
Genau darüber sind sechs Firmware-Fehler jahrelang grün geblieben: der Upload
ging an einen Endpunkt, den es nicht gibt, und der Not-Halt an einen Parameter,
der ihn verschluckt — beides mit „ok" quittiert.

Deshalb bilden diese beiden Klassen nach, was die Firmware wirklich tut:

* :class:`FakeSession` kennt genau die Endpunkte, die
  ``WebUI/WebUIServer.cpp`` registriert. Alles andere ist **404**. Und
  ``/command?plain=`` verhält sich wie ``settings_execute_line()``: es wirft
  das erste Zeichen weg und versteht nur ``$name=wert`` — GCode und
  Realtime-Zeichen bekommen dort die Hilfezeile mit HTTP 200, nicht Wirkung.

  Karte und Flash sind zwei getrennte Ablagen, so wie auf dem Board: ``/upload``
  schreibt auf die SD-Karte, ``/files`` in den Flash. Wer die ``config.yaml``
  auf die Karte lädt, bekommt hier dieselbe freundliche Antwort wie vom echten
  Board — und dieselbe Wirkungslosigkeit. Gelesen wird der Flash über den
  Nicht-gefunden-Zweig (``handle_not_found`` → ``myStreamFile``), und während
  einer Fahrt antwortet der mit **503** statt mit der Datei.
* :class:`FakeFluidNCSocket` ist ein Bytestrom wie der Telnet-Kanal: er
  beantwortet ``?`` mit einem Statusbericht, quittiert Zeilen mit ``ok`` und
  kann auf Wunsch ``error:`` liefern.
"""

from __future__ import annotations

import re

__all__ = [
    "FakeFluidNCSocket",
    "FakeResponse",
    "FakeSession",
    "HELP_LINE",
    "opener_for",
]

HELP_LINE = "[HLP:$$ $+ $# $S $L $G $I $N $x=val $Nx=line $J=line $SLP $C $X $H $F $E=err ~ ! ? ctrl-x]"
"""Was FluidNC auf ein Kommando mit leerem Namen ausgibt — also auf jedes
Realtime-Byte und jedes ``?``, das über ``/command?plain=`` hereinkommt."""

_SETTING_RE = re.compile(r"^[$\[]")


class FakeResponse:
    def __init__(self, text: str = "ok", status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class FakeSession:
    """Ersatz für ``requests.Session``, der nur echte Endpunkte kennt."""

    #: Pfade, die FluidNC laut WebUIServer.cpp registriert.
    KNOWN = {
        "/",
        "/login",
        "/command",
        "/command_silent",
        "/trace",
        "/feedhold_reload",
        "/cyclestart_reload",
        "/restart_reload",
        "/did_restart",
        "/files",
        "/updatefw",
        "/upload",
    }

    def __init__(
        self,
        files: dict[str, str] | None = None,
        blocked: bool = False,
        flash: dict[str, str] | None = None,
        upload_scheitert: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.timeouts: list[float | None] = []
        self.card: dict[str, str] = dict(files or {})
        """Die SD-Karte — hier liegt der GCode."""

        self.flash: dict[str, str] = dict(flash or {})
        """Das lokale Dateisystem — hier liegt die ``config.yaml``."""

        self.blocked = blocked
        """``$HTTP/BlockDuringMotion``: dann antworten ``/command`` und das
        Ausliefern von Flash-Dateien mit 503."""

        self.upload_scheitert = upload_scheitert
        """Der Upload misslingt — und zwar so, wie er es beim echten Board tut:
        mit **HTTP 200** und ``"status":"Upload failed"`` im Rumpf
        (``WebUIServer.cpp:1220-1224``). Wer nur den Statuscode ansieht, hält
        das für einen Erfolg."""

    # -- Hilfsmittel ------------------------------------------------------

    @staticmethod
    def _path(url: str) -> str:
        without_scheme = url.split("://", 1)[-1]
        return "/" + without_scheme.partition("/")[2].partition("?")[0]

    def _dispatch(self, path: str, params: dict | None) -> FakeResponse:
        if path.startswith("/sd/"):
            name = path[len("/sd") :]
            if name in self.card:
                return FakeResponse(self.card[name])
            return FakeResponse("Not found", 404)
        if path not in self.KNOWN:
            # handle_not_found() liefert Dateien aus dem Flash aus, bevor es 404 sagt
            if path in self.flash:
                if self.blocked:
                    return FakeResponse(
                        "<h3>Cannot load WebUI while GCode Program is Running</h3>", 503
                    )
                return FakeResponse(self.flash[path])
            return FakeResponse("Not found", 404)
        if path == "/command":
            return self._command(params or {})
        if path == "/upload":
            listing = ", ".join(f'{{"name":"{n.lstrip("/")}"}}' for n in sorted(self.card))
            return FakeResponse(f'{{"files":[{listing}],"status":"Ok"}}')
        if path.endswith("_reload"):
            return FakeResponse("", 302)
        return FakeResponse("ok")

    def _command(self, params: dict) -> FakeResponse:
        if self.blocked:
            return FakeResponse("Try again when not moving\n", 503)
        plain = params.get("plain")
        if plain is None:
            return FakeResponse("Invalid command", 500)
        # settings_execute_line(): Zeichen 0 wird weggeworfen
        key = plain[1:].partition("=")[0].partition("]")[0].strip()
        if not key:
            # Trifft UserCommand("", "Help", …) — HTTP 200 ohne jede Wirkung
            return FakeResponse(HELP_LINE + "\nok\n")
        if not _SETTING_RE.match(plain):
            return FakeResponse("Error: Invalid statement\n", 500)
        return FakeResponse("ok\n")

    # -- requests-Schnittstelle -------------------------------------------

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        self.calls.append(("get", url, params))
        self.timeouts.append(timeout)
        return self._dispatch(self._path(url), params)

    def post(self, url, params=None, data=None, files=None, timeout=None):
        self.calls.append(("post", url, {"params": params, "data": data, "files": files}))
        self.timeouts.append(timeout)
        path = self._path(url)
        ablage = {"/upload": self.card, "/files": self.flash}.get(path)
        if ablage is None:
            return FakeResponse("Not found", 404)
        if self.upload_scheitert:
            # Nichts wird abgelegt — aber quittiert wird trotzdem mit 200.
            return FakeResponse('{"files":[],"status":"Upload failed"}')
        for _field, (filename, payload, *_rest) in (files or {}).items():
            body = payload.decode("utf-8") if isinstance(payload, bytes) else payload
            ablage[filename if filename.startswith("/") else f"/{filename}"] = body
        return FakeResponse('{"status":"Ok"}')


class FakeFluidNCSocket:
    """Bytestrom, der sich wie FluidNCs Telnet-Kanal verhält."""

    def __init__(
        self,
        status: str = "<Idle|MPos:0.000,0.000,0.000|FS:0,0|WCO:0.000,0.000,0.000>",
        errors: dict[str, str] | None = None,
    ) -> None:
        self.status_line = status
        self.errors = dict(errors or {})
        self.lines: list[str] = []
        """Alle vollständigen Zeilen, die der Client geschickt hat."""
        self.realtime: list[int] = []
        """Alle einzelnen Realtime-Bytes."""
        self.timeouts: list[float] = []
        self.closed = False
        self._out = bytearray()
        self._pending = bytearray()

    # -- Socket-Schnittstelle ---------------------------------------------

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def sendall(self, data: bytes) -> None:
        for byte in data:
            if byte == 0x0A:
                line = self._pending.decode("utf-8", errors="replace").strip("\r")
                self._pending.clear()
                self.lines.append(line)
                self._answer(line)
            elif byte >= 0x80 or byte in (0x18, 0x21, 0x3F, 0x7E):
                # Realtime-Zeichen wirken sofort und werden nicht quittiert
                self.realtime.append(byte)
                if byte == 0x3F:  # '?'
                    self._out += (self.status_line + "\n").encode("utf-8")
            else:
                self._pending.append(byte)

    def recv(self, size: int) -> bytes:
        if not self._out:
            raise TimeoutError("nichts zu lesen")
        chunk = bytes(self._out[:size])
        del self._out[: len(chunk)]
        return chunk

    def close(self) -> None:
        self.closed = True

    # -- Verhalten --------------------------------------------------------

    def _answer(self, line: str) -> None:
        for prefix, message in self.errors.items():
            if line.startswith(prefix):
                self._out += (message + "\n").encode("utf-8")
                return
        self._out += b"ok\n"


def opener_for(sock: FakeFluidNCSocket):
    """Ersatz für ``socket.create_connection``, der immer ``sock`` liefert."""

    def opener(address, timeout=None):
        sock.address = address
        sock.connect_timeout = timeout
        return sock

    return opener
