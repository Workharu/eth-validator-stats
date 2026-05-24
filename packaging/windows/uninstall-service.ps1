<#
.SYNOPSIS
    Uninstall the eth-validator-stats Windows service.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$winswDir = Join-Path $scriptDir "winsw"
$winswExe = Join-Path $winswDir "eth-validator-stats.exe"

if (-not (Test-Path $winswExe)) {
    Write-Warning "WinSW exe not found at $winswExe. Trying SCM removal anyway."
    sc.exe stop eth-validator-stats 2>$null | Out-Null
    sc.exe delete eth-validator-stats 2>$null | Out-Null
    exit 0
}

& $winswExe stop
& $winswExe uninstall

Write-Host "Uninstalled."
