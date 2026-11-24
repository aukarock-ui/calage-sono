#!/usr/bin/env python3
"""
preset_auto.py — De la config au plan de réglages en une commande, pour n'importe quelle pile à composants.
LECTURE SEULE : n'écrit rien dans un processeur. Le plan produit est neutre (indépendant de l'appareil) ;
l'écriture se fait ensuite par l'outil du processeur (dbx : `dbx_hiqnet.py appliquer-plan`) ou à la main
avec la feuille de saisie (`--feuille`).

Chaîne :
  1. calc_systeme  : seuils de limiteur, contrôles électriques. Toute alerte bloquante (DANGER, excursion, réglage de
                     sortie non confirmé, seuil incalculable) marque le plan `bloquant` -> refusé à l'application.
  2. excursion_br  : HPF des voies bass-reflex vérifié sur toute la plage d'accord/volume (connue, ou hypothèses
                     `hypotheses_caisse` de la config) ; s'il ne tient pas Xmax, le plus bas HPF qui tient est PROPOSÉ
                     (jamais appliqué sans validation : le plan reste bloquant).
  3. reglages_pile : temps de limiteur (Proel), délais (géométrie + phase des filtres), gains (équilibre + profil
                     de programme), niveau atteignable vs profil.
  4. Plan JSON + Markdown dans plans/.

Boucle de recalage par la mesure :
  preset_auto.py --recaler <config> <mesures.yaml>
  écrit/complète <config>.recalage.yaml (lu automatiquement par tous les outils via calc_systeme.charger) :
    - delai_mesure_ms   -> centre acoustique effectif (geometrie_pile.profondeur_cm) ;
    - gain_ampli_mesure_db (gain effectif au voltmètre) -> gain_canal_db ;
    - accord_hz (impédance) -> accord_event_hz ;
    - ecart_niveau_db (niveau mesuré dans la bande − visé) -> correction_sensibilite_db.
  Chaque recalage est historisé (date, calculé, mesuré, écart) pour améliorer les modèles.

Usage :
  python tools/preset_auto.py data/configs/ma-sono-cki.yaml --style techno [--distance 20] [--feuille]
  python tools/preset_auto.py --recaler data/configs/ma-sono-cki.yaml mesures/cki-2026-11-30.yaml
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calc_systeme as cs          # noqa: E402
import reglages_pile as rp         # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
BLOQUANT = re.compile(r"^(DANGER|EXCURSION)|NON CONFIRMÉ|AUCUN seuil|absent de la bibliothèque|invalides", re.I)
VERSION_PLAN = 2          # v2 : phase_deg, peq par voie, peq_entree (EQ générale) — v1 reste lisible par dbx_plan
TYPES_PEQ = ("Bell", "Low Shelf", "High Shelf")
PEQ_GAIN_MAX_DB, PEQ_BOSSE_MAX_DB = 12.0, 6.0
PEQ_Q = (0.3, 10.0)
PEQ_BANDES_VOIE, PEQ_BANDES_ENTREE = 8, 12
PHASE_MIN_DEG = -135.0


def valider_peq(bandes, n_max: int, quoi: str) -> list[dict]:
    """Bandes d'EQ {type, hz, gain_db, q} -> liste normalisée ; ValueError si hors bornes (kit, pas machine)."""
    if bandes in (None, []):
        return []
    if not isinstance(bandes, list) or len(bandes) > n_max:
        raise ValueError(f"{quoi} : liste de {n_max} bandes au plus attendue")
    out = []
    for i, b in enumerate(bandes, 1):
        try:
            t, hz, g, q = b.get("type", "Bell"), float(b["hz"]), float(b["gain_db"]), float(b.get("q", 1.0))
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ValueError(f"{quoi} bande {i} : {{type, hz, gain_db, q}} attendus ({b!r})") from None
        if t not in TYPES_PEQ:
            raise ValueError(f"{quoi} bande {i} : type {t!r} (choix : {', '.join(TYPES_PEQ)})")
        if not (20 <= hz <= 20000 and abs(g) <= PEQ_GAIN_MAX_DB and g <= PEQ_BOSSE_MAX_DB
                and PEQ_Q[0] <= q <= PEQ_Q[1]) or not all(map(__import__("math").isfinite, (hz, g, q))):
            raise ValueError(f"{quoi} bande {i} : {hz:g} Hz {g:+g} dB Q {q:g} hors bornes (20 Hz-20 kHz, ±"
                             f"{PEQ_GAIN_MAX_DB:g} dB, bosse ≤ +{PEQ_BOSSE_MAX_DB:g} dB, Q {PEQ_Q[0]:g}-{PEQ_Q[1]:g})")
        out.append({"type": t, "hz": round(hz, 1), "gain_db": round(g, 1), "q": round(q, 2)})
    import numpy as np
    import eq_auto
    f = 20.0 * 2 ** (np.arange(0, 241) / 24)
    pic = float(np.max(20 * np.log10(np.abs(eq_auto.reponse(out, f))))) if out else 0.0
    if pic > PEQ_BOSSE_MAX_DB + 0.05:      # bandes cumulées : la réponse composée compte, pas chaque bande
        raise ValueError(f"{quoi} : réponse composée de {pic:+.1f} dB au plus haut (> +{PEQ_BOSSE_MAX_DB:g} dB)")
    return out


def valider_phase(x, quoi: str) -> float:
    x = float(x or 0.0)
    if not PHASE_MIN_DEG <= x <= 0.0:
        raise ValueError(f"{quoi} : phase {x:g}° hors [{PHASE_MIN_DEG:g}, 0]° (crossover VENU360 : −180…0 ; au-delà de "
                         "−135°, c'est une inversion de polarité : décision de Louis, par la polarité)")
    return round(x, 1)


def _bass_reflex(v: dict) -> bool:
    return bool(re.search(r"reflex|\bBR\b", str(v.get("charge") or ""), re.I))


def _plage(val, hyp) -> list[float] | None:
    if val:
        return [float(val)]
    if hyp and len(hyp) == 2:
        return [round(float(x), 2) for x in np.linspace(float(hyp[0]), float(hyp[1]), 5)]
    return None


def verifier_hpf(v: dict, cfg: dict, hp: dict, v_rms_hp: float) -> dict:
    """Excursion crête ≤ Xmax sur toute la plage d'accord/volume ; sinon HPF minimal proposé (même famille/pente)."""
    from excursion_br import analyse
    hyp = (cfg.get("hypotheses_caisse") or {}).get(v["nom"]) or {}
    fbs = _plage(v.get("accord_event_hz"), hyp.get("accord_hz"))
    vbs = _plage(v.get("volume_caisse_l"), hyp.get("volume_l"))
    hpf_txt = (cfg.get("crossover", {}).get(v["nom"]) or {}).get("hpf")
    if not fbs or not vbs:
        return {"statut": "non_verifie", "motif": "accord et/ou volume inconnus, sans `hypotheses_caisse` : mesurer "
                "l'impédance (accord) et le volume, ou donner une plage d'hypothèses"}

    def pire(txt):
        rs = [analyse(hp, vb, fb, txt, v_rms_hp) for fb in fbs for vb in vbs]
        return max(rs, key=lambda r: r["x_max_mm"])

    r = pire(hpf_txt)
    base = {"plage_accord_hz": [min(fbs), max(fbs)], "plage_volume_l": [min(vbs), max(vbs)],
            "hypothese": not (v.get("accord_event_hz") and v.get("volume_caisse_l")),
            "x_max_mm": r["x_max_mm"], "f_x_max_hz": r["f_x_max_hz"], "xmax_hp_mm": r["xmax_hp_mm"]}
    if r["x_max_mm"] <= r["xmax_hp_mm"]:
        return {"statut": "ok", **base}
    flt = rp.lire_filtre(hpf_txt) if hpf_txt else ("LR", 24, 20.0)
    fam, pente, fc = flt
    for f in range(int(math.ceil(fc)) + 1, int(2 * max(fbs)) + 1):
        txt = f"{fam}{pente} {f} Hz"
        if pire(txt)["x_max_mm"] <= r["xmax_hp_mm"]:
            return {"statut": "a_relever", "hpf_propose": txt, **base}
    return {"statut": "niveau", "motif": "aucun HPF ne suffit : excursion au-dessus de l'accord, c'est le niveau "
            "qu'il faut limiter", **base}


AUCUN = {"aucun", "out", "none"}       # filtre absent DÉCLARÉ (sinon : absence = oubli possible -> bloquant)


def _filtre(txt) -> dict | None:
    if str(txt or "").strip().lower() in AUCUN:
        return None
    f = rp.lire_filtre(txt)
    return {"famille": f[0], "pente_db_oct": f[1], "hz": f[2]} if f else None


def construire_plan(chemin_config: str, style: str | None = None, distance_m: float = 20.0,
                    hauteur_ecoute_m: float = 1.7, accepter_non_verifie: bool = False,
                    eq_generale: str | None = None) -> dict:
    """eq_generale : fichier YAML {bandes: [...]} de l'EQ générale (liée à la salle, mesurée par optim_preset)."""
    mat, cfg = cs.charger(chemin_config)
    peq_entree = []
    if eq_generale:
        doc = yaml.safe_load(Path(eq_generale).read_text(encoding="utf-8")) or {}
        peq_entree = valider_peq(doc.get("bandes"), PEQ_BANDES_ENTREE, f"EQ générale {Path(eq_generale).name}")
    style = style or cfg.get("style_defaut") or "plat"
    if style not in rp.STYLES:
        raise ValueError(f"style inconnu « {style} » : {', '.join(rp.STYLES)}")
    if cfg.get("type_systeme") not in (None, "composants"):
        raise ValueError("preset_auto ne traite que les piles à composants (système constructeur : preset usine, "
                         "seuls gains/délais/EQ système se règlent — méthode §constructeur)")
    alertes, bloquants, hypotheses = [], [], []
    xo = cfg.get("crossover") or {}
    vo = rp.voicing(cfg, mat, style)
    try:
        dl = rp.delais(cfg, distance_m, hauteur_ecoute_m)
    except ValueError as e:
        dl = None
        alertes.append(f"Délais non calculés : {e}")
    if "geometrie_pile" in cfg:
        hypotheses.append("Centres acoustiques de `geometrie_pile` : hypothèses tant que l'IR ne les a pas confirmés.")
    voies = []
    derniere = cfg["voies"][-1]["nom"] if cfg.get("voies") else None
    for v in cfg["voies"]:
        nom = v["nom"]
        r = cs.calc_voie(v, mat, cfg)
        hp = (mat.get("haut_parleurs") or {}).get(v.get("hp")) or {}
        for al in r.get("alertes", []):
            # l'excursion « non calculable » est traitée ci-dessous sur la plage d'hypothèses
            if al.startswith("Excursion non calculable") and _bass_reflex(v):
                continue
            (bloquants if BLOQUANT.search(al) else alertes).append(f"{nom} : {al}")
        if r.get("seuil_limiteur") is None:
            bloquants.append(f"{nom} : seuil de limiteur non calculable")
        f = xo.get(nom) or {}
        hpf = _filtre(f.get("hpf"))
        # un filtre absent s'écrit « Out » dans le processeur : il doit être DÉCLARÉ (« aucun »), sauf le passe-bas de
        # la voie la plus aiguë (en haut de bande)
        if not f.get("hpf"):
            bloquants.append(f"{nom} : crossover.{nom}.hpf absent — renseigner le filtre, ou « aucun » si voulu")
        if not f.get("lpf") and nom != derniere:
            bloquants.append(f"{nom} : crossover.{nom}.lpf absent — renseigner le filtre, ou « aucun » si voulu")
        ctrl_hpf = None
        if _bass_reflex(v) and r.get("v_cible_bornes_v") and hp:
            ctrl_hpf = verifier_hpf(v, cfg, hp, r["v_cible_bornes_v"] / v.get("hp_en_serie", 1))
            if ctrl_hpf["statut"] == "a_relever":
                bloquants.append(f"{nom} : HPF {f.get('hpf')} ne tient pas Xmax ({ctrl_hpf['x_max_mm']} > "
                                 f"{ctrl_hpf['xmax_hp_mm']} mm) — proposé : {ctrl_hpf['hpf_propose']} (à valider, "
                                 "puis corriger `crossover`)")
            elif ctrl_hpf["statut"] == "niveau":
                bloquants.append(f"{nom} : {ctrl_hpf['motif']}")
            elif ctrl_hpf["statut"] == "non_verifie":
                msg = f"{nom} : HPF non vérifié en excursion ({ctrl_hpf['motif']})"
                if accepter_non_verifie:
                    hypotheses.append(msg + " — ACCEPTÉ par --accepter-non-verifie")
                else:
                    bloquants.append(msg + " — ou --accepter-non-verifie sur décision de Louis")
            elif ctrl_hpf.get("hypothese"):
                hypotheses.append(f"{nom} : HPF vérifié sur des HYPOTHÈSES de caisse (accord "
                                  f"{ctrl_hpf['plage_accord_hz']} Hz, volume {ctrl_hpf['plage_volume_l']} L).")
        t = rp.temps_limiteur(hpf["hz"] if hpf else None)
        crete = "compression" in str(hp.get("type", "")).lower()
        g = (vo["voies"].get(nom) or {})
        voies.append({
            "nom": nom, "sortie": v.get("sortie"),
            "hpf": hpf, "lpf": _filtre(f.get("lpf")), "polarite": v.get("polarite", "normale"),
            "gain_db": g.get("gain_style_db"),
            "delai_ms": round(dl["voies"][nom]["delai_ms"], 2) if dl else None,
            "phase_deg": valider_phase(v.get("phase_deg"), f"{nom}.phase_deg"),
            "peq": valider_peq(v.get("peq"), PEQ_BANDES_VOIE, f"{nom}.peq"),
            "limiteur": {"seuil": r.get("seuil_limiteur"), "unite": r.get("unite_seuil"),
                         "attaque_ms": t["attaque_appareil_ms"], "relachement_db_s": t["relachement_appareil_db_s"],
                         "detection": "crete" if crete else "rms",
                         "overshoot_db": cfg.get("processeur_overshoot_db") if crete else None},
            "controle_hpf": ctrl_hpf,
            "sources": {"seuil": "calc_systeme (75 % puissance, gain ampli déclaré)",
                        "temps": "Proel DSO26 p. 34 ; dB/s = 8,686/τ (hypothèse)",
                        "detection": "crête : moteur à compression (McCarthy p. 28) ; RMS : grave (QSC derated)",
                        "delai": "reglages_pile.delais (géométrie + phase LR)" if dl else None,
                        "gain": f"équilibre fiche + profil « {style} »"},
        })
        if g.get("erreur"):
            alertes.append(f"{nom} : gain non calculé ({g['erreur']})")
    if any(x["gain_db"] is None for x in voies):
        alertes.append("Gains incomplets : les voies sans gain calculé restent à 0 dB (à régler à la mesure).")
    utilisees = {x["sortie"] for x in voies}
    proc = (mat.get("processeurs") or {}).get(cfg.get("processeur")) or {}
    n_sorties = proc.get("nb_sorties") or (6 if "VENU360" in str(cfg.get("processeur")) else max(utilisees))
    niveau = rp.niveau_atteignable(vo, distance_m)
    if niveau and niveau.get("marge_db") is not None and niveau["marge_db"] < 0:
        alertes.append(f"PUISSANCE : à {distance_m:g} m, plateau atteignable ≈ {niveau['plateau_db']} dB contre "
                       f"{niveau['cible_dba']} dB(A) visés par le profil « {vo['profil']} » ({niveau['marge_db']} dB) — "
                       f"voie limitante {vo['voie_limitante']}. Une seule pile en champ libre : la salle et le "
                       "couplage ajoutent quelques dB ; sinon ajouter des caisses sur cette voie ou viser un profil plus léger.")
    return {
        "version": VERSION_PLAN, "systeme": cfg.get("nom"), "config": str(Path(chemin_config).as_posix()),
        "date": dt.datetime.now().isoformat(timespec="seconds"), "processeur": cfg.get("processeur"),
        "style": style, "profil": vo.get("profil"), "accepte_non_verifie": accepter_non_verifie, "distance_m": distance_m, "hauteur_ecoute_m": hauteur_ecoute_m,
        "recalage": cfg.get("_recalage"),
        "eq_generale": str(Path(eq_generale).as_posix()) if eq_generale else None, "peq_entree": peq_entree,
        "voies": voies, "sorties_inutilisees": sorted(set(range(1, n_sorties + 1)) - utilisees),
        "voie_limitante": vo.get("voie_limitante"), "niveau": niveau,
        "reserves_db": {k: x.get("reserve_db") for k, x in vo["voies"].items() if "reserve_db" in x},
        "alertes": alertes, "bloquants": bloquants, "bloquant": bool(bloquants), "hypotheses": hypotheses,
    }


def _hz(f):
    return "—" if not f else (f"{f['hz'] / 1000:g} kHz" if f["hz"] >= 1000 else f"{f['hz']:g} Hz")


def markdown(plan: dict) -> str:
    l = [f"# Plan de réglages — {plan['systeme']} · style « {plan['style']} »"
         + (f" (profil {plan['profil']})" if plan.get("profil") else ""),
         f"Généré le {plan['date']} depuis `{plan['config']}` · processeur {plan['processeur']} · "
         f"point d'écoute {plan['distance_m']:g} m / {plan['hauteur_ecoute_m']:g} m"
         + (f" · recalé par la mesure ({plan['recalage']['date']})" if plan.get("recalage") else ""), ""]
    if plan["bloquant"]:
        l += ["## ⛔ BLOQUANT — ne pas appliquer", *[f"- {b}" for b in plan["bloquants"]], ""]
    l += ["## Feuille de saisie (une ligne par sortie, protections d'abord)", "",
          "| Sortie | Voie | HPF | LPF | Polarité | Limiteur seuil | Attaque | Relâchement | Détection | Délai | Gain (crossover) |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for v in sorted(plan["voies"], key=lambda x: x["sortie"] or 0):
        h, lp, li = v["hpf"], v["lpf"], v["limiteur"]
        det = "crête" + (f" (overshoot {li['overshoot_db']} dB)" if li.get("overshoot_db") else "") \
            if li["detection"] == "crete" else "RMS"
        delai = "—" if v["delai_ms"] is None else f"{v['delai_ms']:.2f} ms"
        gain = "—" if v["gain_db"] is None else f"{v['gain_db']:+.1f} dB"
        l.append(f"| {v['sortie']} | {v['nom']} | {(h['famille'] + str(h['pente_db_oct']) + ' ') if h else ''}{_hz(h)} | "
                 f"{(lp['famille'] + str(lp['pente_db_oct']) + ' ') if lp else ''}{_hz(lp)} | {v['polarite']} | "
                 f"**{li['seuil']} {li['unite']}** | {li['attaque_ms']} ms | {li['relachement_db_s']} dB/s | {det} | "
                 f"{delai} | {gain} |")
    if plan["sorties_inutilisees"]:
        l.append(f"\nSorties inutilisées : {', '.join(map(str, plan['sorties_inutilisees']))} → **mutées**.")
    n = plan.get("niveau")
    if n:
        l += ["", f"## Puissance\nVoie limitante : **{plan['voie_limitante']}** · réserves des autres voies : "
              + ", ".join(f"{k} {x} dB" for k, x in plan["reserves_db"].items())
              + f"\nPlateau atteignable à {n['distance_m']:g} m ≈ **{n['plateau_db']} dB**"
              + (f" pour {n['cible_dba']} dB(A) visés par le profil ({n['marge_db']:+} dB)." if n.get("cible_dba") else ".")]
    hpf = [(v["nom"], v["controle_hpf"]) for v in plan["voies"] if v.get("controle_hpf")]
    if hpf:
        l += ["", "## Excursion (HPF des bass-reflex)"]
        for nom, c in hpf:
            l.append(f"- {nom} : {c['statut']}"
                     + (f" — pire cas {c['x_max_mm']} mm à {c['f_x_max_hz']} Hz (Xmax {c['xmax_hp_mm']} mm), accord "
                        f"{c['plage_accord_hz']} Hz, volume {c['plage_volume_l']} L" if "x_max_mm" in c else f" — {c.get('motif')}")
                     + (f" → HPF proposé {c['hpf_propose']}" if c.get("hpf_propose") else ""))
    for titre, cle in (("Alertes", "alertes"), ("Hypothèses (à confirmer par la mesure)", "hypotheses")):
        if plan[cle]:
            l += ["", f"## {titre}", *[f"- {a}" for a in plan[cle]]]
    l += ["", "Application : dbx VENU360/PA2 → `dbx_hiqnet.py appliquer-plan <ip> <plan.json> --case N --nom \"…\" "
          "--appliquer --protection-validee` ; autre processeur → saisir cette feuille, puis relire à l'écran."]
    return "\n".join(l)


# --- Recalage par la mesure -----------------------------------------------------------------------------------------
ECART_GAIN_MAX_DB = 3.0     # au-delà : erreur de mesure probable (calibre, faute de frappe, mauvais sélecteur)


def recaler(chemin_config: str, chemin_mesures: str, distance_m: float | None = None,
            protection_validee: bool = False, forcer_gain: bool = False) -> dict:
    """Compare mesures et modèle, écrit les corrections dans <config>.recalage.yaml (historisées). Ne touche ni la
    config d'origine ni le processeur. Renvoie le résumé des écarts."""
    mes = yaml.safe_load(Path(chemin_mesures).read_text(encoding="utf-8")) or {}
    if mes.get("simulation"):
        raise ValueError(f"{chemin_mesures} vient d'une SIMULATION : jamais utilisé pour recaler les calculs")
    mat, cfg = cs.charger(chemin_config)
    voies = {v["nom"]: v for v in cfg["voies"]}
    pt = mes.get("point") or {}
    d_m, h_m = pt.get("distance_m", distance_m or 20.0), pt.get("hauteur_m", 1.7)
    fichier = cs.chemin_recalage(chemin_config)
    rec = yaml.safe_load(fichier.read_text(encoding="utf-8")) if fichier.exists() else {}
    rec = rec or {}
    rec.setdefault("voies", {}); rec.setdefault("geometrie_pile", {}); rec.setdefault("historique", [])
    ecarts = {}
    mv = mes.get("voies") or {}
    for nom in mv:
        if nom not in voies:
            raise ValueError(f"mesures : voie « {nom} » absente de la config ({', '.join(voies)})")
    # 1. Délais : écart relatif à la voie de référence (délai calculé 0) -> centre acoustique effectif
    avec_delai = {n: x["delai_mesure_ms"] for n, x in mv.items() if x.get("delai_mesure_ms") is not None}
    if avec_delai:
        calc = rp.delais(cfg, d_m, h_m)
        c = calc["c_m_s"]
        ref = min(calc["voies"], key=lambda n: calc["voies"][n]["delai_ms"])
        if ref not in avec_delai:
            raise ValueError(f"délais mesurés : il faut aussi celui de la voie de référence {ref} (délai calculé 0)")
        for nom, dm in avec_delai.items():
            e = (dm - avec_delai[ref]) - (calc["voies"][nom]["delai_ms"] - calc["voies"][ref]["delai_ms"])
            if nom == ref:
                continue
            # délai nécessaire plus grand que prévu = la voie arrive plus tôt = centre acoustique plus en avant
            prof = ((cfg.get("geometrie_pile") or {}).get(nom) or {}).get("profondeur_cm")
            if prof is None:
                raise ValueError(f"délai mesuré pour {nom} mais geometrie_pile.{nom}.profondeur_cm absent de la config")
            nouv = round(prof - e * c / 10, 1)                     # ms × m/s = mm -> /10 = cm
            rec["geometrie_pile"].setdefault(nom, {})["profondeur_cm"] = nouv
            ecarts.setdefault(nom, {})["delai"] = {"calcule_ms": round(calc["voies"][nom]["delai_ms"], 2),
                                                   "mesure_ms": dm, "ecart_ms": round(e, 2),
                                                   "profondeur_cm": [prof, nouv]}
    for nom, x in mv.items():
        v = voies[nom]
        r = cs.calc_voie(v, mat, cfg)
        if x.get("gain_ampli_mesure_db") is not None and r.get("gain_ampli_effectif_db") is not None:
            ajout = r["gain_ampli_effectif_db"] - float(v.get("gain_canal_db") or 0)   # bridge +6,02 etc.
            nouv = round(float(x["gain_ampli_mesure_db"]) - ajout, 2)
            ecart = round(float(x["gain_ampli_mesure_db"]) - r["gain_ampli_effectif_db"], 2)
            if abs(ecart) > ECART_GAIN_MAX_DB and not forcer_gain:
                raise ValueError(f"{nom} : gain mesuré {x['gain_ampli_mesure_db']} dB, calculé {r['gain_ampli_effectif_db']} dB "
                                 f"(écart {ecart:+} dB > {ECART_GAIN_MAX_DB:g}) : vérifier calibre, sélecteur, mode ; "
                                 "si confirmé : --forcer-gain")
            seuil_avant = r.get("seuil_limiteur")
            seuil_apres = cs.calc_voie({**v, "gain_canal_db": nouv}, mat, cfg).get("seuil_limiteur")
            if seuil_avant is not None and seuil_apres is not None and seuil_apres > seuil_avant and not protection_validee:
                raise ValueError(f"{nom} : le gain mesuré RELÈVE le plafond du limiteur de {seuil_avant} à {seuil_apres} "
                                 f"{r.get('unite_seuil')} : accord explicite de Louis requis (--protection-validee)")
            rec["voies"].setdefault(nom, {})["gain_canal_db"] = nouv
            ecarts.setdefault(nom, {})["gain_ampli"] = {"calcule_db": r["gain_ampli_effectif_db"],
                                                        "mesure_db": x["gain_ampli_mesure_db"], "ecart_db": ecart,
                                                        "plafond_limiteur": [seuil_avant, seuil_apres]}
        if x.get("accord_hz") is not None:
            rec["voies"].setdefault(nom, {})["accord_event_hz"] = x["accord_hz"]
            ecarts.setdefault(nom, {})["accord"] = {"avant": v.get("accord_event_hz"), "mesure_hz": x["accord_hz"]}
        if x.get("ecart_niveau_db") is not None:
            avant = float(v.get("correction_sensibilite_db") or 0)
            rec["voies"].setdefault(nom, {})["correction_sensibilite_db"] = round(avant + float(x["ecart_niveau_db"]), 1)
            ecarts.setdefault(nom, {})["niveau"] = {"ecart_db": x["ecart_niveau_db"],
                                                    "correction_cumulee_db": round(avant + float(x["ecart_niveau_db"]), 1)}
        if x.get("phase_deg") is not None:       # mesurés par optim_preset : bornés comme dans un plan
            rec["voies"].setdefault(nom, {})["phase_deg"] = valider_phase(x["phase_deg"], f"mesures {nom}.phase_deg")
            ecarts.setdefault(nom, {})["phase_deg"] = {"avant": v.get("phase_deg"), "mesure": x["phase_deg"]}
        if x.get("peq") is not None:
            rec["voies"].setdefault(nom, {})["peq"] = valider_peq(x["peq"], PEQ_BANDES_VOIE, f"mesures {nom}.peq")
            ecarts.setdefault(nom, {})["peq"] = {"n_bandes": len(x["peq"])}
    date = str(mes.get("date") or dt.date.today().isoformat())
    rec["date"] = date
    rec["historique"].append({"date": date, "fichier": Path(chemin_mesures).as_posix(), "point": {"distance_m": d_m,
                              "hauteur_m": h_m}, "ecarts": ecarts})
    entete = ("# Corrections mesurées de la config voisine — écrit par tools/preset_auto.py --recaler.\n"
              "# Lu automatiquement par calc_systeme.charger (donc par tous les outils). Historique = écarts calcul/mesure.\n")
    fichier.write_text(entete + yaml.safe_dump(rec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"fichier": str(fichier), "ecarts": ecarts}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("mesures", nargs="?", help="avec --recaler : fichier de mesures (format : skill mesure-acoustique)")
    ap.add_argument("--style", help="plat, leger, moyen, lourd, exigeant, ou " + ", ".join(rp.STYLE_PROFIL))
    ap.add_argument("--distance", type=float, default=20.0)
    ap.add_argument("--hauteur-ecoute", type=float, default=1.7)
    ap.add_argument("--recaler", action="store_true")
    ap.add_argument("--protection-validee", action="store_true",
                    help="--recaler : Louis accepte qu'un gain mesuré plus bas RELÈVE un plafond de limiteur")
    ap.add_argument("--forcer-gain", action="store_true", help="--recaler : écart de gain > 3 dB confirmé par Louis")
    ap.add_argument("--accepter-non-verifie", action="store_true",
                    help="HPF bass-reflex non vérifiable (accord/volume inconnus) accepté par Louis : noté dans le plan")
    ap.add_argument("--feuille", action="store_true", help="affiche la feuille de saisie Markdown")
    ap.add_argument("--eq-generale", help="fichier YAML de l'EQ générale mesurée (optim_preset), propre à la salle")
    ap.add_argument("-o", "--sortie", help="plan JSON (défaut plans/<systeme>-<style>-reglages.json)")
    a = ap.parse_args()
    if a.recaler:
        if not a.mesures:
            sys.exit("--recaler : <config> <mesures.yaml> attendus.")
        try:
            r = recaler(a.config, a.mesures, a.distance, a.protection_validee, a.forcer_gain)
        except ValueError as e:
            sys.exit(f"REFUS : {e}. Rien n'a été écrit.")
        print(f"Corrections écrites : {r['fichier']}")
        for nom, e in r["ecarts"].items():
            print(f"  {nom} : " + " · ".join(f"{k} {v}" for k, v in e.items()))
        print("Régénérer le plan : preset_auto.py <config> --style … (les corrections sont prises en compte).")
        return
    plan = construire_plan(a.config, a.style, a.distance, a.hauteur_ecoute, a.accepter_non_verifie, a.eq_generale)
    out = Path(a.sortie or RACINE / "plans" / f"{plan['systeme']}-{plan['style']}-reglages.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    md = markdown(plan)
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print(md if a.feuille else f"Plan : {out} (+ .md){'  ⛔ BLOQUANT' if plan['bloquant'] else ''}")
    if not a.feuille:
        for b in plan["bloquants"]:
            print("  ⛔", b)
        n = plan.get("niveau")
        if n:
            print(f"  Limitante {plan['voie_limitante']} · plateau {n['plateau_db']} dB à {n['distance_m']:g} m"
                  + (f" ({n['marge_db']:+} dB vs profil)" if n.get("marge_db") is not None else ""))
    sys.exit(2 if plan["bloquant"] else 0)


if __name__ == "__main__":
    main()
