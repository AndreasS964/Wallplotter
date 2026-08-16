"""Dateien schreiben, ohne dass ein Tippfehler als Traceback endet.

Alle Kommandozeilenwerkzeuge schreiben Dateien: GCode, Vorschauen, Messreihen,
Korrekturen. Jedes davon kann an denselben banalen Dingen scheitern — der
Ordner gibt es nicht, das Ziel ist ein Verzeichnis, die Karte ist voll, die
Datei gehört jemand anderem. Ein Traceback ist darauf die schlechteste
Antwort: er sieht aus wie ein Softwarefehler, ist aber einer im Aufruf.

Ein fehlender Ordner wird angelegt. ``plot bild.svg -o ausgabe/wand.gcode``
meint fast immer genau das, und der Pfad steht danach in der Erfolgsmeldung —
ein vertippter Ordner fällt dort auf.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["OutputError", "write_text"]


class OutputError(RuntimeError):
    """Die Datei ließ sich nicht schreiben."""


def write_text(path: Path | str, text: str, *, what: str = "Datei") -> Path:
    """Text schreiben; bei Problemen mit einer verständlichen Meldung scheitern."""
    target = Path(path)
    try:
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise OutputError(
            f"{what} lässt sich nicht schreiben ({target}): {exc.strerror or exc}"
        ) from exc
    return target
