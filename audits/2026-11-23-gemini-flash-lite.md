<!-- Audit brut de Gemini (gemini-flash-lite-latest, via proxy local), 2026-11-23. NON ENCORE VÉRIFIÉ par Claude : chaque remarque reste à confronter au code avant toute correction. -->

# Audit du 2026-11-23 — Ingénieur système senior (touring)

Version relue : 2026-11-23 (Passe 1 : physique, calculs, méthode)
Périmètre : `calc_systeme.py`, `skills/methode-calage/SKILL.md`, `skills/mesure-acoustique/SKILL.md`, `data/configs/ma-sono-actuel.yaml`

## Remarques

### 1. Incohérence de la puissance de référence pour les boomers 1re génération (SM118 / SM115)
- Fichier : `data/materiel.yaml` (lignes 21 et 35) et `tools/calc_systeme.py` (fonction `puissance_reference`)
- Gravité : erreur de résultat (risque de sous-protection thermique ou de calcul erroné)
- Constat : Les haut-parleurs `BEYMA_SM118_GEN1` et `BEYMA_SM115_GEN1` ont une puissance `puissance_rms_w: 250` mais pas de `puissance_aes_w`. Le champ `a_verifier` signale qu'il s'agit d'une "norme RMS brochure, pas AES". Cependant, le script `calc_systeme.py` accepte cette valeur brute sans appliquer de facteur de conversion ou de pondération thermique (facteur de creête du signal musical vs bruit rose AES), ce qui fausse le calcul du seuil RMS pour ces voies (KICK et MEDIUM).
- Justification : Une puissance RMS constructeur "brochure" (ancienne norme EIA ou continue non normalisée) est généralement mesurée sur un signal sinusoïdal ou un bruit non normalisé, souvent inférieure de 30 à 50 % à une puissance AES (norme EIA RS-426B / AES2-1984). Calculer le seuil de limiteur thermique sur 75 % d'une valeur RMS non AES conduit à un seuil trop bas (sous-exploitation) ou trop haut selon la définition constructeur d'époque, sans compter le vieillissement des suspensions (signalé dans la base).
- Correction proposée : Dans `calc_systeme.py`, si la norme est "RMS brochure", appliquer un coefficient de pondération de sécurité (ex. 0.8) ou exiger explicitement une puissance AES validée par mesure d'impédance et de résistance thermique ($R_e$). Pour la config actuelle, passer par une mesure d'impédance (`tools/rew_client.py`) pour estimer la tenue en puissance réelle des exemplaires.

### 2. Formule du gain en pont (bridge) et application aux tensions cibles
- Fichier : `tools/calc_systeme.py` (lignes 72 et 98) et `skills/methode-calage/SKILL.md` (§8)
- Gravité : danger matériel (risque de surestimation de la tension aux bornes et d'envoi d'un niveau destructeur)
- Constat : Le script calcule le gain effectif en mode bridge en ajoutant `BRIDGE_DB` ($20 \log_{10}(2) \approx +6{,}02\text{ dB}$) au gain du canal, et calcule la tension cible aux bornes $V_{\text{bornes}} = V_{\text{hp}} \times n_s$.
- Justification : Sur un amplificateur en pont (bridge), la tension de sortie est doublée par rapport à un canal simple pour une même tension d'entrée ($+6{,}02\text{ dB}$ en tension, soit $+6\text{ dB}$ de gain de tension). Toutefois, la puissance délivrée sous une charge donnée dépend de la limite d'alimentation de l'ampli (rail voltages) et de son impédance minimale admissible en bridge (souvent doublée : une charge de $8\,\Omega$ en bridge représente $4\,\Omega$ par canal interne). Si l'ampli s'effondre en courant sous la charge bridgée, la tension réelle aux bornes s'effondre par rapport au calcul théorique, faussement rassurant.
- Correction proposée : Rappeler dans `skills/methode-calage/SKILL.md` (§6) que la vérification au voltmètre en bridge avec une charge résistive est impérative avant d'appliquer les seuils, et ajouter une alerte dans `calc_systeme.py` si l'impédance vue par chaque moitié d'ampli en bridge descend sous la recommandation constructeur en mode bridge.

### 3. Omission du gain unitaire de sommation acoustique dans le SPL max théorique
- Fichier : `tools/calc_systeme.py` (lignes 86-88)
- Gravité : amélioration (précision des prédictions physiques)
- Constat : Le script calcule le SPL max cohérent avec `+ 20 * math.log10(n_hp)` et incohérent avec `+ 10 * math.log10(n_hp)`.
- Justification : Le terme $+20 \log_{10}(n)$ s'applique à la sommation en pression (champ libre, sources en cohérence parfaite à basse fréquence quand $d \ll \lambda$), mais ne tient pas compte de la sommation de puissance en champ diffus ou de l'interaction des directivités. Surtout, pour les voies multiples (ex. 4 caisses de KICK), la sommation n'est cohérente que dans la bande où les centres acoustiques sont distants de moins d'une demi-longueur d'onde.
- Correction proposée : Documenter dans la sortie du script que le SPL cohérent est un maximum théorique en demi-espace (plan de sol) valable uniquement dans la bande d'couplage mutuel des évents/membranes.

### 4. Absence de garde-fou sur le filtrage passe-haut (HPF) obligatoire en Bass-Reflex
- Fichier : `data/configs/ma-sono-actuel.yaml` (ligne 60) et `skills/methode-calage/SKILL.md` (§1)
- Gravité : danger matériel (destruction mécanique par excursion excessive sous l'accord)
- Constat : Dans la config `ma-sono-actuel.yaml`, le HPF du SUB est noté `null` ("HPF SUB non relevé : OBLIGATOIRE (bass-reflex)").
- Justification : Un haut-parleur chargé en Bass-Reflex perd toute charge acoustique en dessous de la fréquence d'accord de l'évent ($f_b$). Envoyer de l'énergie non filtrée en infrasons en dessous de $f_b$ conduit à un dépassement catastrophique de l'excursion ($X_{\max}$) menant à la destruction de la bobine ou du spider, même bien en dessous de la puissance thermique nominale.
- Correction proposée : `calc_systeme.py` devrait refuser de calculer ou émettre une **alerte bloquante** si un HP de type bass-reflex (`charge` contient bass-reflex) a un HPF `null` ou réglé en theorie sous l'accord estimé.

### 5. Méthode d'alignement temporel : ordre IR puis phase et risque de cycle entier
- Fichier : `skills/mesure-acoustique/SKILL.md` (§ Alignement à une coupure)
- Gravité : erreur de résultat
- Constat : La méthode indique : « On cale d'abord grossièrement sur les pics d'IR (filtrées autour de Fc), puis on affine à la phase ; si les deux divergent de plus d'un quart de période, on garde l'IR et on vérifie par la somme. »
- Justification : C'est la bonne méthode touring. Aligner uniquement sur la phase à la fréquence de coupure sans regarder l'enveloppe impulsionnelle (IR) expose à une erreur de $2k\pi$ (un ou plusieurs cycles entiers de retard), ce qui détruit la réponse transitoire tout en affichant une courbe de phase apparente "correcte" au point de mesure unique.
- Correction proposée : Conserver la règle telle quelle, c'est un excellent rappel méthodologique validé par les tests unitaires de REW/OSM. Aucune modification de code requise, valider comme bonne pratique.

---

## Avis général
Le kit de calage présente une rigueur physique et mathématique remarquable, particulièrement sur la chaîne de conversion d'unités ($dBu \leftrightarrow dBFS \leftrightarrow \text{Tension}$), la prise en compte des gains de bridge ($+6{,}02\text{ dB}$) et des coussin d'overshoot des processeurs dbx (PeakStopPlus). Les garde-fous sur la lecture seule et la nécessité de valider les gains réels au voltmètre sont parfaitement adaptés aux exigences du terrain. Les points de vigilance résident principalement dans l'application rigoureuse des limites mécaniques (HPF en bass-reflex) et la qualification des puissances constructeurs non AES.
---

# Audit du 2026-11-23 — Ingénieur système son senior (PA/Touring) & Relecteur Python

Version relue : 2026-11-23 (Passe 2 : sécurité des outils de pilotage)
Périmètre : outils de pilotage réseau et série (`tools/dbx_hiqnet.py`, `tools/tracks_dsp.py`, `tools/dcx2496.py`, `tools/rew_client.py`)

## Remarques

### 1. Absence de vérification des plages de fréquences PEQ et Crossover (risque d'instabilité DSP)
- Fichier : `tools/dbx_hiqnet.py:460` (commande `set`)
- Gravité : danger matériel
- Constat : L'outil `dbx_hiqnet.py` accepte et envoie n'importe quelle chaîne de valeur sans vérifier si elle respecte les limites physiques du processeur (par exemple, un filtre passe-haut de caisse de basses fixé à 5 Hz ou un gain PEQ à +20 dB en régime permanent).
- Justification : D'après `data/venu360_parametres.yaml`, les fréquences sont limitées (ex: 20 Hz – 22 kHz pour les PEQ). Forcer des valeurs hors limites peut saturer les filtres IIR internes du DSP (overflow en virgule fixe/flottante) et générer des transitoires destructifs pour les moteurs d'aigus.
- Correction proposée : Ajouter une fonction de borne (`valider_plage(chemin, valeur)`) avant l'envoi, s'appuyant sur les limites extraites de `data/venu360_parametres.yaml`.

### 2. Absence de relecture après écriture bloquante dans le flux asynchrone (VENU360)
- Fichier : `tools/dbx_hiqnet.py:480` (méthode `set`)
- Gravité : erreur de résultat
- Constat : L'outil écrit une valeur via `d.set(chemin, valeur)`, affiche la réponse brute `setr` reçue de l'appareil, mais ne vérifie pas par une lecture `get` que la valeur effective appliquée par le DSP correspond bien à la demande (arrondi de pas, rejet de format).
- Justification : Le protocole HiQnet renvoie un `setr` contenant la valeur que l'appareil *estime* avoir appliquée, mais en tournées, un décalage d'arrondi ou un format d'unité non reconnu (ex: `"90Hz"` au lieu de `"90.0 Hz"`) peut laisser le processeur dans un état non voulu sans que le script ne lève d'exception.
- Correction proposée : Forcer un `d.get(chemin)` immédiatement après le `set`, comparer la valeur lue à la valeur demandée à la décimale près, et lever une `ValueError` en cas de divergence.

### 3. Gestion incorrecte des arguments négatifs et des options mêlées (`tracks_dsp.py`)
- Fichier : `tools/tracks_dsp.py:350` (fonction `main`)
- Gravité : erreur de résultat
- Constat : Le script utilise `ap.parse_intermixed_args(argv)` avec un hack de remplacement de préfixe (`\x00`), mais le parsing des valeurs négatives (ex: gains en dB à `-3.0`) échoue ou interprète mal les arguments positionnels s'ils sont placés avant les options.
- Justification : En ligne de commande, un argument comme `-3.0` est systématiquement pris pour une option par argparse s'il n'est pas précédé d'un double tiret `--` ou si le parseur n'est pas configuré avec `nargs`. Cela bloque l'écriture rapide de paramètres négatifs en régie.
- Correction proposée : Utiliser systématiquement `parse_intermixed_args()` sans hack artisanal, ou exiger un séparateur `--` avant les paramètres de commande, ou valider que les arguments numériques négatifs sont explicitement acceptés par le type `float`.

### 4. Absence de validation des seuils de limiteur par rapport aux spécifications amplis/processeur (`tracks_dsp.py`)
- Fichier : `tools/tracks_dsp.py:270` (fonction `construire_ecriture`)
- Gravité : danger matériel
- Constat : L'écriture d'un seuil de limiteur (`limiteur`) accepte toute valeur de dBu dans la plage logicielle (`-90` à `+20` dBu) sans la croiser avec la formule de `calc_systeme.py` (75% de la puissance nominale et gain de l'ampli).
- Justification : D'après les notes du kit et le manuel Thomann v4, la pleine échelle du t.racks DSP 206 est incertaine (+5 dBu ou +20 dBu selon source). Envoyer un seuil trop élevé sans vérification thermique préalable peut caler le limiteur au-dessus du seuil de destruction du HP.
- Correction proposée : Importer ou appeler une vérification de `calc_systeme.py` dans `tracks_dsp.py` pour refuser tout seuil de limiteur supérieur à la tension maximale admissible calculée pour le transducteur.

### 5. État partiel en cas d'interruption réseau sur le dump complet (DCX2496)
- Fichier : `tools/dcx2496.py:220` (méthode `dump`)
- Gravité : erreur de résultat
- Constat : Le téléchargement de l'état complet du DCX2496 se fait en deux parties successives (1015 et 911 octets). Si une trame est corrompue ou perdue en cours de route, la boucle `for part in (0, 1):` lève une exception, mais laisse le port série ouvert et le processeur potentiellement bloqué en mode transmission (`3F 0C 00`).
- Justification : Le protocole Behringer exige de quitter explicitement le mode transmission. Laisser la session ouverte dans un état de moitié de dump empêche toute reconnexion propre sans redémarrer physiquement le DCX.
- Correction proposée : Encapsuler la séquence de dump dans un bloc `try...finally` qui envoie une trame de sortie de mode transmission ou ferme proprement le descripteur `pyserial`.

### 6. Absence de borne sur le délai maximum dans le client REW
- Fichier : `tools/rew_client.py:120` (méthode `_aligner`)
- Gravité : amélioration
- Constat : Les paramètres `max-negative-delay` et `max-positive-delay` sont fixés par défaut à `plage_ms` (10 ms), mais aucune vérification stricte n'empêche d'injecter des valeurs délirantes (ex: 500 ms) qui faussent l'outil d'alignement par impulsion de REW.
- Justification : En sonorisation, un délai d'alignement dépasse rarement 50 ms (soit ~17 m). Un délai trop large dans REW autorise l'alignement sur une fausse réflexion tardive (écho/murs) plutôt que sur le premier front d'onde.
- Correction proposée : Plafonner `plage_ms` à un maximum absolu de 50,0 ms dans l'interface CLI de `rew_client.py`.

## Avis général
Le kit présente une architecture de sécurité rigoureuse (principe de lecture seule par défaut, obligation du triptyque `--appliquer`, instantanés automatiques et journalisation dans `journal/ecritures.log`). Les garde-fous interdisant les écritures sans validation explicite (`--protection-validee`) sont bien en place. 

Cependant, la robustesse du code Python face à des erreurs de saisie utilisateur (valeurs hors limites physiques du DSP) et la gestion des exceptions réseau/série (qui pourraient laisser un processeur dans un état transitoire ou bloqué) doivent être renforcées avant les premiers tests grandeur nature sur site.