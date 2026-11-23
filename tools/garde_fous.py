"""Garde-fous communs aux clients de processeurs (dbx, DCX2496, t.racks).

Règles de CLAUDE.md transformées en contrôles de code :
- jamais un seuil de limiteur moins protecteur que la valeur calculée par calc_systeme.py (plafond) ;
- valeurs vérifiées (plage, liste) avant envoi, jamais bornées en silence ;
- chaînes envoyées sur une ligne de protocole : aucun caractère de contrôle (pas d'injection CR/LF).
"""
from __future__ import annotations

import re

TOLERANCE_PLAFOND_DB = 0.05   # arrondi d'affichage des seuils (0,1 dB)


def refuser_controle(*chaines: str) -> None:
    for c in chaines:
        if c is not None and re.search(r"[\x00-\x1f\x7f]", str(c)):
            raise ValueError(f"caractère de contrôle (CR, LF…) refusé dans {c!r}")


_FACTEURS = {"": 1.0, "hz": 1.0, "khz": 1000.0, "s": 1000.0, "ms": 1.0, "us": 0.001, "µs": 0.001,
             "db": 1.0, "dbfs": 1.0, "dbu": 1.0, "deg": 1.0, "db/s": 1.0, "db/oct": 1.0}


def nombre(texte) -> float | None:
    """Nombre d'une valeur texte, ramené à l'unité de base (Hz, ms, dB). None si pas de nombre lisible."""
    if isinstance(texte, (int, float)):
        return float(texte)
    m = re.fullmatch(r"\s*([-+]?\d+(?:[.,]\d+)?)\s*([a-zµ/]*)\s*", str(texte or ""), re.I)
    if not m:
        return None
    u = m.group(2).lower()
    if u not in _FACTEURS:
        return None
    return float(m.group(1).replace(",", ".")) * _FACTEURS[u]


def verifier_plafond(valeur: float, config: str | None, voie: str | None, unite: str, depasser: bool) -> str:
    """Refuse (ValueError) un seuil plus haut que le plafond calculé. Renvoie une ligne à journaliser."""
    if not config or not voie:
        raise ValueError("seuil de limiteur : --config <data/configs/…yaml> et --voie <NOM> obligatoires "
                         "(le plafond calculé par calc_systeme.py est vérifié avant écriture)")
    from calc_systeme import plafond_limiteur          # même dossier tools/
    p = plafond_limiteur(config, voie)
    if p["unite"] != unite:
        raise ValueError(f"unité du plafond ({p['unite']}) ≠ unité du processeur ({unite}) : vérifier la config "
                         f"(processeur {p['processeur']})")
    if valeur > p["seuil"] + TOLERANCE_PLAFOND_DB:
        msg = (f"seuil demandé {valeur:g} {unite} AU-DESSUS du plafond calculé {p['seuil']:g} {unite} "
               f"({config}, voie {voie}) : moins protecteur")
        if not depasser:
            raise ValueError(msg + ". Refusé. Dérogation : --depasser-plafond, sur décision explicite de Louis.")
        return "DEROGATION PLAFOND : " + msg
    return f"plafond OK : {valeur:g} ≤ {p['seuil']:g} {unite} ({voie})"


def autotest() -> None:
    assert nombre("2.5kHz") == 2500 and nombre("-23.1dB") == -23.1 and nombre("1s") == 1000 and nombre("Out") is None
    for mauvais in ("a\nset x", "b\r", "c\x00"):
        try:
            refuser_controle(mauvais)
            raise AssertionError(mauvais)
        except ValueError:
            pass
    refuser_controle("\\\\Preset\\Limiter1\\SV\\Threshold", "-20.0dB")
    print("autotest garde_fous : OK")


if __name__ == "__main__":
    autotest()
