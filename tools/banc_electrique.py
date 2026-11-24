#!/usr/bin/env python3
"""
banc_electrique.py — Caractérisation ÉLECTRIQUE du VENU360 (boucle sortie → entrée de l'interface, REW), sans son.

  banc_electrique.py <config> --sortie N --ip … --amplis-coupes --signal-autorise --protection-validee --appliquer
                     [--rew localhost:4735] [--niveau -20] [--seuil-test -40] [--enregistrer] [--simulation]

Câblage : sortie N du VENU360 -> entrée de mesure de l'interface ; REW avec référence de temps en boucle.
AMPLIS ÉTEINTS ou sorties débranchées (--amplis-coupes : Louis le confirme) : le signal ne va qu'à l'interface.

Mesures (sortie N seule ouverte, les 5 autres mutées et relues) :
  1. PHASE : crossover N à plat (filtres Out, gain 0), Band_1_Phase = 0, −45, −90, −135, −180°. Écart de phase relatif
     au réglage 0 -> modèle « constante » (rotation e^{jφ} à toutes fréquences), « retard » (φ atteint à f_ref) ou
     « table » (écarts mesurés, interpolés). Le modèle est la condition de l'étape phase d'optim_preset.
  2. ORDRE PEQOut / LIMITEUR : limiteur à deux seuils de test (bas, puis 10 dB plus bas : on vérifie qu'il agit), puis
     PEQOut +6 dB à 1 kHz : si la bosse passe entière, le PEQ est APRÈS le limiteur (bosses d'EQ interdites, non
     protégées) ; si elle est écrêtée, il est AVANT (bosses permises). Seuils de test toujours PLUS BAS que l'actuel.
  3. CONTRÔLES : gain de voie −6 dB et délai 1 ms mesurés = valeurs écrites.

Fin (normale, erreur, Ctrl-C) : 6 sorties mutées, état d'origine rétabli et relu (banc.retablir : jamais un seuil
au-dessus du plafond calculé). Résultat : mesures/banc-electrique-<date>/resultat.yaml ; --enregistrer le copie dans
data/protocoles/venu360_phase.yaml (fichier lu par optim_preset et dbx_plan).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banc                        # noqa: E402
import dbx_description as dd       # noqa: E402
import eq_auto                     # noqa: E402
from banc import P, Annule         # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
REGLAGES_PHASE = (0.0, -45.0, -90.0, -135.0, -180.0)
BANDE_HZ = (100.0, 8000.0)          # bande où l'on juge le modèle (boucle électrique : large et propre)
TOL_CONSTANTE_DEG = 10.0
TOL_RETARD_DEG = 10.0
GAIN_TEST_DB, DELAI_TEST_MS = -6.0, 1.0
BOSSE_TEST = {"type": "Bell", "hz": 1000.0, "gain_db": 6.0, "q": 1.0}


def chemins(n: int) -> list[str]:
    X = f"{P}CrossoverIIR{n}\\SV\\Band_1_"
    out = [X + k for k in ("HPFrequency", "HPType", "LPFrequency", "LPType", "Gain", "Polarity", "Phase")]
    out += [f"{P}PEQOut{n}\\SV\\Band_{i}_{k}" for i in range(1, 9) for k in ("Type", "Frequency", "Q", "Gain")]
    out += [f"{P}PEQOut{n}\\SV\\ParametricEQ", f"{P}Limiter{n}\\SV\\Limiter", f"{P}Limiter{n}\\SV\\Threshold",
            f"{P}DelayOut{n}\\SV\\Delay", f"{P}DelayOut{n}\\SV\\Amount"]
    return sorted(set(out) | set(dd.SORTIES_MUTE))


def _ecrire(d, ch: str, val: str, journal, ip: str) -> str:
    desc = dd.decrire_param(d, ch)
    motif = dd.valider_machine(desc, val) if desc else None
    if motif:
        raise Annule(f"{ch} = {val} refusé par la machine ({motif})")
    relu, ok, err = dd.ecrire_et_relire(d, ch, val, desc)
    ok = ok or dd.arrondi_machine(desc, val, relu, ch)
    journal(f"BANC ELEC {ip} {ch} -> {val} (relu {relu}){'' if ok else ' ECART'}")
    if not ok:
        raise Annule(f"{ch} : écrit {val}, relu {relu} ({err})")
    return relu


def _seuil(d, n: int) -> float:
    return banc._ms(d.get(f"{P}Limiter{n}\\SV\\Threshold"))


# ---------------------------------------------------------------- analyse (fonctions pures)
def _H(m: dict) -> tuple[np.ndarray, np.ndarray]:
    f = np.asarray(m["f_hz"], dtype=float)
    if m.get("phase_deg") is None:
        raise Annule("mesure sans phase : référence de temps REW requise")
    return f, 10 ** (np.asarray(m["mag_db"]) / 20) * np.exp(1j * np.deg2rad(np.asarray(m["phase_deg"])))


def ecart(m: dict, ref: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rapport mesure / référence : f, Δphase (deg, déroulée), Δmag (dB)."""
    f, a = _H(m)
    _, b = _H(ref)
    r = a / b
    return f, np.rad2deg(np.unwrap(np.angle(r))), 20 * np.log10(np.abs(r))


def modele(mesures: dict[float, dict]) -> dict:
    """Modèle de Band_1_Phase depuis les mesures {réglage_deg: mesure}. Table toujours jointe (1/6 d'octave)."""
    ref = mesures[0.0]
    f = np.asarray(ref["f_hz"], dtype=float)
    k = (f >= BANDE_HZ[0]) & (f <= BANDE_HZ[1])
    pas = max(1, int(round(len(f) / (math.log2(f[-1] / f[0]) * 6))))
    table, err_c, taus = {}, [], {}
    for r, m in mesures.items():
        if r == 0.0:
            continue
        _, dphi, dmag = ecart(m, ref)
        dphi = dphi - 360 * np.round((dphi[k].mean() - r) / 360)          # même tour que le réglage
        table[f"{r:g}"] = {"f_hz": [round(x, 1) for x in f[::pas]], "dphi_deg": [round(x, 1) for x in dphi[::pas]],
                           "dmag_db": [round(x, 2) for x in dmag[::pas]]}
        err_c.append(float(np.max(np.abs(dphi[k] - r))))
        w = 2 * np.pi * f[k]
        tau = -float(np.sum(w * dphi[k] * np.pi / 180) / np.sum(w * w))     # dphi = −ω τ (moindres carrés)
        taus[r] = (tau, float(np.max(np.abs(dphi[k] + np.rad2deg(w * tau)))))
    base = {"table": table, "bande_hz": list(BANDE_HZ)}
    if max(err_c) <= TOL_CONSTANTE_DEG:
        return {"type": "constante", "ecart_max_deg": round(max(err_c), 1), **base}
    fref = [-r / 360 / t for r, (t, _) in taus.items() if t]
    if all(e <= TOL_RETARD_DEG for _, e in taus.values()) and fref and np.std(fref) / np.mean(fref) < 0.1:
        return {"type": "retard", "f_ref_hz": round(float(np.mean(fref)), 1),
                "ecart_max_deg": round(max(e for _, e in taus.values()), 1), **base}
    return {"type": "table", **base}


def ordre_chaine(lim_bas: dict, lim_plus_bas: dict, bosse: dict, plat_bas: dict) -> dict:
    """lim_bas / lim_plus_bas : PEQ Off, seuils T et T−10 ; bosse : PEQ +6 dB à 1 kHz au seuil T−10 ;
    plat_bas : PEQ Off au seuil T−10 (référence de la bosse)."""
    f = np.asarray(plat_bas["f_hz"])
    i = int(np.argmin(np.abs(f - BOSSE_TEST["hz"])))
    agit = float(np.asarray(lim_bas["mag_db"])[i] - np.asarray(lim_plus_bas["mag_db"])[i])
    passe = float(np.asarray(bosse["mag_db"])[i] - np.asarray(plat_bas["mag_db"])[i])
    out = {"baisse_seuil_moins_10_db": round(agit, 2), "bosse_mesuree_db": round(passe, 2)}
    if agit < 6.0:
        return out | {"chaine_sortie": "indetermine", "motif": f"le limiteur n'agit pas assez ({agit:.1f} dB pour −10 dB "
                                                               "de seuil) : baisser --seuil-test ou monter --niveau"}
    if passe >= 5.0:
        return out | {"chaine_sortie": "peq_apres_limiteur"}
    if passe <= 2.0:
        return out | {"chaine_sortie": "peq_avant_limiteur"}
    return out | {"chaine_sortie": "indetermine", "motif": f"bosse de +6 dB mesurée à {passe:+.1f} dB : ambigu"}


# ---------------------------------------------------------------- séance
def lancer(d, moteur, chemin_config: str, n: int, journal, ip: str, amplis_coupes: bool, signal_autorise: bool,
           protection_validee: bool, niveau: float = -20.0, seuil_test: float = -40.0, dossier=None,
           appliquer: bool = False) -> dict:
    import calc_systeme as cs
    import dbx_hiqnet
    if not amplis_coupes:
        raise ValueError("banc ÉLECTRIQUE : --amplis-coupes requis (amplis éteints ou sorties débranchées, confirmé par Louis)")
    if not protection_validee:
        raise ValueError("le banc écrit dans le limiteur (seuils de test plus bas) : --protection-validee requis")
    if not moteur.simule and not signal_autorise:
        raise ValueError("chaque mesure joue un sweep (vers l'interface) : --signal-autorise après le « go » de Louis")
    if niveau > banc.NIVEAU_PLAFOND_DBFS:
        raise ValueError(f"--niveau {niveau} au-dessus du plafond du kit ({banc.NIVEAU_PLAFOND_DBFS} dBFS)")
    if not 1 <= n <= 6:
        raise ValueError("--sortie 1 à 6")
    _, cfg = cs.charger(chemin_config)
    voie = next((v["nom"] for v in cfg["voies"] if int(v.get("sortie") or 0) == n), None)
    if voie is None:
        raise ValueError(f"aucune voie de {chemin_config} sur la sortie {n} (nécessaire au contrôle du plafond)")
    suivis = chemins(n)
    seuil0 = _seuil(d, n)
    plafond = cs.plafond_limiteur(chemin_config, voie)["seuil"]
    seuil_test = min(seuil_test, seuil0, plafond)  # jamais au-dessus du seuil actuel ni du plafond calculé
    if not appliquer:
        raise banc.Annonce(banc.annonce(ip, suivis, f"le banc électrique (sortie {n}, {voie} ; filtres Out, phase "
                                                    f"0…−180°, gain −6 dB, délai 1 ms, limiteur de test {seuil_test:g} "
                                                    f"puis {seuil_test - 10:g} dB, bosse PEQ +6 dB à 1 kHz)")
                           .replace(f"; {banc.PAS_ANNONCE}", "; réglages de TEST, amplis coupés"))
    dossier = Path(dossier or RACINE / "mesures" / f"banc-electrique-{dt.datetime.now():%Y%m%d-%H%M}")
    dossier.mkdir(parents=True, exist_ok=True)
    origine = {ch: (d.get(ch) or "").strip() for ch in suivis}
    res = {"simulation": bool(moteur.simule),"date": dt.date.today().isoformat(), "sortie": n, "voie": voie, "niveau_dbfs": niveau,
           "firmware": "à relever (VENU360 > Utility > System Info)", "source": "banc_electrique.py, boucle électrique"}
    try:
        res["instantane"] = str(dbx_hiqnet.instantane_avant(d, ip, f"avant-banc-electrique-S{n}"))
        dd.muter_et_verifier(d)
        moteur.verifier()
        journal(f"BANC ELEC {ip} début sortie {n} ({voie}) — amplis coupés (confirmé), "
                f"{'SIMULATION' if moteur.simule else 'REW, signal autorisé par Louis'}")
        X = f"{P}CrossoverIIR{n}\\SV\\Band_1_"
        L = f"{P}Limiter{n}\\SV\\"
        base = f"{P}PEQOut{n}\\SV\\"
        # 1) protection d'abord : limiteur On au seuil de test (plus bas) AVANT de retirer les filtres
        _ecrire(d, L + "Limiter", "On", journal, ip)
        _ecrire(d, L + "Threshold", f"{seuil_test:.1f}dB", journal, ip)
        # 2) PEQ de sortie à plat : Off, 8 bandes à 0 dB (relues), puis On à plat — seule la bosse de test changera
        _ecrire(d, base + "ParametricEQ", "Off", journal, ip)
        for i in range(1, 9):
            _ecrire(d, base + f"Band_{i}_Gain", "0.0dB", journal, ip)
        _ecrire(d, base + "ParametricEQ", "On", journal, ip)
        for ch, val in ((X + "HPFrequency", "Out"), (X + "LPFrequency", "Out"), (X + "Gain", "0.0dB"),
                        (X + "Polarity", "Normal"), (X + "Phase", "0 deg")):
            _ecrire(d, ch, val, journal, ip)
        banc.ecrire_delai(d, n, 0.0, journal, ip)

        def mes(nom, niv=None):
            if moteur.simule:
                moteur.ouvertes = {n}
            else:
                banc.ouvrir(d, {n})
            try:
                m = moteur.mesurer(f"{dossier.name}_{nom}", niveau if niv is None else niv)
            finally:
                if moteur.simule:
                    moteur.ouvertes = set()
                else:
                    banc.ouvrir(d, set())
            (dossier / f"{nom}.json").write_text(json.dumps(m), encoding="utf-8")
            return m

        # 1. phase
        ph = {}
        for r in REGLAGES_PHASE:
            _ecrire(d, X + "Phase", f"{r:g} deg", journal, ip)
            ph[r] = mes(f"phase_{-r:g}")
        _ecrire(d, X + "Phase", "0 deg", journal, ip)
        res["phase"] = modele(ph)
        # 3. contrôles (avant le limiteur bas : signal non limité)
        ref = mes("controle_ref")
        _, _, dlin = ecart(mes("controle_lineaire", niveau + GAIN_TEST_DB), ref)   # −6 dB à l'entrée -> −6 dB ?
        _ecrire(d, X + "Gain", f"{GAIN_TEST_DB:.1f}dB", journal, ip)
        _, _, dmag = ecart(mes("controle_gain"), ref)
        _ecrire(d, X + "Gain", "0.0dB", journal, ip)
        banc.ecrire_delai(d, n, DELAI_TEST_MS, journal, ip)
        f, dphi, _ = ecart(mes("controle_delai"), ref)
        banc.ecrire_delai(d, n, 0.0, journal, ip)
        k = (f >= BANDE_HZ[0]) & (f <= BANDE_HZ[1])
        w = 2 * np.pi * f[k]
        tau_ms = -float(np.sum(w * np.deg2rad(dphi[k])) / np.sum(w * w)) * 1000
        g_mes = float(np.median(dmag[k]))
        lineaire = abs(float(np.median(dlin[k])) - GAIN_TEST_DB) < 0.3      # sinon le limiteur agit déjà
        res["controles"] = {"gain_ecrit_db": GAIN_TEST_DB, "gain_mesure_db": round(g_mes, 2),
                            "gain": ("conforme" if abs(g_mes - GAIN_TEST_DB) < 0.3 else "ECART") if lineaire else
                            "non conclusif (limiteur actif au niveau de mesure)",
                            "delai_ecrit_ms": DELAI_TEST_MS, "delai_mesure_ms": round(tau_ms, 3),
                            "delai": "conforme" if abs(tau_ms - DELAI_TEST_MS) < 0.02 else "ECART"}
        res["controles"]["conformes"] = res["controles"]["delai"] == "conforme" and res["controles"]["gain"] != "ECART"
        # 2. ordre PEQ / limiteur (seuils de test PLUS BAS que l'actuel ; PEQ On à plat, seule la bande 1 change)
        lim_bas = mes("chaine_seuil_bas")
        _ecrire(d, L + "Threshold", f"{seuil_test - 10:.1f}dB", journal, ip)
        plat = mes("chaine_seuil_plus_bas")
        for cle, val in (("Band_1_Type", BOSSE_TEST["type"]), ("Band_1_Frequency", "1.00kHz"), ("Band_1_Q", "1"),
                         ("Band_1_Gain", f"{BOSSE_TEST['gain_db']:.1f}dB")):
            _ecrire(d, base + cle, val, journal, ip)
        bosse = mes("chaine_bosse")
        _ecrire(d, base + "Band_1_Gain", "0.0dB", journal, ip)
        res |= ordre_chaine(lim_bas, plat, bosse, plat)
    except BaseException as e:
        try:
            dd.muter_et_verifier(d)
            journal(f"BANC ELEC {ip} ARRET sortie {n} : {e!r} — sorties re-mutées (relu)")
        except Exception as e2:   # noqa: BLE001
            print(f"!!! RE-MUTE NON CONFIRMÉ ({e2}) : COUPER LES AMPLIS", file=sys.stderr)
        res["erreur"] = repr(e)
        raise
    finally:
        try:
            res["retablissement"] = banc.retablir(d, origine, journal, ip, chemin_config, {n: voie})
        except Exception as e3:   # noqa: BLE001
            res["retablissement"] = {"erreur": repr(e3)}
        if not banc.retablissement_complet(res):
            msg = (f"BANC ELEC {ip} RÉTABLISSEMENT INCOMPLET sortie {n} : {res['retablissement']} — paramètres "
                   "encore en état de test (filtres Out possibles) : sorties laissées mutées, vérifier avant de démuter")
            journal(msg)
            print("!!! " + msg, file=sys.stderr)
        banc._ecrire_atomique(dossier / "resultat.yaml",
                              yaml.safe_dump(json.loads(json.dumps(res)), allow_unicode=True, sort_keys=False))
    res["dossier"] = str(dossier)
    return res


def enregistrer(res: dict, cible: Path | None = None) -> Path:
    """Copie le résultat dans data/protocoles/venu360_phase.yaml (l'ancien est gardé en .ancien)."""
    if res.get("simulation") is not False:
        raise ValueError("résultat simulé (ou d'origine inconnue) : jamais enregistré comme mesure de la machine")
    if "retablissement" not in res or "phase" not in res:
        raise ValueError("résultat incomplet (phase ou rétablissement absents) : rien n'est enregistré")
    if res.get("erreur") or not banc.retablissement_complet(res):
        raise ValueError("banc incomplet (erreur ou rétablissement incomplet) : rien n'est enregistré")
    if not (res.get("controles") or {}).get("conformes"):
        raise ValueError(f"contrôles gain/délai non conformes ({res.get('controles')}) : boucle ou référence de temps "
                         "à vérifier ; rien n'est enregistré")
    cible = Path(cible or banc.FICHIER_BANC_ELECTRIQUE)
    if cible.exists():
        shutil.copy(cible, cible.with_suffix(".yaml.ancien"))
    doc = {k: res[k] for k in ("date", "firmware", "source", "sortie", "voie", "phase", "chaine_sortie", "controles")
           if k in res}
    doc["portee"] = (f"mesuré sur la sortie {res['sortie']} seule ; supposé identique sur les 6 sorties (même bloc "
                     "PEQOut/Limiter) — à revérifier après un changement de firmware ou de topologie")
    doc["motif_chaine"] = res.get("motif")
    banc._ecrire_atomique(cible, yaml.safe_dump(json.loads(json.dumps(doc)), allow_unicode=True, sort_keys=False))
    return cible


# ---------------------------------------------------------------- simulation (tests)
class MoteurElectriqueSimule:
    """Boucle électrique simulée lisant l'état réel du (faux) VENU360 : phase selon `vrai` (modèle caché), PEQOut,
    gain, délai, limiteur (écrêtage au seuil, avant ou après le PEQ selon `chaine`). Jamais de démute réel."""
    simule = True

    def __init__(self, d, n: int, vrai: dict, chaine: str = "peq_avant_limiteur", latence_ms: float = 1.5):
        self.d, self.n, self.vrai, self.chaine, self.lat = d, n, vrai, chaine, latence_ms
        self.f = 20.0 * 2 ** (np.arange(0, 240) / 24)
        self.ouvertes: set[int] = set()

    def verifier(self):
        pass

    def mesurer(self, nom: str, niveau: float) -> dict:
        n, d, f = self.n, self.d, self.f
        if n not in self.ouvertes:
            H = 1e-5 * np.ones(len(f), dtype=complex)
        else:
            X = f"{P}CrossoverIIR{n}\\SV\\Band_1_"
            H = banc.reponse_phase(self.vrai, banc._ms(d.get(X + "Phase")), f)
            H = H * 10 ** (banc._ms(d.get(X + "Gain")) / 20)
            peq = eq_auto.reponse(banc._bandes_lues(d, f"{P}PEQOut{n}\\SV\\", 8), f)
            L = f"{P}Limiter{n}\\SV\\"
            seuil = banc._ms(d.get(L + "Threshold")) if str(d.get(L + "Limiter")).strip() == "On" else 1e9
            if self.chaine == "peq_avant_limiteur":
                H = H * peq
                niv = niveau + 20 * np.log10(np.abs(H))
                H = H * 10 ** ((np.minimum(niv, seuil) - niv) / 20)
            else:
                niv = niveau + 20 * np.log10(np.abs(H))
                H = H * 10 ** ((np.minimum(niv, seuil) - niv) / 20) * peq
            H = H * np.exp(-2j * np.pi * f * (self.lat + banc.delai_sortie(d, n)) / 1000)
        return {"id": nom, "f_hz": f.tolist(), "mag_db": (20 * np.log10(np.abs(H))).tolist(),
                "phase_deg": np.rad2deg(np.angle(H)).tolist(), "ref_temps": "simulation"}


# ---------------------------------------------------------------- CLI
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--sortie", type=int, required=True)
    ap.add_argument("--ip", required=True)
    ap.add_argument("--port", type=int, default=19272)
    ap.add_argument("--rew", default="localhost:4735")
    ap.add_argument("--niveau", type=float, default=-20.0)
    ap.add_argument("--seuil-test", type=float, default=-40.0)
    ap.add_argument("--amplis-coupes", action="store_true")
    ap.add_argument("--signal-autorise", action="store_true")
    ap.add_argument("--protection-validee", action="store_true")
    ap.add_argument("--appliquer", action="store_true", help="sans lui : annonce de ce qui serait écrit, rien n'est touché")
    ap.add_argument("--enregistrer", action="store_true", help="copie le résultat dans data/protocoles/venu360_phase.yaml")
    ap.add_argument("--simulation", action="store_true", help="boucle simulée (HYPOTHÈSE rotation constante)")
    a = ap.parse_args()
    import dbx_hiqnet
    try:
        with dbx_hiqnet.Dbx(a.ip, a.port, os.environ.get("DBX_MDP") or "administrator") as d:
            moteur = (MoteurElectriqueSimule(d, a.sortie, banc.PHASE_HYPOTHESE) if a.simulation
                      else banc.MoteurREW(a.rew))
            r = lancer(d, moteur, a.config, a.sortie, dbx_hiqnet.journaliser, a.ip, a.amplis_coupes,
                       a.signal_autorise, a.protection_validee, a.niveau, a.seuil_test, appliquer=a.appliquer)
        print(f"Banc électrique sortie {a.sortie} : phase « {r['phase']['type']} » · chaîne {r['chaine_sortie']}"
              f"{' (' + r['motif'] + ')' if r.get('motif') else ''} · contrôles {r['controles']}")
        print(f"Résultat : {r['dossier']}\\resultat.yaml · rétablissement "
              f"{'complet' if banc.retablissement_complet(r) else 'INCOMPLET : ' + str(r['retablissement'])}")
        if a.enregistrer:
            if a.simulation:
                sys.exit("--enregistrer refusé en simulation (le modèle simulé est une hypothèse)")
            print(f"Enregistré : {enregistrer(r)}")
        if not banc.retablissement_complet(r):
            sys.exit("RÉTABLISSEMENT INCOMPLET : sorties laissées mutées ; vérifier avant de démuter.")
    except banc.Annonce as e:
        print(e)
        sys.exit(0)
    except (ValueError, Annule, dd.SortiesNonMutees, OSError) as e:
        sys.exit(f"REFUS / ARRÊT : {e}")


if __name__ == "__main__":
    main()
