# Behringer DCX2496, t.racks DSP 206, Linea ASC-48 : ce que le kit sait faire

Mis à jour : 2026-11-23. Les trois clients ont été testés contre des simulateurs, **jamais encore sur l'appareil réel**.
Règles communes, les mêmes que pour le dbx : lecture seule par défaut, `--appliquer` après le « go » de Louis,
`--protection-validee` pour les filtres, les limiteurs, la polarité, les mutes et les rappels de snapshot,
un instantané avant chaque écriture, la relecture après, et une ligne dans `journal/ecritures.log`.

## Behringer ULTRADRIVE PRO DCX2496 — `tools/dcx2496.py`
- **Liaison** : RS-232, 38400 bauds 8N1, avec un adaptateur USB-RS232 (puce FTDI de préférence) et un **câble null-modem**
  (croisé) jusqu'au port RS-232 du DCX. Sur le DCX : SETUP, page 6, PORT = « PC (RS232) ». Fermer DCX Remote.
  Le RS-485 sert à chaîner plusieurs DCX (ID 0 à 15, `--id`).
- **Protocole** : des trames de type SysEx `F0 00 20 32 <id> 0E <cmd> … F7`. `40` recherche les appareils,
  `3F 0C 00` passe en mode transmission, `50 01 00 <part>` lit l'état complet en deux parties (1015 et 911 octets),
  `20 <nb> [canal param hi lo]…` écrit des paramètres. Le DCX ne relit pas un paramètre isolé : l'outil refait un dump
  complet après chaque écriture.
- **Table** : `data/protocoles/dcx2496_parametres.json`, 49 paramètres (setup, entrées et sorties, 9 EQ par canal, sorties),
  convertie depuis dcx-ui (github.com/lasselukkari/dcx-ui, MIT, © 2018 Lasse Lukkari). Trame et dump vérifiés avec
  DuinoDCX (même auteur, testé sur un vrai DCX).
- **Unités** : le seuil du limiteur va de −24 à 0 dB (manuel §4.5) et la sortie maximale est de +22 dBu. Hypothèse
  à vérifier au voltmètre : 0 dB = 0 dBFS = +22 dBu. Le délai court est au pas de 2 mm et le délai long au pas de 5 cm,
  avec le temps (ms) = distance / c(T).
- **Limites** : les fréquences de filtre et d'EQ sont des listes de valeurs (pas de valeur libre), l'outil prend la plus
  proche. Le verrouillage de façade et le mode « Lock » ne sont pas contournés.

### Recoupement avec le logiciel officiel DCX-Remote 1.16 / 1.16a (Drive « Fichiers son », 23/11/2026)
Analyse statique (Borland C++ 1999, formulaires décodés, aide HTML), aucun échange avec un appareil.
- Liaison confirmée : `COM0:38400,n,8,1` ; connexion possible aussi « thru MIDI ».
- **Liens (aide officielle, FAQ 8 à 11)** :
  - « Out Stereo Link » couple les sorties de même type (L/M/H : 1-4, 2-5, 3-6 en LMH LMH). Une valeur écrite sur
    la sortie la plus basse est **recopiée** sur la sortie couplée, y compris le limiteur et le délai court ;
  - le délai long n'est PAS couplé par Out Stereo Link, mais par « Stack Link » (couplage relatif) ;
  - **activer** un lien écrase les réglages des sorties couplées (fenêtre « All Settings of Output … Will Get Lost ! »).
  - Conséquences dans le kit : `dcx2496.py set` refuse d'écrire si un lien est actif (sauf `--lien-accepte`), signale toute
    propagation à un autre canal après relecture, et classe les paramètres `*Link*` comme protection.
- **Limiteur** : l'aide le décrit comme un écrêteur de crêtes, pas fait pour un travail continu. Avec des sorties couplées,
  la valeur est recopiée mais le limiteur n'est pas lié en stéréo. Pour la méthode, il ne protège pas thermiquement :
  la protection thermique (puissance moyenne) dépend du gain de l'ampli et de son propre limiteur. L'échelle de
  DCX-Remote va de 0 à −36 sans unité ; « 0 dB = +22 dBu » reste à vérifier au voltmètre.
- **Auto Align** : tient compte des liens actifs. Le kit ne l'utilise pas (alignement par mesure, `skills/mesure-acoustique`).
- Firmwares fournis (1.16, 1.18A) : compressés/chiffrés, sans intérêt pour le protocole. Le kit ne flashe jamais.
  `dcx2496.py chercher` lit la version de l'appareil.

## t.racks DSP 206 (204, 408) — `tools/tracks_dsp.py`
- **Liaison** : Ethernet, TCP 9761, le DSP est le serveur. **Un seul client à la fois** : fermer l'éditeur t.racks
  et l'appli tracks-dsp-controller. L'adresse correspond à l'« ID number » de la façade (le 206 capturé répondait sur 02 ;
  l'outil essaie 02, 01, puis 3 à 250).
- **Protocole** : `10 02 SRC DST LEN payload 10 03 CHK`, avec CHK = XOR initialisé à 1. Une séquence d'initialisation
  est obligatoire avant de lire la config (19 pages de 50 octets = 928 octets sur le 206). Les écritures sont acquittées par `01`.
- **Source** : la synthèse de protocole de tom-da-costa/tracks-dsp-controller (docs/PROTOCOL.md, AGPL-3.0), vérifiée sur
  un DSP 408 réel. Le DSP 206 y est décodé à partir des captures Wireshark d'un vrai 206 publiées par
  FlexiFlizz/t.racks-DSP-control (GPL-3.0). Le code du kit est indépendant (aucun code copié).
- **Unités** : le **limiteur est en dBu**, avec une pleine échelle de sortie de +20 dBu (mesurée sur le 408 à ±1 dB).
  **Contradiction (manuel Thomann v4, EN et FR, §7, lu le 23/11/2026)** : sortie « niveau max +5 dBu », entrée +15 dBu ;
  limiteur « TH −90 dB…+20 dB », type « Peak limiter ». La plage colle au codage raw/2 − 90, mais la pleine échelle
  (+5 ou +20 dBu) reste à trancher au voltmètre. `calc_systeme.py` alerte si un seuil dépasse la plus basse des deux. Le seuil
  se calcule donc directement : seuil_dBu = dBu aux bornes − gain de l'ampli. La fréquence a 300 pas (≈ 2,3 %), le délai
  se règle en échantillons à 96 kHz, et les pentes vont de BW/BL/LR 6 à 48.
- **Le kit ne fait jamais** : de PIN (`2f`), de générateur de test (`39`), d'écriture sur F00, de trame brute.
- **Alternative interactive** : tracks-dsp-controller (application web, simulateur `--model 206`, export .prs) peut servir
  à Louis pour une vue live. Il prend alors la place de l'unique client TCP, donc il faut le fermer avant `tracks_dsp.py`.

### Recoupement avec l'éditeur officiel (23/11/2026)
Éditeur installé sur le PC de Louis : `C:\Program Files (x86)\the t.racks DSP 206 Processor Editor\…V1.05.exe`
(C++Builder 2009, Indy pour le réseau, JVCL HID pour l'USB). Analyse statique uniquement (formulaires TPF0 décodés,
constantes), aucun échange avec un appareil.
| Point | Kit (`tracks_dsp.py`) | Éditeur officiel | Statut |
|---|---|---|---|
| Port TCP | 9761 | `mov reg, 9761` dans le code (0x3110F) | **confirmé** |
| Découverte | — | `TIdUDPClient` Port 30303 (annonce Microchip) → ajouté : `tracks_dsp.py chercher` | ajouté, à essayer sur site |
| IP d'usine | — | 192.168.1.101 (fenêtre « Setting ID and IP », manuel ou auto/DHCP) | noté |
| Gain | raw 0…400, 0 dB = 280, +12 max | fader Min −280 / Max +120 ; doc RS-232 : 0–80 pas 0,5 dB, 80–400 pas 0,1 dB | **confirmé** (2 sources) |
| Fréquence | raw 0…300, 19,70…20160 Hz | curseurs Max 300 ; constante 20160 présente | cohérent |
| Q | raw 0…100, 0,4·320^(r/100) | Max 100 ; constantes 0,4 et 320 présentes | cohérent |
| Gain PEQ | raw 0…240, (r−120)/10 | Max 240 | cohérent |
| Types PEQ | 9 (Peak…Allpass2) | 9 libellés, même ordre (Peak, Low Shelf, High Shelf, LP−6, LP−12, HP−6, HP−12, Allpass1, Allpass2) | **confirmé** |
| Limiteur | seuil dBu = raw/2 − 90 | écran construit à l'exécution ; constante −90 présente ; manuel : −90…+20 « dB » | plage **confirmée**, unité NON → voltmètre |

### Second protocole, documenté DANS l'éditeur : « Processor Extend Remote Control Protocol » (RS-232)
Table d'aide de l'éditeur (menu Help > RS232 Code Table). Trame `7B 7D | ID | CMD | canal | valeur(2 octets) | 7D 7B`
(exemples de l'éditeur : `7B7D01410000007D7B`). Réservé aux automates (volume / mute / preset), il ne donne pas accès au crossover
ni aux limiteurs. Le kit ne l'implémente pas : il est noté ici comme solution de repli en RS-232.
| CMD | Fonction | Exemple éditeur |
|---|---|---|
| 0x41 | gain (pas +/−) | In1 Gain+ : `7B7D01410000007D7B` |
| 0x42 | mute | Out1 Mute : `7B7D01420100017D7B` |
| 0x43 | rappel de preset | U01 : `7B7D01330100007D7B` (sic, 0x33 dans l'exemple) |
| 0x44 | volume entrée (valeur absolue) | InA +0,0 dB : `7B7D01440001187D7B` (0x0118 = 280) |
| 0x45 | volume sortie (valeur absolue) | Out2 −3,0 dB : `7B7D01450100FA7D7B` (0x00FA = 250) |
| 0x48 | lire le gain | réponse 0–400 (même codage que le kit) |
| 0x49 | lire le mute | 0x00 / 0x01 |
| 0x4A | lire le preset courant | 0x00 = F00, 1…31 = U00…U30 |

## Linea Research ASC-48 — `tools/linea_tipi.py`
- **Liaison** : Ethernet, TCP 51456, protocole **TIPI**, publié par Linea (texte, `$SET` / `$GET` / `$NOTIFY` / `$NOP`).
  Le Secure Mode laisse l'Ethernet actif.
- **Couverture** : Linea ne publie la liste des méthodes que pour les amplis M et C. `sonder` essaie en lecture une liste
  de noms (publiés, ou supposés comme `Out8/Eq2Freq`, cité dans la doc) et enregistre ceux qui répondent. Une méthode
  absente répond `UnsupportedMethod` et ne modifie rien.
- **Le reste** (crossovers, FIR, limiteurs VX/Tmax/Xmax, EQ complète) se règle dans **System Engineer** (protocole ObCom,
  non publié) : feuille de saisie (niveau C). Les presets constructeur verrouillés (par exemple ceux de NCA) ne sont ni
  lus ni forcés.
- **À la première connexion** : lancer `python tools/linea_tipi.py sonder <ip>`, puis comparer avec l'écran de System
  Engineer. Les noms trouvés seront ajoutés ici.

## Matériel à prévoir dans la caisse
- Un adaptateur USB-RS232 FTDI et un câble null-modem DB9 F/F (DCX).
- Un câble RJ45 et un petit switch. Mettre le PC en IP fixe dans le sous-réseau du processeur (t.racks, ASC-48, Venu360).
- `pip install pyserial` (déjà inclus dans `requirements.txt`).
