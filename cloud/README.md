# Brixigo Cloud — Enterprise Device Management Platform

Centralized cloud control plane for Brixigo Payroll & HRMS biometric connectors.

The on-premise **Connector Agent** (`../connector/`) is unchanged. This cloud platform manages organisations, branches, connectors, devices, employees, templates, commands, attendance, reports, and payroll sync from one dashboard.

## Quick start

```powershell
cd cloud
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Open:

- Admin UI: http://127.0.0.1:8080/admin
- Health: http://127.0.0.1:8080/api/health
- Admin API: http://127.0.0.1:8080/api/v1
- Connector API: http://127.0.0.1:8080/api/wf/biometric-enterprise

Default bootstrap admin (change in production via env):

- Email: `admin@brixigo.com`
- Password: `BrixigoAdmin!2026`

## Environment

Prefix: `BRIXIGO_CLOUD_`

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | SQLite (default) or PostgreSQL for scale |
| `JWT_SECRET` | JWT signing secret |
| `COMMAND_HMAC_SECRET` | Must match connector `command_signing_secret` |
| `REDIS_URL` / `ENABLE_REDIS` | Optional queue / worker lock |
| `BOOTSTRAP_ADMIN_EMAIL` | First super-admin |
| `BOOTSTRAP_ADMIN_PASSWORD` | First super-admin password |

## Architecture

```
Organisations → Branches → Connectors → Devices → Employees → Templates
                              ↓
                         Commands / Attendance / Reports / Payroll feeds
```

- **Admin APIs** (`/api/v1`) — JWT + RBAC dashboard operations
- **Connector APIs** (`/api/wf/biometric-enterprise`) — agent contract used by existing `connector/api/cloud_client.py`
- **Workers** — stale connector/device detection, command expiry, notifications
- **Security** — JWT, HMAC command signing, encrypted templates, audit log, API-key connector auth

## Tests

```powershell
python -m pytest tests/ -q
```

## Documentation

See `docs/` for Architecture, Deployment, Admin Guide, Support Manual, API, Disaster Recovery, and Scaling.
