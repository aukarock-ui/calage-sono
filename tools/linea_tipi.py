#!/usr/bin/env python3
"""
linea_tipi.py — Client TIPI (protocole tiers PUBLIÉ par Linea Research) pour ASC-48 et amplis M/C/XiB.

Protocole (doc « Tipi Protocol V1.03/V1.04 », linea-research.co.uk) : texte ASCII, TCP port 51456
(ou RS-232/485 38400 8N1). Trame « $COMMANDE Méthode Valeur\\r ». SET / GET / réponse NOTIFY / NOP (garder la
connexion : délai d'inactivité 120 s). Erreurs : « ERROR … BadCommand 06 », « UnsupportedMethod 09 ».
Valeurs en unités naturelles, suffixes possibles (Hz, dB, ms, …), pas de « k » (15000Hz, pas 15kHz) ; booléens yes/no.

LIMITE IMPORTANTE : la liste officielle des méthodes TIPI n'est publiée que pour les amplis M & C (mutes, gains
d'entrée, snapshot, mesures…). Pour l'ASC-48, les méthodes réellement disponibles sont à DÉCOUVRIR en lecture
(`sonder`) : une méthode absente répond UnsupportedMethod sans rien modifier. Le réglage complet (crossover,
limiteurs VX/Tmax/Xmax, FIR) passe par System Engineer (protocole ObCom, non publié) — feuille de saisie sinon.
Certaines méthodes peuvent être verrouillées par le constructeur ou le propriétaire (presets NCA…) : on ne force pas.

Usage :
  python tools/linea_tipi.py get    <ip> Out1/Mute
  python tools/linea_tipi.py sonder <ip> [--sorties 8] [-o journal/instantanes/asc48-sonde.json]
  python tools/linea_tipi.py set    <ip> Out1/Mute yes [--appliquer] [--protection-validee]
  python tools/linea_tipi.py snapshot <ip> 3 [--appliquer]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
import sys
import time
from pathlib import Path

PORT = 51456
RACINE = Path(__file__).resolve().parent.parent
JOURNAL = RACINE / "journal"
PROTECTION = ("mute", "pol", "hpf", "lpf", "xover", "lim", "tmax", "xmax", "snapshot", "standby", "sleep")

# Méthodes à essayer en lecture. Publiées (amplis M & C) : InX/Mute, InX/Gain, InX/Pol, OutN/Mute, OutN/Meter,
# Snapshot, Type, Version. Les autres sont des HYPOTHÈSES de nommage à confirmer (la doc cite « Out8/Eq2Freq »).
CANDIDATES_DEVICE = ["Type", "Version", "Snapshot", "Alarms/Worst"]
CANDIDATES_IN = ["Mute", "Gain", "Pol", "Meter", "Delay"]
CANDIDATES_OUT = ["Mute", "Gain", "Pol", "Meter", "Delay", "Imp",
                  "Eq1Freq", "Eq1Gain", "Eq1Bw", "Eq2Freq", "Eq2Gain", "HpfFreq", "LpfFreq", "LimThresh"]


class Tipi:
    def __init__(self, hote: str, port: int = PORT, timeout: float = 2.0):
        self.s = socket.create_connection((hote, port), timeout=timeout)
        self.timeout, self.buf = timeout, ""

    def fermer(self):
        self.s.close()

    def _envoyer(self, t: str):
        self.s.sendall(f"${t}\r".encode("ascii"))

    def _lire(self) -> str | None:
        fin = time.time() + self.timeout
        while "\r" not in self.buf:
            if time.time() > fin:
                return None
            try:
                self.s.settimeout(max(0.05, fin - time.time()))
                b = self.s.recv(4096)
            except socket.timeout:
                return None
            if not b:
                raise ConnectionError("connexion fermée")
            self.buf += b.decode("ascii", "replace")
        ligne, self.buf = self.buf.split("\r", 1)
        return ligne.strip().lstrip("$").strip()

    def get(self, methode: str) -> tuple[str, str]:
        """-> ('ok', valeur) | ('erreur', message) | ('silence', '')"""
        self._envoyer(f"GET {methode}")
        while True:
            l = self._lire()
            if l is None:
                return "silence", ""
            mots = l.split()
            if mots and mots[0].upper().startswith("ERR"):
                return "erreur", l
            if mots and mots[0].upper().startswith("NOT") and len(mots) >= 3:
                nom = mots[1].replace("/", "").lower()
                if nom == methode.replace("/", "").lower():
                    return "ok", " ".join(mots[2:])

    def set(self, methode: str, valeur: str) -> str | None:
        self._envoyer(f"SET {methode} {valeur}")
        time.sleep(0.1)
        try:
            self.s.settimeout(0.3)
            b = self.s.recv(4096).decode("ascii", "replace")
            if "ERR" in b.upper():
                return b.strip()
        except socket.timeout:
            pass
        return None


def journaliser(t):
    JOURNAL.mkdir(exist_ok=True)
    with (JOURNAL / "ecritures.log").open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} TIPI {t}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["get", "sonder", "set", "snapshot"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--entrees", default="A,B,C,D")
    ap.add_argument("--sorties", type=int, default=8)
    ap.add_argument("-o", "--fichier")
    ap.add_argument("--appliquer", action="store_true")
    ap.add_argument("--protection-validee", action="store_true")
    argv = [("\x00" + t) if t[:1] == "-" and t[1:2].isdigit() else t for t in sys.argv[1:]]
    a = ap.parse_intermixed_args(argv)
    a.args = [t[1:] if t.startswith("\x00") else t for t in a.args]

    t = Tipi(a.args[0], a.port)
    try:
        if a.cmd == "get":
            print(*t.get(a.args[1]))
        elif a.cmd == "sonder":
            res = {"_meta": {"ip": a.args[0], "date": dt.datetime.now().isoformat(timespec="seconds")}}
            noms = CANDIDATES_DEVICE + [f"In{e}/{m}" for e in a.entrees.split(",") for m in CANDIDATES_IN] \
                + [f"Out{n}/{m}" for n in range(1, a.sorties + 1) for m in CANDIDATES_OUT]
            for n in noms:
                etat, v = t.get(n)
                res[n] = {"etat": etat, "valeur": v}
            out = Path(a.fichier or JOURNAL / "instantanes" / f"tipi-sonde-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            ok = [k for k, v in res.items() if k != "_meta" and v["etat"] == "ok"]
            print(f"{len(ok)} méthodes répondent sur {len(noms)} essayées → {out}")
            for k in ok:
                print(f"  {k} = {res[k]['valeur']}")
        elif a.cmd in ("set", "snapshot"):
            methode, valeur = ("Snapshot", a.args[1]) if a.cmd == "snapshot" else (a.args[1], a.args[2])
            etat, avant = t.get(methode)
            print(f"{methode} : actuel {avant if etat == 'ok' else etat} → demandé {valeur}")
            if etat == "erreur":
                sys.exit("Méthode non supportée (ou verrouillée) : rien n'est envoyé.")
            if any(m in methode.lower() for m in PROTECTION) and not a.protection_validee:
                sys.exit("REFUS : paramètre de PROTECTION / rappel de snapshot. Validation explicite de Louis requise (--protection-validee).")
            if not a.appliquer:
                print("SIMULATION — rien n'a été écrit (ajouter --appliquer).")
                return
            err = t.set(methode, valeur)
            apres = t.get(methode)[1] if methode != "Snapshot" else "(rappel)"
            journaliser(f"SET {a.args[0]} {methode} {avant} -> {valeur} (relu {apres}) {err or ''}")
            print(f"  relu : {apres}" + (f"  ERREUR : {err}" if err else ""))
    finally:
        t.fermer()


if __name__ == "__main__":
    main()
