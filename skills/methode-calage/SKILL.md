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
- Règle ou valeur tirée d'un livre (Boyce, McCarthy, références bass-reflex) et absente de cette skill : la faire
  sourcer par l'agent `documentaliste` (citation courte + page imprimée) avant de s'en servir, sans lire le livre ici.

## D'abord : quel type de système ?
| Type (`type_systeme` dans la config) | Ce que Claude règle | Ce qu'il ne touche PAS |
|---|---|---|
| **composants** (HP + amplis + processeur, DIY/semi-pro) | tout : coupures, polarités, délais internes, EQ, gains, limiteurs calculés | — |
| **constructeur** (presets usine : L-Acoustics, d&b, Meyer, NCA, Adamson…) | alignement entre ensembles (subs ↔ têtes ↔ fills ↔ délais), gains de zones, EQ système, délais de site, choix du preset dans la gamme constructeur | crossover, EQ et limiteurs INTERNES des presets |
| **amplifiee** (enceintes actives avec DSP) | comme « constructeur » + modes/presets de l'enceinte | protections internes |
Système mixte (ex. têtes constructeur + subs DIY) : chaque voie porte son propre type.

## Ordre imposé (aucune étape sautée ; en « constructeur », 1-2 et 8 se limitent à vérifier les docs constructeur)
0. **Pré-vérification**, avant tout réglage (McCarthy ch. 9, p. 401-423 : « vérifié et non calé » vaut mieux que l'inverse) :
   - chaque caisse au sol, une par une : polarité (signe du pic d'IR), niveau, réponse ;
   - latence réelle de chaque sortie, mesurée par IR ;
   - bruit de fond et niveau maximal de la chaîne, pour la structure de gain ;
   - micros de mesure appariés en niveau et en réponse, en remplaçant l'un par l'autre au même point ;
   - après le calage d'un côté, vérifier que le côté symétrique donne la même réponse.
   Méfiance envers le gain unitaire apparent : +20 dB en entrée et −20 dB en sortie font perdre 20 dB de marge.
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
   Repères (Boyce 2014 §11.6, McCarthy 2007 ch. 2 et 6, `docs/references/`) :
   - **Dimensionnement** : `calc_systeme.py --puissance SPL DIST SENS --cable L SECTION RE [--marge 6]` donne la
     puissance continue nécessaire en champ libre (marge dynamique 3 dB au minimum, 6 dB pour la plupart des cas). Ce
     chiffre sert à vérifier que le système est assez gros. Il ne change JAMAIS un plafond de limiteur, qui reste fixé
     par la puissance AES du HP. La perte du câble baisse la tension aux bornes : un seuil calculé aux bornes reste du
     côté sûr.
   - **Distance critique** : `calc_systeme.py --dc VOLUME RT60 Q`. Un public au-delà d'environ 2·Dc perd
     l'intelligibilité (inacceptable vers 3·Dc) : prévoir des renforts retardés ou une enceinte plus directive.
   - **Couverture** : ±3 dB de l'avant au fond du public est la tolérance d'usage. Viser le fond avec l'axe, placer la
     limite avant sur l'angle à −9 dB des polaires 2–10 kHz. L'angle nominal constructeur est en général l'angle à
     −6 dB moyenné sur 1–4 kHz, sans norme commune.
   - **Couplage dans le grave** : deux boomers côte à côte gagnent jusqu'à +6 dB, plus environ +3 dB de couplage
     mutuel ; une enceinte posée au sol gagne +6 dB dans le grave (demi-espace). Prévoir une correction du grave.
   - **Sommation** : deux sources s'additionnent tant que leur écart de phase reste sous ±120° (zone de couplage). À
     10 dB d'écart de niveau ou plus, elles sont isolées et n'interagissent presque plus.
4. **Crossovers** — LR24 par défaut ; tout autre choix justifié. Chaque Fc justifiée (mécanique, excursion, directivité,
   SPL, style). Pas d'arrondi arbitraire. En « constructeur » : point de raccord subs/têtes selon la doc constructeur.
5. **Alignement temporel** — délais physiques (`plan_3d.py` / `calc_systeme.py --delai`), puis MESURE : phase à Fc
   (`osm_client.py comparer`), ajustement sub-ms, test d'inversion de polarité, validation de la sommation
   (≈ +6 dB à niveaux égaux). Le réglage FIN se fait sur la PHASE à Fc, pas sur le pic d'IR brut — mais il part
   d'une position grossière prise sur les IR filtrées autour de Fc (`rew_client.py aligner`), sinon la phase seule
   peut se tromper d'un cycle entier (et inverser la polarité à tort).
   **Subs** (McCarthy p. 304-306) :
   - end-fire : caisses l'une derrière l'autre, chaque caisse avant retardée de son espacement divisé par c. Le gain est
     devant et l'annulation derrière, mais le système est moins efficace qu'un empilement simple ;
   - deux caisses en ligne espacées d'un quart d'onde, avec délai et polarité inversée : de l'ordre de 28 dB d'écart
     avant/arrière à la fréquence visée ;
   - des subs espacés (non couplés) se recouvrent trop et ne passent pas le critère de variance minimale, sauf les
     caisses cardioïdes ;
   - dépointer par délai revient à désaligner volontairement le raccord, et dégrader les niveaux n'oriente pas le
     faisceau. Pour orienter, il faut un arc (angle).
   Délais calculés à partir des distances, puis validés par une mesure avant et arrière.
   **Renforts retardés** (fills, délais) : `calc_systeme.py --renfort D_FACADE D_RENFORT --temp T`, distances prises au
   **point de raccord** (XOVR : là où façade et renfort arrivent au même niveau). Par défaut, le raccord est aligné en
   phase, sans marge (McCarthy p. 464-465) :
   - ajouter 10 ms de « précédence » crée un peigne dès 50 Hz et fait baisser l'intelligibilité ;
   - l'effet de précédence ne joue qu'à l'horizontale.
   Boyce (§11.6.7) conseille au contraire environ 10 ms (`--precedence 10`) : ne l'ajouter qu'après une écoute comparée
   avec le raccord aligné. Le réglage final se fait à l'IR au point XOVR, en mesurant le trajet façade puis le trajet
   renfort ; les deux IR doivent se confondre en une seule.
   Façade à plus de 5 m devant les musiciens : on peut retarder toute la façade sur le son de scène (Boyce §8.8.3).
   McCarthy met en garde : ne synchroniser sur la scène que les systèmes les plus proches, car la scène est une source
   variable.
   Le délai affiché par un processeur n'est pas toujours celui qu'il ajoute : la latence change selon la voie, la
   compilation ou certains filtres, et 0 ms + 0 ms ≠ 0 ms. Mesurer toute la chaîne (McCarthy p. 22, 412-414).
   Température : environ 1 % de vitesse du son par 5 °C, soit 1 ms sur un trajet de 100 ms. En concert, comparer les
   temps de trajet relevés avant le show à ceux du moment, et corriger les délais du même pourcentage (McCarthy p. 479).
6. **Structure de gain** — 90–95 % du potentiel, zéro écrêtage DSP/ampli. Gains amplis confirmés au voltmètre en
   « composants ». Équilibre des voies selon le style.
7. **EQ stratégique** — chaque filtre corrige un problème mesuré, améliore le headroom ou la lisibilité. Pas d'EQ
   décorative. Les creux d'annulation ne se corrigent pas à l'EQ. Règles (McCarthy p. 367, 433-443, 454-463) :
   - **où décider** : à la position **ONAX** de chaque sous-système, c'est-à-dire son point le plus isolé des autres
     (sur l'axe, à mi-profondeur de sa zone). C'est là que l'EQ vaut pour la plus grande surface. Jamais au point de
     raccord (XOVR), qui sert aux délais ; jamais sur un bord (OFFAX), qui sert à orienter ;
   - **niveaux** : régler chaque sous-système pour le même niveau à son ONAX. Les bords doivent rester à 6 dB au plus
     sous l'ONAX ; au-delà, revoir l'orientation et non l'EQ ;
   - **plusieurs positions** : les superposer et suivre l'enveloppe, c'est-à-dire les pics et ce qui survit, sans tenir
     compte des creux étroits. Ne jamais additionner des micros (peigne électrique). `osm_client.py moyenne` fait une
     moyenne en puissance, qui résiste aux creux, mais n'y mettre que des positions ONAX comparables, par exemple une
     par pile d'un système symétrique ;
   - **résolution** : décider d'un filtre sur une vue au 1/24 d'octave (`--resolution 24`). Le tiers d'octave ne sert
     qu'à la vue d'ensemble. Ne retenir que les bandes de cohérence ≥ 0,8 ;
   - **systèmes asymétriques** (façade et renfort) : dans le grave, couper l'EQ du système dominé n'a presque aucun
     effet. 6 dB de coupe n'en donnent que 3 sur la somme, et une coupe de plus ne donne presque rien. Corriger le
     dominant seulement s'il ne dégrade pas sa propre zone ;
   - **ordre** : d'abord la position, puis le niveau et le délai, l'EQ en dernier. Commencer par le système qui porte
     le plus loin (A), puis lui joindre les suivants (B, C), en traitant les sous-systèmes couplés symétriques avant
     les autres.
   Pas de filtre notch pour l'EQ système : il retire une bande entière au lieu d'égaliser. Le notch sert à l'accrochage,
   sur les retours ou la façade, et non au calage. Au-delà de six filtres sur une même voie, chercher d'abord une cause
   physique (placement, alignement, polarité) (McCarthy p. 23-24).
8. **Limiteurs** — « composants » : seuils = sortie de `calc_systeme.py`. RMS (thermique) sur le grave, crête sur les
   moteurs à compression. Attaque/relâchement : sub lent, grave moyen, aigu très rapide — valeurs de départ par
   `tools/reglages_pile.py` (table Proel DSO26 : attaque selon le HPF, relâchement 16×). Thermique : QSC (Neunaber 2004)
   travaille en secondes ; un limiteur unique de processeur ne le peut pas -> seuil réduit (75 %) + détection RMS. « constructeur » : jamais
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
   Détection : un limiteur en crête suit le signal instantané, alors qu'un limiteur en RMS laisse passer les transitoires
   (Boyce §8.6.5). Le crête contient l'excursion et protège les moteurs, le RMS lent contient l'échauffement.
   Côté ampli (Boyce ch. 5) :
   - en bridge, la charge minimale double (4 Ω en stéréo donne 8 Ω en bridge) et le facteur d'amortissement est divisé
     par deux ;
   - certains amplis ont un filtre infrasonique fixe, à repérer face au HPF ;
   - une puissance « DIN 45500 » est donnée à la moitié de la puissance nominale ;
   - un défaut d'ampli en continu (DC) n'est pas arrêté par le limiteur du processeur, seulement par la protection DC
     de l'ampli ;
   - la règle « ampli = 2 × la puissance du HP » n'est qu'un point de départ, le plafond reste celui du limiteur calculé.
9. **Style** — courbe cible (ci-dessous) et niveau visé de la fiche, dans les limites réglementaires.
10. **Validation finale** — cohérence électrique, excursion, phase à Fc, headroom, SPL global, style, sécurité
    mécanique, niveaux réglementaires. Toute incohérence ⇒ corriger avant de sortir quoi que ce soit.
    Test d'ensemble (Boyce §13.4.1) : chaque ampli et chaque enceinte seuls, puis ensemble, puis tous les amplis au
    niveau du concert. Ce dernier essai vérifie que les circuits électriques tiennent la charge, avant que le public
    soit là.
    Sécurité (Boyce §11.10-11.11) :
    - les sorties d'amplis dépassent couramment 50 V : aucune borne nue accessible au public ;
    - protéger tous les circuits par un différentiel (Boyce décrit le GFCI nord-américain : 6 mA, < 30 ms ; en France,
      un différentiel 30 mA selon la NF C 15-100) ;
    - ne jamais retirer la terre secteur contre une boucle de masse, mais lever le blindage côté entrée ou mettre un
      transformateur d'isolement ;
    - empiler au plus trois enceintes, la plus lourde en bas, sanglées au-delà de deux ;
    - n'accrocher qu'aux points et avec le matériel prévus par le constructeur.

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
**Profils chiffrés** (L-Acoustics, diapo « System response », p. 34, fournie par Louis ;
`docs/references/profils_programme_system_response_p34.jpg`) — niveau MOYEN dans la salle, plateau plat au-dessus de
~1 kHz, grave relevé par rapport au plateau :

| Profil | Programme | Plateau | Grave (≈ 55 Hz) | Plateau atteint vers |
|---|---|---|---|---|
| leger | parole, conférence | 99 dB(A) | +2 dB | 500 Hz |
| moyen | jazz, acoustique | 102 dB(A) | +10 dB | 650 Hz |
| lourd | groupe live, rock, hip-hop | 105 dB(A) | +13 dB | 750 Hz |
| exigeant | DJ, techno, dub | 108 dB(A) | +16 dB | 1,1 kHz |

`reglages_pile.STYLES` / `PROFILS` les portent (lecture du graphe à ±1 dB) ; `preset_auto --style` en tire les gains de
sortie et compare le plateau atteignable au niveau du profil (« puissance » : voie limitante + réserve des autres).
Repères qualitatifs par style, à affiner à la mesure et à l'oreille :
- PUNK : médium agressif, kick présent non dominant, voix 1–4 kHz intelligible, grave contrôlé.
- MÉTAL : kick percutant, 200–400 Hz propre, HF clair sans stridence.
- HIP-HOP : sub profond maîtrisé, 80–120 Hz propre, voix 2–5 kHz ultra lisible.
- REGGAE/DUB : sub rond dominant, kick doux, médium chaud, HF non agressif.
- TECHNO : courbe descendante, infra renforcé, kick dominant.
- FREE/TEKNO : impact physique, médium brut, kick agressif.
- NOISE : large dynamique, limiteurs permissifs (dans les limites de sécurité).
Les préférences propres à un système (ex. MA Sono : réglages fixes, voicing bass-heavy) sont dans sa config, champ `preferences`.

## Choix des coupures (`tools/coupures.py`)
Grille au 1/6 d'octave par raccord, pente de la config conservée.
- **Contraintes dures** (bibliothèque) : bande du HP ; `coupure_min_recommandee_hz` ; `puissance_specifiee_au_dessus_hz`
  (sous cette fréquence, le seuil calculé ne protège plus : CP650Ti ≥ 1 kHz) ; `coupure_min_hz` du pavillon ; HPF
  bass-reflex revérifié par excursion_br, et jamais abaissé s'il n'est pas vérifiable ; voie intermédiaire ≥ 1 octave.
- **Critères (dB)** :
  - puissance de programme : énergie du profil dans la bande de chaque voie, filtres compris, contre sa capacité ;
    la voie la plus chargée fixe le niveau, et élargir une bande la charge ;
  - lobes verticaux au raccord : deux sources égales, écartement de `geometrie_pile`, perte moyenne sur le public ;
  - écart de directivité à la demi-couverture du pavillon (piston 2·J1(x)/x contre −6 dB du pavillon).
- **Score** = puissance − lobes − 0,5 × directivité (poids = hypothèses, remplacés par les poids appris de
  `data/apprentissage/poids_coupures.yaml` dès qu'il existe : voir « Retours de mesure »). Les variantes à moins de 0,5 dB de la
  meilleure sont équivalentes : l'outil conseille la plus proche de l'actuel. On départage les 2-3 premières à la
  mesure (ONAX + hauteur) et à l'écoute.
- **Limites** : sensibilité de fiche supposée plate dans la bande ; HP seul (ni réseau de HP, ni caisse) ;
  facteur de crête ignoré.

## Chemin rapide (pile à composants, n'importe quelle config)
1. Config complète (skill `nouveau-systeme`) : voies, amplis, `crossover`, `geometrie_pile`, `hypotheses_caisse`
   (accord/volume supposés tant que non mesurés), `style_defaut`.
1 bis. **Coupures** : `python tools/coupures.py data/configs/<sys>.yaml --style <style> [--public 5-30]` classe les
   variantes (section « Choix des coupures ») ; Louis choisit, puis `--ecrire [N]` écrit la variante N (1 = conseillée)
   dans `crossover` (commentaires conservés, relecture du YAML exigée).
2. `python tools/preset_auto.py data/configs/<sys>.yaml --style <style> [--distance D]` → plan neutre
   `plans/<sys>-<style>-reglages.json` + feuille `.md`. Il enchaîne calc_systeme (seuils), excursion_br (HPF des BR
   sur toute la plage d'hypothèses, HPF minimal proposé s'il ne tient pas), reglages_pile (temps Proel, délais,
   gains + profil, puissance). Code 2 = BLOQUANT (DANGER, excursion, réglage de sortie non confirmé, filtre absent
   non déclaré « aucun », HPF bass-reflex non vérifiable…) : on corrige. HPF non vérifiable faute d'accord/volume :
   `--accepter-non-verifie` seulement sur décision de Louis (noté dans le plan).
3. Annoncer le plan à Louis (feuille + alertes + hypothèses), obtenir le « go » et l'accord protections, puis :
   dbx VENU360 → `dbx_hiqnet.py appliquer-plan <ip> <plan.json> --case N --nom "…" --verifier --appliquer --protection-validee`
   (topologie à sorties indépendantes exigée, mute relu, protections d'abord, relecture, stockage, rappel + relecture) ;
   autre processeur → saisir la feuille `.md`, relire à l'écran.
4. Mesures (skill `mesure-acoustique`) : délais IR par voie, gains ampli au voltmètre, accords par impédance,
   écart de niveau par voie → `mesures/<sys>-<date>.yaml`.
5. `python tools/preset_auto.py --recaler data/configs/<sys>.yaml mesures/<sys>-<date>.yaml` → corrections dans
   `<sys>.recalage.yaml` (lu par tous les outils ; seuls gain_canal_db, accord, correction de sensibilité et
   géométrie y sont admis), historique des écarts calcul/mesure. Un gain mesuré qui RELÈVE un plafond exige
   `--protection-validee` ; un écart de gain > 3 dB exige `--forcer-gain` (calibre ? sélecteur ?). Régénérer le plan (étape 2)
   et réappliquer (pas max : ±3 dB, ±0,5 ms par écriture hors construction de preset). Noter les écarts dans ETAT.
5 bis. **Essai des coupures au micro** (banc avec micro, REW) : `tools/essai_coupures.py` (skill `mesure-acoustique`,
   « Départager des variantes ») mesure le témoin et les 2-3 meilleures variantes, retient la meilleure à la mesure,
   propose le recalage des délais et alimente l'historique qui recale les poids de l'optimiseur.
5 ter. **Optimisation complète du preset à la mesure** : `tools/optim_preset.py` (skill `mesure-acoustique`), dans
   l'ordre de la méthode, chaque étape mesurée, corrigée par pas bornés, remesurée et gardée seulement si la mesure
   s'améliore : gains de voie → délais → phase (Band_1_Phase, seulement si le banc électrique l'a caractérisée) → EQ par
   voie (PEQOut, creux d'abord) → EQ générale (PEQ1, liée à la salle). Prérequis une fois par firmware :
   - ORDRE PEQOut / limiteur : `dbx_hiqnet.py chaine <ip> --enregistrer` (lu dans la topologie de la machine ; établi
     le 24/11/2026 sur 2.0.5.0 : PEQ et mute AVANT le limiteur) ; sans lui, l'EQ ne fait que des creux ;
   - modèle de PHASE : `tools/banc_electrique.py` (boucle REW, amplis coupés) ; sans lui, pas d'étape phase.
   Retouche ponctuelle d'un paramètre (n'importe lequel) : `dbx_hiqnet.py regler <ip> VOIE.bloc.param valeur`, ou un
   `lot` (retour arrière automatique) — skill `venu360-pilotage` ; pas maximum appliqué par l'outil.

## Retours de mesure (amélioration de la technique)
- Chaque essai au micro ajoute à `data/apprentissage/coupures.yaml` les critères PRÉDITS (puissance, lobes,
  directivité) et la pénalité MESURÉE de chaque variante (ajout seulement).
- `python tools/coupures.py --calibrer` ajuste les poids lobes / directivité (moindres carrés bornés à [0, 3]) quand
  l'historique compte ≥ 3 essais à plusieurs positions et ≥ 6 variantes ; il affiche l'erreur de prédiction avant /
  après ; `--appliquer` adopte les poids (fichier `poids_coupures.yaml`, ancien jeu gardé en historique). Proposer à
  Louis avant d'appliquer.
- Jamais appris : contraintes dures (bandes HP, planchers de puissance, pavillon, excursion BR), seuils, garde-fous.
- Délais alignés à la mesure (témoin) → `recalage-propose.yaml` → `preset_auto --recaler` : le centre acoustique
  effectif de chaque voie corrige tous les calculs suivants.
- Optimisation complète (`optim_preset conclure`) → `recalage-propose.yaml` : délais, écart de niveau par voie
  (→ `correction_sensibilite_db`), phase et EQ de voie (= réponse propre de la voie dans sa caisse, réutilisée par les
  plans suivants du même système) ; `eq-generale.yaml` à part (salle, `preset_auto --eq-generale`, noté dans la fiche
  événement) ; historique `data/apprentissage/preset.yaml`.
- Noter dans ETAT ce que chaque essai a appris (prédit / mesuré, poids, centres acoustiques).

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
