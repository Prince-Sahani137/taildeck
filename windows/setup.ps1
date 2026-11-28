<#
.SYNOPSIS
  One-time TailDeck setup on Windows 10/11.
.DESCRIPTION
  Creates a virtual environment, installs the Python dependencies, creates .env
  with a random password, and optionally downloads noVNC.
.PARAMETER InstallDeps
  Installs what is missing: Python and Tailscale through winget, and downloads
  ttyd (the browser terminal) and noVNC into this folder. A VNC server is NOT
  installed automatically; some of them listen on every network interface by
  default, so you should choose and configure one yourself.
.PARAMETER DownloadNoVNC
  Only download noVNC (implied by -InstallDeps).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File windows\setup.ps1 -InstallDeps
#>
param(
  [switch]$InstallDeps,
  [switch]$DownloadNoVNC,
  [string]$NoVNCVersion = "1.6.0"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($InstallDeps) { $DownloadNoVNC = $true }

# Older Windows 10 builds default to TLS 1.0/1.1, which GitHub refuses.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Update-SessionPath {
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$machine;$user"
}

function Install-WithWinget($id, $name) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Warning "winget is not available, so $name cannot be installed automatically. Install it manually."
    return
  }
  Write-Host "Installing $name with winget..."
  winget install -e --id $id --accept-source-agreements --accept-package-agreements
  Update-SessionPath
}

if ($InstallDeps) {
  if (-not ((Get-Command py -ErrorAction SilentlyContinue) -or (Get-Command python -ErrorAction SilentlyContinue))) {
    Install-WithWinget "Python.Python.3.12" "Python 3.12"
  }
  $hasTailscale = (Get-Command tailscale -ErrorAction SilentlyContinue) -or (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe")
  if (-not $hasTailscale) { Install-WithWinget "Tailscale.Tailscale" "Tailscale" }
}

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

# --- ttyd (browser terminal) -------------------------------------------------------------------
function Install-Ttyd {
  # Stable "latest" URLs: no GitHub API (which rate-limits shared IPs). The download is
  # checked against the SHA256SUMS file published with the same release.
  $base = "https://github.com/tsl0922/ttyd/releases/latest/download"
  $dest = Join-Path $root "tools\ttyd.exe"
  $tmp = Join-Path $env:TEMP "ttyd-download.exe"
  try {
    Write-Host "Downloading ttyd (ttyd.win32.exe) from its GitHub releases..."
    Invoke-WebRequest -Uri "$base/ttyd.win32.exe" -OutFile $tmp -UseBasicParsing
    # Save to a file: GitHub serves it as octet-stream, so .Content would be raw bytes, not text.
    $sumsFile = Join-Path $env:TEMP "ttyd-SHA256SUMS"
    Invoke-WebRequest -Uri "$base/SHA256SUMS" -OutFile $sumsFile -UseBasicParsing
    $line = Get-Content $sumsFile | Where-Object { $_ -match "ttyd\.win32\.exe\s*$" } | Select-Object -First 1
    Remove-Item $sumsFile -Force
    if (-not $line) { throw "ttyd.win32.exe is not listed in SHA256SUMS" }
    $expected = ($line.Trim() -split "\s+")[0]
    $actual = (Get-FileHash -Algorithm SHA256 -Path $tmp).Hash
    if ($actual -ne $expected.ToUpper()) { throw "checksum mismatch (expected $expected, got $actual)" }
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Move-Item -Force $tmp $dest
    try { Unblock-File $dest } catch { }   # clears the download mark; not essential
    Write-Host "ttyd saved to tools\ttyd.exe (SHA-256 verified)."
  } catch {
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
    Write-Warning "Could not install ttyd: $($_.Exception.Message). Download ttyd.win32.exe from https://github.com/tsl0922/ttyd/releases and set TTYD_BIN in .env."
  }
}

if ($InstallDeps -and -not (Get-Command ttyd -ErrorAction SilentlyContinue) -and -not (Test-Path "tools\ttyd.exe")) {
  Install-Ttyd
}

# --- what is still missing --------------------------------------------------------------------------
Write-Host ""
Write-Host "Dependency check (same as: python taildeck.py --doctor)" -ForegroundColor Cyan
& $py (Join-Path $root "taildeck.py") --doctor
Write-Host ""
Write-Host "Next:  windows\run.ps1            (run in this window)"
Write-Host "       windows\install-task.ps1   (start automatically when you log in)"
