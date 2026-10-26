# FAQ — Brixigo Connector Agent

## General

**What does the connector do?**  
It polls biometric devices (ZKTeco / eSSL), queues attendance punches in SQLite, and uploads them securely to Brixigo Cloud Payroll & HRMS.

**Should I run the tray and the Windows service together?**  
Yes for UI convenience, but **only the Windows service should own sync**. When the service is running, the tray starts in UI-only mode and will not start a second engine.

**Where is data stored?**  
Under the install directory: `data/` (encrypted config + SQLite) and `logs/` (rotated daily logs).

## Installation & service

**Service name?** `BrixigoConnector`  
**How to restart?** `sc stop BrixigoConnector` then `sc start BrixigoConnector`, or use tray → Restart Service.

**Why delayed auto-start?**  
So Windows networking is up before device/cloud connections begin.

**Do I need Administrator rights?**  
Yes to install/uninstall the service and write machine environment variables.

## Configuration

**Why can’t I decrypt config after moving the PC?**  
Config is encrypted with `BRIXIGO_CONNECTOR_MASTER_KEY` (preferred) or a machine-derived key. Restore the same master key, or restore `connector.config.enc` from backup encrypted with that key.

**What must match the cloud?**  
`command_signing_secret` must equal the cloud HMAC secret. Organisation ID and API/device token must match the tenant.

**Can I use HTTP?**  
Only in lab. Production requires HTTPS and TLS verification.

## Devices

**Supported production manufacturers?** ZKTeco and eSSL (ZK protocol).  
**Port?** Usually TCP 4370.  
**Duplicates?** Prevented by unique `idempotency_key` per punch.

## Queue & uploads

**What is a dead-letter (`dead`) row?**  
A punch that exceeded max upload retries. It is excluded from the active queue so it cannot block others. Fix the root cause (auth, terminal code, network) then engage support to reprocess if needed.

**Will power loss lose punches?**  
Punches already written to SQLite survive. Rows stuck in `uploading` are reset to `pending` after the stale window.

## Logs

**Locations:** `logs/connector.log`, `sync.log`, `upload.log`, `error.log`, `crash.log`, `service.log`, plus category logs.  
**Retention:** Daily rotation, 30 backups.  
**Secrets:** Logging redacts common secret patterns; never paste API keys into tickets without redaction.

## Network / firewall

Outbound HTTPS (443) to the Brixigo cloud URL.  
LAN TCP 4370 (or configured port) to each device.  
Optional proxy via Settings → Proxy URL.
