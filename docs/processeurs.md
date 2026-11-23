# Processeurs et contrôleurs — ce que le kit sait faire

Mis à jour : 2026-11-22. Principe : quel que soit le processeur, la méthode est la même ; seule change la façon
d'appliquer les réglages.

| Niveau | Signification | Processeurs |
|---|---|---|
| **A — pilotage réseau** | Claude lit l'état complet, écrit (après « go ») et relit, mémorise le preset | dbx DriveRack **VENU360**, **PA2** (`tools/dbx_hiqnet.py`) · **Behringer DCX2496** (`tools/dcx2496.py`, RS-232) · **t.racks DSP 206 / 204 / 408** (`tools/tracks_dsp.py`, TCP 9761) |
| **A partiel** | protocole publié mais ne couvrant qu'une partie des réglages | **Linea Research ASC-48** (`tools/linea_tipi.py`, TIPI TCP 51456 : gains, mutes, snapshots, mesures — le reste en C) |
| **B — protocole documenté, à intégrer** | un client peut être écrit sur le même modèle | à vérifier au cas par cas (ex. Meyer GALAXY : ASCII/OSC publié) |
| **C — saisie guidée** | Claude produit la **feuille de saisie** ; Louis saisit ; relecture ou photo d'écran | contrôleurs constructeur, DSP d'enceintes amplifiées, BSS, XTA, Xilica, partie System Engineer de l'ASC-48… |

**Statut matériel au 2026-11-22** : VENU360/PA2, DCX2496, DSP 206 et ASC-48 sont testés contre des **simulateurs**
seulement. Première connexion réelle de chacun : `lire` / `sonder` en lecture seule, comparer à l'écran de
l'appareil, puis UNE écriture non critique (gain −0,5 dB puis retour) relue, avant tout autre réglage.
Détails par appareil : `docs/protocoles-autres.md`.

## Ajouter un processeur
1. Mettre son manuel (PDF) dans le projet ou le dossier ; relever : plages de réglage, types de filtres, pas de réglage,
   unité des seuils de limiteur (dBFS / dBu / V) et niveau de sortie à 0 dBFS, latence.
2. L'ajouter dans `data/materiel.yaml > processeurs` (avec la source).
3. Chercher s'il existe un protocole publié ou une appli de contrôle réseau (même méthode que pour le VENU360 :
   doc constructeur, projets open source, capture Wireshark de l'appli officielle, décompilation de l'appli Android
   pour interopérabilité) → niveau B puis A.
4. Ne jamais envoyer de commande à un processeur dont le protocole n'a pas été vérifié en lecture seule d'abord.

## Systèmes constructeur
Les contrôleurs propriétaires (amplis-contrôleurs à presets usine) restent en niveau C sauf protocole publié :
Claude choisit et vérifie les presets et calcule délais/gains/EQ système ; il ne modifie jamais l'intérieur d'un preset.
