# Build auto-register-windows-vX.Y.Z.zip for GitHub Release
# Usage: powershell -ExecutionPolicy Bypass -File .\build_windows_release.ps1 -Version v1.1.0
param(
  [string]$Version = "v1.1.0"
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location -LiteralPath $Root

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
  $python = Get-Command py -ErrorAction SilentlyContinue
}
if ($python) {
  & $python.Source make_launchers.py
}

$Name = "auto-register-windows-$Version"
$Stage = Join-Path $env:TEMP $Name
if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage | Out-Null

# Collect by pattern so Chinese filenames don't depend on script file encoding.
$patterns = @(
  "*.py",
  "*.bat",
  "*.sh",
  "*.desktop",
  "requirements.txt",
  "README.md",
  ".gitignore"
)
$copied = @()
foreach ($pat in $patterns) {
  Get-ChildItem -LiteralPath $Root -File -Filter $pat -ErrorAction SilentlyContinue | ForEach-Object {
    # Skip local-only helpers
    if ($_.Name -like "_*" ) { return }
    if ($_.Name -eq "build_windows_release.ps1") { return }
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Stage $_.Name) -Force
    $copied += $_.Name
  }
}

$backupDir = Join-Path $Stage "subscription_backups"
New-Item -ItemType Directory -Path $backupDir | Out-Null
Set-Content -LiteralPath (Join-Path $backupDir "README.md") -Value "Subscription YAML/JSON backups. Do not commit credentials." -Encoding UTF8

$distDir = Join-Path $Root "dist"
New-Item -ItemType Directory -Path $distDir -Force | Out-Null
$OutZip = Join-Path $distDir "$Name.zip"
if (Test-Path -LiteralPath $OutZip) { Remove-Item -LiteralPath $OutZip -Force }

Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $OutZip -Force
Write-Host "Built: $OutZip"
Write-Host ("Size: {0:N1} KB" -f ((Get-Item -LiteralPath $OutZip).Length / 1KB))
Write-Host "Files:"
$copied | Sort-Object | ForEach-Object { Write-Host "  $_" }
Remove-Item -LiteralPath $Stage -Recurse -Force
