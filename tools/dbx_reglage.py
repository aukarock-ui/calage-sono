#!/usr/bin/env python3
r"""
dbx_reglage.py — Écrire N'IMPORTE QUEL paramètre du VENU360 par un seul chemin de contrôle.

Utilisé par `dbx_hiqnet.py set | regler | lot`. Toutes les règles de CLAUDE.md passent ici :
  * description machine (bornes, choix, mètre, inactif) + carte YAML, la plus stricte l'emporte ;
  * caractères de contrôle, chemins système interdits ;
  * PROTECTION (limiteur, filtres, mutes, gains, routage) : --protection-validee ;
  * seuil de limiteur : plafond calc_systeme (config + voie), valeur retenue par la machine revérifiée ;
  * générateur : --signal-valide et amplitude ≤ −30 dB ;
  * niveau de sortie analogique (\\Node\Settings\SV\Output<n>Gain) : refusé si la config ne porte pas déjà la
    nouvelle valeur (sinon tous les plafonds seraient faux de jusqu'à 18 dB) ;
  * PAS MAXIMUM par écriture : gains et EQ ±3 dB, délai ±0,5 ms, phase ±45°. Au-delà : refus, sauf
    --par-pas (découpé, chaque pas écrit puis relu) ou --pas-libre (construction de preset : seulement si les
    6 sorties sont relues mutées).

Alias (commande `regler`, à partir de la config du système) :
  VOIE.gain | VOIE.phase | VOIE.polarite | VOIE.hpf.freq | VOIE.hpf.type | VOIE.lpf.freq | VOIE.lpf.type
  VOIE.niveau | VOIE.mute | VOIE.delai | VOIE.delai.actif
  VOIE.eq (On/Off) | VOIE.eq.<1-8>.<gain|freq|q|type|pente>
  VOIE.limiteur.<seuil|actif|attaque|maintien|relachement|peakstop|overshoot|overeasy|auto>
  ENTREE.eq | ENTREE.eq.<1-12>.<…>   SORTIE<n>.… (sortie sans voie)   SYSTEME.niveau_sortie.<n>
  Un chemin complet « \\\\Preset\\… » est aussi accepté.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx_description as dd   # noqa: E402
from garde_fous import nombre, refuser_controle, verifier_plafond   # noqa: E402

P = "\\\\Preset\\"
NIVEAU_SORTIE = re.compile(r"\\\\Node\\Settings\\SV\\Output(\d)Gain$", re.I)

# (motif du chemin, famille d'unité, pas maximum, unité d'écriture des pas intermédiaires)
PAS_MAX = [
    (re.compile(r"\\Delay(Out|Mid)\d+\\SV\\Amount$", re.I), "temps", 0.5, "ms"),
    (re.compile(r"\\SV\\(Band_\d+_|Sub_)?Phase$", re.I), "angle", 45.0, "deg"),
    (re.compile(r"\\\\Preset\\.*(Gain|\\GEQ\d+\\SV\\.*)$", re.I), "niveau", 3.0, "dB"),
]


class Refus(ValueError):
    """Demande refusée AVANT toute écriture."""


class Echec(RuntimeError):
    """Écriture faite mais non conforme (l'état laissé est décrit dans le message)."""


@dataclass
class Options:
    appliquer: bool = False
    protection_validee: bool = False
    signal_valide: bool = False
    config: str | None = None
    voie: str | None = None
    depasser_plafond: bool = False
    hors_carte: bool = False
    par_pas: bool = False
    pas_libre: bool = False
    cumul_valide: bool = False          # --par-pas au-delà de 2 pas sans remesure (accord de Louis, distinct des protections)
    journal: object = None              # fonction texte -> None
    dossier_instantanes: Path | None = None
    notes: list = field(default_factory=list)


def regle_pas(chemin: str) -> tuple[str, float, str] | None:
    """(famille, pas maximum, unité) si ce paramètre est soumis au pas maximum, sinon None."""
    if chemin.lower().startswith("\\\\node\\"):
        return None
    for motif, fam, pas, unite in PAS_MAX:
        if motif.search(chemin):
            return fam, pas, unite
    return None


def decouper(avant: float, cible: float, pas: float) -> list[float]:
    """Valeurs successives de avant (exclu) à cible (inclus), écart ≤ pas entre deux valeurs."""
    import math
    n = max(1, math.ceil(abs(cible - avant) / pas - 1e-9))
    return [round(avant + (cible - avant) * k / n, 4) for k in range(1, n + 1)]


def _formater(v: float, fam: str, unite: str, modele: str) -> str:
    """Valeur intermédiaire, dans l'unité de base ; la consigne finale garde la forme demandée."""
    if fam == "temps":
        return f"{v:.2f}ms"
    if fam == "angle":
        return f"{v:g} deg" if " " in modele else f"{v:g}deg"
    return f"{v:.1f}dB"


def _sorties_mutees(d) -> bool:
    try:
        return all(str(d.get(c) or "").strip() == "On" for c in dd.SORTIES_MUTE)
    except ValueError:
        return False


def _verifier_niveau_sortie(chemin: str, valeur: str, config: str | None) -> str:
    """Niveau de sortie analogique : il cale l'unité de TOUS les plafonds. Refus sauf config déjà à jour."""
    import yaml
    if not config:
        raise Refus(f"{chemin} change le calibrage de tous les seuils de limiteur : --config <système> obligatoire, "
                    "avec processeur_reglage_sortie déjà à la nouvelle valeur et confirmée")
    doc = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
    voulu, dans_config = nombre(str(valeur).replace(" ", "")), nombre(str(doc.get("processeur_reglage_sortie", "")).replace(" ", "") + "dBu")
    if voulu is None or dans_config is None or abs(voulu - dans_config) > 0.01 or not doc.get("processeur_reglage_sortie_confirme"):
        raise Refus(f"{chemin} = {valeur} mais {config} porte processeur_reglage_sortie = "
                    f"{doc.get('processeur_reglage_sortie')!r} (confirmé : {doc.get('processeur_reglage_sortie_confirme')}). "
                    "Mettre la config à jour d'abord, recalculer les plafonds (calc_systeme), puis écrire")
    return f"niveau de sortie {valeur} = config {config}"


def _verifier_niveau_relu(d, n: int, config: str) -> str:
    """Le plafond n'a de sens que si Output<n>Gain relu EN DIRECT = processeur_reglage_sortie de la config."""
    voulu = str(charger_config(config).get("processeur_reglage_sortie") or "").replace(" ", "").lower()
    try:
        relu = str(d.get(f"\\\\Node\\Settings\\SV\\Output{n}Gain") or "").replace(" ", "").lower()
    except ValueError:
        relu = ""
    if not voulu or nombre(relu) is None or abs(nombre(relu) - nombre(voulu)) > 0.01:
        raise Refus(f"seuil de la sortie {n} : niveau de sortie relu {relu or 'illisible'} ≠ config "
                    f"processeur_reglage_sortie {voulu or 'absent'} : le plafond serait faux")
    return f"niveau sortie {n} relu {relu} = config"


def controler(d, chemin: str, valeur: str, o: Options) -> dict:
    """Tous les contrôles, AUCUNE écriture. Renvoie {desc, avant, pas[], notes[], protection}. Lève Refus."""
    from dbx_hiqnet import CHEMINS_INTERDITS, est_protection, regle_carte, valider_valeur
    if any(m in chemin.lower() for m in CHEMINS_INTERDITS):
        raise Refus("chemin système (mot de passe, reboot, commande de config)")
    try:
        refuser_controle(chemin, valeur)
    except ValueError as e:
        raise Refus(str(e)) from None
    notes = []
    desc = dd.decrire_param(d, chemin)
    hors_carte = False
    if desc is not None:
        motif = dd.valider_machine(desc, valeur)
        if motif:
            raise Refus(f"{chemin} = {valeur} : {motif}")
        if regle_carte(chemin) is not None:
            motif = valider_valeur(chemin, valeur)
            if motif:
                raise Refus(f"{chemin} = {valeur} : {motif} (carte data/venu360_parametres.yaml)")
        if chemin.lower().startswith("\\\\node\\") and not dd.REGLAGES_APPAREIL_TESTABLES.search(chemin) and not o.hors_carte:
            raise Refus(f"{chemin} est un réglage d'appareil non cartographié : --hors-carte après vérification")
    else:
        motif = valider_valeur(chemin, valeur)
        if motif and not (o.hors_carte and motif.startswith("chemin absent")):
            raise Refus(f"{chemin} = {valeur} : {motif}"
                        + (". Chemin non cartographié : --hors-carte après vérification par get" if motif.startswith("chemin absent") else ""))
        hors_carte = bool(motif)
    if NIVEAU_SORTIE.search(chemin):
        notes.append(_verifier_niveau_sortie(chemin, valeur, o.config))
        if not _sorties_mutees(d):          # saut de niveau possible de 18 dB : jamais sorties actives
            raise Refus(f"{chemin} : niveau analogique de sortie, seulement les 6 sorties relues mutées")
    m_lim = re.search(r"\\Limiter(\d)\\SV\\Threshold$", chemin, re.I)
    if m_lim and o.config:                   # le plafond doit être celui de la voie câblée sur CETTE sortie
        n = int(m_lim.group(1))
        nom = next((str(v.get("nom")) for v in charger_config(o.config).get("voies") or [] if int(v.get("sortie", 0)) == n), None)
        if nom is None:
            raise Refus(f"Limiter{n} : la sortie {n} n'a pas de voie dans {o.config} : aucun plafond applicable")
        if o.voie and o.voie.upper() != nom.upper():
            raise Refus(f"Limiter{n} = sortie {n} = voie {nom} dans {o.config}, pas {o.voie} : plafond de la mauvaise voie")
        o.voie = nom
        notes.append(_verifier_niveau_relu(d, n, o.config))
    m_bosse = re.search(r"\\PEQOut(\d)\\SV\\Band_\d+_Gain$", chemin, re.I)
    if m_bosse and (nombre(valeur) or 0) > 0:
        import dbx_plan
        ordre = dd.chaine_sortie_machine(d)
        if ordre.get("chaine_sortie") != "peq_avant_limiteur" or int(m_bosse.group(1)) not in (ordre.get("sorties") or []):
            raise Refus(f"bosse d'EQ de voie : ordre PEQ/limiteur relu = {ordre.get('chaine_sortie')} "
                        f"({ordre.get('motif', '')}), sorties couvertes {ordre.get('sorties')}")
        if dbx_plan.chaine_sortie() != "peq_avant_limiteur":
            raise Refus(f"bosse d'EQ de voie : ordre enregistré (venu360_phase.yaml) = {dbx_plan.chaine_sortie()}")
    if re.search(r"\\Limiter\d\\SV\\OvershootDb$", chemin, re.I) and not o.depasser_plafond:
        a_ov, v_ov = nombre(str(d.get(chemin) or "")), nombre(valeur)
        if a_ov is None or v_ov is None or v_ov > a_ov:
            raise Refus(f"{chemin} : relever l'overshoot peut laisser passer plus que le seuil (plafond) : "
                        "seulement à la baisse, sauf --depasser-plafond")
    m_act = re.search(r"\\(DelayOut|PEQOut)(\d)\\SV\\(Delay|ParametricEQ)$|\\PEQ1\\SV\\ParametricEQ$", chemin, re.I)
    if m_act and str(valeur).strip() == "On" and str(d.get(chemin) or "").strip() != "On":
        n_act = int(m_act.group(2)) if m_act.group(2) else None   # activer = appliquer d'un coup le délai / l'EQ mémorisés
        mute = [f"{P}OutputGains\\SV\\Channel_{n_act}_Mute"] if n_act else dd.SORTIES_MUTE
        if not all(str(d.get(c) or "").strip() == "On" for c in mute):
            raise Refus(f"{chemin} = On applique d'un coup tout le bloc mémorisé (hors pas maximum) : "
                        f"seulement sortie{'s' if not n_act else ''} {n_act or '1-6'} relue(s) mutée(s)")
    if re.search(r"\\\\Node\\Settings\\SV\\Input\dGain$", chemin, re.I) and not _sorties_mutees(d):
        raise Refus(f"{chemin} : sensibilité d'entrée (saut jusqu'à 14 dB en une écriture) : seulement les 6 sorties relues mutées")
    if dd.est_signal(chemin):
        if not o.signal_valide:
            raise Refus("générateur de signal. Il faut l'accord explicite de Louis (niveau sûr, personne exposé) : --signal-valide")
        try:
            ampl = d.get(P + "SignalGenerator\\SV\\Signal Amplitude")
        except ValueError:
            ampl = None
        m_sig = dd.verifier_signal(chemin, valeur, ampl)
        if m_sig:
            raise Refus(m_sig)
    if re.search(r"\\Limiter\d*\\SV\\Threshold$", chemin, re.I):
        try:
            notes.append(verifier_plafond(nombre(valeur), o.config, o.voie, "dBFS", o.depasser_plafond))
        except ValueError as e:
            raise Refus(str(e)) from None
    avant = d.get(chemin)
    avant = avant.strip() if isinstance(avant, str) else avant
    if avant is None or avant == "":
        raise Refus(f"{chemin} : valeur actuelle illisible (délai dépassé ?) : rien n'est écrit sans état d'avant connu")
    if m_lim and nombre(str(avant)) is None:
        raise Refus(f"{chemin} : seuil actuel illisible ({avant!r})")
    protection = est_protection(chemin)
    if protection and not o.protection_validee:
        raise Refus("paramètre de PROTECTION. Il faut la validation explicite de Louis (--protection-validee)")
    pas = [valeur]
    regle = regle_pas(chemin)
    a_av, a_ci = dd.analyser(avant), dd.analyser(valeur)
    if regle and a_ci and a_ci[1] == regle[0] and not (a_av and a_av[1] == regle[0]):
        raise Refus(f"{chemin} : valeur actuelle {avant!r} illisible dans l'unité {regle[0]} : pas maximum invérifiable")
    if regle and a_av and a_ci and a_av[1] == a_ci[1] == regle[0]:
        fam, pas_max, unite = regle
        ecart = abs(a_ci[0] - a_av[0])
        if o.par_pas and ecart > 2 * pas_max + 1e-6 and not o.cumul_valide:
            raise Refus(f"--par-pas : écart cumulé {ecart:g} > {2 * pas_max:g} sans remesure : --cumul-valide "
                        "(accord de Louis) ou plusieurs commandes avec mesure entre elles")
        if ecart > pas_max + 1e-6:
            if o.pas_libre:
                if not _sorties_mutees(d):
                    raise Refus(f"--pas-libre : seulement en construction de preset, les 6 sorties relues mutées "
                                f"(écart demandé {ecart:g} > {pas_max:g})")
                notes.append(f"PAS LIBRE (sorties mutées) : écart {ecart:g} en une écriture")
            elif o.par_pas:
                vals = decouper(a_av[0], a_ci[0], pas_max)
                pas = [_formater(v, fam, unite, valeur) for v in vals[:-1]] + [valeur]
                notes.append(f"par pas : {len(pas)} écritures de {pas_max:g} au plus")
            else:
                raise Refus(f"écart {ecart:g} > pas maximum {pas_max:g} par écriture ({avant} → {valeur}) : "
                            "--par-pas pour découper (puis remesurer), ou --pas-libre sorties mutées")
    return {"desc": desc, "avant": avant, "pas": pas, "notes": notes, "protection": protection, "hors_carte": hors_carte}


def _journal(o: Options, texte: str) -> None:
    if o.journal:
        o.journal(texte)


def ecrire_controle(d, ip: str, chemin: str, valeur: str, o: Options) -> dict:
    """Contrôle puis (si o.appliquer) écrit, pas par pas, chaque pas relu. Lève Refus ou Echec."""
    c = controler(d, chemin, valeur, o)
    r = {"chemin": chemin, "avant": c["avant"], "demande": valeur, "pas": c["pas"], "notes": c["notes"],
         "apres": None, "simulation": not o.appliquer}
    if not o.appliquer:
        return r
    if o.dossier_instantanes:
        snap = Path(o.dossier_instantanes) / f"{dt.datetime.now():%Y%m%d-%H%M%S-%f}-avant-set.json"
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_text(json.dumps({chemin: c["avant"]}, ensure_ascii=False), encoding="utf-8")
    seuil = bool(re.search(r"\\Limiter\d*\\SV\\Threshold$", chemin, re.I))
    for i, v in enumerate(c["pas"], 1):
        relu, ok, err = dd.ecrire_et_relire(d, chemin, v, c["desc"])
        if not ok and c["desc"] and dd.arrondi_machine(c["desc"], v, relu, chemin):
            _journal(o, f"ARRONDI {ip} {chemin} : demandé {v}, la machine a retenu {relu} (sa grille)")
            ok = True
        elif not ok and c["desc"] is None:
            from dbx_hiqnet import relecture_conforme
            ok = relecture_conforme(v, relu, chemin)
        _journal(o, f"SET {ip} {chemin} : {c['avant'] if i == 1 else c['pas'][i - 2]} -> {v} (relu : {relu})"
                    + (f" [pas {i}/{len(c['pas'])}]" if len(c["pas"]) > 1 else "")
                    + "".join(f" [{n}]" for n in c["notes"]) + (" [HORS CARTE]" if c["hors_carte"] else "")
                    + ("" if c["desc"] else " [sans description machine]") + ("" if ok else f" ECART {err or ''}"))
        r["apres"] = relu
        if seuil and nombre(str(relu or "").split("/")[0]) is not None:
            try:
                verifier_plafond(nombre(str(relu)), o.config, o.voie, "dBFS", o.depasser_plafond)
            except ValueError as e:
                try:                                       # l'ancien seuil n'est réécrit que s'il respecte le plafond
                    verifier_plafond(nombre(str(c["avant"])), o.config, o.voie, "dBFS", o.depasser_plafond)
                    remis, _, _ = dd.ecrire_et_relire(d, chemin, str(c["avant"]), c["desc"])
                    etat = f"ancien seuil réécrit, relu {remis}"
                except ValueError:
                    etat = f"ancien seuil {c['avant']} AUSSI au-dessus du plafond : rien réécrit, à BAISSER à la main"
                _journal(o, f"ECART PLAFOND {ip} {chemin} : relu {relu} : {e} ; {etat}")
                raise Echec(f"seuil retenu par la machine ({relu}) au-dessus du plafond : {e}. {etat}")
        elif seuil:
            _journal(o, f"ECART PLAFOND {ip} {chemin} : seuil relu illisible ({relu!r}) : plafond invérifiable")
            raise Echec(f"{chemin} : seuil relu illisible ({relu!r}) : plafond invérifiable, relire avant tout")
        if not ok:
            raise Echec(f"{chemin} : pas {i}/{len(c['pas'])} = {v}, relu {relu} ({err or 'écart'}). "
                        "Ne pas continuer avant d'avoir compris l'écart (pas, unité, plage, verrouillage)")
    if NIVEAU_SORTIE.search(chemin):          # les 6 niveaux doivent tous correspondre à la config
        voulu = str(charger_config(o.config).get("processeur_reglage_sortie", "")).replace(" ", "").lower() + "dbu"
        relus = {n: str(d.get(f"\\\\Node\\Settings\\SV\\Output{n}Gain") or "").replace(" ", "").lower() for n in range(1, 7)}
        autres = {n: x for n, x in relus.items() if x != voulu}
        r["notes"].append(f"niveaux de sortie relus : {relus}")
        if autres:
            msg = (f"ATTENTION niveaux de sortie {autres} ≠ config {voulu} : plafonds faux sur ces sorties — les régler "
                   "aussi avant de démuter (appliquer-plan refuse tant qu'ils diffèrent)")
            _journal(o, f"NIVEAUX SORTIE {ip} : {msg}")
            r["notes"].append(msg)
    return r


# ---------------- alias par voie ----------------
_LIM = {"seuil": "Threshold", "actif": "Limiter", "attaque": "Attack", "maintien": "Hold", "relachement": "Release",
        "peakstop": "PeakStopPlus", "overshoot": "OvershootDb", "overeasy": "OverEasy", "auto": "Auto"}
_EQ = {"gain": "Gain", "freq": "Frequency", "q": "Q", "type": "Type", "pente": "Slope"}


def charger_config(config: str | None) -> dict:
    import yaml
    return yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {} if config else {}


def resoudre(alias: str, config: str | None) -> tuple[str, str | None]:
    """Alias -> (chemin, voie pour le plafond). Lève Refus si inconnu."""
    if alias.startswith("\\\\"):
        return alias, None
    morceaux = alias.split(".")
    tete, reste = morceaux[0].upper(), [m.lower() for m in morceaux[1:]]
    doc = charger_config(config)
    voies = {str(v.get("nom", "")).upper(): v for v in doc.get("voies") or []}
    if tete == "ENTREE":
        if reste == ["eq"]:
            return P + "PEQ1\\SV\\ParametricEQ", None
        if len(reste) == 3 and reste[0] == "eq" and reste[1].isdigit() and 1 <= int(reste[1]) <= 12 and reste[2] in _EQ:
            return P + f"PEQ1\\SV\\Band_{int(reste[1])}_{_EQ[reste[2]]}", None
        raise Refus(f"alias ENTREE inconnu : {alias} (ENTREE.eq, ENTREE.eq.<1-12>.<{'|'.join(_EQ)}>)")
    if tete == "SYSTEME":
        if len(reste) == 2 and reste[0] == "niveau_sortie" and reste[1].isdigit() and 1 <= int(reste[1]) <= 6:
            return f"\\\\Node\\Settings\\SV\\Output{int(reste[1])}Gain", None
        raise Refus(f"alias SYSTEME inconnu : {alias} (SYSTEME.niveau_sortie.<1-6>)")
    m = re.fullmatch(r"SORTIE([1-6])", tete)
    if m:
        n, voie = int(m.group(1)), None
    elif tete in voies:
        n, voie = int(voies[tete]["sortie"]), str(voies[tete]["nom"])
    else:
        raise Refus(f"voie inconnue : {morceaux[0]} (config {config} : {', '.join(voies) or 'aucune voie'})")
    X, L, D, E = f"{P}CrossoverIIR{n}\\SV\\Band_1_", f"{P}Limiter{n}\\SV\\", f"{P}DelayOut{n}\\SV\\", f"{P}PEQOut{n}\\SV\\"
    simples = {("gain",): X + "Gain", ("phase",): X + "Phase", ("polarite",): X + "Polarity",
               ("hpf", "freq"): X + "HPFrequency", ("hpf", "type"): X + "HPType",
               ("lpf", "freq"): X + "LPFrequency", ("lpf", "type"): X + "LPType",
               ("niveau",): f"{P}OutputGains\\SV\\Channel_{n}_Gain", ("mute",): f"{P}OutputGains\\SV\\Channel_{n}_Mute",
               ("delai",): D + "Amount", ("delai", "actif"): D + "Delay", ("eq",): E + "ParametricEQ"}
    if tuple(reste) in simples:
        return simples[tuple(reste)], voie
    if len(reste) == 2 and reste[0] == "limiteur" and reste[1] in _LIM:
        return L + _LIM[reste[1]], voie
    if len(reste) == 3 and reste[0] == "eq" and reste[1].isdigit() and 1 <= int(reste[1]) <= 8 and reste[2] in _EQ:
        return E + f"Band_{int(reste[1])}_{_EQ[reste[2]]}", voie
    raise Refus(f"alias inconnu : {alias} (voir l'aide de dbx_reglage.py)")


def verifier_cible(d, chemin: str, alias: str) -> None:
    """Un alias de voie suppose une sortie = un crossover d'une seule bande (preset « 6 × Mono Fullrange ») :
    sur un crossover multivoie, Band_1 ne serait pas la voie visée. Refus sinon (lecture seule)."""
    m = re.match(r"\\\\Preset\\(?:CrossoverIIR|Limiter|DelayOut|PEQOut)(\d)\\SV\\|\\\\Preset\\OutputGains\\SV\\Channel_(\d)_",
                 chemin)
    if not m or alias.startswith("\\\\"):
        return                               # ENTREE / SYSTEME : pas de voie de sortie
    n = m.group(1) or m.group(2)
    try:
        cles = d.ls(f"\\\\Preset\\CrossoverIIR{n}\\SV")
    except ValueError as e:
        raise Refus(f"{alias} : CrossoverIIR{n} illisible ({e})") from None
    if "Band_1_HPFrequency" not in cles or any(re.match(r"(Band_([2-9]|\d\d)_|Sub_)", k) for k in cles):
        raise Refus(f"{alias} : CrossoverIIR{n} n'est pas un crossover d'une bande (preset multivoie) — "
                    "l'alias viserait la mauvaise voie : chemin complet avec `set`")


# ---------------- lot avec retour arrière ----------------
def lire_lot(fichier: str) -> tuple[list[tuple[str, str]], str | None]:
    """YAML : {config: …, reglages: [{alias: valeur}, …]} ou [[alias, valeur], …]. -> ([(alias, valeur)], config)."""
    import yaml
    doc = yaml.safe_load(Path(fichier).read_text(encoding="utf-8")) or {}
    brut = doc.get("reglages") if isinstance(doc, dict) else doc
    items = []
    for x in brut or []:
        if isinstance(x, dict) and len(x) == 1:
            (k, v), = x.items()
        elif isinstance(x, (list, tuple)) and len(x) == 2:
            k, v = x
        else:
            raise Refus(f"ligne de lot illisible : {x!r} (attendu « - ALIAS: valeur »)")
        if isinstance(v, bool):                           # YAML : On / Off non quotés -> True / False
            v = "On" if v else "Off"
        items.append((str(k), str(v)))
    if not items:
        raise Refus(f"{fichier} : aucun réglage")
    return items, (doc.get("config") if isinstance(doc, dict) else None)


def lot(d, ip: str, items: list[tuple[str, str]], o: Options, avant_ecriture=None) -> dict:
    """Contrôle TOUT d'abord (rien n'est écrit si un seul réglage est refusé), puis écrit dans l'ordre.
    Au premier échec : retour arrière, dans l'ordre inverse, de tout ce qui a été touché (par pas si besoin).
    Les seuils de limiteur ne sont jamais remontés par le retour arrière (le seuil abaissé reste : plus protecteur)."""
    import copy
    prevus = []
    for alias, val in items:
        ch, voie = resoudre(alias, o.config)
        verifier_cible(d, ch, alias)
        oo = copy.copy(o)
        oo.voie = voie or o.voie
        oo.appliquer = False
        c = ecrire_controle(d, ip, ch, val, oo)          # lève Refus : rien n'est écrit
        prevus.append({"alias": alias, "chemin": ch, "voie": oo.voie, "avant": c["avant"], "demande": val,
                       "pas": len(c["pas"]), "notes": c["notes"]})
    r = {"prevus": prevus, "ecrits": [], "echec": None, "retour": [], "conforme": None}
    if not o.appliquer:
        return r
    if avant_ecriture:
        r["instantane"] = avant_ecriture()              # instantané complet, seulement si tout le lot est accepté
    _journal(o, f"LOT {ip} début : {len(prevus)} réglage(s)")
    for p in prevus:
        oo = copy.copy(o)
        oo.voie, oo.appliquer = p["voie"], True
        try:
            ecrire_controle(d, ip, p["chemin"], p["demande"], oo)
            r["ecrits"].append(p)
        except (Exception, KeyboardInterrupt) as e:       # Refus, Echec, connexion perdue, Ctrl-C…
            r["echec"] = f"{p['alias']} = {p['demande']} : {type(e).__name__ if not isinstance(e, (Refus, Echec)) else ''} {e}".strip()
            if not isinstance(e, Refus):                   # un Refus à la relecture en direct : rien n'a été écrit
                r["ecrits"].append(p)                      # partiellement écrit : à remettre aussi
            break
    if r["echec"]:
        _journal(o, f"LOT {ip} ÉCHEC : {r['echec']} — retour arrière de {len(r['ecrits'])} réglage(s)")
        # générateur et re-mutes d'abord (couper le signal, rendre le silence), puis le reste dans l'ordre inverse ;
        # un mute dont l'avant était « Off » n'est JAMAIS démuté par le retour arrière (_remettre le laisse On) ;
        # arrêt au PREMIER retour non conforme : on ne continue pas à écrire sur un état inconnu
        prio = [p for p in r["ecrits"] if dd.est_signal(p["chemin"])
                or (p["chemin"] in dd.SORTIES_MUTE and str(p["avant"]).strip() == "On")]
        reste = [p for p in reversed(r["ecrits"]) if p not in prio]
        for p in sorted(prio, key=lambda p: 0 if p["chemin"].endswith("\\Signal Generator") else 1) + reste:
            try:
                x = _remettre(d, ip, p, o)
            except Exception as e:          # connexion perdue, etc.
                x = {"chemin": p["chemin"], "remis": False, "erreur": f"{type(e).__name__}: {e}"}
            r["retour"].append(x)
            if not (x["remis"] or x.get("motif")):
                r["retour_interrompu"] = x["chemin"]
                _journal(o, f"LOT {ip} RETOUR ARRIÈRE INTERROMPU sur {x['chemin']} ({x.get('erreur') or x.get('relu')}) — "
                            "état à vérifier, restaurer l'instantané")
                break
    r["conforme"] = r["echec"] is None
    _journal(o, f"LOT {ip} fin : {'conforme' if r['conforme'] else 'ÉCHEC, retour arrière ' + str(r['retour'])}")
    return r


def _remettre(d, ip: str, p: dict, o: Options) -> dict:
    """Remet la valeur d'avant d'un réglage du lot (par pas pour un gain/délai/phase), après validation machine.
    Jamais : un seuil remonté, un générateur rallumé, une amplitude au-dessus de −30 dB, une valeur d'avant inconnue."""
    ch, avant = p["chemin"], p["avant"]
    if avant is None or str(avant).strip() == "":
        return {"chemin": ch, "remis": False, "erreur": "valeur d'avant inconnue : rien écrit"}
    if re.search(r"\\Limiter\d*\\SV\\Threshold$", ch, re.I):
        actuel = nombre(str(d.get(ch) or ""))
        if actuel is None:
            return {"chemin": ch, "remis": False, "erreur": "seuil actuel illisible : état inconnu"}
        if nombre(str(avant)) is None or nombre(str(avant)) > actuel:
            _journal(o, f"LOT {ip} retour arrière : {ch} laissé à {actuel} (plus bas : plus protecteur que {avant})")
            return {"chemin": ch, "remis": False, "motif": "seuil laissé plus bas (plus protecteur)"}
    if ch in dd.SORTIES_MUTE and str(avant).strip() == "Off":
        _journal(o, f"LOT {ip} retour arrière : {ch} laissé muté (était Off) : démute à la main, après contrôle")
        return {"chemin": ch, "remis": False, "motif": "sortie laissée mutée (plus sûr)"}
    if dd.est_signal(ch):
        if ch.endswith("\\Signal Generator") and str(avant).strip() != "Off":
            relu, ok, _ = dd.ecrire_et_relire(d, ch, "Off", dd.decrire_param(d, ch))
            _journal(o, f"LOT {ip} retour arrière : générateur laissé Off (était {avant}), relu {relu}")
            return {"chemin": ch, "remis": ok and relu == "Off", "relu": relu, "motif": "générateur laissé Off" if ok else None}
        if "amplitude" in ch.lower() and (nombre(str(avant)) is None or nombre(str(avant)) > dd.NIVEAU_SIGNAL_MAX_DB):
            return {"chemin": ch, "remis": False, "motif": f"amplitude laissée ≤ {dd.NIVEAU_SIGNAL_MAX_DB:g} dB (était {avant})"}
        if "signalgeneratorcontrol" in ch.lower() and str(avant).strip() != "SignalGeneratorOff":
            return {"chemin": ch, "remis": False, "motif": f"routage du générateur laissé tel quel (était {avant})"}
    if re.search(r"ms/.*ft", str(avant)):            # délai affiché « 1.5ms/1.69ft/0.51m » : écrire « 1.5ms »
        avant = str(avant).split("/")[0]
    desc = dd.decrire_param(d, ch)
    if desc is not None and dd.valider_machine(desc, str(avant)):
        return {"chemin": ch, "remis": False, "erreur": f"valeur d'avant {avant!r} refusée par la validation machine"}
    actuel = d.get(ch)
    vals = [str(avant)]
    regle, a_ac, a_av = regle_pas(ch), dd.analyser(actuel), dd.analyser(avant)
    if regle and not (a_ac and a_ac[1] == regle[0]):
        return {"chemin": ch, "remis": False, "erreur": f"valeur actuelle illisible ({actuel!r}) : pas maximum invérifiable"}
    if regle and a_ac and a_av and a_ac[1] == a_av[1] == regle[0]:
        vals = [_formater(v, regle[0], regle[2], str(avant)) for v in decouper(a_ac[0], a_av[0], regle[1])[:-1]] + [str(avant)]
    relu, ok = None, False
    for v in vals:
        relu, ok, _ = dd.ecrire_et_relire(d, ch, v, desc)
        ok = ok or bool(desc and dd.arrondi_machine(desc, v, relu, ch))
        if not ok:
            break
    _journal(o, f"LOT {ip} retour arrière : {ch} -> {avant} (relu {relu}){'' if ok else ' ECART'}")
    return {"chemin": ch, "remis": ok, "relu": relu}
