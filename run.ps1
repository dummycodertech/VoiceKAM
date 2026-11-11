param(
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 7860 }),
    [switch]$LocalOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "Virtual environment not found at .venv\Scripts\python.exe" -ForegroundColor Yellow
    Write-Host "Create it with: uv venv --python 3.12 ; uv pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path (Join-Path $RepoRoot ".env"))) {
    Write-Host "Warning: .env file not found. Copy .env.example to .env and add GROQ_API_KEY / GEMINI_API_KEY." -ForegroundColor Yellow
}

$HealthUrl = "http://127.0.0.1:$Port/api/health"
$ServerProc = $null
$TunnelProc = $null

try {
    Write-Host "Starting VoiceKAM backend on http://127.0.0.1:$Port ..." -ForegroundColor Cyan
    $ServerProc = Start-Process -FilePath $PythonExe `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port", "--workers", "1") `
        -PassThru -NoNewWindow

    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        if ($ServerProc.HasExited) {
            throw "Uvicorn exited early with code $($ServerProc.ExitCode)."
        }
        try {
            $resp = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
            if ($resp.status -eq "ok") {
                $ready = $true
                break
            }
        } catch {
            # still booting
        }
    }

    if (-not $ready) {
        throw "Backend did not become healthy at $HealthUrl within 30s."
    }

    Write-Host "Backend ready! Local URL: http://127.0.0.1:$Port" -ForegroundColor Green

    if ($LocalOnly) {
        Write-Host "Running in -LocalOnly mode. Press Ctrl+C to stop." -ForegroundColor Gray
        Wait-Process -Id $ServerProc.Id
        exit 0
    }

    $Cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
    $LocalCloudflared = Join-Path $RepoRoot "cloudflared.exe"
    $CfBin = if ($Cloudflared) { $Cloudflared.Source } elseif (Test-Path $LocalCloudflared) { $LocalCloudflared } else { $null }

    if (-not $CfBin) {
        Write-Host ""
        Write-Host "cloudflared not found. To get a public HTTPS URL (for mic demo from anywhere):" -ForegroundColor Yellow
        Write-Host "  Option 1: winget install --id Cloudflare.Cloudflared -e" -ForegroundColor White
        Write-Host "  Option 2: curl.exe -L -o cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -ForegroundColor White
        Write-Host ""
        Write-Host "Note: Chrome ALSO treats http://127.0.0.1:$Port as a secure context for local mic testing." -ForegroundColor Cyan
        Write-Host "Press Ctrl+C to stop the server." -ForegroundColor Gray
        Wait-Process -Id $ServerProc.Id
        exit 0
    }

    Write-Host "Opening free Cloudflare Quick Tunnel -> http://127.0.0.1:$Port ..." -ForegroundColor Cyan
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $CfBin
    $psi.Arguments = "tunnel --url http://127.0.0.1:$Port --protocol http2 --no-autoupdate"
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardOutput = $false
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $TunnelProc = New-Object System.Diagnostics.Process
    $TunnelProc.StartInfo = $psi
    $null = $TunnelProc.Start()

    $PendingUrl = $null
    $PrintedUrl = $false
    while (-not $TunnelProc.HasExited -and -not $ServerProc.HasExited) {
        $line = $TunnelProc.StandardError.ReadLine()
        if ($null -eq $line) { break }
        if ($line -match "(https://[a-zA-Z0-9-]+\.trycloudflare\.com)") {
            $PendingUrl = $matches[1]
        }
        if ($PendingUrl -and -not $PrintedUrl -and $line -match "Registered tunnel connection") {
            $PrintedUrl = $true
            # Brief propagation buffer so opening the printed link works on first click
            Start-Sleep -Seconds 3
            Write-Host ""
            Write-Host "============================================================" -ForegroundColor Green
            Write-Host " PUBLIC HTTPS LIVE DEMO URL: $PendingUrl" -ForegroundColor Green
            Write-Host " Open in Google Chrome for full voice mic access." -ForegroundColor Green
            Write-Host " Press Ctrl+C to stop both server and tunnel." -ForegroundColor Gray
            Write-Host "============================================================" -ForegroundColor Green
            Write-Host ""
        }
    }
}
finally {
    if ($TunnelProc -and -not $TunnelProc.HasExited) {
        Stop-Process -Id $TunnelProc.Id -Force -ErrorAction SilentlyContinue
    }
    if ($ServerProc -and -not $ServerProc.HasExited) {
        Stop-Process -Id $ServerProc.Id -Force -ErrorAction SilentlyContinue
    }
}

