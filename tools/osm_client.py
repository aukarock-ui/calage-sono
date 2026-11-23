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
GAIN_GEN_MIN_DB = -90.0
JOURNAL = Path(__file__).resolve().parent.parent / "journal"


def journaliser(t: str) -> None:
    JOURNAL.mkdir(exist_ok=True)
    with (JOURNAL / "ecritures.log").open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} OSM {t}\n")


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
def _fini(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def nettoyer_ft(ft: list) -> list:
    """OSM envoie NaN (null en JSON) pour la cohérence là où il n'y a pas de signal (vérifié sur OSM v1.5.2) :
    cohérence absente → 0 (point jugé non fiable, poids minimal) ; fréquence/module/phase absents → point retiré."""
    out = []
    for l in ft:
        if len(l) < 5 or not all(_fini(l[j]) for j in (0, 2, 3)):
            continue
        out.append([l[0], l[1], l[2], l[3], l[4] if _fini(l[4]) else 0.0])
    return out


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
    td = [t for t in td if len(t) >= 2 and _fini(t[0]) and _fini(t[1])]
    if not td:
        return {}
    i = max(range(len(td)), key=lambda k: abs(td[k][1]))
    return {"temps_pic_ms": round(td[i][0], 3), "polarite_pic": "+" if td[i][1] >= 0 else "-"}


def valeur_a(ft: list, f: float):
    c = min(ft, key=lambda x: abs(x[0] - f))
    return c[2], c[3], c[4], c[0]


def charger(p: str) -> dict:
    """Mesure OSM enregistrée : refuse un fichier illisible ou sans ftdata [f, _, module, phase, cohérence]."""
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"Fichier de mesure illisible : {p} ({e})")
    ft = d.get("ftdata") if isinstance(d, dict) else None
    if not ft or not all(isinstance(l, (list, tuple)) and len(l) >= 5 for l in ft):
        sys.exit(f"{p} : pas de ftdata exploitable (attendu [f, _, module, phase, cohérence] par point).")
    d["ftdata"] = nettoyer_ft(ft)
    if not d["ftdata"]:
        sys.exit(f"{p} : aucun point valide (mesure vide ?).")
    return d


def grilles_identiques(mesures: list[list]) -> str | None:
    """None si toutes les mesures ont la même grille de fréquences ; sinon la raison du refus."""
    ref = [l[0] for l in mesures[0]]
    for k, m in enumerate(mesures[1:], 2):
        if len(m) != len(ref):
            return f"mesure n°{k} : {len(m)} points au lieu de {len(ref)}"
        for f0, l in zip(ref, m):
            if abs(l[0] - f0) > 1e-6 * max(1.0, abs(f0)):
                return f"mesure n°{k} : fréquence {l[0]} Hz au lieu de {f0} Hz"
    return None


def main():
    if hasattr(sys.stdout, "reconfigure"):  # console Windows cp1252 : Ω, ≈, −, ≤ sinon UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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
    besoin = {"ecouter": 0, "lire": 2, "comparer": 2, "moyenne": 2, "generateur": 1}[a.cmd]
    if len(a.args) < besoin:
        sys.exit(f"{a.cmd} : au moins {besoin} argument(s) attendu(s) (voir --help).")
    if a.cmd == "comparer" and (a.fc is None or a.fc <= 0):
        sys.exit("comparer : --fc <fréquence de coupure en Hz, > 0> obligatoire.")
    if not 0 < a.bande[0] < a.bande[1]:
        sys.exit("--bande : deux fréquences positives croissantes attendues.")
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
        r["ftdata"] = nettoyer_ft(r.get("ftdata") or [])
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
        motif = grilles_identiques(mes)
        if motif:
            sys.exit(f"REFUS : grilles de fréquences différentes ({motif}). Mesurer avec les mêmes réglages OSM "
                     "(fenêtre, résolution, moyennage) avant de moyenner.")
        n = len(mes[0])
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
        if a.on and a.off:
            sys.exit("REFUS : --on et --off en même temps.")
        if a.on and a.gain is None:
            sys.exit(f"REFUS : --on exige --gain (≤ {GAIN_GEN_MAX_DB} dBFS). Le niveau mémorisé dans OSM est inconnu : "
                     "on impose toujours un niveau sûr avant d'allumer.")
        if a.gain is not None and not (GAIN_GEN_MIN_DB <= a.gain <= GAIN_GEN_MAX_DB):     # refuse aussi nan / inf
            sys.exit(f"REFUS : gain {a.gain} dBFS hors de [{GAIN_GEN_MIN_DB} ; {GAIN_GEN_MAX_DB}] dBFS. "
                     "Monter à la main dans OSM si nécessaire.")
        hello = ecouter(a.duree).get(hote)
        if not hello or hello.get("generator") in (None, "disable"):
            sys.exit("Générateur non pilotable : dans OSM, Remote > cocher « enable for remote ».")
        # Deux messages successifs : le gain d'abord, l'allumage ensuite. Dans un seul objet JSON, l'ordre d'application
        # des clés par OSM n'est pas garanti (QJsonObject trie « enabled » avant « gain »).
        envois = ([{"gain": a.gain}] if a.gain is not None else []) + ([{"enabled": bool(a.on)}] if a.on or a.off else [])
        if not envois:
            sys.exit("Rien à envoyer : préciser --gain, --on ou --off.")
        if not a.appliquer:
            print(f"SIMULATION : générateur {envois} (ajouter --appliquer, après accord de Louis : niveau sûr, personne exposé).")
            return
        for data in envois:
            journaliser(f"GENERATEUR {hote} {data}")
            print(requete(hote, {"message": "update", "uuid": hello["generator"], "objectName": "GeneratorRemote",
                                 "name": "Claude", "data": data}))


if __name__ == "__main__":
    main()
