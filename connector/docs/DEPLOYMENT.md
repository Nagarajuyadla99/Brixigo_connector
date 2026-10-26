# Deployment Guide

## Prerequisites

- Windows 10/11 or Windows Server 2016+
- Local network reachability to biometric devices (typically TCP 4370)
- Outbound HTTPS to Brixigo Cloud
- Administrator rights for service installation

## Package Layout

```text
BrixigoConnector/
  BrixigoConnector.exe          # Tray / dashboard
  BrixigoConnectorService.exe   # Windows Service
  data/                         # Encrypted config + SQLite
  logs/                         # Rotating category logs
```

## Install (Installer)

1. Run `BrixigoConnectorSetup-3.0.2.exe` as Administrator
2. Accept install path
3. Service is registered as `BrixigoConnector` with **delayed auto-start** and crash recovery (5s/15s/60s)
4. Prefer passing `-MasterKey` to `setup_service.ps1` (or set machine env afterward) then restart the service
5. Launch tray app — when the service is running it is **UI-only** (no second sync engine)

## Manual Service Setup

```powershell
.\installer\setup_service.ps1 -InstallDir "C:\Program Files\Brixigo\Connector" -MasterKey "<vault-secret>"
```

Uninstall:

```powershell
.\installer\uninstall_service.ps1
```

## First Configuration

Required:

- Organisation ID
- API Key / Device Token
- Cloud Base URL (HTTPS in production — default `https://brixigo.com`)

Recommended:

- Connector ID (unique per site)
- Command Signing Secret (must match cloud HMAC secret)
- `BRIXIGO_CONNECTOR_ENV=production` + `BRIXIGO_CONNECTOR_MASTER_KEY`
- Max Worker Threads (16+ for large sites)
- Health / Command poll intervals

## Firewall

Allow:

- Outbound TCP 443 to Brixigo Cloud
- LAN TCP 4370 (or configured ports) to biometric devices

## High-Availability Notes

- **One engine per data directory** — Windows service owns sync; tray is UI-only when service is running
- Prefer service mode for unattended sites
- Back up `data/connector.config.enc`, SQLite files, and the master key (see `BACKUP_RESTORE.md`)

## Upgrade

1. Stop service and back up `data/` (see `BACKUP_RESTORE.md`)
2. Install new build over existing path (preserve `data/`)
3. Start service
4. Confirm version in dashboard shows `3.0.2`
5. Schema migrations apply automatically on startup
6. Run `PRODUCTION_CHECKLIST.md` acceptance items

## Related docs

- `PRODUCTION_CHECKLIST.md` — go-live gate
- `BACKUP_RESTORE.md` — backup / restore / disaster
- `FAQ.md` — common questions
- `SOAK_STABILITY.md` — 24/48/72h soak ops
- `TROUBLESHOOTING.md` — diagnostics
