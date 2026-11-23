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
