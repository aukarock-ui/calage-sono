---
name: methode-calage
description: Méthode de calage système pour N'IMPORTE QUELLE sono (DIY à composants, système constructeur avec presets, enceintes amplifiées ; tout processeur) — crossover, alignement, gain, EQ système, limiteurs, adaptation au style, avant d'écrire quoi que ce soit dans un processeur.
---

# Méthode de calage système — niveau festival, tout système

Rôle : ingénieur système senior touring. Physique avant DSP. Zéro approximation : aucune spécification inventée,
aucun réglage sans justification chiffrée. Donnée manquante → la demander à Louis ou la mesurer.

## Sources de vérité (lire avant de commencer)
- `data/configs/<systeme>.yaml` — LE système du jour (voies, éléments, câblage, amplis, processeur, presets).
  Système inconnu du kit → skill `nouveau-systeme` d'abord.
- `data/materiel.yaml` — bibliothèque partagée de composants (HP, pavillons, amplis, processeurs), enrichie à chaque
  nouveau système. Seule source de chiffres constructeur.
- `data/evenements/<fiche>.yaml` — lieu, disposition, public, style, réglementation (skill `fiche-evenement`).
- `docs/processeurs.md` — ce que le kit sait piloter (réseau) ou non (saisie guidée).
- `journal/ETAT.md` — état réel, réglages validés, questions ouvertes.

## D'abord : quel type de système ?
| Type (`type_systeme` dans la config) | Ce que Claude règle | Ce qu'il ne touche PAS |
|---|---|---|
| **composants** (HP + amplis + processeur, DIY/semi-pro) | tout : coupures, polarités, délais internes, EQ, gains, limiteurs calculés | — |
| **constructeur** (presets usine : L-Acoustics, d&b, Meyer, NCA, Adamson…) | alignement entre ensembles (subs ↔ têtes ↔ fills ↔ délais), gains de zones, EQ système, délais de site, choix du preset dans la gamme constructeur | crossover, EQ et limiteurs INTERNES des presets |
| **amplifiee** (enceintes actives avec DSP) | comme « constructeur » + modes/presets de l'enceinte | protections internes |
Système mixte (ex. têtes constructeur + subs DIY) : chaque voie porte son propre type.

## Ordre imposé (aucune étape sautée ; en « constructeur », 1-2 et 8 se limitent à vérifier les docs constructeur)
1. **Mécanique** — par voie : charge (BR / clos / pavillon / BP), accord, bande utile réelle, risque d'excursion sous
   l'accord. Bass-reflex ⇒ HPF obligatoire sous l'accord. Deux contrôles, faits automatiquement par `calc_systeme.py`
   (via `tools/excursion_br.py`) dès que l'accord et le volume de la caisse sont connus :
   (a) **excursion** calculée (modèle T/S de Small) à la tension cible du limiteur, de 10 Hz à 2·fb, ≤ Xmax ; c'est le
       critère qui fait foi. Si le pic est AU-DESSUS de l'accord, le HPF n'y peut rien : il faut limiter le niveau ;
   (b) **pente du HPF** : au moins 14 dB d'atténuation à 0,7·fb, soit ≥ 28 dB/oct entre 0,7·fb et fb (Marshall,
       audioXpress 2016, `docs/references/`). Un 18 dB/oct ne suffit pas. Les filtres raides ajoutent du retard de groupe
       (≈ 27 ms à 20 Hz pour un 24 dB/oct) : préférer le plus doux qui satisfait (a) et (b).
   Le modèle donne un ordre de grandeur. Confirmer l'accord réel par une mesure d'impédance, et l'excursion à faible
   niveau. Compression ⇒ HPF ≥ coupure mini constructeur, pente mini
   respectée. Jamais de Fc sous la bande utile mécanique.
2. **Électrique** — `python tools/calc_systeme.py data/configs/<systeme>.yaml`. Citer la sortie, ne pas recalculer à la
   main. Vérifier : impédance vue par l'ampli, série/parallèle, puissance ampli à cette charge, marge, alertes.
3. **Acoustique** — sensibilité réaliste, couplage (+6 dB cohérent en grave, +3 dB incohérent en aigu), cohérence SPL
   des voies à chaque Fc, directivité des pavillons/enceintes (fiche), couverture de la zone public (plan 3D).
4. **Crossovers** — LR24 par défaut ; tout autre choix justifié. Chaque Fc justifiée (mécanique, excursion, directivité,
   SPL, style). Pas d'arrondi arbitraire. En « constructeur » : point de raccord subs/têtes selon la doc constructeur.
5. **Alignement temporel** — délais physiques (`plan_3d.py` / `calc_systeme.py --delai`), puis MESURE : phase à Fc
   (`osm_client.py comparer`), ajustement sub-ms, test d'inversion de polarité, validation de la sommation
   (≈ +6 dB à niveaux égaux). Le réglage FIN se fait sur la PHASE à Fc, pas sur le pic d'IR brut — mais il part
   d'une position grossière prise sur les IR filtrées autour de Fc (`rew_client.py aligner`), sinon la phase seule
   peut se tromper d'un cycle entier (et inverser la polarité à tort). Subs cardio/end-fire/arc : délais
   calculés à partir des distances, validés par mesure avant/arrière.
6. **Structure de gain** — 90–95 % du potentiel, zéro écrêtage DSP/ampli. Gains amplis confirmés au voltmètre en
   « composants ». Équilibre des voies selon le style.
7. **EQ stratégique** — chaque filtre corrige un problème mesuré (moyenne spatiale ≥ 3 positions, cohérence ≥ 0,8),
   améliore le headroom ou la lisibilité. Pas d'EQ décorative. Les creux d'annulation ne se corrigent pas à l'EQ.
8. **Limiteurs** — « composants » : seuils = sortie de `calc_systeme.py`. RMS (thermique) sur le grave, crête sur les
   moteurs à compression. Attaque/relâchement : sub lent, grave moyen, aigu très rapide. « constructeur » : jamais
   modifiés ; vérifier seulement que le preset correspond à l'élément et à l'ampli.
   Savoir ce que fait le limiteur du processeur : un limiteur CRÊTE seul (ex. DCX2496, « pour les crêtes, pas pour un
   usage continu ») ne protège pas thermiquement → la protection thermique repose alors sur le gain et le limiteur de
   l'ampli, à noter dans la config. **Dépassement des crêtes** : un limiteur dbx laisse passer les pics au-dessus du
   seuil (PA2 : 3 dB fixes ; VENU360 : OVERSHOOT PeakStopPlus 2–6 dB, 6 par défaut). Le manuel PA2 demande de baisser
   le seuil d'autant : `calc_systeme.py` le fait (colonne « seuil (brut − coussin) ») avec l'overshoot de la config,
   lu par `dbx_hiqnet.py carte`. VENU360 : PeakStopPlus OFF = détection RMS (thermique), ON = crête.
   Processeur en dBu : vérifier que le seuil reste sous la pleine échelle RÉELLE de sortie (t.racks : +5 dBu au
   manuel, +20 dBu mesuré sur un 408) ; au-dessus, le limiteur n'agit jamais. Avant d'écrire, vérifier les LIENS (stéréo, délais) : une écriture peut être recopiée
   sur une sortie couplée, et activer un lien peut écraser les réglages des sorties couplées.
9. **Style** — courbe cible (ci-dessous) et niveau visé de la fiche, dans les limites réglementaires.
10. **Validation finale** — cohérence électrique, excursion, phase à Fc, headroom, SPL global, style, sécurité
    mécanique, niveaux réglementaires. Toute incohérence ⇒ corriger avant de sortir quoi que ce soit.

## Seuils de limiteur (type « composants »)
`seuil = (dBu cible aux bornes − gain ampli effectif)`, puis conversion dans l'unité du processeur :
- processeur en dBFS : − dBu(0 dBFS) du réglage de sortie (ex. dbx VENU360 réglé +22 : 0 dBFS = +22,1 dBu) ;
- processeur en dBu : la valeur telle quelle ; en V : 0,775·10^(dBu/20).
Gain ampli effectif = gain canal + 6,02 dB en bridge. Puissance de référence : AES si connue (sinon norme signalée).
Pourcentage de la puissance nominale : celui de la config (défaut prudent 75 %, à valider avec Louis).

## Vérification des gains (obligatoire avant de faire confiance à un seuil)
HP débranchés (ou charge factice), limiteurs désactivés le temps du test, sinus dans la bande de la voie à niveau connu
en sortie du processeur, multimètre TRMS aux bornes : gain = 20·log10(V_bornes / V_entrée).

## Courbes cibles par style
- PUNK : médium agressif, kick présent non dominant, voix 1–4 kHz intelligible, grave contrôlé.
- MÉTAL : kick percutant, 200–400 Hz propre, HF clair sans stridence.
- HIP-HOP : sub profond maîtrisé, 80–120 Hz propre, voix 2–5 kHz ultra lisible.
- REGGAE/DUB : sub rond dominant, kick doux, médium chaud, HF non agressif.
- TECHNO : courbe descendante, infra renforcé, kick dominant.
- FREE/TEKNO : impact physique, médium brut, kick agressif.
- NOISE : large dynamique, limiteurs permissifs (dans les limites de sécurité).
Les préférences propres à un système (ex. MA Sono : réglages fixes, voicing bass-heavy) sont dans sa config, champ `preferences`.

## Appliquer les réglages
- Processeur pilotable par le kit (voir `docs/processeurs.md`) : écriture réseau, lecture seule par défaut,
  annonce avant/après/justification, « go » de Louis, `--protection-validee` pour limiteurs/HPF/polarité/mutes
  après accord explicite. Pas max : EQ ±3 dB, délai ±0,5 ms, gain ±3 dB, puis remesurer.
- Processeur non pilotable : **feuille de saisie** — un tableau par sortie, dans l'ordre et avec les noms de menus du
  processeur (manuel du constructeur), valeurs au pas réel de l'appareil ; Louis saisit, relit à voix haute ou envoie
  une photo de l'écran, Claude vérifie, puis on remesure.
- Jamais d'écriture pendant un concert. Tout réglage validé ⇒ `journal/ETAT.md`.

## Format de réponse
Résumé matériel · Hypothèses · Calculs (sortie des scripts) · Paramètres DSP complets (copiables) · Justifications ·
Adaptation au style · Alertes · Recommandations terrain.
