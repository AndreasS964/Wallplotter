# Wallplotter

Bild-zu-GCode-Toolchain für einen selbstgebauten V-Plotter (Polargraph), der eine
**2 m × 2,5 m** große Wandfläche im Kletterwand-Keller bemalt.

Die Maschine läuft mit **FluidNC** auf einem BIGTREETECH-Rodent-Board (ESP32);
dieses Repo enthält die Software drumherum: SVG oder Foto → optimierte Linien
(vpype) → GCode im FluidNC/GRBL-Dialekt → Upload auf die µSD-Karte des Boards
über die Web-API.

* Hardware, Mechanik und Entscheidungen: [`docs/projektidee.md`](docs/projektidee.md)
* Software-Stufen und UI-Architektur: [`docs/software-roadmap.md`](docs/software-roadmap.md)

## Warum eigener GCode-Export?

Die Makelangelo-Software erzeugt einen Marlin-spezifischen Dialekt (`M280`,
proprietäre `D`-Codes). FluidNC will GRBL: `G0`/`G1` zum Fahren, `M3 S<wert>` /
`M5` für den Pen-Lift-Servo am PWM-Pin. Genau das erzeugt `wallplotter.gcode` —
und sonst nichts.

## Installation

```bash
git clone https://github.com/AndreasS964/Wallplotter.git
cd Wallplotter
python -m venv .venv && source .venv/bin/activate
pip install -e ".[geometry,dev]"      # Kern + vpype + Tests
pip install -e ".[web]"               # zusätzlich für die Web-UI
pip install -e ".[photo]"             # zusätzlich für den Foto-Zweig (hatched)
```

Ohne die Extras funktionieren GCode-Export, Statistik, Vorschau und Upload —
nur das Einlesen von SVG/Bildern braucht vpype.

## Benutzung

### CLI

```bash
plot examples/testmuster.svg --out out/test.gcode --preview out/test-preview.svg
plot examples/testmuster.svg --host 192.168.1.42 --upload --run
plot foto.jpg --pitch 2.5 --levels 64 128 192      # Foto-Zweig
```

Wichtige Optionen: `--width/--height/--margin` (Fläche in mm), `--draw-feed`,
`--pen-down/--pen-up` (S-Werte des Servos), `--travel-as-g1` (Leerwege langsam
fahren, falls die Riemen springen), `--occult` (verdeckte Linien entfernen).
`plot --help` zeigt alles.

Standardmäßig würfelt vpypes `reloop` den Startpunkt geschlossener Kurven, damit
der Stiftansatz nicht als Punktmuster sichtbar wird — derselbe Input ergibt
dadurch nicht denselben GCode. Zum Vergleichen zweier Läufe `--no-reloop`.

### Web-UI

```bash
python -m wallplotter.webapp
```

Erreichbar unter `http://<pc-ip>:8080` — auch vom Handy an der Wand. Upload,
Parameter, Vorschau (Zeichenwege blau, Leerwege rot gestrichelt), Plot-Start und
SD-Fortschritt in einem Tab.

### Als Bibliothek

```python
from wallplotter import PlotConfig, lines_to_gcode, upload_and_run
from wallplotter.pipeline import svg_to_lines

lines = svg_to_lines("bild.svg")
gcode = lines_to_gcode(lines, PlotConfig(width_mm=2000, height_mm=2500, margin_mm=50))
upload_and_run(gcode, "bild.gcode")
```

## Aufbau

| Modul | Aufgabe |
| --- | --- |
| `wallplotter.config` | Wandmaße, Vorschübe, Pen-Servo-Werte, FluidNC-Zugang |
| `wallplotter.geometry` | Bounding-Box, Einpassen, Spiegeln, Längen-/Zeitschätzung (ohne vpype) |
| `wallplotter.pipeline` | SVG/Bild → Linien in mm (vpype), plus SVG-Vorschau |
| `wallplotter.gcode` | Linien → GCode (`G0`/`G1`, `M3`/`M5`) |
| `wallplotter.upload` | FluidNC-Web-API: Upload, `$SD/Run`, Status, Pause/Stop |
| `wallplotter.cli` | Stufe 2 der Roadmap |
| `wallplotter.webapp` | Stufen 3–6 der Roadmap (NiceGUI) |

CLI und Web-UI nutzen dieselben Funktionen — es gibt bewusst keine zweite
Pipeline.

## Tests

```bash
pytest
```

Alles außer `test_pipeline.py` läuft ohne vpype; dieser Test wird übersprungen,
wenn vpype fehlt.

## Stand

Board bestellt/im Test, Mechanik noch nicht gedruckt. Der Upload-Teil ist gegen
die ESP3D-Endpunkte von FluidNC geschrieben, aber **noch nicht am echten Board
verifiziert** — Endpunkt und Zielpfad sind deshalb über `FluidNCConfig`
konfigurierbar. Ebenso sind die Servo-S-Werte für Pen-Up/Down Platzhalter, bis
der MG90S am Board hängt.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
