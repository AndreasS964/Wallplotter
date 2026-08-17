#!/usr/bin/env python3
"""Die Bilder der Web-UI für die Dokumentation neu aufnehmen.

    python tools/ui_screenshots.py

Startet den mitgelieferten Simulator, hängt die Web-UI daran, lädt ein
Testmuster und fotografiert die drei Reiter — am Rechner und am Handy. Damit
veralten die Bilder im README nicht neben der Oberfläche, und wer etwas am
Aussehen ändert, sieht es sofort statt erst beim nächsten Blick ins Repo.

Braucht ``playwright`` und einen Chromium. Beides gehört nicht in die
Abhängigkeiten des Projekts: Ein Wandplotter braucht keinen Browserautomaten,
nur wer die Bilder erneuert.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"

#: Ein Standort mit vollständig eingemessener Fläche — sonst zeigen die Bilder
#: eine Oberfläche, die auf ihre erste Eingabe wartet, und nicht die, mit der
#: man arbeitet.
DEMO_BOOK = {
    "active": "Keller",
    "locations": [
        {
            "name": "Keller",
            "anchor_span_mm": 2300.0,
            "left_belt_zero_mm": 1450.0,
            "right_belt_zero_mm": 1470.0,
            "calibration": {
                "points": {
                    "bottom-left": [-815.0, -2180.0],
                    "bottom-right": [828.0, -2175.0],
                    "top-right": [830.0, -295.0],
                    "top-left": [-812.0, -300.0],
                }
            },
        }
    ],
}

SHOTS = (
    ("Plotten", "ui-plotten.png"),
    ("Kalibrieren", "ui-kalibrieren.png"),
    ("Maschine", "ui-maschine.png"),
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_for(url: str, timeout_s: float = 25.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with contextlib.suppress(Exception):
            urllib.request.urlopen(url, timeout=1)
            return
        time.sleep(0.3)
    raise SystemExit(f"{url} kam nicht hoch")


def _chromium() -> str:
    """Chromium finden, ohne playwright eigene Browser laden zu lassen."""
    candidates = [
        os.environ.get("CHROMIUM_PATH", ""),
        "/opt/pw-browsers/chromium/chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    for base in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"):
        return str(base)
    raise SystemExit("Kein Chromium gefunden — CHROMIUM_PATH setzen")


def _start(tmp: Path) -> tuple[subprocess.Popen, subprocess.Popen, int]:
    board_port, ui_port = _free_port(), _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    board = subprocess.Popen(
        [sys.executable, "-m", "wallplotter.simulator", "--port", str(board_port),
         "--speed", "260", "--quiet"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _wait_for(f"http://127.0.0.1:{board_port}/upload?path=/")

    # Ein laufender Job: Die Bilder sollen die Oberfläche bei der Arbeit zeigen,
    # nicht im Leerlauf.
    program = "G21\nG90\n" + "\n".join(
        f"G1 X{200 + i % 900} Y{300 + i % 1100} F1500" for i in range(1500)
    )
    _upload(board_port, program)

    book = tmp / "standorte.json"
    book.write_text(json.dumps(DEMO_BOOK), encoding="utf-8")
    runner = tmp / "run_ui.py"
    runner.write_text(
        "from wallplotter.webapp import create_app\n"
        f"ui = create_app(host='127.0.0.1:{board_port}', locations_path={str(book)!r})\n"
        f"ui.run(host='127.0.0.1', port={ui_port}, reload=False, show=False)\n",
        encoding="utf-8",
    )
    web = subprocess.Popen(
        [sys.executable, str(runner)], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    _wait_for(f"http://127.0.0.1:{ui_port}/")
    return board, web, ui_port


def _upload(port: int, program: str) -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from wallplotter.config import FluidNCConfig  # noqa: PLC0415
    from wallplotter.upload import FluidNCClient  # noqa: PLC0415

    client = FluidNCClient(FluidNCConfig(host=f"127.0.0.1:{port}", timeout_s=5))
    client.upload(program, "wandbild.gcode")
    client.run_file("/wandbild.gcode")
    client.close()


def _shrink(path: Path, width: int) -> None:
    """Auf Anzeigebreite herunterrechnen.

    Aufgenommen wird mit doppelter Pixeldichte, damit Schrift und Linien nicht
    ausfransen; im Repo liegen soll aber kein Megabyte je Bild.
    """
    from PIL import Image  # noqa: PLC0415

    image = Image.open(path)
    if image.width <= width:
        return
    height = round(image.height * width / image.width)
    image.resize((width, height), Image.LANCZOS).save(path, optimize=True)


def capture(ui_port: int) -> None:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    url = f"http://127.0.0.1:{ui_port}"
    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])

        desk = browser.new_page(viewport={"width": 1400, "height": 950}, device_scale_factor=2)
        desk.goto(url, wait_until="networkidle")
        desk.wait_for_timeout(2500)
        desk.get_by_role("button", name="frame", exact=True).click()
        desk.wait_for_timeout(1400)
        for label, name in SHOTS:
            desk.get_by_role("tab", name=label).click()
            desk.wait_for_timeout(900)
            desk.screenshot(path=str(OUT / name))
            _shrink(OUT / name, 1400)
            print(OUT / name)

        phone = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        phone.goto(url, wait_until="networkidle")
        phone.wait_for_timeout(2500)
        phone.get_by_role("button", name="frame", exact=True).click()
        phone.wait_for_timeout(1400)
        # Der Klick rollt die Seite zum Knopf — für das Bild soll aber die
        # Vorschau oben stehen.
        phone.evaluate(
            "() => { window.scrollTo(0, 0);"
            " document.querySelectorAll('*').forEach(n => n.scrollTop = 0); }"
        )
        phone.wait_for_timeout(400)
        phone.screenshot(path=str(OUT / "ui-handy.png"))
        _shrink(OUT / "ui-handy.png", 390)
        print(OUT / "ui-handy.png")

        browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        board, web, ui_port = _start(Path(raw))
        try:
            capture(ui_port)
        finally:
            for process in (web, board):
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
