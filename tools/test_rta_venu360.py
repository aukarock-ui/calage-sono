#!/usr/bin/env python3
r"""
test_rta_venu360.py — Le RTA 31 bandes du VENU360 est-il lisible EN DIRECT par le réseau ?

  python tools/test_rta_venu360.py <ip>                                  # lecture seule : état actuel
  python tools/test_rta_venu360.py <ip> --appliquer --signal-valide [--json res.json]

Avec --appliquer : générateur en bruit rose à −30 dB, routé vers le RTA SEULEMENT (RTARouter = Signal Generator).
Aucune injection InRouter, donc rien vers les sorties, qui doivent en plus être relues mutées (sinon arrêt).
On relève `\\Preset\RTA\SV\AllMeters` (abonnement), puis on remet tout. Le générateur est coupé en premier.
Toutes les écritures passent par dbx_reglage (contrôles, journal). --signal-valide : seulement après l'accord de
Louis (amplis débranchés ou niveau sûr, personne exposé). Aucun micro nécessaire.

Résultat décisif pour la méthode « VENU360 seul » (alignement par annulation au RTA, voir journal/ETAT.md du 25/11/2026) :
« vivant » = plusieurs mises à jour pendant le relevé, sinon le mètre est figé comme OutputMeters.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx_description as dd   # noqa: E402
import dbx_hiqnet              # noqa: E402
import dbx_reglage as dr       # noqa: E402
import metres                  # noqa: E402

G = "\\\\Preset\\SignalGenerator\\SV\\"
RT = "\\\\Preset\\RTARouter\\SV\\RouteSelect"
ALL = "\\\\Preset\\RTA\\SV\\AllMeters"


def _lire(d, c):
    try:
        return d.get(c)
    except ValueError as e:
        return f"ERREUR {e}"


def injections(d) -> list[str]:
    """Tous les SignalGeneratorControl du preset chargé (InRouter, InMixer, MidRouter…), lus dans l'arbre."""
    out = []
    for b in sorted(d.ls("\\\\Preset")):
        try:
            sv = d.ls(f"\\\\Preset\\{b}\\SV")
        except ValueError:
            continue
        out += [f"\\\\Preset\\{b}\\SV\\{k}" for k in sv if k == "SignalGeneratorControl"]
    return out


def _sur(d, inj) -> str | None:
    """Motif d'arrêt si les sorties ne sont pas toutes mutées ou si le générateur est injecté quelque part."""
    if not all(str(_lire(d, c)).strip() == "On" for c in dd.SORTIES_MUTE):
        return "les 6 sorties ne sont pas relues mutées"
    inj_on = [c for c in inj if str(_lire(d, c)).strip() != "SignalGeneratorOff"]
    return f"générateur injecté ou état illisible : {inj_on}" if inj_on else None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ip")
    ap.add_argument("--appliquer", action="store_true")
    ap.add_argument("--signal-valide", action="store_true")
    ap.add_argument("--duree", type=float, default=4.0)
    ap.add_argument("--json")
    ap.add_argument("--port", type=int, default=dbx_hiqnet.PORT)
    a = ap.parse_args()
    if a.appliquer and not a.signal_valide:
        sys.exit("REFUS : générateur de signal — --signal-valide après l'accord explicite de Louis")
    journal_f = dbx_hiqnet.JOURNAL / "ecritures.log"

    def journal(t):
        t = str(t).replace("\r", " ").replace("\n", " ")
        print("  ", t)
        journal_f.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_f, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} RTA-TEST {t}\n")

    instantanes = dbx_hiqnet.JOURNAL / "instantanes"

    def ecrire(d, ch, v):
        dr.ecrire_controle(d, a.ip, ch, v, dr.Options(appliquer=True, signal_valide=True, journal=journal,
                                                      dossier_instantanes=instantanes))

    mdp = os.environ.get("DBX_MDP") or "administrator"
    code = 0
    with dbx_hiqnet.Dbx(a.ip, a.port, mdp) as d:
        inj = injections(d)
        chemins = [G + k for k in ("Signal Generator", "Signal Type", "Signal Amplitude", "Signal Route")] + [RT] + inj \
            + dd.SORTIES_MUTE
        avant = {c: _lire(d, c) for c in chemins}
        res = {"avant": avant, "injections": inj, "repos": metres.relever(d, [ALL], 2.0)[ALL]}
        print(json.dumps(avant, ensure_ascii=False, indent=1))
        if not a.appliquer:
            print("Lecture seule (ajouter --appliquer --signal-valide pour le test).")
            return
        motif = _sur(d, inj)
        if str(avant[G + "Signal Generator"]).strip() != "Off":
            motif = f"générateur déjà {avant[G + 'Signal Generator']!r} : état inconnu, pas de test"
        for c, choix in ((RT, None), (G + "Signal Type", ("Pink", "White", "Sine"))):
            if str(avant[c]).startswith("ERREUR") or (choix and avant[c] not in choix):
                motif = f"{c} illisible ou inattendu ({avant[c]!r}) : remise impossible, pas de test"
        if motif:
            sys.exit(f"ARRÊT : {motif}")
        instantanes.mkdir(parents=True, exist_ok=True)
        snap = instantanes / f"{time.strftime('%Y%m%d-%H%M%S')}-avant-test-rta.json"
        snap.write_text(json.dumps(avant, ensure_ascii=False, indent=1), encoding="utf-8")
        print("Instantané :", snap)
        try:
            if str(avant[G + "Signal Amplitude"]).strip() != "-30dB":
                ecrire(d, G + "Signal Amplitude", "-30dB")
            if str(avant[G + "Signal Type"]).strip() != "Pink":
                ecrire(d, G + "Signal Type", "Pink")
            ecrire(d, RT, "Signal Generator")
            motif = _sur(d, inj)                    # relu juste avant l'allumage
            if motif:
                raise RuntimeError(f"avant allumage : {motif}")
            ecrire(d, G + "Signal Generator", "On")
            time.sleep(1.0)
            res["rose"] = metres.relever(d, [ALL], a.duree)[ALL]
            motif = _sur(d, inj)                    # relu après le relevé
            if motif:
                raise RuntimeError(f"pendant le relevé : {motif}")
            res["get_successifs"] = [_lire(d, ALL) for _ in range(5)]
        except Exception as e:                      # noqa: BLE001 — remise dans le finally, code d'erreur
            print("INTERROMPU :", e)
            res["erreur"] = str(e)
            code = 1
        finally:
            for essai in range(3):                  # générateur coupé EN PREMIER, relu « Off », jusqu'à 3 essais
                try:
                    if str(_lire(d, G + "Signal Generator")).strip() == "Off":
                        break
                    ecrire(d, G + "Signal Generator", "Off")
                except Exception as e:              # noqa: BLE001
                    print("COUPURE DU GÉNÉRATEUR NON FAITE", essai + 1, e)
            for c in (RT, G + "Signal Type", G + "Signal Amplitude"):
                try:
                    if str(_lire(d, c)).strip() != str(avant[c]).strip():
                        ecrire(d, c, avant[c])
                except Exception as e:              # noqa: BLE001 — on continue la remise, on signale
                    print("RETOUR NON FAIT", c, e)
            apres = {c: _lire(d, c) for c in chemins}
            res["ecarts_apres"] = {c: (avant[c], apres[c]) for c in chemins
                                   if str(avant[c]).strip() != str(apres[c]).strip()}
            if str(apres[G + "Signal Generator"]).strip() != "Off":
                print("!!! GÉNÉRATEUR NON RELU « Off » : LE COUPER À LA MAIN (face avant, menu RTA) !!!")
            print("Écarts après remise :", res["ecarts_apres"] or "aucun")
            if res["ecarts_apres"] or str(apres[G + "Signal Generator"]).strip() != "Off":
                code = 2
    for k in ("repos", "rose"):
        x = res.get(k) or {}
        print(f"{k:6} : {x.get('maj')} mise(s) à jour, {'VIVANT' if x.get('vivant') else 'figé'} | {str(x.get('derniere'))[:160]}")
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
