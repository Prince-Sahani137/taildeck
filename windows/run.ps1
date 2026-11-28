<# Runs TailDeck in this window (Ctrl+C to stop). #>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run windows\setup.ps1 first." }
& $py (Join-Path $root "taildeck.py")
