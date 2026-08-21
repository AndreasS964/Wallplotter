"""Linien → GCode im FluidNC/GRBL-Dialekt.

Bewusst schlank und ohne vpype-gcode-Profil-Datei: Der erzeugte Dialekt ist
klein genug, um ihn direkt zu kontrollieren — ``G0``/``G1`` fürs Fahren, dazu
die Grundeinstellungen und das Programmende.

Was das *Werkzeug* tut, steht hier nicht mehr: Servo senken, Laser
einschalten, Luft zuschalten liefert der Kopf aus :mod:`wallplotter.toolhead`.
Dieses Modul kennt nur die Stellen, an denen so etwas passieren muss — vor
jedem Leerweg ausrücken, danach einrücken —, und ein Test hält am
Modulquelltext fest, dass es dabei bleibt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from .config import PlotConfig
from .geometry import (
    Line,
    Lines,
    draw_length,
    fit_to_area,
    flip_y,
    transform,
    travel_length,
)
from .timing import plot_duration_s
from .toolhead import Toolhead, fmt, head_for

__all__ = [
    "lines_to_gcode",
    "layers_to_gcode",
    "geometry_to_gcode",
    "prepare_geometry",
    "PlotStats",
    "stats_for",
    "comment_lines",
    "MAX_LINE_BYTES",
]


def prepare_geometry(
    lines: Sequence[Line],
    config: PlotConfig | None = None,
    *,
    fit: bool = True,
    invert_y: bool | None = None,
    apply_origin: bool = True,
    correction=None,
    fit_bounds: tuple[float, float, float, float] | None = None,
) -> Lines:
    """Rohlinien in Plot-Geometrie überführen: einpassen, spiegeln, versetzen.

    Damit rechnen GCode-Export, Statistik und Vorschau auf derselben Geometrie
    — sonst weicht z. B. die geschätzte Plotdauer von der tatsächlichen ab.
    ``correction`` verzerrt zum Schluss gegen bekannte Maschinenfehler vor.

    Für die Vorschau ``invert_y=False`` und ``apply_origin=False`` setzen: SVG
    hat wie die Zeichnung den Ursprung oben links, und der Versatz der Fläche
    in Maschinenkoordinaten interessiert dort nicht.

    ``fit_bounds`` gibt die Ausgangs-Bounding-Box vor, statt sie aus ``lines``
    zu nehmen — so wird jede Farbebene mit derselben Abbildung eingepasst wie
    die Zeichnung als Ganzes (siehe :func:`layers_to_gcode`).
    """
    cfg = config or PlotConfig()
    geometry: Lines = [list(line) for line in lines if len(line) >= 2]
    if fit:
        geometry = fit_to_area(
            geometry,
            cfg.width_mm,
            cfg.height_mm,
            cfg.margin_mm,
            center=True,
            source_bounds=fit_bounds,
        )
    if cfg.invert_y if invert_y is None else invert_y:
        geometry = flip_y(geometry, cfg.height_mm)
    if apply_origin and (cfg.origin_x_mm or cfg.origin_y_mm):
        geometry = transform(geometry, 1.0, cfg.origin_x_mm, cfg.origin_y_mm)
    if correction is not None:
        # zuletzt: die Vorverzerrung rechnet in Maschinenkoordinaten
        geometry = correction.apply_lines(geometry)
    return geometry


_fmt = fmt
"""Koordinaten formatiert jetzt :mod:`wallplotter.toolhead` — beide Seiten
müssen dieselben Zahlen schreiben, also gibt es nur eine Fassung davon."""

MAX_LINE_BYTES = 127
"""Was FluidNC je Zeile annimmt — Kommentare eingerechnet.

``gc_execute_line()`` liest in einen ``char line[128]`` und lehnt vorher alles
Längere mit ``Error::LineLengthExceeded`` ab. Die Prüfung greift **vor** dem
Entfernen der Kommentare, ein langes ``;``-Kommentar zählt also mit. Und die
Folge ist kein Alarm, sondern ``Job::abort()``: der Lauf stirbt mitten im Bild
mit ``error:14`` im Log. Gezählt werden Bytes, nicht Zeichen — ein Umlaut
kostet zwei.
"""


def comment_lines(text: str, prefix: str = "; ") -> list[str]:
    """Kommentartext auf mehrere Zeilen umbrechen, keine über 127 Byte.

    Wortweise, damit die Meldung lesbar bleibt; ein einzelnes Wort, das allein
    schon zu lang ist, wird hart getrennt.
    """
    budget = MAX_LINE_BYTES - len(prefix.encode("utf-8"))
    lines: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            lines.append(prefix + current)
            current = ""

    for word in text.split():
        while len(word.encode("utf-8")) > budget:
            head = word
            while len(head.encode("utf-8")) > budget:
                head = head[:-1]
            flush()
            lines.append(prefix + head)
            word = word[len(head) :]
        candidate = f"{current} {word}" if current else word
        if len(candidate.encode("utf-8")) > budget:
            flush()
            current = word
        else:
            current = candidate
    flush()
    return lines or [prefix.rstrip()]


class PlotStats:
    """Kennzahlen eines Plots, für CLI-Ausgabe und Web-UI."""

    def __init__(
        self,
        lines: Sequence[Line],
        config: PlotConfig,
        feeds: Sequence[float] | None = None,
        *,
        park: bool = True,
    ) -> None:
        """``park=False`` für einen Block, dessen GCode nicht zum Ausgangspunkt
        zurückfährt — eine Zwischenebene in :func:`_program_with_pauses`."""
        head = config.toolhead
        self.line_count = len(lines)
        self.point_count = sum(len(line) for line in lines)
        self.passes = max(1, head.passes)
        self.draw_mm = draw_length(lines) * self.passes
        origin = (config.origin_x_mm, config.origin_y_mm)
        # Auch die Anfahrten fallen je Durchgang neu an. Gezählt wird ab dem
        # Ort, an dem das Programm tatsächlich parkt bzw. losfährt — nicht ab
        # Maschinen-(0,0), das nur vor dem Einmessen mit der Fläche zusammenfällt.
        self.travel_mm = travel_length(lines, start=origin, return_to_start=park) * self.passes
        # Was das Werkzeug je Linie kostet: beim Stift zweimal Servo-Wartezeit,
        # beim Laser nichts. Bei einem Punktraster ist das nicht die
        # Nachkommastelle, sondern der Löwenanteil — 5000 Punkte × 0,5 s sind
        # gut 40 Minuten, die eine reine Wegschätzung unterschlägt.
        self.pen_s = self.line_count * max(0.0, head.cycle_time_s()) * self.passes
        # Mit Beschleunigungsprofil statt Weg durch Tempo — bei kurzen Strichen
        # ist das der Unterschied zwischen sieben und einundzwanzig Minuten.
        #
        # Der Leerweg-Vorschub hängt daran, was tatsächlich ausgegeben wird:
        # `G0` fährt Eilgang, nicht `travel_feed`. Nur mit `--travel-as-g1`
        # steht der eingestellte Wert wirklich in der Datei.
        travel_feed = (
            config.travel_feed if config.travel_as_g1 else config.limits.max_rate_mm_min
        )
        drawable = [line for line in lines if len(line) >= 2]
        self.motion_s = (
            plot_duration_s(
                drawable,
                head.feed_for(config.draw_feed),
                travel_feed,
                config.limits,
                start=origin,
                park=park,
                draw_feeds=list(feeds) if feeds is not None else None,
            )
            * self.passes
        )
        self.duration_s = self.motion_s + self.pen_s

    def as_dict(self) -> dict[str, float | int]:
        return {
            "line_count": self.line_count,
            "point_count": self.point_count,
            "draw_mm": round(self.draw_mm, 1),
            "travel_mm": round(self.travel_mm, 1),
            "motion_s": round(self.motion_s, 1),
            "pen_s": round(self.pen_s, 1),
            "passes": self.passes,
            "duration_s": round(self.duration_s, 1),
        }

    def __str__(self) -> str:
        text = (
            f"{self.line_count} Linien, {self.point_count} Punkte, "
            f"{self.draw_mm / 1000:.1f} m zeichnen + {self.travel_mm / 1000:.1f} m "
            f"Leerweg, geschätzt {self.duration_s / 60:.0f} min"
        )
        if self.passes > 1:
            text += f" ({self.passes} Durchgänge)"
        # nur ausweisen, wenn es weh tut — sonst rauscht die Zeile zu
        if self.pen_s > 0.1 * self.duration_s:
            text += f" (davon {self.pen_s / 60:.0f} min Werkzeughübe)"
        return text


def stats_for(
    lines: Sequence[Line],
    config: PlotConfig | None = None,
    feeds: Sequence[float] | None = None,
) -> PlotStats:
    return PlotStats(lines, config or PlotConfig(), feeds)


def lines_to_gcode(
    lines: Sequence[Line],
    config: PlotConfig | None = None,
    *,
    fit: bool = True,
    header_comment: str | None = None,
    feeds: Sequence[float] | None = None,
    correction=None,
) -> str:
    """Linien (in mm) in ein vollständiges GCode-Programm übersetzen.

    Mit ``fit=True`` (Standard) wird die Zeichnung proportional in die
    bezeichnete Fläche abzüglich Rand eingepasst. Ist die Geometrie bereits in
    Flächenkoordinaten (etwa ein Testmuster), ``fit=False`` setzen.

    ``feeds`` setzt den Vorschub je Linie statt für alle gleich — gedacht für
    den Vorschub-Test aus :mod:`wallplotter.patterns`.

    ``correction`` verzerrt die Geometrie zum Schluss gegen die bekannten
    Maschinenfehler vor (siehe :mod:`wallplotter.correction`).
    """
    cfg = config or PlotConfig()
    if feeds is not None and len(feeds) != len([line for line in lines if len(line) >= 2]):
        raise ValueError("feeds muss genauso viele Einträge haben wie es Linien gibt")
    geometry = prepare_geometry(lines, cfg, fit=fit, correction=correction)
    return _program(geometry, cfg, header=header_comment, feeds=feeds)


def geometry_to_gcode(
    geometry: Lines,
    config: PlotConfig | None = None,
    *,
    header: str | None = None,
    feeds: Sequence[float] | None = None,
) -> str:
    """GCode aus bereits fertiger Maschinengeometrie.

    Der Weg für alles, was zwischen Einpassen und Ausgabe noch etwas mit der
    Geometrie vorhat — etwa den Vorschub je Linie an die örtliche Kondition
    der Kinematik anzupassen (:func:`wallplotter.motion.conditioning_feeds`).
    Sonst müsste dieselbe Geometrie zweimal gerechnet werden, einmal für die
    Vorschübe und einmal für die Ausgabe, und beide könnten auseinanderlaufen.
    """
    cfg = config or PlotConfig()
    if feeds is not None and len(feeds) != len(geometry):
        raise ValueError("feeds muss genauso viele Einträge haben wie es Linien gibt")
    return _program(geometry, cfg, header=header, feeds=feeds)


def _program(
    geometry: Lines,
    cfg: PlotConfig,
    *,
    header: str | None = None,
    feeds: Sequence[float] | None = None,
    toolhead: Toolhead | None = None,
    include_setup: bool = True,
    include_end: bool = True,
) -> str:
    """GCode-Programm aus fertiger Maschinengeometrie.

    ``include_setup`` und ``include_end`` schalten Grundeinstellungen
    (``G21``/``G90``/``G17``) und Abschluss (Parkfahrt, ``M2``) ab — gebraucht
    von :func:`_program_with_pauses`, das mehrere Blöcke aneinanderhängt. Was
    dabei *nicht* wegfällt, ist der Vor- und Nachspann des Werkzeugs: nach
    einem Wechsel muss der neue Kopf hochgefahren werden, und vor der Pause
    muss der alte aus sein.

    Hier steht nur noch Bewegung. Was das Werkzeug tut — Servo senken, Laser
    einschalten, Messer eintauchen — liefert der Kopf selbst; ``M3``, ``M5``
    und ``G4`` kommen in dieser Funktion nicht mehr vor, und ein Test hält das
    am Modulquelltext fest.
    """
    head = toolhead or cfg.toolhead
    # Der Kopf darf die Konfiguration ablehnen. Beim Laser ist das kein
    # Formalismus: mit travel_as_g1 führe der Strahl über jeden Leerweg.
    # Die Einwände, die kein Abbruch sind, gehören in die Datei — dort liest
    # sie noch jemand, wenn die Meldung auf der Konsole längst weg ist.
    concerns = head.check(travel_as_g1=cfg.travel_as_g1, draw_feed=cfg.draw_feed)

    stats = PlotStats(
        geometry, cfg if toolhead is None else replace(cfg, toolhead=head), feeds, park=include_end
    )
    out: list[str] = []

    if header:
        out += comment_lines(header)
    out.append("; erzeugt mit wallplotter")
    out.append(f"; Flaeche {_fmt(cfg.width_mm)} x {_fmt(cfg.height_mm)} mm, Rand {_fmt(cfg.margin_mm)} mm")
    out += comment_lines(f"Werkzeug: {head.describe()}")
    for note in concerns:
        out += comment_lines(f"ACHTUNG: {note}")
    out += comment_lines(str(stats))
    if include_setup:
        out.append("G21 ; Millimeter")
        out.append("G90 ; absolute Koordinaten")
        out.append("G17 ; XY-Ebene")
    out.extend(head.program_start())

    travel_cmd = f"G1 F{_fmt(cfg.travel_feed, 1)}" if cfg.travel_as_g1 else "G0"
    default_feed = head.feed_for(cfg.draw_feed)
    passes = max(1, head.passes)

    for run in range(passes):
        if passes > 1:
            out.append(f"; Durchgang {run + 1}/{passes}")
        engaged = False
        for index, line in enumerate(geometry):
            feed = feeds[index] if feeds is not None else default_feed
            start_x, start_y = line[0]
            if engaged:
                out.extend(head.retract())
                engaged = False
            out.append(f"{travel_cmd} X{_fmt(start_x)} Y{_fmt(start_y)}")
            out.extend(head.engage())
            engaged = True

            out.append(f"G1 X{_fmt(line[1][0])} Y{_fmt(line[1][1])} F{_fmt(feed, 1)}")
            for x, y in line[2:]:
                out.append(f"G1 X{_fmt(x)} Y{_fmt(y)}")

        if engaged:
            out.extend(head.retract())
        if run < passes - 1:
            out.extend(head.between_passes())

    out.extend(head.program_end())
    if include_end:
        # Parken an der unteren linken Ecke der Zeichenfläche, nicht auf
        # Maschinen-(0,0). Bei kalibrierter Fläche liegt der Maschinennullpunkt
        # dort, wo die Gondel beim Referenzieren hing — also meist *über* der
        # Fläche, im Bereich der schlechtesten Riemengeometrie (Winkel nahe
        # 180°, 11 bis 19 N Zug). Jedes Programm dorthin enden zu lassen, war
        # die eine Fahrt, die keine Prüfung je angesehen hat.
        out.append(f"{travel_cmd} X{_fmt(cfg.origin_x_mm)} Y{_fmt(cfg.origin_y_mm)}")
        out.append("M2 ; Programmende")
    return "\n".join(out) + "\n"


def _program_with_pauses(blocks: Sequence[tuple[str, str, Lines, Toolhead]], cfg: PlotConfig) -> str:
    """Alle Ebenen in einem Programm, dazwischen Halt zum Werkzeugwechsel.

    Zusammengesetzt über die Schalter von :func:`_program`, nicht durch
    Herausfiltern von Zeilen aus fertigen Programmen. Das war vorher anders und
    hatte eine Nebenwirkung, die man erst an der Wand sieht: die abschließende
    Parkfahrt blieb stehen, und die Gondel fuhr vor jedem Stiftwechsel quer
    über die Fläche zum Nullpunkt und wieder zurück.
    """
    parts = []
    for position, (header, _, geometry, head) in enumerate(blocks):
        last = position == len(blocks) - 1
        body = _program(
            geometry,
            cfg,
            header=header,
            toolhead=head,
            include_setup=position == 0,
            include_end=last,
        ).splitlines()
        if not last:
            # Den Wechseltext formuliert der nächste Kopf: „Stift wechseln auf"
            # stimmt nicht mehr, sobald einer davon ein Laser ist.
            #
            # `(MSG,…)` statt `;`: Ein Semikolon-Kommentar verschwindet beim
            # Einlesen spurlos, der Mensch vor der Wand sieht nur eine Maschine,
            # die grundlos steht. Klammerkommentare mit MSG protokolliert
            # FluidNC dagegen (`gcode_comment_msg` -> `log_info("MSG," …)`),
            # sie stehen also in der Konsole des WebUI.
            next_label, next_head = blocks[position + 1][1], blocks[position + 1][3]
            prompt = next_head.change_prompt(next_label)
            weiter = "weiter mit Cycle Start"
            line = f"M0 (MSG,{prompt} — {weiter})"
            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                room = MAX_LINE_BYTES - len(f"M0 (MSG,… — {weiter})".encode())
                while len(prompt.encode("utf-8")) > room:
                    prompt = prompt[:-1]
                line = f"M0 (MSG,{prompt}… — {weiter})"
            body.append(line)
        parts.append("\n".join(body))
    return "\n".join(parts) + "\n"


def layers_to_gcode(
    layers: Sequence,
    config: PlotConfig | None = None,
    *,
    separate: bool = True,
    fit: bool = True,
    correction=None,
    tools: Mapping[str, Toolhead] | None = None,
) -> dict[str, str] | str:
    """Mehrfarbige Zeichnung in GCode übersetzen — eine Ebene je Stiftfarbe.

    Mit ``separate=True`` (Standard) kommt je Ebene eine eigene Datei heraus.
    Das ist für mehrstündige Plots das Vernünftige: man plottet Schwarz heute,
    Rot morgen, und muss nicht danebenstehen.

    Mit ``separate=False`` entsteht ein einziges Programm, das zwischen den
    Farben mit ``M0`` anhält. Bequemer, aber es setzt voraus, dass die
    Firmware ``M0`` als Pause versteht und jemand zum Weiterdrücken da ist.

    ``tools`` ordnet den Ebenen Werkzeugköpfe zu, angesprochen über die
    Beschriftung oder die Farbe. Damit bekommt jede Farbe ihren eigenen Stift
    samt Servo-Werten und Vorschub — ein Pinselstift will nun einmal langsamer
    geführt werden als ein Fineliner. Ohne Zuordnung nimmt jede Ebene den Kopf
    aus der Konfiguration.

    Wichtig für die Passgenauigkeit: Alle Ebenen werden *gemeinsam*
    eingepasst, nicht jede für sich — sonst zöge jede Farbe ihre eigene
    Skalierung und die Zeichnung fiele auseinander.
    """
    cfg = config or PlotConfig()
    entries = [layer for layer in layers if any(len(line) >= 2 for line in layer.lines)]
    if not entries:
        return {} if separate else ""

    # Gemeinsame Einpassung: die Grenzen über *alle* Ebenen bestimmen und jeder
    # Ebene als Bezug mitgeben. Die Ebene durchläuft danach dieselbe Kette wie
    # eine einfarbige Zeichnung — nur eben mit fremder Bounding-Box.
    combined = [line for layer in entries for line in layer.lines if len(line) >= 2]
    fit_bounds = _bounds(combined) if fit else None

    def geometry_of(layer):
        return prepare_geometry(
            layer.lines, cfg, fit=fit, correction=correction, fit_bounds=fit_bounds
        )

    def head_of(layer) -> Toolhead:
        return head_for(layer, tools, cfg.toolhead)

    if separate:
        result: dict[str, str] = {}
        for position, layer in enumerate(entries, start=1):
            head = head_of(layer)
            result[layer.label] = _program(
                geometry_of(layer),
                cfg,
                header=f"Ebene {position}/{len(entries)}: {layer.label} — {head.name}",
                toolhead=head,
            )
        return result

    blocks = [
        (
            f"Ebene {position}/{len(entries)}: {layer.label} — {head_of(layer).name}",
            layer.label,
            geometry_of(layer),
            head_of(layer),
        )
        for position, layer in enumerate(entries, start=1)
    ]
    return _program_with_pauses(blocks, cfg)


def _bounds(lines: Sequence[Line]) -> tuple[float, float, float, float]:
    xs = [x for line in lines for x, _ in line]
    ys = [y for line in lines for _, y in line]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
