# Gegenprüfung gegen FluidNC und das Rodent-Board

*Stand: August 2026 · geprüft gegen FluidNC `main` sowie die Freigaben v4.0.4,
v3.9.8 und v3.8.0, BTTs `rodent.yaml` und das Rodent-Handbuch V1.03*

Bis zu dieser Prüfung war alles Board-nahe „nach Dokumentation gebaut". Hier
wurde stattdessen der Quelltext der Firmware gelesen, und das Ergebnis war
unangenehm: **die Endpunkte stimmten nicht, die ausgelieferte `config.yaml`
hätte das Board gar nicht erst starten lassen, und der Not-Halt tat nichts.**

> **Alles davon ist inzwischen behoben** — wie, steht in
> [Abschnitt 6](#6-wie-es-behoben-wurde), was sich sonst geändert hat im
> [CHANGELOG](../CHANGELOG.md). Ein sechster Fund kam später dazu, beim Bau des
> Erzeugers: [Abschnitt 2.6](#26-ein-kommentar-hinter-einem-wert-legt-das-board-still). Dieses Dokument bleibt trotzdem stehen, und
> zwar vollständig: Die Begründungen sind der Grund, warum die Wege heute so
> aussehen, wie sie aussehen. Ohne sie sieht `TelnetChannel` neben einem
> vorhandenen HTTP-Client nach unnötigem Umweg aus — und jemand vereinfacht
> ihn zurück.

Die Zeilennummern der Firmware beziehen sich auf `FluidNC/src/…` im
Repo [bdring/FluidNC](https://github.com/bdring/FluidNC). Die Zeiten unten
stehen im Präsens, weil sie die Firmware beschreiben; die ist unverändert.

## 1. Was jetzt schon behoben ist

`config/fluidnc-wallplotter.yaml` ist im selben Zug korrigiert worden — sie ist
das, was aufs Board wandert, und eine Bauanleitung, die eine Datei ausliefert,
die das Board in Alarm setzt, wäre keine.

| Was | Vorher | Jetzt |
| --- | --- | --- |
| `Laser: laser_mode: false` | unbekannter Schlüssel → **ConfigAlarm beim Start** | ersatzlos gestrichen |
| `speed_map: 0=0% 100=100%` | S30 = 6 ms Impuls, Servo im Anschlag | `0=5.000% 100=10.000%` → S0 = 1,0 ms, S100 = 2,0 ms |
| Servo-Pin `gpio.25` mit „PRÜFEN" | geraten | belegt: `Sp-Enable` (CN51), Schaltplan im Handbuch |
| kein `control:`-Block | kein wirksamer Halt | `feed_hold_pin`, `cycle_start_pin`, `reset_pin` vorbereitet |
| Homing-Kommentar | empfahl StallGuard + `$H` | `$H` geht mit dieser Kinematik nicht — steht jetzt da |
| Kommentar hinter dem Wert | elf Zeilen, vier davon **ConfigAlarm** (siehe 2.6) | jeder Kommentar steht in der Zeile darüber |

Alles Übrige in dieser Datei wurde Schlüssel für Schlüssel gegen den
FluidNC-Quelltext gehalten und ist gültig: `stepping`, `axes`, `motor0`, alle
18 `tmc_5160`-Schlüssel, `pwm`, `start`, `i2so`, `spi`, `sdcard`,
`status_outputs`. Kleinschreibung `pwm:` trifft die Sektion `PWM` korrekt
(`Configuration/Parser.cpp:27`, `strncasecmp`).

---

## 2. Die sechs Dinge, die das Board blockiert hätten

### 2.1 Ein unbekannter Schlüssel ist kein Schönheitsfehler

```cpp
// Configuration/ParserHandler.h
if (_parser._token._state == TokenState::Matching) {
    log_config_error("Ignored key " << _parser.key());
}
// Logging.h:83
#define log_config_error(x) if (…) { …; set_state(State::ConfigAlarm); }
```

Ein einziger unbekannter Schlüssel versetzt FluidNC in **ConfigAlarm**. Die
Maschine läuft dann gar nicht. `laser_mode` war so ein Schlüssel: `Laser` *ist*
ein registrierter Spindeltyp (`Spindles/LaserSpindle.cpp:41`), aber
`Laser::group()` kennt nur `pwm_hz` plus die gemeinsamen Spindelfelder — ein
`laser_mode` gibt es nirgends. Lasermodus ist in FluidNC keine Einstellung,
sondern die Klasse der aktiven Spindel; `$32` ist ein reiner Lese-Proxy auf
`spindle->isRateAdjusted()` (`SettingsDefinitions.cpp:146`).

Nebenwirkung, die genauso gezählt hätte: der Block hätte eine zweite Spindel
angelegt, ebenfalls mit `tool_num: 0`. `std::sort` in
`Machine/MachineConfig.cpp:229` ist nicht stabil — welche der beiden nach dem
Start aktiv gewesen wäre, war offen.

### 2.2 `/sdfiles` gibt es nicht — und zwar in keiner Version

```cpp
// WebUI/WebUIServer.cpp:365,373
_webserver->on("/files",  HTTP_ANY, handleFileList,           LocalFSFileupload); // FLASH
_webserver->on("/upload", HTTP_ANY, handle_direct_SDFileList, SDFileUpload);      // SD-KARTE
```

Es ist genau umgekehrt zu dem, was `upload.py` und das Handbuch behaupten:
**`/files` ist der Flash, `/upload` ist die Karte.** Geprüft in `main`, v4.0.4,
v3.9.8 und v3.8.0 — überall gleich. `POST /sdfiles` landet im
Not-Found-Handler und endet als HTTP 404; es kommt nie eine Datei auf die
Karte.

Der gemeinsame Handler `handleFileOps()` kennt außerdem nur `path`, `action`
(mit `filename`, und nur `delete`/`deletedir`/`createdir`/`rename`) und
`dontlist`. `createPath=yes` und `action=list` aus `upload.py` gibt es nicht.

### 2.3 `/command?plain=` ist der falsche Kanal — und der Not-Halt meldet Erfolg

Der Kommando-Endpunkt hat zwei Pfade, und das Repo benutzt durchgehend den
falschen:

```cpp
// WebUI/WebUIServer.cpp:778-800
if (hasParam("cmd")) {
    if (cmdUpper.startsWith("[ESP") || cmdUpper.startsWith("$/"))  synchronousCommand(…);
    else                                                           websocketCommand(…);  // GCode + Realtime
}
if (hasParam("plain")) synchronousCommand(…);                                            // nur $-Kommandos
```

`synchronousCommand()` reicht die Zeile an **`settings_execute_line()`** weiter,
nicht an `execute_line()`. Und das wirft das erste Zeichen weg:

```cpp
// ProcessSettings.cpp:1216-1229
Error settings_execute_line(const char* line, …) {
    std::string_view key(line + 1);          // Zeichen 0 fällt raus
    string_util::split(key, value, *line == '[' ? ']' : '=');
    return do_command_or_setting(key, value, auth_level, out);
}
```

Was daraus folgt:

| Aufruf | Was FluidNC daraus macht | Ergebnis |
| --- | --- | --- |
| `$SD/Run=/x.gcode` | Schlüssel `SD/Run`, Wert `/x.gcode` | **funktioniert** |
| `$J=G91 G21 X-100 F1000` | Schlüssel `J` → `doJog` | **funktioniert** |
| `!` `~` `0x18` `0x85` | Schlüssel `""` → `UserCommand("", "Help", …)` (`ProcessSettings.cpp:1028`) | **HTTP 200, Hilfetext, keine Wirkung** |
| `?` | dito — Hilfezeile statt `<Idle\|MPos:…>` | kein Status |
| `G92 X0 Y0` | Schlüssel `92 X0 Y0` → kein Treffer | **HTTP 500** |
| `G10 L20 P1 X0 Y0` | dito | **HTTP 500** |

Die vier Realtime-Bytes sind der schlimmste Fall: **sie melden Erfolg und tun
nichts.** Pause, Weiter, Stopp und Jog-Abbruch der Web-UI sind wirkungslos.

Dazu kommt eine zweite Sperre in derselben Funktion:

```cpp
if (http_block_during_motion->get() && inMotionState()) {
    request->send(503, "text/plain", "Try again when not moving\n");
```

`DEFAULT_HTTP_BLOCKED_DURING_MOTION` ist 1. Während ein Plot läuft, antwortet
`/command` also überhaupt nur mit 503 — genau in dem Zustand, in dem man
anhalten will. Praktisch heißt das: „Pause" während der Fahrt scheitert
sichtbar mit 503, „Weiter" und „Stopp" im Halt melden lautlos Erfolg. Einen
wirksamen Not-Halt über HTTP gibt es derzeit nicht.

**Die Abhilfe steht drei Zeilen weiter oben in derselben Firmware-Datei:**

```cpp
// WebUI/WebUIServer.cpp:359-361, Handler ab 986
_webserver->on("/feedhold_reload",   …);  // protocol_send_event(&feedHoldEvent)
_webserver->on("/cyclestart_reload", …);  // protocol_send_event(&cycleStartEvent)
_webserver->on("/restart_reload",    …);  // protocol_send_event(&rtResetEvent)
```

Schlichte HTTP-GETs, ohne WebSocket, ohne `plain=`, ohne Realtime-Byte, und
nicht von der Bewegungssperre betroffen. Sie antworten mit einem Redirect — ein
Client darf 302 nicht als Fehlschlag lesen. Für den Jog-Abbruch (0x85) gibt es
keinen solchen Endpunkt; der geht nur über den WebSocket unter `/`.

### 2.4 Die Anker beziehen sich auf den Bootzeitpunkt, nicht auf `G92`

```cpp
// Kinematics/WallPlotter.cpp:61-76
void WallPlotter::init() {
    // We assume the machine starts at cartesian (0, 0, 0).
    xy_to_lengths(0, 0, zero_left, zero_right);
    …
    init_position();   // set_steps(axis, 0)
}
```

Die Firmware friert die Riemenlängen für kartesisch (0,0) **beim Start bzw. beim
Neuladen der Konfiguration** ein. Der Maschinennullpunkt ist damit exakt die
Stelle, an der die Gondel in diesem Moment hing. Ein späteres `G92` verschiebt
nur das *Werkstück*-Koordinatensystem und lässt die Kinematik unberührt.

`location.py` dokumentiert und rechnet aber so, als seien die drei
Zollstockmaße am `G92`-Punkt genommen. Beides fällt nur zusammen, wenn zwischen
Einschalten und Messen nicht gefahren wurde. Der Ablauf muss deshalb lauten:
**Gondel an den Referenzpunkt hängen → Board neu starten → dann erst messen und
nullen.**

Dazu passt ein zweiter Bruch: `wallplotter-calibrate record` nimmt die Ecken aus
`MPos` auf (Vorgabe `$10=1`, `SettingsDefinitions.cpp:101`), der erzeugte GCode
läuft aber in Werkstückkoordinaten. Sobald ein `G92`- oder `G54`-Versatz
gesetzt ist, liegt die Zeichnung um genau diesen Versatz daneben.

### 2.5 `$H` gibt es mit dieser Kinematik nicht

```cpp
// Kinematics/WallPlotter.cpp
bool WallPlotter::canHome(AxisMask axisMask) {
    log_error("This kinematic system cannot home");
    return false;
}
bool WallPlotter::kinematics_homing(AxisMask& axisMask) { return false; }
```

Die Empfehlung „sensorloses StallGuard-Homing, dann `$H`, dann G54 dauerhaft"
steht in `config.yaml`, README, Handbuch, Projektidee und in
`calibrate_cli.py` — und sie kann nicht funktionieren. Die TMC2160 *können*
StallGuard, und das Rodent führt die DIAG-Ausgänge sogar per Steckbrücke auf
die Endstop-GPIOs. Nur nimmt die WallPlotter-Kinematik keine Referenzfahrt an.

Was bleibt: der mechanische Anschlag als Referenz, das Board dort neu starten,
und der Versatz per `G10 L20 P1` in G54 — das liegt tatsächlich im NVS
(`SettingsDefinitions.cpp:72`, `is_saved = true`), anders als G92
(`Zeile 89`, `is_saved = false`).

Nebenbei richtiggestellt: `M2` verwirft den G92-Versatz **nicht**. Es setzt
motion, plane, distance, feed, `coord_select` auf G54, Spindel und Kühlung
zurück (`GCode.cpp:1949-1990`) — `gc_state.coord_offset` bleibt stehen.

---

### 2.6 Ein Kommentar hinter einem Wert legt das Board still

Dieser Fund kam erst dazu, als die `config.yaml` zum Erzeugnis wurde — und er
ist der unangenehmste der Reihe, weil er nach nichts aussieht. Die
ausgelieferte Datei hatte elf solche Zeilen, vier davon tödlich:

```yaml
stepping:
  idle_ms: 255 # Motoren gehalten lassen — die Gondel hängt am Riemen
```

Das ist gültiges YAML, jeder Parser liest daraus `255`. **FluidNC nicht.** Es
bringt einen eigenen Tokenizer mit, und der schneidet Kommentare am Zeilenende
nicht ab:

```cpp
// Configuration/Tokenizer.cpp — nextLine()
if (_line.front() == '#') {          // Comment till end of line
    _line.remove_prefix(_line.size());
}
```

Verworfen wird also nur eine Zeile, die *mit* `#` beginnt. Der Wert dagegen:

```cpp
// Configuration/Tokenizer.cpp — parseValue()
auto delimiter = _line.front();
if (delimiter == '"' || delimiter == '\'') {
    …                                 // bis zum schließenden Zeichen, Rest fällt weg
} else {
    _token._value = _line;            // der GANZE Rest der Zeile
}
```

`_token._value` ist damit `255 # Motoren gehalten lassen — die Gondel hängt am
Riemen`. Was daraus wird, hängt am Typ des Schlüssels:

| Typ | Umwandlung | Folge |
| --- | --- | --- |
| Zahl | `intValue()` / `floatValue()` | `parseError()` → **ConfigAlarm** |
| Wahrheitswert | `boolValue()` | stillschweigend `false` |
| Pin | `Pin::create()` | ErrorPin, `Setting up pin … failed` |
| Text | `stringValue()` | Kommentar wird Teil des Werts |

Die Zahlen sind der harte Fall, und zwar ohne Kulanz:

```cpp
// string_util.cpp
bool from_decimal(std::string_view sv, int32_t& value) {
    auto [ptr, ec] = std::from_chars(sv.data(), sv.data() + sv.length(), value);
    if (ec == … || ptr != sv.data() + sv.length()) { return false; }   // GANZE Zeichenkette
    return true;
}
```

`from_float` prüft genauso auf `floatEnd == str + length`. Schlägt beides fehl,
ruft `Parser::intValue()` `parseError()` — und das ist
`set_state(State::ConfigAlarm)` (`Tokenizer.cpp:25`). Betroffen waren
`idle_ms`, `run_amps`, `hold_amps` und `pwm_hz`; beim ersten davon wäre Schluss
gewesen.

Der stille Fall ist fast schlimmer: `boolValue()` vergleicht die ganze
Zeichenkette mit `"true"`. Ein `hard_limits: true # nur zur Sicherheit` ist
damit **aus**, und gemeldet wird nichts.

Geprüft über alle Freigaben: `parseValue()` ist in v3.8.0, v3.9.8, v4.0.4 und
`main` Zeile für Zeile dieselbe Funktion. Und das Gegenstück steht in BTTs
eigener `rodent.yaml`: dort trägt **keine einzige** Zeile einen Kommentar
hinter dem Wert. Das war kein Zufall.

Behoben ist es zweifach: Der Erzeuger setzt jeden Kommentar in die Zeile
*darüber*, und `fluidnc_schema.check_lines()` liest eine beliebige `config.yaml`
mit denselben Regeln wie der Tokenizer und meldet den Fall. Ein YAML-Parser
kann das nicht finden — deshalb prüft `wallplotter-firmware pruefen` mit beiden
Blickwinkeln.

Was **erlaubt** bleibt: ein Kommentar hinter einem quotierten Wert. Dort liest
`parseValue()` bis zum schließenden Anführungszeichen und wirft den Rest der
Zeile weg — `name: "Wand" # Notiz` geht also. Sauberer ist trotzdem die eigene
Zeile.

---

## 3. Vollständige Fundliste

39 Funde haben eine adversarische Gegenprüfung überstanden (von 46 gemeldeten);
Fund 40 kam später beim Bau des Erzeugers dazu. Sortiert nach Schwere; die
Zeilennummern sind die zum Zeitpunkt der Prüfung.

### Kritisch

| # | Fund | Ort |
| --- | --- | --- |
| 1 | Pause/Stopp/Jog-Abbruch erreichen die Firmware nie und melden Erfolg | `upload.py:175` |
| 2 | `/sdfiles` existiert nicht — Upload und Dateiliste laufen in 404 | `upload.py:192,214` |
| 3 | `G92` und `G10 L20` werden nie ausgeführt (HTTP 500) | `upload.py:283,303` |
| 4 | Zeilen über 127 Byte brechen den Lauf ab (`error:14`) | `gcode.py:236` |
| 5 | Ankerkoordinaten beziehen sich auf den Bootpunkt, nicht auf `G92` | `location.py:43` |
| 6 | Kalibrierung nimmt `MPos` auf, fährt es als Werkstückkoordinate an | `calibrate_cli.py:145` |
| 7 | Web-UI antwortet auf jede Anfrage mit HTTP 500 (NiceGUI ≥ 3) | `webapp.py:866` |
| 8 | Laser läuft über die Ebenenzuordnung ohne Scharfschaltung los | `webapp.py:374` |
| 9 | `Laser: laser_mode` setzt das Board in ConfigAlarm | `config.yaml` ✔ behoben |
| 10 | Handbuch erklärt den wirkungslosen Weg für begründet | `wandplotter-handbuch.md:324` |
| 40 | Kommentar hinter einem Zahlenwert → ConfigAlarm (elf Zeilen, vier tödlich) | `config.yaml` ✔ behoben |

Zu **4**: `GCode.cpp:246-249` prüft `strlen(input_line) > 127` **vor** dem
Entfernen der Kommentare, zählt also Bytes inklusive Kommentar. Die Folge ist
kein Alarm, sondern `Job::abort()` mit `error:14 Line too long` — der Lauf
stirbt mitten im Bild. Erreicht wird das heute nur vom Laserkopf: seine
`; ACHTUNG:`-Zeilen sind 168 und 186 Byte lang; ein Stiftprogramm bleibt bei 95.

Zu **7**: NiceGUI ab 3.0 führt pro Seitenaufruf
`runpy.run_path(sys.argv[0], run_name='__main__')` aus. Bei `python -m
wallplotter.webapp` ist `sys.argv[0]` der Dateipfad, der Wiederablauf als
Top-Level-Skript stirbt an `from .calibration import …`. Nachgestellt: `curl
http://127.0.0.1:8080/` → HTTP 500. Dieselbe App aus einer normalen Skriptdatei
heraus → HTTP 200. `pyproject.toml` erlaubt `nicegui>=2.0`, die Tests rufen nur
`create_app()` und nie `ui.run()` — deshalb bleibt die Suite grün.

### Hoch

| # | Fund | Ort |
| --- | --- | --- |
| 11 | `?` liefert keinen Status — `status()`, `position()`, `--from-board` gehen nie | `upload.py:227` |
| 12 | Multipart-Dateiname ohne Verzeichnis, Größenprüfung fällt aus | `upload.py:195` |
| 13 | Laserprogramme wählen keine Spindel an — `M4` auf der Servospindel | `toolhead.py:412` |
| 14 | Stift-S-Werte passen nicht zur speed_map | `toolhead.py:269` ✔ behoben |
| 15 | Farbebenen mit gleicher Beschriftung überschreiben sich | `pipeline.py:43` |
| 16 | `spiral`: bei kleinem Bahnabstand verschwindet das Bild | `imaging.py:268` |
| 17 | Nach dem Laden eines Musters bleibt die alte Ebenenliste bedienbar | `webapp.py:283` |
| 18 | Status meldet während des Plots „nicht erreichbar" und 0 % | `webapp.py:564` |
| 19 | `--layers` schaltet Resonanzprüfung und Laufzeitschätzung still ab | `cli.py:451` |
| 20 | `speed_map` fährt den Servo in den Anschlag | `config.yaml` ✔ behoben |
| 21 | Nullpunkt-Weg beruht auf zwei falschen Firmware-Aussagen | `config.yaml` ✔ behoben |
| 22 | Web-API-Tabelle im Handbuch ist umgekehrt | `handbuch:329` |
| 23 | `test_firmware_config.py` prüft die YAML nur gegen sich selbst | `tests/` |

### Mittel und niedrig

`resume_cli.py:91` (Absturz bei nicht-UTF-8 nach dem Schreiben) ·
`gcode.py:302` (der Wechseltext der `M0`-Pause erreicht niemanden — FluidNC
protokolliert nur `(MSG,…)`) · `kinematics.py:166` (`resolution_mm` bildet
falsche Spaltennormen, meldet die falsche Problemzone und dreht den adaptiven
Vorschub um; richtig wäre `step_mm / |sin(Riemenwinkel)|`) · `motion.py:53`
(Resonanzprüfung misst Linienabstand statt Bahnlänge) ·
`calibrate_cli.py:124` (StallGuard-Hinweis) · `timing.py:203` (Leerwege mit
`travel_feed` gerechnet, ausgegeben wird `G0`) · `imaging.py:261` (keine
Schranke für die Punktzahl) · `timing.py:74` (`MotionLimits` sind
Motorraumgrenzen, keine Bahngrenzen — ortsabhängig bis Faktor 1,3 daneben) ·
`webapp.py:128,723,464` (keine S-Wert-Grenzen, Rechnung blockiert die
Event-Loop, Erfolgsmeldungen ohne Bestätigung) · `toolhead.py:203` (negative
S-Werte) · `gcode.py:110` (Laufzeit im Dateikopf ignoriert Vorschübe je Linie) ·
`geometry.py:64` (entartete Geometrie wird unverändert durchgereicht) ·
`README.md:173` (Fortschritt kommt als Prozent, nicht als Bytes:
`SD:<prozent>,<pfad>`, `InputFile.cpp:85`).

---

## 4. Was zusätzlich auffiel

**Es gibt derzeit überhaupt keinen funktionierenden Weg, die Maschine
anzuhalten.** Über HTTP nicht (Abschnitt 2.3), und einen `control:`-Block hat
die `config.yaml` bisher nicht gehabt. Dabei liegt die Hardware frei: weil die
WallPlotter-Kinematik nicht referenzieren kann, sind alle fünf optogekoppelten
Endstop-Eingänge des Rodent unbenutzt. `Machine/Control.cpp` bietet
`feed_hold_pin`, `cycle_start_pin`, `reset_pin`, `estop_pin`, `fault_pin`,
`safety_door_pin` und `macro0..3_pin` — jeder löst direkt das entsprechende
Ereignis aus. Ein `cycle_start_pin` ist außerdem die einzige heute
funktionierende Art, nach der `M0`-Stiftwechselpause weiterzumachen. Der Block
steht jetzt in der `config.yaml`.

**`download()` ist die einzige Board-Funktion im Repo, die heute schon geht.**
FluidNC hängt WebDAV auf `/sd` (SD) und `/flash` ein, und `WebUI/WebDAV.cpp`
bedient `HTTP_GET` **und `HTTP_PUT`**. `GET /sd/<pfad>` funktioniert also — und
`PUT /sd/<pfad>` wäre die kleinste denkbare Reparatur für den `/sdfiles`-Fund:
ein Request, kein Multipart.

**Das Test-Badge beschreibt einen Lauf, den die CI nicht macht.** `pytest`
liefert lokal mit allen Extras wörtlich `424 passed, 1 skipped` (425 gesammelt,
der Skip ist das optionale `hatched`) — die Zahl im README stimmt also. Nur:
Job `test` in `.github/workflows/ci.yml` installiert `.[dev,web,photo]` ohne
vpype, Job `test-with-vpype` installiert `.[geometry,hatch,dev]` ohne NiceGUI
und trägt `continue-on-error: true`. Kein Job erzeugt die 424, und der zweite
kann die CI nie rot färben.

**Jede board-nahe Attrappe in den Tests antwortet auf alles mit Erfolg.**
`test_upload.py` gibt immer 200/„ok", `test_calibrate_cli.py` liefert auf jeden
`plain=`-Aufruf eine gültige Statuszeile, `test_sdstore.py` sieht die URL gar
nicht an. Kein Test prüft einen Antwortcode ≠ 200. Das ist der Mechanismus,
über den sämtliche Firmware-Funde grün geblieben sind. Eine aufgezeichnete
echte FluidNC-Antwort liegt nirgends im Repo.

**`wallplotter-correct anpassen` bewertet die Korrektur an genau den Punkten,
aus denen sie gerechnet wurde.** Bei drei Messpunkten ist die affine Anpassung
exakt, der gemeldete Restfehler also zwangsläufig 0 — die eingebaute
Ehrlichkeitswarnung (`if after > before * 0.7`) kann dann gar nicht auslösen.
Beim dokumentierten Standardweg (`raster --steps 4`, 16 Kreuze) wählt
`_degree_for` „kubisch", also 10 Unbekannte aus 16 Punkten; in einer Simulation
mit ±0,5 mm Ablesefehler meldet das kubische Modell an den Messpunkten den
kleineren Fehler (0,45 gegen 0,63 mm) und ist zwischen ihnen doppelt so falsch
(0,63 gegen 0,34 mm).

**Der Selbsttest kann das Board nicht prüfen und meldet das als Entwarnung.**
`doctor.check_board()` beginnt mit `client.status()` — dem Weg, der nie einen
Status liefert. Der Fehlerzweig lautet „Solange nichts an der Wand hängt, ist
das in Ordnung". Ein angeschlossenes, antwortendes Board wird also als „noch
nicht da" gemeldet, und Alarmzustand, Position und SD-Prüfung werden nie
erreicht.

**Die Parkfahrt am Programmende ignoriert den Flächenversatz.** `gcode.py:273`
schreibt bedingungslos `X0 Y0`. In dem Ablauf, den das Repo selbst empfiehlt,
liegt Maschinen-(0,0) *über* der Zeichenfläche — jedes Programm endet also mit
einer Fahrt an den Punkt der schlechtesten Riemengeometrie.

---

## 5. Geprüft und in Ordnung

Damit es niemand „repariert":

* `idle_ms: 255` ist korrekt der Sonderwert „Motoren bestromt lassen"
  (`Protocol.cpp:813`) — die Gondel sackt nicht ab.
* `G4 P0.25` rechnet FluidNC als `int32_t(p * 1000.0f)`, also Sekunden mit
  Millisekundenauflösung (`GCode.cpp:1807`). Die Servo-Wartezeiten stimmen.
* `M6 T<n>` schaltet die Spindel tatsächlich um (`GCode.cpp:1656-1678`).
* `M0` wird unterstützt (`ProgramFlow::Paused` → Feed-Hold-Ereignis).
  `M1` dagegen ist laut Quelltextkommentar **nicht implementiert** und hält
  nicht an.
* Die Vorschubumrechnung der Kinematik
  (`feed_rate = cartesian_feed_rate * motor_segment_length / cartesian_segment_length`)
  macht `F` wirklich zum kartesischen Bahnvorschub.
* `kinematics.position()` im Repo entspricht Formel für Formel FluidNCs
  `lengths_to_xy()`. Beide setzen **gleiche Ankerhöhen** voraus — `distance` ist
  dort nur die X-Differenz. Die Trilateration in `location.anchors()` liefert
  genau das, ist also verträglich.
* GRBLs Planer-Nachbildung in `timing.py` (Junction-Geschwindigkeit,
  Trapez- und Dreiecksprofil), Douglas-Peucker in `geometry.simplify` und die
  gemeinsame Einpassung aller Farbebenen über `fit_bounds` wurden nachgerechnet
  und sind richtig.

---

## 6. Wie es behoben wurde

| Fund | Lösung |
| --- | --- |
| `/sdfiles` | `POST /upload` für die Karte, `GET /upload?path=/` fürs Listen, `GET /sd/<pfad>` fürs Lesen. Der Multipart-Dateiname trägt jetzt den vollen Zielpfad, damit er zum Größenfeld passt. |
| Realtime über `plain=` | Halt, Pause und Weiter über `/feedhold_reload`, `/cyclestart_reload`, `/restart_reload`; die lösen das Firmware-Ereignis direkt aus und werden nicht von `BlockDuringMotion` blockiert. |
| Status, `G92`, `G10 L20`, Jog-Abbruch | über den **TCP-Kanal auf Port 23** (`TelnetChannel`). Der ist ab Werk an und ein vollwertiger `Channel`. Kein Fremdpaket nötig — ein Socket aus der Standardbibliothek reicht, weil FluidNC keine Telnet-Optionen verhandelt. |
| `$`-Kommandos | weiterhin über `/command?plain=`, aber nur als Rückfallebene, falls der Kanal nicht aufgeht. Für GCode gibt es **keinen** Rückfall — lieber laut scheitern. |
| MPos/WPos | `parse_status` rechnet über `WCO` in Werkstückkoordinaten um. Das ist das System, in dem der GCode fährt. |
| Anker vs. Bootzeitpunkt | in `location.py`, `calibrate_cli.py`, `config.yaml`, Handbuch und Bauanleitung richtiggestellt: Gondel an den Anschlag, **Board dort neu starten**, dann messen. |
| Web-UI 500 | Aufbau in einer Wurzelfunktion (`ui.run(root=…)`), `nicegui<4.0`, Konsolenskript `wallplotter-web`, und ein Test, der die Seite wirklich über HTTP abholt. |
| 127-Byte-Zeilen | `gcode.comment_lines()` bricht jeden Kommentar um; ein Test prüft **jede** Zeile jedes erzeugten Programms. |
| `M0`-Wechseltext | `M0 (MSG,…)` statt `;`-Kommentar — Klammerkommentare mit MSG protokolliert FluidNC. |
| Parkfahrt | an die untere linke Ecke der Zeichenfläche statt auf Maschinen-(0,0). |
| `resolution_mm` | `Schrittweite / \|sin(Riemenwinkel)\|`, mit einem Test gegen die geschlossene Form. |
| Resonanzprüfung | misst die **Bahnlänge** zwischen Umkehren, nicht den Bahnabstand. |
| Ebenen gleicher Farbe | werden zusammengefasst, statt sich als Wörterbuchschlüssel zu überschreiben. |
| Spirale | Schrittweite an die Wellenlänge gekoppelt, dazu eine Obergrenze für die Punktzahl. |
| Laser-Riegel | greift über **alle** benutzten Köpfe, auch über die Ebenenzuordnung. |
| Selbsttest | prüft erst HTTP, dann den Kanal — und meldet, welcher der beiden fehlt. |
| Testattrappen | kennen nur die Endpunkte, die FluidNC registriert; alles andere ist 404. `/command?plain=` verhält sich wie `settings_execute_line()`. |
| CI | installiert alle Extras und bricht ab, wenn sich ein Test wegen eines fehlenden Pakets überspringt. |
| Kommentar hinter dem Wert (Fund 40) | Der Erzeuger setzt jeden Kommentar in die Zeile darüber; `check_lines()` liest eine beliebige Datei mit den Regeln des Tokenizers und meldet den Fall. |

Was **nicht** behoben ist, weil es Hardware braucht: die Servo-Werte im
Stiftkatalog, die Steckerbelegung am eigenen Board und der komplette
Laserpfad. Die Reihenfolge zum Prüfen steht in der
[Bauanleitung](bauanleitung.md), Abschnitt 10.

---

## 7. Damit es nicht wieder passiert: die Datei wird jetzt erzeugt

Die Fundliste oben hat einen gemeinsamen Nenner, der in keiner Zeile steht:
Fast alle Fehler in der `config.yaml` waren **Abschreibfehler**. Es gab zwei
Beschreibungen derselben Maschine — die Python-Seite und eine YAML-Datei, die
jemand von Hand nachzog — und die liefen auseinander, ohne dass irgendetwas
gemeldet hätte.

`config/fluidnc-wallplotter.yaml` ist deshalb kein gepflegtes Dokument mehr,
sondern ein **Erzeugnis** von `wallplotter-firmware`. Der Aufruf, der genau
diese Datei wiederherstellt, steht in ihrer eigenen Kopfzeile, und ein Test
hält beides zusammen: die ausgelieferte Datei muss byteweise das sein, was der
Erzeuger schreibt.

Was das an den einzelnen Funden ändert:

| Fund | Vorher | Jetzt |
| --- | --- | --- |
| 9 — `laser_mode` setzt das Board in ConfigAlarm | von Hand entfernt | kann nicht wieder hinein: jeder erzeugte Schlüssel wird gegen die Liste aus dem Quelltext gehalten |
| 20 — `speed_map` fährt den Servo in den Anschlag | von Hand auf `0=5.000% 100=10.000%` gesetzt | aus PWM-Frequenz und Impulsfenster gerechnet; ein Test rechnet zurück auf 1,0 bis 2,0 ms |
| Anker vs. Standort | in beiden Dateien gepflegt | `--location <Name>` nimmt die Trilateration aus demselben Standort, mit dem auch die Vorschau rechnet |
| `steps_per_mm` vs. `microsteps` | zwei Zahlen, die zusammenpassen mussten | eine Rechnung: Vollschritte × Mikroschritte ÷ (Zähne × Riementeilung), also 200 × 16 ÷ 40 = 80 |

### Die Schlüsselliste

`wallplotter/fluidnc_schema.py` führt, welche Schlüssel FluidNC in welchem
Abschnitt kennt und auf welchen Bereich es sie klemmt. Sie ist mechanisch aus
dem Quelltext gezogen — nicht aus dem Wiki abgeschrieben:

```console
grep -rn 'handler\.item(\|handler\.section(' FluidNC/src --include=*.cpp --include=*.h
grep -rn 'InstanceBuilder<'                    FluidNC/src --include=*.cpp --include=*.h
```

Stand: `bdring/FluidNC 8a0f8c8` vom 17.08.2026. Jeder Eintrag trägt seine
Fundstelle mit.

Zwei Dinge, die die Liste sauber auseinanderhält, weil die Firmware sie
auseinanderhält:

* **Unbekannter Schlüssel** → `log_config_error("Ignored key …")` →
  `ConfigAlarm`. Das Board fährt nicht. *(Fehler.)*
* **Wert außerhalb des Bereichs** → `constrain_with_message()` in
  `NutsBolts.h:109` klemmt ihn und schreibt eine Warnung. Das Board fährt.
  *(Warnung.)*

Abschnitte, die die Liste nicht führt — Netzwerkmodule, Pin-Extender, UARTs —
meldet sie als **ungeprüft** und nicht als falsch. Eine Tabelle, die
Vollständigkeit behauptet, die sie nicht hat, wäre genau die Sorte Beleg, gegen
die dieses Dokument geschrieben ist.

### Zwei Blickwinkel, weil einer nicht reicht

`check_mapping()` sieht die Datei so, wie ein YAML-Parser sie sieht: als Baum
aus Abschnitten und Schlüsseln. Das findet erfundene Schlüssel.

`check_lines()` sieht sie so, wie **FluidNCs eigener Tokenizer** sie sieht:
Zeile für Zeile, mit dessen Abweichungen von YAML. Das findet Fund 40 — den
Kommentar hinter dem Wert, an dem ein YAML-Parser nichts Auffälliges bemerkt
und das Board trotzdem in ConfigAlarm geht. Nachgebaut sind `nextLine()`,
`parseKey()` und `parseValue()` aus `Configuration/Tokenizer.cpp` samt der
Wertumwandlung aus `Configuration/Parser.cpp`.

Die zweite Prüfung braucht keinen YAML-Parser und läuft deshalb auch dort, wo
PyYAML nicht installiert ist.

`wallplotter-firmware pruefen --host <ip>` holt die Datei vom Board und hält sie
gegen beide. Damit ist die häufigste Frage aus Abschnitt 2.1 — *welcher
Schlüssel war es?* — ohne serielle Konsole zu beantworten.

### Was der Erzeuger zusätzlich prüft

Nicht jeder Fehler ist ein unbekannter Schlüssel. `check()` sagt vor dem
Schreiben auch:

* zwei Verbraucher auf demselben GPIO — FluidNC belegt einen Pin exklusiv, die
  Datei ließe sich nicht einmal parsen;
* ein Ausgang auf `gpio.34` bis `gpio.39` — die können am ESP32 nur lesen;
* eine Laserspindel mit Servotakt oder mit derselben `tool_num` wie der Stift;
* `run_amps` über dem Nennstrom des Motors;
* `idle_ms` unter 255 — die Gondel hängt am Riemen und sackt sonst irgendwann ab;
* ein Impulsfenster, das nicht in die PWM-Periode passt.

