"""HTTP-Anbindung an FluidNC: Datei-Upload, Job starten, Status pollen.

Die Endpunkte entsprechen dem ESP3D-basierten Webserver von FluidNC
(``/upload`` für Dateien, ``/command?plain=…`` für GRBL-Kommandos). Sie sind
gegen die eigene Firmware-Version zu prüfen, sobald das Board läuft — deshalb
sind Pfad und Endpunkt hier konfigurierbar statt fest verdrahtet.
"""

from __future__ import annotations

import re
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
    "FluidNCClient",
    "MachineStatus",
    "parse_status",
    "upload_and_run",
]

_STATUS_RE = re.compile(r"<([^>]*)>")

# GRBL-Realtime-Bytes. Als Zahlen, nicht als Zeichen: sie gehen als
# Prozent-Escape auf die Leitung, und ein Zeichen jenseits von ASCII würde
# unterwegs zu UTF-8 werden (siehe FluidNCClient.send_realtime).
FEED_HOLD = 0x21
CYCLE_START = 0x7E
SOFT_RESET = 0x18
JOG_CANCEL = 0x85

REALTIME_TIMEOUT_S = 5.0
"""Ein Not-Halt darf nicht so lange warten dürfen wie ein Datei-Upload."""


class FluidNCError(RuntimeError):
    """Kommunikation mit dem Board fehlgeschlagen."""


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
    """Ausgewertete Antwort auf ein ``?``-Statusabfrage."""

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
    """Dünner Client um die FluidNC-Web-API.

    ``session`` ist injizierbar (alles, was ``get``/``post`` wie ``requests``
    anbietet) — das hält die Klasse testbar, ohne ein Board im Netz.
    """

    def __init__(self, config: FluidNCConfig | None = None, session: Any = None) -> None:
        self.config = config or FluidNCConfig()
        self._session = session

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

    # -- Basisoperationen -------------------------------------------------

    def send_command(self, command: str) -> str:
        """GRBL-/FluidNC-Kommando senden und die Antwort als Text liefern."""
        with _as_fluidnc_error(f"Kommando {command!r} fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}/command",
                params={"plain": command},
                timeout=self.config.timeout_s,
            )
        return self._text_or_raise(response, f"Kommando {command!r} fehlgeschlagen")

    def send_realtime(self, byte: int) -> str:
        """Ein Realtime-Byte schicken — Halt, Weiter, Reset, Jog-Abbruch.

        Realtime-Bytes sind keine Kommandos: GRBL wertet sie sofort aus, noch
        bevor eine Zeile im Puffer steht. Zwei Gründe, warum sie hier einen
        eigenen Weg brauchen statt über :meth:`send_command` zu laufen:

        * **Kodierung.** ``requests`` kodiert Zeichen jenseits von ASCII als
          UTF-8, wenn sie durch ``params=`` gehen. Aus dem Jog-Abbruch ``0x85``
          würde ``0xC2 0x85``, also zwei Bytes — das Board sähe nie das Byte,
          auf das es wartet. Die Escape-Sequenz wird deshalb selbst gebaut.
        * **Zeit.** Ein Not-Halt darf nicht dieselbe Geduld haben wie ein
          Datei-Upload; hier gilt ein kurzes, festes Zeitlimit.
        """
        if not 0 <= byte <= 0xFF:
            raise FluidNCError("Ein Realtime-Byte liegt zwischen 0 und 255")
        timeout = min(self.config.timeout_s, REALTIME_TIMEOUT_S)
        url = f"{self.config.base_url}/command?plain=%{byte:02X}"
        with _as_fluidnc_error(f"Realtime-Byte 0x{byte:02X} fehlgeschlagen"):
            response = self.session.get(url, timeout=timeout)
        return self._text_or_raise(response, f"Realtime-Byte 0x{byte:02X} fehlgeschlagen")

    def upload(self, data: bytes | str, filename: str) -> str:
        """Datei auf die µSD-Karte des Boards laden.

        Endpunkt ist ``/sdfiles`` — ``/upload`` schreibt in ESP3D v3 auf den
        Flash-Speicher des ESP32, nicht auf die Karte.
        """
        payload = data.encode("utf-8") if isinstance(data, str) else data
        remote_dir = self.config.remote_dir if self.config.remote_dir.endswith("/") else self.config.remote_dir + "/"
        remote_path = f"{remote_dir}{filename}"

        with _as_fluidnc_error(f"Upload von {filename!r} fehlgeschlagen"):
            response = self.session.post(
                f"{self.config.base_url}/sdfiles",
                params={"path": remote_dir, "createPath": "yes"},
                data={f"{remote_path}S": str(len(payload))},
                files={remote_path: (filename, payload, "text/plain")},
                timeout=self.config.timeout_s,
            )
        self._text_or_raise(response, f"Upload von {filename!r} fehlgeschlagen")
        return remote_path

    def download(self, remote_path: str) -> str:
        """Datei von der Karte lesen (``GET /sd/<pfad>``)."""
        path = remote_path if remote_path.startswith("/") else f"/{remote_path}"
        with _as_fluidnc_error(f"Lesen von {path!r} fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}/sd{path}", timeout=self.config.timeout_s
            )
        return self._text_or_raise(response, f"Lesen von {path!r} fehlgeschlagen")

    def list_files(self, directory: str = "/") -> str:
        """Verzeichnis der Karte auflisten (JSON-Antwort als Text)."""
        with _as_fluidnc_error("Dateiliste fehlgeschlagen"):
            response = self.session.get(
                f"{self.config.base_url}/sdfiles",
                params={"path": directory, "action": "list"},
                timeout=self.config.timeout_s,
            )
        return self._text_or_raise(response, "Dateiliste fehlgeschlagen")

    def run_file(self, remote_path: str) -> str:
        """Datei von der SD-Karte abspielen lassen."""
        return self.send_command(f"$SD/Run={remote_path}")

    # -- Jobsteuerung (Stufe 6) -------------------------------------------

    def status(self) -> MachineStatus:
        return parse_status(self.send_command("?"))

    def pause(self) -> str:
        """Feed Hold (``!``) — die Maschine bremst kontrolliert ab."""
        return self.send_realtime(FEED_HOLD)

    def resume(self) -> str:
        """Cycle Start (``~``) — weiter nach einem Feed Hold."""
        return self.send_realtime(CYCLE_START)

    def stop(self) -> str:
        """Soft-Reset (Ctrl-X) — bricht einen laufenden SD-Job ab.

        Danach ist ein per ``G92`` gesetzter Nullpunkt weg; zum Weiterplotten
        erst die kalibrierte Ecke anfahren, dann
        :func:`wallplotter.resume.resume_program`.
        """
        return self.send_realtime(SOFT_RESET)

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
