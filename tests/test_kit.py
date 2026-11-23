"""Tests sans matériel du kit calage-sono (pytest). Lancés par la CI à chaque push.

Ils couvrent les garde-fous de sécurité : plafond de limiteur, bornes, valeurs strictes, CR/LF,
relecture stricte (sur le simulateur faux_venu360), verrou d'émission de mesure_tf, et les autotests des outils.
Aucun ne remplace une validation sur l'appareil réel.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))

import calc_systeme  # noqa: E402
import dbx_hiqnet  # noqa: E402
import dcx2496  # noqa: E402
import faux_venu360  # noqa: E402
import garde_fous  # noqa: E402
import mesure_tf  # noqa: E402
import tracks_dsp  # noqa: E402

CONFIG = str(RACINE / "data" / "configs" / "ma-sono-actuel.yaml")
SEUIL = "\\\\Preset\\Limiter1\\SV\\Threshold"


# ---------------- autotests des outils ----------------
@pytest.mark.parametrize("module", [garde_fous, dcx2496, tracks_dsp, mesure_tf])
def test_autotest(module, capsys):
    module.autotest()
    assert "OK" in capsys.readouterr().out or module is mesure_tf


def test_yaml_valides():
    import yaml
    for f in list((RACINE / "data").rglob("*.yaml")) + list((RACINE / "data").rglob("*.yml")):
        yaml.safe_load(f.read_text(encoding="utf-8"))


# ---------------- calcul et plafond ----------------
def test_plafond_actuel():
    p = calc_systeme.plafond_limiteur(CONFIG, "SUB")
    assert p["unite"] == "dBFS" and p["seuil"] == pytest.approx(-23.1, abs=0.05)


def test_verifier_plafond():
    assert garde_fous.verifier_plafond(-25, CONFIG, "SUB", "dBFS", False).startswith("plafond OK")
    for args in ((-20, CONFIG, "SUB", "dBFS", False),      # au-dessus du plafond
                 (-25, CONFIG, "SUB", "dBu", False),       # mauvaise unité
                 (-25, None, None, "dBFS", False)):        # config / voie absentes
        with pytest.raises(ValueError):
            garde_fous.verifier_plafond(*args)
    assert garde_fous.verifier_plafond(-20, CONFIG, "SUB", "dBFS", True).startswith("DEROGATION")


def test_rapport_mentionne_limites():
    _, cfg = calc_systeme.charger(CONFIG)
    texte = calc_systeme.rapport_markdown(cfg, [])
    assert "théorique" in texte


@pytest.mark.parametrize("texte", ["a\nset x", "b\r", "c\x00"])
def test_refus_caracteres_controle(texte):
    with pytest.raises(ValueError):
        garde_fous.refuser_controle(texte)


# ---------------- DCX2496 ----------------
LIM = {"nom": "Limiter Threshold", "type": "number", "min": -24, "max": 0, "step": 0.1}


def test_dcx_bornes():
    assert dcx2496.code_valeur(LIM, -24) == 0 and dcx2496.code_valeur(LIM, 0) == 240
    assert dcx2496.code_valeur(LIM, -12.3) == 117
    for v in (-24.1, -30, 0.1, 3):      # l'ancien code repliait −30 en −18 dB (moins protecteur)
        with pytest.raises(ValueError):
            dcx2496.code_valeur(LIM, v)


def test_dcx_booleen_strict():
    b = {"nom": "Mute", "type": "bool"}
    assert dcx2496.code_valeur(b, "on") == 1 and dcx2496.code_valeur(b, "non") == 0
    with pytest.raises(ValueError):
        dcx2496.code_valeur(b, "peut-etre")


# ---------------- t.racks ----------------
@pytest.mark.parametrize("param,vals", [("polarite", ["inv3rse"]), ("mute", ["peut-etre"]),
                                        ("peq", ["10", "0", "100", "1"]), ("hpf", ["5", "LR24"]),
                                        ("peq", ["1", "0", "100", "0"]), ("peq", ["1", "0", "30000", "1"])])
def test_tracks_refus(param, vals):
    with pytest.raises(ValueError):
        tracks_dsp.construire_ecriture(0, param, vals, bytes(928), 2, 1)


def test_tracks_attendu_relecture():
    assert tracks_dsp.attendu_relecture("polarite", ["inverse"], 3) == {"sorties/3/polarite": "inverse"}
    assert tracks_dsp.attendu_relecture("mute", ["oui"], 1) == {"sorties/1/mute": True}


# ---------------- dbx sur simulateur ----------------
@pytest.fixture
def faux_dbx(tmp_path, monkeypatch):
    srv = faux_venu360.S(("127.0.0.1", 0), faux_venu360.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(dbx_hiqnet, "JOURNAL", tmp_path)
    monkeypatch.delenv("DBX_MDP", raising=False)
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def lancer_dbx(port, *args, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["dbx_hiqnet.py", args[0], "127.0.0.1", *args[1:], "--port", str(port)])
    try:
        dbx_hiqnet.main()
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def test_dbx_seuil_sans_config_refuse(faux_dbx, monkeypatch):
    assert lancer_dbx(faux_dbx, "set", SEUIL, "-25.0", "--appliquer", "--protection-validee",
                      monkeypatch=monkeypatch) != 0


def test_dbx_seuil_au_dessus_plafond_refuse(faux_dbx, monkeypatch):
    assert lancer_dbx(faux_dbx, "set", SEUIL, "-10.0", "--appliquer", "--protection-validee",
                      "--config", CONFIG, "--voie", "SUB", monkeypatch=monkeypatch) != 0


def test_dbx_seuil_valide_ecrit_et_relu(faux_dbx, monkeypatch, tmp_path):
    assert lancer_dbx(faux_dbx, "set", SEUIL, "-25.0", "--appliquer", "--protection-validee",
                      "--config", CONFIG, "--voie", "SUB", monkeypatch=monkeypatch) == 0
    log = (tmp_path / "ecritures.log").read_text(encoding="utf-8")
    assert "plafond OK" in log


def test_dbx_hors_plage_refuse(faux_dbx, monkeypatch):
    assert lancer_dbx(faux_dbx, "set", SEUIL, "-80.0", "--appliquer", "--protection-validee",
                      "--config", CONFIG, "--voie", "SUB", monkeypatch=monkeypatch) != 0


def test_dbx_mot_de_passe_jamais_affiche(faux_dbx, monkeypatch, capsys):
    monkeypatch.setenv("DBX_MDP", "administrator")
    lancer_dbx(faux_dbx, "info", "-v", monkeypatch=monkeypatch)
    err = capsys.readouterr().err
    assert 'connect administrator "****"' in err and '"administrator"' not in err


# ---------------- mesure_tf ----------------
def test_mesure_sans_appliquer_n_emet_rien(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "sounddevice", type(sys)("sounddevice"))   # faux module : playrec absent
    monkeypatch.setattr(sys, "argv", ["mesure_tf.py", "mesurer", "--dev", "3"])
    with pytest.raises(SystemExit) as e:
        mesure_tf.main()
    assert "SIMULATION" in str(e.value.code)


def test_mesure_controle_qualite():
    x = mesure_tf.sweep_log(duree=0.5)
    assert mesure_tf.controle_qualite(x * 0.1, x * 0.1) == []
    assert mesure_tf.controle_qualite(x * 0.1, np.clip(x * 2, -1, 1))       # écrêtage
    assert mesure_tf.controle_qualite(x * 1e-4, x * 0.1)                    # référence absente


# ---------------- excursion bass-reflex ----------------
import excursion_br  # noqa: E402


def test_excursion_autotest(capsys):
    excursion_br.autotest()
    assert "OK" in capsys.readouterr().out


def test_excursion_regle_pente():
    hp = {"re_ohm": 5.4, "xmax_mm": 13.5, "ts": {"fs_hz": 28, "qes": 0.39, "qms": 6.9, "vas_l": 385,
                                                 "sd_m2": 0.173, "bl_tm": 30.5, "mms_g": 355}}
    doux = excursion_br.analyse(hp, 385, 34, "BW24 29 Hz", 50)
    raide = excursion_br.analyse(hp, 385, 34, "LR48 30 Hz", 50)
    assert doux["attenuation_07fb_db"] < 14 <= raide["attenuation_07fb_db"]
    assert any("0,7·fb" in a for a in doux["alertes"]) and not any("0,7·fb" in a for a in raide["alertes"])


def test_excursion_refonte_signale_xmax():
    lignes = calc_systeme.calc_voie(
        {"nom": "SUB", "hp": "RCF_LF21X451", "hp_en_serie": 1, "branches_paralleles": 1,
         "charge": "BR 385 L, accord 34 Hz"},
        *calc_systeme.charger(str(RACINE / "data" / "configs" / "ma-sono-refonte.yaml")))
    assert lignes["excursion_max_mm"] > 13.5 and any("EXCURSION" in a for a in lignes["alertes"])
