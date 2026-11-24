---
name: nouveau-systeme
description: Décrire une sono que le kit ne connaît pas encore (la sienne, celle d'un loueur, d'une salle ou d'un festival) — inventaire des éléments, câblage, amplis, processeur, presets constructeur, dimensions — et l'enregistrer comme config réutilisable avant tout calage.
---

# Nouveau système — de l'inventaire à la config

Sortie : `data/configs/<nom>.yaml` (copie de `data/configs/_modele-systeme.yaml`) + composants nouveaux ajoutés à
`data/materiel.yaml` (bibliothèque partagée). Une config sert ensuite à toutes les dates avec ce système.

## Sources acceptées (par ordre de fiabilité)
1. Fiches constructeur / manuels (PDF) — seules sources de chiffres (puissance AES, impédance, sensibilité, T/S, plages DSP).
2. Fiche technique du lieu ou du loueur, plan de câblage, patch.
3. Ce que Louis lit sur le matériel (étiquettes, sélecteurs d'ampli, écran du processeur) — noté comme tel.
4. Photos/vidéos : pour identifier les éléments, compter, repérer le câblage et l'empilement ; jamais pour un chiffre
   de spec. Vidéo → `ffmpeg -i v.mp4 -vf fps=1 img/%03d.jpg` puis lire les images.
Interdit : compléter une spec « de mémoire » ou par analogie avec un modèle voisin. Inconnu = `null` + question.

## Déroulé (questions par blocs courts, choix multiples quand c'est possible)
1. **Type** : composants, constructeur, amplifiée ou mixte ? Propriétaire ? Processeur / contrôleurs ?
2. **Voies**, du grave à l'aigu : nom, sortie processeur.
   - composants : HP (modèle exact, génération), nombre, série/parallèle, charge (BR/clos/pavillon), accord si connu,
     ampli (modèle, mode stéréo/bridge, gain/sensibilité sélectionnés) ;
   - constructeur/amplifiée : modèle d'enceinte, contrôleur/ampli, preset utilisé.
3. **Processeur** : modèle, firmware, réglage de niveau de sortie, unité des seuils ; pilotable ? (`docs/processeurs.md`).
4. **Crossover / presets en service** s'ils existent déjà (relevé écran ou export) → champ `crossover`.
5. **Dimensions** de chaque élément tel que posé (l × p × h, cm) → champ `caisses` (sert au plan 3D).
6. **Géométrie de pile** (pour les délais calculés) → `geometrie_pile` : hauteur du centre de chaque HP au-dessus
   du sol, retrait du HP derrière la face avant, longueur des pavillons ; une photo de la pile aide
   (`data/configs/photos/`). Centres acoustiques = hypothèses jusqu'à l'IR.
7. **Caisses bass-reflex** : accord (mesure d'impédance) et volume net si connus ; sinon une plage supposée →
   `hypotheses_caisse` (le HPF est vérifié sur toute la plage). **Style par défaut** → `style_defaut`.
8. **Composants absents de la bibliothèque** : demander la fiche PDF, relever les valeurs, les ajouter avec la source.

## Contrôles avant de déclarer la config prête
- `python tools/calc_systeme.py data/configs/<nom>.yaml` : aucune alerte « absent de la bibliothèque » ; impédances
  vues par chaque ampli cohérentes avec ses charges minimales ; marges ampli lisibles.
- Toutes les voies d'une fiche événement doivent exister dans `voies` et `caisses`.
- Lister les manques bloquants (ex. gain ampli non relevé, preset inconnu) et les ajouter à `journal/ETAT.md`.

## Règles
- Ne pas modifier une config d'un autre système pour en faire une nouvelle : copier le modèle.
- Les préférences de son propriétaire (voicing, habitudes) vont dans `preferences`, pas dans la méthode.
