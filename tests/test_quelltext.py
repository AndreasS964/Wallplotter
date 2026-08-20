"""Was der Übersetzer beim Einlesen bemängelt — bevor es jemand anders tut.

Hintergrund: In ``fluidnc_schema.py`` stand ein ``grep``-Aufruf im
Modulkopf, und darin ``handler\\.item(``. Python liest ``\\.`` in einer
gewöhnlichen Zeichenkette als *ungültige Maskierung* und warnt beim
Übersetzen. Aufgefallen ist das erst in der CI, weil eine warme
``__pycache__`` die Warnung schluckt: Sie entsteht nur, wenn die Datei
wirklich neu übersetzt wird. Genau deshalb prüft dieser Test den Quelltext
und nicht den Import.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
ORDNER = ("src", "tests", "tools")


def _dateien() -> list[Path]:
    dateien: list[Path] = []
    for ordner in ORDNER:
        dateien.extend(sorted((WURZEL / ordner).rglob("*.py")))
    return dateien


def test_es_gibt_ueberhaupt_etwas_zu_pruefen():
    """Ein leerer Suchlauf wäre still grün — und damit wertlos."""
    assert len(_dateien()) > 20


@pytest.mark.parametrize("pfad", _dateien(), ids=lambda p: p.name)
def test_uebersetzt_ohne_warnung(pfad: Path):
    quelle = pfad.read_text(encoding="utf-8")
    with warnings.catch_warnings(record=True) as gesammelt:
        warnings.simplefilter("always")
        compile(quelle, str(pfad), "exec")
    gemeldet = [f"Zeile {w.lineno}: {w.message}" for w in gesammelt]
    assert not gemeldet, f"{pfad.relative_to(WURZEL)} — " + "; ".join(gemeldet)
