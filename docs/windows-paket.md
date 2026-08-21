# Windows-Datei der Web-Oberfläche

Eine einzelne, eigenständige `Wandplotter.exe` — kein installiertes Python
nötig, kein `pip install`. Enthält nur die Web-Oberfläche
(`wallplotter-web`), nicht die zehn Kommandozeilenbefehle: Zum
Doppelklicken auf einem Rechner, der nicht der Entwicklungsrechner ist.

## Bauen

Von Hand über GitHub: Tab **Actions** → **Windows-Datei bauen** →
**Run workflow**. Nach ein paar Minuten liegt `Wandplotter-windows` unter
„Artifacts" am Ende des Laufs — eine ZIP-Datei mit der `.exe` darin.

Lokal auf einem Windows-Rechner geht es genauso:

```powershell
pip install -e ".[geometry,web,photo]" pyinstaller
pyinstaller tools/wallplotter-web.spec
```

Das Ergebnis liegt danach unter `dist\Wandplotter.exe`.

## Was drin ist — und was nicht

Gebaut mit den Extras `geometry`, `web` und `photo`: SVGs, alle drei
Bildverfahren, die nur Pillow brauchen (`stipple`, `tsp`, `spiral`). **Nicht**
enthalten ist `hatch` (Schraffur über das Fremdpaket `hatched`) — das läuft
seit Shapely 2 ohnehin nicht mehr, siehe README, Abschnitt „Fotos". Wer es
trotzdem braucht, installiert `pip install -e ".[hatch]"` vor dem Bauen dazu.

Warum eine Datei allein nicht reicht, steht im Kopf von
[`tools/wallplotter-web.spec`](https://github.com/AndreasS964/Wallplotter/blob/main/tools/wallplotter-web.spec):
NiceGUI liefert seine Oberfläche über eigene, nicht-Python-Dateien aus,
und vpype entdeckt einen Teil seiner Kommandos erst zur Laufzeit über
Paket-Metadaten — beides übersieht PyInstallers eingebaute Importsuche ohne
Nachhilfe.

## Benutzen

Doppelklicken öffnet ein Konsolenfenster (das Server-Protokoll bleibt
sichtbar, für Fehlersuche) und nach einer Sekunde von selbst den Browser auf
`http://127.0.0.1:8080`. Vom Handy oder einem zweiten Rechner im selben Netz
geht es über die IP-Adresse des PCs, Port 8080 — genau wie beim Start über
`wallplotter-web` auf der Kommandozeile.

Windows fragt beim ersten Start zweimal nach: **SmartScreen** hält eine
unbekannte, unsignierte `.exe` erst einmal zurück — „Weitere Informationen"
→ „Trotzdem ausführen". Und die **Firewall** fragt, ob das Programm im
Netzwerk lauschen darf — das muss sie erlauben, sonst bleibt der Server nur
vom PC selbst erreichbar.

Der Start dauert spürbar länger als bei `wallplotter-web` aus einer venv:
Eine `--onefile`-Datei packt sich bei jedem Aufruf neu in ein temporäres
Verzeichnis aus, das kostet ein paar Sekunden. Dafür ist es die eine Datei,
die sich verschicken lässt.

## Standorte, Kalibrierung, Korrektur

Diese Daten liegen als Dateien neben dem Startverzeichnis (`standorte.json`,
`calibration.json` …) — beim Verschieben der `.exe` in einen anderen Ordner
gehen sie nicht automatisch mit. Wer den Wandplotter regelmäßig von einem
bestimmten Rechner aus bedient, legt die Datei am besten einmal an ihrem Platz
ab und startet sie immer von dort.
