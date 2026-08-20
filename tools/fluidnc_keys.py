"""Die Schlüssel- und Abschnittsnamen aus dem FluidNC-Quelltext ziehen.

    python tools/fluidnc_keys.py ~/src/FluidNC --out tests/fluidnc-schluessel.txt

Warum es dieses Werkzeug gibt: :mod:`wallplotter.fluidnc_schema` behauptet, aus
dem Firmware-Quelltext abgeschrieben zu sein — und eine Abschrift von Hand ist
eine Behauptung, solange sie niemand gegenprüft. Die Gegenprobe braucht aber
den Quelltext, und der liegt nicht im Repo. Also: einmal ziehen, das Ergebnis
als Textdatei danebenlegen, und der Test hält die Tabelle dagegen.

Die Datei ist damit ein **Abzug mit Datum**, kein Orakel. Wer sie erneuert,
schreibt den neuen Stand in die Kopfzeile.

Drei Registrierungswege, alle drei gebraucht:

* ``handler.item("name", …)`` — der Normalfall.
* ``ControlPin(&event, "name", 'K')`` — die Control-Eingänge entstehen in einer
  Schleife über eine Pinliste, es gibt für sie keinen ``item("…")``-Aufruf mit
  Zeichenkette (``Control.cpp:112``: ``item(pin->legend(), *pin)``).
* ``Macro { "name" }`` — dasselbe für die Makros (``Machine/Macros.h:43``).

Abschnittsnamen kommen aus ``handler.section("name", …)``, aus
``handler.sections("name", …)`` (die nummerierten wie ``uart1``) und aus den
Fabriken (``InstanceBuilder<Klasse> registration("Name")``).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MUSTER = {
    "item": re.compile(r'handler\.item\(\s*"([A-Za-z0-9_]+)"'),
    "section": re.compile(r'handler\.sections?\(\s*"([A-Za-z0-9_]+)"'),
    "factory": re.compile(r'InstanceBuilder<[^>]+>\s*[A-Za-z0-9_]*\s*(?:__attribute__\([^)]*\)\s*)?\(\s*"([^"]+)"'),
    "controlpin": re.compile(r'ControlPin\(&[A-Za-z0-9_]+,\s*"([A-Za-z0-9_]+)"'),
    # Beide Schreibweisen aus Macros.cpp: `Macro Macros::_startup_line0 { "…" };`
    # und die Einträge des Feldes, `Macro { "Macro0" },`.
    "macro": re.compile(r'Macro\s+(?:[A-Za-z0-9_:]+\s*)?\{\s*"([A-Za-z0-9_]+)"\s*\}'),
}


def sammeln(quelle: Path) -> dict[str, set[str]]:
    gefunden: dict[str, set[str]] = {name: set() for name in MUSTER}
    dateien = 0
    for datei in sorted(quelle.rglob("*")):
        if datei.suffix not in (".cpp", ".h") or not datei.is_file():
            continue
        dateien += 1
        text = datei.read_text(errors="replace")
        for name, muster in MUSTER.items():
            gefunden[name].update(muster.findall(text))
    if not dateien:
        raise SystemExit(f"Keine Quelldateien unter {quelle} — ist das ein FluidNC-Baum?")
    print(f"{dateien} Dateien gelesen", file=sys.stderr)
    return gefunden


def schreiben(gefunden: dict[str, set[str]], revision: str) -> str:
    zeilen = [
        "# Schlüssel und Abschnittsnamen, die FluidNC kennt.",
        "#",
        f"# Abzug aus {revision}, gezogen mit:",
        "#     python tools/fluidnc_keys.py <FluidNC-Quelltext> --out tests/fluidnc-schluessel.txt",
        "#",
        "# Gegenprobe für wallplotter/fluidnc_schema.py. Kein Orakel, ein Abzug mit Datum:",
        "# Wer ihn erneuert, schreibt den neuen Stand in diese Kopfzeile.",
        "",
    ]
    for art in ("item", "section", "factory", "controlpin", "macro"):
        for name in sorted(gefunden[art], key=str.lower):
            zeilen.append(f"{art}\t{name}")
    return "\n".join(zeilen) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("quelle", type=Path, help="Wurzel des FluidNC-Quelltexts")
    parser.add_argument("--out", type=Path, help="Zieldatei (ohne Angabe: Standardausgabe)")
    parser.add_argument("--revision", default="bdring/FluidNC (Stand siehe git log)")
    args = parser.parse_args(argv)

    wurzel = args.quelle
    if (wurzel / "FluidNC" / "src").is_dir():
        wurzel = wurzel / "FluidNC" / "src"
    text = schreiben(sammeln(wurzel), args.revision)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Geschrieben: {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
