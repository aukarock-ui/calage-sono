#!/usr/bin/env python3
"""
dbx_description.py — Auto-description d'un dbx VENU360 / PA2 et garde-fous fondés sur ce que la machine déclare.

Constat du 24/11/2026 (VENU360 firmware 2.0.5.0) : `ls` sur un paramètre renvoie sa propre description :
    $ = valeur affichée      f / r = valeur brute      % = position 0-100 %
    Min / Max = bornes affichées      Type = Float32 | UByte | Byte | ULong | String
    Sensor = true pour un mètre (lecture seule)      Enabled = false si le paramètre est inactif
  `ls <param>\\EN` = liste des choix {libellé: index} (vide pour un numérique)
  `ls <param>\\AT` = Min / Max bruts, Default, Flags.
La machine ne renvoie JAMAIS d'erreur sur `set` : hors plage -> borne ; valeur inconnue -> premier choix ou borne.
D'où les règles ci-dessous, appliquées AVANT l'envoi :
  * choix : libellé exact pris dans la liste de la machine ;
  * nombre : même famille d'unité que l'affichage (un délai « 1.5 » sans unité serait lu en SECONDES) ;
    bornes = Min / Max affichés (ou bruts si l'affichage n'est pas lisible) ; hors plage = refus ;
  * mètre (Sensor), paramètre inactif, chaîne de données (Type String) : écriture refusée.
Relecture : la valeur affichée relue doit tomber à moins d'un demi-pas d'affichage de la consigne, même unité.
"""
from __future__ import annotations

import math
import re
import time

# famille d'unité -> {suffixe: facteur vers l'unité de base}
_UNITES = {
    "freq": {"hz": 1.0, "khz": 1000.0},
    # « m » = MINUTES (AFS LiftTime « 60m ») ; un délai en mètres (« 5m ») tombe donc hors plage et est refusé.
    "temps": {"m": 60000.0, "s": 1000.0, "ms": 1.0, "us": 0.001, "µs": 0.001},
    "niveau": {"db": 1.0, "dbfs": 1.0, "dbu": 1.0},
    "angle": {"deg": 1.0},
    "pente": {"db/s": 1.0, "db/oct": 1.0},
    "pourcent": {"%": 1.0},
    "rapport": {":1": 1.0},
    "": {"": 1.0},
}
_FAMILLE = {u: f for f, d in _UNITES.items() for u in d}
_NUM = re.compile(r"^\s*([-+]?\d+(?:\.(\d+))?)\s*([a-zµ%/:1]*?)\s*$", re.I)


def analyser(texte) -> tuple[float, str, float] | None:
    """« 1.23kHz » -> (1230.0, 'freq', 10.0) : valeur en unité de base, famille, pas d'affichage.
    Délai composé « 1.5ms/1.69ft/0.51m » : premier segment. Rapport « 4:1 ». None si illisible."""
    if texte is None:
        return None
    s = str(texte).split("/")[0] if re.search(r"ms/.*ft", str(texte)) else str(texte)
    s = s.strip()
    mc = re.fullmatch(r"(\d+)m\s+(\d+)s", s)              # AFS LiftTime « 3m 10s »
    if mc:
        return (int(mc.group(1)) * 60 + int(mc.group(2))) * 1000.0, "temps", 1000.0
    m = re.fullmatch(r"([-+]?\d+(?:\.(\d+))?)\s*(:1)", s)
    if not m:
        m = re.fullmatch(r"([-+]?\d+(?:\.(\d+))?)\s*([a-zµ%/]*)", s, re.I)
    if not m:
        return None
    u = m.group(3).lower()
    if u not in _FAMILLE:
        return None
    fam, fac = _FAMILLE[u], _UNITES[_FAMILLE[u]][u]
    dec = len(m.group(2) or "")
    v = float(m.group(1)) * fac
    pas = (10 ** -dec) * fac
    if fam == "freq" and v > 0:
        # la machine affiche 3 chiffres significatifs (« 1kHz » = 1,00 kHz, « 2.53kHz ») : le pas réel en découle
        pas = min(pas, 10 ** (math.floor(math.log10(v)) - 2))
    # La machine retire les zéros (« 0dB », « 1.5ms ») : le nombre de décimales affichées ne dit pas son pas.
    # Pas réel plafonné : 0,1 dB pour un niveau, 0,01 ms pour un temps court (sinon « 0.4ms » relu « 0ms » passerait).
    if fam == "niveau":
        pas = min(pas, 0.1)
    elif fam == "temps" and u in ("ms", "us", "µs"):
        pas = min(pas, 0.01)
    elif fam == "angle":
        pas = min(pas, 1.0)
    return v, fam, pas


def _borne(texte, brut, fam):
    a = analyser(texte)
    if a and a[1] == fam:
        return a[0]
    if brut not in (None, "") and fam in ("freq", "niveau", "angle", "pente", "pourcent", "", "rapport"):
        try:
            return float(brut)          # unités brutes = unités de base pour ces familles (vérifié sur la machine)
        except ValueError:
            return None
    return None


def decrire_param(d, chemin: str) -> dict | None:
    """Description d'un paramètre lue sur la machine (3 requêtes ls). None si la machine ne le décrit pas."""
    try:
        r = d.ls(chemin)
    except ValueError:
        return None
    if "$" not in r and "Type" not in r:
        return None
    try:
        en = d.ls(chemin + "\\EN")
    except ValueError:
        en = {}
    try:
        at = d.ls(chemin + "\\AT")
    except ValueError:
        at = {}
    choix = [k for k in en if k not in ("..", "")]
    desc = {"valeur": (r.get("$") or "").strip() if r.get("$") is not None else None,
            "brut": r.get("f"), "min": r.get("Min"), "max": r.get("Max"),
            "type": r.get("Type"), "capteur": str(r.get("Sensor", "")).lower() == "true",
            "actif": str(r.get("Enabled", "true")).lower() != "false", "choix": choix,
            "min_brut": at.get("Min"), "max_brut": at.get("Max"), "defaut_brut": at.get("Default")}
    for motif, corr in CORRECTIONS:
        if motif.search(chemin):
            desc.update(corr)
            desc["corrige"] = True
    return desc


# Déclarations de la machine contredites par son comportement (constaté le 24/11/2026, firmware 2.0.5.0).
# La valeur constatée prime : sinon l'outil laisserait passer une valeur que la machine borne en silence.
CORRECTIONS = [
    # Q des PEQ d'entrée et de l'AutoEQ : déclaré 0.1…128, borné à 16 (20.6 écrit -> 16 relu)
    (re.compile(r"\\(PEQ|AutoEQ)\d+\\SV\\(Band_\d+_Q|Band_\d+_AutoEQQ|AutoEQQ)$"), {"max": "16", "max_brut": "16"}),
]


def valider_machine(desc: dict, valeur: str) -> str | None:
    """None si la valeur peut être envoyée telle quelle ; sinon le motif du refus."""
    if desc.get("capteur"):
        return "mètre (Sensor) : lecture seule"
    if not desc.get("actif", True):
        return "paramètre inactif (Enabled=false) dans la configuration chargée"
    if str(valeur) != str(valeur).strip():
        return f"espace en début ou fin de valeur « {valeur} »"
    if "," in str(valeur):
        return f"virgule décimale refusée « {valeur} » : écrire avec un point"
    if "/" in str(valeur) and not str(valeur).lower().endswith(("db/s", "db/oct")):
        return f"format composé refusé « {valeur} » : une seule valeur avec son unité (ex. 1.5ms)"
    if desc.get("choix"):
        return None if valeur in desc["choix"] else f"valeur hors liste {desc['choix']} (libellé exact)"
    if desc.get("type") == "String":
        return "donnée texte (courbe, mesure) : non écrivable par cet outil"
    cible = analyser(desc.get("valeur"))
    if cible is None:
        # affichage non numérique (« Out », « Inf:1 », « 60m »…) : on se rabat sur les bornes affichées
        cible = analyser(desc.get("min")) or analyser(desc.get("max"))
    v = analyser(valeur)
    if v is None:
        if valeur in (desc.get("min"), desc.get("max")):
            return None                                 # « Out », « Inf:1 » : borne affichée exacte
        return f"valeur illisible « {valeur} » (nombre + unité, ex. {desc.get('valeur')})"
    if cible is None:
        return f"unité de « {desc.get('valeur')} » non reconnue par l'outil : écrire avec l'appli officielle"
    if v[1] != cible[1]:
        return (f"unité « {valeur} » incompatible avec l'affichage « {desc.get('valeur')} »"
                + (" (sans unité, la machine lirait l'unité brute, ex. des secondes)" if v[1] == "" else ""))
    if v[1] == "niveau":
        # dB, dBFS, dBu : même famille pour le calcul, mais l'unité écrite doit être celle que la machine affiche
        u_aff = re.sub(r"[-+\d.\s]", "", str(desc.get("valeur") or desc.get("max") or "")).lower()
        u_dem = re.sub(r"[-+\d.\s]", "", str(valeur)).lower()
        if u_aff and u_dem != u_aff:
            return f"unité « {u_dem} » différente de celle de la machine « {u_aff} »"
    lo, hi = _borne(desc.get("min"), desc.get("min_brut"), cible[1]), _borne(desc.get("max"), desc.get("max_brut"), cible[1])
    if lo is None or hi is None:
        return f"bornes illisibles ({desc.get('min')} … {desc.get('max')}) : écriture refusée faute de pouvoir contrôler la plage"
    lo, hi = min(lo, hi), max(lo, hi)                   # certains paramètres affichent Min > Max (Release)
    if not lo - 1e-9 <= v[0] <= hi + 1e-9:
        return f"hors plage {desc.get('min')} … {desc.get('max')} (la machine bornerait sans erreur)"
    return None


def relecture_machine(desc: dict | None, demande: str, relu: str | None) -> bool:
    """True si la valeur affichée relue correspond à la consigne (demi-pas d'affichage, même unité)."""
    if relu is None:
        return False
    if desc and desc.get("choix"):
        return relu == demande
    a, b = analyser(demande), analyser(relu)
    if a is None or b is None:
        return str(relu).strip() == str(demande).strip()
    if a[1] != b[1]:
        return False
    return math.isclose(a[0], b[0], abs_tol=b[2] / 2 + 1e-9) or abs(a[0] - b[0]) <= b[2] / 2 + 1e-9


# ---------- classement des chemins ----------
# Émission de signal : jamais sans accord explicite de Louis (--signal-valide), niveau plafonné.
SIGNAL = ("signal", "generator")          # large exprès : toute instance (SignalGenerator1…) ou commande de routage
NIVEAU_SIGNAL_MAX_DB = -30.0
# Commandes ponctuelles (pas des réglages) : exclues de l'autotest, écrites seulement à la main.
COMMANDES = ("\\flatten", "\\clearall", "\\clearlive")
# Réglages d'appareil (hors preset) testables ; les autres (horloge, AES, presets au démarrage) restent manuels.
REGLAGES_APPAREIL_TESTABLES = re.compile(r"\\\\Node\\Settings\\SV\\(Output|Input)\d+Gain$")


def est_signal(chemin: str) -> bool:
    c = chemin.lower()
    return any(m in c for m in SIGNAL)


def est_commande(chemin: str) -> bool:
    c = chemin.lower()
    return any(c.endswith(m) for m in COMMANDES)


def verifier_signal(chemin: str, valeur: str, desc_amplitude: str | None) -> str | None:
    """Motif de refus pour une écriture du générateur, même avec --signal-valide."""
    if chemin.lower().endswith("\\signal amplitude"):
        a = analyser(valeur)
        if a is None or a[0] > NIVEAU_SIGNAL_MAX_DB:
            return f"niveau du générateur plafonné à {NIVEAU_SIGNAL_MAX_DB:g} dB par l'outil"
    allume = (chemin.lower().endswith("\\signal generator") and valeur == "On") or \
             (chemin.lower().endswith("signalgeneratorcontrol") and valeur != "SignalGeneratorOff")
    if allume:
        a = analyser(desc_amplitude)
        if a is None or a[0] > NIVEAU_SIGNAL_MAX_DB:
            return (f"allumage refusé : niveau actuel {desc_amplitude} > {NIVEAU_SIGNAL_MAX_DB:g} dB "
                    "(baisser « Signal Amplitude » d'abord)")
    return None


FAMILLES_ARRONDI = ("freq", "pente", "pourcent", "rapport", "")
ARRONDI_MAX = 0.025

# Indicateurs d'état (pas des réglages) : changent dès la première écriture, ne se « restaurent » pas.
ETATS_APPAREIL = {"\\\\Node\\Settings\\SV\\PresetIsModified"}


FADERS = re.compile(r"\\(InMixer|MidRouter)\d+\\SV\\.*Gain$")   # loi de fader : grille de 0,1 à 0,5 dB
ARRONDI_FADER_DB = 0.25


def arrondi_machine(desc: dict | None, demande: str, relu: str | None, chemin: str = "") -> bool:
    """True si l'écart relu/demandé n'est que la grille interne de la machine (ex. Release 218 -> 220 dB/s,
    gain de mixeur -25.7 -> -25.5 dB). Jamais pour un choix, un temps (délai, attaque) ou un autre niveau en dB
    (seuils, gains de sortie) : ceux-là restent au demi-pas d'affichage."""
    if relu is None or (desc and desc.get("choix")):
        return False
    a, b = analyser(demande), analyser(relu)
    if a is None or b is None or a[1] != b[1]:
        return False
    if a[1] == "niveau":
        # loi de fader : plus grossière tout en bas de course (-113.7 -> -114.0 dB), sans enjeu sous -60 dB
        tol = ARRONDI_FADER_DB if min(a[0], b[0]) > -60 else 1.0
        return bool(FADERS.search(chemin)) and abs(a[0] - b[0]) <= tol + 1e-9
    if a[1] not in FAMILLES_ARRONDI:
        return False
    tol = ARRONDI_FILTRE if re.search(r"(HP|LP)Frequency$", chemin) else ARRONDI_MAX
    return abs(a[0] - b[0]) <= tol * max(abs(a[0]), 1e-9)


ARRONDI_FILTRE = 0.01     # fréquences de coupure (protection) : 1 % au plus


def blocs(d) -> list[str]:
    """Blocs de traitement présents dans la configuration chargée (`ls \\\\Preset`)."""
    return [k for k in d.ls("\\\\Preset") if k not in ("..", "SV", "AT", "DA", "")]


def decrire_tout(d, racines=("\\\\Preset", "\\\\Node\\Settings"), progression=None) -> dict:
    """{chemin: description} pour tous les paramètres SV de la machine."""
    out: dict = {}
    noms = [f"\\\\Preset\\{b}" for b in blocs(d)] if "\\\\Preset" in racines else []
    noms += [r for r in racines if r != "\\\\Preset"]
    for base in noms:
        try:
            params = [k for k in d.ls(base + "\\SV") if k not in ("..", "*", "")]
        except ValueError:
            continue
        for p in params:
            ch = f"{base}\\SV\\{p}"
            desc = decrire_param(d, ch)
            if desc:
                out[ch] = desc
        if progression:
            progression(base, len(params))
    return out


# ---------- instantané complet, restauration, autotest ----------
SORTIES_MUTE = [f"\\\\Preset\\OutputGains\\SV\\Channel_{c}_Mute" for c in range(1, 7)]


def ecrivable(chemin: str, desc: dict) -> bool:
    """Réglage de preset (ou réglage d'appareil testable), ni mètre, ni texte, ni commande, ni générateur."""
    if desc.get("capteur") or not desc.get("actif", True) or desc.get("type") == "String":
        return False
    if est_signal(chemin) or est_commande(chemin):
        return False
    if chemin.startswith("\\\\Node\\"):
        # réglages d'appareil (niveaux analogiques +12/+22 dBu = calibrage des seuils de limiteur) : jamais en lot
        return False
    if str(desc.get("valeur")).strip() == "-":          # filtre AFS inutilisé (géré par l'AFS lui-même)
        return False
    return analyser(desc.get("valeur")) is not None or bool(desc.get("choix")) \
        or desc.get("valeur") in (desc.get("min"), desc.get("max"))


def instantane_complet(d, description: dict | None = None) -> dict:
    """{chemin: valeur affichée} de tous les réglages (mètres et textes exclus)."""
    description = description or decrire_tout(d)
    return {ch: x["valeur"] for ch, x in description.items()
            if not x["capteur"] and x["type"] != "String" and x["valeur"] is not None}


def _suffixe(desc: dict) -> str:
    """Unité à utiliser pour écrire une valeur numérique de ce paramètre."""
    s = str(desc.get("valeur") or desc.get("max") or "")
    m = re.fullmatch(r"\s*[-+]?\d+(?:\.\d+)?\s*([a-zµ%/:1]*)\s*", s.split("/")[0] if "ms/" in s else s, re.I)
    u = (m.group(1) if m else "").lower()
    fam = _FAMILLE.get(u, "")
    return {"freq": "Hz", "temps": "ms", "angle": " deg", "rapport": ":1", "": ""}.get(fam, m.group(1) if m else "")


def valeur_essai(desc: dict) -> str | None:
    """Valeur proche de l'actuelle, dans la plage, au format de la machine. None si rien de sûr à proposer."""
    if desc.get("choix"):
        autres = [c for c in desc["choix"] if c != desc.get("valeur")]
        return autres[0] if autres else None
    a = analyser(desc.get("valeur"))
    fam = a[1] if a else (analyser(desc.get("min")) or analyser(desc.get("max")) or (None, None))[1]
    if fam is None:
        return None
    lo, hi = _borne(desc.get("min"), desc.get("min_brut"), fam), _borne(desc.get("max"), desc.get("max_brut"), fam)
    if lo is None or hi is None:
        return None
    lo, hi = min(lo, hi), max(lo, hi)
    if fam == "freq" and lo <= 16:
        lo = 20.0                                   # sous 16 Hz la machine affiche « Out »
    if fam == "freq" and hi > 20000:
        hi = 20000.0
    cur = a[0] if a else (lo + hi) / 2
    pas = max((hi - lo) * 0.05, a[2] * 10 if a else 0)
    if fam == "freq":
        v = cur * 1.12 if cur * 1.12 <= hi else cur / 1.12
    else:
        v = cur + pas if cur + pas <= hi else cur - pas
    v = min(max(v, lo), hi)
    suf = _suffixe(desc)
    if fam == "temps":
        # écrire dans l'unité affichée par la machine : « 3000000.00ms » est mal lu par l'AFS (retombe à 5 s)
        u = (re.search(r"[a-zµ]+", str(desc.get("valeur") or "").split("/")[0], re.I) or [None])[0] or "ms"
        u = u if u.lower() in _UNITES["temps"] else "ms"
        x = v / _UNITES["temps"][u.lower()]
        return f"{x:.2f}{u}" if u.lower() == "ms" else f"{x:.0f}{u}" if abs(x) >= 10 else f"{x:.1f}{u}"
    if fam == "freq":
        return f"{round(v)}Hz"
    dec = 0 if fam in ("rapport", "pourcent") or (a and a[2] >= 1) else 1 if not a or a[2] >= 0.1 else 2
    return f"{v:.{dec}f}{suf}"


def ecrire_et_relire(d, chemin: str, valeur: str, desc: dict | None) -> tuple[str | None, bool, str | None]:
    """(relu, conforme, erreur appareil)."""
    try:
        d.set(chemin, valeur)
    except ValueError as e:
        return None, False, str(e)
    try:
        relu = d.get(chemin)
    except ValueError as e:
        return None, False, str(e)
    relu = relu.strip() if isinstance(relu, str) else relu     # l'AFS renvoie « 60m » suivi d'un espace
    return relu, relecture_machine(desc, valeur, relu), None


class SortiesNonMutees(RuntimeError):
    """Le mute des 6 sorties n'a pas pu être vérifié : aucune écriture en lot n'est permise."""


def muter_sorties(d, etat: str | dict) -> dict:
    """Mute (« On »), démute (« Off ») ou remet un état {chemin: valeur}. Renvoie l'état relu."""
    out = {}
    for ch in SORTIES_MUTE:
        cible = etat.get(ch) if isinstance(etat, dict) else etat
        if cible is None:
            continue
        relu, ok, err = ecrire_et_relire(d, ch, cible, {"choix": ["Off", "On"]})
        out[ch] = relu
    return out


def muter_et_verifier(d) -> dict:
    """Mute les 6 sorties et EXIGE la relecture « On » partout ; sinon lève SortiesNonMutees (rien d'autre n'est écrit)."""
    relus = muter_sorties(d, "On")
    manquants = [ch for ch in SORTIES_MUTE if relus.get(ch) != "On"]
    if manquants:
        raise SortiesNonMutees(f"mute non confirmé sur {manquants} (relu : {[relus.get(c) for c in manquants]})")
    return relus


def _seuil(ch: str) -> bool:
    return bool(re.search(r"\\Limiter\d*\\SV\\Threshold$", ch))


def restaurer(d, cible: dict, description: dict | None = None, journal=None, seuils_hausse_autorises: bool = False,
              demuter: bool = True) -> dict:
    """Réécrit chaque écart entre la machine et `cible` ({chemin: valeur}), sorties mutées et vérifiées.
    - Chaque valeur passe par valider_machine (bornes, choix, unité) : une valeur invalide n'est pas envoyée.
    - Un seuil de limiteur plus HAUT que l'actuel n'est réécrit que si `seuils_hausse_autorises`
      (autotest : retour aux valeurs d'origine ; commande : décision explicite de Louis).
    - Réglages d'appareil (\\\\Node), générateur et commandes ponctuelles : jamais touchés.
    - Les sorties ne sont démutées qu'après relecture complète conforme ; sinon elles RESTENT mutées."""
    description = description or decrire_tout(d)

    def lire(ch):
        try:
            v = d.get(ch)
        except ValueError:
            return None
        return v.strip() if isinstance(v, str) else v

    actuel = {ch: lire(ch) for ch in cible if not ch.startswith("_")}
    mutes_origine = {ch: actuel.get(ch) for ch in SORTIES_MUTE}
    muter_et_verifier(d)
    ecarts, echecs, refuses = 0, [], []
    for ch, v in cible.items():
        if ch in SORTIES_MUTE or ch.startswith("_") or v is None:
            continue
        if est_signal(ch) or est_commande(ch) or ch.startswith("\\\\Node\\"):
            continue
        if actuel.get(ch, v) == v:
            continue
        desc = description.get(ch)
        # délai affiché « 4ms/4.51ft/1.37m » : on écrit le premier segment (ms), on compare à l'affichage complet
        a_ecrire = v.split("/")[0] if isinstance(v, str) and re.match(r"[-+\d.]+ms/", v) else v
        motif = valider_machine(desc, a_ecrire) if desc else "paramètre non décrit par la machine"
        if not motif and _seuil(ch) and not seuils_hausse_autorises:
            a, b = analyser(v), analyser(actuel.get(ch))
            if not a or not b:
                motif = f"seuil illisible (actuel {actuel.get(ch)!r}, cible {v!r}) : sens de variation inconnu"
            elif a[0] > b[0] + 1e-9:
                motif = f"seuil plus haut que l'actuel ({actuel.get(ch)} -> {v}) : décision explicite de Louis requise"
        if motif:
            refuses.append({"chemin": ch, "cible": v, "actuel": actuel.get(ch), "motif": motif})
            continue
        ecarts += 1
        relu, ok, err = ecrire_et_relire(d, ch, a_ecrire, desc)
        if relu != v:
            echecs.append({"chemin": ch, "cible": v, "relu": relu, "erreur": err})
        if journal:
            journal(f"RESTAURATION {ch} : {actuel.get(ch)} -> {v} (relu {relu})")
    # contrôle final AVANT de rendre le son : toutes les valeurs visées doivent être relues à l'identique
    refuses_ch = {r["chemin"] for r in refuses}
    restants = {}
    for ch, v in cible.items():
        if ch.startswith("_") or ch in SORTIES_MUTE or ch.startswith("\\\\Node\\") or est_signal(ch) \
                or est_commande(ch) or v is None or ch in refuses_ch:
            continue
        lu = lire(ch)
        if lu != v:
            restants[ch] = (v, lu)
    conforme = not echecs and not restants
    mutes_cibles = {ch: cible.get(ch, mutes_origine[ch]) for ch in SORTIES_MUTE}
    if demuter and conforme and not refuses:
        relus = muter_sorties(d, mutes_cibles)
        manque = [ch for ch, v in mutes_cibles.items() if v is not None and relus.get(ch) != v]
        sorties = "remises" if not manque else f"remise INCOMPLÈTE : {manque} relus {[relus.get(c) for c in manque]}"
    else:
        sorties = "RESTÉES MUTÉES"
        if journal:
            journal(f"RESTAURATION incomplète : sorties laissées mutées ({len(echecs)} échec(s), "
                    f"{len(restants)} écart(s), {len(refuses)} refus)")
    return {"ecarts": ecarts, "echecs": echecs, "refuses": refuses, "ecarts_restants": restants,
            "conforme": conforme and not refuses, "sorties": sorties, "mutes_cibles": mutes_cibles}


# Paramètres de protection dont la valeur BRUTE est vérifiée après l'aller-retour de l'autotest
# (l'affichage arrondi « -23.7dB » peut cacher -23.74 : le retour par l'affichage ne doit pas relever un seuil).
BRUT_CONTROLE = re.compile(r"\\SV\\(Threshold|Band_\d+_HPFrequency|Band_\d+_LPFrequency|Sub_HPFrequency|Sub_LPFrequency|Amount)$")


def _brut(d, ch):
    try:
        return float(d.ls(ch).get("f"))
    except (ValueError, TypeError):
        return None


def autotest(d, filtre_blocs: list[str] | None = None, journal=None, progression=None) -> dict:
    """Aller-retour d'écriture sur chaque réglage écrivable, sorties mutées ET vérifiées, puis contrôle diff = 0.
    Les refus de l'outil (hors plage, liste, virgule, sans unité) sont vérifiés sans rien envoyer.
    Les sorties ne sont rendues qu'après une restauration complète et conforme."""
    description = decrire_tout(d)
    avant = instantane_complet(d, description)
    mutes = {ch: avant.get(ch) for ch in SORTIES_MUTE}
    if journal:
        journal(f"AUTOTEST début : {len(description)} paramètres décrits, mutes d'origine {mutes}")
    muter_et_verifier(d)                               # lève SortiesNonMutees : rien n'est écrit
    lignes, refus, derives = [], [], []
    erreur_boucle = None
    try:
        for ch, desc in description.items():
            if filtre_blocs and not any(f"\\Preset\\{b}" in ch for b in filtre_blocs):
                continue
            if ch in SORTIES_MUTE or not ecrivable(ch, desc):
                continue
            # refus attendus, sans aucune trame envoyée
            for mauvais in _valeurs_refusees(desc):
                motif = valider_machine(desc, mauvais)
                refus.append({"chemin": ch, "valeur": mauvais, "refusee": motif is not None, "motif": motif})
            essai = valeur_essai(desc)
            if essai is not None and relecture_machine(desc, essai, desc["valeur"]):
                essai = None                                # identique à l'actuelle : ne prouverait rien
            if essai is None or valider_machine(desc, essai):
                lignes.append({"chemin": ch, "orig": desc["valeur"], "essai": essai, "statut": "non testé",
                               "motif": valider_machine(desc, essai) if essai else "pas de valeur d'essai"})
                continue
            t0 = time.time()
            relu, ok, err = ecrire_et_relire(d, ch, essai, desc)
            relu2, _, err2 = ecrire_et_relire(d, ch, desc["valeur"], desc)
            arrondi = not ok and arrondi_machine(desc, essai, relu, ch)
            ligne = {"chemin": ch, "orig": desc["valeur"], "essai": essai, "relu": relu, "ecriture_ok": ok or arrondi,
                     "arrondi_machine": arrondi, "restaure": relu2 == desc["valeur"], "relu_final": relu2,
                     "erreur": err or err2, "ms": round((time.time() - t0) * 1000)}
            if BRUT_CONTROLE.search(ch) and desc.get("brut") not in (None, ""):
                b0, b1 = float(desc["brut"]), _brut(d, ch)
                if b1 is None or not math.isclose(b0, b1, rel_tol=1e-4, abs_tol=1e-6):
                    ligne["brut_avant"], ligne["brut_apres"] = b0, b1
                    derives.append(ligne)
            lignes.append(ligne)
            if journal:
                journal(f"AUTOTEST {ch} : {desc['valeur']} -> {essai} (relu {relu}) -> {desc['valeur']} (relu {relu2})")
            if progression:
                progression(len(lignes))
    except BaseException as e:                          # réseau perdu, Ctrl-C… : on tente quand même la restauration
        erreur_boucle = f"{type(e).__name__}: {e}"
        if journal:
            journal(f"AUTOTEST interrompu : {erreur_boucle}")
    try:
        reste = restaurer(d, avant, description, journal, seuils_hausse_autorises=True, demuter=False)
    except Exception as e:
        reste = {"ecarts": 0, "echecs": [{"erreur": f"{type(e).__name__}: {e}"}], "refuses": [], "ecarts_restants": {},
                 "conforme": False, "sorties": "RESTÉES MUTÉES"}
    try:
        apres = instantane_complet(d, decrire_tout(d))
    except Exception as e:
        apres = {"_erreur": f"{type(e).__name__}: {e}"}
    ecarts = {ch: (avant.get(ch), apres.get(ch)) for ch in set(avant) | set(apres)
              if ch not in SORTIES_MUTE and ch not in ETATS_APPAREIL and avant.get(ch) != apres.get(ch)}
    # dérive brute d'un seuil vers le HAUT = protection relâchée : échec bloquant
    seuils_releves = [l for l in derives if _seuil(l["chemin"]) and (l["brut_apres"] is None or l["brut_apres"] > l["brut_avant"] + 1e-6)]
    testes = [l for l in lignes if "ecriture_ok" in l]
    echecs = [l for l in testes if not ((l["ecriture_ok"] or l["erreur"]) and l["restaure"])]
    # toute dérive brute d'une protection (seuil, coupure, délai) bloque le retour du son, même sous le demi-pas
    ok_final = not (erreur_boucle or echecs or ecarts or derives or seuils_releves or not reste.get("conforme"))
    if ok_final:
        relus = muter_sorties(d, mutes)
        manque = [ch for ch, v in mutes.items() if v is not None and relus.get(ch) != v]
        sorties = "remises comme avant" if not manque else f"remise INCOMPLÈTE : {manque} relus {[relus.get(c) for c in manque]}"
    else:
        sorties = "RESTÉES MUTÉES (état différent ou incertain : comparer à l'instantané avant de rendre le son)"
    if journal:
        journal(f"AUTOTEST sorties : {sorties}")
    return {"decrits": len(description), "testes": len(testes),
            "ecriture_ok": sum(l["ecriture_ok"] for l in testes), "restaures": sum(l["restaure"] for l in testes),
            "non_testes": [l for l in lignes if "ecriture_ok" not in l],
            # la machine répond « error » (option indisponible dans cette config) et la valeur d'origine est intacte :
            # c'est une limite constatée, pas un échec de l'outil
            "refus_appareil": [l for l in testes if l["erreur"] and l["restaure"]],
            "echecs": echecs, "erreur_boucle": erreur_boucle,
            "derives_brutes": derives, "seuils_releves": seuils_releves,
            "refus_attendus": len(refus), "refus_manques": [r for r in refus if not r["refusee"]],
            "restauration": reste, "ecarts_finaux": ecarts, "sorties": sorties, "lignes": lignes}


def _valeurs_refusees(desc: dict) -> list[str]:
    """Valeurs que l'outil DOIT refuser pour ce paramètre (la machine les accepterait en silence)."""
    if desc.get("choix"):
        c = desc["choix"][0]
        return [c.lower() if c.lower() != c else c.upper(), c + " ", "inconnu"]
    a = analyser(desc.get("valeur"))
    if a is None:
        return ["abc"]
    fam = a[1]
    lo, hi = _borne(desc.get("min"), desc.get("min_brut"), fam), _borne(desc.get("max"), desc.get("max_brut"), fam)
    suf = _suffixe(desc)
    out = ["abc", f"{a[0]:.1f}".replace(".", ",") + suf]
    if fam != "":
        out.append(f"{a[0]:g}")                       # sans unité
    if lo is not None and hi is not None:
        lo, hi = min(lo, hi), max(lo, hi)
        ecart = max((hi - lo) * 0.1, 1)
        out += [f"{hi + ecart:.2f}{suf if fam != 'temps' else 'ms'}", f"{lo - ecart:.2f}{suf if fam != 'temps' else 'ms'}"]
    return out


def topologie(d) -> dict:
    """Configuration de traitement chargée (lecture seule) : colonnes du builder et choix de l'assistant."""
    import json as _json
    out: dict = {}
    try:
        brut = d.get("\\\\Node\\Config\\SV\\Config")
        cfg = _json.loads(str(brut).replace("`", '"')) if brut else {}
        cols = cfg.get(".builderConfig", {}).get("Columns") or []
        resume = []
        for col in cols:
            if not col:
                continue
            nom, chemins = str(col[0]).replace("Column_", ""), col[1:]
            elts = [" ".join(str(x) for x in c if not isinstance(x, list)) if isinstance(c, list) else "—" for c in chemins]
            resume.append(f"{nom:16} : " + " | ".join(elts))
        out["resume"] = resume
        out["colonnes"] = cols
    except (ValueError, _json.JSONDecodeError) as e:
        out["config_erreur"] = str(e)
    try:
        ch = d.ls("\\\\Node\\SetupWizardCurrentChoices\\SV")
        out["assistant"] = {k: v for k, v in ch.items() if k not in ("..", "*")}
    except ValueError as e:
        out["assistant_erreur"] = str(e)
    return out
