"""Réglage de n'importe quel paramètre : pas maximum, alias par voie, lots avec retour arrière, niveau de sortie
analogique, ordre de la chaîne lu dans la topologie, mètres (simulateur faux_venu360, aucune machine)."""
import json
import sys
import threading
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))
import dbx_description as dd  # noqa: E402
import dbx_hiqnet  # noqa: E402
import dbx_plan  # noqa: E402
import dbx_reglage as dr  # noqa: E402
import faux_venu360  # noqa: E402
import metres  # noqa: E402

CKI = str(RACINE / "data" / "configs" / "ma-sono-cki.yaml")
XG = "\\\\Preset\\CrossoverIIR1\\SV\\Band_1_Gain"
DL = "\\\\Preset\\DelayOut1\\SV\\Amount"
A = faux_venu360.ARBRE


def _K(c):
    return "\\\\Preset\\" + c.replace("/", "\\")


@pytest.fixture
def faux_dbx(tmp_path, monkeypatch):
    for bloc in ("CrossoverIIR1/SV", "DelayOut1/SV", "OutputGains/SV", "PEQOut2/SV", "Limiter4/SV"):
        monkeypatch.setitem(A, _K(bloc), dict(A[_K(bloc)]))
    monkeypatch.setitem(A, "\\\\Node\\Settings\\SV", dict(A["\\\\Node\\Settings\\SV"]))
    srv = faux_venu360.S(("127.0.0.1", 0), faux_venu360.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(dbx_hiqnet, "JOURNAL", tmp_path)
    monkeypatch.delenv("DBX_MDP", raising=False)
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def lancer(port, *args, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["dbx_hiqnet.py", args[0], "127.0.0.1", *args[1:], "--port", str(port)])
    try:
        dbx_hiqnet.main()
        return 0
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1


def _log(tmp_path):
    f = tmp_path / "ecritures.log"
    return f.read_text(encoding="utf-8") if f.exists() else ""


# ---------- règles pures ----------
def test_regles_de_pas():
    assert dr.regle_pas(XG) == ("niveau", 3.0, "dB")
    assert dr.regle_pas(DL) == ("temps", 0.5, "ms")
    assert dr.regle_pas("\\\\Preset\\CrossoverIIR2\\SV\\Band_1_Phase") == ("angle", 45.0, "deg")
    assert dr.regle_pas("\\\\Preset\\PEQOut2\\SV\\Band_3_Gain")[1] == 3.0
    assert dr.regle_pas("\\\\Preset\\Limiter1\\SV\\Threshold") is None          # seuil : plafond, pas de pas
    assert dr.regle_pas("\\\\Preset\\SignalGenerator\\SV\\Signal Amplitude") is None
    assert dr.regle_pas("\\\\Node\\Settings\\SV\\Output1Gain") is None
    assert dr.decouper(0, -6, 3) == [-3.0, -6.0]
    assert dr.decouper(0, 2, 0.5) == [0.5, 1.0, 1.5, 2.0]
    assert dr.decouper(0, -7, 3) == pytest.approx([-2.3333, -4.6667, -7.0])
    assert dr.decouper(1, 2, 3) == [2.0]


def test_alias_cki():
    assert dr.resoudre("MEDIUM.eq.2.gain", CKI) == ("\\\\Preset\\PEQOut2\\SV\\Band_2_Gain", "MEDIUM")
    assert dr.resoudre("sub.limiteur.seuil", CKI) == ("\\\\Preset\\Limiter4\\SV\\Threshold", "SUB")
    assert dr.resoudre("HIGH.delai", CKI) == ("\\\\Preset\\DelayOut1\\SV\\Amount", "HIGH")
    assert dr.resoudre("KICK.hpf.freq", CKI) == ("\\\\Preset\\CrossoverIIR3\\SV\\Band_1_HPFrequency", "KICK")
    assert dr.resoudre("KICK.gain", CKI)[0] == "\\\\Preset\\CrossoverIIR3\\SV\\Band_1_Gain"
    assert dr.resoudre("ENTREE.eq.12.q", CKI) == ("\\\\Preset\\PEQ1\\SV\\Band_12_Q", None)
    assert dr.resoudre("SYSTEME.niveau_sortie.3", CKI) == ("\\\\Node\\Settings\\SV\\Output3Gain", None)
    assert dr.resoudre("SORTIE5.mute", CKI) == ("\\\\Preset\\OutputGains\\SV\\Channel_5_Mute", None)
    for mauvais in ("BASSE.gain", "MEDIUM.eq.9.gain", "ENTREE.eq.13.q", "HIGH.limiteur.vitesse", "SYSTEME.reboot"):
        with pytest.raises(dr.Refus):
            dr.resoudre(mauvais, CKI)


# ---------- set : pas maximum ----------
def test_set_pas_maximum_refuse_puis_par_pas(faux_dbx, monkeypatch, tmp_path):
    assert lancer(faux_dbx, "set", XG, "-6.0dB", "--appliquer", monkeypatch=monkeypatch) != 0
    assert A[_K("CrossoverIIR1/SV")]["Band_1_Gain"] == "0.0dB"                        # rien écrit
    assert lancer(faux_dbx, "set", XG, "-6.0dB", "--appliquer", "--par-pas", monkeypatch=monkeypatch) == 0
    assert A[_K("CrossoverIIR1/SV")]["Band_1_Gain"] == "-6.0dB"
    log = _log(tmp_path)
    assert "[pas 1/2]" in log and "[pas 2/2]" in log and "0.0dB -> -3.0dB" in log
    assert lancer(faux_dbx, "set", XG, "-3.5dB", "--appliquer", monkeypatch=monkeypatch) == 0   # 2,5 dB : direct


def test_set_delai_par_pas(faux_dbx, monkeypatch, tmp_path):
    assert lancer(faux_dbx, "set", DL, "2ms", "--appliquer", monkeypatch=monkeypatch) != 0
    assert lancer(faux_dbx, "set", DL, "2ms", "--appliquer", "--par-pas", monkeypatch=monkeypatch) != 0  # > 2 pas
    assert lancer(faux_dbx, "set", DL, "1ms", "--appliquer", "--par-pas", monkeypatch=monkeypatch) == 0
    assert lancer(faux_dbx, "set", DL, "3ms", "--appliquer", "--par-pas", "--protection-validee",
                  monkeypatch=monkeypatch) == 0
    assert _log(tmp_path).count(DL) == 6 and A[_K("DelayOut1/SV")]["Amount"].startswith("3ms")


def test_pas_libre_exige_sorties_mutees(faux_dbx, monkeypatch, tmp_path):
    assert lancer(faux_dbx, "set", XG, "-9.0dB", "--appliquer", "--pas-libre", monkeypatch=monkeypatch) != 0
    for c in range(1, 7):
        A[_K("OutputGains/SV")][f"Channel_{c}_Mute"] = "On"
    assert lancer(faux_dbx, "set", XG, "-9.0dB", "--appliquer", "--pas-libre", monkeypatch=monkeypatch) == 0
    assert A[_K("CrossoverIIR1/SV")]["Band_1_Gain"] == "-9.0dB" and "PAS LIBRE" in _log(tmp_path)


def test_niveau_sortie_analogique_suit_la_config(faux_dbx, monkeypatch, tmp_path):
    n = "\\\\Node\\Settings\\SV\\Output1Gain"
    args = ("--appliquer", "--protection-validee", "--hors-carte")      # le simulateur ne décrit pas \\Node
    assert lancer(faux_dbx, "set", n, "+12 dBu", *args, monkeypatch=monkeypatch) != 0            # sans config
    assert lancer(faux_dbx, "set", n, "+12 dBu", *args, "--config", CKI, monkeypatch=monkeypatch) != 0  # config +22
    assert A["\\\\Node\\Settings\\SV"]["Output1Gain"] == "+22 dBu"
    assert lancer(faux_dbx, "set", n, "+22 dBu", *args, "--config", CKI, monkeypatch=monkeypatch) != 0  # non muté
    for c in range(1, 7):
        A[_K("OutputGains/SV")][f"Channel_{c}_Mute"] = "On"
    assert lancer(faux_dbx, "set", n, "+22 dBu", *args, "--config", CKI, monkeypatch=monkeypatch) == 0


def test_valeur_avant_illisible_refusee(faux_dbx, monkeypatch, tmp_path):
    vrai = dbx_hiqnet.Dbx.get
    monkeypatch.setattr(dbx_hiqnet.Dbx, "get", lambda self, c: None if c == XG else vrai(self, c))
    assert lancer(faux_dbx, "set", XG, "-12.0dB", "--appliquer", "--par-pas", "--protection-validee",
                  monkeypatch=monkeypatch) != 0
    assert A[_K("CrossoverIIR1/SV")]["Band_1_Gain"] == "0.0dB"


def test_voie_contredite_refusee(faux_dbx, monkeypatch, tmp_path):
    seuil4 = "\\\\Preset\\Limiter4\\SV\\Threshold"          # sortie 4 = SUB dans la config CKI
    assert lancer(faux_dbx, "set", seuil4, "-25.0dB", "--config", CKI, "--voie", "HIGH", "--appliquer",
                  "--protection-validee", monkeypatch=monkeypatch) != 0
    assert lancer(faux_dbx, "regler", "SUB.limiteur.seuil", "-25.0dB", "--config", CKI, "--voie", "HIGH",
                  "--appliquer", "--protection-validee", monkeypatch=monkeypatch) != 0
    assert A[_K("Limiter4/SV")]["Threshold"] == "-20.0dB"
    assert lancer(faux_dbx, "set", seuil4, "-25.0dB", "--config", CKI, "--appliquer",          # voie déduite : SUB
                  "--protection-validee", monkeypatch=monkeypatch) == 0


def test_alias_refuse_sur_crossover_multivoie(faux_dbx, monkeypatch, tmp_path):
    # le simulateur a un CrossoverIIR1 à 5 bandes : HIGH (sortie 1).gain viserait la mauvaise voie
    assert lancer(faux_dbx, "regler", "HIGH.gain", "-1.0dB", "--config", CKI, "--appliquer",
                  monkeypatch=monkeypatch) != 0
    assert A[_K("CrossoverIIR1/SV")]["Band_1_Gain"] == "0.0dB"


def test_bosse_peqout_relit_la_topologie(faux_dbx, monkeypatch, tmp_path):
    assert lancer(faux_dbx, "regler", "MEDIUM.eq.2.gain", "+2.0dB", "--config", CKI, "--appliquer",
                  monkeypatch=monkeypatch) == 0                                   # topologie simulée : PEQ avant
    monkeypatch.setitem(A, "\\\\Node\\Config\\SV", {"Config": "{}"})
    assert lancer(faux_dbx, "regler", "MEDIUM.eq.2.gain", "+2.5dB", "--config", CKI, "--appliquer",
                  monkeypatch=monkeypatch) != 0


def test_regler_par_alias_et_plafond(faux_dbx, monkeypatch, tmp_path):
    # SUB = sortie 4 dans la config CKI ; le plafond du SUB est appliqué sans --voie
    assert lancer(faux_dbx, "regler", "SUB.limiteur.seuil", "-5.0dB", "--config", CKI, "--appliquer",
                  "--protection-validee", monkeypatch=monkeypatch) != 0
    assert lancer(faux_dbx, "regler", "SUB.limiteur.seuil", "-25.0dB", "--config", CKI, "--appliquer",
                  "--protection-validee", monkeypatch=monkeypatch) == 0
    assert A[_K("Limiter4/SV")]["Threshold"] == "-25.0dB" and "plafond OK" in _log(tmp_path)
    assert lancer(faux_dbx, "regler", "MEDIUM.eq.2.gain", "-2.0dB", "--config", CKI, "--appliquer",
                  monkeypatch=monkeypatch) == 0
    assert A[_K("PEQOut2/SV")]["Band_2_Gain"] == "-2.0dB"


# ---------- lot ----------
def _lot(tmp_path, reglages):
    f = tmp_path / "lot.yaml"
    import yaml
    f.write_text(yaml.safe_dump({"config": CKI, "reglages": reglages}, allow_unicode=True), encoding="utf-8")
    return str(f)


def test_lot_refus_avant_toute_ecriture(faux_dbx, monkeypatch, tmp_path):
    f = _lot(tmp_path, [{"MEDIUM.eq.2.gain": "-2.0dB"}, {"SUB.limiteur.seuil": "-5.0dB"}])   # seuil > plafond
    assert lancer(faux_dbx, "lot", f, "--appliquer", "--protection-validee", monkeypatch=monkeypatch) != 0
    assert A[_K("PEQOut2/SV")]["Band_2_Gain"] == "0.0dB"
    assert not list((tmp_path / "instantanes").glob("*avant-lot*"))        # pas d'instantané pour un lot refusé


def test_lot_conforme(faux_dbx, monkeypatch, tmp_path):
    f = _lot(tmp_path, [{"MEDIUM.eq.2.gain": "-2.0dB"}, {"HIGH.delai": "0.4ms"}, {"SUB.limiteur.seuil": "-25.0dB"}])
    assert lancer(faux_dbx, "lot", f, "--appliquer", monkeypatch=monkeypatch) != 0      # seuil : protection exigée
    assert lancer(faux_dbx, "lot", f, "--protection-validee", monkeypatch=monkeypatch) == 0   # simulation
    assert A[_K("PEQOut2/SV")]["Band_2_Gain"] == "0.0dB"
    assert lancer(faux_dbx, "lot", f, "--appliquer", "--protection-validee", monkeypatch=monkeypatch) == 0
    assert A[_K("PEQOut2/SV")]["Band_2_Gain"] == "-2.0dB" and A[_K("Limiter4/SV")]["Threshold"] == "-25.0dB"
    assert list((tmp_path / "instantanes").glob("*avant-lot*"))


def test_lot_echec_retour_arriere(faux_dbx, monkeypatch, tmp_path):
    vrai = dd.ecrire_et_relire
    vu = []

    def panne(d, chemin, valeur, desc):
        if chemin.endswith("DelayOut1\\SV\\Amount") and not vu:
            vu.append(1)
            return "999ms", False, None                     # la machine retient autre chose (une fois)
        return vrai(d, chemin, valeur, desc)

    monkeypatch.setattr(dd, "ecrire_et_relire", panne)
    f = _lot(tmp_path, [{"MEDIUM.eq.2.gain": "-2.0dB"}, {"SUB.limiteur.seuil": "-25.0dB"}, {"HIGH.delai": "0.4ms"}])
    assert lancer(faux_dbx, "lot", f, "--appliquer", "--protection-validee", monkeypatch=monkeypatch) != 0
    assert A[_K("PEQOut2/SV")]["Band_2_Gain"] == "0.0dB"                    # remis
    assert A[_K("Limiter4/SV")]["Threshold"] == "-25.0dB"                   # seuil abaissé laissé (plus protecteur)
    log = _log(tmp_path)
    assert "LOT" in log and "retour arrière" in log and "plus protecteur" in log


def test_retour_arriere_s_arrete_au_premier_ecart(faux_dbx, monkeypatch, tmp_path):
    vrai = dd.ecrire_et_relire
    etat = {"echec": False}

    def panne(d, chemin, valeur, desc):
        if chemin.endswith("DelayOut1\\SV\\Amount"):
            etat["echec"] = True
            return "999ms", False, None
        if etat["echec"] and chemin.endswith("CrossoverIIR3\\SV\\Band_1_Gain"):
            return "9.9dB", False, None                   # le retour arrière de KICK échoue
        return vrai(d, chemin, valeur, desc)

    monkeypatch.setattr(dd, "ecrire_et_relire", panne)
    for n in (3,):
        monkeypatch.setitem(A, _K(f"CrossoverIIR{n}/SV"), {"Band_1_HPFrequency": "Out", "Band_1_Gain": "0.0dB"})
    f = _lot(tmp_path, [{"MEDIUM.eq.2.gain": "-2.0dB"}, {"KICK.gain": "-1.0dB"}, {"HIGH.delai": "0.4ms"}])
    assert lancer(faux_dbx, "lot", f, "--appliquer", monkeypatch=monkeypatch) != 0
    assert A[_K("PEQOut2/SV")]["Band_2_Gain"] == "-2.0dB"                 # pas touché après l'arrêt
    assert "RETOUR ARRIÈRE INTERROMPU" in _log(tmp_path)


def test_retour_arriere_generateur_jamais_rallume():
    class D:
        def __init__(self):
            self.v = {"\\\\Preset\\SignalGenerator\\SV\\Signal Generator": "On",
                      "\\\\Preset\\SignalGenerator\\SV\\Signal Amplitude": "-30dB"}

        def get(self, c):
            return self.v[c]

        def set(self, c, x):
            self.v[c] = x

        def ls(self, c):
            raise ValueError("non décrit")
    d = D()
    o = dr.Options()
    g = dr._remettre(d, "x", {"chemin": "\\\\Preset\\SignalGenerator\\SV\\Signal Generator", "avant": "On"}, o)
    a = dr._remettre(d, "x", {"chemin": "\\\\Preset\\SignalGenerator\\SV\\Signal Amplitude", "avant": "0dB"}, o)
    assert d.v["\\\\Preset\\SignalGenerator\\SV\\Signal Generator"] == "Off" and g["remis"]
    assert d.v["\\\\Preset\\SignalGenerator\\SV\\Signal Amplitude"] == "-30dB" and a["motif"]
    n = dr._remettre(d, "x", {"chemin": XG, "avant": None}, o)
    assert n["remis"] is False and "inconnue" in n["erreur"]


# ---------- chaîne de sortie ----------
def _cfg(pomap, wires=(), auto=True):
    c = {"gridsOA": [{"Name": "POMap", "Map": pomap}], "wires": list(wires)}
    for g in pomap:
        for i in g:
            c[i] = {"AutoWire": auto, "Properties": {"DSP": "dsp3"}}
    return c


ORDRE = [["Inputs3"], ["CrossoverIIR1", "CrossoverIIR2"], ["PEQOut1", "PEQOut2"], ["ZoneGains"], ["OutputGains"],
         ["Limiter1", "Limiter2"], ["DelayOut1", "DelayOut2"], ["Outputs3"]]


def test_chaine_depuis_topologie():
    r = dd.chaine_depuis_config(_cfg(ORDRE, [[["DelayOut1", 0], ["OutputMeters", 0]]]))
    assert r["chaine_sortie"] == "peq_avant_limiteur" and r["mute_avant_limiteur"] and r["sorties"] == [1, 2]
    inverse = [ORDRE[0], ORDRE[1], ORDRE[5], ORDRE[3], ORDRE[4], ORDRE[2], ORDRE[6], ORDRE[7]]
    assert dd.chaine_depuis_config(_cfg(inverse))["chaine_sortie"] == "peq_apres_limiteur"
    assert dd.chaine_depuis_config(_cfg(ORDRE, [[["Inputs3", 2], ["Limiter1", 0]]]))["chaine_sortie"] == "indetermine"
    assert dd.chaine_depuis_config(_cfg(ORDRE, auto=False))["chaine_sortie"] == "indetermine"
    assert dd.chaine_depuis_config({})["chaine_sortie"] == "indetermine"


def test_enregistrer_chaine_garde_la_phase(tmp_path):
    f = tmp_path / "phase.yaml"
    f.write_text("phase: {type: constante}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        dbx_plan.enregistrer_chaine({"chaine_sortie": "indetermine"}, "x", f)
    dbx_plan.enregistrer_chaine(dd.chaine_depuis_config(_cfg(ORDRE)), "2.0.5.0", f)
    import yaml
    doc = yaml.safe_load(f.read_text(encoding="utf-8"))
    assert doc["phase"] == {"type": "constante"} and doc["chaine_sortie"] == "peq_avant_limiteur"
    f.write_text("chaine_banc: peq_apres_limiteur\nchaine_sortie: peq_apres_limiteur\n", encoding="utf-8")
    with pytest.raises(ValueError, match="contredit"):                   # la mesure prime : rien d'écrasé
        dbx_plan.enregistrer_chaine(dd.chaine_depuis_config(_cfg(ORDRE)), "2.0.5.0", f)


def test_topologie_mal_formee_indeterminee():
    class D:
        def get(self, c):
            return '{`gridsOA`:[{`Name`:`POMap`,`Map`:[[`PEQOut1`],[`Limiter1`]]}],`wires`:[[1]]}'
    assert dd.chaine_sortie_machine(D())["chaine_sortie"] == "indetermine"


def test_appliquer_plan_refuse_niveau_sortie_different(faux_dbx, monkeypatch, tmp_path):
    import preset_auto
    plan = preset_auto.construire_plan(CKI, "techno", accepter_non_verifie=True)
    A["\\\\Node\\Settings\\SV"]["Output2Gain"] = "+12 dBu"

    class D:
        def get(self, c):
            base, _, f = c.rpartition("\\")
            return A[base][f]
    pb = dbx_plan.verifier_machine(D(), plan)
    assert any("sortie 2" in p and "+12dBu" in p for p in pb) and len(pb) == 1


# ---------- mètres ----------
def test_metres_lecture_seule(faux_dbx, monkeypatch):
    with dbx_hiqnet.Dbx("127.0.0.1", faux_dbx, "administrator") as d:
        r = metres.relever(d, ["\\\\Preset\\OutputMeters\\SV\\Meter_1"], duree=0.3)
    x = r["\\\\Preset\\OutputMeters\\SV\\Meter_1"]
    assert x["derniere"] == "-80.0dB" and x["vivant"] is False and x["maj"] == 1
    assert not (Path(dbx_hiqnet.JOURNAL) / "ecritures.log").exists()
