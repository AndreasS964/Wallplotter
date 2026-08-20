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

    def __init__(self, eingabe=input, ausgabe=print) -> None:
        self._eingabe = eingabe
        self._ausgabe = ausgabe

    # -- Ausgabe ----------------------------------------------------------

    def _umbrechen(self, text: str, zusatz: str = "") -> list[str]:
        """Absatz umbrechen und dabei die vorhandene Einrückung beibehalten."""
        if not text.strip():
            return [""]
        einzug = zusatz + " " * (len(text) - len(text.lstrip(" ")))
        return textwrap.wrap(
            text.strip(),
            width=self.BREITE,
            initial_indent=einzug,
            subsequent_indent=einzug + ("  " if text.lstrip().startswith(("*", "-")) else ""),
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]

    def sagen(self, text: str) -> None:
        for zeile in self._umbrechen(text):
            self._ausgabe(zeile)

    def warnen(self, text: str) -> None:
        for zeile in self._umbrechen(text, "! " if text.strip() else ""):
            self._ausgabe(zeile)

    # -- Eingabe ----------------------------------------------------------

    def _lesen(self, prompt: str) -> str:
        try:
            return self._eingabe(prompt).strip()
        except (EOFError, KeyboardInterrupt) as exc:
            self._ausgabe("")
            raise Abbruch("abgebrochen") from exc

    def frage(self, text: str, vorgabe: str = "") -> str:
        hinweis = f" [{vorgabe}]" if vorgabe else ""
        return self._lesen(f"{text}{hinweis}: ") or vorgabe

    def zahl(self, text: str, vorgabe: float | None = None, einheit: str = "mm") -> float:
        hinweis = f" [{vorgabe:g}]" if vorgabe is not None else ""
        while True:
            roh = self._lesen(f"{text} in {einheit}{hinweis}: ")
            if not roh and vorgabe is not None:
                return float(vorgabe)
            try:
                return float(roh.replace(",", "."))
            except ValueError:
                self.warnen(f"{roh!r} ist keine Zahl.")

    def ja(self, text: str, vorgabe: bool = True) -> bool:
        hinweis = "[J/n]" if vorgabe else "[j/N]"
        while True:
            roh = self._lesen(f"{text} {hinweis} ").lower()
            if not roh:
                return vorgabe
            if roh in ("j", "ja", "y", "yes"):
                return True
            if roh in ("n", "nein", "no"):
                return False
            self.warnen("Bitte j oder n.")

    def auswahl(self, text: str, optionen: Sequence[tuple[str, str]]) -> str:
        self._ausgabe(text)
        for nummer, (_key, beschreibung) in enumerate(optionen, 1):
            self._ausgabe(f"  {nummer}) {beschreibung}")
        while True:
            roh = self._lesen("Auswahl [1]: ") or "1"
            if roh.isdigit() and 1 <= int(roh) <= len(optionen):
                return optionen[int(roh) - 1][0]
            for key, _beschreibung in optionen:
                if roh.lower() == key.lower():
                    return key
            self.warnen(f"1 bis {len(optionen)}.")

    def weiter(self, text: str) -> None:
        self._lesen(f"{text} [Enter] ")


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
    dialog.sagen("Einrichtung")
    dialog.sagen("=" * 60)
    for schritt in SCHRITTE:
        zustand = schritt.zustand(ctx)
        dialog.sagen(f"{_MARK[zustand]} {schritt.titel:<36} {zustand}")
    dialog.sagen("")
    offen = [s for s in SCHRITTE if s.zustand(ctx) != ERLEDIGT]
    if offen:
        dialog.sagen(f"Weiter mit: wallplotter-setup --ab {offen[0].key}")
    else:
        dialog.sagen("Alles erledigt, soweit es sich ohne Nachfrage feststellen lässt.")
    dialog.sagen("")
    dialog.sagen("? heißt: das kann keine Software wissen — danach wird gefragt.")
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

    try:
        return lauf(ctx, dialog, ab=args.ab, nur=args.nur, alle=args.alle)
    except Abbruch as exc:
        print(f"\nAbgebrochen: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
