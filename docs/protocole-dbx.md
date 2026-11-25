# Protocole réseau dbx DriveRack (VENU360 / PA2) — notes de rétro-ingénierie

Mis à jour : 2026-11-22. Statut : PA2 documentée par une lib open source ; VENU360 : carte complète des paramètres
extraite de l'appli Android officielle v2.0.7 (décompilée le 22/11/2026) → `data/venu360_parametres.yaml`.
Reste à CONFIRMER sur la machine (`dbx_hiqnet.py carte`) : numérotation des instances selon la config chargée,
format exact des valeurs écrites (unités collées : « 90Hz », « -3.0dB », « 2.03ms »…).

## Sources
- PA2 : bibliothèque Python `dbxdriverack` — github.com/mkupferman/dbxdriverack-python (firmware PA2 1.2.0.1).
  dbx ne publie pas de documentation du protocole (réponse officielle sur le forum Harman).
- VENU360 : github.com/fiLLLip/dbx-venu360 (capture Wireshark de l'appli Windows ; mute, rappel de preset, noms des presets).
- Manuels VENU360 et PA2 : ports TCP/UDP 19272 + FTP 21 à ouvrir pour l'appli de contrôle.

## Transport
- TCP 19272, lignes texte UTF-8 terminées par `\n`. Plusieurs commandes peuvent partir dans un même paquet.
- Découverte : broadcast UDP 19272 de `get "\\Node\AT\Class_Name"` (+ Instance_Name, Software_Version).
- Ouverture : le client envoie une ligne vide → `HiQnet Console` → `connect administrator "<mdp>"` →
  `connect logged in as administrator` (échec : `error could not connect as ...`). Mdp par défaut : `administrator`. Le protocole n'est pas chiffré : réseau isolé obligatoire. Le kit lit `DBX_MDP` et ne journalise jamais le mot de passe.
- FTP 21 : probablement transfert de presets / firmware (non exploré).

## Syntaxe
| Commande | Réponse | Rôle |
|---|---|---|
| `get "<chemin>"` | `get "<chemin>" "<valeur>"` | lire |
| `set "<chemin>" "<valeur>"` | `setr "<chemin>" "<valeur relue>"` | écrire |
| `asyncget "<chemin>"` | — | lecture asynchrone (PA2) |
| `sub "<chemin>"` / `unsub` | `subr "<chemin>" "<valeur>"` à chaque changement | abonnement |
| `ls "<chemin>"` | lignes `clé : valeur` puis `endls` | lister un nœud |
| JSON `{"id":"<uuid>_x","method":"Load","params":["<n>"]}` | plusieurs JSON avec le même id, `done:false` puis final | rappel de preset (VENU360) |
| JSON `{"id":"..","method":"Get","params":["1","100","presetName"]}` | `result: {"1":"nom",...}` | noms des presets (VENU360) |

Chemins : `\\Preset\<Module>\SV\<Paramètre>` (SV = valeurs d'état, AT = attributs). Les valeurs sont des chaînes avec
unité (`"90.0 Hz"`, `"-3.0 dB"`, `"On"`). Certaines variantes en pourcentage ajoutent `\%` au chemin (PA2).
Mute VENU360 : valeur envoyée `"0"`/`"1"`, relue `"Off"`/`"On"`.

## Chemins confirmés — PA2 (lib dbxdriverack)
| Fonction | Chemin |
|---|---|
| Modèle / nom / version | `\\Node\AT\Class_Name`, `\\Node\AT\Instance_Name`, `\\Node\AT\Software_Version` |
| Crossover | `\\Preset\Crossover\SV\Band_<1-3>_{HPType,LPType,HPFrequency,LPFrequency,Gain,Polarity}` ; attributs `\\Preset\Crossover\AT` (NumBands, MonoSub) ; sub mono : `MonoSub_*` |
| PEQ de sortie | `\\Preset\{High,Mid,Low} Outputs PEQ\SV\Band_<n>_{Type,Frequency,Q,Slope,Gain}`, `ParametricEQ` (On/Off), `Flatten` |
| Délai de sortie | `\\Preset\{High,Mid,Low} Outputs Delay\SV\{Delay,Amount}` |
| Limiteur | `\\Preset\{High,Mid,Low} Outputs Limiter\SV\{Limiter,Threshold,OverEasy}` |
| Mutes | `\\Preset\OutputGains\SV\{Low,Mid,High}{Left,Right}OutputMute` |
| Générateur | `\\Preset\SignalGenerator\SV\{Signal Generator,Signal Amplitude}` (Off/Pink/White) |
| RTA | `\\Preset\RTA\SV\{Rate,Gain}` |

Types de filtres crossover : `BW 6/12/18/24/30/36/42/48`, `LR 12/24/36/48`.

## Chemins confirmés — VENU360
| Fonction | Chemin / commande |
|---|---|
| Mute de sortie n | `\\Preset\ZoneGains\SV\Channel_<n>_Mute` |
| Progression de rappel | `sub "\\Node\Config\SV\Progress"` |
| Rappel / noms de presets | JSON-RPC `Load` / `Get` (ci-dessus) |

## Carte VENU360 (appli harman.hpro.venu360 v2.0.7)
L'appli est en Qt/C++ ; les définitions des blocs DSP (« ProcessingObjects ») et 25 presets d'usine sont embarqués
dans `libVENU360_arm64-v8a.so`. Détail complet (plages, énumérations, défauts) : `data/venu360_parametres.yaml`.

| Bloc (sortie) | Instance | Paramètres SV |
|---|---|---|
| Crossover | `\\Preset\CrossoverIIR<k>` (une instance par crossover multi-voies) | `Band_<n>_{HPFrequency,HPType,LPFrequency,LPType,Gain,Polarity,Phase}` ; voie sub : `Sub_*` |
| PEQ de sortie (8 bandes) | `\\Preset\PEQOut<k>` | `ParametricEQ`, `Flatten`, `Band_<1-8>_{Type,Frequency,Gain,Q,Slope}` |
| Limiteur | `\\Preset\Limiter<k>` | `Limiter, Threshold, OverEasy, Auto, Attack, Hold, Release, PeakStopPlus, OvershootDb` (+ capteurs `GainReductionMeter`, `MaxInputLevel`, `ThresholdMeter`) |
| Délai d'alignement | `\\Preset\DelayOut<k>` | `Delay` (On/Off), `Amount` |
| Gains / mutes | `\\Preset\OutputGains`, `\\Preset\ZoneGains` | `Channel_<1-6>_{Gain,Mute}` |
| Générateur | SignalGenerator | `Signal Generator`, `Signal Type` (Pink/White/Sine), `Signal Amplitude`, `Sine Frequency`, `Signal Route` |

Points importants tirés du code :
- **Ordre des bandes : `Band_1` = voie la plus aiguë.** En « Mono 5-Way » : Band_1 HIGH, Band_2 MID-HIGH, Band_3 MID,
  Band_4 LOW-MID, Band_5 LOW. Une voie sub déclarée comme telle utilise le préfixe `Sub_`.
- HPF sous 16 Hz = filtre bypassé (valeur « Out ») ; LPF au-dessus de 20 kHz = « Out ».
- Gain de bande crossover −60…+20 dB ; phase −180…0° ; polarité « Normal »/« Inverted ».
- Limiteur : Threshold −60…0 (dBFS) ; Attack 0,1–200 ms ; Hold 0–500 ms ; Release 5–360 dB/s ;
  **PeakStopPlus actif par défaut, Overshoot 6 dB par défaut** — à vérifier sur chaque sortie.
- PEQ de sortie : 20 Hz–22 kHz, ±20 dB, Q 0,1–16, pente des shelfs 3–15 dB/oct.
- JSON-RPC : lecture `Get`, `GetInputRouterSourceNames`, `PresetIsModified`, `PresetRecallList` ; rappel `Load` ;
  mémorisation `Store ["<n>"]` ; `SetPresetName`, `SetPreset`, `EnDisAutoSave`, `CopyObjectSVs`.
  Destructifs (refusés par l'outil) : `FactoryRestore`, `BuildDB`, `CopyPresets`, `StorePresetFiles`, suppression.
- `\\Node\AT\Password`, `Reboot`, `\\Node\Config\SV\Command` : jamais en écriture depuis le kit.

## Phase du crossover et ordre de la chaîne de sortie
- `\\Preset\CrossoverIIR<k>\SV\Band_1_Phase` : −180 à 0 deg (relevé de la description). Effet réel (rotation
  constante ou passe-tout dépendant de la fréquence) : à mesurer par `tools/banc_electrique.py` (amplis coupés).
- Ordre `PEQOut<k>` / `Limiter<k>` : lu dans la topologie (24/11/2026, firmware 2.0.5.0). `\\Node\Config\SV\Config`
  contient `gridsOA` → `POMap`, la liste ordonnée des blocs ; les blocs `AutoWire: true` d'un même DSP y sont chaînés
  dans l'ordre. `wires` ne liste que les câblages manuels : prises de mètres (DelayOut<n> → OutputMeters, MidRouter<n>
  → MidMeters, Inputs2 → Meters123/MetersABC/RTARouter) et routage des Mid (Inputs2 A, B, C, générateur → MidRouter<n>).
  Chaîne de sortie (dsp3) : Inputs3 → CrossoverIIR<n> → PEQOut<n> → ZoneGains → OutputGains (mute) → Limiter<n> →
  DelayOut<n> → Outputs3. Donc PEQ de voie et mute AVANT le limiteur.
  `dbx_hiqnet.py chaine <ip> [--enregistrer]` → `data/protocoles/venu360_phase.yaml` (`chaine_sortie`, `chaine_ordre`) ;
  `appliquer-plan` le relit en direct avant toute bosse. Le banc électrique (boucle REW) mesure la `phase` ; un ordre
  « indéterminé » au banc ne remplace pas celui de la topologie.
- Mètres (24/11/2026) : seuls `Meters123` et `MetersABC` sont poussés par la console (lignes `set "<chemin>" "<val>"`,
  environ 10/s, valeur efficace). `OutputMeters`, `MidMeters`, `InputMeters`, `Limiter<n>` GainReductionMeter /
  ThresholdMeter / MaxInputLevel, `Compressor1` : une seule valeur, jamais mise à jour (même sortie démutée, signal présent).
  Outil : `tools/metres.py` (lecture seule).
- Générateur : `\\Preset\SignalGenerator\SV\{Signal Generator, Signal Type (Pink/White/Sine), Signal Amplitude (−60…0 dB),
  Sine Frequency (20 Hz-20 kHz), Signal Route (In1…In7, All)}` ; injection par `InRouter<n>\SV\SignalGeneratorControl`
  (SignalGeneratorOff/On/MuteAll) ; `MidRouter<n>\SV\SignalGeneratorControl` existe aussi. Constaté : sinus −30 dB
  → Meters123 / MetersABC à −33,0 dB.
- `RTARouter\SV\RouteSelect` : choisir « A » rend le paramètre illisible (`get` → error) ; « RTA Mic » le rétablit.
  « Signal Generator » est accepté et relu (25/11/2026).
- `RTA\SV\AllMeters` (31 bandes, chaîne de texte) : figé à « −80.0 » par la console, même avec le générateur rose à
  −30 dB routé vers le RTA (abonnement et `get` répétés, 25/11/2026). Comme les mètres de sortie, il n'est pas
  exploitable par le protocole texte.
  Option refusée par la machine dans cette topologie (autotest et lot du 24/11/2026).
- Niveau de sortie analogique `\\Node\Settings\SV\Output<n>Gain` (+4, +8, +12, +14, +17, +20, +22 dBu ; relu +22) :
  écrit seulement si la config porte déjà la valeur (`dbx_reglage`), et relu par `appliquer-plan` avant d'écrire.
- `\\Preset\PEQ1\SV` : PEQ d'entrée 12 bandes (EQ générale du plan v2, `peq_entree`).

## Encore inconnu
- Numéro `<k>` des instances PEQOut/Limiter/DelayOut : supposé = numéro de sortie (1–6) → à confirmer avec `carte`.
- Mode de câblage interne de Louis : un crossover « Mono 5-Way » (CrossoverIIR1, Band_1…5) ou « 4-Way + sub »
  (Band_1…4 + Sub_) → lire `\\Preset\CrossoverIIR1\AT\NumBands` et `SubFormat`.
- Format d'écriture accepté pour Amount (délai) : relire avec `get` et écrire au même format.

## Si le texte ne suffit pas
1. Wireshark sur le PC qui fait tourner l'appli VENU360 Windows : filtre `tcp.port == 19272`, « Suivre le flux TCP ».
2. Appli Android : FAIT le 22/11/2026 (v2.0.7). Pour une nouvelle version : récupérer base.apk + split_config.arm64_v8a.apk,
   extraire `libVENU360_*.so`, puis chercher les blocs XML `<ProcessingObject` (texte clair) et les flux zlib (ressources Qt).
   Cadre légal : rétro-ingénierie à des fins d'interopérabilité de son propre matériel (directive UE 2009/24/CE, art. 6).

## Constats sur machine réelle (23/11/2026, firmware 2.0.5.0)
- `set` ne renvoie jamais `error` : hors plage, la valeur est ramenée à la borne ; une valeur invalide est remplacée en silence (nombre illisible → borne haute, type de filtre inconnu → `BW 6`, virgule décimale → partie entière). Toujours relire.
- Les listes de choix sont sensibles à la casse et à l'espace : `LR 24` est accepté, `LR24` et `lr 24` donnent `BW 6`.
- `DelayOut<k>\SV\Amount` : écriture en `ms`, `m`, `ft` ou `s` ; la relecture vaut `<ms>ms/<ft>ft/<m>m` (pas 0,01 ms).
- `\\Node\Settings\SV\Output<k>Gain` : écriture et relecture sous forme de libellé (`+22 dBu`) ; un index nu (`5`) est accepté (→ `+20 dBu`).
- JSON-RPC `Load ["n"]` : réponse `- Load [3]: loaded sv settings`, de 8 à 18 s. Les instances changent avec la topologie (stéréo liée : instances impaires seulement).

## Auto-description (24/11/2026, firmware 2.0.5.0) — outil `tools/dbx_description.py`
- `ls "\\Preset"` : liste des blocs de la configuration chargée (60 avec le preset 31 : InputGains, InMixer1-3, Afs1/3, GEQ1/3,
  PEQ1/3, MidRouter1/5, AutoEQ1/5, CompressorMid1, DelayMid5, CrossoverIIR1/5, PEQOut1-6, Limiter1-6, DelayOut1-6,
  OutputGains, ZoneGains, SignalGenerator, RTA, RTARouter, mètres…).
- `ls "<bloc>\SV"` : paramètres du bloc et valeurs affichées.
- `ls "<bloc>\SV\<param>"` : `$` affichage, `f`/`r` brut, `%` position, `Min`/`Max` affichés, `Type` (Float32, UByte, Byte, ULong,
  String), `Sensor` (true = mètre), `Enabled`.
- `ls "…\<param>\EN"` : choix `{libellé: index}`. `ls "…\<param>\AT"` : `Min`/`Max` bruts, `Default`, `Flags`.
- Unités brutes ≠ affichage pour certains paramètres : délai en **secondes** (`f = 0.0011875` pour 1,19 ms), AFS LiftTime en
  secondes (affiché `60m` = minutes, ou `3m 10s`), niveau de sortie analogique en index (6 = +22 dBu). D'où l'obligation d'écrire
  avec l'unité affichée.
- Bornes déclarées fausses : Q des PEQ d'entrée et de l'AutoEQ déclaré 0,1…128, borné à 16 en réalité. Release affiché Min 360 / Max 5 (inversé).
- Grilles internes : Release par pas de 10 dB/s environ (218 → 220) ; gains InMixer/MidRouter en loi de fader (−25,7 → −25,5 dB ;
  −113,7 → −114,0 dB) ; fréquences affichées à 3 chiffres significatifs.
- Option refusée (`error`) : `RTARouter\SV\RouteSelect = A` dans cette configuration.
- AFS : modifier `MaxFixedFilters` ou `FilterMode` efface les filtres appris (`Band_n_*` passent à `-`) ; ils se réécrivent
  (Frequency, Gain, Q, FilterStatus) depuis un instantané.
- Stockage : `{"method":"Store","params":["27"]}` → résultat nul, le preset prend le nom chargé. Les mutes de zone (ZoneGains 3/4)
  reviennent à On au rappel : ils ne sont pas mémorisés comme réglés au moment du stockage.
- Topologie : `\\Node\Config\SV\Config` (JSON aux guillemets remplacés par des accents graves, clé `.builderConfig.Columns`),
  choix de l'assistant dans `\\Node\SetupWizardCurrentChoices\SV`. Commande `Reconfig` (`\\Node\Config\SV\Command`) non testée, interdite.
- Renommer en stockant : `{"method":"Store","params":["27","CKI 4V Mono"]}` → la case 27 prend ce nom (constaté le 24/11/2026).
- Délais : grille d'un échantillon à 96 kHz (1.7 ms → 1.71 ms).
