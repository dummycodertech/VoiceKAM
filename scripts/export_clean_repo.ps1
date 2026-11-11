param(
    [string]$TargetDir = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    $Py = "python"
}

$ProjectName = (& $Py -c "from app.config import PROJECT_NAME; print(PROJECT_NAME)").Trim()
if (-not $TargetDir) {
    $TargetDir = Join-Path (Split-Path -Parent $RepoRoot) "$ProjectName-clean"
}

if (Test-Path $TargetDir) {
    throw "Destination '$TargetDir' already exists. Remove it or pass -TargetDir."
}

New-Item -ItemType Directory -Path $TargetDir | Out-Null
$ArchiveZip = Join-Path $env:TEMP ("{0}-export-{1}.zip" -f $ProjectName, [System.Guid]::NewGuid().ToString("N"))

try {
    git archive --format=zip -o $ArchiveZip HEAD
    & $Py -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" $ArchiveZip $TargetDir

    Push-Location $TargetDir
    try {
        git init | Out-Null
        git add -A
        git commit -m "Initial commit: $ProjectName" | Out-Null
    }
    finally {
        Pop-Location
    }
}
finally {
    if (Test-Path $ArchiveZip) {
        Remove-Item $ArchiveZip -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Clean repository exported with single initial commit at: $TargetDir" -ForegroundColor Green
