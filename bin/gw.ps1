#!/usr/bin/env pwsh
# Resolve a Python interpreter that has the deps, then run gw.py.
# Order: plugin-local .venv (created by /gw-setup) -> python/python3 on PATH.
# Works on Windows PowerShell 5.1 and PowerShell 7+.
$ErrorActionPreference = "Stop"
$pluginRoot = Split-Path -Parent $PSScriptRoot
$gw = Join-Path $pluginRoot "cli/gw.py"

$candidates = @(
    (Join-Path $pluginRoot ".venv/Scripts/python.exe"),
    (Join-Path $pluginRoot ".venv/bin/python3"),
    (Join-Path $pluginRoot ".venv/bin/python")
)
foreach ($py in $candidates) {
    if (Test-Path $py) {
        & $py $gw @args
        exit $LASTEXITCODE
    }
}

$cmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $cmd) { $cmd = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $cmd) {
    Write-Error "No Python interpreter found on PATH. Run /gw-setup or install Python 3."
    exit 1
}
& $cmd.Source $gw @args
exit $LASTEXITCODE
