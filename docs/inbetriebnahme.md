# Inbetriebnahme

Der Ablauf für den Tag, an dem das Board da ist — und die Liste dessen, was
sich in der Woche davor schon erledigen lässt.

Der Gedanke dahinter: Am Tag der ersten Inbetriebnahme will man an der Wand
stehen und Werte messen, nicht Software debuggen. Alles, was ohne Hardware
entschieden werden kann, ist deshalb vorher entschieden — und alles, was
Hardware braucht, steht hier mit dem Kriterium daneben, an dem man erkennt,
dass es sitzt.

*Stand: August 2026*

---

## Teil 1 — Vorher, ohne Board

### Schon erledigt

| | Womit nachprüfbar |
| --- | --- |
| Bild-zu-GCode-Kette, Geometrie, Bildverfahren, Testmuster | `pytest`, `wallplotter-doctor` |
| Kinematik für die Beispielaufhängung durchgerechnet | `wallplotter-kinematics`, [`kinematik.md`](kinematik.md) |
| Laufzeitschätzung mit Bewegungsprofil | `plot --pattern grid --out /tmp/x.gcode` (Kopfzeile) |
| Web-API gegen den Firmware-Quelltext geprüft, nicht gegen die ESP3D-Doku | [Projekthandbuch §6](wandplotter-handbuch.md#web-api-des-boards) |
| Die ganze Kette einmal ohne Board durchgefahren | `wallplotter-sim`, siehe unten |

### Trockenlauf gegen den Simulator

Das Wichtigste, was ohne Board geht: den kompletten Ablauf einmal fahren, den
man später vor der Wand fährt. Der Simulator verhält sich wie die Firmware,
einschließlich ihrer Eigenheiten.

```bash
wallplotter-sim --port 8080 &

wallplotter-doctor --host 127.0.0.1:8080
wallplotter-location new Probe --span 2300 --left 1450 --right 1470
wallplotter-calibrate --host 127.0.0.1:8080 zero
wallplotter-calibrate --host 127.0.0.1:8080 jog --dx -100
plot --pattern frame --host 127.0.0.1:8080 --upload --run
wallplotter-location push --host 127.0.0.1:8080
```

Und die Web-UI dagegen: `python -m wallplotter.webapp`, oben im Kopf
`127.0.0.1:8080` eintragen. Der Reiter **Maschine** zeigt dann einen
Fortschrittsbalken, der wirklich läuft, und Pause/Weiter/Stopp tun etwas.

Damit ist geübt, was am Tag X zählt — und wenn dort etwas anders aussieht,
weiß man, dass es an der Hardware liegt und nicht an der Bedienung.

### Noch zu beschaffen

- [ ] **GT2-Riemen mit Stahlkern**, 6 mm, offen, rund 8 m — der wichtigste
      Einkauf. Glasfaser kostet Faktor 5 an Genauigkeit
      ([Handbuch §8](wandplotter-handbuch.md#riemenwahl)) und legt die
      Längsresonanz mit 22 Hz genau dorthin, wo 1500 mm/min anregen.
- [ ] Servo **MG90S** (Metallgetriebe)
- [ ] **Eigenes 5-V-Netzteil für den Servo** — nicht vom Board versorgen,
      ein MG90S zieht blockiert rund 700 mA
- [ ] 24-V-Netzteil für die Motoren (2 × 1,2 A + Reserve)
- [ ] GT2-Pulley 20 Zähne, Bohrung 5 mm (2 ×)
- [ ] Lager 6706 (30 × 37 × 4 mm), 2 × — für die Gondel
- [ ] Aderendhülsen, Motor-Verlängerungskabel
- [ ] PETG für den Druck (kein PLA — PLA bricht an den Gondelarmen)

### Drucken, solange das Board unterwegs ist

- [ ] Gondel/Pen-Holder ([Thingiverse 7388981](https://www.thingiverse.com/thing:7388981))
- [ ] Motorhalterung, federgespannt ([Thingiverse 3440067](https://www.thingiverse.com/thing:3440067)) —
      **vor dem Druck** die Wellenhöhe (20 oder 22 mm) gegen die vorhandenen
      NEMA17 prüfen, die Variante ist nicht nachträglich anpassbar

---

## Teil 2 — Der Tag, an dem das Board kommt

Reihenfolge ist Absicht: Jeder Schritt setzt voraus, dass der vorige sitzt.
Wer die Mechanik vor dem Servo baut, sucht später im Dunkeln.

### 1. Board auspacken und ansehen (ohne Strom)

- [ ] **PWM-Header:** Der Servo kommt hier **nicht** dran. Der Anschluss ist
      trotz seines Namens eine analoge 0–10-V-Ausgabe (RC-Glied, LM358,
      Trimmpoti — Schaltplan Blatt 3, Handbuch 3.6). Aus einer
      Servo-Impulsfolge wird dort ein Gleichspannungspegel.
- [ ] **OLED-Header** suchen: trägt `gpio.26` und `gpio.27` und ist frei,
      solange kein Display steckt. Pinfolge und Versorgungsspannung
      **nachmessen**, nicht aus dem Bild ablesen.
- [ ] **DIAG-Jumper** suchen — die braucht das sensorlose Homing (Schritt 8).
      Achtung, das Handbuch ist da eindeutig: Wo DIAG benutzt wird, darf am
      zugehörigen Endstop-Eingang **kein** Jumper stecken.

### 2. Flashen und ins WLAN

```
$Sta/SSID=...
$Sta/Password=...
$WiFi/Mode=STA
$Hostname=wandplotter
```

- [ ] `config.yaml` hochladen (die aus [`config/`](../config/fluidnc-wallplotter.yaml))
- [ ] **`$HTTP/BlockDuringMotion=OFF`** — einmalig. Ab Werk steht die Sperre
      an, und dann antwortet `/command` während jeder Fahrt mit HTTP 503.
      Unsere Software geht über den WebSocket-Kanal und merkt davon nichts;
      jede Diagnose mit `curl` oder im Browser merkt es sofort.

**Kriterium:** `wallplotter-doctor --host <ip>` meldet Board erreichbar,
Position und SD-Karte grün.

### 3. Motoren auf dem Tisch

Noch ohne Mechanik, noch ohne Riemen.

- [ ] Treiberstrom prüfen: `run_amps: 1.2` (17HS4412P1-3) — nicht höher
- [ ] Beide Achsen einzeln jog­gen, Laufrichtung notieren
- [ ] Werden die Treiber warm? Kühlkörper sitzen?

**Kriterium:** Beide Motoren drehen auf `wallplotter-calibrate jog` in die
erwartete Richtung, ohne Schrittverluste und ohne heiß zu werden.

### 4. Servo und Pen-Lift

Der erste Punkt, an dem Zahlen aus dem Repo durch Messwerte ersetzt werden.

- [ ] Servo an den in Schritt 1 gefundenen GPIO, Versorgung aus dem eigenen
      5-V-Netzteil, **Masse gemeinsam**
- [ ] `output_pin` in der `config.yaml` eintragen
- [ ] S-Werte für oben und unten ertasten (`M3 S30`, `M5` über den Kanal)
- [ ] Werte in `toolhead.PENS` eintragen — die Katalogwerte sind Schätzungen,
      keine Messwerte

**Kriterium:** `plot --pattern pen-test` zeichnet alle Striche vollständig.
Fehlende Strichanfänge heißen: `dwell_s` erhöhen. Ausgefranste Enden heißen:
zu viel Anpressdruck.

### 5. Mechanik und Wandmontage

- [ ] Motorhalterungen setzen, Riemen ablängen (längster Riemen 3,4 m je
      Seite bei 2 × 2,5 m, plus Umlenkung und Reserve)
- [ ] Gondel montieren, Gegengewicht ist keins — sie wiegt sich selbst

### 6. Standort einmessen

```bash
wallplotter-calibrate --host <ip> zero          # Gondel am Referenzpunkt
wallplotter-location new Keller --span 2300 --left 1450 --right 1470
wallplotter-location config Keller              # Block in die config.yaml
```

- [ ] Drei Maße mit dem Zollstock: Abstand der Umlenkpunkte, linke und rechte
      Riemenlänge bei der Gondel am Nullpunkt
- [ ] Kinematikblock in die `config.yaml`, Board neu starten
- [ ] `wallplotter-location show` lesen — Auflösung, Riemenkräfte, Urteil

**Kriterium:** `wallplotter-doctor --host <ip>` meldet die Ankermaße der
Firmware als passend zum aktiven Standort.

### 7. Fläche anfahren

```bash
wallplotter-calibrate --host <ip> jog --dx -100
wallplotter-calibrate --host <ip> record bottom-left    # und die übrigen
wallplotter-calibrate show
```

- [ ] Vier Ecken sind ideal (dann warnt das Werkzeug bei schiefer Aufhängung),
      zwei diagonale reichen

### 8. Homing festlegen — die Entscheidung, die man nicht vertagen sollte

Ohne reproduzierbare Referenzfahrt ist ein dauerhaft gespeicherter Nullpunkt
wertlos: Nach dem Einschalten ist die Maschinenposition willkürlich, und der
Versatz zeigt ins Leere. Mehrfarbige Plots über mehrere Tage hängen daran.

- [ ] StallGuard einrichten (`cycle: 1`, DIAG-Jumper gesetzt,
      Empfindlichkeit tasten)
- [ ] `$H` mehrfach fahren, Wiederholbarkeit messen
- [ ] Dann `wallplotter-calibrate zero --persistent` (G54 im NVS)

**Kriterium:** Nach `$H`, Board aus, Board an, `$H` steht die Gondel wieder
an derselben Stelle — auf den Millimeter.

### 9. Die vier Testmuster, in dieser Reihenfolge

| Muster | Prüft | Was man mitnimmt |
| --- | --- | --- |
| `frame` | Fläche, Rechtwinkligkeit, Erreichbarkeit der Ecken | der erste ehrliche Test |
| `pen-test` | Servo-Wartezeit | `dwell_s` je Stift |
| `feed-ramp` | Tempo bis zum Riemenspringen | brauchbarer `--draw-feed` |
| `grid` | Maßstab, mit dem Zollstock nachmessen | Eingabe für `wallplotter-correct` |

- [ ] Bei `grid` die Uhr mitlaufen lassen und mit der Laufzeitangabe im
      GCode-Kopf vergleichen. Weicht sie ab, sind `--acceleration` und
      `--max-rate` die Stellschrauben.

### 10. Vorverzerrung scharf stellen

```bash
wallplotter-correct raster --steps 4 -o raster.gcode
wallplotter-correct messen --steps 4      # Vorlage zum Eintragen
wallplotter-correct anpassen              # → korrektur.json
```

- [ ] Der Anpassungsschritt sagt, wieviel die Korrektur wegnimmt. Wird der
      Fehler nicht deutlich kleiner, war zu grob gemessen oder das Modell
      passt nicht — dann ist die Mechanik die richtige Antwort, nicht die
      Software.

### 11. Erst danach: der Laser

Nicht am selben Tag. Der Laserpfad ist gegen die Dokumentation und den
Quelltext gebaut, aber an keiner Hardware erprobt. Vor dem ersten scharfen
Schuss gehört das erzeugte Programm **gelesen**, nicht geglaubt — und Stift
und Laser können weder denselben Pin noch dieselbe Frequenz benutzen.

---

## Wenn etwas nicht geht

| Symptom | Wahrscheinliche Ursache |
| --- | --- |
| HTTP 503 bei jeder Abfrage während der Fahrt | `$HTTP/BlockDuringMotion` steht noch an (Schritt 2) |
| Upload läuft ins Leere, Datei nicht auf der Karte | falscher Endpunkt — die Karte ist `/upload`, `/files` ist der Flash |
| Nullpunkt lässt sich nicht setzen | G-Code über `/command?plain=` erreicht den Parser nicht; das geht nur über den Kanal |
| Kein Statusreport | dasselbe — `?` über HTTP liefert die Hilfeseite, nicht `<Idle\|…>` |
| Nach dem Stopp steht Alarm | so gehört es: Ein Soft-Reset in der Fahrt kostet die Maschinenposition. `$X`, dann Ecke anfahren und Nullpunkt herstellen |
| Nullpunkt nach dem Farbwechsel weg | `G92` ist flüchtig. `wallplotter-calibrate zero --persistent`, aber erst zusammen mit Homing (Schritt 8) |
| „Die Maschine ist beschäftigt“ beim Start | so gehört es: Es läuft schon ein Programm. Abwarten, stoppen — oder `--trotzdem`, wenn die Statusabfrage lügt |
| Wellige Linien bei Schraffur | Pendelresonanz — `wallplotter.motion` warnt vorher und nennt zwei Auswege |
| Riemen springt bei Leerwegen | `--travel-as-g1`, oder `feed-ramp` neu auswerten |

Und immer zuerst: `wallplotter-doctor --host <ip>`. Der geht die Kette von
der Installation bis zum Board durch und sagt bei jedem Punkt, was der
nächste Schritt wäre.
