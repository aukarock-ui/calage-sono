"""Tests sans matériel du kit calage-sono (pytest). Lancés par la CI à chaque push.

Ils couvrent les garde-fous de sécurité : plafond de limiteur, bornes, valeurs strictes, CR/LF,
relecture stricte (sur le simulateur faux_venu360), verrou d'émission de mesure_tf, et les autotests des outils.
Aucun ne remplace une validation sur l'appareil réel.
"""
from __future__ import annotations

import json
import socketserver
import sys
import tempfile
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
import linea_tipi  # noqa: E402
import mesure_tf  # noqa: E402
import osm_client  # noqa: E402
import tracks_dsp  # noqa: E402

CONFIG_BRUT = str(RACINE / "data" / "configs" / "ma-sono-actuel.yaml")   # réglage de sortie VENU360 non confirmé


def _config_confirmee() -> str:
    """Copie de la config actuelle dont le réglage de sortie est marqué « lu sur l'appareil » (pour tester le plafond)."""
    texte = Path(CONFIG_BRUT).read_text(encoding="utf-8")
    assert "processeur_reglage_sortie_confirme: false" in texte
    p = Path(tempfile.mkdtemp(prefix="calage-test-")) / "config-confirmee.yaml"
    p.write_text(texte.replace("processeur_reglage_sortie_confirme: false", "processeur_reglage_sortie_confirme: true"),
                 encoding="utf-8")
    return str(p)


CONFIG = _config_confirmee()
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
    assert lancer_dbx(faux_dbx, "set", SEUIL, "-25.0dB", "--appliquer", "--protection-validee",
                      monkeypatch=monkeypatch) != 0


def test_dbx_seuil_au_dessus_plafond_refuse(faux_dbx, monkeypatch):
    assert lancer_dbx(faux_dbx, "set", SEUIL, "-10.0dB", "--appliquer", "--protection-validee",
                      "--config", CONFIG, "--voie", "SUB", monkeypatch=monkeypatch) != 0


def test_dbx_seuil_valide_ecrit_et_relu(faux_dbx, monkeypatch, tmp_path):
    assert lancer_dbx(faux_dbx, "set", SEUIL, "-25.0dB", "--appliquer", "--protection-validee",
                      "--config", CONFIG, "--voie", "SUB", monkeypatch=monkeypatch) == 0
    log = (tmp_path / "ecritures.log").read_text(encoding="utf-8")
    assert "plafond OK" in log


def test_dbx_hors_plage_refuse(faux_dbx, monkeypatch):
    assert lancer_dbx(faux_dbx, "set", SEUIL, "-80.0dB", "--appliquer", "--protection-validee",
                      "--config", CONFIG, "--voie", "SUB", monkeypatch=monkeypatch) != 0


XO_TYPE = "\\\\Preset\\CrossoverIIR1\\SV\\Band_1_HPType"


@pytest.mark.parametrize("valeur", ["lr 24", "LR24", "LR 96", "bw 6"])
def test_dbx_liste_casse_exacte(valeur):
    # Constaté sur VENU360 réel (23/11/2026) : toute valeur inconnue devient « BW 6 » sans erreur.
    assert dbx_hiqnet.valider_valeur(XO_TYPE, valeur) is not None


def test_dbx_liste_valeur_exacte_acceptee():
    assert dbx_hiqnet.valider_valeur(XO_TYPE, "LR 24") is None
    assert dbx_hiqnet.valider_valeur("\\\\Preset\\OutputGains\\SV\\Channel_1_Mute", "1") is None


def test_dbx_virgule_decimale_refusee():
    # L'appareil tronque « -12,5dB » en -12.0 dB.
    assert dbx_hiqnet.valider_valeur(SEUIL, "-12,5dB") is not None
    assert dbx_hiqnet.valider_valeur(SEUIL, "-12.5dB") is None


@pytest.mark.parametrize("demande,relu,ok", [
    ("1.5ms", "1.5ms/1.69ft/0.51m", True),         # format composé du délai
    ("1.19ms", "1.19ms/1.34ft/0.41m", True),
    ("1.5ms", "1.19ms/1.34ft/0.41m", False),
    ("1234.5Hz", "1.23kHz", True),                  # arrondi d'affichage
    ("-12.35dB", "-12.4dB", True),
    ("-12.5dB", "-12.0dB", False),
    ("LR 24", "LR 24", True),
    ("LR 24", "BW 6", False),                       # remplacement silencieux
    ("lr 24", "LR 24", False),                      # plus de comparaison insensible à la casse
    ("Out", "Out", True),
    ("-20dB", None, False),
])
def test_dbx_relecture_conforme(demande, relu, ok):
    assert dbx_hiqnet.relecture_conforme(demande, relu) is ok


MUTE = "\\\\Preset\\OutputGains\\SV\\Channel_1_Mute"


def test_dbx_relecture_mute_1_0_seulement_sur_mute():
    assert dbx_hiqnet.relecture_conforme("1", "On", MUTE)
    assert dbx_hiqnet.relecture_conforme("0", "Off", MUTE)
    assert not dbx_hiqnet.relecture_conforme("1", "Off", MUTE)
    assert not dbx_hiqnet.relecture_conforme("1", "On", XO_TYPE)


@pytest.mark.parametrize("valeur", ["out", "OUT", " Out", "LR 24 ", " -12.5dB"])
def test_dbx_valeur_non_exacte_refusee(valeur):
    chemin = SEUIL if "dB" in valeur else ("\\\\Preset\\CrossoverIIR1\\SV\\Band_1_HPFrequency" if "ut" in valeur.lower() else XO_TYPE)
    assert dbx_hiqnet.valider_valeur(chemin, valeur) is not None


def test_dbx_virgule_refusee_meme_hors_carte():
    motif = dbx_hiqnet.valider_valeur("\\\\Preset\\Inconnu1\\SV\\X", "1,5")
    assert motif is not None and "virgule" in motif


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


# --- Dimensionnement : exemples chiffrés de Boyce (2014) §11.9, §11.6.6, §11.6.7 ---
def test_perte_cable_exemple_boyce():
    # 2 x 20 m de 16 AWG (1,31 mm²) sur une bobine d'environ 6 ohms : Boyce trouve 0,73 dB (~15 % de puissance)
    assert calc_systeme.perte_cable_db(20, 1.31, 6.0) == pytest.approx(0.73, abs=0.01)
    assert calc_systeme.perte_cable_db(20, 4.0, 6.0) < 0.25          # section plus forte : perte plus faible
    with pytest.raises(ValueError):
        calc_systeme.perte_cable_db(20, 0, 6.0)


def test_puissance_requise_exemple_boyce():
    # 95 dB SPL à 20 m, sensibilité 97 dB, marge 6 dB, câble 0,73 dB -> Boyce : 1183 W (avec chute arrondie à 26 dB)
    r = calc_systeme.puissance_requise(95, 20, 97, marge_db=6, perte_cable=0.73)
    assert r["spl_requis_1m_db"] == pytest.approx(127.0, abs=0.05)
    assert r["puissance_w"] == pytest.approx(1183, rel=0.01)
    assert calc_systeme.puissance_requise(95, 20, 97, marge_db=9)["puissance_w"] == pytest.approx(
        2 * calc_systeme.puissance_requise(95, 20, 97, marge_db=6)["puissance_w"], rel=0.01)  # +3 dB = x2


def test_delai_renfort_exemple_boyce():
    # 45 m de la façade, 10 m du renfort, c = 340 m/s, précédence 10 ms -> Boyce : 113 ms
    t = (340 - 331.3) / 0.606
    assert calc_systeme.delai_renfort_ms(45, 10, t, precedence_ms=10) == pytest.approx(113, abs=0.5)
    # défaut : raccord aligné en phase, sans marge (McCarthy 2007 ch. 10 : la marge crée un peigne dès 50 Hz)
    assert calc_systeme.delai_renfort_ms(45, 10, t) == pytest.approx(35 / 340 * 1000, abs=0.01)
    with pytest.raises(ValueError):
        calc_systeme.delai_renfort_ms(5, 10)
    with pytest.raises(ValueError):
        calc_systeme.delai_renfort_ms(45, 10, precedence_ms=-1)


def test_distance_critique_exemple_boyce():
    # 2500 m³, RT60 1 s, Q 10 -> Boyce : public « bien au-delà d'environ 18 m » = 2 Dc
    assert 2 * calc_systeme.distance_critique_m(2500, 1.0, 10) == pytest.approx(18, abs=0.2)


def test_cli_dimensionnement_sans_config(capsys):
    sys.argv = ["calc_systeme.py", "--puissance", "95", "20", "97", "--cable", "20", "1.31", "6"]
    calc_systeme.main()
    out = capsys.readouterr().out
    assert "0.73 dB" in out and "Seuils limiteurs : inchangés" in out and "Ne change jamais le plafond" in out


# --- McCarthy 2007 : résolution d'analyse, moyenne spatiale, positions de micro ---
def test_resolution_bandes_osm():
    import osm_client
    ft = [[f, 0.0, 1.0, 0.0, 1.0] for f in np.geomspace(20, 20000, 4000)]
    b3, b24 = osm_client.tiers_octave(ft), osm_client.tiers_octave(ft, n=24)
    assert len(b3) == 29 and b3[0]["f_hz"] == 24.8 and b3[-1]["f_hz"] == 16000.0   # mêmes tiers d'octave qu'avant
    assert 230 <= len(b24) <= 242 and all(abs(b["mag_db"]) < 1e-6 for b in b24)
    with pytest.raises(ValueError):
        osm_client.tiers_octave(ft, n=5)


def test_resolution_bandes_mesure_tf():
    f = np.linspace(1, 24000, 48000); H = np.ones_like(f, dtype=complex); ir = np.zeros(256); ir[10] = 1
    r3 = mesure_tf.resume(f, H, ir); r24 = mesure_tf.resume(f, H, ir, n=24)
    assert len(r24["tiers_octave"]) > 7 * len(r3["tiers_octave"])


def test_moyenne_en_puissance_resiste_aux_creux():
    # Exemple de McCarthy (ch. 10) : pics de 5 / 5,5 / 6 dB et une annulation à -19,2 dB.
    # Moyenne en dB : -0,7 dB (le livre imprime -0,4 : erreur de calcul, la conclusion tient) ; l'annulation efface
    # trois additions. Moyenne en puissance (celle du kit) : +4,3 dB, proche des +5,5 dB entendus.
    import math
    vals = [5, 5.5, 6, -19.2]
    assert sum(vals) / 4 == pytest.approx(-0.675, abs=0.001)
    assert 10 * math.log10(sum(10 ** (v / 10) for v in vals) / 4) == pytest.approx(4.27, abs=0.01)


def test_plan_micros_onax_offax():
    import plan_3d
    import yaml
    fiche = yaml.safe_load((RACINE / "data" / "evenements" / "exemple_2piles_exterieur.yaml").read_text(encoding="utf-8"))
    cfg = yaml.safe_load((RACINE / "data" / "configs" / f"{fiche['systeme']['config']}.yaml").read_text(encoding="utf-8"))
    sc = plan_3d.construire(fiche, cfg)
    m = {x["id"]: x for x in sc["mics"]}
    assert m["P2"]["role"] == "eq" and m["P3"]["role"] == "offax" and m["P4"]["role"] == "offax"
    assert m["P2"]["x"] == m["P1"]["x"]                      # ONAX sur l'axe de la pile de référence
    assert abs(m["P2"]["x"]) > 1                             # jamais sur la ligne centrale G/D


# ---------------- audit du 23/11 (2e passe) : réglage de sortie, dbx rappel/stocker/rpc, TIPI, OSM ----------------
def code_sortie(fonction, argv, monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", argv)
    try:
        fonction()
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def test_seuil_refuse_si_reglage_sortie_non_confirme():
    with pytest.raises(ValueError, match="non confirmé"):
        garde_fous.verifier_plafond(-25, CONFIG_BRUT, "SUB", "dBFS", False)
    r = calc_systeme.calc_voie(*_voie_sub(CONFIG_BRUT))
    assert any("NON CONFIRMÉ" in x for x in r["alertes"])


def _voie_sub(chemin):
    mat, cfg = calc_systeme.charger(chemin)
    return next(v for v in cfg["voies"] if v["nom"] == "SUB"), mat, cfg


def test_dbx_rappel_exige_protection_validee(faux_dbx, monkeypatch, tmp_path):
    assert lancer_dbx(faux_dbx, "rappel", "3", "--appliquer", monkeypatch=monkeypatch) != 0
    assert not (tmp_path / "instantanes").exists()
    assert lancer_dbx(faux_dbx, "rappel", "0", "--appliquer", "--protection-validee", monkeypatch=monkeypatch) != 0


def test_dbx_refus_sans_connexion(monkeypatch):
    # Port fermé : un refus décidable sans l'appareil doit tomber AVANT toute tentative de connexion.
    def interdit(*a, **k):
        raise AssertionError("connexion tentée pour une demande refusée")
    monkeypatch.setattr(dbx_hiqnet.socket, "create_connection", interdit)
    for args in (("rappel", "3", "--appliquer"), ("rpc", "Load", "1"), ("stocker", "abc")):
        monkeypatch.setattr(sys, "argv", ["dbx_hiqnet.py", args[0], "127.0.0.1", *args[1:]])
        with pytest.raises(SystemExit):
            dbx_hiqnet.main()


def test_dbx_numero_preset_normalise(faux_dbx, monkeypatch, tmp_path):
    assert lancer_dbx(faux_dbx, "stocker", "02", "--appliquer", monkeypatch=monkeypatch) == 0
    assert "preset 2 ('TEST')" in (tmp_path / "ecritures.log").read_text(encoding="utf-8")


def test_dbx_rappel_fait_un_instantane_complet(faux_dbx, monkeypatch, tmp_path):
    assert lancer_dbx(faux_dbx, "rappel", "3", "--appliquer", "--protection-validee", monkeypatch=monkeypatch) == 0
    snaps = list((tmp_path / "instantanes").glob("*avant-rappel-3.json"))
    assert len(snaps) == 1 and len(dbx_hiqnet.aplatir(json.loads(snaps[0].read_text(encoding="utf-8")))) > 20
    log = (tmp_path / "ecritures.log").read_text(encoding="utf-8")
    assert "RAPPEL preset 3" in log and "chargé = SIMU" in log


def test_dbx_stocker_fait_un_instantane(faux_dbx, monkeypatch, tmp_path):
    assert lancer_dbx(faux_dbx, "stocker", "2", "--appliquer", monkeypatch=monkeypatch) == 0
    assert list((tmp_path / "instantanes").glob("*avant-stocker-2.json"))
    assert "'TEST'" in (tmp_path / "ecritures.log").read_text(encoding="utf-8")   # nom de l'ancien preset journalisé


def test_dbx_rpc_ecriture_refusee(faux_dbx, monkeypatch):
    for m in ("Load", "Store", "FactoryRestore", "Inconnue"):
        assert lancer_dbx(faux_dbx, "rpc", m, "1", "--appliquer", monkeypatch=monkeypatch) != 0
    assert lancer_dbx(faux_dbx, "rpc", "Get", "1", "100", "presetName", monkeypatch=monkeypatch) == 0


class _FauxTipi(socketserver.StreamRequestHandler):
    """Appareil TIPI minimal : GET → NOTIFY, SET mémorisé (sauf Out2/Gain, qui ignore l'écriture)."""
    valeurs = {"Out1/Gain": "0.0dB", "Out2/Gain": "0.0dB", "Out1/Mute": "no"}
    recu: list = []

    def handle(self):
        buf = b""
        while True:
            b = self.request.recv(4096)
            if not b:
                return
            buf += b
            while b"\r" in buf:
                ligne, buf = buf.split(b"\r", 1)
                t = ligne.decode().lstrip("$").split()
                _FauxTipi.recu.append(" ".join(t))
                if t[0] == "GET":
                    v = self.valeurs.get(t[1])
                    rep = f"$NOTIFY {t[1]} {v}\r" if v is not None else "$ERROR UnsupportedMethod 09\r"
                    self.request.sendall(rep.encode())
                elif t[0] == "SET" and t[1] != "Out2/Gain":
                    self.valeurs[t[1]] = t[2]


@pytest.fixture
def faux_tipi(tmp_path, monkeypatch):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _FauxTipi)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(linea_tipi, "JOURNAL", tmp_path)
    _FauxTipi.recu = []
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def lancer_tipi(port, *args, monkeypatch):
    return code_sortie(linea_tipi.main, ["linea_tipi.py", args[0], "127.0.0.1", *args[1:], "--port", str(port)],
                       monkeypatch)


def test_tipi_injection_refusee_avant_tout_envoi(faux_tipi, monkeypatch):
    for methode, valeur in (("Out1/Gain\rSET Out1/Mute yes", "0"), ("Out1/Gain", "0\r$SET Out1/Mute yes"),
                            ("Out1/Gain", "0 dB"), ("Out1/Gain\n", "0")):
        assert lancer_tipi(faux_tipi, "set", methode, valeur, "--appliquer", "--protection-validee",
                           monkeypatch=monkeypatch) != 0
    assert lancer_tipi(faux_tipi, "get", "Out1/Gain\rSET Out1/Mute yes", monkeypatch=monkeypatch) != 0
    assert _FauxTipi.recu == []                                   # rien n'a atteint l'appareil


def test_tipi_methode_hors_liste_refusee(faux_tipi, monkeypatch):
    for m in ("Out1/Meter", "Out1/LimThresh", "Factory/Reset", "Out9/Gain", "InE/Gain"):
        assert lancer_tipi(faux_tipi, "set", m, "1", "--appliquer", "--protection-validee", monkeypatch=monkeypatch) != 0
    assert _FauxTipi.recu == []


def test_tipi_relecture_stricte(faux_tipi, monkeypatch, tmp_path):
    assert lancer_tipi(faux_tipi, "set", "Out1/Gain", "-3.0dB", "--appliquer", monkeypatch=monkeypatch) == 0
    assert lancer_tipi(faux_tipi, "set", "Out2/Gain", "-3.0dB", "--appliquer", monkeypatch=monkeypatch) != 0
    assert "ECART" in (tmp_path / "ecritures.log").read_text(encoding="utf-8")
    assert list((tmp_path / "instantanes").glob("*tipi-avant-set.json"))


def test_tipi_pas_maximum(faux_tipi, monkeypatch):
    _FauxTipi.valeurs["Out1/Gain"] = "0.0dB"
    assert lancer_tipi(faux_tipi, "set", "Out1/Gain", "+15dB", monkeypatch=monkeypatch) != 0     # même en simulation
    assert lancer_tipi(faux_tipi, "set", "Out1/Gain", "+3dB", monkeypatch=monkeypatch) == 0
    assert linea_tipi.controle_pas("Out1/Delay", "1.0ms", "1.6ms") and not linea_tipi.controle_pas("Out1/Delay", "1ms", "1.5ms")
    assert linea_tipi.relu_conforme("15000Hz", "15kHz")


def _osm(monkeypatch, *args):
    envoye = []
    monkeypatch.setattr(osm_client, "journaliser", lambda t: None)
    monkeypatch.setattr(osm_client, "ecouter", lambda duree=5.0: {"1.2.3.4": {"generator": "uuid-gen"}})
    monkeypatch.setattr(osm_client, "requete", lambda hote, objet, timeout=5.0: envoye.append(objet) or {"ok": 1})
    code = code_sortie(osm_client.main, ["osm_client.py", "generateur", "1.2.3.4", *args], monkeypatch)
    return code, envoye


def test_osm_generateur_niveau_impose(monkeypatch):
    for args in (("--on", "--appliquer"),                          # niveau mémorisé inconnu
                 ("--on", "--off", "--gain", "-30", "--appliquer"),
                 ("--on", "--gain", "-6", "--appliquer"),         # trop fort
                 ("--on", "--gain", "nan", "--appliquer"),        # NaN passait le test « > -10 »
                 ("--on", "--gain=-inf", "--appliquer"),
                 ("--appliquer",)):                               # rien à faire
        code, envoye = _osm(monkeypatch, *args)
        assert code != 0 and envoye == []
    code, envoye = _osm(monkeypatch, "--on", "--gain", "-30", "--appliquer")
    assert code == 0 and [e["data"] for e in envoye] == [{"gain": -30.0}, {"enabled": True}]   # gain AVANT allumage
    code, envoye = _osm(monkeypatch, "--off", "--appliquer")
    assert code == 0 and envoye[0]["data"] == {"enabled": False}


def test_osm_moyenne_grilles_differentes_refusee(tmp_path, monkeypatch):
    def mesure(nom, freqs):
        p = tmp_path / nom
        p.write_text(json.dumps({"ftdata": [[f, 0, 1.0, 0.0, 1.0] for f in freqs]}), encoding="utf-8")
        return str(p)
    a, b = mesure("a.json", [100, 200, 400]), mesure("b.json", [100, 210, 400])
    c = mesure("c.json", [100, 200, 400])
    argv = ["osm_client.py", "moyenne"]
    assert code_sortie(osm_client.main, argv + [a, b, "-o", str(tmp_path / "m.json")], monkeypatch) != 0
    assert code_sortie(osm_client.main, argv + [a, c, "-o", str(tmp_path / "m.json")], monkeypatch) == 0
    assert code_sortie(osm_client.main, ["osm_client.py", "lire", "1.2.3.4"], monkeypatch) != 0   # argument manquant


def test_osm_coherence_nulle_toleree(tmp_path, monkeypatch):
    # OSM v1.5.2 réel : cohérence NaN → null dans le JSON là où il n'y a pas de signal (trouvé le 23/11/2026).
    ft = [[100.0, 0, 1.0, 0.0, None], [125.0, 0, 1.0, 0.0, 0.9], [160.0, 0, None, 0.0, 0.9]]
    propre = osm_client.nettoyer_ft(ft)
    assert propre == [[100.0, 0, 1.0, 0.0, 0.0], [125.0, 0, 1.0, 0.0, 0.9]]
    assert osm_client.tiers_octave(propre, (90, 200))                    # plus de TypeError
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"ftdata": ft, "timeData": [[0.0, None], [0.1, 0.5]]}), encoding="utf-8")
    assert len(osm_client.charger(str(p))["ftdata"]) == 2
    assert osm_client.pic_ir([[0.0, None], [0.1, 0.5]])["temps_pic_ms"] == 0.1


# ---------------- auto-description du VENU360 (constats firmware 2.0.5.0, 24/11/2026) ----------------
import dbx_description as dd  # noqa: E402

D_SEUIL = {"valeur": "-23.7dB", "min": "-60.0dB", "max": "0.0dB", "type": "Float32", "capteur": False, "actif": True,
           "choix": [], "min_brut": "-60", "max_brut": "0"}
D_DELAI = {"valeur": "1.19ms/1.34ft/0.41m", "min": "0ms/0ft/0m", "max": "1000ms/1127ft/343m", "type": "Float32",
           "capteur": False, "actif": True, "choix": [], "min_brut": "0", "max_brut": "100000"}
D_TYPE = {"valeur": "LR 24", "min": "BW 6", "max": "LR 48", "type": "UByte", "capteur": False, "actif": True,
          "choix": ["BW 6", "BW 12", "LR 24", "LR 48"]}
D_HPF = {"valeur": "1.5kHz", "min": "Out", "max": "20kHz", "type": "Float32", "capteur": False, "actif": True,
         "choix": [], "min_brut": "15.9", "max_brut": "20000"}
D_RELEASE = {"valeur": "200dB/s", "min": "360dB/s", "max": "5dB/s", "type": "Float32", "capteur": False, "actif": True,
             "choix": [], "min_brut": "5", "max_brut": "360"}
D_LIFT = {"valeur": "60m", "min": "5s", "max": "60m", "type": "Float32", "capteur": False, "actif": True, "choix": [],
          "min_brut": "5", "max_brut": "3600"}


@pytest.mark.parametrize("texte,attendu", [
    ("1.23kHz", (1230.0, "freq", 10.0)),
    ("1kHz", (1000.0, "freq", 10.0)),                 # 3 chiffres significatifs, pas ±500 Hz
    ("1.5ms/1.69ft/0.51m", (1.5, "temps", 0.01)),     # pas plafonné : 0.4 relu 0 = écart
    ("60m", (3600000.0, "temps", 60000.0)),           # minutes (AFS)
    ("3m 10s", (190000.0, "temps", 1000.0)),
    ("4:1", (4.0, "rapport", 1.0)),
    ("-12.4dB", (-12.4, "niveau", 0.1)),
])
def test_dd_analyser(texte, attendu):
    v, f, p = dd.analyser(texte)
    assert (round(v, 6), f, round(p, 6)) == attendu


@pytest.mark.parametrize("desc,valeur", [
    (D_SEUIL, "-61dB"), (D_SEUIL, "3dB"), (D_SEUIL, "abc"), (D_SEUIL, "-12,5dB"), (D_SEUIL, " -12.5dB"),
    (D_SEUIL, "-20"),                                 # sans unité
    (D_SEUIL, "-20Hz"),                               # mauvaise famille
    (D_DELAI, "1.5"),                                 # serait lu en SECONDES par la machine
    (D_DELAI, "1500ms"), (D_DELAI, "5m"),             # « m » = minutes : hors plage
    (D_TYPE, "lr 24"), (D_TYPE, "LR24"), (D_TYPE, "LR 96"),
    (D_HPF, "25kHz"), (D_HPF, "out"),
    (D_RELEASE, "400dB/s"),                           # Min > Max déclarés : bornes remises dans l'ordre
    ({**D_SEUIL, "capteur": True}, "-20dB"), ({**D_SEUIL, "actif": False}, "-20dB"),
    ({**D_SEUIL, "type": "String", "valeur": "-29.3 -29.3"}, "-29.3 -29.3"),
])
def test_dd_valider_refuse(desc, valeur):
    assert dd.valider_machine(desc, valeur) is not None


@pytest.mark.parametrize("desc,valeur", [
    (D_SEUIL, "-24.7dB"), (D_SEUIL, "0dB"), (D_DELAI, "1.5ms"), (D_TYPE, "LR 48"), (D_HPF, "Out"),
    (D_HPF, "1680Hz"), (D_RELEASE, "220dB/s"), (D_LIFT, "50m"), (D_LIFT, "10s"),
])
def test_dd_valider_accepte(desc, valeur):
    assert dd.valider_machine(desc, valeur) is None


def test_dd_relecture_et_arrondis():
    assert dd.relecture_machine(D_DELAI, "1.5ms", "1.5ms/1.69ft/0.51m")
    assert dd.relecture_machine(D_HPF, "1234Hz", "1.23kHz")
    assert not dd.relecture_machine(D_HPF, "1120Hz", "1kHz")              # plus de tolérance de ±500 Hz
    assert not dd.relecture_machine(D_TYPE, "LR 24", "BW 6")
    assert dd.relecture_machine(D_LIFT, "190s", "3m 10s")
    # grille interne de la machine
    assert dd.arrondi_machine(D_RELEASE, "218dB/s", "220dB/s")
    assert dd.arrondi_machine(None, "-25.7dB", "-25.5dB", "\\\\Preset\\InMixer1\\SV\\Output_1_Input_1_Gain")
    assert not dd.arrondi_machine(None, "-25.7dB", "-25.5dB", SEUIL)       # jamais sur un seuil
    assert not dd.arrondi_machine(None, "1.5ms", "1.6ms")                   # jamais sur un temps
    assert dd.arrondi_machine(None, "1.7ms", "1.71ms/1.93ft/0.59m", "\\\\Preset\\DelayOut3\\SV\\Amount")  # 1 échantillon
    assert not dd.arrondi_machine(None, "1.7ms", "1.73ms", "\\\\Preset\\DelayOut3\\SV\\Amount")      # > 1 échantillon
    assert not dd.arrondi_machine(None, "10ms", "10.01ms", "\\\\Preset\\Limiter1\\SV\\Attack")       # pas une attaque


def test_dd_corrections_q():
    assert any(m.search("\\\\Preset\\PEQ1\\SV\\Band_3_Q") and c["max"] == "16" for m, c in dd.CORRECTIONS)


def test_dd_signal_plafonne():
    assert dd.est_signal("\\\\Preset\\SignalGenerator\\SV\\Signal Amplitude")
    assert dd.est_signal("\\\\Preset\\InMixer1\\SV\\SignalGeneratorControl")
    assert dd.verifier_signal("\\\\Preset\\SignalGenerator\\SV\\Signal Amplitude", "-20dB", None)
    assert dd.verifier_signal("\\\\Preset\\SignalGenerator\\SV\\Signal Amplitude", "-40dB", None) is None
    assert dd.verifier_signal("\\\\Preset\\SignalGenerator\\SV\\Signal Generator", "On", "-10dB")
    assert dd.verifier_signal("\\\\Preset\\SignalGenerator\\SV\\Signal Generator", "On", "-49dB") is None


@pytest.mark.parametrize("chemin", [
    "\\\\Preset\\InputGains\\SV\\Channel_1_Gain", "\\\\Preset\\InMixer2\\SV\\Output_1_Input_1_Gain",
    "\\\\Preset\\MidRouter5\\SV\\Output_1_MasterGain", "\\\\Preset\\OutputGains\\SV\\Channel_3_Gain",
    "\\\\Node\\Settings\\SV\\Output1Gain",
])
def test_dbx_protection_etendue(chemin):
    assert dbx_hiqnet.est_protection(chemin)


def test_dbx_simulateur_se_decrit_et_refuse(faux_dbx, monkeypatch):
    # bornes données par la machine (simulée) : -61 dB refusé avant envoi
    assert lancer_dbx(faux_dbx, "set", SEUIL, "-61.0dB", "--appliquer", "--protection-validee",
                      "--config", CONFIG, "--voie", "SUB", monkeypatch=monkeypatch) != 0
    # type de filtre : libellé exact de la liste de la machine
    assert lancer_dbx(faux_dbx, "set", "\\\\Preset\\CrossoverIIR1\\SV\\Band_1_HPType", "LR24", "--appliquer",
                      "--protection-validee", monkeypatch=monkeypatch) != 0


def test_dbx_generateur_exige_signal_valide(faux_dbx, monkeypatch):
    amp = "\\\\Preset\\SignalGenerator\\SV\\Signal Amplitude"
    assert lancer_dbx(faux_dbx, "set", amp, "-50dB", "--appliquer", monkeypatch=monkeypatch) != 0
    assert lancer_dbx(faux_dbx, "set", amp, "-20dB", "--appliquer", "--signal-valide", monkeypatch=monkeypatch) != 0
    assert lancer_dbx(faux_dbx, "set", amp, "-50dB", "--appliquer", "--signal-valide", monkeypatch=monkeypatch) == 0


def test_dbx_restaurer_et_autotest_simulateur(faux_dbx, monkeypatch, tmp_path):
    ch = "\\\\Preset\\CrossoverIIR1\\SV\\Band_2_Gain"
    with dbx_hiqnet.Dbx("127.0.0.1", faux_dbx) as d:
        avant = dd.instantane_complet(d)
        d.set(ch, "-6.0dB")
        r = dd.restaurer(d, avant)
        assert r["ecarts"] == 1 and not r["echecs"]
        assert d.get(ch) == avant[ch]
        rap = dd.autotest(d)
        assert rap["testes"] > 0 and not rap["echecs"] and not rap["refus_manques"] and not rap["ecarts_finaux"]
        assert d.get("\\\\Preset\\SignalGenerator\\SV\\Signal Generator") == "Off"     # jamais touché
        assert all(d.get(m) == avant[m] for m in dd.SORTIES_MUTE)                      # mutes remis


def test_dd_post_revue_valeurs():
    assert not dd.relecture_machine(D_DELAI, "0.4ms", "0ms/0ft/0m")       # délai relu sans décimales : non conforme
    assert dd.valider_machine(D_SEUIL, "-20dB/s") is not None             # « / » refusé hors dB/s, dB/oct
    assert dd.valider_machine(D_SEUIL, "-20dBu") is not None              # unité de niveau différente de l'affichage
    assert not dd.ecrivable("\\\\Node\\Settings\\SV\\Output1Gain", {**D_SEUIL, "valeur": "+22dBu"})
    assert dbx_hiqnet.est_protection("\\\\Preset\\CrossoverIIR1\\SV\\Band_1_LPFrequency")
    assert dbx_hiqnet.est_protection("\\\\Preset\\CrossoverIIR1\\SV\\Band_1_LPType")


def test_dd_restaurer_delai_compose(faux_dbx):
    ch = "\\\\Preset\\DelayOut1\\SV\\Amount"
    with dbx_hiqnet.Dbx("127.0.0.1", faux_dbx) as d:
        avant = dd.instantane_complet(d)
        d.set(ch, "1.5ms")
        r = dd.restaurer(d, avant)                     # cible affichée « 0ms/0ft/0m » : écrite en ms
        assert r["conforme"] and not r["refuses"] and d.get(ch) == avant[ch]


def test_dd_mute_non_confirme_rien_ecrit(faux_dbx):
    ch = "\\\\Preset\\CrossoverIIR1\\SV\\Band_2_Gain"
    with dbx_hiqnet.Dbx("127.0.0.1", faux_dbx) as d:
        avant = dd.instantane_complet(d)
        set_orig = d.set
        d.set = lambda c, v: None if c in dd.SORTIES_MUTE else set_orig(c, v)   # mute qui ne prend pas
        with pytest.raises(dd.SortiesNonMutees):
            dd.restaurer(d, {**avant, ch: "-6.0dB"})
        with pytest.raises(dd.SortiesNonMutees):
            dd.autotest(d)
        assert d.get(ch) == avant[ch]


def test_dd_restaurer_seuil_plus_haut_refuse_et_reste_mute(faux_dbx):
    seuil = "\\\\Preset\\Limiter1\\SV\\Threshold"
    with dbx_hiqnet.Dbx("127.0.0.1", faux_dbx) as d:
        avant = dd.instantane_complet(d)
        bas = f"{dd.analyser(avant[seuil])[0] - 5:.1f}dB"                  # simulateur partagé : relatif à l'état
        d.set(seuil, bas)
        r = dd.restaurer(d, avant)                                          # bas -> avant : seuil relevé
        assert any(x["chemin"] == seuil for x in r["refuses"])
        assert d.get(seuil).strip() == bas
        assert not r["conforme"] and r["sorties"] == "RESTÉES MUTÉES"
        assert all(d.get(m) == "On" for m in dd.SORTIES_MUTE)
        r = dd.restaurer(d, avant, seuils_hausse_autorises=True)            # décision explicite
        assert r["conforme"] and d.get(seuil).strip() == avant[seuil]
        assert all(d.get(m) == avant[m] for m in dd.SORTIES_MUTE)


# --- reglages_pile : temps de limiteur (Proel) et délais de pile ---
import reglages_pile  # noqa: E402


@pytest.mark.parametrize("hpf,att,rel_db_s", [(35, 16.0, 34), (90, 8.0, 68), (400, 2.0, 271), (1600, 0.5, 360), (None, 45.0, 12)])
def test_temps_limiteur_proel(hpf, att, rel_db_s):
    r = reglages_pile.temps_limiteur(hpf)
    assert r["attaque_ms"] == att and r["relachement_ms"] == att * 16
    assert r["relachement_appareil_db_s"] == rel_db_s


def test_delais_pile_coincidente_et_decalee():
    base = {"temperature_c": 20, "voies": [{"nom": "BAS"}, {"nom": "HAUT"}],
            "crossover": {"BAS": {"lpf": "LR24 1000 Hz"}, "HAUT": {"hpf": "LR24 1000 Hz"}},
            "geometrie_pile": {"BAS": {"profondeur_cm": 0, "hauteur_cm": 170}, "HAUT": {"profondeur_cm": 0, "hauteur_cm": 170}}}
    r = reglages_pile.delais(base, 10, 1.7)
    assert abs(r["voies"]["BAS"]["delai_ms"]) < 1e-6 and abs(r["voies"]["HAUT"]["delai_ms"]) < 1e-6   # LR en phase
    base["geometrie_pile"]["HAUT"]["profondeur_cm"] = 34.34          # aigu 1 ms plus loin -> retarder le bas de 1 ms
    r = reglages_pile.delais(base, 10, 1.7)
    assert abs(r["voies"]["BAS"]["delai_ms"] - 1.0) < 0.02 and r["voies"]["HAUT"]["delai_ms"] == 0
    assert r["paires"][0]["somme_db"] > -0.1



def test_dbx_stocker_nom_refuse_avant_connexion(monkeypatch, tmp_path):
    monkeypatch.setattr(dbx_hiqnet, "JOURNAL", tmp_path)
    for args in (["stocker", "27", "--nom", "Caisson élevé"], ["stocker", "27", "--nom", 'a"b'], ["rappel", "27", "--nom", "X"]):
        monkeypatch.setattr(sys, "argv", ["dbx_hiqnet.py", args[0], "127.0.0.1", *args[1:], "--port", "9", "--appliquer",
                                          "--protection-validee"])
        with pytest.raises(SystemExit) as e:
            dbx_hiqnet.main()
        assert "REFUS" in str(e.value)



def test_equilibre_gains_cki():
    import calc_systeme
    mat, cfg = calc_systeme.charger(str(Path(__file__).resolve().parent.parent / "data" / "configs" / "ma-sono-cki.yaml"))
    eq = reglages_pile.equilibre_gains(cfg, mat)
    assert {k: v["gain_propose_db"] for k, v in eq.items()} == {"SUB": 0.0, "KICK": -6.0, "MEDIUM": -2.0, "HIGH": -4.0}
    assert all(v["gain_propose_db"] <= 0 for v in eq.values())            # jamais de gain positif
