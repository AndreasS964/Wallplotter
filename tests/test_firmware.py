"""Die erzeugte config.yaml — und dass sie nicht von der Software wegdriftet.

Der Sinn des Erzeugers ist, dass es nur *eine* Beschreibung der Maschine gibt.
Diese Datei nagelt genau das fest: dass die ausgelieferte YAML wirklich aus dem
Werkzeug fällt, dass jeder Schlüssel darin FluidNC bekannt ist, und dass die
Prüfung anschlägt, wenn jemand etwas hineinschreibt, das die Firmware in
ConfigAlarm setzt.

Die Schlüsselliste in :mod:`wallplotter.fluidnc_schema` stammt aus dem
FluidNC-Quelltext. Sie hier gegen die eigene Ausgabe zu halten ist deshalb kein
Zirkelschluss: die eine Seite ist abgeschrieben von der Firmware, die andere
gebaut aus der Python-Konfiguration.
"""

from __future__ import annotations

import dataclasses
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

yaml = pytest.importorskip("yaml")

from fluidnc_fake import FakeFluidNCSocket, FakeSession, opener_for  # noqa: E402
from wallplotter import firmware_cli  # noqa: E402
from wallplotter.config import FluidNCConfig  # noqa: E402
from wallplotter.firmware import (  # noqa: E402
    RODENT_V1,
    FirmwareConfig,
    FirmwareError,
    LaserSpindle,
    ServoSpindle,
)
from wallplotter.fluidnc_schema import (  # noqa: E402
    ERROR,
    INFO,
    WARN,
    check_lines,
    check_mapping,
)
from wallplotter.kinematics import Motor  # noqa: E402
from wallplotter.location import Location, LocationBook  # noqa: E402
from wallplotter.timing import MotionLimits  # noqa: E402
from wallplotter.upload import FluidNCClient, TelnetChannel  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "fluidnc-wallplotter.yaml"


def parsed(config: FirmwareConfig) -> dict:
    return yaml.safe_load(config.render())


def errors(config: FirmwareConfig) -> list[str]:
    return [p.text for p in config.check() if p.level == ERROR]


def warnings(config: FirmwareConfig) -> list[str]:
    return [p.text for p in config.check() if p.level == WARN]


# ---------------------------------------------------------------------------
# Die ausgelieferte Datei ist das Erzeugnis
# ---------------------------------------------------------------------------


def test_die_ausgelieferte_datei_faellt_aus_dem_werkzeug():
    """Sonst ist „erzeugt, nicht von Hand ändern" eine Behauptung.

    Schlägt dieser Test fehl, hat jemand die YAML angefasst statt den Erzeuger.
    Der Weg zurück steht in der Meldung.
    """
    assert CONFIG_PATH.read_text(encoding="utf-8") == FirmwareConfig().render(), (
        "config/fluidnc-wallplotter.yaml weicht vom Erzeuger ab. Neu erzeugen mit:\n"
        "    wallplotter-firmware config --out config/fluidnc-wallplotter.yaml\n"
        "Unterschiede zeigt: wallplotter-firmware diff"
    )


def test_der_kopf_nennt_den_befehl_der_die_datei_wiederherstellt():
    text = FirmwareConfig().render()
    assert "wallplotter-firmware config" in text
    assert "ERZEUGT" in text


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(FirmwareConfig(), id="vorgabe"),
        pytest.param(
            dataclasses.replace(
                FirmwareConfig(),
                motor=dataclasses.replace(Motor(), microsteps=32),
                run_amps=0.9,
                segment_length_mm=0.5,
                laser=LaserSpindle(enabled=True),
            ),
            id="viele-abweichungen",
        ),
        pytest.param(
            dataclasses.replace(
                FirmwareConfig(),
                name="Werkstattwand groß",
                meta="",
                limits=MotionLimits(acceleration_mm_s2=350.0, max_rate_mm_min=6000.0),
                servo=ServoSpindle(pwm_hz=100, min_pulse_ms=0.9, max_pulse_ms=2.1),
                controls=False,
                control_active_low=False,
                idle_ms=200,
                max_travel_mm=5000.0,
            ),
            id="alles-anders",
        ),
        pytest.param(
            FirmwareConfig.from_location(
                Location(
                    name="Werkstatt",
                    anchor_span_mm=2300.0,
                    left_belt_zero_mm=1450.0,
                    right_belt_zero_mm=1470.0,
                )
            ),
            id="aus-standort",
        ),
    ],
)
def test_der_genannte_befehl_erzeugt_wirklich_dieselbe_datei(config, tmp_path, monkeypatch):
    """Der Aufruf in der Kopfzeile ist keine Zierde, sondern muss stimmen.

    Ein unvollständiger Aufruf wäre schlimmer als gar keiner: Er sähe aus wie
    eine Anleitung und erzeugte eine andere Datei.
    """
    befehl = shlex.split(config.command_line())
    assert befehl[:2] == ["wallplotter-firmware", "config"]

    if config.location_name:
        buch = tmp_path / "standorte.json"
        LocationBook(
            locations={
                "Werkstatt": Location(
                    name="Werkstatt",
                    anchor_span_mm=2300.0,
                    left_belt_zero_mm=1450.0,
                    right_belt_zero_mm=1470.0,
                )
            },
            active="Werkstatt",
        ).save(buch)
        befehl += ["--standorte", str(buch)]

    ziel = tmp_path / "aus-der-kopfzeile.yaml"
    assert firmware_cli.main(befehl[1:] + ["--out", str(ziel), "--trotzdem"]) == 0
    assert ziel.read_text(encoding="utf-8") == config.render()


# ---------------------------------------------------------------------------
# Was FluidNC davon versteht
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(FirmwareConfig(), id="vorgabe"),
        pytest.param(
            dataclasses.replace(FirmwareConfig(), laser=LaserSpindle(enabled=True)), id="laser"
        ),
        pytest.param(dataclasses.replace(FirmwareConfig(), controls=False), id="ohne-taster"),
        pytest.param(
            dataclasses.replace(FirmwareConfig(), control_active_low=False), id="aktiv-high"
        ),
        pytest.param(
            dataclasses.replace(
                FirmwareConfig(), motor=dataclasses.replace(Motor(), microsteps=64)
            ),
            id="64-mikroschritte",
        ),
        pytest.param(
            dataclasses.replace(
                FirmwareConfig(),
                limits=MotionLimits(acceleration_mm_s2=350.0, max_rate_mm_min=6000.0),
            ),
            id="andere-grenzen",
        ),
    ],
)
def test_jeder_erzeugte_schluessel_ist_fluidnc_bekannt(config):
    """Ein unbekannter Schlüssel setzt das Board in ConfigAlarm — jeder einzelne.

    Geprüft wird gegen die Liste aus dem Firmware-Quelltext, nicht gegen die
    eigene Erwartung.
    """
    findings = check_mapping(parsed(config))
    assert [str(f) for f in findings if f.level == ERROR] == []
    assert [str(f) for f in findings if f.level == WARN] == []


def test_die_erzeugte_datei_hat_keine_ungeprueften_abschnitte():
    """Alles, was drinsteht, ist auch wirklich nachgeschlagen worden."""
    assert [str(f) for f in check_mapping(parsed(FirmwareConfig())) if f.level == INFO] == []


def test_der_pruefer_erkennt_den_schluessel_der_das_board_lahmgelegt_haette():
    """`laser_mode` stand einmal wirklich in dieser Datei.

    Es ist kein Konfigschlüssel von FluidNC; ein solcher Eintrag hätte das Board
    beim Einlesen in ConfigAlarm gesetzt, ohne dass irgendein Test es merkte.
    """
    findings = check_mapping({"Laser": {"laser_mode": False}})
    assert [f.level for f in findings] == [ERROR]
    assert "ConfigAlarm" in findings[0].text


def test_ein_wert_ausserhalb_des_bereichs_ist_nur_eine_warnung():
    """FluidNC klemmt ihn und fährt weiter (NutsBolts.h, constrain_with_message).

    Das ist ein echter Unterschied zum unbekannten Schlüssel und darf nicht
    zusammenfallen, sonst weiß niemand mehr, wann das Board wirklich steht.
    """
    findings = check_mapping({"stepping": {"pulse_us": 99}})
    assert [f.level for f in findings] == [WARN]


def test_ein_fremder_abschnitt_in_der_wurzel_ist_nur_ein_hinweis():
    """Die Tabelle führt nicht jeden Abschnitt — und behauptet es auch nicht."""
    assert [f.level for f in check_mapping({"oled": {"i2c_num": 0}})] == [INFO]


def test_ein_fremder_abschnitt_tiefer_unten_ist_ein_fehler():
    """Innerhalb von `axes:` ist die Tabelle vollständig, dort gilt kein Zweifel."""
    findings = check_mapping({"axes": {"q": {"steps_per_mm": 80}}})
    assert [f.level for f in findings] == [ERROR]


# ---------------------------------------------------------------------------
# Die Zahlen kommen aus der Software, nicht aus der Datei
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("microsteps, erwartet", [(8, 40.0), (16, 80.0), (32, 160.0)])
def test_schrittweite_folgt_den_mikroschritten(microsteps, erwartet):
    """Genau diese Kopplung ging von Hand schon schief.

    Wer im Treiberblock die Mikroschritte ändert und `steps_per_mm` vergisst,
    fährt um denselben Faktor daneben — und zwar ohne jede Meldung.
    """
    config = dataclasses.replace(
        FirmwareConfig(), motor=dataclasses.replace(Motor(), microsteps=microsteps)
    )
    data = parsed(config)
    for achse in ("x", "y"):
        assert data["axes"][achse]["steps_per_mm"] == erwartet
        assert data["axes"][achse]["motor0"]["tmc_5160"]["microsteps"] == microsteps


def test_grenzen_stammen_aus_derselben_quelle_wie_die_laufzeitschaetzung():
    limits = MotionLimits(acceleration_mm_s2=275.0, max_rate_mm_min=5500.0)
    data = parsed(dataclasses.replace(FirmwareConfig(), limits=limits))
    for achse in ("x", "y"):
        assert data["axes"][achse]["max_rate_mm_per_min"] == 5500.0
        assert data["axes"][achse]["acceleration_mm_per_sec2"] == 275.0


def test_anker_kommen_aus_dem_eingemessenen_standort():
    """Der eigentliche Zweck: Firmware und Vorschau rechnen mit denselben Maßen."""
    standort = Location(
        name="Werkstatt", anchor_span_mm=2300.0, left_belt_zero_mm=1450.0, right_belt_zero_mm=1470.0
    )
    config = FirmwareConfig.from_location(standort)
    anker = standort.anchors()
    wall = parsed(config)["kinematics"]["WallPlotter"]

    assert wall["left_anchor_x"] == pytest.approx(anker.left_x, abs=1e-3)
    assert wall["right_anchor_x"] == pytest.approx(anker.right_x, abs=1e-3)
    assert wall["left_anchor_y"] == wall["right_anchor_y"] == pytest.approx(anker.y, abs=1e-3)
    # und eben nicht mehr die Beispielwerte
    assert wall["left_anchor_x"] != FirmwareConfig().anchors.left_x


def test_der_standort_steht_mit_seinen_massen_im_kopf():
    """Damit an der Wand nachvollziehbar ist, welche Messung im Board steckt."""
    standort = Location(
        name="Werkstatt", anchor_span_mm=2300.0, left_belt_zero_mm=1450.0, right_belt_zero_mm=1470.0
    )
    text = FirmwareConfig.from_location(standort).render()
    assert "Standort: Werkstatt" in text
    assert "Spannweite 2300 mm" in text
    assert "1450/1470 mm" in text
    assert "--location Werkstatt" in text


def test_die_mikroschritte_des_standorts_gehen_mit():
    standort = Location(
        name="A",
        anchor_span_mm=2300.0,
        left_belt_zero_mm=1450.0,
        right_belt_zero_mm=1470.0,
        microsteps=32,
    )
    assert parsed(FirmwareConfig.from_location(standort))["axes"]["x"]["steps_per_mm"] == 160.0


# ---------------------------------------------------------------------------
# Der Servo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pwm_hz", [50, 100, 200, 333])
def test_die_speed_map_trifft_das_impulsfenster(pwm_hz):
    """Rückwärts gerechnet muss wieder 1,0 bis 2,0 ms herauskommen.

    Die speed_map bildet auf das Tastverhältnis ab. Hier stand einmal
    `0=0.000% 100=100.000%`; damit lag der ganze Servoweg zwischen S5 und S10,
    und die Werte aus dem Stiftkatalog fuhren in den Anschlag.
    """
    servo = ServoSpindle(pwm_hz=pwm_hz)
    data = parsed(dataclasses.replace(FirmwareConfig(), servo=servo))
    periode_ms = 1000.0 / data["pwm"]["pwm_hz"]

    eintraege = [teil.partition("=") for teil in data["pwm"]["speed_map"].split()]
    impulse = {
        int(s): periode_ms * float(prozent.rstrip("%")) / 100.0 for s, _, prozent in eintraege
    }
    assert impulse[servo.s_min] == pytest.approx(servo.min_pulse_ms, abs=1e-6)
    assert impulse[servo.s_max] == pytest.approx(servo.max_pulse_ms, abs=1e-6)


def test_der_stiftkatalog_passt_in_den_erzeugten_s_bereich():
    """Die mitgelieferten Stifte tragen S-Werte von 0 bis 100 — die müssen abbilden."""
    from wallplotter.toolhead import PENS

    servo = FirmwareConfig().servo
    for stift in PENS.values():
        assert servo.s_min <= stift.up_value <= servo.s_max
        assert servo.s_min <= stift.down_value <= servo.s_max


def test_ein_verkehrtes_impulsfenster_wird_gar_nicht_erst_gebaut():
    with pytest.raises(FirmwareError):
        ServoSpindle(min_pulse_ms=2.0, max_pulse_ms=1.0)


def test_ein_zu_breiter_impuls_wird_gemeldet():
    config = dataclasses.replace(
        FirmwareConfig(), servo=ServoSpindle(min_pulse_ms=0.4, max_pulse_ms=2.4)
    )
    assert any("RC-Servo" in text for text in errors(config))


def test_ein_impuls_der_nicht_in_die_periode_passt_wird_gemeldet():
    config = dataclasses.replace(FirmwareConfig(), servo=ServoSpindle(pwm_hz=1000))
    assert any("Periode" in text for text in errors(config))


# ---------------------------------------------------------------------------
# Was schiefgehen kann
# ---------------------------------------------------------------------------


def test_zwei_koepfe_auf_einem_pin_werden_gemeldet():
    """FluidNC belegt einen Pin exklusiv — die Datei ließe sich nicht einlesen."""
    config = dataclasses.replace(
        FirmwareConfig(), laser=LaserSpindle(enabled=True, output_pin=RODENT_V1.servo_pin)
    )
    assert any("doppelt belegt" in text for text in errors(config))


def test_ein_laser_mit_servotakt_wird_gemeldet():
    config = dataclasses.replace(FirmwareConfig(), laser=LaserSpindle(enabled=True, pwm_hz=50))
    assert any("1000" in text for text in errors(config))


def test_zwei_spindeln_mit_derselben_werkzeugnummer_werden_gemeldet():
    config = dataclasses.replace(FirmwareConfig(), laser=LaserSpindle(enabled=True, tool_num=0))
    assert any("tool_num" in text for text in errors(config))


def test_strom_ueber_dem_nennwert_wird_gemeldet():
    assert any("Nennstrom" in text for text in errors(dataclasses.replace(FirmwareConfig(), run_amps=2.0)))


def test_ein_ausgang_auf_einem_reinen_eingangspin_wird_gemeldet():
    """gpio.34 bis gpio.39 des ESP32 können nur lesen."""
    board = dataclasses.replace(RODENT_V1, servo_pin="gpio.36")
    assert any("Eingänge" in text for text in errors(dataclasses.replace(FirmwareConfig(), board=board)))


def test_abschaltbare_motoren_werden_gemeldet():
    """Die Gondel hängt am Riemen; ein idle_ms unter 255 lässt sie irgendwann fallen."""
    assert any("255" in text for text in warnings(dataclasses.replace(FirmwareConfig(), idle_ms=60)))


def test_ohne_taster_faellt_der_halt_ersatzlos_weg():
    config = dataclasses.replace(FirmwareConfig(), controls=False)
    assert "control" not in parsed(config)
    assert any("Halt" in text for text in warnings(config))


def test_die_vorgabe_beanstandet_nichts():
    """Die ausgelieferte Konfiguration soll sauber durchgehen — sonst ist die
    Prüfung Lärm, den man wegzuklicken lernt."""
    assert FirmwareConfig().check() == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_config_schreibt_die_datei(tmp_path, capsys):
    ziel = tmp_path / "config.yaml"
    assert firmware_cli.main(["config", "--out", str(ziel), "--kein-standort"]) == 0
    assert yaml.safe_load(ziel.read_text(encoding="utf-8"))["kinematics"]["WallPlotter"]


def test_config_schreibt_nichts_wenn_die_pruefung_fehler_findet(tmp_path):
    ziel = tmp_path / "config.yaml"
    code = firmware_cli.main(
        ["config", "--out", str(ziel), "--kein-standort", "--laser", "--laser-pin", "gpio.25"]
    )
    assert code == 2
    assert not ziel.exists()


def test_config_schreibt_auf_ausdrueckliches_verlangen_trotzdem(tmp_path):
    ziel = tmp_path / "config.yaml"
    code = firmware_cli.main(
        [
            "config",
            "--out",
            str(ziel),
            "--kein-standort",
            "--laser",
            "--laser-pin",
            "gpio.25",
            "--trotzdem",
        ]
    )
    assert code == 0
    assert ziel.exists()


def test_pruefen_nimmt_die_ausgelieferte_datei_an(capsys):
    assert firmware_cli.main(["pruefen", str(CONFIG_PATH)]) == 0


def test_pruefen_schlaegt_bei_einem_erfundenen_schluessel_an(tmp_path, capsys):
    datei = tmp_path / "kaputt.yaml"
    datei.write_text("Laser:\n  laser_mode: false\n", encoding="utf-8")
    assert firmware_cli.main(["pruefen", str(datei)]) == 1
    assert "ConfigAlarm" in capsys.readouterr().out


def test_pruefen_sagt_es_wenn_die_datei_fehlt(tmp_path):
    assert firmware_cli.main(["pruefen", str(tmp_path / "gibtsnicht.yaml")]) == 4


def test_diff_ist_still_wenn_nichts_abweicht(capsys):
    assert firmware_cli.main(["diff", str(CONFIG_PATH), "--kein-standort"]) == 0


def test_diff_zeigt_eine_von_hand_hineingeratene_aenderung(tmp_path, capsys):
    datei = tmp_path / "config.yaml"
    datei.write_text(
        FirmwareConfig().render().replace("run_amps: 1.200", "run_amps: 2.000"), encoding="utf-8"
    )
    assert firmware_cli.main(["diff", str(datei), "--kein-standort"]) == 1
    assert "run_amps: 2.000" in capsys.readouterr().out


def test_ein_verlangter_aber_unbekannter_standort_ist_ein_fehler(tmp_path):
    leer = tmp_path / "standorte.json"
    leer.write_text('{"active": null, "locations": []}', encoding="utf-8")
    assert (
        firmware_cli.main(["config", "--location", "Gibtsnicht", "--standorte", str(leer)]) == 3
    )


# ---------------------------------------------------------------------------
# Übertragen
# ---------------------------------------------------------------------------


def _client(**kwargs):
    session = FakeSession(**kwargs)
    sock = FakeFluidNCSocket()
    config = FluidNCConfig(host="wandplotter.local")
    channel = TelnetChannel(config.hostname, config.telnet_port, 5.0, opener_for(sock))
    return FluidNCClient(config, session, channel), session, sock


def test_die_konfiguration_geht_in_den_flash_und_nicht_auf_die_karte():
    """Auf der SD-Karte liest FluidNC sie nie — der Upload gälte als geglückt
    und das Board führe weiter mit der alten Konfiguration."""
    client, session, _sock = _client()
    client.upload_local("board: Test\n", "/config.yaml")

    assert session.flash["/config.yaml"] == "board: Test\n"
    assert session.card == {}
    assert any(url.endswith("/files") for _art, url, _rest in session.calls)


def test_der_flash_upload_meldet_die_groesse_vorab():
    """FluidNC prüft über `<pfad>S`, ob der Platz reicht (fileUpload(), Zeile 1032)."""
    client, session, _sock = _client()
    inhalt = "board: Test\n"
    client.upload_local(inhalt, "/config.yaml")

    _art, _url, rest = session.calls[-1]
    assert rest["data"] == {"/config.yamlS": str(len(inhalt.encode("utf-8")))}
    assert rest["files"]["file"][0] == "/config.yaml"


def test_die_konfiguration_laesst_sich_zurueckholen():
    client, _session, _sock = _client(flash={"/config.yaml": "board: Test\n"})
    assert client.download_local("/config.yaml") == "board: Test\n"


def test_waehrend_der_fahrt_gibt_es_die_datei_nicht():
    """Der Server weist das Ausliefern aus dem Flash bei Bewegung mit 503 ab."""
    from wallplotter.upload import FluidNCError

    client, _session, _sock = _client(flash={"/config.yaml": "board: Test\n"}, blocked=True)
    with pytest.raises(FluidNCError):
        client.download_local("/config.yaml")


def test_der_neustart_geht_ueber_bye():
    client, _session, sock = _client()
    client.restart()
    assert "$Bye" in sock.lines


def test_push_sichert_erst_und_schreibt_dann(tmp_path, monkeypatch, capsys):
    sitzung = FakeSession(flash={"/config.yaml": "board: alt\n"})
    sock = FakeFluidNCSocket()

    def fabrik(config):
        channel = TelnetChannel(config.hostname, config.telnet_port, 5.0, opener_for(sock))
        return FluidNCClient(config, sitzung, channel)

    monkeypatch.setattr("wallplotter.upload.FluidNCClient", fabrik)
    sicherung = tmp_path / "vorher.yaml"
    code = firmware_cli.main(
        [
            "push",
            "--host",
            "wandplotter.local",
            "--kein-standort",
            "--sicherung",
            str(sicherung),
        ]
    )
    assert code == 0
    assert sicherung.read_text(encoding="utf-8") == "board: alt\n"
    assert sitzung.flash["/config.yaml"] == FirmwareConfig().render()
    assert "$Bye" in sock.lines


def test_push_uebertraegt_nichts_wenn_die_pruefung_fehler_findet(monkeypatch):
    sitzung = FakeSession(flash={"/config.yaml": "board: alt\n"})

    monkeypatch.setattr(
        "wallplotter.upload.FluidNCClient",
        lambda config: FluidNCClient(config, sitzung, None),
    )
    code = firmware_cli.main(
        [
            "push",
            "--host",
            "wandplotter.local",
            "--kein-standort",
            "--laser",
            "--laser-pin",
            "gpio.25",
        ]
    )
    assert code == 2
    assert sitzung.flash["/config.yaml"] == "board: alt\n"


# ---------------------------------------------------------------------------
# Ohne die Extras
# ---------------------------------------------------------------------------


def test_erzeugen_geht_auch_ohne_pyyaml(tmp_path, monkeypatch, capsys):
    """Der Kern soll ohne Extras laufen — erzeugt wird ohne Parser.

    Wichtig ist nur, dass die ausgefallene Prüfung dasteht: eine Prüfung, die
    stillschweigend nicht stattfindet, ist schlimmer als keine.
    """
    import builtins

    echt = builtins.__import__

    def ohne_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("kein PyYAML")
        return echt(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", ohne_yaml)

    ziel = tmp_path / "config.yaml"
    assert firmware_cli.main(["config", "--out", str(ziel), "--kein-standort"]) == 0
    assert ziel.read_text(encoding="utf-8") == FirmwareConfig().render()
    assert "PyYAML fehlt" in capsys.readouterr().err


def test_pruefen_kommt_ohne_pyyaml_bis_zur_zeilenpruefung(monkeypatch, capsys):
    """Die Sicht des Tokenizers braucht keinen YAML-Parser — die geht immer.

    Was ohne PyYAML wegfällt, ist der Abgleich der Schlüssel. Und genau das
    muss dastehen.
    """
    import builtins

    echt = builtins.__import__

    def ohne_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("kein PyYAML")
        return echt(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", ohne_yaml)
    assert firmware_cli.main(["pruefen", str(CONFIG_PATH)]) == 0
    ausgabe = capsys.readouterr()
    assert "PyYAML fehlt" in ausgabe.err
    assert "nicht ob es die Schlüssel kennt" in ausgabe.err


def test_pruefen_findet_den_kommentar_hinter_dem_wert_auch_ohne_pyyaml(
    tmp_path, monkeypatch, capsys
):
    import builtins

    echt = builtins.__import__

    def ohne_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("kein PyYAML")
        return echt(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", ohne_yaml)
    datei = tmp_path / "config.yaml"
    datei.write_text("stepping:\n  idle_ms: 255 # gehalten lassen\n", encoding="utf-8")
    assert firmware_cli.main(["pruefen", str(datei)]) == 1
    assert "ConfigAlarm" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Wie FluidNC die Zeilen liest — und was YAML davon nicht sieht
# ---------------------------------------------------------------------------


def test_hinter_keinem_wert_steht_ein_kommentar():
    """Der Fund, der die ausgelieferte Datei unfahrbar gemacht hätte.

    FluidNCs Tokenizer schneidet Kommentare am Zeilenende **nicht** ab:
    ``parseValue()`` nimmt bei einem unquotierten Wert den ganzen Rest der Zeile
    (``_token._value = _line;``). Bei einer Zahl scheitert danach
    ``from_decimal``/``from_float`` — und ``parseError()`` ist
    ``set_state(State::ConfigAlarm)``.

    Ein YAML-Parser sieht davon nichts. Deshalb wird hier über die Zeilen
    geprüft und nicht über den geladenen Baum.
    """
    for config in (
        FirmwareConfig(),
        dataclasses.replace(FirmwareConfig(), laser=LaserSpindle(enabled=True)),
        dataclasses.replace(FirmwareConfig(), controls=False),
    ):
        assert [str(f) for f in check_lines(config.render())] == []


def test_der_auskommentierte_laserblock_bleibt_beim_einschalten_heil():
    """Wer die Rautezeichen entfernt, soll gültiges YAML bekommen.

    Sonst ist der auskommentierte Block eine Falle: Er sieht aus wie eine
    Vorlage und legt beim Benutzen das Board still.
    """
    aus = FirmwareConfig().render().splitlines()
    beginn = next(i for i, zeile in enumerate(aus) if zeile.startswith("# Laser:"))
    ende = next(i for i, zeile in enumerate(aus[beginn:], beginn) if zeile.startswith("# ---"))
    entkommentiert = "\n".join(
        zeile[2:] if zeile.startswith("# ") else zeile[1:] if zeile == "#" else zeile
        for zeile in aus[beginn:ende]
    )
    assert [str(f) for f in check_lines(entkommentiert)] == []
    assert yaml.safe_load(entkommentiert)["Laser"]["pwm_hz"] == 5000


@pytest.mark.parametrize(
    "zeile, erwartet",
    [
        ("stepping:\n  idle_ms: 255 # gehalten lassen\n", ERROR),
        ("axes:\n  x:\n    steps_per_mm: 80.0 # gerechnet\n", ERROR),
        ("start:\n  must_home: false # kein Homing\n", ERROR),
        ("control:\n  reset_pin: gpio.35:low # X-MAX\n", WARN),
        ("board: BTT Rodent V1.0\n", None),
        # Ein quotierter Wert endet am Anführungszeichen, der Rest der Zeile
        # fällt weg — parseValue(), Tokenizer.cpp:127ff. Das ist erlaubt.
        ('name: "Wand" # Notiz\n', None),
        # Ein Kommentar in einer eigenen Zeile wird verworfen, auch eingerückt.
        ("axes:\n  # nur ein Kommentar\n  x:\n    steps_per_mm: 80.0\n", None),
    ],
)
def test_die_zeilenpruefung_trifft_genau_die_faelle(zeile, erwartet):
    stufen = [f.level for f in check_lines(zeile)]
    assert stufen == ([erwartet] if erwartet else [])


def test_ein_tabulator_als_einzug_wird_gemeldet():
    """`Use spaces, not tabs, for indentation` — Tokenizer.cpp:99."""
    assert [f.level for f in check_lines("axes:\n\tx:\n")] == [ERROR]


def test_ein_schluessel_ohne_doppelpunkt_wird_gemeldet():
    assert [f.level for f in check_lines("board BTT Rodent\n")] == [ERROR]


# ---------------------------------------------------------------------------
# Freitext im Kopf der Datei
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Wandplotter Keller",
        "Wand: groß",  # Doppelpunkt — ohne Anführungszeichen kein gültiges YAML
        "Keller #2",  # Raute — der Rest der Zeile ginge verloren
        "*stern",  # YAML läse einen Anker
        "'einfach'",
        '"doppelt"',
        "123",  # sonst eine Zahl statt eines Namens
        "true",  # sonst ein Wahrheitswert
        "  führend",
        "Werkstatt — zweite Wand",
    ],
)
def test_ein_maschinenname_kommt_heil_durch(name):
    """Der Name ist Freitext und darf alles enthalten, was ein Mensch tippt.

    Ohne Anführungszeichen legt ein Doppelpunkt die Datei lahm, und eine Raute
    schneidet den Rest ab. Geprüft wird beides: dass ein YAML-Parser den Namen
    unverändert zurückgibt und dass FluidNCs Tokenizer nichts zu beanstanden hat.
    """
    config = dataclasses.replace(FirmwareConfig(), name=name)
    text = config.render()
    assert yaml.safe_load(text)["name"] == name
    assert [str(f) for f in check_lines(text)] == []


def test_ein_name_mit_beiden_anfuehrungszeichen_wird_abgelehnt():
    """FluidNCs Tokenizer kennt keine Maskierung — das ist nicht darstellbar.

    Lieber laut ablehnen als eine Datei schreiben, die das Board nicht liest.
    """
    config = dataclasses.replace(FirmwareConfig(), name="beides ' und \" drin")
    with pytest.raises(FirmwareError):
        config.render()


def test_ein_zeilenumbruch_im_namen_wird_abgelehnt():
    config = dataclasses.replace(FirmwareConfig(), name="zwei\nZeilen")
    with pytest.raises(FirmwareError):
        config.render()
