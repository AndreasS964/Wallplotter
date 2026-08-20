"""Das Aussehen — eine Beschreibung, drei Oberflächen.

Farben sind der klassische Fall für stille Doppelpflege: Die Website hatte
ihre Palette, die Web-UI die von Quasar, und das Terminal gar keine. Hier wird
festgenagelt, dass es bei einer Quelle bleibt — und dass die Farbe im Terminal
verschwindet, wo niemand hinsieht.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from wallplotter.design import (
    DUNKEL,
    LICHT,
    MONO,
    Palette,
    Terminal,
    css_variables,
    quasar_colors,
    stylesheet,
)

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Die Paletten
# ---------------------------------------------------------------------------


def test_beide_paletten_tragen_dieselben_namen():
    """Sonst müsste jede Stelle, die eine Farbe benutzt, den Modus kennen."""
    assert LICHT.as_dict().keys() == DUNKEL.as_dict().keys()


@pytest.mark.parametrize("palette", [LICHT, DUNKEL], ids=["licht", "dunkel"])
def test_jede_farbe_ist_eine_farbe(palette: Palette):
    for name, wert in palette.as_dict().items():
        assert re.fullmatch(r"#[0-9a-f]{6}", wert), f"{name} = {wert!r}"


def test_der_akzent_ist_die_stiftfarbe():
    """Nicht irgendein Blau: der Fineliner aus dem Stiftkatalog.

    Läuft das auseinander, hat die Oberfläche eine andere Farbe als der Strich,
    den sie ankündigt — und die Legende unter der Vorschau lügt.
    """
    from wallplotter.toolhead import PenToolhead

    assert LICHT.accent == PenToolhead().color


def test_hell_und_dunkel_sind_wirklich_hell_und_dunkel():
    def helligkeit(hexwert: str) -> int:
        return sum(int(hexwert[i : i + 2], 16) for i in (1, 3, 5))

    assert helligkeit(LICHT.bg) > helligkeit(LICHT.fg)
    assert helligkeit(DUNKEL.bg) < helligkeit(DUNKEL.fg)


def test_das_raster_ist_blasser_als_die_linien():
    """Ein Raster, gegen das die Zeichnung ankämpfen muss, ist kein Hintergrund."""

    def helligkeit(hexwert: str) -> int:
        return sum(int(hexwert[i : i + 2], 16) for i in (1, 3, 5))

    assert helligkeit(LICHT.raster) > helligkeit(LICHT.line)
    assert helligkeit(DUNKEL.raster) < helligkeit(DUNKEL.line)


# ---------------------------------------------------------------------------
# Das Stylesheet
# ---------------------------------------------------------------------------


def test_jede_benutzte_variable_ist_auch_definiert():
    """Eine nicht definierte CSS-Variable fällt still aus — das Element bleibt
    dann durchsichtig oder schwarz, je nach Eigenschaft."""
    css = stylesheet()
    benutzt = set(re.findall(r"var\(--([a-z-]+)\)", css))
    definiert = set(LICHT.as_dict())
    assert benutzt <= definiert, f"nicht definiert: {sorted(benutzt - definiert)}"


def test_das_stylesheet_setzt_beide_moden():
    css = stylesheet()
    assert "prefers-color-scheme: dark" in css
    assert "body.body--dark" in css, "ohne das folgt Quasar dem System nicht"


def test_die_quasar_ueberschreibungen_sind_spezifisch_genug():
    """Quasars Stylesheet wird **nach** diesem geladen.

    Bei gleicher Spezifität gewinnt das spätere — auch mit ``!important`` auf
    beiden Seiten. Jede Regel, die an einer ``.q-``-Klasse ansetzt, braucht
    deshalb eine Stufe mehr: entweder ein ``body`` davor oder eine zweite
    Klasse. Genau daran ist der erste Anlauf gescheitert, sichtbar an einem
    Knopf, der im Dunkelmodus im hellen Blau stehen blieb.

    Geprüft werden alle Regeln, nicht nur die mehrzeiligen — die einzeilige
    Form ``.q-tab {{ … }}`` ist genauso schwach und fiel beim ersten Anlauf
    dieses Tests durch das Raster.
    """
    schwach = []
    for block in stylesheet().split("}"):
        if "{" not in block:
            continue
        for selektor in block.rsplit("{", 1)[0].split(","):
            selektor = selektor.strip()
            if ".q-" not in selektor or selektor.startswith("@"):
                continue
            teile = selektor.split()
            stark = not teile[0].startswith(".") or teile[-1].count(".") >= 2
            if not stark:
                schwach.append(selektor)
    assert schwach == [], f"zu schwach gegen Quasar: {schwach}"


def test_die_schriften_sind_gesetzt():
    assert MONO in stylesheet()


def test_quasar_bekommt_die_eigenen_farben():
    farben = quasar_colors()
    assert farben["primary"] == LICHT.accent
    assert farben["negative"] == LICHT.bad


def test_css_variables_baut_gueltiges_css():
    block = css_variables(LICHT, ":root")
    assert block.startswith(":root {")
    assert block.rstrip().endswith("}")
    assert "--accent: #1a4fd6;" in block


# ---------------------------------------------------------------------------
# Die Website nimmt dieselben Farben
# ---------------------------------------------------------------------------


def test_die_website_baut_ihre_palette_aus_demselben_modul(tmp_path):
    """Sonst driften Website und Web-UI auseinander — langsam und unbemerkt."""
    ergebnis = subprocess.run(
        [sys.executable, str(REPO / "tools" / "build_site.py"), "--out", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    if ergebnis.returncode != 0:
        pytest.skip(f"Website nicht baubar (Extra: markdown fehlt?): {ergebnis.stderr[-200:]}")

    css = (tmp_path / "style.css").read_text(encoding="utf-8")
    for name, wert in LICHT.as_dict().items():
        assert f"--{name}: {wert};" in css, f"{name} fehlt in der Website"
    assert f"--accent: {DUNKEL.accent};" in css


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------


class KeinTerminal:
    def isatty(self) -> bool:
        return False


class EchtesTerminal:
    def isatty(self) -> bool:
        return True


def test_ohne_terminal_keine_steuerzeichen():
    """Sonst landen sie in jeder umgeleiteten Datei und in jedem CI-Protokoll."""
    stil = Terminal(strom=KeinTerminal())
    assert stil.farbe is False
    assert stil("Text", "fett") == "Text"


def test_no_color_wird_beachtet(monkeypatch):
    """no-color.org: die Variable zählt, ihr Inhalt nicht — auch leer ist gesetzt."""
    monkeypatch.setenv("NO_COLOR", "")
    assert Terminal(strom=EchtesTerminal()).farbe is False


def test_term_dumb_wird_beachtet(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert Terminal(strom=EchtesTerminal()).farbe is False


def test_am_terminal_wird_gefaerbt(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    stil = Terminal(strom=EchtesTerminal())
    assert stil.farbe is True
    gefaerbt = stil("Text", "akzent")
    assert gefaerbt.startswith("\033[") and gefaerbt.endswith("\033[0m")
    assert "Text" in gefaerbt


def test_unbekannte_stile_faerben_nicht_versehentlich():
    stil = Terminal(farbe=True)
    assert stil("Text", "gibtsnicht") == "Text"


def test_leerer_text_bleibt_leer():
    assert Terminal(farbe=True)("", "fett") == ""


def test_der_kopf_traegt_titel_und_linie():
    kopf = Terminal(farbe=False).kopf("Einrichtung", "Untertitel", 20)
    zeilen = kopf.splitlines()
    assert zeilen[0] == "Einrichtung"
    assert zeilen[1] == "Untertitel"
    assert zeilen[2] == "━" * 20


def test_die_laenge_stimmt_ohne_farbe():
    """Damit Tabellen und Einrückungen ausgerichtet bleiben, wenn umgeleitet wird."""
    stil = Terminal(farbe=False)
    assert len(stil("abcde", "fett", "akzent")) == 5
