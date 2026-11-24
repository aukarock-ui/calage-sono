---
name: dbx-reseau
description: Piloter ou explorer en réseau un dbx DriveRack VENU360 ou PA2 (protocole HiQnet Console, TCP 19272) — auto-description de la machine, lecture complète, écriture sécurisée, autotest, restauration, presets, topologie.
---

# dbx VENU360 / PA2 en réseau

Outils : `tools/dbx_hiqnet.py` (commandes) et `tools/dbx_description.py` (auto-description, garde-fous, autotest).
Détails protocole : `docs/protocole-dbx.md`. Simulateur sans matériel : `python tools/faux_venu360.py`, puis viser `127.0.0.1`.
Toujours `.venv\Scripts\python.exe` quand `.venv` existe.

## Mise en réseau
- Réseau ISOLÉ (switch ou routeur dédié, Wi-Fi sécurisé sans accès Internet). Le VENU360 est en DHCP par défaut.
- Adresse : Utility → System Info sur la façade, ou `dbx_hiqnet.py decouvrir`. S'il y a plusieurs cartes réseau, donner l'IP à la main.
- Pare-feu Windows : autoriser TCP/UDP 19272.
- Mot de passe : variable d'environnement `DBX_MDP`. À défaut, le mot de passe d'usine est utilisé avec un avertissement. Il n'est jamais affiché.

## Principe : la machine se décrit elle-même (constaté le 24/11/2026, firmware 2.0.5.0)
`ls` sur un paramètre donne sa valeur, ses bornes, son type, s'il s'agit d'un mètre (`Sensor`) et la liste exacte des choix (`\EN`).
L'outil s'en sert pour **valider avant d'envoyer** et **relire après écriture**.
La carte `data/venu360_parametres.yaml` ne sert plus que de repli (simulateur, firmware qui ne se décrit pas).
**La machine ne renvoie jamais d'erreur** : une valeur hors plage est bornée, et une valeur inconnue est remplacée par le premier choix (« LR24 » donne BW 6, « abc » sur un seuil donne 0 dB). La validation de l'outil est donc la seule barrière.

Règles appliquées par l'outil (ne pas les contourner) :
- **Choix** : libellé exact, majuscules et espaces compris (`LR 24`).
- **Nombre** : point décimal, unité de la même famille que l'affichage (`-12.5dB`, `1680Hz`, `1.5ms`, `10s`, `4:1`).
  Sans unité, c'est refusé : un délai « 1.5 » serait lu en **secondes**. « m » veut dire minutes (AFS), pas mètres.
- **Hors plage** : refusé avant envoi.
- **Mètres** (`Sensor`), données texte (courbes AutoEQ, RTA), paramètres inactifs : écriture refusée.
- **Corrections** : quand la machine déclare une borne qu'elle ne respecte pas, la valeur constatée prime (table `CORRECTIONS` dans `dbx_description.py`). Exemple : Q des PEQ d'entrée et de l'AutoEQ, déclaré jusqu'à 128 mais borné à 16.
- **Relecture** : la valeur relue doit être à moins d'un demi-pas d'affichage. Les fréquences s'affichent avec 3 chiffres significatifs.
  Un « arrondi machine » est accepté et journalisé dans deux cas seulement :
  - grille interne de moins de 2,5 % sur fréquence, pente, Q, pourcentage ou rapport (Release 218 donne 220 dB/s) ;
  - gains de mixeur InMixer et MidRouter à 0,25 dB près (1 dB sous −60 dB).
  Jamais sur un seuil, un temps ou un gain de sortie.

## Étape 0 — Décrire et situer (lecture seule)
```
dbx_hiqnet.py info <ip>
dbx_hiqnet.py topologie <ip>          # étages de traitement chargés (ex. Mono 3-Way + Sub | Mono 2-Way) et rôle des sorties
dbx_hiqnet.py decrire <ip>            # data/venu360_description.json : environ 1100 paramètres, ~1 min
dbx_hiqnet.py instantane <ip> -o journal/instantanes/<nom>.json   # tous les réglages (entrées, AFS, GEQ, PEQ, AutoEQ, compresseur, sorties…)
```
`topologie` dit quelle sortie porte quelle voie (High, Mid, Low, Sub). Il faut la vérifier avant de comparer aux plafonds de `calc_systeme.py`.

## Étape 1 — Mode apprentissage (trouver un chemin inconnu)
`instantane` avant, puis Louis change UN paramètre dans l'appli officielle, puis `instantane` après, puis `diff avant.json apres.json`.
En direct : `ecouter <ip> "<chemin du bloc>" --duree 120`. Reporter dans `docs/protocole-dbx.md`.

## Étape 2 — Écrire (seulement après accord de Louis)
```
dbx_hiqnet.py set <ip> "<chemin>" "<valeur>"              # simulation : description, actuel, demandé
dbx_hiqnet.py set <ip> "<chemin>" "<valeur>" --appliquer  # écrit, relit, journalise
```
- **Protection** (limiteurs, HPF/LPF et leurs types, polarité, mutes, PeakStop, et aussi gains d'entrée, de zone et de sortie, routage InMixer et MidRouter, niveaux analogiques d'appareil) : refusée sans `--protection-validee`.
- **Seuil de limiteur** : `--config data/configs/<système>.yaml --voie <NOM>`. Un seuil au-dessus du plafond calculé est refusé (`--depasser-plafond` seulement sur décision explicite de Louis).
- **Générateur de signal** (`SignalGenerator`, `SignalGeneratorControl`) : refusé sans `--signal-valide`, et Louis doit avoir confirmé un niveau sûr et que personne n'est exposé. Le niveau est plafonné à −30 dB, et l'allumage est refusé si le niveau réglé est plus haut.
- **Commandes ponctuelles** (`Flatten`, `ClearAll`, `ClearLive` de l'AFS) : seulement à la main, jamais en lot.
- Pas maximum par écriture en calage : EQ ±3 dB, délai ±0,5 ms, gain ±3 dB, puis remesurer.
- Pour un chemin aussi présent dans la carte YAML, les deux validations s'appliquent (la plus stricte gagne).
- Réglage d'appareil `\\Node` autre que les niveaux d'entrée/sortie : refusé sans `--hors-carte`.
- Seuil : la valeur **relue** est elle aussi comparée au plafond.
- Si la relecture diffère : « ÉCHEC », code non nul. S'arrêter.
- Le pas maximum par écriture (±3 dB, ±0,5 ms) n'est PAS encore vérifié par `set` : le respecter à la main.

## Étape 3 — Restaurer un état complet
```
dbx_hiqnet.py restaurer <ip> journal/instantanes/<nom>.json --appliquer --protection-validee
```
- Refusée si l'instantané a été pris sur une autre IP.
- Les 6 sorties sont mutées, et le mute est **relu**. Si une sortie ne se mute pas, rien n'est écrit.
- Chaque valeur passe par la validation machine avant d'être envoyée.
- Un seuil de limiteur plus haut que l'actuel est **refusé** (`--depasser-plafond` sur décision explicite de Louis).
- Contrôle final. Les mutes ne sont rendus que si tout est relu à l'identique, sans refus ; sinon les sorties **restent mutées**.
- Les réglages d'appareil (`\\Node`) et les indicateurs d'état (PresetIsModified) ne sont pas touchés.
Elle sert aussi à récupérer les filtres AFS appris si un réglage AFS les a effacés (MaxFixedFilters, FilterMode).

## Étape 4 — Autotest (vérifier que l'outil maîtrise toute la machine)
```
dbx_hiqnet.py autotest <ip> --appliquer --protection-validee [--blocs GEQ1,Afs1,Limiter]
```
1. Instantané complet, puis mute des 6 sorties.
2. Pour chaque réglage écrivable : écriture d'une valeur proche, relecture, remise.
3. Pour chaque réglage, les valeurs piégées (casse, virgule, sans unité, hors plage) doivent être refusées par l'outil, sans rien envoyer.
4. Restauration, puis contrôle d'écart final = 0. Les valeurs brutes des seuils, HPF/LPF et délais sont aussi comparées (un seuil relevé = échec).
5. Les sorties ne sont rendues que si tout est conforme ; sinon elles restent mutées et l'outil affiche la commande `restaurer` à lancer.
Rapport dans `journal/autotest-*.json` (pas dans git).
Le générateur n'est jamais touché. Aucun son n'est émis.
Résultat de référence (24/11/2026, preset 31, après revue de sécurité) : 967 réglages testés, 966 OK (59 arrondis machine), 1 option refusée par la machine (routage RTA « A »), 4062 refus vérifiés, 0 écart final, 0 dérive brute, sorties remises.
Non testés : 3 réglages à valeur unique (Min = Max : AFS MaxTotalFilters, type de compresseur « dbx 1066 »). Les niveaux d'appareil `\\Node` sont exclus de l'autotest.

## Presets
```
dbx_hiqnet.py presets <ip>
dbx_hiqnet.py rappel <ip> <n> --appliquer --protection-validee     # muter / amplis coupés avant
dbx_hiqnet.py stocker <ip> <n> --appliquer                          # ÉCRASE le preset n (accord de Louis, bon numéro)
```
- Un rappel prend de 8 à 18 s et remet les mutes du preset. Les mutes de zone ne sont pas gardés tels qu'au stockage : ZoneGains 3 et 4 sur On au rappel du n° 27, comme au n° 31.
- Les numéros d'instance dépendent de la topologie. En stéréo liée, seules les instances impaires existent et une écriture agit sur les deux sorties. Toujours relancer `topologie` et `instantane` après un rappel.
- **Créer une nouvelle topologie** : partir d'un preset qui a la bonne (les n° 76 à 100 sont les modèles d'usine), régler, puis `stocker` dans une case libre. La commande de reconfiguration directe (`\\Node\Config`) reste interdite tant que son format n'est pas appris et testé.
- RPC destructifs (FactoryRestore, BuildDB, suppression…) : refusés par l'outil.
