# Kit « Assistant calage système »

Kit pour Claude Code sur un PC Windows : caler n'importe quelle sono (à composants, constructeur, amplifiée, mixte).
Contenu : méthode de calage en skills, bibliothèque de composants, un fichier par système, fiches d'événement,
plan 3D avec positions micro, calculs de gain/limiteurs, mesure Open Sound Meter, pilotage réseau des dbx VENU360/PA2
(autres processeurs : feuille de saisie guidée), simulateur pour s'entraîner sans matériel.
Systèmes déjà décrits : `ma-sono-actuel`, `ma-sono-refonte` (parc de Louis).

## Relecture / audit
Pour relire le kit (humain ou IA) et laisser des notes : voir [`AUDIT.md`](AUDIT.md) (dossier `audits/`, issues GitHub).

## Installation (une fois)
1. **Récupérer le dossier** : installer Google Drive pour ordinateur ; le dossier `calage-sono` apparaît dans
   `G:\Mon Drive\...` (ou le télécharger en zip). Travailler sur une copie locale si le PC sera hors ligne sur site.
2. **Python 3.11+** : https://www.python.org (cocher « Add to PATH »).
3. **Dépendances** (PowerShell, dans le dossier) : `pip install -r requirements.txt`
4. **Claude Code** : installer puis lancer `claude` dans le dossier. Il lit `CLAUDE.md` automatiquement.
5. **Skills** (optionnel, pour qu'ils se déclenchent seuls) : `powershell -ExecutionPolicy Bypass -File installer.ps1`
6. **Pare-feu** : autoriser TCP/UDP 19272 (dbx) et 49007 (Open Sound Meter) pour Python.
7. **Open Sound Meter** : installer depuis github.com/psmokotnin/osm/releases, puis Remote → cocher « Server ».

## Vérifier que tout marche (sans matériel)
```
python tools/calc_systeme.py data/configs/ma-sono-actuel.yaml
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

## Mise à jour
Ce dossier est tenu à jour depuis le projet Claude « MA Sono » (là où il a été créé). Le journal `journal/ETAT.md` est la mémoire du terrain :
le garder synchronisé (Google Drive le fait tout seul si on travaille dans le dossier synchronisé).
