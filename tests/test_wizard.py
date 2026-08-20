"""Die geführte Einrichtung — gegen ein Skript aus Antworten und ein Fake-Board.

Ein Wizard, der nur mit einem Menschen davor prüfbar ist, wird nicht geprüft.
Deshalb redet der Ablauf über :class:`~wallplotter.wizard.Dialog` mit der Welt,
und hier steht ein Dialog, der vorbereitete Antworten ausspuckt und alle Fragen
mitschreibt.

:class:`SkriptDialog` ist absichtlich streng: Geht dem Skript die Antwort aus,
bricht der Test ab und **nennt die unbeantwortete Frage**. Damit fällt jede
Änderung am Ablauf hier auf, statt still eine Vorgabe zu nehmen.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fluidnc_fake import FakeFluidNCSocket, FakeSession, opener_for  # noqa: E402
from wallplotter import (
    wizard,  # noqa: E402
    wizard_cli,  # noqa: E402
)
from wallplotter.location import LocationBook, ServoSettings  # noqa: E402
from wallplotter.upload import FluidNCClient, TelnetChannel  # noqa: E402
from wallplotter.wizard import ERLEDIGT, UNBEKANNT, Abbruch, Kontext  # noqa: E402

yaml = pytest.importorskip("yaml")


# ---------------------------------------------------------------------------
# Werkzeug
# ---------------------------------------------------------------------------


class SkriptDialog:
    """Antwortet der Reihe nach aus einer Liste und schreibt alles mit."""

    def __init__(self, antworten: list) -> None:
        self.antworten = list(antworten)
        self.protokoll: list[tuple[str, str, object]] = []
        self.gesagt: list[str] = []
        self.gewarnt: list[str] = []

    def _naechste(self, art: str, text: str):
        if not self.antworten:
            raise AssertionError(
                f"Das Skript ist zu Ende, aber der Wizard fragt weiter: {art}({text!r})"
            )
        antwort = self.antworten.pop(0)
        self.protokoll.append((art, text, antwort))
        return antwort

    def sagen(self, text: str) -> None:
        self.gesagt.append(text)

    def warnen(self, text: str) -> None:
        self.gewarnt.append(text)

    def frage(self, text: str, vorgabe: str = "") -> str:
        return str(self._naechste("frage", text))

    def zahl(self, text: str, vorgabe=None, einheit: str = "mm") -> float:
        return float(self._naechste("zahl", text))

    def ja(self, text: str, vorgabe: bool = True) -> bool:
        return bool(self._naechste("ja", text))

    def auswahl(self, text: str, optionen) -> str:
        return str(self._naechste("auswahl", text))

    def weiter(self, text: str) -> None:
        self._naechste("weiter", text)

    # -- Auswertung -------------------------------------------------------

    @property
    def text(self) -> str:
        return "\n".join(self.gesagt + self.gewarnt)

    def fragen(self, art: str) -> list[str]:
        return [t for a, t, _ in self.protokoll if a == art]


class Fahrt:
    """Die Stellen, an denen die Gondel nacheinander steht.

    Der Zähler gehört bewusst hierher und nicht in den Socket: Der Wizard baut
    für jeden Schritt eine frische Verbindung auf, und eine Maschine vergisst
    beim Verbindungsabbruch nicht, wo sie steht.
    """

    def __init__(self, positionen: list[tuple[float, float]]) -> None:
        self.positionen = list(positionen)
        self.index = 0

    def naechste(self) -> tuple[float, float]:
        wert = self.positionen[min(self.index, len(self.positionen) - 1)]
        self.index += 1
        return wert


class FahrenderSocket(FakeFluidNCSocket):
    """Ein Board, das an verschiedenen Stellen steht.

    Die Attrappe aus ``fluidnc_fake`` meldet immer dieselbe Position. Zum
    Einmessen einer Fläche ist das genau der Fall, der nichts prüft: Vier Ecken
    am selben Punkt ergeben ein Rechteck der Größe null.
    """

    def __init__(self, fahrt: Fahrt) -> None:
        super().__init__()
        self.fahrt = fahrt

    def sendall(self, data: bytes) -> None:
        for byte in data:
            if byte == 0x3F:  # '?'
                x, y = self.fahrt.naechste()
                self.status_line = (
                    f"<Idle|MPos:{x:.3f},{y:.3f},0.000|FS:0,0|WCO:0.000,0.000,0.000>"
                )
        super().sendall(data)


ECKEN = [(-800.0, -2000.0), (800.0, -2000.0), (800.0, -300.0), (-800.0, -300.0)]

# Die Reihenfolge der Statusabfragen im vollen Durchlauf: einmal beim Verbinden,
# einmal beim Nullpunkt (dort MUSS 0/0 stehen, sonst schlägt der Schritt an),
# dann je Ecke einmal.
FAHRWEG = [(0.0, 0.0), (0.0, 0.0), *ECKEN]


def board(positionen=None, mpos=(0.0, 0.0)):
    """Kontext-Fabrik plus die Attrappen, auf die man hinterher schaut."""
    session = FakeSession()
    sockets: list[FakeFluidNCSocket] = []

    fahrt = Fahrt(positionen) if positionen is not None else None

    def factory(config):
        if fahrt is not None:
            sock = FahrenderSocket(fahrt)
        else:
            sock = FakeFluidNCSocket(
                f"<Idle|MPos:{mpos[0]:.3f},{mpos[1]:.3f},0.000|FS:0,0|WCO:0.000,0.000,0.000>"
            )
        sockets.append(sock)
        channel = TelnetChannel(config.hostname, config.telnet_port, 5.0, opener_for(sock))
        return FluidNCClient(config, session, channel)

    return factory, session, sockets


def kontext(tmp_path, factory=None, **kwargs) -> Kontext:
    return Kontext(
        host="wandplotter.local",
        standorte=tmp_path / "standorte.json",
        firmware_datei=tmp_path / "config" / "fluidnc-wallplotter.yaml",
        arbeitsverzeichnis=tmp_path,
        client_factory=factory,
        **kwargs,
    )


# Die Antworten für einen vollständigen Durchlauf. Reihenfolge = Fragenfolge.
VOLLSTAENDIG = [
    "wandplotter.local",  # board: Hostname
    None,                 # standort: weiter (Gondel hängt, Board neu gestartet)
    "Keller",             # standort: Name
    2300, 1450, 1470,     # standort: die drei Maße
    True,                 # firmware: übertragen?
    None,                 # firmware: weiter (neu gestartet)
    None,                 # nullpunkt: weiter
    "hier", "hier", "hier", "hier",   # flaeche: vier Ecken, Gondel steht jeweils schon dort
    30, 0, 0.25,          # servo: unten, oben, Wartezeit
    True,                 # servo: jetzt plotten?
    None,                 # servo: weiter (Muster fertig)
    True,                 # servo: passt so?
    True,                 # probe: Rahmen plotten?
]


# ---------------------------------------------------------------------------
# Der ganze Weg
# ---------------------------------------------------------------------------


def test_ein_durchlauf_richtet_die_maschine_vollstaendig_ein(tmp_path):
    factory, session, sockets = board(positionen=FAHRWEG)
    ctx = kontext(tmp_path, factory)
    dialog = SkriptDialog(VOLLSTAENDIG)

    assert wizard.lauf(ctx, dialog) == 0
    assert dialog.antworten == [], f"unbenutzte Antworten: {dialog.antworten}"

    standort = LocationBook.load(ctx.standorte).get()
    assert standort.name == "Keller"
    assert standort.anchor_span_mm == 2300
    assert standort.calibration.complete
    assert standort.servo == ServoSettings(down_value=30, up_value=0, dwell_s=0.25)

    # config.yaml liegt auf der Platte UND im Flash des Boards — nicht auf der Karte
    assert ctx.firmware_datei.exists()
    assert session.flash["/config.yaml"] == ctx.firmware_datei.read_text(encoding="utf-8")
    assert "/config.yaml" not in session.card

    # und die Ankermaße darin sind die gemessenen
    wall = yaml.safe_load(session.flash["/config.yaml"])["kinematics"]["WallPlotter"]
    anker = standort.anchors()
    assert wall["left_anchor_x"] == pytest.approx(anker.left_x, abs=1e-3)

    # zwei GCode-Dateien auf der Karte, beide gestartet
    assert set(session.card) == {"/stifttest.gcode", "/rahmen.gcode"}
    gestartet = [z for sock in sockets for z in sock.lines if z.startswith("$SD/Run=")]
    assert gestartet == ["$SD/Run=/stifttest.gcode", "$SD/Run=/rahmen.gcode"]

    # und das Board wurde nach der neuen Konfiguration neu gestartet
    assert any("$Bye" in sock.lines for sock in sockets)


def test_nach_dem_durchlauf_ist_nichts_mehr_offen(tmp_path):
    factory, _session, _sockets = board(positionen=FAHRWEG)
    ctx = kontext(tmp_path, factory)
    assert wizard.lauf(ctx, SkriptDialog(VOLLSTAENDIG)) == 0

    zustaende = {s.key: s.zustand(ctx) for s in wizard.SCHRITTE}
    assert zustaende["standort"] == ERLEDIGT
    assert zustaende["flaeche"] == ERLEDIGT
    assert zustaende["servo"] == ERLEDIGT
    # Was keine Software wissen kann, behauptet auch keine
    assert zustaende["nullpunkt"] == UNBEKANNT
    assert zustaende["probe"] == UNBEKANNT


def test_ein_zweiter_lauf_ueberspringt_das_erledigte(tmp_path):
    """Fortsetzen heißt fortsetzen, nicht von vorn."""
    factory, _session, _sockets = board(positionen=FAHRWEG)
    ctx = kontext(tmp_path, factory)
    wizard.lauf(ctx, SkriptDialog(VOLLSTAENDIG))

    # Zweiter Lauf am selben Aufbau: Gondel hängt wieder am Referenzpunkt.
    factory2, _s2, _so2 = board(positionen=[(0.0, 0.0)])
    zweiter = SkriptDialog(
        [
            "wandplotter.local",  # board
            True,                 # firmware: übertragen? (Board kennt seinen Stand nicht)
            None,                 # firmware: weiter
            None,                 # nullpunkt: weiter
            True,                 # probe: plotten?
        ]
    )
    ctx2 = kontext(tmp_path, factory2)
    assert wizard.lauf(ctx2, zweiter) == 0
    # Standort, Fläche und Servo werden nicht noch einmal erfragt
    assert "Name für diesen Standort" not in zweiter.fragen("frage")
    assert zweiter.fragen("auswahl") == []


# ---------------------------------------------------------------------------
# Ohne Board
# ---------------------------------------------------------------------------


def test_ohne_board_geht_die_vorbereitung_trotzdem(tmp_path):
    """Messen, rechnen und die config.yaml schreiben braucht keine Hardware."""

    def kaputte_factory(config):
        session = FakeSession()

        def opener(address, timeout=None):
            raise OSError("kein Board")

        channel = TelnetChannel(config.hostname, config.telnet_port, 0.1, opener)
        return FluidNCClient(config, session, channel)

    ctx = kontext(tmp_path, kaputte_factory)
    dialog = SkriptDialog(
        [
            "wandplotter.local",  # board: Hostname
            True,                 # board: ohne Board weitermachen?
            None,                 # standort: weiter
            "Werkstatt",          # standort: Name
            1800, 1200, 1200,     # standort: Maße
        ]
    )

    # Kein Abbruch: Was ohne Maschine geht, wird erledigt; der Rest steht am Ende
    # als Liste da statt als Fehlermeldung pro Schritt.
    assert wizard.lauf(ctx, dialog) == 0
    assert ctx.board_erreichbar is False
    assert ctx.firmware_datei.exists()
    assert LocationBook.load(ctx.standorte).get().name == "Werkstatt"
    assert any("push" in notiz for notiz in ctx.notizen)

    offen = " | ".join(ctx.notizen)
    for erwartet in ("--ab flaeche", "--ab servo", "--ab probe"):
        assert erwartet in offen


def test_ohne_board_wird_die_datei_trotzdem_geprueft(tmp_path):
    """Die erzeugte Datei muss auch offline gegen FluidNC gehalten werden."""
    from wallplotter.fluidnc_schema import ERROR, check_lines, check_mapping

    def kaputte_factory(config):
        session = FakeSession()

        def opener(address, timeout=None):
            raise OSError("kein Board")

        return FluidNCClient(
            config, session, TelnetChannel(config.hostname, 23, 0.1, opener)
        )

    ctx = kontext(tmp_path, kaputte_factory)
    wizard.lauf(
        ctx,
        SkriptDialog(["wandplotter.local", True, None, "W", 1800, 1200, 1200]),
    )
    text = ctx.firmware_datei.read_text(encoding="utf-8")
    befunde = list(check_lines(text)) + list(check_mapping(yaml.safe_load(text)))
    assert [str(f) for f in befunde if f.level == ERROR] == []


# ---------------------------------------------------------------------------
# Die Stellen, an denen es schiefgeht
# ---------------------------------------------------------------------------


def test_unmoegliche_masse_werden_zurueckgewiesen_und_neu_erfragt(tmp_path):
    """Riemen, die kein Dreieck ergeben, sind ein Messfehler — kein Standort."""
    factory, _s, _so = board()
    ctx = kontext(tmp_path, factory)
    dialog = SkriptDialog(
        [
            None, "Keller",
            2300, 400, 400,   # 400 + 400 < 2300 — die Riemen treffen sich nie
            True,             # noch einmal messen?
            2300, 1450, 1470,  # und jetzt richtig
        ]
    )
    assert wizard.schritt_nach_name("standort").ausfuehren(ctx, dialog) is True
    assert any("Dreieck" in w for w in dialog.gewarnt)
    assert LocationBook.load(ctx.standorte).get().left_belt_zero_mm == 1450


def test_wer_die_masse_nicht_korrigieren_will_bricht_ab(tmp_path):
    factory, _s, _so = board()
    ctx = kontext(tmp_path, factory)
    dialog = SkriptDialog([None, "Keller", 2300, 400, 400, False])
    with pytest.raises(Abbruch):
        wizard.schritt_nach_name("standort").ausfuehren(ctx, dialog)


def test_der_standortschritt_besteht_auf_dem_neustart_vor_dem_messen(tmp_path):
    """Der Fehler, der eine Zeichnung lautlos verzerrt.

    FluidNC friert die Riemenlängen für (0,0) beim Start ein. Wer erst joggt und
    dann nullt, misst Ankermaße, die um die Jog-Strecke danebenliegen.
    """
    factory, _s, _so = board()
    ctx = kontext(tmp_path, factory)
    dialog = SkriptDialog([None, "Keller", 2300, 1450, 1470])
    wizard.schritt_nach_name("standort").ausfuehren(ctx, dialog)

    gefragt = " ".join(dialog.fragen("weiter"))
    assert "neu gestartet" in gefragt
    assert "eingefroren" in dialog.text or "friert" in dialog.text


def test_ein_nullpunkt_neben_null_wird_gemeldet(tmp_path):
    """Steht MPos nicht auf 0/0, wurde nicht am Referenzpunkt neu gestartet."""
    factory, _s, _so = board(mpos=(430.0, -120.0))
    ctx = kontext(tmp_path, factory)
    ctx.board_erreichbar = True
    dialog = SkriptDialog([None, False])  # weiter, dann: trotzdem weiter? nein

    assert wizard.schritt_nach_name("nullpunkt").ausfuehren(ctx, dialog) is False
    assert any("430" in w for w in dialog.gewarnt)


def test_eine_flache_aufhaengung_wird_gemeldet(tmp_path):
    """Anker dicht über dem Nullpunkt heißt: oben laufen die Riemen fast in eine Linie."""
    factory, _s, _so = board()
    ctx = kontext(tmp_path, factory)
    dialog = SkriptDialog([None, "Flach", 2000, 1010, 1010])
    wizard.schritt_nach_name("standort").ausfuehren(ctx, dialog)
    assert any("flach" in w.lower() for w in dialog.gewarnt)


def test_die_flaeche_bleibt_offen_wenn_ecken_fehlen(tmp_path):
    factory, _s, _so = board(positionen=FAHRWEG)
    ctx = kontext(tmp_path, factory)
    ctx.board_erreichbar = True
    wizard.schritt_nach_name("standort").ausfuehren(
        ctx, SkriptDialog([None, "Keller", 2300, 1450, 1470])
    )
    dialog = SkriptDialog(["hier", "weiter", "weiter", "weiter"])
    assert wizard.schritt_nach_name("flaeche").ausfuehren(ctx, dialog) is False
    assert any("fehlen" in w for w in dialog.gewarnt)


def test_der_servoschritt_laesst_die_werte_wiederholen(tmp_path):
    """Beim ersten Muster passt es selten. Also noch einmal, ohne von vorn."""
    factory, _s, _so = board(positionen=FAHRWEG)
    ctx = kontext(tmp_path, factory)
    assert wizard.lauf(ctx, SkriptDialog(VOLLSTAENDIG)) == 0

    dialog = SkriptDialog(
        [
            40, 0, 0.3, True, None, False,   # erster Versuch — passt nicht
            34, 2, 0.4, True, None, True,    # zweiter — passt
        ]
    )
    assert wizard.schritt_nach_name("servo").ausfuehren(ctx, dialog) is True
    assert LocationBook.load(ctx.standorte).get().servo == ServoSettings(34, 2, 0.4)


def test_ein_s_wert_ausserhalb_der_speed_map_wird_zurueckgewiesen(tmp_path):
    """Die speed_map der erzeugten config.yaml bildet 0…100 ab — mehr geht nicht."""
    factory, _s, _so = board(positionen=FAHRWEG)
    ctx = kontext(tmp_path, factory)
    wizard.lauf(ctx, SkriptDialog(VOLLSTAENDIG))

    dialog = SkriptDialog([140, 0, 0.25, 30, 0, 0.25, True, None, True])
    assert wizard.schritt_nach_name("servo").ausfuehren(ctx, dialog) is True
    assert any("0…100" in w for w in dialog.gewarnt)


# ---------------------------------------------------------------------------
# Reihenfolge
# ---------------------------------------------------------------------------


def test_jeder_schritt_sagt_warum_er_hier_steht():
    """Der halbe Nutzen eines geführten Ablaufs ist die Begründung."""
    for schritt in wizard.SCHRITTE:
        assert len(schritt.warum) > 40, schritt.key


def test_die_reihenfolge_ist_die_der_abhaengigkeiten():
    keys = [s.key for s in wizard.SCHRITTE]
    assert keys.index("standort") < keys.index("firmware")
    assert keys.index("firmware") < keys.index("nullpunkt")
    assert keys.index("nullpunkt") < keys.index("flaeche")
    assert keys.index("flaeche") < keys.index("servo")
    assert keys.index("servo") < keys.index("probe")


def test_schritte_ohne_voraussetzung_scheitern_hoeflich(tmp_path):
    """Wer `--nur servo` ruft, ohne eingemessen zu haben, bekommt einen Satz
    statt eines Stacktrace."""
    ctx = kontext(tmp_path)
    dialog = SkriptDialog([])
    assert wizard.schritt_nach_name("servo").ausfuehren(ctx, dialog) is False
    assert any("Fläche" in w for w in dialog.gewarnt)


def test_ein_unbekannter_schritt_ist_ein_fehler():
    with pytest.raises(KeyError):
        wizard.schritt_nach_name("gibtsnicht")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_status_zeigt_die_liste(tmp_path, capsys):
    assert wizard_cli.main(["--status", "--standorte", str(tmp_path / "s.json")]) == 0
    ausgabe = capsys.readouterr().out
    assert "Aufhängung einmessen" in ausgabe
    assert "wallplotter-setup --ab" in ausgabe


def test_ein_unbekannter_schrittname_bricht_ab(tmp_path, capsys):
    assert wizard_cli.main(["--nur", "quatsch"]) == 2
    assert "unbekannt" in capsys.readouterr().err


def test_der_konsolendialog_versteht_deutsche_zahlen():
    dialog = wizard_cli.KonsolenDialog(eingabe=lambda _p: "1450,5", ausgabe=lambda _t: None)
    assert dialog.zahl("Riemen", None) == 1450.5


def test_der_konsolendialog_nimmt_die_vorgabe_bei_leerer_eingabe():
    dialog = wizard_cli.KonsolenDialog(eingabe=lambda _p: "", ausgabe=lambda _t: None)
    assert dialog.zahl("Riemen", 1200.0) == 1200.0
    assert dialog.frage("Name", "Keller") == "Keller"
    assert dialog.ja("Weiter?", True) is True
    assert dialog.ja("Weiter?", False) is False


def test_der_konsolendialog_besteht_auf_einer_antwort():
    antworten = iter(["vielleicht", "j"])
    gewarnt: list[str] = []
    dialog = wizard_cli.KonsolenDialog(
        eingabe=lambda _p: next(antworten), ausgabe=gewarnt.append
    )
    assert dialog.ja("Weiter?", True) is True
    assert any("j oder n" in zeile for zeile in gewarnt)


def test_die_auswahl_geht_ueber_nummer_und_ueber_namen():
    for eingabe, erwartet in (("2", "hier"), ("ende", "ende"), ("", "fahren")):
        dialog = wizard_cli.KonsolenDialog(
            eingabe=lambda _p, e=eingabe: e, ausgabe=lambda _t: None
        )
        assert (
            dialog.auswahl("Ecke", [("fahren", "…"), ("hier", "…"), ("ende", "…")]) == erwartet
        )


def test_strg_c_ist_ein_abbruch_und_kein_absturz():
    def eingabe(_prompt):
        raise KeyboardInterrupt

    dialog = wizard_cli.KonsolenDialog(eingabe=eingabe, ausgabe=lambda _t: None)
    with pytest.raises(Abbruch):
        dialog.frage("Name")
