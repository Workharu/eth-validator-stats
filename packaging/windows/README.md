# Windows service install (Phase 1)

Wraps `eth-validator-stats watch` as a Windows service using
[WinSW](https://github.com/winsw/winsw).

## Prerequisites

- Python 3.11+ with `uv` installed.
- The repo cloned and `uv sync` run from the repo root, so
  `.venv\Scripts\eth-validator-stats.exe` exists.
- A populated config at `%APPDATA%\eth-validator-stats\config.yml`
  (run `uv run eth-validator-stats init` if needed).
- An elevated PowerShell prompt (required to register a Windows service).

## Install

From an elevated PowerShell:

```powershell
cd packaging\windows
.\install-service.ps1
```

The script will:

1. Download the pinned WinSW release and verify its SHA256.
2. Fill the service XML template with your repo's install directory.
3. Prompt for the credentials the service should run under (defaults to your
   current user — press Enter to accept, then enter your password). The SCM
   needs these so it can launch the process as you on every start.
4. Register the service and start it.

To run as `LocalSystem` instead (no credential prompt):

```powershell
.\install-service.ps1 -LocalSystem
```

When using `-LocalSystem`, set `ETH_VALIDATOR_STATS_CONFIG` and
`ETH_VALIDATOR_STATS_STATE` env vars (via `setx` in an elevated prompt) to
point at your user-scope config and state files; otherwise the service will
look under `C:\Windows\System32\config\systemprofile\AppData\...`.

## Verify

```powershell
Get-Service eth-validator-stats
Get-Content -Wait packaging\windows\winsw\eth-validator-stats.out.log
```

## Uninstall

```powershell
.\uninstall-service.ps1
```

## Notes

- WinSW logs roll by size: ~10 MB per file, 5 files retained (~50 MB cap),
  written next to `eth-validator-stats.exe` in `packaging\windows\winsw\`.
- The Phase 2 `.msi` installer (planned) will replace this manual flow.
