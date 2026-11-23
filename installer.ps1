# installer.ps1 — copie les skills du kit dans .claude/skills du dossier (Claude Code les détecte alors seul)
$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $racine ".claude\skills"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Get-ChildItem -Directory (Join-Path $racine "skills") | ForEach-Object {
    Copy-Item -Recurse -Force $_.FullName (Join-Path $dest $_.Name)
    Write-Host "Skill installé :" $_.Name
}
pip install -r (Join-Path $racine "requirements.txt")
Write-Host "OK. Lancer 'claude' dans $racine"
