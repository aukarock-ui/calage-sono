# installer.ps1 — prépare le kit sur un PC Windows :
#   1. vérifie Python 3.12+ (numpy 2.5 n'existe plus pour 3.11) ;
#   2. crée l'environnement isolé .venv (rien n'est installé dans le Python global) ;
#   3. installe les dépendances aux versions testées (constraints.txt) ;
#   4. copie les skills dans .claude\skills (Claude Code les détecte alors seul).
# Usage : powershell -ExecutionPolicy Bypass -File installer.ps1   [-Dev pour pytest]
param([switch]$Dev)
$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. Python
$py = $null
foreach ($c in @("py -3", "python")) {
    try {
        $v = & ([scriptblock]::Create("$c -c `"import sys; print('%d.%d' % sys.version_info[:2])`"")) 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) { $py = $c; break }
    } catch {}
}
if (-not $py) { throw "Python introuvable. Installer Python 3.12 ou plus récent (python.org), puis relancer." }
$maj, $min = $v.Trim().Split(".") | ForEach-Object { [int]$_ }
if ($maj -lt 3 -or ($maj -eq 3 -and $min -lt 12)) { throw "Python $v trouvé : 3.12 ou plus récent requis." }
Write-Host "Python $v ($py)"

# 2. Environnement isolé
$venv = Join-Path $racine ".venv"
$vpy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $vpy)) {
    & ([scriptblock]::Create("$py -m venv `"$venv`""))
    if ($LASTEXITCODE -ne 0) { throw "Création de .venv impossible." }
}
Write-Host "Environnement : $venv"

# 3. Dépendances aux versions testées
& $vpy -m pip install --upgrade pip --quiet
$req = @("-r", (Join-Path $racine "requirements.txt"))
if ($Dev) { $req += @("-r", (Join-Path $racine "requirements-dev.txt")) }
& $vpy -m pip install @req -c (Join-Path $racine "constraints.txt")
if ($LASTEXITCODE -ne 0) { throw "Installation des dépendances échouée." }

# 4. Skills
$dest = Join-Path $racine ".claude\skills"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Get-ChildItem -Directory (Join-Path $racine "skills") | ForEach-Object {
    Copy-Item -Recurse -Force $_.FullName (Join-Path $dest $_.Name)
    Write-Host "Skill installé :" $_.Name
}

& $vpy (Join-Path $racine "tools\garde_fous.py")
Write-Host ""
Write-Host "OK. Les outils se lancent avec : .venv\Scripts\python.exe tools\<outil>.py"
Write-Host "    (ou activer l'environnement : .venv\Scripts\Activate.ps1). Puis lancer 'claude' dans $racine"
