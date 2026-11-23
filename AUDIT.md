# Auditer le kit — mode d'emploi pour un relecteur (humain ou IA)

Tu relis la méthode de calage et les outils d'un ingénieur système. Le but est de trouver des erreurs de physique,
de calcul ou de sécurité, pas de reformuler. Le kit n'a encore jamais été testé sur le matériel réel.

## Quoi lire, dans l'ordre
1. `CLAUDE.md` : règles absolues (lecture seule par défaut, aucune donnée inventée…).
2. `skills/methode-calage/SKILL.md` : la méthode en 10 étapes.
3. `tools/calc_systeme.py` + `data/materiel.yaml` + `data/configs/ma-sono-actuel.yaml` : calcul des seuils de limiteur.
4. `skills/mesure-acoustique/SKILL.md`, `tools/rew_client.py`, `tools/osm_client.py` : mesure et alignement.
5. `docs/protocole-dbx.md`, `docs/protocoles-autres.md`, `tools/*.py` : pilotage des processeurs.
6. `journal/ETAT.md` : état et questions ouvertes (ce qui est déjà su comme non vérifié).

## Points où un avis extérieur est le plus utile
- Seuils de limiteur : formule dBu → dBFS, gain bridge +6,02 dB, coussin d'overshoot dbx, 75 % de la puissance nominale.
- Unités des seuils : dBFS (dbx), dB relatif (DCX2496), dBu (t.racks, pleine échelle +5 ou +20 dBu ?).
- Alignement : « IR d'abord, phase ensuite », critères de cohérence, moyenne spatiale.
- Garde-fous d'écriture dans les processeurs : un cas où le kit pourrait écrire sans le vouloir ?
- Tout chiffre de spec qui ne serait pas sourcé.

## Comment laisser tes notes (au choix)
- **Un fichier** : copie `audits/_modele.md` en `audits/AAAA-MM-JJ-<ton-nom-ou-modele-IA>.md`, remplis-le, et propose-le
  (pull request, ou envoie-le à Louis s'il n'a pas les droits d'écriture).
- **Une issue GitHub** par problème (modèle « Remarque d'audit »).
- **Un commentaire de relecture** sur une pull request, ligne par ligne.

Pour chaque remarque : fichier et ligne, ce qui est faux, pourquoi (calcul, source, manuel), gravité
(**danger matériel / erreur de résultat / amélioration**), et la correction proposée. Ne modifie pas le code toi-même
dans la même pull request que tes notes : Louis tranche, puis la correction est faite à part.

## Ce qui ne compte pas comme un défaut
Les valeurs marquées « À CONFIRMER » ou listées dans les questions ouvertes de `journal/ETAT.md` : elles attendent une
mesure sur site. Signale-les seulement si la méthode prévue pour les confirmer est fausse.
