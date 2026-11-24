#!/usr/bin/env python3
"""
banc.py — Banc de mesure partagé par essai_coupures.py et optim_preset.py : SEULE implémentation des garde-fous.

  - moteurs : MoteurREW (chaque mesure JOUE UN SWEEP) et MoteurSimule (aucun son, mutes virtuels : la machine n'est
    jamais démutée en simulation) ;
  - ouvrir() : n'ouvre que les sorties demandées (mute vérifié avant tout démute) ;
  - ecrire_delai() : pas ≤ 0,5 ms, borne 0-30 ms, relecture à chaque pas ;
  - retablir() : retour à l'état du début du lancement, sans jamais remettre une protection moins sûre ;
  - Seance : contexte d'un lancement (contrôles préalables, instantané complet, mesure à niveau par voie normalisée
    à −20 dBFS, rampe de niveau, alignement des raccords, re-mute sur toute exception, rétablissement en sortie).
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyse_raccord as ar       # noqa: E402
import calc_systeme as cs          # noqa: E402
import dbx_description as dd       # noqa: E402
import reglages_pile as rp         # noqa: E402

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


def cles_raccords(plan: dict) -> list[tuple[str, str]]:
    noms = [v["nom"] for v in plan["voies"]]      # du grave à l'aigu (ordre de la config)
    return list(zip(noms, noms[1:]))



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


RACINE = Path(__file__).resolve().parent.parent
FICHIER_BANC_ELECTRIQUE = RACINE / "data" / "protocoles" / "venu360_phase.yaml"   # écrit par banc_electrique.py
PHASE_HYPOTHESE = {"type": "constante", "source": "HYPOTHÈSE de simulation (rotation constante) : non mesurée"}


def modele_phase() -> dict | None:
    """Modèle de Band_1_Phase mesuré par banc_electrique.py (None tant que non mesuré)."""
    import yaml
    if not FICHIER_BANC_ELECTRIQUE.exists():
        return None
    return (yaml.safe_load(FICHIER_BANC_ELECTRIQUE.read_text(encoding="utf-8")) or {}).get("phase")


def reponse_phase(modele: dict, phase_deg: float, f: np.ndarray) -> np.ndarray:
    """Effet de Band_1_Phase = phase_deg selon le modèle : « constante » (rotation e^{jφ}), « retard » (φ à f_ref),
    ou « table » (écarts mesurés {reglage_deg: {f_hz, dphi_deg, dmag_db}}, interpolés entre réglages)."""
    if not phase_deg:
        return np.ones(len(f), dtype=complex)
    t = modele.get("type")
    if t == "constante":
        return np.exp(1j * np.deg2rad(phase_deg)) * np.ones(len(f))
    if t == "retard":
        tau = -phase_deg / 360.0 / float(modele["f_ref_hz"])
        return np.exp(-2j * np.pi * f * tau)
    if t == "table":
        regl = sorted(float(k) for k in modele["table"])
        x = float(np.clip(phase_deg, regl[0], regl[-1]))
        hauts = [r for r in regl if r >= x]
        bas = [r for r in regl if r <= x]
        r0, r1 = (bas[-1] if bas else regl[0]), (hauts[0] if hauts else regl[-1])

        def un(r):
            e = modele["table"][next(k for k in modele["table"] if float(k) == r)]
            ph = np.interp(np.log(f), np.log(e["f_hz"]), np.unwrap(np.deg2rad(e["dphi_deg"])))
            mg = np.interp(np.log(f), np.log(e["f_hz"]), e.get("dmag_db") or np.zeros(len(e["f_hz"])))
            return mg, ph
        m0, p0 = un(r0)
        m1, p1 = un(r1)
        w = 0.0 if r1 == r0 else (x - r0) / (r1 - r0)
        return 10 ** (((1 - w) * m0 + w * m1) / 20) * np.exp(1j * ((1 - w) * p0 + w * p1))
    raise ValueError(f"modèle de phase inconnu : {t}")


def _bandes_lues(d, base: str, n: int) -> list[dict]:
    if str(d.get(base + "ParametricEQ")).strip() != "On":
        return []
    out = []
    for i in range(1, n + 1):
        g = _ms(d.get(base + f"Band_{i}_Gain"))
        if abs(g) < 0.05:
            continue
        out.append({"type": str(d.get(base + f"Band_{i}_Type")).strip(), "hz": _hz(d.get(base + f"Band_{i}_Frequency")),
                    "gain_db": g, "q": _ms(d.get(base + f"Band_{i}_Q"))})
    return out


class MoteurSimule:
    """Acoustique simulée, SANS SON : la réponse au micro est calculée depuis ce qui est RÉELLEMENT dans le processeur
    (mutes, filtres, gain de voie, polarité, délai relus), la géométrie de pile (trajets, 1/r) et le niveau naturel de
    chaque voie (inverse de l'équilibre de reglages_pile). `erreurs_cm` décale le centre acoustique réel de certaines
    voies (vers l'arrière = +) : c'est ce que l'alignement doit retrouver. Bruit de fond fixe à −80 dB."""
    simule = True

    def __init__(self, d, chemin_config: str, sorties: dict[int, str], position: dict,
                 erreurs_cm: dict[str, float] | None = None, graine: int = 0,
                 propre: dict[str, list[dict]] | None = None, ecart_sens_db: dict[str, float] | None = None,
                 phase: dict | None = None):
        """propre : « réponse propre » synthétique par voie (bandes {type, hz, gain_db, q}) que l'EQ doit retrouver ;
        ecart_sens_db : écart de sensibilité réel par voie (que les gains doivent retrouver) ; phase : modèle de
        Band_1_Phase (défaut : celui du banc électrique, sinon HYPOTHÈSE de rotation constante)."""
        self.d, self.sorties, self.pos = d, sorties, position
        self.propre, self.ecart_sens = propre or {}, ecart_sens_db or {}
        self.phase = phase or modele_phase() or PHASE_HYPOTHESE
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
        import eq_auto
        H = H * reponse_phase(self.phase, _ms(self.d.get(X + "Phase")), self.f)
        H = H * eq_auto.reponse(_bandes_lues(self.d, f"{P}PEQOut{n}\\SV\\", 8) + _bandes_lues(self.d, f"{P}PEQ1\\SV\\", 12)
                                + list(self.propre.get(nom) or []), self.f)
        gain = _ms(self.d.get(X + "Gain")) + self.ecart_sens.get(nom, 0.0)   # « -12.6dB » : premier nombre
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


def delai_sortie(d, n: int) -> float:
    dl = f"{P}DelayOut{n}\\SV\\"
    return _ms(d.get(dl + "Amount")) if str(d.get(dl + "Delay")).strip() == "On" else 0.0


def ecrire_delai(d, n: int, cible_ms: float, journal, ip: str) -> float:
    """Va au délai cible par pas ≤ 0,5 ms (écriture + relecture à chaque pas). Renvoie le délai relu."""
    if not 0 <= cible_ms <= 30:
        raise Annule(f"délai {cible_ms:.2f} ms hors [0, 30] ms sur la sortie {n}")
    dl = f"{P}DelayOut{n}\\SV\\"
    actuel = delai_sortie(d, n)
    while abs(cible_ms - actuel) > 0.005:
        pas = max(-PAS_DELAI_MS, min(PAS_DELAI_MS, cible_ms - actuel))
        val = round(actuel + pas, 2)
        relu, ok, err = dd.ecrire_et_relire(d, dl + "Amount", f"{val:.2f}ms", dd.decrire_param(d, dl + "Amount"))
        dd.ecrire_et_relire(d, dl + "Delay", "On" if val > 0 else "Off", None)
        lu = delai_sortie(d, n) if val > 0 else 0.0
        journal(f"ESSAI {ip} DelayOut{n} : {actuel:.2f} -> {val:.2f} ms (relu {relu}){'' if abs(lu - val) < 0.011 else ' ECART'}")
        if abs(lu - val) >= 0.011:
            raise Annule(f"délai sortie {n} : écrit {val:.2f} ms, relu {relu} ({err})")
        actuel = val
    return actuel


def chemins_suivis(plans: list[dict]) -> list[str]:
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



def _snr(m: dict, bruit: dict, voie_plan: dict, decalage_db: float = 0.0) -> float:
    """Signal (ramené à −20 dBFS, donc + decalage_db pour le niveau réel joué) sur le bruit de fond, dans la bande."""
    f = np.asarray(m["f_hz"])
    lo = (voie_plan.get("hpf") or {}).get("hz") or 20.0
    hi = (voie_plan.get("lpf") or {}).get("hz") or 16000.0
    k = (f >= lo) & (f <= hi)
    s = np.asarray(m["mag_db"])[k]
    b = np.asarray(bruit["mag_db"])[k]
    return float(10 * np.log10(np.mean(10 ** (s / 10))) + decalage_db - 10 * np.log10(np.mean(10 ** (b / 10))))


class Annonce(ValueError):
    """Sans --appliquer : on annonce ce qui serait écrit, rien n'est touché (code de sortie 0)."""


PAS_ANNONCE = ("pas par écriture : gain ±3 dB, délai ±0,5 ms, EQ ±3 dB par bande, phase ±45° ; le plan lui-même est "
               "écrit d'un bloc, sorties mutées (construction de preset)")


def annonce(ip: str, suivis: list[str], quoi: str) -> str:
    """Texte d'annonce : blocs touchés (avant = état relu au lancement, rétabli à la fin)."""
    blocs: dict[str, int] = {}
    for ch in suivis:
        m = re.match(r"\\\\Preset\\([^\\]+)", ch)
        blocs[m.group(1) if m else ch] = blocs.get(m.group(1) if m else ch, 0) + 1
    return (f"ANNONCE — rien n'a été écrit. {quoi} écrirait sur {ip} : "
            + ", ".join(f"{b} ({n})" for b, n in sorted(blocs.items()))
            + f" — {len(suivis)} paramètres, relus avant (instantané) et remis à l'état du début à la fin ; sorties "
            f"mutées entre les mesures ; {PAS_ANNONCE}. Relancer avec --appliquer après le « go » de Louis.")


class Seance:
    """Un lancement de mesure à une position. Usage :
        with Seance(d, moteur, dossier, etat, position, plans, journal, ip, niveau_max,
                    protection_validee, signal_autorise, sauver) as s:
            s.mesure(nom, {sorties}, niveau) / s.rampe(plan, tag) / s.aligner(...)
    En sortie (normale, erreur, Ctrl-C) : 6 sorties mutées, état d'origine rétabli → s.resultat["retablissement"]."""

    def __init__(self, d, moteur, dossier, etat: dict, position: dict, plans: list[dict], journal, ip: str,
                 niveau_max: float, protection_validee: bool, signal_autorise: bool, sauver, etiquette: str = "ESSAI",
                 appliquer: bool = False):
        if not protection_validee:
            raise ValueError("le plan écrit des PROTECTIONS (limiteurs, filtres, mutes) : --protection-validee requis")
        if niveau_max > NIVEAU_PLAFOND_DBFS:
            raise ValueError(f"--niveau-max {niveau_max} dBFS au-dessus du plafond du kit ({NIVEAU_PLAFOND_DBFS} dBFS)")
        if not moteur.simule and not signal_autorise:
            raise ValueError("chaque mesure JOUE UN SWEEP : --signal-autorise seulement après le « go » de Louis "
                             "(niveau sûr, personne exposé, pas de concert)")
        if etat.get("niveau_dbfs") and max(etat["niveau_dbfs"].values()) > niveau_max:
            raise ValueError(f"niveaux retenus {etat['niveau_dbfs']} au-dessus de --niveau-max {niveau_max} dBFS : "
                             "relancer (preparer) pour refaire la rampe sous ce plafond")
        self.d, self.moteur, self.dossier, self.etat, self.pos = d, moteur, Path(dossier), etat, position
        self.journal, self.ip, self.niveau_max, self.sauver, self.etiquette = journal, ip, niveau_max, sauver, etiquette
        self.appliquer = appliquer
        self.sorties = {x["sortie"]: x["nom"] for x in plans[0]["voies"]}
        self.suivis = chemins_suivis(plans)        # refuse aussi un plan devenu incohérent (config modifiée)
        self.depart = min(NIVEAU_DEPART_DBFS, niveau_max)
        self.niveaux = dict(etat.get("niveau_dbfs") or {})
        self.rep = self.dossier / "mesures"
        self.resultat = {"position": position["nom"], "mesures": 0, "alertes": []}

    def __enter__(self):
        import dbx_hiqnet
        if not self.appliquer:          # avant tout instantané ou écriture : annonce seulement
            raise Annonce(annonce(self.ip, self.suivis, f"{self.etiquette} position {self.pos['nom']}"))
        d = self.d
        self.origine = {ch: (d.get(ch) or "").strip() for ch in self.suivis}   # relu À CHAQUE lancement
        try:
            snap = str(dbx_hiqnet.instantane_avant(d, self.ip, f"avant-{self.etiquette.lower()}-{self.pos['nom']}"))
        except ValueError as e:
            raise ValueError(f"instantané complet impossible : {e}") from None
        self.resultat["instantane"] = snap
        self.etat.setdefault("instantanes", {})[self.pos["nom"]] = {"fichier": snap, "suivis": self.origine}
        self.sauver(self.etat)
        self.rep.mkdir(parents=True, exist_ok=True)
        self.journal(f"{self.etiquette} {self.ip} début position {self.pos['nom']} ({self.pos['distance_m']} m, "
                     f"{self.pos['hauteur_m']} m) — {'SIMULATION acoustique' if self.moteur.simule else 'REW, signal autorisé par Louis'}"
                     f", plafond {self.niveau_max} dBFS")
        try:
            self.moteur.verifier()
            if self.moteur.simule:
                dd.muter_et_verifier(d)             # simulation : la machine reste mutée de bout en bout
        except BaseException as e:
            self.__exit__(type(e), e, None)
            raise
        return self

    def __exit__(self, typ, e, tb):
        d, j = self.d, self.journal
        if e is not None:               # Ctrl-C compris : on re-mute avant tout
            try:
                dd.muter_et_verifier(d)
                j(f"{self.etiquette} {self.ip} ARRET position {self.pos['nom']} : {e!r} — 6 sorties re-mutées (relu)")
            except Exception as e2:   # noqa: BLE001
                j(f"{self.etiquette} {self.ip} ARRET position {self.pos['nom']} : {e!r} — RE-MUTE NON CONFIRMÉ ({e2}) : couper les amplis")
                print(f"!!! RE-MUTE NON CONFIRMÉ ({e2}) : COUPER LES AMPLIS", file=sys.stderr)
            self.resultat["erreur"] = repr(e)
        if "retablissement" not in self.resultat:
            try:
                self.resultat["retablissement"] = retablir(d, self.origine, j, self.ip, self.etat["config"], self.sorties)
            except Exception as e3:   # noqa: BLE001
                self.resultat["retablissement"] = {"erreur": repr(e3)}
                j(f"{self.etiquette} {self.ip} rétablissement en échec : {e3!r} — vérifier les mutes, couper les amplis au moindre doute")
                print(f"!!! RÉTABLISSEMENT EN ÉCHEC ({e3!r}) : vérifier les mutes ; couper les amplis au moindre doute",
                      file=sys.stderr)
        if not retablissement_complet(self.resultat):
            msg = (f"{self.etiquette} {self.ip} RÉTABLISSEMENT INCOMPLET position {self.pos['nom']} : "
                   f"{self.resultat['retablissement']} — paramètres encore en état d'essai ; sorties laissées mutées")
            j(msg)
            print("!!! " + msg, file=sys.stderr)
        return False

    def mesure(self, nom: str, ouvertes: set[int], niveau: float) -> dict:
        """Une mesure ; magnitudes ramenées à −20 dBFS (système linéaire) pour comparer des niveaux différents."""
        if niveau > self.niveau_max:
            raise Annule(f"niveau {niveau} dBFS au-dessus de --niveau-max {self.niveau_max}")
        m_ = self.moteur
        if m_.simule:
            m_.ouvertes = set(ouvertes)
        else:
            ouvrir(self.d, ouvertes)
        try:
            m = m_.mesurer(f"{self.dossier.name}_{nom}", niveau)
        finally:
            if m_.simule:
                m_.ouvertes = set()
            else:
                ouvrir(self.d, set())
        m["niveau_dbfs"] = niveau
        m["mag_db"] = [x - (niveau - NIVEAU_DEPART_DBFS) for x in m["mag_db"]]
        (self.rep / f"{nom}.json").write_text(json.dumps(m), encoding="utf-8")
        self.resultat["mesures"] += 1
        return m

    def niv(self, *noms: str) -> float:      # paire, système : le plus BAS des niveaux des voies concernées
        return min(self.niveaux[n] for n in noms)

    def rampe(self, plan: dict, tag: str) -> dict:
        """Niveau PAR VOIE (une fois par essai) : départ min(−20, plafond), +3 dB tant que le signal est < 15 dB
        au-dessus du bruit de fond (mesuré sorties toutes mutées)."""
        if self.niveaux:
            return self.niveaux
        bruit = self.mesure(f"{tag}_bruit", set(), self.depart)
        for x in plan["voies"]:
            n_ = self.depart
            while True:
                m = self.mesure(f"{tag}_rampe_{x['nom']}_{n_:+.0f}", {x["sortie"]}, n_)
                snr = _snr(m, bruit, x, n_ - self.depart)
                if snr >= SNR_VISE_DB or n_ + PAS_NIVEAU_DB > self.niveau_max:
                    break
                n_ += PAS_NIVEAU_DB
            if snr < SNR_MIN_DB:
                raise Annule(f"{x['nom']} : rapport signal/bruit {snr:.1f} dB < {SNR_MIN_DB} dB au plafond "
                             f"{self.niveau_max} dBFS — vérifier câblage, micro, ampli")
            self.niveaux[x["nom"]] = n_
        self.etat["niveau_dbfs"] = self.niveaux
        self.sauver(self.etat)
        self.journal(f"{self.etiquette} {self.ip} niveaux de mesure retenus : {self.niveaux} dBFS")
        return self.niveaux

    def solos(self, plan: dict, tag: str) -> dict:
        return {x["nom"]: self.mesure(f"{tag}_{x['nom']}", {x["sortie"]}, self.niveaux[x["nom"]]) for x in plan["voies"]}

    def aligner(self, plan: dict, coupures_hz: dict, solos: dict, tag: str, ident: str) -> dict:
        """Aligne chaque raccord, de l'aigu vers le grave (comme le calcul) : REW (IR) ou simulé, contre-vérifié par
        analyse_raccord.meilleur_delai ; ±0,5 ms par écriture puis remesure ; une inversion n'est que PROPOSÉE.
        Met à jour `solos` ; renvoie les délais relus par voie."""
        d, al_ = self.d, self.resultat["alertes"]
        par_nom = {x["nom"]: x for x in plan["voies"]}
        noms = list(par_nom)
        for bas, haut in reversed(cles_raccords(plan)):
            fc = coupures_hz[f"{bas}/{haut}"]
            for _ in range(ITERATIONS_ALIGNEMENT):
                al = self.moteur.aligner(solos[haut], solos[bas], fc)
                loc = ar.meilleur_delai(solos[haut], solos[bas], fc, PLAGE_ALIGNEMENT_MS)
                al_ += [f"{ident} {bas}/{haut} : {a}" for a in al["alertes"]]
                if al["inverser_b"] or loc["inverser_b"]:
                    msg = (f"{ident} {bas}/{haut} : la mesure propose d'INVERSER {bas} (efficacité "
                           f"{loc['efficacite_inverse']} inversé contre {loc['efficacite_non_inverse']} ; "
                           "polarité = protection : décision de Louis, non appliquée ; mesuré au "
                           "meilleur délai non inversé)")
                    if msg not in al_:
                        al_.append(msg)
                if al["inverser_b"]:
                    delta = loc["delai_non_inverse_ms"]
                else:
                    delta = al["delai_b_ms"]
                    if not self.moteur.simule and abs(delta - loc["delai_non_inverse_ms"]) > 250 / fc:
                        al_.append(f"{ident} {bas}/{haut} : REW {delta:+.2f} ms et contre-vérification "
                                   f"{loc['delai_non_inverse_ms']:+.2f} ms divergent de plus d'1/4 de période : "
                                   "REW (IR) retenu, vérifier la somme mesurée")
                if abs(delta) <= TOLERANCE_DELAI_MS:
                    break
                nb = delai_sortie(d, par_nom[bas]["sortie"]) + delta
                if nb < 0:        # pas de délai négatif : on retarde d'autant la voie haute et celles au-dessus
                    for n in noms[noms.index(haut):]:
                        s_ = par_nom[n]["sortie"]
                        ecrire_delai(d, s_, delai_sortie(d, s_) - nb, self.journal, self.ip)
                        solos[n] = self.mesure(f"{tag}_{n}", {s_}, self.niveaux[n])
                    nb = 0.0
                ecrire_delai(d, par_nom[bas]["sortie"], nb, self.journal, self.ip)
                solos[bas] = self.mesure(f"{tag}_{bas}", {par_nom[bas]["sortie"]}, self.niveaux[bas])
            else:
                al_.append(f"{ident} {bas}/{haut} : alignement non convergé en {ITERATIONS_ALIGNEMENT} itérations")
        return {n: delai_sortie(d, x["sortie"]) for n, x in par_nom.items()}
