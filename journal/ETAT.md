# ÉTAT DU SYSTÈME — journal vivant (le plus récent en haut)

Règle : toute valeur validée sur le terrain est notée ici avec la date et la mesure qui la justifie.
Claude Code lit ce fichier au début de chaque session et le met à jour à la fin.

## 2026-11-24 (suite 2) : chemin rapide config → processeur, profils de programme, techno « exigeant »

- **Nouveaux outils** :
  - `tools/preset_auto.py` : plan neutre (JSON + feuille `.md`) depuis n'importe quelle config à composants. Il enchaîne calc_systeme, excursion_br (HPF des BR vérifiés sur `hypotheses_caisse`, HPF minimal proposé sinon), reglages_pile et un contrôle de puissance. Le plan est marqué BLOQUANT sur DANGER, excursion ou réglage de sortie non confirmé.
  - `--recaler <config> <mesures.yaml>` : écrit `<config>.recalage.yaml`, que tous les outils lisent via `calc_systeme.charger`. Le délai IR corrige la profondeur, le gain voltmètre corrige `gain_canal_db`, l'impédance corrige l'accord, l'écart de niveau corrige la sensibilité. Chaque recalage est historisé.
  - `tools/dbx_plan.py` + `dbx_hiqnet.py appliquer-plan` : topologie à sorties indépendantes exigée, mute relu, protections d'abord (un échec arrête tout), relecture finale, `--demuter`, stockage + nom relu, `--verifier` (rappel puis relecture). Le plafond est recalculé hors ligne.
- **Profils de programme** (L-Acoustics, diapo p. 34 fournie par Louis) : léger 99 / moyen 102 / lourd 105 / exigeant 108 dB(A). Grave relevé de +2 / +10 / +13 / +16 dB, plateau plat au-dessus de ~1 kHz. Ils remplacent mes courbes approximatives : l'ancienne techno ne relevait le grave que de +6 dB.
- **Techno CKI = profil exigeant**, appliqué sur la vraie machine par `appliquer-plan` : 76/76 écritures conformes, case 27 « CKI 4V Techno » stockée, rappelée, **0 écart**. Sorties 1-4 démutées, 5-6 mutées. Instantané d'avant : `journal/instantanes/20261124-112325-avant-plan.json`.
  - Gains de sortie : SUB 0, KICK −12,6, MEDIUM −16,3, HIGH −19,9 dB. Seuils, temps et délais inchangés.
  - **Puissance** : voie limitante SUB (2 × 21"). Plateau atteignable ≈ 93 dB à 20 m (une pile, champ libre), contre 108 dB(A) pour le profil exigeant, soit **−15 dB**. Réserves inutilisées : KICK 9,6, MEDIUM 13,3, HIGH 8,9 dB. Pour un vrai « exigeant » à 20 m, il faut plus de subs (chaque doublement en couplage ≈ +6 dB) ou un public plus proche. À confirmer par la mesure : la salle et le sol ajoutent du grave.
- **Revue controleur-securite** (11 constats, tous traités sauf un) : liste blanche et gardes du recalage (gain qui relève un plafond → `--protection-validee`, écart > 3 dB → `--forcer-gain`) ; plan régénéré et comparé à la config avant écriture ; filtre absent bloquant sauf « aucun » déclaré ; démute après conformité, relu, puis re-mute sur tout échec ; `--ecraser` pour une case portant un autre nom ; gain ≤ 0 dB et délai 0-30 ms ; seuil arrondi vers le bas ; HPF BR non vérifiable bloquant sauf `--accepter-non-verifie`. Reste : une protection non décrite par la machine n'est contrôlée que par relecture stricte.
  - Conséquence : le plan CKI est désormais **BLOQUANT** tant que le HPF 90 Hz du KICK n'est pas vérifié (accord et volume inconnus). L'écriture de la case 27 s'est faite avant cette garde. **Décision de Louis (24/11/2026) : accepté non vérifié, à vérifier en mesure micro.** Plan régénéré avec `--accepter-non-verifie`. Il est identique à la case 27, cohérent avec la config et dans les plafonds (simulation `appliquer-plan`), donc rien n'a été réécrit.
  - **À la mesure** : à pleine charge KICK, écouter et regarder si la membrane flotte ou si le son se dégrade sous 90 Hz. Mesurer l'impédance (accord), puis noter `accord_hz` dans le fichier de mesures et lancer `--recaler` : le HPF sera alors vérifié par le modèle.
- **Preset complet réécrit par la séquence sécurisée finale** (demande de Louis, 24/11/2026 12:16) : `appliquer-plan … --case 27 --nom "CKI 4V Techno" --verifier --demuter`. 76/76 conformes, démute relu (1-4 Off, 5-6 On), stocké, nom relu, rappel puis relecture : **0 écart**. Relu après coup : preset chargé « CKI 4V Techno », NotModified. Instantané d'avant : `journal/instantanes/20261124-121626-avant-plan.json`.
  - Sorties 5-6 : mutées mais limiteur Off (valeurs du modèle de preset). Si on les utilise un jour, il faudra une voie dans la config et un plan.
- **Questions ouvertes** : chaud = plateau plat dans l'aigu (source) ou léger retrait au-dessus de 4 kHz ? Hypothèses de caisse du KICK (accord, volume) pour vérifier son HPF 90 Hz.







## 2026-11-24 (suite) : méthodes sourcées et réglages calculés pour la CKI

- **Renommage** : `Store [n, nom]` renomme la case → `dbx_hiqnet.py stocker … --nom`. Case 27 = « CKI 4V Mono ».
- **HPF SUB** : `excursion_br.py` (Small 1973, critère Marshall 2016) à 109,5 V (tension du limiteur). LR24 35 Hz garde Xmax pour fb 28-40 Hz et Vb 400-550 L (tension qui amène à Xmax : 112-132 V). Retenu. Accord réel à mesurer (impédance REW).
- **Limiteurs** : table Proel DSO26 (attaque selon le HPF, relâchement 16×) via `tools/reglages_pile.py`.
  - Valeurs écrites (attaque / relâchement) : SUB 16 ms / 34 dB/s ; KICK 8 ms / 68 dB/s ; MEDIUM 2 ms / 270 dB/s ; HIGH 0,5 ms / 360 dB/s (borné). Auto Off, Hold 0.
  - QSC (Neunaber 2004) : thermique en secondes. Impossible avec le limiteur unique du VENU360, d'où le seuil réduit à 75 % et la détection RMS sur le grave.
  - Conversion ms → dB/s = 8,686/τ : hypothèse à vérifier.
- **Bridge** : manuels QSC PL3 (« twice the normal voltage swing ») et RMX (« double the output voltage ») = +6,02 dB. Boyce p. 98 le confirme. À mesurer au voltmètre.
- **Délais** : `reglages_pile.py`. Géométrie de la pile (centres acoustiques supposés : HP640 43,7 cm + moteur) et phase des filtres LR au raccord.
  - Résultat : HIGH 0, MEDIUM 1,23, KICK 1,71 (grille), SUB 4,60 ms.
  - Le raccord SUB/KICK doit 3 ms à la phase des filtres, et seulement 1,4 ms à la géométrie.
  - **À comparer à l'IR** : noter l'écart dans `reglages_calcules.mesure` de la config.
- Outil : arrondi machine accepté sur les délais à ±1 échantillon (96 kHz).
- **Équilibre des gains** (`reglages_pile.py`, à niveau processeur égal) :
  - calcul : sensibilité fiche + puissance par HP + somme cohérente sous 1 kHz (incohérente au-dessus) ;
  - SPL 1 m : SUB 129,8, KICK 135,8, MEDIUM 131,8, HIGH 133,8 dB ;
  - gains de sortie écrits : HIGH −4, MEDIUM −2, KICK −6, SUB 0 dB.
  - Non modélisé : charge des caisses, sol, HP640 (sensibilité du CP650Ti sur TD-460). Voicing bass-heavy non appliqué.
- Case 27 « CKI 4V Mono » : rappel + diff = identique ; nom relu par `stocker --nom`. Stockée sorties mutées.
- Revue contrôleur (--nom, arrondi des délais) : rien de bloquant. Nom validé avant connexion, nom relu et journalisé après Store.

## 2026-11-24 : preset « CKI 4 voies mono » écrit dans le VENU360 (case 27), test de Claude

Système `data/configs/ma-sono-cki.yaml` (photo `data/configs/photos/ma-sono-cki-pile.jpg`). Pile en colonne, sans super-aigus :
- 2 SUB RCF LF21X451 sur PL380 bridge 4 Ω, gain 32 dB ;
- 4 KICK 2×SM118 sur PLX3402 bridge 4 Ω ;
- 2 MEDIUM 2×SM115 sur PLX3002 bridge 8 Ω ;
- 2 HIGH CP650Ti/HP640 sur RMX1450 en parallèle (8 Ω par canal).
Fiches constructeur ajoutées (`docs/references/`, hors git) ; PLX3402 ajouté à `materiel.yaml`.

**Preset :** base = preset 29 (6 sorties mono indépendantes). Sorties : 1 HIGH, 2 MEDIUM, 3 KICK, 4 SUB ; 5-6 mutées et inutilisées. 60/60 écritures relues.

| Sortie | HPF | LPF | Limiteur (calc_systeme) | Détection |
|---|---|---|---|---|
| 1 HIGH | 1,6 kHz LR 24 | Out | −31,1 dB | crête, overshoot 6 dB |
| 2 MEDIUM | 400 Hz LR 24 | 1,6 kHz LR 24 | −26,1 dB | RMS |
| 3 KICK | 90 Hz LR 24 | 400 Hz LR 24 | −26,1 dB | RMS |
| 4 SUB | 35 Hz LR 24 (provisoire) | 90 Hz LR 24 | −23,1 dB | RMS |

Réglages communs :
- délais Off à 0, PEQ de sortie Off, gains 0 dB, OverEasy Off ;
- attaque/relâchement laissés sur Auto (valeurs du preset 29) ;
- tout le traitement d'entrée est Off (AFS, GEQ, PEQ, compresseur, AutoEQ, sous-harmoniques).

**Contrôles :** `stocker 27`, puis `rappel 27`, puis `diff` → identique, sauf l'indicateur PresetIsModified. Le preset est stocké avec les 6 sorties **mutées** (à démuter après vérification).

**Limites / à reprendre :**
- nom de la case 27 = « Total Flat 4VM Li 35 70 160 800 a faire » : le Store garde le nom chargé, et le renommage n'est pas encore connu (à apprendre via l'appli) ;
- HPF SUB 35 Hz provisoire : accord des évents à mesurer (impédance REW) ;
- gains en bridge (+6 dB) à confirmer au voltmètre ; puissances Beyma non AES ;
- délais entre voies à 0 : alignement à faire à la mesure (IR, puis phase) ;
- attaque/relâchement : appliquer « sub lent, grave moyen, aigu rapide » une fois les temps sourcés.

## 2026-11-24 : VENU360 passé en « deux têtes 3 voies » (stéréo)

- Rappel du modèle d'usine 86 « Stereo 3Way Mains », puis les 6 sorties ont été mutées et relues « On ». Stocké en case **27** (qui contenait la copie de test du 31 ; l'original QuiDri reste en 31).
- Sorties : 1 Left High, 2 Right High, 3 Left Mid, 4 Right Mid, 5 Left Low, 6 Right Low. Liaison stéréo : instances impaires seulement (Limiter1 = aigus G+D, Limiter3 = médiums, Limiter5 = graves ; idem PEQOut et DelayOut).
- Valeurs d'usine du modèle :
  - Low : HPF 50 Hz BW 18, LPF 500 Hz LR 48 ;
  - Mid : 500 Hz-2,5 kHz LR 48 ;
  - High : HPF 2,5 kHz LR 48 ;
  - limiteurs Off à 0 dB, délais Off, gains 0 dB ;
  - entrées : AFS, GEQ, sous-harmonique, AutoEQ et compresseur.
- **Rien n'est calé** : aucune config système pour ces têtes. Sorties laissées MUTÉES. Il faut une config (`nouveau-systeme`) pour régler coupures et limiteurs.

## 2026-11-24 (suite) : revue de sécurité appliquée, cycles 4 et 5 sur la machine

Revue `controleur-securite` du pilotage complet ; corrigé :
- mute des sorties **relu** avant toute écriture en lot (sinon rien n'est écrit) ;
- sorties rendues seulement si l'état final est conforme, sinon laissées mutées avec la commande `restaurer` à lancer ;
- `restaurer` valide chaque valeur, refuse un seuil relevé, vérifie l'IP de l'instantané ;
- contrôle des valeurs brutes (seuils, HPF/LPF, délais) après l'aller-retour ;
- LPF/LPType en protection ; carte YAML appliquée en plus de la machine ; `\\Node` non cartographié sous `--hors-carte` ;
- seuil relu comparé au plafond ; « / » et unité de niveau différente refusés ; arrondi des coupures limité à 1 % ;
- `decrire` n'écrit plus l'IP ni de réglage réseau dans `data/venu360_description.json`.

Cycles :
- 4 : 966/967 OK, mais l'indicateur `PresetIsModified` comptait comme un écart. Résultat : sorties laissées mutées (le garde-fou a joué). Exclu du contrôle, puis `restaurer` → 0 écart, sorties remises.
- 5 : **966/967 OK, 0 échec, 4062 refus vérifiés, 0 écart final, 0 dérive brute, sorties remises comme avant.** Les 3 non testés ont une valeur unique (Min = Max).
- Seconde revue `controleur-securite` : pas de P1. Corrigé : Ctrl-C pendant l'autotest → restauration quand même ;
  seuil illisible refusé par `restaurer` ; délais composés (« 4ms/4.51ft/1.37m ») restaurables ; seuil relu au-dessus du plafond → ancien seuil réécrit ;
  démute relu ; toute dérive brute bloque le retour du son ; `\\Node` insensible à la casse.
- 6 : **966/967 OK, 0 échec, 0 écart, 0 dérive, sorties remises.** 127 tests sans matériel.

**Reste :**
- `set` : rien ne vérifie que l'instance du limiteur (Limiter3…) correspond à `--voie` ;
- contrôles « chemin seul » (protection, signal) faits après les premières trames de lecture, pas avant la connexion ;
- pas maximum par écriture (±3 dB, ±0,5 ms) non vérifié par `dbx_hiqnet.py set` ;
- topologie (reconfiguration), AutoEQ/RTA avec micro, générateur sur « go » ;
- réseau du VENU360 non isolé et aucun mot de passe sur la machine (confirmé par Louis) : n'importe quel appareil du Wi-Fi peut la piloter ;
- le preset 27 contient une copie de QuiDri.

## 2026-11-24 : le kit pilote tout le VENU360 (auto-description, autotest, restauration)

**Découverte :** le VENU360 se décrit lui-même (bornes, type, mètre, liste des choix). Nouveau module `tools/dbx_description.py` :
- validation avant envoi d'après la machine (choix exacts, unité de la même famille, plage, mètres refusés) ;
- relecture au demi-pas d'affichage ; arrondis de la machine reconnus et journalisés ;
- table des bornes déclarées fausses (`CORRECTIONS`).
Nouvelles commandes `decrire`, `topologie`, `instantane` complet, `restaurer`, `autotest`.
Protections étendues (gains d'entrée, de zone et de sortie, routage, niveaux d'appareil). Générateur sous `--signal-valide`, plafonné à −30 dB.

**Machine (preset 31 « QuiDri Gab1 Beta Caisson ») :** 60 blocs, 1126 paramètres, 985 réglages écrivables.
Topologie : Mono 3-Way + Mono Sub (sorties 1-4 : High, Mid, Low, Sub) | Mono 2-Way (sorties 5-6 : High, Low).
Description enregistrée dans `data/venu360_description.json`.

**Cycles de test sur la machine (sorties mutées, aucun son) :**
1. 898/930 OK. Trouvés :
   - restauration qui comparait à des valeurs périmées : 6 filtres AFS effacés, puis récupérés par `restaurer` ;
   - pas des fréquences mal compris (« 1kHz » pris pour ±500 Hz) ;
   - Q borné à 16 malgré la déclaration de 128 ;
   - unité de l'AFS (ms mal lu) ;
   - grille des faders.
2. 956/976 OK. Trouvés : loi de fader en bas de course, format « 3m 10s ».
3. **975/976 OK, 0 échec, 0 écart final.** 59 arrondis machine, 4089 valeurs piégées toutes refusées. La seule option refusée l'est par la machine elle-même (routage RTA « A »).
- Stockage testé : `stocker 27`, puis `rappel 27`, puis `diff`. État identique, sauf les mutes de zone 3 et 4 (remis à On par le rappel), réécrits ensuite.
  **Le preset 27 contient désormais une copie de « QuiDri Gab1 Beta Caisson »** (la case était « Empty »).
- 123 tests sans matériel (simulateur qui se décrit et borne comme la machine).

**Reste à faire :**
- reconfiguration directe de la topologie : apprendre le format avec l'appli officielle ;
- AutoEQ et RTA : utilisation réelle avec un micro ;
- générateur : essai avec signal, sur « go » de Louis.

## 2026-11-23 (suite 7) : premier test d'écriture complet sur le vrai VENU360 (10.104.31.113)

Appareil : VENU360 « CKIDriveRack », firmware 2.0.5.0, 96 kHz, aucun mot de passe réglé (Louis, 24/11). Réseau partagé avec le Wi-Fi Internet (à isoler).
Preset chargé : n° 31 « QuiDri Gab1 Beta Caisson » (modifié). Sorties analogiques **+22 dBu sur les 6 sorties, confirmé par lecture**.
Instantanés : `journal/instantanes/carte-test-avant.json` et `carte-test-apres.json` (identiques en fin de test).

**Protocole :** les 6 sorties mutées pendant tout le test, chaque valeur écrite, relue puis restaurée, sens sûr d'abord (seuil plus bas, HPF plus haut).
- **366/366 paramètres** du preset écrits, relus et restaurés (crossover, PEQ, limiteurs, délais, gains). Environ 60 ms par écriture.
- **Rappel de preset (RPC Load)** testé sur 6 topologies (87, 86, 22, 100, 8, 31) : de 8 à 18 s par chargement, écriture OK dans chacune.
- **Réglage d'appareil** `Output1Gain` : écrit et relu (+20, +12), puis remis à +22 dBu.

**Limites constatées (importantes pour les garde-fous) :**
- L'appareil ne renvoie **jamais d'erreur**. Une valeur hors plage est ramenée à la borne, et une valeur invalide est remplacée en silence :
  `Threshold "abc"` donne **0 dB** (limiteur neutralisé) ; `HPType "LR24"`, `"lr 24"` ou `"LR 96"` donnent **BW 6** ; `"-12,5dB"` donne −12,0 dB ; `Output1Gain "5"` donne +20 dBu (un index est accepté).
  La validation côté outil (carte + relecture) est donc la seule barrière.
- Plages confirmées : Threshold −60…0 dB (pas 0,1), Attack 0,1…200 ms, Overshoot 2…6 dB, gain crossover −60…+20 dB,
  HPF 16 Hz…20 kHz (< 16 Hz donne Out), PEQ gain ±20 dB, Q 0,1…16, fréquence 20 Hz…22 kHz (3 chiffres significatifs),
  délai 0…1000 ms (pas 0,01 ms, accepte aussi les unités `m`, `ft` et `s`), gain de sortie −120…+20 dB.
- La numérotation des instances dépend de la topologie : en stéréo liée, seules les instances impaires existent (PEQOut1/3/5),
  et une écriture agit sur les deux sorties. Toujours refaire `carte` après un rappel.
- Juste après un rappel, `carte` peut manquer des valeurs (délai d'attente de 1 s), comme Band_2 du preset 8 : attendre quelques secondes.
- Le rappel d'un preset remet les mutes du preset (ici ZoneGains 3 et 4 sur On dans le n° 31 stocké).

**Défauts de `dbx_hiqnet.py` CORRIGÉS (branche fix/dbx-validation-relecture, relu par `controleur-securite`, 77 tests, vérifié sur l'appareil) :**
1. Faux « ÉCHEC » sur `DelayOut*\Amount` : la relecture `1.5ms/1.69ft/0.51m` n'est pas comparée à la consigne `1.5ms`.
2. `valider_valeur` compare les listes sans tenir compte de la casse : `"lr 24"` passe alors que l'appareil écrit BW 6. La comparaison doit être exacte.
3. Ajouts après relecture : valeur avec espace en début ou fin refusée, « Out » en casse exacte, virgule refusée même hors carte, 1/0 = On/Off seulement pour Mute.
4. Reste à faire : attente ou nouvelle tentative de `carte` après un rappel ; tolérance de relecture relative (0,5 %) sans contrôle d'unité (pas de cas réel trouvé).

**Questions ouvertes :** quel système est branché derrière ce processeur ? `ma-sono-actuel` ne correspond pas au preset 31 (sortie 1 = aigu). Limiteurs non recalculés pour cette raison.

## 2026-11-23 (suite 6) : audit approfondi n° 2 traité, voir `audits/2026-11-23-audit-approfondi-2.md`

**Corrections logicielles, testées sans matériel (49 tests) :**
- **TIPI :** injection CR/LF bloquée avant toute connexion ; liste blanche des méthodes ; relecture stricte.
- **dbx :** `rappel` exige `--protection-validee` ; instantané complet avant `rappel` et `stocker` ; `rpc` en lecture seule.
- **OSM :** le générateur ne s'allume qu'avec un gain imposé (≤ −10 dBFS) ; arguments et grilles de fréquences contrôlés.

**Nouveau garde-fou :** `processeur_reglage_sortie_confirme` vaut false dans les deux configs. Tant que ce champ ne vaut pas true,
`dbx_hiqnet set` refuse d'écrire un seuil de limiteur. Le passer à true seulement après avoir lu `Output1Gain…Output6Gain`
sur le VENU360 (valeur d'usine +12 dBu, configs à +22).

**REW testé en vrai le 23/11/2026** (REW 5.40 beta 121, API 0.9.4, lancé avec `-api`, aucune sortie audio branchée, aucun son joué) :
- `info` et `liste` fonctionnent.
- `mesurer` est refusé sans `--appliquer`.
- L'autotest est OK : l'IR trouve −1,50 ms avec inversion. La phase seule trouve +0,13 ms, ce qui est l'ambiguïté de cycle
  attendue, et l'alerte se déclenche.
- `importer`, `lire` et `ir` fonctionnent sur un WAV synthétique (passe-bande 60 Hz–2 kHz). Les coupures sont bien
  à −3 dB sous le centre. La mesure de test a été supprimée de REW.

**Open Sound Meter testé en vrai le 23/11/2026** (OSM v1.5.2 sur le PC, hôte « AUKA », Remote > Server activé ;
générateur non pilotable et non touché). Tout en lecture seule :
- `ecouter` trouve OSM et sa mesure.
- `lire` récupère 8192 points FFT et 16384 points d'IR.
- `lire --resolution 24`, `moyenne` et `comparer` fonctionnent sur les fichiers lus.
- **Bug trouvé et corrigé :** OSM envoie une cohérence `null` (NaN) là où il n'y a pas de signal, et `tiers_octave` plantait.
  `nettoyer_ft` met la cohérence à 0 (point jugé non fiable) et retire les points sans module ni phase. Test de non-régression ajouté.

**Points matériels (rien à coder) :**
- Le SUB actuel n'est pas validé tant que le HPF réel et l'accord ne sont pas relevés.
- Refonte : l'excursion du SUB atteint Xmax vers 99 V RMS, sous le plafond thermique de 109,5 V. C'est une contrainte
  distincte, à trancher par Louis après mesure. Ne pas compenser par un HPF plus raide : le pic est à 46 Hz, au-dessus de l'accord.

## 2026-11-23 (suite 5) — Ouvrages de Louis lus (Boyce, McCarthy en entier pour le calage)
- **Boyce**, *Introduction to Live Sound Reinforcement* (2014), lu sur tout ce qui touche au calage. Ajouts à `calc_systeme.py` :
  - `--puissance` : puissance ampli requise ;
  - `--cable` : perte du câble HP ;
  - `--renfort` : délai d'un renfort (précédence en option, voir McCarthy plus bas) ;
  - `--dc` : distance critique.
  Les quatre sont testés sur les exemples chiffrés du livre : 1188 W contre 1183 W, 0,73 dB, 112,9 ms contre 113 ms, 2·Dc = 18 m (5 tests pytest de plus). Aucun ne modifie un plafond de limiteur.
- Correctif : la sortie console de `calc_systeme.py` plantait sous Windows (cp1252) sur « Ω » et « ≈ ». Elle passe maintenant en UTF-8.
- `skills/methode-calage` §3, 5, 7, 8, 10 :
  - couverture, couplage, sommation ;
  - renforts et température ;
  - pas de notch en EQ système, au plus six filtres environ ;
  - bridge, détection crête ou RMS, protections d'ampli ;
  - test d'ensemble et sécurité.
  Sources dans `docs/references/README.md`, avec chaque chiffre vérifié dans le texte.
- **McCarthy**, *Sound Systems: Design and Optimization* (2007), PDF complet envoyé par Louis, lu sur les chapitres du calage (ch. 2, 6 subs, 8, 9 vérification, 10 calibration). Il corrige le kit sur trois points :
  1. **Renforts** : `--renfort` aligne en phase au point de raccord par défaut (0 ms de précédence). Les 10 ms de Boyce passent en option (`--precedence 10`), et le programme signale le peigne qu'elles créent.
  2. **EQ** : décision en position ONAX, sur une vue au 1/24 d'octave (nouvelle option `--resolution` dans `osm_client.py` et `mesure_tf.py`). Une moyenne ne réunit que des positions ONAX comparables, et les bords ne servent qu'à l'orientation (≤ 6 dB).
  3. **`plan_3d.py`** : P2 devient l'ONAX (axe de la pile, mi-profondeur), alors qu'il était à mi-chemin du centre, dans la zone de peigne G/D. P3 et P4 deviennent des OFFAX de contrôle.
  Méthode §0 (pré-vérification), §5 (subs, renforts, latence, température en concert) et §7 (règles d'EQ, ordre d'opérations).
  Erreur de calcul relevée dans le livre (p. 434 : −0,7 dB et non −0,4 dB), consignée dans `docs/references/README.md`.
- Correctif console Windows (cp1252) étendu à tous les outils : `plan_3d.py --json` plantait aussi.
- Installateur : copie des skills corrigée (elle créait un sous-dossier périmé quand la cible existait).
- Tests : 39, tous verts.
- Rien n'est publié (Drive, GitHub) avant la validation de Louis. Il reste aussi un commit précédent à pousser une fois l'autorisation « workflow » de GitHub accordée.

## 2026-11-23 (suite 4) — Trois audits externes traités (Gemini, ChatGPT, audit approfondi) — voir `audits/`
- Les garde-fous sont maintenant dans le code (`tools/garde_fous.py`) :
  - **Plafond de limiteur** : écrire un seuil (dbx, DCX, t.racks) exige `--config` et `--voie`. Un seuil au-dessus du plafond de `calc_systeme.py` est refusé ; la dérogation `--depasser-plafond` est journalisée.
  - **Relecture stricte** : un écart entre la valeur relue et la consigne donne un code de sortie non nul.
  - **Valeurs** : plages, enums et booléens stricts ; plus aucun bornage silencieux. Pour dbx, la carte `venu360_parametres.yaml` sert de validateur ; un chemin inconnu est refusé sauf avec `--hors-carte`.
  - **CR/LF** refusés sur le protocole dbx.
- **Bug DCX corrigé** : une valeur sous le minimum était repliée vers le haut. −30 sur le seuil limiteur donnait −18 dB.
- `mesure_tf.py mesurer` : rien n'est émis sans `--appliquer` et `--dev`.
- `calc_systeme.py` :
  - lit `dsp_depart` ;
  - signale un HPF sous l'accord d'évent ;
  - si l'ampli écrête avant la tension HP, le seuil est limité à sa tension d'écrêtage.
- Hypothèse ouverte : 0 dB du limiteur DCX = 0 dBFS = +22 dBu. Le plafond DCX est vérifié en dBFS ; **voltmètre**.
- P2 traités ensuite, à la demande de Louis :
  - `installer.ps1` crée `.venv` et installe les versions figées (`constraints.txt`, Python 3.12+) ;
  - tests pytest (`tests/`) et CI GitHub Actions ;
  - mot de passe dbx dans la variable `DBX_MDP`, jamais affiché ;
  - `mesure_tf.py` : moyennes, cohérence et refus des mesures défectueuses ;
  - rapport calc : section « Limites de ce calcul » ;
  - licence MIT.
- **Pente du HPF bass-reflex** : sources réunies dans `docs/references/` (index public, PDF hors GitHub). Nouvel outil `tools/excursion_br.py`, branché dans `calc_systeme.py`.
  - Modèle : circuit équivalent de Small 1973, avec le HPF BW/LR réel. Validé sur l'exemple publié par Marshall : 6,0 mm calculés pour 5 mm publiés, donc prudent.
  - Règle de pente : ≥ 14 dB à 0,7·fb (≥ 28 dB/oct entre 0,7·fb et fb).
  - **Résultat sur la refonte** :
    - SUB (RCF 21" dans 385 L, accord 34 Hz, 109,5 V visés) : 14,9 mm crête à 46 Hz pour un Xmax de 13,5 mm. Le pic est au-dessus de l'accord, donc c'est le niveau qu'il faut limiter : Xmax est atteint vers 99 V RMS. Le HPF BW24 29 Hz n'atténue que 7,7 dB à 0,7·fb ; un LR48 30 Hz satisfait la règle (17,4 dB).
    - KICK (2×18" dans 313 L, accord 44 Hz) : 4,2 mm pour un Xmax de 5,5 mm, OK.
  - Dépasser Xmax veut dire distorsion, pas forcément casse : la limite mécanique est bien plus haut. C'est une alerte, le plafond du limiteur reste thermique. À trancher par Louis après mesure.
  - Config actuelle : accords inconnus, excursion non calculable. **Mesure d'impédance à faire.**

## 2026-11-23 (suite 3) — Manuels officiels (dossier Téléchargements) recoupés avec le kit
- **VENU360** (manuel 5058681-C) : table 0 dBFS par réglage de sortie (p. 10) = celle du kit, confirmée. Limiteur −60…0,
  OverEasy, Auto, PeakStopPlus (OFF = détection RMS, ON = crête), OVERSHOOT 2–6 dB. La procédure dbx (p. 53-54) règle
  les seuils à l'écrêtage de l'ampli, HP débranchés, overshoot 3.
- **PA2** (manuel FR 5044138-A) : 0 dBFS = +19,9 dBu en sortie (p. 8) ; limiteurs PeakPlus = RMS + pics jusqu'à **+3 dB
  au-dessus du seuil**, le manuel demande un « coussin » de 3 dB (p. 44). Bibliothèque d'amplis : RMX2450 présent,
  PL380 et PLX3002 absents (« Not Listed » ⇒ limiteurs non réglés par le Wizard).
- **Correction de méthode** : `calc_systeme.py` retire désormais l'overshoot du seuil dBFS (dbx). Avec le défaut 6 dB
  et le réglage +22 : SUB −23,1 · KICK −26,1 · MEDIUM −28,1 · HIGH −31,1 · SUPER −30,1 dBFS (brut −17,1/−20,1/−22,1/
  −25,1/−24,1). Nouveau champ de config `processeur_overshoot_db` (à lire par `carte`). L'unité de seuil de la
  bibliothèque prime sur celle de la config (alerte si elles diffèrent).
- **t.racks DSP 206** (manuel Thomann v4 EN/FR) : limiteur « TH −90…+20 dB » (crête) — plage = codage du kit ; mais
  **sortie max annoncée +5 dBu** contre +20 dBu mesurés sur un 408. Contradiction non tranchée → voltmètre. Alerte
  ajoutée si seuil dBu > plus basse pleine échelle connue.
- DCX2496 (QSG) : rien de neuf sur le 0 dB du limiteur. DBX.rar = PA2Setup 1.1.4 (installeur, non exécuté).

## 2026-11-23 (suite 2) — Logiciels DCX2496 du Drive analysés (sans matériel)
- DCX-Remote 1.16/1.16a + aide officielle : liaison 38400 8N1 confirmée. **Liens** : Out Stereo Link recopie
  les réglages (limiteur et délai court compris) sur la sortie couplée de même type, et l'activer écrase les sorties
  couplées. → `dcx2496.py set` refuse d'écrire si un lien est actif (`--lien-accepte`), alerte en cas de propagation,
  et les paramètres `*Link*` deviennent des paramètres de protection. Autotest étendu, OK.
- Limiteur DCX = écrêteur de crêtes (aide officielle) → pas de protection thermique : ajouté à `methode-calage` (§8).
- `methode-calage` §5 mis en cohérence : grossier sur l'IR, fin sur la phase.
- Firmwares 1.16 / 1.18A : chiffrés/compressés, inexploitables (le kit ne flashe pas).
- DBX.rar (123 Mo) : trop gros pour le connecteur Drive (limite 10 Mo) → pas encore analysé.

## 2026-11-23 (suite) — REW intégré + éditeur t.racks recoupé (méthode, sans matériel)
- Consigne de Louis : pas de test en direct pour l'instant, on finit la méthode.
- **`tools/rew_client.py`** : REW piloté par son API REST (5.40 b121, API 0.9.4, doc relevée dans `docs/rew_api_5.40b121.json`).
  Lecture FR/IR, alignement (Align IRs + Align phase), import d'IR, mesure par sweep verrouillée (`--appliquer`, ≤ −10 dBFS,
  arrêt sur écrêtage forcé). `autotest` OK sans son (IR synthétiques importées puis supprimées).
  Les appels `mesurer` (niveau, nom, commande SPL) suivent la doc Swagger mais n'ont pas été exécutés.
- **Règle de méthode ajoutée (skill mesure-acoustique)** : alignement IR d'abord, phase ensuite. Démonstration :
  B = +1,5 ms inversée → phase seule à 300 Hz : +0,13 ms non inversé (faux, un cycle d'écart) ; IR : −1,5 ms inversé (juste).
- **Éditeur t.racks V1.05 décortiqué (statique)** : port 9761 confirmé dans le code, découverte UDP 30303, IP d'usine
  192.168.1.101, codage du gain confirmé par 2 sources, bornes freq/Q/PEQ et 9 types cohérents avec le kit.
  Seuil limiteur en dBu toujours non confirmé. Second protocole RS-232 « Extend Remote Control » (7B 7D…) documenté
  (`docs/protocoles-autres.md`). Nouvelle commande `tracks_dsp.py chercher` (UDP 30303) — écrite, pas encore essayée.

## 2026-11-23 — Kit installé sur le PC de Louis + appli VENU360 Windows analysée
- Copie locale : `C:\Users\aukar\Documents\Optimisation pc claude code\calage-sono` (Google Drive pour ordinateur absent :
  le journal n'est PAS synchronisé tout seul, le renvoyer sur le Drive en fin de session).
- Dépendances installées, autotests OK : `calc_systeme`, `mesure_tf autotest`, `dbx_hiqnet info/carte` contre `faux_venu360`.
- Logiciels déjà présents : Open Sound Meter 1.5.2, REW 5.40 b121, Smaart 9.1.6, VENU360 (Windows), DriveRack PA2 app,
  éditeur t.racks DSP 206 V1.05, Universal Control / Capture 3, pilotes Focusrite. Absents : Wireshark/Npcap, Java, 7-Zip.
  Aucune interface audio pro branchée au moment de l'inventaire (seulement Realtek + Bluetooth).
- **Découverte (appli Windows VENU360, dictionnaire `Venu360Settings`)** : le niveau de sortie analogique est
  `\\Node\Settings\SV\Output<1-6>Gain`, index 0…6 = +4/+8/+12/+14/+17/+20/+22 dBu, **défaut usine 2 = +12 dBu**.
  Réglage d'appareil (hors preset). `dbx_hiqnet.py carte` le lit maintenant (section `\\Node\Settings`).
  Conséquence : si la sortie est restée à +12, les seuils calculés pour +22 (SUB −17,1 …) protègent ~10 dB de trop
  (sans danger, mais on perd la marge) ; à +12 le script donne SUB −7,4 · KICK −10,4 · MEDIUM −12,4 · HIGH −15,4 ·
  SUPER −14,4 dBFS. Le danger est l'inverse : appliquer des seuils « +12 » sur une sortie à +22.
  → **Lire `carte` avant toute écriture de limiteur et caler `processeur_reglage_sortie` sur la valeur lue.**
- 44 dictionnaires de classes DSP (min/max/défauts officiels) rangés dans `data/protocoles/venu360_windows_classes/`.
  Limiteur confirmé identique à l'analyse Android (PeakStopPlus actif par défaut, overshoot 6 dB).

## 2026-11-22 (soir, suite) — Kit généralisé à toute sono
- Le kit n'est plus lié à la MA Sono : un fichier par système (`data/configs/`, modèle `_modele-systeme.yaml`), types
  composants / constructeur / amplifiée / mixte ; bibliothèque de composants partagée (`data/materiel.yaml`).
- Nouveau skill `nouveau-systeme` ; `docs/processeurs.md` (pilotage réseau / protocole à intégrer / feuille de saisie).
- `calc_systeme.py` : unité de seuil par processeur (dBFS, dBu, V), voies constructeur non recalculées.
- `plan_3d.py` : voies, couleurs et procédure tirées de la config (plus de noms SUB/KICK… codés en dur) ;
  micro au sol pour les voies coupées ≤ 500 Hz. Nouveau : générateur de plan 3D + positions micro.

## 2026-11-22 (soir) — Mesure open source + fiche événement
- Moteur de mesure principal : Open Sound Meter (open source). Claude lit magnitude/phase/cohérence/RI en direct
  via `tools/osm_client.py` (protocole lu dans le code source d'OSM, testé contre un simulateur).
- Nouveau skill `fiche-evenement` + modèle `data/evenements/_modele.yaml` (lieu, piles, public, style, réglementation
  102 dB(A)/118 dB(C), positions micro, réseau).

## 2026-11-22 (après-midi) — Appli VENU360 décompilée
- Appli Android harman.hpro.venu360 v2.0.7 (Qt/C++) analysée : carte complète des paramètres → data/venu360_parametres.yaml.
- Nouveaux outils : `dbx_hiqnet.py carte` (lecture de tout l'état connu), `stocker` (JSON-RPC Store), `rpc` (méthodes
  destructives bloquées). Simulateur aligné sur la structure réelle (Mono 5-Way, PEQOut/Limiter/DelayOut 1–6).
- À vérifier à la première connexion : PeakStopPlus (actif par défaut dans l'appli, overshoot 6 dB) sur chaque sortie ;
  config crossover (Mono 5-Way ou 4-Way + Sub) ; numéro d'instance = numéro de sortie ?

## 2026-11-22 — Création du kit (conversation Claude, projet MA Sono)
- Kit créé : méthode, base matériel, calculs, client réseau dbx, mesure double canal.
- Protocole VENU360 identifié : texte « HiQnet Console » TCP 19272, identique à la PA2 (cf. docs/protocole-dbx.md).
- **Correction** : les seuils de limiteur du 05/11 (« seuil = dBu − gain ampli ») omettaient la conversion en dBFS
  (−22,1 dB au réglage +22) et le +6 dB du bridge. Valeurs recalculées par `calc_systeme.py` (config actuelle,
  réglage de sortie +22 supposé) : SUB −17,1 · KICK −20,1 · MEDIUM −22,1 · HIGH −25,1 · SUPER −24,1 dBFS.
  À CONFIRMER : réglage de sortie réel du Venu360, gains ampli au voltmètre.
- Les tensions cibles aux bornes de la refonte (109,5 / 77,5 / 38,7 / 21,9 / 12,2 V) sont confirmées par le script.
- Config actuelle SUPER : 2 CP22 en série par coffret ⇒ cible 24,5 V aux bornes ampli (12,2 V par HP).

## 2026-11-22 — Trois nouveaux processeurs (DCX2496, t.racks DSP 206, Linea ASC-48)
- `tools/dcx2496.py` : RS-232 (null-modem), table de 49 paramètres (dcx-ui, MIT). Lecture complète et écriture relue par dump.
- `tools/tracks_dsp.py` : TCP 9761, d'après la synthèse de protocole de tracks-dsp-controller (vérifiée sur un 408, 206 décodé
  depuis des captures). **Seuils de limiteur en dBu.**
- `tools/linea_tipi.py` : TIPI TCP 51456, couverture partielle (méthodes à sonder). Le crossover et les limiteurs restent dans System Engineer.
- Les trois sont testés sur simulateur, pas encore sur l'appareil.

## Questions ouvertes
- [ ] Longueur et section des câbles HP de chaque voie (pour `--cable`), Re des HP manquants.
- [ ] Réglage « analog output gain » des sorties du Venu360 (+4 … +22) — chemin trouvé (`\\Node\Settings\SV\OutputNGain`), reste à le lire sur la machine (`carte`).
- [ ] Gains réels des amplis en bridge (PL380, PLX3002, RMX2450) — mesure voltmètre.
- [ ] HPF actuel du SUB (obligatoire en bass-reflex) et type du HPF SUPER.
- [ ] Accord réel des évents des caisses actuelles (mesure d'impédance).
- [ ] Norme des 250 W des SM118/SM115 1re gén. (RMS brochure ≠ AES).
- [x] Cartographie des chemins réseau du Venu360 (depuis l'appli) — [ ] confirmation sur la machine (`carte`).
- [ ] Interface audio et micro de mesure disponibles (modèles, calibration) pour OSM ?
- [ ] Specs IMG STA-400D à saisir.
- [ ] DCX2496 : 0 dB du limiteur = +22 dBu en sortie ? (voltmètre, sinus 1 kHz) ; première lecture `lire` à comparer à l'écran.
- [ ] DSP 206 : ID façade, firmware (V0104P ?), PIN éventuel ; **pleine échelle +5 (manuel) ou +20 dBu (408 mesuré) ?** et unité du seuil (voltmètre) ; essayer `chercher` (UDP 30303).
- [ ] VENU360 : OVERSHOOT PeakStopPlus réel de chaque sortie (`carte`) → `processeur_overshoot_db`.
- [ ] REW : premier `mesurer` réel (vérifier nom/niveau appliqués, interface audio en ASIO plutôt que Java).
- [ ] ASC-48 : résultat de `sonder` (méthodes TIPI réellement disponibles).

## Réglages validés sur le terrain
(aucun pour l'instant)
