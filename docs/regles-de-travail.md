# Règles de travail du kit (pour toute IA ou personne qui reprend le dépôt)

Ces règles venaient de la mémoire privée de Claude sur le PC de Louis. Elles sont recopiées ici (25/11/2026) pour
qu'une autre IA, un autre compte ou un autre ordinateur les ait aussi. Elles complètent `CLAUDE.md` (règles absolues
de calage), qui reste prioritaire.

## Reprendre le travail
1. Lire `CLAUDE.md`, puis `journal/ETAT.md` (le plus récent en haut), puis `skills/methode-calage/SKILL.md`.
2. Installer l'environnement : `installer.ps1` (Python 3.12+, `.venv`), tests `.venv\Scripts\python.exe -m pytest`.
3. Pour le VENU360 : skills `dbx-reseau` et `venu360-pilotage`. Mot de passe dans la variable `DBX_MDP`
   (usine « administrator », à changer si le réseau est partagé). Jamais de mot de passe Wi-Fi écrit nulle part.

## Publication
- Ne rien publier (GitHub, Drive) sans validation explicite de Louis, sauf s'il a dit « une fois fini, mets à jour ».
  Présenter les changements, demander « je publie ? », puis publier.
- **GitHub est PUBLIC** (aukarock-ui/calage-sono). Jamais :
  - les extraits de la machine ou de REW (`journal/autotest-*.json`, instantanés…) ;
  - les PDF et les textes de `docs/references/` (sous droit d'auteur : PC et Drive seulement) ;
  - de secret, ni l'adresse e-mail personnelle.
  Commits avec l'e-mail noreply GitHub du compte.
- **Drive** (dossier `calage-sono`) : le connecteur ne remplace pas un fichier. Il faut créer la nouvelle version,
  puis déplacer l'ancienne dans `_archives-AAAA-MM-JJ`, renommée `dossier__fichier_vN`. Google Drive pour ordinateur
  n'est pas installé : rien ne se synchronise seul, il faut renvoyer ETAT.md en fin de session.

## Périmètre
- Ici : le **système** (processeurs, amplis, enceintes, mesure OSM ou REW). La console (StudioLive 32SC + 16R,
  UC Surface, guide de mixage) relève d'un autre projet (« Regie-Auka ») : la signaler en une ligne sans la traiter.
- Répondre à Louis en français.

## Matériel
- Avant de régler un limiteur du VENU360, relire `\\Node\Settings\SV\Output<n>Gain` : usine +12 dBu, machine de
  Louis +22 dBu (relu le 24/11/2026). L'outil le revérifie pour chaque seuil.
- Pas de broadcast réseau. Pas de test en direct sur le matériel sans l'accord de Louis.
- Level Assist du VENU360 : « Auto Trim » toujours **Off** (corrections invisibles et non modifiables).

## Pièges techniques connus
- Le shell Bash mange les antislashs des chemins HiQnet (`\\Preset\…`) : écrire les scripts avec un éditeur de
  fichiers (chaînes `r"…"`) ou passer par PowerShell.
- REW n'ouvre son API que lancé avec `-api` (port 4735). Open Sound Meter : cocher Remote > Server.
- Pousser un workflow GitHub demande le scope `workflow` de `gh`.
