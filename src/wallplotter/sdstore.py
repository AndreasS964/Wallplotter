"""Projektdaten auf der µSD-Karte des Boards ablegen.

Der Plotter wandert zwischen Wänden — dann sollen Standorte, Flächen­maße und
Korrekturen mitwandern, statt auf einem bestimmten Rechner zu liegen. Die
Karte steckt ohnehin im Board und ist über die Web-API les- und schreibbar.

Was hier *nicht* hingehört: der Maschinennullpunkt. Der lässt sich nicht als
Zahl konservieren, weil er von der Referenzfahrt abhängt — dazu
:meth:`wallplotter.upload.FluidNCClient.set_work_offset`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .location import LocationBook, LocationError
from .upload import FluidNCClient, FluidNCError

__all__ = ["REMOTE_LOCATIONS", "push_locations", "pull_locations", "sync_locations"]

REMOTE_LOCATIONS = "wallplotter-standorte.json"
"""Dateiname auf der Karte. Bewusst sprechend — auf der Karte liegen sonst
nur GCode-Dateien, und wer sie am Rechner einsteckt, soll sehen, was das ist."""


def push_locations(
    book: LocationBook, client: FluidNCClient | None = None, filename: str = REMOTE_LOCATIONS
) -> str:
    """Standorte auf die Karte schreiben."""
    active = client or FluidNCClient()
    payload = json.dumps(
        {
            "active": book.active,
            "locations": [location.to_dict() for location in book.locations.values()],
        },
        indent=2,
        ensure_ascii=False,
    )
    return active.upload(payload, filename)


def pull_locations(
    client: FluidNCClient | None = None, filename: str = REMOTE_LOCATIONS
) -> LocationBook:
    """Standorte von der Karte lesen."""
    active = client or FluidNCClient()
    raw = active.download(filename)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LocationError(f"{filename} auf der Karte ist nicht lesbar: {exc}") from exc

    from .location import Location  # noqa: PLC0415 - zirkulären Import vermeiden

    book = LocationBook(
        locations={
            entry["name"]: Location.from_dict(entry) for entry in data.get("locations", [])
        }
    )
    active_name = data.get("active")
    book.active = active_name if active_name in book.locations else next(iter(book.locations), None)
    return book


def sync_locations(
    local_path: Path | str,
    client: FluidNCClient | None = None,
    filename: str = REMOTE_LOCATIONS,
    direction: str = "push",
) -> str:
    """Lokale Datei und Karte abgleichen.

    Bewusst ohne automatisches Zusammenführen: Zwei Stände, die auseinander
    laufen, kann nur beurteilen, wer weiß, welcher der richtige ist. Deshalb
    ``push`` oder ``pull``, und der jeweils andere Stand wird überschrieben.
    """
    if direction not in ("push", "pull"):
        raise LocationError("Richtung ist 'push' oder 'pull'")
    path = Path(local_path)

    if direction == "push":
        book = LocationBook.load(path)
        if not book.locations:
            raise LocationError(f"{path} enthält keine Standorte")
        remote = push_locations(book, client, filename)
        return f"{len(book.locations)} Standorte → Karte ({remote})"

    book = pull_locations(client, filename)
    book.save(path)
    return f"{len(book.locations)} Standorte ← Karte → {path}"


def describe_remote(client: FluidNCClient | None = None) -> str:
    """Was auf der Karte liegt — Rohantwort der Web-API."""
    active = client or FluidNCClient()
    try:
        return active.list_files("/")
    except FluidNCError as exc:
        return str(exc)
