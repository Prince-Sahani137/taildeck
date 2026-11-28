<#
.SYNOPSIS
  One-time TailDeck setup on Windows 10/11.
.DESCRIPTION
  Creates a virtual environment, installs the Python dependencies, creates .env
  with a random password, and optionally downloads noVNC.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\setup.ps1 -DownloadNoVNC
#>
param(
  [switch]$DownloadNoVNC,
  [string]$NoVNCVersion = "1.6.0"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- Python ---------------------------------------------------------------------
$hasPy = [bool](Get-Command py -ErrorAction SilentlyContinue)
$hasPython = [bool](Get-Command python -ErrorAction SilentlyContinue)
if (-not ($hasPy -or $hasPython)) {
  throw "Python 3.9+ was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "Creating virtual environment..."
  if ($hasPy) { & py -3 -m venv .venv } else { & python -m venv .venv }
  if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
}
$py = Join-Path $root ".venv\Scripts\python.exe"
Write-Host "Installing dependencies..."
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

# --- .env with a random password ------------------------------------------------------
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  $bytes = New-Object byte[] 16
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $password = -join ($bytes | ForEach-Object { $_.ToString("x2") })
  $text = (Get-Content ".env" -Raw) -replace "(?m)^PANEL_PASSWORD=.*$", "PANEL_PASSWORD=$password"
  [IO.File]::WriteAllText((Join-Path $root ".env"), $text, (New-Object Text.UTF8Encoding($false)))
  # only the current user may read the file
  icacls ".env" /inheritance:r /grant:r "${env:USERNAME}:(R,W)" | Out-Null
  Write-Host "Created .env with a random PANEL_PASSWORD (open .env to read or change it)."
} else {
  Write-Host ".env already exists, leaving it alone."
}

# --- noVNC ---------------------------------------------------------------------------------
if ($DownloadNoVNC -and -not (Test-Path "novnc\vnc.html")) {
  $url = "https://github.com/novnc/noVNC/archive/refs/tags/v$NoVNCVersion.zip"
  $zip = Join-Path $env:TEMP "novnc-$NoVNCVersion.zip"
  $tmp = Join-Path $env:TEMP "novnc-$NoVNCVersion"
  Write-Host "Downloading noVNC $NoVNCVersion..."
  Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
  if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
  New-Item -ItemType Directory -Force -Path "novnc" | Out-Null
  Copy-Item (Join-Path $inner.FullName "*") "novnc" -Recurse -Force
  Remove-Item $zip, $tmp -Recurse -Force
  Write-Host "noVNC installed in .\novnc"
}

# --- what is still missing ----------------------------------------------------------------------
Write-Host ""
Write-Host "Checklist" -ForegroundColor Cyan
function Check($ok, $okText, $todo) {
  if ($ok) { Write-Host "  [ok]   $okText" -ForegroundColor Green } else { Write-Host "  [todo] $todo" -ForegroundColor Yellow }
}
$ts = (Get-Command tailscale -ErrorAction SilentlyContinue) -or (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe")
Check $ts "Tailscale found" "Install Tailscale for Windows: https://tailscale.com/download/windows"
$ttyd = (Get-Command ttyd -ErrorAction SilentlyContinue) -ne $null
Check $ttyd "ttyd found" "Download the Windows ttyd.exe from https://github.com/tsl0922/ttyd/releases (1.7.0+), put it on PATH or set TTYD_BIN in .env"
Check (Test-Path "novnc\vnc.html") "noVNC found (.\novnc)" "Run this script again with -DownloadNoVNC, or set NOVNC_DIR in .env"
$vnc = Get-NetTCPConnection -State Listen -LocalPort 5900 -ErrorAction SilentlyContinue
Check ($vnc -ne $null) "A VNC server is listening on port 5900" "Install a VNC server (TigerVNC or TightVNC) and let it listen on 127.0.0.1:5900 with a VNC password"
Write-Host ""
Write-Host "Next:  windows\run.ps1            (run in this window)"
Write-Host "       windows\install-task.ps1   (start automatically when you log in)"
