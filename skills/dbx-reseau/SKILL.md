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
  - délais (DelayOut/DelayMid Amount) au nombre entier d'échantillons : 1 échantillon à 96 kHz = 0,0104 ms (1.7 → 1.71 ms).
  Jamais sur un seuil, une attaque ou un gain de sortie.

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
Tout passe par UN seul chemin de contrôle (`tools/dbx_reglage.py`), pour `set`, `regler` et `lot`.
```
dbx_hiqnet.py regler <ip> MEDIUM.eq.2.gain -2dB --config data/configs/<sys>.yaml             # simulation
dbx_hiqnet.py regler <ip> MEDIUM.eq.2.gain -2dB --config data/configs/<sys>.yaml --appliquer
dbx_hiqnet.py set    <ip> "<chemin complet>" "<valeur>" [--appliquer]                            # chemin brut
dbx_hiqnet.py lot    <ip> mon-lot.yaml --appliquer [--protection-validee]                        # plusieurs réglages
```
**Alias** (`regler`, voies lues dans la config) : `VOIE.gain` (crossover), `.phase`, `.polarite`, `.hpf.freq|type`,
`.lpf.freq|type`, `.niveau` / `.mute` (OutputGains), `.delai`, `.delai.actif`, `.eq` (On/Off),
`.eq.<1-8>.<gain|freq|q|type|pente>`, `.limiteur.<seuil|actif|attaque|maintien|relachement|peakstop|overshoot|overeasy|auto>` ;
`ENTREE.eq[.<1-12>.<…>]` (PEQ1) ; `SORTIE<n>.…` ; `SYSTEME.niveau_sortie.<n>`. Avec un alias de voie, le plafond du
seuil est celui de la voie, sans `--voie`.
**Lot** (`mon-lot.yaml`) : `config: data/configs/<sys>.yaml` puis `reglages: [ - ALIAS: valeur , … ]`.
1. Tout est contrôlé d'abord : un seul refus, et rien n'est écrit (pas d'instantané non plus).
2. Instantané complet, puis écriture dans l'ordre, chaque pas relu.
3. Au premier échec : **retour arrière** dans l'ordre inverse (par pas). Un seuil de limiteur abaissé n'est jamais remonté par le retour arrière : il reste plus bas, donc plus protecteur.
Vérifié en vrai le 24/11/2026 : un lot refusé n'écrit rien ; un lot qui échoue (RTARouter « A ») remet les 4 réglages précédents ; un lot conforme puis son lot inverse laissent la machine telle qu'avant.

Règles (ne pas les contourner) :
- **Pas maximum par écriture** : gains et EQ ±3 dB, délai ±0,5 ms, phase ±45°. Au-delà, refus.
  - `--par-pas` découpe l'écart, chaque pas étant écrit puis relu (vérifié en vrai : KICK −12,6 → −18,6 dB en 2 pas, délai 0 → 1 ms, phase 0 → −90°). Au-delà de 2 pas sans remesure (6 dB, 1 ms, 90°), il faut aussi `--protection-validee`.
  - Valeur actuelle illisible (délai dépassé, unité inconnue) : refus, car rien n'est écrit sans état d'avant connu.
  - Bosse (gain > 0) sur un PEQOut : l'ordre PEQ/limiteur est relu en direct dans la topologie avant d'écrire.
  - Alias de voie sur un crossover multivoie (CrossoverIIR à plusieurs bandes) : refusé, car Band_1 ne serait pas la voie visée.
  - Seuil `Limiter<n>` : la voie est déduite de la sortie n dans la config ; un `--voie` contradictoire est refusé.
  - Retour arrière d'un lot : générateur coupé et mutes d'abord, puis le reste dans l'ordre inverse. Il s'arrête au premier écart (code 2 : restaurer l'instantané). Il ne rallume jamais le générateur et ne remonte jamais un seuil.
  - `--pas-libre` lève le pas, seulement si les 6 sorties sont relues mutées (construction de preset).
  - Seuils, fréquences, Q, types : pas de pas maximum (le seuil a son plafond).
- **Protection** (limiteurs, HPF/LPF et leurs types, polarité, mutes, PeakStop, et aussi gains d'entrée, de zone et de sortie, routage InMixer et MidRouter, niveaux analogiques d'appareil) : refusée sans `--protection-validee`.
- **Seuil de limiteur** : `--config` + `--voie` (ou un alias de voie). Au-dessus du plafond : refusé (`--depasser-plafond` seulement sur décision explicite de Louis). La valeur **relue** est aussi comparée au plafond.
- **Niveau de sortie analogique** (`\\Node\Settings\SV\Output<n>Gain`, +4…+22 dBu) : il cale TOUS les plafonds. Refusé tant que la config ne porte pas déjà la nouvelle valeur, confirmée (`processeur_reglage_sortie`), et que les 6 sorties ne sont pas relues mutées. Après l'écriture, les 6 niveaux sont relus, avec un avertissement s'ils diffèrent de la config. `appliquer-plan` relit aussi ces niveaux en direct et refuse s'ils diffèrent de la config.
- **Générateur de signal** (`SignalGenerator`, `SignalGeneratorControl`) : refusé sans `--signal-valide`, et Louis doit avoir confirmé un niveau sûr et que personne n'est exposé. Le niveau est plafonné à −30 dB, et l'allumage est refusé si le niveau réglé est plus haut.
- **Commandes ponctuelles** (`Flatten`, `ClearAll`, `ClearLive` de l'AFS) : seulement à la main, jamais en lot.
- Pour un chemin aussi présent dans la carte YAML, les deux validations s'appliquent (la plus stricte gagne).
- Réglage d'appareil `\\Node` autre que les niveaux d'entrée/sortie : refusé sans `--hors-carte`.
- Si la relecture diffère : « ÉCHEC », code non nul. S'arrêter.

## Lire sans rien toucher : mètres, ordre de la chaîne
```
python tools/metres.py <ip> [--duree 3] [--tous] [--json f.json]     # abonnement aux 66 mètres, lecture seule
dbx_hiqnet.py chaine <ip> [--enregistrer]                           # ordre PEQ / mute / limiteur lu dans la topologie
```
- **Mètres** : seuls Meters123 et MetersABC (entrées 1-3, A-B-C) sont vivants par la console : environ 10 mises à jour par seconde, valeur efficace (sinus −30 dB crête → −33,0 dB). Sorties, Mid, InputMeters, capteurs des limiteurs et du compresseur restent **figés**, même sortie démutée avec signal. Contrôler une sortie ou un limiteur passe donc par la boucle REW, pas par les mètres.
- **Chaîne de sortie** (topologie, POMap + AutoWire, firmware 2.0.5.0) : CrossoverIIR → PEQOut → ZoneGains → OutputGains (mute) → Limiter → DelayOut, même DSP. Les PEQ de voie sont donc AVANT le limiteur : une bosse est limitée. `--enregistrer` écrit `chaine_sortie` dans `data/protocoles/venu360_phase.yaml`, ce qui débloque les bosses d'EQ de voie ; `appliquer-plan` relit la topologie en direct avant d'en écrire une.
- **Générateur** : il entre par les routeurs d'entrée (`InRouter<n>\SV\SignalGeneratorControl` = SignalGeneratorOn, Signal Route All). Il est visible sur Meters123 et MetersABC. Utile pour vérifier une entrée, pas une sortie.

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

## Construire un preset complet depuis un plan (chemin rapide)
```
python tools/preset_auto.py data/configs/<sys>.yaml --style techno            # plan neutre + feuille (lecture seule)
dbx_hiqnet.py appliquer-plan <ip> plans/<sys>-techno-reglages.json --case 27 --nom "CKI 4V Techno" --verifier   # simulation
dbx_hiqnet.py appliquer-plan <ip> plans/<sys>-techno-reglages.json --case 27 --nom "CKI 4V Techno" --verifier     --appliquer --protection-validee [--demuter]
```
`tools/dbx_plan.py` refuse hors ligne un plan BLOQUANT, un autre processeur, un seuil au-dessus du plafond recalculé,
un plan qui ne correspond plus à la config actuelle (il est régénéré et comparé : sorties, filtres, polarité, seuils),
une config hors `data/configs/`, un gain > 0 dB ou un délai hors [0, 30] ms. Seuils arrondis vers le bas.
Plan v2 (`optim_preset`) : `phase_deg` (Band_1_Phase, bornée à −135…0 : −180° = inversion, décision de Louis), `peq` par voie (PEQOut, ≤ 8 bandes, ±12 dB, bosse ≤ +6,
Q 0,3-10) et `peq_entree` (PEQ1, EQ générale) ; toute bosse de voie refusée tant que `data/protocoles/venu360_phase.yaml`
n'établit pas `chaine_sortie: peq_avant_limiteur` (`chaine --enregistrer`, établi le 24/11/2026), et revérifiée en direct. Plan v1 toujours accepté (phase 0, PEQ Off).
Topologie exigée : une sortie = un crossover indépendant (`CrossoverIIR<n>` à une seule bande, `Limiter<n>`,
`DelayOut<n>`). Le gain de voie est écrit dans le crossover (`CrossoverIIR<n>\SV\Band_1_Gain`, choix de Louis :
réglé au même endroit que les filtres) ; OutputGains reste à 0 dB. Les deux sont avant le limiteur. Charger d'abord un preset « 6 × Mono Fullrange » (case 29 sur la machine de Louis) ; sinon refus
avant toute écriture. Puis : instantané, mute relu, protections d'abord (limiteurs, filtres, polarité ; un échec arrête
tout, sorties mutées), délais/gains/EQ à plat, relecture finale ; puis, seulement si tout est conforme : démute des
sorties utilisées (`--demuter`, état relu exigé), stockage (case portant un autre nom : `--ecraser`) + nom relu,
rappel + relecture (`--verifier`). Tout échec après les écritures re-mute les 6 sorties avant de sortir. Pas max par écriture levé pour cette construction
(sorties mutées), journalisé.

## Presets
```
dbx_hiqnet.py presets <ip>
dbx_hiqnet.py rappel <ip> <n> --appliquer --protection-validee     # muter / amplis coupés avant
dbx_hiqnet.py stocker <ip> <n> --appliquer [--nom "CKI 4V Mono"]   # ÉCRASE le preset n ; --nom renomme la case
```
- Un rappel prend de 8 à 18 s et remet les mutes du preset. Les mutes de zone ne sont pas gardés tels qu'au stockage : ZoneGains 3 et 4 sur On au rappel du n° 27, comme au n° 31.
- Les numéros d'instance dépendent de la topologie. En stéréo liée, seules les instances impaires existent et une écriture agit sur les deux sorties. Toujours relancer `topologie` et `instantane` après un rappel.
- **Créer une nouvelle topologie** : partir d'un preset qui a la bonne (les n° 76 à 100 sont les modèles d'usine), régler, puis `stocker` dans une case libre. La commande de reconfiguration directe (`\\Node\Config`) reste interdite tant que son format n'est pas appris et testé.
- RPC destructifs (FactoryRestore, BuildDB, suppression…) : refusés par l'outil.
