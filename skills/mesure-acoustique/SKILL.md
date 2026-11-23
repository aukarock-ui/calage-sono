---
name: mesure-acoustique
description: Mesurer et analyser la réponse d'un système son (fonction de transfert double canal, délai, polarité, phase à la coupure, cohérence, moyenne spatiale) avec Open Sound Meter piloté en réseau (moteur principal), mesure_tf.py ou REW.
---

# Mesure acoustique pour le calage

## Moteurs de mesure (par ordre de préférence)
1. **Open Sound Meter (OSM)** — open source (GPL-3), double FFT temps réel façon Smaart : magnitude, phase, cohérence,
   réponse impulsionnelle, générateur. Claude lit les données en direct via `tools/osm_client.py`
   (serveur JSON intégré à OSM, TCP/UDP 49007).
   - Installation (Windows) : télécharger la dernière version sur github.com/psmokotnin/osm/releases (ou
     opensoundmeter.com) et lancer l'installeur — Louis valide l'invite Windows. Pas d'installation silencieuse.
   - Réglage une fois : dans OSM, ouvrir Remote → cocher **Server** ; cocher **enable for remote** seulement si
     Claude doit piloter le générateur. Pare-feu : autoriser OSM et Python sur 49007.
   - Dans OSM, Louis crée les mesures (entrée micro + entrée référence, calibration micro, délai auto) et les nomme
     clairement (« SUB », « KICK », « P2 »…). Claude ne configure pas les entrées audio.
   - Commandes : `osm_client.py ecouter` (trouve OSM et ses mesures), `lire <hôte> <nom> -o mesures/x.json`,
     `comparer A.json B.json --fc 55`, `moyenne P1.json P2.json P3.json -o moy.json`,
     `generateur <hôte> --gain -30 --on --appliquer` (plafonné à −10 dBFS, accord de Louis obligatoire).
   - Avant de lire : laisser la moyenne d'OSM se stabiliser (quelques secondes) ; vérifier la cohérence.
2. **`tools/mesure_tf.py`** — sweep log double canal, sans logiciel tiers (`pip install sounddevice numpy scipy`) ;
   utile si OSM n'est pas disponible. `autotest` vérifie les calculs.
3. **REW** (installé : 5.40 beta 121) — piloté directement par `tools/rew_client.py` (API REST locale, port 4735,
   REW lancé avec `-api`), sans MCP. Sweep log : meilleur rapport signal/bruit qu'OSM, IR propre, distorsion,
   impédance (accord d'évent), outil d'alignement. Commandes : `info`, `liste`, `lire <id>`, `ir <id>`,
   `aligner <A> <B> --fc <Hz>`, `importer <ir.wav>`, `mesurer --nom … --niveau … --appliquer` (joue un sweep :
   accord de Louis obligatoire, plafond −10 dBFS, arrêt sur écrêtage forcé), `autotest` (aucun son).
   Mesures de grave et d'impédance : préférer REW ; mesures pendant les balances : OSM.
4. **Smaart** — exports ASCII dans `mesures/` (API : SDK sur demande, non intégré).

## Protocole de mesure
- Micro calibré, fichier de calibration chargé, position notée (distance, hauteur, angle) dans le nom du fichier :
  `mesures/<date>_<voie>_<pos>.npz`.
- Chaque voie SEULE d'abord (autres voies mutées), puis par paires autour de chaque Fc, puis système complet.
- Niveau : départ −20 dBFS, +3 dB par pas, jamais au-delà de ce qui est nécessaire pour 10–15 dB au-dessus du bruit.
- Grave : mesures en extérieur au sol (demi-espace) ou à ≥ 2× la hauteur de pile ; noter la température (vitesse du son).
- Moyenne spatiale : ≥ 3 positions pour toute décision d'EQ ; une seule position suffit pour l'alignement si elle est
  sur l'axe de sommation des deux voies.
- Cohérence faible (< 0,8) ⇒ ne pas décider sur ces fréquences (`osm_client` marque chaque tiers d'octave « fiable » ou non).

## Alignement à une coupure (ex. SUB/KICK à 55 Hz)
1. Mesurer A seul et B seul, même position micro, même niveau de référence.
2. `python tools/osm_client.py comparer A.json B.json --fc 55` (ou `mesure_tf.py comparer A.npz B.npz --fc 55`,
   ou `rew_client.py aligner <A> <B> --fc 55`) → écart de niveau, écart de phase, correction de délai proposée.
   **Règle : IR d'abord, phase ensuite.** Un alignement en phase seul peut tomber à un cycle près (démontré par
   `rew_client.py autotest` : B à +1,5 ms inversée → la phase seule à 300 Hz propose +0,13 ms non inversé, l'IR
   donne −1,5 ms inversé). On cale d'abord grossièrement sur les pics d'IR (filtrées autour de Fc), puis on affine
   à la phase ; si les deux divergent de plus d'un quart de période, on garde l'IR et on vérifie par la somme.
3. Ajuster le délai de la voie en avance : Δt (ms) = écart de phase (°) / (360 × Fc) × 1000. Pas max 0,5 ms.
4. Tester polarité inversée si l'écart dépasse ±90° sans solution de délai raisonnable.
5. Valider en mesurant A+B : sommation ≈ +6 dB à Fc pour des niveaux égaux.

## Ce que Claude ne fait pas
- Décider d'une EQ sur une mesure isolée ou à faible cohérence.
- Corriger un creux d'annulation à l'EQ (c'est un problème de délai, de placement ou de polarité).
