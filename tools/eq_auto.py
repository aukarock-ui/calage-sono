#!/usr/bin/env python3
"""
eq_auto.py — Ajustement automatique de bandes d'EQ sur un écart mesuré (fonctions pures, aucun matériel).

Filtres : formes analogiques RBJ (Audio EQ Cookbook, R. Bristow-Johnson) — Bell, Low Shelf, High Shelf (S = 1) ;
même paramétrage que le PEQ du VENU360 (fréquence, gain, Q).

Méthode (règles de calage, McCarthy ch. 11 « equalization » ; méthode-calage) :
  - on corrige l'ÉCART à la cible, lissé au 1/6 d'octave, seulement sur les fréquences FIABLES (masque fourni :
    présentes à toutes les positions de mesure, faible écart entre positions) ;
  - glouton : à chaque tour, le plus grand écart restant ; largeur lue à mi-hauteur de l'écart -> Q (borné) ;
  - CREUX préférés aux bosses : bosse ≤ +3 dB (défaut), jamais sur un creux étroit (< 1/6 d'octave : annulation,
    qu'aucun gain ne remplit) ni sous `bosse_interdite_sous_hz` (excursion d'un bass-reflex sous l'accord) ;
  - affinage final des gains par moindres carrés bornés (fréquences et Q figés, signe de chaque bande conservé) ;
  - arrêt sous 1 dB d'écart restant ou au nombre maximal de bandes.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import signal
from scipy.optimize import lsq_linear

SEUIL_DB = 1.0
LARGEUR_MIN_BOSSE_OCT = 1 / 6


def bande(type_: str, hz: float, gain_db: float, q: float, f: np.ndarray) -> np.ndarray:
    """Réponse complexe d'une bande (analogique RBJ)."""
    w0 = 2 * math.pi * hz
    A = 10 ** (gain_db / 40)
    if type_ == "Bell":
        b = [1, w0 * A / q, w0 ** 2]
        a = [1, w0 / (A * q), w0 ** 2]
    elif type_ in ("Low Shelf", "High Shelf"):
        s = math.sqrt(A) / 0.7071          # S = 1
        if type_ == "Low Shelf":
            b = [A, A * s * w0, A * A * w0 ** 2]
            a = [A, s * w0, w0 ** 2]
        else:
            b = [A * A, A * s * w0, A * w0 ** 2]
            a = [1, s * w0, A * w0 ** 2]
    else:
        raise ValueError(f"type de bande inconnu : {type_}")
    return signal.freqs(b, a, 2 * math.pi * np.asarray(f, dtype=float))[1]


def reponse(bandes: list[dict], f: np.ndarray) -> np.ndarray:
    H = np.ones(len(f), dtype=complex)
    for b in bandes or []:
        H = H * bande(b["type"], b["hz"], b["gain_db"], b["q"], f)
    return H


def lisser(f: np.ndarray, x: np.ndarray, frac: int = 6) -> np.ndarray:
    """Moyenne glissante sur ±1/(2·frac) d'octave (en dB)."""
    lf = np.log2(f)
    out = np.empty_like(x)
    for i, c in enumerate(lf):
        k = np.abs(lf - c) <= 0.5 / frac
        out[i] = x[k].mean()
    return out


def _q_de_largeur(oct_: float) -> float:
    n = 2 ** oct_
    return math.sqrt(n) / (n - 1)


def _largeur_brute(lf: np.ndarray, brut: np.ndarray, zone: np.ndarray) -> float:
    """Largeur à mi-profondeur du creux NON lissé le plus profond de la zone (octaves)."""
    idx = np.flatnonzero(zone)
    j = int(idx[np.argmin(brut[idx])])
    seuil = brut[j] / 2
    g, d = j, j
    while g > 0 and brut[g - 1] <= seuil:
        g -= 1
    while d < len(lf) - 1 and brut[d + 1] <= seuil:
        d += 1
    return float(max(lf[d] - lf[g], 1 / 48))


def ajuster(f, ecart_db, fiable=None, n_max: int = 6, bosse_max_db: float = 3.0, coupe_max_db: float = 12.0,
            q_bornes=(0.7, 6.0), bosse_interdite_sous_hz: float | None = None, bosses_autorisees: bool = True) -> dict:
    """ecart_db = mesure − cible (dB, + = trop fort). Renvoie {bandes, residu_rms_db, avant_rms_db, refus}."""
    f = np.asarray(f, dtype=float)
    brut = np.asarray(ecart_db, dtype=float)
    e = lisser(f, brut)
    ok = np.ones(len(f), bool) if fiable is None else np.asarray(fiable, bool).copy()
    if ok.sum() < 3:
        raise ValueError("trop peu de fréquences fiables pour ajuster une EQ")
    brut = brut - e[ok].mean()
    e = e - e[ok].mean()                     # le niveau moyen relève du gain de voie, pas de l'EQ
    reste = e.copy()
    exclus = ~ok
    bandes, refus = [], []
    lf = np.log2(f)
    while len(bandes) < n_max:
        r = np.where(exclus, 0.0, reste)
        i = int(np.argmax(np.abs(r)))
        pic = r[i]
        if abs(pic) < SEUIL_DB:
            break
        # largeur à mi-hauteur de l'écart, du côté de la bosse/du creux
        g, d = i, i
        while g > 0 and np.sign(reste[g - 1]) == np.sign(pic) and abs(reste[g - 1]) >= abs(pic) / 2 and ok[g - 1]:
            g -= 1
        while d < len(f) - 1 and np.sign(reste[d + 1]) == np.sign(pic) and abs(reste[d + 1]) >= abs(pic) / 2 and ok[d + 1]:
            d += 1
        largeur = max(lf[d] - lf[g], 1 / 24)
        zone = (lf >= lf[g] - 1 / 48) & (lf <= lf[d] + 1 / 48)
        gain = -pic
        if gain > 0:                                  # creux mesuré -> bosse d'EQ
            motif = None
            if not bosses_autorisees:
                motif = "bosses interdites (ordre PEQ/limiteur non établi ou PEQ après le limiteur)"
            elif _largeur_brute(lf, brut, zone) < LARGEUR_MIN_BOSSE_OCT:   # le lissage élargit les creux
                motif = f"creux étroit ({_largeur_brute(lf, brut, zone):.2f} oct < 1/6, non lissé) : annulation, ne se remplit pas"
            elif bosse_interdite_sous_hz and f[i] < bosse_interdite_sous_hz:
                motif = f"sous {bosse_interdite_sous_hz:g} Hz : bosse interdite (excursion bass-reflex)"
            if motif:
                refus.append({"hz": round(float(f[i]), 1), "ecart_db": round(float(pic), 1), "motif": motif})
                exclus |= zone
                continue
            gain = min(gain, bosse_max_db)
        else:
            gain = max(gain, -coupe_max_db)
        q = float(np.clip(_q_de_largeur(largeur), *q_bornes))
        b = {"type": "Bell", "hz": float(f[i]), "gain_db": float(gain), "q": q}
        bandes.append(b)
        reste = reste + 20 * np.log10(np.abs(bande("Bell", b["hz"], b["gain_db"], q, f)))
        exclus |= zone & (np.abs(reste) < SEUIL_DB)
    if bandes:      # affinage des gains (forme ≈ linéaire en gain dans le domaine dB pour une bande isolée)
        formes = np.column_stack([20 * np.log10(np.abs(bande("Bell", b["hz"], 1.0, b["q"], f))) for b in bandes])
        lo = [(-coupe_max_db if b["gain_db"] < 0 else 0.0) for b in bandes]
        hi = [(0.0 if b["gain_db"] < 0 else bosse_max_db) for b in bandes]
        sol = lsq_linear(formes[ok], -e[ok], bounds=(lo, hi))
        for b, g_ in zip(bandes, sol.x):
            b["gain_db"] = float(g_)
        bandes = [b for b in bandes if abs(b["gain_db"]) >= 0.3]
    corr = 20 * np.log10(np.abs(reponse(bandes, f))) if bandes else np.zeros(len(f))
    res = e + corr
    out = [{"type": "Bell", "hz": round(b["hz"], 1), "gain_db": round(b["gain_db"], 1), "q": round(b["q"], 2)}
           for b in sorted(bandes, key=lambda b: b["hz"])]
    return {"bandes": out, "avant_rms_db": round(float(np.sqrt(np.mean(e[ok] ** 2))), 2),
            "residu_rms_db": round(float(np.sqrt(np.mean(res[ok] ** 2))), 2), "refus": refus}


def pas_vers(actuelles: list[dict], visees: list[dict], pas_db: float = 3.0) -> list[dict]:
    """Bandes à écrire pour aller des actuelles aux visées par pas ≤ pas_db par bande (même fréquence/Q visés ;
    une bande absente des actuelles part de 0 dB)."""
    out = []
    for i, v in enumerate(visees):
        a = actuelles[i]["gain_db"] if i < len(actuelles) and abs(actuelles[i]["hz"] - v["hz"]) < 0.5 else 0.0
        g = a + max(-pas_db, min(pas_db, v["gain_db"] - a))
        out.append(dict(v, gain_db=round(g, 1)))
    return out
