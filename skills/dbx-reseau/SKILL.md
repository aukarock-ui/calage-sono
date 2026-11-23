---
name: dbx-reseau
description: Piloter ou explorer en réseau un dbx DriveRack VENU360 ou PA2 (protocole HiQnet Console, TCP 19272) — lecture, cartographie des paramètres, mode apprentissage, écriture sécurisée, rappel de presets.
---

# dbx VENU360 / PA2 en réseau

Outil : `tools/dbx_hiqnet.py` (Python 3.10+, aucune dépendance). Détails protocole : `docs/protocole-dbx.md`.
Simulateur pour tester sans matériel : `python tools/faux_venu360.py` puis viser `127.0.0.1`.

## Mise en réseau
- Réseau ISOLÉ (switch/routeur dédié, Wi-Fi sécurisé sans accès Internet). Le Venu360 est en DHCP par défaut.
- Adresse : Utility → System Info sur la façade, ou `python tools/dbx_hiqnet.py decouvrir`.
- Pare-feu Windows : autoriser TCP/UDP 19272 (et 21 pour l'appli officielle).
- Mot de passe par défaut `administrator` (option `--mdp`).
- Si plusieurs cartes réseau sur le PC : donner l'IP à la main.

## Étape 1 — Lire l'état complet (lecture seule, sans risque)
```
python tools/dbx_hiqnet.py info <ip>
python tools/dbx_hiqnet.py carte <ip> -o journal/instantanes/carte-AAAAMMJJ.json
```
`carte` lit tous les chemins connus (`data/venu360_parametres.yaml`, extraits de l'appli officielle v2.0.7) :
crossover (`CrossoverIIR<k>`), PEQ (`PEQOut<k>`), limiteurs (`Limiter<k>`), délais (`DelayOut<k>`), gains/mutes.
Rappels : `Band_1` = voie la plus AIGUË ; PeakStopPlus est actif par défaut (overshoot 6 dB) — le vérifier.
Comparer ensuite la carte aux valeurs calculées (`calc_systeme.py`) et signaler tout écart AVANT de proposer quoi que ce soit.
Si un chemin attendu manque : `ls <ip> "\\Preset"` ou `arbre` pour voir les instances réellement présentes.

## Étape 2 — Mode apprentissage (trouver le chemin d'un réglage)
1. `instantane <ip> -o journal/instantanes/avant.json`
2. Louis change UN seul paramètre dans l'appli dbx officielle (PC ou tablette), ex. Fc du HPF de la voie 1.
3. `instantane <ip> -o journal/instantanes/apres.json`
4. `diff avant.json apres.json` → le chemin et le format de valeur exacts. Les reporter dans `docs/protocole-dbx.md`.
Variante en direct : `ecouter <ip> "<chemin du module>" --duree 120` pendant que Louis manipule l'appli.
Si `ls` ne suffit pas : capture Wireshark filtre `tcp.port == 19272`, « Follow TCP Stream » (le protocole est en clair).

## Étape 3 — Écrire (seulement après accord de Louis)
```
python tools/dbx_hiqnet.py set <ip> "<chemin>" "<valeur>"              # simulation : affiche actuel / demandé
python tools/dbx_hiqnet.py set <ip> "<chemin>" "<valeur>" --appliquer  # écrit, relit, journalise
```
- Paramètre de protection (Limiter, Threshold, HPF, HPType, Polarity, Mute, PeakStop) : refus sans `--protection-validee`.
- Seuil de limiteur : ajouter `--config data/configs/<système>.yaml --voie <NOM>`. Un seuil au-dessus du plafond calculé est refusé. `--depasser-plafond` n'est permis que sur décision explicite de Louis, et il est journalisé. Même règle dans `dcx2496.py` et `tracks_dsp.py`.
- Valeur hors carte (`venu360_parametres.yaml`) : refusée. Pour un chemin inconnu : `--hors-carte`, après un `get` de confirmation.
- Après l'écriture, la valeur relue doit correspondre à la consigne. Sinon : « ÉCHEC », code non nul, ne pas continuer.
- Instantané automatique et log dans `journal/ecritures.log` à chaque écriture.
- Toujours relire la valeur après écriture ; si la relecture diffère, s'arrêter et le signaler.
- Mémoriser ensuite : `python tools/dbx_hiqnet.py stocker <ip> <n> --appliquer` (JSON-RPC Store, ÉCRASE le preset n ;
  seulement après accord de Louis et avec le bon numéro). Les RPC destructifs (FactoryRestore, BuildDB…) sont refusés.

## Presets
```
python tools/dbx_hiqnet.py presets <ip>
python tools/dbx_hiqnet.py rappel <ip> <n> --appliquer     # muter / amplis coupés avant
```

## Limites connues
- Chemins VENU360 issus du code de l'appli : à confirmer par `carte` à la première connexion (numérotation des
  instances, format d'écriture des valeurs). Écrire une valeur au MÊME format que celui relu par `get`.
- Ne pas laisser l'appli officielle et le script écrire en même temps sur le même paramètre.
