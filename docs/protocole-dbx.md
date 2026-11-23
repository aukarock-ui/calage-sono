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
