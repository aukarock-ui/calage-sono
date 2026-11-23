#!/usr/bin/env python3
"""
calc_systeme.py — Calculs déterministes de structure de gain et de limiteurs.

Claude ne fait JAMAIS ces calculs « de tête » : il lance ce script et cite sa sortie.

Usage :
  python tools/calc_systeme.py data/configs/ma-sono-actuel.yaml            # tableau Markdown
  python tools/calc_systeme.py data/configs/ma-sono-actuel.yaml --json     # sortie machine
  python tools/calc_systeme.py --delai 12.4 --temp 28                      # distance (m) -> délai (ms)

Formules (méthode §2, §5, §8) :
  V = sqrt(P x R)                      tension aux bornes d'un HP
  dBu = 20 log10(V / 0.775)
  Gain bridge = gain canal + 20 log10(2) (= +6,02 dB)   -> À CONFIRMER au voltmètre
  dBu sortie processeur = dBu bornes - gain ampli
  Seuil Venu360 (dBFS) = dBu sortie processeur - dBu(0 dBFS) du réglage de sortie
  c (m/s) = 331,3 + 0,606 x T(°C) ; délai (ms) = d / c x 1000
"""
from __future__ import annotations

import argparse
import json
import math
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
    # Bass-reflex : sous l'accord, plus de charge acoustique → excursion libre. HPF obligatoire (méthode §1).
    filtres = (cfg.get("crossover") or {}).get(v["nom"]) or {}
    if "reflex" in str(v.get("charge") or "").lower() and not filtres.get("hpf"):
        alertes.append("DANGER : voie bass-reflex sans HPF renseigné (crossover." + v["nom"] + ".hpf) — "
                       "HPF obligatoire sous l'accord de l'évent ; le relever ou le régler AVANT toute mise en puissance.")
    z_hp = hp["impedance_nominale_ohm"]
    n_s, n_p = v["hp_en_serie"], v["branches_paralleles"]
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
            alertes.append("L'ampli écrête AVANT la tension cible : le seuil doit suivre l'ampli (clip), pas le HP.")
        if amp_nom == "QSC_PL380" and mode == "bridge":
            alertes.append("PL380 bridge : puissances constructeur en burst — marge réelle continue plus faible.")
    else:
        alertes.append(f"{amp_nom} : pas de puissance constructeur à {z_charge} Ω en mode {mode}.")

    dbu_proc = dbu(v_bornes) - gain_eff
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
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--delai", type=float, help="distance en m")
    ap.add_argument("--temp", type=float, default=20.0)
    a = ap.parse_args()
    if a.delai is not None:
        print(f"{a.delai} m à {a.temp} °C (c = {vitesse_son(a.temp):.1f} m/s) -> {delai_ms(a.delai, a.temp):.3f} ms")
        return
    if not a.config:
        ap.error("config requise (ou --delai)")
    mat, cfg = charger(a.config)
    lignes = [calc_voie(v, mat, cfg) for v in cfg["voies"]]
    print(json.dumps({"config": cfg["nom"], "voies": lignes}, ensure_ascii=False, indent=2) if a.json else rapport_markdown(cfg, lignes))


if __name__ == "__main__":
    main()
