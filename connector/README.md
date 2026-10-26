# Brixigo Connector Agent — Enterprise Phase 3

Production biometric connector and device management platform for multi-branch organizations.

## Version

**3.1.0** — Operations platform: named daily/monthly attendance, health score, diagnostics split

## Capabilities

### Phase 1
Attendance polling, queue/upload, adapters, service, dashboard, encryption, installer

### Phase 2
Discovery, health, remote commands/config, diagnostics, alerts, security, docs

### Phase 3
Employee sync, templates, device users, time sync, backup/restore, provisioning,
firmware, inventory, access-control architecture, reporting, remote support,
notifications, mTLS, cloud dashboard feeds

## Quick Start

```powershell
cd connector
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

For unattended sites use the Windows service (`BrixigoConnector`). The tray UI detects a running service and stays UI-only so sync is never duplicated.

## Documentation

- `docs/ARCHITECTURE.md`
- `docs/DEPLOYMENT.md`
- `docs/CUSTOMER_INSTALLATION.md`
- `docs/ADMIN_GUIDE.md`
- `docs/TROUBLESHOOTING.md`
- `docs/DEVELOPER_GUIDE.md`
- `docs/PHASE3_ENTERPRISE.md`
- `docs/PRODUCTION_CHECKLIST.md`
- `docs/BACKUP_RESTORE.md`
- `docs/FAQ.md`
- `docs/SOAK_STABILITY.md`
- `docs/PRODUCTION_READINESS_REPORT.md`
