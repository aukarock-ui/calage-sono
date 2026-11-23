#!/usr/bin/env python3
"""
rew_client.py — Piloter REW (Room EQ Wizard) par son API REST, sans serveur MCP ni Node.

Deuxième moteur de mesure du kit, à côté d'Open Sound Meter (osm_client.py) :
  - OSM  = double FFT temps réel sur programme ou bruit (mesure en continu, pendant les balances) ;
  - REW  = sinus glissant (log sweep) : meilleur rapport signal/bruit, IR propre, distorsion, impédance,
           outil d'alignement (Align IRs / Align phase), moyennes, arithmétique, EQ automatique.

API : REW lancé avec l'option -api (ou Préférences > API > Start server) -> http://localhost:4735
Doc locale : docs/rew_api_5.40b121.json (Swagger 2.0, API 0.9.4, relevée sur REW 5.40 beta 121 le 23/11/2026).
Format des données : base64 de flottants 32 bits BIG-ENDIAN. FR log-espacée : f_k = startFreq · 2^(k/ppo).

  python tools/rew_client.py info
  python tools/rew_client.py liste
  python tools/rew_client.py lire 3 --ppo 24 --lissage 1/6 -o mesures/sub_pos1.json
  python tools/rew_client.py aligner 1 2 --fc 90          # IR d'abord, puis raffinement en phase à Fc
  python tools/rew_client.py importer mesures/ir_kick.wav --nom KICK_pos1
  python tools/rew_client.py mesurer --nom SUB_pos1 --niveau -20 --appliquer   # JOUE UN SWEEP : voir garde-fous
  python tools/rew_client.py autotest                     # aucun son : IR synthétiques importées puis supprimées

Garde-fous (CLAUDE.md) : lecture seule par défaut. `mesurer` envoie un signal dans la sono : refusé sans --appliquer,
et --appliquer n'est passé qu'après confirmation explicite de Louis (niveau sûr, personne exposé). Niveau du sweep
plafonné à -10 dBFS par ce script. La protection REW (arrêt sur écrêtage) est forcée à ON avant chaque mesure.

Piège d'alignement (vérifié par l'autotest) : B retardée de 1,5 ms ET inversée -> « Align phase » seul à 300 Hz
répond +0,13 ms non inversé (un cycle d'écart, ambiguïté de phase) ; « Align IRs » répond -1,5 ms inversé (juste).
D'où la commande `aligner` : IR d'abord (grossier, sans ambiguïté), phase ensuite, et alerte si les deux divergent.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

HOTE = "http://localhost:4735"
NIVEAU_MAX_DBFS = -10.0


class ErreurREW(RuntimeError):
    pass


class REW:
    def __init__(self, hote: str = HOTE, timeout: float = 30.0):
        self.hote, self.timeout = hote.rstrip("/"), timeout

    # ---------- transport ----------
    def req(self, methode: str, chemin: str, corps=None):
        data = None if corps is None else json.dumps(corps).encode()
        r = urllib.request.Request(self.hote + chemin, method=methode, data=data,
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=self.timeout) as f:
                t = f.read().decode("utf-8", "replace")
                return json.loads(t) if t.strip() else None
        except urllib.error.HTTPError as e:
            raise ErreurREW(f"{methode} {chemin} -> HTTP {e.code} : {e.read().decode('utf-8', 'replace')[:300]}")
        except urllib.error.URLError as e:
            raise ErreurREW(f"REW injoignable sur {self.hote} ({e.reason}). Lancer REW avec -api "
                            "(ou Préférences > API > Start server).")

    def get(self, chemin):
        return self.req("GET", chemin)

    def post(self, chemin, corps):
        return self.req("POST", chemin, corps)

    def attendre_process(self, url_resultat: str, nom_attendu: str | None = None, delai: float = 30.0) -> dict:
        fin = time.time() + delai
        while time.time() < fin:
            r = self.get(url_resultat) or {}
            if r.get("message") == "Completed" and (nom_attendu is None or nom_attendu in r.get("processName", "")):
                return r
            if "rror" in (r.get("message") or ""):
                raise ErreurREW(f"{url_resultat} : {r}")
            time.sleep(0.3)
        raise ErreurREW(f"Délai dépassé en attendant {url_resultat}")

    # ---------- lecture ----------
    def info(self) -> dict:
        return {"version": (self.get("/version") or {}).get("message"),
                "pilote_audio": (self.get("/audio/driver") or {}).get("driver"),
                "audio": self.get("/audio/status"),
                "generateur": self.get("/generator/status"),
                "protection_mesure": self.get("/measure/protection-options"),
                "nb_mesures": len(self.get("/measurements") or {})}

    def liste(self) -> dict:
        return self.get("/measurements") or {}

    @staticmethod
    def _f32(b64: str) -> np.ndarray:
        return np.frombuffer(base64.b64decode(b64), dtype=">f4").astype(float)

    def reponse_frequence(self, ident, ppo: int = 24, lissage: str = "1/6", unite: str = "SPL") -> dict:
        q = f"?ppo={ppo}&smoothing={urllib.request.quote(lissage)}&unit={unite}"
        fr = self.get(f"/measurements/{ident}/frequency-response{q}")
        mag = self._f32(fr["magnitude"])
        ph = self._f32(fr["phase"]) if fr.get("phase") else None
        if fr.get("ppo"):
            f = fr["startFreq"] * 2.0 ** (np.arange(len(mag)) / fr["ppo"])
        else:
            f = fr["startFreq"] + fr["freqStep"] * np.arange(len(mag))
        return {"unite": fr.get("unit"), "lissage": fr.get("smoothing"), "f_hz": f, "mag_db": mag, "phase_deg": ph}

    def reponse_impulsionnelle(self, ident) -> dict:
        ir = self.get(f"/measurements/{ident}/impulse-response?normalised=false")
        d = self._f32(ir["data"])
        return {"fs": ir["sampleRate"], "t0_s": ir["startTime"], "ref_temps": ir.get("timingReference"),
                "delai_s": ir.get("delay"), "ir": d}

    # ---------- import (aucun son) ----------
    def importer_ir(self, ir: np.ndarray, fs: float, nom: str, t0: float = 0.0) -> str:
        avant = set(self.liste())
        self.post("/import/impulse-response-data", {
            "identifier": nom, "startTime": t0, "sampleRate": fs, "splOffset": 0.0, "applyCal": False,
            "data": base64.b64encode(np.asarray(ir, ">f4").tobytes()).decode()})
        fin = time.time() + 20
        while time.time() < fin:
            nouveaux = set(self.liste()) - avant
            if nouveaux:
                return sorted(nouveaux, key=int)[-1]
            time.sleep(0.3)
        raise ErreurREW("Import non terminé")

    def supprimer(self, ident):
        self.req("DELETE", f"/measurements/{ident}")

    # ---------- alignement (calcul seul, aucun son) ----------
    def _aligner(self, a, b, mode: str, commande: str, fc: float, plage_ms: float) -> dict:
        self.post("/alignment-tool/command", {"command": "Reset all"})
        self.post("/alignment-tool/index-a", int(a))
        self.post("/alignment-tool/index-b", int(b))
        self.post("/alignment-tool/mode", mode)
        self.post("/alignment-tool/remove-time-delay", False)
        self.post("/alignment-tool/max-negative-delay", -abs(plage_ms))
        self.post("/alignment-tool/max-positive-delay", abs(plage_ms))
        self.post("/alignment-tool/command", {"command": commande, "frequency": float(fc)})
        r = self.attendre_process("/alignment-tool/result", commande)
        v = r["results"]["0"]
        return {"mode": mode, "delai_b_ms": float(v["Delay B ms"]), "inverser_b": str(v["Invert B"]).strip().lower() == "true",
                "fc_hz": float(v["Alignment frequency"])}

    def aligner(self, a, b, fc: float, plage_ms: float = 10.0) -> dict:
        ir = self._aligner(a, b, "Impulse", "Align IRs", fc, plage_ms)
        ph = self._aligner(a, b, "Phase", "Align phase", fc, plage_ms)
        periode = 1000.0 / fc
        ecart = ph["delai_b_ms"] - ir["delai_b_ms"]
        alertes = []
        if ph["inverser_b"] != ir["inverser_b"] or abs(ecart) > 0.25 * periode:
            alertes.append(f"IR et phase divergent ({ir['delai_b_ms']:+.2f} ms {'inv.' if ir['inverser_b'] else 'norm.'} "
                           f"vs {ph['delai_b_ms']:+.2f} ms {'inv.' if ph['inverser_b'] else 'norm.'}) : ambiguïté "
                           f"de cycle (période {periode:.2f} ms à {fc:g} Hz). Retenir l'IR, vérifier la somme mesurée.")
        retenu = ir if alertes else ph
        return {"impulsion": ir, "phase": ph, "retenu": retenu, "alertes": alertes,
                "note": "Délai/inversion à appliquer à B (signe REW : négatif = avancer B). Pas max ±0,5 ms par écriture."}

    # ---------- mesure (JOUE UN SIGNAL) ----------
    def mesurer(self, nom: str, niveau_dbfs: float, longueur: str | None = None) -> str:
        if niveau_dbfs > NIVEAU_MAX_DBFS:
            raise ErreurREW(f"Niveau {niveau_dbfs} dBFS refusé (plafond du kit {NIVEAU_MAX_DBFS} dBFS).")
        prot = self.get("/measure/protection-options") or {}
        prot["clippingAbort"] = True
        self.req("PUT", "/measure/protection-options", prot)
        self.post("/measure/level", {"value": niveau_dbfs, "unit": "dBFS"})
        self.req("PUT", "/measure/naming", {"title": nom})
        avant = set(self.liste())
        self.post("/measure/command", {"command": "SPL"})
        fin = time.time() + 120
        while time.time() < fin:
            nouveaux = set(self.liste()) - avant
            if nouveaux:
                return sorted(nouveaux, key=int)[-1]
            time.sleep(0.5)
        raise ErreurREW("Mesure non terminée (vérifier l'interface audio et /application/errors).")


# ---------- autotest (aucun son) ----------
def autotest(rew: REW) -> int:
    from scipy.signal import butter, lfilter
    fs, n = 48000, 16384
    x = np.zeros(n); x[2000] = 1.0
    b, a = butter(2, [60 / (fs / 2), 2000 / (fs / 2)], "band")
    ira = lfilter(b, a, x)
    irb = -np.roll(ira, 72)                     # B : +1,5 ms et inversée
    ia = rew.importer_ir(ira, fs, "AUTOTEST_A")
    ib = rew.importer_ir(irb, fs, "AUTOTEST_B")
    try:
        r = rew.aligner(ia, ib, fc=300)
        fr = rew.reponse_frequence(ia, ppo=12, lissage="1/6")
        ok = abs(r["impulsion"]["delai_b_ms"] + 1.5) < 0.05 and r["impulsion"]["inverser_b"]
        ok = ok and r["retenu"] is r["impulsion"] and bool(r["alertes"])
        k = int(np.argmin(abs(fr["f_hz"] - 350)))
        print(json.dumps({"attendu": "B -1,5 ms inversée", "impulsion": r["impulsion"], "phase": r["phase"],
                          "alertes": r["alertes"], "fr_350Hz_db": round(float(fr["mag_db"][k]), 2)},
                         ensure_ascii=False, indent=2))
        print("AUTOTEST", "OK" if ok else "ÉCHEC")
        return 0 if ok else 1
    finally:
        for i in sorted((ia, ib), key=int, reverse=True):
            rew.supprimer(i)


def _serialiser(d):
    if isinstance(d, dict):
        return {k: _serialiser(v) for k, v in d.items()}
    if isinstance(d, np.ndarray):
        return [round(float(x), 4) for x in d]
    return d


def main():
    if hasattr(sys.stdout, "reconfigure"):  # console Windows cp1252 : Ω, ≈, −, ≤ sinon UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("commande", choices=["info", "liste", "lire", "ir", "aligner", "importer", "mesurer", "autotest"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--hote", default=HOTE)
    ap.add_argument("--ppo", type=int, default=24)
    ap.add_argument("--lissage", default="1/6")
    ap.add_argument("--fc", type=float, help="fréquence d'alignement (Hz), en général la fréquence de coupure")
    ap.add_argument("--plage", type=float, default=10.0, help="recherche de délai ± ms (aligner)")
    ap.add_argument("--nom")
    ap.add_argument("--niveau", type=float, default=-30.0, help="niveau du sweep en dBFS (mesurer)")
    ap.add_argument("--appliquer", action="store_true", help="autorise l'envoi d'un signal (mesurer)")
    ap.add_argument("-o", "--sortie")
    a = ap.parse_args()
    rew = REW(a.hote)
    try:
        if a.commande == "info":
            res = rew.info()
        elif a.commande == "liste":
            res = {k: {"titre": v.get("title"), "date": v.get("date"), "fs": v.get("sampleRate"),
                       "pic_IR_ms": round(1000 * v.get("timeOfIRPeakSeconds", 0), 3)} for k, v in rew.liste().items()}
        elif a.commande == "lire":
            res = rew.reponse_frequence(a.args[0], a.ppo, a.lissage)
        elif a.commande == "ir":
            res = rew.reponse_impulsionnelle(a.args[0])
        elif a.commande == "aligner":
            if len(a.args) != 2 or not a.fc:
                ap.error("aligner <A> <B> --fc <Hz>")
            res = rew.aligner(a.args[0], a.args[1], a.fc, a.plage)
        elif a.commande == "importer":
            from scipy.io import wavfile
            fs, d = wavfile.read(a.args[0])
            d = d.astype(float); d = d[:, 0] if d.ndim > 1 else d
            if np.issubdtype(d.dtype, np.integer) or np.max(abs(d)) > 1.5:
                d = d / np.max(abs(d))
            res = {"id": rew.importer_ir(d, fs, a.nom or Path(a.args[0]).stem)}
        elif a.commande == "mesurer":
            if not a.appliquer:
                print("REFUS : `mesurer` joue un sweep dans la sono. Relancer avec --appliquer UNIQUEMENT après "
                      "confirmation explicite de Louis (niveau sûr, personne exposé, ampli au gain prévu).")
                return 2
            res = {"id": rew.mesurer(a.nom or "mesure", a.niveau)}
        else:
            return autotest(rew)
    except ErreurREW as e:
        print("ERREUR :", e, file=sys.stderr)
        return 1
    txt = json.dumps(_serialiser(res), ensure_ascii=False, indent=2)
    if a.sortie:
        Path(a.sortie).parent.mkdir(parents=True, exist_ok=True)
        Path(a.sortie).write_text(txt, encoding="utf-8")
        print("Écrit :", a.sortie)
    else:
        print(txt if len(txt) < 20000 else txt[:20000] + "\n… (tronqué, utiliser -o)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
