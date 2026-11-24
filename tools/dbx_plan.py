#!/usr/bin/env python3
"""
dbx_plan.py — Écrit un plan de réglages neutre (tools/preset_auto.py) dans un dbx DriveRack VENU360.
Appelé par `dbx_hiqnet.py appliquer-plan` ; reprend le déroulé validé sur la machine réelle (CKI, 24/11/2026) :
  1. refus hors ligne : plan bloquant, processeur différent, seuil au-dessus du plafond recalculé (calc_systeme),
     plan incohérent avec la config ACTUELLE (régénéré et comparé : sorties, filtres, polarité, seuils), config hors
     data/configs/, gain de sortie > 0 dB, délai hors [0, 30] ms ; seuil arrondi vers le bas ;
  2. topologie : une sortie = un crossover indépendant (CrossoverIIR<n>, Band_1 seule), Limiter<n>, DelayOut<n> ;
     sinon refus AVANT toute écriture (charger un preset « 6 × Mono Fullrange », ex. case 29) ;
  3. mute des 6 sorties, relu (SortiesNonMutees sinon) ;
  4. écritures « protections d'abord » (limiteurs, puis filtres et polarité) : chaque valeur validée par la
     description de la machine, écrite, relue ; un échec de PROTECTION arrête tout (sorties laissées mutées) ;
  5. puis délais, gains, EQ de sortie à plat ; relecture finale de tout ce qui a été écrit ;
  6. appliquer() rend la main sorties TOUJOURS mutées ; le CLI démute (--demuter, état relu exigé) seulement si tout
     est conforme, puis stocke et vérifie ; tout échec ensuite re-mute les 6 sorties avant de sortir.
Limite connue : une protection que la machine ne décrit pas n'est contrôlée que par la relecture stricte.
Pas maximum par écriture (±3 dB, ±0,5 ms) : levé ici parce qu'on CONSTRUIT un preset sorties mutées — c'est journalisé.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import dbx_description as dd
from garde_fous import verifier_plafond

P = "\\\\Preset\\"
PROCESSEURS = ("VENU360",)          # PA2 : chemins différents, non cartographiés pour ce plan
RACINE = Path(__file__).resolve().parent.parent
GAIN_MAX_DB = 0.0                    # gains de sortie ≤ 0 (le voicing les normalise ainsi)
DELAI_MAX_MS = 30.0                  # au-delà : géométrie ou plan faux (une pile fait < 2 m)


def chemin_config(plan: dict) -> Path:
    """Config du plan, obligatoirement sous data/configs/ (un JSON édité ne peut pas pointer ailleurs)."""
    p = Path(plan["config"])
    p = (p if p.is_absolute() else RACINE / p).resolve()
    if (RACINE / "data" / "configs").resolve() not in p.parents:
        raise ValueError(f"config du plan hors de data/configs/ : {p}")
    return p


def verifier_coherence(plan: dict) -> list[str]:
    """Régénère le plan depuis la config ACTUELLE : sorties, filtres, polarité, détection identiques et seuils ≤ ceux
    recalculés ; sinon refus (config modifiée depuis, ou JSON édité). Renvoie des notes."""
    import preset_auto
    neuf = preset_auto.construire_plan(str(chemin_config(plan)), plan["style"], plan["distance_m"],
                                       plan["hauteur_ecoute_m"], bool(plan.get("accepte_non_verifie")))
    if neuf["bloquant"]:
        raise ValueError("la config actuelle donne un plan BLOQUANT : " + " / ".join(neuf["bloquants"]))
    a, b = {v["nom"]: v for v in plan["voies"]}, {v["nom"]: v for v in neuf["voies"]}
    if set(a) != set(b):
        raise ValueError(f"voies du plan {sorted(a)} ≠ voies de la config {sorted(b)} : régénérer le plan")
    for nom, v in a.items():
        w = b[nom]
        for cle in ("sortie", "hpf", "lpf", "polarite"):
            if v.get(cle) != w.get(cle):
                raise ValueError(f"{nom}.{cle} : plan {v.get(cle)} ≠ config {w.get(cle)} — régénérer le plan")
        if v["limiteur"].get("detection") != w["limiteur"].get("detection"):
            raise ValueError(f"{nom} : détection du limiteur différente de la config — régénérer le plan")
        for cle, tol in (("gain_db", 0.05), ("delai_ms", 0.011)):     # 0,011 ms ≈ 1 échantillon à 96 kHz
            a_, b_ = v.get(cle), w.get(cle)
            if (a_ is None) != (b_ is None) or (a_ is not None and abs(float(a_) - float(b_)) > tol):
                raise ValueError(f"{nom}.{cle} : plan {a_} ≠ recalculé {b_} — régénérer le plan")
        if float(v["limiteur"]["seuil"]) > float(w["limiteur"]["seuil"]):
            raise ValueError(f"{nom} : seuil du plan {v['limiteur']['seuil']} au-dessus du seuil recalculé "
                             f"{w['limiteur']['seuil']} — régénérer le plan")
    return [f"plan cohérent avec {chemin_config(plan).name}"
            + (f" (recalage du {neuf['recalage']['date']})" if neuf.get("recalage") else "")]


def _f(hz: float) -> str:
    return f"{hz:g}Hz"


def ecritures(plan: dict, depasser_plafond: bool = False) -> tuple[list[tuple[str, str, str, bool]], list[str]]:
    """Plan -> [(chemin, valeur, voie, protection)] dans l'ordre d'écriture, + notes de plafond. ValueError si refus."""
    if plan.get("version") != 1:
        raise ValueError(f"version de plan inconnue : {plan.get('version')}")
    if plan.get("bloquant"):
        raise ValueError("plan BLOQUANT : " + " / ".join(plan.get("bloquants") or []))
    if not any(p in str(plan.get("processeur")) for p in PROCESSEURS):
        raise ValueError(f"plan prévu pour {plan.get('processeur')} : appliquer-plan ne sait écrire que {PROCESSEURS}")
    sorties = [v["sortie"] for v in plan["voies"]]
    if len(set(sorties)) != len(sorties) or not all(isinstance(s, int) and 1 <= s <= 6 for s in sorties):
        raise ValueError(f"sorties invalides ou en double : {sorties}")
    prot, reste, notes = [], [], verifier_coherence(plan)
    for v in plan["voies"]:
        n, nom, li = v["sortie"], v["nom"], v["limiteur"]
        if li.get("unite") != "dBFS" or li.get("seuil") is None:
            raise ValueError(f"{nom} : seuil {li.get('seuil')} {li.get('unite')} — dBFS attendu pour le VENU360")
        notes.append(verifier_plafond(float(li["seuil"]), plan["config"], nom, "dBFS", depasser_plafond))
        L, X = f"{P}Limiter{n}\\SV\\", f"{P}CrossoverIIR{n}\\SV\\Band_1_"
        crete = li.get("detection") == "crete"
        seuil = math.floor(float(li["seuil"]) * 10 + 1e-9) / 10      # arrondi vers le BAS (plus protecteur)
        p = [(L + "Limiter", "On"), (L + "Threshold", f"{seuil:.1f}dB"), (L + "OverEasy", "Off"),
             (L + "Auto", "Off"), (L + "Attack", f"{li['attaque_ms']:g}ms"), (L + "Hold", "0ms"),
             (L + "Release", f"{li['relachement_db_s']:g}dB/s"), (L + "PeakStopPlus", "On" if crete else "Off")]
        if crete and li.get("overshoot_db") is not None:
            p.append((L + "OvershootDb", f"{float(li['overshoot_db']):.1f}dB"))
        for cote, f in (("HP", v.get("hpf")), ("LP", v.get("lpf"))):
            if f:
                p += [(X + f"{cote}Type", f"{f['famille']} {f['pente_db_oct']}"), (X + f"{cote}Frequency", _f(f["hz"]))]
            else:
                p.append((X + f"{cote}Frequency", "Out"))
        p.append((X + "Polarity", "Inverted" if str(v.get("polarite", "")).lower().startswith("inv") else "Normal"))
        prot += [(c, val, nom) for c, val in p]
        for cle in ("gain_db", "delai_ms"):
            if v.get(cle) is not None and not math.isfinite(float(v[cle])):
                raise ValueError(f"{nom} : {cle} non fini ({v[cle]})")
        if v.get("gain_db") is not None and float(v["gain_db"]) > GAIN_MAX_DB:
            raise ValueError(f"{nom} : gain de voie {v['gain_db']} dB > {GAIN_MAX_DB:g} dB refusé")
        # Gain de voie dans le CROSSOVER (choix de Louis, 24/11/2026 : réglé et lu au même endroit que les filtres) ;
        # le gain de sortie (OutputGains) reste à 0 dB. Les deux sont avant le limiteur : protections inchangées.
        gain_voie = "0.0dB" if v.get("gain_db") is None else f"{float(v['gain_db']):.1f}dB"
        reste += [(X + "Gain", gain_voie, nom), (X + "Phase", "0 deg", nom),
                  (f"{P}OutputGains\\SV\\Channel_{n}_Gain", "0.0dB", nom),
                  (f"{P}PEQOut{n}\\SV\\ParametricEQ", "Off", nom)]
        if v.get("delai_ms") is not None and not 0 <= float(v["delai_ms"]) <= DELAI_MAX_MS:
            raise ValueError(f"{nom} : délai {v['delai_ms']} ms hors [0, {DELAI_MAX_MS:g}] ms refusé")
        if v.get("delai_ms") is not None:
            reste += [(f"{P}DelayOut{n}\\SV\\Amount", f"{v['delai_ms']:.2f}ms", nom),
                      (f"{P}DelayOut{n}\\SV\\Delay", "On" if v["delai_ms"] > 0 else "Off", nom)]
    out = [(c, val, nom, True) for c, val, nom in prot] + [(c, val, nom, False) for c, val, nom in reste]
    return out, notes


def verifier_topologie(d, plan: dict) -> list[str]:
    """Problèmes de topologie (liste vide = OK). Lecture seule."""
    pb = []
    for v in plan["voies"]:
        n = v["sortie"]
        try:
            xo = d.ls(f"{P}CrossoverIIR{n}\\SV")
            lim = d.ls(f"{P}Limiter{n}\\SV")
            dl = d.ls(f"{P}DelayOut{n}\\SV")
        except ValueError as e:
            pb.append(f"sortie {n} ({v['nom']}) : lecture impossible ({e})")
            continue
        if "Band_1_HPFrequency" not in xo:
            pb.append(f"sortie {n} ({v['nom']}) : pas de crossover indépendant CrossoverIIR{n}")
        elif any(re.match(r"Band_[2-9]_", k) for k in xo):
            pb.append(f"sortie {n} ({v['nom']}) : CrossoverIIR{n} a plusieurs bandes (crossover multivoie partagé)")
        if "Threshold" not in lim:
            pb.append(f"sortie {n} ({v['nom']}) : Limiter{n} absent")
        if "Amount" not in dl:
            pb.append(f"sortie {n} ({v['nom']}) : DelayOut{n} absent")
    return pb


def appliquer(d, plan: dict, journal, ip: str, depasser_plafond: bool = False) -> dict:
    """Écrit le plan. Renvoie le rapport ; lève ValueError (refus avant écriture) ou dd.SortiesNonMutees."""
    liste, notes = ecritures(plan, depasser_plafond)
    pb = verifier_topologie(d, plan)
    if pb:
        raise ValueError("topologie incompatible : " + " ; ".join(pb)
                         + " — charger un preset à sorties indépendantes (6 × Mono Fullrange, ex. case 29)")
    journal(f"DBX PLAN {ip} début : {plan['systeme']} style {plan['style']} ({len(liste)} écritures) — pas maximum "
            "levé : construction de preset, sorties mutées. " + " | ".join(notes))
    mutes = dd.muter_et_verifier(d)
    r = {"ecrits": 0, "echecs": [], "refus": [], "arret": None, "lignes": [], "sorties": None}
    for chemin, valeur, voie, protection in liste:
        desc = dd.decrire_param(d, chemin)
        if desc is not None:
            motif = dd.valider_machine(desc, valeur)
            if motif:
                r["refus"].append({"chemin": chemin, "valeur": valeur, "motif": motif})
                if protection:
                    r["arret"] = f"protection refusée par la machine : {chemin} = {valeur} ({motif})"
                    break
                continue
        avant = (desc or {}).get("valeur")
        if avant is None:
            try:
                avant = d.get(chemin)
            except ValueError as e:
                r["echecs"].append({"chemin": chemin, "valeur": valeur, "erreur": str(e)})
                if protection:
                    r["arret"] = f"protection illisible : {chemin} ({e})"
                    break
                continue
        relu, ok, err = dd.ecrire_et_relire(d, chemin, valeur, desc)
        ok = ok or dd.arrondi_machine(desc, valeur, relu, chemin)
        journal(f"DBX PLAN {ip} {chemin} : {avant} -> {valeur} (relu {relu}){'' if ok else ' ECART'}")
        r["lignes"].append({"chemin": chemin, "voie": voie, "avant": avant, "demande": valeur, "relu": relu, "ok": ok})
        if ok:
            r["ecrits"] += 1
        else:
            r["echecs"].append({"chemin": chemin, "valeur": valeur, "relu": relu, "erreur": err})
            if protection:
                r["arret"] = f"protection non conforme : {chemin} = {valeur}, relu {relu}"
                break
    # relecture finale de tout ce qui a été écrit (une autre écriture a pu en modifier une)
    derives = []
    if not r["arret"]:
        for l in r["lignes"]:
            if not l["ok"]:
                continue
            relu = d.get(l["chemin"])
            relu = relu.strip() if isinstance(relu, str) else relu
            if relu != l["relu"]:
                derives.append({"chemin": l["chemin"], "attendu": l["relu"], "relu": relu})
    r["derives"] = derives
    r["conforme"] = not (r["arret"] or r["echecs"] or r["refus"] or derives)
    r["sorties"] = mutes          # TOUJOURS mutées en sortie d'appliquer : le démute se décide après (demuter_utilisees)
    journal(f"DBX PLAN {ip} fin : {r['ecrits']}/{len(liste)} OK, {len(r['echecs'])} échec(s), {len(r['refus'])} refus, "
            f"{len(derives)} dérive(s){' ARRET : ' + r['arret'] if r['arret'] else ''} — sorties {r['sorties']}")
    return r


def demuter_utilisees(d, plan: dict) -> tuple[bool, dict]:
    """Démute les sorties du plan, laisse les autres mutées, et EXIGE la relecture de cet état exact."""
    utilisees = {v["sortie"] for v in plan["voies"]}
    attendu = {ch: ("Off" if int(re.search(r"Channel_(\d)_", ch).group(1)) in utilisees else "On") for ch in dd.SORTIES_MUTE}
    relu = dd.muter_sorties(d, attendu)
    return all(relu.get(ch) == x for ch, x in attendu.items()), relu


def verifier_apres_rappel(d, rapport: dict) -> list[dict]:
    """Après rappel du preset stocké : chaque valeur écrite doit être relue identique."""
    ecarts = []
    for l in rapport["lignes"]:
        if l["ok"]:
            relu = d.get(l["chemin"])
            relu = relu.strip() if isinstance(relu, str) else relu
            if relu != l["relu"]:
                ecarts.append({"chemin": l["chemin"], "ecrit": l["relu"], "apres_rappel": relu})
    return ecarts
