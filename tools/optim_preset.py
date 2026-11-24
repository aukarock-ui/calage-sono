#!/usr/bin/env python3
"""
optim_preset.py — Optimisation COMPLÈTE du preset à la mesure (REW sweep, un micro) : gains de voie, délais, phase
(Band_1_Phase du crossover), EQ par voie (PEQOut), EQ générale (PEQ1 d'entrée). Chaque étape : mesure, correction
par pas bornés, remesure. Les écarts mesurés reviennent dans le calcul (preset_auto --recaler).

  1. preparer <config> --style techno --positions P1:20x1.7,P2:20x1.2,P3:10x1.7 [--accepter-non-verifie]
     [--eq-generale <yaml>] [--etapes gains,delais,phase,eq-voies,eq-generale]
     -> plan de départ (preset_auto, v2), dossier mesures/optim-<sys>-<date>/. Aucun accès machine, aucun son.
  2. mesurer <dossier> --position P1 --ip … --protection-validee --signal-autorise [--niveau-max] [--simulation]
     -> 1re position (P1, ONAX) : plan écrit sorties mutées, puis GAINS (niveau de chaque voie seule dans sa bande
        contre la cible du style, ±3 dB par écriture, gains ≤ 0), DÉLAIS (banc.Seance.aligner), PHASE (aux raccords
        à perte de somme > 0,5 dB : rotation de la voie basse choisie avec le modèle du banc électrique, ±45° par
        écriture, gardée seulement si la mesure confirme) ; puis voies seules et système.
        Positions suivantes : plan + réglages trouvés réécrits, voies seules et système (moyenne spatiale).
  3. egaliser <dossier> --ip … (micro laissé à la dernière position) : EQ PAR VOIE depuis la moyenne spatiale des
     voies seules (fréquences fiables = écart entre positions < 2 dB ; creux étroits jamais remplis ; bosses ≤ +3 dB
     et seulement si l'ordre PEQ/limiteur est établi ; pas de bosse sous 1,5 × HPF), puis EQ GÉNÉRALE (système,
     corrections larges, Q ≤ 2, Σ|gain| ≤ 6 dB) ; ±3 dB par écriture, gardées seulement si la mesure s'améliore.
  4. conclure <dossier> -> rapport avant/après, `recalage-propose.yaml` (format preset_auto --recaler : délais, écart
     de niveau, phase, EQ de voie), `eq-generale.yaml` (salle), historique data/apprentissage/preset.yaml.
     Rien n'est stocké dans le processeur : preset définitif = recaler + preset_auto + appliquer-plan, « go » de Louis.

GARDE-FOUS : ceux de banc.Seance (signal seulement avec --signal-autorise après le « go » de Louis ; niveau par voie
≤ --niveau-max ≤ −10 dBFS ; --protection-validee ; re-mute sur toute erreur ; rétablissement de l'état du début de
lancement sans protection moins sûre ; simulation jamais démutée). Seuils, filtres et polarité ne sont jamais
modifiés par l'optimisation (une inversion reste une proposition).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyse_raccord as ar       # noqa: E402
import banc                        # noqa: E402
import dbx_description as dd       # noqa: E402
import eq_auto                     # noqa: E402
import reglages_pile as rp         # noqa: E402
from banc import P, Annule, Seance, charger_etat, cles_raccords, ecrire_delai, sauver_etat  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
HISTORIQUE = RACINE / "data" / "apprentissage" / "preset.yaml"
ETAPES = ("gains", "delais", "phase", "eq-voies", "eq-generale")
PAS_GAIN_DB, PAS_PHASE_DEG, PAS_EQ_DB = 3.0, 45.0, 3.0
GAIN_MIN_DB = -40.0
TOLERANCE_GAIN_DB = 0.5
ITERATIONS_GAIN = 4
SEUIL_PHASE_DB = 0.5          # perte de somme au raccord au-delà de laquelle on essaie la phase
PHASE_MIN_DEG = -135.0        # −180° = inversion de polarité (modèle « constante ») : décision de Louis, pas d'optim
GRILLE_PHASE = [-15.0 * k for k in range(10)]          # 0 … −135°
CORRECTION_GAIN_MAX_DB = 6.0  # hausse max par voie au-dessus du gain du plan ; écart initial au-delà : panne probable
ECART_SPATIAL_MAX_DB = 2.0
EQ_GENERALE_SOMME_MAX_DB = 6.0
EQ_GENERALE_HAUT_HZ = 12000.0


# ---------------------------------------------------------------- utilitaires
def _bande_interieure(x: dict) -> tuple[float, float]:
    """Bande de la voie hors pentes des filtres (1/3 d'octave à l'intérieur des coupures)."""
    lo = ((x.get("hpf") or {}).get("hz") or 20.0) * 2 ** (1 / 3)
    hi = ((x.get("lpf") or {}).get("hz") or 16000.0) * 2 ** (-1 / 3)
    return lo, max(hi, lo * 1.26)


def niveau_bande(m: dict, x: dict, style: str) -> float:
    """Niveau de la voie seule dans sa bande intérieure, RELATIF à la cible du style (moyenne en puissance)."""
    f, mag = np.asarray(m["f_hz"]), np.asarray(m["mag_db"])
    lo, hi = _bande_interieure(x)
    k = (f >= lo) & (f <= hi)
    cible = np.array([rp.cible_style(style, v) for v in f[k]])
    return float(10 * np.log10(np.mean(10 ** ((mag[k] - cible) / 10))))


def ecarts_niveau(solos: dict, plan: dict, style: str) -> dict:
    """Écart de chaque voie à la cible (dB), moyenne des voies retirée : + = trop forte."""
    L = {x["nom"]: niveau_bande(solos[x["nom"]], x, style) for x in plan["voies"]}
    ref = float(np.mean(list(L.values())))
    return {n: round(v - ref, 2) for n, v in L.items()}


def _lire_gain(d, n: int) -> float:
    return banc._ms(d.get(f"{P}CrossoverIIR{n}\\SV\\Band_1_Gain"))


def _lire_phase(d, n: int) -> float:
    return banc._ms(d.get(f"{P}CrossoverIIR{n}\\SV\\Band_1_Phase"))


def ecrire_par_pas(d, chemin: str, actuel: float, cible: float, pas: float, fmt, bornes, journal, ip: str) -> float:
    """Va de `actuel` à `cible` par pas ≤ `pas`, chaque pas écrit et relu (valeur validée par la machine)."""
    if not bornes[0] <= cible <= bornes[1] or not math.isfinite(cible):
        raise Annule(f"{chemin} : {cible} hors bornes {bornes}")
    desc = dd.decrire_param(d, chemin)
    while abs(cible - actuel) > 1e-6:
        val = actuel + max(-pas, min(pas, cible - actuel))
        txt = fmt(val)
        motif = dd.valider_machine(desc, txt) if desc else None
        if motif:
            raise Annule(f"{chemin} = {txt} refusé par la machine ({motif})")
        relu, ok, err = dd.ecrire_et_relire(d, chemin, txt, desc)
        ok = ok or dd.arrondi_machine(desc, txt, relu, chemin)
        journal(f"OPTIM {ip} {chemin} : {fmt(actuel)} -> {txt} (relu {relu}){'' if ok else ' ECART'}")
        if not ok:
            raise Annule(f"{chemin} : écrit {txt}, relu {relu} ({err})")
        actuel = val
    return actuel


def ecrire_gain(d, n, cible, journal, ip):
    return ecrire_par_pas(d, f"{P}CrossoverIIR{n}\\SV\\Band_1_Gain", _lire_gain(d, n), round(cible, 1), PAS_GAIN_DB,
                          lambda x: f"{x:.1f}dB", (GAIN_MIN_DB, 0.0), journal, ip)


def ecrire_phase(d, n, cible, journal, ip):
    return ecrire_par_pas(d, f"{P}CrossoverIIR{n}\\SV\\Band_1_Phase", _lire_phase(d, n), round(cible), PAS_PHASE_DEG,
                          lambda x: f"{x:g} deg", (PHASE_MIN_DEG, 0.0), journal, ip)


def lire_peq(d, base: str, n_bandes: int) -> list[dict]:
    """Les n bandes telles qu'écrites (index = numéro de bande), quel que soit l'état On/Off."""
    return [{"type": str(d.get(base + f"Band_{i}_Type")).strip(), "hz": banc._hz(d.get(base + f"Band_{i}_Frequency")),
             "gain_db": banc._ms(d.get(base + f"Band_{i}_Gain")), "q": banc._ms(d.get(base + f"Band_{i}_Q"))}
            for i in range(1, n_bandes + 1)]


def _meme_forme(a: dict, b: dict) -> bool:
    return bool(a["type"] == b["type"] and a["hz"] and abs(a["hz"] / b["hz"] - 1) < 0.01
                and abs(a["q"] - b["q"]) <= max(0.02, 0.02 * b["q"]))


def _ecrire_val(d, ch: str, val: str) -> None:
    desc = dd.decrire_param(d, ch)
    motif = dd.valider_machine(desc, val) if desc else None
    if motif:
        raise Annule(f"{ch} = {val} refusé par la machine ({motif})")
    relu, ok, err = dd.ecrire_et_relire(d, ch, val, desc)
    if not (ok or dd.arrondi_machine(desc, val, relu, ch)):
        raise Annule(f"{ch} : écrit {val}, relu {relu} ({err})")


def ecrire_peq(d, base: str, visees: list[dict], n_bandes: int, journal, ip: str) -> list[dict]:
    """Amène le PEQ `base` à `visees` (bandes 1..k ; les autres à 0 dB) : gain ≤ ±3 dB par écriture et par bande ; une
    bande qui change de forme (type/fréquence/Q) est d'abord ramenée à 0 dB par pas, puis reformée à 0 dB. PEQ Off au
    départ : bandes mises à 0 dB (inaudible) avant de l'activer. Bosse sur un PEQ de SORTIE refusée tant que l'ordre
    PEQ/limiteur n'est pas établi. Relecture complète à la fin : tout écart -> Annule."""
    import dbx_plan
    import preset_auto
    visees = preset_auto.valider_peq(visees, n_bandes, base)
    if "PEQOut" in base and any(b["gain_db"] > 0 for b in visees) and dbx_plan.chaine_sortie() != "peq_avant_limiteur":
        raise Annule(f"{base} : bosse refusée — ordre PEQOut / limiteur non établi (banc_electrique.py)")
    cibles = visees + [None] * (n_bandes - len(visees))          # None = bande à 0 dB, forme indifférente
    if str(d.get(base + "ParametricEQ")).strip() != "On":
        for i, c in enumerate(lire_peq(d, base, n_bandes), 1):
            if abs(c["gain_db"]) >= 0.05:
                _ecrire_val(d, base + f"Band_{i}_Gain", "0.0dB")
        if not visees:
            return []
        _ecrire_val(d, base + "ParametricEQ", "On")
    for _ in range(40):
        fini = True
        for i, (c, t) in enumerate(zip(lire_peq(d, base, n_bandes), cibles), 1):
            tg = t["gain_db"] if t else 0.0
            if t and not _meme_forme(c, t):
                fini = False
                if abs(c["gain_db"]) >= 0.05:
                    g = c["gain_db"] - max(-PAS_EQ_DB, min(PAS_EQ_DB, c["gain_db"]))
                    _ecrire_val(d, base + f"Band_{i}_Gain", f"{g:.1f}dB")
                    continue
                for cle, val in (("Type", t["type"]), ("Frequency", _f(t["hz"])), ("Q", f"{t['q']:g}")):
                    _ecrire_val(d, base + f"Band_{i}_{cle}", val)
                continue
            if abs(c["gain_db"] - tg) >= 0.05:
                fini = False
                g = c["gain_db"] + max(-PAS_EQ_DB, min(PAS_EQ_DB, tg - c["gain_db"]))
                _ecrire_val(d, base + f"Band_{i}_Gain", f"{g:.1f}dB")
        if fini:
            break
    else:
        raise Annule(f"{base} : EQ non atteinte après 40 passes (valeurs relues ≠ visées)")
    if not visees:
        _ecrire_val(d, base + "ParametricEQ", "Off")
    relu = lire_peq(d, base, n_bandes)
    for i, (c, t) in enumerate(zip(relu, cibles), 1):
        if abs(c["gain_db"] - (t["gain_db"] if t else 0.0)) > 0.11 or (t and not _meme_forme(c, t)):
            raise Annule(f"{base} bande {i} : relu {c}, visé {t}")
    journal(f"OPTIM {ip} {base} : EQ {[(b['hz'], b['gain_db']) for b in visees] or 'à plat (Off)'} atteinte, relue")
    return visees


def _f(hz: float) -> str:
    import dbx_plan
    return dbx_plan._f(hz)


def suivis_supplementaires(plan: dict) -> list[str]:
    """Chemins que l'optimisation peut toucher en plus du plan (toutes les bandes d'EQ) : rétablis en fin de lancement."""
    out = []
    for x in plan["voies"]:
        n = x["sortie"]
        out += [f"{P}PEQOut{n}\\SV\\Band_{i}_{k}" for i in range(1, 9) for k in ("Type", "Frequency", "Q", "Gain")]
        out += [f"{P}PEQOut{n}\\SV\\ParametricEQ", f"{P}CrossoverIIR{n}\\SV\\Band_1_Phase",
                f"{P}DelayOut{n}\\SV\\Amount", f"{P}DelayOut{n}\\SV\\Delay"]   # délais : même si le plan n'en a pas
    out += [f"{P}PEQ1\\SV\\Band_{i}_{k}" for i in range(1, 13) for k in ("Type", "Frequency", "Q", "Gain")]
    return out + [f"{P}PEQ1\\SV\\ParametricEQ"]


def _coupures(plan: dict) -> dict:
    v = {x["nom"]: x for x in plan["voies"]}
    return {f"{b}/{h}": ((v[h].get("hpf") or {}).get("hz") or (v[b].get("lpf") or {}).get("hz"))
            for b, h in cles_raccords(plan)}


# ---------------------------------------------------------------- 1. preparer
def preparer(chemin_config: str, style: str | None, positions: list[dict], etapes=ETAPES,
             accepter_non_verifie: bool = False, eq_generale: str | None = None, dossier=None) -> Path:
    import preset_auto
    inconnues = set(etapes) - set(ETAPES)
    if inconnues:
        raise ValueError(f"étapes inconnues {sorted(inconnues)} (choix : {', '.join(ETAPES)})")
    if not positions:
        raise ValueError("au moins une position de micro")
    src = Path(chemin_config).resolve()
    p1 = positions[0]
    plan = preset_auto.construire_plan(str(src), style, p1["distance_m"], p1["hauteur_m"], accepter_non_verifie,
                                       eq_generale)
    if plan["bloquant"]:
        raise ValueError("plan BLOQUANT : " + " / ".join(plan["bloquants"]))
    dossier = Path(dossier or RACINE / "mesures" / f"optim-{src.stem}-{dt.datetime.now():%Y%m%d-%H%M}")
    if (dossier / "etat.json").exists():
        raise ValueError(f"{dossier} contient déjà une optimisation")
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    etat = {"version": 1, "config": str(src), "systeme": plan["systeme"], "style": plan["style"],
            "positions": positions, "etapes": list(etapes), "plan": str(dossier / "plan.json"),
            "cree": dt.datetime.now().isoformat(timespec="seconds"), "positions_faites": [], "egalise": False,
            "niveau_dbfs": None, "reglages": {"gains": {}, "delais": {}, "phase": {}, "peq_voies": {}, "peq_entree": []},
            "bilan": {}, "alertes": []}
    sauver_etat(dossier, etat)
    return dossier


# ---------------------------------------------------------------- 2. mesurer
def _plan(etat):
    return json.loads(Path(etat["plan"]).read_text(encoding="utf-8"))


def _seance(d, moteur, dossier, etat, pos, plan, journal, ip, niveau_max, prot, sig):
    s = Seance(d, moteur, dossier, etat, pos, [plan], journal, ip, niveau_max, prot, sig,
               lambda e: sauver_etat(dossier, e), etiquette="OPTIM")
    s.suivis = sorted(set(s.suivis) | set(suivis_supplementaires(plan)))
    return s


def reappliquer(d, plan: dict, reg: dict, journal, ip: str) -> None:
    """Plan (sorties mutées) puis réglages déjà trouvés, chacun par pas bornés et relu."""
    import dbx_plan
    r = dbx_plan.appliquer(d, plan, journal, ip)
    if not r["conforme"]:
        raise Annule(f"plan non conforme ({r['arret'] or r['echecs'] or r['refus'] or r['derives']})")
    par_nom = {x["nom"]: x for x in plan["voies"]}
    for nom, g in reg["gains"].items():
        ecrire_gain(d, par_nom[nom]["sortie"], g, journal, ip)
    for nom, ms in reg["delais"].items():
        ecrire_delai(d, par_nom[nom]["sortie"], ms, journal, ip)
    for nom, ph in reg["phase"].items():
        ecrire_phase(d, par_nom[nom]["sortie"], ph, journal, ip)
    for nom, b in reg["peq_voies"].items():
        ecrire_peq(d, f"{P}PEQOut{par_nom[nom]['sortie']}\\SV\\", b, 8, journal, ip)
    if reg["peq_entree"]:
        ecrire_peq(d, f"{P}PEQ1\\SV\\", reg["peq_entree"], 12, journal, ip)


def etape_gains(s: Seance, d, plan, etat, solos, tag, journal, ip) -> dict:
    par_nom = {x["nom"]: x for x in plan["voies"]}
    initial = ecarts_niveau(solos, plan, etat["style"])
    pire = max(initial, key=lambda n: abs(initial[n]))
    if abs(initial[pire]) > CORRECTION_GAIN_MAX_DB:
        raise Annule(f"gains : {pire} à {initial[pire]:+.1f} dB de la cible (> {CORRECTION_GAIN_MAX_DB:g} dB) — panne "
                     "probable (câble, ampli, calibre, micro) plutôt qu'un réglage : vérifier avant de relancer")
    g0 = {n: _lire_gain(d, x["sortie"]) for n, x in par_nom.items()}     # gains du plan
    for it in range(ITERATIONS_GAIN):
        e = ecarts_niveau(solos, plan, etat["style"])
        if max(abs(v) for v in e.values()) <= TOLERANCE_GAIN_DB:
            break
        voulu = {n: _lire_gain(d, par_nom[n]["sortie"]) - e[n] for n in e}
        haut = max(voulu.values())
        voulu = {n: max(GAIN_MIN_DB, g - max(0.0, haut)) for n, g in voulu.items()}   # gains ≤ 0 dB
        voulu = {n: min(g0[n] + CORRECTION_GAIN_MAX_DB, g) for n, g in voulu.items()}   # hausse bornée ; baisse = sens sûr
        for n, g in voulu.items():
            actuel = _lire_gain(d, par_nom[n]["sortie"])
            pas = max(-PAS_GAIN_DB, min(PAS_GAIN_DB, g - actuel))
            if abs(pas) >= 0.1:
                neuf = actuel + pas
                # niveau de mesure baissé d'autant que le gain dépasse celui du plan : le niveau joué reste celui de la rampe
                s.niveaux[n] = round(s.niveaux[n] - (max(0.0, neuf - g0[n]) - max(0.0, actuel - g0[n])), 1)
                ecrire_gain(d, par_nom[n]["sortie"], neuf, journal, ip)
                solos[n] = s.mesure(f"{tag}_{n}_g{it}", {par_nom[n]["sortie"]}, s.niveaux[n])
    final = ecarts_niveau(solos, plan, etat["style"])
    etat["reglages"]["gains"] = {n: _lire_gain(d, x["sortie"]) for n, x in par_nom.items()}
    etat["niveau_dbfs"] = dict(s.niveaux)
    return {"ecart_initial_db": initial, "ecart_final_db": final}


def _perte_predite(a: dict, b: dict, fc: float, modele: dict, phi: float, phi0: float) -> tuple[float, dict]:
    f = np.asarray(b["f_hz"])
    H = ar.complexe(b)[1] * banc.reponse_phase(modele, phi, f) / banc.reponse_phase(modele, phi0, f)
    b2 = {"f_hz": b["f_hz"], "mag_db": (20 * np.log10(np.abs(H))).tolist(), "phase_deg": np.rad2deg(np.angle(H)).tolist()}
    al = ar.meilleur_delai(a, b2, fc, banc.PLAGE_ALIGNEMENT_MS)
    H2 = ar.complexe(b2)[1] * np.exp(-2j * np.pi * f * al["delai_non_inverse_ms"] / 1000)
    b3 = {"f_hz": b["f_hz"], "mag_db": (20 * np.log10(np.abs(H2))).tolist(), "phase_deg": np.rad2deg(np.angle(H2)).tolist()}
    return ar.perte_somme_db(a, b3, ar.somme_complexe(a, b3), fc), b3


def etape_phase(s: Seance, d, plan, etat, solos, tag, journal, ip) -> dict:
    modele = banc.modele_phase()
    if modele is None:
        s.resultat["alertes"].append("phase : étape sautée — Band_1_Phase non caractérisée (banc_electrique.py, "
                                     "amplis coupés) : son effet réel est inconnu")
        return {"sautee": "modele de phase absent"}
    par_nom = {x["nom"]: x for x in plan["voies"]}
    cp = _coupures(plan)
    out = {}
    for bas, haut in reversed(cles_raccords(plan)):
        k, fc = f"{bas}/{haut}", cp[f"{bas}/{haut}"]
        pair = s.mesure(f"{tag}_{k.replace('/', '+')}_avant_phase", {par_nom[bas]["sortie"], par_nom[haut]["sortie"]},
                        s.niv(bas, haut))
        avant = ar.perte_somme_db(solos[haut], solos[bas], pair, fc)
        if avant <= SEUIL_PHASE_DB:
            out[k] = {"perte_db": round(avant, 2), "phase": "inutile"}
            continue
        n = par_nom[bas]["sortie"]
        phi0 = _lire_phase(d, n)
        pred = {phi: _perte_predite(solos[haut], solos[bas], fc, modele, phi, phi0)[0] for phi in GRILLE_PHASE}
        meilleur = min(pred, key=pred.get)
        if pred[meilleur] > avant - 0.3:
            out[k] = {"perte_db": round(avant, 2), "phase": "sans gain prédit", "predit": round(pred[meilleur], 2)}
            continue
        ecrire_phase(d, n, meilleur, journal, ip)
        solos[bas] = s.mesure(f"{tag}_{bas}_phase", {n}, s.niveaux[bas])
        s.aligner(plan, cp, solos, f"{tag}_phase", "optim")
        pair = s.mesure(f"{tag}_{k.replace('/', '+')}_apres_phase", {n, par_nom[haut]["sortie"]}, s.niv(bas, haut))
        apres = ar.perte_somme_db(solos[haut], solos[bas], pair, fc)
        if apres > avant - 0.2:      # la mesure ne confirme pas : retour
            ecrire_phase(d, n, phi0, journal, ip)
            solos[bas] = s.mesure(f"{tag}_{bas}_phase_retour", {n}, s.niveaux[bas])
            s.aligner(plan, cp, solos, f"{tag}_phase_retour", "optim")
            out[k] = {"perte_db": round(avant, 2), "essai_deg": meilleur, "mesure_db": round(apres, 2), "phase": "rejetée"}
        else:
            etat["reglages"]["phase"][bas] = meilleur
            out[k] = {"perte_db": round(avant, 2), "phase_deg": meilleur, "predit_db": round(pred[meilleur], 2),
                      "mesure_db": round(apres, 2)}
    etat["reglages"]["delais"] = {nm: banc.delai_sortie(d, x["sortie"]) for nm, x in par_nom.items()}
    return out


def mesurer_position(dossier, nom_position: str, d, moteur_pour, journal, ip: str, protection_validee: bool,
                     signal_autorise: bool, niveau_max: float = -12.0) -> dict:
    etat = charger_etat(dossier)
    pos = next((p for p in etat["positions"] if p["nom"] == nom_position), None)
    if pos is None:
        raise ValueError(f"position {nom_position} inconnue")
    if nom_position in etat["positions_faites"]:
        raise ValueError(f"position {nom_position} déjà mesurée")
    if etat["egalise"]:
        raise ValueError("égalisation déjà faite : conclure, ou préparer une nouvelle optimisation")
    plan = _plan(etat)
    sorties = {x["sortie"]: x["nom"] for x in plan["voies"]}
    premiere = not etat["positions_faites"]
    with _seance(d, moteur_pour(pos, sorties), dossier, etat, pos, plan, journal, ip, niveau_max,
                 protection_validee, signal_autorise) as s:
        reappliquer(d, plan, etat["reglages"] if not premiere else
                    {"gains": {}, "delais": {}, "phase": {}, "peq_voies": {}, "peq_entree": []}, journal, ip)
        tag = f"{nom_position}"
        s.rampe(plan, tag)
        solos = s.solos(plan, tag + "_initial")
        if premiere:
            b = etat["bilan"]
            b["systeme_initial"] = _ecart_systeme(s.mesure(f"{tag}_systeme_initial", set(sorties), s.niv(*sorties.values())),
                                                  etat["style"])
            if "gains" in etat["etapes"]:
                b["gains"] = etape_gains(s, d, plan, etat, solos, tag, journal, ip)
            if "delais" in etat["etapes"]:
                avant = {k: v for k, v in _pertes(s, plan, solos, f"{tag}_avant_delais").items()}
                etat["reglages"]["delais"] = s.aligner(plan, _coupures(plan), solos, tag, "optim")
                b["delais"] = {"perte_avant_db": avant, "perte_apres_db": _pertes(s, plan, solos, f"{tag}_apres_delais"),
                               "delais_ms": etat["reglages"]["delais"]}
            if "phase" in etat["etapes"]:
                b["phase"] = etape_phase(s, d, plan, etat, solos, tag, journal, ip)
            sauver_etat(dossier, etat)
        solos = s.solos(plan, f"{tag}_final")
        s.mesure(f"{tag}_systeme", set(sorties), s.niv(*sorties.values()))
        etat["positions_faites"].append(nom_position)
        etat["alertes"] += s.resultat["alertes"]
        sauver_etat(dossier, etat)
    return s.resultat


def _pertes(s: Seance, plan, solos, tag) -> dict:
    par_nom = {x["nom"]: x for x in plan["voies"]}
    cp = _coupures(plan)
    out = {}
    for bas, haut in cles_raccords(plan):
        k = f"{bas}/{haut}"
        pair = s.mesure(f"{tag}_{bas}+{haut}", {par_nom[bas]["sortie"], par_nom[haut]["sortie"]}, s.niv(bas, haut))
        out[k] = round(ar.perte_somme_db(solos[haut], solos[bas], pair, cp[k]), 2)
    return out


def _ecart_systeme(m: dict, style: str) -> float:
    return round(ar.ecart_cible_db(m, style), 2)


# ---------------------------------------------------------------- 3. egaliser
def _moyenne(mesures: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    f = np.asarray(mesures[0]["f_hz"])
    M = np.vstack([np.asarray(m["mag_db"]) for m in mesures])
    moy = 10 * np.log10(np.mean(10 ** (M / 10), axis=0))
    norm = M - M.mean(axis=1, keepdims=True)         # forme seule (la distance change le niveau)
    return f, moy, np.std(norm, axis=0)


def calculer_eq_voie(mesures: list[dict], x: dict, style: str, bosses_autorisees: bool) -> dict:
    f, moy, ecart_pos = _moyenne(mesures)
    lo, hi = _bande_interieure(x)
    fiable = (f >= lo) & (f <= hi) & (ecart_pos < ECART_SPATIAL_MAX_DB if len(mesures) > 1 else True)
    cible = np.array([rp.cible_style(style, v) for v in f])
    hpf = (x.get("hpf") or {}).get("hz")
    return eq_auto.ajuster(f, moy - cible, fiable, n_max=6, bosse_max_db=3.0,
                           bosse_interdite_sous_hz=1.5 * hpf if hpf else None, bosses_autorisees=bosses_autorisees)


def calculer_eq_generale(mesures: list[dict], style: str, bosses_autorisees: bool, hpf_bas_hz: float | None) -> dict:
    """hpf_bas_hz : HPF de la voie la plus grave — pas de bosse sous 1,5 × HPF (excursion), et la plage fiable
    s'arrête à EQ_GENERALE_HAUT_HZ (on ne poursuit pas la chute naturelle de l'extrême aigu)."""
    f, moy, ecart_pos = _moyenne(mesures)
    bas = max(40.0, 1.5 * hpf_bas_hz) if hpf_bas_hz else 40.0
    fiable = (f >= bas) & (f <= EQ_GENERALE_HAUT_HZ) & (ecart_pos < ECART_SPATIAL_MAX_DB if len(mesures) > 1 else True)
    cible = np.array([rp.cible_style(style, v) for v in f])
    r = eq_auto.ajuster(f, moy - cible, fiable, n_max=4, bosse_max_db=3.0, coupe_max_db=6.0, q_bornes=(0.3, 2.0),
                        bosse_interdite_sous_hz=1.5 * hpf_bas_hz if hpf_bas_hz else None,
                        bosses_autorisees=bosses_autorisees)
    somme = sum(abs(b["gain_db"]) for b in r["bandes"])
    if somme > EQ_GENERALE_SOMME_MAX_DB:
        k = EQ_GENERALE_SOMME_MAX_DB / somme
        r["bandes"] = [dict(b, gain_db=round(b["gain_db"] * k, 1)) for b in r["bandes"]]
        r["reduite"] = f"Σ|gain| {somme:.1f} dB ramené à {EQ_GENERALE_SOMME_MAX_DB:g} dB"
    return r


def egaliser(dossier, d, moteur_pour, journal, ip: str, protection_validee: bool, signal_autorise: bool,
             niveau_max: float = -12.0) -> dict:
    import dbx_plan
    etat = charger_etat(dossier)
    if not etat["positions_faites"]:
        raise ValueError("aucune position mesurée")
    if etat["egalise"]:
        raise ValueError("égalisation déjà faite")
    pos = next(p for p in etat["positions"] if p["nom"] == etat["positions_faites"][-1])   # micro laissé là
    plan = _plan(etat)
    sorties = {x["sortie"]: x["nom"] for x in plan["voies"]}
    rep = Path(dossier) / "mesures"
    lire = lambda nom: json.loads((rep / f"{nom}.json").read_text(encoding="utf-8"))  # noqa: E731
    bosses = dbx_plan.chaine_sortie() == "peq_avant_limiteur"
    with _seance(d, moteur_pour(pos, sorties), dossier, etat, pos, plan, journal, ip, niveau_max,
                 protection_validee, signal_autorise) as s:
        reappliquer(d, plan, etat["reglages"], journal, ip)
        tag = f"{pos['nom']}_eq"
        bilan = {"bosses_autorisees": bosses}
        if not bosses:
            s.resultat["alertes"].append("EQ : creux seulement — ordre PEQ/limiteur non établi (banc_electrique.py)")
        if "eq-voies" in etat["etapes"]:
            bv = {}
            for x in plan["voies"]:
                nom, n = x["nom"], x["sortie"]
                mes = [lire(f"{p}_final_{nom}") for p in etat["positions_faites"]]
                calc = calculer_eq_voie(mes, x, etat["style"], bosses)
                ici_avant = lire(f"{pos['nom']}_final_{nom}")
                if not calc["bandes"]:
                    e_av = _ecart_voie(ici_avant, x, etat["style"])
                    bv[nom] = {"bandes": [], "ecart_avant_db": e_av, "ecart_apres_db": e_av, "refus": calc["refus"]}
                    continue
                ecrire_peq(d, f"{P}PEQOut{n}\\SV\\", calc["bandes"], 8, journal, ip)
                apres = s.mesure(f"{tag}_{nom}", {n}, s.niveaux[nom])
                e_av = _ecart_voie(ici_avant, x, etat["style"])
                e_ap = _ecart_voie(apres, x, etat["style"])
                if e_ap > e_av - 0.1:       # pas d'amélioration mesurée ici : on retire
                    ecrire_peq(d, f"{P}PEQOut{n}\\SV\\", [], 8, journal, ip)
                    bv[nom] = {"bandes": [], "rejetee": calc["bandes"], "ecart_avant_db": e_av, "ecart_essai_db": e_ap}
                else:
                    etat["reglages"]["peq_voies"][nom] = calc["bandes"]
                    bv[nom] = {"bandes": calc["bandes"], "ecart_avant_db": e_av, "ecart_apres_db": e_ap,
                               "residu_predit_db": calc["residu_rms_db"], "refus": calc["refus"]}
            bilan["eq_voies"] = bv
        sys_ici_avant = lire(f"{pos['nom']}_systeme")
        sys_ici = s.mesure(f"{tag}_systeme_apres_eq_voies", set(sorties), s.niv(*sorties.values()))
        if "eq-generale" in etat["etapes"]:
            delta = np.asarray(sys_ici["mag_db"]) - np.asarray(sys_ici_avant["mag_db"])   # effet de l'EQ des voies
            corr = [dict(lire(f"{p}_systeme"), mag_db=(np.asarray(lire(f"{p}_systeme")["mag_db"]) + delta).tolist())
                    for p in etat["positions_faites"]]
            hpf_bas = min(((x.get("hpf") or {}).get("hz") or 1e9 for x in plan["voies"]), default=1e9)
            hpf_bas = None if hpf_bas >= 1e9 else hpf_bas
            calc = calculer_eq_generale(corr, etat["style"], bosses, hpf_bas)
            e_av = _ecart_systeme(sys_ici, etat["style"])
            if calc["bandes"]:
                ecrire_peq(d, f"{P}PEQ1\\SV\\", calc["bandes"], 12, journal, ip)
                apres = s.mesure(f"{tag}_systeme_apres_eq_generale", set(sorties), s.niv(*sorties.values()))
                e_ap = _ecart_systeme(apres, etat["style"])
                if e_ap > e_av - 0.1:
                    ecrire_peq(d, f"{P}PEQ1\\SV\\", [], 12, journal, ip)
                    bilan["eq_generale"] = {"bandes": [], "rejetee": calc["bandes"], "ecart_avant_db": e_av, "ecart_essai_db": e_ap}
                else:
                    etat["reglages"]["peq_entree"] = calc["bandes"]
                    bilan["eq_generale"] = {"bandes": calc["bandes"], "ecart_avant_db": e_av, "ecart_apres_db": e_ap,
                                            "note": calc.get("reduite")}
            else:
                bilan["eq_generale"] = {"bandes": [], "ecart_avant_db": e_av}
        etat["bilan"]["egalisation"] = bilan
        etat["egalise"] = True
        etat["alertes"] += s.resultat["alertes"]
        sauver_etat(dossier, etat)
    return s.resultat


def _ecart_voie(m: dict, x: dict, style: str) -> float:
    f, mag = np.asarray(m["f_hz"]), np.asarray(m["mag_db"])
    lo, hi = _bande_interieure(x)
    k = (f >= lo) & (f <= hi)
    e = mag[k] - np.array([rp.cible_style(style, v) for v in f[k]])
    return round(float(np.sqrt(np.mean((e - e.mean()) ** 2))), 2)


# ---------------------------------------------------------------- 4. conclure
def conclure(dossier, ajouter_historique: bool = True, historique: Path | None = None) -> dict:
    dossier = Path(dossier)
    etat = charger_etat(dossier)
    if not etat["positions_faites"]:
        raise ValueError("aucune position mesurée")
    reg, b = etat["reglages"], etat["bilan"]
    p1 = next(p for p in etat["positions"] if p["nom"] == etat["positions_faites"][0])
    voies = {}
    for x in _plan(etat)["voies"]:
        n = x["nom"]
        v = {}
        if n in reg["delais"]:
            v["delai_mesure_ms"] = round(reg["delais"][n], 2)
        e0 = ((b.get("gains") or {}).get("ecart_initial_db") or {}).get(n)
        if e0 is not None:
            v["ecart_niveau_db"] = e0
        if n in reg["phase"]:
            v["phase_deg"] = reg["phase"][n]
        if reg["peq_voies"].get(n):
            v["peq"] = reg["peq_voies"][n]
        if v:
            voies[n] = v
    recal = {"date": dt.date.today().isoformat(), "source": f"optim_preset {dossier.name}",
             "point": {"distance_m": p1["distance_m"], "hauteur_m": p1["hauteur_m"]}, "voies": voies}
    banc._ecrire_atomique(dossier / "recalage-propose.yaml", yaml.safe_dump(recal, allow_unicode=True, sort_keys=False))
    if reg["peq_entree"]:
        banc._ecrire_atomique(dossier / "eq-generale.yaml", yaml.safe_dump(
            {"date": recal["date"], "source": recal["source"], "lieu": "à renseigner (fiche événement)",
             "bandes": reg["peq_entree"]}, allow_unicode=True, sort_keys=False))
    out = {"dossier": str(dossier), "systeme": etat["systeme"], "style": etat["style"], "date": recal["date"],
           "positions": etat["positions_faites"], "etapes": etat["etapes"], "egalise": etat["egalise"],
           "bilan": b, "reglages": reg, "alertes": etat["alertes"],
           "recalage_propose": str(dossier / "recalage-propose.yaml"),
           "eq_generale": str(dossier / "eq-generale.yaml") if reg["peq_entree"] else None}
    banc._ecrire_atomique(dossier / "rapport.md", rapport(out))
    if ajouter_historique:
        h = Path(historique or HISTORIQUE)
        doc = (yaml.safe_load(h.read_text(encoding="utf-8")) if h.exists() else None) or {"optimisations": []}
        if any(e.get("dossier") == out["dossier"] for e in doc["optimisations"]):
            raise ValueError(f"{out['dossier']} déjà dans l'historique")
        doc["optimisations"].append({k: out[k] for k in ("dossier", "date", "systeme", "style", "positions", "bilan")})
        banc._ecrire_atomique(h, yaml.safe_dump(json.loads(json.dumps(doc)), allow_unicode=True, sort_keys=False))
    return out


def rapport(out: dict) -> str:
    b = out["bilan"]
    L = [f"# Optimisation du preset à la mesure — {out['systeme']} · {out['style']} · {out['date']}", "",
         f"Positions : {', '.join(out['positions'])} · étapes : {', '.join(out['etapes'])}", ""]
    if b.get("systeme_initial") is not None:
        L.append(f"- Système, écart à la cible au départ (P1) : {b['systeme_initial']} dB RMS")
    if b.get("gains"):
        L.append(f"- Gains : écarts {b['gains']['ecart_initial_db']} → {b['gains']['ecart_final_db']} dB ; "
                 f"gains de voie {out['reglages']['gains']}")
    if b.get("delais"):
        L.append(f"- Délais : pertes de somme {b['delais']['perte_avant_db']} → {b['delais']['perte_apres_db']} dB ; "
                 f"{b['delais']['delais_ms']} ms")
    if b.get("phase"):
        L.append(f"- Phase : {b['phase']}")
    eg = b.get("egalisation") or {}
    for nom, x in (eg.get("eq_voies") or {}).items():
        L.append(f"- EQ {nom} : {x.get('bandes') or 'aucune'} (écart {x.get('ecart_avant_db')} → "
                 f"{x.get('ecart_apres_db', x.get('ecart_essai_db'))} dB){' REJETÉE' if x.get('rejetee') else ''}")
    if eg.get("eq_generale"):
        x = eg["eq_generale"]
        L.append(f"- EQ générale : {x.get('bandes') or 'aucune'} (écart {x.get('ecart_avant_db')} → "
                 f"{x.get('ecart_apres_db', x.get('ecart_essai_db'))} dB)")
    if out["alertes"]:
        L += ["", "Alertes :"] + [f"- {a}" for a in out["alertes"]]
    L += ["", "Suite (go de Louis) : `preset_auto.py --recaler <config> " + out["recalage_propose"] + "` puis "
          "`preset_auto.py <config> --style …" + (f" --eq-generale {out['eq_generale']}" if out["eq_generale"] else "")
          + "` et `appliquer-plan`. Écouter avant de stocker."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- CLI
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("preparer")
    a1.add_argument("config")
    a1.add_argument("--style")
    a1.add_argument("--positions", required=True)
    a1.add_argument("--etapes", default=",".join(ETAPES))
    a1.add_argument("--accepter-non-verifie", action="store_true")
    a1.add_argument("--eq-generale")
    for nom in ("mesurer", "egaliser"):
        x = sub.add_parser(nom)
        x.add_argument("dossier")
        if nom == "mesurer":
            x.add_argument("--position", required=True)
        x.add_argument("--ip", required=True)
        x.add_argument("--port", type=int, default=19272)
        x.add_argument("--rew", default="localhost:4735")
        x.add_argument("--niveau-max", type=float, default=-12.0)
        x.add_argument("--protection-validee", action="store_true")
        x.add_argument("--signal-autorise", action="store_true")
        x.add_argument("--simulation", action="store_true")
    a3 = sub.add_parser("conclure")
    a3.add_argument("dossier")
    a = ap.parse_args()
    try:
        if a.cmd == "preparer":
            d_ = preparer(a.config, a.style, banc.lire_positions(a.positions), [e for e in a.etapes.split(",") if e],
                          a.accepter_non_verifie, a.eq_generale)
            print(f"Optimisation préparée : {d_}\nSuite : mesurer {d_} --position "
                  f"{charger_etat(d_)['positions'][0]['nom']} … (go de Louis pour le signal)")
        elif a.cmd in ("mesurer", "egaliser"):
            import dbx_hiqnet
            with dbx_hiqnet.Dbx(a.ip, a.port, os.environ.get("DBX_MDP") or "administrator") as d:
                etat = charger_etat(a.dossier)
                if a.simulation:
                    moteur_pour = lambda pos, so: banc.MoteurSimule(d, etat["config"], so, pos)  # noqa: E731
                else:
                    moteur_pour = lambda pos, so: banc.MoteurREW(a.rew)  # noqa: E731
                if a.cmd == "mesurer":
                    r = mesurer_position(a.dossier, a.position, d, moteur_pour, dbx_hiqnet.journaliser, a.ip,
                                         a.protection_validee, a.signal_autorise, a.niveau_max)
                else:
                    r = egaliser(a.dossier, d, moteur_pour, dbx_hiqnet.journaliser, a.ip, a.protection_validee,
                                 a.signal_autorise, a.niveau_max)
            ok = banc.retablissement_complet(r)
            print(f"{a.cmd} : {r['mesures']} mesures · sorties mutées · rétablissement "
                  f"{'complet' if ok else 'INCOMPLET : ' + str(r['retablissement'])} · instantané {r['instantane']}")
            for x in r["alertes"]:
                print(f"  ! {x}")
            if not ok:
                sys.exit("RÉTABLISSEMENT INCOMPLET : sorties laissées mutées ; vérifier avant de démuter.")
        else:
            out = conclure(a.dossier)
            print(rapport(out))
    except (ValueError, Annule, dd.SortiesNonMutees, OSError) as e:
        sys.exit(f"REFUS / ARRÊT : {e}")


if __name__ == "__main__":
    main()
