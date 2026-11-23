#!/usr/bin/env python3
"""
tracks_dsp.py — Client réseau pour t.racks DSP 206 (et 204 / 408, même famille), lecture complète, écriture sécurisée.

Implémentation propre au kit, écrite d'après la synthèse de protocole publiée par tom-da-costa/tracks-dsp-controller
(docs/PROTOCOL.md, AGPL-3.0, qui croise les captures Wireshark d'un vrai DSP 206 de FlexiFlizz/t.racks-DSP-control,
GPL-3.0). Aucun code de ces projets n'est copié ici ; seuls les faits de protocole sont repris.

Transport : TCP port 9761, le DSP est serveur, UN SEUL client à la fois (fermer l'éditeur officiel t.racks / l'app).
Trame : 10 02 | SRC | DST | LEN | PAYLOAD | 10 03 | CHK  avec CHK = 1 ^ SRC ^ DST ^ LEN ^ octets du payload.
Adresse = « ID number » de la façade (DSP 206 capturé : 02). Une requête à la fois, ≥ 40 ms entre trames.

Codages (niveau de confiance dans PROTOCOL.md : HW sur 408, captures sur 206) :
  gain     raw<80 : dB = raw/2 − 60 ; raw≥80 : dB = (raw−80)/10 − 20 (0 dB = 280, +12 dB max)
  fréq     Hz = 19.70 · (20160/19.70)^(raw/300)
  Q PEQ    Q = 0.4 · 320^(raw/100)              gain PEQ dB = (raw−120)/10 (±12 dB)
  délai    ms = raw / 96 (échantillons à 96 kHz)
  seuils   dBu = raw/2 − 90  ← LIMITEUR EN dBu, pleine échelle sortie = +20 dBu
           (manuel Thomann v4 : sortie max « +5 dBu », TH −90…+20 dB → pleine échelle à mesurer au voltmètre)
  pentes   0 bypass, 1 BW6, 2 BL6, 3 BW12, 4 BL12, 5 LR12, 6 BW18, 7 BL18, 8 BW24, 9 BL24, 10 LR24, …, 20 LR48

Recoupement avec l'éditeur officiel « the t.racks DSP 206 Processor Editor V1.05 » (C++Builder 2009), décortiqué le
23/11/2026 (docs/protocoles-autres.md § t.racks) : port 9761 codé en dur, découverte UDP 30303, IP d'usine 192.168.1.101,
fader de gain -280…+120 (= raw 0…400, 0 dB = 280), fréquence 0…300, Q 0…100, gain PEQ 0…240, 9 types de PEQ,
constantes 20160 / 320 / 0,4 présentes ; table de gain identique dans sa doc RS-232 intégrée. Seuil limiteur en dBu :
toujours NON confirmé (écran construit à l'exécution) -> voltmètre.

Usage :
  python tools/tracks_dsp.py chercher                       # découverte UDP 30303, lecture seule
  python tools/tracks_dsp.py info  <ip> [--id 2]
  python tools/tracks_dsp.py lire  <ip> [-o journal/instantanes/dsp206.json]      # config complète décodée
  python tools/tracks_dsp.py niveaux <ip> [--n 10]
  python tools/tracks_dsp.py set <ip> --sortie 1 gain -3.0         [--appliquer]
  python tools/tracks_dsp.py set <ip> --sortie 1 delai 2.35        [--appliquer]          (ms)
  python tools/tracks_dsp.py set <ip> --sortie 2 peq 3 -2.5 250 1.4 [--appliquer]         (bande, dB, Hz, Q)
  python tools/tracks_dsp.py set <ip> --sortie 1 hpf 40 LR24       [--appliquer --protection-validee]
  python tools/tracks_dsp.py set <ip> --sortie 1 lpf 100 LR24      [--appliquer --protection-validee]
  python tools/tracks_dsp.py set <ip> --sortie 1 limiteur 8.5      [--appliquer --protection-validee]   (seuil dBu)
  python tools/tracks_dsp.py set <ip> --sortie 1 polarite inverse  [--appliquer --protection-validee]
  python tools/tracks_dsp.py set <ip> --sortie 1 mute oui          [--appliquer --protection-validee]
  python tools/tracks_dsp.py autotest

Jamais : PIN (2f), générateur de test (39), stockage sur F00, envoi de trames brutes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import socket
import struct
import sys
import time
from pathlib import Path

PORT = 9761
RACINE = Path(__file__).resolve().parent.parent
JOURNAL = RACINE / "journal"
PENTES = ["bypass", "BW6", "BL6", "BW12", "BL12", "LR12", "BW18", "BL18", "BW24", "BL24", "LR24", "BW30", "BL30",
          "BW36", "BL36", "LR36", "BW42", "BL42", "BW48", "BL48", "LR48"]
TYPES_PEQ = ["Peak", "LowShelf", "HighShelf", "LP6", "LP12", "HP6", "HP12", "AP1", "AP2"]
MODELES = {"206": (2, 6, 19), "204": (2, 4, 15), "408": (4, 8, 29)}   # entrées, sorties, pages de config


# ---------------- codages ----------------
def gain_dec(r): return r / 2 - 60 if r < 80 else (r - 80) / 10 - 20
def gain_enc(db):
    if not -60 <= db <= 12: raise ValueError("gain hors plage −60…+12 dB")
    return round((db + 60) * 2) if db < -20 else round(80 + (db + 20) * 10)
def freq_dec(r): return 19.70 * (20160 / 19.70) ** (r / 300)
def freq_enc(hz): return max(0, min(300, round(300 * math.log(hz / 19.70) / math.log(20160 / 19.70))))
def q_dec(r): return 0.4 * 320 ** (r / 100)
def q_enc(q): return max(0, min(100, round(100 * math.log(q / 0.4) / math.log(320))))
def peqg_dec(r): return (r - 120) / 10
def peqg_enc(db):
    if not -12 <= db <= 12: raise ValueError("gain PEQ hors plage ±12 dB")
    return round(db * 10 + 120)
def seuil_dec(r): return r / 2 - 90
def seuil_enc(dbu):
    if not -90 <= dbu <= 20: raise ValueError("seuil hors plage −90…+20 dBu")
    return round(2 * dbu + 180)
def delai_dec(r): return r / 96
def delai_enc(ms):
    if not 0 <= ms <= 680: raise ValueError("délai hors plage 0…680 ms")
    return round(ms * 96)


def trame(src, dst, payload: bytes) -> bytes:
    chk = 1 ^ src ^ dst ^ len(payload)
    for b in payload:
        chk ^= b
    return bytes([0x10, 0x02, src, dst, len(payload)]) + payload + bytes([0x10, 0x03, chk])


def extraire(buf: bytearray):
    """Retire et renvoie le premier payload valide du tampon (cadrage par LEN, pas par recherche de 10 03)."""
    while True:
        i = buf.find(b"\x10\x02")
        if i < 0:
            buf.clear(); return None
        del buf[:i]
        if len(buf) < 5: return None
        n = buf[4]
        if len(buf) < 8 + n: return None
        f = bytes(buf[:8 + n])
        chk = 1 ^ f[2] ^ f[3] ^ n
        for b in f[5:5 + n]: chk ^= b
        if f[5 + n:7 + n] == b"\x10\x03" and f[7 + n] == chk:
            del buf[:8 + n]
            return f[5:5 + n]
        del buf[:2]


# ---------------- disposition de la config ----------------
def decoder_config(blob: bytes, n_in: int, n_out: int) -> dict:
    u16 = lambda o: struct.unpack_from("<H", blob, o)[0]
    nom = lambda o, n: blob[o:o + n].split(b"\x00")[0].decode("latin-1").strip()
    def filtres(o):
        return {"hpf_hz": round(freq_dec(u16(o)), 1), "lpf_hz": round(freq_dec(u16(o + 2)), 1),
                "hpf_pente": PENTES[blob[o + 4]] if blob[o + 4] < len(PENTES) else blob[o + 4],
                "lpf_pente": PENTES[blob[o + 5]] if blob[o + 5] < len(PENTES) else blob[o + 5]}
    def peq(o, n):
        return {str(b + 1): {"gain_db": peqg_dec(u16(o + 6 * b)), "freq_hz": round(freq_dec(u16(o + 6 * b + 2)), 1),
                             "q": round(q_dec(blob[o + 6 * b + 4]), 2),
                             "type": TYPES_PEQ[blob[o + 6 * b + 5]] if blob[o + 6 * b + 5] < 9 else blob[o + 6 * b + 5]}
                for b in range(n)}
    t = 16 + 140 * n_in + (96 + 2 * n_in) * n_out
    mute_in, mute_out = u16(t), u16(t + 2)
    etat = {"preset": nom(2, 14), "entrees": {}, "sorties": {}}
    for i in range(n_in):
        o = 16 + 140 * i
        etat["entrees"]["ABCD"[i]] = {"nom": nom(o, 8), **filtres(o + 126), "peq": peq(o + 78, 8),
            "gain_db": gain_dec(u16(o + 132)), "polarite": "inverse" if blob[o + 134] else "normale",
            "delai_ms": round(delai_dec(u16(o + 136)), 3), "mute": bool(mute_in >> i & 1)}
    so = 96 + 2 * n_in
    for k in range(n_out):
        o = 16 + 140 * n_in + so * k
        x = 10 + 2 * n_in                                    # début des filtres (408 : 18 ; 206 : 14)
        etat["sorties"][str(k + 1)] = {"nom": nom(o, 8), "routage": [("ABCD"[i]) for i in range(n_in) if u16(o + 8) >> i & 1],
            **filtres(o + x), "peq": peq(o + x + 6, 9),
            "limiteur": {"attaque_ms": u16(o + x + 70) + 1, "release_ms": u16(o + x + 72) + 1,
                         "seuil_dbu": seuil_dec(u16(o + x + 76))},
            "gain_db": gain_dec(u16(o + x + 78)), "polarite": "inverse" if blob[o + x + 80] else "normale",
            "delai_ms": round(delai_dec(u16(o + x + 82)), 3), "mute": bool(mute_out >> k & 1)}
    return etat


def offset_sortie(n_in, k):
    return 16 + 140 * n_in + (96 + 2 * n_in) * (k - 1), 10 + 2 * n_in


# ---------------- transport ----------------
class TracksDSP:
    def __init__(self, hote, port=PORT, id_app=None, timeout=1.5):
        self.s = socket.create_connection((hote, port), timeout=timeout)
        self.buf, self.timeout, self.id = bytearray(), timeout, id_app
        self.modele = None

    def fermer(self): self.s.close()

    def req(self, payload: bytes, attente=None):
        time.sleep(0.045)
        self.s.sendall(trame(0, self.id, payload))
        fin = time.time() + (attente or self.timeout)
        while time.time() < fin:
            p = extraire(self.buf)
            if p is not None:
                return p
            try:
                self.s.settimeout(max(0.05, fin - time.time()))
                d = self.s.recv(4096)
                if not d: raise ConnectionError("connexion fermée par le DSP")
                self.buf += d
            except socket.timeout:
                break
        return None

    def init(self):
        ids = [self.id] if self.id else [2, 1] + list(range(3, 251))
        for i in ids:
            self.id = i
            if self.req(b"\x10", 0.3 if len(ids) > 1 else None):
                break
        else:
            raise ConnectionError("aucun DSP ne répond (IP, port 9761, éditeur officiel encore connecté ?)")
        ident = (self.req(b"\x13") or b"\x13?")[1:].decode("ascii", "replace").strip()
        self.modele = next((m for m in MODELES if m in ident), None)
        if not self.modele:
            raise ConnectionError(f"modèle non reconnu : {ident!r}")
        verrou = self.req(b"\x2c")
        self.req(b"\x22"); actif = self.req(b"\x14")
        noms = [(self.req(bytes([0x29, i])) or b"")[2:].decode("latin-1").strip() for i in range(20)]
        return {"identite": ident, "id": self.id, "verrou_pin": bool(verrou and len(verrou) > 6 and verrou[6]),
                "preset_actif": actif[1] if actif and len(actif) > 1 else None, "presets": noms}

    def config(self) -> bytes:
        n_in, n_out, pages = MODELES[self.modele]
        blob = bytearray()
        for n in range(pages):
            p = self.req(bytes([0x27, n]))
            if not p or p[0] != 0x24 or p[1] != n:
                raise IOError(f"page de config {n} manquante")
            blob += p[2:]
        self.req(b"\x12")
        return bytes(blob)

    def niveaux(self):
        p = self.req(b"\x40")
        if not p: return None
        n_in, n_out, _ = MODELES[self.modele]
        def lvl(i):
            b0, b1, b2 = p[1 + 3 * i:4 + 3 * i]
            v = struct.unpack("<f", bytes([0, b2, b0, b1]))[0]
            return round(20 * math.log10(v) + 20, 1) if v > 1e-12 else None
        return {"entrees_dbu": [lvl(i) for i in range(n_in)], "sorties_dbu": [lvl(4 + k) for k in range(n_out)],
                "limiteur_actif": [bool(p[-1] >> k & 1) for k in range(n_out)]}


def aplatir(d, p=""):
    o = {}
    for k, v in d.items():
        q = f"{p}/{k}" if p else str(k)
        o.update(aplatir(v, q) if isinstance(v, dict) else {q: v})
    return o


def construire_ecriture(ch: int, param: str, v: list, cfg: bytes, n_in: int, k: int) -> tuple[bytes, bool]:
    """-> (payload, est_protection). ch = index protocole de la sortie k."""
    o, x = offset_sortie(n_in, k)
    u16 = lambda off: struct.unpack_from("<H", cfg, off)[0]
    P = lambda *vals: struct.pack("<" + "H" * len(vals), *vals)
    if param == "gain":
        return bytes([0x34, ch]) + P(gain_enc(float(v[0]))), False
    if param == "delai":
        return bytes([0x38, ch]) + P(delai_enc(float(v[0]))), False
    if param == "peq":
        b, g, f, q = int(v[0]), float(v[1]), float(v[2]), float(v[3])
        if not 1 <= b <= 9: raise ValueError("bande PEQ hors plage 1…9")
        if len(v) > 4 and v[4] not in TYPES_PEQ: raise ValueError(f"type PEQ inconnu ({v[4]}) : {', '.join(TYPES_PEQ)}")
        typ = TYPES_PEQ.index(v[4]) if len(v) > 4 else 0
        return bytes([0x33, ch, b - 1]) + P(peqg_enc(g), freq_enc(f)) + bytes([q_enc(q), typ, 0]), False
    if param in ("hpf", "lpf"):
        nom_pente = v[1].upper().replace("-", "").replace("BYPASS", "bypass")
        if nom_pente not in PENTES: raise ValueError(f"pente inconnue ({v[1]}) : {', '.join(PENTES)}")
        pente = PENTES.index(nom_pente)
        return bytes([0x32 if param == "hpf" else 0x31, ch]) + P(freq_enc(float(v[0]))) + bytes([pente]), True
    if param == "limiteur":
        atk, rel, r3 = u16(o + x + 70), u16(o + x + 72), u16(o + x + 74)   # attaque/release conservés
        return bytes([0x3F, ch]) + P(atk, rel, r3, seuil_enc(float(v[0]))), True
    if param == "polarite":
        return bytes([0x36, ch, 1 if v[0].lower().startswith("inv") else 0]), True
    if param == "mute":
        return bytes([0x35, ch, 1 if v[0].lower() in ("oui", "on", "1", "yes") else 0]), True
    raise KeyError(param)


def autotest():
    for db in (-60, -30, -20, -3.0, 0, 6.5, 12):
        assert abs(gain_dec(gain_enc(db)) - db) < 0.06, db
    assert gain_enc(0) == 280
    for hz in (20, 45, 100, 1000, 18000):
        assert abs(freq_dec(freq_enc(hz)) / hz - 1) < 0.012, hz
    assert seuil_enc(8.5) == 197 and seuil_dec(197) == 8.5
    assert delai_enc(4.4375) == 426
    f = trame(0, 2, b"\x10")
    assert f == bytes([0x10, 2, 0, 2, 1, 0x10, 0x10, 3, 1 ^ 0 ^ 2 ^ 1 ^ 0x10])
    b = bytearray(b"\x00\x99" + trame(2, 0, b"\x10\x19") + trame(2, 0, b"\x01"))
    assert extraire(b) == b"\x10\x19" and extraire(b) == b"\x01"
    n_in, n_out, pages = MODELES["206"]
    assert 16 + 140 * n_in + (96 + 2 * n_in) * n_out + 16 + 2 * (n_in + n_out) == 928 == 18 * 50 + 28
    print("autotest t.racks : OK (codages, trame, cadrage, taille de config 206 = 928 octets)")


PORT_DECOUVERTE = 30303   # annonce UDP du module Ethernet (Microchip « Announce ») ; vu dans l'éditeur officiel V1.05


def chercher(duree: float = 2.5) -> list[dict]:
    """Découverte en lecture seule : diffuse un paquet sur UDP 30303 et liste les réponses (IP, texte d'annonce).
    L'éditeur officiel (C++Builder, TIdUDPClient Port=30303) procède ainsi avant d'ouvrir TCP 9761.
    Format de la réponse non documenté : le brut est conservé. Aucun effet sur l'audio."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    s.bind(("", 0))
    s.sendto(b"Discovery: Who is out there?\x00\n", ("255.255.255.255", PORT_DECOUVERTE))
    vus, fin = {}, time.time() + duree
    while time.time() < fin:
        try:
            data, (ip, _) = s.recvfrom(2048)
        except socket.timeout:
            continue
        vus[ip] = {"ip": ip, "texte": data.decode("latin-1", "replace").strip(), "hex": data[:64].hex(" ")}
    s.close()
    return list(vus.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["chercher", "info", "lire", "niveaux", "set", "autotest"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--id", type=int)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--sortie", type=int)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("-o", "--fichier")
    ap.add_argument("--appliquer", action="store_true")
    ap.add_argument("--protection-validee", action="store_true")
    argv = [("\x00" + t) if t[:1] == "-" and t[1:2].isdigit() else t for t in sys.argv[1:]]
    a = ap.parse_intermixed_args(argv)
    a.args = [t[1:] if t.startswith("\x00") else t for t in a.args]
    if a.cmd == "autotest":
        return autotest()
    if a.cmd == "chercher":
        r = chercher()
        print(json.dumps(r, ensure_ascii=False, indent=1) if r else
              "Aucune réponse sur UDP 30303 (DSP éteint, autre sous-réseau, pare-feu ?). L'IP d'usine de l'éditeur est 192.168.1.101.")
        return

    d = TracksDSP(a.args[0], a.port, a.id)
    try:
        info = d.init()
        n_in, n_out, _ = MODELES[d.modele]
        if info["verrou_pin"]:
            print("ATTENTION : DSP verrouillé par PIN. Demander le code au propriétaire (on ne contourne pas le verrou).")
        if a.cmd == "info":
            print(json.dumps(info, ensure_ascii=False, indent=1)); return
        if a.cmd == "niveaux":
            for _ in range(a.n):
                print(d.niveaux()); time.sleep(0.3)
            return
        cfg = d.config()
        etat = decoder_config(cfg, n_in, n_out)
        if a.cmd == "lire":
            etat["_meta"] = {**info, "ip": a.args[0], "date": dt.datetime.now().isoformat(timespec="seconds")}
            out = Path(a.fichier or JOURNAL / "instantanes" / f"tracks{d.modele}-{dt.datetime.now():%Y%m%d-%H%M%S}.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(etat, ensure_ascii=False, indent=1), encoding="utf-8")
            (out.with_suffix(".bin")).write_bytes(cfg)
            print(f"Écrit : {out} (+ .bin brut)")
            for k, s in etat["sorties"].items():
                print(f"Out {k} {s['nom']!r} : HP {s['hpf_pente']} {s['hpf_hz']} Hz | LP {s['lpf_pente']} {s['lpf_hz']} Hz | "
                      f"gain {s['gain_db']} dB | délai {s['delai_ms']} ms | pol {s['polarite']} | "
                      f"lim {s['limiteur']['seuil_dbu']} dBu | mute {s['mute']}")
            return
        # set
        if not a.sortie or not 1 <= a.sortie <= n_out:
            sys.exit(f"--sortie 1…{n_out} obligatoire")
        param, vals = a.args[1].lower(), a.args[2:]
        ch = n_in + a.sortie - 1
        payload, prot = construire_ecriture(ch, param, vals, cfg, n_in, a.sortie)
        print(f"Out {a.sortie} {param} {' '.join(vals)}  → trame {payload.hex(' ')}")
        if prot and not a.protection_validee:
            sys.exit("REFUS : paramètre de PROTECTION. Validation explicite de Louis requise (--protection-validee).")
        if not a.appliquer:
            print("SIMULATION — rien n'a été écrit (ajouter --appliquer)."); return
        snap = JOURNAL / "instantanes" / f"tracks{d.modele}-{dt.datetime.now():%Y%m%d-%H%M%S}-avant-set.bin"
        snap.parent.mkdir(parents=True, exist_ok=True); snap.write_bytes(cfg)
        ack = d.req(payload, 3)
        apres = decoder_config(d.config(), n_in, n_out)
        A, B = aplatir(etat), aplatir(apres)
        change = {k: (A.get(k), v) for k, v in B.items() if A.get(k) != v}
        JOURNAL.mkdir(exist_ok=True)
        with (JOURNAL / "ecritures.log").open("a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} TRACKS{d.modele} {a.args[0]} Out{a.sortie} {param} "
                    f"{vals} ack={ack.hex() if ack else None} relu={change}\n")
        print("ACK :", ack.hex() if ack else "AUCUN", "| relu :", change or "AUCUN CHANGEMENT")
    finally:
        d.fermer()


if __name__ == "__main__":
    main()
