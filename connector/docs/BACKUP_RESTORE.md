# Backup & Recovery Guide — Brixigo Connector Agent 3.0.2

## What to back up

| Path | Contents | Critical |
|------|----------|----------|
| `data/connector.config.enc` | Encrypted settings (org, API key, secrets) | Yes |
| `data/*.db`, `*.db-wal`, `*.db-shm` | SQLite queue, devices, health, commands | Yes |
| `data/backups/` | Device/config backups created by the agent | Recommended |
| Machine env `BRIXIGO_CONNECTOR_MASTER_KEY` | Fernet key material | **Yes — vault it** |
| Machine env `BRIXIGO_CONNECTOR_ENV` | `production` flag | Yes |

Logs (`logs/`) are useful for forensics but not required for restore.

## Backup schedule

1. **Daily** (automated or scripted): copy `data/` to a secure share while the service is running (SQLite WAL is checkpointed hourly by cleanup).
2. **Before upgrade / Windows patching**: stop service, copy `data/`, start service.
3. **After any Settings change**: verify a fresh copy of `connector.config.enc` exists off-box.

Suggested PowerShell (run as admin on the connector host):

```powershell
$src = "C:\Program Files\Brixigo\Connector\data"
$dst = "\\backup-server\brixigo\$(hostname)\$(Get-Date -Format yyyyMMdd-HHmm)"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item -Recurse -Force $src\* $dst\
```

## Restore procedure

1. Stop the service: `sc stop BrixigoConnector`
2. Stop any tray UI process
3. Restore `data/` files over the install `data/` folder
4. Set machine environment variables to the **same** master key used when the backup was taken
5. Start service: `sc start BrixigoConnector`
6. Confirm heartbeat in cloud and one live punch end-to-end
7. Review `logs/error.log` for decrypt or schema errors

## Disaster scenarios

### Lost master key
Encrypted config cannot be decrypted. Re-enter settings in the UI (creates a new encrypted file) and re-add device passwords. SQLite attendance history remains, but cloud credentials must be re-entered.

### Corrupt SQLite
Restore latest good `*.db*` set. If only WAL is corrupt, restoring `*.db` alone after deleting `-wal`/`-shm` may work but can lose the last uncheckpointed seconds — prefer full trio restore.

### Duplicate service engines
Ensure only one `BrixigoConnector` service exists (`sc query BrixigoConnector`). Tray must be UI-only when the service runs. Remove accidental second installs.

### Dead-lettered punches
Inspect rows with `status='dead'`. Fix cloud/auth/terminal issues first. Re-queue only with engineering approval to avoid payroll duplicates.

## Upgrade backup

1. Note current version from About / `APP_VERSION`
2. Stop service; back up `data/`
3. Install new build
4. Start service; run Production Checklist soak items
5. Keep previous installer for rollback for at least one payroll cycle
