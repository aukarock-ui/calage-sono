# Audit approfondi n° 2 du 2026-11-23 (révision fea2cf6) : vérification par Claude

**Source :** `audit_calage_sono_2026-11-23.txt`, fourni par Louis. L'auteur n'est pas indiqué.

**Méthode de l'audit :** revue statique, sans matériel.

**Vérification :** j'ai relu chaque constat dans le code. Les points logiciels sont corrigés et testés sans matériel :
- faux serveur TIPI dans les tests ;
- simulateur `faux_venu360` ;
- OSM simulé dans les tests.

**Résultat :** 49 tests passent (39 avant).

| Constat | Verdict | Suite donnée |
|---|---|---|
| **P1 2.1** : le générateur OSM s'allume sans niveau imposé | Confirmé | `--on` exige `--gain` (≤ −10 dBFS). Gain et ON partent dans le même message. `--on --off` est refusé, une commande vide aussi |
| **P1 2.2** : dbx Load, Store et RPC partent sans instantané | Confirmé | `rappel` exige `--protection-validee`. `rappel` et `stocker` font une carte complète dans `journal/instantanes/` avant d'agir, et journalisent le nom du preset. Une réponse en erreur sort en code 1. Après un rappel, le preset chargé est relu. `rpc` passe en liste blanche de lecture : `Load`, `Store` et toute méthode inconnue sont refusés |
| **P1 2.3** : TIPI ne filtre pas CR/LF et n'a pas de liste blanche | Confirmé, **plus grave qu'annoncé** | Le `GET` envoyé avant le `SET` partait avant tout contrôle, donc une injection passait même en simulation. Corrections : grammaire stricte de la méthode et de la valeur, vérifiée avant la connexion ; contrôle des caractères dans `_envoyer` ; liste blanche des méthodes modifiables. Les mesures, les identifiants et `LimThresh` sont exclus, car aucun plafond calculé n'est vérifié pour ce dernier |
| **P1 2.4** : le SUB actuel n'a pas de HPF renseigné | Déjà signalé par `calc_systeme` | Point de mise en service : relever le HPF réel et l'accord des évents (journal) |
| **P1 2.5** : la refonte dépasse Xmax au plafond thermique | Déjà au journal (14,9 mm pour 13,5 mm ; Xmax vers 99 V RMS) | Xmax est désormais traité comme une contrainte distincte du plafond thermique. La décision revient à Louis, après mesure du T/S et de l'accord |
| **P2 3.1** : la relecture TIPI n'est pas stricte | Confirmé | Relecture comparée à la consigne (numérique avec tolérance, ou texte). Un écart est journalisé `ECART` et sort en code 1, une erreur de l'appareil aussi |
| **P2 3.2** : la couverture de tests est incomplète | Confirmé | 10 tests ajoutés : TIPI, OSM, dbx rappel/stocker/rpc, réglage de sortie. REW n'est pas dans pytest, car il faut REW en marche. Il a été testé à la main sur le vrai REW 5.40 b121 : autotest, import, lecture, refus de `mesurer` (voir journal) |
| **P2 3.3** : les arguments OSM ne sont pas contrôlés | Confirmé | Nombre d'arguments vérifié par commande. `--fc` > 0, `--bande` vérifiée. Un fichier de mesure illisible ou sans `ftdata` est refusé |
| **P2 3.4** : la moyenne OSM suppose des grilles identiques | Confirmé | Refus si le nombre de points ou une fréquence diffère |
| **P2 3.5** : le DCX2496 n'est pas validé sur l'appareil | Déjà au journal | Validation terrain (lecture seule, puis une écriture non critique relue) |
| **P2 3.6** : le réglage OutputGain du VENU360 n'est pas lu | Confirmé | **Nouveau garde-fou :** `processeur_reglage_sortie_confirme: false` dans les configs. `calc_systeme` l'affiche en alerte, et `verifier_plafond` refuse d'écrire le seuil tant que ce champ ne vaut pas true |
| **P2 3.7** : Beyma SM118 et SM115 en puissance brochure, non AES | Déjà signalé | Question ouverte (fiches de génération ou mesure) |
| **P2 3.8** : VENU360 avec le mot de passe d'usine | Déjà averti | Changer le mot de passe sur la machine, `DBX_MDP`, réseau isolé |

Corrigé en passant : les messages de refus (`sys.exit`) sortaient en cp1252 sur la console Windows. Tous les outils passent aussi stderr en UTF-8.

## Relecture par l'agent `controleur-securite` (premier essai de l'agent, sur le diff fea2cf6..689e1c6)

Constats vérifiés dans le code, puis corrigés, avec un test pour chacun (53 tests) :

| Gravité | Constat | Correction |
|---|---|---|
| P1 | `generateur --gain nan` (ou `-inf`) passait le garde-fou : `nan > -10` vaut faux | Gain borné à [−90 ; −10] dBFS, ce qui refuse aussi nan et inf |
| P1 (à vérifier) | Gain et allumage partaient dans un seul message JSON, sans garantie d'ordre côté OSM | Deux messages successifs, gain d'abord ; chaque envoi est journalisé |
| P2 | TIPI : pas de pas maximum par écriture (règle ±3 dB / ±0,5 ms), pas d'instantané | `controle_pas` refuse au-delà, même en simulation ; instantané avant l'écriture |
| P2 | Rappel dbx : le preset chargé n'était pas comparé au preset demandé | Comparaison du nom : ECART et code 1. Avertissement de relire les seuils du preset chargé |
| P2 | Instantané avant rappel ou stockage : pouvait être vide sans bloquer | Refus si moins de 20 valeurs ou sans limiteur ni crossover |
| P3 | Numéro de preset « 05 » pas normalisé ; refus décidés après la connexion | Normalisé (1 à 100) ; refus de rappel, stocker et rpc avant toute connexion |
| P3 | TIPI : « 15000Hz » relu « 15kHz » donnait un faux écart | Comparaison des unités via `garde_fous.nombre` |

Non retenus pour l'instant (P3) :
- `PresetRecallList` dans la liste de lecture : elle vient de la liste « lecture » de l'appli dbx. À confirmer sur l'appareil.
- Faire stocker la valeur lue d'OutputGain plutôt qu'un simple booléen.
- Confirmation du 0 dBFS pour le DCX2496.
