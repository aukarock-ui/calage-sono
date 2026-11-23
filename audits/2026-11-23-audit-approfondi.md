# Audit approfondi du 2026-11-23 (révision ecbe238) — vérification par Claude

Source : `Audit_calage-sono_2026-11-23.txt`, fourni par Louis. L'auteur n'est pas indiqué, probablement une seconde passe ChatGPT.
Méthode de l'audit : revue statique, sans matériel ni exécution des autotests.
Vérification : chaque constat a été relu dans le code, puis corrigé et testé sans matériel (autotests, simulateur faux_venu360).

| Constat | Verdict | Suite donnée |
|---|---|---|
| P0 `mesure_tf.py` lance un sweep sans verrou | **Exact** | `--appliquer` obligatoire. Sans lui, le script affiche ce qui serait émis et la liste de contrôle, puis s'arrête avant d'ouvrir l'audio. `--dev` est obligatoire |
| P1 Plafond de limiteur non imposé par le code | **Exact** | `garde_fous.verifier_plafond` + `calc_systeme.plafond_limiteur`. L'écriture d'un seuil exige `--config` et `--voie`. Un seuil au-dessus du plafond est refusé. La dérogation `--depasser-plafond` est journalisée. Le plafond est vérifié dans l'unité du processeur : dBFS pour dbx, dBFS pour DCX (hypothèse 0 dB = +22 dBu, à vérifier), dBu pour t.racks |
| P1 Relecture DCX/t.racks : la consigne n'est pas vérifiée | **Exact** | Le champ visé est comparé à la valeur quantifiée attendue. En cas d'écart : journal `ECART` et code de sortie non nul. Les autres canaux restent surveillés (alerte de propagation). Pour dbx, la relecture stricte a la même tolérance |
| P1 DCX : hors plage non refusé | **Exact, et plus grave que décrit** | `round(abs(min − v)/step)` repliait une valeur sous le minimum vers le haut : −30 dB sur un seuil de −24…0 donnait −18 dB, donc **moins protecteur**. Corrigé en `(v − min)/step`, avec refus hors [min, max]. Les booléens sont stricts et les enums numériques sont refusés hors plage |
| P1 DBX : la carte n'est pas utilisée comme validateur | **Exact** | `regle_carte` / `valider_valeur` lisent `venu360_parametres.yaml` : plage, enum, unité. Un chemin inconnu est refusé sauf avec `--hors-carte`. Le YAML lui-même était invalide (lignes 87/91) : corrigé |
| P1 DBX : CR/LF non neutralisés | **Exact** | `refuser_controle` dans `_envoyer`. Tout caractère de contrôle est refusé ; autotest ajouté |
| P1 HPF : `dsp_depart` non lu par le calculateur | **Exact** | `filtres_voie` lit `crossover`, sinon `dsp_depart`. La refonte affiche maintenant « HPF 29 Hz sous l'accord 34 Hz » |
| P1 HPF bass-reflex : seule l'absence est détectée | **Exact** | Alerte si le HPF est sous l'accord et si l'accord est inconnu. La mention « ne certifie pas Xmax » est ajoutée. La pente n'est pas encore évaluée : pas de règle sourcée |
| P2 t.racks : fréquence et Q bornés en silence | **Exact** | Refus hors 19,7…20 160 Hz et hors Q 0,4…128. Bande PEQ 1…9 validée |
| P2 t.racks : mute/polarité mal tapés acceptés | **Exact** | Listes fermées, erreur sinon |
| P2 Limites du calcul électrique peu visibles | Juste | Docstring : « calcul théorique à confirmer par mesure ». Le coussin d'overshoot est affiché. Une mention dans le rapport imprimé est encore à ajouter |
| P2 SM118/SM115 : 250 W non AES | Juste, déjà documenté | Reste bloquant pour la validation finale (ETAT) |
| P2 `mesure_tf.py` : une seule acquisition | Juste | **Pas fait.** OSM/REW restent les outils de référence pour la cohérence et les moyennes |
| P2 Pas de CI ni de verrouillage des dépendances | Juste | **Pas fait** (à prévoir : pytest + GitHub Actions Windows) |
| P2 `installer.ps1` sans venv | Juste | **Pas fait** |
| P2 Mot de passe dbx par défaut | Juste | **Pas fait** : variable d'environnement à ajouter. Le réseau isolé reste obligatoire (doc) |
| P2 Licence absente | Juste | **Pas fait** : décision de Louis. Attention aux données tierces exclues du dépôt |

Bilan : tous les constats de sécurité (P0/P1) sont exacts et corrigés. Restent à faire les P2 marqués « Pas fait ».
Aucune de ces corrections n'est validée sur un appareil réel.
