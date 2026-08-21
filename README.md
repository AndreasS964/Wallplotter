<h1 align="center">Wallplotter</h1>

<p align="center">
  Bild-zu-GCode-Toolchain für einen selbstgebauten V-Plotter.<br>
  SVG oder Foto rein, GCode für <b>FluidNC</b> auf dem BIGTREETECH-Rodent-Board raus.
</p>

<p align="center">
  <a href="https://github.com/AndreasS964/Wallplotter/actions/workflows/ci.yml">
    <img alt="CI" src="https://github.com/AndreasS964/Wallplotter/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue">
  <img alt="Tests" src="https://img.shields.io/badge/Tests-728-brightgreen">
  <img alt="Lizenz" src="https://img.shields.io/badge/Lizenz-MIT-lightgrey">
</p>

<p align="center">
  <img src="docs/images/ui-plotten.png" alt="Web-UI, Reiter Plotten" width="880">
</p>

---

## Was das ist

Eine Gondel hängt an zwei Zahnriemen zwischen zwei Motoren und zieht einen Stift
über die Wand. Diese Software erzeugt das GCode-Programm dazu, lädt es auf das
Board und startet es.

Der Plotter hängt nicht immer an derselben Wand. Ankerabstand und Ankerhöhe
gehören deshalb zum Aufbau und nicht ins Programm: pro Standort drei Maße mit dem
Zollstock, alles Weitere rechnet die Software.

<p align="center">
  <img src="docs/images/geometrie.svg" alt="Geometrie: Anker, Riemen, Gondel, Zeichenfläche" width="620">
</p>

Der erzeugte GCode ist GRBL, wie FluidNC ihn erwartet: `G0`/`G1` zum Fahren,
`M3 S<wert>` und `M5` für den Servo am Stiftheber. Die Makelangelo-Software
schreibt stattdessen Marlin-Dialekt mit `M280` und eigenen `D`-Codes, der auf
diesem Board nicht läuft.

Wer den Plotter erst noch baut, fängt bei der
**[Bauanleitung](docs/bauanleitung.md)** an.

## Installation

```bash
git clone https://github.com/AndreasS964/Wallplotter.git
cd Wallplotter
python -m venv .venv && source .venv/bin/activate
pip install -e ".[geometry,web,dev]"
```

| Extra | Wofür |
| --- | --- |
| `geometry` | SVG einlesen (vpype) |
| `web` | Web-Oberfläche (NiceGUI) |
| `photo` | Fotos rastern (Pillow) |
| `hatch` | Schraffur; zieht vpype, OpenCV und matplotlib nach |
| `dev` | Tests und Linter |
| `site` | `tools/build_site.py` |

GCode-Export, Vorschau, Kalibrierung und Upload laufen auch ganz ohne Extras.

## Schnellstart

```bash
wallplotter-setup
```

Führt in acht Schritten von der leeren Wand zum ersten Strich: Installation
prüfen, Board erreichen, Aufhängung einmessen, `config.yaml` erzeugen und
übertragen, Nullpunkt setzen, Fläche abstecken, Stiftheber einstellen, Rahmen
plotten. Jeder Schritt stellt selbst fest, ob er schon erledigt ist, deshalb
lässt sich der Ablauf jederzeit abbrechen und später fortsetzen.

```bash
wallplotter-setup --status     # nur zeigen, was noch fehlt
wallplotter-setup --ab servo   # ab einem bestimmten Schritt weitermachen
```

Ohne Board läuft die Hälfte trotzdem: messen, rechnen, `config.yaml` schreiben
und prüfen. Was die Maschine braucht, steht am Ende als Liste offener Punkte da.

Alles Folgende geht auch einzeln.

## Standort einrichten

Drei Maße pro Aufhängung, danach kennt die Software die Geometrie:

```bash
wallplotter-calibrate --host 192.168.1.42 zero      # Gondel am Referenzpunkt
wallplotter-location new Keller --span 2300 --left 1450 --right 1470
wallplotter-location show                           # Maße und Ankerkoordinaten nachsehen
```

`--span` ist der Abstand der beiden Umlenkpunkte, `--left` und `--right` sind die
Riemenlängen vom jeweiligen Umlenkpunkt zur Gondel am Nullpunkt. Daraus fallen
die Ankerkoordinaten per Trilateration heraus.

`wallplotter-location list` zeigt alle Aufhängungen, `use <Name>` wechselt. In
der Web-UI steht die Auswahl oben in der Kopfzeile. Die Standortdaten wandern mit
dem Plotter, wenn man sie auf die SD-Karte legt:

```bash
wallplotter-location push --host 192.168.1.42
wallplotter-location pull --host 192.168.1.42
```

Zusammengeführt wird dabei nichts automatisch. Sind zwei Stände auseinander
gelaufen, entscheidet man selbst, welcher gilt.

## Die `config.yaml` erzeugen

Die Firmware-Konfiguration ist ein Erzeugnis derselben Beschreibung, mit der auch
die Vorschau rechnet:

```bash
wallplotter-firmware config  --location Keller --out config/fluidnc-wallplotter.yaml
wallplotter-firmware pruefen config/fluidnc-wallplotter.yaml
wallplotter-firmware diff    config/fluidnc-wallplotter.yaml
wallplotter-firmware push    --host 192.168.1.42 --location Keller
```

Drei Werte hängen damit nicht mehr daran, dass jemand sie von Hand nachzieht:
die Ankermaße folgen aus dem eingemessenen Standort, `steps_per_mm` aus Pulley,
Riementeilung und Mikroschritten, und die `speed_map` aus dem Impulsfenster des
Servos (bei 50 Hz will ein RC-Servo 1,0 bis 2,0 ms, also 5 bis 10 % Tastverhältnis).

`pruefen` liest die Datei zweimal: einmal als YAML, mit jedem Schlüssel gegen eine
Liste aus dem FluidNC-Quelltext, und einmal mit den Regeln von FluidNCs eigenem
Tokenizer. Der zweite Blick lohnt sich, weil die Firmware Kommentare am
Zeilenende nicht abschneidet. Aus

```yaml
idle_ms: 255 # Motoren gehalten lassen
```

liest jeder YAML-Parser `255`, FluidNC dagegen die ganze Zeile. Die ausgelieferte
Datei hatte elf solche Zeilen; Details in der
[Gegenprüfung](docs/firmware-gegenpruefung.md), Abschnitt 2.6.

Mit `--host` holt `pruefen` die Datei vorher vom Board. `push` schreibt in den
Flash, denn von der SD-Karte liest FluidNC die Konfiguration nicht, und sichert
die bisherige Fassung vorher.

## Fläche einmessen

Wie groß die bemalbare Fläche ist, hängt am Aufbau. Statt zu messen, fährt man
die Ecken an:

```bash
wallplotter-calibrate --host 192.168.1.42 jog --dx -100
wallplotter-calibrate --host 192.168.1.42 record bottom-left
# ... die übrigen drei Ecken, dann:
wallplotter-calibrate show
```

Die Ecken landen im aktiven Standort. Vier sind ideal, dann meldet das Tool auch
eine schiefe Aufhängung; zwei diagonale reichen. Das Ergebnis ist das größte
Rechteck innerhalb der angefahrenen Punkte, also eher etwas kleiner als die
tatsächliche Fläche. `wallplotter-location show` rechnet Auflösung, Riemenkräfte
und Riemenlänge für genau diese Fläche durch.

## Plotten

```bash
plot examples/testmuster.svg --out out/test.gcode --preview out/test-vorschau.svg
plot bild.svg --location --upload --run
plot foto.jpg --technique tsp
```

Die erste Zeile läuft in einem frischen Klon sofort durch und schreibt GCode und
Vorschau nach `out/`.

Wichtige Optionen: `--width`, `--height`, `--margin` für die Fläche in mm,
`--draw-feed` für den Vorschub, `--pen-down`/`--pen-up` für die S-Werte des
Servos, `--travel-as-g1` für langsame Leerwege, falls die Riemen springen, und
`--occult` zum Entfernen verdeckter Linien. `plot --help` zeigt den Rest.

vpypes `reloop` würfelt den Startpunkt geschlossener Kurven, damit der
Stiftansatz nicht als Punktmuster sichtbar wird. Derselbe Input ergibt dadurch
nicht denselben GCode; zum Vergleichen zweier Läufe hilft `--no-reloop`.

### Werkzeug wählen

```bash
plot --list-toolheads
plot bild.svg --toolhead marker
plot bild.svg --toolhead pinsel --pen-dwell 0.6
```

Im Katalog stehen `fineliner`, `fineliner-rot`, `kugelschreiber`, `marker`,
`kreide`, `pinsel` und `laser`. Die Servowerte darin sind geschätzte Startwerte,
weil Hebel, Federweg und Halter an jedem Aufbau anders sind. Nachgezogen wird mit
`plot --pattern pen-test`: fehlende Strichanfänge bedeuten zu kurze Wartezeit,
ausgefranste Enden zu viel Anpressdruck.

### Mehrfarbig

```bash
plot bild.svg --layers                 # je Strichfarbe eine GCode-Datei
plot bild.svg --layers --one-file      # eine Datei mit M0-Pause zum Stiftwechsel
plot bild.svg --layers --pen-for '#000000=fineliner' --pen-for '#e02020=marker'
```

Getrennte Dateien sind bei mehrstündigen Plots das Praktischere: Schwarz heute,
Rot morgen. Alle Ebenen werden gemeinsam eingepasst, sonst fiele die Zeichnung
auseinander. Mit `--pen-for` bekommt jede Farbe ihren eigenen Stift samt
Servowerten und Vorschub, und die `M0`-Pause nennt Farbe und Stift.

### Testmuster

```bash
plot --list-patterns
plot --pattern frame --location --upload --run
```

![Testmuster](docs/images/muster.png)

`frame` (Rahmen, Diagonalen, Eckkreuze), `grid` (Maßstab nachmessen), `circles`
(Verzerrung), `pen-test` (Servo-Wartezeit), `feed-ramp` (Tempo bis zum
Riemenspringen).

### Abgebrochenen Plot fortsetzen

```bash
wallplotter-resume wand.gcode --from-board --host 192.168.1.42
wallplotter-resume wand.gcode --percent 42
```

Angesetzt wird am Anfang des angefangenen Strichs. FluidNC meldet den Fortschritt
als Prozent der gelesenen Bytes, und der Planer liest der Mechanik voraus; ein
doppelt gezogener Strich fällt weniger auf als ein fehlender. Wer es genauer
will, nimmt `--exact`. Vor dem Start muss der Nullpunkt wieder stehen.

## Web-UI

```bash
wallplotter-web --board 192.168.1.42
```

Erreichbar unter `http://<pc-ip>:8080`, auch vom Handy an der Wand. Drei Reiter:

* **Plotten** — Upload oder Testmuster, Flächen- und Stiftparameter, Vorschau
  (Zeichenwege in der Stiftfarbe, Leerwege rot gestrichelt), Plot starten
* **Kalibrieren** — Jog-Pad, Nullpunkt setzen, Ecken übernehmen und wieder
  anfahren, Schiefstandswarnung, Standort anlegen samt Kinematik-Urteil
* **Maschine** — SD-Fortschritt, Pause, Weiter, Stopp

Auf dem Handy stapeln sich die Karten, das Jog-Pad steht dabei oben.

<table>
<tr>
<td width="70%"><img src="docs/images/ui-kalibrieren.png" alt="Reiter Kalibrieren"></td>
<td width="30%"><img src="docs/images/ui-handy.png" alt="Dieselbe Seite auf dem Handy"></td>
</tr>
<tr>
<td align="center"><sub>Kalibrieren: Jog-Pad, Ecken, Standort samt Kinematik-Urteil</sub></td>
<td align="center"><sub>… und auf dem Handy an der Wand</sub></td>
</tr>
</table>

## Fotos

```bash
plot --list-techniques
plot foto.jpg --technique spiral --pitch 25
```

| Verfahren | Charakter | Stifthübe | Stand |
| --- | --- | --- | --- |
| `stipple` | Punktraster, fotografisch | einer je Punkt | läuft |
| `tsp` | dieselben Punkte als eine durchgehende Linie | keine | läuft |
| `spiral` | Spirale mit dunkelheitsabhängiger Auslenkung | keine | läuft |
| `hatch` | Schraffur nach Helligkeitsstufen, grafisch | viele | fällt derzeit aus |

![Vergleich der Bildverfahren](docs/images/verfahren.png)

`tsp` und `spiral` zeichnen ohne abzusetzen, damit entfallen Servo-Artefakte und
Pendelstöße durch Leerfahrten. `hatch` hängt am Fremdpaket `hatched`, dessen
letzte Veröffentlichung (0.2.0) mit Shapely 2 nicht mehr läuft; der Aufruf meldet
das im Klartext samt Ausweg. Die Zeitangaben im Bild gelten für 2 × 2,5 m bei
1500 mm/min.

## Laser

Der Laserpfad ist umgesetzt, aber an keiner Hardware erprobt. Vor dem ersten
Einsatz gehört das erzeugte Programm durchgesehen.

```bash
plot bild.svg --toolhead laser --laser-verstanden \
     --laser-smax 1000 --laser-power 35 --laser-passes 2
```

`--laser-smax` steht in der `speed_map` der `config.yaml` und ist je nach Aufbau
255 oder 1000; die Leistung wird in Prozent davon gerechnet. Ohne
`--laser-verstanden` entsteht kein Laser-GCode. `--travel-as-g1` lehnt der
Export zusammen mit einem Laser ab, weil der Strahl auf einem G1-Leerweg
anbliebe.

Stift und Laser teilen sich weder Pin noch PWM-Frequenz (50 Hz gegen Kilohertz).
Die `config.yaml` trägt den zweiten Spindelblock auskommentiert bei.

## Genauigkeit

Nachgerechnet mit der eigenen Kinematik ([`docs/kinematik.md`](docs/kinematik.md))
liegt der Flaschenhals bei der **Riemendehnung**: 0,12 bis 0,83 mm über die
Fläche. Die Motorauflösung liegt mit 0,013 mm weit darunter, die Segmentierung
noch weiter.

Der wirksamste Hebel wird beim Einkauf entschieden: GT2 mit Stahlkern statt
Glasfaser drückt die Dehnung auf 0,17 mm und schiebt die Längsresonanz von 22 auf
50 Hz, weg von der Anregung durch typische Vorschübe. Was danach übrig bleibt,
rechnet `wallplotter-correct` gegen:

```bash
wallplotter-correct raster --steps 4 -o raster.gcode   # 16 Kreuze plotten
wallplotter-correct messen --steps 4                   # Vorlage zum Eintragen
wallplotter-correct anpassen                           # → korrektur.json
plot bild.svg --correction korrektur.json
```

Der Anpassungsschritt sagt, wieviel die Korrektur überhaupt wegnimmt. Wird der
Fehler nicht deutlich kleiner, war zu grob gemessen oder das Modell passt nicht.
Dann liegt es an der Mechanik.

Ein zweiter Effekt ist die Gondel selbst: sie pendelt mit 1,3 bis 2 Hz. Trifft
die Umkehrfrequenz einer Schraffur diesen Bereich, werden die Linien wellig.
`wallplotter.motion` warnt vorher und nennt zwei Auswege.

Wo die Anker am besten sitzen, lässt sich vor dem Bohren durchrechnen:

```bash
wallplotter-kinematics --compare   # Überstand und Höhe gegeneinander stellen
```

Die Tabelle zeigt je Position Auflösung, Riemenwinkel, größte Zugkraft und
Riemenbedarf. Anker höher zu setzen hilft dabei mehr, als sie weiter zu spreizen.

## Wenn etwas klemmt

```bash
wallplotter-doctor --host 192.168.1.42
```

Geht die Kette von vorn nach hinten durch: Installation, Kern, Standort,
Firmware-Konfiguration, Board. An der `config.yaml` prüft es drei Dinge — ob
FluidNC jeden Schlüssel darin kennt, ob die Ankermaße zum aktiven Standort
passen und ob die Datei noch das ist, was `wallplotter-firmware` schreiben würde.

War das Board zwischen zwei Farben aus, ist der Nullpunkt weg, denn `G92` ist
flüchtig:

```bash
# sofort: kalibrierte Ecke anfahren, Nullpunkt darüber wiederherstellen
wallplotter-calibrate goto bottom-left
wallplotter-calibrate zero --corner bottom-left

# dauerhaft: als G54-Versatz ablegen, der im NVS des ESP32 überlebt
wallplotter-calibrate zero --persistent
```

Der G54-Weg trägt nur zusammen mit einer reproduzierbaren Referenz. Eine
Referenzfahrt per `$H` gibt es hier nicht: `WallPlotter::canHome()` liefert in
FluidNC `false`, mit Endschalter wie ohne. Reproduzierbar wird die Referenz
mechanisch, indem man die Gondel an den Anschlag fährt und das Board dort neu
startet; FluidNC friert die Riemenlängen für kartesisch (0,0) beim Booten ein.
Ausführlich in der [Bauanleitung](docs/bauanleitung.md), Abschnitt 9.4.

## Als Bibliothek

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
| `config` | Wandmaße, Vorschübe, FluidNC-Zugang |
| `geometry` | Bounding-Box, Einpassen, Spiegeln, Längen- und Zeitschätzung |
| `pipeline` | SVG → Linien in mm (vpype), plus SVG-Vorschau |
| `imaging` | Fotos → Linien: hatch, stipple, tsp, spiral |
| `gcode` | Linien → GCode (`G0`/`G1`, `M3`/`M5`) |
| `toolhead` | Stiftkatalog: Servowerte, Vorschub, Strichbreite |
| `kinematics` | Auflösung, Riemenlängen, Zugkräfte |
| `calibration` | angefahrene Ecken → nutzbare Fläche mit Versatz |
| `location` | Standorte: Ankermaße und Fläche je Aufhängung |
| `correction` | Vorverzerrung gegen Riemendehnung und Messfehler |
| `motion` | Pendelresonanz, positionsabhängiger Vorschub |
| `timing` | Beschleunigung, Vorschubgrenzen, Laufzeitschätzung |
| `firmware` + `fluidnc_schema` | `config.yaml` erzeugen und gegen FluidNC halten |
| `upload` + `sdstore` | FluidNC-Web-API, Telnet-Kanal, Daten auf der SD-Karte |
| `resume` | abgebrochenen Plot fortsetzen |
| `patterns` | Testmuster für die Erstinbetriebnahme |
| `doctor` | Selbsttest über Installation, Standort, Konfiguration, Board |
| `wizard` | der geführte Ablauf, unabhängig von der Oberfläche |
| `design` | eine Palette für Website, Web-UI und Terminal |
| `cli`, `webapp`, `*_cli` | Kommandozeile und Web-Oberfläche |

CLI und Web-UI nutzen dieselben Funktionen; eine zweite Pipeline gibt es nicht.

## Tests

```bash
pytest
```

Tests, die vpype, NiceGUI, PyYAML oder `markdown` brauchen, überspringen sich
selbst, wenn das Paket fehlt. Im CI-Auftrag mit allen Extras ist dagegen alles
installiert, und ein übersprungener Test färbt den Lauf dort rot.

## Dokumentation

Alles gesetzt und verlinkt unter
**[andreass964.github.io/Wallplotter](https://andreass964.github.io/Wallplotter/)**.

| Dokument | Inhalt |
| --- | --- |
| **[Bauanleitung](docs/bauanleitung.md)** | vom Karton bis zum ersten Strich: Stückliste, Mechanik, Verkabelung, Inbetriebnahme, Fehlersuche |
| [Projekthandbuch](docs/wandplotter-handbuch.md) | Hardware, Kinematik, Firmware, Abläufe, Qualität, offene Punkte |
| [Gegenprüfung](docs/firmware-gegenpruefung.md) | was der FluidNC-Quelltext sagt und wo das Repo danebenlag |
| [Kinematik-Auswertung](docs/kinematik.md) | gerechnete Zahlen für eine Beispielaufhängung |
| [Projektidee](docs/projektidee.md) | Hardware, Mechanik, Entscheidungen |
| [Software-Roadmap](docs/software-roadmap.md) | Stufenplan und UI-Architektur |
| [FluidNC-Konfiguration](config/fluidnc-wallplotter.yaml) | die erzeugte `config.yaml` fürs Rodent-Board |

## Stand

Board unterwegs, Mechanik noch nicht gedruckt.

Ohne Hardware geprüft sind Geometrie, GCode-Export, Kalibrierlogik, Testmuster,
Kinematikrechnung, Bildverfahren und die Verdrahtung der Oberflächen: 728 Tests.
Die board-nahen laufen gegen eine Gegenstelle, die sich wie FluidNC verhält,
unbekannte Endpunkte also mit 404 beantwortet und bei `/command?plain=` nur
`$`-Kommandos versteht.

Hardware brauchen noch die Servowerte im Stiftkatalog, die Steckerbelegung am
eigenen Board und der Laserpfad. Die Reihenfolge zum Prüfen steht in der
[Bauanleitung](docs/bauanleitung.md), Abschnitt 10. Was die Gegenprüfung gegen
den FluidNC-Quelltext zutage gefördert hat und wie es behoben wurde, steht in
[docs/firmware-gegenpruefung.md](docs/firmware-gegenpruefung.md) und im
[CHANGELOG](CHANGELOG.md).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
