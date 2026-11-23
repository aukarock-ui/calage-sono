#!/usr/bin/env python3
"""
mesure_tf.py — Mesure de fonction de transfert double canal (sweep log, méthode Farina / Müller-Massarani).
Alternative scriptable à REW/Smaart : Claude peut lancer une mesure, lire le résultat chiffré et l'analyser.

Branchement (interface audio ≥ 2 entrées) :
  sortie 1 de l'interface -> entrée du processeur (ou console)
  sortie 1 -> aussi en boucle sur l'entrée REF (câble de référence) : la latence de l'interface s'annule,
     le délai mesuré = processeur + amplis + air (on compare toujours les voies entre elles)
  micro de mesure (calibré, fantôme) -> entrée MIC

Usage :
  python tools/mesure_tf.py lister                                  # périphériques audio
  python tools/mesure_tf.py mesurer --dev 3 --ref 1 --mic 2 --niveau -20 -o mesures/sub_pos1.npz
  python tools/mesure_tf.py analyser mesures/sub_pos1.npz [--bande 30 120]
  python tools/mesure_tf.py comparer mesures/sub.npz mesures/kick.npz --fc 55   # phase / sommation à Fc
  python tools/mesure_tf.py autotest                                 # vérifie les maths sur un système simulé

SÉCURITÉ : niveau de départ -20 dBFS max, monter par pas de 3 dB. Jamais de sweep pleine échelle sur le HF :
 commencer chaque voie seule, limiteurs actifs. Protection auditive.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
from scipy import signal

FS = 48000


def sweep_log(f1=20.0, f2=20000.0, duree=3.0, fs=FS):
    t = np.arange(int(duree * fs)) / fs
    k = duree / np.log(f2 / f1)
    x = np.sin(2 * np.pi * f1 * k * (np.exp(t / k) - 1))
    fade = int(0.01 * fs)
    x[:fade] *= np.hanning(2 * fade)[:fade]
    x[-fade:] *= np.hanning(2 * fade)[fade:]
    return x


def fonction_transfert(ref: np.ndarray, mic: np.ndarray, fs=FS, eps=1e-6):
    """H = MIC/REF (double canal : indépendant du signal source). Renvoie f, H, IR."""
    n = 1 << int(np.ceil(np.log2(len(ref) + len(mic))))
    R, M = np.fft.rfft(ref, n), np.fft.rfft(mic, n)
    H = M * np.conj(R) / (np.abs(R) ** 2 + eps * np.max(np.abs(R) ** 2))
    ir = np.fft.irfft(H, n)
    f = np.fft.rfftfreq(n, 1 / fs)
    return f, H, ir


def delai_ir(ir: np.ndarray, fs=FS) -> tuple[float, int]:
    """Délai (ms) au pic de |IR| avec interpolation parabolique ; polarité = signe du pic."""
    i = int(np.argmax(np.abs(ir[: len(ir) // 2])))
    if 0 < i < len(ir) - 1:
        a, b, c = np.abs(ir[i - 1]), np.abs(ir[i]), np.abs(ir[i + 1])
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    else:
        d = 0.0
    return (i + d) / fs * 1000, int(np.sign(ir[i]))


def lissage_oct(f, mag_db, frac=6):
    out = np.empty_like(mag_db)
    for i, fi in enumerate(f):
        if fi <= 0:
            out[i] = mag_db[i]
            continue
        m = (f >= fi * 2 ** (-1 / (2 * frac))) & (f <= fi * 2 ** (1 / (2 * frac)))
        out[i] = 10 * np.log10(np.mean(10 ** (mag_db[m] / 10)))
    return out


def resume(f, H, ir, fs=FS, bande=(20, 20000)):
    dly, pol = delai_ir(ir, fs)
    # phase avec le délai de propagation retiré
    Hc = H * np.exp(2j * np.pi * f * dly / 1000)
    mag = 20 * np.log10(np.abs(H) + 1e-12)
    centres = [f0 for f0 in 1000 * 2 ** (np.arange(-17, 15) / 3) if bande[0] <= f0 <= bande[1]]
    tiers = []
    for fc in centres:
        m = (f >= fc / 2 ** (1 / 6)) & (f < fc * 2 ** (1 / 6))
        if m.any():
            tiers.append({"f_hz": round(float(fc), 1), "mag_db": round(float(10 * np.log10(np.mean(np.abs(H[m]) ** 2))), 2),
                          "phase_deg": round(float(np.degrees(np.angle(np.mean(Hc[m])))), 1)})
    return {"delai_ms": round(dly, 3), "polarite": "+" if pol > 0 else "-", "tiers_octave": tiers}


def phase_a(f, H, dly_ms, fc):
    i = int(np.argmin(np.abs(f - fc)))
    return float(np.degrees(np.angle(H[i] * np.exp(2j * np.pi * f[i] * dly_ms / 1000))))


def cmd_comparer(a_path, b_path, fc):
    A, B = np.load(a_path), np.load(b_path)
    fa, Ha, ira = fonction_transfert(A["ref"], A["mic"], int(A["fs"]))
    fb, Hb, irb = fonction_transfert(B["ref"], B["mic"], int(B["fs"]))
    i = int(np.argmin(np.abs(fa - fc)))
    da, _ = delai_ir(ira); db, _ = delai_ir(irb)
    ph = np.degrees(np.angle(Ha[i] / Hb[i]))
    somme = 20 * np.log10(np.abs(Ha[i] + Hb[i]) / max(np.abs(Ha[i]), np.abs(Hb[i])))
    print(json.dumps({"fc_hz": fc, "ecart_niveau_db": round(float(20 * np.log10(np.abs(Ha[i]) / np.abs(Hb[i]))), 2),
                      "ecart_phase_deg": round(float(ph), 1), "gain_sommation_db": round(float(somme), 2),
                      "delai_pic_A_ms": round(da, 3), "delai_pic_B_ms": round(db, 3),
                      "lecture": "objectif : écart de phase proche de 0° à Fc et sommation ≈ +6 dB (niveaux égaux)."},
                     ensure_ascii=False, indent=2))


def autotest():
    """Système simulé : retard 7,25 ms + passe-bas LR24 80 Hz + polarité inversée. Doit être retrouvé."""
    x = sweep_log(duree=2.0)
    sos = signal.butter(2, 80, "low", fs=FS, output="sos")
    y = -signal.sosfilt(sos, signal.sosfilt(sos, x))      # LR24 = 2 × BW12 en cascade
    d = int(round(7.25e-3 * FS))
    y = np.concatenate([np.zeros(d), y])[: len(x) + FS // 2]
    xr = np.concatenate([x, np.zeros(len(y) - len(x))])
    f, H, ir = fonction_transfert(xr, y)
    r = resume(f, H, ir, bande=(20, 400))
    i80 = min(r["tiers_octave"], key=lambda t: abs(t["f_hz"] - 80))
    print(json.dumps({"delai_ms_attendu": 7.25, "polarite_attendue": "-", **{k: r[k] for k in ("delai_ms", "polarite")},
                      "mag_80Hz_attendue_db": -6.02, "mag_80Hz_mesuree_db": i80["mag_db"]}, ensure_ascii=False, indent=2))
    yb = np.concatenate([np.zeros(d), x])[: len(xr)]
    _, _, ir2 = fonction_transfert(xr, yb)
    print("Délai pur large bande attendu 7,25 ms -> mesuré", round(delai_ir(ir2)[0], 3), "ms")
    print("NB : le pic d'IR d'un passe-bas arrive APRÈS le délai pur (retard de groupe) — c'est normal ; pour l'alignement, comparer les phases à Fc (commande comparer).")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["lister", "mesurer", "analyser", "comparer", "autotest"])
    ap.add_argument("fichiers", nargs="*")
    ap.add_argument("--dev", type=int)
    ap.add_argument("--ref", type=int, default=1)
    ap.add_argument("--mic", type=int, default=2)
    ap.add_argument("--sortie-canal", type=int, default=1)
    ap.add_argument("--niveau", type=float, default=-20.0, help="dBFS crête du sweep")
    ap.add_argument("--duree", type=float, default=3.0)
    ap.add_argument("--fs", type=int, default=FS)
    ap.add_argument("--bande", type=float, nargs=2, default=[20, 20000])
    ap.add_argument("--fc", type=float)
    ap.add_argument("-o", "--sortie")
    a = ap.parse_args()

    if a.cmd == "autotest":
        return autotest()
    if a.cmd == "comparer":
        return cmd_comparer(a.fichiers[0], a.fichiers[1], a.fc)
    if a.cmd == "analyser":
        D = np.load(a.fichiers[0])
        f, H, ir = fonction_transfert(D["ref"], D["mic"], int(D["fs"]))
        print(json.dumps(resume(f, H, ir, int(D["fs"]), tuple(a.bande)), ensure_ascii=False, indent=1))
        return
    try:
        import sounddevice as sd
    except Exception as e:  # pragma: no cover
        sys.exit(f"sounddevice indisponible ({e}). pip install sounddevice")
    if a.cmd == "lister":
        print(sd.query_devices())
        return
    if a.niveau > -6:
        sys.exit("Niveau refusé (> -6 dBFS). Monter progressivement depuis -20 dBFS.")
    x = sweep_log(duree=a.duree, fs=a.fs) * 10 ** (a.niveau / 20)
    x = np.concatenate([x, np.zeros(a.fs)])            # 1 s de queue pour la réverbération / le délai
    nout = a.sortie_canal
    play = np.zeros((len(x), nout)); play[:, nout - 1] = x
    rec = sd.playrec(play, samplerate=a.fs, device=a.dev, input_mapping=[a.ref, a.mic], output_mapping=list(range(1, nout + 1)))
    sd.wait()
    ref, mic = rec[:, 0], rec[:, 1]
    if np.max(np.abs(mic)) > 0.89 or np.max(np.abs(ref)) > 0.89:
        print("ATTENTION : écrêtage probable à l'entrée de l'interface — baisser le gain d'entrée.", file=sys.stderr)
    out = a.sortie or "mesure.npz"
    np.savez_compressed(out, ref=ref, mic=mic, fs=a.fs, niveau=a.niveau)
    f, H, ir = fonction_transfert(ref, mic, a.fs)
    print(f"Enregistré : {out}")
    print(json.dumps(resume(f, H, ir, a.fs, tuple(a.bande)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
