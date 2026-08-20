"""Das Aussehen — einmal beschrieben, dreimal benutzt.

Drei Oberflächen zeigen dasselbe Projekt: die Website, die Web-UI und das
Terminal. Bisher sah jede anders aus, und die Web-UI vor allem nach nichts:
Standard-Quasar-Blau auf Standard-Grau, wie jedes Dashboard.

Das hier ist die eine Beschreibung, aus der alle drei ihre Farben nehmen —
dieselbe Idee wie bei der ``config.yaml``: zwei Beschreibungen derselben Sache
laufen auseinander, und niemand merkt es.

Woher die Farben kommen
-----------------------
Nicht aus einer Palette von der Stange. Die Maschine zieht Tinte über eine
Wand, also:

* **Papier statt Weiß.** ``#fbfaf8`` ist gebrochen, wie Zeichenpapier. Reines
  Weiß gibt es an einer Wand nicht.
* **Tinte statt Schwarz.** ``#23201c`` ist warm, kein reines Schwarz.
* **Der Akzent ist die Stiftfarbe.** ``#1a4fd6`` steht schon als Vorgabe im
  Stiftkatalog (``PenToolhead.color``) — der blaue Fineliner, mit dem dieses
  Projekt angefangen hat. Er war die naheliegendste Farbe und stand die ganze
  Zeit im Code.
* **Zahlen in Monospace.** Koordinaten, Vorschübe und S-Werte sind Maschinen-
  angaben; in einer Proportionalschrift springen sie beim Aktualisieren.

Dunkel ist kein invertiertes Hell, sondern die Werkstatt am Abend: ein kühles,
dunkles Grau, in dem der Stiftblau heller wird, damit er noch trägt.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

__all__ = [
    "Palette",
    "LICHT",
    "DUNKEL",
    "SCHRIFT",
    "MONO",
    "css_variables",
    "stylesheet",
    "quasar_colors",
    "Terminal",
]


@dataclass(frozen=True)
class Palette:
    """Ein Satz Farben. Beide Sätze tragen dieselben Namen — sonst müsste jede
    Stelle, die eine Farbe benutzt, den Modus kennen."""

    bg: str
    """Der Grund, auf dem alles liegt."""

    bg_soft: str
    """Abgesetzte Flächen: Kopfzeile, Seitenleiste, Vorschauhintergrund."""

    surface: str
    """Karten — das, was über dem Grund liegt."""

    fg: str
    fg_soft: str
    line: str
    raster: str
    """Die Linien des Zeichenbretts. Blasser als ``line`` — ein Raster, gegen
    das die Zeichnung ankämpfen muss, ist kein Hintergrund mehr."""

    accent: str
    accent_soft: str
    ok: str
    warn: str
    bad: str

    def as_dict(self) -> dict[str, str]:
        return {
            "bg": self.bg,
            "bg-soft": self.bg_soft,
            "surface": self.surface,
            "fg": self.fg,
            "fg-soft": self.fg_soft,
            "line": self.line,
            "raster": self.raster,
            "accent": self.accent,
            "accent-soft": self.accent_soft,
            "ok": self.ok,
            "warn": self.warn,
            "bad": self.bad,
        }


LICHT = Palette(
    bg="#fbfaf8",
    bg_soft="#f2efe9",
    surface="#ffffff",
    fg="#23201c",
    fg_soft="#5d574e",
    line="#e2ded5",
    raster="#efece5",
    accent="#1a4fd6",
    accent_soft="#e8eefc",
    ok="#2f7d4f",
    warn="#a3661a",
    bad="#b3372b",
)

DUNKEL = Palette(
    bg="#16151a",
    bg_soft="#1e1d24",
    surface="#1e1d24",
    fg="#e8e5e0",
    fg_soft="#a49f97",
    line="#302e38",
    raster="#232229",
    accent="#8aa9ff",
    accent_soft="#21243a",
    ok="#5fbf85",
    warn="#e0a94f",
    bad="#ff7a6b",
)

SCHRIFT = (
    '-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, '
    '"Helvetica Neue", Arial, sans-serif'
)
MONO = '"SF Mono", "JetBrains Mono", "Fira Code", ui-monospace, Menlo, Consolas, monospace'


def css_variables(palette: Palette, selector: str = ":root") -> str:
    """Die Farben eines Satzes als CSS-Eigenschaften."""
    zeilen = "\n".join(f"  --{name}: {wert};" for name, wert in palette.as_dict().items())
    return f"{selector} {{\n{zeilen}\n}}"


def stylesheet() -> str:
    """Das Stylesheet der Web-UI.

    Quasar bringt seine eigenen Farben mit, und die sind für ein Dashboard
    gedacht. Hier wird nicht gegen jede einzelne Klasse angeschrieben, sondern
    an den paar Stellen angesetzt, die den Eindruck ausmachen: Grund, Karten,
    Kopfzeile, Reiter, Eingabefelder.

    Warum die Quasar-Regeln mit ``body`` davor stehen: Dessen Stylesheet wird
    **nach** diesem geladen. Bei gleicher Spezifität gewinnt das spätere, und
    dann nützt auch ``!important`` nichts — beide haben es. Eine Stufe mehr
    Spezifität entscheidet das unabhängig von der Ladereihenfolge.
    """
    return f"""
{css_variables(LICHT)}

@media (prefers-color-scheme: dark) {{
{css_variables(DUNKEL, "  :root:not(.body--light)")}
}}
{css_variables(DUNKEL, "body.body--dark")}

body, body .q-page-container, body .q-tab-panels {{
  background: var(--bg) !important;
  color: var(--fg);
  font-family: {SCHRIFT};
}}

/* Kopfzeile: kein farbiger Balken, sondern Papier mit einer Haarlinie darunter.
   Ein blauer Riegel über der Seite ist das, was jedes Dashboard macht. */
body .q-header {{
  background: var(--bg-soft) !important;
  color: var(--fg) !important;
  border-bottom: 1px solid var(--line);
  box-shadow: none;
}}

.wp-marke {{
  font-weight: 650;
  letter-spacing: -0.015em;
  font-size: 1.15rem;
  color: var(--fg);
}}
.wp-marke .wp-punkt {{ color: var(--accent); }}

/* Karten: eine Linie statt eines Schattens. Technische Zeichnungen haben
   Kanten, keine Schlagschatten. */
body .q-card {{
  background: var(--surface) !important;
  color: var(--fg);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: none !important;
}}

/* Abschnittsüberschriften in den Karten */
.wp-titel {{
  font-size: 0.7rem;
  font-weight: 650;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fg-soft);
}}

/* Zahlen sind Maschinenangaben und gehören in eine feste Breite, sonst
   springt die Anzeige bei jedem Aktualisieren. */
.wp-zahl, .wp-zahl * {{ font-family: {MONO}; font-variant-numeric: tabular-nums; }}

body .q-tab {{ color: var(--fg-soft); }}
body .q-tab--active {{ color: var(--accent); }}
body .q-tabs {{
  background: var(--bg-soft);
  border-bottom: 1px solid var(--line);
}}
body .q-tab__indicator {{ background: var(--accent); height: 2px; }}

body .q-field__control {{ border-radius: 8px; }}
body .q-field--outlined .q-field__control:before {{ border-color: var(--line); }}
body .q-separator {{ background: var(--line); }}
body .q-expansion-item {{ border-radius: 10px; }}

.text-grey, .text-grey-7, .text-grey-8 {{ color: var(--fg-soft) !important; }}

/* Die Vorschau ist ein Zeichenbrett, kein leeres Feld: ein Raster darunter,
   damit Größenverhältnisse sichtbar sind, bevor etwas geladen ist. Fein und
   grob übereinander wie auf Millimeterpapier — und beides so blass, dass die
   Zeichnung darüber nicht dagegen ankämpfen muss. */
body .wp-brett {{
  background-color: var(--surface);
  background-image:
    repeating-linear-gradient(var(--raster) 0 1px, transparent 1px 48px),
    repeating-linear-gradient(90deg, var(--raster) 0 1px, transparent 1px 48px);
  border-radius: 8px;
}}

.wp-leer {{
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  min-height: 58vh;
  color: var(--fg-soft);
  font-size: 0.9rem;
  text-align: center;
  line-height: 1.6;
}}
.wp-leer svg {{ opacity: 0.5; }}

/* Der Knopf, der die Maschine losfahren lässt, darf aussehen wie eine
   Entscheidung — und nicht wie jeder andere Knopf daneben. */
.q-btn.wp-los {{
  background: var(--accent) !important;
  color: #fff !important;
  font-weight: 600;
  letter-spacing: 0.01em;
  border-radius: 9px;
}}
body.body--dark .q-btn.wp-los {{ color: #12131a !important; }}

.q-btn.wp-halt {{ color: var(--bad) !important; border-color: var(--bad) !important; }}

/* Der Datei-Ablegebereich: eine gestrichelte Fläche, wie ein Feld, in das
   etwas hineingehört — nicht ein blauer Riegel mit weißem Kasten darunter. */
body .q-uploader {{
  background: transparent !important;
  border: 1.5px dashed var(--line);
  border-radius: 10px;
  box-shadow: none;
  width: 100%;
}}
body .q-uploader__header {{
  background: var(--bg-soft) !important;
  color: var(--fg-soft) !important;
}}
body .q-uploader__list {{
  background: transparent !important;
  color: var(--fg-soft);
  min-height: 64px;
}}
body .q-uploader__title {{ font-weight: 550; color: var(--fg); }}
body .q-uploader__subtitle {{ color: var(--fg-soft); }}

/* Quasar schreibt Knopfbeschriftungen groß. Auf einer Seite mit acht Knöpfen
   nebeneinander liest sich das wie ein Formular von 1998. */
body .q-btn {{ text-transform: none; letter-spacing: 0; }}

/* Die Statuszeile in der Kopfzeile kann eine ganze Fehlermeldung enthalten.
   Ungekürzt schiebt sie die Adresse aus dem Bild. */
.wp-status {{
  max-width: 42ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}

/* Eingabefelder: die Beschriftung darf im Dunkeln nicht verschwinden. */
body .q-field__label {{ color: var(--fg-soft); }}
body .q-field__native, body .q-field__input {{ color: var(--fg); }}
"""


def quasar_colors() -> dict[str, str]:
    """Die Farben, die Quasar selbst kennt — für ``ui.colors(**…)``."""
    return {
        "primary": LICHT.accent,
        "secondary": LICHT.fg_soft,
        "accent": LICHT.accent,
        "positive": LICHT.ok,
        "negative": LICHT.bad,
        "warning": LICHT.warn,
        "info": LICHT.accent,
        "dark": DUNKEL.bg,
    }


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------


class Terminal:
    """Farbe im Terminal — aber nur, wenn dort auch jemand hinsieht.

    Ausgeschaltet bei ``NO_COLOR`` (no-color.org), bei ``TERM=dumb`` und immer
    dann, wenn die Ausgabe kein Terminal ist. Sonst landen Steuerzeichen in
    jeder Datei, in die jemand umleitet — und in jedem CI-Protokoll.
    """

    _CODES = {
        "fett": "1",
        "matt": "2",
        "kursiv": "3",
        "akzent": "38;5;69",
        "ok": "38;5;71",
        "warn": "38;5;172",
        "bad": "38;5;167",
        "leise": "38;5;245",
    }

    def __init__(self, strom=None, farbe: bool | None = None) -> None:
        self.strom = strom or sys.stdout
        self.farbe = self._entscheiden() if farbe is None else farbe

    def _entscheiden(self) -> bool:
        if os.environ.get("NO_COLOR") is not None:
            return False
        if os.environ.get("TERM", "") == "dumb":
            return False
        return bool(getattr(self.strom, "isatty", lambda: False)())

    def __call__(self, text: str, *stile: str) -> str:
        if not self.farbe or not stile or not text:
            return text
        codes = ";".join(self._CODES[stil] for stil in stile if stil in self._CODES)
        return f"\033[{codes}m{text}\033[0m" if codes else text

    # -- Bausteine --------------------------------------------------------

    def regel(self, breite: int = 78) -> str:
        return self("─" * breite, "leise")

    def kopf(self, titel: str, unterzeile: str = "", breite: int = 78) -> str:
        """Ein Kopf mit Linie darunter — der Anfang von etwas, nicht nur Text."""
        zeilen = [self(titel, "fett"), self("━" * breite, "akzent")]
        if unterzeile:
            zeilen.insert(1, self(unterzeile, "leise"))
        return "\n".join(zeilen)
