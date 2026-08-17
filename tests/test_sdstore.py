"""Projektdaten auf der SD-Karte — gegen einen simulierten Kartenspeicher."""

import json

import pytest

from wallplotter.calibration import AreaCalibration
from wallplotter.config import FluidNCConfig
from wallplotter.location import Location, LocationBook, LocationError
from wallplotter.sdstore import REMOTE_LOCATIONS, pull_locations, push_locations, sync_locations
from wallplotter.upload import FluidNCClient


class FakeCard:
    """Speichert, was hochgeladen wird, und gibt es beim Lesen zurück.

    Spielt beide Rollen zugleich: den HTTP-Teil (Schreiben geht über
    ``POST /upload``) und den Kanal (Lesen geht über ``$SD/Show=…``, weil
    FluidNC vor Version 4 keinen HTTP-Pfad zum Kartenspeicher hat).
    """

    def __init__(self, content: str | None = None):
        self.files: dict[str, str] = {}
        if content is not None:
            self.files[f"/{REMOTE_LOCATIONS}"] = content
        self._answer: list[str] = []

    # -- HTTP ---------------------------------------------------------
    def post(self, url, params=None, data=None, files=None, timeout=None):
        (path, (_, payload, _)), = files.items()
        self.files[path] = payload.decode() if isinstance(payload, bytes) else payload
        return type("R", (), {"text": "ok", "status_code": 200})()

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        return type("R", (), {"text": "{}", "status_code": 200})()

    # -- Kanal --------------------------------------------------------
    connected = True

    def connect(self):
        pass

    def close(self):
        pass

    def send_line(self, text):
        if text.startswith("$SD/Show="):
            path = text.split("=", 1)[1]
            # Die Firmware meldet einen Lesefehler als error-Zeile, nicht als
            # HTTP-Status — der Kanal kennt keine Statuscodes.
            self._answer = [self.files.get(path, "error:60 (Bad file)")]

    def poll(self, timeout_s=0.0):
        answer, self._answer = self._answer, []
        return answer


def make_location(name="Keller", **kwargs) -> Location:
    return Location(
        name=name,
        anchor_span_mm=2300.0,
        left_belt_zero_mm=1450.0,
        right_belt_zero_mm=1470.0,
        **kwargs,
    )


def client_for(card: FakeCard) -> FluidNCClient:
    return FluidNCClient(FluidNCConfig(host="wandplotter.local"), card, card)


def test_push_writes_a_readable_file():
    card = FakeCard()
    book = LocationBook()
    book.add(make_location())
    push_locations(book, client_for(card))

    stored = json.loads(card.files[f"/{REMOTE_LOCATIONS}"])
    assert stored["active"] == "Keller"
    assert stored["locations"][0]["anchor_span_mm"] == 2300.0


def test_push_and_pull_roundtrip():
    card = FakeCard()
    book = LocationBook()
    book.add(make_location(calibration=AreaCalibration(points={"bottom-left": (1.0, 2.0)})))
    book.add(make_location("Werkstatt"), activate=False)
    push_locations(book, client_for(card))

    restored = pull_locations(client_for(card))
    assert set(restored.locations) == {"Keller", "Werkstatt"}
    assert restored.active == "Keller"
    assert restored.get("Keller").calibration.points == {"bottom-left": (1.0, 2.0)}


def test_pull_from_an_empty_card_is_a_clear_error():
    with pytest.raises(Exception, match="404|fehlgeschlagen"):
        pull_locations(client_for(FakeCard()))


def test_pull_rejects_junk_on_the_card():
    with pytest.raises(LocationError, match="nicht lesbar"):
        pull_locations(client_for(FakeCard("kein json")))


def test_sync_push_reads_the_local_file(tmp_path):
    path = tmp_path / "standorte.json"
    book = LocationBook()
    book.add(make_location())
    book.save(path)

    card = FakeCard()
    message = sync_locations(path, client_for(card), direction="push")
    assert "1 Standorte" in message
    assert f"/{REMOTE_LOCATIONS}" in card.files


def test_sync_pull_overwrites_the_local_file(tmp_path):
    path = tmp_path / "standorte.json"
    LocationBook().save(path)

    card = FakeCard()
    remote = LocationBook()
    remote.add(make_location("Garage"))
    push_locations(remote, client_for(card))

    sync_locations(path, client_for(card), direction="pull")
    assert set(LocationBook.load(path).locations) == {"Garage"}


def test_sync_refuses_to_push_nothing(tmp_path):
    path = tmp_path / "leer.json"
    LocationBook().save(path)
    with pytest.raises(LocationError, match="keine Standorte"):
        sync_locations(path, client_for(FakeCard()), direction="push")


def test_unknown_direction_is_rejected(tmp_path):
    with pytest.raises(LocationError, match="push"):
        sync_locations(tmp_path / "x.json", client_for(FakeCard()), direction="merge")
