# Projekt: V-Plotter für Kletterwand-Keller

## Ziel
Im Keller wird eine Kletterwand gebaut, darunter eine Wandfläche (**2 m Breite × 2,5 m Höhe**), die per selbstgebautem V-Plotter/Polargraph bemalt/beplottet werden soll.

## Status
Elektronik-Board bestellt, Motoren vorhanden. Nächster Schritt: Board testen, danach Software entwickeln, erst danach Mechanik drucken/aufbauen.

---

## Hardware

### Steuerung
- **BIGTREETECH Rodent CNC Control Board** (gebraucht, eBay, ~30€)
  - ESP32-D0WD-V3 mit WiFi (802.11 b/g/n)
  - 4× TMC2160-Treiber (SPI-konfigurierbar, bis 3A, für NEMA23 ausgelegt – bei uns nur 2 Kanäle genutzt, 2 Reserve für spätere Recycling-Nutzung)
  - Eingangsspannung: DC24-56V
  - FluidNC-kompatibel

### Motoren
- **3× NEMA17 Typ 17HS4412P1-3**, 1,2A, 0,45 Nm, 40mm (2 im Einsatz, 1 Ersatz)
- (Alternative im Fundus vorhanden, aber nicht verwendet: NEMA17 0,4Nm/1,7A – zu hoher Strom für ruhigen Betrieb am Board)

### Antrieb
- GT2-Zahnriemen 6mm (open end, ca. 7-8m Gesamtbedarf für beide Seiten inkl. Reserve)
- GT2-Pulley 20 Zähne, Bohrung passend zur Motorwelle (5mm)

### Mechanik-Design-Entscheidungen
- **Selbstgewichtete Gondel** statt separatem fallendem Gegengewicht am Kabelende → macht die 1,4×-Breite-Fallweg-Beschränkung (aus dem klassischen Polargraph-Design) irrelevant, 2,5m Deckenhöhe reicht problemlos
- **Federgespannte Motorhalterung** statt schraubfixierter Variante → gleicht Riemendehnung über die Zeit automatisch aus, wichtig bei mehrstündigen unbeaufsichtigten Plots und mehreren Metern Riemenlänge
- **Homing ohne mechanische Endstop-Schalter**: physischer Anschlag oben am Motorhalter + `G92`/„Set Home" beim Referenzieren, alternativ sensorloses StallGuard-Homing (TMC2160 kann das)

### Ausgewählte STL-Dateien
- **Gondel/Pen-Holder**: „Makelangelo plotter head July 2026" von i-make-robots (Dan Royer/Marginally Clever) — https://www.thingiverse.com/thing:7388981
  - Aktuelle Werksversion, engere Toleranzen, weniger Teile
  - Benötigt: 2× 6706-Lager (30×37×4mm), 1 Büroklammer, Kreuzschlitzschraubendreher
  - Montageanleitung: https://mcr.dozuki.com/Guide/How+to+assemble+pen+holder+2026/52?lang=en
- **Motorhalterung mit Belt-Tensioner**: „Polargraph / Vertical Plotter Spring Tensioned Motor Mount" von flickeringsight — https://www.thingiverse.com/thing:3440067
  - Federtensioniert, kein Gegengewicht nötig, zwei Wellenhöhen-Varianten (20mm/22mm) — vor dem Druck gegen die 17HS4412P1-3 prüfen
- **Zusatzteil, ggf. ergänzend prüfen**: Makelangelo Belt Tensioner (Rollenlager-Version) — offiziell von Marginally Clever, verhindert Skipping/Slack bei großen Maschinen

### Sonstiges Material (noch zu beschaffen)
- Servo (MG90S empfohlen, Metallgetriebe) für Pen-Lift
- 24V-Netzteil, ausreichend Ampere für 2× 1,2A Motoren + Servo + Reserve
- PETG-Filament (nicht PLA – bricht an Gondelarmen)
- Aderendhülsen/Kabel für Terminal-Blöcke, Motor-Verlängerungskabel

---

## Firmware & Software

### Firmware: FluidNC
- Läuft auf dem ESP32 des Rodent-Boards, Konfiguration über `config.yaml` (kein Neukompilieren nötig)
- Kinematik: `WallPlotter` (Ankerpunkte, Segmentlänge)
- TMC2160-Treiber: Strom exakt auf 1,2A der Motoren einstellen, StallGuard optional für sensorloses Homing
- **WLAN dauerhaft aktiv** (Station-Modus, verbindet sich mit Heimnetz), erreichbar über Hostname/IP
- Pen-Lift über Servo, angesteuert per `M3`/`M5` (GRBL-Konvention) — **nicht** `M280` (das ist Marlin-spezifisch)

### Steuerung/Bedienung
- FluidNC-eingebautes **Web-UI** (ESP3D-basiert): Datei-Upload auf µSD-Karte am Board, Jog-Controls, Terminal, Live-Status — von jedem Gerät im Heimnetz per Browser erreichbar
- Alternativ: Universal Gcode Sender / gSender (auch netzwerkfähig)

### Bild-zu-GCode-Pipeline — **wird selbst programmiert**
Entscheidung: Keine direkte Nutzung der Makelangelo-Software für den GCode-Export, weil diese einen Marlin/Makelangelo-firmware-spezifischen GCode-Dialekt erzeugt (`M280`-Servobefehle, proprietäre `D`-Codes, `M101`-Geometrie-Header) — inkompatibel mit FluidNC/GRBL ohne Nacharbeit.

**Geplanter Ansatz:**
- **vpype** (Python-Bibliothek, nicht nur CLI) als Basis für Geometrie-Verarbeitung (Linien sortieren, Occlusion entfernen, Hatching für Flächen, Skalierung)
- Eigenes Python-Skript für:
  - SVG einlesen → vpype-Pipeline → GCode-Export im FluidNC-eigenen, sauberen Dialekt (`G0`/`G1` + `M3`/`M5` fürs Pen-Lift)
  - Ggf. direkter Upload per HTTP an FluidNC-Web-API statt manuellem Hochladen im Browser
- Für Bildvorlagen (Fotos, Halftone/Dithering): später ergänzende vpype-Plugins (`hatched` o.ä.) statt Eigenentwicklung
  *(so kam es nicht: alle vier Bildverfahren rechnen selbst — `hatched` ist seit
  Shapely 2 nicht mehr zusammen mit vpype installierbar. Siehe Handbuch §9.)*
- **Wird gemeinsam abends nach und nach entwickelt**, sobald das Board getestet ist

---

## Toolheads (perspektivisch)
- **Stift/Marker** (aktueller Plan) – Sharpie, Faserstift, Kreidemarker
- **Pinsel** – braucht Farbnachschub-Lösung (Reservoir), sonst trocknet er zwischen Strichen
- **Sprühdose** – wie beim „Hektor"-Projekt (Jürg Lehni); braucht Ventilbetätigung statt reinem Hub, Absaugung/Belüftung im Keller nötig
- **Kreide/Pastell** – ähnlich Stift, empfindlicher gegen Anpressdruck, ggf. Federmechanismus statt starrem Servo-Hub
- **Laser-Modul** – firmwareseitig vorbereitet (M3/M4 am Board vorhanden), aber für verputzte Wand unpraktisch (Rauch/Geruch, kein Materialabtrag); eher Kandidat für ein separates Zweitprojekt auf anderer Arbeitsfläche

### Laser-Mode (FluidNC/GRBL) — Hintergrund
- `M3`/`M4` + `S<wert>` steuern PWM-Leistung einer Spindel/eines Lasers
- `M3` = konstante Leistung; `M4` = **dynamischer Lasermodus**, Leistung wird automatisch an Verfahrgeschwindigkeit angepasst (verhindert Verbrennungen in Kurven/Ecken beim Abbremsen)
- Lasermodus schaltet den Ausgang zusätzlich bei Feed-Hold/Alarm automatisch ab (Sicherheitsfunktion)
- **Für unseren Pen-Lift-Servo reicht `M3 S<Winkel>`** – wir zweckentfremden denselben PWM-Pin/Mechanismus, brauchen aber nicht die dynamische Skalierung von `M4`

---

## Offene Punkte / nächste Schritte (in Reihenfolge)
1. Board erhalten, FluidNC grundlegend zum Laufen bringen (Pins, Treiberstrom) — Motoren erstmal ohne Mechanik auf dem Tisch testen
2. WallPlotter-Kinematik mit Testwerten simulieren/prüfen
3. Servo/Pen-Lift-Ansteuerung durchtesten
4. Erst danach: Gondel + Motorhalterungen drucken (PETG), Wellenhöhen-Variante der Motorhalterung final gegen die Motoren prüfen
5. Wandmontage, Riemen ablängen, Erstinbetriebnahme (Homing per physischem Anschlag + G92)
6. Software/GCode-Pipeline parallel bzw. danach entwickeln (gemeinsame Abendsessions)

## Bewusst verworfene Optionen (zur Erinnerung, falls später nochmal aufkommt)
- AliExpress-Fertigkits (~30-40€): zu klein (A4/A3), Perlenkette statt Riemen, trotzdem Verkabelung nötig
- Makelangelo 5 als Kaufkit: funktioniert, aber teurer, Versand/Zoll aus Kanada, weniger flexibel als Eigenbau
- Home-Assistant-Integration/Pi-Bridge: kein Mehrwert für dieses Projekt, bewusst nicht umgesetzt
- CNC-Shield V4 statt RAMPS/Rodent: Lötarbeit an Microstepping-Pins nötig, deshalb verworfen
- Separates fallendes Gegengewicht: durch selbstgewichtete Gondel ersetzt (siehe oben)
