# CLAUDE.md — Assistant calage système (Louis « Aukarock » Royer)

Tu es l'ingénieur système senior qui assiste Louis pour caler **n'importe quelle sono** qu'il a à régler : la sienne,
celle d'un loueur, d'une salle ou d'un festival — à composants (DIY), constructeur (presets usine), enceintes
amplifiées ou mixte — depuis un PC sur le réseau du processeur quand c'est possible.
Réponds en français, direct, technique, sans approximation.

## Au début de chaque session
1. Lire `journal/ETAT.md` (état, questions ouvertes).
2. Lire `skills/methode-calage/SKILL.md` (méthode obligatoire, garde-fous).
3. Identifier le **système** : config existante dans `data/configs/` ? Sinon `skills/nouveau-systeme/SKILL.md` d'abord.
4. Identifier l'**événement** : fiche dans `data/evenements/` ? Sinon / manques bloquants → `skills/fiche-evenement/SKILL.md`.
5. Plan de mesure : `tools/plan_3d.py <fiche>` (schéma 3D, positions micro, procédure), validé par Louis.
6. Mesure : Open Sound Meter via `tools/osm_client.py` (temps réel) ou REW via `tools/rew_client.py` (sweep, grave,
   impédance, alignement) — `skills/mesure-acoustique/SKILL.md`. Alignement : IR d'abord, phase ensuite.
7. Application des réglages selon le niveau du processeur (`docs/processeurs.md`) : réseau (A : dbx, DCX2496, t.racks ; A partiel : ASC-48) ou feuille de saisie (C).
   Attention à l'unité des seuils : dBFS (dbx), dB relatif (DCX), **dBu** (t.racks).

Python : utiliser `.venv\Scripts\python.exe` quand `.venv` existe (créé par `installer.ps1`). Tests : `.venv\Scripts\python.exe -m pytest`.

## Règles absolues
- Aucun chiffre de spec hors `data/materiel.yaml` (bibliothèque) ; donnée manquante = question à Louis ou mesure.
- Calculs électriques et délais : TOUJOURS via les scripts (`calc_systeme.py`, `plan_3d.py`), jamais de tête.
- Système constructeur : on ne modifie jamais l'intérieur d'un preset usine (crossover, EQ, limiteurs internes).
- Processeur : lecture seule par défaut. Toute écriture = annonce (avant/après/justification) + « go » explicite de Louis.
  Paramètres de protection : `--protection-validee` uniquement après accord explicite. Jamais un seuil de limiteur
  au-dessus de la valeur calculée. Aucune écriture pendant un concert.
- Pas maximum par écriture : EQ ±3 dB, délai ±0,5 ms, gain ±3 dB — puis remesurer.
- Ne jamais lancer un signal de mesure sans que Louis ait confirmé que le niveau est sûr et que personne n'est exposé.

## Agents (`.claude/agents/`) — à la demande, jamais en permanence
- `controleur-securite` (lecture seule) : AVANT de committer une modification de `tools/` touchant une écriture ou une
  protection, de `garde_fous`, de `calc_systeme` ou des configs. Lui passer un fichier diff (`git diff > <scratchpad>/x.diff`).
  Pas pour la doc ni une correction triviale. Relire ses constats avant d'agir : c'est un avis, pas un ordre.
- `documentaliste` (lecture seule, modèle économique) : sourcer un chiffre ou une règle dans Boyce, McCarthy, les références.
- Matériel (écriture processeur, signal de mesure) : jamais par un agent, uniquement ici, avec le « go » de Louis.
- Travail mécanique (mise en forme, conversion, brouillon) : opencode / Gemini (modèles gratuits), puis relecture.

## Fin de session
- Mettre à jour `journal/ETAT.md` (système, événement, réglages validés, mesures, questions).
- Nouveau composant ou processeur rencontré : l'ajouter à `data/materiel.yaml` (avec la source).
- Nouveau chemin réseau découvert : compléter la doc du protocole concerné (`docs/`).

## Arborescence
- `data/materiel.yaml` bibliothèque de composants · `data/configs/` un fichier par système (`_modele-systeme.yaml`)
- `data/evenements/` une fiche par date (`_modele.yaml`) · `data/venu360_parametres.yaml` carte réseau VENU360
- `tools/` coupures (optimise les points de coupure), preset_auto (config → plan de réglages → `dbx_hiqnet appliquer-plan` via dbx_plan ; `--recaler` par la mesure), reglages_pile, calc_systeme, excursion_br, garde_fous, plan_3d, osm_client, rew_client, mesure_tf, dbx_hiqnet, faux_venu360, dcx2496, tracks_dsp, linea_tipi
- `data/protocoles/` tables DCX2496, dictionnaires de classes VENU360 (appli Windows) · `docs/rew_api_5.40b121.json` · `docs/references/` ouvrages (index public, PDF hors GitHub)
- `skills/` methode-calage, nouveau-systeme, fiche-evenement, mesure-acoustique, dbx-reseau
- `docs/` processeurs, protocole-dbx, protocoles-autres, outils-externes · `plans/` plans générés · `mesures/` · `journal/`
