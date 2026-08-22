"""Anbindung an FluidNC: Dateien über HTTP, Kommandos über den TCP-Kanal.

Diese Aufteilung ist nicht willkürlich, sondern folgt dem, was die Firmware
tatsächlich anbietet (geprüft gegen ``FluidNC/src/WebUI/WebUIServer.cpp`` und
``ProcessSettings.cpp``, siehe ``docs/firmware-gegenpruefung.md``):

* **Dateien** liegen auf HTTP. ``POST /upload`` schreibt auf die SD-Karte,
  ``GET /upload?path=/`` listet sie, ``GET /sd/<datei>`` liest zurück. (Und
  ``/files`` ist der *Flash*, nicht die Karte — das war hier lange umgekehrt
  eingetragen. Ein ``/sdfiles`` gibt es überhaupt nicht.)

  Die Unterscheidung ist keine Feinheit: Die ``config.yaml`` liegt im Flash,
  nicht auf der Karte. Wer sie auf die SD-Karte lädt, bekommt eine erfolgreiche
  Antwort und ein Board, das weiterhin die alte Konfiguration fährt. Dafür gibt
  es :meth:`FluidNCClient.upload_local`.
* **Kommandos** gehen über den Telnet-Kanal auf Port 23. Der ist bei FluidNC
  ab Werk an (``DEFAULT_TELNET_STATE = 1``) und ein vollwertiger ``Channel``:
  dort wirken Realtime-Zeichen, ``?`` liefert einen Statusbericht, und GCode
  wird ausgeführt.

  Über HTTP geht davon nichts. ``/command?plain=`` reicht die Zeile an
  ``settings_execute_line()`` weiter, das das erste Zeichen wegwirft und nur
  ``$name=wert`` versteht. Ein ``?`` oder ein Realtime-Byte landet dort beim
  namenlosen Hilfe-Kommando und wird mit **HTTP 200 quittiert, ohne dass
  irgendetwas passiert** — der Not-Halt meldete so jahrelang Erfolg. Für
  ``$``-Kommandos bleibt der Weg gangbar und dient hier als Rückfallebene,
  falls Telnet abgeschaltet wurde.

* **Pause, Weiter und Reset** haben eigene HTTP-Endpunkte
  (``/feedhold_reload``, ``/cyclestart_reload``, ``/restart_reload``). Die
  lösen direkt das Firmware-Ereignis aus, brauchen keinen Kanal und werden
  auch nicht von ``$HTTP/BlockDuringMotion`` blockiert — also genau das, was
  ein Halt können muss.
"""

from __future__ import annotations

import re
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .config import FluidNCConfig

__all__ = [
    "CYCLE_START",
    "FEED_HOLD",
    "JOG_CANCEL",
    "SOFT_RESET",
    "FluidNCError",
    "FluidNCRejected",
    "FluidNCClient",
    "MachineStatus",
    "TelnetChannel",
    "parse_status",
    "upload_and_run",
]

_STATUS_RE = re.compile(r"<([^>]*)>")

# GRBL-Realtime-Bytes. Sie gehen als einzelnes Byte über den TCP-Kanal hinaus;
# über HTTP wären sie wirkungslos (siehe Modul-Docstring).
FEED_HOLD = 0x21
CYCLE_START = 0x7E
SOFT_RESET = 0x18
JOG_CANCEL = 0x85

REALTIME_TIMEOUT_S = 5.0
"""Ein Not-Halt darf nicht so lange warten dürfen wie ein Datei-Upload."""

_OK_RE = re.compile(r"^ok$", re.IGNORECASE)
_ERROR_RE = re.compile(r"^(error:.*|ALARM:.*)$", re.IGNORECASE)


class FluidNCError(RuntimeError):
    """Kommunikation mit dem Board fehlgeschlagen."""


class FluidNCRejected(FluidNCError):
    """Das Board hat geantwortet — mit ``error:`` oder ``ALARM:``.

    Eigene Klasse, weil der Unterschied folgenreich ist: Ein Kanal, der nicht
    aufgeht, darf über HTTP nachgereicht werden. Eine **Ablehnung** darf das
    nicht — dann hat das Board die Frage schon beantwortet, und die Antwort war
    nein. Sie über den zweiten Weg noch einmal zu stellen und den Erfolg zu
    melden, wäre genau die Sorte stiller Fehlschlag, gegen die dieses Modul
    geschrieben ist.
    """


@contextmanager
def _as_fluidnc_error(message: str):
    """Netzwerkfehler in FluidNCError übersetzen.

    Ein nicht erreichbares Board ist der Normalfall, nicht die Ausnahme — die
    Oberfläche soll das melden können, statt an einer requests-Ausnahme
    hängenzubleiben.
    """
    try:
        yield
    except FluidNCError:
        raise
    except Exception as exc:
        raise FluidNCError(f"{message}: {exc}") from exc


@dataclass(frozen=True)
class MachineStatus:
    """Ausgewertete Antwort auf eine ``?``-Statusabfrage."""

    state: str
    """z. B. ``Idle``, ``Run``, ``Hold:0``, ``Alarm``."""

    position: tuple[float, float, float] | None = None
    """Position in **Werkstückkoordinaten**, siehe :func:`parse_status`."""

    machine_position: tuple[float, float, float] | None = None
    """Position in Maschinenkoordinaten, falls bestimmbar."""

    work_offset: tuple[float, float, float] | None = None
    """``WCO`` aus dem Bericht: Maschinen- minus Werkstückkoordinaten."""

    sd_percent: float | None = None
    sd_file: str | None = None
    sd_done: bool = False
    """``True``, wenn das Board das Programmende gemeldet hat (``SD: …: Sent``)."""

    raw: str = ""

    @property
    def is_running(self) -> bool:
        return self.state.split(":")[0] in {"Run", "Hold", "Jog"}


def parse_status(raw: str) -> MachineStatus:
    """FluidNC-Statuszeile parsen.

    Beispiel: ``<Run|MPos:12.000,3.000,0.000|FS:1500,0|WCO:5.000,0.000,0.000|SD:42.30,/wand.gcode>``

    Zwei Dinge, die hier früher zusammenfielen und es nicht dürfen:

    * **``MPos`` und ``WPos`` sind nicht dasselbe.** Welches von beiden das
      Board schickt, hängt an ``$10`` (Vorgabe 1 = ``MPos``). Der erzeugte
      GCode läuft aber immer in *Werkstück*-Koordinaten. Sobald ein ``G92``-
      oder ``G54``-Versatz gesetzt ist, liegen beide auseinander — und eine
      Kalibrierung, die das eine aufnimmt und das andere anfährt, verschiebt
      die ganze Zeichnung. :attr:`MachineStatus.position` ist deshalb immer
      die Werkstückkoordinate, umgerechnet über ``WCO``, wenn nötig.
    * **Der Fortschritt hat zwei Formate.** Während des Laufs
      ``SD:<prozent>,<pfad>``, am Ende ``SD: <name>: Sent``. Letzteres ist
      nicht „kein Fortschritt", sondern „fertig".
    """
    match = _STATUS_RE.search(raw)
    if not match:
        raise FluidNCError(f"Unerwartete Statusantwort: {raw!r}")

    fields = match.group(1).split("|")
    state = fields[0]
    machine_position: tuple[float, float, float] | None = None
    work_position: tuple[float, float, float] | None = None
    work_offset: tuple[float, float, float] | None = None
    sd_percent: float | None = None
    sd_file: str | None = None
    sd_done = False

    def triple(value: str) -> tuple[float, float, float] | None:
        try:
            numbers = [float(part) for part in value.split(",")[:3]]
        except ValueError:
            return None
        while len(numbers) < 3:
            numbers.append(0.0)
        return (numbers[0], numbers[1], numbers[2])

    for field in fields[1:]:
        key, _, value = field.partition(":")
        if key == "MPos" and machine_position is None:
            machine_position = triple(value)
        elif key == "WPos" and work_position is None:
            work_position = triple(value)
        elif key == "WCO" and work_offset is None:
            work_offset = triple(value)
        elif key == "SD":
            # "SD:42.30,/wand.gcode" während des Laufs, "SD: wand.gcode: Sent" am Ende
            if value.rstrip().endswith("Sent"):
                sd_done = True
                sd_percent = 100.0
                name = value.split(":")[0].strip()
                sd_file = name or None
                continue
            percent, _, name = value.partition(",")
            try:
                sd_percent = float(percent)
            except ValueError:
                sd_percent = None
            sd_file = name or None

    if work_position is None and machine_position is not None and work_offset is not None:
        work_position = tuple(m - o for m, o in zip(machine_position, work_offset, strict=True))
    if machine_position is None and work_position is not None and work_offset is not None:
        machine_position = tuple(w + o for w, o in zip(work_position, work_offset, strict=True))

    return MachineStatus(
        state=state,
        position=work_position if work_position is not None else machine_position,
        machine_position=machine_position,
        work_offset=work_offset,
        sd_percent=sd_percent,
        sd_file=sd_file,
        sd_done=sd_done,
        raw=match.group(0),
    )


class TelnetChannel:
    """Roher TCP-Kanal zu FluidNC — der einzige Weg für GCode und Realtime.

    FluidNC meldet einen Telnet-Client als vollwertigen ``Channel`` an
    (``TelnetServer::poll`` → ``allChannels.registration``), ohne
    Telnet-Optionsverhandlung: es ist ein reiner Bytestrom. Deshalb reicht ein
    Socket aus der Standardbibliothek, und es kommt keine Abhängigkeit dazu.

    ``opener`` ist injizierbar (Signatur wie :func:`socket.create_connection`),
    damit sich das ohne Board testen lässt.
    """

    def __init__(
        self,
        host: str,
        port: int = 23,
        timeout_s: float = 5.0,
        opener: Any = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._opener = opener or socket.create_connection
        self._socket: Any = None
        # Schützt nur die "Unterhaltungen", die auf eine Antwort warten
        # (send_line, status) — beide lesen aus demselben _buffer, und ohne
        # Sperre könnte eine zweite, gleichzeitig laufende Anfrage (die
        # Web-UI cacht den Client je Host/Zeitlimit, mehrere Bedienelemente
        # teilen sich also denselben Kanal) Zeilen der ersten abbekommen.
        # send_realtime() nimmt diese Sperre bewusst NICHT: Ein Not-Halt oder
        # Jog-Abbruch darf nicht erst warten, bis eine andere Anfrage ihre
        # Antwort fertig eingesammelt hat.
        self._lock = threading.Lock()

    # -- Verbindung -------------------------------------------------------

    def connect(self) -> Any:
        if self._socket is None:
            with _as_fluidnc_error(f"Kein TCP-Kanal zu {self.host}:{self.port}"):
                self._socket = self._opener((self.host, self.port), self.timeout_s)
            self._buffer = b""
        return self._socket

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def __enter__(self) -> TelnetChannel:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- Lesen und Schreiben ----------------------------------------------

    def _read_line(self, deadline_s: float) -> str | None:
        """Eine Zeile lesen; ``None``, wenn innerhalb der Frist keine kam."""
        sock = self.connect()
        buffer = getattr(self, "_buffer", b"")
        while b"\n" not in buffer:
            sock.settimeout(deadline_s)
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                self._buffer = buffer
                return None
            except OSError as exc:
                self._buffer = b""
                # Der Socket ist jetzt kaputt, nicht nur gerade langsam — anders
                # als beim TimeoutError oben, wo der nächste recv() noch etwas
                # bringen kann. Ohne close() bliebe er in self._socket stehen,
                # und connect() gäbe ihn immer wieder zurück, statt neu zu
                # verbinden: Ein einziger Verbindungsabbruch legte den Kanal
                # dann für den Rest des Prozesses lahm — jede Statusabfrage
                # danach hätte "antwortet nicht" gemeldet, ganz gleich, was am
                # Netzwerk oder am Board repariert wurde.
                self.close()
                raise FluidNCError(f"Kanal zu {self.host} abgerissen: {exc}") from exc
            if not chunk:
                self._buffer = buffer
                return None
            buffer += chunk
        line, _, rest = buffer.partition(b"\n")
        self._buffer = rest
        return line.decode("utf-8", errors="replace").strip("\r")

    def _write(self, payload: bytes) -> None:
        sock = self.connect()
        with _as_fluidnc_error(f"Senden an {self.host} fehlgeschlagen"):
            try:
                sock.settimeout(self.timeout_s)
                sock.sendall(payload)
            except OSError:
                # Derselbe Grund wie in _read_line: ein Schreibabriss macht den
                # Socket unbrauchbar, und ohne close() reichte connect() ihn
                # beim nächsten Versuch trotzdem wieder heraus.
                self.close()
                raise

    def send_line(self, line: str, expect_ok: bool = True) -> str:
        """Eine Zeile senden und die Antwort bis ``ok``/``error:`` einsammeln.

        FluidNC quittiert jede Zeile mit ``ok`` oder ``error:<n>``; alles, was
        davor kommt, ist die eigentliche Ausgabe. Ein ``error:`` wird zur
        Ausnahme — stillschweigend „Erfolg" zu melden war genau der Fehler,
        den der HTTP-Weg gemacht hat.
        """
        with self._lock:
            self._write(line.rstrip("\n").encode("utf-8") + b"\n")
            if not expect_ok:
                return ""
            collected: list[str] = []
            while True:
                answer = self._read_line(self.timeout_s)
                if answer is None:
                    raise FluidNCError(
                        f"Keine Quittung auf {line!r} innerhalb von {self.timeout_s:.0f} s"
                    )
                if _OK_RE.match(answer):
                    return "\n".join(collected)
                if _ERROR_RE.match(answer):
                    raise FluidNCRejected(f"{line!r} abgelehnt: {answer}")
                collected.append(answer)

    def send_realtime(self, byte: int) -> None:
        """Ein Realtime-Byte schicken — Halt, Weiter, Reset, Jog-Abbruch.

        Realtime-Zeichen werden von FluidNC sofort ausgewertet, noch bevor
        eine Zeile vollständig ist, und **nicht** quittiert. Wer hier auf ein
        ``ok`` wartet, wartet vergebens.
        """
        if not 0 <= byte <= 0xFF:
            raise FluidNCError("Ein Realtime-Byte liegt zwischen 0 und 255")
        self._write(bytes([byte]))

    def status(self) -> MachineStatus:
        """``?`` senden und den Statusbericht abholen."""
        with self._lock:
            self.send_realtime(ord("?"))
            deadline = min(self.timeout_s, REALTIME_TIMEOUT_S)
            while True:
                line = self._read_line(deadline)
                if line is None:
                    raise FluidNCError(f"{self.host} antwortet nicht auf die Statusabfrage")
                if _STATUS_RE.search(line):
                    return parse_status(line)


class FluidNCClient:
    """Client für ein FluidNC-Board — HTTP für Dateien, TCP für Kommandos.

    ``session`` (alles mit ``get``/``post`` wie ``requests``) und ``channel``
    (etwas mit der Schnittstelle von :class:`TelnetChannel`) sind injizierbar
    — das hält die Klasse testbar, ohne ein Board im Netz.
    """

    def __init__(
        self,
        config: FluidNCConfig | None = None,
        session: Any = None,
        channel: Any = None,
    ) -> None:
        self.config = config or FluidNCConfig()
        self._session = session
        self._channel = channel

    @property
    def session(self) -> Any:
        if self._session is None:
            try:
                import requests  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover
                raise FluidNCError(
                    "requests ist nicht installiert — `pip install -e .`"
                ) from exc
            self._session = requests.Session()
        return self._session

    @property
    def channel(self) -> Any:
        if self._channel is None:
            self._channel = TelnetChannel(
                self.config.hostname,
                self.config.telnet_port,
                min(self.config.timeout_s, REALTIME_TIMEOUT_S * 2),
            )
        return self._channel

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()

    # -- Kommandos --------------------------------------------------------

    def send_command(self, command: str) -> str:
        """Ein Kommando ans Board schicken.

        Der Weg hängt am Kommando, und das ist keine Feinheit:

        * ``$…`` und ``[ESP…]`` versteht auch der HTTP-Endpunkt. Er dient hier
          als Rückfallebene, falls der TCP-Kanal nicht aufgeht (Telnet
          abgeschaltet, Port belegt).
        * Alles andere — GCode, ``?``, Realtime — geht **ausschließlich** über
          den Kanal. Über HTTP käme es nicht durch, würde aber mit HTTP 200
          quittiert.
        """
        stripped = command.strip()
        is_setting = stripped.startswith(("$", "[ESP"))
        try:
            return self.channel.send_line(stripped)
        except FluidNCRejected:
            # Das Board hat geantwortet, und zwar mit nein. Dieselbe Frage über
            # den zweiten Weg noch einmal zu stellen, änderte an der Antwort
            # nichts — es verstecke sie nur.
            raise
        except FluidNCError:
            if not is_setting:
                raise
            return self._http_setting(stripped)

    def _http_setting(self, command: str) -> str:
        """``$``-Kommando über ``/command?plain=`` — nur als Rückfallebene."""
        with _as_fluidnc_error(f"Kommando {command!r} fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}/command",
                params={"plain": command},
                timeout=self.config.timeout_s,
            )
        return self._text_or_raise(response, f"Kommando {command!r} fehlgeschlagen")

    def send_realtime(self, byte: int) -> None:
        """Realtime-Byte über den Kanal — der einzige Weg, auf dem es wirkt."""
        self.channel.send_realtime(byte)

    # -- Dateien ----------------------------------------------------------

    def upload(self, data: bytes | str, filename: str) -> str:
        """Datei auf die µSD-Karte des Boards laden.

        Endpunkt ist ``POST /upload``. Der Dateiname im Multipart-Teil trägt
        den **vollen Zielpfad** — FluidNC legt die Datei genau dort ab, und
        nur dann passt er zum Größenfeld ``<pfad>S``, über das die Firmware
        vorab prüft, ob der Platz reicht.
        """
        payload = data.encode("utf-8") if isinstance(data, str) else data
        remote_dir = self._remote_dir()
        remote_path = f"{remote_dir}{filename}"

        with _as_fluidnc_error(f"Upload von {filename!r} fehlgeschlagen"):
            response = self.session.post(
                f"{self.config.base_url}/upload",
                params={"path": remote_dir},
                data={f"{remote_path}S": str(len(payload))},
                files={"file": (remote_path, payload, "text/plain")},
                timeout=self.config.timeout_s,
            )
        self._upload_geglueckt(
            self._text_or_raise(response, f"Upload von {filename!r} fehlgeschlagen"),
            f"Upload von {filename!r} fehlgeschlagen",
        )
        return remote_path

    def upload_local(self, data: bytes | str, remote_path: str = "/config.yaml") -> str:
        """Datei in den **Flash** des Boards schreiben — dorthin, wo die config.yaml liegt.

        Nicht dasselbe wie :meth:`upload`: Die SD-Karte trägt die GCode-Dateien,
        die Konfiguration liest FluidNC dagegen aus dem lokalen Dateisystem.
        Belegt ist das an zwei Stellen im Quelltext:

        * ``Machine/MachineConfig.cpp:282`` öffnet sie als
          ``FileStream file(filename, "rb", LocalFS)`` — der Name kommt aus der
          Einstellung ``$Config/Filename``, ab Werk ``config.yaml``
          (``SettingsDefinitions.cpp:98``).
        * ``WebUI/WebUIServer.cpp:365`` hängt genau dafür einen eigenen
          Endpunkt ein: ``_webserver->on("/files", HTTP_ANY, handleFileList,
          LocalFSFileupload)``. Der Endpunkt ``/upload`` daneben (Zeile 373)
          schreibt auf die SD-Karte.

        Die Form des Multipart ist bei beiden dieselbe (``fileUpload()``,
        Zeile 1032): Der Dateiname trägt den vollen Zielpfad, und ein
        Formularfeld ``<pfad>S`` nennt die Größe, über die die Firmware vorab
        prüft, ob der Platz reicht.

        Wirksam wird die neue Datei erst nach einem Neustart — siehe
        :meth:`restart`.
        """
        payload = data.encode("utf-8") if isinstance(data, str) else data
        path = remote_path if remote_path.startswith("/") else f"/{remote_path}"
        directory = path.rsplit("/", 1)[0] + "/"

        with _as_fluidnc_error(f"Schreiben von {path!r} in den Flash fehlgeschlagen"):
            response = self.session.post(
                f"{self.config.base_url}/files",
                params={"path": directory},
                data={f"{path}S": str(len(payload))},
                files={"file": (path, payload, "text/plain")},
                timeout=self.config.timeout_s,
            )
        self._upload_geglueckt(
            self._text_or_raise(response, f"Schreiben von {path!r} in den Flash fehlgeschlagen"),
            f"Schreiben von {path!r} in den Flash fehlgeschlagen",
        )
        return path

    def download_local(self, remote_path: str = "/config.yaml") -> str:
        """Datei aus dem Flash zurücklesen — die Gegenprobe zu :meth:`upload_local`.

        Einen eigenen Endpunkt gibt es dafür nicht; der Webserver liefert
        Dateien aus dem Flash über den Nicht-gefunden-Zweig aus
        (``WebUI/WebUIServer.cpp:687`` ruft ``myStreamFile(request, path, true)``
        auf, und das öffnet ``FluidPath { path, LocalFS }``). ``GET /config.yaml``
        liefert also genau die Datei, die das Board beim Start eingelesen hat.

        Währenddessen darf die Maschine nicht fahren: Ist ``$HTTP/BlockDuringMotion``
        an — ab Werk ist es das —, weist der Server die Anfrage bei Bewegung ab,
        damit das Lesen aus dem Flash die Schrittausgabe nicht ins Stocken bringt.
        """
        path = remote_path if remote_path.startswith("/") else f"/{remote_path}"
        with _as_fluidnc_error(f"Lesen von {path!r} aus dem Flash fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}{path}", timeout=self.config.timeout_s
            )
        return self._text_or_raise(response, f"Lesen von {path!r} aus dem Flash fehlgeschlagen")

    def restart(self) -> str:
        """Board neu starten, damit es die Konfiguration neu einliest.

        ``$Bye`` — ``FileCommands.cpp:701`` meldet es als
        ``new WebCommand("RESTART", WEBCMD, WA, NULL, "Bye", restart)`` an.

        Die Antwort ist unzuverlässig: Das Board fährt herunter, während sie
        noch unterwegs ist. Ein Abbruch der Verbindung ist hier deshalb kein
        Fehler, sondern der Normalfall — nur weitergemeldet wird er trotzdem,
        damit niemand einen ausgebliebenen Neustart für erledigt hält.
        """
        try:
            return self.send_command("$Bye")
        except FluidNCRejected:
            # Das Board läuft weiter und liest die neue Datei nicht ein. Das
            # als „Neustart angestoßen" zu melden, wäre schlicht falsch.
            raise
        except FluidNCError as exc:
            # Die Verbindung bricht ab, WEIL das Board neu startet — Normalfall.
            return f"Neustart angestoßen; das Board hat nicht mehr geantwortet ({exc})"

    def _remote_dir(self) -> str:
        """``config.remote_dir`` mit Schrägstrich am Ende."""
        return (
            self.config.remote_dir
            if self.config.remote_dir.endswith("/")
            else self.config.remote_dir + "/"
        )

    def download(self, remote_path: str) -> str:
        """Datei von der Karte lesen (``GET /sd/<pfad>``, WebDAV-Zweig).

        Wie bei :meth:`upload`: Ein Name ohne führenden Schrägstrich wird
        relativ zu ``config.remote_dir`` aufgelöst. Ohne das las ``download``
        immer ab Kartenwurzel — bei einem ``remote_dir`` ungleich ``/`` griff
        ein ``push`` (schreibt unter ``remote_dir``) an einer anderen Stelle
        als das folgende ``pull`` (las immer ab ``/``).
        """
        path = remote_path if remote_path.startswith("/") else f"{self._remote_dir()}{remote_path}"
        with _as_fluidnc_error(f"Lesen von {path!r} fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}/sd{path}", timeout=self.config.timeout_s
            )
        return self._text_or_raise(response, f"Lesen von {path!r} fehlgeschlagen")

    def list_files(self, directory: str = "/") -> str:
        """Verzeichnis der Karte auflisten (JSON-Antwort als Text).

        ``GET /upload?path=…`` — eine Liste kommt ohne weitere Parameter
        zurück; ``action`` kennt die Firmware nur zusammen mit ``filename``
        und nur für Löschen, Anlegen und Umbenennen.
        """
        with _as_fluidnc_error("Dateiliste fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}/upload",
                params={"path": directory},
                timeout=self.config.timeout_s,
            )
        return self._text_or_raise(response, "Dateiliste fehlgeschlagen")

    def run_file(self, remote_path: str) -> str:
        """Datei von der SD-Karte abspielen lassen."""
        return self.send_command(f"$SD/Run={remote_path}")

    # -- Jobsteuerung -----------------------------------------------------

    def status(self) -> MachineStatus:
        return self.channel.status()

    def pause(self) -> str:
        """Feed Hold — die Maschine bremst kontrolliert ab.

        Über ``GET /feedhold_reload``: der Handler löst direkt
        ``protocol_send_event(&feedHoldEvent)`` aus, braucht keinen Kanal und
        wird auch nicht von ``$HTTP/BlockDuringMotion`` blockiert.
        """
        return self._trigger("/feedhold_reload", "Pause")

    def resume(self) -> str:
        """Cycle Start — weiter nach einem Feed Hold oder einer ``M0``-Pause."""
        return self._trigger("/cyclestart_reload", "Fortsetzen")

    def stop(self) -> str:
        """Soft-Reset — bricht einen laufenden SD-Job ab.

        Danach ist ein per ``G92`` gesetzter Nullpunkt weg; zum Weiterplotten
        erst die kalibrierte Ecke anfahren, dann
        :func:`wallplotter.resume.resume_program`.
        """
        return self._trigger("/restart_reload", "Stopp")

    def _trigger(self, endpoint: str, what: str) -> str:
        """Einen der Ereignis-Endpunkte auslösen.

        Sie antworten mit einem Redirect (302 auf ``/`` bzw. ``/did_restart``)
        — das ist Erfolg, kein Fehler, deshalb wird der Umleitung nicht
        gefolgt und 3xx als gut gewertet.
        """
        with _as_fluidnc_error(f"{what} fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}{endpoint}",
                timeout=min(self.config.timeout_s, REALTIME_TIMEOUT_S),
                allow_redirects=False,
            )
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            raise FluidNCError(f"{what} fehlgeschlagen (HTTP {status_code})")
        return getattr(response, "text", "")

    # -- Jog & Kalibrierung -----------------------------------------------

    def jog(self, dx: float = 0.0, dy: float = 0.0, feed: float = 1000.0) -> str:
        """Relativ verfahren (GRBL-Jog).

        ``$J=`` läuft an der Jobsteuerung vorbei und lässt sich mit
        :meth:`jog_cancel` sauber abbrechen — anders als ein normales ``G1``.
        """
        if dx == 0 and dy == 0:
            raise FluidNCError("Jog ohne Weg")
        if feed <= 0:
            raise FluidNCError("Jog braucht einen Vorschub > 0")
        return self.send_command(f"$J=G91 G21 X{dx:.3f} Y{dy:.3f} F{feed:.0f}")

    def jog_to(self, x: float, y: float, feed: float = 1000.0) -> str:
        """Absolut verfahren, ohne den Stift abzusetzen."""
        return self.send_command(f"$J=G90 G21 X{x:.3f} Y{y:.3f} F{feed:.0f}")

    def jog_cancel(self) -> None:
        """Laufende Jog-Bewegung abbrechen (Realtime-Byte 0x85).

        Nur über den Kanal — dafür gibt es keinen HTTP-Endpunkt.
        """
        self.send_realtime(JOG_CANCEL)

    def set_work_offset(self, x: float = 0.0, y: float = 0.0, system: int = 1) -> str:
        """Der aktuellen Position feste Koordinaten geben — **dauerhaft**.

        ``G10 L20`` verschiebt das Werkstück-Koordinatensystem (G54 bei
        ``system=1``) so, dass die aktuelle Position die genannten Koordinaten
        bekommt. Anders als ``G92`` liegt das im NVS des ESP32 und übersteht
        Aus- und Einschalten.

        Der Haken liegt woanders: Die WallPlotter-Kinematik kann nicht
        referenzieren (``canHome()`` gibt ``false`` zurück, ``$H`` scheitert),
        und FluidNC friert den Maschinennullpunkt **beim Booten** auf die
        Gondelposition ein. Dauerhaft nützt ein Versatz deshalb nur zusammen
        mit einer reproduzierbaren mechanischen Referenz: Gondel an den
        Anschlag, Board dort neu starten.
        """
        if not 1 <= system <= 6:
            raise FluidNCError("Koordinatensystem 1..6 (G54..G59)")
        return self.send_command(f"G10 L20 P{system} X{x:.3f} Y{y:.3f}")

    def work_offsets(self) -> str:
        """Gespeicherte Versätze abfragen (``$#``)."""
        return self.send_command("$#")

    def set_zero(self, x: float = 0.0, y: float = 0.0) -> str:
        """Der aktuellen Position feste Koordinaten zuweisen (G92).

        ``G92`` überlebt kein Ausschalten und keinen Soft-Reset — es liegt
        nicht im NVS. (Ein ``M2`` verwirft es entgegen früherer Behauptung
        hier **nicht**; das setzt nur die Modalgruppen zurück.) Für alles, was
        länger halten soll, ist :meth:`set_work_offset` das richtige Werkzeug.

        Ohne Argumente ist das der Nullpunkt beim Referenzieren am Anschlag.
        Mit Argumenten lässt sich ein verlorener Nullpunkt wiederherstellen:
        eine kalibrierte Ecke anfahren und ihre gespeicherten Koordinaten
        setzen.
        """
        return self.send_command(f"G92 X{x:.3f} Y{y:.3f}")

    def position(self) -> tuple[float, float]:
        """Aktuelle XY-Position in **Werkstückkoordinaten**.

        Das ist dasselbe System, in dem der erzeugte GCode fährt — und der
        Grund, warum hier nicht einfach ``MPos`` durchgereicht wird.
        """
        machine = self.status()
        if machine.position is None:
            raise FluidNCError(f"Status ohne Position: {machine.raw}")
        return (machine.position[0], machine.position[1])

    # -- intern -----------------------------------------------------------

    @staticmethod
    def _text_or_raise(response: Any, message: str) -> str:
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            raise FluidNCError(f"{message} (HTTP {status_code})")
        return getattr(response, "text", "")

    @staticmethod
    def _upload_geglueckt(antwort: str, message: str) -> str:
        """Den Rumpf einer Upload-Antwort ansehen — HTTP 200 heißt hier nichts.

        ``handleFileOps()`` antwortet auch im Fehlerfall mit **HTTP 200** und
        trägt das Ergebnis nur in den JSON-Rumpf ein::

            std::string sstatus("Ok");
            if (_upload_status == UploadStatus::FAILED) {
                sstatus = "Upload failed";
            }

        (``WebUI/WebUIServer.cpp:1220-1224``, ausgeliefert über
        ``create_file_list_response``). Wer nur den Statuscode ansieht, meldet
        „geschrieben" und startet ein Board neu, in dessen Flash unverändert
        die alte Datei liegt — derselbe Fehlertyp, den dieses Modul an anderer
        Stelle anprangert.
        """
        if "upload failed" in antwort.lower():
            raise FluidNCError(
                f"{message}: Das Board meldet »Upload failed« (mit HTTP 200). "
                "Häufigste Gründe: kein Platz oder die Datei ließ sich nicht anlegen."
            )
        return antwort


def upload_and_run(
    gcode: str | bytes,
    filename: str = "plot.gcode",
    config: FluidNCConfig | None = None,
    *,
    run: bool = True,
    client: FluidNCClient | None = None,
) -> str:
    """GCode hochladen und optional direkt starten. Gibt den Remote-Pfad zurück."""
    active = client or FluidNCClient(config)
    remote_path = active.upload(gcode, filename)
    if run:
        active.run_file(remote_path)
    return remote_path
