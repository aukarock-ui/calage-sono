"""Tests sans matériel ni son de l'essai des coupures au micro (essai_coupures, analyse_raccord, coupures --calibrer).
L'essai complet tourne contre le simulateur faux_venu360 avec le moteur acoustique simulé."""
from __future__ import annotations

import json
import math
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
import yaml

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))

import analyse_raccord as ar  # noqa: E402
import coupures  # noqa: E402
import dbx_description as dd  # noqa: E402
import dbx_hiqnet  # noqa: E402
import essai_coupures as ec  # noqa: E402
import faux_venu360  # noqa: E402
import reglages_pile as rp  # noqa: E402

CKI = str(RACINE / "data" / "configs" / "ma-sono-cki.yaml")
F = 20.0 * 2 ** (np.arange(0, 240) / 24)


def _K(chemin: str) -> str:
    return "\\\\Preset\\" + chemin.replace("/", "\\")


def _rep(H) -> dict:
    return {"f_hz": F.tolist(), "mag_db": (20 * np.log10(np.abs(H))).tolist(), "phase_deg": np.rad2deg(np.angle(H)).tolist()}


def _lr4(fc, passe_haut, retard_ms=0.0, signe=1.0):
    return signe * rp.reponse_filtre(("LR", 24, fc), passe_haut, F) * np.exp(-2j * np.pi * F * retard_ms / 1000)


# ---------------- métriques (cas analytiques)
def test_somme_lr_en_phase_et_inversee():
    a, b = _lr4(1000, True), _lr4(1000, False)
    ab = _rep(a + b)
    assert abs(ar.perte_somme_db(_rep(a), _rep(b), ab, 1000)) < 0.05          # LR en phase : somme parfaite
    assert abs(ar.ecart_phase_deg(_rep(a), _rep(b), 1000)) < 1
    assert ar.perte_somme_db(_rep(a), _rep(-b), _rep(a - b), 1000) > 10        # inversé : trou à fc
    assert ar.ondulation_db(ab, 1000) < 0.05                                   # LR4 : somme plate


def test_regularite_nulle_si_positions_identiques_et_niveau_ignore():
    ab = _lr4(500, True) + _lr4(500, False)
    assert ar.regularite_db([_rep(ab), _rep(ab * 0.5)], 500) < 1e-6           # 6 dB plus bas = même forme
    creux = ab * (1 - 0.7 * np.exp(-((np.log2(F / 500)) ** 2) / 0.02))
    assert ar.regularite_db([_rep(ab), _rep(creux)], 500) > 0.5


def test_meilleur_delai_retrouve_retard_et_inversion():
    a = _lr4(1410, True)
    r = ar.meilleur_delai(_rep(a), _rep(_lr4(1410, False, retard_ms=-0.17)), 1410)
    assert not r["inverser_b"] and abs(r["delai_b_ms"] - 0.17) < 0.02         # B en avance de 0,17 ms -> le retarder
    r = ar.meilleur_delai(_rep(a), _rep(_lr4(1410, False, signe=-1.0)), 1410)
    assert r["inverser_b"] and abs(r["delai_b_ms"]) < 0.02


def test_grilles_differentes_refusees():
    a = _rep(_lr4(1000, True))
    b = dict(_rep(_lr4(1000, False)), f_hz=(F * 1.01).tolist())
    with pytest.raises(ValueError, match="grilles"):
        ar.perte_somme_db(a, b, a, 1000)


# ---------------- essai complet sur simulateur
@pytest.fixture
def machine(tmp_path, monkeypatch):
    for n in range(1, 7):                    # topologie « 6 × Mono Fullrange »
        monkeypatch.setitem(faux_venu360.ARBRE, _K(f"CrossoverIIR{n}/SV"), {
            "Band_1_HPFrequency": "Out", "Band_1_HPType": "LR 24", "Band_1_LPFrequency": "Out", "Band_1_LPType": "LR 24",
            "Band_1_Gain": "0.0dB", "Band_1_Polarity": "Normal", "Band_1_Phase": "0 deg"})
        for bloc in (f"Limiter{n}/SV", f"DelayOut{n}/SV"):
            monkeypatch.setitem(faux_venu360.ARBRE, _K(bloc), dict(faux_venu360.ARBRE[_K(bloc)]))
        # état de départ PROTÉGÉ (seuil sous tout plafond CKI) : sinon le rétablissement refuse, à raison, de le remettre
        monkeypatch.setitem(faux_venu360.ARBRE[_K(f"Limiter{n}/SV")], "Threshold", "-40.0dB")
        monkeypatch.setitem(faux_venu360.ARBRE[_K(f"Limiter{n}/SV")], "Limiter", "On")
    monkeypatch.setitem(faux_venu360.ARBRE, _K("OutputGains/SV"), dict(faux_venu360.ARBRE[_K("OutputGains/SV")]))
    monkeypatch.setattr(dbx_hiqnet, "JOURNAL", tmp_path)
    srv = faux_venu360.S(("127.0.0.1", 0), faux_venu360.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def essai(tmp_path):
    dos = ec.preparer(CKI, "techno", ec.lire_positions("P1:20x1.7,P2:20x1.2"), 2, True, tmp_path / "essai")
    yield dos
    for f in ec.DOSSIER_VARIANTES.glob(f"*{dos.name}*"):     # configs de variantes : nettoyées
        f.unlink()


def _lancer(dos, pos, port, erreurs=None, **kw):
    with dbx_hiqnet.Dbx("127.0.0.1", port, "administrator") as d:
        cfg = ec.charger_etat(dos)["config"]
        moteur = kw.pop("moteur", None) or (lambda p, s: ec.MoteurSimule(d, cfg, s, p, erreurs or {}))
        args = dict(protection_validee=True, signal_autorise=False, niveau_max=-12.0, appliquer=True) | kw
        return ec.mesurer_position(dos, pos, d, moteur, dbx_hiqnet.journaliser, "127.0.0.1", **args)


def test_preparer_variantes_et_temoin(essai):
    e = ec.charger_etat(essai)
    assert e["variantes"][0]["temoin"] and e["variantes"][0]["coupures_hz"]["MEDIUM/HIGH"] == 1410.0
    assert len(e["variantes"]) >= 2 and len({tuple(v["coupures_hz"].values()) for v in e["variantes"]}) == len(e["variantes"])
    for v in e["variantes"]:
        assert Path(v["config"]).parent == ec.DOSSIER_VARIANTES and Path(v["plan"]).exists()
    assert Path(CKI).read_text(encoding="utf-8").count("LR24 1410 Hz") == 2     # config d'origine intacte


def test_refus_sans_validation_signal_ou_niveau(essai, machine):
    with pytest.raises(ValueError, match="protection"):
        _lancer(essai, "P1", machine, protection_validee=False)
    with pytest.raises(ValueError, match="plafond"):
        _lancer(essai, "P1", machine, niveau_max=-6.0)

    class Reel:
        simule = False

    with pytest.raises(ValueError, match="signal-autorise"):
        _lancer(essai, "P1", machine, moteur=lambda p, s: Reel())
    assert ec.charger_etat(essai)["positions_faites"] == []


def test_erreur_en_cours_remute_et_retablit(essai, machine):
    origine = {}

    class Panne(ec.MoteurSimule):
        n = 0

        def mesurer(self, nom, niveau):
            Panne.n += 1
            if Panne.n == 4:
                raise RuntimeError("REW ne répond plus")
            return super().mesurer(nom, niveau)

    cfg = ec.charger_etat(essai)["config"]
    with pytest.raises(RuntimeError, match="REW"):
        with dbx_hiqnet.Dbx("127.0.0.1", machine, "administrator") as d:
            origine.update({ch: d.get(ch) for ch in ec._chemins_suivis(
                [json.loads(Path(v["plan"]).read_text(encoding="utf-8")) for v in ec.charger_etat(essai)["variantes"]])})
            ec.mesurer_position(essai, "P1", d, lambda p, s: Panne(d, cfg, s, p), dbx_hiqnet.journaliser, "127.0.0.1",
                                True, False, -12.0, appliquer=True)
    A = faux_venu360.ARBRE[_K("OutputGains/SV")]
    assert all(A[f"Channel_{c}_Mute"] == "On" for c in range(1, 7))            # 6 sorties mutées
    with dbx_hiqnet.Dbx("127.0.0.1", machine, "administrator") as d:
        for ch, v in origine.items():
            if ch not in dd.SORTIES_MUTE:
                assert (d.get(ch) or "").strip() == (v or "").strip(), ch      # état d'origine rétabli
    assert ec.charger_etat(essai)["positions_faites"] == []
    assert "ARRET position P1" in (Path(dbx_hiqnet.JOURNAL) / "ecritures.log").read_text(encoding="utf-8")


def test_essai_complet_simule(essai, machine, tmp_path):
    r1 = _lancer(essai, "P1", machine, {"MEDIUM": -6.0})
    assert r1["retablissement"]["ecarts_restants"] == {}
    e = ec.charger_etat(essai)
    plan0 = json.loads(Path(e["variantes"][0]["plan"]).read_text(encoding="utf-8"))
    calc = {x["nom"]: x["delai_ms"] for x in plan0["voies"]}
    aligne = e["delais_alignes"]["v0"]
    # MEDIUM 6 cm plus près que prévu -> 0,175 ms de retard en plus par rapport à HIGH (raccord LR pur à 1410 Hz)
    attendu = 0.06 / ec.cs.vitesse_son(20) * 1000
    assert abs((aligne["MEDIUM"] - aligne["HIGH"]) - (calc["MEDIUM"] - calc["HIGH"]) - attendu) < 0.03
    with pytest.raises(ValueError, match="déjà mesurée"):
        _lancer(essai, "P1", machine)
    _lancer(essai, "P2", machine, {"MEDIUM": -6.0})
    out = ec.conclure(essai, ajouter_historique=False)
    assert out["regularite_mesuree"] and out["gagnante"] == out["variantes"][0]["id"]
    scores = [v["mesure"]["score"] for v in out["variantes"]]
    assert scores == sorted(scores, reverse=True)
    v0 = next(v for v in out["variantes"] if v["temoin"])
    assert v0["mesure"]["raccords"]["MEDIUM/HIGH"]["perte_somme_db"] < 0.3   # aligné par la mesure
    assert (essai / "rapport.md").exists() and (essai / "resultat.yaml").exists()
    rec = yaml.safe_load(Path(out["recalage_propose"]).read_text(encoding="utf-8"))
    assert rec["voies"]["MEDIUM"]["delai_mesure_ms"] == round(aligne["MEDIUM"], 2)
    h = tmp_path / "historique.yaml"
    ec.ajouter_a_historique(out, h)
    with pytest.raises(ValueError, match="déjà"):
        ec.ajouter_a_historique(out, h)                                        # ajout seulement, pas de doublon
    assert len(yaml.safe_load(h.read_text(encoding="utf-8"))["essais"]) == 1
    log = (Path(dbx_hiqnet.JOURNAL) / "ecritures.log").read_text(encoding="utf-8")
    assert "SIMULATION acoustique" in log and "DelayOut" in log


def test_ecriture_delai_par_pas_de_05_ms(machine):
    lignes = []
    with dbx_hiqnet.Dbx("127.0.0.1", machine, "administrator") as d:
        ec.ecrire_delai(d, 2, 0.0, lignes.append, "x")
        assert ec.ecrire_delai(d, 2, 1.3, lignes.append, "x") == 1.3
        with pytest.raises(ec.Annule):
            ec.ecrire_delai(d, 2, 31.0, lignes.append, "x")
    pas = [l for l in lignes if "DelayOut2" in l]
    assert [l.split("-> ")[1][:4] for l in pas] == ["0.50", "1.00", "1.30"]     # pas ≤ 0,5 ms, chacun relu


# ---------------- apprentissage des poids
def _historique(tmp_path, wl, wd, n_essais=3, multi=True):
    rng = np.random.default_rng(1)
    essais = []
    for i in range(n_essais):
        vs = []
        for j in range(3):
            lo, di = float(rng.uniform(0, 3)), float(rng.uniform(0, 4))
            vs.append({"id": f"v{j}", "predit": {"lobes_db": lo, "directivite_db": di},
                       "penalite_mesuree_db": wl * lo + wd * di})
        essais.append({"dossier": f"e{i}", "regularite_mesuree": multi, "variantes": vs})
    h = tmp_path / "coupures.yaml"
    h.write_text(yaml.safe_dump({"essais": essais}), encoding="utf-8")
    return h


def test_calibrer_retrouve_les_poids_et_respecte_les_bornes(tmp_path):
    fp = tmp_path / "poids.yaml"
    c = coupures.calibrer(_historique(tmp_path, 1.5, 0.2), fp, appliquer=True)
    assert abs(c["apres"]["lobes"] - 1.5) < 0.01 and abs(c["apres"]["directivite"] - 0.2) < 0.01
    assert c["rmse_apres_db"] < c["rmse_avant_db"]
    p = coupures.poids(fp)
    assert p["lobes"] == 1.5 and "appris" in p["source"]
    c = coupures.calibrer(_historique(tmp_path, 6.0, 0.0), tmp_path / "p2.yaml")
    assert c["apres"]["lobes"] == coupures.POIDS_BORNES[1] and "ecrit" not in c   # borné, pas écrit sans --appliquer


def test_calibrer_refuse_historique_insuffisant_ou_une_position(tmp_path):
    assert "refus" in coupures.calibrer(_historique(tmp_path, 1, 1, n_essais=2), tmp_path / "p.yaml")
    assert "refus" in coupures.calibrer(_historique(tmp_path, 1, 1, multi=False), tmp_path / "p.yaml")


def test_poids_hors_bornes_refuses(tmp_path):
    fp = tmp_path / "poids.yaml"
    fp.write_text("lobes: 9\ndirectivite: 0.5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bornes"):
        coupures.poids(fp)
    assert coupures.poids(tmp_path / "absent.yaml")["lobes"] == coupures.POIDS_LOBES


def test_simulation_ne_demute_jamais_la_machine(essai, machine):
    vus = []

    class Espion(ec.MoteurSimule):
        def mesurer(self, nom, niveau):
            vus.append(tuple(self.d.get(ch).strip() for ch in dd.SORTIES_MUTE))
            return super().mesurer(nom, niveau)

    cfg = ec.charger_etat(essai)["config"]
    with dbx_hiqnet.Dbx("127.0.0.1", machine, "administrator") as d:
        r = ec.mesurer_position(essai, "P1", d, lambda p, s: Espion(d, cfg, s, p), dbx_hiqnet.journaliser,
                                "127.0.0.1", True, False, -12.0, appliquer=True)
    assert vus and all(etat == ("On",) * 6 for etat in vus)                  # muette pendant CHAQUE mesure
    assert r["mesures"] > 10 and ec.retablissement_complet(r)


def test_retablir_refuse_un_seuil_d_origine_au_dessus_du_plafond(essai, machine, monkeypatch):
    monkeypatch.setitem(faux_venu360.ARBRE[_K("Limiter4/SV")], "Threshold", "0.0dB")   # origine non protégée
    monkeypatch.setitem(faux_venu360.ARBRE[_K("Limiter3/SV")], "Limiter", "Off")
    r = _lancer(essai, "P1", machine)
    refus = r["retablissement"]["refuses"]
    assert any("Limiter4" in ch and "Threshold" in ch for ch in refus)
    assert any(ch.endswith("Limiter3\\SV\\Limiter") for ch in refus)
    assert not ec.retablissement_complet(r)
    A = faux_venu360.ARBRE
    assert A[_K("Limiter4/SV")]["Threshold"] != "0.0dB" and A[_K("Limiter3/SV")]["Limiter"] == "On"   # protections gardées


def test_niveau_par_voie_et_plafond_bas(essai, machine):
    r = _lancer(essai, "P1", machine, niveau_max=-26.0)
    niv = ec.charger_etat(essai)["niveau_dbfs"]
    assert set(niv) == {"SUB", "KICK", "MEDIUM", "HIGH"} and max(niv.values()) <= -26.0   # départ sous le plafond
    with pytest.raises(ValueError, match="niveau-max"):
        _lancer(essai, "P2", machine, niveau_max=-30.0)                     # plafond abaissé : refus, pas d'excès
    assert r["mesures"] > 0


def test_penalite_jamais_negative():
    assert ar.penalite({"a": {"perte_somme_db": -0.4, "regularite_db": None, "ondulation_db": 1.0}}) == 1.0
