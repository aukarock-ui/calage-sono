#!/usr/bin/env python3
"""
reglages_pile.py — Réglages calculés a priori pour une pile à composants : temps des limiteurs et délais entre voies.
Ce sont des POINTS DE DÉPART : la mesure (IR d'abord, phase ensuite) les corrige, et l'écart est noté pour
améliorer le modèle (journal/ETAT.md).

1. Temps des limiteurs (étage rapide, crête/excursion) — Proel DSO26, manuel « Output Limiters » (p. 34) :
   le constructeur fixe l'attaque d'après la fréquence du passe-haut de la voie, puis relâchement = 16 × attaque.
   Bande du HPF -> attaque : <31 Hz 45 ms · 31-63 Hz 16 ms · 63-125 Hz 8 ms · 125-250 Hz 4 ms · 250-500 Hz 2 ms ·
   500 Hz-1 kHz 1 ms · 1-2 kHz 0,5 ms · >2 kHz 0,3 ms. Le même manuel avertit qu'un temps trop court
   distord le grave.
   Protection thermique : QSC (Neunaber, « Power Limiting », 2004) emploie des constantes de l'ordre de la seconde
   (attaque 0,9-3 s, relâchement 2,6-8,9 s), liées à la bobine. Le VENU360 n'a qu'un limiteur par sortie
   (attaque max. 200 ms) : la protection thermique repose donc sur le seuil réduit (75 % de la puissance,
   « derated » chez QSC) et la détection RMS sur le grave (PeakStopPlus Off). Les moteurs à compression gardent
   la détection crête (méthode du kit, McCarthy p. 28 : « trauma protection must be much faster than heat »).
   Conversion du relâchement en dB/s (le VENU360 l'exprime en vitesse) : constante de temps exponentielle τ,
   soit 20·log10(e) = 8,686 dB par τ -> vitesse = 8,686 / τ. C'est une HYPOTHÈSE de conversion, à vérifier.

2. Délais entre voies d'une même pile :
   - trajet de chaque voie = distance du centre acoustique (profondeur derrière la face avant, hauteur) au point
     d'écoute ;
   - chaque voie = ses filtres (LR/BW analogiques, comme le processeur) + ce trajet ;
   - de haut en bas, on cherche le délai de la voie grave qui annule l'écart de phase avec la voie aiguë à la
     coupure, en retenant la solution la plus proche de l'écart de trajet (pas de saut de période arbitraire).
   McCarthy p. 465 : « The impulse responses should combine to a single impulse » ; p. 400 : la mesure d'IR sert à
   trouver les décalages. Les centres acoustiques de la config sont des hypothèses tant qu'ils ne sont pas mesurés.
   Non modélisés : réponse propre des HP et des caisses (accord BR, pavillon), filtres internes des amplis.

Usage :
  python tools/reglages_pile.py data/configs/ma-sono-cki.yaml [--distance 20] [--hauteur-ecoute 1.7]
"""
from __future__ import annotations

import argparse
import cmath
import math
import re
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calc_systeme import vitesse_son   # noqa: E402

# Proel DSO26 (p. 34) : borne haute de la bande du HPF (Hz) -> attaque (ms) ; relâchement = 16 × attaque
TABLE_PROEL = ((31, 45.0), (63, 16.0), (125, 8.0), (250, 4.0), (500, 2.0), (1000, 1.0), (2000, 0.5), (math.inf, 0.3))
FACTEUR_RELACHEMENT = 16
DB_PAR_TAU = 20 * math.log10(math.e)          # 8,686 dB par constante de temps
VENU360_ATTAQUE_MS = (0.1, 200.0)             # materiel.yaml (manuel 5058681-C)
VENU360_RELACHEMENT_DB_S = (5.0, 360.0)


def temps_limiteur(hpf_hz: float | None, appareil=(VENU360_ATTAQUE_MS, VENU360_RELACHEMENT_DB_S)) -> dict:
    """Attaque/relâchement d'après la fréquence du passe-haut (Proel), convertis et bornés pour l'appareil."""
    f = hpf_hz or 10.0                        # pas de HPF : bande la plus basse (le plus lent)
    att = next(a for borne, a in TABLE_PROEL if f < borne)
    rel_ms = att * FACTEUR_RELACHEMENT
    vitesse = DB_PAR_TAU / (rel_ms / 1000)
    (amin, amax), (vmin, vmax) = appareil
    return {"hpf_hz": hpf_hz, "attaque_ms": att, "relachement_ms": rel_ms, "relachement_db_s": round(vitesse, 1),
            "attaque_appareil_ms": min(max(att, amin), amax),
            "relachement_appareil_db_s": round(min(max(vitesse, vmin), vmax)),
            "borne": not (amin <= att <= amax and vmin <= vitesse <= vmax)}


def lire_filtre(texte) -> tuple[str, int, float] | None:
    """« LR24 90 Hz » / « BW 18 1,6 kHz » -> (famille, pente dB/oct, fc Hz). None si absent."""
    if not texte:
        return None
    m = re.search(r"(LR|BW)\s*(\d+)\s+([\d.,]+)\s*(k?)Hz", str(texte), re.I)
    if not m:
        raise ValueError(f"filtre illisible : {texte!r}")
    return m.group(1).upper(), int(m.group(2)), float(m.group(3).replace(",", ".")) * (1000 if m.group(4) else 1)


def reponse_filtre(filtre, passe_haut: bool, f: np.ndarray) -> np.ndarray:
    if filtre is None:
        return np.ones_like(f, dtype=complex)
    fam, pente, fc = filtre
    ordre = pente // 6
    wc, w = 2 * math.pi * fc, 2 * math.pi * f
    btype = "highpass" if passe_haut else "lowpass"
    if fam == "LR":
        b, a = signal.butter(ordre // 2, wc, btype, analog=True)
        return signal.freqs(b, a, w)[1] ** 2
    b, a = signal.butter(ordre, wc, btype, analog=True)
    return signal.freqs(b, a, w)[1]


def delais(cfg: dict, distance_m: float = 20.0, hauteur_ecoute_m: float = 1.7) -> dict:
    """Délais (ms) par voie pour aligner la pile au point d'écoute, voie la plus tardive à 0."""
    c = vitesse_son(cfg.get("temperature_c", 20))
    voies = cfg["voies"]
    xo = cfg.get("crossover", {})
    geo = cfg.get("geometrie_pile", {})
    info = {}
    for v in voies:
        g = geo.get(v["nom"]) or {}
        if g.get("profondeur_cm") is None or g.get("hauteur_cm") is None:
            raise ValueError(f"geometrie_pile.{v['nom']} : profondeur_cm et hauteur_cm requis")
        dx = distance_m + g["profondeur_cm"] / 100
        dz = g["hauteur_cm"] / 100 - hauteur_ecoute_m
        trajet = math.hypot(dx, dz)
        f = xo.get(v["nom"], {})
        info[v["nom"]] = {"trajet_m": trajet, "hpf": lire_filtre(f.get("hpf")), "lpf": lire_filtre(f.get("lpf")),
                          "delai_ms": 0.0}

    def h(nom, fr, delai_ms):
        x = info[nom]
        fr = np.atleast_1d(np.asarray(fr, dtype=float))
        t = x["trajet_m"] / c + delai_ms / 1000
        return reponse_filtre(x["hpf"], True, fr) * reponse_filtre(x["lpf"], False, fr) * np.exp(-2j * math.pi * fr * t)

    # du haut vers le bas : la voie grave s'aligne sur la voie aiguë voisine, à la fréquence de coupure
    ordre = [v["nom"] for v in voies]          # du grave à l'aigu (convention des configs)
    paires = []
    for bas, haut in reversed(list(zip(ordre, ordre[1:]))):
        if not (info[haut]["hpf"] or info[bas]["lpf"]):
            raise ValueError(f"raccord {bas}/{haut} : ni HPF sur {haut} ni LPF sur {bas} dans `crossover`")
        fc = info[haut]["hpf"][2] if info[haut]["hpf"] else info[bas]["lpf"][2]
        dphi = cmath.phase(h(haut, fc, info[haut]["delai_ms"])[0] / h(bas, fc, 0.0)[0])   # retard à ajouter au bas
        periode_ms = 1000 / fc
        base = -dphi / (2 * math.pi) * periode_ms                 # solution modulo une période
        geom = (info[haut]["trajet_m"] - info[bas]["trajet_m"]) / c * 1000 + info[haut]["delai_ms"]
        k = round((geom - base) / periode_ms)
        d = base + k * periode_ms
        info[bas]["delai_ms"] = d
        somme = abs(h(bas, fc, d)[0] + h(haut, fc, info[haut]["delai_ms"])[0])
        paires.append({"bas": bas, "haut": haut, "fc_hz": fc, "delai_bas_ms": d, "geometrique_ms": geom,
                       "somme_db": 20 * math.log10(max(somme, 1e-9))})
    mini = min(x["delai_ms"] for x in info.values())
    for x in info.values():
        x["delai_ms"] -= mini                                     # aucun délai négatif
    return {"c_m_s": c, "distance_m": distance_m, "voies": info, "paires": paires}


# --- 3. Équilibre des gains entre voies ---------------------------------------------------------------------------
# Même tension en sortie du processeur sur toutes les voies -> SPL à 1 m de chaque voie dans sa bande :
#   V bornes = V proc × gain ampli effectif (bridge +6,02 dB : manuels QSC « twice the voltage swing ») ;
#   chaque HP reçoit V bornes / (HP en série) ; P = V²/Z nominale ; SPL HP = sensibilité 1 W/1 m + 10·log P ;
#   N HP d'une voie : somme cohérente 20·log N si leur écartement est petit devant la longueur d'onde (grave, médium
#   bas), incohérente 10·log N sinon (aigus côte à côte, au-dessus de ~1 kHz).
# Gains proposés = voie la moins forte − chaque voie (tous ≤ 0 dB, donc ils ne touchent pas aux limiteurs placés après).
# NON modélisé : charge de la caisse (BR, clos, pavillon ≠ fiche HP), couplage au sol, réponse propre des HP,
# sensibilité du CP650Ti donnée sur TD-460 (pas sur HP640). La mesure corrige ; l'écart mesuré sert à recaler.
COHERENT_SOUS_HZ = 1000.0


def equilibre_gains(cfg: dict, mat: dict, niveau_proc_dbu: float = 0.0) -> dict:
    from calc_systeme import calc_voie
    out = {}
    for v in cfg["voies"]:
        r = calc_voie(v, mat, cfg)
        hp = mat["haut_parleurs"][v["hp"]]
        g = r.get("gain_ampli_effectif_db")
        if g is None or hp.get("sensibilite_db_1w1m") is None:
            out[v["nom"]] = {"erreur": "gain ampli ou sensibilité inconnus"}
            continue
        n_s, n_p = v.get("hp_en_serie", 1), v.get("branches_paralleles", 1)
        canaux = 2 if v.get("mode") == "parallele" else 1          # parallèle : 2 canaux, même signal
        n = n_s * n_p * canaux
        v_bornes = 0.7746 * 10 ** ((niveau_proc_dbu + g) / 20)
        p_hp = (v_bornes / n_s) ** 2 / hp["impedance_nominale_ohm"]
        flt = lire_filtre((cfg.get("crossover", {}).get(v["nom"]) or {}).get("hpf"))
        lpf = lire_filtre((cfg.get("crossover", {}).get(v["nom"]) or {}).get("lpf"))
        f_centre = math.sqrt((flt[2] if flt else 30.0) * (lpf[2] if lpf else 16000.0))
        coherent = f_centre < COHERENT_SOUS_HZ
        corr = float(v.get("correction_sensibilite_db") or 0.0)   # recalage par la mesure (preset_auto --recaler)
        spl = hp["sensibilite_db_1w1m"] + corr + 10 * math.log10(p_hp) + (20 if coherent else 10) * math.log10(n)
        out[v["nom"]] = {"n_hp": n, "gain_ampli_db": g, "v_bornes": v_bornes, "p_hp_w": p_hp, "f_centre_hz": f_centre,
                         "somme": "cohérente" if coherent else "incohérente", "spl_1m_db": spl,
                         "correction_sensibilite_db": corr}
    ok = [x["spl_1m_db"] for x in out.values() if "spl_1m_db" in x]
    for x in out.values():
        if "spl_1m_db" in x:
            x["gain_propose_db"] = round(min(ok) - x["spl_1m_db"], 1)
    return out


# --- 4. Courbe cible par style ------------------------------------------------------------------------------------
# Écart visé (dB) par rapport au plateau aigu, en fonction de la fréquence ; interpolé en log au centre de la bande de
# chaque voie. SOURCE : L-Acoustics, diapo « Loudspeaker system — System response », p. 34 (fournie par Louis le
# 24/11/2026, docs/references/profils_programme_system_response_p34.jpg) : performance d'un système exprimée en niveau
# + fréquence, en MOYENNE dans la salle, selon le programme. Lecture du graphe à ±1 dB :
#   Light (conférence) plateau 99 dB(A), +2 dB vers 60 Hz ;
#   Medium (jazz, acoustique) 102 dB(A), +10 dB vers 50 Hz, plateau atteint vers 600 Hz ;
#   Heavy (groupe live) 105 dB(A), +13 dB vers 55 Hz, plateau vers 750 Hz ;
#   Demanding (DJ, électro) 108 dB(A), +16 dB vers 55 Hz, plateau vers 1 kHz.
# Plateau plat au-dessus (pas de pente dans l'aigu). Sous ~50 Hz les courbes tombent : c'est la coupure du système,
# laissée au HPF (on garde ici la valeur du pic, le HPF fait la chute).
PROFILS = {
    "leger":    {60: 2, 200: 1.5, 500: 0, 20000: 0},
    "moyen":    {50: 9.5, 100: 8, 200: 5.5, 500: 1.5, 650: 0, 20000: 0},
    "lourd":    {55: 13, 100: 11.5, 200: 8, 500: 2, 750: 0, 20000: 0},
    "exigeant": {55: 16, 100: 13.5, 200: 9, 500: 4, 1100: 0, 20000: 0},
}
NIVEAU_PROFIL_DBA = {"leger": 99, "moyen": 102, "lourd": 105, "exigeant": 108}   # plateau, moyenne salle
STYLE_PROFIL = {"techno": "exigeant", "reggae-dub": "exigeant", "hip-hop": "lourd", "rock": "lourd",
                "jazz": "moyen", "acoustique": "moyen", "parole": "leger"}
STYLES = {"plat": {20: 0, 20000: 0}, **PROFILS, **{s: PROFILS[p] for s, p in STYLE_PROFIL.items()}}


def profil_de(style: str | None) -> str | None:
    return style if style in PROFILS else STYLE_PROFIL.get(style or "")


def cible_style(style: str, f_hz: float) -> float:
    pts = sorted(STYLES[style].items())
    if f_hz <= pts[0][0]:
        return float(pts[0][1])
    for (f0, g0), (f1, g1) in zip(pts, pts[1:]):
        if f_hz <= f1:
            return g0 + (g1 - g0) * math.log(f_hz / f0) / math.log(f1 / f0)
    return float(pts[-1][1])


def voicing(cfg: dict, mat: dict, style: str = "plat") -> dict:
    """Gains de sortie = équilibre plat + courbe du style, ramenés à ≤ 0 dB ; niveau max limité par la voie la plus faible."""
    from calc_systeme import calc_voie
    eq = equilibre_gains(cfg, mat)
    ok = {k: x for k, x in eq.items() if "spl_1m_db" in x}
    for nom, x in ok.items():
        x["cible_style_db"] = round(cible_style(style, x["f_centre_hz"]), 1)
        x["brut"] = x["gain_propose_db"] + x["cible_style_db"]
    haut = max(x["brut"] for x in ok.values())
    marges = {}
    for v in cfg["voies"]:
        if v["nom"] not in ok:
            continue
        x = ok[v["nom"]]
        x["gain_style_db"] = round(x["brut"] - haut, 1)
        r = calc_voie(v, mat, cfg)
        smax = r.get("spl_max_1m_coherent_db") if x["somme"] == "cohérente" else r.get("spl_max_1m_incoherent_db")
        if smax is not None:
            smax += x.get("correction_sensibilite_db", 0.0)
            x["spl_max_1m_db"] = smax
            marges[v["nom"]] = smax - x["cible_style_db"]        # niveau « médium équivalent » atteignable par cette voie
    limite = min(marges, key=marges.get) if marges else None
    for nom, x in ok.items():
        if limite and nom in marges:
            x["reserve_db"] = round(marges[nom] - marges[limite], 1)   # dB inutilisés avant son limiteur
    return {"style": style, "profil": profil_de(style), "voies": eq, "voie_limitante": limite,
            "niveau_medium_max_1m_db": round(marges[limite], 1) if limite else None}


def niveau_atteignable(vo: dict, distance_m: float) -> dict | None:
    """Plateau atteignable à `distance_m` (une pile, champ libre, source ponctuelle : -20·log d) comparé au niveau du
    profil. Ignore le gain de salle et de couplage au sol : ordre de grandeur, la mesure tranche."""
    if vo.get("niveau_medium_max_1m_db") is None:
        return None
    n = vo["niveau_medium_max_1m_db"] - 20 * math.log10(max(distance_m, 1.0))
    cible = NIVEAU_PROFIL_DBA.get(vo.get("profil") or "")
    return {"distance_m": distance_m, "plateau_db": round(n, 1), "cible_dba": cible,
            "marge_db": round(n - cible, 1) if cible else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--distance", type=float, default=20.0, help="distance horizontale du point d'écoute (m)")
    ap.add_argument("--hauteur-ecoute", type=float, default=1.7)
    ap.add_argument("--style", default=None, help="courbe cible : " + ", ".join(STYLES))
    a = ap.parse_args()
    from calc_systeme import charger
    mat, cfg = charger(a.config)              # avec les corrections mesurées (<config>.recalage.yaml) s'il y en a
    print(f"# Réglages calculés — {cfg.get('nom')}\n\n## Limiteurs (Proel : attaque selon le HPF, relâchement 16×)\n")
    print("| Voie | HPF | Attaque | Relâchement | → VENU360 |\n|---|---|---|---|---|")
    for v in cfg["voies"]:
        flt = lire_filtre((cfg.get("crossover", {}).get(v["nom"]) or {}).get("hpf"))
        t = temps_limiteur(flt[2] if flt else None)
        print(f"| {v['nom']} | {flt[2] if flt else '—'} Hz | {t['attaque_ms']} ms | {t['relachement_ms']:.0f} ms "
              f"({t['relachement_db_s']} dB/s) | {t['attaque_appareil_ms']} ms · {t['relachement_appareil_db_s']} dB/s"
              + (" (borné)" if t["borne"] else "") + (" (pas de HPF : réglage le plus lent)" if not flt else "") + " |")
    r = delais(cfg, a.distance, a.hauteur_ecoute)
    print(f"\n## Délais (point d'écoute à {a.distance} m, {a.hauteur_ecoute} m de haut, c = {r['c_m_s']:.1f} m/s)\n")
    print("| Voie | Trajet | Délai |\n|---|---|---|")
    for nom, x in r["voies"].items():
        print(f"| {nom} | {x['trajet_m']:.3f} m | **{x['delai_ms']:.2f} ms** |")
    print("\n| Raccord | fc | Délai géométrique seul | Délai avec phase des filtres | Somme à fc |\n|---|---|---|---|---|")
    for p in r["paires"]:
        print(f"| {p['bas']}/{p['haut']} | {p['fc_hz']:.0f} Hz | {p['geometrique_ms']:.2f} ms | {p['delai_bas_ms']:.2f} ms | "
              f"{p['somme_db']:+.1f} dB |")
    eq = equilibre_gains(cfg, mat)
    print("\n## Équilibre des gains (même niveau processeur 0 dBu sur chaque voie)\n")
    print("| Voie | HP | Gain ampli | P par HP | Somme | SPL 1 m | Gain de sortie proposé |\n|---|---|---|---|---|---|---|")
    for nom, x in eq.items():
        if "erreur" in x:
            print(f"| {nom} | — | — | — | — | — | {x['erreur']} |"); continue
        print(f"| {nom} | {x['n_hp']} | {x['gain_ampli_db']:.1f} dB | {x['p_hp_w']:.2f} W | {x['somme']} | "
              f"{x['spl_1m_db']:.1f} dB | **{x['gain_propose_db']:+.1f} dB** |")
    if a.style:
        vo = voicing(cfg, mat, a.style)
        print(f"\n## Style « {a.style} » (gains ramenés à ≤ 0 dB)\n")
        print("| Voie | Centre de bande | Cible style | Gain de sortie | Réserve avant limiteur |\n|---|---|---|---|---|")
        for nom, x in vo["voies"].items():
            if "gain_style_db" in x:
                print(f"| {nom} | {x['f_centre_hz']:.0f} Hz | {x['cible_style_db']:+.1f} dB | **{x['gain_style_db']:+.1f} dB** | "
                      f"{x.get('reserve_db', '—')} dB |")
        print(f"\nVoie limitante : **{vo['voie_limitante']}** — niveau médium max ≈ {vo['niveau_medium_max_1m_db']} dB à 1 m "
              "(les autres voies gardent leur réserve inutilisée).")
    print("\nHypothèses : centres acoustiques de `geometrie_pile` (non mesurés) ; réponses propres des HP/caisses ignorées. "
          "À comparer à la mesure (IR puis phase) et noter l'écart.")


if __name__ == "__main__":
    main()
