# Outils externes utiles (skills, serveurs MCP, bibliothèques) — recensement du 22/11/2026

Rien de tout cela n'est requis pour démarrer : le kit fonctionne seul (Python). Installer au besoin.
Toujours lire le code d'un skill/MCP tiers avant de l'installer (il s'exécute sur le PC du réseau son).

## Rétro-ingénierie / réseau
| Outil | Usage ici | Installation |
|---|---|---|
| Skill `protocol-reverse-engineering` (wshobson/agents) | méthode générale capture → dissection → doc de protocole | `npx -y skills add wshobson/agents --skill protocol-reverse-engineering --agent claude-code` |
| Wireshark MCP (bx33661) | Claude analyse les captures `.pcap` de l'appli dbx | `pip install wireshark-mcp` puis `wireshark-mcp install` (Wireshark/tshark requis) |
| mcp-wireshark (khuynh22) | idem + capture live, suivi de flux TCP | `pip install mcp-wireshark` |
| JADX MCP (zinja-coder/jadx-ai-mcp ou Qtty/jadx-mcp-server) | décompiler l'appli Android VENU360 pour lister les chemins | voir dépôts GitHub |
| `dbxdriverack` (Python) | référence PA2 (chemins, parsing) | `pip install dbxdriverack` (dernière version retirée de PyPI : préférer le dépôt GitHub) |
| fiLLLip/dbx-venu360 | échantillons Python + Node-RED VENU360 | GitHub |

## Mesure / acoustique
| Outil | Usage ici | Installation |
|---|---|---|
| **Open Sound Meter** (psmokotnin/osm, GPL-3, v1.5.2 sept. 2025) | moteur de mesure principal : double FFT temps réel, Claude lit les données via `tools/osm_client.py` (TCP/UDP 49007) | installeur Windows sur github.com/psmokotnin/osm/releases ; Remote → Server |
| BRP-PACU, OpenOptimize | autres analyseurs double FFT open source, peu maintenus, sans API | non retenus |
| **`tools/rew_client.py`** (kit, 23/11/2026) | REW piloté directement par son API REST (port 4735), sans Node ni MCP ; doc Swagger relevée dans `docs/rew_api_5.40b121.json` | REW lancé avec `-api` |
| `rew-mcp` (koltyj) | piloter REW depuis Claude Code (mesures, EQ, moyennes) | `claude mcp add rew-mcp -- npx -y rew-mcp` ; REW lancé avec `-api` |
| `room-eq-wizard-mcp` (brandon-fryslie) | alternative, plus d'outils d'analyse (moyennes, arithmétique de mesures) | `npx -y room-eq-wizard-mcp` |
| Smaart API | SDK gratuit sur demande (Rational Acoustics) | demande à faire si Smaart retenu |
| pyfar / pyroomacoustics | traitement du signal, mesures de RI | `pip install pyfar` |

## Skills Claude Code généraux
| Skill | Usage |
|---|---|
| `skill-creator` (Anthropic) | améliorer / tester les skills de ce kit |
| Catalogues : sickn33/antigravity-awesome-skills, BehiSecc/awesome-claude-skills, morluto/awesome-reverse-engineering | chercher d'autres briques |

## Constat
Aucun skill public de calage système touring (crossover, alignement, limiteurs) n'existe à ce jour : les skills de ce kit
(`methode-calage`, `dbx-reseau`, `mesure-acoustique`) sont spécifiques. Les serveurs MCP existants couvrent la mesure
(REW) et la rétro-ingénierie (Wireshark, JADX), pas le pilotage des processeurs de diffusion grand public.
