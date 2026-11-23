---
name: fiche-evenement
description: Remplir avec Louis la fiche d'un événement (lieu, disposition des piles, public, style, réglementation, positions micro, réseau) avant tout calage, puis en déduire délais physiques, points de vigilance et plan de mesure.
---

# Fiche événement — préparer un calage

Avant de toucher au processeur, le contexte doit être complet. Modèle : `data/evenements/_modele.yaml`.
Sortie : `data/evenements/AAAA-MM-JJ_<lieu>.yaml` (copie du modèle remplie).

## Déroulé
1. Vérifier si une fiche existe déjà pour cette date/ce lieu ; sinon copier le modèle. Demander quel système est
   utilisé : config existante dans `data/configs/`, sinon skill `nouveau-systeme` d'abord.
2. Poser les questions **par blocs courts** (3–5 questions max par message), dans cet ordre :
   événement & style → réglementation → lieu & météo → public & régie → disposition du système → mesure & réseau.
   Utiliser le choix multiple quand c'est possible. Ne jamais remplir un champ par supposition : `null` + question.
3. Distances : demander des valeurs mesurées. Si Louis estime, noter `# estimé` en commentaire et le signaler
   comme à re-mesurer sur site.
4. Écrire la fiche au fur et à mesure (rien ne se perd si la session coupe).

## Ce qu'on en déduit (à présenter à la fin)
- **Délais physiques** : distance pile → positions micro et pile → régie, via
  `python tools/calc_systeme.py --delai <m> --temp <°C>` (température de la fiche). Écart de trajet entre piles et
  zones du public → zones de filtrage en peigne à surveiller.
- **Config système** : vérifier que `systeme.config` existe, que chaque élément des piles est une voie de la config
  (ou un élément annexe signalé) et que l'empilement correspond au câblage déclaré.
- **Subs** : si cardioïde ou ligne, calculer espacements/délais à partir des distances (méthode §5) et les marquer
  « à valider par mesure ».
- **Réglementation** (décret 2017-1244, art. R.1336-1 CSP) : LAeq 15 min ≤ 102 dB(A) et LCeq 15 min ≤ 118 dB(C) partout
  où le public a accès ; 94 dB(A) / 104 dB(C) si l'activité est destinée aux enfants jusqu'à 6 ans révolus.
  Festivals et lieux > 300 places : enregistrement + affichage dB(A)/dB(C), conservation 6 mois. En tekno/dub,
  le dB(C) est souvent la limite qui bloque : le dire et prévoir la mesure LCeq à la régie.
- **Style** : courbe cible correspondante (skill `methode-calage`), niveau visé à la régie.
- **Plan de mesure** : ordre (chaque voie seule → paires autour de chaque Fc → système), positions micro, temps estimé
  vs `temps_dispo_calage_min`.
- **Liste des manques** : champs encore `null` qui bloquent une étape du calage.

## Schéma 3D et plan de mesure
Dès que la fiche contient les piles (position, orientation, empilement), la zone public et la régie :
`python tools/plan_3d.py data/evenements/<fiche>.yaml -o plans/<fiche>.html`
→ page autonome (hors ligne) : vue 3D orbitable, vue de dessus cotée, positions micro P1…P5 avec distances et temps de
vol vers chaque voie de chaque pile, premier creux d'interférence G/D, pré-alignement physique à P1, procédure.
Montrer la page à Louis et ajuster la fiche s'il corrige une position. Photos/vidéos du site : les regarder pour
VÉRIFIER la fiche (réflexions, obstacles, orientation, cohérence des cotes) — jamais pour en déduire une distance.
Vidéo : extraire des images (`ffmpeg -i video.mp4 -vf fps=1 images/%03d.jpg`) puis les lire.

## Règles
- Ne pas demander ce qui est déjà dans `data/configs/` ou `data/materiel.yaml`.
- Fiche = faits du jour ; les choix de réglage restent dans le journal (`journal/ETAT.md`).
- Fin de préparation : ajouter une ligne dans `journal/ETAT.md` (« Fiche <date> <lieu> prête, manques : … »).
