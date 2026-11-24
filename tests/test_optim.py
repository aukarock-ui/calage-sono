"""Tests sans matériel ni son de l'optimisation complète du preset (optim_preset, eq_auto, plan v2).
L'optimisation tourne contre faux_venu360 avec le moteur acoustique simulé (machine jamais démutée)."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
import yaml

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))

import banc  # noqa: E402
import dbx_hiqnet  # noqa: E402
import dbx_plan  # noqa: E402
import eq_auto  # noqa: E402
import faux_venu360  # noqa: E402
import optim_preset as op  # noqa: E402
import preset_auto  # noqa: E402

CKI = str(RACINE / "data" / "configs" / "ma-sono-cki.yaml")
F = 20.0 * 2 ** (np.arange(0, 240) / 24)


def _K(chemin: str) -> str:
    return "\\\\Preset\\" + chemin.replace("/", "\\")


# ---------------- eq_auto
def test_eq_auto_retrouve_trois_bandes_et_refuse_creux_etroit():
    vraies = [{"type": "Bell", "hz": 120.0, "gain_db": 4.0, "q": 2.0}, {"type": "Bell", "hz": 900.0, "gain_db": -3.0, "q": 1.5},
              {"type": "Bell", "hz": 4000.0, "gain_db": 5.0, "q": 3.0}]
    e = 20 * np.log10(np.abs(eq_auto.reponse(vraies, F)))
    r = eq_auto.ajuster(F, e, n_max=6, bosse_max_db=6)
    assert r["residu_rms_db"] < 0.6 and r["avant_rms_db"] > 1.2
    for v in vraies:
        assert any(abs(np.log2(b["hz"] / v["hz"])) < 1 / 6 and b["gain_db"] * v["gain_db"] < 0 for b in r["bandes"])
    creux = -12 * np.exp(-((np.log2(F / 2000)) ** 2) / (2 * 0.02 ** 2))      # annulation étroite
    r = eq_auto.ajuster(F, creux)
    assert not [b for b in r["bandes"] if b["gain_db"] > 0] and r["refus"]


def test_eq_auto_sans_bosses():
    e = -20 * np.log10(np.abs(eq_auto.reponse([{"type": "Bell", "hz": 500.0, "gain_db": -6.0, "q": 1.0}], F)))
    r = eq_auto.ajuster(F, -e, bosses_autorisees=False)          # creux mesuré -> bosse voulue, interdite
    assert all(b["gain_db"] <= 0 for b in r["bandes"]) and any("interdites" in x["motif"] for x in r["refus"])


# ---------------- plan v2
def test_plan_v2_bornes():
    with pytest.raises(ValueError, match="hors bornes"):
        preset_auto.valider_peq([{"type": "Bell", "hz": 100, "gain_db": 8, "q": 1}], 8, "x")
    with pytest.raises(ValueError, match="type"):
        preset_auto.valider_peq([{"type": "Notch", "hz": 100, "gain_db": -3, "q": 1}], 8, "x")
    with pytest.raises(ValueError, match="phase"):
        preset_auto.valider_phase(30, "x")
    assert preset_auto.valider_phase(-90, "x") == -90.0


def test_bosse_refusee_sans_ordre_de_chaine(tmp_path, monkeypatch):
    monkeypatch.setattr(dbx_plan, "FICHIER_BANC_ELECTRIQUE", tmp_path / "absent.yaml")
    plan = preset_auto.construire_plan(CKI, "techno", 20, 1.7, True)
    plan["voies"][1]["peq"] = [{"type": "Bell", "hz": 300.0, "gain_db": 2.0, "q": 1.0}]
    with pytest.raises(ValueError, match="bosses"):
        dbx_plan.ecritures(plan)
    plan["voies"][1]["peq"] = [{"type": "Bell", "hz": 300.0, "gain_db": -2.0, "q": 1.0}]
    with pytest.raises(ValueError, match="recalage actuel"):      # plan édité à la main ≠ recalage : refusé
        dbx_plan.ecritures(plan)


# ---------------- optimisation complète simulée
@pytest.fixture
def machine(tmp_path, monkeypatch):
    for n in range(1, 7):
        monkeypatch.setitem(faux_venu360.ARBRE, _K(f"CrossoverIIR{n}/SV"), {
            "Band_1_HPFrequency": "Out", "Band_1_HPType": "LR 24", "Band_1_LPFrequency": "Out", "Band_1_LPType": "LR 24",
            "Band_1_Gain": "0.0dB", "Band_1_Polarity": "Normal", "Band_1_Phase": "0 deg"})
        for bloc in (f"Limiter{n}/SV", f"DelayOut{n}/SV", f"PEQOut{n}/SV"):
            monkeypatch.setitem(faux_venu360.ARBRE, _K(bloc), dict(faux_venu360.ARBRE[_K(bloc)]))
        monkeypatch.setitem(faux_venu360.ARBRE[_K(f"Limiter{n}/SV")], "Threshold", "-40.0dB")
        monkeypatch.setitem(faux_venu360.ARBRE[_K(f"Limiter{n}/SV")], "Limiter", "On")
    for bloc in ("OutputGains/SV", "PEQ1/SV"):
        monkeypatch.setitem(faux_venu360.ARBRE, _K(bloc), dict(faux_venu360.ARBRE[_K(bloc)]))
    monkeypatch.setattr(dbx_hiqnet, "JOURNAL", tmp_path)
    srv = faux_venu360.S(("127.0.0.1", 0), faux_venu360.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def banc_elec(tmp_path, monkeypatch):
    """Banc électrique fictif (HYPOTHÈSE de test) : rotation constante, PEQ avant limiteur."""
    f = tmp_path / "venu360_phase.yaml"
    f.write_text(yaml.safe_dump({"phase": {"type": "constante", "source": "test"},
                                 "chaine_sortie": "peq_avant_limiteur"}), encoding="utf-8")
    monkeypatch.setattr(banc, "FICHIER_BANC_ELECTRIQUE", f)
    monkeypatch.setattr(dbx_plan, "FICHIER_BANC_ELECTRIQUE", f)
    return f


PROPRE = {"MEDIUM": [{"type": "Bell", "hz": 700.0, "gain_db": 4.0, "q": 1.5}],
          "HIGH": [{"type": "Bell", "hz": 5000.0, "gain_db": 3.5, "q": 1.2}]}


def _lancer(dos, cmd, port, pos=None, **kw):
    with dbx_hiqnet.Dbx("127.0.0.1", port, "administrator") as d:
        cfg = op.charger_etat(dos)["config"]
        moteur = lambda p, s: banc.MoteurSimule(d, cfg, s, p, {"MEDIUM": -6.0}, propre=PROPRE,  # noqa: E731
                                                ecart_sens_db={"MEDIUM": 4.0})
        args = dict(protection_validee=True, signal_autorise=False, niveau_max=-12.0, appliquer=True) | kw
        if cmd == "mesurer":
            return op.mesurer_position(dos, pos, d, moteur, dbx_hiqnet.journaliser, "127.0.0.1", **args)
        return op.egaliser(dos, d, moteur, dbx_hiqnet.journaliser, "127.0.0.1", **args)


def _muette(port):
    A = faux_venu360.ARBRE[_K("OutputGains/SV")]
    return all(A[f"Channel_{c}_Mute"] == "On" for c in range(1, 7))


def test_optimisation_complete_simulee(machine, banc_elec, tmp_path):
    dos = op.preparer(CKI, "techno", banc.lire_positions("P1:20x1.7,P2:20x1.2"), accepter_non_verifie=True,
                      dossier=tmp_path / "optim")
    r1 = _lancer(dos, "mesurer", machine, "P1")
    assert banc.retablissement_complet(r1) and _muette(machine)
    e = op.charger_etat(dos)
    g = e["bilan"]["gains"]
    assert g["ecart_initial_db"]["MEDIUM"] > 2.5                       # +4 dB de sensibilité vu à la mesure
    assert max(abs(v) for v in g["ecart_final_db"].values()) <= 1.0
    assert all(v <= 0 for v in e["reglages"]["gains"].values())
    assert max(e["bilan"]["delais"]["perte_apres_db"].values()) < 0.5
    with pytest.raises(ValueError, match="déjà mesurée"):
        _lancer(dos, "mesurer", machine, "P1")
    r2 = _lancer(dos, "mesurer", machine, "P2")
    assert banc.retablissement_complet(r2)
    r3 = _lancer(dos, "egaliser", machine)
    assert banc.retablissement_complet(r3) and _muette(machine)
    e = op.charger_etat(dos)
    ev = e["bilan"]["egalisation"]["eq_voies"]
    med = ev["MEDIUM"]
    assert med["bandes"] and med["ecart_apres_db"] < med["ecart_avant_db"]
    assert any(abs(np.log2(b["hz"] / 700)) < 0.3 and b["gain_db"] < 0 for b in med["bandes"])   # bosse de 700 Hz coupée
    out = op.conclure(dos, ajouter_historique=False)
    rec = yaml.safe_load(Path(out["recalage_propose"]).read_text(encoding="utf-8"))
    assert rec["voies"]["MEDIUM"]["ecart_niveau_db"] > 2.5 and rec["voies"]["MEDIUM"]["peq"]
    assert (dos / "rapport.md").exists()
    # machine rendue à son état d'origine (PEQ de sortie à plat, phase 0, PEQ d'entrée Off)
    for n in range(1, 7):
        assert all(faux_venu360.ARBRE[_K(f"PEQOut{n}/SV")][f"Band_{i}_Gain"] == "0.0dB" for i in range(1, 9))
        assert faux_venu360.ARBRE[_K(f"CrossoverIIR{n}/SV")]["Band_1_Phase"] == "0 deg"
    assert faux_venu360.ARBRE[_K("PEQ1/SV")]["ParametricEQ"] == "Off"
    log = (Path(dbx_hiqnet.JOURNAL) / "ecritures.log").read_text(encoding="utf-8")
    assert "SIMULATION acoustique" in log and "OPTIM" in log


def test_phase_sautee_sans_banc_electrique(machine, tmp_path, monkeypatch):
    monkeypatch.setattr(banc, "FICHIER_BANC_ELECTRIQUE", tmp_path / "absent.yaml")
    monkeypatch.setattr(dbx_plan, "FICHIER_BANC_ELECTRIQUE", tmp_path / "absent.yaml")
    dos = op.preparer(CKI, "techno", banc.lire_positions("P1:20x1.7"), etapes=("gains", "phase"),
                      accepter_non_verifie=True, dossier=tmp_path / "optim")
    r = _lancer(dos, "mesurer", machine, "P1")
    assert banc.retablissement_complet(r)
    assert op.charger_etat(dos)["bilan"]["phase"] == {"sautee": "modele de phase absent"}
    assert any("non caractérisée" in a for a in r["alertes"])


def test_recaler_accepte_phase_et_peq(tmp_path):
    m = tmp_path / "m.yaml"
    m.write_text(yaml.safe_dump({"date": "2026-11-24", "point": {"distance_m": 20, "hauteur_m": 1.7},
                                 "voies": {"MEDIUM": {"phase_deg": -45, "peq": [{"type": "Bell", "hz": 700, "gain_db": -3.5, "q": 1.5}]}}}),
                 encoding="utf-8")
    m_bad = tmp_path / "b.yaml"
    m_bad.write_text(yaml.safe_dump({"voies": {"MEDIUM": {"phase_deg": 40}}}), encoding="utf-8")
    import shutil
    cfg = tmp_path / "ma-sono-cki.yaml"
    shutil.copy(CKI, cfg)
    preset_auto.recaler(str(cfg), str(m))
    import calc_systeme as cs
    rec = yaml.safe_load(cs.chemin_recalage(str(cfg)).read_text(encoding="utf-8"))
    assert rec["voies"]["MEDIUM"]["phase_deg"] == -45.0 and rec["voies"]["MEDIUM"]["peq"][0]["gain_db"] == -3.5
    plan = preset_auto.construire_plan(str(cfg), "techno", 20, 1.7, True)
    med = next(v for v in plan["voies"] if v["nom"] == "MEDIUM")
    assert med["phase_deg"] == -45.0 and med["peq"][0]["hz"] == 700.0      # relus par le calcul
    with pytest.raises(ValueError, match="phase"):
        preset_auto.recaler(str(cfg), str(m_bad))


# ---------------- banc électrique (boucle simulée)
import banc_electrique as be  # noqa: E402


def _banc(port, tmp_path, vrai, chaine, **kw):
    with dbx_hiqnet.Dbx("127.0.0.1", port, "administrator") as d:
        m = be.MoteurElectriqueSimule(d, 2, vrai, chaine)
        args = dict(amplis_coupes=True, signal_autorise=False, protection_validee=True, dossier=tmp_path / "be",
                    appliquer=True) | kw
        return be.lancer(d, m, CKI, 2, dbx_hiqnet.journaliser, "127.0.0.1", **args)


def test_banc_electrique_constante_et_peq_avant(machine, tmp_path):
    r = _banc(machine, tmp_path, {"type": "constante"}, "peq_avant_limiteur")
    assert r["phase"]["type"] == "constante" and r["chaine_sortie"] == "peq_avant_limiteur"
    assert r["controles"]["delai"] == "conforme" and r["controles"]["conformes"]
    assert banc.retablissement_complet(r) and _muette(machine)
    assert faux_venu360.ARBRE[_K("Limiter2/SV")]["Threshold"] == "-40.0dB"           # seuil d'origine rendu
    with pytest.raises(ValueError, match="simulé"):
        be.enregistrer(r, tmp_path / "venu360_phase.yaml")            # jamais un résultat simulé
    cible = be.enregistrer(dict(r, simulation=False), tmp_path / "venu360_phase.yaml")
    doc = yaml.safe_load(cible.read_text(encoding="utf-8"))
    assert doc["chaine_sortie"] == "peq_avant_limiteur" and doc["phase"]["type"] == "constante"


def test_banc_electrique_retard_et_peq_apres(machine, tmp_path):
    r = _banc(machine, tmp_path, {"type": "retard", "f_ref_hz": 1000.0}, "peq_apres_limiteur")
    assert r["phase"]["type"] == "retard" and abs(r["phase"]["f_ref_hz"] - 1000) < 20
    assert r["chaine_sortie"] == "peq_apres_limiteur"


def test_banc_electrique_refus(machine, tmp_path):
    with pytest.raises(ValueError, match="amplis-coupes"):
        _banc(machine, tmp_path, {"type": "constante"}, "peq_avant_limiteur", amplis_coupes=False)
    with pytest.raises(ValueError, match="protection"):
        _banc(machine, tmp_path, {"type": "constante"}, "peq_avant_limiteur", protection_validee=False)
    with pytest.raises(ValueError, match="incomplet"):
        be.enregistrer({"erreur": "x", "simulation": False, "phase": {}, "retablissement": {}}, tmp_path / "v.yaml")


# ---------------- constats de la revue de sécurité
def test_banc_bande_du_preset_ne_masque_pas_la_bosse(machine, tmp_path, monkeypatch):
    """P1 : une bande du preset à −6 dB vers 1 kHz ne doit pas faire conclure « PEQ avant limiteur »."""
    peq = faux_venu360.ARBRE[_K("PEQOut2/SV")]
    monkeypatch.setitem(peq, "Band_2_Frequency", "1.00kHz")
    monkeypatch.setitem(peq, "Band_2_Gain", "-6.0dB")
    r = _banc(machine, tmp_path, {"type": "constante"}, "peq_apres_limiteur")
    assert r["chaine_sortie"] == "peq_apres_limiteur"
    assert peq["Band_2_Gain"] == "-6.0dB"                                        # rétabli


def test_ecrire_peq_par_pas_et_bosse_refusee(machine, tmp_path, monkeypatch):
    monkeypatch.setattr(dbx_plan, "FICHIER_BANC_ELECTRIQUE", tmp_path / "absent.yaml")
    base = f"{banc.P}PEQOut3\\SV\\"
    lignes, ecrits = [], []
    vrai = op.dd.ecrire_et_relire

    def espion(d, ch, val, desc):
        ecrits.append((ch, val))
        return vrai(d, ch, val, desc)

    monkeypatch.setattr(op.dd, "ecrire_et_relire", espion)
    with dbx_hiqnet.Dbx("127.0.0.1", machine, "administrator") as d:
        with pytest.raises(banc.Annule, match="bosse"):
            op.ecrire_peq(d, base, [{"type": "Bell", "hz": 500.0, "gain_db": 2.0, "q": 1.0}], 8, lignes.append, "x")
        op.ecrire_peq(d, base, [{"type": "Bell", "hz": 500.0, "gain_db": -7.0, "q": 1.0}], 8, lignes.append, "x")
        assert op.lire_peq(d, base, 8)[0]["gain_db"] == -7.0
        op.ecrire_peq(d, base, [{"type": "Bell", "hz": 2000.0, "gain_db": -4.0, "q": 2.0}], 8, lignes.append, "x")
        b1 = op.lire_peq(d, base, 8)[0]
        assert b1["hz"] == 2000.0 and b1["gain_db"] == -4.0
        op.ecrire_peq(d, base, [], 8, lignes.append, "x")
        assert all(b["gain_db"] == 0 for b in op.lire_peq(d, base, 8))
    gains = [float(v.replace("dB", "")) for ch, v in ecrits if ch == base + "Band_1_Gain"]
    assert len(gains) >= 6
    assert all(abs(b - a) <= 3.05 for a, b in zip([0.0] + gains, gains))        # jamais plus de 3 dB d'un coup
    i_freq = next(i for i, (ch, v) in enumerate(ecrits) if ch == base + "Band_1_Frequency" and v.startswith("2"))
    assert [float(v.replace("dB", "")) for ch, v in ecrits[:i_freq] if ch == base + "Band_1_Gain"][-1] == 0.0  # reformée à 0 dB


def test_phase_180_et_bosses_cumulees_refusees():
    with pytest.raises(ValueError, match="polarité"):
        preset_auto.valider_phase(-180, "x")
    with pytest.raises(ValueError, match="composée"):
        preset_auto.valider_peq([{"type": "Bell", "hz": 1000, "gain_db": 5, "q": 1}] * 3, 8, "x")
    assert max(op.GRILLE_PHASE) == 0 and min(op.GRILLE_PHASE) == -135


def test_sans_appliquer_annonce_et_rien_n_est_ecrit(machine, tmp_path, monkeypatch):
    import copy
    avant = copy.deepcopy(faux_venu360.ARBRE)
    ecrits = []
    vrai = op.dd.ecrire_et_relire
    monkeypatch.setattr(op.dd, "ecrire_et_relire", lambda *a: ecrits.append(a[1]) or vrai(*a))
    dos = op.preparer(CKI, "techno", banc.lire_positions("P1:20x1.7"), accepter_non_verifie=True,
                      dossier=tmp_path / "optim")
    with pytest.raises(banc.Annonce, match="rien n'a été écrit") as e:
        _lancer(dos, "mesurer", machine, "P1", appliquer=False)
    assert "PEQOut" in str(e.value) and "--appliquer" in str(e.value)
    with pytest.raises(banc.Annonce, match="banc électrique"):
        _banc(machine, tmp_path, {"type": "constante"}, "peq_avant_limiteur", appliquer=False)
    assert ecrits == [] and faux_venu360.ARBRE == avant                       # aucune écriture, aucun mute
    assert op.charger_etat(dos)["positions_faites"] == [] and not op.charger_etat(dos).get("instantanes")
