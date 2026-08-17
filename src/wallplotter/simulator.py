"""Ein Board, das es noch nicht gibt — als Gegenstelle zum Üben.

    wallplotter-sim --port 8080
    wallplotter-doctor --host 127.0.0.1:8080
    plot --pattern frame --host 127.0.0.1:8080 --upload --run

Der Simulator bildet den Webserver von FluidNC nach, und zwar bewusst
**einschließlich seiner Eigenheiten**. Eine Gegenstelle, die freundlicher ist
als die echte, wäre wertlos: Sie würde genau die Fehler durchgehen lassen, die
sie finden soll. Deshalb hält er sich auch an die unbequemen Regeln:

* ``/upload`` ist die Karte, ``/files`` der Flash, ``/sdfiles`` gibt es nicht.
* ``/command?plain=<text>`` schneidet das erste Zeichen ab, bevor es in der
  Kommandotabelle sucht — G-Code und ``?`` laufen hier ins Leere, genau wie
  auf dem Board.
* Solange die Maschine fährt, antwortet ``/command`` mit **503**, wenn
  ``$HTTP/BlockDuringMotion`` steht (Voreinstellung: an).
* Statusreports gibt es nur auf dem WebSocket, dort dafür von selbst alle
  200 ms.
* Ein Soft-Reset mitten in der Fahrt hinterlässt einen **Alarm**, kein
  aufgeräumtes Idle — die Maschinenposition ist danach eben weg.

Was er dagegen *mitschreibt*: den Spindelzustand. Mit ``laser_mode=True``
merkt er sich modal, welcher Modus und welches ``S`` gelten, und hält fest,
wo ein Leerweg oder eine Wartezeit mit eingeschaltetem Strahl angetreten
wird — die eine Laserinvariante, die sich nicht am Text eines Programms
ablesen lässt, sondern nur an seinem Ablauf.

Was er *nicht* ist: ein Bewegungssimulator. Er fährt keine Rampen und rechnet
keine Kinematik; die Position folgt schlicht den X/Y-Werten der abgespielten
Zeilen. Für Laufzeiten ist :mod:`wallplotter.timing` zuständig, für Auflösung
und Kräfte :mod:`wallplotter.kinematics`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote_plus, urlsplit

__all__ = ["BoardSimulator", "SimulatorServer", "serve", "main"]

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_XY_RE = re.compile(r"([XY])\s*(-?\d*\.?\d+)", re.IGNORECASE)

DEFAULT_PORT = 8080
DEFAULT_BYTES_PER_S = 4000.0
"""Wie schnell ein Job „abgespielt" wird. Nicht physikalisch gemeint — nur
schnell genug, dass ein Testlauf in Sekunden durch ist, und langsam genug,
dass man dem Fortschrittsbalken beim Laufen zusehen kann."""

REPORT_INTERVAL_S = 0.2
"""So oft meldet ``WSChannel`` von sich aus (``setReportInterval(200)``)."""


# ---------------------------------------------------------------------------
# Zustand
# ---------------------------------------------------------------------------


@dataclass
class Job:
    """Ein laufendes Programm von der Karte."""

    path: str
    lines: list[str]
    total_bytes: int
    sent_bytes: int = 0
    index: int = 0
    credit: float = 0.0
    """Angesparte Bytes. Muss über die Takte hinweg stehen bleiben: Ein
    Zeitschritt reicht sonst nie für eine Zeile, die länger ist als das
    Bytebudget eines Takts — und der Job bliebe für immer bei denselben
    Prozent stehen."""

    @property
    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 100.0
        return min(100.0, 100.0 * self.sent_bytes / self.total_bytes)

    @property
    def done(self) -> bool:
        return self.index >= len(self.lines)


@dataclass
class BoardSimulator:
    """Der Zustand eines FluidNC-Boards, so weit wir ihn brauchen.

    Alle öffentlichen Methoden sind unter ``lock`` zu benutzen; der HTTP-Server
    ist mehrfädig, und der Job läuft in einem eigenen Faden.
    """

    card: dict[str, str] = field(default_factory=dict)
    """Die µSD-Karte: Pfad (mit führendem ``/``) → Inhalt."""

    state: str = "Idle"
    x: float = 0.0
    y: float = 0.0
    job: Job | None = None
    settings: dict[str, str] = field(
        default_factory=lambda: {
            "HTTP/BlockDuringMotion": "ON",
            "Report/Interval": "200",
            "Hostname": "wandplotter",
        }
    )
    work_offset: tuple[float, float] = (0.0, 0.0)
    g92_offset: tuple[float, float] = (0.0, 0.0)
    bytes_per_s: float = DEFAULT_BYTES_PER_S
    log: list[str] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    # -- Spindel / Laser --------------------------------------------------

    spindle_mode: str = "M5"
    """``M3`` (konstante Leistung), ``M4`` (mit dem Vorschub geregelt), ``M5`` (aus)."""

    spindle_s: float = 0.0

    laser_mode: bool = False
    """Entspricht ``laser_mode`` in der ``config.yaml``.

    Nur damit werden Laserverstöße mitgeschrieben: Am Stift ist ein
    stehendes ``S`` während eines Leerwegs völlig normal — es *hält* den Servo
    in der oberen Stellung. Am Laser wäre genau dasselbe ein Brandloch quer
    über die Wand. Dieselbe Zeile, entgegengesetzte Bedeutung; ohne diesen
    Schalter könnte der Simulator die beiden Fälle nicht unterscheiden.
    """

    laser_violations: list[str] = field(default_factory=list)
    """Was beim Abspielen aufgefallen ist — Strahl an, wo er aus sein müsste."""

    # -- Auskunft ---------------------------------------------------------

    @property
    def in_motion(self) -> bool:
        """Was ``inMotionState()`` in der Firmware beantwortet."""
        return self.state in {"Run", "Jog", "Home"}

    def status_report(self) -> str:
        """Die Zeile, die ``?`` auslöst — Format wie in ``Report.cpp``."""
        fields = [
            self.state,
            f"MPos:{self.x:.3f},{self.y:.3f},0.000",
            "FS:0,0" if not self.in_motion else "FS:1500,0",
        ]
        if self.job is not None:
            fields.append(f"SD:{self.job.percent:.2f},{self.job.path}")
        return "<" + "|".join(fields) + ">"

    def file_list(self, path: str = "/") -> str:
        """Die JSON-Antwort von ``handleFileOps`` — Schlüssel wie im Original."""
        prefix = path if path.endswith("/") else path + "/"
        entries = [
            {"name": name[len(prefix) :], "shortname": name[len(prefix) :],
             "size": len(content.encode("utf-8")), "datetime": ""}
            for name, content in sorted(self.card.items())
            if name.startswith(prefix) and "/" not in name[len(prefix) :]
        ]
        used = sum(len(content.encode("utf-8")) for content in self.card.values())
        total = 8 * 1024 * 1024 * 1024
        return json.dumps(
            {
                "files": entries,
                "path": path.rstrip("/") or "/",
                "total": f"{total / 1e9:.2f} GB",
                "used": f"{used / 1024:.2f} KB",
                "occupation": int(100 * used / total),
                "status": "Ok",
            }
        )

    # -- Kommandos --------------------------------------------------------

    def execute_settings_line(self, line: str) -> str:
        """``settings_execute_line()`` nachgebildet — **samt** abgeschnittenem Zeichen.

        Die Firmware erwartet hier ``$name=wert`` oder ``[ESPxxx]wert`` und
        wirft deshalb das erste Zeichen weg, ohne es anzusehen. Genau das ist
        der Grund, warum ``G92 X0 Y0`` und ``?`` über ``/command?plain=`` nicht
        tun, was man erwartet — und deshalb steht es hier auch so.
        """
        key, _, value = line[1:].partition("=")
        key = key.strip()
        return self._command(key, value)

    def _command(self, key: str, value: str) -> str:
        name = key.upper()
        if name == "":
            # `new UserCommand("", "Help", show_help, anyState)` — dahin läuft
            # ein `?`, das über HTTP hereinkommt.
            return "[MSG: Help: $H $J=line $S $X ...]"
        if name == "SD/RUN":
            return self.start_job(value)
        if name == "SD/SHOW":
            return self.show_file(value)
        if name == "SD/LIST":
            return "\n".join(f"[FILE:{path}|SIZE:{len(text)}]" for path, text in sorted(self.card.items()))
        if name == "SD/STATUS":
            return "SD card detected"
        if name == "SD/DELETE":
            self.card.pop(self._absolute(value), None)
            return ""
        if name == "H" or name == "HOME":
            self.x = self.y = 0.0
            self.state = "Idle"
            return "[MSG:Homed]"
        if name == "X" or name == "ALARM/DISABLE":
            self.state = "Idle"
            return "[MSG:Caution: Unlocked]"
        if name == "T" or name == "STATE":
            return f"State ({self.state})"
        if name == "#" or name == "GCODE/OFFSETS":
            return f"[G54:{self.work_offset[0]:.3f},{self.work_offset[1]:.3f},0.000]"
        if name in {"J", "JOG"}:
            return self._jog(value)
        if key in self.settings or "/" in key:
            if value:
                self.settings[key] = value
                return ""
            # Format wie `show_setting()`: ein Dollar, Name, Gleichheitszeichen.
            return f"${key}={self.settings.get(key, '')}"
        return f"error: Invalid statement ({key})"

    def execute_line(self, line: str) -> str:
        """``execute_line()`` nachgebildet: ``$``/``[`` zu den Kommandos, Rest G-Code."""
        text = line.strip()
        if not text:
            return ""
        self.log.append(text)
        if text[0] in "$[":
            return self.execute_settings_line(text)
        if self.state.startswith("Alarm"):
            return "error:9 (G-code locked out during alarm or jog state)"
        return self._gcode(text)

    def _gcode(self, line: str) -> str:
        upper = line.upper()
        self._spindle(upper)
        if upper.startswith("G92"):
            target = self._xy(line, self.x, self.y)
            self.g92_offset = (self.x - target[0], self.y - target[1])
            return ""
        if upper.startswith("G10"):
            target = self._xy(line, 0.0, 0.0)
            self.work_offset = target
            return ""
        if upper.startswith("G4"):
            if self.laser_mode and self.beam_on:
                self.laser_violations.append(f"Warten mit eingeschaltetem Strahl: {line}")
            return ""
        if upper.startswith("G0"):
            if self.laser_mode and self.beam_on:
                self.laser_violations.append(f"Leerweg mit eingeschaltetem Strahl: {line}")
            self.x, self.y = self._xy(line, self.x, self.y)
            return ""
        if upper.startswith("G1"):
            self.x, self.y = self._xy(line, self.x, self.y)
            return ""
        return ""

    @property
    def beam_on(self) -> bool:
        """Gibt die Spindel gerade Leistung ab?

        ``M4`` regelt die Leistung mit dem Vorschub und wäre im Stillstand
        harmlos — für die Prüfung zählt trotzdem das gesetzte ``S``: Ein
        Programm, das den Leerweg mit ``S>0`` antritt, verlässt sich darauf,
        dass die Firmware im richtigen Modus steht. Das ist eine Zeile in
        einer YAML-Datei, und Brandlöcher sind kein guter Ort für Vertrauen.
        """
        return self.spindle_mode in {"M3", "M4"} and self.spindle_s > 0

    def _spindle(self, upper: str) -> None:
        """``M3``/``M4``/``M5`` und ``S`` mitschreiben — modal, wie bei GRBL."""
        for token in upper.replace(";", " ; ").split():
            if token == ";":
                break
            if token in {"M3", "M03", "M4", "M04"}:
                self.spindle_mode = "M3" if token in {"M3", "M03"} else "M4"
            elif token in {"M5", "M05"}:
                self.spindle_mode = "M5"
                self.spindle_s = 0.0
            elif token.startswith("S") and len(token) > 1:
                try:
                    self.spindle_s = float(token[1:])
                except ValueError:
                    pass

    def _jog(self, argument: str) -> str:
        """``$J=G91 …`` und ``$J=G90 …``. Bewegung ohne Zeit — siehe Modulkopf."""
        relative = "G91" in argument.upper()
        if relative:
            dx, dy = self._xy(argument, 0.0, 0.0)
            self.x += dx
            self.y += dy
        else:
            self.x, self.y = self._xy(argument, self.x, self.y)
        return ""

    @staticmethod
    def _xy(line: str, x: float, y: float) -> tuple[float, float]:
        for axis, number in _XY_RE.findall(line):
            if axis.upper() == "X":
                x = float(number)
            else:
                y = float(number)
        return x, y

    @staticmethod
    def _absolute(path: str) -> str:
        path = path.strip()
        return path if path.startswith("/") else f"/{path}"

    # -- Karte und Job ----------------------------------------------------

    def store(self, path: str, content: str) -> None:
        self.card[self._absolute(path)] = content

    def show_file(self, path: str) -> str:
        """``$SD/Show`` — verlangt Idle oder Alarm, wie ``notIdleOrAlarm()``."""
        if self.state != "Idle" and not self.state.startswith("Alarm"):
            return "error:8 (Idle error)"
        content = self.card.get(self._absolute(path))
        if content is None:
            return "error:60 (Bad file)"
        return content

    def start_job(self, path: str) -> str:
        if self.state.startswith("Alarm"):
            return "Alarm"
        full = self._absolute(path)
        content = self.card.get(full)
        if content is None:
            return "error:60 (Bad file)"
        lines = content.splitlines()
        self.job = Job(path=full, lines=lines, total_bytes=len(content.encode("utf-8")))
        self.state = "Run"
        return f"[MSG:Running {full}]"

    def tick(self, elapsed_s: float) -> None:
        """Den laufenden Job um ``elapsed_s`` weiterdrehen."""
        job = self.job
        if job is None or self.state != "Run":
            return
        job.credit += self.bytes_per_s * elapsed_s
        while not job.done:
            line = job.lines[job.index]
            cost = len(line.encode("utf-8")) + 1
            if cost > job.credit:
                break
            job.credit -= cost
            job.sent_bytes += cost
            job.index += 1
            code = line.split(";")[0].strip()
            if not code:
                continue
            if code.split()[0].upper() in {"M0", "M00"}:
                # M0 ist in FluidNC eine Pause per Feed Hold (GCode.cpp:
                # ProgramFlow::Paused → feedHoldEvent) — weiter geht es mit
                # Cycle Start, nicht von allein.
                self.state = "Hold"
                return
            self._gcode(code)
        if job.done:
            job.sent_bytes = job.total_bytes
            self.state = "Idle"
            self.job = None

    # -- Realtime ---------------------------------------------------------

    def realtime(self, byte: int) -> str | None:
        """Ein Realtime-Byte. Gibt eine Antwortzeile zurück, wenn eine anfällt."""
        if byte == 0x3F:  # '?'
            return self.status_report()
        if byte == 0x21:  # '!' Feed Hold
            if self.state in {"Run", "Jog"}:
                self.state = "Hold"
            return None
        if byte == 0x7E:  # '~' Cycle Start
            if self.state == "Hold":
                self.state = "Run"
            return None
        if byte == 0x18:  # Ctrl-X Soft Reset
            # Mitten in der Fahrt kostet ein Reset die Maschinenposition —
            # GRBL geht dafür in Alarm, und das ist keine Kleinigkeit: Der
            # Nullpunkt ist danach weg. Ein Reset im Alarm *löst* ihn nicht:
            # Dazu ist `$X` da. Sonst räumte ein zweiter Not-Halt genau die
            # Warnung weg, die der erste aufgestellt hat.
            if self.in_motion:
                self.state = "Alarm:2"
            elif not self.state.startswith("Alarm"):
                self.state = "Idle"
            self.job = None
            return "[MSG:Reset]"
        if byte == 0x85:  # Jog Cancel
            if self.state == "Jog":
                self.state = "Idle"
            return None
        return None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def _ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """Server → Client, also unmaskiert (RFC 6455)."""
    head = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        head += bytes([length])
    elif length < (1 << 16):
        head += bytes([126]) + struct.pack(">H", length)
    else:
        head += bytes([127]) + struct.pack(">Q", length)
    return head + payload


class _Handler(BaseHTTPRequestHandler):
    """Die Endpunkte des FluidNC-Webservers, so weit sie hier gebraucht werden."""

    protocol_version = "HTTP/1.1"
    board: BoardSimulator  # wird von SimulatorServer gesetzt

    def log_message(self, fmt: str, *args: object) -> None:  # pragma: no cover
        if getattr(self.server, "verbose", False):
            print(f"[sim] {fmt % args}")

    def _send(self, code: int, body: str, content_type: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # -- GET ---------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - von BaseHTTPRequestHandler vorgegeben
        parts = urlsplit(self.path)
        query = parse_qs(parts.query, keep_blank_values=True)

        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._websocket()
            return
        if parts.path == "/command":
            self._command(query)
            return
        if parts.path == "/upload":
            with self.board.lock:
                self._send(200, self.board.file_list(query.get("path", ["/"])[0]), "application/json")
            return
        if parts.path == "/files":
            self._send(200, '{"files":[],"path":"/","status":"Ok"}', "application/json")
            return
        if parts.path in ("/feedhold_reload", "/cyclestart_reload", "/restart_reload"):
            byte = {"/feedhold_reload": 0x21, "/cyclestart_reload": 0x7E, "/restart_reload": 0x18}[
                parts.path
            ]
            with self.board.lock:
                self.board.realtime(byte)
            # Die Firmware antwortet mit einer Umleitung auf die Startseite.
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parts.path == "/":
            self._send(200, "FluidNC (Simulator)", "text/html")
            return
        self._send(404, "Not found", "text/plain")

    def _command(self, query: dict[str, list[str]]) -> None:
        """``/command`` — nur ``$``-Kommandos, und nicht während der Fahrt."""
        with self.board.lock:
            blocked = self.board.settings.get("HTTP/BlockDuringMotion", "ON") == "ON"
            if blocked and self.board.in_motion:
                self._send(503, "Try again when not moving\n", "text/plain")
                return
            if "plain" in query:
                answer = self.board.execute_settings_line(query["plain"][0])
                self._send(200, answer, "text/plain")
                return
        self._send(500, "Invalid command", "text/plain")

    # -- POST --------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - von BaseHTTPRequestHandler vorgegeben
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""

        if parts.path != "/upload":
            # /files wäre der Flash. Wer da ein Wandbild ablegt, findet es auf
            # der Karte nicht wieder — deshalb hier ausdrücklich nichts tun.
            self._send(404, "Not found", "text/plain")
            return

        boundary = self._boundary()
        if boundary is None:
            self._send(400, "no boundary", "text/plain")
            return
        fields, files = _parse_multipart(body, boundary)
        for path, payload in files.items():
            expected = fields.get(f"{path}S")
            if expected is not None and int(expected) != len(payload):
                self._send(200, '{"status":"Upload failed"}', "application/json")
                return
            with self.board.lock:
                self.board.store(path, payload.decode("utf-8", "replace"))
        query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
        with self.board.lock:
            self._send(200, self.board.file_list(query.get("path", ["/"])[0]), "application/json")

    def _boundary(self) -> bytes | None:
        content_type = self.headers.get("Content-Type", "")
        marker = "boundary="
        if marker not in content_type:
            return None
        return content_type.split(marker, 1)[1].strip().strip('"').encode("ascii")

    # -- WebSocket ---------------------------------------------------------

    def _websocket(self) -> None:
        # Nach dem Upgrade gehört die Verbindung dem Kanal, nicht mehr dem
        # HTTP-Handler — sonst versucht der danach, die nächste Anfrage aus
        # einem Socket zu lesen, auf dem längst Rahmen liegen.
        self.close_connection = True
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        try:
            self.wfile.flush()
        except OSError:
            return
        channel = _WebSocketChannel(self.connection, self.board)
        server = self.server
        server.channels.add(channel)
        try:
            channel.run()
        finally:
            server.channels.discard(channel)


class _WebSocketChannel:
    """Ein offener Kanal — dasselbe, was ``WSChannel`` in der Firmware ist.

    Meldet von sich aus alle 200 ms Status, fängt Realtime-Bytes im
    Zeichenstrom ab und gibt den Rest zeilenweise an den Parser.
    """

    def __init__(self, connection, board: BoardSimulator) -> None:
        self._connection = connection
        self._board = board
        self._buffer = b""
        self._pending = b""
        self._alive = True

    def stop(self) -> None:
        """Den Kanal abwürgen — das, was ein Board-Neustart mit ihm macht.

        Ohne das überlebt eine offene Verbindung das Abschalten des Servers:
        ``shutdown()`` beendet nur die Annahme neuer Anfragen, und der Faden
        eines bestehenden Kanals redet munter weiter. Ein Test könnte dann
        nicht prüfen, ob die Gegenseite einen Verbindungsabriss übersteht —
        es gäbe keinen.
        """
        self._alive = False
        try:
            self._connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._connection.close()
        except OSError:
            pass

    def run(self) -> None:
        connection = self._connection
        connection.settimeout(0.05)
        self._send_text("currentID:1")
        last_report = 0.0
        while self._alive:
            now = time.monotonic()
            try:
                chunk = connection.recv(4096)
            except (TimeoutError, OSError):
                chunk = b""
            if chunk:
                self._buffer += chunk
                if not self._consume():
                    break
            elif chunk == b"" and self._closed(connection):
                break
            if now - last_report >= REPORT_INTERVAL_S:
                last_report = now
                with self._board.lock:
                    report = self._board.status_report()
                if not self._send_text(report):
                    break

    @staticmethod
    def _closed(connection) -> bool:
        try:
            connection.getpeername()
        except OSError:
            return True
        return False

    def _send_text(self, text: str) -> bool:
        try:
            self._connection.sendall(_ws_frame((text + "\n").encode("utf-8")))
        except OSError:
            self._alive = False
            return False
        return True

    def _consume(self) -> bool:
        while True:
            frame = self._next_frame()
            if frame is None:
                return True
            opcode, payload = frame
            if opcode == 0x8:
                self._alive = False
                return False
            if opcode == 0x9:
                try:
                    self._connection.sendall(_ws_frame(payload, 0xA))
                except OSError:
                    return False
                continue
            if opcode in (0x0, 0x1, 0x2):
                self._push(payload)

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

    def _push(self, payload: bytes) -> None:
        """Byte für Byte — Realtime-Zeichen unterwegs abfangen, wie ``Channel::push``."""
        for byte in payload:
            if byte in (0x3F, 0x21, 0x7E, 0x18, 0x85):
                with self._board.lock:
                    answer = self._board.realtime(byte)
                if answer:
                    self._send_text(answer)
                continue
            if byte == 0x0A:
                line = self._pending.decode("utf-8", "replace").strip()
                self._pending = b""
                if line:
                    with self._board.lock:
                        answer = self._board.execute_line(line)
                    self._reply(answer)
                continue
            self._pending += bytes([byte])

    def _reply(self, answer: str) -> None:
        """Ausgabe und Quittung — die Firmware schließt jede Zeile mit beidem ab.

        ``ok`` beziehungsweise ``error:<n>`` ist die Grenze, an der die
        Gegenseite weiß, dass die Antwort vollständig ist. Ohne sie müsste sie
        raten, und in eine von der Karte gelesene Datei geriete der nächste
        Statusreport.
        """
        text = answer or ""
        if text and not text.startswith("error:"):
            self._send_text(text)
        self._send_text(text if text.startswith("error:") else "ok")


def _parse_multipart(body: bytes, boundary: bytes) -> tuple[dict[str, str], dict[str, bytes]]:
    """Nur so viel Multipart, wie ``requests`` erzeugt und die Firmware liest.

    Wichtig ist dabei die Unterscheidung, an der es sonst still schiefgeht:
    Als Zielpfad zählt der **Dateiname** des Teils, nicht sein Feldname — so
    macht es auch ``fileUpload()`` in der Firmware.
    """
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}
    separator = b"--" + boundary
    for part in body.split(separator):
        if not part.strip() or part.strip() == b"--":
            continue
        head, _, payload = part.partition(b"\r\n\r\n")
        if not _:
            continue
        payload = payload[:-2] if payload.endswith(b"\r\n") else payload
        disposition = ""
        for line in head.decode("utf-8", "replace").splitlines():
            if line.lower().startswith("content-disposition:"):
                disposition = line
        name = _disposition_value(disposition, "name")
        filename = _disposition_value(disposition, "filename")
        if filename:
            files[filename if filename.startswith("/") else f"/{filename}"] = payload
        elif name:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, files


def _disposition_value(disposition: str, key: str) -> str:
    match = re.search(rf'{key}="([^"]*)"', disposition)
    return unquote_plus(match.group(1)) if match else ""


class SimulatorServer(ThreadingHTTPServer):
    """HTTP- und WebSocket-Gegenstelle in einem, auf einem Faden je Verbindung."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, board: BoardSimulator, port: int = DEFAULT_PORT, verbose: bool = False):
        handler = type("_BoundHandler", (_Handler,), {"board": board})
        super().__init__(("127.0.0.1", port), handler)
        self.board = board
        self.verbose = verbose
        self.channels: set[_WebSocketChannel] = set()
        self._running = True
        # Ein laufender Job hängt nicht daran, dass jemand zusieht: Auf dem
        # Board läuft er weiter, wenn die Oberfläche zugeklappt wird, und hier
        # muss er das auch — sonst stünde jeder Test still, der nach dem Start
        # erst einmal den Kanal schließt.
        self._motor = threading.Thread(target=self._advance, daemon=True)
        self._motor.start()

    def _advance(self) -> None:
        step = 0.05
        while self._running:
            time.sleep(step)
            with self.board.lock:
                self.board.tick(step)

    def server_close(self) -> None:
        self._running = False
        for channel in list(self.channels):
            channel.stop()
        super().server_close()

    @property
    def host(self) -> str:
        """Was in ``--host`` gehört."""
        return f"127.0.0.1:{self.server_address[1]}"


def serve(
    port: int = DEFAULT_PORT,
    *,
    card: dict[str, str] | None = None,
    verbose: bool = False,
) -> SimulatorServer:
    """Simulator starten und den Server zurückgeben (läuft in einem Hintergrundfaden)."""
    board = BoardSimulator(card=dict(card or {}))
    server = SimulatorServer(board, port, verbose)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallplotter-sim",
        description="Ein FluidNC-Board nachspielen, solange keines an der Wand hängt.",
        epilog="Danach etwa: wallplotter-doctor --host 127.0.0.1:8080",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port (Vorgabe 8080)")
    parser.add_argument(
        "--card",
        type=Path,
        help="Verzeichnis, dessen Dateien als Inhalt der SD-Karte gelten",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=DEFAULT_BYTES_PER_S,
        help="Wie schnell ein Job abgespielt wird, in Byte je Sekunde",
    )
    parser.add_argument("--quiet", action="store_true", help="keine Zugriffsmeldungen")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    card: dict[str, str] = {}
    if args.card:
        if not args.card.is_dir():
            print(f"{args.card} ist kein Verzeichnis")
            return 2
        for path in sorted(args.card.iterdir()):
            if path.is_file():
                card[f"/{path.name}"] = path.read_text(encoding="utf-8", errors="replace")

    board = BoardSimulator(card=card, bytes_per_s=args.speed)
    server = SimulatorServer(board, args.port, verbose=not args.quiet)
    print(f"Simulator auf http://{server.host} — beenden mit Strg-C")
    print(f"  wallplotter-doctor --host {server.host}")
    print(f"  plot --pattern frame --host {server.host} --upload --run")
    if card:
        print(f"  Karte: {len(card)} Datei(en) aus {args.card}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSimulator beendet.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
