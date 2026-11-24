#!/usr/bin/env python3
"""
analyse_raccord.py — Métriques MESURÉES d'un raccord de coupure (fonctions pures, aucun matériel, aucun son).

Entrée : réponses complexes au micro, au format de rew_client.REW.reponse_frequence (ou du moteur simulé) :
  {"f_hz": [...], "mag_db": [...], "phase_deg": [...], "coherence": [...] (facultatif, 0-1)}
Les réponses d'une même comparaison doivent partager la grille de fréquences (même ppo) et la même référence de temps
(REW : timing reference en boucle ; sinon les phases de deux sweeps ne sont pas comparables).

Métriques (toutes en dB, « plus petit = mieux », sauf la puissance calculée qui reste celle de coupures.py) :
  perte_somme_db   20·log((|A|+|B|) / |A+B|) en moyenne d'énergie sur ±1/6 d'octave autour de fc : 0 dB = les deux
                   voies s'additionnent parfaitement en phase ; un trou de sommation la fait monter (McCarthy ch. 2,
                   « summation » : la somme de deux sources égales vaut +6 dB en phase, s'annule en opposition).
  ecart_phase_deg  phase(A) − phase(B) à fc (−180…180), après alignement.
  ondulation_db    RMS de (|A+B| − cible du style) sur [fc/2, 2·fc], moyenne retirée : bosse ou creux du raccord.
  regularite_db    écart-type ENTRE positions de la réponse de la paire sur [fc/2, 2·fc], chaque position ramenée à sa
                   moyenne (la distance change le niveau, pas la forme) : c'est ce que perte_lobes_db modélise.
  ecart_cible_db   RMS de (système complet − cible du style) sur 40 Hz-16 kHz, moyenne retirée.
Bandes à cohérence < 0,8 écartées quand la cohérence est fournie (méthode : on ne décide pas sur une bande peu fiable).

Pénalité mesurée d'une variante = Σ raccords (perte_somme + regularite + ondulation) — poids 1 (HYPOTHÈSE explicite,
même unité que les pénalités de coupures.py : dB équivalents). Score mesuré = puissance calculée − pénalité mesurée.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reglages_pile as rp         # noqa: E402

COHERENCE_MIN = 0.8
DEMI_FENETRE_OCT = 1 / 6           # ±1/6 d'octave autour de fc pour la sommation
BANDE_RACCORD_OCT = 1.0            # [fc/2, 2·fc]
BANDE_SYSTEME_HZ = (40.0, 16000.0)
POIDS_MESURE = {"perte_somme_db": 1.0, "regularite_db": 1.0, "ondulation_db": 1.0}   # HYPOTHÈSE, voir en-tête


def complexe(m: dict) -> tuple[np.ndarray, np.ndarray]:
    """(f, H complexe) depuis {f_hz, mag_db, phase_deg}. Phase absente = refus (somme impossible)."""
    f = np.asarray(m["f_hz"], dtype=float)
    if m.get("phase_deg") is None:
        raise ValueError("réponse sans phase : la sommation ne peut pas être évaluée (REW : timing reference requise)")
    mag = np.asarray(m["mag_db"], dtype=float)
    ph = np.asarray(m["phase_deg"], dtype=float)
    if not (len(f) == len(mag) == len(ph)) or len(f) < 4:
        raise ValueError("réponse incomplète (grilles de longueurs différentes ou trop courte)")
    return f, 10 ** (mag / 20) * np.exp(1j * np.deg2rad(ph))


def _meme_grille(*mesures: dict) -> np.ndarray:
    f0 = np.asarray(mesures[0]["f_hz"], dtype=float)
    for m in mesures[1:]:
        f = np.asarray(m["f_hz"], dtype=float)
        if len(f) != len(f0) or not np.allclose(f, f0, rtol=1e-6):
            raise ValueError("grilles de fréquences différentes : relire les mesures avec le même ppo")
    return f0


def _fiable(f: np.ndarray, *mesures: dict) -> np.ndarray:
    ok = np.isfinite(f)
    for m in mesures:
        c = m.get("coherence")
        if c is not None:
            ok &= np.asarray(c, dtype=float) >= COHERENCE_MIN
    return ok


def _masque(f: np.ndarray, fc: float, demi_oct: float) -> np.ndarray:
    return (f >= fc * 2 ** -demi_oct) & (f <= fc * 2 ** demi_oct)


def perte_somme_db(a: dict, b: dict, ab: dict, fc: float) -> float:
    """Perte de la somme mesurée A+B par rapport à l'addition parfaite |A|+|B|, autour de fc (dB, ≥ 0 en théorie)."""
    f = _meme_grille(a, b, ab)
    _, A = complexe(a)
    _, B = complexe(b)
    _, S = complexe(ab)
    k = _masque(f, fc, DEMI_FENETRE_OCT) & _fiable(f, a, b, ab)
    if not k.any():
        raise ValueError(f"aucune bande fiable autour de {fc:g} Hz (cohérence < {COHERENCE_MIN})")
    ideal = np.mean((np.abs(A[k]) + np.abs(B[k])) ** 2)
    reel = np.mean(np.abs(S[k]) ** 2)
    return float(10 * math.log10(ideal / max(reel, 1e-30)))


def somme_complexe(a: dict, b: dict) -> dict:
    """A+B calculé (pour comparer à la paire mesurée, ou quand seule la mesure des voies seules existe)."""
    f = _meme_grille(a, b)
    S = complexe(a)[1] + complexe(b)[1]
    return {"f_hz": f, "mag_db": 20 * np.log10(np.maximum(np.abs(S), 1e-15)), "phase_deg": np.rad2deg(np.angle(S))}


def ecart_phase_deg(a: dict, b: dict, fc: float) -> float:
    f = _meme_grille(a, b)
    A, B = complexe(a)[1], complexe(b)[1]
    i = int(np.argmin(np.abs(np.log2(f / fc))))
    return float(np.rad2deg(np.angle(A[i] / B[i])))


def _ecart_cible(m: dict, k: np.ndarray, style: str) -> np.ndarray:
    f = np.asarray(m["f_hz"], dtype=float)
    mag = np.asarray(m["mag_db"], dtype=float)
    cible = np.array([rp.cible_style(style, x) for x in f])
    e = (mag - cible)[k]
    return e - e.mean()


def ondulation_db(ab: dict, fc: float, style: str = "plat") -> float:
    f = np.asarray(ab["f_hz"], dtype=float)
    k = _masque(f, fc, BANDE_RACCORD_OCT) & _fiable(f, ab)
    if k.sum() < 3:
        raise ValueError(f"trop peu de points fiables sur [fc/2, 2·fc] à {fc:g} Hz")
    return float(np.sqrt(np.mean(_ecart_cible(ab, k, style) ** 2)))


def regularite_db(paires_par_position: list[dict], fc: float) -> float:
    """Écart-type entre positions (dB), moyenné sur [fc/2, 2·fc] ; chaque position ramenée à sa moyenne de bande."""
    if len(paires_par_position) < 2:
        return 0.0
    f = _meme_grille(*paires_par_position)
    k = _masque(f, fc, BANDE_RACCORD_OCT) & _fiable(f, *paires_par_position)
    if k.sum() < 3:
        raise ValueError(f"trop peu de points fiables communs sur [fc/2, 2·fc] à {fc:g} Hz")
    courbes = []
    for m in paires_par_position:
        x = np.asarray(m["mag_db"], dtype=float)[k]
        courbes.append(x - x.mean())
    return float(np.mean(np.std(np.vstack(courbes), axis=0)))


def ecart_cible_db(systeme: dict, style: str = "plat") -> float:
    f = np.asarray(systeme["f_hz"], dtype=float)
    k = (f >= BANDE_SYSTEME_HZ[0]) & (f <= BANDE_SYSTEME_HZ[1]) & _fiable(f, systeme)
    if k.sum() < 3:
        raise ValueError("trop peu de points fiables sur 40 Hz-16 kHz")
    return float(np.sqrt(np.mean(_ecart_cible(systeme, k, style) ** 2)))


def meilleur_delai(a: dict, b: dict, fc: float, plage_ms: float = 2.0, pas_ms: float = 0.01) -> dict:
    """Délai (ms) à AJOUTER à B (négatif = avancer B) pour mettre B en phase avec A autour de fc. Règle de la méthode :
    TEMPS D'ARRIVÉE D'ABORD, PHASE ENSUITE.
      1. grossier : retard de groupe de l'interspectre A·B* pondéré par min(|A|,|B|)² sur ±1/2 octave = écart des temps
         d'arrivée, sans ambiguïté de cycle (les sorties passe-bas et passe-haut d'un LR ont le même retard de groupe) ;
      2. fin : efficacité de sommation |A+B|²/(|A|+|B|)² maximale à ±1/4 de période autour du grossier ;
      3. inversion de B proposée seulement si elle fait nettement mieux (+0,05 d'efficacité) à ±1/2 période : dans la
         seule bande où les deux voies pèsent, « inversé + une demi-période » ressemble beaucoup à « en phase »
         (constaté en simulation sur MEDIUM/HIGH à 1410 Hz : 0,984 contre 0,980 sans l'étape 1).
    Limite : l'étape 1 est biaisée par les filtres non complémentaires (HPF d'une voie médium, etc.) ; si l'erreur de
    centre acoustique dépasse ±1/2 période au raccord (≈ 12 cm à 1,4 kHz), la phase peut converger sur un mauvais cycle.
    En réel, l'IR de REW (rew.aligner) prime ; ceci sert de contre-vérification et de moteur au simulateur."""
    f = _meme_grille(a, b)
    A, B = complexe(a)[1], complexe(b)[1]
    k = _masque(f, fc, 0.5) & _fiable(f, a, b)
    if k.sum() < 3:
        raise ValueError(f"trop peu de points fiables autour de {fc:g} Hz pour aligner")
    fk, Ak, Bk = f[k], A[k], B[k]
    w = np.minimum(np.abs(Ak), np.abs(Bk)) ** 2
    ph = np.unwrap(np.angle(Ak * np.conj(Bk)))
    tau = -np.gradient(ph, 2 * np.pi * fk)                      # s ; = tA − tB
    grossier = float(np.clip(np.sum(w * tau) / np.sum(w) * 1000, -plage_ms, plage_ms))
    periode = 1000.0 / fc
    ideal = (np.abs(Ak) + np.abs(Bk)) ** 2

    def chercher(s: float, demi: float) -> tuple[float, float]:
        deltas = np.arange(grossier - demi, grossier + demi + pas_ms / 2, pas_ms)
        deltas = deltas[np.abs(deltas) <= plage_ms + 1e-9]
        if not len(deltas):
            return 0.0, grossier
        rot = np.exp(-2j * np.pi * np.outer(deltas / 1000, fk))
        e = np.mean(np.abs(Ak[None, :] + s * Bk[None, :] * rot) ** 2 / ideal[None, :], axis=1)
        i = int(np.argmax(e))
        return float(e[i]), float(deltas[i])

    e_n, d_n = chercher(1.0, periode / 4)
    e_i, d_i = chercher(-1.0, periode / 2)
    inv = e_i > e_n + 0.05
    return {"delai_b_ms": round(d_i if inv else d_n, 3), "inverser_b": inv, "fc_hz": fc,
            "delai_non_inverse_ms": round(d_n, 3), "grossier_ms": round(grossier, 3), "efficacite": round(e_i if inv else e_n, 4),
            "efficacite_inverse": round(e_i, 4), "efficacite_non_inverse": round(e_n, 4)}


def penalite(metriques_raccords: dict) -> float:
    """Σ raccords des métriques pondérées (POIDS_MESURE). Chaque métrique est bornée à 0 : une somme mesurée
    au-dessus de |A|+|B| (bruit, dérive de gain) ne doit pas avantager une variante. Une métrique absente (régularité
    à une seule position) compte 0 pour TOUTES les variantes de l'essai (même nombre de positions)."""
    return float(sum(POIDS_MESURE[k] * max(0.0, m.get(k) or 0.0) for m in metriques_raccords.values() for k in POIDS_MESURE))
