# Änderungen

## Unveröffentlicht — Gegenprüfung gegen den FluidNC-Quelltext, Bauanleitung

Bisher war alles Board-nahe „nach Dokumentation gebaut". Diese Runde hat den
Quelltext gelesen statt die Doku — FluidNC `main` sowie v4.0.4, v3.9.8 und
v3.8.0, dazu BTTs `rodent.yaml` und das Rodent-Handbuch V1.03. Ergebnis: 39
belegte Funde, davon fünf, die das Board blockiert hätten.

### Neu

- **[Bauanleitung](docs/bauanleitung.md)** — vom Karton bis zum ersten Strich:
  Stückliste, Riemen- und Ankerentscheidung mit den gerechneten Zahlen, Druck,
  Montage, Verkabelung am Rodent, Firmware, Erstinbetriebnahme in prüfbaren
  Etappen, Einmessen, Testmusterreihenfolge, Fehlersuche.
- **[Gegenprüfung](docs/firmware-gegenpruefung.md)** — alle Funde mit Beleg aus
  dem Firmware-Quelltext, plus eine Liste dessen, was geprüft und in Ordnung ist.

### Behoben in der `config.yaml`

- **`Laser: laser_mode: false` gestrichen.** Der Schlüssel existiert in FluidNC
  nicht, und ein unbekannter Schlüssel setzt die Firmware in **ConfigAlarm** —
  das Board wäre gar nicht gefahren. Lasermodus ist die Klasse der aktiven
  Spindel, keine Einstellung; `$32` ist ein reiner Lese-Proxy auf
  `isRateAdjusted()`. Der Block hätte zusätzlich eine zweite Spindel angelegt,
  ebenfalls mit `tool_num: 0`.
- **`speed_map` auf das Servofenster gelegt** (`0=5.000% 100=10.000%`). Die
  Werte bilden das *Tastverhältnis* ab, nicht einen Winkel: bei 50 Hz sind
  1,0–2,0 ms Impuls gleich 5–10 %. Vorher lag der ganze Servoweg zwischen S5
  und S10, und der Stiftkatalog (26 bis 40) fuhr in den Anschlag.
- **Servo-Pin geklärt statt geraten.** `gpio.25` liegt als `Sp-Enable` auf dem
  3-poligen Stecker CN51 heraus, über 100 Ω und auf 3,3 V geklemmt. Der Stecker
  mit der Aufschrift „PWM" ist ein analoger 3–10-V-Ausgang mit Trimmpoti und
  wäre falsch gewesen. Die Servoversorgung darf nicht vom +5-V-Pin desselben
  Steckers kommen — dort liegen ebenfalls 100 Ω.
- **`control:`-Block ergänzt**: Taster für Halt, Pause und Weiter an den freien
  Endstop-Eingängen. Kein Komfort, sondern Ersatz — über HTTP lässt sich die
  Maschine derzeit nicht anhalten (siehe unten). `cycle_start_pin` ist außerdem
  der einzige heute funktionierende Weg, die `M0`-Stiftwechselpause aufzulösen.
- **Homing-Kommentar richtiggestellt.** `WallPlotter::canHome()` gibt `false`
  zurück — `$H` geht mit dieser Kinematik nicht, auch nicht mit StallGuard. Und
  FluidNC friert die Riemenlängen für kartesisch (0,0) beim *Booten* ein: Der
  Nullpunkt entsteht dadurch, dass man das Board am Referenzpunkt neu startet,
  nicht durch ein späteres `G92`.

### Richtiggestellt in der Dokumentation

- Die Web-API-Tabelle im Handbuch war umgekehrt: **`/files` ist der Flash,
  `/upload` ist die SD-Karte**, und `/sdfiles` gibt es in keiner Version.
- `/command?plain=` führt nur `$`-Kommandos aus. `?`, `G92`, `G10 L20` und die
  Realtime-Bytes kommen darüber nicht durch — letztere melden dabei sogar
  HTTP 200. Für Pause, Weiter und Reset gibt es `/feedhold_reload`,
  `/cyclestart_reload` und `/restart_reload`.
- `M2` verwirft den `G92`-Versatz **nicht**; flüchtig ist er erst beim
  Ausschalten. `M1` hält nicht an (im Quelltext ausdrücklich nicht
  implementiert), `M0` schon.
- Der SD-Fortschritt kommt als Prozent, nicht in Bytes: `SD:<prozent>,<pfad>`.
- StallGuard-Homing ist auch aus Projektidee und Roadmap gestrichen, der
  `plain=?`-Poll aus Stufe 6 ebenfalls — sonst baut die nächste Runde beides
  wieder ein.

### Tests

- `test_firmware_config.py` prüfte die `config.yaml` gegen sich selbst und
  zementierte dabei den ConfigAlarm-Schlüssel. Jetzt prüft es, dass **kein**
  `Laser`-Abschnitt da ist, dass die `speed_map` im RC-Servofenster landet und
  dass es überhaupt einen Weg gibt, die Maschine anzuhalten. 426 Tests.

### Nicht behoben — der Code kommt als Nächstes dran

`upload.py`, `webapp.py`, `calibrate_cli.py` und `location.py` tragen die
falschen Annahmen weiter; die Reihenfolge fürs Aufräumen steht am Ende der
[Gegenprüfung](docs/firmware-gegenpruefung.md). Bis dahin läuft der Weg zum
Board über FluidNCs eigenes WebUI im Browser.

## 0.2.0 — Werkzeugköpfe, ehrliche Zahlen, Fortsetzen

Der Schwerpunkt: Was unten an der Gondel hängt, ist keine Konstante mehr.

### Werkzeugköpfe

`wallplotter.toolhead` trennt *Weg* von *Werkzeug*. `gcode.py` kennt kein
`M3`, kein `M5` und kein `G4` mehr — ein Test hält das am Modulquelltext fest.
Der Kopf liefert die Zeilen, die ihn ein- und ausrücken.

- **Sechs Stiftsorten** im Katalog (`--list-toolheads`): Fineliner schwarz und
  rot, Kugelschreiber, Marker, Kreidemarker, Pinselstift — je mit eigener
  Strichbreite, Servo-Wartezeit und Vorschub. Die Zahlen sind ausdrücklich
  Startwerte, keine Messwerte; nachgezogen wird mit `--pattern pen-test`.
- **Ein Stift je Farbebene** beim mehrfarbigen Plotten:
  `--pen-for "#e02020=marker"`. Die `M0`-Pause nennt Farbe *und* Stift.
- **Laserkopf**, gegen die GRBL-Laserdoku und den FluidNC-Quelltext gebaut,
  aber noch an keiner Hardware erprobt:
  - `M4` (dynamische Leistung) als Vorgabe — bei konstanter Leistung brennt
    ein Seilplotter jede Ecke durch, weil er weich und lange beschleunigt.
  - Leistung in **Prozent**, abgebildet auf ein einstellbares `s_max`. Ob
    volle Leistung `S255` oder `S1000` heißt, steht in der `speed_map` der
    `config.yaml` — ein hart verdrahtetes Maximum ist die häufigste Laserpanne.
  - `travel_as_g1` zusammen mit einem Laser wird **verweigert**, nicht
    gewarnt: ein G1-Leerweg fährt mit eingeschaltetem Strahl.
  - Keine Wartezeiten, `S0` vor jedem Leerweg, mehrere Durchgänge ausgerollt
    (GRBL kennt keine Schleifen), Air Assist über `M8`/`M9`.
  - Laser-GCode entsteht nur mit `--laser-verstanden`.
- Die `config.yaml` trägt den zweiten Spindelblock auskommentiert bei, samt
  Begründung, warum Stift und Laser weder denselben Pin noch dieselbe
  PWM-Frequenz teilen können.

### Zahlen, die vorher gelogen haben

- **Laufzeitschätzung mit Bewegungsprofil** (`wallplotter.timing`). Statt
  Strecke durch Vorschub wird nachgebildet, was GRBL tut: Ecktempo nach
  *junction deviation*, Rückwärts- und Vorwärtslauf über die Segmente,
  Trapez- oder Dreiecksprofil je Segment. Für ein Punktraster aus tausend
  Ein-Millimeter-Strichen versprach die alte Rechnung sieben Minuten, die
  Maschine braucht einundzwanzig.
- **Servo-Wartezeiten zählen mit.** 5000 Punkte × 0,5 s sind gut 40 Minuten,
  die vorher in keiner Zahl auftauchten.

### Fortsetzen abgebrochener Plots

`wallplotter-resume` schneidet aus einer GCode-Datei das lauffähige
Restprogramm — mit rekonstruiertem modalem Zustand (Einheiten, absolute
Koordinaten, Vorschub, Werkzeug). Angesetzt wird am Anfang des angefangenen
Strichs, nicht exakt an der Abbruchstelle: FluidNC meldet den Fortschritt in
gelesenen Bytes, und der Planer liest der Mechanik voraus.

Mit `--from-board` kommt der Fortschritt aus dem Status der Maschine.

### Neue Werkzeuge

- `wallplotter-doctor` — Selbsttest von der Installation bis zum Board.
  Prüft unter anderem, ob die Ankermaße in der `config.yaml` noch zu denen des
  aktiven Standorts passen; genau da wird ein Wandbild unbemerkt schief.
- `wallplotter-correct` — der fehlende Weg zur Vorverzerrung: Messraster
  plotten, Werte eintragen, anpassen. Vorher konsumierte `plot --correction`
  ein Dateiformat, das die Toolchain selbst nicht herstellen konnte. Der
  Anpassungsschritt sagt, ob die Korrektur überhaupt etwas wegnimmt.

### Behobene Fehler

- **Mehrfarbig plotten landete neben der Fläche.** Der Flächenversatz einer
  kalibrierten Wand wurde in X doppelt gerechnet und in Y weggekürzt — bei
  300/200 mm Versatz lag die Zeichnung gut 30 cm daneben, während dieselbe
  Vorlage einfarbig richtig lag. Ohne Versatz und ohne Spiegelung hob sich der
  Fehler auf, und genau so standen die Tests.
- **Zeichnungen ohne Höhe oder Breite wurden nicht skaliert.**
- **Der Jog-Abbruch konnte nie funktionieren.** Das Realtime-Byte `0x85` ging
  durch die URL-Aufbereitung von `requests` und kam als UTF-8-Folge
  `0xC2 0x85` an — also als zwei Bytes, die das Board nicht kennt. Realtime-
  Bytes gehen jetzt als Prozent-Escape hinaus, mit kurzem Zeitlimit: ein
  Not-Halt darf nicht so lange warten wie ein Datei-Upload.
- **Beim Fortsetzen ging lautlos ein Segment verloren.** `M3 S0` (Stift oben)
  ist von `M3 S30` (Stift unten) nicht am Befehl zu unterscheiden; unterschieden
  wird jetzt am S-Wert.
- **Die Web-UI fror alle zwei Sekunden ein**, sobald das Board nicht antwortete.
  Alle Netzaufrufe und die Bildumrechnung laufen jetzt in Threads.
- **Drei Absturzpfade in der Oberfläche**: geleertes Zahlenfeld
  „Bahnabstand", abgewähltes Jog-Toggle, Rand größer als die halbe Fläche.
- **`sort_lines` war quadratisch** — ein Punktraster mit zehntausend Strichen
  kostete zwanzig Sekunden, jetzt eine Viertelsekunde. Dass das Ergebnis dabei
  wirklich dasselbe bleibt, stimmte erst nach einem Nachtrag (siehe unten).
- Jede Ebene und jeder Plot bekommen einen eigenen Dateinamen auf der Karte,
  statt sich gegenseitig als `plot.gcode` zu überschreiben — das Fortsetzen
  braucht die Originaldatei noch.

### Verdrahtet, was schon dalag

`correction.py` und `motion.py` waren implementiert, getestet und von keiner
Oberfläche aus erreichbar. Jetzt: `--correction`, `--adaptive-feed`, und die
Resonanzprüfung läuft bei jedem Plot mit — in der CLI wie in der Web-UI.

### Aus der Nachprüfung

Fünf unabhängige Prüfer über den fertigen Umbau, jeder Fund einzeln
gegengeprüft. Sieben hielten stand:

- **Der Laser-Riegel hatte ein Loch.** `--laser-verstanden` prüfte nur den Kopf
  aus `--toolhead`; über `--pen-for 'FARBE=laser'` entstand vollständiger
  Laser-GCode ohne Rückfrage und ohne Warnung. Dieselbe Stelle ließ die
  `--laser-*`-Schalter ins Leere laufen — bei `--laser-power 5 --laser-smax 255`
  kam `S200` heraus statt `S13`.
- **Der Web-UI fehlte das Gegenstück dazu** ganz.
- **Ein fortgesetztes Laserprogramm zeichnete nicht.** Spindelmodus und
  Luftunterstützung stehen nur einmal im Vorspann; das Restprogramm baute
  beides nicht wieder auf und fuhr trocken ab. Mit `--exact` stand dort ein
  fest verdrahtetes `M3` — konstante Leistung, wo dynamische gemeint war.
- **`sort_lines` lieferte doch nicht dasselbe wie die alte Fassung.** Teilen
  sich zwei Linien einen Endpunkt, liegen sie exakt gleich weit weg; die
  Rasterreihenfolge entschied anders als die Listenreihenfolge und
  gelegentlich schlechter. Der Test verglich an Zufallszahlen — die liegen
  praktisch nie exakt gleich weit, eine Zeichnung dagegen ständig.
- `--adaptive-feed` und die Resonanzprüfung rechneten mit dem Vorschub aus der
  Konfiguration statt mit dem des Kopfes.
- `to_plot_config` verlor die Bewegungsgrenzen; `pass_pause_s` landete
  nirgends; `travel_mm` ignorierte die Durchgänge.
- Ein vertippter Ausgabepfad endete im Traceback statt in einer Meldung.

### Verpackung

- `photo` zieht nur noch Pillow nach. Schraffur steht als eigenes Extra
  `hatch` daneben, weil `hatched` vpype[all], OpenCV, scikit-image und
  matplotlib mitbringt.
- `py.typed` liegt bei.
- Die CI installiert `web` und `photo` und überspringt damit nicht mehr
  stillschweigend die Tests der Oberfläche und der Bildverfahren.

## 0.1.0

Erste Fassung: SVG und Fotos nach FluidNC-GCode, Testmuster, Kalibrierung über
angefahrene Ecken, Standorte, Web-UI, Upload auf die SD-Karte.
