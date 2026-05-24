<#
.SYNOPSIS
    Install eth-validator-stats as a Windows service via WinSW.

.DESCRIPTION
    Downloads a pinned WinSW release, fills the service XML template
    with this repo's install directory, registers the service with the
    Windows Service Control Manager, and starts it.

    By default the service runs as the current user (prompts once for
    your password so SCM can persist it). Use -LocalSystem to skip
    the prompt and run as LocalSystem instead.

.PARAMETER LocalSystem
    Register the service to run as NT AUTHORITY\SYSTEM instead of the
    current user. Removes the password prompt but state files end up
    under C:\Windows\System32\config\systemprofile unless you also set
    ETH_VALIDATOR_STATS_CONFIG / ETH_VALIDATOR_STATS_STATE env vars.

.EXAMPLE
    .\install-service.ps1
.EXAMPLE
    .\install-service.ps1 -LocalSystem
#>

[CmdletBinding()]
param(
    [switch] $LocalSystem
)

$ErrorActionPreference = "Stop"

# Pinned WinSW release. Update both URL and SHA256 in lockstep when bumping.
$WinSWVersion = "2.12.0"
$WinSWUrl = "https://github.com/winsw/winsw/releases/download/v$WinSWVersion/WinSW-x64.exe"
$WinSWSha256 = "05B82D46AD331CC16BDC00DE5C6332C1EF818DF8CEEFCD49C726553209B3A0DA"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$winswDir = Join-Path $scriptDir "winsw"
$winswExe = Join-Path $winswDir "eth-validator-stats.exe"
$winswXml = Join-Path $winswDir "eth-validator-stats.xml"
$template = Join-Path $winswDir "eth-validator-stats.xml.template"

$venvExe = Join-Path $repoRoot ".venv\Scripts\eth-validator-stats.exe"
if (-not (Test-Path $venvExe)) {
    Write-Error "Expected $venvExe but it does not exist. Run 'uv sync' from the repo root first."
    exit 1
}

if (-not (Test-Path $template)) {
    Write-Error "Template not found at $template."
    exit 1
}

# Download WinSW if missing or hash mismatch.
$needsDownload = $true
if (Test-Path $winswExe) {
    $actual = (Get-FileHash -Path $winswExe -Algorithm SHA256).Hash
    if ($actual -ieq $WinSWSha256) {
        $needsDownload = $false
    }
}

if ($needsDownload) {
    Write-Host "Downloading WinSW $WinSWVersion ..."
    Invoke-WebRequest -Uri $WinSWUrl -OutFile $winswExe -UseBasicParsing
    $actual = (Get-FileHash -Path $winswExe -Algorithm SHA256).Hash
    if ($actual -ine $WinSWSha256) {
        Write-Error "WinSW download hash mismatch. Expected $WinSWSha256, got $actual."
        Remove-Item -Force $winswExe
        exit 1
    }
}

# Render the service xml.
(Get-Content -Raw $template) `
    -replace "@INSTALL_DIR@", $repoRoot.ToString().Replace('\', '\\') `
    | Set-Content -Encoding UTF8 $winswXml

# Optionally inject service account.
if (-not $LocalSystem) {
    $cred = Get-Credential -Message "Enter the credentials the service should run under (current user is the default)" -UserName "$env:USERDOMAIN\$env:USERNAME"
    $plainPwd = [System.Net.NetworkCredential]::new("", $cred.Password).Password
    $serviceAccountXml = "  <serviceaccount>`n    <username>$($cred.UserName)</username>`n    <password>$plainPwd</password>`n  </serviceaccount>`n</service>"
    (Get-Content -Raw $winswXml) -replace '</service>', $serviceAccountXml | Set-Content -Encoding UTF8 $winswXml
}

# Register + start.
& $winswExe install
& $winswExe start

Write-Host ""
Write-Host "Installed. Verify with:"
Write-Host "  Get-Service eth-validator-stats"
Write-Host "  Get-Content -Wait `"$winswDir\eth-validator-stats.out.log`""
