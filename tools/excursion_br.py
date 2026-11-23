#!/usr/bin/env python3
"""
excursion_br.py — Excursion du HP d'une caisse bass-reflex avec son passe-haut (HPF), comparée à Xmax.

Modèle : circuit équivalent à éléments localisés de Thiele et Small (Small, « Vented-Box Loudspeaker Systems »,
JAES 21, 1973), résolu en fréquence sans approximation d'alignement. On utilise :
  - le HP : Re, Le, Bl, Mms, Cms (tirée de Vas), Rms (tirée de Qms) ;
  - la caisse : compliance Cab = Vb/(ρc²), masse d'évent tirée de l'accord fb, pertes QL (7 par défaut, valeur usuelle
    de Small pour une caisse réelle) ;
  - le filtre : Butterworth (BW) ou Linkwitz-Riley (LR = deux Butterworth d'ordre moitié en cascade), analogique.
La charge de rayonnement est négligée : c'est une approximation courante en grave, légèrement pessimiste.
Le calcul se fait en sinus permanent : la tension RMS aux bornes du HP donne une excursion crête de √2 × |X|.

Règle tirée de la littérature (Marshall, « The DSP Assisted Reflex System », audioXpress, sept. 2016, p. 40) :
juste sous fb, l'excursion s'emballe ; elle ne se stabilise que vers 0,7·fb. Le HPF doit donc atténuer d'au moins
14 dB à 0,7·fb, soit une pente d'au moins 28 dB/oct entre 0,7·fb et fb. Un 18 dB/oct ne suffit pas.
C'est une heuristique d'auteur, pas une norme. Le critère retenu par le kit est physique : l'excursion crête,
calculée à la tension du limiteur, doit rester ≤ Xmax à toutes les fréquences (10 Hz … 2·fb).

LIMITES : modèle petits signaux, avec T/S nominaux, Xmax constructeur et ni compression, ni non-linéarité de Bl(x)
et Cms(x), ni vitesse d'évent. Il donne un ORDRE DE GRANDEUR à confirmer par la mesure (excursion visuelle ou laser
à faible niveau, impédance pour l'accord réel). Le contenu sous fb de la musique est en général plus faible que celui
d'un sinus pleine puissance : le critère est donc prudent.

Usage :
  python tools/excursion_br.py --hp RCF_LF21X451 --vb 385 --fb 34 --hpf "BW24 29 Hz" --v 80
  python tools/excursion_br.py autotest
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

import numpy as np
from scipy import signal

RHO, C = 1.18, 343.0      # air à ~20 °C
RACINE = Path(__file__).resolve().parent.parent
ATTENUATION_MIN_07FB_DB = 14.0      # Marshall 2016
PENTE_MIN_DB_OCT = 28.0             # Marshall 2016, entre 0,7·fb et fb


def parse_hpf(texte: str | None) -> tuple[str, int, float] | None:
    """« BW24 29 Hz », « LR 24 55Hz », « LR48 30 Hz » → (famille, ordre, fc). None si illisible."""
    if not texte:
        return None
    m = re.search(r"\b(BW|LR|BUT\w*|LINK\w*)\s*-?\s*(\d{1,2})\b.*?(\d+(?:[.,]\d+)?)\s*(k?)Hz", str(texte), re.I)
    if not m:
        return None
    fam = "LR" if m.group(1).upper().startswith(("LR", "LINK")) else "BW"
    pente = int(m.group(2))
    if pente % 6 or not 6 <= pente <= 96:
        return None
    ordre = pente // 6
    if fam == "LR" and ordre % 2:
        return None
    fc = float(m.group(3).replace(",", ".")) * (1000 if m.group(4) else 1)
    return fam, ordre, fc


def reponse_hpf(f: np.ndarray, hpf: tuple[str, int, float] | None) -> np.ndarray:
    """Réponse complexe du HPF analogique aux fréquences f (1 si pas de filtre)."""
    if hpf is None:
        return np.ones_like(f, dtype=complex)
    fam, ordre, fc = hpf
    w = 2 * np.pi * f
    if fam == "BW":
        b, a = signal.butter(ordre, 2 * np.pi * fc, "high", analog=True)
        return signal.freqs(b, a, w)[1]
    b, a = signal.butter(ordre // 2, 2 * np.pi * fc, "high", analog=True)
    return signal.freqs(b, a, w)[1] ** 2


def parametres_hp(hp: dict) -> dict:
    """Grandeurs SI du HP à partir de la bibliothèque. Lève ValueError s'il manque une donnée."""
    ts = hp.get("ts") or {}
    manque = [k for k in ("fs_hz", "qes", "qms", "vas_l", "sd_m2") if ts.get(k) is None]
    if hp.get("re_ohm") is None:
        manque.append("re_ohm")
    if hp.get("xmax_mm") is None:
        manque.append("xmax_mm")
    if manque:
        raise ValueError("T/S incomplets : " + ", ".join(manque))
    ws = 2 * math.pi * ts["fs_hz"]
    sd = ts["sd_m2"]
    cms = ts["vas_l"] / 1000 / (RHO * C ** 2 * sd ** 2)
    mms = ts["mms_g"] / 1000 if ts.get("mms_g") else 1 / (ws ** 2 * cms)
    re_ = hp["re_ohm"]
    bl = ts.get("bl_tm") or math.sqrt(ws * mms * re_ / ts["qes"])
    return {"re": re_, "le": (ts.get("le_mh") or 0) / 1000, "bl": bl, "mms": mms, "cms": cms,
            "rms": ws * mms / ts["qms"], "sd": sd, "xmax": hp["xmax_mm"] / 1000}


def excursion(f: np.ndarray, p: dict, vb_l: float, fb: float, v_rms: float, hpf=None, ql: float = 7.0) -> np.ndarray:
    """Excursion crête (m) du HP en sinus permanent, tension RMS v_rms aux bornes du HP AVANT filtre."""
    w = 2 * np.pi * f
    jw = 1j * w
    cab = vb_l / 1000 / (RHO * C ** 2)
    map_ = 1 / ((2 * np.pi * fb) ** 2 * cab)
    ral = ql / (2 * np.pi * fb * cab)          # pertes de fuite (Small : QL = ωb·Cab·Ral)
    zbox = 1 / (jw * cab + 1 / (jw * map_) + 1 / ral)
    ze = p["re"] + jw * p["le"]
    zm = jw * p["mms"] + p["rms"] + 1 / (jw * p["cms"]) + p["sd"] ** 2 * zbox
    v = v_rms * math.sqrt(2) * reponse_hpf(f, hpf)
    u = p["bl"] * v / (ze * zm + p["bl"] ** 2)  # vitesse de la membrane
    return np.abs(u / jw)


def analyse(hp: dict, vb_l: float, fb: float, hpf_txt: str | None, v_rms_hp: float, ql: float = 7.0) -> dict:
    """Bilan d'excursion et contrôle du HPF. Les alertes sont des chaînes prêtes pour le rapport."""
    p = parametres_hp(hp)
    hpf = parse_hpf(hpf_txt)
    f = np.geomspace(10, 2 * fb, 400)
    x = excursion(f, p, vb_l, fb, v_rms_hp, hpf, ql)
    i = int(np.argmax(x))
    r = {"x_max_mm": round(float(x[i]) * 1000, 2), "f_x_max_hz": round(float(f[i]), 1),
         "xmax_hp_mm": p["xmax"] * 1000, "v_rms_hp": round(v_rms_hp, 1), "alertes": []}
    if hpf_txt and hpf is None:
        r["alertes"].append(f"HPF « {hpf_txt} » : type/pente illisible (attendu p. ex. « BW24 29 Hz » ou « LR24 30 Hz ») — "
                            "excursion calculée SANS filtre.")
    att07 = -20 * math.log10(abs(reponse_hpf(np.array([0.7 * fb]), hpf)[0]))
    att1 = -20 * math.log10(abs(reponse_hpf(np.array([fb]), hpf)[0]))
    r["attenuation_07fb_db"] = round(att07, 1)
    r["pente_07fb_fb_db_oct"] = round((att07 - att1) / math.log2(1 / 0.7), 1)
    r["v_rms_hp_a_xmax"] = round(v_rms_hp * p["xmax"] / float(x[i]), 1)      # modèle linéaire : x ∝ V
    if x[i] > p["xmax"]:
        remede = ("sous l'accord : HPF plus haut et/ou plus raide" if f[i] < fb else
                  "AU-DESSUS de l'accord : le HPF n'y change presque rien, c'est le niveau qu'il faut limiter")
        r["alertes"].append(
            f"EXCURSION : {r['x_max_mm']} mm crête à {r['f_x_max_hz']} Hz > Xmax {r['xmax_hp_mm']} mm "
            f"(sinus {v_rms_hp:.1f} V RMS par HP = tension cible du limiteur, modèle T/S). Pic {remede}. "
            f"Xmax atteint vers {r['v_rms_hp_a_xmax']} V RMS par HP : ne pas viser plus haut sans mesure.")
    if att07 < ATTENUATION_MIN_07FB_DB:
        r["alertes"].append(
            f"HPF : {att07:.1f} dB d'atténuation à 0,7·fb ({0.7 * fb:.1f} Hz) < {ATTENUATION_MIN_07FB_DB:g} dB recommandés "
            f"(pente {r['pente_07fb_fb_db_oct']} dB/oct entre 0,7·fb et fb, ≥ {PENTE_MIN_DB_OCT:g} visés ; Marshall, "
            "audioXpress 2016). Heuristique : l'excursion calculée fait foi.")
    return r


def autotest() -> None:
    # Cas publié (Marshall 2016) : SB Acoustics SB15NAC30-8, caisse 12 L accordée à 40 Hz, Xmax 5 mm.
    # Publié : « à 20 Hz l'excursion atteint sa limite à 1,5 W » ; on prend P = V²/8 Ω (convention 2,83 V = 1 W).
    hp = {"re_ohm": 5.7, "xmax_mm": 5.0,
          "ts": {"fs_hz": 35.5, "qes": 0.39, "qms": 6.0, "vas_l": 17.7, "sd_m2": 0.0082, "bl_tm": 5.9}}
    p = parametres_hp(hp)
    x20 = excursion(np.array([20.0]), p, 12, 40, math.sqrt(1.5 * 8))[0] * 1000
    assert 3.5 < x20 < 6.5, x20           # modèle ≈ publication (± 30 % : QL, rayonnement, Mms déduite)
    x60 = excursion(np.array([60.0]), p, 12, 40, math.sqrt(15 * 8))[0] * 1000
    assert 3.5 < x60 < 6.5, x60
    # minimum d'excursion à l'accord
    f = np.geomspace(20, 80, 200)
    x = excursion(f, p, 12, 40, 2.83)
    assert abs(f[np.argmin(x)] - 40) < 4
    # filtres : BW24 à fc → −3 dB ; LR24 à fc → −6 dB ; une octave sous fc : BW24 ≈ 24 dB
    assert abs(-20 * math.log10(abs(reponse_hpf(np.array([30.0]), ("BW", 4, 30))[0])) - 3.01) < 0.05
    assert abs(-20 * math.log10(abs(reponse_hpf(np.array([30.0]), ("LR", 4, 30))[0])) - 6.02) < 0.05
    assert parse_hpf("BW24 29 Hz") == ("BW", 4, 29.0) and parse_hpf("LR 48 30Hz") == ("LR", 8, 30.0)
    assert parse_hpf("LR18 30 Hz") is None and parse_hpf("10 kHz (type à relever)") is None
    # un HPF raide réduit l'excursion sous l'accord
    sans = excursion(np.array([25.0]), p, 12, 40, 5)[0]
    avec = excursion(np.array([25.0]), p, 12, 40, 5, ("LR", 8, 34))[0]
    assert avec < sans / 4
    print(f"autotest excursion_br : OK (publication Marshall : 20 Hz/1,5 W → {x20:.1f} mm, 60 Hz/15 W → {x60:.1f} mm ; "
          "Xmax publié 5 mm)")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):  # console Windows cp1252 : Ω, ≈, −, ≤ sinon UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="calcul", choices=["calcul", "autotest"])
    ap.add_argument("--hp", help="clé du HP dans data/materiel.yaml")
    ap.add_argument("--vb", type=float, help="volume net de la caisse (L)")
    ap.add_argument("--fb", type=float, help="accord de l'évent (Hz)")
    ap.add_argument("--hpf", help="« BW24 29 Hz », « LR24 30 Hz »…")
    ap.add_argument("--v", type=float, help="tension RMS aux bornes du HP (V), p. ex. celle du limiteur")
    ap.add_argument("--ql", type=float, default=7.0)
    a = ap.parse_args()
    if a.cmd == "autotest":
        return autotest()
    if None in (a.hp, a.vb, a.fb, a.v):
        sys.exit("--hp, --vb, --fb et --v obligatoires")
    import yaml
    mat = yaml.safe_load((RACINE / "data" / "materiel.yaml").read_text(encoding="utf-8"))
    hp = mat["haut_parleurs"].get(a.hp) or sys.exit(f"HP {a.hp} absent de la bibliothèque")
    r = analyse(hp, a.vb, a.fb, a.hpf, a.v, a.ql)
    for k, v in r.items():
        if k != "alertes":
            print(f"{k:24} {v}")
    for al in r["alertes"]:
        print("ALERTE :", al)
    print("Modèle T/S petits signaux : ordre de grandeur, à confirmer par mesure.")


if __name__ == "__main__":
    main()
