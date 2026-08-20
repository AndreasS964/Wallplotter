"""``wallplotter-setup`` — die geführte Einrichtung im Terminal.

    wallplotter-setup                    # dort weiter, wo es aufgehört hat
    wallplotter-setup --status           # nur nachsehen, was noch fehlt
    wallplotter-setup --ab flaeche       # ab diesem Schritt
    wallplotter-setup --nur servo        # genau diesen einen
    wallplotter-setup --alle             # auch Erledigtes noch einmal

Der Ablauf selbst steht in :mod:`wallplotter.wizard` und weiß nichts von einem
Terminal. Hier ist nur die Frage- und Antwortseite: :class:`KonsolenDialog`.
Deshalb kann derselbe Ablauf später in der Web-UI laufen, ohne dass ein Schritt
angefasst wird.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path

from .design import Terminal
from .wizard import (
    ERLEDIGT,
    OFFEN,
    SCHRITTE,
    UNBEKANNT,
    Abbruch,
    Kontext,
    lauf,
    schritt_nach_name,
)

_MARK = {ERLEDIGT: "✓", OFFEN: "·", UNBEKANNT: "?"}
_STIL = {ERLEDIGT: "ok", OFFEN: "leise", UNBEKANNT: "warn"}


class KonsolenDialog:
    """Fragen und Antworten über stdin/stdout.

    Ctrl-C und ein geschlossenes stdin sind hier kein Absturz, sondern ein
    :class:`~wallplotter.wizard.Abbruch` — der Wizard sagt dann, wie es
    weitergeht, statt einen Stacktrace zu hinterlassen.
    """

    BREITE = 78
    """Zeilenbreite. Die Erklärungen im Wizard sind ganze Absätze; ungebrochen
    laufen sie im Terminal zu einer einzigen Zeile zusammen und werden nicht
    gelesen."""

    def __init__(self, eingabe=input, ausgabe=print, stil: Terminal | None = None) -> None:
        self._eingabe = eingabe
        self._ausgabe = ausgabe
        self.stil = stil or Terminal()
        self._nach_kopf = False
        """Ob die zuletzt ausgegebene Zeile eine Schrittüberschrift war — nur
        deren Begründung darunter wird zurückgenommen, nicht jeder eingerückte
        Text."""

    # -- Ausgabe ----------------------------------------------------------

    def _umbrechen(self, text: str, zusatz: str = "") -> list[str]:
        """Absatz umbrechen und dabei die vorhandene Einrückung beibehalten.

        ``zusatz`` steht nur vor der **ersten** Zeile: Ein ``!`` vor jeder
        Folgezeile sähe nach vier Warnungen aus, wo es eine ist.
        """
        if not text.strip():
            return [""]
        einzug = " " * (len(text) - len(text.lstrip(" ")))
        folge = einzug + " " * len(zusatz)
        if text.lstrip().startswith(("*", "-")):
            folge += "  "
        return textwrap.wrap(
            text.strip(),
            width=self.BREITE,
            initial_indent=zusatz + einzug,
            subsequent_indent=folge,
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]

    def sagen(self, text: str) -> None:
        for zeile in self._umbrechen(text):
            self._ausgabe(self._faerben(zeile))

    def warnen(self, text: str) -> None:
        for zeile in self._umbrechen(text, "! " if text.strip() else ""):
            self._ausgabe(self.stil(zeile, "warn"))

    def _faerben(self, zeile: str) -> str:
        """Die wenigen Zeilen hervorheben, die Struktur tragen.

        Der Wizard schickt fertige Sätze; welche davon eine Überschrift sind,
        steht ihm nicht an. Erkennbar sind sie an der Form — und nur diese
        Handvoll Muster wird eingefärbt, damit nicht am Ende alles bunt ist und
        nichts mehr auffällt.
        """
        blank = zeile.strip()
        if blank.startswith("[") and "]" in blank:
            nummer, _, rest = blank.partition("] ")
            if rest.endswith(("übersprungen", "kein Board")):
                self._nach_kopf = False
                return self.stil(zeile, "leise")
            self._nach_kopf = True
            return f"{self.stil(nummer + ']', 'akzent')} {self.stil(rest, 'fett')}"
        if self._nach_kopf and zeile.startswith("    ") and blank:
            return self.stil(zeile, "leise")
        self._nach_kopf = False
        if blank in ("Noch offen:", "Durch. Ab hier ist es ein Plotter."):
            return self.stil(zeile, "fett")
        return zeile

    # -- Eingabe ----------------------------------------------------------

    def _lesen(self, prompt: str) -> str:
        try:
            return self._eingabe(prompt).strip()
        except (EOFError, KeyboardInterrupt) as exc:
            self._ausgabe("")
            raise Abbruch("abgebrochen") from exc

    def frage(self, text: str, vorgabe: str = "") -> str:
        hinweis = self.stil(f" [{vorgabe}]", "leise") if vorgabe else ""
        return self._lesen(f"{self.stil(text, 'akzent')}{hinweis}: ") or vorgabe

    def zahl(self, text: str, vorgabe: float | None = None, einheit: str = "mm") -> float:
        hinweis = f" [{vorgabe:g}]" if vorgabe is not None else ""
        while True:
            roh = self._lesen(
                f"{self.stil(text, 'akzent')} in {einheit}"
                f"{self.stil(hinweis, 'leise')}: "
            )
            if not roh and vorgabe is not None:
                return float(vorgabe)
            try:
                return float(roh.replace(",", "."))
            except ValueError:
                self.warnen(f"{roh!r} ist keine Zahl.")

    def ja(self, text: str, vorgabe: bool = True) -> bool:
        hinweis = "[J/n]" if vorgabe else "[j/N]"
        while True:
            roh = self._lesen(
                f"{self.stil(text, 'akzent')} {self.stil(hinweis, 'leise')} "
            ).lower()
            if not roh:
                return vorgabe
            if roh in ("j", "ja", "y", "yes"):
                return True
            if roh in ("n", "nein", "no"):
                return False
            self.warnen("Bitte j oder n.")

    def auswahl(self, text: str, optionen: Sequence[tuple[str, str]]) -> str:
        self._ausgabe(self.stil(text, "akzent"))
        for nummer, (_key, beschreibung) in enumerate(optionen, 1):
            self._ausgabe(f"  {self.stil(str(nummer) + ')', 'fett')} {beschreibung}")
        while True:
            roh = self._lesen("Auswahl [1]: ") or "1"
            if roh.isdigit() and 1 <= int(roh) <= len(optionen):
                return optionen[int(roh) - 1][0]
            for key, _beschreibung in optionen:
                if roh.lower() == key.lower():
                    return key
            self.warnen(f"1 bis {len(optionen)}.")

    def weiter(self, text: str) -> None:
        self._lesen(f"{self.stil(text, 'akzent')} {self.stil('[Enter]', 'leise')} ")


def build_parser() -> argparse.ArgumentParser:
    from .location import DEFAULT_PATH as LOCATION_PATH  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="wallplotter-setup",
        description="Geführte Einrichtung: von der leeren Wand bis zum ersten Strich.",
        epilog="Jeder Schritt geht auch einzeln — der Wizard kennt vor allem die Reihenfolge.",
    )
    parser.add_argument("--host", default="fluidnc.local", help="Hostname oder IP des Boards")
    parser.add_argument("--standorte", type=Path, default=LOCATION_PATH)
    parser.add_argument(
        "--firmware-config",
        type=Path,
        default=Path("config/fluidnc-wallplotter.yaml"),
        help="wohin die erzeugte config.yaml geschrieben wird",
    )
    parser.add_argument("--status", action="store_true", help="nur zeigen, was noch fehlt")
    parser.add_argument("--ab", metavar="SCHRITT", help="bei diesem Schritt beginnen")
    parser.add_argument("--nur", metavar="SCHRITT", help="genau diesen Schritt ausführen")
    parser.add_argument(
        "--alle", action="store_true", help="auch Schritte, die schon erledigt sind"
    )
    return parser


def zeige_status(ctx: Kontext, dialog: KonsolenDialog) -> int:
    stil = dialog.stil
    print(stil.kopf("Wandplotter · Einrichtung", f"Board {ctx.host}", 60))
    for schritt in SCHRITTE:
        zustand = schritt.zustand(ctx)
        marke = stil(_MARK[zustand], _STIL[zustand])
        titel = schritt.titel if zustand != ERLEDIGT else stil(schritt.titel, "leise")
        breite = 36 + (len(titel) - len(schritt.titel))
        print(f"{marke} {titel:<{breite}} {stil(zustand, _STIL[zustand])}")
    print()
    offen = [s for s in SCHRITTE if s.zustand(ctx) != ERLEDIGT]
    if offen:
        print("Weiter mit: " + stil(f"wallplotter-setup --ab {offen[0].key}", "akzent"))
    else:
        print(stil("Alles erledigt, soweit es sich ohne Nachfrage feststellen lässt.", "ok"))
    print()
    print(stil("? heißt: das kann keine Software wissen — danach wird gefragt.", "leise"))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = Kontext(
        host=args.host,
        standorte=args.standorte,
        firmware_datei=args.firmware_config,
    )
    dialog = KonsolenDialog()

    if args.status:
        return zeige_status(ctx, dialog)

    for name in (args.ab, args.nur):
        if name:
            try:
                schritt_nach_name(name)
            except KeyError as exc:
                print(str(exc), file=sys.stderr)
                return 2

    print(
        dialog.stil.kopf(
            "Wandplotter · Einrichtung",
            "von der leeren Wand bis zum ersten Strich · Abbruch mit Strg-C",
        )
    )
    try:
        return lauf(ctx, dialog, ab=args.ab, nur=args.nur, alle=args.alle)
    except Abbruch as exc:
        print(f"\nAbgebrochen: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
