# Software-Roadmap: Bild-zu-GCode-Tool

**Teil des V-Plotter-Projekts (Kletterwand-Keller)** — Ergänzung zum Toolchain-Plan (vpype als Geometrie-Basis, FluidNC/GRBL-GCode-Dialekt, HTTP-Upload). Dieses Dokument klärt die UI-Architektur und beschreibt die Software-Entwicklung in Stufen.

*Stand: August 2026*

---

## Architekturentscheidung: Standalone vs. browserbasiert

Die pragmatischste Lösung ist keine Entweder-Oder-Entscheidung, sondern eine **lokale Web-App**: Ein kleiner Python-Server läuft auf dem PC, die Oberfläche ist eine ganz normale Browser-Seite. Das ist streng genommen „standalone" (läuft komplett lokal, keine Cloud, keine Installation nötig) **und** „browserbasiert" zugleich — genau das Modell, das FluidNC selbst mit seinem ESP3D-WebUI schon nutzt, nur läuft der Server diesmal auf dem PC statt auf dem ESP32.

### Warum nicht ein natives GUI-Toolkit (Qt/Tkinter)?

Würde funktionieren, aber mehr Aufwand für Layout/Styling, schwerer von einem zweiten Gerät aus erreichbar (z. B. Handy an der Wand, um den Fortschritt zu checken), und bringt keinen Vorteil gegenüber der Web-Variante, da ohnehin alles in Python läuft.

### Warum nicht "echtes" Browser-only, ganz ohne eigenen Server (Pyodide/WASM)?

Technisch inzwischen machbarer als man denkt — Pyodide hat mittlerweile `opencv-python` und `shapely` im Paketangebot, und generell werden immer mehr Pakete mit C-/C++-/Rust-Erweiterungen portiert. Aber: wissenschaftliche Pakete bringen ein WASM-Bundle von mehreren zehn MB mit (spürbare Ladezeit), jede vpype-Operation liefe langsamer als nativ, und ob sich vpype samt Plugins (`hatched`, `occult`) dort überhaupt sauber per micropip installieren lässt, ist ungetestetes Terrain. Für ein Zwei-Personen-Heimnetz-Projekt ohne Deployment-Zwang bringt das nur Risiko, keinen Mehrwert.

### Framework-Empfehlung: NiceGUI

Reines Python, kein HTML/CSS/JS nötig, baut auf FastAPI (HTTP) und WebSockets (Live-Updates) auf — praktisch für einen Fortschrittsbalken während des Plots, ohne Page-Reload. Aktiv weiterentwickelt (Major-Release 3.0, Herbst 2025), explizit auch für Robotik-Projekte und das Tunen von Motorsteuerungen positioniert — trifft den Anwendungsfall ziemlich genau.

Alternativen:
- **Streamlit** — noch simpler für reine Prototypen, führt aber bei jeder Interaktion das komplette Skript neu aus; für eine Live-Fortschrittsanzeige während eines mehrstündigen Plots unpraktisch.
- **Flask/FastAPI + eigenes HTML** — maximale Kontrolle, aber mehr Frontend-Arbeit.

Eine fertige Web-Oberfläche speziell für vpype gibt es nicht — vpype selbst bringt nur einen nativen, Qt-basierten Viewer zum reinen Betrachten mit, keine Pipeline-Oberfläche. Eigene kleine GUI-Wrapper um vpype-Kommandos zu bauen ist unter Plotter-Bastlern aber ein bekanntes Muster (meist als einfaches Skript) — die Web-Variante ist der konsequente nächste Schritt.

---

## Roadmap: Software-Stufen

Baut auf Etappe 0 aus dem Hardware-Fahrplan auf (Board/Servo/Laser-Mode fertig konfiguriert). Die folgenden Stufen verfeinern die Software-Etappen aus dem Toolchain-Plan mit Fokus auf die UI-Architektur.

### 1. Core-Bibliothek (kein UI)

Reine Python-Funktionen ohne jede Oberfläche:
- `pipeline.py` — SVG → vpype-Verarbeitung → optimierte Linien
- `gcode.py` — Linien → GCode via vpype-gcode-Profil
- `upload.py` — HTTP-Upload + `$SD/Run` an FluidNC

Jede Funktion einzeln mit einem Testmuster (Quadrat, Kreis) in der Python-Konsole durchtestbar. **Wichtigste Design-Entscheidung der Roadmap:** CLI und Web-UI greifen später auf exakt dieselbe Logik zu — keine doppelte Pipeline.

### 2. CLI-Wrapper

```
plot.py input.svg --upload --run
```

Dünner Wrapper um Stufe 1. Schnellster Weg zum ersten kompletten End-to-End-Plot, bleibt als Debugging-/Scripting-Werkzeug auch nach der Web-UI erhalten.

### 3. Lokale Web-UI, Basisversion

NiceGUI-App obendrauf: SVG-Upload-Feld, Eingaben für Skalierung/Ränder/linesort-Optionen, „Generieren"- und „Auf Wand plotten"-Button. Läuft mit `python webapp.py`, erreichbar über `http://<pc-ip>:8080` von jedem Gerät im Heimnetz — auch vom Handy oder Tablet an der Kletterwand.

```python
from nicegui import ui, events
from core.pipeline import svg_to_lines
from core.gcode import lines_to_gcode
from core.upload import upload_and_run

state = {"gcode": None}

def generate(e: events.UploadEventArguments):
    svg_bytes = e.content.read()
    lines = svg_to_lines(svg_bytes, width_mm=2000, height_mm=2500)
    state["gcode"] = lines_to_gcode(lines, profile="fluidnc_wallplotter")
    ui.notify(f"{len(lines)} Linien optimiert")

ui.upload(on_upload=generate, label="SVG hochladen").props("accept=.svg")
ui.button("Auf Wand plotten", on_click=lambda: upload_and_run(state["gcode"]))
ui.run(host="0.0.0.0", port=8080, title="Wandplotter")
```

### 4. Vorschau vor dem Plotten

Optimierten Pfad direkt im Browser anzeigen (Pen-Up-Wege z. B. rot gestrichelt, Pen-Down blau durchgezogen) — verhindert, dass erst nach zwei Stunden Plotzeit auffällt, dass die Skalierung nicht stimmte. NiceGUI kann SVG-Inhalte direkt per `ui.html()` einbetten, ohne Zusatzbibliothek.

### 5. Foto-Zweig

`hatched`-Parameter (Levels, Hatch-Pitch, Blur) als Regler in derselben Oberfläche, gleicher Vorschau-/Upload-Flow wie bei Vektor-SVGs. Dank WebSocket-Basis kann die Vorschau bei Reglerbewegung sogar live nachziehen.

### 6. Live-Status & Jobsteuerung

FluidNC-Status (Position, SD-Fortschritt) per periodischem `/command?plain=?`-Poll in die eigene Oberfläche holen: Fortschrittsbalken, Pause/Resume/Stop. Damit bleibt für den ganzen Ablauf nur noch ein Browser-Tab statt Wechsel zwischen eigenem Tool und separatem FluidNC-WebUI.

### 7. Komfort (optional, später)

Verlauf vergangener Plots mit Thumbnail, evtl. eine kleine gemeinsame Warteliste für die Abendsessions.

---

## Quellen

- NiceGUI: https://github.com/zauberzeug/nicegui, https://nicegui.io/documentation
- Pyodide (Paketunterstützung, Bundle-Größe): https://pyodide.org/en/stable/project/changelog.html, https://pyodide.com/
- vpype: https://vpype.readthedocs.io/
