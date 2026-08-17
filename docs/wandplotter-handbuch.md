# Wandplotter — Projekthandbuch

Alles zum Projekt an einer Stelle: was gebaut wird, warum es so gebaut wird,
was gemessen und gerechnet wurde, und wie man es bedient.

*Stand: August 2026 · Version 0.3.0 · Repo: [AndreasS964/Wallplotter](https://github.com/AndreasS964/Wallplotter) · 478 Tests, CI grün*

---

## 1. Was gebaut wird

Ein selbstgebauter V-Plotter (Polargraph): Eine Gondel mit Stift hängt an zwei
Zahnriemen, die von zwei Motoren an den oberen Ecken auf- und abgewickelt
werden. Ansteuerung über GCode, Bilder und Zeichnungen werden von einer eigenen
Software dafür aufbereitet.

**Erste Wand:** Kletterwand-Keller, 2 m breit × 2,5 m hoch.

**Wichtig:** Der Plotter soll an **wechselnden Standorten** hängen. Ankerabstand,
Ankerhöhe und nutzbare Fläche sind deshalb keine Konstanten, sondern werden pro
Aufbau gemessen. Die Software ist danach gebaut — nirgends steht eine feste
Aufhängung im Code.

### Stand

| Bereich | Zustand |
| --- | --- |
| Software (Geometrie, GCode, Kalibrierung, Bildverfahren, UI, Werkzeugköpfe) | fertig und unter Test |
| Kinematik durchgerechnet | fertig, siehe Abschnitt 4 |
| FluidNC-Konfiguration | entworfen, Servo-Pin aus dem Schaltplan bestimmt (Abschnitt 5) |
| Board | bestellt, noch nicht da — Ablauf für den Tag X: [inbetriebnahme.md](inbetriebnahme.md) |
| Mechanik | noch nicht gedruckt |
| Alles Board-nahe (Upload, Jog, Status, Laser) | gegen den Firmware-Quelltext gebaut, gegen den Simulator gefahren, nie an einem Board |

---

## 2. Hardware

### Steuerung

**BIGTREETECH Rodent CNC Control Board V1.0** (gebraucht, eBay, ~30 €)

- ESP32-D0WD-V3 mit WLAN (802.11 b/g/n)
- 4× TMC2160-Treiber, SPI-konfigurierbar, bis 3 A — wir nutzen 2 Kanäle, 2 bleiben Reserve
- Eingangsspannung DC 24–56 V
- FluidNC-kompatibel, µSD-Slot, 5 Endstop-Eingänge
- „PWM-Ausgang (3–10 V)" — trotz des Namens ein **analoger** Ausgang für
  einen VFD, kein Servoanschluss (Abschnitt 5)

### Motoren und Antrieb

- **3× NEMA17 17HS4412P1-3**, 1,2 A, 0,45 Nm, 40 mm (2 im Einsatz, 1 Ersatz)
- GT2-Zahnriemen 6 mm, offen — **Stahlkern statt Glasfaser**, siehe Abschnitt 8
- GT2-Pulley 20 Zähne, Bohrung 5 mm
- Riemenbedarf: rund 7,8 m gesamt (längster Riemen 3,4 m pro Seite bei der
  2×2,5-m-Wand, plus Umlenkung und Reserve)

Nicht verwendet: NEMA17 0,4 Nm/1,7 A aus dem Fundus — zu hoher Strom für ruhigen
Betrieb am Board.

### Mechanik

- **Gondel/Pen-Holder:** „Makelangelo plotter head July 2026" von i-make-robots
  ([Thingiverse 7388981](https://www.thingiverse.com/thing:7388981)) —
  braucht 2× Lager 6706 (30×37×4 mm), eine Büroklammer.
  [Montageanleitung](https://mcr.dozuki.com/Guide/How+to+assemble+pen+holder+2026/52?lang=en)
- **Motorhalterung:** „Polargraph / Vertical Plotter Spring Tensioned Motor Mount"
  von flickeringsight ([Thingiverse 3440067](https://www.thingiverse.com/thing:3440067)) —
  federgespannt, gleicht Riemendehnung über die Zeit aus. Zwei Wellenhöhen-Varianten
  (20/22 mm), vor dem Druck gegen die Motoren prüfen.
- **Material: PETG**, nicht PLA — PLA bricht an den Gondelarmen.

### Noch zu beschaffen

- Servo MG90S (Metallgetriebe) für den Pen-Lift
- **Eigenes 5-V-Netzteil für den Servo** — blockiert zieht ein MG90S rund
  700 mA, das gehört nicht an die Bordversorgung
- 24-V-Netzteil für die Motoren, ausreichend für 2× 1,2 A + Reserve
- Aderendhülsen, Motor-Verlängerungskabel
- GT2-Riemen **mit Stahlkern** (siehe Abschnitt 8 — das ist der wichtigste Einkauf)

### Design-Entscheidungen

- **Selbstgewichtete Gondel** statt fallendem Gegengewicht → die 1,4×-Breite-Fallweg-
  Beschränkung des klassischen Polargraph-Designs entfällt, 2,5 m Deckenhöhe reichen.
- **Federgespannte Motorhalterung** statt schraubfixiert → gleicht Riemendehnung
  über Stunden aus, wichtig bei unbeaufsichtigten Plots.
- **Kein mechanischer Endschalter** — Referenz über physischen Anschlag oder
  sensorloses StallGuard-Homing (siehe Abschnitt 5).

---

## 3. Werkzeugköpfe

Seit 0.2.0 ist der Kopf keine Konstante mehr, sondern ein austauschbares Stück
Software (`wallplotter.toolhead`). `gcode.py` kennt kein `M3`, kein `M5` und
kein `G4` mehr — was das Werkzeug tut, liefert das Werkzeug.

### Was mitgeliefert wird

`plot --list-toolheads` zeigt den Katalog. **Alle Stiftwerte sind geschätzte
Startwerte, keine Messwerte**: Servohebel, Federweg und Halter sind an jedem
Aufbau anders. Nachgezogen wird mit `plot --pattern pen-test`.

| Kopf | S unten | Wartezeit | Breite | Vorschub | wofür |
| --- | --- | --- | --- | --- | --- |
| `fineliner`, `fineliner-rot` | 30 | 0,25 s | 0,5 mm | (Konfig) | der Normalfall |
| `kugelschreiber` | 38 | 0,20 s | 0,3 mm | 1800 | braucht Druck, verträgt Tempo |
| `marker` | 34 | 0,35 s | 2,0 mm | 1200 | blutet im Stillstand |
| `kreide` | 40 | 0,40 s | 5,0 mm | 900 | dunkle Wände, hoher Abrieb |
| `pinsel` | 26 | 0,50 s | 6,0 mm | 700 | Pinsel*stift*, kein Reservoir |
| `laser` | — | — | — | 600 | vorbereitet, nicht erprobt |

Mehrfarbig bekommt jede Strichfarbe ihren eigenen Kopf:
`--pen-for '#e02020=marker'`. Die `M0`-Pause nennt dann Farbe *und* Stift.

### Laser

Gegen die GRBL-Laserdoku und den FluidNC-Quelltext gebaut, **an keiner
Hardware erprobt**. Vier Unterschiede zum Stift, alle zwingend:

| | Stift am Servo | Laser |
| --- | --- | --- |
| Bedeutung von `S` | Position (Pulsbreite) | Leistung |
| `S` ohne Bewegung | normal und nötig | wirkungslos (M4) bis brandgefährlich (M3) |
| `G4` danach | zwingend | schädlich — brennt ein Loch |
| Leerweg | Position muss halten | muss aus sein |
| PWM | ~50 Hz | 1–100 kHz |

Daraus folgt: Stift und Laser können **weder denselben Pin noch dieselbe
Frequenz** benutzen. Der saubere Weg sind zwei Spindeln in einer `config.yaml`
(`PWM:` mit `tool_num: 0`, `Laser:` mit eigener `tool_num` und eigenem GPIO),
umgeschaltet mit `M6 T<n>`. Der Block steht auskommentiert in der
`config.yaml`. Teilen beide denselben Pin, hilft nur eine zweite YAML.

Die Software erzeugt `M4` (dynamische Leistung — bei konstanter Leistung
brennt ein Seilplotter jede Ecke durch), rechnet die Leistung in Prozent von
einem einstellbaren `s_max`, setzt `S0` vor jeden Leerweg, rollt mehrere
Durchgänge aus und **verweigert** `--travel-as-g1`. Ohne
`--laser-verstanden` entsteht kein Laser-GCode.

Seit 0.3.0 wird das erzeugte Programm zusätzlich **abgespielt**: Der Simulator
führt es mit `laser_mode` aus, schreibt den Spindelzustand modal mit und hält
fest, wo ein Leerweg oder eine Wartezeit mit eingeschaltetem Strahl angetreten
würde. Das ist der Unterschied zwischen „die richtigen Zeilen stehen da" und
„an keiner Stelle des Ablaufs brennt es" — ein `S0` an der falschen Stelle
fällt nur beim Ausführen auf. Eine Gegenprobe mit einem absichtlich falschen
Programm steht daneben, damit die Prüfung nicht nur sich selbst prüft.

### Was bewusst fehlt

| Kopf | warum nicht |
| --- | --- |
| Pinsel mit Reservoir | Nachtunken ist eine Fahrt zum Farbtopf, also Geometrie — Vorschau und Laufzeit würden lügen |
| Sprühdose | wie „Hektor" (Jürg Lehni); braucht Ventilvorlauf, also ebenfalls Geometrie |
| Schleppmesser | braucht Anschnittbögen an jeder Ecke |

Lieber gar kein Kopf als einer, der so tut. Alle drei wären machbar, sobald
das Werkzeug Geometrie beisteuern darf — das ist heute nicht vorgesehen.

---

## 4. Kinematik — die gerechneten Zahlen

Nachrechenbar mit `wallplotter-kinematics`, ausführlich in
[`kinematik.md`](kinematik.md). Für 2000 × 2500 mm, Anker 150 mm seitlich und
150 mm über der Fläche, Gondel 300 g:

| Größe | Wert |
| --- | --- |
| Auflösung pro Mikroschritt | 0,013 mm bester Fall, 0,049 mm schlechtester |
| Riemenauflösung | 80 Schritte/mm (20Z-Pulley = 40 mm/Umdrehung, 16 Mikroschritte) |
| Längster Riemen | 3,41 m pro Seite |
| Maximale Riemenkraft | 11,4 N gegen ~71 N Motorkraft → Faktor 6 Reserve |
| Kritische Zone | oberer Rand, Riemen laufen bis 165° auseinander |

**Ankerposition:** Die Höhe über der Fläche wirkt deutlich stärker als seitlicher
Überstand. 400 mm statt 100 mm drücken den schlechtesten Fall von 72 auf 23 µm
und die Zugkraft von 17 auf 4,5 N. Zu bedenken: Bei 2,5 m Wandhöhe und ~2,5 m
Deckenhöhe muss die Fläche entsprechend tiefer beginnen.

**Konditionierung:** Schlecht ist beides — Riemenwinkel nahe 0° (parallel) und
nahe 180° (in einer Linie, direkt unter den Ankern). Die Bewertung misst deshalb
den Sinus des Winkels, nicht den Winkel.

### Pro Standort: drei Maße

Nach dem Aufhängen, Gondel am Referenzpunkt, Nullpunkt gesetzt:

1. Abstand der beiden Umlenkpunkte
2. Länge des linken Riemens bis zur Gondel
3. dasselbe rechts

Daraus fallen die Ankerkoordinaten per Trilateration heraus — und damit
Kinematik-Auswertung, FluidNC-Konfiguration und erreichbare Fläche.

```bash
wallplotter-location new Keller --span 2300 --left 1450 --right 1470
wallplotter-location config Keller     # gibt den kinematics-Block aus
wallplotter-location show              # Auflösung, Kräfte, Riemenlänge
```

---

## 5. Firmware: FluidNC

Konfiguration: [`config/fluidnc-wallplotter.yaml`](../config/fluidnc-wallplotter.yaml).
Pinbelegung stammt aus [BTTs eigener rodent.yaml](https://github.com/bigtreetech/Rodent/blob/master/rodent.yaml)
— also echte Werte, keine geratenen.

### Die wichtigsten Einstellungen

| Einstellung | Wert | Grund |
| --- | --- | --- |
| Kinematik | `WallPlotter` | linker Riemen an X, rechter an Y |
| `steps_per_mm` | 80 | 20Z-Pulley, 40 mm/Umdrehung, 16 Mikroschritte |
| `run_amps` / `hold_amps` | 1,2 / 1,2 | Nennstrom; voller Haltestrom, damit die Gondel nicht absackt |
| `idle_ms` | 255 | Motoren gehalten lassen — die Gondel hängt am Riemen |
| Treibertyp | `tmc_5160` | so konfiguriert BTT die TMC2160 des Rodent, Register identisch |
| `segment_length` | 1–2 mm | 2 mm kosten nur 1 µm Bahntreue, siehe Abschnitt 8 |
| `laser_mode` | false | M4 würde die Leistung mit dem Tempo skalieren — für einen Servo Unsinn |

### Pen-Lift

Servo am PWM-Ausgang, angesteuert per **`M3 S<wert>` / `M5`** — bewusst *nicht*
`M280`, das ist Marlin/Makelangelo-Konvention und existiert in FluidNC nicht.

**Geklärt — und zwar negativ: Der Servo darf nicht an den PWM-Header.**

Der Anschluss heißt „PWM (3–10 V)", ist aber keiner. Der Schaltplan (Blatt 3,
„USB_485_PWM") führt das Signal des ESP32 über ein RC-Glied und einen LM358
auf `0_10V_Out`, mit 10-kΩ-Trimmpoti daneben — im Handbuch als „SP-PWM
potentiometer" (3.6) beschrieben. Das ist die analoge Drehzahlvorgabe für
einen VFD: Heraus kommt ein Gleichspannungspegel. Ein RC-Servo braucht die
Impulsfolge selbst, 50 Hz mit 1–2 ms Pulsbreite, und die überlebt das Filter
nicht.

Der Servo gehört deshalb an einen roh herausgeführten GPIO. Am leichtesten
erreichbar ist der **OLED-Header**: Er trägt `gpio.26` und `gpio.27`
(I2C), und beide sind frei, solange kein Display steckt. In der `config.yaml`
steht jetzt `gpio.26`.

Was am Board nachzumessen bleibt: Pinfolge und Versorgungsspannung des
OLED-Headers, und ob der MG90S am 3,3-V-Pegel des ESP32 sauber schaltet.
Seine *Versorgung* gehört ohnehin nicht ans Board — blockiert zieht er rund
700 mA und braucht ein eigenes 5-V-Netzteil mit gemeinsamer Masse. Weitere
unbelegte GPIOs laut rodent.yaml: 2, 4, 12, 13, 25; ob die irgendwo
herausgeführt sind, sagt erst das Board.

### Nullpunkt und Homing — die wichtigste Firmware-Entscheidung

Ohne Endschalter gibt es zwei Wege, und die Wahl entscheidet, ob der Nullpunkt
ein Ausschalten übersteht:

**a) Von Hand:** Gondel an den oberen Anschlag, dann `G92`.
Einfach, aber `G92` ist laut GRBL-Konvention flüchtig — es wird beim
Programmende (`M2`) verworfen. Bei mehrfarbigen Plots heißt das: nach jeder
Farbebene ist der Nullpunkt weg.

**b) Sensorlos per StallGuard:** Die TMC2160 können das. Mit `cycle: 1` und
passender StallGuard-Empfindlichkeit liefert `$H` reproduzierbare
Maschinenkoordinaten. Dann lässt sich der Flächenversatz als **G54 dauerhaft**
im NVS des ESP32 ablegen (`G10 L20`, in unserer CLI
`wallplotter-calibrate zero --persistent`).

Am Rodent hängt das am **DIAG-Anschluss**. Das Board-Handbuch (3.8) ist dabei
eindeutig: Wo DIAG benutzt wird, darf am zugehörigen Endstop-Eingang *kein*
Jumper stecken, und die Empfindlichkeit wird in der Software eingestellt,
nicht am Board.

**Empfehlung:** Variante b früh einrichten. Mehrfarbige Plots über mehrere Tage
hängen daran. Ein gespeicherter Versatz allein reicht nicht — ohne
reproduzierbare Referenzfahrt ist die Maschinenposition nach dem Einschalten
willkürlich, und der Versatz zeigt ins Leere.

### WLAN

Station-Modus, dauerhaft aktiv. SSID und Passwort **nicht** in die `config.yaml`
(landet sonst im Repo), sondern nach dem Flashen über das FluidNC-Terminal:

```
$Sta/SSID=...
$Sta/Password=...
$WiFi/Mode=STA
$Hostname=wandplotter
```

---

## 6. Software

Eigene Bild-zu-GCode-Pipeline. **Nicht** die Makelangelo-Software, weil die einen
Marlin-spezifischen Dialekt erzeugt (`M280`, proprietäre `D`-Codes,
`M101`-Header) — inkompatibel mit FluidNC/GRBL ohne Nacharbeit.

### Module

| Modul | Aufgabe |
| --- | --- |
| `config` | Wandmaße, Vorschübe, Bewegungsgrenzen, FluidNC-Zugang |
| `toolhead` | was an der Gondel hängt: Stiftkatalog, Laser, Schnittstelle dazwischen |
| `geometry` | Einpassen, Spiegeln, Douglas-Peucker, Linien sortieren, Längen-/Zeitschätzung |
| `pipeline` | SVG → Linien in mm (vpype), Ebenen je Strichfarbe, SVG-Vorschau |
| `imaging` | Fotos → Linien: hatch, stipple, tsp, spiral |
| `gcode` | Linien → GCode; kennt nur Bewegung, das Werkzeug liefert seine Zeilen selbst |
| `timing` | Laufzeit mit Beschleunigungsprofil, nachgebildet nach GRBLs Planer |
| `resume` | abgebrochenen Plot fortsetzen, modalen Zustand rekonstruieren |
| `kinematics` | Auflösung, Riemenlängen, Zugkräfte, Ankervergleich |
| `location` | Standorte: Ankermaße + Flächenkalibrierung je Aufhängung |
| `calibration` | angefahrene Ecken → nutzbare Fläche mit Versatz |
| `correction` | Vorverzerrung gegen Riemendehnung und Messfehler |
| `motion` | Pendelresonanz prüfen, positionsabhängiger Vorschub |
| `patterns` | Testmuster für die Erstinbetriebnahme |
| `channel` | WebSocket-Kanal zum Board: Status, G-Code, Realtime-Bytes |
| `upload` | FluidNC-Anbindung: SD-Upload über HTTP, Maschine über den Kanal |
| `simulator` | ein Board nachspielen, solange keines an der Wand hängt |
| `sdstore` | Standortdaten auf der SD-Karte des Boards |
| `cli` / `calibrate_cli` / `location_cli` / `correct_cli` / `resume_cli` | Kommandozeile |
| `doctor` | Selbsttest von der Installation bis zum Board |
| `webapp` | NiceGUI-Oberfläche, drei Reiter |

CLI und Web-UI nutzen dieselben Funktionen — es gibt bewusst keine zweite Pipeline.

### Installation

```bash
git clone https://github.com/AndreasS964/Wallplotter.git
cd Wallplotter
python -m venv .venv && source .venv/bin/activate
pip install -e ".[geometry,dev]"   # Kern + vpype + Tests
pip install -e ".[web]"            # Web-UI
pip install -e ".[photo]"          # alle vier Bildverfahren (nur Pillow)
```

Ohne Extras funktionieren GCode-Export, Kalibrierlogik, Testmuster und Upload.

`[hatch]` gibt es noch, damit alte Befehle laufen; seit 0.3.0 ist es dasselbe
wie `[photo]`.

### Web-UI

`python -m wallplotter.webapp`, dann `http://<pc-ip>:8080` — auch vom Handy an
der Wand. Drei Reiter für die drei Situationen vor der Wand:

- **Plotten** — Upload oder Testmuster, Verfahren für Fotos, Flächen- und
  Stiftparameter, Vorschau (Zeichenwege blau, Leerwege rot gestrichelt),
  Farbebenen einzeln startbar
- **Kalibrieren** — Jog-Pad, Nullpunkt, Ecken übernehmen und anfahren,
  Schiefstandswarnung, Standort anlegen samt Kinematik-Urteil
- **Maschine** — SD-Fortschritt, Pause/Weiter/Stopp

Auf dem Handy stapeln sich die Karten, das Jog-Pad steht oben.

### Web-API des Boards

Geprüft gegen die [ESP3D-v3-Dokumentation](https://esp3d.io/esp3d/version-3x/documentation/api/webhandlers/):

| Zweck | Aufruf |
| --- | --- |
| Datei auf SD schreiben | `POST /upload` (Dateiname im Multipart = voller Pfad, dazu Feld `<pfad>S` mit der Bytegröße) |
| SD auflisten | `GET /upload?path=/` |
| `$`-Kommando senden | `GET /command?plain=$SD/Run=…` |
| Halt / Weiter / Not-Aus | `GET /feedhold_reload`, `/cyclestart_reload`, `/restart_reload` |
| Status, G-Code, Realtime | **WebSocket** `ws://<host>/` |

Das steht so **nicht** in der ESP3D-Dokumentation, und die Abweichung ist
kein Detail. FluidNC bringt einen eigenen Webserver mit, der ESP3D nur
teilweise nachbildet. Nachgeprüft im Quelltext von v3.9.9, v4.0.4 und master —
in diesen Punkten identisch:

* **`/upload` ist die Karte, `/files` ist der Flash.** Genau andersherum als
  in der ESP3D-Doku. Ein `/sdfiles` gibt es überhaupt nicht; der Pfad landet
  im 404-Handler. (Zweimal falsch gelegen: erst `/upload` für den Flash
  gehalten, dann auf `/sdfiles` ausgewichen. Beide Male war die Quelle die
  Doku der falschen Firmware.)
* **`/command?plain=` taugt nur für `$`-Kommandos.** Der Handler ruft
  `settings_execute_line()` auf, und die Funktion schneidet das erste Zeichen
  ab, weil sie `$` oder `[` erwartet. Aus `G92 X0 Y0` wird damit die Suche
  nach einer Einstellung namens `92 X0 Y0`, aus `?` die Suche nach dem leeren
  Namen — was auf die Hilfeseite führt, nicht auf einen Statusreport. Kein
  Fehler, den man sieht: ein Nullpunkt, der nie gesetzt wurde.
* **Realtime-Bytes erreichen ihren Zweig auf diesem Weg nie.** Die Firmware
  fängt `?`, `!`, `~`, `0x18` und `0x85` im Zeichenstrom eines *Kanals* ab
  (`Channel::push()`), und einen Kanal gibt es nur über WebSocket oder
  seriell. Für Halt, Weiter und Not-Aus bringt FluidNC deshalb eigene
  HTTP-Endpunkte mit, die dasselbe Firmware-Ereignis auslösen. Diese drei
  Griffe gehen bei uns über **beide** Wege gleichzeitig: Der Kanal ist
  schneller, quittiert aber nichts — ist die Gegenstelle weggebrochen, ohne
  dass es schon aufgefallen wäre, gelingt das erste `send` trotzdem, weil die
  Bytes im Puffer des Betriebssystems landen. Ein Not-Halt wäre damit still
  verschluckt. Der HTTP-Endpunkt antwortet dagegen mit einem Statuscode.
* **`/command` antwortet mit HTTP 503, solange die Maschine fährt** — sofern
  `$HTTP/BlockDuringMotion` steht, und das ist die Voreinstellung
  (`DEFAULT_HTTP_BLOCKED_DURING_MOTION = 1`). Der WebSocket-Kanal ist davon
  nicht betroffen.

Der Kanal ist damit kein Luxus, sondern der Hauptweg: Er meldet von sich aus
alle 200 ms einen Statusreport (`WSChannel` setzt `setReportInterval(200)`),
nimmt G-Code und Realtime-Bytes an und trägt den laufenden Job. Die
Arbeitsteilung in `wallplotter.upload` folgt daraus — Dateien über HTTP,
alles Maschinennahe über `wallplotter.channel`.

Zum Lesen einer Datei von der Karte dient `$SD/Show=<pfad>` statt eines
HTTP-Pfads: Den WebDAV-Mount `/sd` gibt es erst ab FluidNC 4, `$SD/Show` in
jeder Fassung. Die Firmware verlangt dafür Idle oder Alarm — während eines
laufenden Plots liest dort niemand.

---

## 7. Arbeitsabläufe

### Was heute schon läuft — und was nicht

Die Software ist vollständig und getestet; verifiziert ist sie aber nur, soweit
sie ohne Maschine verifizierbar ist. Diese Trennung ehrlich zu halten, ist
wichtiger als eine Versionsnummer:

| | Zustand |
| --- | --- |
| Geometrie, Einpassen, Bildverfahren, GCode-Erzeugung | **läuft**, 478 Tests |
| Laufzeitschätzung, Fortsetzen, Vorverzerrung, Kalibrierlogik | **läuft**, gegen erzeugte Programme geprüft |
| Web-UI, alle sieben CLIs | **läuft**, ohne Board bedienbar |
| Upload, Jog, Status, Halt, `$SD/Run` | gegen den Firmware-**Quelltext** gebaut und gegen den mitgelieferten Simulator gefahren, **nie an einem Board** |
| Servo-Werte | **Platzhalter**, hängen an der Hardware |
| Servo-Pin | aus Schaltplan und Board-Handbuch bestimmt, am Board nachzumessen |
| Laserpfad | nach Doku und Quelltext gebaut, **nie erprobt** |

Wer heute `pip install -e .` macht, kann sofort: Bilder und SVGs in GCode
übersetzen, Vorschau ansehen, Laufzeit schätzen, Testmuster erzeugen, Standorte
und Flächen rechnen, Korrekturen anpassen. Was ein Board braucht, ist genau
das, was ohne Board auch keinen Sinn hätte.

Selbst nachsehen: `wallplotter-doctor`.

### Erstinbetriebnahme

1. Board flashen, `config.yaml` hochladen, WLAN einrichten
2. `wallplotter-doctor --host <ip>` — sagt, was noch fehlt
3. Motoren ohne Mechanik auf dem Tisch testen, Treiberstrom prüfen
4. Servo/Pen-Lift durchtesten, S-Werte für oben/unten ermitteln,
   in `toolhead.PENS` eintragen (die Katalogwerte sind Schätzungen)
5. Mechanik drucken (PETG), Wellenhöhen-Variante gegen die Motoren prüfen
6. Wandmontage, Riemen ablängen
7. Standort anlegen (drei Maße), Nullpunkt setzen
8. Ecken anfahren und aufnehmen
9. `--pattern frame` plotten — der erste ehrliche Test
10. `--pattern pen-test` — Servo-Wartezeit festklopfen
11. `--pattern feed-ramp` — brauchbare Höchstgeschwindigkeit finden
12. `--pattern grid` plotten und nachmessen → `wallplotter-correct`

### Neuer Standort

```bash
wallplotter-calibrate --host <ip> zero                        # am Referenzpunkt
wallplotter-location new Werkstatt --span 1800 --left 1200 --right 1200
wallplotter-location config Werkstatt                         # in die config.yaml
wallplotter-calibrate --host <ip> jog --dx -100               # in die Ecken fahren
wallplotter-calibrate --host <ip> record bottom-left          # ... und alle vier aufnehmen
wallplotter-location show                                     # Urteil zur Geometrie
```

Vier Ecken sind ideal (dann warnt das Tool bei schiefer Aufhängung), zwei
diagonale reichen. Das Ergebnis ist bewusst das größte Rechteck *innerhalb* der
angefahrenen Punkte — lieber etwas kleiner als neben der Wand.

### Plotten

```bash
plot bild.svg --location --upload --run          # Fläche vom aktiven Standort
plot foto.jpg --technique tsp --location --run
plot --pattern frame --location --run
```

Gestartet wird nur, wenn die Maschine frei ist. Läuft schon ein Programm,
lehnt der Start ab und nennt, was gerade läuft — FluidNC selbst würde das neue
Programm in das laufende hineinschachteln (`Job::nest`) und beide Zeichnungen
übereinanderlegen. Hochladen bleibt dabei erlaubt: Eine zusätzliche Datei auf
der Karte stört keinen laufenden Plot. `--trotzdem` überspringt die Prüfung.

### Mehrfarbig

```bash
plot bild.svg --layers              # je Strichfarbe eine Datei
plot bild.svg --layers --one-file   # eine Datei mit M0-Pausen
```

Alle Ebenen werden **gemeinsam** eingepasst — würde jede für sich skaliert,
fiele die Zeichnung auseinander. Getrennte Dateien sind für mehrstündige Plots
das Vernünftige: Schwarz heute, Rot morgen.

Nullpunkt nach Unterbrechung wiederherstellen:

```bash
wallplotter-calibrate goto bottom-left
wallplotter-calibrate zero --corner bottom-left     # sofort, flüchtig
wallplotter-calibrate zero --persistent             # dauerhaft als G54
```

### Ohne Rechner plotten

Der Plot läuft ohnehin autark: `$SD/Run` startet ihn von der Karte, danach
liest das Board die Datei selbst. Rechner zuklappen, WLAN abschalten — das
Bild wird trotzdem fertig. Genau darauf beruht auch `--from-board`: Nach einem
Abbruch weiß das Board, wie weit es kam, nicht der Rechner.

Was ohne Rechner **nicht** geht, ist das Starten. Dafür kennt FluidNC vier
Makros und Eingänge, die sie auslösen:

```yaml
control:
  macro0_pin: gpio.32:low:pu      # Taster „Plot starten"
  feed_hold_pin: gpio.33:low:pu   # Taster „Pause"
  cycle_start_pin: gpio.34:low    # Taster „Weiter"

macros:
  macro0: $H&$SD/Run=/wand.gcode
```

`&` trennt die Zeilen eines Makros — ein echtes Zeilenende lässt sich in
einem YAML-Wert nicht eintragen. Ohne Taster geht dasselbe über den Kanal:
`$Macros/Run=0`.

Die fünf Endstop-Eingänge des Rodent sind dafür frei, weil wir nicht über
Schalter referenzieren. Sie sind optokoppler-isoliert und haben eine wählbare
Spannung — genau das Richtige für Taster, die drei Meter neben dem Board an
der Wand hängen. Zu beachten: `gpio.34` und `gpio.35` sind am ESP32 nur
Eingang und **ohne internen Pull-up**; BTT setzt `:pu` deshalb auch nur an
`gpio.32` und `gpio.33`.

**Das `$H` im Makro ist kein Beiwerk.** Ein Startknopf ist erst dann sinnvoll,
wenn der Nullpunkt reproduzierbar ist (Abschnitt 5, StallGuard) — sonst
beginnt der Plot dort, wo die Gondel gerade zufällig hängt, und malt quer über
die Wand.

**Und bewusst nicht:** `startup_line0` mit einem `$SD/Run` belegen. Das
startet den Plot bei jedem Einschalten neu, also auch nach einem
Spannungsflackern um drei Uhr nachts — über das fertige Bild.

Proben lässt sich der ganze Ablauf vorher gegen den Simulator, Makro
eingetragen und `$Macros/Run=0` geschickt.

### Daten mitnehmen

```bash
wallplotter-location push --host <ip>   # Standorte auf die SD-Karte
wallplotter-location pull --host <ip>   # und zurück
```

Bewusst ohne automatisches Zusammenführen — welcher von zwei auseinander
gelaufenen Ständen der richtige ist, kann nur entscheiden, wer dabei war.

### Testmuster

| Muster | Prüft |
| --- | --- |
| `frame` | Fläche, Rechtwinkligkeit, Erreichbarkeit der Ecken |
| `grid` | Maßstab (mit dem Zollstock nachmessen) |
| `circles` | Verzerrung — aus Kreisen werden Eier, wo die Kinematik schwächelt |
| `pen-test` | Servo-Wartezeit — fehlende Strichanfänge heißen: `dwell_s` erhöhen |
| `feed-ramp` | Tempo bis zum Riemenspringen — erste wellige Linie zählt |

---

## 8. Druckqualität

### Fehlerbudget

Gerechnet mit der eigenen Kinematik. Die Rangfolge ist eindeutig und
kontraintuitiv:

| Quelle | Größenordnung |
| --- | --- |
| Motorauflösung | 0,013 mm |
| Segmentierung bei `segment_length: 1` | 0,0003 mm |
| **Riemendehnung** | **0,12 – 0,83 mm** |

Die Elektronik ist also um Faktor 40 besser als nötig. **Der Riemen ist der
Flaschenhals.** Über 3,4 m ist GT2 eine Feder, und die Zugkraft schwankt über
die Fläche zwischen 2,6 und 11 N.

### Riemenwahl

| Variante | Dehnung | Längsresonanz |
| --- | --- | --- |
| GT2 6 mm Glasfaser | 0,83 mm | 22 Hz |
| **GT2 6 mm Stahlkern** | **0,17 mm** | **50 Hz** |
| GT2 10 mm Stahlkern | 0,10 mm | 65 Hz |
| HTD 5M 9 mm | 0,06 mm | 80 Hz |
| Stahlseil auf Trommel | 0,02 mm | 150 Hz |

**Stahlkern reicht.** Steifer geht immer, lohnt aber nicht — der Rest liegt
längst unter der Strichbreite eines Filzstifts. Seil auf Trommel handelt sich
zwei neue Probleme ein: veränderlicher Wickelradius und Schlupf; der Formschluss
eines Zahnriemens ist mehr wert als die zusätzliche Steifigkeit.

Zwei weitere Gründe für Stahlkern:

- **Längsresonanz:** Riemen und Gondel bilden einen Schwinger. Glasfaser landet
  bei 22 Hz — und 1500 mm/min bei 1 mm Segmentlänge regen mit 25 Hz genau dort
  an. Stahlkern schiebt das auf 50 Hz. Zusätzlich hilft `segment_length: 2`:
  kostet 1 µm Bahntreue und halbiert die Anregung auf 12 Hz.
- **Kriechen:** Glasfaser kriecht unter Dauerlast; bei mehrstündigen Plots
  driftet das.

### Vorverzerrung

Was der Riemen übrig lässt, rechnet die Software gegen. Zwei Wege, gleiche
Schnittstelle:

| Ansatz | Restfehler | Aufwand |
| --- | --- | --- |
| ohne Korrektur | 0,83 mm | — |
| Polynom affin / bilinear | 0,53 mm | 3–4 Messpunkte |
| Polynom kubisch | 0,15 mm | 16 Messpunkte |
| **Dehnungsmodell** | **< 0,01 mm** | **9 Messpunkte, ein Materialwert** |

Das Modell gewinnt deutlich, weil es die *Form* des Fehlerfelds kennt, während
das Polynom sie nur nachahmt. Der Weg ist also: Raster plotten, nachmessen,
`StretchCorrection.fit_stiffness` bestimmt daraus die Riemensteifigkeit. Das
Polynom bleibt die Ergänzung für alles, was das Modell nicht kennt (schiefe
Ankermaße, Pulley-Toleranz).

### Pendel

Die Gondel schwingt mit 1,3–2 Hz (je nach Abstand Stift/Aufhängung). Jede
Richtungsumkehr gibt ihr einen Stoß; trifft die Umkehrfrequenz einer Schraffur
die Eigenfrequenz, werden die Linien wellig.

| Bahnabstand | 600 mm/min | 1500 mm/min | 3000 mm/min |
| --- | --- | --- | --- |
| 3 mm | **1,7 Hz — kritisch** | 4,2 Hz | 8,3 Hz |
| 5 mm | 1,0 Hz | 2,5 Hz | 5,0 Hz |
| 10 mm | 0,5 Hz | **1,2 Hz — kritisch** | 2,5 Hz |

`wallplotter.motion.resonance_warning` prüft das vor dem Plot und nennt zwei
Auswege. `conditioning_feeds` drosselt zusätzlich dort, wo die Kinematik schlecht
konditioniert ist — welche Zone das ist, hängt an der Aufhängung und wird
ausgerechnet statt angenommen.

### Verfahren für Fotos

Bei Bildvorlagen entscheidet das Verfahren mehr als die Mechanik:

| Verfahren | Charakter | Stifthübe |
| --- | --- | --- |
| `hatch` | Schraffur nach Helligkeitsstufen, grafisch | viele |
| `stipple` | Punktraster, fotografisch | einer je Punkt |
| `tsp` | dieselben Punkte als eine durchgehende Linie | keine |
| `spiral` | Spirale mit dunkelheitsabhängiger Auslenkung | keine |

`tsp` und `spiral` zeichnen ohne abzusetzen — damit entfallen Servo-Artefakte
und Pendelstöße durch Leerfahrten ganz.

**Zeitrechnung:** Flächendeckende Verfahren brauchen Zeichenweg ≈ Fläche ÷
Bahnabstand. Auf 2 × 2,5 m sind das bei 25 mm Bahnabstand rund 2,5 Stunden,
unabhängig vom Algorithmus. `tsp` kommt mit ~1,4 h davon, weil es nur die
dunklen Stellen bedient. Die Statistik zeigt das vor jedem Plot an.

---

## 9. Entscheidungen und verworfene Optionen

| Verworfen | Grund |
| --- | --- |
| Makelangelo-Software für den Export | Marlin-Dialekt, inkompatibel mit FluidNC |
| AliExpress-Fertigkits (~30–40 €) | zu klein (A4/A3), Perlenkette statt Riemen |
| Makelangelo 5 als Kaufkit | teurer, Versand/Zoll aus Kanada, weniger flexibel |
| Home-Assistant-Integration / Pi-Bridge | kein Mehrwert für dieses Projekt |
| CNC-Shield V4 statt Rodent | Lötarbeit an Microstepping-Pins nötig |
| Separates fallendes Gegengewicht | durch selbstgewichtete Gondel ersetzt |
| Natives GUI (Qt/Tkinter) | mehr Aufwand, schlechter vom Handy erreichbar |
| Browser-only via Pyodide/WASM | zig MB Bundle, vpype-Plugins ungetestet |
| Streamlit | führt bei jeder Interaktion das Skript neu aus |
| Kalibrierung als reine Polynom-Messung | Modell schlägt Polynom um Faktor 10 |
| Paket `hatched` für die Schraffur | setzt Shapely 1.x voraus, vpype verlangt 2.x — zusammen nicht installierbar |

---

## 10. Offene Punkte

**Bis das Board da ist — nichts davon ist an echter Hardware verifiziert:**

- Die Web-API ist inzwischen gegen den **Quelltext** von FluidNC geprüft
  (v3.9.9, v4.0.4, master) statt gegen die ESP3D-Dokumentation, und die ganze
  Kette läuft gegen den Simulator im Repo. Was fehlt, ist die echte Firmware
  auf echter Hardware — Handshake und Rahmenformat sind Standard, aber ein
  ESP32 unter Last ist kein Python-Server auf localhost.
- Realtime-Bytes (`!`, `~`, `0x18`, `0x85`) gehen jetzt über den
  WebSocket-Kanal, weil sie über HTTP ihren Zweig in der Firmware gar nicht
  erreichen. Der Not-Halt gehört trotzdem als Erstes ausprobiert — und zwar
  beide Wege: über den Kanal und über `/feedhold_reload`.
- Servo-S-Werte für Pen-Up/Down sind Platzhalter, ebenso alle Werte im
  Stiftkatalog
- Servo-Pin: Der PWM-Header ist ausgeschieden (Abschnitt 5), `gpio.26` am
  OLED-Header ist die begründete Wahl — Pinfolge, Spannung und Pegelfestigkeit
  sind am Board zu messen
- Der komplette Laserpfad an echter Hardware. Das erzeugte Programm läuft
  inzwischen durch den Simulator, ohne dass der Strahl je auf einem Leerweg
  oder in einer Wartezeit anstünde — aber ein Simulator brennt auch nichts an.

**Seit der Nachprüfung geklärt:**

- **`M0` versteht FluidNC als Pause.** `GCode.cpp` setzt
  `ProgramFlow::Paused` und löst einen Feed Hold aus; heraus kommt man mit
  Cycle Start — also mit demselben „Weiter" wie aus jedem anderen Halt. Damit
  trägt `--layers --one-file` mit `M0`-Pause zum Stiftwechsel.
- **`/sdfiles` gibt es nicht.** Die Karte liegt auf `/upload`, der Flash auf
  `/files` (Abschnitt 6).
- **`$HTTP/BlockDuringMotion` steht ab Werk an** und lässt `/command` während
  jeder Fahrt mit HTTP 503 antworten.
- **Der PWM-Header ist kein PWM-Ausgang**, sondern eine analoge 0–10-V-Vorgabe
  für einen VFD (Abschnitt 5).

**Danach zu bestimmen:**

- Riemensteifigkeit aus einem gemessenen Raster → Dehnungskorrektur scharf
  stellen (`wallplotter-correct anpassen --model dehnung`)
- StallGuard-Empfindlichkeit fürs sensorlose Homing
- Brauchbare Höchstgeschwindigkeit über `--pattern feed-ramp`
- Ob die Laufzeitschätzung stimmt: `--pattern grid` plotten, Uhr mitlaufen
  lassen, mit der Angabe im GCode-Kopf vergleichen. Weicht sie ab, sind
  `--acceleration` und `--max-rate` die Stellschrauben.

**Ideen, noch nicht gebaut:**

- Farbseparation für Fotos (CMY oder feste Stiftfarben, je Farbe eigene Dichte)
- Flow-Field-Verfahren als fünfte Bildtechnik
- Verlauf vergangener Plots mit Thumbnail
- Bewegung im Simulator wirklich fahren (Rampen, Segmentierung) statt die
  Position den X/Y-Werten der abgespielten Zeilen folgen zu lassen

---

## 11. Quellen

- FluidNC: [Repo](https://github.com/bdring/FluidNC) · [Wiki](http://wiki.fluidnc.com/)
- Rodent-Board: [BTT Wiki](https://global.bttwiki.com/Rodent.html) ·
  [rodent.yaml](https://github.com/bigtreetech/Rodent/blob/master/rodent.yaml)
- ESP3D Web-Handler: [esp3d.io](https://esp3d.io/esp3d/version-3x/documentation/api/webhandlers/)
- vpype: [Dokumentation](https://vpype.readthedocs.io/)
- NiceGUI: [nicegui.io](https://nicegui.io/documentation)
- G54 vs. G92: [CNCCookbook](https://www.cnccookbook.com/g54-g92-g52-work-offsets-cnc-g-code/)
