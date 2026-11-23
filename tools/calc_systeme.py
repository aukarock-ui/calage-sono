#!/usr/bin/env python3
"""
calc_systeme.py — Calculs déterministes de structure de gain et de limiteurs.

Claude ne fait JAMAIS ces calculs « de tête » : il lance ce script et cite sa sortie.

Usage :
  python tools/calc_systeme.py data/configs/ma-sono-actuel.yaml            # tableau Markdown
  python tools/calc_systeme.py data/configs/ma-sono-actuel.yaml --json     # sortie machine
  python tools/calc_systeme.py --delai 12.4 --temp 28                      # distance (m) -> délai (ms)
  python tools/calc_systeme.py --renfort 45 10 --temp 25                   # délai d'un renfort (au point de raccord)
  python tools/calc_systeme.py --puissance 95 20 97 --cable 20 1.5 6       # puissance ampli requise
  python tools/calc_systeme.py --dc 2500 1.0 10                            # distance critique (V, RT60, Q)

Formules (méthode §2, §5, §8) :
  V = sqrt(P x R)                      tension aux bornes d'un HP
  dBu = 20 log10(V / 0.775)
  Gain bridge = gain canal + 20 log10(2) (= +6,02 dB)   -> À CONFIRMER au voltmètre
  dBu sortie processeur = dBu bornes - gain ampli
  Seuil Venu360 (dBFS) = dBu sortie processeur - dBu(0 dBFS) du réglage de sortie - overshoot (coussin dbx)
  Calcul THÉORIQUE (impédance nominale, puissance de référence) : ni impédance complexe, ni affaissement
  d'alimentation, ni compression thermique, ni excursion. À confirmer par mesure (tension, gain, excursion).
  c (m/s) = 331,3 + 0,606 x T(°C) ; délai (ms) = d / c x 1000
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML manquant : pip install pyyaml")

RACINE = Path(__file__).resolve().parent.parent
BRIDGE_DB = 20 * math.log10(2)


def dbu(v: float) -> float:
    return 20 * math.log10(v / 0.775)


def vitesse_son(temp_c: float) -> float:
    return 331.3 + 0.606 * temp_c


def delai_ms(distance_m: float, temp_c: float = 20.0) -> float:
    return distance_m / vitesse_son(temp_c) * 1000


# --- Dimensionnement (Boyce 2014, §11.6.6, §11.6.7, §11.9 ; contrôlé sur ses exemples chiffrés) ---
RHO_CUIVRE = 0.0172  # ohm.mm²/m à 20 °C (16 AWG = 1,31 mm² -> 0,0131 ohm/m, Boyce : 0,0132)


def perte_cable_db(longueur_m: float, section_mm2: float, re_ohm: float) -> float:
    """Perte (dB) dans un câble HP aller-retour, diviseur de tension câble / résistance de bobine Re.
    Elle réduit la tension aux bornes : un seuil calculé aux bornes reste du côté sûr. Ne jamais
    relever un limiteur au nom du câble sans mesurer la tension aux bornes."""
    if min(longueur_m, section_mm2, re_ohm) <= 0:
        raise ValueError("longueur, section et Re doivent être > 0")
    r_cable = 2 * RHO_CUIVRE * longueur_m / section_mm2
    return 20 * math.log10((re_ohm + r_cable) / re_ohm)


def puissance_requise(spl_cible_db: float, distance_m: float, sensibilite_db_1w1m: float,
                      marge_db: float = 6.0, perte_cable: float = 0.0) -> dict:
    """Puissance ampli continue nécessaire (champ libre, une enceinte, sur l'axe ; ni gain de salle ni
    absorption de l'air ni du public). Marge : 3 dB au minimum, 6 dB adapté à la plupart des cas (Boyce)."""
    if distance_m <= 0:
        raise ValueError("distance doit être > 0")
    chute = 20 * math.log10(distance_m / 1.0)
    excedent = spl_cible_db + chute + marge_db - sensibilite_db_1w1m + perte_cable
    return {"chute_distance_db": round(chute, 2), "spl_requis_1m_db": round(spl_cible_db + chute + marge_db, 2),
            "excedent_sur_1w_db": round(excedent, 2), "puissance_w": round(10 ** (excedent / 10), 0)}


def delai_renfort_ms(d_principal_m: float, d_renfort_m: float, temp_c: float = 20.0,
                     precedence_ms: float = 0.0) -> float:
    """Délai d'un renfort = écart des trajets façade / renfort au point de raccord (XOVR : point d'égal niveau des
    deux systèmes), plus une marge de précédence éventuelle.
    Par défaut 0 ms : raccord aligné en phase (McCarthy 2007, ch. 10 : ajouter 10 ms crée un filtrage en peigne
    jusqu'à 50 Hz et baisse l'intelligibilité ; il ne propose la marge qu'à la demande, après écoute).
    Boyce (§11.6.7) conseille environ 10 ms pour garder la localisation sur la façade : à passer explicitement.
    Point de départ dans tous les cas : le réglage final se fait à l'IR au point de raccord."""
    if d_principal_m < d_renfort_m:
        raise ValueError("l'auditeur doit être plus loin de la façade que du renfort")
    if precedence_ms < 0:
        raise ValueError("marge de précédence négative : le renfort arriverait avant la façade")
    return delai_ms(d_principal_m - d_renfort_m, temp_c) + precedence_ms


def distance_critique_m(volume_m3: float, rt60_s: float, q: float) -> float:
    """Distance critique sur l'axe : Dc = 0,057 x sqrt(Q.V / RT60) (unités SI). Au-delà de ~2 Dc, la
    parole se dégrade nettement ; vers 3 Dc (direct/réverbéré ~ -10 dB) elle devient inacceptable."""
    if min(volume_m3, rt60_s, q) <= 0:
        raise ValueError("volume, RT60 et Q doivent être > 0")
    return 0.057 * math.sqrt(q * volume_m3 / rt60_s)


def charger(chemin_config: str):
    mat = yaml.safe_load((RACINE / "data" / "materiel.yaml").read_text(encoding="utf-8"))
    cfg = yaml.safe_load(Path(chemin_config).read_text(encoding="utf-8"))
    return mat, cfg


def puissance_reference(hp: dict) -> tuple[float | None, str]:
    """Puissance de référence pour le limiteur RMS : AES si connue, sinon RMS brochure (signalé)."""
    if hp.get("puissance_aes_w"):
        return hp["puissance_aes_w"], "AES"
    if hp.get("puissance_rms_w"):
        return hp["puissance_rms_w"], "RMS brochure (norme non AES — à confirmer)"
    return None, "inconnue"


def filtres_voie(cfg: dict, nom: str) -> tuple[dict, str | None]:
    """Filtres d'une voie : section `crossover` (en service) sinon `dsp_depart` (proposition avant mesure)."""
    for section in ("crossover", "dsp_depart"):
        f = (cfg.get(section) or {}).get(nom)
        if f:
            return f, section
    return {}, None


def _hz(texte) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(k?)Hz", str(texte or ""), re.I)
    return float(m.group(1).replace(",", ".")) * (1000 if m.group(2) else 1) if m else None


def controle_hpf_bass_reflex(v: dict, cfg: dict) -> list[str]:
    """Bass-reflex : sous l'accord, plus de charge acoustique → excursion libre. HPF obligatoire (méthode §1).
    Contrôle de cohérence seulement : ne certifie pas Xmax (il faudrait un modèle de caisse et des T/S validés)."""
    charge = str(v.get("charge") or "")
    if not re.search(r"reflex|\bBR\b", charge, re.I):
        return []
    f, section = filtres_voie(cfg, v["nom"])
    hpf = _hz(f.get("hpf")) if f.get("hpf") else None
    fb = v.get("accord_event_hz") or _hz(re.search(r"accord[^,;)]*", charge, re.I).group(0)
                                         if re.search(r"accord", charge, re.I) else None)
    if not f.get("hpf"):
        return [f"DANGER : voie bass-reflex sans HPF renseigné ({section or 'crossover'}.{v['nom']}.hpf) — HPF obligatoire "
                "sous l'accord de l'évent ; le relever ou le régler AVANT toute mise en puissance."]
    if hpf is None:
        return [f"HPF bass-reflex « {f.get('hpf')} » illisible (fréquence) : le relever avec sa pente."]
    a = []
    if fb is None:
        a.append(f"Accord d'évent inconnu : HPF {hpf:g} Hz non comparable à l'accord (mesure d'impédance à faire).")
    elif hpf < fb:
        a.append(f"HPF {hpf:g} Hz sous l'accord {fb:g} Hz ({f.get('hpf')}) : énergie résiduelle sous l'accord — "
                 "valider l'excursion par modèle de caisse ou mesure avant pleine puissance.")
    if section == "dsp_depart":
        a.append("Filtres lus dans dsp_depart (proposition avant mesure), pas dans le crossover en service.")
    return a


def controle_excursion(v: dict, cfg: dict, hp: dict, v_rms_hp: float) -> tuple[list[str], dict | None]:
    """Bass-reflex : excursion crête calculée (modèle T/S, tools/excursion_br.py) à la tension du limiteur, avec le HPF
    de la voie, comparée à Xmax ; contrôle aussi la pente du HPF (≥ 14 dB à 0,7·fb, Marshall 2016)."""
    charge = str(v.get("charge") or "")
    if not re.search(r"reflex|\bBR\b", charge, re.I):
        return [], None
    fb = v.get("accord_event_hz") or _hz(re.search(r"accord[^,;)]*", charge, re.I).group(0)
                                         if re.search(r"accord", charge, re.I) else None)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:L|litres?)\b", charge)
    vb = v.get("volume_caisse_l") or (float(m.group(1).replace(",", ".")) if m else None)
    f, _ = filtres_voie(cfg, v["nom"])
    if fb is None or vb is None:
        return [f"Excursion non calculable : {'accord' if fb is None else 'volume de caisse'} inconnu "
                "(renseigner accord_event_hz / volume_caisse_l)."], None
    from excursion_br import analyse            # même dossier tools/
    try:
        r = analyse(hp, vb, fb, f.get("hpf"), v_rms_hp)
    except ValueError as e:
        return [f"Excursion non calculable : {e}."], None
    return r["alertes"], r


def reglage_sortie_confirme(cfg: dict) -> bool:
    return cfg.get("processeur_reglage_sortie_confirme") is True


def plafond_limiteur(chemin_config: str, nom_voie: str) -> dict:
    """Seuil de limiteur calculé pour une voie (le plus haut autorisé) et son unité. Lève ValueError si non calculable."""
    mat, cfg = charger(chemin_config)
    voies = {v["nom"]: v for v in cfg["voies"]}
    if nom_voie not in voies:
        raise ValueError(f"voie « {nom_voie} » absente de {chemin_config} : {', '.join(voies)}")
    r = calc_voie(voies[nom_voie], mat, cfg)
    if r.get("seuil_limiteur") is None:
        raise ValueError(f"seuil non calculable pour {nom_voie} : " + " / ".join(r.get("alertes", [])))
    proc = (mat.get("processeurs") or {}).get(cfg.get("processeur")) or {}
    return {"seuil": r["seuil_limiteur"], "unite": r.get("unite_seuil"), "processeur": cfg.get("processeur"),
            "alertes": r.get("alertes", []),
            "reglage_a_confirmer": bool(proc.get("sortie_0dbfs_dbu")) and not reglage_sortie_confirme(cfg)}


def calc_voie(v: dict, mat: dict, cfg: dict) -> dict:
    alertes: list[str] = []
    tv = v.get("type") or cfg.get("type_systeme") or "composants"
    if tv in ("constructeur", "amplifiee"):
        return {"voie": v["nom"], "sortie": v.get("sortie"), "hp": v.get("enceinte") or "—", "nb_hp": "—",
                "alertes": [f"Type « {tv} » : crossover/limiteurs internes = preset constructeur "
                            f"({v.get('preset_constructeur') or 'preset à préciser'}, contrôleur {v.get('controleur') or '?'}) — non recalculés."]}
    if not v.get("hp") or v["hp"] not in mat.get("haut_parleurs", {}):
        return {"voie": v["nom"], "sortie": v.get("sortie"), "hp": v.get("hp") or "?", "nb_hp": "?",
                "alertes": [f"HP « {v.get('hp')} » absent de la bibliothèque : l'ajouter depuis sa fiche (skill nouveau-systeme)."]}
    hp = mat["haut_parleurs"][v["hp"]]
    alertes += controle_hpf_bass_reflex(v, cfg)
    z_hp = hp["impedance_nominale_ohm"]
    n_s, n_p = v.get("hp_en_serie"), v.get("branches_paralleles")
    if not (isinstance(n_s, int) and isinstance(n_p, int) and n_s >= 1 and n_p >= 1):
        return {"voie": v["nom"], "sortie": v.get("sortie"), "hp": v["hp"], "nb_hp": "?",
                "alertes": [f"hp_en_serie / branches_paralleles invalides ({n_s} / {n_p}) : entiers ≥ 1 attendus."]}
    z_charge = z_hp * n_s / n_p
    n_hp = n_s * n_p

    p_ref, norme = puissance_reference(hp)
    pct = cfg.get("pourcentage_puissance_limite", 0.75)
    res: dict = {
        "voie": v["nom"], "sortie": v.get("sortie"), "hp": v["hp"], "nb_hp": n_hp,
        "z_vue_ampli_ohm": round(z_charge, 2), "norme_puissance": norme,
    }
    if "norme non AES" in norme:
        alertes.append(f"{v['hp']} : puissance {p_ref} W non AES — limiteur calculé dessus, à confirmer.")
    if p_ref is None:
        alertes.append(f"{v['hp']} : puissance inconnue — AUCUN seuil calculable.")
        res["alertes"] = alertes
        return res

    p_lim_hp = pct * p_ref
    v_hp = math.sqrt(p_lim_hp * z_hp)
    v_bornes = v_hp * n_s
    res.update({
        "p_limite_par_hp_w": round(p_lim_hp, 1),
        "v_cible_bornes_v": round(v_bornes, 1),
        "dbu_cible_bornes": round(dbu(v_bornes), 1),
        "p_totale_voie_w": round(p_lim_hp * n_hp, 0),
    })
    # Excursion à la tension cible par HP (majorant : le limiteur peut être plus bas si l'ampli écrête avant)
    al_x, r_x = controle_excursion(v, cfg, hp, v_hp)
    alertes += al_x
    if r_x:
        res["excursion_max_mm"] = r_x["x_max_mm"]
        res["excursion_f_hz"] = r_x["f_x_max_hz"]
        res["hpf_attenuation_07fb_db"] = r_x["attenuation_07fb_db"]
    # SPL max théorique (1 m, champ libre). Somme cohérente 20log(n) valable seulement
    # si les HP sont rapprochés devant la longueur d'onde (graves) ; sinon 10log(n).
    sens = hp.get("sensibilite_db_1w1m")
    if sens is not None:
        res["spl_max_1m_coherent_db"] = round(sens + 10 * math.log10(p_lim_hp) + 20 * math.log10(n_hp), 1)
        res["spl_max_1m_incoherent_db"] = round(sens + 10 * math.log10(p_lim_hp) + 10 * math.log10(n_hp), 1)
        if v["hp"] == "BEYMA_CP650TI":
            alertes.append("Sensibilité CP650Ti donnée sur TD-460, pas sur HP640 : SPL indicatif.")

    amp_nom = v.get("ampli")
    if not amp_nom:
        alertes.append("Ampli non défini : seule la tension cible aux bornes est donnée.")
        res["alertes"] = alertes
        return res

    if amp_nom not in mat.get("amplis", {}):
        alertes.append(f"Ampli « {amp_nom} » absent de la bibliothèque : l'ajouter depuis sa fiche.")
        res["alertes"] = alertes
        return res
    amp = mat["amplis"][amp_nom]
    mode = v.get("mode", "stereo")
    gain = v.get("gain_canal_db") if v.get("gain_canal_db") is not None else amp.get("gain_db")
    if gain is None:
        alertes.append(f"{amp_nom} : gain inconnu.")
        res["alertes"] = alertes
        return res
    gain_eff = gain + (BRIDGE_DB if mode == "bridge" else 0)
    v_lim = v_bornes   # tension visée par le limiteur (abaissée à l'écrêtage ampli s'il est plus bas)
    if mode == "bridge":
        alertes.append(f"{amp_nom} en bridge : gain pris à {gain_eff:.1f} dB (canal +6,02) — CONFIRMER au voltmètre.")

    table = amp.get("bridge_w" if mode == "bridge" else "stereo_w", {}) or {}
    z_key = int(z_charge) if float(z_charge).is_integer() else None
    p_amp = table.get(z_key) if z_key is not None else None
    if p_amp:
        v_clip = math.sqrt(p_amp * z_charge)
        res["v_ampli_max_v"] = round(v_clip, 1)
        res["marge_ampli_db"] = round(20 * math.log10(v_clip / v_bornes), 1)
        if v_clip < v_bornes:
            alertes.append(f"L'ampli écrête AVANT la tension cible ({v_clip:.1f} V < {v_bornes:.1f} V) : seuil calculé "
                           "sur la tension d'écrêtage de l'ampli, pas sur celle du HP.")
            v_lim = v_clip
        if amp_nom == "QSC_PL380" and mode == "bridge":
            alertes.append("PL380 bridge : puissances constructeur en burst — marge réelle continue plus faible.")
    else:
        alertes.append(f"{amp_nom} : pas de puissance constructeur à {z_charge} Ω en mode {mode}.")

    dbu_proc = dbu(v_lim) - gain_eff
    res["gain_ampli_effectif_db"] = round(gain_eff, 2)
    res["dbu_sortie_processeur"] = round(dbu_proc, 1) + 0.0

    proc = mat.get("processeurs", {}).get(cfg.get("processeur") or "", {})
    unite_lib = (proc.get("limiteur") or {}).get("unite_threshold") if isinstance(proc.get("limiteur"), dict) else None
    unite_lib = unite_lib if unite_lib in ("dBFS", "dBu", "V") else None
    unite = unite_lib or cfg.get("processeur_unite_seuil") or "dBFS"
    if unite_lib and cfg.get("processeur_unite_seuil") and cfg["processeur_unite_seuil"] != unite_lib:
        alertes.append(f"Unité de seuil de la config ({cfg['processeur_unite_seuil']}) ≠ bibliothèque ({unite_lib}) : "
                       "bibliothèque retenue.")
    res["unite_seuil"] = unite
    if unite == "dBu":
        res["seuil_limiteur"] = round(dbu_proc, 1) + 0.0
        pe = [v for v in (proc.get("pleine_echelle_sortie_dbu"), proc.get("sortie_max_dbu_manuel"), proc.get("sortie_max_dbu"))
              if v is not None]
        if pe and dbu_proc > min(pe):
            alertes.append(f"Seuil {dbu_proc:.1f} dBu au-dessus de la pleine échelle possible du processeur "
                           f"({min(pe)} dBu, sources divergentes {pe}) : le limiteur risque de ne jamais agir — "
                           "mesurer la pleine échelle au voltmètre avant de compter sur lui.")
    elif unite == "V":
        res["seuil_limiteur"] = round(0.775 * 10 ** (dbu_proc / 20), 3)
    else:
        reglage = cfg.get("processeur_reglage_sortie") or cfg.get("venu_reglage_sortie")
        ref = (proc.get("sortie_0dbfs_dbu") or {}).get(reglage) if reglage else None
        if proc.get("sortie_0dbfs_dbu") and not reglage_sortie_confirme(cfg):
            alertes.append(f"Réglage de sortie {reglage or '?'} dBu NON CONFIRMÉ sur l'appareil (usine VENU360 : +12) : "
                           "lire Output1Gain…Output6Gain (`dbx_hiqnet carte`) puis passer "
                           "processeur_reglage_sortie_confirme à true. Écriture du seuil refusée d'ici là.")
        if ref is None and proc.get("sortie_max_dbu") is not None and not proc.get("sortie_0dbfs_dbu"):
            ref = proc["sortie_max_dbu"]
            alertes.append(f"0 dBFS pris = sortie max du processeur ({ref} dBu) — à confirmer dans son manuel.")
        if ref is None:
            alertes.append("Niveau de sortie à 0 dBFS du processeur inconnu : seuil dBFS non calculable (renseigner la bibliothèque / le réglage).")
        else:
            seuil = dbu_proc - ref
            # Dépassement des crêtes au-dessus du seuil (dbx : PA2 « PeakPlus » 3 dB fixe ; VENU360 PeakStopPlus
            # OVERSHOOT 2–6 dB, 6 par défaut). Manuel PA2 p. 44 : baisser le seuil d'autant (« coussin »).
            lim = proc.get("limiteur") or {}
            over = cfg.get("processeur_overshoot_db")
            if over is None:
                over = lim.get("overshoot_fixe_db")
            if over is None and lim.get("peakstopplus_overshoot_db"):
                over = lim.get("overshoot_defaut_db", max(lim["peakstopplus_overshoot_db"]))
                alertes.append(f"Overshoot PeakStopPlus non renseigné : défaut {over} dB pris (lire `carte`, "
                               "renseigner `processeur_overshoot_db`).")
            if over:
                res["seuil_sans_coussin"] = round(seuil, 1) + 0.0
                res["coussin_overshoot_db"] = over
                seuil -= over
            res["seuil_limiteur"] = round(seuil, 1) + 0.0
            lo, hi = lim.get("threshold_db", [-60, 0])
            if not lo <= seuil <= hi:
                alertes.append(f"Seuil {seuil:.1f} dBFS hors plage processeur [{lo}, {hi}] : revoir gain ampli ou réglage de sortie.")
            if seuil + (over or 0) < -24:
                alertes.append("Seuil très bas (< -24 dBFS) : gain ampli trop élevé pour cette voie — envisager de baisser le gain ampli.")
    res["alertes"] = alertes
    return res


def rapport_markdown(cfg: dict, lignes: list[dict]) -> str:
    out = [f"# Structure de gain — {cfg['nom']}",
           f"Type {cfg.get('type_systeme', 'composants')} · processeur {cfg.get('processeur') or '?'} (sortie {cfg.get('processeur_reglage_sortie') or cfg.get('venu_reglage_sortie') or '—'}) · limite {int(cfg.get('pourcentage_puissance_limite', 0.75)*100)} % puissance nominale · T = {cfg.get('temperature_c', 20)} °C (c = {vitesse_son(cfg.get('temperature_c', 20)):.1f} m/s)", "",
           f"| Voie | HP | Z vue | V cible bornes | dBu bornes | Gain ampli eff. | dBu sortie proc. | Seuil ({(lignes[0].get('unite_seuil') if lignes else None) or 'dBFS'}) | Marge ampli | SPL max 1 m (coh./incoh.) |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for r in lignes:
        def g(k, suf=""):
            return f"{r[k]}{suf}" if r.get(k) is not None else "—"
        hp_txt = r['hp'] if r['nb_hp'] in ("—", "?") else f"{r['nb_hp']}× {r['hp']}"
        out.append(f"| {r['voie']} | {hp_txt} | {g('z_vue_ampli_ohm',' Ω')} | {g('v_cible_bornes_v',' V')} | {g('dbu_cible_bornes')} | {g('gain_ampli_effectif_db',' dB')} | {g('dbu_sortie_processeur')} | **{g('seuil_limiteur')}**{f" ({r['seuil_sans_coussin']} − {r['coussin_overshoot_db']})" if r.get('coussin_overshoot_db') else ""} | {g('marge_ampli_db',' dB')} | {g('spl_max_1m_coherent_db')} / {g('spl_max_1m_incoherent_db')} |")
    out += ["", "## Alertes"]
    for r in lignes:
        for a in r.get("alertes", []):
            out.append(f"- **{r['voie']}** : {a}")
    out += ["", "## Limites de ce calcul",
            "Calcul **théorique**, à confirmer par mesure avant de considérer une voie comme protégée.",
            "- Les hypothèses sont la puissance de référence de la bibliothèque, l'impédance nominale et le gain ampli déclaré.",
            "- Le calcul ne modélise pas l'impédance réelle en fonction de la fréquence, ni l'affaissement de l'alimentation ampli, "
            "ni la compression thermique, ni le facteur de crête du programme, ni l'excursion dans la caisse (Xmax).",
            "- À vérifier sur place, HP débranchés : tension aux bornes au seuil (voltmètre RMS), puis gain ampli et point d'écrêtage réels."]
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):  # console Windows cp1252 : Ω, ≈, − sinon UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--delai", type=float, help="distance en m")
    ap.add_argument("--temp", type=float, default=20.0)
    ap.add_argument("--renfort", nargs=2, type=float, metavar=("D_FACADE", "D_RENFORT"),
                    help="distances (m) du point de raccord (égal niveau) à la façade et au renfort -> délai du renfort")
    ap.add_argument("--precedence", type=float, default=0.0,
                    help="marge de précédence (ms) : 0 par défaut (raccord aligné, McCarthy) ; ~10 selon Boyce")
    ap.add_argument("--puissance", nargs=3, type=float, metavar=("SPL", "DIST", "SENS"),
                    help="SPL visé (dB) à DIST (m), sensibilité 1 W/1 m -> puissance ampli requise")
    ap.add_argument("--marge", type=float, default=6.0, help="marge dynamique (dB), défaut 6")
    ap.add_argument("--cable", nargs=3, type=float, metavar=("LONGUEUR_M", "SECTION_MM2", "RE_OHM"),
                    help="câble HP (longueur simple, section, Re du HP) -> perte en dB")
    ap.add_argument("--dc", nargs=3, type=float, metavar=("VOLUME_M3", "RT60_S", "Q"),
                    help="distance critique de la salle pour une enceinte de facteur Q")
    a = ap.parse_args()
    if a.delai is not None:
        print(f"{a.delai} m à {a.temp} °C (c = {vitesse_son(a.temp):.1f} m/s) -> {delai_ms(a.delai, a.temp):.3f} ms")
        return
    outils = False
    perte = 0.0
    if a.cable:
        perte = perte_cable_db(*a.cable)
        print(f"Câble {a.cable[0]:g} m x2 en {a.cable[1]:g} mm² sur Re {a.cable[2]:g} Ω -> perte {perte:.2f} dB "
              f"({(1 - 10 ** (-perte / 10)) * 100:.0f} % de la puissance). Seuils limiteurs : inchangés (calculés aux bornes).")
        outils = True
    if a.puissance:
        r = puissance_requise(*a.puissance, marge_db=a.marge, perte_cable=perte)
        print(f"{a.puissance[0]:g} dB SPL à {a.puissance[1]:g} m : chute {r['chute_distance_db']} dB, "
              f"{r['spl_requis_1m_db']} dB requis à 1 m (marge {a.marge:g} dB), {r['excedent_sur_1w_db']} dB au-dessus de 1 W "
              f"(câble {perte:.2f} dB) -> {r['puissance_w']:.0f} W continus par enceinte. Champ libre, sans gain de salle. "
              "Ne change jamais le plafond du limiteur (puissance AES du HP).")
        outils = True
    if a.renfort:
        d = delai_renfort_ms(*a.renfort, a.temp, a.precedence)
        print(f"Renfort : {d:.1f} ms (écart {a.renfort[0] - a.renfort[1]:g} m à {a.temp:g} °C"
              + (f" + précédence {a.precedence:g} ms" if a.precedence else ", raccord aligné en phase") + "), à confirmer à l'IR au point de raccord.")
        if a.precedence:
            print(f"  Attention : {a.precedence:g} ms de décalage créent un peigne dès {1000 / (2 * a.precedence):.0f} Hz "
                  "au point de raccord (McCarthy). Comparer à l'écoute avec le raccord aligné avant de garder la marge.")
        outils = True
    if a.dc:
        dc = distance_critique_m(*a.dc)
        print(f"Distance critique ≈ {dc:.1f} m ; public au-delà de ~{2 * dc:.0f} m : envisager renforts retardés "
              f"ou enceinte plus directive (parole inacceptable vers {3 * dc:.0f} m).")
        outils = True
    if outils:
        return
    if not a.config:
        ap.error("config requise (ou --delai, --renfort, --puissance, --cable, --dc)")
    mat, cfg = charger(a.config)
    lignes = [calc_voie(v, mat, cfg) for v in cfg["voies"]]
    print(json.dumps({"config": cfg["nom"], "voies": lignes}, ensure_ascii=False, indent=2) if a.json else rapport_markdown(cfg, lignes))


if __name__ == "__main__":
    main()
