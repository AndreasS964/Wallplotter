"""HTTP-Anbindung an FluidNC: Datei-Upload, Job starten, Status pollen.

Die Endpunkte entsprechen dem ESP3D-basierten Webserver von FluidNC
(``/upload`` für Dateien, ``/command?plain=…`` für GRBL-Kommandos). Sie sind
gegen die eigene Firmware-Version zu prüfen, sobald das Board läuft — deshalb
sind Pfad und Endpunkt hier konfigurierbar statt fest verdrahtet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import FluidNCConfig

__all__ = [
    "FluidNCError",
    "FluidNCClient",
    "MachineStatus",
    "parse_status",
    "upload_and_run",
]

_STATUS_RE = re.compile(r"<([^>]*)>")


class FluidNCError(RuntimeError):
    """Kommunikation mit dem Board fehlgeschlagen."""


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
        response = self.session.get(
            f"{self.config.base_url}/command",
            params={"plain": command},
            timeout=self.config.timeout_s,
        )
        return self._text_or_raise(response, f"Kommando {command!r} fehlgeschlagen")

    def upload(self, data: bytes | str, filename: str) -> str:
        """Datei auf die µSD-Karte des Boards laden."""
        payload = data.encode("utf-8") if isinstance(data, str) else data
        remote_dir = self.config.remote_dir if self.config.remote_dir.endswith("/") else self.config.remote_dir + "/"
        remote_path = f"{remote_dir}{filename}"

        response = self.session.post(
            f"{self.config.base_url}/upload",
            data={"path": remote_dir, f"{remote_path}S": str(len(payload))},
            files={remote_path: (filename, payload, "text/plain")},
            timeout=self.config.timeout_s,
        )
        self._text_or_raise(response, f"Upload von {filename!r} fehlgeschlagen")
        return remote_path

    def run_file(self, remote_path: str) -> str:
        """Datei von der SD-Karte abspielen lassen."""
        return self.send_command(f"$SD/Run={remote_path}")

    # -- Jobsteuerung (Stufe 6) -------------------------------------------

    def status(self) -> MachineStatus:
        return parse_status(self.send_command("?"))

    def pause(self) -> str:
        return self.send_command("!")

    def resume(self) -> str:
        return self.send_command("~")

    def stop(self) -> str:
        """Soft-Reset (Ctrl-X) — bricht einen laufenden SD-Job ab."""
        return self.send_command("\x18")

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
