#!/usr/bin/env python3
r"""
metres.py — Relevé des mètres du VENU360 (LECTURE SEULE : aucune écriture, jamais).

  python tools/metres.py <ip> [--duree 3] [--json fichier.json]

Mètres cherchés dans chaque bloc du preset chargé : *_Level, *_Clip, *Meter, MaxInputLevel, AllMeters (RTA).
L'outil s'abonne (`sub`) à chacun pendant --duree secondes et compte les mises à jour poussées par la machine.

Constat du 24/11/2026 (firmware 2.0.5.0, générateur −30 dB 1 kHz, sorties démutées, amplis débranchés) :
  * VIVANTS par la console : Meters123 et MetersABC (entrées 1-3 et A-B-C, avant la chaîne de sortie) ;
    poussés en lignes « set "<chemin>" "<valeur>" » environ 10 fois par seconde, valeur efficace en dB
    (sinus −30 dB crête → −33,0 dB) ;
  * FIGÉS (une seule valeur, jamais mise à jour) : OutputMeters, MidMeters, InputMeters, capteurs des limiteurs
    (GainReductionMeter, ThresholdMeter, MaxInputLevel) et du compresseur. On ne peut donc PAS vérifier une sortie,
    un gain de sortie ou l'action d'un limiteur par les mètres : il faut la boucle REW (câble sortie → interface).
Usage en exploitation : vérifier qu'une entrée reçoit du signal et ne sature pas (Clip), sans rien toucher.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx_hiqnet   # noqa: E402

METRE = re.compile(r"(_Level|_Clip|Meter|MaxInputLevel|AllMeters)$")
BLOC_METRE = re.compile(r"(Meters|Limiter\d|Compressor|RTA$)")
VIVANTS_CONNUS = ("Meters123", "MetersABC")


def chemins_metres(d) -> list[str]:
    out = []
    for b in sorted(d.ls("\\\\Preset")):
        if not BLOC_METRE.search(b) or b in ("RTARouter",):
            continue
        try:
            sv = d.ls(f"\\\\Preset\\{b}\\SV")
        except ValueError:
            continue
        out += [f"\\\\Preset\\{b}\\SV\\{k}" for k in sv if METRE.search(k)]
    return out


def _valeur(texte: str) -> float | None:
    m = re.match(r"\s*([-+]?\d+(?:\.\d+)?)", str(texte))
    return float(m.group(1)) if m else None


def relever(d, chemins: list[str], duree: float = 3.0) -> dict:
    """{chemin: {premiere, derniere, min, max, maj, vivant}} — abonnement puis désabonnement, lecture seule."""
    vus: dict[str, list[str]] = {c: [] for c in chemins}
    index = {c.rstrip("\\").lower(): c for c in chemins}
    for c in chemins:
        d._envoyer(f"sub {dbx_hiqnet._q(c)}")
    fin = time.time() + duree
    while time.time() < fin:
        l = d._lire_ligne(timeout=max(0.05, fin - time.time()))
        if not l or not (l.startswith("subr") or l.startswith("set ")):
            continue
        p = l.split('"')
        if len(p) >= 4 and p[1].rstrip("\\").lower() in index:
            vus[index[p[1].rstrip("\\").lower()]].append(p[3])
    for c in chemins:
        d._envoyer(f"unsub {dbx_hiqnet._q(c)}")
    t = time.time() + 0.5
    while time.time() < t:
        d._lire_ligne(timeout=0.1)
    out = {}
    for c, v in vus.items():
        if not v:
            try:
                v = [d.get(c)]
            except ValueError as e:
                out[c] = {"erreur": str(e)}
                continue
        nums = [x for x in (_valeur(s) for s in v) if x is not None]
        out[c] = {"premiere": v[0], "derniere": v[-1], "maj": len(v),
                  "min": min(nums) if nums else None, "max": max(nums) if nums else None,
                  "vivant": len(v) > 1}
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip")
    ap.add_argument("--duree", type=float, default=3.0)
    ap.add_argument("--json")
    ap.add_argument("--port", type=int, default=dbx_hiqnet.PORT)
    ap.add_argument("--tous", action="store_true", help="afficher aussi les mètres figés")
    a = ap.parse_args()
    mdp = os.environ.get("DBX_MDP") or "administrator"
    with dbx_hiqnet.Dbx(a.ip, a.port, mdp) as d:
        r = relever(d, chemins_metres(d), a.duree)
    vivants = {c: x for c, x in r.items() if x.get("vivant")}
    print(f"{len(r)} mètres relevés pendant {a.duree:g} s : {len(vivants)} vivant(s), {len(r) - len(vivants)} figé(s)")
    for c, x in sorted(r.items()):
        if not (x.get("vivant") or a.tous):
            continue
        nom = c.split("\\")[-3] + "." + c.split("\\")[-1]
        if "erreur" in x:
            print(f"  {nom:40} ERREUR {x['erreur']}")
        else:
            plage = f"{x['min']:g} … {x['max']:g}" if x["min"] is not None else ""
            print(f"  {nom:40} {x['derniere']:>12}  {plage:>18}  {x['maj']:4d} maj  {'vivant' if x['vivant'] else 'FIGÉ'}")
    if a.json:
        Path(a.json).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Écrit : {a.json}")


if __name__ == "__main__":
    main()
