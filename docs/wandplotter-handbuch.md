# Wandplotter — Projekthandbuch

Alles zum Projekt an einer Stelle: was gebaut wird, warum es so gebaut wird,
was gemessen und gerechnet wurde, und wie man es bedient.

*Stand: August 2026 · Version 0.2.0 · Repo: [AndreasS964/Wallplotter](https://github.com/AndreasS964/Wallplotter) · 461 Tests*

> **Seit der [Gegenprüfung gegen den FluidNC-Quelltext](firmware-gegenpruefung.md)
> sind mehrere Aussagen in diesem Handbuch überholt.** Sie sind unten
> richtiggestellt; wo eine Passage ganz falsch war, steht das dabei. Für den
> Aufbau ist die [Bauanleitung](bauanleitung.md) maßgeblich.

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
| FluidNC-Konfiguration | gegen den Quelltext geprüft und korrigiert, Servo-Pin geklärt |
| Board | bestellt, noch nicht da |
| Mechanik | noch nicht gedruckt |
| Board-nah: Upload, Status, Halt, Jog, `G92` | auf die echten Wege umgestellt, gegen eine FluidNC-Gegenstelle getestet |
| Board-nah an echter Hardware | **noch nicht gelaufen** — Prüfreihenfolge in der [Bauanleitung](bauanleitung.md), Abschnitt 10 |
| Laser | nach Doku gebaut, nie an Hardware |

---

## 2. Hardware

### Steuerung

**BIGTREETECH Rodent CNC Control Board V1.0** (gebraucht, eBay, ~30 €)

- ESP32-D0WD-V3 mit WLAN (802.11 b/g/n)
- 4× TMC2160-Treiber, SPI-konfigurierbar, bis 3 A — wir nutzen 2 Kanäle, 2 bleiben Reserve
- Eingangsspannung DC 24–56 V
- FluidNC-kompatibel, µSD-Slot, PWM-Ausgang, 5 Endstop-Eingänge

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
- **ein** 24-V-Netzteil, 4–5 A: 2× 1,2 A Motoren plus Reserve. Servo, Endstops
  und Logik kommen aus der Platine (Board nimmt DC 24–56 V)
- Aderendhülsen, Motor-Verlängerungskabel
- GT2-Riemen **mit Stahlkern** (siehe Abschnitt 8 — das ist der wichtigste Einkauf)

### Design-Entscheidungen

- **Selbstgewichtete Gondel** statt fallendem Gegengewicht → die 1,4×-Breite-Fallweg-
  Beschränkung des klassischen Polargraph-Designs entfällt, 2,5 m Deckenhöhe reichen.
- **Federgespannte Motorhalterung** statt schraubfixiert → gleicht Riemendehnung
  über Stunden aus, wichtig bei unbeaufsichtigten Plots.
- **Kein mechanischer Endschalter** — referenziert wird über den physischen
  Anschlag plus Neustart des Boards an dieser Stelle (siehe Abschnitt 5; die
  früher hier genannte StallGuard-Variante geht mit dieser Kinematik nicht).
  Die fünf Endstop-Eingänge bleiben damit frei — und tragen jetzt die Taster
  für Halt, Pause und Weiter.

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
wallplotter-location show              # Auflösung, Kräfte, Riemenlänge
wallplotter-firmware config --location Keller --out config.yaml
```

---

## 5. Firmware: FluidNC

Konfiguration: [`config/fluidnc-wallplotter.yaml`](../config/fluidnc-wallplotter.yaml).
Pinbelegung stammt aus [BTTs eigener rodent.yaml](https://github.com/bigtreetech/Rodent/blob/master/rodent.yaml)
— also echte Werte, keine geratenen.

### Die Datei wird erzeugt

Sie ist ein **Erzeugnis**, kein gepflegtes Dokument. Geschrieben hat sie
`wallplotter-firmware` aus derselben Beschreibung, mit der auch die Vorschau
rechnet; der Aufruf, der sie wiederherstellt, steht in ihrer eigenen Kopfzeile.

```bash
wallplotter-firmware config --location Keller --out config/fluidnc-wallplotter.yaml
wallplotter-firmware pruefen config/fluidnc-wallplotter.yaml   # ohne Board
wallplotter-firmware pruefen --host <ip>                       # die vom Board
wallplotter-firmware diff   config/fluidnc-wallplotter.yaml    # von Hand geändert?
wallplotter-firmware push   --host <ip> --location Keller
```

Der Grund ist nicht Bequemlichkeit, sondern dass es vorher **zwei**
Beschreibungen derselben Maschine gab: die Python-Seite und eine YAML-Datei,
die jemand von Hand nachzog. Zwei Beschreibungen laufen auseinander, und zwar
— die Vorschau rechnet mit den gemessenen Ankermaßen, das Board mit denen von
vor drei Wochen, und das Bild an der Wand ist verzerrt. `wallplotter-doctor`
merkte das schon vorher an; nur muss man ihn dafür aufrufen, und sein Rat
verwies auf `wallplotter-location config`, das nur den Kinematikblock liefert.

Drei Kopplungen hält der Erzeuger fest, die von Hand schon danebengegangen sind:

| Was | Folgt woraus | Was von Hand schiefging |
| --- | --- | --- |
| `left_anchor_*` / `right_anchor_*` | den drei Maßen des Standorts | zwei Stellen, die auseinanderliefen |
| `steps_per_mm` | Vollschritte × Mikroschritte ÷ (Zähne × Riementeilung), also 200 × 16 ÷ 40 = 80 | Mikroschritte geändert, Schritte vergessen — Faktor daneben |
| `speed_map` | Impulsfenster ÷ PWM-Periode | `0=0.000% 100=100.000%` — der ganze Servoweg lag zwischen S5 und S10 |

`pruefen` schaut mit zwei Blickwinkeln auf die Datei.

Erstens mit dem eines YAML-Parsers: jeder Schlüssel gegen eine Liste, die aus
dem FluidNC-Quelltext gezogen ist (`handler.item()` / `handler.section()`, Stand
`8a0f8c8`). Der Unterschied, den sie macht, ist scharf: Ein **unbekannter
Schlüssel** setzt das Board in ConfigAlarm, dann fährt nichts mehr; ein **Wert
außerhalb des Bereichs** wird von der Firmware geklemmt und nur gemeldet
(`NutsBolts.h`, `constrain_with_message`). Beides sagt das Werkzeug getrennt.

Zweitens mit dem von FluidNCs **eigenem Tokenizer**, denn der weicht von YAML
ab. Am folgenreichsten hier:

> Hinter einen Wert gehört kein Kommentar. `parseValue()` nimmt bei einem
> unquotierten Wert den ganzen Rest der Zeile — `idle_ms: 255 # gehalten
> lassen` wird also als Zahl `255 # gehalten lassen` gelesen, scheitert, und das
> Board geht in **ConfigAlarm**. Bei `true`/`false` wird stillschweigend `false`
> daraus, bei einem Pin gibt es `Setting up pin … failed`.

Ein YAML-Parser sieht davon nichts — deshalb beide Blickwinkel. In BTTs eigener
`rodent.yaml` trägt aus demselben Grund keine Zeile einen Kommentar hinter dem
Wert. Ausführlich in der [Gegenprüfung](firmware-gegenpruefung.md),
Abschnitt 2.6.

`push` schreibt in den **Flash**, nicht auf die SD-Karte. Das ist keine
Feinheit: FluidNC liest die Konfiguration über `FileStream(filename, "rb",
LocalFS)`, und ein Upload auf die Karte gälte als geglückt, während das Board
weiter mit der alten Datei fährt. Die bisherige Fassung wird vorher
heruntergeladen und gesichert.

### Die wichtigsten Einstellungen

| Einstellung | Wert | Grund |
| --- | --- | --- |
| Kinematik | `WallPlotter` | linker Riemen an X, rechter an Y |
| `steps_per_mm` | 80 | 20Z-Pulley, 40 mm/Umdrehung, 16 Mikroschritte |
| `run_amps` / `hold_amps` | 1,2 / 1,2 | Nennstrom; voller Haltestrom, damit die Gondel nicht absackt |
| `idle_ms` | 255 | Motoren gehalten lassen — die Gondel hängt am Riemen |
| Treibertyp | `tmc_5160` | so konfiguriert BTT die TMC2160 des Rodent, Register identisch |
| `segment_length` | 1–2 mm | 2 mm kosten nur 1 µm Bahntreue, siehe Abschnitt 8 |
| ~~`laser_mode`~~ | — | **Gibt es nicht.** Der Schlüssel existiert in FluidNC nicht und hätte das Board in ConfigAlarm gesetzt; er ist aus der `config.yaml` entfernt. Lasermodus ist die Klasse der aktiven Spindel, keine Einstellung — `pwm:` ist bereits nicht rate-adjusted. |
| `control:` | drei Taster | Halt, Pause und Weiter über die freien Endstop-Eingänge — über HTTP geht keines davon (Abschnitt 6) |

### Pen-Lift

Servo am PWM-Ausgang, angesteuert per **`M3 S<wert>` / `M5`** — bewusst *nicht*
`M280`, das ist Marlin/Makelangelo-Konvention und existiert in FluidNC nicht.

**Offener Punkt:** Der PWM-Pin ist der einzige geratene Wert in der Konfiguration.
BTTs eigene Datei nutzt einen VFD über RS485 und belegt gar keinen PWM-Port. Zwei
**Erledigt.** Das Rodent-Handbuch V1.03 (Pinbild S. 6/7, Schaltplan S. 12) klärt
es: `gpio.25` liegt als `Sp-Enable` auf dem 3-poligen Stecker CN51 heraus, über
100 Ω und auf 3,3 V geklemmt — genau richtig für einen Servo-Signaleingang. Der
Stecker mit der Aufschrift „PWM" ist dagegen ein analoger 3–10-V-Ausgang mit
Trimmpoti und wäre falsch gewesen.

Die **Versorgung** des Servos darf nicht vom +5-V-Pin desselben Steckers kommen —
dort liegen ebenfalls 100 Ω. Hier stand daraufhin „dafür braucht es ein eigenes
Netzteil", und das war zu kurz gedacht: Unbrauchbar ist nur dieser eine Pin, nicht
der 5-V-Zweig der Platine. Ohne Vorwiderstand kommt er am **OLED-Stecker** (Pin
`+5V`) heraus und an jedem **Endstop-Stecker** (Pin `V-Lim`, wenn die
`SW_VCC`-Brücke auf +5 V steckt). Dazu ein Elko an der Gondel — dann genügt **ein**
Netzteil für die ganze Maschine. Verdrahtung und die Messung, mit der man das
absichert, in der [Bauanleitung](bauanleitung.md), Abschnitt 7.4.

Ebenfalls richtiggestellt, und diesmal in die andere Richtung: Hier stand, gpio.2,
4, 12 und 13 seien am Rodent gar nicht herausgeführt. Für **gpio.2, gpio.4 und
gpio.12 ist das falsch** — sie schalten die drei V-MOS-Leistungsausgänge
(DC 12–36 V, bis 5 A). Als Servosignal taugen sie trotzdem nicht (Low-Side-MOSFETs,
keine Logikausgänge), aber als Air Assist, Absaugung oder Arbeitslicht sehr wohl:
`coolant: flood_pin: gpio.4`, geschaltet mit `M8`/`M9`.

### Nullpunkt und Homing — die wichtigste Firmware-Entscheidung

Dieser Abschnitt stand hier falsch. Er bot zwei Wege an, und der empfohlene
existiert nicht.

**Es gibt kein `$H`.** Die WallPlotter-Kinematik nimmt keine Referenzfahrt an:

```cpp
bool WallPlotter::canHome(AxisMask axisMask) {
    log_error("This kinematic system cannot home");
    return false;
}
```

Das gilt mit Endschalter wie ohne und auch mit sensorlosem StallGuard. Die
TMC2160 *können* StallGuard, und das Rodent führt die DIAG-Ausgänge sogar per
Steckbrücke auf die Endstop-GPIOs — nur nützt das dieser Kinematik nichts.

**Und `M2` verwirft `G92` nicht.** `GCode.cpp` setzt beim Programmende motion,
plane, distance, feed, `coord_select` (auf G54), Spindel und Kühlung zurück;
`gc_state.coord_offset` bleibt stehen. Flüchtig ist `G92` trotzdem — aber erst
beim Ausschalten oder Soft-Reset (`SettingsDefinitions.cpp`: G54–G59 werden im
NVS gesichert, G92 nicht).

**Was tatsächlich trägt:** Der Nullpunkt entsteht mechanisch. FluidNC friert die
Riemenlängen für kartesisch (0,0) **beim Booten** ein
(`WallPlotter::init` → `xy_to_lengths(0, 0, …)`), der Maschinennullpunkt ist
also exakt die Gondelposition in diesem Augenblick. Daraus folgt der Ablauf:

1. Gondel an den Anschlag / Referenzpunkt bringen,
2. **das Board dort neu starten**,
3. erst dann messen, kalibrieren und plotten.

Wer nach dem Booten erst joggt und dann `G92` setzt, bekommt Ankermaße, die um
die Jog-Strecke daneben liegen — ohne Fehlermeldung. Ein dauerhafter Versatz per
`G10 L20 P1` (G54, liegt im NVS) trägt erst zusammen mit dieser
reproduzierbaren mechanischen Referenz.

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
| `upload` | FluidNC-Web-API: SD-Upload, `$SD/Run`, Status, Jog, Pause/Stop |
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
pip install -e ".[photo]"          # Foto-Zweig: stipple, tsp, spiral (nur Pillow)
pip install -e ".[hatch]"          # zusätzlich Schraffur — `hatched` zieht vpype[all],
                                   # OpenCV, scikit-image und matplotlib nach
```

Ohne Extras funktionieren GCode-Export, Kalibrierlogik, Testmuster und Upload.

### Web-UI

`python -m wallplotter.webapp`, dann `http://<pc-ip>:8080` — auch vom Handy an
der Wand. Drei Reiter für die drei Situationen vor der Wand:

- **Plotten** — Upload oder Testmuster, Verfahren für Fotos, Flächen- und
  Stiftparameter, Vorschau (Zeichenwege in der Stiftfarbe, Leerwege rot
  gestrichelt), Farbebenen einzeln startbar
- **Kalibrieren** — Jog-Pad, Nullpunkt, Ecken übernehmen und anfahren,
  Schiefstandswarnung, Standort anlegen samt Kinematik-Urteil
- **Maschine** — SD-Fortschritt, Pause/Weiter/Stopp

Auf dem Handy stapeln sich die Karten, das Jog-Pad steht oben.

### Web-API des Boards

Diese Tabelle stand hier falsch. Sie war gegen die allgemeine ESP3D-v3-Doku
geschrieben; FluidNCs Webserver ist eine eigene Implementierung. Was
`WebUI/WebUIServer.cpp` tatsächlich registriert:

| Zweck | Aufruf | Status |
| --- | --- | --- |
| Datei auf **SD** schreiben | `POST /upload` (multipart) | `/sdfiles` gibt es nicht |
| Datei von SD lesen | `GET /sd/<datei>` (WebDAV) | funktioniert |
| SD auflisten | `GET /upload?path=/` | `action=list` gibt es nicht |
| Datei in den **Flash** | `POST /files` | |
| `$`-Kommando senden | `GET /command?plain=$SD/Run=/x.gcode` | nur `$…` und `[ESP…]` |
| GCode / Realtime senden | **TCP-Kanal, Port 23** | ab Werk an (`$Telnet/Enable`) |
| … alternativ über HTTP | `GET /command?cmd=<x>` | braucht einen offenen WebSocket |
| Pause | `GET /feedhold_reload` | |
| Weiter | `GET /cyclestart_reload` | |
| Soft-Reset | `GET /restart_reload` | |

Es ist also genau umgekehrt zu dem, was oben stand: **`/files` ist der Flash,
`/upload` ist die Karte.** Und `?plain=` reicht die Zeile an
`settings_execute_line()` weiter, das nur `$name=wert` versteht — `?`, `G92`,
`G10 L20` und die Realtime-Bytes kommen darüber nicht durch. Details:
[Gegenprüfung](firmware-gegenpruefung.md), Abschnitt 2.3.

`wallplotter.upload` ist danach gebaut: **Dateien über HTTP, Kommandos über den
TCP-Kanal**. Der ist bei FluidNC ab Werk an (`DEFAULT_TELNET_STATE = 1`, Port
23) und ein vollwertiger `Channel` — dort wirken Realtime-Zeichen, `?` liefert
einen Statusbericht, GCode wird ausgeführt. Für `$`-Kommandos bleibt der
HTTP-Weg als Rückfallebene, falls Telnet abgeschaltet wurde.

Dazu: Solange `$HTTP/BlockDuringMotion` an ist (Werkszustand), antwortet
`/command` während jeder Bewegung nur mit HTTP 503.

---

## 7. Arbeitsabläufe

### Was heute schon läuft — und was nicht

Die Software ist vollständig und getestet; verifiziert ist sie aber nur, soweit
sie ohne Maschine verifizierbar ist. Diese Trennung ehrlich zu halten, ist
wichtiger als eine Versionsnummer:

| | Zustand |
| --- | --- |
| Geometrie, Einpassen, Bildverfahren, GCode-Erzeugung | **läuft**, 461 Tests |
| Laufzeitschätzung, Fortsetzen, Vorverzerrung, Kalibrierlogik | **läuft**, gegen erzeugte Programme geprüft |
| Web-UI, alle sechs CLIs | **läuft**, ohne Board bedienbar |
| Upload, Jog, Status, `$SD/Run` | nach ESP3D-Doku gebaut, **nie an einem Board** |
| Servo-Werte, PWM-Pin, `M0`-Pause | **Platzhalter**, hängen an der Hardware |
| Laserpfad | nach Doku und Quelltext gebaut, **nie erprobt** |

Wer heute `pip install -e .` macht, kann sofort: Bilder und SVGs in GCode
übersetzen, Vorschau ansehen, Laufzeit schätzen, Testmuster erzeugen, Standorte
und Flächen rechnen, Korrekturen anpassen. Was ein Board braucht, ist genau
das, was ohne Board auch keinen Sinn hätte.

Selbst nachsehen: `wallplotter-doctor`.

### Erstinbetriebnahme

Geführt in einem Aufwasch:

```bash
wallplotter-setup --host <ip>
```

Acht Schritte, jeder mit Begründung, jeder nachgeprüft, jederzeit abbrechbar und
fortsetzbar (`--status`, `--ab <schritt>`). Der Ablauf steht in
`wallplotter/wizard.py` und weiß nichts von einem Terminal — er redet über ein
schmales `Dialog`-Protokoll mit der Welt, damit derselbe Ablauf später auch in
der Web-UI laufen kann und im Test gegen ein Skript aus Antworten prüfbar ist.

Von Hand ist es dieselbe Reihenfolge:

1. Board flashen, `config.yaml` erzeugen und in den Flash laden
   (`wallplotter-firmware push --host <ip>`), WLAN einrichten
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
wallplotter-firmware push --host <ip> --location Werkstatt    # config.yaml + Neustart
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
| `hatch` | Schraffur nach Helligkeitsstufen, grafisch | viele — **derzeit nicht lauffähig**: `hatched` 0.2.0 verträgt sich nicht mit Shapely 2 |
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

---

## 10. Offene Punkte

**Bis das Board da ist — nichts davon ist an echter Hardware verifiziert:**

**Erledigt** (Belege in der [Gegenprüfung](firmware-gegenpruefung.md)):

- Die Endpunkte stimmten nicht: `/sdfiles` gibt es nicht, `/upload` ist die
  Karte, `/files` der Flash. Umgestellt.
- Die Realtime-Bytes über `?plain=` waren wirkungslos und meldeten dabei
  HTTP 200. Halt, Pause und Weiter laufen jetzt über `/feedhold_reload`,
  `/cyclestart_reload` und `/restart_reload`, Status und GCode über den
  TCP-Kanal. Zusätzlich steht ein `control:`-Block mit drei Tastern in der
  `config.yaml` — ein Not-Halt, der WLAN und Browser braucht, ist keiner.
- Der PWM-Pin ist geklärt: `gpio.25` = `Sp-Enable` (CN51), siehe Abschnitt 5.
- `M0` versteht FluidNC als Pause (`ProgramFlow::Paused`), und der Wechseltext
  steht jetzt als `(MSG,…)` in der Zeile — so protokolliert die Firmware ihn.
  `M1` dagegen ist im Quelltext ausdrücklich nicht implementiert.

**Weiterhin offen, weil es Hardware braucht:**

- Servo-S-Werte für Pen-Up/Down und alle Werte im Stiftkatalog sind Platzhalter
- Die Steckerbelegung am eigenen Board (Revision V1.0 gegen V1.1)
- Der komplette Laserpfad

**Danach zu bestimmen:**

- Riemensteifigkeit aus einem gemessenen Raster → Dehnungskorrektur scharf
  stellen (`wallplotter-correct anpassen --model dehnung`)
- ~~StallGuard-Empfindlichkeit fürs sensorlose Homing~~ — entfällt, siehe Abschnitt 5
- Brauchbare Höchstgeschwindigkeit über `--pattern feed-ramp`
- Ob die Laufzeitschätzung stimmt: `--pattern grid` plotten, Uhr mitlaufen
  lassen, mit der Angabe im GCode-Kopf vergleichen. Weicht sie ab, sind
  `--acceleration` und `--max-rate` die Stellschrauben.

**Ideen, noch nicht gebaut:**

- Farbseparation für Fotos (CMY oder feste Stiftfarben, je Farbe eigene Dichte)
- Flow-Field-Verfahren als fünfte Bildtechnik
- Verlauf vergangener Plots mit Thumbnail
- Simulator als Testgegenstelle fest im Repo statt als Wegwerf-Skript

---

## 11. Quellen

- FluidNC: [Repo](https://github.com/bdring/FluidNC) · [Wiki](http://wiki.fluidnc.com/)
- Rodent-Board: [BTT Wiki](https://global.bttwiki.com/Rodent.html) ·
  [rodent.yaml](https://github.com/bigtreetech/Rodent/blob/master/rodent.yaml)
- ESP3D Web-Handler: [esp3d.io](https://esp3d.io/esp3d/version-3x/documentation/api/webhandlers/)
- vpype: [Dokumentation](https://vpype.readthedocs.io/)
- NiceGUI: [nicegui.io](https://nicegui.io/documentation)
- G54 vs. G92: [CNCCookbook](https://www.cnccookbook.com/g54-g92-g52-work-offsets-cnc-g-code/)
