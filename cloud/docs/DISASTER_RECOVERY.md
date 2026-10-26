# Brixigo Cloud — Disaster Recovery

## Recovery objectives

| Metric | Target (recommended) |
|--------|---------------------|
| RPO (data loss) | ≤ 1 hour with hourly DB backups |
| RTO (restore time) | ≤ 4 hours for full platform restore |

## Critical assets

1. **Database** — organisations, devices, employees, templates (encrypted), commands, audit logs
2. **Storage** — `storage/firmware/`, `storage/reports/`, `storage/artifacts/`, `storage/backups/`
3. **Secrets** — JWT secret, HMAC command secret, bootstrap credentials
4. **Configuration** — `.env` file, reverse proxy TLS certificates

## Backup strategy

### SQLite (development / small deployments)

```bash
# Stop writes (optional — WAL mode supports hot copy)
sqlite3 data/brixigo_cloud.db ".backup storage/backups/brixigo_$(date +%Y%m%d_%H%M).db"
tar -czf storage/backups/brixigo_storage_$(date +%Y%m%d).tar.gz storage/
```

### PostgreSQL (production)

```bash
pg_dump -Fc -h db-host -U brixigo brixigo_cloud > storage/backups/brixigo_$(date +%Y%m%d).dump
```

Schedule via cron or your orchestrator. Retain 30 daily + 12 monthly backups.

## Restore procedure

### 1. Database restore

**SQLite:**
```bash
cp storage/backups/brixigo_YYYYMMDD_HHMM.db data/brixigo_cloud.db
```

**PostgreSQL:**
```bash
pg_restore -d brixigo_cloud -c storage/backups/brixigo_YYYYMMDD.dump
```

### 2. Storage restore

```bash
tar -xzf storage/backups/brixigo_storage_YYYYMMDD.tar.gz -C /
```

### 3. Secrets

Restore `.env` from secure vault. **JWT secret must match** pre-disaster value or all sessions invalidate (acceptable). **HMAC secret must match** connector configurations or commands will fail validation.

### 4. Verify

1. `GET /health` returns ok
2. Admin login succeeds
3. Connector heartbeats resume
4. Pending commands process correctly
5. Sample report generation works

## Failure scenarios

### Single instance crash

- Restart uvicorn/systemd service.
- Background worker restarts via lifespan.
- No data loss if DB intact.

### Database corruption

- Restore from latest backup.
- Accept RPO data loss since last backup.
- Reconcile connector state (may need reconnect).

### Region / datacenter loss

- Provision new VM in DR region.
- Restore DB + storage from off-site backup.
- Update DNS to new endpoint.
- Update connector `cloud_url` on-premise (or use stable CNAME).

### Secret compromise

1. Rotate `BRIXIGO_CLOUD_JWT_SECRET` — forces re-login.
2. Rotate `BRIXIGO_CLOUD_COMMAND_HMAC_SECRET` — update all connectors simultaneously.
3. Rotate compromised API keys via connector re-registration.

## Audit trail preservation

Audit logs have `audit_retention_days` (default 730). Include audit table in backups for compliance.

## Testing DR

Quarterly drill:

1. Restore backup to staging environment
2. Run `pytest tests/test_cloud_core.py`
3. Verify admin login and connector list
4. Document actual RTO achieved
