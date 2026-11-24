---
name: venu360-pilotage
description: Régler N'IMPORTE QUEL paramètre d'un dbx VENU360 en réseau (par nom de voie, en lot avec retour arrière, pas maximum appliqué), lire ses mètres et l'ordre de sa chaîne de sortie, savoir quel outil et quelle règle s'appliquent à chaque bloc. Catalogue des blocs testés sur la vraie machine.
---

# Piloter tout le VENU360

Complète `dbx-reseau` (protocole, autotest, restauration, presets). Ici, on choisit le bon outil pour chaque geste, et
on connaît les règles qui s'appliquent à chaque bloc. Toujours `.venv\Scripts\python.exe`, toujours une simulation
(sans `--appliquer`) montrée à Louis avant l'écriture, puis son « go ».

## Quel outil pour quel geste
| Geste | Commande | Écrit ? |
|---|---|---|
| Lire un paramètre, un bloc | `dbx_hiqnet.py get / ls <ip> "<chemin>"` | non |
| Tout lire (état complet) | `dbx_hiqnet.py instantane <ip>` | non |
| Mètres (entrées vivantes) | `tools/metres.py <ip> [--duree 3] [--tous]` | non |
| Ordre PEQ / mute / limiteur | `dbx_hiqnet.py chaine <ip> [--enregistrer]` | fichier local seulement |
| Topologie (rôle des sorties) | `dbx_hiqnet.py topologie <ip>` | non |
| Retoucher un réglage par voie | `dbx_hiqnet.py regler <ip> VOIE.bloc.param valeur --config <sys>` | oui, par pas |
| Retoucher un chemin brut | `dbx_hiqnet.py set <ip> "<chemin>" valeur` | oui, par pas |
| Plusieurs réglages d'un coup | `dbx_hiqnet.py lot <ip> lot.yaml` | oui, retour arrière auto |
| Construire tout un preset | `preset_auto.py` puis `dbx_hiqnet.py appliquer-plan` | oui, sorties mutées |
| Optimiser à la mesure | `optim_preset.py` (skill `mesure-acoustique`) | oui, par pas bornés |
| Revenir à un état | `dbx_hiqnet.py restaurer <ip> <instantane.json>` | oui, sorties mutées |
| Rappeler / stocker un preset | `dbx_hiqnet.py rappel / stocker <ip> <n>` | oui |

Alias de `regler` et format d'un lot : skill `dbx-reseau`, étape 2. Exemples :
```
dbx_hiqnet.py regler <ip> SUB.delai 4.5ms --config data/configs/ma-sono-cki.yaml --par-pas
dbx_hiqnet.py regler <ip> HIGH.limiteur.seuil -32.0dB --config data/configs/ma-sono-cki.yaml --protection-validee
dbx_hiqnet.py regler <ip> ENTREE.eq.3.gain -2dB --config data/configs/ma-sono-cki.yaml
```

## Règles appliquées par l'outil, par classe de paramètre
- **Réglage** (EQ, délai, AFS, GEQ, AutoEQ, compresseur…) : validation machine (bornes, choix exacts, unité), puis relecture. Pas maximum : gains et EQ ±3 dB, délai ±0,5 ms, phase ±45° (`--par-pas` pour découper ; `--pas-libre` seulement 6 sorties relues mutées).
- **Protection** (limiteur, filtres et leurs types, polarité, mutes, PeakStop, gains d'entrée, de zone et de sortie, routage) : `--protection-validee` après accord explicite de Louis. Seuil : plafond calc_systeme (config + voie), consigne et valeur relue. Jamais au-dessus.
- **Système** (`\\Node\Settings`) : niveaux analogiques d'entrée et de sortie seulement. Un niveau de sortie n'est écrit que si la config porte déjà la nouvelle valeur, car il cale tous les plafonds. Le reste exige `--hors-carte`. Mot de passe, reboot, commande de config : interdits.
- **Signal** (générateur, `SignalGeneratorControl`) : `--signal-valide` après confirmation de Louis (niveau sûr, personne exposé), amplitude ≤ −30 dB.
- **Commande** ponctuelle (Flatten, ClearAll, ClearLive) : à la main seulement.
- **Mètre** et **texte** (courbes AutoEQ, RTA) : lecture seule.

## Ce que la machine dit d'elle-même (firmware 2.0.5.0, 24/11/2026)
- **Chaîne de sortie** : CrossoverIIR → PEQOut → ZoneGains → OutputGains (mute) → Limiter → DelayOut, sur le même DSP. Une bosse de PEQ de voie et un gain de voie passent donc par le limiteur. Le mute est en amont du limiteur.
- **Mètres** : seuls Meters123 et MetersABC (entrées) bougent par la console. Les mètres de sortie, ceux du milieu et les capteurs du limiteur restent figés. On ne vérifie donc pas une sortie au mètre : c'est la boucle REW.
- **Générateur** : il entre par `InRouter<n>\SV\SignalGeneratorControl` (SignalGeneratorOn) et se voit sur Meters123 et MetersABC (sinus −30 dB crête → −33,0 dB efficace). Il coupe en premier, et se relit « Off », à la fin de toute utilisation.
- **RTARouter `A`** : la machine rend le paramètre illisible. On revient à « RTA Mic ».
- **PEQ d'entrée (PEQ1)** : sur la machine de Louis, elle est Off mais garde des bandes d'un ancien preset (bande 1 à +10 dB). Avant de la passer On : mettre les bandes à plat (`optim_preset` le fait), jamais l'activer telle quelle.

## Catalogue des blocs
Généré par `python tools/catalogue_venu360.py --autotest journal/autotest-complet-6.json`. Description prise sur le
preset QuiDri ; les blocs d'entrée varient selon le preset (CKI : Compressor1, SubharmonicSynth…), il faut alors relancer `decrire`.
Colonne « en vrai » : écrit puis relu par l'autotest 6 (24/11/2026). Les mutes sont exclus de l'autotest, et gérés à part.
Le générateur a été testé à la main le même jour.

| Bloc | Paramètres | Classes | Pas max | Écrit + relu en vrai (autotest) | Exemple |
|---|---|---|---|---|---|
| Afs<n> | 120 | réglage 114, commande 4, mètre 2 | ±3 dB | 82/120 | `AFS` |
| AutoEQ<n> | 248 | réglage 240, texte 4, protection 2, commande 2 | ±3 dB | 242/248 | `AutoEQ` |
| CompressorMid<n> | 14 | réglage 9, mètre 3, protection 2 | ±3 dB | 10/14 | `Compressor` |
| CrossoverIIR<n> | 42 | protection 30, réglage 12 | ±3 dB, ±45 deg | 42/42 | `Band_1_HPFrequency` |
| DelayMid<n> | 2 | réglage 2 | ±0.5 ms | 2/2 | `Delay` |
| DelayOut<n> | 12 | réglage 12 | ±0.5 ms | 12/12 | `Delay` |
| GEQ<n> | 66 | réglage 66 | — | 66/66 | `GraphicEQ` |
| InMixer<n> | 51 | protection 48, signal 3 | ±3 dB | 48/51 | `SignalGeneratorControl` |
| InputGains | 16 | protection 16 | ±3 dB | 16/16 | `Channel_1_Gain` |
| InputMeters | 14 | mètre 14 | — | 0/14 | `Channel_1_Clip` |
| Limiter<n> | 72 | protection 54, mètre 18 | — | 54/72 | `Limiter` |
| Meters<n> | 6 | mètre 6 | — | 0/6 | `Channel_1_Clip` |
| MetersABC | 6 | mètre 6 | — | 0/6 | `Channel_1_Clip` |
| MidMeters | 12 | mètre 12 | — | 0/12 | `Channel_1_Clip` |
| MidRouter<n> | 6 | protection 4, signal 2 | ±3 dB | 4/6 | `SignalGeneratorControl` |
| Node\Settings | 24 | système 21, mètre 3 | — | 0/24 | `LoadedPresetID` |
| OutputGains | 12 | protection 12 | ±3 dB | 6/12 | `Channel_1_Gain` |
| OutputMeters | 6 | mètre 6 | — | 0/6 | `Channel_1_Level` |
| PEQ<n> | 124 | réglage 122, commande 2 | ±3 dB | 122/124 | `ParametricEQ` |
| PEQOut<n> | 252 | réglage 246, commande 6 | ±3 dB | 246/252 | `ParametricEQ` |
| RTA | 3 | réglage 2, mètre 1 | ±3 dB | 2/3 | `Rate` |
| RTARouter | 1 | réglage 1 | — | 0/1 | `RouteSelect` |
| SignalGenerator | 5 | signal 5 | — | 0/5 (à la main : 5/5) | `Signal Generator` |
| ZoneGains | 12 | protection 12 | ±3 dB | 12/12 | `Channel_1_Gain` |

Essais du 24/11/2026 sur la vraie machine (amplis débranchés, sorties mutées), tous remis à l'état d'avant :
`regler` KICK.gain par pas (2 × 3 dB), HIGH.delai 0 → 1 ms (2 pas), MEDIUM.phase 0 → −90° (2 pas), HIGH.limiteur.seuil,
MEDIUM.eq.2.gain ; `lot` refusé (seuil au-dessus du plafond → rien écrit), `lot` en échec (retour arrière de 4 réglages),
`lot` conforme et son lot inverse ; SYSTEME.niveau_sortie refusé (config +22 dBu) ; `chaine` = peq_avant_limiteur.

## Ce qui reste à Louis
- Brancher la boucle REW (sortie → interface) pour mesurer la phase (`banc_electrique.py`).
- Mesurer au micro (`optim_preset.py` en vrai), écouter, décider de stocker.
- Brancher les amplis et démuter (`appliquer-plan --demuter` ou à la main).
- Changer le mot de passe d'usine si le réseau n'est pas isolé.
