#!/usr/bin/env python3
"""
essai_coupures.py — Essai RÉEL des variantes de coupure au micro (REW sweep, un micro), choix de la meilleure par la
mesure, et historique prédit/mesuré qui sert à recaler les poids de coupures.py (`coupures.py --calibrer`).

Trois étapes, chacune relançable ; un dossier d'essai garde l'état entre elles (mesures/essai-<sys>-<date>/) :
  1. preparer  <config> --style techno --positions P1:20x1.7,P2:20x1.2,P3:10x1.7 [--variantes 3]
               [--accepter-non-verifie]
     -> variantes = config actuelle (témoin) + meilleures de coupures.optimiser ; une config par variante
        (data/configs/_essai/, gitignoré) et son plan (preset_auto). Aucun accès machine, aucun son.
  2. mesurer   <dossier> --position P1 --ip <VENU360> --protection-validee --signal-autorise [--niveau-max -12]
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

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_VARIANTES = RACINE / "data" / "configs" / "_essai"
HISTORIQUE = RACINE / "data" / "apprentissage" / "coupures.yaml"
P = "\\\\Preset\\"
NIVEAU_DEPART_DBFS = -20.0
PAS_NIVEAU_DB = 3.0
NIVEAU_PLAFOND_DBFS = -10.0        # = rew_client.NIVEAU_MAX_DBFS
SNR_VISE_DB, SNR_MIN_DB = 15.0, 10.0
PAS_DELAI_MS = 0.5                 # pas maximum par écriture de délai (CLAUDE.md)
TOLERANCE_DELAI_MS = 0.03
ITERATIONS_ALIGNEMENT = 6
PLAGE_ALIGNEMENT_MS = 2.0          # le plan est déjà aligné par le calcul : on cherche une correction, pas un cycle


class Annule(RuntimeError):
    """Arrêt propre de l'essai (cause lisible) : sorties re-mutées, état d'origine rétabli."""


# ---------------------------------------------------------------- positions et état
def lire_positions(texte: str) -> list[dict]:
    """« P1:20x1.7,P2:20x1.2 » -> [{nom, distance_m, hauteur_m}] (distance au pied de la pile, hauteur du micro)."""
    out = []
    for morceau in texte.split(","):
        m = re.fullmatch(r"\s*([A-Za-z0-9_-]+)\s*:\s*([\d.]+)\s*x\s*([\d.]+)\s*", morceau)
        if not m:
            raise ValueError(f"position illisible : {morceau!r} (attendu NOM:distance_m x hauteur_m, ex. P1:20x1.7)")
        out.append({"nom": m.group(1), "distance_m": float(m.group(2)), "hauteur_m": float(m.group(3))})
    if len({p["nom"] for p in out}) != len(out):
        raise ValueError("noms de positions en double")
    return out


def _ecrire_atomique(p: Path, texte: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(texte)
    os.replace(tmp, p)


def charger_etat(dossier: str | Path) -> dict:
    return json.loads((Path(dossier) / "etat.json").read_text(encoding="utf-8"))


def sauver_etat(dossier: str | Path, etat: dict) -> None:
    _ecrire_atomique(Path(dossier) / "etat.json", json.dumps(etat, ensure_ascii=False, indent=1))


def _cles_raccords(plan: dict) -> list[tuple[str, str]]:
    noms = [v["nom"] for v in plan["voies"]]      # du grave à l'aigu (ordre de la config)
    return list(zip(noms, noms[1:]))


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


# ---------------------------------------------------------------- moteurs de mesure
class MoteurREW:
    """REW par son API (rew_client) : chaque `mesurer` JOUE UN SWEEP."""
    simule = False

    def __init__(self, hote: str = "localhost:4735"):
        import rew_client
        self.rc = rew_client
        self.rew = rew_client.REW(hote)

    def verifier(self) -> None:
        self.rew.info()

    def mesurer(self, nom: str, niveau_dbfs: float) -> dict:
        ident = self.rew.mesurer(nom, niveau_dbfs)
        ir = self.rew.reponse_impulsionnelle(ident)
        ref = str(ir.get("ref_temps") or "").strip()
        if not ref or ref.lower() in ("none", "null", "no timing reference"):
            raise Annule("REW mesure sans timing reference : les phases de deux sweeps ne sont pas comparables. "
                         "Régler une référence de temps en boucle (Preferences > Analysis > Timing reference)")
        fr = self.rew.reponse_frequence(ident, ppo=24, lissage="1/24")
        return {"id": ident, "f_hz": list(map(float, fr["f_hz"])), "mag_db": list(map(float, fr["mag_db"])),
                "phase_deg": None if fr["phase_deg"] is None else list(map(float, fr["phase_deg"])), "ref_temps": ref}

    def aligner(self, a: dict, b: dict, fc: float) -> dict:
        r = self.rew.aligner(a["id"], b["id"], fc, PLAGE_ALIGNEMENT_MS)
        return {"delai_b_ms": r["retenu"]["delai_b_ms"], "inverser_b": r["retenu"]["inverser_b"],
                "alertes": list(r["alertes"])}


def _hz(texte) -> float | None:
    m = re.search(r"([\d.]+)\s*(k?)Hz", str(texte or ""), re.I)
    return float(m.group(1)) * (1000 if m.group(2) else 1) if m else None


def _ms(texte) -> float:
    m = re.match(r"\s*([-\d.]+)", str(texte or "0"))
    return float(m.group(1)) if m else 0.0


class MoteurSimule:
    """Acoustique simulée, SANS SON : la réponse au micro est calculée depuis ce qui est RÉELLEMENT dans le processeur
    (mutes, filtres, gain de voie, polarité, délai relus), la géométrie de pile (trajets, 1/r) et le niveau naturel de
    chaque voie (inverse de l'équilibre de reglages_pile). `erreurs_cm` décale le centre acoustique réel de certaines
    voies (vers l'arrière = +) : c'est ce que l'alignement doit retrouver. Bruit de fond fixe à −80 dB."""
    simule = True

    def __init__(self, d, chemin_config: str, sorties: dict[int, str], position: dict,
                 erreurs_cm: dict[str, float] | None = None, graine: int = 0):
        self.d, self.sorties, self.pos = d, sorties, position
        mat, self.cfg = cs.charger(chemin_config)
        self.c = cs.vitesse_son(self.cfg.get("temperature_c", 20))
        eq = rp.equilibre_gains(self.cfg, mat)
        self.naturel = {n: -float(x.get("gain_propose_db", 0.0)) for n, x in eq.items()}
        self.erreurs = erreurs_cm or {}
        self.f = 20.0 * 2 ** (np.arange(0, int(24 * math.log2(20000 / 20)) + 1) / 24)
        self.rng = np.random.default_rng(graine)
        self.ouvertes: set[int] = set()        # mutes VIRTUELS : la simulation ne démute jamais la machine

    def verifier(self) -> None:
        pass

    def _voie(self, n: int, nom: str) -> np.ndarray:
        X = f"{P}CrossoverIIR{n}\\SV\\Band_1_"
        H = np.ones_like(self.f, dtype=complex)
        for cote, ph in (("HP", True), ("LP", False)):
            fc = _hz(self.d.get(X + f"{cote}Frequency"))
            if fc:
                m = re.match(r"\s*(LR|BW)\s*(\d+)", str(self.d.get(X + f"{cote}Type")), re.I)
                fam, pente = (m.group(1).upper(), int(m.group(2))) if m else ("LR", 24)
                H = H * rp.reponse_filtre((fam, pente, fc), ph, self.f)
        gain = _ms(self.d.get(X + "Gain"))                    # « -12.6dB » : premier nombre
        pol = -1.0 if "inv" in str(self.d.get(X + "Polarity")).lower() else 1.0
        dl = f"{P}DelayOut{n}\\SV\\"
        delai = _ms(self.d.get(dl + "Amount")) if str(self.d.get(dl + "Delay")).strip() == "On" else 0.0
        g = (self.cfg.get("geometrie_pile") or {}).get(nom) or {}
        dx = self.pos["distance_m"] + (g.get("profondeur_cm", 0) + self.erreurs.get(nom, 0.0)) / 100
        dz = g.get("hauteur_cm", 100) / 100 - self.pos["hauteur_m"]
        r = math.hypot(dx, dz)
        t = r / self.c + delai / 1000
        niv = 10 ** ((self.naturel.get(nom, 0.0) + gain) / 20) / r
        return pol * niv * H * np.exp(-2j * np.pi * self.f * t)

    def mesurer(self, nom: str, niveau_dbfs: float) -> dict:
        if niveau_dbfs > NIVEAU_PLAFOND_DBFS:
            raise Annule(f"niveau {niveau_dbfs} dBFS au-dessus du plafond {NIVEAU_PLAFOND_DBFS} dBFS")
        S = np.zeros_like(self.f, dtype=complex)
        for n, voie in self.sorties.items():
            if n in self.ouvertes:
                S = S + self._voie(n, voie)
        S = S * 10 ** ((niveau_dbfs - NIVEAU_DEPART_DBFS) / 20)
        S = S + 1e-4 * np.exp(2j * np.pi * self.rng.random(len(self.f)))      # bruit de fond (−80 dB)
        return {"id": nom, "f_hz": self.f.tolist(), "mag_db": (20 * np.log10(np.abs(S))).tolist(),
                "phase_deg": np.rad2deg(np.angle(S)).tolist(), "ref_temps": "simulation"}

    def aligner(self, a: dict, b: dict, fc: float) -> dict:
        r = ar.meilleur_delai(a, b, fc, PLAGE_ALIGNEMENT_MS)
        return {"delai_b_ms": r["delai_b_ms"], "inverser_b": r["inverser_b"], "alertes": []}


# ---------------------------------------------------------------- 2. mesurer (une position)
def ouvrir(d, sorties: set[int]) -> None:
    """N'ouvre QUE `sorties` : on mute d'abord les autres et on EXIGE leur relecture « On » avant de démuter quoi que ce
    soit ; puis on démute et on relit l'état complet."""
    voulu = {ch: ("Off" if int(re.search(r"Channel_(\d)_", ch).group(1)) in sorties else "On") for ch in dd.SORTIES_MUTE}
    a_muter = [ch for ch, v in voulu.items() if v == "On"]
    dd.muter_sorties(d, {ch: "On" for ch in a_muter})
    pas_mutees = [ch for ch in a_muter if (d.get(ch) or "").strip() != "On"]
    if pas_mutees:
        raise dd.SortiesNonMutees(f"mute non confirmé sur {pas_mutees} : rien n'est démuté")
    if sorties:
        dd.muter_sorties(d, {ch: "Off" for ch, v in voulu.items() if v == "Off"})
    relu = {ch: (d.get(ch) or "").strip() for ch in dd.SORTIES_MUTE}
    faux = [ch for ch in dd.SORTIES_MUTE if relu.get(ch) != voulu[ch]]
    if faux:
        raise dd.SortiesNonMutees(f"état des mutes non conforme : {faux} relus {[relu[c] for c in faux]}")


def _delai(d, n: int) -> float:
    dl = f"{P}DelayOut{n}\\SV\\"
    return _ms(d.get(dl + "Amount")) if str(d.get(dl + "Delay")).strip() == "On" else 0.0


def ecrire_delai(d, n: int, cible_ms: float, journal, ip: str) -> float:
    """Va au délai cible par pas ≤ 0,5 ms (écriture + relecture à chaque pas). Renvoie le délai relu."""
    if not 0 <= cible_ms <= 30:
        raise Annule(f"délai {cible_ms:.2f} ms hors [0, 30] ms sur la sortie {n}")
    dl = f"{P}DelayOut{n}\\SV\\"
    actuel = _delai(d, n)
    while abs(cible_ms - actuel) > 0.005:
        pas = max(-PAS_DELAI_MS, min(PAS_DELAI_MS, cible_ms - actuel))
        val = round(actuel + pas, 2)
        relu, ok, err = dd.ecrire_et_relire(d, dl + "Amount", f"{val:.2f}ms", dd.decrire_param(d, dl + "Amount"))
        dd.ecrire_et_relire(d, dl + "Delay", "On" if val > 0 else "Off", None)
        lu = _delai(d, n) if val > 0 else 0.0
        journal(f"ESSAI {ip} DelayOut{n} : {actuel:.2f} -> {val:.2f} ms (relu {relu}){'' if abs(lu - val) < 0.011 else ' ECART'}")
        if abs(lu - val) >= 0.011:
            raise Annule(f"délai sortie {n} : écrit {val:.2f} ms, relu {relu} ({err})")
        actuel = val
    return actuel


def _chemins_suivis(plans: list[dict]) -> list[str]:
    import dbx_plan
    out = set(dd.SORTIES_MUTE)
    for plan in plans:
        out |= {c for c, *_ in dbx_plan.ecritures(plan)[0]}
    return sorted(out)


def _nombre(texte) -> float | None:
    m = re.match(r"\s*([-+]?[\d.]+)", str(texte or ""))
    return float(m.group(1)) if m else None


def retablir(d, origine: dict, journal, ip: str, config: str, sorties: dict[int, str]) -> dict:
    """Sorties mutées ; chaque valeur suivie est relue et remise à son état d'ORIGINE (début du lancement), valeur
    validée par la machine. Jamais réécrit : une valeur d'origine vide ou illisible, un limiteur d'origine « Off », un
    seuil d'origine au-dessus du plafond calculé (calc_systeme) — signalés dans `refuses`. Sorties LAISSÉES MUTÉES."""
    from garde_fous import verifier_plafond
    dd.muter_et_verifier(d)
    restants, refuses = {}, {}
    for ch, v in origine.items():
        if ch in dd.SORTIES_MUTE:
            continue
        lu = (d.get(ch) or "").strip()
        if lu == v:
            continue
        if not v:
            refuses[ch] = "valeur d'origine vide (lecture échouée au départ) : non réécrite"
            continue
        m = re.search(r"\\Limiter(\d)\\SV\\(Limiter|Threshold)$", ch)
        if m and m.group(2) == "Limiter" and v != "On":
            refuses[ch] = f"limiteur d'origine « {v} » : non réécrit (il reste « {lu} »)"
            continue
        if m and m.group(2) == "Threshold":
            seuil = _nombre(v)
            try:
                if seuil is None:
                    raise ValueError(f"seuil illisible {v!r}")
                verifier_plafond(seuil, config, sorties.get(int(m.group(1))), "dBFS", False)
            except ValueError as e:
                refuses[ch] = f"seuil d'origine {v} non réécrit ({e}) ; il reste {lu}"
                continue
        desc = dd.decrire_param(d, ch)
        ecrit = v.split("/")[0] if re.match(r"[-+\d.]+ms/", v) else v
        motif = dd.valider_machine(desc, ecrit) if desc else None
        if motif:
            refuses[ch] = f"valeur d'origine {v} refusée par la machine ({motif})"
            continue
        relu, ok, _ = dd.ecrire_et_relire(d, ch, ecrit, desc)
        journal(f"ESSAI {ip} RETABLI {ch} : {lu} -> {v} (relu {relu})")
        if (relu or "").strip() != v and not ok:
            restants[ch] = (v, relu)
    for ch, motif in refuses.items():
        journal(f"ESSAI {ip} RETABLISSEMENT REFUSE {ch} : {motif}")
    return {"ecarts_restants": restants, "refuses": refuses, "sorties": "mutées"}


def retablissement_complet(r: dict) -> bool:
    x = r.get("retablissement") or {}
    return not (x.get("erreur") or x.get("ecarts_restants") or x.get("refuses"))


def mesurer_position(dossier: str | Path, nom_position: str, d, moteur_pour, journal, ip: str,
                     protection_validee: bool, signal_autorise: bool, niveau_max: float = -12.0) -> dict:
    """Mesure toutes les variantes à UNE position. `moteur_pour(position, sorties)` crée le moteur (REW ou simulé)."""
    import dbx_plan
    etat = charger_etat(dossier)
    pos = next((p for p in etat["positions"] if p["nom"] == nom_position), None)
    if pos is None:
        raise ValueError(f"position {nom_position} inconnue ({', '.join(p['nom'] for p in etat['positions'])})")
    if not protection_validee:
        raise ValueError("le plan écrit des PROTECTIONS (limiteurs, filtres, mutes) : --protection-validee requis")
    if niveau_max > NIVEAU_PLAFOND_DBFS:
        raise ValueError(f"--niveau-max {niveau_max} dBFS au-dessus du plafond du kit ({NIVEAU_PLAFOND_DBFS} dBFS)")
    plans = {v["id"]: json.loads(Path(v["plan"]).read_text(encoding="utf-8")) for v in etat["variantes"]}
    sorties = {x["sortie"]: x["nom"] for x in next(iter(plans.values()))["voies"]}
    moteur = moteur_pour(pos, sorties)
    if not moteur.simule and not signal_autorise:
        raise ValueError("chaque mesure JOUE UN SWEEP : --signal-autorise seulement après le « go » de Louis "
                         "(niveau sûr, personne exposé, pas de concert)")
    premiere = not etat["positions_faites"]
    if nom_position in etat["positions_faites"]:
        raise ValueError(f"position {nom_position} déjà mesurée dans cet essai")
    if etat["niveau_dbfs"] and max(etat["niveau_dbfs"].values()) > niveau_max:
        raise ValueError(f"niveaux retenus {etat['niveau_dbfs']} au-dessus de --niveau-max {niveau_max} dBFS : "
                         "relancer un essai (preparer) pour refaire la rampe sous ce plafond")
    suivis = _chemins_suivis(list(plans.values()))          # refuse aussi un plan devenu incohérent (config modifiée)
    origine = {ch: (d.get(ch) or "").strip() for ch in suivis}   # état de départ relu À CHAQUE lancement
    import dbx_hiqnet
    try:
        resultat_snap = str(dbx_hiqnet.instantane_avant(d, ip, f"avant-essai-{nom_position}"))
    except ValueError as e:
        raise ValueError(f"instantané complet impossible : {e}") from None
    etat.setdefault("instantanes", {})[nom_position] = {"fichier": resultat_snap, "suivis": origine}
    sauver_etat(dossier, etat)
    rep = Path(dossier) / "mesures"
    rep.mkdir(exist_ok=True)
    journal(f"ESSAI {ip} début position {nom_position} ({pos['distance_m']} m, {pos['hauteur_m']} m) — "
            f"{'SIMULATION acoustique' if moteur.simule else 'REW, signal autorisé par Louis'}, plafond {niveau_max} dBFS")
    resultat = {"position": nom_position, "mesures": 0, "alertes": [], "instantane": resultat_snap}
    depart = min(NIVEAU_DEPART_DBFS, niveau_max)
    niveaux = dict(etat["niveau_dbfs"] or {})
    try:
        moteur.verifier()
        if moteur.simule:
            dd.muter_et_verifier(d)             # simulation : la machine reste mutée de bout en bout

        def mesure(nom: str, ouvertes: set[int], niveau: float) -> dict:
            """Une mesure ; magnitudes ramenées à −20 dBFS (système linéaire) pour comparer des niveaux différents."""
            if niveau > niveau_max:
                raise Annule(f"niveau {niveau} dBFS au-dessus de --niveau-max {niveau_max}")
            if moteur.simule:
                moteur.ouvertes = set(ouvertes)
            else:
                ouvrir(d, ouvertes)
            try:
                m = moteur.mesurer(f"{Path(dossier).name}_{nom}", niveau)
            finally:
                if moteur.simule:
                    moteur.ouvertes = set()
                else:
                    ouvrir(d, set())
            m["niveau_dbfs"] = niveau
            m["mag_db"] = [x - (niveau - NIVEAU_DEPART_DBFS) for x in m["mag_db"]]
            (rep / f"{nom}.json").write_text(json.dumps(m), encoding="utf-8")
            resultat["mesures"] += 1
            return m

        def niv(*noms: str) -> float:      # paire, système : le plus BAS des niveaux des voies concernées
            return min(niveaux[n] for n in noms)

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
            if not niveaux:     # rampe de niveau PAR VOIE, une fois pour tout l'essai
                bruit = mesure(f"{tag}_bruit", set(), depart)
                for x in plan["voies"]:
                    n_ = depart
                    while True:
                        m = mesure(f"{tag}_rampe_{x['nom']}_{n_:+.0f}", {x["sortie"]}, n_)
                        snr = _snr(m, bruit, x, n_ - depart)
                        if snr >= SNR_VISE_DB or n_ + PAS_NIVEAU_DB > niveau_max:
                            break
                        n_ += PAS_NIVEAU_DB
                    if snr < SNR_MIN_DB:
                        raise Annule(f"{x['nom']} : rapport signal/bruit {snr:.1f} dB < {SNR_MIN_DB} dB au plafond "
                                     f"{niveau_max} dBFS — vérifier câblage, micro, ampli")
                    niveaux[x["nom"]] = n_
                etat["niveau_dbfs"] = niveaux
                sauver_etat(dossier, etat)
                journal(f"ESSAI {ip} niveaux de mesure retenus : {niveaux} dBFS")
            solos = {x["nom"]: mesure(f"{tag}_{x['nom']}", {x["sortie"]}, niveaux[x["nom"]]) for x in plan["voies"]}
            if premiere:
                for bas, haut in reversed(_cles_raccords(plan)):    # de l'aigu vers le grave, comme le calcul
                    fc = v["coupures_hz"][f"{bas}/{haut}"]
                    for _ in range(ITERATIONS_ALIGNEMENT):
                        al = moteur.aligner(solos[haut], solos[bas], fc)
                        loc = ar.meilleur_delai(solos[haut], solos[bas], fc, PLAGE_ALIGNEMENT_MS)
                        resultat["alertes"] += [f"{v['id']} {bas}/{haut} : {a}" for a in al["alertes"]]
                        if al["inverser_b"] or loc["inverser_b"]:
                            msg = (f"{v['id']} {bas}/{haut} : la mesure propose d'INVERSER {bas} (efficacité "
                                   f"{loc['efficacite_inverse']} inversé contre {loc['efficacite_non_inverse']} ; "
                                   "polarité = protection : décision de Louis, non appliquée ; variante mesurée au "
                                   "meilleur délai non inversé)")
                            if msg not in resultat["alertes"]:
                                resultat["alertes"].append(msg)
                        if al["inverser_b"]:
                            delta = loc["delai_non_inverse_ms"]
                        else:
                            delta = al["delai_b_ms"]
                            if not moteur.simule and abs(delta - loc["delai_non_inverse_ms"]) > 250 / fc:
                                resultat["alertes"].append(
                                    f"{v['id']} {bas}/{haut} : REW {delta:+.2f} ms et contre-vérification "
                                    f"{loc['delai_non_inverse_ms']:+.2f} ms divergent de plus d'1/4 de période : "
                                    "REW (IR) retenu, vérifier la somme mesurée")
                        if abs(delta) <= TOLERANCE_DELAI_MS:
                            break
                        nb = _delai(d, par_nom[bas]["sortie"]) + delta
                        if nb < 0:        # pas de délai négatif : on retarde d'autant la voie haute et celles au-dessus
                            dessus = [n for n in par_nom if list(par_nom).index(n) >= list(par_nom).index(haut)]
                            for n in dessus:
                                ecrire_delai(d, par_nom[n]["sortie"], _delai(d, par_nom[n]["sortie"]) - nb, journal, ip)
                                solos[n] = mesure(f"{tag}_{n}", {par_nom[n]["sortie"]}, niveaux[n])
                            nb = 0.0
                        ecrire_delai(d, par_nom[bas]["sortie"], nb, journal, ip)
                        solos[bas] = mesure(f"{tag}_{bas}", {par_nom[bas]["sortie"]}, niveaux[bas])
                    else:
                        resultat["alertes"].append(f"{v['id']} {bas}/{haut} : alignement non convergé en "
                                                   f"{ITERATIONS_ALIGNEMENT} itérations")
                etat["delais_alignes"][v["id"]] = {n: _delai(d, x["sortie"]) for n, x in par_nom.items()}
                sauver_etat(dossier, etat)
            for bas, haut in _cles_raccords(plan):
                mesure(f"{tag}_{bas}+{haut}", {par_nom[bas]["sortie"], par_nom[haut]["sortie"]}, niv(bas, haut))
            mesure(f"{tag}_systeme", {x["sortie"] for x in plan["voies"]}, niv(*par_nom))
        etat["positions_faites"].append(nom_position)
        etat["alertes"] += resultat["alertes"]
        sauver_etat(dossier, etat)
    except BaseException as e:      # Ctrl-C compris : on re-mute avant tout
        try:
            dd.muter_et_verifier(d)
            journal(f"ESSAI {ip} ARRET position {nom_position} : {e!r} — 6 sorties re-mutées (relu)")
        except Exception as e2:   # noqa: BLE001
            journal(f"ESSAI {ip} ARRET position {nom_position} : {e!r} — RE-MUTE NON CONFIRMÉ ({e2}) : couper les amplis")
            print(f"!!! RE-MUTE NON CONFIRMÉ ({e2}) : COUPER LES AMPLIS", file=sys.stderr)
        resultat["erreur"] = repr(e)
        raise
    finally:
        try:
            resultat["retablissement"] = retablir(d, origine, journal, ip, etat["config"], sorties)
        except Exception as e3:   # noqa: BLE001
            resultat["retablissement"] = {"erreur": repr(e3)}
            journal(f"ESSAI {ip} rétablissement en échec : {e3!r} — vérifier les mutes, couper les amplis au moindre doute")
            print(f"!!! RÉTABLISSEMENT EN ÉCHEC ({e3!r}) : vérifier les mutes ; couper les amplis au moindre doute",
                  file=sys.stderr)
    return resultat


def _snr(m: dict, bruit: dict, voie_plan: dict, decalage_db: float = 0.0) -> float:
    """Signal (ramené à −20 dBFS, donc + decalage_db pour le niveau réel joué) sur le bruit de fond, dans la bande."""
    f = np.asarray(m["f_hz"])
    lo = (voie_plan.get("hpf") or {}).get("hz") or 20.0
    hi = (voie_plan.get("lpf") or {}).get("hz") or 16000.0
    k = (f >= lo) & (f <= hi)
    s = np.asarray(m["mag_db"])[k]
    b = np.asarray(bruit["mag_db"])[k]
    return float(10 * np.log10(np.mean(10 ** (s / 10))) + decalage_db - 10 * np.log10(np.mean(10 ** (b / 10))))


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
           "simulation": None, "regularite_mesuree": len(faites) > 1}
    _ecrire_atomique(dossier / "resultat.yaml", yaml.safe_dump(out, allow_unicode=True, sort_keys=False))
    _ecrire_atomique(dossier / "rapport.md", rapport(out))
    out["recalage_propose"] = str(_fichier_recalage(dossier, etat, resultats))
    if ajouter_historique:
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
    _ecrire_atomique(f, yaml.safe_dump(doc, allow_unicode=True, sort_keys=False))
    return f


def ajouter_a_historique(out: dict, fichier: Path | None = None) -> None:
    """Ajout seulement (jamais de réécriture d'une entrée) ; le même essai n'est pas ajouté deux fois."""
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
                                     a.protection_validee, a.signal_autorise, a.niveau_max)
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
    except (ValueError, Annule, dd.SortiesNonMutees, OSError) as e:
        sys.exit(f"REFUS / ARRÊT : {e}")


if __name__ == "__main__":
    main()
