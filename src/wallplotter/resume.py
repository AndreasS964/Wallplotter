"""Einen abgebrochenen Plot dort fortsetzen, wo er stehengeblieben ist.

Ein Wandbild läuft Stunden. Stromausfall, versehentlicher Reset, leerer Stift,
Abendessen — irgendwann bricht ein Lauf ab, und dann noch einmal von vorn
anzufangen heißt: dieselbe Fläche ein zweites Mal überzeichnen. Also wird aus
der GCode-Datei ein Restprogramm geschnitten.

Was dabei zu rekonstruieren ist, steht nicht in der Zeile, an der es aufgehört
hat, sondern verteilt darüber: GRBL ist eine *modale* Sprache. Einheiten,
absolute Koordinaten, der zuletzt gesetzte Vorschub und der Zustand des
Werkzeugs gelten so lange weiter, bis sie jemand ändert. Ein Schnitt mitten
hinein ergibt nur dann ein lauffähiges Programm, wenn dieser Zustand vorn
wieder aufgebaut wird.

Angesetzt wird standardmäßig am Anfang des angefangenen Strichs, nicht exakt
an der Abbruchstelle: ein mittendrin fortgesetzter Strich hinterlässt einen
sichtbaren Ansatz, ein neu gezeichneter nicht.

Ein Punkt, den keine Software abnehmen kann: Nach einem Soft-Reset ist der
Nullpunkt weg, wenn er per ``G92`` gesetzt war. Erst die Ecke wieder anfahren
und den Nullpunkt herstellen (``wallplotter-calibrate zero --corner …``),
dann das Restprogramm starten — sonst zeichnet die Maschine sauber und
versetzt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .gcode import comment_lines

__all__ = [
    "ResumeError",
    "ProgramState",
    "scan_program",
    "line_for_percent",
    "resume_program",
    "read_program",
    "resume_file",
]

_WORD = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+)")


class ResumeError(RuntimeError):
    """Das Programm lässt sich an dieser Stelle nicht fortsetzen."""


@dataclass
class ProgramState:
    """Der modale Zustand nach einer bestimmten Programmzeile."""

    x: float | None = None
    y: float | None = None
    feed: float | None = None
    tool_value: float | None = None
    """Zuletzt gesetzter ``S``-Wert; ``None`` heißt: nach ``M5``, also aus."""

    tool_on: bool = False
    """Spindelzustand: ``M3``/``M4`` an, ``M5`` aus."""

    tool_mode: str = ""
    """Der zuletzt gesehene Spindelbefehl — ``M3`` oder ``M4``.

    Beim Stift steht er in jedem Absetzen mit drin und ist deshalb nie
    verloren. Beim Laser steht er *einmal* im Vorspann, danach folgen nur noch
    nackte ``S``-Zeilen: wer den Modus nicht wieder aufbaut, bekommt ein
    Restprogramm, das die halbe Zeichnung abfährt, ohne dass der Strahl je
    zündet.
    """

    drawing: bool = False
    """Ob das Werkzeug *wirkt* — und das ist nicht dasselbe wie ``tool_on``.

    Der Stift wird mit ``M3 S0`` angehoben, nicht mit ``M5``: die Spindel läuft
    weiter, der Servo hält nur die obere Stellung. Wer allein auf ``M3`` sieht,
    hält jeden Leerweg für einen Strich. Unterschieden wird deshalb am
    ``S``-Wert (siehe :func:`scan_program`).
    """

    move_count: int = 0
    draw_count: int = 0
    units_mm: bool = True
    absolute: bool = True
    setup: list[str] = field(default_factory=list)
    """Die gesehenen Grundeinstellungen (G21/G90/G17), in Reihenfolge."""

    coolant: str = ""
    """Zuletzt gesehene Kühlmittel-/Luftschaltung — ``M7``, ``M8`` oder leer.

    Beim Laser ist das die Luftunterstützung, und sie steht wie der
    Spindelmodus nur einmal im Vorspann. Ein Restprogramm ohne sie schneidet
    trocken und schaltet die Luft am Ende trotzdem ab.
    """

    last_pause: str = ""
    """Text der zuletzt passierten ``M0``-Pause — welches Werkzeug stecken muss."""


def _pause_text(line: str) -> str:
    """Den Wechseltext einer ``M0``-Zeile herausholen.

    Erzeugt wird heute ``M0 (MSG,Stift wechseln auf: … — weiter mit Cycle
    Start)`` — Klammerkommentare mit ``MSG`` protokolliert FluidNC, ein
    ``;``-Kommentar dagegen verschwindet spurlos. Ältere Programme (und
    fremde) tragen den Text hinter einem Semikolon; beide Formen werden hier
    gelesen, sonst verlöre ein Fortsetzen die Angabe, welcher Stift im Halter
    stecken muss.
    """
    if "(" in line and ")" in line:
        inside = line[line.index("(") + 1 : line.rindex(")")].strip()
        for marker in ("MSG,", "MSG "):
            if inside.upper().startswith(marker):
                inside = inside[len(marker) :].strip()
                break
        if inside:
            return inside
    return line.partition(";")[2].strip() or "Halt"


def _code(line: str) -> str:
    """Erstes Wort ohne Kommentar, in Großschreibung — ``"g1"`` → ``"G1"``."""
    text = line.split(";", 1)[0].strip()
    return text.split()[0].upper() if text else ""


def _words(line: str) -> dict[str, float]:
    text = line.split(";", 1)[0]
    return {letter.upper(): float(value) for letter, value in _WORD.findall(text)}


def _idle_value(program: str) -> float:
    """Welcher ``S``-Wert in diesem Programm „Werkzeug aus" bedeutet.

    Steht nirgends im GCode und lässt sich nur erschließen: Der kleinste
    vorkommende ``S``-Wert ist die Ruhestellung — beim Stift die angehobene
    (meist ``S0``), beim Laser die Leistung null. Kommt nur ein einziger Wert
    vor, wird der Stift über ``M5`` gehoben; dann ist alles über null ein
    wirkender Zustand.
    """
    values = set()
    for line in program.splitlines():
        code = _code(line)
        if code in {"M3", "M03", "M4", "M04"} or (code.startswith("S") and len(code) > 1):
            words = _words(line)
            if "S" in words:
                values.add(words["S"])
    return min(values) if len(values) >= 2 else 0.0


def scan_program(program: str) -> list[ProgramState]:
    """Zustand nach jeder Zeile — Grundlage für jeden Schnitt.

    Zurück kommt eine Liste mit genauso vielen Einträgen wie das Programm
    Zeilen hat; Eintrag *i* beschreibt die Maschine, *nachdem* Zeile *i*
    abgearbeitet ist.
    """
    idle = _idle_value(program)
    states: list[ProgramState] = []
    current = ProgramState()
    for line in program.splitlines():
        code = _code(line)
        words = _words(line)
        following = ProgramState(
            x=current.x,
            y=current.y,
            feed=current.feed,
            tool_value=current.tool_value,
            tool_on=current.tool_on,
            tool_mode=current.tool_mode,
            coolant=current.coolant,
            drawing=current.drawing,
            move_count=current.move_count,
            draw_count=current.draw_count,
            units_mm=current.units_mm,
            absolute=current.absolute,
            setup=list(current.setup),
            last_pause=current.last_pause,
        )
        if code in {"G21", "G20", "G90", "G91", "G17"}:
            if code == "G20":
                following.units_mm = False
            elif code == "G21":
                following.units_mm = True
            elif code == "G90":
                following.absolute = True
            elif code == "G91":
                following.absolute = False
            if code not in following.setup:
                following.setup.append(code)
        elif code in {"G0", "G00", "G1", "G01"}:
            if "F" in words:
                following.feed = words["F"]
            if "X" in words:
                following.x = words["X"] if following.absolute else (following.x or 0) + words["X"]
            if "Y" in words:
                following.y = words["Y"] if following.absolute else (following.y or 0) + words["Y"]
            if "X" in words or "Y" in words:
                following.move_count += 1
                if code in {"G1", "G01"} and following.drawing:
                    following.draw_count += 1
        elif code in {"M3", "M03", "M4", "M04"}:
            following.tool_on = True
            following.tool_mode = "M4" if code in {"M4", "M04"} else "M3"
            following.tool_value = words.get("S", current.tool_value)
        elif code in {"M5", "M05"}:
            following.tool_on = False
            following.tool_value = None
        elif code.startswith("S") and len(code) > 1 and "S" in words:
            # Nackte S-Zeile: setzt die Leistung, nicht den Spindelzustand.
            # So schaltet der Laserkopf zwischen Strich und Leerweg um.
            following.tool_value = words["S"]
        elif code in {"M7", "M07", "M8", "M08"}:
            following.coolant = "M7" if code in {"M7", "M07"} else "M8"
        elif code in {"M9", "M09"}:
            following.coolant = ""
        elif code in {"M0", "M00"}:
            following.last_pause = _pause_text(line)
        elif code == "F" or (code.startswith("F") and len(code) > 1):
            if "F" in words:
                following.feed = words["F"]
        following.drawing = following.tool_on and (following.tool_value or 0.0) > idle
        states.append(following)
        current = following
    return states


def line_for_percent(program: str, percent: float) -> int:
    """Zeilennummer (0-basiert) zu einem Fortschritt in Prozent.

    FluidNC meldet den Fortschritt eines SD-Jobs als Anteil der gelesenen
    *Bytes*, nicht der Zeilen — deshalb wird hier auch über die Bytes gesucht.
    """
    if not 0 <= percent <= 100:
        raise ResumeError("Fortschritt liegt zwischen 0 und 100 Prozent")
    data = program.encode("utf-8")
    target = int(len(data) * percent / 100)
    seen = 0
    for index, line in enumerate(program.splitlines(keepends=True)):
        seen += len(line.encode("utf-8"))
        if seen > target:
            return index
    return max(0, len(program.splitlines()) - 1)


def _has_xy(line: str) -> bool:
    return bool({"X", "Y"} & set(_words(line)))


def _block_starts(lines: list[str], states: list[ProgramState]) -> list[int]:
    """Die Anfahrbewegungen — dort beginnt jeweils ein Strichblock.

    Ein Block ist: hinfahren, Werkzeug einrücken, zeichnen. Erkennbar ist der
    Anfang daran, dass eine Bewegung stattfindet, während das Werkzeug *nicht*
    wirkt.
    """
    return [
        index
        for index, line in enumerate(lines)
        if _has_xy(line) and not states[index].drawing
    ]


def _stroke_start(lines: list[str], states: list[ProgramState], target: int) -> int:
    """Zum Anfang des Strichblocks zurückgehen, in dem der Abbruch liegt.

    Zurückgegangen wird bis zur *Anfahrt*, nicht bis zum Werkzeugbefehl: sonst
    landet die letzte Zeichenbewegung des vorigen Strichs hinter dem
    eingefügten „Werkzeug aus" und wird mit gehobenem Stift abgefahren statt
    gezeichnet — ein Segment, das lautlos im Bild fehlt.

    Fällt die Abbruchstelle zwischen zwei Blöcke, wird der *vorige* noch einmal
    gezogen. Das ist Absicht: FluidNC meldet den Fortschritt anhand der
    gelesenen Bytes, und der Planer liest der Mechanik um etliche Zeilen
    voraus. Die gemeldete Stelle liegt also weiter als die tatsächliche —
    lieber einen Strich doppelt als einen gar nicht.
    """
    starts = _block_starts(lines, states)
    earlier = [index for index in starts if index <= target]
    return earlier[-1] if earlier else 0


def resume_program(
    program: str,
    *,
    line: int | None = None,
    percent: float | None = None,
    whole_stroke: bool = True,
    header: str = "",
) -> str:
    """Aus einem Programm das lauffähige Restprogramm ab einer Stelle schneiden.

    Entweder ``line`` (0-basiert) oder ``percent`` angeben — letzteres ist der
    Wert, den FluidNC im Status als ``SD:`` meldet.

    Mit ``whole_stroke`` (Standard) beginnt das Restprogramm am Anfang des
    angefangenen Strichs. Sonst wird auf den Millimeter genau angesetzt, was
    einen sichtbaren Ansatz hinterlässt, dafür nichts doppelt zeichnet.
    """
    lines = program.splitlines()
    if not lines:
        raise ResumeError("Leeres Programm")
    if (line is None) == (percent is None):
        raise ResumeError("Entweder line oder percent angeben, nicht beides")

    target = line_for_percent(program, percent) if percent is not None else line
    if not 0 <= target < len(lines):
        raise ResumeError(f"Zeile {target} liegt außerhalb des Programms (0…{len(lines) - 1})")

    states = scan_program(program)
    start = _stroke_start(lines, states, target) if whole_stroke else target
    before = states[start - 1] if start > 0 else ProgramState()

    rest = [text for text in lines[start:] if text.strip()]
    if states[-1].draw_count == 0:
        # Kein einziger Zug mit wirkendem Werkzeug: entweder ist das Programm
        # leer, oder es hebt den Stift anders als über die Spindel (etwa über
        # eine Z-Achse) — das kann hier niemand rekonstruieren.
        raise ResumeError(
            "In diesem Programm findet sich keine einzige Zeichenbewegung. Erwartet werden "
            "Programme dieser Toolchain, die das Werkzeug über M3/M4/M5 und S-Werte führen; "
            "ein Stiftheben über die Z-Achse lässt sich hier nicht nachvollziehen."
        )
    if states[-1].draw_count <= before.draw_count:
        raise ResumeError(
            "Ab dieser Stelle wird nichts mehr gezeichnet — der Plot war schon durch."
        )
    if before.draw_count == 0:
        # Abgebrochen, bevor der erste Strich saß: dann ist das Original das
        # Restprogramm, und ein zusammengeschnittenes wäre nur unübersichtlicher.
        return (
            f"; {header or 'Fortsetzung'}: der Abbruch lag noch vor dem ersten Strich,\n"
            "; deshalb steht hier das unveränderte Programm.\n" + program
        )

    total = states[-1].draw_count
    out = [
        f"; {header}" if header else "; Fortsetzung eines abgebrochenen Plots",
        f"; angesetzt an Zeile {start + 1} von {len(lines)}"
        + (f" (gemeldet waren {percent:.1f} %)" if percent is not None else ""),
        f"; {before.draw_count} von {total} Zeichenbewegungen waren durch"
        + (", der angefangene Strich wird neu gezogen" if whole_stroke and start < target else ""),
        "; Nullpunkt muss stehen — nach einem Reset erst die kalibrierte Ecke anfahren",
    ]
    if before.last_pause:
        # Bei einem mehrfarbigen Programm mit M0-Pausen steckt jetzt ein
        # bestimmtes Werkzeug im Halter — und das steht nur im Original.
        stecken = before.last_pause.removeprefix("anhalten — ")
        stecken = stecken.removesuffix(" — weiter mit Cycle Start")
        out += comment_lines(f"Im Halter muss stecken: {stecken}")
    # Modalen Zustand wieder aufbauen: die Maschine weiß nach einem Abbruch nichts mehr
    out += before.setup or ["G21", "G90", "G17"]
    out.append("M5 ; Werkzeug aus, bevor irgendetwas verfahren wird")
    if before.coolant:
        # Luft zuerst: sie soll stehen, bevor das Werkzeug wieder scharf ist
        out.append(f"{before.coolant} ; Luft wie im Original")
    if before.tool_mode:
        # Den Spindelmodus wieder setzen, Leistung auf null. Ohne das fährt ein
        # fortgesetztes Laserprogramm den ganzen Rest ab, ohne je zu zünden:
        # seine S-Zeilen setzen die Leistung, nicht den Zustand.
        out.append(f"{before.tool_mode} S0 ; Werkzeugmodus wie im Original")

    first_move = next((text for text in rest if _has_xy(text)), "")
    starts_with_rapid = _code(first_move) in {"G0", "G00"}
    if not starts_with_rapid and before.x is not None and before.y is not None:
        # Das Restprogramm fährt nicht selbst an: mit gehobenem Werkzeug hin
        out.append(f"G0 X{before.x:g} Y{before.y:g} ; an die Abbruchstelle")
    # Der Vorschub ist modal. Bringt das Restprogramm keinen eigenen mit — bei
    # fremden Dateien der Normalfall —, führe die erste G1-Bewegung ohne
    # Vorschub und die Firmware quittiert mit error:22.
    if before.feed and "F" not in _words(first_move):
        out.append(f"F{before.feed:g} ; Vorschub war modal gesetzt")
    if not whole_stroke and before.drawing and before.tool_value is not None:
        # Modus aus dem Original übernehmen: ein mit M4 erzeugtes Programm mit
        # M3 fortzusetzen hieße konstante Leistung, wo dynamische gemeint war
        out.append(f"{before.tool_mode or 'M3'} S{before.tool_value:g} ; Werkzeug stand an")
    out += rest
    return "\n".join(out) + "\n"


def read_program(path: Path | str) -> str:
    """GCode-Datei einlesen — nachsichtig, was die Kodierung angeht.

    Fremde Toolchains schreiben Kommentare gern in Latin-1; ein Umlaut darin
    ist kein Grund, ein halbfertiges Wandbild aufzugeben.
    """
    source = Path(path)
    if not source.exists():
        raise ResumeError(f"Keine Datei unter {source}")
    return source.read_text(encoding="utf-8", errors="replace")


def resume_file(
    path: Path | str,
    *,
    line: int | None = None,
    percent: float | None = None,
    whole_stroke: bool = True,
) -> str:
    source = Path(path)
    return resume_program(
        read_program(source),
        line=line,
        percent=percent,
        whole_stroke=whole_stroke,
        header=f"Fortsetzung von {source.name}",
    )
