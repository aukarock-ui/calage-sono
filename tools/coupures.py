#!/usr/bin/env python3
"""
coupures.py — Optimise les fréquences de coupure d'une pile à composants. LECTURE SEULE : il classe des variantes,
Louis choisit, puis on corrige `crossover` dans la config et on régénère le plan (preset_auto). La mesure et l'écoute
départagent les 2-3 meilleures.

Pour chaque raccord entre voies voisines, fréquences candidates au 1/6 d'octave (pente de la config conservée).

Contraintes DURES (données de data/materiel.yaml ; un candidat hors bornes est éliminé) :
  - voie haute : fc ≥ bande basse du HP, ≥ coupure_min_recommandee_hz, ≥ puissance_specifiee_au_dessus_hz (sinon le
    seuil de limiteur calculé sur cette puissance ne vaut plus), ≥ coupure_min_hz du pavillon ;
  - voie basse : fc ≤ bande haute du HP ;
  - voie haute bass-reflex : HPF revérifié par excursion_br (preset_auto.verifier_hpf) ; s'il n'est pas vérifiable
    (accord/volume inconnus), fc ne descend pas sous le HPF actuel ;
  - une voie intermédiaire garde au moins une octave de bande (HYPOTHÈSE : en dessous, les filtres LR se chevauchent
    et la voie ne monte plus à son niveau nominal).

Critères, tous en dB :
  1. Puissance : niveau de programme atteignable à 1 m (au tiers d'octave du plateau) avec le profil de style ; chaque
     voie doit rayonner l'énergie du profil dans sa bande, filtres compris (puissance_programme) ; la voie la plus
     chargée par rapport à sa capacité fixe le niveau — plus haut = mieux. Valeur RELATIVE (comparer les variantes).
  2. Lobes verticaux au raccord : deux sources égales séparées de d (geometrie_pile, en hauteur), alignées au point
     d'écoute de référence ; écart de phase φ = 2π·d·(sin θ − sin θ_ref)/λ ; perte = −20·log|cos(φ/2)|, moyennée sur
     le public (distances --public, hauteur d'écoute). Interférence de deux sources (McCarthy 2007, ch. 2, sommation).
  3. Directivité au raccord : à l'angle α (demi-couverture horizontale du pavillon s'il y en a un, sinon 30°), écart
     de niveau entre les deux voies. HP = piston plan dans un écran : D(θ) = 2·J1(ka·sin θ)/(ka·sin θ), rayon tiré de
     sd_m2 ; pavillon = −6 dB à sa demi-couverture (au-dessus de sa fréquence de contrôle). Limites : un HP seul (pas
     le réseau de HP ni la caisse), plan horizontal.
Score = puissance − POIDS_LOBES·lobes − POIDS_DIRECTIVITE·directivité. Les poids sont des HYPOTHÈSES (à recaler par la
mesure et l'écoute) ; chaque critère est affiché séparément pour juger soi-même.

Usage :
  python tools/coupures.py data/configs/ma-sono-cki.yaml --style techno [--public 5-30] [--top 5]
"""
from __future__ import annotations

import argparse
import copy
import itertools
import math
import sys
from pathlib import Path

import numpy as np
import yaml
from scipy.special import j1

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calc_systeme as cs          # noqa: E402
import reglages_pile as rp         # noqa: E402

POIDS_LOBES = 1.0          # 1 dB de perte moyenne au public = 1 dB de puissance (hypothèse)
POIDS_DIRECTIVITE = 0.5    # écart de directivité moins pénalisé : partiellement corrigé par le placement (hypothèse)
ANGLE_DEFAUT_DEG = 30.0
X_MOINS_6DB = 2.2151       # 2·J1(x)/x = 0,5
PAS_OCTAVE = 6
BANDE_MIN_OCTAVES = 1.0
LIMITE_COMBINAISONS = 100_000   # au-delà : grilles éclaircies au 1/3 d'octave (l'actuel est gardé)
EQUIVALENCE_DB = 0.5       # écart de score sous lequel deux variantes sont tenues pour équivalentes (hypothèse :
                           # en dessous, ni la mesure ni l'écoute ne les départagent) -> on garde la plus proche de l'actuel


def grille(fmin: float, fmax: float) -> list[float]:
    """Fréquences au 1/6 d'octave (base 1 kHz), arrondies comme on les saisit (1 Hz < 1 kHz, 10 Hz au-dessus)."""
    out = []
    k = math.floor(PAS_OCTAVE * math.log2(max(fmin, 1) / 1000)) - 1
    while True:
        f = 1000 * 2 ** (k / PAS_OCTAVE)
        f = round(f) if f < 1000 else round(f, -1)
        if f > fmax + 1e-9:
            break
        if f >= fmin - 1e-9:
            out.append(float(f))
        k += 1
    return out


def piston_db(sd_m2: float, f: float, angle_deg: float, c: float) -> float:
    a = math.sqrt(sd_m2 / math.pi)
    x = 2 * math.pi * f / c * a * math.sin(math.radians(angle_deg))
    if x < 1e-6:
        return 0.0
    return 20 * math.log10(max(abs(2 * j1(x) / x), 1e-6))


def ouverture_piston_deg(sd_m2: float, f: float, c: float) -> float:
    """Couverture −6 dB totale d'un piston (180° s'il ne se resserre pas encore à −6 dB)."""
    ka = 2 * math.pi * f / c * math.sqrt(sd_m2 / math.pi)
    return 180.0 if ka <= X_MOINS_6DB else 2 * math.degrees(math.asin(X_MOINS_6DB / ka))


def perte_lobes_db(d_m: float, h_milieu_m: float, f: float, c: float, public: tuple[float, float],
                   h_ecoute_m: float, ref: tuple[float, float]) -> float:
    """Perte moyenne (dB) au public due à l'interférence verticale de deux sources égales écartées de d."""
    if d_m <= 0:
        return 0.0
    lam = c / f
    s_ref = math.sin(math.atan2(ref[1] - h_milieu_m, ref[0]))
    pertes = []
    for r in np.linspace(public[0], public[1], 25):
        s = math.sin(math.atan2(h_ecoute_m - h_milieu_m, r))
        phi = 2 * math.pi * d_m * (s - s_ref) / lam
        pertes.append(-20 * math.log10(max(abs(math.cos(phi / 2)), 1e-3)))
    return float(np.mean(pertes))


def bornes_raccord(cfg, mat, bas: dict, haut: dict) -> tuple[float, float, list[str]]:
    """(fmin, fmax, justifications) d'un raccord, depuis la bibliothèque."""
    hps = mat.get("haut_parleurs") or {}
    for v in (bas, haut):        # sans fiche, plus aucun plancher de protection : refus plutôt qu'un résultat faux
        if v.get("hp") not in hps:
            raise ValueError(f"{v['nom']} : HP « {v.get('hp')} » absent de la bibliothèque (materiel.yaml)")
    if haut.get("pavillon") and haut["pavillon"] not in (mat.get("pavillons") or {}):
        raise ValueError(f"{haut['nom']} : pavillon « {haut['pavillon']} » absent de materiel.yaml > pavillons")
    hb, hh = hps[bas["hp"]], hps[haut["hp"]]
    lo, hi, why = 10.0, 20000.0, []

    def plancher(val, motif):
        nonlocal lo
        if val and val > lo:
            lo = float(val)
            why.append(f"≥ {val:g} Hz ({motif})")

    def plafond(val, motif):
        nonlocal hi
        if val and val < hi:
            hi = float(val)
            why.append(f"≤ {val:g} Hz ({motif})")

    plancher((hh.get("bande_hz") or [None])[0], f"bande de {haut.get('hp')}")
    plancher(hh.get("coupure_min_recommandee_hz"), f"coupure mini recommandée {haut.get('hp')}")
    plancher(hh.get("puissance_specifiee_au_dessus_hz"), f"puissance de {haut.get('hp')} spécifiée au-dessus")
    pav = (mat.get("pavillons") or {}).get(haut.get("pavillon") or "") or {}
    plancher(pav.get("coupure_min_hz"), f"pavillon {haut.get('pavillon')}")
    plafond((hb.get("bande_hz") or [None, None])[1], f"bande de {bas.get('hp')}")
    return lo, hi, why


TIERS = np.array([1000 * 2 ** (k / 3) for k in range(-17, 14)])      # 20 Hz … 20 kHz au tiers d'octave


def puissance_programme(cfg: dict, mat: dict, style: str) -> dict:
    """Niveau de programme atteignable : chaque voie doit rayonner l'énergie du profil dans SA bande (filtres compris),
    Σ |H_v(f)|²·10^(T(f)/10) au tiers d'octave, avec sa capacité (SPL max 1 m au seuil du limiteur, sensibilité de
    fiche supposée plate dans la bande). Niveau au tiers d'octave du plateau = min sur les voies de (SPL max − énergie).
    Élargir la bande d'une voie l'oblige à rayonner plus : c'est ce qui fixe la répartition. Ne traite pas la crête
    (excursion : excursion_br) ni le facteur de crête (identique pour toutes les variantes)."""
    t = np.array([rp.cible_style(style, f) for f in TIERS])
    xo = cfg.get("crossover") or {}
    marges = {}
    for v in cfg["voies"]:
        r = cs.calc_voie(v, mat, cfg)
        f = xo.get(v["nom"]) or {}
        hpf = rp.lire_filtre(f.get("hpf")) if f.get("hpf") and str(f["hpf"]).lower() not in ("aucun", "out") else None
        lpf = rp.lire_filtre(f.get("lpf")) if f.get("lpf") and str(f["lpf"]).lower() not in ("aucun", "out") else None
        h2 = np.abs(rp.reponse_filtre(hpf, True, TIERS) * rp.reponse_filtre(lpf, False, TIERS)) ** 2
        centre = math.sqrt((hpf[2] if hpf else 30.0) * (lpf[2] if lpf else 16000.0))
        smax = r.get("spl_max_1m_coherent_db") if centre < rp.COHERENT_SOUS_HZ else r.get("spl_max_1m_incoherent_db")
        if smax is None:      # une voie sans capacité connue fausserait le classement : refus explicite
            raise ValueError(f"{v['nom']} : SPL max non calculable (sensibilité ou puissance manquante) — "
                             + " / ".join(r.get("alertes", [])))
        smax += float(v.get("correction_sensibilite_db") or 0)
        energie = 10 * math.log10(float(np.sum(h2 * 10 ** (t / 10))))
        marges[v["nom"]] = smax - energie
    if not marges:
        return {"niveau_db": None}
    lim = min(marges, key=marges.get)
    return {"niveau_db": round(marges[lim], 2), "voie_limitante": lim,
            "reserves_db": {k: round(x - marges[lim], 1) for k, x in marges.items()}}


def _avec_coupures(cfg: dict, coupures: dict[tuple[str, str], float], pentes: dict) -> dict:
    c = copy.deepcopy(cfg)
    xo = c.setdefault("crossover", {})
    for (b, h), f in coupures.items():
        fam, p = pentes[(b, h)]
        txt = f"{fam}{p} {f:g} Hz"
        xo.setdefault(b, {})["lpf"] = txt
        xo.setdefault(h, {})["hpf"] = txt
    return c


def optimiser(chemin_config: str, style: str | None = None, public: tuple[float, float] = (5.0, 30.0),
              hauteur_ecoute_m: float = 1.7, distance_ref_m: float = 20.0, top: int = 5) -> dict:
    import preset_auto
    mat, cfg = cs.charger(chemin_config)
    style = style or cfg.get("style_defaut") or "plat"
    c_son = cs.vitesse_son(cfg.get("temperature_c", 20))
    voies = cfg["voies"]
    xo = cfg.get("crossover") or {}
    geo = cfg.get("geometrie_pile") or {}
    hps = mat.get("haut_parleurs") or {}
    raccords, pentes, actuel, candidats, notes = [], {}, {}, {}, []
    for bas, haut in zip(voies, voies[1:]):
        cle = (bas["nom"], haut["nom"])
        txt = (xo.get(haut["nom"]) or {}).get("hpf") or (xo.get(bas["nom"]) or {}).get("lpf")
        flt = rp.lire_filtre(txt) if txt and str(txt).lower() not in preset_auto.AUCUN else None
        if not flt:
            raise ValueError(f"raccord {cle} : aucun filtre dans `crossover` (HPF de {haut['nom']} ou LPF de {bas['nom']})")
        pentes[cle], actuel[cle] = (flt[0], flt[1]), flt[2]
        autre = (xo.get(bas["nom"]) or {}).get("lpf")
        if autre and str(autre).lower() not in preset_auto.AUCUN and rp.lire_filtre(autre)[:2] != flt[:2]:
            notes.append(f"{cle[0]}/{cle[1]} : pentes asymétriques ({autre} / {txt}) — l'optimiseur applique "
                         f"{flt[0]}{flt[1]} des deux côtés")
        lo, hi, why = bornes_raccord(cfg, mat, bas, haut)
        hh = hps[haut["hp"]]
        if ("compression" in str(hh.get("type", "")).lower() or hh.get("puissance_specifiee_au_dessus_hz")) \
                and not haut.get("pavillon"):
            notes.append(f"{haut['nom']} : moteur à compression sans `pavillon:` dans la config — coupure mini du "
                         "pavillon NON prise en compte")
        cands = grille(lo, hi)
        if lo <= flt[2] <= hi and flt[2] not in cands:
            cands = sorted(cands + [flt[2]])
        if preset_auto._bass_reflex(haut):
            # chaque fréquence candidate devient le HPF de la voie haute : excursion revérifiée UNE PAR UNE ;
            # si l'actuel n'est pas vérifié « ok », on ne descend jamais sous lui
            r = cs.calc_voie(haut, mat, cfg)
            v_hp = r["v_cible_bornes_v"] / haut.get("hp_en_serie", 1) if r.get("v_cible_bornes_v") else None
            statut_actuel = preset_auto.verifier_hpf(haut, cfg, hh, v_hp)["statut"] if v_hp else "non_verifie"
            if statut_actuel != "ok":
                cands = [f for f in cands if f >= flt[2]]
                why.append(f"≥ {flt[2]:g} Hz (HPF bass-reflex de {haut['nom']} : actuel « {statut_actuel} », on ne descend pas)")
            if v_hp:
                gardes = []
                for f in cands:
                    st = preset_auto.verifier_hpf(haut, _avec_coupures(cfg, {cle: f}, pentes), hh, v_hp)["statut"]
                    if st == "ok" or (st == "non_verifie" and f >= flt[2]):
                        gardes.append(f)
                if len(gardes) < len(cands):
                    why.append(f"{len(cands) - len(gardes)} fréquence(s) écartée(s) : excursion > Xmax (excursion_br)")
                cands = gardes
            lo = min(cands) if cands else lo
        if not cands:
            raise ValueError(f"raccord {cle} : aucune fréquence possible entre {lo:g} et {hi:g} Hz ({'; '.join(why)})")
        if not lo <= flt[2] <= hi:
            notes.append(f"{cle[0]}/{cle[1]} : la coupure ACTUELLE {flt[2]:g} Hz viole une contrainte ({'; '.join(why)})")
        raccords.append({"bas": bas["nom"], "haut": haut["nom"], "actuel_hz": flt[2], "bornes_hz": [lo, hi],
                         "justification": why, "n_candidats": len(cands)})
        candidats[cle] = cands

    # critères par raccord (ne dépendent que de la fréquence de ce raccord)
    def criteres_raccord(cle, f):
        bas, haut = (next(v for v in voies if v["nom"] == n) for n in cle)
        gb, gh = geo.get(cle[0]) or {}, geo.get(cle[1]) or {}
        lob = None
        if gb.get("hauteur_cm") is not None and gh.get("hauteur_cm") is not None:
            d = abs(gh["hauteur_cm"] - gb["hauteur_cm"]) / 100
            hm = (gh["hauteur_cm"] + gb["hauteur_cm"]) / 200
            lob = perte_lobes_db(d, hm, f, c_son, public, hauteur_ecoute_m, (distance_ref_m, hauteur_ecoute_m))
        sd_b = ((hps.get(bas.get("hp")) or {}).get("ts") or {}).get("sd_m2")
        sd_h = ((hps.get(haut.get("hp")) or {}).get("ts") or {}).get("sd_m2")
        pav = (mat.get("pavillons") or {}).get(haut.get("pavillon") or "") or {}
        cov = (pav.get("directivite_deg") or {}).get("h")
        alpha = cov / 2 if cov else ANGLE_DEFAUT_DEG
        dirp = None
        if sd_b:
            lb = piston_db(sd_b, f, alpha, c_son)
            lh = -6.0 if cov else (piston_db(sd_h, f, alpha, c_son) if sd_h else None)
            if lh is not None:
                dirp = abs(lb - lh)
        return {"lobes_db": lob, "directivite_db": dirp, "angle_deg": alpha,
                "ouverture_bas_deg": ouverture_piston_deg(sd_b, f, c_son) if sd_b else None,
                "pavillon_sous_controle": bool(pav) and f < (pav.get("controle_h_des_hz") or 0)}

    if math.prod(len(c) for c in candidats.values()) > LIMITE_COMBINAISONS:
        for cle in candidats:
            c = candidats[cle]
            candidats[cle] = sorted(set(c[::2]) | ({actuel[cle]} & set(c)))
        notes.append("trop de combinaisons : grilles éclaircies au 1/3 d'octave")
    if math.prod(len(c) for c in candidats.values()) > LIMITE_COMBINAISONS:
        raise ValueError("trop de combinaisons même au 1/3 d'octave : optimiser une partie des raccords à la fois")
    cache = {(cle, f): criteres_raccord(cle, f) for cle in candidats for f in candidats[cle]}
    cles = list(candidats)

    def evaluer(choix: tuple[float, ...]) -> dict | None:
        for i in range(1, len(choix)):
            if choix[i] / choix[i - 1] < 2 ** BANDE_MIN_OCTAVES - 1e-9:
                return None
        cp = _avec_coupures(cfg, dict(zip(cles, choix)), pentes)
        vo = puissance_programme(cp, mat, style)
        p = vo.get("niveau_db")
        if p is None:
            return None
        crit = [cache[(cle, f)] for cle, f in zip(cles, choix)]
        lob = sum(x["lobes_db"] or 0 for x in crit)
        dirp = sum(x["directivite_db"] or 0 for x in crit)
        return {"coupures_hz": dict(zip([f"{b}/{h}" for b, h in cles], choix)), "puissance_db": p,
                "voie_limitante": vo["voie_limitante"], "reserves_db": vo["reserves_db"], "lobes_db": round(lob, 2), "directivite_db": round(dirp, 2),
                "score": round(p - POIDS_LOBES * lob - POIDS_DIRECTIVITE * dirp, 2),
                "detail": {f"{b}/{h}": x for (b, h), x in zip(cles, crit)}}

    res = [r for r in (evaluer(ch) for ch in itertools.product(*(candidats[k] for k in cles))) if r]
    if not res:
        raise ValueError("aucune combinaison ne respecte les contraintes (bandes d'au moins une octave)")
    res.sort(key=lambda r: -r["score"])
    ref = evaluer(tuple(actuel[k] for k in cles))

    def ecart_actuel(r):        # changement total, en octaves
        return sum(abs(math.log2(f / actuel[k])) for k, f in zip(cles, r["coupures_hz"].values()))
    equiv = [r for r in res if r["score"] >= res[0]["score"] - EQUIVALENCE_DB]
    conseil = min(equiv, key=ecart_actuel)
    res = [conseil] + [r for r in res if r is not conseil]
    # contrôle de sommation des meilleures (alignement calculé au point de référence)
    for r in res[:top] + ([ref] if ref else []):
        try:
            dl = rp.delais(_avec_coupures(cfg, dict(zip(cles, r["coupures_hz"].values())), pentes),
                           distance_ref_m, hauteur_ecoute_m)
            r["sommes_db"] = {f"{p['bas']}/{p['haut']}": round(p["somme_db"], 1) for p in dl["paires"]}
            r["delais_ms"] = {n: round(x["delai_ms"], 2) for n, x in dl["voies"].items()}
        except ValueError as e:
            r["sommes_db"] = {"erreur": str(e)}
    return {"config": str(Path(chemin_config).resolve()), "empreinte": _empreinte(chemin_config),
            "systeme": cfg.get("nom"), "style": style, "public_m": list(public), "hauteur_ecoute_m": hauteur_ecoute_m,
            "raccords": raccords, "actuel": ref, "n_equivalentes": len(equiv),
            "gain_vs_actuel_db": round(conseil["score"] - ref["score"], 2) if ref else None, "classement": res[:top], "n_combinaisons": len(res), "notes": notes,
            "pentes": {f"{b}/{h}": f"{fam}{p}" for (b, h), (fam, p) in pentes.items()},
            "crossover_actuel": {v["nom"]: dict(xo.get(v["nom"]) or {}) for v in voies}}


def rapport(r: dict) -> str:
    l = [f"# Coupures — {r['systeme']} · style « {r['style']} » · public {r['public_m'][0]:g}-{r['public_m'][1]:g} m, "
         f"écoute {r['hauteur_ecoute_m']:g} m", "", "## Bornes (bibliothèque)"]
    for x in r["raccords"]:
        l.append(f"- {x['bas']}/{x['haut']} : {x['bornes_hz'][0]:g}–{x['bornes_hz'][1]:g} Hz "
                 f"(actuel {x['actuel_hz']:g}) — " + ("; ".join(x["justification"]) or "aucune contrainte de fiche"))
    l += [*[f"- ⚠ {n}" for n in r["notes"]], "",
          f"## Classement ({r['n_combinaisons']} combinaisons valides ; score = puissance − {POIDS_LOBES:g}×lobes − "
          f"{POIDS_DIRECTIVITE:g}×directivité, poids = hypothèses)", "",
          f"Variante 1 = CONSEILLÉE : la plus proche de l'actuel parmi les {r['n_equivalentes']} variantes à moins de "
          f"{EQUIVALENCE_DB:g} dB de la meilleure ; gain de score vs actuel : {r['gain_vs_actuel_db']:+} dB"
          + (" (non significatif : garder l'actuel)" if (r['gain_vs_actuel_db'] or 0) < EQUIVALENCE_DB else "") + ".", "",
          "| # | Coupures | Puissance (programme, 1/3 oct, 1 m) | Lobes (perte moy. public) | Directivité (écart) | Score | Somme à fc |",
          "|---|---|---|---|---|---|---|"]

    def ligne(i, x):
        cps = " · ".join(f"{k} **{v:g}**" for k, v in x["coupures_hz"].items())
        sm = ", ".join(f"{v:+.1f}" if isinstance(v, (int, float)) else str(v) for v in (x.get("sommes_db") or {}).values())
        return (f"| {i} | {cps} | {x['puissance_db']:.1f} dB ({x['voie_limitante']}) | {x['lobes_db']:.1f} dB | "
                f"{x['directivite_db']:.1f} dB | **{x['score']:.1f}** | {sm} |")
    if r["actuel"]:
        l.append(ligne("actuel", r["actuel"]))
    for i, x in enumerate(r["classement"], 1):
        l.append(ligne(i, x))
    best = r["classement"][0]
    l += ["", "## Détail par raccord (variante conseillée)"]
    for k, d in best["detail"].items():
        l.append(f"- {k} à {best['coupures_hz'][k]:g} Hz : lobes {d['lobes_db'] if d['lobes_db'] is None else round(d['lobes_db'], 1)} dB, "
                 f"directivité {d['directivite_db'] if d['directivite_db'] is None else round(d['directivite_db'], 1)} dB à "
                 f"{d['angle_deg']:g}°, ouverture du HP bas ≈ {d['ouverture_bas_deg'] and round(d['ouverture_bas_deg'])}°"
                 + (" — pavillon SOUS sa fréquence de contrôle" if d["pavillon_sous_controle"] else ""))
    l += ["", "## Extrait `crossover:` (variante 1, à copier si Louis la retient)", "```yaml"]
    pentes = r["pentes"]
    xo: dict = {n: {k: (x.get(k) or "aucun") for k in ("hpf", "lpf")} for n, x in r["crossover_actuel"].items()}
    for k, f in best["coupures_hz"].items():
        b, h = k.split("/")
        xo.setdefault(b, {})["lpf"] = f"{pentes[k]} {f:g} Hz"
        xo.setdefault(h, {})["hpf"] = f"{pentes[k]} {f:g} Hz"
    for nom, x in xo.items():
        l.append(f"  {nom}: {{" + ", ".join(f"{k}: \"{v}\"" for k, v in sorted(x.items())) + "}")
    l += ["```", "Extrait complet (voies extrêmes : filtres actuels ; « aucun » = filtre absent déclaré). Départager les 2-3 premières à la mesure "
          "(ONAX + hauteur) et à l'écoute, puis preset_auto pour régénérer le plan."]
    return "\n".join(l)


def _empreinte(chemin_config: str) -> str:
    """Empreinte du texte de la config (et de son recalage s'il existe) : un résultat d'optimiser() calculé sur une
    autre config, ou avant une modification du fichier, est refusé par ecrire_coupures()."""
    import hashlib
    h = hashlib.sha256(Path(chemin_config).read_bytes())
    rc = Path(cs.chemin_recalage(chemin_config))
    if rc.exists():
        h.update(rc.read_bytes())
    return h.hexdigest()


def ecrire_coupures(chemin_config: str, r: dict, rang: int = 1) -> dict:
    """Écrit la variante `rang` du classement dans la section `crossover` de la config (texte modifié sur place :
    commentaires conservés), puis relit le YAML et EXIGE que chaque filtre corresponde ; sinon rien n'est écrit.
    Renvoie {voie: {hpf/lpf: (avant, après)}}."""
    import re as _re
    import yaml as _yaml
    if r.get("config") != str(Path(chemin_config).resolve()) or r.get("empreinte") != _empreinte(chemin_config):
        raise ValueError("résultat d'optimisation périmé ou calculé sur une autre config : relancer coupures.py")
    if not 1 <= rang <= len(r["classement"]):
        raise ValueError(f"variante {rang} absente du classement (1 à {len(r['classement'])})")
    var = r["classement"][rang - 1]
    voulu: dict = {}
    for k, f in var["coupures_hz"].items():
        b, h = k.split("/")
        txt = f"{r['pentes'][k]} {f:g} Hz"
        voulu.setdefault(b, {})["lpf"] = txt
        voulu.setdefault(h, {})["hpf"] = txt
    p = Path(chemin_config)
    with open(p, encoding="utf-8", newline="") as fh:   # fins de ligne conservées (pas de conversion LF/CRLF)
        lignes = fh.read().splitlines(keepends=True)
    try:
        debut = next(i for i, l in enumerate(lignes) if _re.match(r"^crossover:\s*(#.*)?$", l))
    except StopIteration:
        raise ValueError("section `crossover:` introuvable dans la config") from None
    fin = next((i for i in range(debut + 1, len(lignes)) if _re.match(r"^\S", lignes[i])), len(lignes))
    avant_cfg = _yaml.safe_load("".join(lignes)) or {}
    changes = {}
    for nom, filtres in voulu.items():
        idx = next((i for i in range(debut + 1, fin) if _re.match(rf"^\s+{_re.escape(nom)}\s*:", lignes[i])), None)
        if idx is None:
            raise ValueError(f"crossover.{nom} introuvable (ligne « {nom}: {{hpf: …, lpf: …}} » attendue)")
        for cle, val in filtres.items():
            ancien = ((avant_cfg.get("crossover") or {}).get(nom) or {}).get(cle)
            nouv_l, n = _re.subn(rf"(\b{cle}\s*:\s*)(\"[^\"]*\"|'[^']*'|null|[^,}}#]+?)(\s*[,}}])",
                                 lambda m: f'{m.group(1)}"{val}"{m.group(3)}', lignes[idx], count=1)
            if n != 1:
                raise ValueError(f"crossover.{nom}.{cle} : format de ligne non reconnu, à modifier à la main")
            lignes[idx] = nouv_l
            if ancien != val:
                changes.setdefault(nom, {})[cle] = (ancien, val)
    texte = "".join(lignes)
    relu = (_yaml.safe_load(texte) or {}).get("crossover") or {}
    for nom, filtres in voulu.items():
        for cle, val in filtres.items():
            if (relu.get(nom) or {}).get(cle) != val:
                raise ValueError(f"relecture : crossover.{nom}.{cle} = {(relu.get(nom) or {}).get(cle)!r}, attendu {val!r}")
    # Rien d'autre ne doit bouger : configs complètes égales une fois les seules clés visées remises à l'ancienne valeur.
    apres_cfg = _yaml.safe_load(texte) or {}
    for nom, filtres in voulu.items():
        for cle in filtres:
            apres_cfg["crossover"][nom][cle] = ((avant_cfg.get("crossover") or {}).get(nom) or {}).get(cle)
    if apres_cfg != avant_cfg:
        raise ValueError("relecture : une autre valeur que les filtres visés aurait changé")
    if changes:   # écriture atomique : fichier temporaire du même dossier puis remplacement
        import os as _os
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(texte)
        _os.replace(tmp, p)
    return changes


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--style")
    ap.add_argument("--public", default="5-30", help="distances du public, min-max en m (défaut 5-30)")
    ap.add_argument("--hauteur-ecoute", type=float, default=1.7)
    ap.add_argument("--distance-ref", type=float, default=20.0, help="point d'alignement des délais (m)")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--ecrire", type=int, nargs="?", const=1, metavar="N",
                    help="écrit la variante N (défaut 1 = conseillée) dans `crossover` de la config ; ensuite "
                         "preset_auto (plan) puis appliquer-plan (processeur)")
    a = ap.parse_args()
    pmin, pmax = (float(x) for x in a.public.split("-"))
    try:
        r = optimiser(a.config, a.style, (pmin, pmax), a.hauteur_ecoute, a.distance_ref, a.top)
    except ValueError as e:
        sys.exit(f"ERREUR : {e}")
    print(rapport(r))
    if a.ecrire is not None:
        try:
            ch = ecrire_coupures(a.config, r, a.ecrire)
        except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as e:
            sys.exit(f"REFUS : {e}. Config inchangée.")
        if not ch:
            print(f"\nVariante {a.ecrire} = config actuelle : rien à écrire.")
        if ch:
            print(f"\nÉcrit dans {a.config} :")
            for nom, x in ch.items():
                print(f"  {nom} : " + " · ".join(f"{k} {v[0]} -> {v[1]}" for k, v in x.items()))
            print("Suite : preset_auto.py <config> --style … puis dbx_hiqnet.py appliquer-plan … (ou feuille de saisie).")


if __name__ == "__main__":
    main()
