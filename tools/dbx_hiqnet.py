#!/usr/bin/env python3
"""
dbx_hiqnet.py — Client réseau pour dbx DriveRack VENU360 / PA2 (protocole texte « HiQnet Console », TCP 19272).

Protocole (sources : github.com/mkupferman/dbxdriverack-python pour la PA2,
github.com/fiLLLip/dbx-venu360 pour le VENU360 ; capture Wireshark de l'appli officielle) :
  1. TCP <ip>:19272, envoi d'une ligne vide -> l'appareil répond « HiQnet Console »
  2. connect administrator "<mdp>"           -> « connect logged in as administrator »
  3. Commandes texte terminées par \\n :
       get  "<chemin>"                  -> get "<chemin>" "<valeur>"
       set  "<chemin>" "<valeur>"       -> setr "<chemin>" "<valeur>"
       ls   "<chemin>"                  -> lignes « clé : valeur » ... puis « endls »
       sub  "<chemin>" / unsub          -> « subr » à chaque changement
     Chemins : \\\\Preset\\<Module>\\SV\\<Param> (SV = valeurs, AT = attributs), \\\\Node\\AT\\Class_Name ...
  4. JSON-RPC sur la même socket (VENU360) : {"id":"..","method":"Load","params":["1"]} (rappel preset),
     {"id":"..","method":"Get","params":["1","100","presetName"]} (noms des presets).

SÉCURITÉ — cet outil pilote un processeur relié à des amplis de plusieurs kW :
  * Par défaut TOUT est en lecture seule. Une écriture exige --appliquer.
  * Avant toute écriture : instantané automatique (journal/instantanes/ ; carte complète avant rappel/stockage de preset),
    et journalisation (journal/ecritures.log).
  * Écritures sur un chemin de protection (Limiter, HPF/HPType du crossover, Mute...) : refusées sans --protection-validee,
    flag que Claude ne passe QUE si Louis a confirmé explicitement dans la conversation.
  * Pas d'écriture pendant un concert. Réseau isolé uniquement.

Usage :
  python tools/dbx_hiqnet.py decouvrir                         # broadcast UDP 19272
  python tools/dbx_hiqnet.py info     <ip>
  python tools/dbx_hiqnet.py get      <ip> "\\\\Preset\\Crossover\\SV\\Band_1_HPFrequency"
  python tools/dbx_hiqnet.py ls       <ip> "\\\\Preset"
  python tools/dbx_hiqnet.py arbre    <ip> [--racine "\\\\Preset"] [--profondeur 4] [-o journal/arbre.json]
  python tools/dbx_hiqnet.py carte    <ip> -o journal/instantanes/carte.json   # lit tous les chemins connus (VENU360)
  python tools/dbx_hiqnet.py instantane <ip> -o journal/instantanes/avant.json
  python tools/dbx_hiqnet.py diff     avant.json apres.json    # mode « apprentissage » : quel chemin a bougé ?
  python tools/dbx_hiqnet.py ecouter  <ip> "\\\\Preset\\..."   # sub : affiche les changements en direct
  python tools/dbx_hiqnet.py set      <ip> "<chemin>" "<valeur>" [--appliquer] [--protection-validee]
  python tools/dbx_hiqnet.py presets  <ip>
  python tools/dbx_hiqnet.py rappel   <ip> <n> [--appliquer --protection-validee]  # Load : instantané complet avant
  python tools/dbx_hiqnet.py stocker  <ip> <n> [--appliquer] [--nom N]               # JSON-RPC Store : instantané complet avant
  python tools/dbx_hiqnet.py appliquer-plan <ip> plans/<x>-reglages.json [--case N --nom N --verifier --demuter] --appliquer --protection-validee
                                         # plan de tools/preset_auto.py, écrit par tools/dbx_plan.py (protections d'abord)
  python tools/dbx_hiqnet.py rpc      <ip> <Methode> [params...]             # méthodes de LECTURE seulement (liste blanche)
Carte complète des paramètres VENU360 : data/venu360_parametres.yaml (issue de l'appli v2.0.7).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import socket
import sys
import time
import uuid
from pathlib import Path

PORT = 19272
RACINE = Path(__file__).resolve().parent.parent
JOURNAL = RACINE / "journal"
MOTS_PROTECTION = ("limiter", "hptype", "hpfrequency", "lptype", "lpfrequency", "mute", "peakstop", "threshold", "polarity", "overshoot", "\\sv\\auto",
                   # niveaux et routage (24/11/2026) : un gain d'entrée, de zone ou de sortie, ou un routage, change ce que
                   # reçoivent les amplis ; le niveau de sortie analogique change le calibrage des seuils de limiteur.
                   "\\inputgains\\", "\\inmixer", "\\midrouter", "\\outputgains\\", "\\zonegains\\",
                   "\\node\\settings\\sv\\output", "\\node\\settings\\sv\\input")
CHEMINS_INTERDITS = ("password", "reboot", "\\node\\config\\sv\\command")
RPC_LECTURE = {"Get", "GetInputRouterSourceNames", "PresetIsModified", "PresetRecallList"}
RPC_INTERDITS = {"FactoryRestore", "BuildDB", "CopyPresets", "StorePresetFiles", "WizardChoicesDefault", "Delete", "DeletePreset"}
CARTE = Path(__file__).resolve().parent.parent / "data" / "venu360_parametres.yaml"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from garde_fous import nombre, refuser_controle, verifier_plafond   # noqa: E402
import dbx_description as dd   # noqa: E402

# Préfixe d'instance -> section de la carte ; préfixe de paramètre multi-canal retiré avant recherche
_SECTIONS = (("CrossoverIIR", "crossover", r"^(Band_\d+_|Sub_)"), ("PEQOut", "peq_sortie", r"^Band_\d+_"),
             ("Limiter", "limiteur", None), ("DelayOut", "delai_sortie", None),
             ("OutputGains", "gains", r"^Channel_\d+_"), ("ZoneGains", "gains", r"^Channel_\d+_"))


def regle_carte(chemin: str) -> dict | None:
    """Règle (min/max/enum) de data/venu360_parametres.yaml pour un chemin « \\\\Preset\\<Instance>\\SV\\<Param> »."""
    m = re.match(r"^\\\\Preset\\([A-Za-z]+)\d*\\SV\\(\w+)$", chemin)
    if not m:
        return None
    import yaml
    carte = yaml.safe_load(CARTE.read_text(encoding="utf-8"))
    for prefixe, section, multi in _SECTIONS:
        if m.group(1) == prefixe:
            sec = carte.get(section) or {}
            param = m.group(2)
            if multi and re.match(multi, param):
                return (sec.get("par_bande") or sec).get(re.sub(multi, "", param))
            return sec.get(param) or (sec.get("par_bande") or {}).get(param)
    return None


def valider_valeur(chemin: str, valeur: str) -> str | None:
    """None si la valeur respecte la carte ; sinon le motif du refus. Chemin hors carte : motif aussi."""
    # Le VENU360 ne renvoie jamais d'erreur : une valeur qu'il ne reconnaît pas est remplacée en silence
    # (constaté le 23/11/2026). La chaîne validée doit donc être exactement celle envoyée.
    if str(valeur) != str(valeur).strip():
        return f"espace en début ou fin de valeur « {valeur} » : la valeur est envoyée telle quelle"
    if "," in str(valeur):
        # L'appareil tronque à la virgule (« -12,5dB » -> -12.0 dB) : point décimal obligatoire.
        return f"virgule décimale refusée « {valeur} » : écrire avec un point (ex. -12.5dB)"
    r = regle_carte(chemin)
    if r is None:
        return "chemin absent de la carte VENU360 (data/venu360_parametres.yaml)"
    if "enum" in r:
        # Comparaison EXACTE (casse et espaces) : valeur inconnue -> première de la liste (« lr 24 » -> « BW 6 »).
        permis = [str(x) for x in r["enum"]] + (["0", "1"] if "Mute" in chemin else [])
        return None if valeur in permis else f"valeur hors liste {permis} (casse et espaces exacts)"
    lo, hi, v = nombre(r.get("min")), nombre(r.get("max")), nombre(valeur)
    if v is None:
        if valeur == "Out" and "Frequency" in chemin:     # casse exacte : « out » -> borne haute sur l'appareil
            return None
        return f"valeur illisible « {valeur} » (nombre + unité attendus, ex. {r.get('exemple') or r.get('min')})"
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        return f"hors plage {r.get('min')}…{r.get('max')}"
    return None


def relecture_conforme(demande: str, relu: str | None, chemin: str = "") -> bool:
    """True si la valeur relue correspond à la consigne.
    Numérique : tolérance d'arrondi / pas de l'appareil. Format composé du délai (« 1.5ms/1.69ft/0.51m ») :
    seul le premier segment (ms) est comparé. Liste : égalité exacte (Mute « 1 »/« 0 » = « On »/« Off »)."""
    if relu is None:
        return False
    relu_txt = str(relu).strip()
    vn, an = nombre(demande), nombre(relu_txt.split("/")[0])
    if vn is not None and an is not None:
        return abs(vn - an) <= max(0.051, abs(vn) * 0.005)      # arrondi d'affichage / pas de l'appareil
    d = str(demande).strip()
    if chemin.endswith("_Mute"):
        d = {"1": "On", "0": "Off"}.get(d, d)
    return relu_txt == d


XO_PARAMS = ("HPFrequency", "HPType", "LPFrequency", "LPType", "Gain", "Polarity", "Phase")
LIM_PARAMS = ("Limiter", "Threshold", "OverEasy", "Auto", "Attack", "Hold", "Release", "PeakStopPlus", "OvershootDb")
PEQ_BANDE = ("Type", "Frequency", "Gain", "Q", "Slope")
LIGNE_LS = re.compile(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$")


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


class Dbx:
    def __init__(self, hote: str, port: int = PORT, mdp: str = "administrator", timeout: float = 3.0, verbose: bool = False):
        self.hote, self.port, self.mdp, self.timeout, self.verbose = hote, port, mdp, timeout, verbose
        self.sock: socket.socket | None = None
        self._buf = ""

    # ---------- transport ----------
    def __enter__(self):
        self.connecter()
        return self

    def __exit__(self, *_):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _envoyer(self, ligne: str) -> None:
        refuser_controle(ligne)              # une entrée ne doit jamais produire deux lignes de protocole
        if self.verbose:   # le mot de passe n'est jamais affiché
            print(">> " + ("connect administrator \"****\"" if ligne.startswith("connect ") else ligne), file=sys.stderr)
        assert self.sock
        self.sock.sendall((ligne + "\n").encode("utf-8"))

    def _lire_ligne(self, timeout: float | None = None) -> str | None:
        assert self.sock
        fin = time.time() + (timeout if timeout is not None else self.timeout)
        while "\n" not in self._buf:
            reste = fin - time.time()
            if reste <= 0:
                return None
            self.sock.settimeout(reste)
            try:
                bloc = self.sock.recv(8192)
            except socket.timeout:
                return None
            if not bloc:
                raise ConnectionError("Connexion fermée par l'appareil")
            self._buf += bloc.decode("utf-8", errors="replace")
        ligne, self._buf = self._buf.split("\n", 1)
        ligne = ligne.rstrip("\r")
        if self.verbose:
            print(f"<< {ligne}", file=sys.stderr)
        return ligne

    def connecter(self) -> None:
        self.sock = socket.create_connection((self.hote, self.port), timeout=self.timeout)
        self._envoyer("")
        fin = time.time() + self.timeout
        while time.time() < fin:
            l = self._lire_ligne()
            if l and "HiQnet Console" in l:
                break
        else:
            raise ConnectionError("Pas de bannière « HiQnet Console »")
        self._envoyer(f"connect administrator {_q(self.mdp)}")
        while True:
            l = self._lire_ligne()
            if l is None:
                raise ConnectionError("Pas de réponse à l'authentification")
            if l.startswith("connect logged in as"):
                return
            if l.startswith("error"):
                raise PermissionError(f"Authentification refusée : {l}")

    # ---------- commandes ----------
    def get(self, chemin: str) -> str | None:
        self._envoyer(f"get {_q(chemin)}")
        cible = chemin.rstrip("\\")
        while True:
            l = self._lire_ligne()
            if l is None:
                return None
            if l.startswith("error"):
                raise ValueError(l)
            parts = shlex.split(l, posix=False)
            if len(parts) >= 3 and parts[0] in ("get", "subr") and parts[1].strip('"').rstrip("\\") == cible:
                return parts[2].strip('"')

    def ls(self, chemin: str) -> dict[str, str]:
        self._envoyer(f"ls {_q(chemin)}")
        res: dict[str, str] = {}
        while True:
            l = self._lire_ligne()
            if l is None or l.strip() == "endls":
                return res
            if l.startswith("error"):
                raise ValueError(l)
            if l.startswith("ls "):
                continue
            m = LIGNE_LS.match(l)
            if m:
                res[m.group(1)] = m.group(2)
            elif l.strip():
                res[l.strip()] = ""

    def set(self, chemin: str, valeur: str) -> str | None:
        self._envoyer(f"set {_q(chemin)} {_q(valeur)}")
        while True:
            l = self._lire_ligne()
            if l is None:
                return None
            if l.startswith("error"):
                raise ValueError(l)
            if l.startswith("setr"):
                return l

    def rpc(self, methode: str, params: list, attendre_fin: bool = True, timeout: float = 30) -> list[dict]:
        rid = f"{uuid.uuid4()}_{methode.lower()}"
        self._envoyer(json.dumps({"id": rid, "method": methode, "params": params}))
        reps, fin = [], time.time() + timeout
        while time.time() < fin:
            l = self._lire_ligne(timeout=fin - time.time())
            if l is None:
                break
            if l.startswith("{"):
                try:
                    j = json.loads(l)
                except json.JSONDecodeError:
                    continue
                if j.get("id") == rid:
                    reps.append(j)
                    if not attendre_fin or j.get("done", True) is not False:
                        break
        return reps

    def ecouter(self, chemin: str, duree: float) -> None:
        self._envoyer(f"sub {_q(chemin)}")
        fin = time.time() + duree
        while time.time() < fin:
            l = self._lire_ligne(timeout=fin - time.time())
            if l:
                print(f"{dt.datetime.now():%H:%M:%S.%f}"[:-3], l, flush=True)
        self._envoyer(f"unsub {_q(chemin)}")

    # ---------- carte des chemins connus (VENU360, issue de la décompilation de l'appli) ----------
    def _essai(self, chemin: str) -> str | None:
        try:
            return self.get(chemin)
        except ValueError:
            return None

    def carte(self, sorties=range(1, 7)) -> dict:
        """Lit tous les chemins connus (data/venu360_parametres.yaml). Instance absente = sautée.
        Sortie au même format que `arbre` pour pouvoir utiliser `diff`."""
        ancien, self.timeout = self.timeout, min(self.timeout, 1.0)
        res: dict = {}
        try:
            for k in sorties:
                base = f"\\\\Preset\\CrossoverIIR{k}\\SV"
                if self._essai(f"{base}\\Band_1_HPFrequency") is not None:
                    noeud = {}
                    for pref in [f"Band_{b}" for b in range(1, 7)] + ["Sub"]:
                        if self._essai(f"{base}\\{pref}_HPFrequency") is None:
                            continue
                        for prm in XO_PARAMS:
                            noeud[f"{pref}_{prm}"] = self._essai(f"{base}\\{pref}_{prm}")
                    res[f"CrossoverIIR{k}"] = {"SV": noeud}
                base = f"\\\\Preset\\PEQOut{k}\\SV"
                if self._essai(f"{base}\\ParametricEQ") is not None:
                    noeud = {"ParametricEQ": self._essai(f"{base}\\ParametricEQ")}
                    for b in range(1, 9):
                        for prm in PEQ_BANDE:
                            noeud[f"Band_{b}_{prm}"] = self._essai(f"{base}\\Band_{b}_{prm}")
                    res[f"PEQOut{k}"] = {"SV": noeud}
                base = f"\\\\Preset\\Limiter{k}\\SV"
                if self._essai(f"{base}\\Limiter") is not None:
                    res[f"Limiter{k}"] = {"SV": {p: self._essai(f"{base}\\{p}") for p in LIM_PARAMS}}
                base = f"\\\\Preset\\DelayOut{k}\\SV"
                if self._essai(f"{base}\\Delay") is not None:
                    res[f"DelayOut{k}"] = {"SV": {p: self._essai(f"{base}\\{p}") for p in ("Delay", "Amount")}}
            for inst in ("OutputGains", "ZoneGains"):
                base = f"\\\\Preset\\{inst}\\SV"
                noeud = {}
                for c in range(1, 7):
                    for prm in ("Gain", "Mute"):
                        v = self._essai(f"{base}\\Channel_{c}_{prm}")
                        if v is not None:
                            noeud[f"Channel_{c}_{prm}"] = v
                if noeud:
                    res[inst] = {"SV": noeud}
            # Réglages d'appareil hors preset (appli Windows VENU360, classe Venu360Settings) :
            # OutputNGain 0..6 = +4/+8/+12/+14/+17/+20/+22 dBu (défaut usine 2 = +12) ; InputNGain 0..3.
            reglages = {}
            for nom in [f"Output{c}Gain" for c in range(1, 7)] + [f"Input{c}Gain" for c in range(1, 4)] \
                    + ["LoadedPresetName", "PresetIsModified", "MutesPowerup", "CurrentSampleRate"]:
                v = self._essai(f"\\\\Node\\Settings\\SV\\{nom}")
                if v is not None:
                    reglages[nom] = v
        finally:
            self.timeout = ancien
        out = {"\\\\Preset": res}
        if reglages:
            out["\\\\Node\\Settings"] = {"SV": reglages}
        return out

    # ---------- exploration ----------
    def arbre(self, racine: str = "\\\\Preset", profondeur: int = 4) -> dict:
        """Parcours récursif par ls. Les nœuds SV/AT sont lus comme feuilles.
        Format exact des sorties ls du VENU360 non documenté : le brut est conservé pour analyse."""
        vus: set[str] = set()

        def visiter(chemin: str, p: int) -> dict:
            if chemin in vus or p < 0:
                return {}
            vus.add(chemin)
            try:
                enfants = self.ls(chemin)
            except ValueError as e:
                return {"_erreur": str(e)}
            noeud: dict = {"_ls": enfants}
            for cle in enfants:
                nom = cle.strip().rstrip("\\")
                if not nom or nom.startswith("_"):
                    continue
                sous = f"{chemin}\\{nom}"
                if nom in ("SV", "AT"):
                    continue
                noeud[nom] = visiter(sous, p - 1)
            for feuille in ("SV", "AT"):
                try:
                    v = self.ls(f"{chemin}\\{feuille}")
                    if v:
                        noeud[feuille] = v
                except ValueError:
                    pass
            return noeud

        return {racine: visiter(racine, profondeur)}


# ---------- instantanés & diff ----------
def aplatir(d: dict, prefixe: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        if k in ("_ls", "_meta"):
            continue
        chemin = f"{prefixe}\\{k}" if prefixe else k
        if isinstance(v, dict):
            out.update(aplatir(v, chemin))
        else:
            out[chemin] = v
    return out


def diff(a: dict, b: dict) -> list[tuple[str, str | None, str | None]]:
    fa, fb = aplatir(a), aplatir(b)
    return [(k, fa.get(k), fb.get(k)) for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]


def journaliser(texte: str) -> None:
    JOURNAL.mkdir(exist_ok=True)
    with (JOURNAL / "ecritures.log").open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {texte}\n")


def instantane_avant(d: "Dbx", ip: str, motif: str) -> Path:
    """Carte complète du processeur avant une opération globale (rappel, stockage) : permet de comparer/revenir."""
    t = d.carte()
    n_val = len(aplatir(t))
    if n_val < 20 or not any(k.startswith(("Limiter", "Crossover")) for k in t.get("\\\\Preset", {})):
        raise ValueError(f"instantané incomplet ({n_val} valeurs, limiteurs/crossover absents) : opération annulée, "
                         "rien n'a été envoyé. Vérifier la connexion (`carte`) avant de recommencer.")
    t["_meta"] = {"ip": ip, "date": dt.datetime.now().isoformat(timespec="seconds"), "motif": motif}
    out = JOURNAL / "instantanes" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{motif}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def est_protection(chemin: str) -> bool:
    c = chemin.lower()
    return any(m in c for m in MOTS_PROTECTION)


def decouvrir(timeout: float = 3.0) -> list[tuple[str, str]]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("", 0))
    for c in ("delay 100", 'get "\\\\Node\\AT\\Class_Name"', 'get "\\\\Node\\AT\\Instance_Name"', 'get "\\\\Node\\AT\\Software_Version"'):
        s.sendto((c + "\n").encode(), ("<broadcast>", PORT))
    s.settimeout(timeout)
    res = []
    try:
        while True:
            data, addr = s.recvfrom(2048)
            res.append((addr[0], data.decode("utf-8", "replace").strip()))
    except socket.timeout:
        pass
    return res


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):  # console Windows cp1252 : Ω, ≈, −, ≤ sinon UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["decouvrir", "info", "get", "ls", "arbre", "carte", "instantane", "diff", "ecouter", "set",
                                    "presets", "rappel", "stocker", "rpc", "decrire", "restaurer", "autotest", "topologie",
                                    "appliquer-plan", "regler", "lot", "chaine"])
    ap.add_argument("--signal-valide", action="store_true",
                    help="Louis a confirmé niveau sûr et personne exposé : autorise l'écriture du générateur (≤ -30 dB)")
    ap.add_argument("--nom", help="stocker : nom donné au preset (sinon il garde le nom du preset chargé)")
    ap.add_argument("--case", type=int, help="appliquer-plan : case où stocker le preset (1-100), avec --nom")
    ap.add_argument("--demuter", action="store_true", help="appliquer-plan : démuter les sorties utilisées si tout est conforme")
    ap.add_argument("--ecraser", action="store_true", help="appliquer-plan : remplacer une case qui porte un autre nom")
    ap.add_argument("--verifier", action="store_true", help="appliquer-plan : rappeler la case stockée et tout relire")
    ap.add_argument("--blocs", help="autotest : blocs à tester, séparés par des virgules (ex. GEQ1,Afs1,Limiter)")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--mdp", default=None,
                    help="déconseillé (visible dans l'historique) : préférer la variable d'environnement DBX_MDP")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--racine", default="\\\\Preset")
    ap.add_argument("--profondeur", type=int, default=4)
    ap.add_argument("--duree", type=float, default=60)
    ap.add_argument("-o", "--sortie")
    ap.add_argument("--appliquer", action="store_true", help="autorise l'écriture (sinon simulation)")
    ap.add_argument("--protection-validee", action="store_true", help="Louis a validé l'écriture d'un paramètre de protection")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--config", help="config système (data/configs/…yaml) : plafond des seuils de limiteur")
    ap.add_argument("--voie", help="nom de la voie dans la config (ex. SUB)")
    ap.add_argument("--depasser-plafond", action="store_true", help="dérogation explicite de Louis, journalisée")
    ap.add_argument("--hors-carte", action="store_true", help="écrire un chemin absent de la carte (vérifié par get)")
    ap.add_argument("--par-pas", action="store_true",
                    help="set/regler/lot : découper un écart > pas maximum (±3 dB, ±0,5 ms, ±45°), chaque pas relu")
    ap.add_argument("--pas-libre", action="store_true",
                    help="set/regler/lot : lever le pas maximum (construction de preset) — seulement 6 sorties relues mutées")
    ap.add_argument("--enregistrer", action="store_true", help="chaine : enregistrer l'ordre lu dans data/protocoles/venu360_phase.yaml")
    # valeurs négatives (« -2.0dB ») : les protéger d'argparse
    argv = [("\x00" + t) if re.match(r"^-\d", t) else t for t in sys.argv[1:]]
    a = ap.parse_args(argv)
    a.args = [t[1:] if t.startswith("\x00") else t for t in a.args]

    if a.cmd == "decouvrir":
        for ip, l in decouvrir():
            print(ip, l)
        return
    if a.cmd == "diff":
        av, ap_ = (json.loads(Path(p).read_text(encoding="utf-8")) for p in a.args[:2])
        for k, x, y in diff(av, ap_):
            print(f"{k}\n    avant : {x}\n    après : {y}")
        return

    besoin = {"appliquer-plan": 2, "rappel": 2, "stocker": 2, "rpc": 2, "set": 3, "regler": 3, "lot": 2, "get": 2, "ls": 2, "ecouter": 2}.get(a.cmd, 1)
    if len(a.args) < besoin:
        sys.exit(f"{a.cmd} : {besoin} argument(s) attendu(s) (voir --help).")
    # Refus décidables sans l'appareil : AVANT toute connexion (aucune trame ne part pour une demande refusée).
    if a.cmd in ("rappel", "stocker"):
        if not re.fullmatch(r"\d{1,3}", a.args[1]) or not 1 <= int(a.args[1]) <= 100:
            sys.exit(f"REFUS : numéro de preset invalide : {a.args[1]!r} (1 à 100).")
        a.args[1] = str(int(a.args[1]))                      # « 05 » → « 5 », clé de Get presetName
        if a.nom and a.cmd == "rappel":
            sys.exit("REFUS : --nom ne sert qu'à `stocker` (le rappel ne renomme rien).")
        if a.nom and not re.fullmatch(r"[A-Za-z0-9 _.+\-()/']{1,50}", a.nom):
            sys.exit("REFUS : nom de preset : 1 à 50 caractères (lettres sans accent, chiffres, espace, _ . + - ( ) / ').")
        if a.cmd == "rappel" and not a.protection_validee:
            sys.exit("REFUS : un rappel de preset modifie aussi les PROTECTIONS (limiteurs, HPF, mutes). "
                     "Validation explicite de Louis requise (--protection-validee).")
    plan_ecr = None
    if a.cmd == "appliquer-plan":
        import dbx_plan
        if a.nom and not re.fullmatch(r"[A-Za-z0-9 _.+\-()/']{1,50}", a.nom):
            sys.exit("REFUS : nom de preset : 1 à 50 caractères (lettres sans accent, chiffres, espace, _ . + - ( ) / ').")
        if a.case is not None and not 1 <= a.case <= 100:
            sys.exit("REFUS : --case : 1 à 100.")
        if (a.verifier or a.nom) and a.case is None:
            sys.exit("REFUS : --nom / --verifier demandent --case (case où stocker le preset).")
        try:
            plan = json.loads(Path(a.args[1]).read_text(encoding="utf-8"))
            plan_ecr, notes = dbx_plan.ecritures(plan, a.depasser_plafond)
        except (OSError, ValueError, KeyError) as e:
            sys.exit(f"REFUS : {e}")
        if not a.protection_validee:
            sys.exit("REFUS : le plan écrit des PROTECTIONS (limiteurs, filtres, mutes). "
                     "Validation explicite de Louis requise (--protection-validee).")
        if not a.appliquer:
            print(f"SIMULATION — {plan['systeme']} · style {plan['style']} · {len(plan_ecr)} écritures "
                  "(protections d'abord), sorties mutées pendant l'opération :")
            for n_ in notes:
                print("  " + n_)
            for ch, val, voie, prot in plan_ecr:
                print(f"  {'[P] ' if prot else '    '}{voie:7} {ch} = {val}")
            print("Ajouter --appliquer" + (f" ; stockage case {a.case} {a.nom!r}" if a.case else "") + ".")
            return
    if a.cmd == "rpc" and a.args[1] not in RPC_LECTURE:      # liste blanche : la commande rpc ne fait que lire
        sys.exit(f"REFUS : {a.args[1]} n'est pas une méthode de lecture connue ({', '.join(sorted(RPC_LECTURE))}). "
                 "Rappel et stockage de presets : commandes « rappel » et « stocker » (instantané automatique) ; "
                 + ("méthode irréversible : appli officielle seulement." if a.args[1] in RPC_INTERDITS else
                    "autre écriture : non prise en charge."))

    ip = a.args[0]
    mdp = a.mdp or os.environ.get("DBX_MDP")
    if not mdp:
        mdp = "administrator"
        print("NB : mot de passe d'usine « administrator » utilisé (DBX_MDP non défini). "
              "Le changer selon le manuel si le processeur est sur un réseau partagé.", file=sys.stderr)
    with Dbx(ip, a.port, mdp, verbose=a.verbose) as d:
        if a.cmd == "info":
            for c in ("Class_Name", "Instance_Name", "Software_Version"):
                print(c, ":", d.get(f"\\\\Node\\AT\\{c}"))
        elif a.cmd == "get":
            print(d.get(a.args[1]))
        elif a.cmd == "ls":
            for k, v in d.ls(a.args[1]).items():
                print(f"{k} : {v}")
        elif a.cmd == "decrire":
            desc = dd.decrire_tout(d, progression=lambda b, n: print(f"  {b} : {n} paramètres", file=sys.stderr))
            # fichier destiné au dépôt : ni adresse IP, ni réglage d'appareil sensible
            desc = {c: x for c, x in desc.items()
                    if not re.search(r"password|passwd|mdp|e?mail|address|\bip\b|network|dhcp|subnet|gateway", c, re.I)}
            out = Path(a.sortie or RACINE / "data" / "venu360_description.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"_meta": {"date": dt.datetime.now().isoformat(timespec="seconds"),
                                                 "preset": d.get("\\\\Node\\Settings\\SV\\LoadedPresetName")},
                                       **desc}, ensure_ascii=False, indent=1), encoding="utf-8")
            n_ecr = sum(dd.ecrivable(c, x) for c, x in desc.items())
            print(f"Écrit : {out} ({len(desc)} paramètres décrits, dont {n_ecr} réglages écrivables)")
        elif a.cmd == "topologie":
            print(json.dumps(dd.topologie(d), ensure_ascii=False, indent=1))
        elif a.cmd == "appliquer-plan":
            import dbx_plan
            try:
                snap = instantane_avant(d, ip, "avant-plan")
            except ValueError as e:
                sys.exit(f"REFUS : {e}")
            print(f"Instantané avant écriture : {snap}")
            try:
                r = dbx_plan.appliquer(d, plan, journaliser, ip, depasser_plafond=a.depasser_plafond)
            except ValueError as e:
                sys.exit(f"REFUS : {e}. Rien n'a été écrit.")
            except dd.SortiesNonMutees as e:
                sys.exit(f"REFUS : {e}. Rien n'a été écrit ; certaines sorties ont pu rester mutées (état d'avant : {snap}).")
            print(f"{r['ecrits']}/{len(plan_ecr)} écritures conformes · {len(r['echecs'])} échec(s) · {len(r['refus'])} refus · "
                  f"{len(r['derives'])} dérive(s) · sorties mutées")
            for e in r["echecs"] + r["refus"] + r["derives"]:
                print(f"  ✗ {e}")
            if r["arret"]:
                print(f"  ARRÊT : {r['arret']} — sorties laissées MUTÉES.")
            if not r["conforme"]:
                print(f"Pour revenir à l'état d'avant : restaurer {ip} {snap} --appliquer --protection-validee")
                sys.exit(1)

            def echec(msg):
                """Tout échec après les écritures : re-muter les 6 sorties (relu) avant de sortir."""
                try:
                    dd.muter_et_verifier(d)
                    etat = "6 sorties re-mutées (relu)"
                except dd.SortiesNonMutees as e2:
                    etat = f"RE-MUTE NON CONFIRMÉ : {e2} — couper les amplis"
                journaliser(f"ECHEC plan sur {ip} : {msg} — {etat}")
                sys.exit(f"ÉCHEC : {msg}. {etat}. État d'avant : {snap}")

            if a.demuter:
                ok, relu = dbx_plan.demuter_utilisees(d, plan)
                journaliser(f"DBX PLAN {ip} démute des sorties utilisées : {'OK' if ok else 'NON CONFORME'} {relu}")
                if not ok:
                    echec(f"état des mutes non conforme après démute : {relu}")
                print("Sorties du plan démutées (relu), inutilisées mutées.")
            if a.case is not None:
                n = str(a.case)
                avant_nom = {}
                for x in d.rpc("Get", [n, n, "presetName"]):
                    avant_nom.update(x.get("result") or {})
                ancien = str(avant_nom.get(n) or "").strip()
                if ancien and ancien != (a.nom or "").strip() and not a.ecraser:
                    echec(f"la case {n} contient déjà « {ancien} » : --ecraser pour la remplacer (son contenu ne peut "
                          "pas être sauvegardé avant)")
                reps = d.rpc("Store", [n] + ([a.nom] if a.nom else []))
                erreurs = [x["error"] for x in reps if x.get("error")]
                if not reps or erreurs:
                    echec(f"stockage case {n} : {erreurs or 'aucune réponse'} (réglages écrits mais non stockés)")
                relu_nom = {}
                for x in d.rpc("Get", [n, n, "presetName"]):
                    relu_nom.update(x.get("result") or {})
                journaliser(f"STORE preset {n} sur {ip} (plan {plan['systeme']}) : {ancien!r} -> nom relu {relu_nom.get(n)!r}")
                print(f"Stocké case {n} : nom relu {relu_nom.get(n)!r}")
                if a.nom and str(relu_nom.get(n, "")).strip() != a.nom.strip():
                    echec(f"la case {n} s'appelle {relu_nom.get(n)!r}, pas {a.nom!r}")
                if a.verifier:
                    d.rpc("Load", [n])
                    ecarts = dbx_plan.verifier_apres_rappel(d, r)
                    journaliser(f"VERIF plan case {n} sur {ip} : {len(ecarts)} écart(s) après rappel")
                    print(f"Après rappel de la case {n} : {len(ecarts)} écart(s)")
                    for e in ecarts:
                        print(f"  ✗ {e}")
                    if ecarts:
                        echec(f"{len(ecarts)} écart(s) après rappel de la case {n}")
        elif a.cmd == "instantane":
            t = dd.instantane_complet(d)
            if len(t) < 50:                       # firmware ou simulateur qui ne se décrit pas : carte connue
                t = aplatir(d.carte())
            t["_meta"] = {"ip": ip, "date": dt.datetime.now().isoformat(timespec="seconds")}
            out = Path(a.sortie or JOURNAL / "instantanes" / f"{dt.datetime.now():%Y%m%d-%H%M%S}.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"Écrit : {out} ({len(t) - 1} valeurs)")
        elif a.cmd in ("restaurer", "autotest"):
            if not a.protection_validee:
                sys.exit(f"REFUS : {a.cmd} écrit aussi des PROTECTIONS (limiteurs, HPF, mutes). "
                         "Validation explicite de Louis requise (--protection-validee).")
            if not a.appliquer:
                print(f"SIMULATION : {a.cmd} (ajouter --appliquer). Les 6 sorties seront mutées pendant l'opération.")
                return
            snap = JOURNAL / "instantanes" / f"{dt.datetime.now():%Y%m%d-%H%M%S}-avant-{a.cmd}.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            etat = dd.instantane_complet(d)
            etat["_meta"] = {"ip": ip, "date": dt.datetime.now().isoformat(timespec="seconds")}
            snap.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"Instantané complet avant {a.cmd} : {snap}")
            if a.cmd == "restaurer":
                if len(a.args) < 2:
                    sys.exit("restaurer : <ip> <instantane.json> attendus.")
                brut = json.loads(Path(a.args[1]).read_text(encoding="utf-8"))
                ip_snap = (brut.get("_meta") or {}).get("ip")
                if ip_snap and ip_snap != ip:
                    sys.exit(f"REFUS : instantané pris sur {ip_snap}, pas sur {ip}.")
                cible = aplatir(brut)
                try:
                    r = dd.restaurer(d, cible, journal=journaliser, seuils_hausse_autorises=a.depasser_plafond)
                except dd.SortiesNonMutees as e:
                    sys.exit(f"REFUS : {e}. Aucun réglage réécrit ; certaines sorties ont pu rester mutées "
                             f"(état d'avant : {snap}).")
                print(f"{r['ecarts']} écart(s) réécrit(s), {len(r['echecs'])} échec(s), {len(r['refuses'])} refus, "
                      f"{len(r['ecarts_restants'])} écart(s) restant(s) · sorties : {r['sorties']}")
                for e in r["echecs"]:
                    print(f"  ÉCHEC {e.get('chemin')} : cible {e.get('cible')}, relu {e.get('relu')} {e.get('erreur') or ''}")
                for e in r["refuses"]:
                    print(f"  REFUS {e['chemin']} : {e['cible']} ({e['motif']})")
                if not r["conforme"]:
                    sys.exit(1)
                return
            filtre = [b.strip() for b in a.blocs.split(",")] if a.blocs else None
            try:
                rapport = dd.autotest(d, filtre, journal=journaliser,
                                      progression=lambda n: n % 100 == 0 and print(f"  {n} paramètres…", file=sys.stderr))
            except dd.SortiesNonMutees as e:
                sys.exit(f"REFUS : {e}. Autotest annulé, aucun réglage écrit ; certaines sorties ont pu rester mutées "
                         f"(état d'avant : {snap}).")
            out = Path(a.sortie or JOURNAL / f"autotest-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
            out.write_text(json.dumps(rapport, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"Paramètres décrits : {rapport['decrits']} · testés : {rapport['testes']} · écriture OK : "
                  f"{rapport['ecriture_ok']} · restaurés : {rapport['restaures']} · non testés : {len(rapport['non_testes'])}")
            print(f"Refus attendus vérifiés : {rapport['refus_attendus']} · refus manqués : {len(rapport['refus_manques'])}")
            print(f"Arrondis par la machine : {sum(1 for l in rapport['lignes'] if l.get('arrondi_machine'))} · "
                  f"refusés par la machine (option indisponible) : {len(rapport['refus_appareil'])} · "
                  f"échecs : {len(rapport['echecs'])}")
            print(f"Restauration finale : {rapport['restauration']['ecarts']} écart(s), "
                  f"{len(rapport['restauration']['echecs'])} échec(s) · écarts restants : {len(rapport['ecarts_finaux'])}")
            print(f"Dérives brutes (protections) : {len(rapport['derives_brutes'])} · seuils relevés : "
                  f"{len(rapport['seuils_releves'])}" + (f" · INTERROMPU : {rapport['erreur_boucle']}" if rapport['erreur_boucle'] else ""))
            print(f"Sorties : {rapport['sorties']}")
            print(f"Rapport : {out}")
            journaliser(f"AUTOTEST fin {ip} : {rapport['ecriture_ok']}/{rapport['testes']} OK, "
                        f"{len(rapport['ecarts_finaux'])} écart(s) final(aux), sorties {rapport['sorties']} [{out.name}]")
            if rapport["echecs"] or rapport["refus_manques"] or rapport["ecarts_finaux"] or rapport["seuils_releves"] \
                    or rapport["erreur_boucle"]:
                print(f"Pour revenir à l'état d'avant : restaurer {ip} {snap} --appliquer --protection-validee")
                sys.exit(1)
        elif a.cmd in ("arbre", "carte"):
            t = d.arbre(a.racine, a.profondeur) if a.cmd == "arbre" else d.carte()
            t["_meta"] = {"ip": ip, "date": dt.datetime.now().isoformat(timespec="seconds")}
            out = Path(a.sortie or JOURNAL / "instantanes" / f"{dt.datetime.now():%Y%m%d-%H%M%S}.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"Écrit : {out} ({len(aplatir(t))} valeurs)")
        elif a.cmd == "ecouter":
            d.ecouter(a.args[1], a.duree)
        elif a.cmd == "presets":
            for r in d.rpc("Get", ["1", "100", "presetName"]):
                for k, v in sorted((r.get("result") or {}).items(), key=lambda t: int(t[0])):
                    print(f"{k}: {v}")
        elif a.cmd in ("rappel", "stocker"):
            n = a.args[1]                       # validé, normalisé et verrou vérifié AVANT la connexion
            rappel = a.cmd == "rappel"
            if not a.appliquer:
                print(f"SIMULATION : rappel du preset {n} (ajouter --appliquer). Couper les amplis ou muter avant un rappel."
                      if rappel else
                      f"SIMULATION : mémoriser l'état courant dans le preset {n} (ÉCRASE ce preset)"
                      + (f" sous le nom {a.nom!r}" if a.nom else "") + ". Ajouter --appliquer.")
                return
            noms = {}
            for r in d.rpc("Get", ["1", "100", "presetName"]):
                noms.update(r.get("result") or {})
            try:
                snap = instantane_avant(d, ip, f"avant-{a.cmd}-{n}")
            except ValueError as e:
                sys.exit(f"REFUS : {e}")
            print(f"Instantané complet avant {a.cmd} : {snap}")
            journaliser(f"{'RAPPEL' if rappel else 'STORE'} preset {n} ({noms.get(n, 'vide')!r}) sur {ip} "
                        f"[instantané {snap.name}]"
                        + ("" if rappel else " — l'ancien contenu du preset n'est pas sauvegardé (illisible sans le charger)")
                        + (f" — renommé {noms.get(n)!r} -> {a.nom!r}" if a.nom and not rappel else ""))
            # Store [n, nom] renomme la case (constaté le 24/11/2026) ; Store [n] garde le nom du preset chargé
            reps = d.rpc("Load" if rappel else "Store", [n] + ([a.nom] if a.nom and not rappel else []))
            for r in reps:
                print(r.get("result"), r.get("error") or "")
            erreurs = [r["error"] for r in reps if r.get("error")]
            if not reps or erreurs:
                journaliser(f"ECHEC {a.cmd} preset {n} sur {ip} : {erreurs or 'aucune réponse'}")
                sys.exit(f"ÉCHEC : {a.cmd} du preset {n} : {erreurs or 'aucune réponse'}. Comparer à l'instantané {snap}.")
            if not rappel:
                relu_nom = {}
                for r in d.rpc("Get", [n, n, "presetName"]):
                    relu_nom.update(r.get("result") or {})
                attendu = a.nom or None
                print(f"  nom de la case {n} (relu) : {relu_nom.get(n)!r}")
                journaliser(f"STORE preset {n} sur {ip} : nom relu {relu_nom.get(n)!r}")
                if attendu and str(relu_nom.get(n, "")).strip() != attendu.strip():
                    journaliser(f"ECART nom preset {n} sur {ip} : relu {relu_nom.get(n)!r}, attendu {attendu!r}")
                    sys.exit(f"ÉCHEC : la case {n} s'appelle {relu_nom.get(n)!r}, pas {attendu!r}.")
            if rappel:
                try:
                    charge = d.get("\\\\Node\\Settings\\SV\\LoadedPresetName")
                except ValueError as e:
                    charge = None
                    print(f"  relecture du preset chargé impossible ({e}) : vérifier à l'écran.")
                print(f"  preset chargé (relu) : {charge}")
                journaliser(f"RAPPEL preset {n} sur {ip} : chargé = {charge} (attendu {noms.get(n)!r})")
                print("  À FAIRE : relire les seuils de limiteur du preset chargé (`carte`) et les comparer aux plafonds "
                      "calculés (calc_systeme) : un preset peut contenir des seuils plus hauts.")
                if noms.get(n) and charge is not None and str(charge).strip() != str(noms[n]).strip():
                    journaliser(f"ECART rappel preset {n} sur {ip} : chargé {charge!r}, attendu {noms[n]!r}")
                    sys.exit(f"ÉCHEC : preset chargé « {charge} » au lieu de « {noms[n]} ». Comparer à l'instantané {snap}.")
        elif a.cmd == "rpc":
            methode, params = a.args[1], a.args[2:]   # liste blanche de lecture vérifiée avant la connexion
            print(json.dumps(d.rpc(methode, params), ensure_ascii=False, indent=1))
        elif a.cmd in ("set", "regler"):
            import dbx_reglage as dr
            o = _options(a)
            try:
                if a.cmd == "regler":
                    chemin, voie = dr.resoudre(a.args[1], a.config)
                    if voie and o.voie and o.voie.upper() != voie.upper():
                        raise dr.Refus(f"--voie {o.voie} contredit l'alias {a.args[1]} (voie {voie})")
                    o.voie = voie or o.voie
                    dr.verifier_cible(d, chemin, a.args[1])
                    print(f"{a.args[1]} -> {chemin}" + (f" (voie {voie})" if voie else ""))
                else:
                    chemin = a.args[1]
                valeur = a.args[2]
                r = dr.ecrire_controle(d, ip, chemin, valeur, o)
            except dr.Refus as e:
                sys.exit(f"REFUS : {e}.")
            except dr.Echec as e:
                sys.exit(f"ÉCHEC : {e}.")
            print(f"{chemin}\n  actuel : {r['avant']}\n  demandé : {valeur}")
            for n_ in r["notes"]:
                print(f"  {n_}")
            if len(r["pas"]) > 1:
                print(f"  pas : {' → '.join(r['pas'])}")
            if r["simulation"]:
                print("SIMULATION — rien n'a été écrit (ajouter --appliquer).")
                return
            print(f"  relu : {r['apres']}")
        elif a.cmd == "lot":
            import dbx_reglage as dr
            o = _options(a)
            try:
                items, cfg = dr.lire_lot(a.args[1])
                o.config = o.config or cfg
                r = dr.lot(d, ip, items, o, avant_ecriture=lambda: instantane_avant(d, ip, "avant-lot"))
                if r.get("instantane"):
                    print(f"Instantané complet avant le lot : {r['instantane']}")
            except dr.Refus as e:
                sys.exit(f"REFUS : {e}. Rien n'a été écrit.")
            except ValueError as e:
                sys.exit(f"REFUS : {e}. Rien n'a été écrit.")
            print(f"Config de référence (plafonds, voies) : {o.config or 'AUCUNE'}"
                  + (" (prise dans le fichier de lot)" if not a.config and o.config else ""))
            for p in r["prevus"]:
                print(f"  {p['alias']:28} {str(p['avant']):>14} → {p['demande']:<14}"
                      + (f" ({p['pas']} pas)" if p["pas"] > 1 else "") + "".join(f" [{n_}]" for n_ in p["notes"]))
            if not o.appliquer:
                print(f"SIMULATION — {len(r['prevus'])} réglage(s) contrôlé(s), rien n'a été écrit (ajouter --appliquer).")
                return
            if not r["conforme"]:
                print(f"ÉCHEC : {r['echec']}")
                for x in r["retour"]:
                    print(f"  retour arrière {x['chemin']} : {'remis' if x['remis'] else 'NON remis'} "
                          f"({x.get('relu') or x.get('motif') or x.get('erreur')})")
                if r.get("retour_interrompu"):
                    print(f"  RETOUR ARRIÈRE INTERROMPU sur {r['retour_interrompu']} : restaurer l'instantané "
                          f"{r.get('instantane')} (restaurer --appliquer --protection-validee)")
                sys.exit(1 if all(x["remis"] or x.get("motif") for x in r["retour"]) else 2)
            print(f"{len(r['ecrits'])} réglage(s) écrit(s) et relu(s).")
        elif a.cmd == "chaine":
            ordre = dd.chaine_sortie_machine(d)
            print(json.dumps(ordre, ensure_ascii=False, indent=1))
            if a.enregistrer:
                if ordre["chaine_sortie"] == "indetermine":
                    sys.exit("REFUS : ordre indéterminé, rien n'est enregistré.")
                import dbx_plan
                out = dbx_plan.enregistrer_chaine(ordre, d.get("\\\\Node\\AT\\Software_Version"))
                journaliser(f"CHAINE {ip} : {ordre['chaine_sortie']} enregistrée dans {out} (topologie machine)")
                print(f"Enregistré : {out}")


def _options(a):
    import dbx_reglage as dr
    return dr.Options(appliquer=a.appliquer, protection_validee=a.protection_validee, signal_valide=a.signal_valide,
                      config=a.config, voie=a.voie, depasser_plafond=a.depasser_plafond, hors_carte=a.hors_carte,
                      par_pas=a.par_pas, pas_libre=a.pas_libre, journal=journaliser,
                      dossier_instantanes=JOURNAL / "instantanes")


if __name__ == "__main__":
    main()
