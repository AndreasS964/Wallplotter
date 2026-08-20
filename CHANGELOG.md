# Änderungen

## Unveröffentlicht — die Funde behoben, Projektseite

Zweiter Teil derselben Runde: Was die Gegenprüfung gefunden hat, ist jetzt
behoben, und die Dokumentation steht als Website unter
[andreass964.github.io/Wallplotter](https://andreass964.github.io/Wallplotter/).

### Der Kern: eine Weiche, die es vorher nicht gab

`wallplotter.upload` trennt jetzt sauber, was FluidNC selbst trennt:

* **Dateien über HTTP.** `POST /upload` schreibt auf die Karte,
  `GET /upload?path=/` listet sie, `GET /sd/<datei>` liest zurück. Der
  Multipart-Dateiname trägt den vollen Zielpfad, damit er zum Größenfeld passt
  — sonst landete die Datei im Wurzelverzeichnis und die Platzprüfung fiel aus.
* **Kommandos über den TCP-Kanal auf Port 23** (`TelnetChannel`). Der ist ab
  Werk an und ein vollwertiger `Channel`: dort wirken Realtime-Zeichen, `?`
  liefert einen Statusbericht, GCode wird ausgeführt. Ohne neue Abhängigkeit —
  FluidNC verhandelt keine Telnet-Optionen, ein Socket aus der
  Standardbibliothek reicht.
* **Halt, Pause und Weiter über die Ereignis-Endpunkte** `/feedhold_reload`,
  `/cyclestart_reload`, `/restart_reload`. Die lösen direkt das
  Firmware-Ereignis aus und werden nicht von `$HTTP/BlockDuringMotion`
  blockiert — also genau das, was ein Halt können muss.
* `$`-Kommandos gehen weiterhin über `/command?plain=`, aber nur als
  Rückfallebene, falls der Kanal nicht aufgeht. Für GCode gibt es **keinen**
  Rückfall: lieber laut scheitern als still nichts tun.

`parse_status` unterscheidet außerdem `MPos` und `WPos` und rechnet über `WCO`
in Werkstückkoordinaten um — das ist das System, in dem der erzeugte GCode
fährt. Und `SD: <name>: Sent` heißt „fertig", nicht „kein Fortschritt".

### Web-UI

Sie antwortete seit NiceGUI 3 auf **jede** Anfrage mit HTTP 500: Die
Bibliothek führt pro Seitenaufruf `runpy.run_path(sys.argv[0])` aus, und bei
`python -m wallplotter.webapp` brachen dabei die relativen Importe. Die
Oberfläche baut sich jetzt in einer Wurzelfunktion auf (`ui.run(root=…)`),
`python -m wallplotter.webapp` nimmt `--host/--port/--board`, es gibt ein
Konsolenskript `wallplotter-web`, `nicegui` ist auf `<4.0` eingegrenzt — und
ein Test holt die Seite wirklich über HTTP ab. Vorher rief kein einziger sie
je auf.

Dazu: Der Laser-Riegel greift jetzt über *alle* benutzten Köpfe (über die
Ebenenzuordnung ging er vorbei), ein geladenes Testmuster räumt die alte
Ebenenliste weg, S-Werte werden auf 0…100 begrenzt, eine verpasste
Statusabfrage lässt den Fortschritt stehen statt ihn auf 0 zu setzen, und
„Plot gestartet" wird erst gemeldet, wenn das Board bestätigt, dass es fährt.

### Zwei Rechenfehler

* `kinematics.resolution_mm` mischte Komponenten aus beiden Spalten der
  inversen Jacobi-Matrix. Richtig ist `Schrittweite / |sin(Riemenwinkel)|`. Die
  Zahlen sahen plausibel aus, wiesen aber die falsche Problemzone aus — und
  `motion.conditioning_feeds` bremste danach am falschen Ende.
  `docs/kinematik.md` ist neu gerechnet.
* Die Resonanzprüfung maß den **Bahnabstand** statt der **Bahnlänge**. Wie oft
  die Gondel umkehrt, hängt daran, wie lang eine Bahn ist. Bei einer Schraffur
  über 1000 mm mit 3 mm Abstand lagen zwischen beiden Zahlen mehr als zwei
  Größenordnungen: Alarm gab es bei genau den Mustern, die harmlos sind.

### Kleineres, aber ebenso konkret

* Kommentarzeilen über 127 Byte brachen den Lauf mit `error:14` ab — FluidNC
  liest in einen `char line[128]` und prüft **vor** dem Entfernen der
  Kommentare. `gcode.comment_lines()` bricht jetzt um, ein Test prüft jede
  Zeile jedes erzeugten Programms.
* Der Stiftwechseltext stand hinter einem Semikolon und erreichte damit
  niemanden; jetzt `M0 (MSG,…)`, das protokolliert FluidNC.
* Die Parkfahrt ging auf Maschinen-(0,0) — bei kalibrierter Fläche in die Zone
  mit der schlechtesten Riemengeometrie. Jetzt an die Ecke der Zeichenfläche.
* Drei Ebenen derselben Farbe (`black`, `#000000`, `rgb(0,0,0)` sind für vpype
  drei) überschrieben sich als Wörterbuchschlüssel lautlos. Jetzt zusammengefasst.
* Die Spirale verlor bei feinem Bahnabstand durch Aliasing das ganze Bild; dazu
  gibt es jetzt eine Obergrenze für die Punktzahl statt einer minutenlang
  rechnenden Oberfläche.
* `--layers` zeigt Laufzeit und Resonanzprüfung, `--adaptive-feed` wird nicht
  mehr still verschluckt.
* Das Raster von `--pattern grid` schließt bis an den Rand — vorher lag die
  letzte Linie beim letzten Vielfachen der Teilung, und wer nachmaß, maß einen
  Fehler, den es nicht gab.
* `wallplotter-correct zeigen` nimmt `--in`; `anpassen` sagt jetzt, wenn zu
  wenige Messpunkte für den gewählten Grad da sind und der Restfehler deshalb
  zwangsläufig 0 ist.
* `wallplotter-resume` stürzt nicht mehr bei nicht-UTF-8-Dateien ab, nachdem
  das Restprogramm schon geschrieben war.
* `wallplotter-doctor` prüft erst HTTP, dann den Kanal — und sagt, welcher der
  beiden fehlt, statt „Board noch nicht da" zu melden, während es antwortet.

### Tests und CI

Die Attrappen sagen jetzt auch Nein. Die alten quittierten jede URL und jedes
Kommando mit 200 — darüber sind sechs Firmware-Fehler grün geblieben. Die neue
Gegenstelle (`tests/fluidnc_fake.py`) kennt nur die Endpunkte, die FluidNC
registriert, und verhält sich bei `plain=` wie `settings_execute_line()`.
**461 Tests.**

Dabei kam gleich etwas heraus, das vorher unter dem Skip lag: Das Fremdpaket
`hatched` (0.2.0, die letzte Veröffentlichung) ist mit Shapely 2 nicht mehr
lauffähig — es baut ein leeres `MultiLineString` aus einem numpy-Array.
`imaging.hatch` sagt das jetzt im Klartext und nennt die drei Verfahren, die
nur Pillow brauchen; der Test meldet ein sichtbares xfail statt eines stillen
Skips und wird von selbst grün, wenn upstream nachzieht.

Die CI installiert alle Extras in einem Job und bricht ab, wenn sich ein Test
wegen eines fehlenden Pakets überspringt — genau so blieben ganze Testdateien
unbemerkt liegen. Dazu ein Job, der den Kern ohne Extras prüft und jedes
Konsolenskript einmal startet, und einer nur für die `config.yaml`.

### Projektseite

`tools/build_site.py` baut die Dokumentation zu einer statischen Website,
`.github/workflows/pages.yml` veröffentlicht sie. Kein Jekyll, kein Theme von
der Stange: `python tools/build_site.py && python -m http.server -d site` zeigt
lokal genau das, was online steht, und die CI bricht ab, wenn ein interner
Verweis ins Leere zeigt.

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
