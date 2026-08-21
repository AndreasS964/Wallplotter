# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-Bauplan für die eigenständige Windows-Datei der Web-Oberfläche.

    pip install -e ".[geometry,web,photo]" pyinstaller
    pyinstaller tools/wallplotter-web.spec

Baut eine einzelne Datei (``--onefile``, hier über ``EXE(..., binaries=...,
datas=...)`` statt eines separaten ``COLLECT``-Schritts). Startet dadurch
langsamer als ein Ordner mit vielen Dateien — die Datei packt sich bei jedem
Start in ein temporäres Verzeichnis aus —, ist aber die eine Datei, die sich
verschicken und doppelklicken lässt.

Zwei Pakete brauchen mehr als PyInstallers eingebaute Importsuche:

* **NiceGUI** liefert seine Oberfläche über eigene, nicht-Python-Dateien aus
  (Vue/Quasar-Bausteine, Icons, Übersetzungen) — ohne die als ``--add-data``
  wäre die ausgelieferte Seite leer.
* **vpype**/**vpype_cli** entdecken einen Teil ihrer Kommandos über
  Paket-Metadaten zur Laufzeit (Plugin-Mechanismus) statt über normale
  ``import``-Anweisungen — genau das übersieht die statische Importsuche.
  Dasselbe gilt für ``pnoise`` (eine vpype-Abhängigkeit): es liest seine
  eigene Version über ``importlib.metadata`` — ohne die mitgepackten
  ``.dist-info``-Daten bricht schon der Import ab.
* ``tomli`` (über vpype eingezogen) ist mit ``mypyc`` übersetzt; die
  übersetzten Module verweisen auf ein gemeinsames Laufzeitmodul
  (``<hash>__mypyc``), das als einzelne Datei lose neben den Paketen liegt —
  keine normale ``import``-Anweisung, die die statische Suche sehen könnte.
  Der Name hängt vom genauen Build ab, deshalb wird er unten zur Bauzeit
  gesucht statt fest eingetragen.

``collect_all`` holt für ein Paket Code, Daten und Metadaten vollständig statt
nur das, was ``Analysis`` selbst findet.

Enthält bewusst nicht: das Extra ``hatch`` (Schraffur über das Fremdpaket
``hatched``) — das läuft seit Shapely 2 ohnehin nicht mehr, siehe README,
Abschnitt „Fotos". Wer es trotzdem braucht, installiert es separat und baut
mit ``pip install -e ".[hatch]"`` vorher mit.
"""

import sysconfig
from pathlib import Path

import nicegui
from PyInstaller.utils.hooks import collect_all

REPO = Path(SPECPATH).resolve().parent
NICEGUI_DIR = Path(nicegui.__file__).parent

block_cipher = None


def _ohne_tests(modulname: str) -> bool:
    # shapely liefert seine eigene Testsuite als Unterpakete mit aus —
    # collect_all() würde sie sonst als "versteckte" Importe mitschleppen.
    return ".tests" not in modulname and ".test_" not in modulname


# collect_all() liefert Quell/Ziel-Paare im Format von Analysis(datas=…) —
# nicht im internen TOC-Format von a.datas danach. Deshalb hier einsammeln
# und der Analysis mitgeben, nicht im Nachhinein an a.datas/a.binaries hängen.
datas = [(str(NICEGUI_DIR), "nicegui")]
binaries = []
hiddenimports = []

for paket in ("vpype", "vpype_cli", "pnoise", "shapely"):
    paket_datas, paket_binaries, paket_hiddenimports = collect_all(
        paket, filter_submodules=_ohne_tests
    )
    datas += paket_datas
    binaries += paket_binaries
    hiddenimports += paket_hiddenimports

# lose "<hash>__mypyc"-Laufzeitmodule mypyc-übersetzter Pakete einsammeln
# (siehe Erklärung oben) — der Name ist bauabhängig, nicht fest eintragbar.
site_packages = Path(sysconfig.get_paths()["purelib"])
hiddenimports += [
    pfad.name.split(".")[0] for pfad in site_packages.glob("*__mypyc*")
]

a = Analysis(
    [str(REPO / "tools" / "windows_app.py")],
    pathex=[str(REPO / "src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,  # a.binaries/a.datas direkt in EXE() statt in ein COLLECT()
    a.datas,     # danach macht daraus die eine Datei, nicht einen Ordner
    [],
    name="Wandplotter",
    console=True,  # kein --windowed: das Server-Protokoll bleibt sichtbar
)
