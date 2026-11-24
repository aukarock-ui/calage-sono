#!/usr/bin/env python3
"""
essai_coupures.py — Essai RÉEL des variantes de coupure au micro (REW sweep, un micro), choix de la meilleure par la
mesure, et historique prédit/mesuré qui sert à recaler les poids de coupures.py (`coupures.py --calibrer`).

Trois étapes, chacune relançable ; un dossier d'essai garde l'état entre elles (mesures/essai-<sys>-<date>/) :
  1. preparer  <config> --style techno --positions P1:20x1.7,P2:20x1.2,P3:10x1.7 [--variantes 3]
               [--accepter-non-verifie]
     -> variantes = config actuelle (témoin) + meilleures de coupures.optimiser ; une config par variante
        (data/configs/_essai/, gitignoré) et son plan (preset_auto). Aucun accès machine, aucun son.
  2. mesurer   <dossier> --position P1 --ip <VENU360> --protection-validee --signal-autorise --appliquer [--niveau-max -12]
               [--simulation]
     -> UNE position par lancement (un seul micro : Louis le déplace entre deux lancements et dit « en place »).
        Pour chaque variante : plan écrit sorties mutées (dbx_plan.appliquer), chaque voie seule, alignement à
        chaque raccord (1re position : IR d'abord puis phase, ±0,5 ms par écriture puis remesure), chaque paire,
        système complet. En fin de lancement (même sur erreur ou Ctrl-C) : 6 sorties mutées, valeurs touchées
        remises à leur état du DÉBUT DE CE LANCEMENT (relu à chaque lancement), sauf une protection moins sûre (seuil
        au-dessus du plafond calculé, limiteur Off) qui est signalée et non réécrite ; sorties LAISSÉES MUTÉES ;
        code de sortie non nul si le rétablissement n'est pas complet.
  3. conclure  <dossier>
     -> métriques (analyse_raccord), classement par score mesuré, rapport .md/.yaml, ajout à l'historique
        data/apprentissage/coupures.yaml, fichier de recalage proposé (format preset_auto --recaler). Aucun accès
        machine. La gagnante n'est écrite (coupures.py --ecrire puis appliquer-plan) qu'avec le « go » de Louis.

GARDE-FOUS (CLAUDE.md) :
  - aucun sweep sans --signal-autorise, passé seulement après le « go » de Louis (niveau sûr, personne exposé, pas
    de concert) ; niveau de départ min(−20 dBFS, --niveau-max), +3 dB tant que le rapport signal/bruit est < 15 dB,
    jamais au-dessus de --niveau-max (≤ −10 dBFS, plafond REW du kit), niveau propre à chaque voie (le HF ne reçoit pas
    le niveau du sub) ; arrêt sur écrêtage forcé par rew_client ;
  - --protection-validee obligatoire (le plan écrit limiteurs et filtres) ; limiteurs du plan actifs en solo ;
  - une seule sortie ouverte en solo, état des mutes relu à chaque changement ;
  - --simulation : moteur acoustique simulé (aucun son) ; les écritures processeur sont réelles mais les sorties ne
    sont JAMAIS démutées (le moteur simulé lit un état de mutes virtuel) ;
  - limites connues : SIGTERM / fermeture de console ne sont pas interceptés (seul Ctrl-C l'est) ; un sweep REW en
    cours n'est pas arrêté par Ctrl-C (les sorties sont mutées, REW finit son sweep dans le vide).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyse_raccord as ar       # noqa: E402
import calc_systeme as cs          # noqa: E402
import coupures                    # noqa: E402
import dbx_description as dd       # noqa: E402
import reglages_pile as rp         # noqa: E402
from banc import (P, Annonce, NIVEAU_DEPART_DBFS, NIVEAU_PLAFOND_DBFS, PAS_DELAI_MS, Annule, MoteurREW,  # noqa: E402,F401
                  MoteurSimule, Seance, charger_etat, chemins_suivis as _chemins_suivis, cles_raccords as _cles_raccords,
                  ecrire_delai, lire_positions, ouvrir, retablir, retablissement_complet, sauver_etat,
                  _ecrire_atomique)

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_VARIANTES = RACINE / "data" / "configs" / "_essai"
HISTORIQUE = RACINE / "data" / "apprentissage" / "coupures.yaml"

# ---------------------------------------------------------------- 1. preparer
def preparer(chemin_config: str, style: str | None, positions: list[dict], n_variantes: int = 3,
             accepter_non_verifie: bool = False, dossier: str | Path | None = None) -> Path:
    import preset_auto
    if not 1 <= n_variantes <= 3:
        raise ValueError("--variantes : 1 à 3 (au-delà, l'essai devient trop long pour être départagé)")
    if not positions:
        raise ValueError("au moins une position de micro")
    src = Path(chemin_config).resolve()
    p1 = positions[0]
    r = coupures.optimiser(str(src), style, top=max(5, n_variantes + 1), hauteur_ecoute_m=p1["hauteur_m"],
                           distance_ref_m=p1["distance_m"])
    style = r["style"]
    choisies = []
    if r["actuel"]:
        choisies.append(("v0", r["actuel"], True))
    vus = {tuple(r["actuel"]["coupures_hz"].values())} if r["actuel"] else set()
    for var in r["classement"]:
        cle = tuple(var["coupures_hz"].values())
        if cle in vus:
            continue
        vus.add(cle)
        choisies.append((f"v{len(choisies) if r['actuel'] else len(choisies) + 1}", var, False))
        if len(choisies) >= n_variantes + (1 if r["actuel"] else 0):
            break
    date = dt.datetime.now().strftime("%Y%m%d-%H%M")
    dossier = Path(dossier or RACINE / "mesures" / f"essai-{src.stem}-{date}")
    if (dossier / "etat.json").exists():
        raise ValueError(f"{dossier} contient déjà un essai")
    (dossier / "plans").mkdir(parents=True, exist_ok=True)
    variantes = []
    for ident, var, temoin in choisies:
        dst = DOSSIER_VARIANTES / f"{src.stem}-{dossier.name}-{ident}.yaml"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        rc = cs.chemin_recalage(src)
        if rc.exists():
            shutil.copyfile(rc, cs.chemin_recalage(dst))
        if not temoin:   # même texte que la source : le résultat de l'optimiseur vaut pour la copie
            rv = dict(r, config=str(dst.resolve()), empreinte=coupures._empreinte(str(dst)), classement=[var])
            coupures.ecrire_coupures(str(dst), rv, 1)
        plan = preset_auto.construire_plan(str(dst), style, p1["distance_m"], p1["hauteur_m"], accepter_non_verifie)
        if plan["bloquant"]:
            dst.unlink()
            continue
        fp = dossier / "plans" / f"{ident}.json"
        fp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        variantes.append({"id": ident, "temoin": temoin, "coupures_hz": var["coupures_hz"], "config": str(dst),
                          "plan": str(fp), "predit": {k: var.get(k) for k in
                                                      ("puissance_db", "lobes_db", "directivite_db", "score")}})
    if len(variantes) < 2:
        for v in variantes:
            Path(v["config"]).unlink(missing_ok=True)
            cs.chemin_recalage(v["config"]).unlink(missing_ok=True)
        raise ValueError("moins de deux variantes valides : rien à départager")
    etat = {"version": 1, "config": str(src), "systeme": r["systeme"], "style": style, "positions": positions,
            "variantes": variantes, "accepte_non_verifie": accepter_non_verifie,
            "poids_predits": coupures.poids(), "cree": dt.datetime.now().isoformat(timespec="seconds"),
            "positions_faites": [], "niveau_dbfs": None, "delais_alignes": {}, "instantane": None, "alertes": []}
    sauver_etat(dossier, etat)
    return dossier



def mesurer_position(dossier: str | Path, nom_position: str, d, moteur_pour, journal, ip: str,
                     protection_validee: bool, signal_autorise: bool, niveau_max: float = -12.0,
                     appliquer: bool = False) -> dict:
    """Mesure toutes les variantes à UNE position. `moteur_pour(position, sorties)` crée le moteur (REW ou simulé).
    Garde-fous : banc.Seance (contrôles, instantané, re-mute sur toute erreur, rétablissement)."""
    import dbx_plan
    etat = charger_etat(dossier)
    pos = next((p for p in etat["positions"] if p["nom"] == nom_position), None)
    if pos is None:
        raise ValueError(f"position {nom_position} inconnue ({', '.join(p['nom'] for p in etat['positions'])})")
    if nom_position in etat["positions_faites"]:
        raise ValueError(f"position {nom_position} déjà mesurée dans cet essai")
    plans = {v["id"]: json.loads(Path(v["plan"]).read_text(encoding="utf-8")) for v in etat["variantes"]}
    sorties = {x["sortie"]: x["nom"] for x in next(iter(plans.values()))["voies"]}
    seance = Seance(d, moteur_pour(pos, sorties), dossier, etat, pos, list(plans.values()), journal, ip, niveau_max,
                    protection_validee, signal_autorise, lambda e: sauver_etat(dossier, e), appliquer=appliquer)
    premiere = not etat["positions_faites"]
    with seance as s:
        for v in etat["variantes"]:
            plan = plans[v["id"]]
            r = dbx_plan.appliquer(d, plan, journal, ip)
            if not r["conforme"]:
                raise Annule(f"variante {v['id']} : plan non conforme ({r['arret'] or r['echecs'] or r['refus']})")
            par_nom = {x["nom"]: x for x in plan["voies"]}
            if not premiere:      # délais retrouvés à la 1re position
                for nom, ms in etat["delais_alignes"][v["id"]].items():
                    ecrire_delai(d, par_nom[nom]["sortie"], ms, journal, ip)
            tag = f"{v['id']}_{nom_position}"
            s.rampe(plan, tag)
            solos = s.solos(plan, tag)
            if premiere:
                etat["delais_alignes"][v["id"]] = s.aligner(plan, v["coupures_hz"], solos, tag, v["id"])
                sauver_etat(dossier, etat)
            for bas, haut in _cles_raccords(plan):
                s.mesure(f"{tag}_{bas}+{haut}", {par_nom[bas]["sortie"], par_nom[haut]["sortie"]}, s.niv(bas, haut))
            s.mesure(f"{tag}_systeme", {x["sortie"] for x in plan["voies"]}, s.niv(*par_nom))
        etat["positions_faites"].append(nom_position)
        etat["alertes"] += s.resultat["alertes"]
        sauver_etat(dossier, etat)
    return s.resultat


# ---------------------------------------------------------------- 3. conclure
def conclure(dossier: str | Path, ajouter_historique: bool = True) -> dict:
    dossier = Path(dossier)
    etat = charger_etat(dossier)
    faites = [p for p in etat["positions"] if p["nom"] in etat["positions_faites"]]
    if not faites:
        raise ValueError("aucune position mesurée")
    rep = dossier / "mesures"

    def lire(nom):
        return json.loads((rep / f"{nom}.json").read_text(encoding="utf-8"))

    p1 = faites[0]["nom"]
    resultats = []
    for v in etat["variantes"]:
        plan = json.loads(Path(v["plan"]).read_text(encoding="utf-8"))
        rac = {}
        for bas, haut in _cles_raccords(plan):
            k = f"{bas}/{haut}"
            fc = v["coupures_hz"][k]
            t = f"{v['id']}_{p1}"
            a, b, ab = lire(f"{t}_{haut}"), lire(f"{t}_{bas}"), lire(f"{t}_{bas}+{haut}")
            paires = [lire(f"{v['id']}_{p['nom']}_{bas}+{haut}") for p in faites]
            rac[k] = {"fc_hz": fc, "perte_somme_db": round(ar.perte_somme_db(a, b, ab, fc), 2),
                      "ecart_phase_deg": round(ar.ecart_phase_deg(a, b, fc), 1),
                      "ondulation_db": round(ar.ondulation_db(ab, fc, etat["style"]), 2),
                      "regularite_db": round(ar.regularite_db(paires, fc), 2) if len(paires) > 1 else None}
        pen = ar.penalite(rac)
        p_calc = v["predit"]["puissance_db"]
        pw = etat["poids_predits"]
        resultats.append({"id": v["id"], "temoin": v["temoin"], "coupures_hz": v["coupures_hz"], "predit": v["predit"],
                          "penalite_predite_db": round(pw["lobes"] * (v["predit"]["lobes_db"] or 0)
                                                       + pw["directivite"] * (v["predit"]["directivite_db"] or 0), 2),
                          "mesure": {"raccords": rac, "penalite_db": round(pen, 2),
                                     "ecart_cible_db": round(ar.ecart_cible_db(lire(f"{v['id']}_{p1}_systeme"),
                                                                               etat["style"]), 2),
                                     "score": round(p_calc - pen, 2) if p_calc is not None else None},
                          "delais_alignes_ms": etat["delais_alignes"].get(v["id"])})
    resultats.sort(key=lambda x: -(x["mesure"]["score"] if x["mesure"]["score"] is not None else -1e9))
    out = {"dossier": str(dossier), "systeme": etat["systeme"], "config": etat["config"], "style": etat["style"],
           "date": dt.date.today().isoformat(), "positions": faites, "niveau_dbfs": etat["niveau_dbfs"],
           "variantes": resultats, "gagnante": resultats[0]["id"], "alertes": etat["alertes"],
           "simulation": bool(etat.get("simulees")), "regularite_mesuree": len(faites) > 1}
    _ecrire_atomique(dossier / "resultat.yaml", yaml.safe_dump(out, allow_unicode=True, sort_keys=False))
    _ecrire_atomique(dossier / "rapport.md", rapport(out))
    out["recalage_propose"] = str(_fichier_recalage(dossier, etat, resultats))
    if ajouter_historique and not out["simulation"]:
        ajouter_a_historique(out)
    return out


def _fichier_recalage(dossier: Path, etat: dict, resultats: list[dict]) -> Path:
    """Délais alignés du TÉMOIN (config actuelle) au format de preset_auto --recaler (centres acoustiques)."""
    t = next((x for x in resultats if x["temoin"]), None)
    p1 = next(p for p in etat["positions"] if p["nom"] in etat["positions_faites"])
    f = dossier / "recalage-propose.yaml"
    if not t or not t["delais_alignes_ms"]:
        return f
    doc = {"date": dt.date.today().isoformat(), "source": f"essai_coupures {dossier.name} (témoin, {p1['nom']})",
           "point": {"distance_m": p1["distance_m"], "hauteur_m": p1["hauteur_m"]},
           "voies": {n: {"delai_mesure_ms": round(ms, 2)} for n, ms in t["delais_alignes_ms"].items()}}
    doc["simulation"] = bool(etat.get("simulees"))
    _ecrire_atomique(f, yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    return f


def ajouter_a_historique(out: dict, fichier: Path | None = None) -> None:
    """Ajout seulement (jamais de réécriture d'une entrée) ; le même essai n'est pas ajouté deux fois ; jamais une
    simulation (elle fausserait l'apprentissage des poids)."""
    if out.get("simulation"):
        raise ValueError("essai simulé : jamais ajouté à l'historique d'apprentissage")
    fichier = Path(fichier or HISTORIQUE)
    h = yaml.safe_load(fichier.read_text(encoding="utf-8")) if fichier.exists() else None
    h = h or {"essais": []}
    if any(e.get("dossier") == out["dossier"] for e in h["essais"]):
        raise ValueError(f"essai {out['dossier']} déjà dans l'historique")
    h["essais"].append({k: out[k] for k in ("dossier", "date", "systeme", "config", "style", "positions",
                                            "niveau_dbfs", "regularite_mesuree")}
                       | {"variantes": [{"id": v["id"], "temoin": v["temoin"], "coupures_hz": v["coupures_hz"],
                                         "predit": v["predit"], "penalite_predite_db": v["penalite_predite_db"],
                                         "penalite_mesuree_db": v["mesure"]["penalite_db"],
                                         "score_mesure": v["mesure"]["score"]} for v in out["variantes"]]})
    _ecrire_atomique(fichier, yaml.safe_dump(h, allow_unicode=True, sort_keys=False))


def rapport(out: dict) -> str:
    L = [f"# Essai des coupures au micro — {out['systeme']} · style {out['style']} · {out['date']}", "",
         f"Positions : {', '.join(f'{p['nom']} ({p['distance_m']:g} m, {p['hauteur_m']:g} m)' for p in out['positions'])}"
         f" · niveaux {out['niveau_dbfs']} dBFS", "",
         "| Variante | Coupures | Puissance calc. (dB) | Pénalité prédite | Pénalité mesurée | Score mesuré |",
         "|---|---|---|---|---|---|"]
    for v in out["variantes"]:
        cp = " · ".join(f"{k} {f:g}" for k, f in v["coupures_hz"].items())
        L.append(f"| {v['id']}{' (actuelle)' if v['temoin'] else ''} | {cp} | {v['predit']['puissance_db']} | "
                 f"{v['penalite_predite_db']} | {v['mesure']['penalite_db']} | **{v['mesure']['score']}** |")
    L += ["", "Détail par raccord (première position ; régularité = entre positions) :", ""]
    for v in out["variantes"]:
        for k, m in v["mesure"]["raccords"].items():
            L.append(f"- {v['id']} {k} @ {m['fc_hz']:g} Hz : perte de somme {m['perte_somme_db']} dB, phase "
                     f"{m['ecart_phase_deg']:+}°, ondulation {m['ondulation_db']} dB, régularité "
                     f"{m['regularite_db'] if m['regularite_db'] is not None else 'n/a (1 position)'} dB")
    if not out["regularite_mesuree"]:
        L += ["", "**Une seule position : la régularité en hauteur n'est pas mesurée (lobes non départagés).**"]
    if out["alertes"]:
        L += ["", "Alertes :"] + [f"- {a}" for a in out["alertes"]]
    g = out["variantes"][0]
    L += ["", f"Gagnante à la mesure : **{g['id']}** ({' · '.join(f'{k} {f:g} Hz' for k, f in g['coupures_hz'].items())})."
          " À écouter avec un morceau de référence avant décision. Écriture : `coupures.py <config> --ecrire` "
          "(variante choisie) puis `preset_auto` et `appliquer-plan`, avec le « go » de Louis."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- CLI
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("preparer")
    a1.add_argument("config")
    a1.add_argument("--style")
    a1.add_argument("--positions", required=True)
    a1.add_argument("--variantes", type=int, default=3)
    a1.add_argument("--accepter-non-verifie", action="store_true")
    a2 = sub.add_parser("mesurer")
    a2.add_argument("dossier")
    a2.add_argument("--position", required=True)
    a2.add_argument("--ip", required=True)
    a2.add_argument("--port", type=int, default=19272)
    a2.add_argument("--rew", default="localhost:4735")
    a2.add_argument("--niveau-max", type=float, default=-12.0)
    a2.add_argument("--protection-validee", action="store_true")
    a2.add_argument("--signal-autorise", action="store_true")
    a2.add_argument("--appliquer", action="store_true", help="sans lui : annonce de ce qui serait écrit, rien n'est touché")
    a2.add_argument("--simulation", action="store_true", help="acoustique simulée (aucun son), écritures réelles")
    a2.add_argument("--erreur-sim", default="", help="simulation : décalages de centre acoustique, ex. SUB:12,MEDIUM:-6 (cm)")
    a3 = sub.add_parser("conclure")
    a3.add_argument("dossier")
    a = ap.parse_args()
    try:
        if a.cmd == "preparer":
            d = preparer(a.config, a.style, lire_positions(a.positions), a.variantes, a.accepter_non_verifie)
            e = charger_etat(d)
            print(f"Essai préparé : {d}")
            for v in e["variantes"]:
                print(f"  {v['id']}{' (actuelle)' if v['temoin'] else ''} : "
                      + " · ".join(f"{k} {f:g} Hz" for k, f in v["coupures_hz"].items()) + f" — prédit {v['predit']}")
            print("Suite : mesurer <dossier> --position " + e["positions"][0]["nom"] + " … (go de Louis pour le signal)")
        elif a.cmd == "mesurer":
            import dbx_hiqnet
            erreurs = {k: float(x) for k, x in (m.split(":") for m in a.erreur_sim.split(",") if m)}
            mdp = os.environ.get("DBX_MDP") or "administrator"
            with dbx_hiqnet.Dbx(a.ip, a.port, mdp) as d:
                etat = charger_etat(a.dossier)
                if a.simulation:
                    moteur_pour = lambda pos, sorties: MoteurSimule(d, etat["config"], sorties, pos, erreurs)  # noqa: E731
                else:
                    moteur_pour = lambda pos, sorties: MoteurREW(a.rew)  # noqa: E731
                r = mesurer_position(a.dossier, a.position, d, moteur_pour, dbx_hiqnet.journaliser, a.ip,
                                     a.protection_validee, a.signal_autorise, a.niveau_max, a.appliquer)
            ok = retablissement_complet(r)
            print(f"Position {a.position} : {r['mesures']} mesures · sorties mutées · rétablissement "
                  f"{'complet' if ok else 'INCOMPLET : ' + str(r['retablissement'])} · instantané {r['instantane']}")
            for x in r["alertes"]:
                print(f"  ! {x}")
            if not ok:
                sys.exit("RÉTABLISSEMENT INCOMPLET : sorties laissées mutées ; comparer à l'instantané ou rappeler "
                         "le preset (rappel --protection-validee) avant de démuter.")
            reste = [p["nom"] for p in etat["positions"] if p["nom"] not in charger_etat(a.dossier)["positions_faites"]]
            print(f"Positions restantes : {', '.join(reste) or 'aucune'} — "
                  + (f"Louis place le micro en {reste[0]}, puis relancer." if reste else "suite : conclure"))
        else:
            out = conclure(a.dossier)
            print(rapport(out))
            print(f"Recalage proposé : {out['recalage_propose']} (preset_auto --recaler, accord de Louis)")
    except Annonce as e:
        print(e)
        sys.exit(0)
    except (ValueError, Annule, dd.SortiesNonMutees, OSError) as e:
        sys.exit(f"REFUS / ARRÊT : {e}")


if __name__ == "__main__":
    main()
