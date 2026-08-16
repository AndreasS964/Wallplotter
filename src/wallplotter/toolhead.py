"""Werkzeugköpfe: was am unteren Ende der Gondel hängt.

Bisher war das fest verdrahtet ein Servo mit Stift. Gebraucht werden aber
mehrere Stifte — schwarz dünn, rot dick, Kreide auf Beton — und irgendwann ein
Laser. Deshalb steht hier eine Schnittstelle zwischen *Weg* und *Werkzeug*:
:mod:`wallplotter.gcode` kümmert sich um Koordinaten und weiß nicht mehr, was
ein Servo ist; der Kopf liefert die Zeilen, die ihn ein- und ausrücken.

Der Zuschnitt der Schnittstelle folgt dem, was Stift und Laser *wirklich*
unterscheidet — und das ist mehr als der Buchstabe im GCode:

======================  =============================  ==========================
                        Stift am Servo                 Laser
======================  =============================  ==========================
Bedeutung von ``S``     Position (Pulsbreite)          Leistung
``S`` ohne Bewegung     normal und nötig               wirkungslos (M4) bis
                                                       brandgefährlich (M3)
``G4`` danach           zwingend, der Servo braucht     schädlich — brennt ein
                        seine Zeit                     Loch
Leerweg                 Position muss *halten*         muss *aus* sein
PWM-Frequenz            ~50 Hz (RC-Servo)              1–100 kHz
======================  =============================  ==========================

Daraus folgt unmittelbar: ein Stift darf in FluidNC niemals an einer
``Laser:``-Spindel hängen (die skaliert ``S`` mit dem Vorschub — der Servo
würde im Betrieb mitflattern und bei jedem Leerweg in die Nullstellung
schnappen), und ein Laser niemals an der 50-Hz-PWM des Stifts. Zwei Köpfe
heißen zwei Spindeln in der ``config.yaml``, unterschieden über ``tool_num``
und im Programm angewählt mit ``M6 T<n>``.

Zum Laser: Alles hier ist gegen die GRBL-1.1-Laserdoku und den FluidNC-Quelltext
geschrieben, aber **nicht an Hardware erprobt** — es hängt noch keiner an der
Wand. Vor dem ersten scharfen Schuss gehört das Programm gelesen, nicht
geglaubt.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

__all__ = [
    "Toolhead",
    "ToolheadError",
    "PenToolhead",
    "LaserToolhead",
    "TOOLHEADS",
    "PENS",
    "toolhead_by_name",
    "describe_toolheads",
    "head_for",
    "pen_map",
    "fmt",
]


class ToolheadError(RuntimeError):
    """Der Kopf verweigert diese Konfiguration."""


def fmt(value: float, decimals: int = 3) -> str:
    """Zahl ohne unnötige Nullen (``10.500`` → ``10.5``).

    Liegt hier statt in :mod:`wallplotter.gcode`, weil jetzt beide Seiten
    formatieren müssen und zwei Fassungen davon irgendwann auseinanderlaufen.
    """
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


@runtime_checkable
class Toolhead(Protocol):
    """Was :mod:`wallplotter.gcode` von einem Werkzeug wissen muss.

    Strukturelle Typisierung statt Vererbungszwang: wer die Methoden hat, ist
    ein Kopf. Ein Schleppmesser oder eine Sprühdose lässt sich damit von außen
    nachrüsten, ohne dieses Modul anzufassen.
    """

    name: str
    color: str
    width_mm: float
    passes: int
    """Wie oft dieselbe Geometrie abgefahren wird — Attribut, nicht Methode.

    Ein Laser schneidet mehrfach; Kreide auf rauem Putz deckt im zweiten
    Durchgang erst richtig.
    """

    def describe(self) -> str:
        """Eine Zeile für den GCode-Kopf und die Oberfläche."""
        ...

    def program_start(self) -> list[str]:
        """Zeilen direkt nach ``G21``/``G90``/``G17``: Kopf in sicheren Zustand."""
        ...

    def program_end(self) -> list[str]:
        """Letzte Zeilen des Kopfes, vor Parkfahrt und Programmende."""
        ...

    def engage(self) -> list[str]:
        """Werkzeug wirkt: Stift auf der Wand, Laser an, Messer im Material."""
        ...

    def retract(self) -> list[str]:
        """Gegenstück zu :meth:`engage` — immer *vor* jedem Leerweg."""
        ...

    def cycle_time_s(self) -> float:
        """Sekunden je Linie fürs einmalige Ein- und Ausrücken."""
        ...

    def between_passes(self) -> list[str]:
        """Zeilen zwischen zwei Durchgängen — beim Stift nichts."""
        ...

    def feed_for(self, default: float) -> float:
        """Zeichenvorschub dieses Kopfes; ``default`` ist der aus der Konfiguration."""
        ...

    def check(self, *, travel_as_g1: bool, draw_feed: float) -> list[str]:
        """Einwände gegen diese Konfiguration.

        Zurück kommen Warnungen im Klartext. Ist die Kombination *gefährlich*
        statt nur unklug, wirft die Methode :class:`ToolheadError` — dann
        entsteht gar kein GCode.
        """
        ...

    def change_prompt(self, label: str = "") -> str:
        """Was der Mensch vor der Wand liest, wenn das Programm zum Wechsel hält.

        ``label`` ist die Beschriftung der Ebene, also meist die Strichfarbe.
        """
        ...


# ---------------------------------------------------------------------------
# Stift
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PenToolhead:
    """Stift am Servo, angesteuert über den PWM-/Spindel-Pin.

    FluidNC/GRBL-Dialekt: ``M3 S<wert>`` setzt die Servostellung, ``M5``
    schaltet ab. Bewusst *nicht* ``M280`` — das ist Marlin/Makelangelo.

    Die mitgelieferten Stiftsorten (:data:`PENS`) sind **Startwerte, keine
    Messwerte**: Servohebel, Federweg und Stifthalter sind an jedem Aufbau
    anders. Der erste Plot ist deshalb ``--pattern pen-test`` — fehlende
    Strichanfänge heißen zu kurze ``dwell_s``, ausgefranste Enden zu hohe
    ``down_value``.
    """

    name: str = "Stift"
    down_value: int = 30
    """S-Wert für „Stift auf der Wand"."""

    up_value: int = 0
    """S-Wert für „Stift abgehoben"."""

    dwell_s: float = 0.25
    """Wartezeit nach jedem Hub, damit der Servo die Position erreicht."""

    use_m5_for_up: bool = False
    """``True`` sendet ``M5`` statt ``M3 S<up_value>`` zum Abheben.

    Nur sinnvoll, wenn der Servo bei abgeschaltetem PWM in die Up-Position
    federt — bei einem MG90S ist das normalerweise nicht der Fall.
    """

    color: str = "#1a4fd6"
    """Strichfarbe, für Vorschau und Beschriftung."""

    width_mm: float = 0.5
    """Strichbreite, für die Vorschau und die Wahl des Bahnabstands."""

    draw_feed: float | None = None
    """Eigener Zeichenvorschub; ``None`` nimmt den aus der Konfiguration.

    Ein Fineliner verträgt 1500 mm/min, ein Pinsel reißt dabei ab.
    """

    passes: int = 1
    """Mehrfach überzeichnen — Kreide auf rauem Putz deckt erst im zweiten Zug."""

    tool_number: int | None = None
    """``M6 T<n>``, falls in der ``config.yaml`` mehrere Spindeln stehen.

    Ohne Angabe wird nicht umgeschaltet — das ist richtig, solange nur eine
    Spindel konfiguriert ist. Sobald ein Laser danebensteht, muss *jeder* Kopf
    seine Spindel anwählen, sonst bleibt nach dem ersten Laserprogramm die
    Laserspindel aktiv und der Stift landet an der falschen PWM.
    """

    note: str = ""

    def __post_init__(self) -> None:
        # `name` steht vorn, weil der Katalog danach benannt ist — ein alter
        # positionaler Aufruf `PenConfig(30, 0, 0.25)` landete damit still als
        # Stift namens „30" mit down_value 0. Lieber laut danebenliegen.
        if not isinstance(self.name, str):
            raise ToolheadError(
                f"name muss Text sein, nicht {self.name!r} — die Felder heißen "
                "name, down_value, up_value, dwell_s; benannte Argumente benutzen"
            )
        if self.dwell_s < 0:
            raise ToolheadError("dwell_s kann nicht negativ sein")
        if self.width_mm <= 0:
            raise ToolheadError("Strichbreite muss größer als 0 sein")
        if self.draw_feed is not None and self.draw_feed <= 0:
            raise ToolheadError("draw_feed muss größer als 0 sein")
        if self.passes < 1:
            raise ToolheadError("mindestens ein Durchgang")

    # -- Schnittstelle ----------------------------------------------------

    def describe(self) -> str:
        text = f"{self.name}, {fmt(self.width_mm, 2)} mm, S{self.down_value}/{self.up_value}"
        if self.dwell_s:
            text += f", {fmt(self.dwell_s, 2)} s Wartezeit"
        if self.draw_feed:
            text += f", {fmt(self.draw_feed, 0)} mm/min"
        return text + (f" ({self.note})" if self.note else "")

    def program_start(self) -> list[str]:
        lines = [f"M6 T{self.tool_number} ; Stiftspindel wählen"] if self.tool_number is not None else []
        return lines + self.retract()

    def program_end(self) -> list[str]:
        return ["M5 ; Servo/PWM aus"]

    def engage(self) -> list[str]:
        lines = [f"M3 S{self.down_value}"]
        if self.dwell_s > 0:
            lines.append(f"G4 P{fmt(self.dwell_s, 2)}")
        return lines

    def retract(self) -> list[str]:
        lines = ["M5" if self.use_m5_for_up else f"M3 S{self.up_value}"]
        if self.dwell_s > 0:
            lines.append(f"G4 P{fmt(self.dwell_s, 2)}")
        return lines

    def cycle_time_s(self) -> float:
        return 2 * max(0.0, self.dwell_s)

    def between_passes(self) -> list[str]:
        return []

    def feed_for(self, default: float) -> float:
        return self.draw_feed or default

    def check(self, *, travel_as_g1: bool, draw_feed: float) -> list[str]:
        notes: list[str] = []
        feed = self.feed_for(draw_feed)
        if self.dwell_s == 0:
            notes.append(
                f"{self.name}: keine Servo-Wartezeit — die ersten Millimeter jedes "
                "Strichs fehlen, solange der Servo noch fährt."
            )
        # Die speed_map der mitgelieferten config.yaml bildet 0…100 ab; alles
        # darüber landet am Anschlag, ohne dass jemand es merkt.
        for label, value in (("down_value", self.down_value), ("up_value", self.up_value)):
            if not 0 <= value <= 100:
                notes.append(
                    f"{self.name}: {label}={value} liegt außerhalb von 0…100 — die "
                    "speed_map der config.yaml bildet nur diesen Bereich ab."
                )
        if self.draw_feed and feed > 2500:
            notes.append(
                f"{self.name}: {fmt(feed, 0)} mm/min ist viel für einen Stift — "
                "erst mit --pattern feed-ramp prüfen, ab wann die Riemen springen."
            )
        return notes

    def change_prompt(self, label: str = "") -> str:
        # Der namenlose Vorgabestift trägt nichts bei — dann steht dort nur die Farbe
        name = "" if self.name == "Stift" else self.name
        what = " — ".join(part for part in (label, name) if part)
        return f"Stift wechseln auf: {what or 'Stift'}"


# ---------------------------------------------------------------------------
# Laser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaserToolhead:
    """Laser an einer ``Laser:``-Spindel von FluidNC.

    Vier Dinge macht dieser Kopf anders als der Stift, und alle vier sind
    keine Geschmacksfrage:

    * **M4 statt M3.** Bei dynamischer Leistung regelt die Firmware ``S`` mit
      dem Vorschub mit und schaltet den Strahl im Stillstand ab. Ein
      Seilplotter beschleunigt weich und lange — mit konstanter Leistung (M3)
      brennt jede Ecke durch. M3 gibt es hier nur ausdrücklich über
      ``dynamic=False``, für Fokuspunkte.
    * **Leerwege sind ``G0``.** Im Lasermodus zündet GRBL bei Eilgang nie.
      ``travel_as_g1`` zusammen mit einem Laser ist deshalb kein Kompromiss,
      sondern ein Brandsatz — der Kopf verweigert die Kombination.
    * **Keine Wartezeit.** Was dem Servo Zeit gibt, gibt dem Laser Gelegenheit,
      ein Loch zu bohren.
    * **Leistung in Prozent.** Welcher ``S``-Wert 100 % bedeutet, steht in der
      ``speed_map`` der ``config.yaml`` und ist je nach Aufbau 255 oder 1000.
      Hier wird deshalb in Prozent gerechnet und erst beim Erzeugen auf
      ``s_max`` abgebildet — die häufigste Laserpanne ist ein hart
      verdrahtetes Maximum, das um den Faktor vier danebenliegt.

    Mehrere Durchgänge rollt der Erzeuger aus: GRBL kennt keine Schleifen.
    """

    name: str = "Laser"
    power_pct: float = 20.0
    """Leistung in Prozent von ``s_max``."""

    s_max: int = 1000
    """``S``-Wert für volle Leistung — aus der ``speed_map`` der config.yaml."""

    max_power_pct: float = 100.0
    """Selbstgesetzte Obergrenze; ``power_pct`` darf sie nicht überschreiten."""

    dynamic: bool = True
    """``True`` = ``M4`` (Leistung folgt dem Vorschub), ``False`` = ``M3``."""

    passes: int = 1
    """Wie oft dieselbe Geometrie abgefahren wird."""

    pass_pause_s: float = 0.0
    """Pause zwischen zwei Durchgängen, gegen Wärmestau."""

    draw_feed: float | None = 600.0
    """Zeichenvorschub; für einen Laser deutlich langsamer als für einen Stift."""

    air_assist: str | None = "M8"
    """``M7``/``M8`` für die Luftunterstützung, ``None`` für keine.

    Welcher der beiden Pins die Luft schaltet, legt FluidNC nicht fest — das
    ist Verdrahtungskonvention, deshalb einstellbar. ``M9`` schaltet beide ab.
    """

    air_delay_s: float = 0.5
    """Vorlauf, damit die Luft steht, bevor der Strahl kommt."""

    tool_number: int | None = None
    """``M6 T<n>``, falls in der ``config.yaml`` mehrere Spindeln stehen."""

    color: str = "#c81e1e"
    width_mm: float = 0.2
    note: str = ""

    def __post_init__(self) -> None:
        if not 0 < self.s_max <= 100000:
            raise ToolheadError("s_max muss zwischen 1 und 100000 liegen")
        if not 0 < self.max_power_pct <= 100:
            raise ToolheadError("max_power_pct liegt zwischen 0 und 100")
        if not 0 < self.power_pct <= self.max_power_pct:
            # Kein stilles Zurechtstutzen: zu viel Leistung ist kein Rundungsfehler
            raise ToolheadError(
                f"Leistung {self.power_pct:.1f} % liegt außerhalb von 0 bis "
                f"{self.max_power_pct:.1f} % — Grenze bewusst anheben, wenn das so gewollt ist"
            )
        if self.passes < 1:
            raise ToolheadError("mindestens ein Durchgang")
        if self.draw_feed is not None and self.draw_feed <= 0:
            raise ToolheadError("draw_feed muss größer als 0 sein")
        if self.air_assist not in (None, "M7", "M8"):
            raise ToolheadError("air_assist ist 'M7', 'M8' oder None")
        if self.pass_pause_s < 0:
            raise ToolheadError("pass_pause_s kann nicht negativ sein")

    # -- abgeleitet -------------------------------------------------------

    @property
    def s_value(self) -> int:
        """Der ``S``-Wert, der ``power_pct`` entspricht."""
        return max(0, min(self.s_max, round(self.power_pct / 100.0 * self.s_max)))

    @property
    def mode(self) -> str:
        return "M4" if self.dynamic else "M3"

    # -- Schnittstelle ----------------------------------------------------

    def describe(self) -> str:
        text = (
            f"{self.name}, {fmt(self.power_pct, 1)} % = S{self.s_value} von {self.s_max}, "
            f"{self.mode} {'dynamisch' if self.dynamic else 'konstant'}"
        )
        if self.passes > 1:
            text += f", {self.passes} Durchgänge"
        if self.air_assist:
            text += f", Luft {self.air_assist}"
        return text + (f" ({self.note})" if self.note else "")

    def program_start(self) -> list[str]:
        lines = ["M5 ; Laser aus, bevor irgendetwas fährt", "S0"]
        if self.tool_number is not None:
            lines.append(f"M6 T{self.tool_number} ; Laserspindel wählen")
        if self.air_assist:
            lines.append(f"{self.air_assist} ; Luft an")
            if self.air_delay_s > 0:
                lines.append(f"G4 P{fmt(self.air_delay_s, 2)} ; Luft aufbauen lassen")
        # Leistung 0: der Modusbefehl allein zündet nichts
        lines.append(f"{self.mode} S0 ; Lasermodus, Leistung noch aus")
        return lines

    def program_end(self) -> list[str]:
        lines = ["S0", "M5 ; Laser aus"]
        if self.air_assist:
            lines.append("M9 ; Luft aus")
        return lines

    def engage(self) -> list[str]:
        # Kein G4: eine Wartezeit mit stehendem Strahl brennt ein Loch
        return [f"S{self.s_value}"]

    def retract(self) -> list[str]:
        return ["S0"]

    def cycle_time_s(self) -> float:
        return 0.0

    def between_passes(self) -> list[str]:
        """Was zwischen zwei Durchgängen passiert — Leistung aus, Wärme raus."""
        lines = ["S0"]
        if self.pass_pause_s > 0:
            lines.append(f"G4 P{fmt(self.pass_pause_s, 2)} ; abkühlen lassen")
        return lines

    def feed_for(self, default: float) -> float:
        return self.draw_feed or default

    def check(self, *, travel_as_g1: bool, draw_feed: float) -> list[str]:
        if travel_as_g1:
            # Der einzige harte Riegel im ganzen Modul, und er gehört hierher:
            # ein Leerweg als G1 fährt im Lasermodus mit eingeschaltetem Strahl.
            raise ToolheadError(
                "Laser und travel_as_g1 zusammen wären ein Brandsatz: Leerwege als G1 "
                "fahren mit eingeschaltetem Strahl quer über die Fläche. Im Lasermodus "
                "zündet GRBL bei G0 grundsätzlich nicht — deshalb G0 verwenden."
            )
        notes = [
            "Laser: Schutzbrille für diese Wellenlänge, Absaugung, Feuerlöscher in Reichweite.",
            "Der Strahl trifft hier auf eine senkrechte, offene Wand — kein Gehäuse, "
            "kein Deckel, nichts fängt eine Reflexion ab. Nicht unbeaufsichtigt laufen lassen.",
        ]
        if not self.dynamic:
            notes.append(
                "Konstante Leistung (M3): bei einem Halt bleibt der Strahl an, und ein "
                "Seilplotter beschleunigt langsam — Ecken brennen durch. M4 ist der "
                "Normalfall, M3 nur für Fokuspunkte."
            )
        if self.passes > 1:
            notes.append(
                f"{self.passes} Durchgänge: die geschätzte Laufzeit gilt für alle zusammen."
            )
        return notes

    def change_prompt(self, label: str = "") -> str:
        what = " — ".join(part for part in (label, self.name) if part)
        return f"Werkzeug wechseln auf: {what}"

    # -- Extras -----------------------------------------------------------

    def at_power(self, power_pct: float) -> LaserToolhead:
        """Derselbe Kopf mit anderer Leistung — für den Testrahmen."""
        return replace(self, power_pct=power_pct)


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------

PENS: dict[str, PenToolhead] = {
    "fineliner": PenToolhead(
        name="Fineliner schwarz",
        down_value=30,
        dwell_s=0.25,
        color="#111111",
        width_mm=0.5,
        note="Startwerte, mit pen-test nachziehen",
    ),
    "fineliner-rot": PenToolhead(
        name="Fineliner rot",
        down_value=30,
        dwell_s=0.25,
        color="#e02020",
        width_mm=0.5,
        note="Startwerte, mit pen-test nachziehen",
    ),
    "kugelschreiber": PenToolhead(
        name="Kugelschreiber",
        down_value=38,
        dwell_s=0.2,
        color="#1a3a8f",
        width_mm=0.3,
        draw_feed=1800.0,
        note="braucht Anpressdruck, verträgt Tempo",
    ),
    "marker": PenToolhead(
        name="Marker",
        down_value=34,
        dwell_s=0.35,
        color="#111111",
        width_mm=2.0,
        draw_feed=1200.0,
        note="blutet bei Stillstand — nicht zu langsam fahren",
    ),
    "kreide": PenToolhead(
        name="Kreidemarker",
        down_value=40,
        dwell_s=0.4,
        color="#f5f5f5",
        width_mm=5.0,
        draw_feed=900.0,
        note="für dunkle Wände, hoher Abrieb",
    ),
    "pinsel": PenToolhead(
        name="Pinselstift",
        down_value=26,
        dwell_s=0.5,
        color="#111111",
        width_mm=6.0,
        draw_feed=700.0,
        note="Strichbreite hängt am Druck — langsam fahren",
    ),
}
"""Mitgelieferte Stiftsorten.

Alle Zahlen sind **geschätzte Startwerte**, keine Messwerte: Servohebel,
Federweg und Halter unterscheiden sich an jedem Aufbau. Nachgezogen wird mit
``plot --pattern pen-test``.
"""

TOOLHEADS: dict[str, Toolhead] = {
    **PENS,
    "laser": LaserToolhead(note="ungetestet — vor dem ersten Schuss das Programm lesen"),
}


def toolhead_by_name(name: str) -> Toolhead:
    """Kopf aus dem Katalog holen."""
    try:
        return TOOLHEADS[name]
    except KeyError:
        raise ToolheadError(
            f"Unbekannter Werkzeugkopf {name!r}, bekannt: {', '.join(sorted(TOOLHEADS))}"
        ) from None


def describe_toolheads() -> str:
    """Übersicht für ``--list-toolheads``."""
    lines = []
    for key, head in TOOLHEADS.items():
        lines.append(f"{key:<16} {head.describe()}")
    lines.append("")
    lines.append(
        "Die Stiftwerte sind Startwerte, keine Messwerte — mit `plot --pattern pen-test` nachziehen."
    )
    return "\n".join(lines)


def pen_map(spec: str) -> dict[str, Toolhead]:
    """``"#000000=fineliner,#e02020=marker"`` → Zuordnung Ebene zu Kopf.

    Gedacht fürs mehrfarbige Plotten: welche Strichfarbe im SVG mit welchem
    Stift gezeichnet wird, weiß nur der Mensch vor dem Stiftkasten.
    """
    mapping: dict[str, Toolhead] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        label, _, head = entry.partition("=")
        if not head:
            raise ToolheadError(f"Zuordnung {entry!r} braucht die Form Ebene=Kopf")
        mapping[label.strip()] = toolhead_by_name(head.strip())
    return mapping


def head_for(layer, mapping: dict[str, Toolhead] | None, default: Toolhead) -> Toolhead:
    """Kopf für eine Farbebene: erst über die Beschriftung, dann über die Farbe."""
    if not mapping:
        return default
    return mapping.get(layer.label) or mapping.get(layer.color) or default
