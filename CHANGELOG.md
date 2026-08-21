# Änderungen

## Unveröffentlicht — die Web-Oberfläche als eigenständige Windows-Datei

`wallplotter-web` lässt sich jetzt zu einer einzelnen `Wandplotter.exe`
bauen — kein installiertes Python nötig, zum Doppelklicken auf einem
anderen Rechner als dem Entwicklungsrechner. Von Hand über GitHub Actions
(„Windows-Datei bauen" im Actions-Tab) oder lokal mit PyInstaller, siehe
[docs/windows-paket.md](docs/windows-paket.md).

Zwei Pakete brauchten dafür mehr als PyInstallers eingebaute Importsuche,
beide erst am gebauten Programm sichtbar geworden, nicht am Quelltext:

* **NiceGUI** liefert seine Oberfläche über eigene, nicht-Python-Dateien aus
  (Vue/Quasar-Bausteine, Icons) — ohne die als `--add-data` blieb die
  ausgelieferte Seite leer.
* **vpype** entdeckt einen Teil seiner Kommandos über Paket-Metadaten zur
  Laufzeit statt über normale `import`-Anweisungen; eine seiner
  Abhängigkeiten (`tomli`) ist mit `mypyc` übersetzt und verweist auf ein
  lose danebenliegendes Laufzeitmodul, dessen Name vom genauen Build abhängt
  — `tools/wallplotter-web.spec` sucht ihn deshalb zur Bauzeit, statt ihn
  fest einzutragen.

Geprüft nicht nur am Bau, sondern an einem laufenden, gebauten Programm:
Server gestartet, mit einem echten Browser (Playwright) eine SVG-Datei
hochgeladen und bis zur GCode-Statistik durchgerechnet — auf Linux, weil
hier kein Windows zur Verfügung steht, aber mit denselben PyInstaller-Flags,
die der Windows-Bau in der CI verwendet. Genau dabei ist auch der oben
verzeichnete Upload-Fehler aufgefallen.

## Unveröffentlicht — jeder echte Upload in der Web-UI schlug fehl

`load_upload()` sprach noch die alte NiceGUI-Schnittstelle an
(`event.content.read()`, `event.name`) — die installierte NiceGUI-Version
reicht seit der Umstellung auf `UploadEventArguments.file` ein eigenes
`FileUpload`-Objekt mit **asynchronem** `read()` durch, kein `.content` und
kein `.name` mehr auf dem Ereignis selbst. Jeder Upload über den Browser
endete serverseitig mit `AttributeError`, bevor er bei `render_upload()`
ankam — die Oberfläche zeigte dabei nichts an, denn die Ausnahme fällt
NiceGUI in den Schoß, nicht dem Nutzer vor den Latz.

Gefunden hat das kein Test, sondern ein echter Browser (Playwright) gegen
einen laufenden Server: Alle bestehenden Upload-Tests setzen
`app.upload_data`/`upload_name` direkt und rufen `render_upload()` auf —
`load_upload()` selbst, der tatsächliche Weg vom Browser-Ereignis, wurde von
keinem einzigen Test durchlaufen. Jetzt gibt es einen, mit einem echten
`UploadEventArguments`/`SmallFileUpload` aus NiceGUI selbst statt einer
Attrappe.

## Unveröffentlicht — der letzte Fund: eine überholte Umrechnung gewann

`webapp.render_upload()` schickt die (bei einer Spirale über eine große Wand
durchaus sekundenlange) Bild- oder SVG-Umrechnung in einen Thread. Wechselt
jemand währenddessen das Verfahren — der naheliegende nächste Klick, während
man auf das Ergebnis wartet —, läuft ein zweiter `render_upload()`-Aufruf
parallel dazu. Ohne Weiteres gewann danach, wer zufällig zuerst *fertig*
wurde, nicht wer zuletzt *angestoßen* wurde: Die langsamere, längst
überholte erste Umrechnung konnte das frischere, schon angezeigte Ergebnis
der zweiten wieder überschreiben. Ein Generationszähler sorgt jetzt dafür,
dass nur das Ergebnis des zuletzt gestarteten Aufrufs angewendet wird — ein
überholtes wird verworfen, ohne die Oberfläche noch einmal anzufassen.

Damit sind alle 21 Funde aus der aktuellen Gegenprüfungsrunde behoben (28
Agenten, 7 Fachbereiche parallel gelesen, jeder Fund einzeln adversarisch
nachgeprüft — 21 von 21 bestätigt).

## Unveröffentlicht — `TelnetChannel` gegen gleichzeitige Nutzung abgesichert

Die Web-UI cacht den `FluidNCClient` je Host/Zeitlimit und ruft ihn aus
eigenen Threads auf (`asyncio.to_thread`) — Jog, Jog-Abbruch, Nullpunkt
setzen und eine Ecke anfahren teilen sich damit denselben `TelnetChannel`.
Der hatte keinerlei Sperre um seinen gemeinsamen Socket-Puffer: Liefen zwei
`send_line()`- oder `status()`-Aufrufe gleichzeitig, konnte die Antwort auf
die eine Anfrage bei der anderen landen — am naheliegendsten beim Jog-Pad,
das bei gehaltener Taste mehrere `send_line()`-Aufrufe kurz hintereinander
auslöst.

`send_line()` und `status()` — beide lesen aus demselben Puffer — teilen sich
jetzt eine Sperre, die nur eine „Unterhaltung" gleichzeitig zulässt.
`send_realtime()` (Not-Halt, Jog-Abbruch) nimmt diese Sperre bewusst
**nicht**: Ein Abbruch darf nicht erst warten, bis eine andere, noch laufende
Anfrage ihre Antwort fertig eingesammelt hat — genau der Grund, warum es
dafür überhaupt einen eigenen, unquittierten Kanalweg gibt.

## Unveröffentlicht — ein Verbindungsabbruch beim Einmessen kostete alle Ecken

`wallplotter-setup`s Flächenschritt (`_tue_flaeche`) sammelte die
angefahrenen Ecken in einer lokalen Variable und schrieb sie erst ganz am
Ende ins Standortbuch. Riss die WLAN-Verbindung mitten in der dritten Ecke ab
(`FluidNCError`), sprang der Schritt sofort mit „Verbindung verloren" zurück
— und die ersten beiden, längst erfolgreich aufgenommenen Ecken waren
mitsamt der Ausnahme weg, nie gespeichert. Wer danach `wallplotter-setup`
erneut aufrief, fing wieder bei null an. Gespeichert wird jetzt in jedem
Fall, was bis zum Abbruch aufgenommen wurde; die Warnung bleibt.

## Unveröffentlicht — eine kaputte Kalibrierdatei sah aus wie eine fehlende

`wallplotter-calibrate`s `_Store.load()` fing `CalibrationError` einheitlich
ab und fiel in beiden Fällen auf eine leere Kalibrierung zurück: sowohl wenn
die Datei fehlt (der Normalfall vor der ersten Aufnahme) als auch wenn sie
existiert, aber kaputt ist (Handbearbeitung, abgebrochenes Schreiben). Im
zweiten Fall hätte der nächste `record`-Aufruf die Datei stillschweigend mit
nur der einen neuen Ecke überschrieben — der Rest der Kalibrierung wäre weg
gewesen, ohne dass irgendwo eine Meldung erschien. Jetzt wird zuerst geprüft,
ob die Datei überhaupt existiert; existiert sie, muss sie auch lesbar sein.

## Unveröffentlicht — `download()` ignorierte `remote_dir`

`FluidNCClient.upload()` legt eine Datei unter `config.remote_dir` ab —
`download()` las aber immer ab Kartenwurzel, ganz gleich, was `remote_dir`
sagte. Solange `remote_dir` bei `/` (der Vorgabe) blieb, fiel das nie auf.
Bei jedem anderen Wert schrieb `wallplotter-location push` die Standorte
unter `remote_dir`, und das folgende `pull` suchte sie an der Kartenwurzel —
zwei verschiedene Pfade, ein `404`. `download()` löst einen Namen ohne
führenden Schrägstrich jetzt genauso gegen `remote_dir` auf wie `upload()`;
ein Name *mit* führendem Schrägstrich bleibt weiterhin ein expliziter,
absoluter Pfad.

## Unveröffentlicht — drei kleinere Absicherungen

Weiter aus derselben Gegenprüfungsrunde:

* `wallplotter-kinematics --overhang 0 --above 0` stürzte ab: Der Anker fällt
  dann exakt in eine Ecke der abgerasterten Fläche, und `analyze_area()`
  rastert immer bis an den Rand — der Stift sitzt an genau diesem einen
  Punkt „auf dem Anker", was `_unit_vectors()` mit `ValueError` quittiert.
  Eine einzelne Singularität im Raster übersprang die Auswertung bisher
  nicht, sie brach ab. Jetzt wird nur der eine Punkt übersprungen.
* `LaserSpindle` hatte kein Gegenstück zu `ServoSpindle`s
  `s_min >= s_max`-Prüfung: `s_max=0` ließ sich anstandslos bauen und ergab
  eine `speed_map`, die S0 auf zwei widersprüchliche Tastverhältnisse
  abbildet (`0=0.000% 0=100.000%`). Baut jetzt gar nicht erst.
* `PlotConfig` prüfte nur, ob `margin_mm` zu *groß* für die Fläche ist. Ein
  negativer Rand rutschte durch und machte `drawable_width_mm`/
  `drawable_height_mm` rechnerisch größer als die Wand selbst, statt
  abgelehnt zu werden.

## Unveröffentlicht — kaputte Dateien melden sich jetzt sauber statt mit Traceback

Vier Stellen, ein Muster: Eine gespeicherte JSON-Datei ist gültig geparst,
aber nicht in der erwarteten Form — von Hand bearbeitet, unterbrochen
geschrieben, aus einer anderen Version. Der jeweilige `try/except` fing
schon einiges ab, aber nicht `AttributeError`, den zum Beispiel eine Liste
statt eines Wörterbuchs auslöst (`["a","b"].items()`). Betroffen:
`correction.load_correction()`, `calibration.AreaCalibration.load()`,
`location.Location.from_dict()`. Alle drei melden jetzt den eigenen,
dokumentierten Fehlertyp statt eines rohen Tracebacks.

`wallplotter-doctor` hatte dieselbe Lücke ohne die Datei-Form-Ausrede:
`check_firmware_config()` rechnete ein Ankermaß aus der `config.yaml` mit
`float(...)` um, ohne die Möglichkeit vorzusehen, dass dort kein Zahlwort
steht (Tippfehler, kaputtes YAML) — ein `ValueError` riss den kompletten
Selbsttest mit sich, statt nur diesen einen Befund als FAIL zu melden.

Dazu, in derselben Datei gefunden: `AreaCalibration.complete` akzeptierte von
den zwei möglichen Eck-Diagonalen nur `bottom-left`/`top-right` fest
verdrahtet. Die andere (`bottom-right`/`top-left`) — genauso gültig, `rectangle()`
konnte sie schon immer auswerten — zählte fälschlich als unvollständig. Wer
beim Einmessen genau diese zwei Ecken anfuhr (eine gültige Reihenfolge beim
Abbrechen des Wizards), bekam „Kalibrierung unvollständig" gemeldet, obwohl
sie es nicht war.

## Unveröffentlicht — zwei CLI-Funde: `0` als Wert, `--adaptive-feed` und `--layers`

Dieselbe Fundklasse wie schon einmal im CHANGELOG vermerkt ("`0` auf der
Kommandozeile fiel bei fünf Optionen still auf die Vorgabe zurück") war noch
an vier weiteren Stellen offen: `--pitch`, `--spacing`, `--dot` und
`--pattern-spacing` prüften mit `if args.x` statt `if args.x is not None`.
Ein ausdrückliches `--pitch 0` verschwand damit lautlos, statt die
Validierung des Verfahrens zu erreichen (`spiral()` lehnt `pitch_mm=0` ab —
aber nur, wenn der Wert dort ankommt).

`--adaptive-feed` ohne `--location` brach außerdem grundsätzlich ab, auch
zusammen mit `--layers` — obwohl der `--layers`-Zweig `--adaptive-feed`
längst als reinen Hinweis behandelt (er steht in der `ignored`-Liste dort und
tut nichts). Der frühe Abbruch prüft jetzt `not args.layers` mit.

## Unveröffentlicht — drei weitere Funde: Warnschwelle, Spirale, Bildrand

Weiter aus derselben Gegenprüfungsrunde.

### Die Übergeschwindigkeits-Warnung griff nur mit Stift-Übersteuerung

`toolhead.py`: `PenToolhead.check()` berechnete den wirksamen Vorschub
korrekt über `feed_for()` — der fällt auf den globalen `draw_feed` zurück,
wenn der Stift keinen eigenen hat —, warnte aber nur `if self.draw_feed and
feed > 2500`. Ohne Stift-eigene Übersteuerung blieb die Bedingung falsch,
selbst wenn der *globale* Vorschub weit über der Schwelle lag, ab der die
Riemen springen. Genau der naheliegendste Weg, einen Plot schneller zu
machen — `--draw-feed` global hochsetzen —, umging damit die eigene Warnung.

### Die Wobble-Phase der Fotospirale: ein Fix, der sich selbst aufhob

`imaging.py`: `spiral()` sollte im Zentrum, wo der Radius kleiner als
`pitch_mm / 4` ist, den Bogen um weniger als `step_mm` wachsen lassen — das
steht auch weiterhin so im Kommentar. Die Rechnung dazu war aber
`delta * max(radius, pitch_mm / 4)`, und das ist algebraisch **immer** genau
`step_mm`, weil `delta` selbst als `step_mm / max(radius, pitch_mm / 4)`
definiert ist. Der Bogen wuchs also unbedingt um den vollen Nennschritt,
ganz gleich wie klein der Radius war — die Phase des Wobbles lief im Zentrum
weiter davon, obwohl der Kommentar das Gegenteil beschreibt. Jetzt zählt
`delta * radius`, der tatsächlich gefahrene Bogen beim wahren Radius.

### Ein Bildrand, der keiner war

`imaging.py`: `GrayImage.darkness()` prüfte die Bildgrenzen mit `int(x)`,
`int(y)` — Kürzung Richtung 0. Für `-1 < x < 0` (ebenso für `y`) liefert das
fälschlich Pixel 0 statt „außerhalb", denn `int(-0.5) == 0`. Betroffen davon
ist unter anderem `spiral()`, deren Wobble Bildkoordinaten knapp unter 0
erzeugen kann. Jetzt `math.floor()`.

## Unveröffentlicht — frische Gegenprüfung des Codes: drei Funde behoben

Eine neue, unabhängige Runde: 7 Fachbereiche parallel gegengelesen, jeder
Fund einzeln adversarisch nachgeprüft (28 Agenten, 21 bestätigt). Diese drei
zuerst, weil einer sicherheitsrelevant ist und die anderen beiden dieselbe
Datei betreffen; der Rest folgt in den nächsten Einträgen.

### Der Laser-Riegel hatte noch ein zweites Loch

`webapp.py`: Der Schalter „Laser scharf" wird von `regenerate()` über
`laser_blocked()` geprüft — auch über die Ebenenzuordnung, seit einer
früheren Runde. Aber der Sende-Knopf **je Ebene** (`send_layer()`) rief
`laser_blocked()` nie auf. Eine Farbebene, deren Dropdown auf „Laser"
gestellt war, erzeugte darüber vollständigen Laser-GCode und lud ihn
hoch — ohne dass der Riegel je angefasst wurde, auch wenn das
`Laser scharf`-Häkchen nie gesetzt war. `send_layer()` prüft jetzt denselben
Riegel wie `regenerate()`, vor dem Erzeugen des Programms.

### `travel_mm` rechnete vom falschen Nullpunkt

`gcode.py`: `PlotStats.travel_mm` maß die Leerwege ab Maschinen-`(0,0)`, obwohl
die Geometrie längst auf `config.origin_x_mm`/`origin_y_mm` verschoben ist —
den Punkt, an dem der Plot tatsächlich parkt. Die Nachbarrechnung `motion_s`
tat das schon richtig, zwei Zeilen darunter. Bei kalibrierter Fläche (der
Normalfall, nicht der Sonderfall) wich die ausgewiesene Leerweg-Strecke damit
um ein Vielfaches vom tatsächlichen Weg ab — sichtbar in jeder generierten
`.gcode`-Datei, im CLI-Ausdruck und in der Web-UI.

Dieselbe Stelle zählte in einem zusammenhängenden Mehrfarbenprogramm
(`--layers --one-file`) für jede Zwischenebene eine Rückfahrt zum Nullpunkt
mit, die deren eigener GCode-Block gar nicht enthält — die passiert erst ganz
am Schluss, im letzten Block. `travel_length()` und `PlotStats` kennen jetzt
beide einen `park`/`return_to_start`-Schalter, den `_program()` passend zu
`include_end` setzt.

## Unveröffentlicht — Testlücke bei `wallplotter-location` geschlossen

`location_cli.py` war das einzige der zehn Konsolenbefehle ohne eigene
Testdatei — `new`, `list`, `show`, `use`, `remove`, `config` und der Abgleich
mit der Karte (`push`/`pull`) liefen nur, wenn sie jemand von Hand ausprobiert
hat. `tests/test_location_cli.py` deckt jetzt alle sieben Unterbefehle ab,
inklusive der Fehlerpfade: ein Standort mit unmöglichen Maßen (Rückgabe 3),
ein unbekannter Name bei `show`/`remove`, `remove` des gerade aktiven
Standorts (die Aktivität fällt auf einen verbliebenen zurück), und ein
`pull` von einer Karte ohne abgelegte Standorte (Rückgabe 5). Der
Karten-Abgleich läuft wie in `tests/test_sdstore.py` gegen eine simulierte
Karte, die nur die Endpunkte kennt, die FluidNC wirklich registriert.

## Unveröffentlicht — das README aufgeräumt

Von 2930 auf rund 2200 Wörter. Gestrichen ist nichts, was man zum Bedienen
braucht; gestrichen sind die Erklärstrecken, die ausführlich in den Dokumenten
stehen, auf die das README ohnehin verweist: die Tokenizer-Geschichte in der
Gegenprüfung, das Fehlerbudget in der Kinematik-Auswertung, die Homing-Frage in
der Bauanleitung.

Neu geordnet nach dem, was ein Leser in welcher Reihenfolge tut: Was das ist,
Installation, Schnellstart, dann die Arbeitsschritte, danach Aufbau, Tests,
Doku, Stand. Die Dokumententabelle stand vorher vor der Installation, also vor
allem, was man zuerst macht.

### Sechs Sachfehler, fünf davon älter als diese Änderung

Das neue README ist aus drei Blickwinkeln gegengelesen worden (Fakten, KI-Stil,
was beim Kürzen verlorenging), 38 Funde, jeder einzeln adversarisch nachgeprüft.
Vier haben standgehalten; zusammen mit zwei Funden aus der eigenen Prüfung:

| Stelle | Was nicht stimmte |
| --- | --- |
| Modultabelle, `pipeline` | „SVG oder Bild → Linien" — die Pipeline kann kein Bild. `pipeline.image_to_lines` ist seit dem Umbau nur noch ein Grabstein mit `NotImplementedError`; Fotos laufen über `imaging`. |
| Modultabelle, `config` | „Wandmaße, Vorschübe, Servowerte" — die Servowerte stehen in `toolhead`, `config.PenConfig` ist nur ein Alias. |
| Web-UI-Vorschau | „Zeichenwege blau" — `webapp.py` übergibt `stroke=toolhead.color`, beim Fineliner also `#111111`. Blau ist die Vorgabe von `lines_to_svg` (die CLI-Vorschau) und das Legendenkästchen. Dieselbe Zeile stand im Projekthandbuch und ist dort mitkorrigiert. |
| `wallplotter-location show` | Der Kommentar versprach Auflösung, Riemenkräfte und Riemenlänge. Direkt nach `new` sagt der Befehl „Fläche noch nicht eingemessen"; die Zahlen kommen erst mit kalibrierter Fläche. |
| Fehlerbudget | „alles Elektronische zwei Größenordnungen darunter" — gegen 0,013 mm bei 0,12–0,83 mm Riemendehnung ist das am unteren Ende Faktor 9, nicht 100. Jetzt stehen die Zahlen da. |
| Nullpunkt-Abschnitt | Ein Satz war zerrissen: „Das Rodent Richtigstellung dazu:" — Rest einer halb ausgeführten Änderung. |

### Sonst

`wallplotter-web` löst `python -m wallplotter.webapp` als dokumentierten
Startbefehl ab; nur darüber lassen sich Port und Board setzen. Das
Konsolenskript läuft jetzt im CI-Rauchtest mit, es kommt auch ohne NiceGUI bis
zum `--help`.

`wallplotter-kinematics` war der einzige der zehn Konsolenbefehle, den das
README nirgends nannte. Er steht jetzt unter „Genauigkeit", wo er hingehört:
Ankerposition durchrechnen, bevor gebohrt wird.

Das erste Beispiel unter „Plotten" zeigt wieder `examples/testmuster.svg`. Die
Datei liegt im Repo, die Zeile läuft in einem frischen Klon durch — ein
Beispiel mit `bild.svg` tut das nicht.

Die Extras werden als Tabelle erklärt statt als fünf `pip`-Zeilen; `site` fehlte
in der Erklärung ganz.

### Ton

Warnungen sachlich statt dramatisch. Der Laser bleibt als ungeprüft
gekennzeichnet und die Ablehnung von `--travel-as-g1` wird begründet, aber ohne
Bilder vom Strahl quer über die Wand; ConfigAlarm wird genannt, nicht
beschworen; „vier davon tödlich" ist raus.

Dazu weniger Sprachtics: 48 Geviertstriche auf 4, Fettdruck von 25 auf 7
Stellen, ein paar Antithesen und pointierte Schlusssätze weniger.

## Unveröffentlicht — die Gegenlesung und was sie fand

Der Erzeuger ist von 54 Agenten gegengelesen worden, in sechs Sichten und mit
adversarischer Nachprüfung jedes einzelnen Fundes: 48 gemeldet, **31 bestätigt,
17 widerlegt**. Die bestätigten sind behoben, jeder mit einem Test, der ohne die
Behebung rot wird.

### Der Prüfer meldete gültige Dateien als kaputt

Der schwerste Fund war eine Falschmeldung — die unangenehmere Richtung: In YAML
ist ein Abschnitt ohne Unterschlüssel `None` und sah damit aus wie ein Schlüssel.

```yaml
kinematics:
  Cartesian:        # die Standardkinematik jeder kartesischen FluidNC-Maschine
probe:              # Abschnitt ohne Inhalt
```

Beides meldete `pruefen` als »unbekannter Schlüssel → ConfigAlarm«, Rückgabecode
1. FluidNC kennt den Fall ausdrücklich (`ParserHandler.h:35`: *„If thisIndent <=
entryIndent, the section is empty"*), und `Parser::is()` setzt den Token schon
beim Namen auf Matched — »Ignored key« wird dort nie protokolliert. Leere
Abschnitte sind die übliche Schreibweise für alles, dessen `group()` leer ist:
`Cartesian:`, `null_motor:`, `probe:`.

Dazu fehlten der Tabelle zwanzig echte Schlüssel (die sieben Rohregister des
`tmc_5160Pro`, die zehn Registerbefehle der VFD-Spindeln, drei von Dynamixel2),
`tool_num` klemmte bei 100 statt bei `MaxToolNumber` = 99999999, `stallguard`
kannte nur den SPI-Bereich −64…63 und nicht die 0…255 des TMC2209, und
`homing.cycle` verbot die −1 (`set_mpos_only`).

### Die Tabelle prüfte sich selbst nicht

Bisher hielten die Tests nur *eine* Richtung fest: Schreibt der Erzeuger nur
Bekanntes? Die gefährlichere Richtung — **steht in der Tabelle nur, was FluidNC
wirklich kennt?** — prüfte nichts. Ein erfundener Eintrag wäre durch jede
Prüfung gegangen und hätte das Board in ConfigAlarm gesetzt.

`tools/fluidnc_keys.py` zieht die Namen jetzt mechanisch aus dem Quelltext —
`handler.item()`, `handler.section()`, die Fabriken, dazu die Control-Pins und
Makros, die über Schleifen registriert werden und in keinem `item("…")` stehen.
Das Ergebnis liegt als `tests/fluidnc-schluessel.txt` daneben: ein Abzug mit
Datum, kein Orakel. Zwei Tests halten die Tabelle dagegen; die Mutationsprobe
(zwei erfundene Einträge) macht beide rot.

### Stille Fehlschläge auf dem Weg zum Board

* **`upload_local` hielt HTTP 200 für Erfolg.** `handleFileOps()` antwortet auch
  im Fehlerfall mit 200 und trägt das Ergebnis nur in den JSON-Rumpf ein
  (`"status":"Upload failed"`, `WebUIServer.cpp:1220`). `push` meldete
  »Geschrieben«, startete neu und gab 0 zurück, während im Flash die alte Datei
  lag — derselbe Fehlertyp, den dieses Projekt am HTTP-Weg aufgedeckt hat.
  Jetzt wird der Rumpf gelesen **und** nach dem Schreiben zurückgelesen, vor
  dem Neustart.
* **`pruefen` sprach ohne PyYAML einen Freispruch aus**, den es nicht decken
  konnte: »FluidNC kennt jeden Schlüssel«, Rückgabecode 0 — obwohl genau diese
  Prüfung ausgefallen war. Jetzt sagt es »unvollständig geprüft« und gibt 4
  zurück.
* **Eine Ablehnung des Boards wurde als Erfolg gemeldet.** `send_command` fiel
  bei jedem Fehler auf HTTP zurück, auch bei einem `error:` — dieselbe Frage
  über den zweiten Weg zu stellen, ändert an der Antwort nichts. Neu ist
  `FluidNCRejected`: Ein Kanal, der nicht aufgeht, darf nachgereicht werden,
  eine Ablehnung nicht.
* **Der zweite `push` überschrieb die einzige echte Sicherung.** Sie ging immer
  auf denselben Namen; der zweite Lauf sicherte damit die Datei, die der erste
  gerade geschrieben hatte. Jetzt wird nie überschrieben (`config.yaml.bak.1`,
  `.2`, …), und wenn auf dem Board bereits genau diese Datei liegt, entsteht gar
  keine Sicherung.

### Kleinere Schärfen

Der Aufruf in der Kopfzeile erzeugte eine **andere Datei**, sobald
`--microsteps` den Wert des Standorts überschrieb — Faktor zwei auf beiden
Achsen. Eine `0` auf der Kommandozeile fiel bei fünf Optionen still auf die
Vorgabe zurück (`or` statt `is not None`). `--acceleration 0` und ein negativer
Vorschub endeten im Stapelabzug statt in einer Meldung. `--location` zusammen
mit `--kein-standort` wurde kommentarlos verworfen. `--out -` legte eine Datei
namens `-` an. `segment_length` wurde gar nicht geprüft, `hold_amps` hatte keine
Obergrenze, und ein Zeilenumbruch im Standortnamen brach die erzeugte Datei auf.

### Zwei Stellen, an denen die Doku übertrieb

Die Formel für `steps_per_mm` stand in drei Dokumenten **invertiert**: »Pulley ×
Riementeilung ÷ Mikroschritte« — richtig ist Vollschritte × Mikroschritte ÷
(Zähne × Riementeilung), also 200 × 16 ÷ 40 = 80. Nach der falschen Fassung
hätten mehr Mikroschritte *weniger* Schritte/mm bedeutet; wer damit nachrechnete,
korrigierte in die falsche Richtung — ausgerechnet in der Zeile, die diesen
Fehler erklären sollte.

Und viermal stand, das Auseinanderlaufen von `config.yaml` und Standort sei
früher »ohne jede Meldung« geblieben. Das stimmte nicht: `wallplotter-doctor`
prüfte die Ankerwerte schon vorher und warnte. Die echte Verbesserung ist eine
andere — die Meldung kam nur, wenn jemand den Selbsttest aufrief, und ihr Rat
verwies auf einen Befehl, der nur den Kinematikblock liefert.

### Ein übersprungener Test ist kein grüner Test

Der Test, der die Website gegen `design.py` hält, übersprang sich in der CI:
`tools/build_site.py` braucht `markdown`, und das stand in keinem Extra — der
Pages-Auftrag installierte es von Hand. Damit prüfte niemand mehr, ob Website
und Web-UI dieselben Farben benutzen; genau die Drift, gegen die der Test
gebaut wurde. Neu ist deshalb das Extra `site` (`markdown`, `pygments`), das
beide Aufträge installieren.

Dazu ein Fund aus dem eigenen Protokoll: Der Modulkopf von `fluidnc_schema.py`
zeigt den `grep`-Aufruf, mit dem die Schlüsselliste entstand — mit `handler\.item(`
darin. Python liest `\.` als ungültige Maskierung und warnt beim Übersetzen.
Aufgefallen ist es erst in der CI, weil eine warme `__pycache__` die Warnung
schluckt: Sie entsteht nur beim echten Übersetzen. `tests/test_quelltext.py`
übersetzt jetzt jede Datei aus `src/`, `tests/` und `tools/` einzeln und lässt
keine Warnung durch.

## Unveröffentlicht — ein Aussehen statt drei

Die Web-UI sah nach Standard-Quasar aus: blauer Riegel oben, graue Fläche
darunter, wie jedes Dashboard. Die Website hatte ihre eigene Palette, das
Terminal gar keine. Drei Oberflächen, drei Erscheinungen — und beim nächsten
Anfassen wären es vier gewesen.

`wallplotter/design.py` ist jetzt die eine Beschreibung, aus der alle drei ihre
Farben nehmen. Dieselbe Idee wie bei der `config.yaml`: zwei Beschreibungen
derselben Sache laufen auseinander, und niemand merkt es.

### Woher die Farben kommen

Nicht aus einer Palette von der Stange. Die Maschine zieht Tinte über eine Wand:

| Token | Hell | Warum |
| --- | --- | --- |
| `--bg` | `#fbfaf8` | Papier, nicht Weiß. Reines Weiß gibt es an einer Wand nicht. |
| `--fg` | `#23201c` | Tinte, nicht Schwarz. |
| `--accent` | `#1a4fd6` | **Die Stiftfarbe.** Steht seit jeher als Vorgabe in `PenToolhead.color` — der blaue Fineliner, mit dem das Projekt angefangen hat. |
| `--raster` | `#efece5` | Die Linien des Zeichenbretts, blasser als `--line`. |

Ein Test hält `--accent` gegen `PenToolhead().color`: Läuft das auseinander, hat
die Oberfläche eine andere Farbe als der Strich, den sie ankündigt — und die
Legende unter der Vorschau lügt.

Dunkel ist kein invertiertes Hell, sondern die Werkstatt am Abend.

### Web-UI

Kopfzeile aus Papier mit einer Haarlinie statt eines blauen Balkens; Karten mit
Kante statt Schlagschatten (technische Zeichnungen haben Kanten); Zahlen in
fester Breite, damit die Positionsanzeige beim Aktualisieren nicht springt; die
Vorschau als Zeichenbrett mit feinem Raster statt leerer Fläche; ein
gestricheltes Ablegefeld statt eines blauen Riegels mit weißem Kasten darunter;
Knopfbeschriftungen in Groß- und Kleinschreibung. Und Dunkelmodus, der dem Gerät
folgt — an der Wand im Keller steht abends jemand mit dem Handy.

**Zwei Dinge, die dabei nicht offensichtlich waren:**

* Quasars Stylesheet wird **nach** dem eigenen geladen. Bei gleicher Spezifität
  gewinnt das spätere, und `!important` hilft nicht, wenn beide es haben. Alle
  Überschreibungen brauchen deshalb eine Stufe mehr (`body .q-card`). Ein Test
  prüft das für jede Regel — die erste Fassung des Tests übersah die
  einzeiligen, und genau die waren wirkungslos.
* NiceGUI hängt an jeden Knopf ein `bg-primary`. Der blieb im Dunkelmodus im
  hellen Blau stehen, bis der Knopf `color=None` bekam.

### Terminal

`wallplotter-setup` bekommt Farbe und Struktur: ein Kopf mit Linie, Schrittzahl
im Akzent, die Begründung darunter zurückgenommen, Warnungen in Warnfarbe,
erledigte Schritte leise. Die Statusliste zeigt ✓ grün, · grau, ? in Warnfarbe.

Abgeschaltet wird das, wo niemand hinsieht: bei `NO_COLOR`, bei `TERM=dumb` und
immer dann, wenn die Ausgabe kein Terminal ist. Sonst landen Steuerzeichen in
jeder umgeleiteten Datei und in jedem CI-Protokoll.

### Website

`tools/build_site.py` zieht seine Palette aus demselben Modul. Ein Test baut die
Website und prüft, dass jede Farbe darin auch wirklich die aus `design.py` ist.

## Unveröffentlicht — geführte Einrichtung

`wallplotter-setup`: acht Schritte von der leeren Wand bis zum ersten Strich.

```bash
wallplotter-setup            # dort weiter, wo es aufgehört hat
wallplotter-setup --status   # nur nachsehen, was noch fehlt
wallplotter-setup --ab servo # gezielt einen Abschnitt
```

Jeder einzelne Schritt ging vorher auch schon — `wallplotter-location`,
`wallplotter-firmware`, `wallplotter-calibrate`, `plot`. Was fehlte, war die
**Reihenfolge**, und die verzeiht an drei Stellen keinen Fehler:

* Die Ankermaße müssen **nach einem Neustart am Referenzpunkt** genommen werden.
  FluidNC friert die Riemenlängen für (0,0) in `WallPlotter::init()` ein; wer
  erst joggt und dann nullt, misst Maße, die um die Jog-Strecke danebenliegen —
  und bekommt eine verzerrte Zeichnung ohne jede Meldung.
* Die `config.yaml` muss **vor** dem Einmessen der Fläche im Board stehen, sonst
  fährt die Kalibrierung in einem anderen Koordinatensystem als der spätere Plot.
* Der Stifttest geht **erst nach** dem Einmessen, weil das Muster eine Fläche
  braucht.

Der Wizard kennt diese Reihenfolge, prüft nach jedem Schritt nach und begründet
jeden Schritt an Ort und Stelle. Abbrechen und fortsetzen geht jederzeit: Jeder
Schritt sagt selbst, ob er erledigt ist — und wo das keine Software wissen kann
(hängt die Gondel am Anschlag?), steht `nicht prüfbar` statt einer Behauptung.

Ohne Board läuft die halbe Vorbereitung trotzdem: messen, rechnen, `config.yaml`
schreiben und prüfen. Die Schritte, die die Maschine fahren lassen, werden
sauber übersprungen und stehen am Ende als Liste da — statt einer Fehlermeldung
pro Schritt.

### Wie er prüfbar bleibt

Die Schritte wissen nichts über die Oberfläche, über die sie fragen; das läuft
über ein schmales `Dialog`-Protokoll. Damit läuft derselbe Ablauf im Terminal,
kann später in der Web-UI laufen, und im Test gegen ein Skript aus vorbereiteten
Antworten. Das Skript ist streng: Geht ihm die Antwort aus, nennt es die
unbeantwortete Frage — jede Änderung am Ablauf fällt im Test auf, statt still
eine Vorgabe zu nehmen.

Der Test fährt den ganzen Weg gegen ein Fake-Board, das sich zwischen den Ecken
tatsächlich bewegt (eine Attrappe mit fester Position hätte eine Fläche der
Größe null ergeben und nichts geprüft) und schaut danach nach, was wirklich
passiert ist: Standort gespeichert, `config.yaml` **im Flash und nicht auf der
Karte**, zwei GCode-Dateien hochgeladen und gestartet, `$Bye` geschickt.

### Nebenher

* `Location` hat einen optionalen `servo`-Block bekommen (`ServoSettings`:
  `down_value`, `up_value`, `dwell_s`). Der Stiftkatalog liefert nur
  Schätzungen; was am eigenen Aufbau herauskommt, bleibt jetzt stehen und
  erscheint in `wallplotter-location show` samt der passenden `plot`-Schalter.
* Der Nullpunkt-Schritt liest nach dem Neustart die Maschinenposition zurück und
  schlägt an, wenn sie nicht auf 0/0 steht — dann wurde nicht am Referenzpunkt
  neu gestartet.

## Unveröffentlicht — die `config.yaml` wird erzeugt, nicht getippt

Bis hierher gab es **zwei** Beschreibungen derselben Maschine: die Python-Seite
(Anker aus `location.py`, Motor aus `kinematics.py`, Grenzen aus `timing.py`,
Servowerte aus `toolhead.py`) und eine 300 Zeilen lange YAML-Datei, die jemand
von Hand nachzog. Zwei Beschreibungen laufen auseinander: Die Vorschau rechnet
mit den gemessenen Ankermaßen, das Board mit denen von vor drei Wochen, und das
Bild an der Wand ist verzerrt. `wallplotter-doctor` hat das schon vorher
angemerkt — aber eben nur, wenn man ihn aufrief, und sein Rat verwies auf
`wallplotter-location config`, das nur den Kinematikblock liefert. Fast alle
Funde der Gegenprüfung an dieser Datei waren im Kern Abschreibfehler.

### Neu: `wallplotter-firmware`

```bash
wallplotter-firmware config --location Keller --out config/fluidnc-wallplotter.yaml
wallplotter-firmware pruefen config/fluidnc-wallplotter.yaml   # ohne Board
wallplotter-firmware pruefen --host <ip>                       # die vom Board
wallplotter-firmware diff   config/fluidnc-wallplotter.yaml    # von Hand geändert?
wallplotter-firmware push   --host <ip> --location Keller
```

`config/fluidnc-wallplotter.yaml` ist damit ein **Erzeugnis**. Der Aufruf, der
genau diese Datei wiederherstellt, steht in ihrer Kopfzeile — und ein Test hält
beides zusammen: Die ausgelieferte Datei muss byteweise das sein, was der
Erzeuger schreibt, und der genannte Aufruf muss sie wirklich hervorbringen.
Die Kommentare gehen dabei nicht verloren; sie sind die halbe Substanz der
Datei und werden mitgeschrieben.

Drei Kopplungen, die von Hand schon danebengegangen sind, hält jetzt der
Erzeuger:

| Was | Folgt woraus |
| --- | --- |
| `left_anchor_*` / `right_anchor_*` | den drei Maßen des Standorts, per Trilateration — dieselbe Rechnung wie in der Vorschau |
| `steps_per_mm` | Vollschritte × Mikroschritte ÷ (Zähne × Riementeilung), also 200 × 16 ÷ 40 = 80; wer die Mikroschritte änderte und die Schritte vergaß, fuhr um denselben Faktor daneben |
| `speed_map` | Impulsfenster ÷ PWM-Periode; hier stand einmal `0=0.000% 100=100.000%`, womit der ganze Servoweg zwischen S5 und S10 lag |

### Fund 40, gefunden beim Bauen: der Kommentar hinter dem Wert

Beim Nachbauen von FluidNCs Tokenizer fiel etwas auf, das die Gegenprüfung
übersehen hatte — und das die ausgelieferte `config.yaml` unfahrbar machte.
FluidNC schneidet Kommentare am Zeilenende **nicht** ab. `parseValue()` nimmt
bei einem unquotierten Wert den ganzen Rest der Zeile; verworfen wird nur eine
Zeile, die *mit* `#` beginnt.

```yaml
stepping:
  idle_ms: 255 # Motoren gehalten lassen — die Gondel hängt am Riemen
```

Jeder YAML-Parser liest daraus `255`. FluidNC liest die ganze Zeile, gibt sie an
`intValue()`, und `from_decimal` verlangt, dass die *gesamte* Zeichenkette die
Zahl ist. Es folgt `parseError()` — also `set_state(State::ConfigAlarm)`. **Das
Board fährt nicht.**

Die Datei hatte elf solche Zeilen. Vier davon tödlich (`idle_ms`, `run_amps`,
`hold_amps`, `pwm_hz`), eine still gefährlich (`boolValue()` vergleicht die
ganze Zeile mit `"true"` — mit einem Kommentar dahinter kommt nie `true`
heraus), sechs an Pins und Texten, die den Kommentar in den Wert übernommen
hätten. Geprüft über v3.8.0, v3.9.8, v4.0.4 und `main`: dieselbe Funktion. Und
in BTTs eigener `rodent.yaml` trägt keine einzige Zeile einen Kommentar hinter
dem Wert — das war kein Zufall.

Behoben doppelt: Der Erzeuger setzt jeden Kommentar in die Zeile darüber
(auch im auskommentierten Laserblock, damit das Entkommentieren keine Falle
ist), und `check_lines()` liest eine beliebige `config.yaml` mit den Regeln des
Tokenizers und meldet den Fall. Einzelheiten in der
[Gegenprüfung](docs/firmware-gegenpruefung.md), Abschnitt 2.6.

### Die Schlüsselliste aus dem Firmware-Quelltext

Neu ist `wallplotter.fluidnc_schema`: welche Schlüssel FluidNC in welchem
Abschnitt kennt und auf welchen Bereich es sie klemmt. Mechanisch aus
`bdring/FluidNC 8a0f8c8` gezogen (`handler.item()`, `handler.section()`,
`InstanceBuilder<>`), nicht aus dem Wiki abgeschrieben. Jeder Eintrag trägt
seine Fundstelle.

Dazu kommt `check_lines()` — dieselbe Datei, aber mit dem Blick von FluidNCs
eigenem Tokenizer statt dem eines YAML-Parsers. Nur so ist Fund 40 zu sehen.
Diese Prüfung braucht kein PyYAML und läuft deshalb auch im nackten Kern.

Die Schlüsselliste unterscheidet, was auch die Firmware unterscheidet:

* **Unbekannter Schlüssel** → `ConfigAlarm`, das Board fährt nicht. Genau so
  hätte ein `laser_mode` die Maschine stillgelegt.
* **Wert außerhalb des Bereichs** → `constrain_with_message()` klemmt ihn und
  warnt. Das Board fährt.

Abschnitte, die die Liste nicht führt, meldet sie als **ungeprüft** statt als
falsch.

### Was `check()` zusätzlich meldet

Zwei Verbraucher auf einem GPIO (FluidNC belegt Pins exklusiv — die Datei ließe
sich nicht einmal parsen), ein Ausgang auf `gpio.34` bis `gpio.39` (am ESP32
reine Eingänge), eine Laserspindel mit Servotakt oder mit der `tool_num` des
Stifts, `run_amps` über dem Motornennstrom, `idle_ms` unter 255 (die Gondel
hängt am Riemen), ein Impuls, der nicht in die PWM-Periode passt.

### Freitext im Kopf

`board`, `name` und `meta` werden jetzt in Anführungszeichen gesetzt, sobald sie
welche brauchen. Ohne das legte ein Maschinenname mit Doppelpunkt die Datei lahm
(`name: Wand: groß` ist kein gültiges YAML), und eine Raute schnitt den Rest der
Zeile ab.

Enger als YAML ist das dabei aus einem Grund: FluidNCs Tokenizer kennt **keine
Maskierung** — `parseValue()` liest bis zum ersten passenden Anführungszeichen
und wirft den Rest der Zeile weg. Ein Backslash bleibt ein Backslash. Deshalb
einfache Anführungszeichen zuerst, doppelte nur ohne Backslash, und ein Text mit
beiden Sorten wird abgelehnt statt kaputt geschrieben.

### Zwei Netzteile waren einmal zu kurz gedacht

Aus dem Rodent-Handbuch (Pinbild S. 6/7, Schaltpläne S. 8/9/12/13) noch einmal
nachgeschlagen, weil die Empfehlung „der Servo braucht ein eigenes 5-V-Netzteil"
schlicht unbequem war — und tatsächlich falsch:

* Unbrauchbar ist nur der **+5-V-Pin am Spindelstecker** (100 Ω in Reihe, R25).
  Der 5-V-Zweig der Platine selbst kommt ohne Vorwiderstand am **OLED-Stecker**
  (Pin `+5V`) und an jedem **Endstop-Stecker** (Pin `V-Lim`, `SW_VCC`-Brücke auf
  +5 V) heraus. Die 1 kΩ im Endstop-Schaltplan liegen in der LED-Strecke des
  Optokopplers, nicht im Versorgungspin — sonst ließe sich dort auch kein
  NPN-Näherungsschalter mit 12 V betreiben, wofür die Spannungswahl da ist.
* Damit reicht **ein** Netzteil für die ganze Maschine. Der Rodent nimmt
  DC 24–56 V und erzeugt daraus selbst +12 V und +5 V; für zwei NEMA17 mit
  1,2 A Phasenstrom genügen 24 V bei 4–5 A. Weder Endstops noch Motoren brauchen
  eine eigene Versorgung.
* Dazu gehört ein Elko (470–1000 µF) plus 100 nF direkt an der Gondel. Ein
  Stiftheber zieht im Mittel fast nichts, aber für rund 100 ms beim Hub einige
  hundert Milliampere — der Puffer liefert genau diese Spitze vor Ort.
* Wie viel der 5-V-Zweig hergibt, steht im Handbuch nirgends. Also nachmessen;
  sackt die Spannung unter ~4,5 V, kommt ein Abwärtswandler (24 V → 5 V) an die
  Motorklemme. Auch das ist kein zweites Netzteil.

Und eine Richtigstellung in die andere Richtung: Hier stand, `gpio.2`, `gpio.4`
und `gpio.12` seien am Rodent nirgends herausgeführt. **Falsch** — sie schalten
die drei V-MOS-Leistungsausgänge (DC 12–36 V, bis 5 A). Für einen Servo bleiben
sie ungeeignet (Low-Side-MOSFETs, keine Logikausgänge), aber der Air Assist des
Lasers hängt jetzt dort statt an `NO_PIN`: `coolant: flood_pin: gpio.4`.

Neu in der Bauanleitung außerdem: **die DIAG-Steckbrücken müssen ab**, wenn die
Not-Halt-Taster benutzt werden. `DIAGX`→gpio.35, `DIAGY`→gpio.34, `DIAGZ`→gpio.33,
`DIAGE`→gpio.32 liegen auf denselben Pins; bleibt eine drin, treibt der Treiber
gegen den Taster.

### Flash statt SD-Karte

`FluidNCClient` kann jetzt beides auseinanderhalten, weil FluidNC es
auseinanderhält: `upload_local()` schreibt über `POST /files` in den **Flash**,
wo die Firmware ihre Konfiguration liest (`FileStream(filename, "rb",
LocalFS)`); `download_local()` liest sie über den Nicht-gefunden-Zweig des
Webservers zurück; `restart()` schickt `$Bye`. Ein Upload der `config.yaml` auf
die SD-Karte hätte erfolgreich ausgesehen und nichts bewirkt.

`push` sichert die bisherige Fassung, bevor es überschreibt, und weigert sich,
eine Datei zu übertragen, die das Board in ConfigAlarm setzen würde — von dort
holt es nur die serielle Schnittstelle zurück.

### Nebenher

* `wallplotter-doctor` prüft die `config.yaml` jetzt dreifach: kennt FluidNC
  jeden Schlüssel, passen die Ankermaße zum aktiven Standort, ist die Datei noch
  das Erzeugnis.
* `wallplotter-location config` gibt weiterhin nur den Kinematikblock aus und
  sagt jetzt dazu, womit man die ganze Datei bekommt.
* Die Testattrappe hat ein zweites Dateisystem: Karte und Flash sind getrennt,
  und während einer Fahrt liefert der Flash-Zweig **503** statt der Datei —
  genau wie das Board.
* CI hält die ausgelieferte Datei gegen den Erzeuger und gegen die
  Schlüsselliste.

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

Der Workflow schaltet Pages beim ersten Lauf selbst frei. Ohne das scheiterte
er an `configure-pages` mit „Get Pages site failed" — und zwar erst *nach* dem
Bauen, was aussieht, als sei die Website kaputt, obwohl bloß ein Schalter in
den Repo-Einstellungen fehlte.

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
