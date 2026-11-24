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
- **Classes de positions** (McCarthy 2007, ch. 10 ; reprises par `plan_3d.py`) :
  - **ONAX** : point le plus isolé de chaque sous-système (sur son axe, à mi-profondeur). Sert à l'EQ, au niveau et au
    contrôle de la salle ;
  - **OFFAX** : bords de la zone public (fond, côté, premier rang). Sert à l'orientation ; ≤ 6 dB sous l'ONAX ;
  - **XOVR** : point d'égal niveau entre deux sous-systèmes. Il se cherche à la mesure et se déplace vers le plus
    faible. Sert aux délais et à la phase, jamais à l'EQ ;
  - **SYM** : le symétrique d'une position déjà calée, pour vérifier.
- Plusieurs positions : superposer les courbes et suivre l'enveloppe. Une moyenne
  (`osm_client.py moyenne`, en puissance pondérée par la cohérence) ne réunit que des positions ONAX comparables. Ne
  jamais additionner des micros. Une moyenne en dB serait tirée vers le bas par les annulations : dans l'exemple de
  McCarthy, 3 positions à +5 / +5,5 / +6 dB et une à −19,2 dB donnent −0,7 dB en dB, mais +4,3 dB en puissance.
- Résolution : pour décider d'un filtre d'EQ, lire au 1/24 d'octave (`osm_client.py lire … --resolution 24`,
  `mesure_tf.py analyser … --resolution 24`). Le tiers d'octave par défaut ne sert qu'à la vue d'ensemble.
- Micro : hauteur debout (environ 1,6 m) pour éviter les dossiers de sièges vides, au sol (ground plane, +6 dB à
  compter) pour le grave ; une tolérance d'orientation de ±30° suffit. Dans une salle qui va se remplir, ne pas
  corriger les réflexions de sol ou de sièges, qui disparaîtront avec le public.
- Cohérence faible (< 0,8) ⇒ ne pas décider sur ces fréquences (`osm_client` marque chaque bande « fiable » ou non).
- Contrôles de vraisemblance : l'ONAX doit être la meilleure réponse, sinon chercher l'erreur (mauvais axe, mauvaise
  enceinte). En doublant la distance sur l'axe, on doit perdre environ 6 dB. Les deux côtés symétriques doivent
  donner la même réponse.

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

## Départager des variantes de coupure : `tools/essai_coupures.py` (automatique, REW, un micro)
Prérequis : REW lancé avec `-api`, **timing reference en boucle** (sinon refus : les phases de deux sweeps ne seraient
pas comparables), interface en ASIO, micro calibré ; VENU360 sur un preset « 6 × Mono Fullrange » à seuils protégés
(sous les plafonds calculés).
1. `essai_coupures.py preparer data/configs/<sys>.yaml --style <style> --positions P1:20x1.7,P2:20x1.2,P3:10x1.7
   [--variantes 3] [--accepter-non-verifie]` → témoin (config actuelle) + meilleures variantes de `coupures.py`, une
   config et un plan par variante. Aucun accès machine, aucun son. P1 = point du plan (ONAX) ; ≥ 2 positions pour
   mesurer la régularité en hauteur (= les lobes).
2. Louis place le micro en P1 et confirme : **niveau sûr, personne exposé, pas de concert** → seulement alors
   `essai_coupures.py mesurer <dossier> --position P1 --ip <ip> --protection-validee --signal-autorise --appliquer
   [--niveau-max -12]`. Une position par lancement : entre deux, Louis déplace le micro et le dit.
   Déroulé par variante : plan écrit sorties mutées, chaque voie seule, alignement à chaque raccord (1re position :
   temps d'arrivée puis phase, REW `aligner` contre-vérifié par `analyse_raccord.meilleur_delai`, ±0,5 ms par écriture
   puis remesure ; une inversion de polarité est seulement PROPOSÉE), chaque paire, système complet. Niveau PAR VOIE :
   min(−20 dBFS, plafond) puis +3 dB tant que le signal est à moins de 15 dB au-dessus du bruit (mesure sorties toutes
   mutées), jamais au-dessus de `--niveau-max` ; paires et système au plus bas des niveaux concernés. À la fin, et sur
   toute erreur ou Ctrl-C : 6 sorties mutées, valeurs touchées remises à l'état du début du lancement (relu à chaque
   lancement, instantané complet dans journal/instantanes) — sauf protection moins sûre (seuil au-dessus du plafond,
   limiteur Off), signalée et non réécrite ; sorties LAISSÉES MUTÉES (Louis démute) ; code non nul si incomplet.
3. `essai_coupures.py conclure <dossier>` → `rapport.md` : par variante et raccord, perte de sommation, écart de
   phase, ondulation contre la cible du style, régularité entre positions ; pénalité mesurée et score (puissance
   calculée − pénalité). Ajout à l'historique d'apprentissage, `recalage-propose.yaml` (délais du témoin).
4. Écouter la gagnante et le témoin avec un même morceau de référence ; décision de Louis ; écriture par
   `coupures.py --ecrire N` (rang du rapport de coupures) puis `preset_auto` et `appliquer-plan`. Noter dans ETAT.
Essai à blanc (aucun son) : `mesurer … --simulation [--erreur-sim MEDIUM:-6]` — acoustique calculée depuis ce qui est
réellement écrit dans le processeur, sorties JAMAIS démutées (mutes virtuels) ; sert à vérifier la chaîne avant le jour
du banc. Limites : SIGTERM non intercepté ; Ctrl-C ne stoppe pas un sweep REW en cours (sorties mutées).

## Optimiser tout le preset : `tools/optim_preset.py` (automatique, REW, un micro)
Prérequis : ceux de l'essai des coupures ; et, une fois par firmware, le **banc électrique**
`banc_electrique.py <config> --sortie N --ip <ip> --amplis-coupes --signal-autorise --protection-validee --appliquer [--enregistrer]`
(boucle sortie N → entrée de l'interface, AMPLIS ÉTEINTS) : modèle de Band_1_Phase (constante / retard / table),
ordre PEQOut / limiteur (seuils de test toujours plus bas que l'actuel), contrôles gain et délai. `--enregistrer`
écrit `data/protocoles/venu360_phase.yaml` seulement si tout est conforme et rétabli.
1. `optim_preset.py preparer data/configs/<sys>.yaml --style <style> --positions P1:20x1.7,P2:20x1.2,P3:10x1.7
   [--etapes gains,delais,phase,eq-voies,eq-generale] [--eq-generale <yaml>]` : aucun accès machine, aucun son.
2. Micro en P1, « go » de Louis (niveau sûr, personne exposé, pas de concert) :
   `optim_preset.py mesurer <dossier> --position P1 --ip <ip> --protection-validee --signal-autorise --appliquer`.
   P1 : plan écrit sorties mutées ; GAINS (niveau de chaque voie seule dans sa bande intérieure contre la cible du
   style, `Band_1_Gain` ±3 dB par écriture, gains ≤ 0) ; DÉLAIS (comme l'essai) ; PHASE (raccords à perte de somme
   > 0,5 dB : rotation de la voie basse prédite par le modèle du banc, ±45° par écriture, gardée seulement si la
   mesure confirme). Positions suivantes : réglages trouvés réécrits, voies seules et système (moyenne spatiale).
3. Micro laissé à la dernière position : `optim_preset.py egaliser <dossier> --ip …` → EQ PAR VOIE (moyenne
   spatiale ; fréquences fiables = écart entre positions < 2 dB ; creux étroits jamais remplis ; bosses ≤ +3 dB, jamais
   sous 1,5 × HPF, et seulement si le banc a établi « PEQ avant limiteur ») puis EQ GÉNÉRALE (système, Q ≤ 2,
   Σ|gain| ≤ 6 dB, 40 Hz/1,5 × HPF – 12 kHz). ±3 dB par écriture ; retirée si la mesure ne s'améliore pas.
4. `optim_preset.py conclure <dossier>` → `rapport.md` avant/après, `recalage-propose.yaml`, `eq-generale.yaml`,
   historique. Rien n'est stocké dans le processeur : preset définitif = `preset_auto --recaler` + `preset_auto
   [--eq-generale]` + `appliquer-plan`, après écoute et « go » de Louis.
Sans `--appliquer` (essai, optimisation, banc électrique) : ANNONCE de ce qui serait écrit (blocs, nombre de paramètres, pas), rien n'est touché, code 0 ; on la montre à Louis, puis on relance avec `--appliquer` sur son « go ».
Garde-fous identiques à l'essai (banc.Seance) ; seuils, filtres et polarité jamais modifiés par l'optimisation.
Essai à blanc : `--simulation` (machine jamais démutée).

## Recalage des calculs par la mesure (`preset_auto --recaler`)
Après application d'un plan, mesurer et noter dans `mesures/<sys>-<date>.yaml` :
```yaml
date: 2026-11-30
point: {distance_m: 20, hauteur_m: 1.7}       # même point que le plan
voies:
  HIGH:   {delai_mesure_ms: 0.0}               # délai qui aligne la voie (IR puis phase) ; la voie de référence du plan
  MEDIUM: {delai_mesure_ms: 1.10, ecart_niveau_db: -1.5}   # niveau dans la bande − niveau visé (ONAX), dB
  SUB:    {delai_mesure_ms: 4.9, gain_ampli_mesure_db: 37.4, accord_hz: 32}   # voltmètre (HP débranchés) ; impédance
```
`python tools/preset_auto.py --recaler data/configs/<sys>.yaml mesures/<sys>-<date>.yaml` : délai → centre acoustique
effectif (`profondeur_cm`), gain mesuré → `gain_canal_db` (donc seuils recalculés), accord → `accord_event_hz` (HPF
revérifié), écart de niveau → `correction_sensibilite_db`. Tout va dans `data/configs/<sys>.recalage.yaml` avec
l'historique des écarts : c'est ce qui permet d'améliorer les modèles (noter la synthèse dans ETAT). Garde-fous : gain
mesuré qui relève un plafond de limiteur → `--protection-validee` (accord de Louis) ; écart de gain > 3 dB →
`--forcer-gain` après contre-mesure.

## Ce que Claude ne fait pas
- Décider d'une EQ ailleurs qu'en ONAX, sur une vue en tiers d'octave, ou à faible cohérence.
- Égaliser un pic qui varie d'une chanson à l'autre pendant le concert : c'est la scène (fuite des instruments ou
  peigne entre micros dans la console), pas le système. Le signaler au mixeur (McCarthy p. 477).
- Corriger un creux d'annulation à l'EQ (c'est un problème de délai, de placement ou de polarité).
