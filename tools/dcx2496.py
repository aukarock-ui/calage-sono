#!/usr/bin/env python3
"""
dcx2496.py — Client RS-232 pour Behringer ULTRADRIVE PRO DCX2496 (lecture complète, écriture sécurisée).

Protocole : trames type SysEx F0 00 20 32 … F7, 38400 bauds 8N1. Table des paramètres :
data/protocoles/dcx2496_parametres.json (issue de dcx-ui, MIT, © Lasse Lukkari ; référence matérielle DuinoDCX).

Câblage : adaptateur USB-RS232 → port « RS-232 » du DCX, câble NULL-MODEM (croisé). Sur le DCX : SETUP, page 6,
PORT = « PC (RS232) ». Un seul logiciel à la fois sur le port (fermer le DCX Remote officiel).

Usage :
  python tools/dcx2496.py ports                                   # liste des ports série
  python tools/dcx2496.py chercher COM3                           # appareils présents (id, nom, version)
  python tools/dcx2496.py lire COM3 [--id 0] [-o journal/instantanes/dcx.json]   # état complet lisible
  python tools/dcx2496.py diff avant.json apres.json
  python tools/dcx2496.py set COM3 --sortie 1 "Highpass Frequency" 45 [--appliquer] [--protection-validee]
  python tools/dcx2496.py set COM3 --entree A "Gain" -3.0 [--appliquer]
  python tools/dcx2496.py set COM3 --sortie 2 --eq 3 "EQ Gain" -2.5 [--appliquer]
  python tools/dcx2496.py parametres                              # noms de paramètres disponibles
  python tools/dcx2496.py autotest                                # vérifie codage/décodage sans matériel

Sécurité : lecture seule par défaut ; écriture = --appliquer ; paramètres de protection (filtres HP/LP, limiteur,
mute, polarité) = --protection-validee après accord explicite de Louis ; instantané + journal à chaque écriture.
Le DCX n'a pas de relecture d'un paramètre isolé : après écriture, l'outil refait un dump complet et vérifie.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
TABLE = json.loads((RACINE / "data" / "protocoles" / "dcx2496_parametres.json").read_text(encoding="utf-8"))
JOURNAL = RACINE / "journal"
ENTETE = bytes([0xF0, 0x00, 0x20, 0x32])
FIN = 0xF7
LONGUEURS_DUMP = {0: 1015, 1: 911}
# « link » : activer un lien stéréo ÉCRASE les réglages des sorties couplées (DCX-Remote : « All Settings of Output
# 4 5 6 Will Get Lost ! ») -> jamais sans --protection-validee.
PROTECTION = ("highpass", "lowpass", "limiter", "mute", "polarity", "link")
LIENS = ("Stereolink", "Stereolink Mode", "Delay Link", "Crossover Link")   # paramètres setup qui couplent des canaux


# ---------------- codage ----------------
def code_valeur(p: dict, v):
    t = p["type"]
    if t == "bool":
        return 1 if str(v).lower() in ("1", "true", "on", "oui", "yes") else 0
    if t == "enum":
        vals = [str(x) for x in p["valeurs"]]
        if str(v) in vals:
            return vals.index(str(v))
        try:  # valeur numérique : plus proche dans la liste (ex. fréquence)
            f = float(v)
            num = [(abs(float(x) - f), i) for i, x in enumerate(vals) if _est_nombre(x)]
            if num:
                return min(num)[1]
        except ValueError:
            pass
        raise ValueError(f"« {v} » absent des valeurs de {p['nom']} : {vals[:8]}…")
    n = round(abs(p["min"] - float(v)) / p["step"])
    return n


def decode_valeur(p: dict, n: int):
    t = p["type"]
    if t == "bool":
        return n != 0
    if t == "enum":
        return p["valeurs"][n] if 0 <= n < len(p["valeurs"]) else None
    return round(p["min"] + p["step"] * n, 4)


def _est_nombre(x) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def trame_set(id_app: int, liste: list[tuple[int, int, int]]) -> bytes:
    corps = bytearray(ENTETE + bytes([id_app, 0x0E, 0x20, len(liste)]))
    for canal, param, v in liste:
        if not 0 <= v < 128 * 128:
            raise ValueError("valeur hors plage 14 bits")
        corps += bytes([canal, param, v // 128, v % 128])
    corps.append(FIN)
    return bytes(corps)


def localiser(nom: str, entree: str | None, sortie: int | None, eq: int | None):
    """-> (définition, canal, numéro de paramètre, index de synchro)."""
    cle = nom.lower()
    if eq is not None:
        for p in TABLE["eq"]:
            if p["nom"].lower() == cle:
                ch = _canal(entree, sortie)
                io = ch - 1
                return p, ch, p["param_base"] + (eq - 1) * 5, io * 9 + (eq - 1)
    if sortie is not None:
        for p in TABLE["sorties"]:
            if p["nom"].lower() == cle:
                return p, sortie + 4, p["param"], sortie - 1
    if entree is not None or sortie is not None:
        for p in TABLE["io"]:
            if p["nom"].lower() == cle:
                ch = _canal(entree, sortie)
                return p, ch, p["param"], ch - 1
    for p in TABLE["setup"]:
        if p["nom"].lower() == cle:
            return p, 0, p["param"], None
    raise KeyError(f"Paramètre inconnu : {nom} (voir `parametres`)")


def _canal(entree, sortie) -> int:
    if entree is not None:
        return TABLE["entrees"].index(str(entree)) + 1
    return int(sortie) + 4


def valeur_brute(parts: dict, sync: dict) -> int:
    b6 = sync["bits6"]
    v = parts[b6["part"]][b6["index"]]
    if sync.get("bit7"):
        b7 = sync["bit7"]
        v += 128 if parts[b7["part"]][b7["index"]] & (1 << b7["bit"]) else 0
    if sync.get("bits8"):
        b8 = sync["bits8"]
        v += parts[b8["part"]][b8["index"]] * 256
    return v


def decoder_etat(parts: dict) -> dict:
    etat = {"setup": {}, "entrees": {}, "sorties": {}}
    for p in TABLE["setup"]:
        etat["setup"][p["nom"]] = decode_valeur(p, valeur_brute(parts, p["sync"]))
    canaux = [("entrees", e) for e in TABLE["entrees"]] + [("sorties", s) for s in TABLE["sorties_noms"]]
    for g, c in canaux:
        etat[g][c] = {"EQ": {}}
    for p in TABLE["io"]:
        for i, s in enumerate(p["sync"]):
            g, c = canaux[i]
            etat[g][c][p["nom"]] = decode_valeur(p, valeur_brute(parts, s))
    for p in TABLE["sorties"]:
        for i, s in enumerate(p["sync"]):
            etat["sorties"][TABLE["sorties_noms"][i]][p["nom"]] = decode_valeur(p, valeur_brute(parts, s))
    for p in TABLE["eq"]:
        for io, (g, c) in enumerate(canaux):
            for k in range(9):
                etat[g][c]["EQ"].setdefault(str(k + 1), {})[p["nom"]] = decode_valeur(p, valeur_brute(parts, p["sync"][io * 9 + k]))
    return etat


# ---------------- transport ----------------
class DCX:
    def __init__(self, port: str, id_app: int = 0, timeout: float = 3.0):
        import serial  # pyserial
        self.s = serial.Serial(port, 38400, bytesize=8, parity="N", stopbits=1, timeout=0.1)
        self.id, self.timeout = id_app, timeout

    def fermer(self):
        self.s.close()

    def _envoyer(self, b: bytes):
        self.s.write(b)
        self.s.flush()

    def _lire_message(self, timeout=None) -> bytes | None:
        fin, buf = time.time() + (timeout or self.timeout), bytearray()
        while time.time() < fin:
            c = self.s.read(1)
            if not c:
                continue
            if c[0] == 0xF0:
                buf = bytearray(c)
            elif buf:
                buf += c
                if c[0] == FIN:
                    return bytes(buf)
        return None

    def chercher(self) -> list[dict]:
        self._envoyer(ENTETE + bytes([0x20, 0x0E, 0x40, FIN]))
        res, fin = [], time.time() + 2
        while time.time() < fin:
            m = self._lire_message(0.5)
            if m and len(m) >= 25 and m[6] == 0x00:
                res.append({"id": m[4], "version": f"{m[7]}.{m[8]}", "nom": bytes(m[9:25]).decode("ascii", "replace").strip()})
        return res

    def dump(self) -> dict:
        self._envoyer(ENTETE + bytes([self.id, 0x0E, 0x3F, 0x0C, 0x00, FIN]))   # mode transmission
        parts = {}
        for part in (0, 1):
            self._envoyer(ENTETE + bytes([self.id, 0x0E, 0x50, 0x01, 0x00, part, FIN]))
            fin = time.time() + self.timeout
            while time.time() < fin:
                m = self._lire_message()
                if m and len(m) > 12 and m[6] == 0x10 and m[12] == part:
                    if len(m) != LONGUEURS_DUMP[part]:
                        raise IOError(f"dump part {part} : {len(m)} octets au lieu de {LONGUEURS_DUMP[part]}")
                    parts[part] = m
                    break
            else:
                raise TimeoutError(f"pas de réponse au dump part {part} (port, câble null-modem, PORT=PC (RS232) ?)")
        return parts

    def ecrire(self, liste):
        self._envoyer(trame_set(self.id, liste))


# ---------------- outils ----------------
def aplatir(d, p=""):
    o = {}
    for k, v in d.items():
        q = f"{p}/{k}" if p else str(k)
        o.update(aplatir(v, q) if isinstance(v, dict) else {q: v})
    return o


def journaliser(t: str):
    JOURNAL.mkdir(exist_ok=True)
    with (JOURNAL / "ecritures.log").open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} DCX2496 {t}\n")


def autotest():
    # 1. codage des valeurs
    p, ch, num, idx = localiser("Limiter Threshold", None, 3, None)
    assert (ch, num, idx) == (7, 71, 2), (ch, num, idx)
    v = code_valeur(p, -6.0)
    assert decode_valeur(p, v) == -6.0
    p2, ch2, num2, idx2 = localiser("EQ Gain", None, 2, 4)
    assert (ch2, num2) == (6, 21 + 15), (ch2, num2)
    p3, *_ = localiser("Highpass Frequency", None, 1, None)
    assert decode_valeur(p3, code_valeur(p3, 45)) in ("45", "44.5", "45.5")
    # 2. trame
    t = trame_set(0, [(7, 71, v)])
    assert t[:8] == bytes([0xF0, 0, 0x20, 0x32, 0, 0x0E, 0x20, 1]) and t[-1] == 0xF7
    # 3. décodage d'un état synthétique cohérent avec la table
    parts = {0: bytearray(LONGUEURS_DUMP[0]), 1: bytearray(LONGUEURS_DUMP[1])}
    s = p["sync"][2]
    parts[s["bits6"]["part"]][s["bits6"]["index"]] = v % 128 if "bit7" in s else v
    if "bit7" in s and v >= 128:
        b7 = s["bit7"]; parts[b7["part"]][b7["index"]] |= 1 << b7["bit"]
    e = decoder_etat(parts)
    assert e["sorties"]["3"]["Limiter Threshold"] == -6.0, e["sorties"]["3"]["Limiter Threshold"]
    # 4. tous les paramètres de lien existent dans la table, et un dump vierge ne signale aucun lien
    noms_setup = {q["nom"] for q in TABLE["setup"]}
    assert set(LIENS) <= noms_setup, set(LIENS) - noms_setup
    assert all(e["setup"][k] in (None, False, 0, "Off", "off", "0") for k in LIENS), {k: e["setup"][k] for k in LIENS}
    print("autotest DCX2496 : OK (codage, numéros de paramètres, trame, décodage du dump, liens)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["ports", "chercher", "lire", "diff", "set", "parametres", "autotest"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--id", type=int, default=0)
    ap.add_argument("--entree")
    ap.add_argument("--sortie", type=int)
    ap.add_argument("--eq", type=int)
    ap.add_argument("-o", "--fichier")
    ap.add_argument("--appliquer", action="store_true")
    ap.add_argument("--protection-validee", action="store_true")
    ap.add_argument("--lien-accepte", action="store_true", help="écrire malgré un lien stéréo/délai/crossover actif")
    argv = [("\x00" + t) if t[:1] == "-" and t[1:2].isdigit() else t for t in sys.argv[1:]]
    a = ap.parse_intermixed_args(argv)
    a.args = [t[1:] if t.startswith("\x00") else t for t in a.args]

    if a.cmd == "autotest":
        return autotest()
    if a.cmd == "parametres":
        for g in ("setup", "io", "sorties", "eq"):
            print(f"[{g}]", " | ".join(p["nom"] for p in TABLE[g]))
        return
    if a.cmd == "ports":
        from serial.tools import list_ports
        for p in list_ports.comports():
            print(p.device, "-", p.description)
        return
    if a.cmd == "diff":
        x, y = (aplatir(json.loads(Path(f).read_text(encoding="utf-8"))) for f in a.args[:2])
        for k in sorted(set(x) | set(y)):
            if x.get(k) != y.get(k) and not k.startswith("_"):
                print(f"{k} : {x.get(k)} -> {y.get(k)}")
        return

    d = DCX(a.args[0], a.id)
    try:
        if a.cmd == "chercher":
            for r in d.chercher():
                print(r)
        elif a.cmd == "lire":
            etat = decoder_etat(d.dump())
            etat["_meta"] = {"port": a.args[0], "id": a.id, "date": dt.datetime.now().isoformat(timespec="seconds")}
            out = Path(a.fichier or JOURNAL / "instantanes" / f"dcx-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"Écrit : {out}")
            for s, v in etat["sorties"].items():
                print(f"Sortie {s} : {v.get('Channel Name')} | HP {v.get('Highpass Filter')} {v.get('Highpass Frequency')} Hz | "
                      f"LP {v.get('Lowpass Filter')} {v.get('Lowpass Frequency')} Hz | gain {v.get('Gain')} dB | "
                      f"pol {v.get('Polarity')} | lim {v.get('Is Limiter On')} {v.get('Limiter Threshold')} dB | mute {v.get('Mute')}")
        elif a.cmd == "set":
            nom, valeur = a.args[1], a.args[2]
            p, ch, num, idx = localiser(nom, a.entree, a.sortie, a.eq)
            brut = code_valeur(p, valeur)
            avant = decoder_etat(d.dump())
            print(f"{nom} (canal {ch}, param {num}) -> {decode_valeur(p, brut)} {p.get('unite') or ''}")
            if any(m in nom.lower() for m in PROTECTION) and not a.protection_validee:
                sys.exit("REFUS : paramètre de PROTECTION. Il faut la validation explicite de Louis (--protection-validee).")
            # Liens (aide officielle DCX-Remote, FAQ 8-11) : avec un lien stéréo actif, la valeur d'une sortie est
            # recopiée sur la sortie couplée de même type (L/M/H : 1-4, 2-5, 3-6), limiteur et délai court compris.
            liens = {k: v for k, v in (avant.get("setup") or {}).items()
                     if k in LIENS and v not in (None, False, 0, "Off", "off", "0")}
            if liens:
                print(f"ATTENTION — liens actifs sur le DCX : {liens}. L'écriture peut se propager à d'autres canaux "
                      "(sortie couplée, délais liés).")
                if not a.lien_accepte:
                    sys.exit("REFUS : lien actif. Désactiver le lien sur le DCX, ou relancer avec --lien-accepte "
                             "après accord de Louis sur les canaux couplés.")
            if not a.appliquer:
                print("SIMULATION — rien n'a été écrit (ajouter --appliquer).")
                return
            snap = JOURNAL / "instantanes" / f"dcx-{dt.datetime.now():%Y%m%d-%H%M%S}-avant-set.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap.write_text(json.dumps(avant, ensure_ascii=False), encoding="utf-8")
            d.ecrire([(ch, num, brut)])
            time.sleep(0.3)
            apres = decoder_etat(d.dump())
            change = {k: (aplatir(avant).get(k), v) for k, v in aplatir(apres).items() if aplatir(avant).get(k) != v}
            journaliser(f"SET canal {ch} param {num} {nom}={valeur} brut={brut} ; changements relus : {change}")
            print("Relu après écriture :", change or "AUCUN CHANGEMENT — vérifier la connexion / le verrouillage du DCX")
            cible = f"sorties/{a.sortie}/" if a.sortie else (f"entrees/{a.entree}/" if a.entree else "setup/")
            ailleurs = {k: v for k, v in change.items() if not k.startswith(cible)}
            if ailleurs:
                journaliser(f"ALERTE propagation hors canal cible : {ailleurs}")
                print("ALERTE — d'autres canaux ont changé (lien ?) :", ailleurs)
    finally:
        d.fermer()


if __name__ == "__main__":
    main()
