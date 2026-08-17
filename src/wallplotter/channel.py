"""Der WebSocket-Kanal zum Board — und warum es ohne ihn nicht geht.

FluidNC hat zwei Wege hinein, und sie können sehr Unterschiedliches:

* **HTTP** ``/command?plain=<text>`` landet in ``settings_execute_line()``.
  Diese Funktion schneidet das erste Zeichen ab (sie erwartet ``$`` oder ``[``)
  und sucht den Rest in der Kommandotabelle. Für ``$SD/Run=…`` ist das genau
  richtig. Für alles andere ist es das Gegenteil davon: ``G92 X0 Y0`` wird zu
  einer Suche nach der Einstellung ``92 X0 Y0``, und ``?`` wird zu einer Suche
  nach dem leeren Namen — beides ergibt keinen Statusreport und keine
  Bewegung. Zusätzlich antwortet ``/command`` mit **HTTP 503**, solange die
  Maschine fährt und ``$HTTP/BlockDuringMotion`` steht (Voreinstellung: an).

* **WebSocket** auf ``ws://<host>/`` ist ein *Channel* im Sinne der Firmware,
  genau wie die serielle Schnittstelle. Jedes Byte geht durch
  ``Channel::push()``, das Realtime-Zeichen (``?``, ``!``, ``~``, ``0x18``,
  ``0x85``) sofort abfängt und alles andere zeilenweise dem GCode-Parser
  gibt. Der Kanal meldet von sich aus alle 200 ms einen Statusreport
  (``WSChannel`` setzt ``setReportInterval(200)`` im Konstruktor) und wird von
  der Bewegungssperre nicht angefasst.

Daraus folgt die Arbeitsteilung im :mod:`wallplotter.upload`-Client: Dateien
über HTTP, alles Maschinennahe über diesen Kanal.

Bewusst ohne Fremdbibliothek: Was hier gebraucht wird — Handshake, Textrahmen,
Ping/Pong — sind rund zweihundert Zeilen, und eine Abhängigkeit mehr wäre für
ein Werkzeug, das auf einem Bastelrechner neben der Kletterwand laufen soll,
der schlechtere Tausch.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import time
from collections import deque
from urllib.parse import urlsplit

__all__ = [
    "CYCLE_START",
    "FEED_HOLD",
    "JOG_CANCEL",
    "SOFT_RESET",
    "STATUS_REPORT",
    "BoardChannel",
    "ChannelError",
]

# GRBL-Realtime-Bytes. Als Zahlen, nicht als Zeichen: sie gehen als rohe Bytes
# über den Kanal, und ein Zeichen jenseits von ASCII würde unterwegs zu UTF-8
# werden — aus 0x85 würden 0xC2 0x85, und das Board sähe nie das Byte, auf das
# es wartet.
STATUS_REPORT = 0x3F  # '?'
FEED_HOLD = 0x21  # '!'
CYCLE_START = 0x7E  # '~'
SOFT_RESET = 0x18  # Ctrl-X
JOG_CANCEL = 0x85

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

_MAX_FRAME = 1 << 20
"""Ein Rahmen vom Board bleibt weit darunter. Die Grenze ist da, damit eine
kaputte Gegenstelle nicht beliebig viel Speicher anfordern kann."""


class ChannelError(RuntimeError):
    """Der Kanal zum Board ließ sich nicht aufbauen oder brach ab."""


def _host_and_port(host: str, default_port: int = 80) -> tuple[str, int]:
    """``fluidnc.local``, ``192.168.1.42:8080`` oder ``http://…`` zerlegen."""
    text = host if "//" in host else f"//{host}"
    parts = urlsplit(text)
    if not parts.hostname:
        raise ChannelError(f"Kein Hostname in {host!r}")
    return parts.hostname, parts.port or default_port


class BoardChannel:
    """Eine offene WebSocket-Verbindung zum Board.

    Die Klasse ist absichtlich klein und synchron: Sie wird aus einem
    CLI-Aufruf oder aus einem Hintergrund-Thread der Web-UI benutzt, nie aus
    einer Event-Loop.

    Empfangene Zeilen werden gepuffert, weil das Board unaufgefordert redet —
    Statusreports alle 200 ms, dazu ``ok``, ``error:…`` und Meldungen mit
    ``[MSG:…]``. Wer auf eine Antwort wartet, sucht sie in diesem Puffer.
    """

    def __init__(
        self,
        host: str,
        *,
        timeout_s: float = 5.0,
        connect_timeout_s: float = 3.0,
    ) -> None:
        self.host = host
        self.timeout_s = timeout_s
        self.connect_timeout_s = connect_timeout_s
        self._socket: socket.socket | None = None
        self._buffer = b""
        self._pending = b""
        self._lines: deque[str] = deque(maxlen=500)

    # -- Auf- und Abbau ---------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        """Handshake fahren. Mehrfaches Aufrufen ist folgenlos."""
        if self._socket is not None:
            return

        hostname, port = _host_and_port(self.host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\n"
            f"Host: {hostname}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")

        try:
            sock = socket.create_connection((hostname, port), timeout=self.connect_timeout_s)
        except OSError as exc:
            raise ChannelError(f"Board {hostname}:{port} antwortet nicht: {exc}") from exc

        try:
            sock.settimeout(self.connect_timeout_s)
            sock.sendall(request)
            header, rest = self._read_http_header(sock)
            self._check_handshake(header, key)
        except ChannelError:
            sock.close()
            raise
        except OSError as exc:
            sock.close()
            raise ChannelError(f"Handshake mit {hostname}:{port} fehlgeschlagen: {exc}") from exc

        sock.settimeout(self.timeout_s)
        self._socket = sock
        self._buffer = rest
        self._pending = b""
        self._lines.clear()

    def close(self) -> None:
        """Verbindung schließen — auch wenn die Gegenstelle schon weg ist."""
        sock, self._socket = self._socket, None
        if sock is None:
            return
        try:
            sock.sendall(self._frame(_OP_CLOSE, b""))
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def __enter__(self) -> BoardChannel:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _read_http_header(sock: socket.socket) -> tuple[bytes, bytes]:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise ChannelError("Verbindung brach während des Handshakes ab")
            data += chunk
            if len(data) > 16384:
                raise ChannelError("Antwort auf den Handshake ist unplausibel lang")
        head, _, rest = data.partition(b"\r\n\r\n")
        return head, rest

    @staticmethod
    def _check_handshake(header: bytes, key: str) -> None:
        text = header.decode("latin-1")
        first = text.split("\r\n", 1)[0]
        if "101" not in first:
            raise ChannelError(
                f"Board hat den WebSocket abgelehnt: {first!r}. "
                "Läuft dort wirklich FluidNC, und ist der Port richtig?"
            )
        expected = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode(
            "ascii"
        )
        for line in text.split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "sec-websocket-accept" and value.strip() == expected:
                return
        raise ChannelError("Board hat den Handshake nicht korrekt bestätigt")

    # -- Senden -----------------------------------------------------------

    def send_line(self, text: str) -> None:
        """Eine Kommandozeile schicken (Zeilenende wird ergänzt).

        Hier gehen G-Code und ``$``-Kommandos gleichermaßen durch — der Kanal
        ist derselbe, den auch die serielle Schnittstelle benutzt.
        """
        line = text if text.endswith("\n") else text + "\n"
        self._send(self._frame(_OP_TEXT, line.encode("utf-8")))

    def send_realtime(self, byte: int) -> None:
        """Ein einzelnes Realtime-Byte schicken.

        Als **Binärrahmen**: Ein Textrahmen müsste gültiges UTF-8 enthalten,
        und der Jog-Abbruch 0x85 ist das nicht. FluidNC schiebt beide
        Rahmenarten durch dieselbe ``push()``-Kette, die Realtime-Bytes
        abfängt — der Unterschied ist nur, dass der Binärweg das Byte
        unverfälscht überträgt.
        """
        if not 0 <= byte <= 0xFF:
            raise ChannelError("Ein Realtime-Byte liegt zwischen 0 und 255")
        self._send(self._frame(_OP_BINARY, bytes([byte])))

    def _send(self, payload: bytes) -> None:
        sock = self._socket
        if sock is None:
            raise ChannelError("Kanal ist nicht offen")
        try:
            sock.sendall(payload)
        except OSError as exc:
            self.close()
            raise ChannelError(f"Senden fehlgeschlagen: {exc}") from exc

    @staticmethod
    def _frame(opcode: int, payload: bytes) -> bytes:
        """Einen Rahmen bauen. Client → Server ist immer maskiert (RFC 6455)."""
        head = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            head += bytes([0x80 | length])
        elif length < (1 << 16):
            head += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return head + mask + masked

    # -- Empfangen --------------------------------------------------------

    def poll(self, timeout_s: float = 0.0) -> list[str]:
        """Alles lesen, was bis ``timeout_s`` hereinkommt, und als Zeilen geben.

        Blockiert höchstens ``timeout_s`` — mit 0 also gar nicht. Ping wird
        unterwegs beantwortet, damit die Verbindung über lange Plots steht.
        """
        deadline = time.monotonic() + max(timeout_s, 0.0)
        while True:
            self._drain_socket(max(deadline - time.monotonic(), 0.0))
            if timeout_s <= 0 or self._lines or time.monotonic() >= deadline:
                break
        return self._take_lines()

    def read_matching(self, prefix: str, timeout_s: float = 2.0) -> str | None:
        """Auf die erste Zeile warten, die mit ``prefix`` anfängt.

        Zeilen, die vorher kommen, bleiben nicht liegen — sie werden verworfen,
        weil auf diesem Kanal ohnehin ununterbrochen Statusreports laufen und
        ein wachsender Puffer nur alt würde.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            for line in self._take_lines():
                if line.startswith(prefix):
                    return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._drain_socket(remaining)

    def request_status(self, timeout_s: float = 2.0) -> str:
        """``?`` schicken und den Statusreport ``<…>`` zurückgeben."""
        self.send_realtime(STATUS_REPORT)
        line = self.read_matching("<", timeout_s)
        if line is None:
            raise ChannelError(
                "Board meldet keinen Status. Läuft die Firmware, und ist der Kanal offen?"
            )
        return line

    def _take_lines(self) -> list[str]:
        lines = list(self._lines)
        self._lines.clear()
        return lines

    def _drain_socket(self, timeout_s: float) -> None:
        sock = self._socket
        if sock is None:
            raise ChannelError("Kanal ist nicht offen")
        sock.settimeout(max(timeout_s, 0.0))
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            return
        except OSError as exc:
            self.close()
            raise ChannelError(f"Kanal abgebrochen: {exc}") from exc
        finally:
            if self._socket is not None:
                self._socket.settimeout(self.timeout_s)
        if not chunk:
            self.close()
            raise ChannelError("Board hat die Verbindung geschlossen")
        self._buffer += chunk
        self._consume_frames()

    def _consume_frames(self) -> None:
        while True:
            frame = self._next_frame()
            if frame is None:
                return
            opcode, payload = frame
            if opcode == _OP_PING:
                self._send(self._frame(_OP_PONG, payload))
            elif opcode == _OP_CLOSE:
                self.close()
                raise ChannelError("Board hat die Verbindung geschlossen")
            elif opcode in (_OP_TEXT, _OP_BINARY, _OP_CONT):
                self._absorb(payload)
            # Pong ignorieren — wir fragen nie danach.

    def _next_frame(self) -> tuple[int, bytes] | None:
        data = self._buffer
        if len(data) < 2:
            return None
        opcode = data[0] & 0x0F
        masked = bool(data[1] & 0x80)
        length = data[1] & 0x7F
        offset = 2
        if length == 126:
            if len(data) < offset + 2:
                return None
            length = struct.unpack(">H", data[offset : offset + 2])[0]
            offset += 2
        elif length == 127:
            if len(data) < offset + 8:
                return None
            length = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 8
        if length > _MAX_FRAME:
            self.close()
            raise ChannelError(f"Rahmen mit {length} Byte ist zu groß")
        mask = b""
        if masked:
            if len(data) < offset + 4:
                return None
            mask = data[offset : offset + 4]
            offset += 4
        if len(data) < offset + length:
            return None
        payload = data[offset : offset + length]
        self._buffer = data[offset + length :]
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, payload

    def _absorb(self, payload: bytes) -> None:
        """Nutzlast in Zeilen zerlegen.

        FluidNC schickt seine Ausgabe zeilenweise, aber ein Rahmen muss keine
        ganze Zeile enthalten — deshalb der Rest in ``_pending``.
        """
        self._pending += payload
        while True:
            index = self._pending.find(b"\n")
            if index < 0:
                break
            line, self._pending = self._pending[:index], self._pending[index + 1 :]
            text = line.decode("utf-8", "replace").strip("\r")
            if text:
                self._lines.append(text)
        # Ein Statusreport steht auch ohne Zeilenende für sich: Er endet mit
        # '>', und das Board hängt nicht immer ein '\n' an.
        while True:
            start = self._pending.find(b"<")
            end = self._pending.find(b">", start + 1) if start >= 0 else -1
            if start < 0 or end < 0:
                break
            report = self._pending[start : end + 1].decode("utf-8", "replace")
            self._pending = self._pending[end + 1 :]
            self._lines.append(report)
        if len(self._pending) > _MAX_FRAME:
            self._pending = b""
