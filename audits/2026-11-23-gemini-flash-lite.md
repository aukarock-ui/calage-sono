# Audit du 2026-11-23 — Gemini (gemini-flash-lite-latest), deux passages, vérifié par Claude

Gemini a fait deux passages (méthode et calculs, puis sécurité des outils). Flash et Pro n'étaient pas disponibles
(quota gratuit ou surcharge). Ses remarques brutes suivent, après le tableau de vérification fait par Claude dans le code.

## Vérification (Claude, 2026-11-23)
| Remarque de Gemini | Verdict | Suite |
|---|---|---|
| SUB bass-reflex avec HPF `null` : pas d'alerte bloquante | **juste** | `calc_systeme.py` sort maintenant « DANGER : voie bass-reflex sans HPF » |
| `dbx_hiqnet set` : pas de relecture après écriture | **faux** : la valeur est relue | elle n'était pas comparée → avertissement ajouté et ligne `ECART` dans le journal si la valeur relue diffère (testé sur le simulateur) |
| `dbx_hiqnet set` : pas de bornes de plage | partiel | l'appareil borne lui-même ; l'écart est désormais signalé. Bornes côté kit (depuis `venu360_parametres.yaml`) : amélioration possible |
| `tracks_dsp` : pas de bornes gain/délai/seuil | **faux** | bornes présentes (`raise ValueError`, l. 69-85) |
| `tracks_dsp` : pas de relecture | **faux** | config relue et comparée (l. 356-363) |
| `tracks_dsp` : seuil non croisé avec `calc_systeme` | amélioration valable | à faire : refuser un seuil au-dessus de la valeur calculée (règle de CLAUDE.md, appliquée aujourd'hui à la main) |
| `tracks_dsp` : nombres négatifs mal lus | **faux** | l'autotest passe des valeurs négatives |
| DCX : dump interrompu → appareil bloqué en « mode transmission » | non fondé | `3F 0C 00` active seulement les réponses, ce n'est pas un état bloquant ; la session a déjà un `finally` |
| SM118/SM115 « RMS brochure » : appliquer un facteur 0,8 | **refusé** | facteur inventé ; la règle du kit reste : signaler et demander la norme ou mesurer |
| Bridge : vérifier au voltmètre | déjà prévu | méthode §6 et alertes de `calc_systeme` |
| SPL cohérent +20·log(n) : à nuancer | juste, mineur | déjà étiqueté « coh./incoh. » ; à documenter |
| REW : plafonner la plage d'alignement à 50 ms | amélioration mineure | défaut 10 ms ; à voir |
| Alignement « IR d'abord, phase ensuite » | validé par Gemini | — |

---

## Passage 1 (brut, premier essai)
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
---

## Passage 2 (brut, relance)
# Audit du 2026-11-23 — Ingénieur système senior (touring) & relecteur Python

Version relue : 2026-11-23 (kit complet)
Périmètre : Physique, méthode de calage, scripts de calcul (`calc_systeme.py`), alignement et critères de mesure.

## Remarques

### 1. Prise en compte incorrecte de l'impédance dans le calcul de la tension cible du HP
- Fichier : `tools/calc_systeme.py:101-103`
- Gravité : erreur de résultat
- Constat : Le script calcule la tension par HP en faisant `v_hp = math.sqrt(p_lim_hp * z_hp)` puis multiplie par le nombre de HP en série (`v_bornes = v_hp * n_s`). 
- Justification : Mathématiquement, pour $n_s$ haut-parleurs identiques de résistance $R$ en série, l'impédance totale est $Z_{tot} = n_s \cdot R$. La tension totale aux bornes de l'association pour une puissance limite totale $P_{tot}$ est $\sqrt{P_{tot} \cdot Z_{tot}} = \sqrt{(n_s \cdot p_{lim\_hp}) \cdot (n_s \cdot R)} = n_s \cdot \sqrt{p_{lim\_hp} \cdot R}$. La formule actuelle calcule $n_s \cdot \sqrt{p_{lim\_hp} \cdot R}$, ce qui est numériquement équivalent. **Cependant**, utiliser $z_{hp}$ (impédance nominale unitaire) au lieu de l'impédance de la branche série $z_hp \cdot n_s$ dans la formule de puissance amène à un résultat erroné si l'on raisonne par HP individuel vs branche. Plus grave, le script calcule `z_charge = z_hp * n_s / n_p` mais utilise ensuite `z_hp` (et non `z_charge` ou la résistance équivalente de la branche) pour remonter à la tension, ce qui fonctionne par un concours de simplification algébrique, mais est peu robuste et source de confusion sur la tension par HP (`v_hp` représente en réalité la tension par étage et non par HP).
- Correction proposée : Clarifier les variables dans `calc_voie` en calculant directement la tension totale aux bornes de la charge en fonction de `p_totale_voie_w` et `z_charge` : `v_bornes = math.sqrt(pct * p_ref * n_hp * z_charge)`.

---

### 2. Formule du SPL max théorique incohérente pour les sources multiples
- Fichier : `tools/calc_systeme.py:109-110`
- Gravité : amélioration (erreur de résultat prévisionnel)
- Constat : Le script calcule le SPL max cohérent par `sens + 10 * log10(p_lim_hp) + 20 * log10(n_hp)`. Or, `n_hp` est le nombre *total* de haut-parleurs (série $\times$ parallèle).
- Justification : Le terme `20 * log10(n_hp)` suppose un couplage parfaitement cohérent (sommation en tension, $+6\text{ dB}$ par doublement) de *tous* les haut-parleurs de la voie. Physiquement, cela n'est vrai qu'à basse fréquence pour des haut-parleurs regroupés dans un même volume acoustique (ou à entraxe inférieur à $\lambda/2$). Pour les branches parallèles distantes ou les configurations mixtes (ex: 4 caisses de kick séparées), le sommation haut-parleur par haut-parleur dans une même caisse est cohérente, mais entre caisses distinctes, elle devient rapidement incohérente ($+3\text{ dB}$) ou mixte selon la disposition spatiale.
- Correction proposée : Dissocier le gain de couplage intra-caisse (série) et inter-caisses (parallèle), ou limiter l'application du $+20\log(n)$ aux seuls HP d'une même enceinte, en basculant à $+10\log$ pour le nombre de boîtes séparées.

---

### 3. Omission de la vérification de la tension maximale d'entrée (Clip) des amplificateurs
- Fichier : `skills/methode-calage/SKILL.md` (section seuils) et `tools/calc_systeme.py`
- Gravité : danger matériel / erreur de résultat
- Constat : Le calcul détermine un niveau de sortie processeur (en dBu ou dBFS) basé uniquement sur le gain de l'ampli et la tension cible du HP. Il ne vérifie pas si le signal de sortie maximal du processeur dépasse la sensibilité d'entrée maximale (limite de surcharge/clip d'entrée) de l'amplificateur.
- Justification : Par exemple, un QSC PL380 avec un gain sélectionné à 32 dB accepte un niveau d'entrée max de $+29\text{ dBu}$. Si le calcul demande un niveau de sortie processeur supérieur à $+29\text{ dBu}$ pour driver l'ampli à pleine puissance (ce qui arrive avec des gains d'ampli faibles comme 26 dB ou 32 dB sur des charges exigeantes), le processeur va écrêter en amont de l'ampli de manière invisible sur les voyants de l'ampli, ou l'ampli va saturer en entrée.
- Correction proposée : Ajouter dans `calc_systeme.py` une vérification : si `dbu_sortie_processeur > ampli.get("entree_max_dbu", 22)` (ou valeur selon le gain de l'ampli), déclencher une alerte bloquante « Surcharge entrée amplificateur : risque d'écrêtage DSP/Ampli ».

---

### 4. Absence de garde-fou sur la latence induite par les filtres FIR/IIR croisés et le déphasage global
- Fichier : `skills/methode-calage/SKILL.md` (étape 5 & 8)
- Gravité : amélioration
- Constat : La méthode impose l'alignement temporel (IR puis phase) sans mentionner la vérification de la latence totale ajoutée par le processeur et les choix de filtres (notamment si des filtres à phase linéaire ou des pentes raides > 48 dB/oct sont employées sur le VENU360 ou un processeur externe).
- Justification : En live, une latence processeur excessive (> 5-10 ms) décale le retour scène/face ou perturbe le jeu des musiciens sur scène (monitoring). Le VENU360 a une latence fixe de 2,57 ms à 48 kHz, mais l'utilisation de certains blocs ou filtres lourds peut s'additionner.
- Correction proposée : Ajouter dans l'étape 5 de la méthode un rappel sur le contrôle de la latence globale de processing du processeur utilisé.

---

### 5. Confusion potentielle sur l'unité du seuil du t.racks DSP 206 dans la bibliothèque
- Fichier : `data/materiel.yaml` (lignes 118-125)
- Gravité : erreur de résultat
- Constat : Le matériel `TRACKS_DSP206` indique explicitement dans sa source une contradiction non résolue entre le manuel (+5 dBu max) et la mesure sur 408 (+20 dBu), avec un seuil de limiteur documenté en dBu (`-90` à `+20`).
- Justification : Si le processeur attend du dBu réel mais que le script ou l'utilisateur y injecte une valeur calculée sans avoir tranché la pleine échelle au voltmètre, le limiteur fonctionnera complètement à côté de la plaque (soit totalement ouvert, soit mutant la voie en permanence). Le kit signale l'alerte dans le yaml mais le script `calc_systeme.py` ne traite pas spécifiquement le cas `unite_threshold: dBu` pour les processeurs dont la pleine échelle est ambigüe.
- Correction proposée : Dans `calc_systeme.py`, si l'unité est `dBu` et qu'une incertitude de pleine échelle est signalée dans le dictionnaire du processeur, forcer une alerte critique bloquante dans le rapport Markdown.

---

## Avis général
Le kit atteint un degré de maturité impressionnant pour un outil de touring indépendant. La rigueur apportée sur la distinction entre processeurs en dBFS, dBu, et la gestion du coussin d'overshoot des limiteurs dbx (PeakStopPlus) témoigne d'une excellente compréhension des pièges constructeurs réels. Les scripts sont propres et bien structurés. Les quelques remarques ci-dessus visent à sécuriser davantage les cas limites (notamment l'adéquation entre tension de sortie processeur et sensibilité d'entrée ampli).
# Audit du 2026-11-23 — Ingénieur système son senior & relecteur Python

Version relue : 2026-11-23 (Passe 2 : sécurité des outils de pilotage)
Périmètre : `tools/dbx_hiqnet.py`, `tools/tracks_dsp.py`, `tools/dcx2496.py`, `tools/rew_client.py`

## Remarques

### 1. Absence de vérification des bornes de seuil du limiteur dans l'API TCP du dbx
- Fichier : `tools/dbx_hiqnet.py:500` (`main` - commande `set`)
- Gravité : danger matériel
- Constat : La commande `set` accepte n'importe quelle chaîne de caractères comme valeur (`valeur = a.args[2]`), sans vérifier si un seuil de limiteur dépasse 0 dBFS ou une valeur destructrice pour les transducteurs, malgré la règle absolue « Jamais un seuil de limiteur au-dessus de la valeur calculée ».
- Justification (calcul, source, manuel) : `CLAUDE.md` et `docs/processeurs.md` imposent que le seuil calculé par `calc_systeme.py` soit le plafond absolu. Un oubli de `--protection-validee` ou une erreur de saisie (`set \\\\Preset\\Limiter1\\SV\\Threshold "+3.0 dB"`) enverrait un seuil positif dans l'ampli, annihilant toute protection thermique et de crête.
- Correction proposée : Valider et parser la valeur si le chemin contient `threshold`. Interdire toute valeur > 0.0 (dBFS) ou supérieure au seuil calculé de référence si transmis.

### 2. Risque d'état partiel et absence de relecture/vérification de cohérence sur le DCX2496
- Fichier : `tools/dcx2496.py:340` (`main` - commande `set`)
- Gravité : erreur de résultat / danger matériel
- Constat : L'outil envoie une trame SysEx unique via `d.ecrire([(ch, num, brut)])`, puis relit tout le dump et affiche les changements. Cependant, si le DCX rejette la trame (tampon saturé, verrouillage) ou l'applique partiellement en raison d'un lien stéréo non géré, aucune exception n'est levée et le script se termine avec un code de retour `0`.
- Justification (calcul, source, manuel) : Le protocole SysEx du DCX2496 ne renvoie pas d'acquittement positif ou négatif sur le port RS-232. Si l'état relu ne correspond pas à la valeur demandée, le script doit lever une exception pour éviter de laisser un état partiel non validé sur le processeur.
- Correction proposée : Après le dump de vérification, comparer la valeur relue avec `brut` (via `code_valeur`). Si le paramètre cible n'a pas pris la valeur exacte, lever `RuntimeError("Le DCX n'a pas appliqué le paramètre (verrouillage ou erreur de trame)")`.

### 3. Absence de validation des plages de délais et gains sur les commandes set du t.racks DSP
- Fichier : `tools/tracks_dsp.py:270` (`construire_ecriture`)
- Gravité : danger matériel / erreur de résultat
- Constat : La fonction `construire_ecriture` appelle les fonctions d'encodage (`gain_enc`, `delai_enc`, etc.) qui lèvent bien des `ValueError` si hors plage, mais les exceptions ne sont pas interceptées proprement dans `main()` pour afficher un message clair et stopper net l'exécution avant l'envoi réseau.
- Justification (calcul, source, manuel) : Un dépassement de plage non intercepté provoque un plantage brut de l'interpréteur Python (stack trace) au lieu d'un refus explicite de l'outil, ce qui va à l'encontre des règles de sécurité des outils de tournée (CLAUDE.md).
- Correction proposée : Dans `main()`, entourer l'appel à `construire_ecriture` et l'envoi par un bloc `try...except ValueError as e: sys.exit(f"ERREUR DE PLAGE : {e}")`.

### 4. Absence de relecture après écriture sur le t.racks DSP lors d'un set
- Fichier : `tools/tracks_dsp.py:340` (`main` - commande `set`)
- Gravité : erreur de résultat
- Constat : Le script envoie la trame de modification, récupère l'acquittement `ack`, puis décode la nouvelle config (`apres = decoder_config(...)`), mais ne vérifie pas explicitement si la valeur lue dans l'état décodé correspond à la valeur demandée.
- Justification (calcul, source, manuel) : La règle absolue du kit exige une relecture après écriture. Si le DSP est occupé ou refuse la trame tout en renvoyant un acquittement générique `0x01`, le kit considère l'écriture comme réussie à tort.
- Correction proposée : Vérifier dans le dictionnaire `change` que le paramètre modifié correspond bien à la valeur voulue à la tolérance près, sinon lever une erreur critique.

## Avis général
Le kit présente une architecture robuste avec une volonté claire de cloisonner les écritures (`--appliquer`, `--protection-validee`, instantanés automatiques). Les protocoles tiers (DCX, t.racks, dbx, REW) sont bien cernés. L'effort doit désormais porter sur le durcissement de la boucle fermée (validation stricte de la relecture post-écriture) pour garantir qu'aucune instruction n'est appliquée partiellement ou incorrectement sur le matériel en conditions réelles.