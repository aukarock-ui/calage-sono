#!/usr/bin/env python3
"""
faux_venu360.py — Simulateur minimal du protocole « HiQnet Console » pour tester les outils SANS matériel.
Il imite le comportement décrit par la capture du VENU360 (bannière, login, get/set/ls/sub, JSON-RPC Load/Get).
L'arbre de paramètres est FICTIF : il ne reflète pas les vrais chemins du VENU360 (à relever avec `arbre`).

  python tools/faux_venu360.py            # écoute sur 127.0.0.1:19272
  python tools/dbx_hiqnet.py info 127.0.0.1
"""
import json
import shlex
import socketserver
import threading

def _arbre():
    """Structure calquée sur l'appli VENU360 v2.0.7 (config « Mono 5-Way »). VALEURS fictives."""
    a = {
        "\\\\Node\\AT": {"Class_Name": "dbxDriveRackVENU360", "Instance_Name": "FAUX", "Software_Version": "0.0-sim"},
        "\\\\Preset": {},
    }
    xo = {}
    hp = {1: "10kHz", 2: "1.6kHz", 3: "400Hz", 4: "90Hz", 5: "Out"}
    lp = {1: "Out", 2: "18kHz", 3: "1.6kHz", 4: "400Hz", 5: "90Hz"}
    for b in range(1, 6):
        xo.update({f"Band_{b}_HPFrequency": hp[b], f"Band_{b}_HPType": "LR 24", f"Band_{b}_LPFrequency": lp[b],
                   f"Band_{b}_LPType": "LR 24", f"Band_{b}_Gain": "0.0dB", f"Band_{b}_Polarity": "Normal", f"Band_{b}_Phase": "0 deg"})
    a["\\\\Preset\\CrossoverIIR1\\SV"] = xo
    for k in range(1, 7):
        peq = {"ParametricEQ": "On"}
        for b in range(1, 9):
            peq.update({f"Band_{b}_Type": "Bell", f"Band_{b}_Frequency": "1kHz", f"Band_{b}_Gain": "0.0dB", f"Band_{b}_Q": "1.0", f"Band_{b}_Slope": "3.0"})
        a[f"\\\\Preset\\PEQOut{k}\\SV"] = peq
        a[f"\\\\Preset\\Limiter{k}\\SV"] = {"Limiter": "On", "Threshold": "-20.0dB", "OverEasy": "Off", "Auto": "Off", "Attack": "10.0ms",
                                               "Hold": "0.0ms", "Release": "45.0dB/s", "PeakStopPlus": "On", "OvershootDb": "6.0dB"}
        a[f"\\\\Preset\\DelayOut{k}\\SV"] = {"Delay": "On", "Amount": "0.00ms"}
    for inst in ("OutputGains", "ZoneGains"):
        a[f"\\\\Preset\\{inst}\\SV"] = {f"Channel_{c}_{p}": v for c in range(1, 7) for p, v in (("Gain", "0.0dB"), ("Mute", "Off"))}
    a["\\\\Preset"] = {k.split("\\")[3]: "" for k in a if k.startswith("\\\\Preset\\")}
    # Réglages d'appareil (classe Venu360Settings de l'appli Windows) — libellés supposés, à confirmer sur la machine
    a["\\\\Node\\Settings\\SV"] = {**{f"Output{c}Gain": "+12 dBu" for c in range(1, 7)},
                                   **{f"Input{c}Gain": "+14 dBu" for c in range(1, 4)},
                                   "LoadedPresetName": "SIMU", "PresetIsModified": "0", "MutesPowerup": "0",
                                   "CurrentSampleRate": "48kHz"}
    return a


ARBRE = _arbre()
VERROU = threading.Lock()


class H(socketserver.StreamRequestHandler):
    def handle(self):
        def w(s):
            self.wfile.write((s + "\n").encode())

        subs = []
        for raw in self.rfile:
            l = raw.decode().strip()
            if l == "":
                w("HiQnet Console")
                continue
            if l.startswith("{"):
                j = json.loads(l)
                if j["method"] == "Get":
                    w(json.dumps({"error": None, "id": j["id"], "result": {"1": "MA SONO", "2": "TEST"}}))
                elif j["method"] == "Store":
                    w(json.dumps({"error": None, "id": j["id"], "result": f"Store [{j['params'][0]}]: ok (simulé)"}))
                elif j["method"] == "Load":
                    w(json.dumps({"done": False, "error": None, "id": j["id"], "result": f"Load [{j['params'][0]}]: loaded from db"}))
                    w(json.dumps({"error": None, "id": j["id"], "result": "- Load [3]: loaded sv settings"}))
                continue
            p = [t[1:-1] if len(t) > 1 and t[0] == t[-1] == '"' else t for t in shlex.split(l, posix=False)]
            cmd = p[0]
            if cmd == "connect":
                w("connect logged in as administrator" if p[2] == "administrator" else "error could not connect as administrator")
            elif cmd in ("ls",):
                w(f'ls "{p[1]}"')
                for k, v in ARBRE.get(p[1].rstrip("\\"), {}).items():
                    w(f"{k} : {v}")
                w("endls")
            elif cmd in ("get", "set", "sub", "unsub"):
                base, _, feuille = p[1].rstrip("\\").rpartition("\\")
                with VERROU:
                    noeud = ARBRE.get(base)
                    if noeud is None or feuille not in noeud:
                        w(f'error "{p[1]}" not found')
                        continue
                    if cmd == "set":
                        noeud[feuille] = p[2]
                        w(f'setr "{base}\\{feuille}" "{p[2]}"')
                    elif cmd == "get":
                        w(f'get "{base}\\{feuille}" "{noeud[feuille]}"')
                    elif cmd == "sub":
                        subs.append(p[1])
                        w(f'subr "{base}\\{feuille}" "{noeud[feuille]}"')


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with S(("127.0.0.1", 19272), H) as srv:
        print("Faux VENU360 sur 127.0.0.1:19272")
        srv.serve_forever()
