# Plan de réglages — ma-sono-cki · style « techno » (profil exigeant)
Généré le 2026-11-24T12:12:07 depuis `data/configs/ma-sono-cki.yaml` · processeur DBX_VENU360 · point d'écoute 20 m / 1.7 m

## Feuille de saisie (une ligne par sortie, protections d'abord)

| Sortie | Voie | HPF | LPF | Polarité | Limiteur seuil | Attaque | Relâchement | Détection | Délai | Gain |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | HIGH | LR24 1.6 kHz | — | normale | **-31.1 dBFS** | 0.5 ms | 360 dB/s | crête (overshoot 6 dB) | 0.00 ms | -19.9 dB |
| 2 | MEDIUM | LR24 400 Hz | LR24 1.6 kHz | normale | **-26.1 dBFS** | 2.0 ms | 271 dB/s | RMS | 1.23 ms | -16.3 dB |
| 3 | KICK | LR24 90 Hz | LR24 400 Hz | normale | **-26.1 dBFS** | 8.0 ms | 68 dB/s | RMS | 1.70 ms | -12.6 dB |
| 4 | SUB | LR24 35 Hz | LR24 90 Hz | normale | **-23.1 dBFS** | 16.0 ms | 34 dB/s | RMS | 4.60 ms | +0.0 dB |

Sorties inutilisées : 5, 6 → **mutées**.

## Puissance
Voie limitante : **SUB** · réserves des autres voies : SUB 0.0 dB, KICK 9.6 dB, MEDIUM 13.3 dB, HIGH 8.9 dB
Plateau atteignable à 20 m ≈ **92.9 dB** pour 108 dB(A) visés par le profil (-15.1 dB).

## Excursion (HPF des bass-reflex)
- SUB : ok — pire cas 12.13 mm à 46.7 Hz (Xmax 13.5 mm), accord [28.0, 40.0] Hz, volume [400.0, 550.0] L
- KICK : non_verifie — accord et/ou volume inconnus, sans `hypotheses_caisse` : mesurer l'impédance (accord) et le volume, ou donner une plage d'hypothèses

## Alertes
- SUB : Accord d'évent inconnu : HPF 35 Hz non comparable à l'accord (mesure d'impédance à faire).
- SUB : QSC_PL380 en bridge : gain pris à 38.0 dB (canal +6,02) — CONFIRMER au voltmètre.
- SUB : PL380 bridge : puissances constructeur en burst — marge réelle continue plus faible.
- KICK : Accord d'évent inconnu : HPF 90 Hz non comparable à l'accord (mesure d'impédance à faire).
- KICK : BEYMA_SM118_GEN1 : puissance 250 W non AES — limiteur calculé dessus, à confirmer.
- KICK : QSC_PLX3402 en bridge : gain pris à 38.0 dB (canal +6,02) — CONFIRMER au voltmètre.
- MEDIUM : BEYMA_SM115_GEN1 : puissance 250 W non AES — limiteur calculé dessus, à confirmer.
- MEDIUM : QSC_PLX3002 en bridge : gain pris à 38.0 dB (canal +6,02) — CONFIRMER au voltmètre.
- HIGH : BEYMA_CP650TI : puissance 80 W non AES — limiteur calculé dessus, à confirmer.
- HIGH : Sensibilité CP650Ti donnée sur TD-460, pas sur HP640 : SPL indicatif.
- HIGH : Seuil très bas (< -24 dBFS) : gain ampli trop élevé pour cette voie — envisager de baisser le gain ampli.
- PUISSANCE : à 20 m, plateau atteignable ≈ 92.9 dB contre 108 dB(A) visés par le profil « exigeant » (-15.1 dB) — voie limitante SUB. Une seule pile en champ libre : la salle et le couplage ajoutent quelques dB ; sinon ajouter des caisses sur cette voie ou viser un profil plus léger.

## Hypothèses (à confirmer par la mesure)
- Centres acoustiques de `geometrie_pile` : hypothèses tant que l'IR ne les a pas confirmés.
- SUB : HPF vérifié sur des HYPOTHÈSES de caisse (accord [28.0, 40.0] Hz, volume [400.0, 550.0] L).
- KICK : HPF non vérifié en excursion (accord et/ou volume inconnus, sans `hypotheses_caisse` : mesurer l'impédance (accord) et le volume, ou donner une plage d'hypothèses) — ACCEPTÉ par --accepter-non-verifie

Application : dbx VENU360/PA2 → `dbx_hiqnet.py appliquer-plan <ip> <plan.json> --case N --nom "…" --appliquer --protection-validee` ; autre processeur → saisir cette feuille, puis relire à l'écran.