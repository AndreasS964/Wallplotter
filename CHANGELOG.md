# Änderungen

## 0.3.0 — Das Board vorbereiten, bevor es da ist

Der Schwerpunkt: alles vorziehen, was sich ohne Hardware entscheiden lässt.
Das hieß vor allem, die board-nahe Seite nicht mehr gegen die
ESP3D-Dokumentation zu bauen, sondern gegen den Quelltext der Firmware, die
am Ende antwortet (FluidNC v3.9.9, v4.0.4 und master — in diesen Punkten
identisch).

### Vier Annahmen, die nicht gestimmt haben

Jede einzelne hätte am Tag der Inbetriebnahme gekostet, und keine hätte sich
als Fehler *gezeigt* — sie hätten still das Falsche getan:

- **`/sdfiles` gibt es in FluidNC nicht.** Der eingebaute Webserver legt die
  SD-Karte auf `/upload` und den Flash auf `/files` — genau andersherum, als
  die ESP3D-Doku es für die eigenständige ESP3D-Firmware beschreibt. Unser
  Upload wäre im 404-Handler gelandet.
- **`/command?plain=` überträgt nur `$`-Kommandos.** Der Handler ruft
  `settings_execute_line()`, und die Funktion schneidet das erste Zeichen ab.
  Aus `G92 X0 Y0` wurde damit die Suche nach einer Einstellung `92 X0 Y0`, aus
  `?` die Suche nach dem leeren Namen. Kein Nullpunkt, kein Statusreport.
- **Realtime-Bytes erreichen ihren Zweig auf diesem Weg nie.** Die Firmware
  fängt `?`, `!`, `~`, `0x18` und `0x85` im Zeichenstrom eines Kanals ab, und
  einen Kanal gibt es nur über WebSocket oder seriell.
- **`/command` antwortet mit 503, solange die Maschine fährt** —
  `$HTTP/BlockDuringMotion` steht ab Werk an.

### Zwei Wege statt einem

- **`wallplotter.channel`** — ein WebSocket-Kanal zum Board, ohne
  Fremdbibliothek. Er ist im Sinne der Firmware ein *Channel* wie die serielle
  Schnittstelle: nimmt G-Code und Realtime-Bytes an, meldet von sich aus alle
  200 ms Status und wird von der Bewegungssperre nicht angefasst.
- Dateien laufen weiter über HTTP. Halt, Weiter und Not-Aus haben zusätzlich
  die firmware-eigenen Endpunkte `/feedhold_reload`, `/cyclestart_reload` und
  `/restart_reload` als zweiten Weg — die drei Griffe müssen auch dann gehen,
  wenn der Kanal steht.
- Lesen von der Karte über `$SD/Show=<pfad>` statt über einen HTTP-Pfad: den
  WebDAV-Mount `/sd` gibt es erst ab FluidNC 4, `$SD/Show` in jeder Fassung.

### Simulator im Repo

`wallplotter-sim` spielt das Board nach — absichtlich einschließlich seiner
Eigenheiten, denn eine gutmütigere Gegenstelle würde genau die Fehler
durchgehen lassen, die sie finden soll. Damit ist die Kette zum ersten Mal
ganz durchgefahren, ohne Board an der Wand: erzeugen, hochladen, starten,
Fortschritt sehen, halten, weiter, abbrechen, Restprogramm mit `--from-board`,
Standorte auf die Karte und zurück.

Drei Fehler sind dabei aufgefallen und behoben: Kommandoantworten und
Statusreports liefen in denselben Puffer (eine von der Karte gelesene Datei
bekam so einen Statusreport in die Mitte); die Statusabfrage gab den ältesten
gepufferten Report zurück statt eines frischen, sodass der Zustand sichtbar
hinterherhinkte; und der Dateiname im Multipart war der kurze statt des vollen
Pfads, weshalb die Firmware ihre Größenprüfung nicht gefunden hätte.

### Zwei offene Punkte geklärt

- **`M0` versteht FluidNC als Pause** (`GCode.cpp`: `ProgramFlow::Paused` löst
  einen Feed Hold aus, heraus kommt man mit Cycle Start). Damit trägt
  `--layers --one-file` mit `M0`-Pause zum Stiftwechsel.
- **Der PWM-Anschluss des Rodent ist kein PWM-Ausgang.** Laut Schaltplan
  (Blatt 3) läuft das Signal über RC-Glied und LM358 auf einen analogen
  0–10-V-Ausgang mit Trimmpoti — die Drehzahlvorgabe für einen VFD. Von einer
  Servo-Impulsfolge bleibt dort nichts übrig. Der Servo gehört an einen roh
  herausgeführten GPIO; die `config.yaml` nennt jetzt `gpio.26` am
  OLED-Header, mit dem, was am Board nachzumessen ist.

### Schraffur rechnet selbst

Aufgefallen ist es beim Aufräumen der CI: Das Verfahren `hatch` war
**kaputt**, und zwar nicht bei uns. Das Paket `hatched` (letzte Fassung 0.2.0)
setzt Shapely 1.x voraus, vpype verlangt Shapely 2.x — beides zusammen lässt
sich nicht installieren, und die Schraffur brach mit einer Meldung aus dem
Innersten von Shapely ab.

`wallplotter.imaging.hatch` rechnet jetzt selbst, wie die drei anderen
Verfahren auch: je Helligkeitsstufe ein Satz paralleler Linien, abgetastet und
auf die dunklen Bereiche beschnitten, Richtung von Lage zu Lage gewechselt.
Das kostet weniger Zeilen als die Fehlerbehandlung drumherum und nimmt dem
Projekt seine schwerste optionale Abhängigkeit — `hatched` zog vpype[all],
OpenCV, scikit-image und matplotlib nach. Der Extra-Name `[hatch]` bleibt,
damit alte Installationsbefehle laufen; er ist jetzt dasselbe wie `[photo]`.

Dabei fiel ein zweiter, älterer Fehler auf: `GrayImage.darkness()` prüfte die
Bildgrenzen erst nach `int()`. Das rundet Richtung Null, aus -0,4 wurde also 0
— der Streifen zwischen -1 und 0 galt als im Bild, und eine Zeichnung konnte
um bis zu ein Pixel über den Rand hinausragen. Bei 15 mm je Pixel sind das
15 mm neben der Fläche.

Die Abbildung `docs/images/verfahren.png` zeigt endlich alle vier Verfahren
(vorher fehlte `hatch` darin, weil es beim Erzeugen nicht lief) und lässt sich
mit `tools/technique_figure.py` reproduzieren — samt gerechneter Angaben unter
den Bildern.

### Dokumentation

- **[`docs/inbetriebnahme.md`](docs/inbetriebnahme.md)** — was sich vorher
  erledigen lässt (Einkauf, Druck, Trockenlauf gegen den Simulator) und der
  Ablauf für den Tag, an dem das Board kommt: elf Schritte, jeder mit dem
  Kriterium, an dem man erkennt, dass er sitzt.
- Handbuch §5 und §6 auf den nachgeprüften Stand, §10 um das Geklärte
  bereinigt.

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
