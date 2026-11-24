#!/usr/bin/env python3
"""
catalogue_venu360.py — Catalogue des blocs du VENU360 pour skills/venu360-pilotage/SKILL.md (hors ligne, lecture seule).

  python tools/catalogue_venu360.py [--autotest journal/autotest-complet-6.json]  > tableau markdown

Sources : data/venu360_description.json (auto-description machine) et, si fourni, le rapport d'autotest (preuve
d'écriture + relecture sur la vraie machine). Classe et règle de pas : celles appliquées par dbx_reglage / dbx_hiqnet.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "tools"))
import dbx_description as dd   # noqa: E402
import dbx_reglage as dr   # noqa: E402
from dbx_hiqnet import est_protection   # noqa: E402


def classe(chemin: str, desc: dict) -> str:
    if desc.get("capteur"):
        return "mètre"
    if not desc.get("actif", True):
        return "inactif"
    if dd.est_signal(chemin):
        return "signal"
    if dd.est_commande(chemin):
        return "commande"
    if chemin.lower().startswith("\\\\node\\"):
        return "système"
    if str(desc.get("type")) == "String":
        return "texte"
    return "protection" if est_protection(chemin) else "réglage"


def bloc(chemin: str) -> str:
    p = chemin.strip("\\").split("\\")
    nom = p[1] if len(p) > 1 else chemin
    return re.sub(r"\d+$", "<n>", nom) if p[0] == "Preset" else "Node\\" + nom


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--autotest")
    a = ap.parse_args()
    desc = json.loads((RACINE / "data" / "venu360_description.json").read_text(encoding="utf-8"))
    desc = {k: v for k, v in desc.items() if not k.startswith("_")}
    ok = set()
    if a.autotest and Path(a.autotest).exists():
        rap = json.loads(Path(a.autotest).read_text(encoding="utf-8"))
        ok = {l["chemin"] for l in rap.get("lignes", []) if l.get("ecriture_ok")}
    t = collections.defaultdict(lambda: {"n": 0, "classes": collections.Counter(), "ok": 0, "pas": set(), "ex": None})
    for ch, d in desc.items():
        b = t[bloc(ch)]
        b["n"] += 1
        b["classes"][classe(ch, d)] += 1
        b["ok"] += ch in ok
        r = dr.regle_pas(ch)
        if r:
            b["pas"].add(f"±{r[1]:g} {r[2]}")
        b["ex"] = b["ex"] or ch.split("\\")[-1]
    print("| Bloc | Paramètres | Classes | Pas max | Écrit + relu en vrai (autotest) | Exemple |")
    print("|---|---|---|---|---|---|")
    for nom in sorted(t):
        b = t[nom]
        cl = ", ".join(f"{k} {v}" for k, v in b["classes"].most_common())
        print(f"| {nom} | {b['n']} | {cl} | {', '.join(sorted(b['pas'])) or '—'} | "
              f"{b['ok']}/{b['n']} | `{b['ex']}` |")


if __name__ == "__main__":
    main()
