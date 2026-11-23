#!/usr/bin/env python3
"""
plan_3d.py — Fiche événement -> page HTML autonome : vue 3D orbitable, vue de dessus cotée,
positions micro proposées, délais physiques et procédure de mesure. Fonctionne hors ligne (aucune bibliothèque web).

  python tools/plan_3d.py data/evenements/2027-06-12_lieu.yaml -o plans/2027-06-12_lieu.html
  python tools/plan_3d.py data/evenements/exemple_2piles_exterieur.yaml --json     # données seules

Repère : origine = milieu du bord de scène, x vers la DROITE vu depuis la régie (côté cour… à préciser dans la fiche),
y vers le public, z vers le haut. Mètres.

Règles de placement des micros (méthode §5, McCarthy) :
  - ALIGNEMENT : sur l'axe de la pile de référence, au 1/3 de la profondeur public ; micro à 1,6 m pour les voies
    médium/aigu, POSÉ AU SOL (demi-espace) au même point pour les voies coupées ≤ 500 Hz (supprime le creux du rebond sol).
  - EQ et niveau : position ONAX = sur l'axe de la pile de référence, à mi-profondeur (point le plus isolé de l'autre
    pile). Jamais au centre entre deux piles (interférences G/D, point de raccord). McCarthy 2007, ch. 10.
  - OFFAX (fond, bord) : contrôle de la couverture, pas de décision d'EQ ; ≤ 6 dB sous ONAX avec la même tendance,
    sinon revoir l'orientation. La régie sert de contrôle, pas de référence.
Hauteurs des centres acoustiques = centre géométrique des caisses (approximation, signalée).
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
PALETTE = ["#3b4a6b", "#2f6f73", "#7a6a2f", "#8a3d3d", "#6b3d7a", "#4f6b3b", "#7a4f2f", "#3d5a7a"]


def ordre_voies(cfg: dict) -> list:
    """Ordre des voies = ordre de la config (du plus grave au plus aigu)."""
    return [v["nom"] for v in cfg.get("voies", [])] or list((cfg.get("caisses") or {}).keys())


def voies_grave(cfg: dict, xo: dict) -> set:
    """Voies coupées à 500 Hz ou moins : micro posé au sol pour leur mesure (le rebond sur le sol d'un micro à 1,6 m
    creuse typiquement entre 300 et 500 Hz à 10–20 m). À défaut de crossover : la première voie."""
    out = set()
    for v in ordre_voies(cfg):
        f = freq_coupure((xo.get(v) or {}).get("lpf"))
        if f is not None and f <= 500:
            out.add(v)
    return out or set(ordre_voies(cfg)[:1])


def c_son(t):
    return 331.3 + 0.606 * (t if t is not None else 20.0)


def num(v, defaut=None):
    return defaut if v is None else float(v)


def freq_coupure(chaine):
    if not chaine:
        return None
    m = re.search(r"([\d.,]+)\s*(k?)Hz", str(chaine))
    if not m:
        return None
    f = float(m.group(1).replace(",", "."))
    return f * 1000 if m.group(2) else f


def construire(fiche: dict, cfg: dict) -> dict:
    alertes = []
    T = (fiche.get("lieu") or {}).get("meteo_prevue", {}).get("temperature_c")
    if T is None:
        alertes.append("Température inconnue : vitesse du son calculée à 20 °C.")
    c = c_son(T)
    dims = cfg.get("caisses", {})
    ordre = ordre_voies(cfg)
    xo = cfg.get("crossover") or cfg.get("crossover_actuel") or {}
    graves = voies_grave(cfg, xo)
    couleur = {v: PALETTE[i % len(PALETTE)] for i, v in enumerate(ordre)}

    # ---------- piles ----------
    boites, piles = [], []
    for p in fiche["systeme"]["piles"]:
        px, py = num(p["position"]["x_m"]), num(p["position"]["y_m"], 0.0)
        if px is None:
            sys.exit(f"Pile {p['nom']} : position x manquante dans la fiche.")
        rot = num(p.get("orientation_deg"), 0.0)
        z = num(p.get("hauteur_sol_m"), 0.0)
        centres = {}
        for voie in p["empilement_bas_en_haut"]:
            d = dims.get(voie) or {}
            l, pr, h = d.get("l"), d.get("p"), d.get("h")
            if None in (l, pr, h):
                alertes.append(f"{voie} : dimensions inconnues, dessin indicatif (40×40×30 cm).")
                l, pr, h = 40, 40, 30
            l, pr, h = l / 100, pr / 100, h / 100
            boites.append({"nom": f"{p['nom']}·{voie}", "voie": voie, "x": px, "y": py, "z": z, "l": l, "p": pr, "h": h,
                           "rot": rot, "couleur": couleur.get(voie, "#777")})
            centres.setdefault(voie, []).append(z + h / 2)
            z += h
        # centre acoustique d'une voie = moyenne des centres des caisses de cette voie
        piles.append({"nom": p["nom"], "x": px, "y": py, "rot": rot, "hauteur": round(z, 2),
                      "centres": {v: sum(zs) / len(zs) for v, zs in centres.items()}})

    # ---------- zone public / régie ----------
    zp = (fiche.get("public") or {}).get("zone") or {}
    y0, D, W = num(zp.get("distance_premier_rang_m")), num(zp.get("profondeur_m")), num(zp.get("largeur_m"))
    if None in (y0, D, W):
        sys.exit("Zone public incomplète (distance_premier_rang_m, profondeur_m, largeur_m).")
    rg = fiche.get("regie") or {}
    regie = {"x": num(rg.get("x_m"), 0.0), "y": num(rg.get("distance_scene_m")), "z": num(rg.get("hauteur_m"), 0.0)}

    # ---------- positions micro ----------
    ref = min(piles, key=lambda q: q["x"]) if len(piles) > 1 else piles[0]
    s = -1 if ref["x"] < 0 else 1
    deux_piles = len(piles) > 1
    x_eq = ref["x"] if deux_piles else 0.0   # ONAX : sur l'axe de la pile de référence
    mics = [
        {"id": "P1", "role": "alignement", "x": ref["x"], "y": y0 + D / 3, "z": 1.6,
         "consigne": f"Sur l'axe de la pile {ref['nom']}. Micro POSÉ AU SOL pour {', '.join(v for v in ordre if v in graves)} ; "
                     f"à 1,6 m au même point pour les autres voies."},
        {"id": "P2", "role": "eq", "x": x_eq, "y": y0 + D / 2, "z": 1.6,
         "consigne": f"ONAX : axe de la pile {ref['nom']}, mi-profondeur. Référence de niveau et d'EQ (vue au 1/24 d'octave)."},
        {"id": "P3", "role": "offax", "x": x_eq, "y": y0 + 0.9 * D, "z": 1.6,
         "consigne": "OFFAX fond : contrôle de couverture, ≤ 6 dB sous P2 (pas de décision d'EQ ici)."},
        {"id": "P4", "role": "offax", "x": s * 0.85 * W / 2, "y": y0 + D / 2, "z": 1.6,
         "consigne": f"OFFAX bord extérieur côté {ref['nom']} : contrôle de couverture, ≤ 6 dB sous P2."},
    ]
    if regie["y"] is not None:
        mics.append({"id": "P5", "role": "controle", "x": regie["x"] + (0.75 if abs(regie["x"]) < 0.5 and deux_piles else 0),
                     "y": regie["y"], "z": regie["z"] + 1.6,
                     "consigne": "Régie : contrôle et LAeq/LCeq 15 min. Pas de décision d'EQ sur ce seul point."})
    for m in mics:
        m["x"], m["y"] = round(max(-W / 2, min(W / 2, m["x"])), 2), round(m["y"], 2)

    # ---------- calculs par micro ----------
    for m in mics:
        m["piles"] = {}
        for p in piles:
            arr = {}
            for v, zc in p["centres"].items():
                zm = 0.0 if (m["id"] == "P1" and v in graves) else m["z"]
                d = math.dist((m["x"], m["y"], zm), (p["x"], p["y"], zc))
                arr[v] = {"distance_m": round(d, 2), "temps_ms": round(d / c * 1000, 2)}
            m["piles"][p["nom"]] = arr
        if deux_piles:
            def d_mid(arr):
                ks = [v for v in ordre if v in arr] or list(arr)
                return arr[ks[len(ks) // 2]]["distance_m"]
            ts = [d_mid(m["piles"][p["nom"]]) for p in piles]
            delta = max(ts) - min(ts)
            m["ecart_piles_m"] = round(delta, 2)
            m["premier_creux_hz"] = round(c / (2 * delta), 0) if delta > 0.01 else None

    # pré-alignement physique à P1 sur la pile de référence (micro à 1,6 m pour toutes les voies : hauteur d'écoute)
    m1 = mics[0]
    p1 = {v: {"temps_ms": round(math.dist((m1["x"], m1["y"], 1.6), (ref["x"], ref["y"], zc)) / c * 1000, 3)}
          for v, zc in ref["centres"].items()}
    tmax = max(a["temps_ms"] for a in p1.values())
    prealign = []
    voies = [v for v in ordre if v in p1]
    for a, b in zip(voies, voies[1:]):
        # zone de recouvrement : on retient le HPF de la voie haute (cas du recouvrement HIGH/SUPER), sinon le LPF de la basse
        fc = freq_coupure((xo.get(b) or {}).get("hpf")) or freq_coupure((xo.get(a) or {}).get("lpf"))
        dt = p1[a]["temps_ms"] - p1[b]["temps_ms"]
        prealign.append({"paire": f"{a}/{b}", "fc_hz": fc, "ecart_trajet_ms": round(dt, 3),
                         "periode_fc_ms": round(1000 / fc, 2) if fc else None,
                         "lecture": (f"{b} arrive {abs(dt):.2f} ms {'après' if dt < 0 else 'avant'} {a} : point de départ physique ; "
                                     "la valeur finale se décide sur la PHASE mesurée à Fc (retard de groupe des filtres inclus).")})
    retards = {v: round(tmax - p1[v]["temps_ms"], 3) for v in voies}

    inconnus = sorted({b["voie"] for b in boites if b["voie"] not in ordre})
    if inconnus:
        alertes.append(f"Éléments de pile absents des voies de la config : {', '.join(inconnus)} (dessinés, non calculés).")
    for p in piles:
        if p["hauteur"] > 4.0:
            alertes.append(f"Pile {p['nom']} : {p['hauteur']:.2f} m — vérifier sanglage/stabilité et vent.")
    if deux_piles:
        alertes.append("Deux piles en mono : interférences G/D au centre et en peigne ailleurs — normal, ne pas les « corriger » à l'EQ.")
    alertes.append("Centres acoustiques = centres géométriques des caisses (approximation ; sur un pavillon, le point d'émission des aigus est en retrait — à affiner par la mesure).")

    sol = [v for v in voies if v in graves]
    air = [v for v in voies if v not in graves]
    paires = [f"{a}/{b}" for a, b in zip(voies, voies[1:])]
    procedure = [
        "Sécurité : niveau de départ −20 dBFS au générateur, personne devant les piles, protections auditives.",
        (f"P1, micro au SOL : {', '.join(sol)} chacun seul (polarité, réponse, cohérence)" if sol else "P1 : voies graves seules")
        + (f", puis paire(s) {', '.join(p for p in paires if all(x in sol for x in p.split('/')))} : phase à Fc, délai (pas ≤ 0,5 ms), sommation." if len(sol) > 1 else "."),
        f"P1, micro à 1,6 m : {', '.join(air)} chacun seul ; puis paires {', '.join(p for p in paires if not all(x in sol for x in p.split('/')))} "
        "(pour une paire grave/aigu, remesurer la voie grave à 1,6 m).",
        "Si plusieurs piles : reproduire P1 en miroir sur chaque autre pile, vérifier niveau et polarité (réglages identiques attendus).",
        "P2 (ONAX) : EQ système et niveau, décidés sur une vue au 1/24 d'octave (`--resolution 24`), bandes fiables "
        "seulement (cohérence ≥ 0,8), en suivant l'enveloppe (les creux étroits ne s'égalisent pas).",
        "P3, P4 (OFFAX) : superposer à P2. Viser au plus 6 dB d'écart, avec la même tendance spectrale ; au-delà, revoir "
        "l'orientation ou le placement, pas l'EQ. Une moyenne (`osm_client.py moyenne`) ne réunit que des positions ONAX "
        "comparables (ex. une par pile), jamais un bord ni un point de raccord.",
        "P5 régie : contrôle global, niveau LAeq/LCeq 15 min sur programme réel ; comparer aux limites réglementaires de la fiche.",
        "Écoute avec musique de référence du style, ajustements fins, puis mémorisation du preset et mise à jour du journal.",
    ]
    if any((v.get("type") or cfg.get("type_systeme")) in ("constructeur", "amplifiee") for v in cfg.get("voies", [])):
        procedure.insert(1, "Système constructeur : vérifier d'abord que chaque élément a le bon preset usine ; on n'aligne/égalise "
                            "qu'ENTRE ensembles (subs, têtes, fills), jamais à l'intérieur d'un preset.")

    return {"meta": {"evenement": (fiche.get("evenement") or {}).get("nom"), "lieu": (fiche.get("lieu") or {}).get("nom"),
                     "temperature_c": T, "vitesse_son_ms": round(c, 1), "style": (fiche.get("style") or {}).get("principal")},
            "zone": {"y0": y0, "profondeur": D, "largeur": W}, "regie": regie, "piles": piles, "boites": boites,
            "mics": mics, "prealignement_P1": prealign, "retards_physiques_P1_ms": retards,
            "alertes": alertes, "procedure": procedure}


# ---------------------------------------------------------------- HTML
def svg_dessus(sc: dict) -> str:
    z, W = sc["zone"], sc["zone"]["largeur"]
    ys = [b["y"] for b in sc["boites"]] + [m["y"] for m in sc["mics"]] + [z["y0"] + z["profondeur"]]
    xmin, xmax = -W / 2 - 3, W / 2 + 3
    ymin, ymax = min(ys + [-3]) - 2, max(ys) + 3
    S = 600 / (xmax - xmin)
    Hpx = (ymax - ymin) * S

    def X(x):
        return (x - xmin) * S

    def Y(y):
        return (y - ymin) * S

    o = [f'<svg viewBox="0 0 600 {Hpx:.0f}" xmlns="http://www.w3.org/2000/svg" class="dessus" role="img" aria-label="Vue de dessus cotée">']
    o.append(f'<rect x="{X(-W/2):.1f}" y="{Y(z["y0"]):.1f}" width="{W*S:.1f}" height="{z["profondeur"]*S:.1f}" class="zone"/>')
    o.append(f'<text x="{X(W/2)-4:.1f}" y="{Y(z["y0"])+14:.1f}" text-anchor="end" class="petit">Public {W:g} × {z["profondeur"]:g} m</text>')
    o.append(f'<line x1="{X(xmin):.1f}" y1="{Y(0):.1f}" x2="{X(xmax):.1f}" y2="{Y(0):.1f}" class="scene"/>')
    o.append(f'<text x="{X(xmin)+4:.1f}" y="{Y(0)-5:.1f}" class="petit">bord de scène (y = 0)</text>')
    for p in sc["piles"]:
        b = [q for q in sc["boites"] if q["nom"].startswith(p["nom"] + "·")]
        l = max(q["l"] for q in b); pr = max(q["p"] for q in b)
        o.append(f'<rect x="{X(p["x"]-l/2):.1f}" y="{Y(p["y"]-pr):.1f}" width="{l*S:.1f}" height="{pr*S:.1f}" class="pile" '
                 f'transform="rotate({p["rot"]:g} {X(p["x"]):.1f} {Y(p["y"]):.1f})"/>')
        o.append(f'<text x="{X(p["x"]):.1f}" y="{Y(p["y"]-pr)-5:.1f}" text-anchor="middle" class="lbl">Pile {html.escape(p["nom"])}</text>')
    ref = min(sc["piles"], key=lambda q: q["x"])
    for m in sc["mics"]:
        d = m["piles"][ref["nom"]]
        ks = list(d)
        dist = d[ks[len(ks) // 2]]["distance_m"]
        o.append(f'<line x1="{X(ref["x"]):.1f}" y1="{Y(ref["y"]):.1f}" x2="{X(m["x"]):.1f}" y2="{Y(m["y"]):.1f}" class="cote"/>')
        o.append(f'<circle cx="{X(m["x"]):.1f}" cy="{Y(m["y"]):.1f}" r="7" class="mic {m["role"]}"/>')
        o.append(f'<text x="{X(m["x"])+10:.1f}" y="{Y(m["y"])+4:.1f}" class="lbl">{m["id"]}</text>')
        o.append(f'<text x="{X(m["x"]):.1f}" y="{Y(m["y"])+20:.1f}" text-anchor="middle" class="petit">{dist:g} m</text>')
    if sc["regie"]["y"] is not None:
        r = sc["regie"]
        o.append(f'<rect x="{X(r["x"]-1.5):.1f}" y="{Y(r["y"]-0.75):.1f}" width="{3*S:.1f}" height="{1.5*S:.1f}" class="regie"/>')
        o.append(f'<text x="{X(r["x"]):.1f}" y="{Y(r["y"]+0.75)+14:.1f}" text-anchor="middle" class="petit">Régie</text>')
    o.append("</svg>")
    return "".join(o)


def page(sc: dict) -> str:
    m = sc["meta"]
    titre = f"Plan de mesure — {m.get('evenement') or 'événement'}"
    def carte_mic(x):
        noms = list(x["piles"])
        voies = list(next(iter(x["piles"].values())))
        tete = "".join(f"<th>Pile {html.escape(n)}</th>" for n in noms)
        corps = "".join("<tr><td>" + v + "</td>" + "".join(
            f"<td>{x['piles'][n][v]['distance_m']} m · {x['piles'][n][v]['temps_ms']} ms</td>" for n in noms) + "</tr>" for v in voies)
        creux = f" · 1er creux G/D ≈ {x['premier_creux_hz']:g} Hz" if x.get("premier_creux_hz") else ""
        return (f'<div class="card mic-c"><div><b>{x["id"]}</b> · <span class="role {x["role"]}">{x["role"]}</span> · '
                f'x {x["x"]:g} m · y {x["y"]:g} m · h {x["z"]:g} m{creux}</div>'
                f'<p>{html.escape(x["consigne"])}</p><table><tr><th>Voie</th>{tete}</tr>{corps}</table></div>')
    lignes_mic = "".join(carte_mic(x) for x in sc["mics"])
    lignes_pa = "".join(f"<tr><td>{p['paire']}</td><td>{f"{p['fc_hz']:g} Hz" if p['fc_hz'] else '—'}</td><td>{p['ecart_trajet_ms']}</td><td>{p['periode_fc_ms'] or '—'}</td></tr>"
                        for p in sc["prealignement_P1"])
    retards = " · ".join(f"{v} {t} ms" for v, t in sc["retards_physiques_P1_ms"].items())
    proc = "".join(f"<li>{html.escape(s)}</li>" for s in sc["procedure"])
    alertes = "".join(f"<li>{html.escape(a)}</li>" for a in sc["alertes"])
    data = json.dumps({"boites": sc["boites"], "mics": sc["mics"], "zone": sc["zone"], "regie": sc["regie"]})
    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(titre)}</title>
<style>
:root{{--bg:#f6f5f1;--fg:#1d1d1b;--mut:#6b6a66;--card:#fff;--line:#d9d7d0;--acc:#c2410c;}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#eceae4;--mut:#a3a19b;--card:#1f1f1d;--line:#3a3935;--acc:#fb923c;}}}}
*{{box-sizing:border-box}} body{{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:980px;margin:auto;padding:16px}} h1{{font-size:1.35rem;margin:.2rem 0}} h2{{font-size:1.05rem;margin:1.6rem 0 .5rem}}
.sub{{color:var(--mut);font-size:.9rem}} .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;overflow:auto}}
canvas{{width:100%;height:min(70vh,520px);display:block;touch-action:none;border-radius:8px}}
.btns{{display:flex;gap:8px;margin:8px 0;flex-wrap:wrap}} button{{font:inherit;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}}
table{{border-collapse:collapse;width:100%;font-size:.86rem}} td,th{{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}}
svg.dessus{{width:100%;height:auto}} .zone{{fill:rgba(194,65,12,.08);stroke:var(--acc);stroke-dasharray:4 3}} .scene{{stroke:var(--fg);stroke-width:2}}
.pile{{fill:#2f3a4a}} .regie{{fill:none;stroke:var(--mut)}} .cote{{stroke:var(--mut);stroke-dasharray:2 3}}
.mic{{stroke:#fff;stroke-width:2}} .mic.alignement{{fill:#c2410c}} .mic.eq{{fill:#2563eb}} .mic.controle{{fill:#16a34a}} .mic.offax{{fill:#7c3aed}}
.lbl{{font-size:12px;font-weight:600;fill:var(--fg)}} .petit{{font-size:10px;fill:var(--mut)}} ol li,ul li{{margin:.3rem 0}}
  .mic-c{{margin:10px 0}} .mic-c p{{margin:.4rem 0;color:var(--mut);font-size:.9rem}} .role{{font-weight:600}} .role.alignement{{color:#c2410c}} .role.eq{{color:#2563eb}} .role.controle{{color:#16a34a}} .role.offax{{color:#7c3aed}}
.leg span{{display:inline-block;margin-right:12px}} .pt{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:middle}}
</style></head><body><main>
<h1>{html.escape(titre)}</h1>
<div class="sub">{html.escape(str(m.get('lieu') or ''))} · style {html.escape(str(m.get('style') or '—'))} · {m.get('temperature_c') if m.get('temperature_c') is not None else '20 (supposé)'} °C · c = {m['vitesse_son_ms']} m/s</div>

<h2>Vue 3D</h2>
<div class="card"><canvas id="c3d"></canvas>
<div class="btns"><button data-v="3d">3D</button><button data-v="dessus">Dessus</button><button data-v="face">Face</button><button data-v="micro">Depuis P1</button></div>
<div class="sub leg"><span><i class="pt" style="background:#c2410c"></i>alignement</span><span><i class="pt" style="background:#2563eb"></i>EQ / niveau (ONAX)</span><span><i class="pt" style="background:#7c3aed"></i>couverture (OFFAX)</span><span><i class="pt" style="background:#16a34a"></i>contrôle</span> · glisser pour tourner, pincer/molette pour zoomer</div></div>

<h2>Vue de dessus cotée</h2>
<div class="card">{svg_dessus(sc)}</div>

<h2>Positions micro</h2>
<p class="sub">Repère : origine au milieu du bord de scène, x vers la droite vu de la régie, y vers le public, h = hauteur du micro.</p>{lignes_mic}

<h2>Pré-alignement physique à P1</h2>
<div class="card"><p class="sub">Retards à appliquer pour égaliser les temps de vol (avant mesure) : {retards}</p>
<table><tr><th>Paire</th><th>Fc</th><th>Écart de trajet (ms)</th><th>Période à Fc (ms)</th></tr>{lignes_pa}</table>
<p class="sub">Point de départ seulement : le réglage final se fait sur la phase mesurée à Fc (filtres et retard de groupe inclus).</p></div>

<h2>Procédure</h2><div class="card"><ol>{proc}</ol></div>
<h2>Alertes</h2><div class="card"><ul>{alertes}</ul></div>
</main>
<script>
const S={data};
const cv=document.getElementById('c3d'),cx=cv.getContext('2d');
let az=Math.PI-0.55,el=0.5,dist=0,tx=0,ty=0,tz=1.5;
const Z=S.zone; ty=(Z.y0+Z.profondeur)*0.4; dist=Math.max(Z.largeur,Z.y0+Z.profondeur)*0.85;
function vue(v){{if(v==='3d'){{az=Math.PI-0.55;el=0.5}} if(v==='dessus'){{az=Math.PI;el=1.55}} if(v==='face'){{az=Math.PI;el=0.05}}
 if(v==='micro'){{const m=S.mics[0];az=Math.PI;el=0.12;tx=m.x;ty=m.y;tz=m.z;dist=0.01;draw();return}}
 tx=0;ty=(Z.y0+Z.profondeur)*0.4;tz=1.5;dist=Math.max(Z.largeur,Z.y0+Z.profondeur)*0.85;draw()}}
document.querySelectorAll('button[data-v]').forEach(b=>b.onclick=()=>vue(b.dataset.v));
function cam(){{const ce=Math.cos(el),se=Math.sin(el),ca=Math.cos(az),sa=Math.sin(az);
 const ex=tx+dist*ce*sa, ey=ty-dist*ce*ca, ez=tz+dist*se; return {{ex,ey,ez,ca,sa,ce,se}}}}
function proj(p,C,W,H){{let dx=p[0]-C.ex,dy=p[1]-C.ey,dz=p[2]-C.ez;
 // repère caméra : avant = vers la cible
 const fx=-C.ce*C.sa, fy=C.ce*C.ca, fz=-C.se; const rx=C.ca, ry=C.sa, rz=0;
 const ux=ry*fz-rz*fy, uy=rz*fx-rx*fz, uz=rx*fy-ry*fx;
 const zc=dx*fx+dy*fy+dz*fz; if(zc<0.05) return null; const f=Math.min(W,H)*0.9;
 return [W/2+(dx*rx+dy*ry+dz*rz)/zc*f, H/2-(dx*ux+dy*uy+dz*uz)/zc*f, zc]}}
function boxPts(b){{const r=b.rot*Math.PI/180,c=Math.cos(r),s=Math.sin(r);const pts=[];
 for(const [u,v] of [[-b.l/2,-b.p],[b.l/2,-b.p],[b.l/2,0],[-b.l/2,0]]) pts.push([b.x+u*c-v*s,b.y+u*s+v*c]);
 return pts.map(q=>[q[0],q[1],b.z]).concat(pts.map(q=>[q[0],q[1],b.z+b.h]))}}
function shade(hex,k){{const n=parseInt(hex.slice(1),16);const r=(n>>16)*k,g=((n>>8)&255)*k,bl=(n&255)*k;return `rgb(${{r|0}},${{g|0}},${{bl|0}})`}}
function draw(){{const dpr=devicePixelRatio||1,W=cv.clientWidth,H=cv.clientHeight;cv.width=W*dpr;cv.height=H*dpr;cx.setTransform(dpr,0,0,dpr,0,0);
 const dark=matchMedia('(prefers-color-scheme:dark)').matches; cx.fillStyle=dark?'#1a1a19':'#eef0f2';cx.fillRect(0,0,W,H);
 const C=cam(),P=p=>proj(p,C,W,H); const polys=[];
 // sol : grille 1 m
 cx.strokeStyle=dark?'#2b2b29':'#d6d9dd';cx.lineWidth=1;const gx=Math.ceil(Z.largeur/2)+4, gy=Z.y0+Z.profondeur+4;
 for(let x=-gx;x<=gx;x+=1){{const a=P([x,-4,0]),b=P([x,gy,0]);if(a&&b){{cx.beginPath();cx.moveTo(a[0],a[1]);cx.lineTo(b[0],b[1]);cx.stroke()}}}}
 for(let y=-4;y<=gy;y+=1){{const a=P([-gx,y,0]),b=P([gx,y,0]);if(a&&b){{cx.beginPath();cx.moveTo(a[0],a[1]);cx.lineTo(b[0],b[1]);cx.stroke()}}}}
 polys.push({{pts:[[-Z.largeur/2,Z.y0,0.01],[Z.largeur/2,Z.y0,0.01],[Z.largeur/2,Z.y0+Z.profondeur,0.01],[-Z.largeur/2,Z.y0+Z.profondeur,0.01]],fill:'rgba(194,65,12,0.13)',stroke:'#c2410c'}});
 polys.push({{pts:[[-gx+2,-4,0.005],[gx-2,-4,0.005],[gx-2,0,0.005],[-gx+2,0,0.005]],fill:dark?'rgba(255,255,255,.06)':'rgba(0,0,0,.07)',stroke:null}});
 if(S.regie.y!=null){{const r=S.regie;polys.push({{pts:[[r.x-1.5,r.y-.75,.9],[r.x+1.5,r.y-.75,.9],[r.x+1.5,r.y+.75,.9],[r.x-1.5,r.y+.75,.9]],fill:'rgba(120,120,120,.5)',stroke:'#777'}})}}
 for(const b of S.boites){{const q=boxPts(b);const F=[[0,1,2,3,.55],[4,5,6,7,1],[0,1,5,4,.85],[1,2,6,5,.7],[2,3,7,6,.6],[3,0,4,7,.7]];
  for(const f of F) polys.push({{pts:f.slice(0,4).map(i=>q[i]),fill:shade(b.couleur,f[4]),stroke:'rgba(0,0,0,.35)'}})}}
 const pp=[];for(const p of polys){{const s=p.pts.map(P);if(s.some(v=>!v))continue;pp.push({{s,d:s.reduce((a,v)=>a+v[2],0)/s.length,p}})}}
 pp.sort((a,b)=>b.d-a.d);for(const o of pp){{cx.beginPath();o.s.forEach((v,i)=>i?cx.lineTo(v[0],v[1]):cx.moveTo(v[0],v[1]));cx.closePath();cx.fillStyle=o.p.fill;cx.fill();if(o.p.stroke){{cx.strokeStyle=o.p.stroke;cx.stroke()}}}}
 const col={{alignement:'#c2410c',eq:'#2563eb',controle:'#16a34a',offax:'#7c3aed'}};cx.font='600 12px system-ui';
 for(const m of S.mics){{const a=P([m.x,m.y,0]),b=P([m.x,m.y,m.z]);if(!a||!b)continue;cx.strokeStyle=col[m.role];cx.lineWidth=2;cx.beginPath();cx.moveTo(a[0],a[1]);cx.lineTo(b[0],b[1]);cx.stroke();
  cx.fillStyle=col[m.role];cx.beginPath();cx.arc(b[0],b[1],6,0,7);cx.fill();cx.fillStyle=dark?'#fff':'#111';cx.fillText(m.id,b[0]+8,b[1]-6)}}
}}
let drag=null,pinch=null;cv.addEventListener('pointerdown',e=>{{drag=[e.clientX,e.clientY];cv.setPointerCapture(e.pointerId)}});
cv.addEventListener('pointermove',e=>{{if(!drag)return;az+=(e.clientX-drag[0])*0.008;el=Math.max(0.02,Math.min(1.56,el+(e.clientY-drag[1])*0.006));drag=[e.clientX,e.clientY];draw()}});
cv.addEventListener('pointerup',()=>drag=null);cv.addEventListener('wheel',e=>{{e.preventDefault();dist=Math.max(2,dist*(1+Math.sign(e.deltaY)*0.1));draw()}},{{passive:false}});
cv.addEventListener('touchmove',e=>{{if(e.touches.length===2){{const d=Math.hypot(e.touches[0].clientX-e.touches[1].clientX,e.touches[0].clientY-e.touches[1].clientY);if(pinch)dist=Math.max(2,dist*pinch/d);pinch=d;drag=null;draw()}}}},{{passive:true}});
cv.addEventListener('touchend',()=>pinch=null);addEventListener('resize',draw);draw();
</script></body></html>"""


def main():
    if hasattr(sys.stdout, "reconfigure"):  # console Windows cp1252 : Ω, ≈, −, ≤ sinon UnicodeEncodeError
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fiche")
    ap.add_argument("-o", "--sortie")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    fiche = yaml.safe_load(Path(a.fiche).read_text(encoding="utf-8"))
    cfg_nom = (fiche.get("systeme") or {}).get("config")
    if not cfg_nom:
        sys.exit("La fiche n'indique pas systeme.config (fichier de data/configs/).")
    cfg = yaml.safe_load((RACINE / "data" / "configs" / f"{cfg_nom}.yaml").read_text(encoding="utf-8"))
    sc = construire(fiche, cfg)
    if a.json:
        print(json.dumps({k: v for k, v in sc.items() if k != "boites"}, ensure_ascii=False, indent=1))
        return
    out = Path(a.sortie or RACINE / "plans" / (Path(a.fiche).stem + ".html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page(sc), encoding="utf-8")
    print(f"Plan écrit : {out}")
    for al in sc["alertes"]:
        print(" -", al)


if __name__ == "__main__":
    main()
