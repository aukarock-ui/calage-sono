#!/usr/bin/env python3
"""
osm_client.py — Lire en direct les mesures d'Open Sound Meter (OSM, open source GPL-3, double FFT temps réel).

OSM fait la mesure (comme Smaart) ; ce client récupère les données chiffrées pour que Claude les analyse.
Protocole lu dans le code source d'OSM (github.com/psmokotnin/osm, src/remote/) :
  - Découverte : OSM diffuse en UDP (broadcast, port 49007) un JSON « hello » toutes les secondes environ :
    {"message":"hello","host":..,"port":49007,"sources":[{"uuid":..,"objectName":..}],"generator":<uuid|"disable">}
  - Requête : TCP <hôte>:49007, en-tête 4 octets (taille, little-endian) + JSON.
    Réponse : en-tête 4 octets + données qCompress (4 octets big-endian = taille décompressée, puis flux zlib).
  - {"message":"requestData","uuid":<source>,"name":"Claude"} -> "sourceData" :
       ftdata = [[fréquence Hz, module (spectre), magnitude TF linéaire, phase TF (rad), cohérence 0-1], ...]
       timeData = [[temps (ms), valeur], ...]  (réponse impulsionnelle)
  - {"message":"update","uuid":..,"objectName":..,"data":{propriété: valeur}} : modifier une mesure / le générateur.

PRÉREQUIS dans OSM : menu Remote -> cocher « Server » (et « enable for remote » pour piloter le générateur).
Pare-feu Windows : autoriser OSM et Python sur TCP/UDP 49007.

Usage :
  python tools/osm_client.py ecouter [--duree 5]                         # trouve les OSM et leurs mesures
  python tools/osm_client.py lire <hôte> <uuid|nom de mesure> -o mesures/sub_pos1.json [--bande 30 200]
  python tools/osm_client.py comparer A.json B.json --fc 55              # phase / niveau / sommation à la coupure
  python tools/osm_client.py moyenne A.json B.json C.json -o moy.json    # moyenne spatiale (puissance, pondérée cohérence)
  --resolution 24 (lire, moyenne) : bandes au 1/24 d'octave pour décider d'un filtre d'EQ (3 par défaut = vue d'ensemble)
  python tools/osm_client.py generateur <hôte> --gain -30 --on|--off --appliquer
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import struct
import sys
import time
import zlib
from pathlib import Path

PORT = 49007
GAIN_GEN_MAX_DB = -10.0          # garde-fou : jamais de bruit au-dessus de -10 dBFS lancé par Claude


# ---------------- transport ----------------
def ecouter(duree: float = 5.0) -> dict:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", PORT))
    s.settimeout(0.5)
    vus: dict = {}
    fin = time.time() + duree
    while time.time() < fin:
        try:
            data, (ip, _) = s.recvfrom(65535)
        except socket.timeout:
            continue
        try:
            j = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if j.get("api") == "Open Sound Meter" and j.get("message") == "hello":
            vus[ip] = j
    s.close()
    return vus


def requete(hote: str, objet: dict, timeout: float = 5.0) -> dict:
    corps = json.dumps(objet).encode("utf-8")
    with socket.create_connection((hote, PORT), timeout=timeout) as c:
        c.sendall(struct.pack("<i", len(corps)) + corps)
        tete = _lire_n(c, 4)
        n = struct.unpack("<i", tete)[0]
        brut = _lire_n(c, n)
    if len(brut) < 4:
        return {}
    data = zlib.decompress(brut[4:])           # qCompress : 4 octets BE (taille) + zlib
    return json.loads(data.decode("utf-8")) if data else {}


def _lire_n(c: socket.socket, n: int) -> bytes:
    b = b""
    while len(b) < n:
        x = c.recv(n - len(b))
        if not x:
            raise ConnectionError("connexion fermée par OSM")
        b += x
    return b


def trouver_source(hote: str, cle: str, duree: float = 4.0) -> str:
    if len(cle) >= 32 and "-" in cle:
        return cle if cle.startswith("{") else "{" + cle + "}"
    for ip, hello in ecouter(duree).items():
        if ip != hote:
            continue
        for s in hello.get("sources", []):
            if s.get("objectName", "").lower() == cle.lower():
                return s["uuid"]
    sys.exit(f"Mesure « {cle} » introuvable sur {hote} (vérifier le nom et que Remote > Server est coché).")


# ---------------- analyse ----------------
def tiers_octave(ft: list, bande=(20.0, 20000.0), coh_min=0.0, n: int = 3) -> list:
    """Bandes de 1/n d'octave (n = 3 par défaut : vue d'ensemble). Décider d'un filtre d'EQ sur une vue au 1/24
    d'octave (McCarthy 2007, ch. 8 : une EQ en 1/6 d'octave demande une analyse 4 fois plus fine)."""
    if n not in (1, 3, 6, 12, 24, 48):
        raise ValueError("résolution : 1, 3, 6, 12, 24 ou 48 bandes par octave")
    out = []
    for k in range(math.floor(n * math.log2(20 / 1000)), math.ceil(n * math.log2(20000 / 1000)) + 1):
        fc = 1000 * 2 ** (k / n)
        if not bande[0] <= fc <= bande[1]:
            continue
        lo, hi = fc / 2 ** (1 / (2 * n)), fc * 2 ** (1 / (2 * n))
        cel = [c for c in ft if lo <= c[0] < hi and c[2] > 0]
        if not cel:
            continue
        poids = [max(c[4], 1e-3) for c in cel]
        mag = 10 * math.log10(sum(w * c[2] ** 2 for w, c in zip(poids, cel)) / sum(poids))
        re = sum(w * math.cos(c[3]) for w, c in zip(poids, cel))
        im = sum(w * math.sin(c[3]) for w, c in zip(poids, cel))
        coh = sum(c[4] for c in cel) / len(cel)
        out.append({"f_hz": round(fc, 1), "mag_db": round(mag, 2), "phase_deg": round(math.degrees(math.atan2(im, re)), 1),
                    "coherence": round(coh, 2), "fiable": coh >= max(coh_min, 0.8)})
    return out


def pic_ir(td: list) -> dict:
    if not td:
        return {}
    i = max(range(len(td)), key=lambda k: abs(td[k][1]))
    return {"temps_pic_ms": round(td[i][0], 3), "polarite_pic": "+" if td[i][1] >= 0 else "-"}


def valeur_a(ft: list, f: float):
    c = min(ft, key=lambda x: abs(x[0] - f))
    return c[2], c[3], c[4], c[0]


def charger(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main():
    if hasattr(sys.stdout, "reconfigure"):  # console Windows cp1252 : Ω, ≈, −, ≤ sinon UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["ecouter", "lire", "comparer", "moyenne", "generateur"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--duree", type=float, default=5.0)
    ap.add_argument("--bande", type=float, nargs=2, default=[20, 20000])
    ap.add_argument("--fc", type=float)
    ap.add_argument("--gain", type=float)
    ap.add_argument("--on", action="store_true")
    ap.add_argument("--off", action="store_true")
    ap.add_argument("--appliquer", action="store_true")
    ap.add_argument("-o", "--sortie")
    ap.add_argument("--resolution", type=int, default=3, help="bandes par octave : 3 (vue d'ensemble), 24 (décision d'EQ)")
    a = ap.parse_args()
    cle_b = "tiers_octave" if a.resolution == 3 else f"bandes_1_{a.resolution}_octave"

    if a.cmd == "ecouter":
        vus = ecouter(a.duree)
        if not vus:
            print("Aucun OSM trouvé : OSM lancé ? Remote > Server coché ? Même réseau ? Pare-feu (49007) ?")
        for ip, h in vus.items():
            print(f"{ip}  {h.get('host')}  version {h.get('version')}  générateur: {h.get('generator')}")
            for s in h.get("sources", []):
                print(f"    {s.get('objectName'):<30} {s.get('uuid')}")
        return

    if a.cmd == "lire":
        hote, cle = a.args[0], a.args[1]
        uuid = trouver_source(hote, cle)
        r = requete(hote, {"message": "requestData", "uuid": uuid, "name": "Claude"})
        if r.get("message") != "sourceData":
            sys.exit(f"Réponse inattendue : {r}")
        r["_meta"] = {"hote": hote, "source": cle, "date": time.strftime("%Y-%m-%d %H:%M:%S")}
        out = Path(a.sortie or f"mesures/{cle}_{time.strftime('%H%M%S')}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r), encoding="utf-8")
        print(f"Enregistré : {out} ({len(r.get('ftdata', []))} points)")
        print(json.dumps({**pic_ir(r.get("timeData", [])), cle_b: tiers_octave(r["ftdata"], tuple(a.bande), n=a.resolution)},
                         ensure_ascii=False, indent=1))
        return

    if a.cmd == "comparer":
        A, B = charger(a.args[0]), charger(a.args[1])
        ma, pa, ca, f = valeur_a(A["ftdata"], a.fc)
        mb, pb, cb, _ = valeur_a(B["ftdata"], a.fc)
        dphi = math.degrees(math.atan2(math.sin(pa - pb), math.cos(pa - pb)))
        somme = abs(complex(ma * math.cos(pa), ma * math.sin(pa)) + complex(mb * math.cos(pb), mb * math.sin(pb)))
        print(json.dumps({
            "f_hz": round(f, 2), "ecart_niveau_db": round(20 * math.log10(ma / mb), 2), "ecart_phase_deg": round(dphi, 1),
            "correction_delai_ms_si_phase": round(dphi / (360 * a.fc) * 1000, 3),
            "gain_sommation_db": round(20 * math.log10(somme / max(ma, mb)), 2),
            "coherence": [round(ca, 2), round(cb, 2)],
            "lecture": "Objectif : |écart de phase| < 30° à Fc, sommation proche de +6 dB à niveaux égaux. "
                       "correction > 0 : retarder la voie A de cette valeur ; < 0 : retarder la voie B. "
                       "Vérifier ensuite par une nouvelle mesure (ambiguïté de 360° possible). Ne pas conclure si cohérence < 0,8.",
        }, ensure_ascii=False, indent=2))
        return

    if a.cmd == "moyenne":
        mes = [charger(p)["ftdata"] for p in a.args]
        n = min(len(m) for m in mes)
        moy = []
        for i in range(n):
            w = [max(m[i][4], 1e-3) for m in mes]
            p2 = sum(wi * m[i][2] ** 2 for wi, m in zip(w, mes)) / sum(w)
            re = sum(wi * math.cos(m[i][3]) for wi, m in zip(w, mes))
            im = sum(wi * math.sin(m[i][3]) for wi, m in zip(w, mes))
            moy.append([mes[0][i][0], 0.0, math.sqrt(p2), math.atan2(im, re), min(m[i][4] for m in mes)])
        out = {"message": "moyenne", "sources": a.args, "ftdata": moy}
        Path(a.sortie or "moyenne.json").write_text(json.dumps(out), encoding="utf-8")
        print("Moyenne en puissance pondérée par la cohérence : n'y mettre que des positions comparables et isolées (ONAX), "
              "jamais un bord (OFFAX) ni un point de raccord (XOVR). Regarder aussi les courbes séparées (enveloppe).", file=sys.stderr)
        print(json.dumps(tiers_octave(moy, tuple(a.bande), n=a.resolution), ensure_ascii=False, indent=1))
        return

    if a.cmd == "generateur":
        hote = a.args[0]
        hello = ecouter(a.duree).get(hote)
        if not hello or hello.get("generator") in (None, "disable"):
            sys.exit("Générateur non pilotable : dans OSM, Remote > cocher « enable for remote ».")
        data = {}
        if a.gain is not None:
            if a.gain > GAIN_GEN_MAX_DB:
                sys.exit(f"REFUS : gain {a.gain} dBFS > {GAIN_GEN_MAX_DB} dBFS. Monter à la main dans OSM si nécessaire.")
            data["gain"] = a.gain
        if a.on or a.off:
            data["enabled"] = bool(a.on)
        if not a.appliquer:
            print(f"SIMULATION : générateur {data} (ajouter --appliquer, après accord de Louis : niveau sûr, personne exposé).")
            return
        print(requete(hote, {"message": "update", "uuid": hello["generator"], "objectName": "GeneratorRemote",
                             "name": "Claude", "data": data}))


if __name__ == "__main__":
    main()
