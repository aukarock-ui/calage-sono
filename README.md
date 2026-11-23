# Kit « Assistant calage système »

Kit pour Claude Code sur un PC Windows : caler n'importe quelle sono (à composants, constructeur, amplifiée, mixte).
Contenu : méthode de calage en skills, bibliothèque de composants, un fichier par système, fiches d'événement,
plan 3D avec positions micro, calculs de gain/limiteurs, mesure Open Sound Meter, pilotage réseau des dbx VENU360/PA2
(autres processeurs : feuille de saisie guidée), simulateur pour s'entraîner sans matériel.
Systèmes déjà décrits : `ma-sono-actuel`, `ma-sono-refonte` (parc de Louis).

## Relecture / audit
Pour relire le kit (humain ou IA) et laisser des notes : voir [`AUDIT.md`](AUDIT.md) (dossier `audits/`, issues GitHub).
Deux agents Claude Code en lecture seule sont fournis dans [`.claude/agents/`](.claude/agents/) :
- `controleur-securite` relit chaque modification sensible avant commit ;
- `documentaliste` retrouve une valeur dans les livres de référence. Les textes de ces livres restent sur le PC (droit d'auteur).

## Installation (une fois)
1. **Récupérer le dossier** : installer Google Drive pour ordinateur ; le dossier `calage-sono` apparaît dans
   `G:\Mon Drive\...` (ou le télécharger en zip). Travailler sur une copie locale si le PC sera hors ligne sur site.
2. **Python 3.12+** : https://www.python.org (cocher « Add to PATH »).
3. **Installation** (PowerShell, dans le dossier) : `powershell -ExecutionPolicy Bypass -File installer.ps1`
   Le script crée l'environnement isolé `.venv`, installe les versions testées (`constraints.txt`) et copie les skills.
   Les outils se lancent ensuite avec `.venv\Scripts\python.exe tools\<outil>.py`, ou après `.venv\Scripts\Activate.ps1`.
4. **Claude Code** : l'installer, puis lancer `claude` dans le dossier. Il lit `CLAUDE.md` automatiquement.
5. **Mot de passe dbx** : s'il n'est plus celui d'usine, le mettre dans la variable d'environnement de la session,
   par exemple `$env:DBX_MDP = "…"`. L'option `--mdp` reste possible, mais le mot de passe apparaît alors dans l'historique.
   Il n'est jamais affiché ni journalisé. Le processeur doit rester sur un réseau isolé, jamais sur Internet ni sur un réseau partagé.
6. **Pare-feu** : autoriser TCP/UDP 19272 (dbx) et 49007 (Open Sound Meter) pour Python.
7. **Open Sound Meter** : installer depuis github.com/psmokotnin/osm/releases, puis Remote → cocher « Server ».

## Vérifier que tout marche (sans matériel)
Tests automatiques (installés par `installer.ps1 -Dev`) : `.venv\Scripts\python.exe -m pytest`.
Ils sont aussi lancés par GitHub Actions à chaque push (`.github/workflows/tests.yml`, Windows, Python 3.12 et 3.13).
```
python tools/calc_systeme.py data/configs/ma-sono-actuel.yaml
python tools/calc_systeme.py --puissance 95 20 97 --cable 20 1.5 6   # dimensionnement (Boyce §11.9)
python tools/calc_systeme.py --renfort 45 10 --temp 25             # délai d'un renfort
python tools/mesure_tf.py autotest
python tools/faux_venu360.py          # dans une 2e fenêtre
python tools/dbx_hiqnet.py info 127.0.0.1
python tools/osm_client.py ecouter      # OSM lancé, Remote > Server coché
```

## Nouveau système / nouvelle date
Dans Claude Code : « Nouveau système : … » (skill nouveau-systeme), puis « Prépare la fiche de la date du … »
(skill fiche-evenement) → plan 3D et procédure générés automatiquement.

## Première session sur le vrai VENU360
Dans Claude Code : « Cartographie le Venu360 à l'adresse 192.168.x.x en lecture seule » → il suit
`skills/dbx-reseau/SKILL.md` (info, arbre, mode apprentissage) et complète `docs/protocole-dbx.md`.

## Licence
MIT (licence libre) : voir [`LICENSE`](LICENSE). Elle ne couvre ni les marques ni les manuels des constructeurs.

## Mise à jour
Ce dossier est tenu à jour depuis le projet Claude « MA Sono » (là où il a été créé). Le journal `journal/ETAT.md` est la mémoire du terrain :
le garder synchronisé (Google Drive le fait tout seul si on travaille dans le dossier synchronisé).
