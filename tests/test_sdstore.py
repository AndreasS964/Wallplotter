"""Projektdaten auf der SD-Karte — gegen einen simulierten Kartenspeicher."""

import json
import sys
from pathlib import Path

import pytest

from wallplotter.calibration import AreaCalibration
from wallplotter.config import FluidNCConfig
from wallplotter.location import Location, LocationBook, LocationError
from wallplotter.sdstore import REMOTE_LOCATIONS, pull_locations, push_locations, sync_locations
from wallplotter.upload import FluidNCClient

sys.path.insert(0, str(Path(__file__).parent))

from fluidnc_fake import FakeSession  # noqa: E402


class FakeCard(FakeSession):
    """Simulierte Karte — kennt nur die Endpunkte, die FluidNC registriert.

    Erbt von :class:`tests.fluidnc_fake.FakeSession`, damit ein Rückfall auf
    einen erfundenen Endpunkt hier genauso 404 gibt wie am echten Board.
    """

    def __init__(self, content: str | None = None):
        super().__init__(files={f"/{REMOTE_LOCATIONS}": content} if content is not None else None)

    @property
    def files(self) -> dict[str, str]:
        return self.card


def make_location(name="Keller", **kwargs) -> Location:
    return Location(
        name=name,
        anchor_span_mm=2300.0,
        left_belt_zero_mm=1450.0,
        right_belt_zero_mm=1470.0,
        **kwargs,
    )


def client_for(card: FakeCard) -> FluidNCClient:
    return FluidNCClient(FluidNCConfig(host="wandplotter.local"), card)


def test_push_writes_a_readable_file():
    card = FakeCard()
    book = LocationBook()
    book.add(make_location())
    push_locations(book, client_for(card))

    stored = json.loads(card.files[f"/{REMOTE_LOCATIONS}"])
    assert stored["active"] == "Keller"
    assert stored["locations"][0]["anchor_span_mm"] == 2300.0


def test_push_and_pull_roundtrip_with_a_non_root_remote_dir():
    """download() ignorierte remote_dir, upload() nicht — bei einem
    remote_dir ungleich "/" schrieb push dorthin und pull las weiterhin ab
    Kartenwurzel, traf also nie auf das, was push gerade abgelegt hatte."""
    card = FakeCard()
    client = FluidNCClient(FluidNCConfig(host="wandplotter.local", remote_dir="/wallplotter"), card)
    book = LocationBook()
    book.add(make_location())
    push_locations(book, client)

    restored = pull_locations(client)
    assert set(restored.locations) == {"Keller"}


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
