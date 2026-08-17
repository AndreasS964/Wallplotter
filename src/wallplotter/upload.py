"""Anbindung an FluidNC: Dateien über HTTP, alles Maschinennahe über den Kanal.

Die Endpunkte sind gegen den Quelltext der Firmware geprüft (v3.9.9, v4.0.4
und der Stand von master — in diesen Punkten identisch), nicht gegen die
allgemeine ESP3D-Dokumentation. Das ist ein Unterschied mit Folgen: FluidNC
bringt einen **eigenen** Webserver mit, der nur einen Teil von ESP3D
nachbildet und dabei andere Pfade benutzt.

Die drei Dinge, die man wissen muss:

* ``/upload`` ist die **SD-Karte**, ``/files`` ist der Flash des ESP32.
  Ein ``/sdfiles`` gibt es in FluidNC nicht — dieser Pfad landet im
  404-Handler. (In der ESP3D-Doku steht es andersherum; die beschreibt die
  eigenständige ESP3D-Firmware, nicht den in FluidNC eingebauten Server.)
* ``/command?plain=…`` taugt nur für ``$``-Kommandos. Der Handler ruft
  ``settings_execute_line()`` auf, und die Funktion schneidet das erste
  Zeichen ab. ``G92 X0 Y0`` wird dadurch zur Suche nach einer Einstellung
  namens ``92 X0 Y0``, ``?`` zur Suche nach dem leeren Namen. Kein G-Code,
  kein Statusreport, keine Realtime-Bytes.
* ``/command`` antwortet mit **HTTP 503**, solange die Maschine fährt und
  ``$HTTP/BlockDuringMotion`` steht — und das ist die Voreinstellung.

Deshalb: Für Status, Jog, Nullpunkt und Realtime läuft alles über
:class:`wallplotter.channel.BoardChannel`. Halt, Weiter und Not-Aus haben
zusätzlich eigene HTTP-Endpunkte (``/feedhold_reload`` und Geschwister), die
die Firmware auch während der Fahrt bedient — die bleiben damit auch dann
erreichbar, wenn der Kanal gerade nicht steht.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .channel import (
    CYCLE_START,
    FEED_HOLD,
    JOG_CANCEL,
    SOFT_RESET,
    STATUS_REPORT,
    BoardChannel,
    ChannelError,
)
from .config import FluidNCConfig

__all__ = [
    "CYCLE_START",
    "FEED_HOLD",
    "JOG_CANCEL",
    "SOFT_RESET",
    "STATUS_REPORT",
    "FluidNCError",
    "FluidNCClient",
    "MachineStatus",
    "parse_status",
    "upload_and_run",
]

_STATUS_RE = re.compile(r"<([^>]*)>")

# -- Endpunkte, gegen den Firmware-Quelltext geprüft -------------------------

SD_PATH = "/upload"
"""Datei-Upload **und** Verzeichnis der µSD-Karte (``SDFileUpload`` /
``handle_direct_SDFileList``)."""

FLASH_PATH = "/files"
"""Der Flash-Speicher des ESP32. Steht hier nur, damit klar ist, was er ist —
benutzt wird er nicht: Ein Wandbild gehört auf die Karte."""

COMMAND_PATH = "/command"
FEED_HOLD_PATH = "/feedhold_reload"
CYCLE_START_PATH = "/cyclestart_reload"
RESET_PATH = "/restart_reload"

REALTIME_TIMEOUT_S = 5.0
"""Ein Not-Halt darf nicht so lange warten dürfen wie ein Datei-Upload."""

_BLOCKED_HINT = (
    "Das Board blockiert HTTP-Kommandos während der Fahrt. "
    "Einmalig `$HTTP/BlockDuringMotion=OFF` setzen — oder den Kanal benutzen, "
    "den die Sperre nicht betrifft."
)


class FluidNCError(RuntimeError):
    """Kommunikation mit dem Board fehlgeschlagen."""


@contextmanager
def _as_fluidnc_error(message: str):
    """Netzwerk- und Kanalfehler in FluidNCError übersetzen.

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
    """z. B. ``Idle``, ``Run``, ``Hold``, ``Alarm``."""

    position: tuple[float, float, float] | None = None
    sd_percent: float | None = None
    sd_file: str | None = None
    raw: str = ""

    @property
    def is_running(self) -> bool:
        return self.state.split(":")[0] in {"Run", "Hold", "Jog"}


def parse_status(raw: str) -> MachineStatus:
    """FluidNC-Statuszeile parsen.

    Beispiel: ``<Run|MPos:12.000,3.000,0.000|FS:1500,0|SD:42.30,/wand.gcode>``
    """
    match = _STATUS_RE.search(raw)
    if not match:
        raise FluidNCError(f"Unerwartete Statusantwort: {raw!r}")

    fields = match.group(1).split("|")
    state = fields[0]
    position: tuple[float, float, float] | None = None
    sd_percent: float | None = None
    sd_file: str | None = None

    for field in fields[1:]:
        key, _, value = field.partition(":")
        if key in {"MPos", "WPos"} and position is None:
            try:
                numbers = [float(part) for part in value.split(",")[:3]]
            except ValueError:
                continue
            while len(numbers) < 3:
                numbers.append(0.0)
            position = (numbers[0], numbers[1], numbers[2])
        elif key == "SD":
            percent, _, name = value.partition(",")
            try:
                sd_percent = float(percent)
            except ValueError:
                sd_percent = None
            sd_file = name or None

    return MachineStatus(
        state=state,
        position=position,
        sd_percent=sd_percent,
        sd_file=sd_file,
        raw=match.group(0),
    )


class FluidNCClient:
    """Client um die beiden Wege ins Board.

    ``session`` (alles mit ``get``/``post`` wie bei ``requests``) und
    ``channel`` (alles wie :class:`~wallplotter.channel.BoardChannel`) sind
    injizierbar — das hält die Klasse testbar, ohne ein Board im Netz.
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
        """Der offene Kanal zum Board, bei Bedarf aufgebaut."""
        if self._channel is None:
            self._channel = BoardChannel(
                self.config.host,
                timeout_s=min(self.config.timeout_s, 5.0),
                connect_timeout_s=min(self.config.timeout_s, 5.0),
            )
        if not self._channel.connected:
            with _as_fluidnc_error("Kanal zum Board"):
                self._channel.connect()
        return self._channel

    def close(self) -> None:
        """Den Kanal schließen. Der HTTP-Teil braucht kein Aufräumen."""
        if self._channel is not None:
            self._channel.close()

    def __enter__(self) -> FluidNCClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- Basisoperationen -------------------------------------------------

    def send_command(self, command: str) -> str:
        """Eine Zeile an die Maschine schicken.

        Bevorzugt den Kanal, weil dort auch G-Code ankommt und die
        Bewegungssperre nicht greift. Steht der Kanal nicht, bleibt für
        ``$``-Kommandos der HTTP-Weg; für G-Code gibt es keinen zweiten Weg,
        und dann sagt die Meldung genau das.
        """
        try:
            channel = self.channel
        except FluidNCError as exc:
            if not command.startswith(("$", "[")):
                raise FluidNCError(
                    f"{command!r} braucht den WebSocket-Kanal — über HTTP nimmt "
                    f"FluidNC nur $-Kommandos an ({exc})"
                ) from exc
            return self.send_http_command(command)

        with _as_fluidnc_error(f"Kommando {command!r} fehlgeschlagen"):
            channel.send_line(command)
            return "\n".join(channel.poll(0.3))

    def send_http_command(self, command: str) -> str:
        """Ein ``$``-Kommando über ``/command?plain=`` schicken.

        Nur für ``$``- und ``[ESPxxx]``-Kommandos brauchbar (siehe Modulkopf).
        Bleibt als Rückfallebene und für den Selbsttest erreichbar.
        """
        with _as_fluidnc_error(f"Kommando {command!r} fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}{COMMAND_PATH}",
                params={"plain": command},
                timeout=self.config.timeout_s,
            )
        return self._text_or_raise(response, f"Kommando {command!r} fehlgeschlagen")

    def send_realtime(self, byte: int) -> str:
        """Ein Realtime-Byte schicken — Status, Halt, Weiter, Reset, Jog-Abbruch.

        Realtime-Bytes sind keine Kommandos: Die Firmware wertet sie aus,
        bevor eine Zeile im Puffer steht. Über HTTP führt kein Weg dorthin
        (``/command?plain=%21`` landet in der Kommandotabelle statt beim
        Realtime-Zweig), über den Kanal schon.
        """
        if not 0 <= byte <= 0xFF:
            raise FluidNCError("Ein Realtime-Byte liegt zwischen 0 und 255")
        with _as_fluidnc_error(f"Realtime-Byte 0x{byte:02X} fehlgeschlagen"):
            self.channel.send_realtime(byte)
        return ""

    def upload(self, data: bytes | str, filename: str) -> str:
        """Datei auf die µSD-Karte des Boards laden.

        Endpunkt ist ``/upload``. Das Feld im Multipart heißt wie der volle
        Zielpfad, und daneben steht ein Parameter ``<pfad>S`` mit der
        Bytegröße — die Firmware prüft damit vorher den freien Platz und
        hinterher, ob alles angekommen ist.
        """
        payload = data.encode("utf-8") if isinstance(data, str) else data
        remote_dir = (
            self.config.remote_dir
            if self.config.remote_dir.endswith("/")
            else self.config.remote_dir + "/"
        )
        remote_path = f"{remote_dir}{filename}"

        with _as_fluidnc_error(f"Upload von {filename!r} fehlgeschlagen"):
            response = self.session.post(
                f"{self.config.base_url}{SD_PATH}",
                params={"path": remote_dir},
                data={f"{remote_path}S": str(len(payload))},
                files={remote_path: (filename, payload, "text/plain")},
                timeout=self.config.timeout_s,
            )
        self._text_or_raise(response, f"Upload von {filename!r} fehlgeschlagen")
        return remote_path

    def download(self, remote_path: str) -> str:
        """Datei von der Karte lesen.

        Über ``$SD/Show=<pfad>``, nicht über einen HTTP-Pfad: Der WebDAV-Mount
        ``/sd`` gibt es erst ab FluidNC 4, ``$SD/Show`` in jeder Fassung. Die
        Firmware verlangt dafür Idle oder Alarm — während eines laufenden
        Plots liest hier niemand.
        """
        path = remote_path if remote_path.startswith("/") else f"/{remote_path}"
        text = self.send_command(f"$SD/Show={path}")
        stripped = text.strip()
        if stripped.startswith("error:") or "Bad file" in stripped or "No SD" in stripped:
            raise FluidNCError(f"Lesen von {path!r} fehlgeschlagen: {stripped}")
        return text

    def list_files(self, directory: str = "/") -> str:
        """Verzeichnis der Karte auflisten (JSON-Antwort als Text).

        Die Firmware listet immer, wenn nicht ``dontlist=yes`` danebensteht —
        ein ``action=list`` kennt sie nicht; ``action`` ist dort für Löschen,
        Anlegen und Umbenennen reserviert.
        """
        with _as_fluidnc_error("Dateiliste fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}{SD_PATH}",
                params={"path": directory},
                timeout=self.config.timeout_s,
            )
        return self._text_or_raise(response, "Dateiliste fehlgeschlagen")

    def run_file(self, remote_path: str) -> str:
        """Datei von der SD-Karte abspielen lassen.

        Bewusst über den Kanal: Die Firmware hängt den Job an den Kanal, der
        ihn gestartet hat. Über HTTP wäre das ein Wegwerf-Kanal, der mit der
        Antwort stirbt — die Ausgabe des Jobs ginge ins Leere.
        """
        return self.send_command(f"$SD/Run={remote_path}")

    # -- Jobsteuerung -----------------------------------------------------

    def status(self) -> MachineStatus:
        """Statusreport holen (``?`` über den Kanal)."""
        with _as_fluidnc_error("Statusabfrage fehlgeschlagen"):
            return parse_status(self.channel.request_status(min(self.config.timeout_s, 3.0)))

    def pause(self) -> str:
        """Feed Hold (``!``) — die Maschine bremst kontrolliert ab."""
        return self._hold_command(FEED_HOLD, FEED_HOLD_PATH, "Halt")

    def resume(self) -> str:
        """Cycle Start (``~``) — weiter nach einem Feed Hold.

        Auch der Weg zurück aus einer ``M0``-Pause: FluidNC setzt bei ``M0``
        einen Feed Hold, und aus dem kommt man mit Cycle Start heraus.
        """
        return self._hold_command(CYCLE_START, CYCLE_START_PATH, "Weiter")

    def stop(self) -> str:
        """Soft-Reset (Ctrl-X) — bricht einen laufenden SD-Job ab.

        Danach ist ein per ``G92`` gesetzter Nullpunkt weg; zum Weiterplotten
        erst die kalibrierte Ecke anfahren, dann
        :func:`wallplotter.resume.resume_program`.
        """
        return self._hold_command(SOFT_RESET, RESET_PATH, "Stopp")

    def _hold_command(self, byte: int, path: str, label: str) -> str:
        """Erst über den Kanal, sonst über den eigenen HTTP-Endpunkt.

        Halt, Weiter und Stopp sind die drei Griffe, die auch dann noch
        funktionieren müssen, wenn sonst nichts mehr geht — deshalb zwei Wege.
        Die ``…_reload``-Endpunkte lösen dasselbe Firmware-Ereignis aus und
        werden von der Bewegungssperre nicht angefasst.
        """
        try:
            self.channel.send_realtime(byte)
            return label
        except (FluidNCError, ChannelError):
            pass
        timeout = min(self.config.timeout_s, REALTIME_TIMEOUT_S)
        with _as_fluidnc_error(f"{label} fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}{path}",
                timeout=timeout,
                allow_redirects=False,
            )
        # Die Endpunkte antworten mit einer Umleitung auf die WebUI-Startseite;
        # dass sie überhaupt antworten, ist die Quittung.
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            raise FluidNCError(f"{label} fehlgeschlagen (HTTP {status_code})")
        return label

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

    def jog_cancel(self) -> str:
        """Laufende Jog-Bewegung abbrechen (Realtime-Byte 0x85)."""
        return self.send_realtime(JOG_CANCEL)

    def set_work_offset(self, x: float = 0.0, y: float = 0.0, system: int = 1) -> str:
        """Der aktuellen Position feste Koordinaten geben — **dauerhaft**.

        ``G10 L20`` verschiebt das Werkstück-Koordinatensystem (G54 bei
        ``system=1``) so, dass die aktuelle Position die genannten Koordinaten
        bekommt. Anders als ``G92`` liegt das im NVS des ESP32 und übersteht
        Aus- und Einschalten.

        Der Haken liegt woanders: Ohne Referenzfahrt ist die *Maschinen*-
        position nach dem Einschalten willkürlich, dann zeigt auch ein
        gespeicherter Versatz ins Leere. Dauerhaft nützt das erst zusammen mit
        einer reproduzierbaren Referenz — Anschlag oder StallGuard-Homing.
        """
        if not 1 <= system <= 6:
            raise FluidNCError("Koordinatensystem 1..6 (G54..G59)")
        return self.send_command(f"G10 L20 P{system} X{x:.3f} Y{y:.3f}")

    def work_offsets(self) -> str:
        """Gespeicherte Versätze abfragen (``$#``)."""
        return self.send_command("$#")

    def set_zero(self, x: float = 0.0, y: float = 0.0) -> str:
        """Der aktuellen Position feste Koordinaten zuweisen (G92).

        Achtung: ``G92`` ist laut GRBL-Konvention flüchtig und wird beim
        Programmende verworfen — für alles, was ein Ausschalten oder ein
        ``M2`` überleben soll, ist :meth:`set_work_offset` das richtige
        Werkzeug.

        Ohne Argumente ist das der Nullpunkt beim Referenzieren am Anschlag.
        Mit Argumenten lässt sich ein verlorener Nullpunkt wiederherstellen:
        eine kalibrierte Ecke anfahren und ihre gespeicherten Koordinaten
        setzen. Genau das braucht man, wenn eine mehrfarbige Zeichnung über
        mehrere Tage entsteht und das Board zwischendurch aus war.
        """
        return self.send_command(f"G92 X{x:.3f} Y{y:.3f}")

    def position(self) -> tuple[float, float]:
        """Aktuelle XY-Position in Maschinenkoordinaten."""
        machine = self.status()
        if machine.position is None:
            raise FluidNCError(f"Status ohne Position: {machine.raw}")
        return (machine.position[0], machine.position[1])

    # -- intern -----------------------------------------------------------

    @staticmethod
    def _text_or_raise(response: Any, message: str) -> str:
        status_code = getattr(response, "status_code", 200)
        if status_code == 503:
            raise FluidNCError(f"{message} (HTTP 503). {_BLOCKED_HINT}")
        if status_code >= 400:
            raise FluidNCError(f"{message} (HTTP {status_code})")
        return getattr(response, "text", "")


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
