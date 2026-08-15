# Wallplotter

Bild-zu-GCode-Toolchain für einen selbstgebauten V-Plotter (Polargraph), der eine
**2 m × 2,5 m** große Wandfläche im Kletterwand-Keller bemalt.

Die Maschine läuft mit **FluidNC** auf einem BIGTREETECH-Rodent-Board (ESP32);
dieses Repo enthält die Software drumherum: SVG oder Foto → optimierte Linien
(vpype) → GCode im FluidNC/GRBL-Dialekt → Upload auf die µSD-Karte des Boards
über die Web-API.

* Hardware, Mechanik und Entscheidungen: [`docs/projektidee.md`](docs/projektidee.md)
* Software-Stufen und UI-Architektur: [`docs/software-roadmap.md`](docs/software-roadmap.md)
* Nachgerechnete Kinematik (Auflösung, Riemenlängen, Zugkräfte): [`docs/kinematik.md`](docs/kinematik.md)
* Firmware-Konfiguration: [`config/fluidnc-wallplotter.yaml`](config/fluidnc-wallplotter.yaml)

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

### Fläche einmessen

Wie groß die bemalbare Fläche wirklich ist, hängt an der Aufhängung — also
nicht messen, sondern anfahren:

```bash
wallplotter-calibrate --host 192.168.1.42 zero          # am Anschlag: Nullpunkt
wallplotter-calibrate --host 192.168.1.42 jog --dx -100 # Gondel bewegen
wallplotter-calibrate --host 192.168.1.42 record bottom-left
# ... die übrigen drei Ecken, dann:
wallplotter-calibrate show
plot bild.svg --calibration calibration.json --upload --run
```

Vier Ecken sind ideal (dann warnt das Tool auch bei schiefer Aufhängung), zwei
diagonale reichen. Das Ergebnis ist bewusst das größte Rechteck *innerhalb* der
angefahrenen Punkte: lieber etwas kleiner als neben der Wand.

### Testmuster

```bash
plot --list-patterns
plot --pattern frame --calibration calibration.json --upload --run
plot --pattern feed-ramp --out out/tempo.gcode
```

`frame` (Rahmen, Diagonalen, Eckkreuze), `grid` (Maßstab nachmessen),
`circles` (Verzerrung), `pen-test` (Servo-Wartezeit), `feed-ramp` (Tempo bis
zum Riemenspringen).

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

Erreichbar unter `http://<pc-ip>:8080` — auch vom Handy an der Wand. Drei
Reiter für die drei Situationen vor der Wand:

* **Plotten** — Upload oder Testmuster, Flächen- und Stiftparameter, Vorschau
  (Zeichenwege blau, Leerwege rot gestrichelt), Plot starten
* **Kalibrieren** — Jog-Pad, Nullpunkt setzen, Ecken übernehmen und wieder
  anfahren, Schiefstandswarnung
* **Maschine** — SD-Fortschritt, Pause/Weiter/Stopp

Auf dem Handy stapeln sich die Karten, das Jog-Pad steht dabei oben.

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
| `wallplotter.kinematics` | Auflösung, Riemenlängen, Zugkräfte nachrechnen |
| `wallplotter.calibration` | angefahrene Ecken → nutzbare Fläche mit Versatz |
| `wallplotter.patterns` | Testmuster für die Erstinbetriebnahme |
| `wallplotter.cli` | Stufe 2 der Roadmap |
| `wallplotter.calibrate_cli` | Jog, Nullpunkt, Ecken aufnehmen |
| `wallplotter.webapp` | Stufen 3–6 der Roadmap (NiceGUI) |

CLI und Web-UI nutzen dieselben Funktionen — es gibt bewusst keine zweite
Pipeline.

## Tests

```bash
pytest
```

Tests, die vpype, NiceGUI oder PyYAML brauchen, überspringen sich selbst, wenn
das Paket fehlt — der Rest läuft immer.

## Stand

Board unterwegs, Mechanik noch nicht gedruckt. Was das heißt:

* **Verifiziert ohne Hardware:** Geometrie, GCode-Export, Kalibrierlogik,
  Testmuster, Kinematikrechnung, UI-Verdrahtung — alles unter Test.
* **Noch offen bis das Board da ist:** die ESP3D-Endpunkte (`/upload`,
  `/command`) sind nach Dokumentation gebaut, aber nie gegen echte Firmware
  gelaufen; die Servo-S-Werte für Pen-Up/Down sind Platzhalter; und der
  PWM-Pin für den Servo in der `config.yaml` ist der einzige geratene Wert
  (siehe Kommentar dort — der 3–10-V-Ausgang des Rodent passt womöglich nicht
  zu einem MG90S).

## Lizenz

MIT — siehe [LICENSE](LICENSE).
