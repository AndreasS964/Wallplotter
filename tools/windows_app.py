"""Einstieg für die eigenständige Windows-Datei — gebaut mit PyInstaller.

Kein Teil des Pakets: ``wallplotter.webapp`` bleibt frei davon, wie es
verpackt wird (dieselbe Trennung wie bei ``tools/build_site.py``). Der einzige
Unterschied zu ``wallplotter-web`` auf der Kommandozeile: Wer eine Datei
doppelklickt, sieht keine Startzeile und kein „unter http://… erreichbar" —
also öffnet dieses Skript den Browser selbst.

Gebaut wird darüber mit ``.github/workflows/windows-build.yml`` (Auslöser:
von Hand über „Run workflow", siehe docs/windows-paket.md), lokal mit::

    pip install -e ".[geometry,web,photo]" pyinstaller
    pyinstaller tools/wallplotter-web.spec
"""

from __future__ import annotations

import multiprocessing
import webbrowser
from threading import Timer

HOST = "0.0.0.0"
"""Auf allen Schnittstellen lauschen — auch vom Handy an der Wand erreichbar,
genau wie beim dokumentierten Start über ``wallplotter-web``."""

PORT = 8080

BOARD = "fluidnc.local"


def _oeffne_browser() -> None:
    # Über 127.0.0.1, nicht HOST: "0.0.0.0" ist keine Adresse, die ein
    # Browser aufrufen kann, auch wenn der Server dort lauscht.
    webbrowser.open(f"http://127.0.0.1:{PORT}")


def main() -> None:
    # Auf Windows nötig, sobald ein gefrorenes Programm irgendwo
    # multiprocessing anfassen könnte — ohne das startet sich die Datei sonst
    # bei jedem Start selbst erneut, statt den Server hochzufahren.
    multiprocessing.freeze_support()

    from wallplotter.webapp import main as server_main  # noqa: PLC0415

    print(f"Wandplotter startet — gleich unter http://127.0.0.1:{PORT} im Browser.")
    print("Vom Handy oder einem anderen Rechner im selben Netz über die IP dieses PCs.")
    print("Dieses Fenster schließen, um den Server zu beenden.")

    Timer(1.0, _oeffne_browser).start()
    server_main(host=HOST, port=PORT, board=BOARD)


if __name__ in {"__main__", "__mp_main__"}:  # NiceGUI startet den Prozess neu
    main()
