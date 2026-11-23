# Références techniques

Ce dossier contient des documents de référence téléchargés gratuitement auprès de leurs éditeurs. Ils sont protégés par le droit d'auteur : ils restent sur le PC et le Drive, et **ne sont pas publiés sur GitHub** (voir `.gitignore`). Seul ce fichier d'index est public.

## Pente du passe-haut (HPF) des caisses bass-reflex — utilisés par `tools/excursion_br.py`

| Fichier | Source | Ce qu'on en retient |
|---|---|---|
| `Marshall_2016_DSP_Assisted_Reflex_audioXpress.pdf` | P. Marshall, « The DSP Assisted Reflex System », *audioXpress*, sept. 2016, p. 36-43 (audioxpress.com/files/attachment/2721) | Sous fb, l'excursion explose ; elle ne se stabilise que vers 0,7·fb. Il faut au moins 14 dB d'atténuation à 0,7·fb, donc une pente d'au moins 28 dB/oct entre 0,7·fb et fb ; un 18 dB/oct ne suffit pas. Il propose un filtre raide vers 0,85·fb et prévient du retard de groupe des filtres raides (24 dB/oct à 20 Hz ≈ 27 ms). Il donne un exemple chiffré (SB15NAC30-8), qui sert à valider le modèle dans l'autotest. Ce sont les règles d'un auteur, pas une norme |
| `UnivLeMans_cours_4.2_charges_acoustiques.html` | Université du Mans, cours d'électroacoustique, grain 4.2 (electroacoustique.univ-lemans.fr) | Excursion d'une caisse bass-reflex sans filtre très élevée sous la résonance de Helmholtz. Le passe-haut actif est la solution, avec un exemple Sallen-Key |
| `QSC_closed_vented_bandpass.html` | QSC, blog Live Sound, « Understanding Closed, Vented and Band-pass Subwoofer Designs » | Une caisse bass-reflex chute naturellement de 24 dB/oct sous l'accord (12 pour une close). Un filtre subsonique est recommandé avec toute caisse bass-reflex |
| `diyAudio_wiki_WinISD_vented.html` | Wiki diyAudio, « Designing a vented woofer or subwoofer with WinISD Pro » | Méthode de simulation : régler le HPF jusqu'à ce que l'excursion sous l'accord reste dans Xmax. HPF d'au moins 3e ordre. Vitesse d'évent |
| `Leach_Intro_Electroacoustics_errata.pdf` | W. M. Leach, Georgia Tech, errata du livre *Introduction to Electroacoustics and Audio Amplifier Design* | Corrections des équations bass-reflex du chapitre 8, à consulter si on travaille avec le livre |
| `Keele_2017_True_Bass_Large_Space_audioXpress.pdf` (11 Mo, **PC seulement** : trop gros pour le connecteur Drive) | D. B. Keele Jr., « True Bass in a Large Space », *audioXpress*, sept. 2017 | Simulation complète d'une grosse caisse bass-reflex (EV 30W, TL303) : excursion, puissance, comparaison avec une caisse close moderne |

## Ouvrages fournis par Louis (Drive « Fichiers son » et envoi direct ; pas dans le dépôt)

| Ouvrage | Ce qui a été lu | Ce qu'on en retient dans le kit |
|---|---|---|
| G. Boyce, *Introduction to Live Sound Reinforcement*, 2014 | En entier pour le calage : ch. 4 (HP), 5 (amplis), 8.6-8.8 (limiteurs, délais), 9.15 (gain), 11 (conception : placement, puissance, masses, sécurité), 13.4 (essais). Les pages citées sont les pages imprimées | `calc_systeme.py --puissance/--cable/--renfort/--dc`, testés sur ses exemples chiffrés (§11.9 : 1183 W et 0,73 dB de câble ; §11.6.7 : renfort à 113 ms ; §11.6.6 : 2·Dc ≈ 18 m). Méthode §3, 5, 8, 10 : couverture ±3 dB, précédence 10 ms, bridge, détection crête ou RMS, sécurité |
| B. McCarthy, *Sound Systems: Design and Optimization*, 1re éd., 2007 (deux PDF fournis par Louis, `McCarthy_2007_Sound_Systems_Part_1.pdf` et `_Part_2.pdf`, PC seulement) | Lu pour le calage : ch. 1-2 (transmission, sommation), 6 (réseaux de subs, p. 303-313), 8 (analyseurs, résolution), 9 (vérification, p. 401-423) et 10 (calibration, p. 424-480) en entier. Pages citées = pages imprimées | Zones de sommation ; demi-espace +6 dB (p. 144) ; pas de notch en EQ système (p. 24) ; au plus six filtres environ par voie (p. 23) ; latence et délai affiché non fiables (p. 22, 412-414) ; micros ONAX / OFFAX / XOVR / SYM (p. 441-451) ; EQ et niveau décidés en ONAX, délais au point de raccord XOVR ; bords ≤ 6 dB sous l'ONAX ; superposition des courbes plutôt que moyenne en dB (p. 433-437) ; résolution au 1/24 d'octave pour l'EQ (p. 367) ; renfort aligné sans marge de précédence (p. 464-465) ; ordre d'opérations (p. 467) ; subs end-fire, deux caisses en ligne à un quart d'onde (environ 28 dB avant/arrière), subs espacés déconseillés (p. 304-306) ; pré-vérification (ch. 9) ; suivi en concert (p. 475-480) |

**Désaccord entre les deux ouvrages, tranché dans le kit.** Pour un renfort retardé, Boyce (§11.6.7) ajoute environ
10 ms de précédence. McCarthy (p. 464-465) le refuse : la marge crée un peigne dès 50 Hz et baisse l'intelligibilité.
Le kit suit McCarthy par défaut (raccord aligné en phase au point XOVR, `--precedence 0`), et garde la marge de Boyce en
option, à comparer à l'écoute.

**Erreur relevée dans McCarthy** (p. 434) : la moyenne en dB de son exemple (5 ; 5,5 ; 6 ; −19,2 dB) vaut −0,7 dB, et
non −0,4 dB comme imprimé. Sa conclusion reste vraie, et le test `test_moyenne_en_puissance_resiste_aux_creux` vérifie
les deux calculs.

Les points de méthode sont reformulés et non recopiés. Chaque chiffre a été vérifié dans le texte source avant d'être
retenu. Les deux points suivants viennent d'un calcul, pas d'une citation :
- Boyce ne donne pas la résistance de bobine de son exemple de câble : ses 0,73 dB impliquent environ 6 Ω ;
- la constante 0,057 de la distance critique (formule de Davis) est confirmée par son résultat (2·Dc ≈ 18 m).

## Ouvrages de référence non disponibles gratuitement (à acheter si besoin)

| Ouvrage | Pourquoi |
|---|---|
| R. H. Small, « Vented-Box Loudspeaker Systems », parties 1 à 4, *JAES* 21, 1973 (bibliothèque AES, environ 20 $ par partie, 5 $ pour un membre) | Le modèle utilisé par `excursion_br.py`. La partie 2 traite des grands signaux : excursion, puissance limitée par le déplacement |
| A. N. Thiele, « Loudspeakers in Vented Boxes », *JAES* 19, 1971 (AES) | Les alignements et l'usage d'un filtre auxiliaire (alignements assistés) |
| V. Dickason, *The Loudspeaker Design Cookbook*, 7e éd., Audio Amateur Press | La référence pratique : alignements, filtres subsoniques, excursion |
| W. M. Leach, *Introduction to Electroacoustics and Audio Amplifier Design*, Kendall/Hunt | Chapitre 8 : modèle de caisse, déplacement de membrane et de l'air de l'évent (exemples SPICE) |
| L. Beranek et T. Mellow, *Acoustics: Sound Fields and Transducers*, Academic Press 2012 | La théorie des circuits équivalents |
| D. Davis, E. Patronis, P. Brown, *Sound System Engineering*, 4e éd., Focal Press | La protection des systèmes de sonorisation et les limiteurs en exploitation |

## Ce qui reste à mesurer, quoi qu'en dise la littérature
- L'accord réel de chaque caisse, par une mesure d'impédance avec REW.
- Les T/S réels des exemplaires, surtout les Beyma de 1re génération.
- L'excursion à faible niveau sous l'accord : observation visuelle ou laser, à comparer au modèle.
