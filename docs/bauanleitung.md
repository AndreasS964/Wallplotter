# Bauanleitung

Vom Karton bis zum ersten Strich an der Wand. Diese Anleitung ist gegen den
FluidNC-Quelltext, BTTs `rodent.yaml` und das Rodent-Handbuch V1.03
geschrieben — nicht gegen Erinnerung. Wo etwas ungeprüft ist, steht es dabei.

*Stand: August 2026 · zugehörige [Gegenprüfung gegen den Firmware-Quelltext](firmware-gegenpruefung.md)*

---

## 0. Vorher lesen

**Was hier steht, ist gegen den Firmware-Quelltext geprüft, nicht gegen
Erinnerung** — die [Gegenprüfung](firmware-gegenpruefung.md) listet, was dabei
herauskam. Fünf Funde hätten das Board blockiert; sie sind behoben, und die
ausgelieferte `config.yaml` ist die korrigierte.

**Was noch aussteht, ist Hardware.** Alles Board-nahe ist gegen den Quelltext
gebaut und gegen eine Gegenstelle getestet, die sich wie FluidNC verhält — aber
an keinem echten Board gelaufen. Was das im Einzelnen heißt, steht in
[Abschnitt 10](#10-was-noch-an-hardware-zu-prüfen-ist).

Die Reihenfolge dieser Anleitung ist nicht beliebig. Sie ist so gebaut, dass
jeder Schritt prüfbar ist, bevor der nächste darauf aufsetzt — und dass ein
Fehler dort auffällt, wo er entsteht, und nicht drei Stunden später an der Wand.

### Sicherheit, in drei Sätzen

Das Netzteil liefert 24–56 V bei bis zu 10 A. Das ist keine Kleinspannung zum
Danebengreifen: **stromlos verdrahten, Aderendhülsen verwenden, vor dem
Einschalten zweimal hinsehen.** Die Gondel hängt an zwei Riemen über einer
Fläche, unter der Menschen stehen — beim ersten Lauf niemand darunter, und ein
Not-Aus-Taster gehört an die Wand, bevor der erste unbeaufsichtigte Plot läuft
(siehe [7.5](#75-not-halt-und-pause-als-taster)).

---

## 1. Stückliste

### Elektronik

| Teil | Menge | Anmerkung |
| --- | --- | --- |
| BIGTREETECH Rodent CNC V1.0 oder V1.1 | 1 | ESP32, 4× TMC2160, FluidNC ab Werk |
| NEMA17 17HS4412P1-3 (1,2 A, 0,45 Nm, 40 mm) | 2 + 1 Ersatz | mehr Strom heißt nicht ruhiger |
| Netzteil 24 V | 1 | ≥ 4 A: 2 × 1,2 A Motoren plus Reserve |
| Netzteil 5 V | 1 | **getrennt**, für den Servo — Begründung in [7.4](#74-servo-anschließen) |
| Servo MG90S (Metallgetriebe) | 1 | Pen-Lift |
| µSD-Karte | 1 | FAT32, klein reicht |
| Aderendhülsen, Motorverlängerungen, Litze | | 4-adrig für die Motoren |
| Dupont-Buchse 3-polig, Raster 2,54 mm | 1 | für den Servo an CN51 |
| Taster, Schließer | 2–3 | Not-Halt, Pause, Weiter |

Zum Boardtyp: BTT liefert **eine** `rodent.yaml`, und die trägt
`board: BTT Rodent V1.0` mit `r_sense_ohms: 0.022`. Im Pinbild der V1.1 im
Handbuch steht dagegen „RSENSE 75MR". Wer eine V1.1 hat, prüft diesen Wert am
eigenen Board nach, bevor die Motoren zum ersten Mal Strom sehen — ein falscher
`r_sense` skaliert den Motorstrom direkt.

### Mechanik

| Teil | Menge | Anmerkung |
| --- | --- | --- |
| GT2-Zahnriemen 6 mm, offen, **Stahlkern** | ~8 m | siehe [2.1](#21-der-riemen-ist-die-einzige-entscheidung-die-vor-dem-kauf-fällt) |
| GT2-Pulley 20 Zähne, Bohrung 5 mm | 2 | |
| Rillenkugellager 6706 (30×37×4 mm) | 2 | für die Gondel |
| Büroklammer | 1 | Federelement der Gondel |
| PETG-Filament | ~400 g | **nicht PLA** |
| Schrauben/Dübel für die Motorhalter | | nach Wandbeschaffenheit |
| Gegengewicht für die Gondel | ~300 g | Muttern, Bleiband, was da ist |

### Druckteile

* **Gondel / Pen-Holder:** „Makelangelo plotter head July 2026" von
  i-make-robots — [Thingiverse 7388981](https://www.thingiverse.com/thing:7388981),
  [Montageanleitung](https://mcr.dozuki.com/Guide/How+to+assemble+pen+holder+2026/52?lang=en)
* **Motorhalter mit Federspannung:** „Polargraph / Vertical Plotter Spring
  Tensioned Motor Mount" von flickeringsight —
  [Thingiverse 3440067](https://www.thingiverse.com/thing:3440067)

---

## 2. Was vor dem Kauf entschieden sein muss

### 2.1 Der Riemen ist die einzige Entscheidung, die vor dem Kauf fällt

Alles andere lässt sich später ändern. Der Riemen nicht — er wird abgelängt und
verbaut.

Nachgerechnet (`wallplotter-kinematics`, ausführlich in
[kinematik.md](kinematik.md)) ist die Rangfolge der Fehlerquellen eindeutig und
gegen die Intuition:

| Quelle | Größenordnung |
| --- | --- |
| Motorauflösung | 0,013 mm |
| Segmentierung bei `segment_length: 1` | 0,0003 mm |
| **Riemendehnung** | **0,12 – 0,83 mm** |

Die Elektronik ist um Faktor 40 besser als nötig. Über 3,4 m Länge ist ein
GT2-Riemen eine Feder, und die Zugkraft schwankt über die Fläche zwischen 2,6
und 11 N.

| Variante | Dehnung | Längsresonanz |
| --- | --- | --- |
| GT2 6 mm Glasfaser | 0,83 mm | 22 Hz |
| **GT2 6 mm Stahlkern** | **0,17 mm** | **50 Hz** |
| HTD 5M 9 mm | 0,06 mm | 80 Hz |

**Stahlkern reicht, und steifer lohnt nicht** — der Rest liegt längst unter der
Strichbreite eines Filzstifts. Zwei weitere Gründe: Glasfaser kriecht unter
Dauerlast, was bei mehrstündigen Plots driftet. Und Riemen plus Gondel bilden
einen Längsschwinger — Glasfaser landet bei 22 Hz, und 1500 mm/min bei 1 mm
Segmentlänge regen mit 25 Hz genau dort an. Stahlkern schiebt das auf 50 Hz.

### 2.2 Wie hoch die Anker sitzen, entscheidet mehr als wie weit sie auseinander sind

Ebenfalls gerechnet, für 2000 × 2500 mm Fläche:

| Überstand seitlich | Höhe über der Fläche | schlechteste Auflösung | max. Zug |
| --- | --- | --- | --- |
| 0 mm | 100 mm | 63 µm | 14,8 N |
| 150 mm | 100 mm | 72 µm | 17,0 N |
| 150 mm | 250 mm | 30 µm | 6,9 N |
| **150 mm** | **400 mm** | **23 µm** | **4,5 N** |
| 300 mm | 400 mm | 22 µm | 5,0 N |

Höhe wirkt, seitlicher Überstand kaum. Von 100 auf 400 mm über der Oberkante
fällt die schlechteste Auflösung von 72 auf 23 µm und der Zug von 17 auf
4,5 N — **hänge die Motoren so hoch, wie die Decke es zulässt.**

Der Grund steckt in der Geometrie: direkt unter den Ankern laufen die beiden
Riemen fast in eine Linie (bis 165° bei 150 mm Höhe). Dort ist die Höhe der
Gondel schlecht bestimmt, und beide Riemen ziehen gegeneinander statt zu
tragen. Höhere Anker verschieben diese Zone nach oben aus der Fläche heraus.

Preis der Höhe: Bei 2,5 m Wandhöhe und 2,5 m Decke muss die Zeichenfläche
entsprechend tiefer beginnen. Das ist der Handel, und er lohnt sich.

---

## 3. Drucken

PETG, nicht PLA — PLA bricht an den Gondelarmen, und zwar irgendwann mitten im
Plot.

| Einstellung | Wert | Grund |
| --- | --- | --- |
| Material | PETG | zäh statt spröde |
| Schichthöhe | 0,2 mm | |
| Wandlinien | 4 | die Arme tragen |
| Füllung | 40 % | |
| Stützen | nur wo das Modell es verlangt | |

Vor dem Druck der Motorhalter: Die Vorlage gibt es in zwei Wellenhöhen (20 und
22 mm). **Miss an deinem Motor nach**, wie weit die Welle aus dem Flansch
steht, und nimm die passende Variante. Ein 2-mm-Fehler bedeutet, dass Pulley
und Riemen nicht fluchten und der Riemen am Flansch schleift.

---

## 4. Mechanik montieren

### 4.1 Motoren und Halter

1. Pulley auf die Welle, Madenschraube auf die **Abflachung** der Welle — nicht
   daneben, sonst rutscht sie unter Last durch.
2. Pulley so weit aufschieben, dass der Riemen mittig auf der Umlenkung läuft.
3. Motor in den Halter, Federspannung noch nicht einhängen.

Die federgespannte Halterung ist Absicht: Sie gleicht die Riemendehnung über
Stunden selbsttätig aus. Bei einem schraubfixierten Halter musst du nach jeder
Stunde nachspannen, und zwischen zwei Farbebenen ändert sich damit die
Geometrie.

### 4.2 Gondel

Nach der [Dozuki-Anleitung](https://mcr.dozuki.com/Guide/How+to+assemble+pen+holder+2026/52?lang=en)
zusammenbauen. Zwei Punkte, die dort nicht stehen:

* **Gewicht.** Die Gondel arbeitet selbstgewichtet, ohne fallendes
  Gegengewicht. Sie muss schwer genug sein, dass die Riemen unter allen
  Positionen straff bleiben — 300 g sind der Wert, mit dem gerechnet wurde. Zu
  leicht heißt schlaffe Riemen in der oberen Mitte, zu schwer heißt mehr Zug in
  den Ecken.
* **Pendellänge.** Der Abstand zwischen Riemenaufhängung und Stiftspitze
  bestimmt die Pendelfrequenz (1,3–2 Hz). Kurz ist besser: `wallplotter.motion`
  rechnet damit, ob eine Schraffur die Gondel zum Schaukeln bringt.

### 4.3 Servo und Stift

Servohebel so einstellen, dass er in der Mittelstellung etwa mittig im Hub
steht — dann bleibt nach oben und unten Weg. Die genauen S-Werte kommen später
aus dem Test ([9.3](#93-servoweg-einstellen)); jetzt geht es nur darum, dass
der Hebel weder oben noch unten mechanisch anschlägt. Ein Servo, der gegen
einen Anschlag drückt, zieht Dauerstrom und wird heiß.

---

## 5. An die Wand

1. Zeichenfläche anzeichnen. Die Oberkante liegt so tief, dass über ihr noch
   Platz für die Anker bleibt (siehe [2.2](#22-wie-hoch-die-anker-sitzen-entscheidet-mehr-als-wie-weit-sie-auseinander-sind)).
2. Motorhalter setzen: seitlich etwa 150 mm neben der Fläche, so hoch wie
   möglich über der Oberkante. **Beide gleich hoch** — die Firmware rechnet mit
   gleicher Ankerhöhe (in `lengths_to_xy()` geht nur die X-Differenz der Anker
   ein). Ungleiche Höhen verzerren die Rückrechnung, ohne dass es jemand meldet.
   Mit der Wasserwaage arbeiten, nicht nach Augenmaß.
3. Riemen ablängen: Der längste Riemen misst bei 2 × 2,5 m rund 3,4 m pro Seite,
   mit Umlenkung und Reserve **gut 8 m gesamt**. Lieber großzügig — kürzen geht,
   verlängern nicht.
4. Riemen an der Gondel befestigen, über die Pulleys führen, Federspannung
   einhängen.
5. Von Hand prüfen: Gondel in alle vier Ecken schieben. Nirgends darf ein
   Riemen am Flansch schleifen, sich verdrehen oder von der Pulley springen.

---

## 6. Board vorbereiten

**Vor jedem Griff ans Board: Netzteil aus, und zwar wirklich aus.**

Steckbrücken am Rodent, in dieser Reihenfolge geprüft:

| Brücke | Wo | Was |
| --- | --- | --- |
| `SW_VCC` (Endstops) | am Endstop-Block | 5 V, 12 V oder VCC — **immer nur eine** |
| VProbe-Spannung | am VProbe-Block | dito |
| `VUSB` | neben der USB-Buchse | **nur** zum Flashen ohne externe Versorgung |

Das Handbuch ist an zwei Stellen ausdrücklich: Es darf nie mehr als eine
Spannungsbrücke gleichzeitig stecken, und **die VCC-Brücke ist verboten, sobald
die Eingangsspannung über 24 V liegt**. Bei 24 V Netzteil und Tastern gegen
Masse ist 5 V die richtige Wahl.

---

## 7. Verkabeln

### 7.1 Was wo liegt

Aus BTTs `rodent.yaml` und dem Pinbild des Handbuchs — echte Werte, nichts
geraten:

| Funktion | Anschluss | Signal |
| --- | --- | --- |
| Motor links | Klemme **X** (A2/A1/B2/B1) | Step I2SO.2, Dir I2SO.1, En I2SO.0 |
| Motor rechts | Klemme **Y** | Step I2SO.5, Dir I2SO.4, En I2SO.7 |
| Treiber-SPI | — | CS gpio.5, SCK gpio.18, SDI gpio.19, SDO gpio.23 |
| Endstop X-MAX | 3-polig | gpio.35 |
| Endstop Y-MAX | 3-polig | gpio.34 |
| Endstop Z-MAX | 3-polig | gpio.33 |
| Endstop E0-MAX | 3-polig | gpio.32 |
| Endstop E1-MAX | 3-polig | gpio.37 (V1.0) / gpio.39 (V1.1) |
| V-Probe | 2-polig | gpio.36 |
| **Sp-Enable (CN51)** | 3-polig: VCC / GND / Signal | **gpio.25** ← hier kommt der Servo hin |
| Sp-Direction (CN52) | 3-polig | gpio.15 |
| Sp-Feedback (CN53) | 3-polig | gpio.14 |
| SP-PWM | 2-polig + Trimmpoti | analog 3–10 V, **kein Logikpegel** |
| SD-Karte | — | CS gpio.0, CLK gpio.18, D0 gpio.19, CMD gpio.23 |
| OLED (I2C) | 4-polig | SDA gpio.27, SCL gpio.26 |
| RS485 | Klemme | über gpio.15 / gpio.16 / gpio.14 |

Zwei Dinge, die man daraus sofort sieht: **Sp-Direction und Sp-Feedback teilen
sich die Leitungen mit dem RS485-Treiber** — wer den einen benutzt, kann den
anderen nicht. Und **der `SP-PWM`-Stecker ist ein analoger Drehzahlausgang mit
Poti**, nicht der 3,3-V-PWM-Ausgang, den man für einen Servo bräuchte.

### 7.2 Motoren

Die vier Adern eines NEMA17 gehören paarweise zusammen: A2/A1 ist die eine
Spule, B2/B1 die andere. Welches Paar welches ist, findet man mit dem
Durchgangsprüfer — zwei Adern mit Durchgang bilden ein Paar.

Die Drehrichtung ist an dieser Stelle noch egal. Sie wird in
[9.2](#92-drehrichtung-prüfen) geprüft und notfalls durch Tauschen *eines* Paares
umgekehrt.

### 7.3 Warum kein Endschalter nötig ist

Die WallPlotter-Kinematik von FluidNC kann grundsätzlich nicht referenzieren:

```cpp
bool WallPlotter::canHome(AxisMask axisMask) {
    log_error("This kinematic system cannot home");
    return false;
}
```

`$H` endet also in einer Fehlermeldung, mit Endschalter wie ohne, und auch mit
sensorlosem StallGuard. Referenziert wird stattdessen mechanisch: Gondel an
einen definierten Anschlag fahren und das Board dort neu starten
([9.4](#94-nullpunkt-setzen)).

Das ist keine Einschränkung, sondern eine Chance: **alle fünf optogekoppelten
Endstop-Eingänge sind damit frei** — und genau die braucht der Not-Halt.

### 7.4 Servo anschließen

Der Servo kommt an **CN51 (Sp-Enable)**, den 3-poligen Stecker neben dem
Spindelblock. Der Schaltplan im Handbuch (S. 12) zeigt für alle drei Sp-Stecker
denselben Aufbau:

```
CN51 Pin 1 ──[ R25  100 Ω ]── +5 V
CN51 Pin 2 ─────────────────── GND
CN51 Pin 3 ──[ R24  100 Ω ]── gpio.25,  ESD-Klemmdiode 3,3 V gegen GND
```

Daraus folgen drei Dinge, und das mittlere ist das wichtige:

* **Signal an Pin 3.** 3,3 V über 100 Ω — für den hochohmigen Signaleingang
  eines Servos unkritisch. Ein MG90S nimmt 3,3-V-Logik in aller Regel an; falls
  er zuckt statt sauber zu stellen, hilft ein Pegelwandler auf 5 V.
* **Der Servo darf seinen Strom NICHT aus Pin 1 ziehen.** Da liegen ebenfalls
  100 Ω in Reihe. Ein MG90S zieht im Anlauf mehrere hundert Milliampere — an
  100 Ω bricht die Spannung vollständig zusammen. Der Servo braucht ein eigenes
  5-V-Netzteil.
* **Masse zusammenlegen.** Servo-Netzteil und Board müssen dieselbe Masse
  sehen, sonst hat das Signal keinen Bezug. Pin 2 von CN51 ist der richtige
  Punkt dafür.

```
   5-V-Netzteil ──── rot   ─────────────── Servo +
                └─── schwarz ──┬────────── Servo −
                               │
   Rodent CN51 Pin 2 (GND) ────┘
   Rodent CN51 Pin 3 (gpio.25) ─── orange ─ Servo Signal
```

Der `SP-PWM`-Stecker bleibt frei. Er liefert 3–10 V analog über ein Trimmpoti
und ist für einen VFD gedacht, nicht für einen RC-Servo.

### 7.5 Not-Halt und Pause als Taster

**Das ist kein optionaler Komfort.** Über das Netzwerk hält die Maschine
inzwischen an — Pause, Weiter und Reset laufen über die Ereignis-Endpunkte der
Firmware —, aber ein Not-Halt, der ein funktionierendes WLAN, einen wachen
Browser und drei Schichten Software braucht, ist keiner. Ein Taster an einem
optogekoppelten Eingang ist einer.

Die Eingänge sind ohnehin frei: Die WallPlotter-Kinematik kann nicht
referenzieren, also wird kein einziger der fünf Endstop-Eingänge gebraucht.
Drei Taster gegen Masse:

| Taster | Eingang | Wirkung |
| --- | --- | --- |
| Not-Halt | gpio.35 (X-MAX) | `reset_pin` — Soft-Reset, bricht den Lauf ab |
| Pause | gpio.34 (Y-MAX) | `feed_hold_pin` — bremst kontrolliert ab |
| Weiter | gpio.33 (Z-MAX) | `cycle_start_pin` — setzt fort |

Der Weiter-Taster ist außerdem der bequemste Weg, die `M0`-Pause beim
Stiftwechsel aufzulösen — man steht ohnehin an der Wand und hat die Hände am
Stift, nicht am Handy.

Die Eingänge sind optogekoppelt; Spannungswahl über die `SW_VCC`-Brücke
(5 V bei einem 24-V-Netzteil). Die Taster schließen gegen GND, in der
`config.yaml` steht deshalb `:low` hinter dem Pin. FluidNC verlangt, dass alle
Control-Eingänge beim Start **inaktiv** lesen — ein klemmender Taster löst
sonst gleich beim Einschalten Alarm aus. Das ist so gewollt und ein guter
Selbsttest.

---

## 8. Firmware

### 8.1 Aufspielen

Das Rodent kommt in aller Regel mit FluidNC ab Werk. Wenn nicht: Die Binaries
liegen unter `Firmware/` in [BTTs Repo](https://github.com/bigtreetech/Rodent),
das Board geht per BOOT+RESET in den DFU-Modus, geflasht wird über USB-C
(CH340 ist an Bord). Der `VUSB`-Jumper steckt dabei nur, wenn keine externe
Versorgung anliegt — und wird danach wieder gezogen.

### 8.2 WLAN

Nach dem ersten Start macht FluidNC einen eigenen Hotspot auf. Damit verbinden,
Weboberfläche öffnen, im Terminal:

```
$Sta/SSID=DeinNetz
$Sta/Password=DeinPasswort
$WiFi/Mode=STA
$Hostname=wandplotter
$Bye
```

Das gehört **nicht** in die `config.yaml` — sonst landet das WLAN-Passwort im
Git-Repo.

Gleich mit erledigen, es erspart später Rätselraten:

```
$HTTP/BlockDuringMotion=OFF
$Telnet/Enable=ON
```

Zum ersten: Solange `BlockDuringMotion` an ist (Werkszustand), antwortet der
Kommando-Endpunkt während jeder Bewegung nur mit HTTP 503.

Zum zweiten: Über den **TCP-Kanal auf Port 23** laufen Statusabfrage, GCode und
die Realtime-Zeichen — er ist ab Werk an, und ohne ihn geht von der Software
nur noch der Datei-Upload. Prüfen lässt sich das ohne Werkzeug:

```bash
printf '?' | nc <ip> 23        # muss eine Zeile wie <Idle|MPos:…> liefern
```

Warum nicht über HTTP? Weil `/command?plain=` die Zeile an
`settings_execute_line()` weiterreicht, und das versteht nur `$name=wert`. Ein
`?` oder ein Realtime-Byte landet dort beim namenlosen Hilfe-Kommando und wird
mit HTTP 200 quittiert, ohne dass irgendetwas passiert — der Not-Halt meldete
so lange Zeit Erfolg. Belege in der
[Gegenprüfung](firmware-gegenpruefung.md), Abschnitt 2.3.

### 8.3 Konfiguration hochladen

Die Datei wird **erzeugt, nicht getippt**:

```bash
wallplotter-firmware pruefen config/fluidnc-wallplotter.yaml   # ohne Board
wallplotter-firmware push --host <ip>                          # in den Flash, dann Neustart
```

`push` schreibt in den Flash und startet das Board neu; die bisherige Fassung
lädt es vorher herunter und legt sie als `config.yaml.bak` daneben. Von Hand
geht es auch: über das FluidNC-WebUI (Dateisymbol → Upload) — aber auf den
**Flash**, nicht auf die SD-Karte. Dort liest FluidNC die Konfiguration nie,
und der Upload sähe trotzdem erfolgreich aus.

**Die Ankerkoordinaten kommen aus dem Standort, nicht aus dem Editor.** Die vier
Zeilen im `kinematics`-Block der ausgelieferten Datei sind Beispielwerte für eine
symmetrische Aufhängung; die echten fallen in [Abschnitt 9.5](#95-einmessen) an
und wandern über `--location <Name>` hinein. Ohne sie fährt die Maschine zwar,
aber in einem falschen Koordinatensystem.

Was sonst noch in der Datei steht, ändert man ebenfalls über das Werkzeug und
nicht im Editor: `wallplotter-firmware config --help` zählt die Stellschrauben
auf — Mikroschritte, Motorstrom, Höchstgeschwindigkeit, Servofenster, Laser,
Taster. Der Aufruf, der genau die vorliegende Datei wiederherstellt, steht in
ihrer eigenen Kopfzeile.

> **Falle, wenn doch von Hand:** Hinter einen Wert gehört **kein** Kommentar.
> FluidNC schneidet ihn nicht ab — bei einer Zahl scheitert das Einlesen und
> das Board geht in ConfigAlarm, bei einem `true`/`false` wird stillschweigend
> `false` daraus. Kommentare immer in die Zeile darüber. Einzelheiten in der
> [Gegenprüfung](firmware-gegenpruefung.md), Abschnitt 2.6.

Beim Start ins Log sehen. Zwei Zeilen entscheiden:

* `Kinematic system: WallPlotter` — die Kinematik ist aktiv.
* Kein `[MSG:ERR: Ignored key …]` — **ein einziger unbekannter Schlüssel setzt
  FluidNC in ConfigAlarm**, und dann fährt gar nichts. Wer die Datei anpasst,
  liest danach das Log.

---

## 9. Erstinbetriebnahme

Ab hier wird eingeschaltet. Jeder Schritt hat ein Ergebnis, das man sehen kann;
weitergehen erst danach.

### 9.1 Motoren ohne Mechanik

Motoren vom Riemen lösen (oder das Board vor der Wandmontage auf den Tisch
legen — besser). Im WebUI-Terminal:

```
$J=G91 G21 X10 F500
```

**Erwartet:** Der linke Motor dreht ein kleines Stück, hörbar leise
(StealthChop). **Wenn nicht:** Verkabelung, `run_amps`, SPI-Verbindung. Ein
Motor, der brummt und nicht dreht, hat eine vertauschte Ader innerhalb eines
Spulenpaares.

Motoren dürfen handwarm werden, nicht heiß. Bei 1,2 A und diesen Motoren ist
das der Normalfall; wird einer heiß, stimmt `r_sense_ohms` nicht zum Board
(siehe [Stückliste](#elektronik)).

### 9.2 Drehrichtung prüfen

Jetzt mit eingehängten Riemen, Gondel hängend:

```
$J=G91 G21 X0 Y50 F500
```

**Erwartet:** Die Gondel fährt nach **oben**. Die Y-Achse zeigt in der
WallPlotter-Kinematik nach oben, zum Anker hin.

```
$J=G91 G21 X50 Y0 F500
```

**Erwartet:** Die Gondel fährt nach **rechts**.

Stimmt eine Richtung nicht, wird sie nicht in der Software korrigiert, sondern
am Motor: ein Spulenpaar tauschen. Der Grund steht in der Firmware selbst —
`cartesian_to_motors()` invertiert den linken Motor bereits fest
(`cables[0] = 0 - (…)`), eine zweite Umkehr in der Konfiguration macht das
Vorzeichenbild nur unübersichtlich.

### 9.3 Servoweg einstellen

```
M3 S0     ; unten
M3 S100   ; oben
M5        ; PWM aus
```

Mit der korrigierten `speed_map` (`0=5.000% 100=10.000%` bei 50 Hz) entspricht
S0 einem Impuls von 1,0 ms und S100 einem von 2,0 ms — also dem vollen
RC-Servofenster. **S ist damit direkt Prozent des Servowegs.**

Suche die beiden Werte, bei denen der Stift sauber aufliegt und sauber frei
steht, und trage sie in `toolhead.PENS` ein. Die Katalogwerte (26 bis 40) sind
Schätzungen, keine Messwerte — Servohebel, Federweg und Halter sind an jedem
Aufbau anders.

Zwei Fehlerbilder und was sie heißen:

* **Servo schlägt an oder brummt dauerhaft** → mechanischer Anschlag, Hebel
  anders setzen. Nicht per S-Wert wegdrehen; der Servo zieht dann trotzdem.
* **Erste Millimeter jedes Strichs fehlen** → `dwell_s` zu kurz, der Servo ist
  noch unterwegs, wenn die Bewegung anfängt.

### 9.4 Nullpunkt setzen

Hier liegt die Stelle, an der der ganze Aufbau kippt, wenn man sie falsch
macht.

FluidNC friert die Riemenlängen für den Punkt „kartesisch (0,0)" **beim
Booten** ein:

```cpp
void WallPlotter::init() {
    // We assume the machine starts at cartesian (0, 0, 0).
    xy_to_lengths(0, 0, zero_left, zero_right);
```

Der Maschinennullpunkt ist also exakt die Stelle, an der die Gondel beim
Einschalten hing. Ein späteres `G92` verschiebt nur das
Werkstück-Koordinatensystem und lässt die Kinematik unberührt.

**Daraus folgt der Ablauf:**

1. Gondel von Hand an den mechanischen Anschlag / Referenzpunkt bringen.
2. **Board neu starten** (`$Bye` oder Netzteil aus/an).
3. Erst jetzt messen und kalibrieren.

Wer stattdessen einschaltet, hinjoggt und dann `G92` setzt, bekommt
Ankerkoordinaten, die um genau diese Jog-Strecke daneben liegen — und eine
Zeichnung, die verzerrt ist, ohne dass irgendwo eine Fehlermeldung erscheint.

Der Referenzpunkt sollte reproduzierbar sein: ein Anschlag oben am Motorhalter,
eine angezeichnete Ecke, irgendetwas, das man in vier Wochen wiederfindet. Für
mehrfarbige Plots über mehrere Tage ist das die Bedingung, unter der die zweite
Farbe auf die erste passt.

### 9.5 Einmessen

Drei Maße mit dem Zollstock, Gondel am Referenzpunkt, Board dort gestartet:

1. Abstand der beiden Umlenkpunkte zueinander
2. Länge des linken Riemens vom Umlenkpunkt bis zur Gondel
3. dasselbe rechts

```bash
wallplotter-location new Keller --span 2300 --left 1450 --right 1470
wallplotter-firmware push --host <ip> --location Keller
```

Der zweite Aufruf erzeugt die `config.yaml` mit genau diesen Maßen, schreibt sie
in den Flash und startet das Board neu. Wer sie erst ansehen will:
`wallplotter-firmware config --location Keller` gibt sie auf der Konsole aus.
Aus den drei Maßen fallen die Ankerkoordinaten per Trilateration heraus — unter
der Annahme gleicher Ankerhöhen, was zu dem passt, was die Firmware rechnet.

Danach die Fläche abstecken: Gondel in jede Ecke joggen und die Position
festhalten. Vier Ecken sind ideal — dann warnt das Werkzeug auch bei schiefer
Aufhängung —, zwei diagonale reichen. Das Ergebnis ist bewusst das größte
Rechteck *innerhalb* der angefahrenen Punkte: lieber etwas kleiner als neben
der Wand.

```bash
wallplotter-location show     # Auflösung, Riemenkräfte, Urteil zur Geometrie
```

Wichtig für diesen Schritt: **kein `G92`- und kein `G54`-Versatz gesetzt**,
solange die Ecken aufgenommen werden. Die Ecken werden als Maschinenkoordinaten
festgehalten, der erzeugte GCode läuft aber in Werkstückkoordinaten — sind
beide nicht identisch, liegt die Zeichnung um genau den Versatz daneben.

### 9.6 Der erste Plot

In dieser Reihenfolge, jedes Muster prüft genau eine Sache:

| Schritt | Muster | Was du siehst |
| --- | --- | --- |
| 1 | `frame` | Rahmen parallel zu den Wandkanten? Diagonalen kreuzen sich mittig? Kommt der Stift in alle vier Ecken? |
| 2 | `pen-test` | Fehlende Strichanfänge → `dwell_s` erhöhen. Ausgefranste Enden → `down_value` zurück. |
| 3 | `feed-ramp` | Die erste wellige Linie zählt: darüber springen die Riemen. |
| 4 | `grid` | Mit dem Zollstock nachmessen. Weicht der Maßstab ab, stimmen `steps_per_mm` oder die Ankermaße nicht. |
| 5 | `circles` | Aus Kreisen werden Eier, wo die Kinematik schwächelt — meist oben in der Mitte. |

Erst wenn `frame` sitzt, lohnt sich alles Weitere. Ein schiefer Rahmen heißt:
zurück zu [9.5](#95-einmessen), nicht weiterprobieren.

### 9.7 Nachmessen und gegenrechnen

Was der Riemen an Dehnung übrig lässt, rechnet die Software gegen:

```bash
wallplotter-correct raster --steps 4 -o raster.gcode   # 16 Kreuze plotten
wallplotter-correct messen --steps 4                   # Vorlage zum Eintragen
# Ist-Werte mit dem Zollstock nachtragen, dann:
wallplotter-correct anpassen
plot bild.svg --correction korrektur.json
```

Eine Warnung dazu, die das Werkzeug selbst noch nicht ausspricht: Der gemeldete
Restfehler wird an genau den Punkten gemessen, aus denen die Korrektur gerechnet
wurde. Bei wenigen Messpunkten ist er zwangsläufig klein, ohne dass die
Korrektur dazwischen etwas taugt. Traue der Zahl erst, wenn du an einer *nicht*
gemessenen Stelle nachgemessen hast.

---

## 10. Was noch an Hardware zu prüfen ist

Die Software spricht das Board jetzt auf den Wegen an, die FluidNC wirklich
anbietet — Dateien über HTTP, Kommandos über den TCP-Kanal, Halt und Pause über
die Ereignis-Endpunkte. Getestet ist das gegen eine Gegenstelle, die sich wie
die Firmware verhält. **An einem echten Board ist es noch nicht gelaufen.**

Diese Liste ist die Reihenfolge, in der du das prüfst, sobald das Board hängt:

| Prüfen | Erwartet | Wenn nicht |
| --- | --- | --- |
| `wallplotter-doctor --host <ip>` | „Board erreichbar", „SD-Karte", „Kommandokanal" alle grün | Der Selbsttest sagt, welcher der drei fehlt |
| Not-Halt-Taster drücken (Motor läuft) | Maschine hält sofort, Zustand `Alarm` | `control:`-Block und Steckbrücken prüfen, [7.5](#75-not-halt-und-pause-als-taster) |
| `wallplotter-calibrate --host <ip> status` | eine Statuszeile mit Position | Telnet aus? `$Telnet/Enable=ON`, dann `$Bye` |
| `plot --pattern frame --upload --run` | Datei liegt auf der Karte, Plot läuft an | Antwort des Boards lesen; `wallplotter-doctor` |
| Pause/Weiter in der Web-UI | Zustand wechselt auf `Hold` und zurück auf `Run` | die Oberfläche meldet jetzt, wenn das Board **nicht** umschaltet |
| `--layers --one-file`, `M0`-Pause | Maschine hält, Wechseltext steht in der WebUI-Konsole | mit Cycle Start (Taster oder Knopf) weiter |

Die Servo-Werte im Stiftkatalog und der komplette Laserpfad bleiben ungeprüft —
beides hängt an Hardware, die noch nicht da ist, und beides steht als solches
gekennzeichnet in [Abschnitt 9.3](#93-servoweg-einstellen) bzw. im
[Projekthandbuch](wandplotter-handbuch.md).

---

## 11. Fehlersuche

| Symptom | Wahrscheinliche Ursache |
| --- | --- |
| Board startet, meldet aber `ConfigAlarm` | Unbekannter Schlüssel in der `config.yaml`. Das Log nennt ihn: `[MSG:ERR: Ignored key …]`. Ohne Log: `wallplotter-firmware pruefen --host <ip>` holt die Datei vom Board und sagt, welcher Schlüssel es ist. |
| `$H` meldet „This kinematic system cannot home" | Richtig so. Die WallPlotter-Kinematik referenziert nicht — siehe [7.3](#73-warum-kein-endschalter-nötig-ist). |
| Motor brummt, dreht nicht | Ader innerhalb eines Spulenpaares vertauscht. |
| Motor wird heiß | `run_amps` zu hoch, oder `r_sense_ohms` passt nicht zur Boardrevision. |
| Gondel sackt beim Stillstand ab | `idle_ms` steht nicht auf 255. Nur dieser Wert heißt „Motoren bestromt lassen". |
| Zeichnung ist verzerrt, Rahmen schief | Ankermaße stimmen nicht — meist wurde nach dem Booten gejoggt, bevor gemessen wurde ([9.4](#94-nullpunkt-setzen)). |
| Zeichnung sitzt komplett versetzt | `G92`- oder `G54`-Versatz zwischen Kalibrierung und Plot. |
| Servo zuckt oder flattert im Betrieb | Der Stift hängt an einer `Laser:`-Spindel statt an `pwm:` — die skaliert S mit dem Vorschub. |
| Servo geht in den Anschlag | `speed_map` bildet nicht auf das Servofenster ab (`0=5.000% 100=10.000%` bei 50 Hz). |
| Erste Millimeter jedes Strichs fehlen | `dwell_s` zu kurz. |
| Linien werden wellig | Pendelresonanz. `wallplotter.motion` nennt zwei Auswege: Vorschub deutlich darüber oder darunter, oder Bahnabstand ändern. |
| Riemen springt bei Leerfahrten | `--travel-as-g1` setzen, dann laufen Leerwege mit `travel_feed` statt Eilgang. |
| Lauf bricht mitten im Bild ab, `error:14` im Log | Zeile länger als 127 Byte — betrifft heute nur Laserprogramme mit langen Warnkommentaren. |
| Upload endet mit HTTP 404 | Falsche Firmware-Version oder ein Proxy dazwischen — die SD-Karte hängt an `POST /upload`, nicht an `/sdfiles`. |
| „Kein TCP-Kanal zu …:23" | Telnet ist aus: `$Telnet/Enable=ON`, dann `$Bye`. Oder ein anderer Port: `$Telnet/Port`. |
| Statusabfrage läuft in eine Zeitüberschreitung | Der Kanal steht, das Board antwortet nicht — meist ein zweiter Client, der ihn belegt. |
| Web-UI antwortet mit HTTP 500 | NiceGUI-Hauptversion neuer als getestet. `pip install -e ".[web]"` hält sie unter 4.0. |

---

## 12. Quellen

* FluidNC: [Repo](https://github.com/bdring/FluidNC) · [Wiki](http://wiki.fluidnc.com/) —
  maßgeblich war der Quelltext unter `FluidNC/src/`, nicht das Wiki
* Rodent: [BTT-Repo](https://github.com/bigtreetech/Rodent) ·
  [rodent.yaml](https://github.com/bigtreetech/Rodent/blob/master/rodent.yaml) ·
  User Manual V1.03 (Pinbild S. 6/7, Spindel-Schaltplan S. 12) ·
  [BTT-Wiki](https://global.bttwiki.com/Rodent.html)
* Gondel: [Thingiverse 7388981](https://www.thingiverse.com/thing:7388981) ·
  [Montageanleitung](https://mcr.dozuki.com/Guide/How+to+assemble+pen+holder+2026/52?lang=en)
* Motorhalter: [Thingiverse 3440067](https://www.thingiverse.com/thing:3440067)
* Gerechnete Zahlen: [kinematik.md](kinematik.md), erzeugt mit
  `python -m wallplotter.kinematics`
