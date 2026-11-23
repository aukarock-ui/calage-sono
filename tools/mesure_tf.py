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
  python tools/mesure_tf.py mesurer --dev 3 --ref 1 --mic 2 --niveau -20 --moyennes 3 -o mesures/sub_pos1.npz [--appliquer]
  python tools/mesure_tf.py analyser mesures/sub_pos1.npz [--bande 30 120]
  python tools/mesure_tf.py comparer mesures/sub.npz mesures/kick.npz --fc 55   # phase / sommation à Fc
  python tools/mesure_tf.py autotest                                 # vérifie les maths sur un système simulé

QUALITÉ : --moyennes N (défaut 3) répète le sweep et moyenne les spectres croisés ; la cohérence (0…1) est donnée
 par tiers d'octave (< 0,8 = bande peu fiable : bruit, réflexions, vent). Écrêtage ou référence absente = mesure
 refusée (code de sortie non nul). REW / Open Sound Meter restent les outils de référence.

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
    """H = MIC/REF (double canal : indépendant du signal source). Renvoie f, H, IR.
    ref/mic : 1 acquisition (1-D) ou N acquisitions (2-D, une par ligne) moyennées par spectres croisés."""
    f, H, ir, _ = fonction_transfert_moy(ref, mic, fs, eps)
    return f, H, ir


def fonction_transfert_moy(ref: np.ndarray, mic: np.ndarray, fs=FS, eps=1e-6):
    """Estimateur H1 moyenné : H = Σ M·R* / Σ |R|², cohérence γ² = |Σ M·R*|² / (Σ|R|²·Σ|M|²) (1 si N = 1)."""
    ref, mic = np.atleast_2d(ref), np.atleast_2d(mic)
    if ref.shape != mic.shape:
        raise ValueError(f"ref {ref.shape} et mic {mic.shape} de tailles différentes")
    n = 1 << int(np.ceil(np.log2(2 * ref.shape[1])))
    R, M = np.fft.rfft(ref, n, axis=1), np.fft.rfft(mic, n, axis=1)
    Smr, Srr, Smm = (M * np.conj(R)).sum(0), (np.abs(R) ** 2).sum(0), (np.abs(M) ** 2).sum(0)
    H = Smr / (Srr + eps * np.max(Srr))
    coh = np.abs(Smr) ** 2 / (Srr * Smm + 1e-30)
    ir = np.fft.irfft(H, n)
    f = np.fft.rfftfreq(n, 1 / fs)
    return f, H, ir, np.clip(coh, 0, 1)


ECRETAGE = 0.89          # ≈ −1 dBFS crête
REF_MIN_DBFS = -50.0     # en dessous : câble de référence absent ou mauvais canal


def controle_qualite(ref: np.ndarray, mic: np.ndarray) -> list[str]:
    """Défauts bloquants d'une acquisition (liste vide = OK)."""
    err = []
    if np.max(np.abs(mic)) > ECRETAGE:
        err.append("écrêtage du canal MIC (baisser le gain d'entrée de l'interface)")
    if np.max(np.abs(ref)) > ECRETAGE:
        err.append("écrêtage du canal REF (baisser le retour de référence)")
    rms_ref = 20 * np.log10(np.sqrt(np.mean(np.square(ref))) + 1e-12)
    if rms_ref < REF_MIN_DBFS:
        err.append(f"référence trop faible ({rms_ref:.0f} dBFS RMS) : câble REF absent ou mauvais canal d'entrée")
    if np.max(np.abs(mic)) < 10 ** (-70 / 20):
        err.append("micro muet (fantôme ? canal ?)")
    return err


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


def resume(f, H, ir, fs=FS, bande=(20, 20000), coh=None):
    dly, pol = delai_ir(ir, fs)
    # phase avec le délai de propagation retiré
    Hc = H * np.exp(2j * np.pi * f * dly / 1000)
    mag = 20 * np.log10(np.abs(H) + 1e-12)
    centres = [f0 for f0 in 1000 * 2 ** (np.arange(-17, 15) / 3) if bande[0] <= f0 <= bande[1]]
    tiers = []
    for fc in centres:
        m = (f >= fc / 2 ** (1 / 6)) & (f < fc * 2 ** (1 / 6))
        if m.any():
            t = {"f_hz": round(float(fc), 1), "mag_db": round(float(10 * np.log10(np.mean(np.abs(H[m]) ** 2))), 2),
                 "phase_deg": round(float(np.degrees(np.angle(np.mean(Hc[m])))), 1)}
            if coh is not None:
                t["coherence"] = round(float(np.mean(coh[m])), 2)
            tiers.append(t)
    r = {"delai_ms": round(dly, 3), "polarite": "+" if pol > 0 else "-", "tiers_octave": tiers}
    if coh is not None:
        r["bandes_peu_fiables_hz"] = [t["f_hz"] for t in tiers if t["coherence"] < 0.8]
    return r


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
    assert r["polarite"] == "-" and abs(delai_ir(ir2)[0] - 7.25) < 0.05
    # Cas dégradés : bruit (cohérence), écrêtage, référence absente
    rng = np.random.default_rng(1)
    refs = np.tile(xr * 0.1, (3, 1))
    propre = np.concatenate([np.zeros(d), xr * 0.1])[: len(xr)]
    mics = np.stack([propre + rng.normal(0, 0.05, len(xr)) for _ in range(3)])
    f3, H3, ir3, coh = fonction_transfert_moy(refs, mics)
    r3 = resume(f3, H3, ir3, bande=(20, 20000), coh=coh)
    assert abs(r3["delai_ms"] - 7.25) < 0.05, r3["delai_ms"]
    assert r3["bandes_peu_fiables_hz"], "le bruit doit faire baisser la cohérence quelque part"
    _, _, _, coh1 = fonction_transfert_moy(refs, np.stack([propre] * 3))
    assert np.median(coh1[100:20000]) > 0.99
    assert controle_qualite(xr * 0.1, np.clip(xr * 2, -1, 1)) and controle_qualite(xr * 1e-4, xr * 0.1)
    assert controle_qualite(xr * 0.1, xr * 0.1) == []
    print("autotest mesure_tf : OK (délai, polarité, moyennes, cohérence, écrêtage, référence absente)")


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
    ap.add_argument("--moyennes", type=int, default=3, help="nombre de sweeps moyennés (1…16)")
    ap.add_argument("--fs", type=int, default=FS)
    ap.add_argument("--bande", type=float, nargs=2, default=[20, 20000])
    ap.add_argument("--fc", type=float)
    ap.add_argument("-o", "--sortie")
    ap.add_argument("--appliquer", action="store_true",
                    help="émettre réellement le sweep (après confirmation de Louis : routage, niveau, zone sûre)")
    a = ap.parse_args()

    if a.cmd == "autotest":
        return autotest()
    if a.cmd == "comparer":
        return cmd_comparer(a.fichiers[0], a.fichiers[1], a.fc)
    if a.cmd == "analyser":
        D = np.load(a.fichiers[0])
        f, H, ir, coh = fonction_transfert_moy(D["ref"], D["mic"], int(D["fs"]))
        n = np.atleast_2d(D["ref"]).shape[0]
        print(json.dumps(resume(f, H, ir, int(D["fs"]), tuple(a.bande), coh if n > 1 else None),
                         ensure_ascii=False, indent=1))
        return
    try:
        import sounddevice as sd
    except Exception as e:  # pragma: no cover
        sys.exit(f"sounddevice indisponible ({e}). pip install sounddevice")
    if a.cmd == "lister":
        print(sd.query_devices())
        return
    if not (1 <= a.moyennes <= 16 and 0.5 <= a.duree <= 30 and 8000 <= a.fs <= 192000
            and 10 <= a.bande[0] < a.bande[1] <= a.fs / 2):
        sys.exit("REFUS : --moyennes 1…16, --duree 0,5…30 s, --fs 8…192 kHz, --bande f1 < f2 ≤ fs/2.")
    if a.niveau > -6:
        sys.exit("Niveau refusé (> -6 dBFS). Monter progressivement depuis -20 dBFS.")
    if a.cmd == "mesurer":
        if a.dev is None:
            sys.exit("REFUS : --dev obligatoire (interface choisie explicitement, voir « lister »).")
        print(f"Émission prévue : {a.moyennes} × sweep {a.duree:g} s à {a.niveau:g} dBFS crête sur la sortie {a.sortie_canal} "
              f"de l'interface {a.dev} ({a.bande[0]:g}–{a.bande[1]:g} Hz analysés).")
        if not a.appliquer:
            sys.exit("SIMULATION — aucun son émis. Avant --appliquer, Louis confirme : routage de la sortie "
                     f"{a.sortie_canal} vers la bonne voie, gain ampli connu, protection HF en place, zone dégagée, "
                     "niveau de départ sûr. Démarrer voie par voie, à -20 dBFS, puis +3 dB par pas.")
    x = sweep_log(duree=a.duree, fs=a.fs) * 10 ** (a.niveau / 20)
    x = np.concatenate([x, np.zeros(a.fs)])            # 1 s de queue pour la réverbération / le délai
    nout = a.sortie_canal
    play = np.zeros((len(x), nout)); play[:, nout - 1] = x
    refs, mics = [], []
    for k in range(a.moyennes):
        rec = sd.playrec(play, samplerate=a.fs, device=a.dev, input_mapping=[a.ref, a.mic],
                         output_mapping=list(range(1, nout + 1)))
        sd.wait()
        defauts = controle_qualite(rec[:, 0], rec[:, 1])
        if defauts:     # arrêt au premier défaut : inutile de rejouer un sweep qui écrête
            sys.exit(f"MESURE REFUSÉE (sweep {k + 1}/{a.moyennes}) : " + " ; ".join(defauts))
        refs.append(rec[:, 0]); mics.append(rec[:, 1])
    ref, mic = np.stack(refs), np.stack(mics)
    out = a.sortie or "mesure.npz"
    np.savez_compressed(out, ref=ref, mic=mic, fs=a.fs, niveau=a.niveau)
    f, H, ir, coh = fonction_transfert_moy(ref, mic, a.fs)
    print(f"Enregistré : {out} ({a.moyennes} acquisition(s))")
    print(json.dumps(resume(f, H, ir, a.fs, tuple(a.bande), coh if a.moyennes > 1 else None),
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
