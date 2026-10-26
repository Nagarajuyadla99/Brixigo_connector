# Brixigo Cloud — Deployment Guide

## Requirements

- Python 3.11+
- SQLite (default) or PostgreSQL 14+
- Optional: Redis 6+ for multi-instance worker coordination

## Quick start (development)

```bash
cd cloud
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Open `http://localhost:8080/admin` and sign in with bootstrap credentials (see `.env`).

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BRIXIGO_CLOUD_DATABASE_URL` | SQLite in `data/` | PostgreSQL URL for production |
| `BRIXIGO_CLOUD_JWT_SECRET` | change-me | **Must change in production** |
| `BRIXIGO_CLOUD_COMMAND_HMAC_SECRET` | change-me | Shared with connectors |
| `BRIXIGO_CLOUD_BOOTSTRAP_ADMIN_EMAIL` | admin@brixigo.com | First-run admin |
| `BRIXIGO_CLOUD_BOOTSTRAP_ADMIN_PASSWORD` | BrixigoAdmin!2026 | First-run password |
| `BRIXIGO_CLOUD_ENABLE_REDIS` | false | Enable distributed worker lock |
| `BRIXIGO_CLOUD_REDIS_URL` | | e.g. `redis://localhost:6379/0` |

## Production (systemd example)

```ini
[Unit]
Description=Brixigo Cloud
After=network.target

[Service]
User=brixigo
WorkingDirectory=/opt/brixigo/cloud
EnvironmentFile=/opt/brixigo/cloud/.env
ExecStart=/opt/brixigo/cloud/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 4
Restart=always

[Install]
WantedBy=multi-user.target
```

## PostgreSQL

```env
BRIXIGO_CLOUD_DATABASE_URL=postgresql+psycopg2://user:pass@db-host:5432/brixigo_cloud
```

Add `psycopg2-binary` to your deployment requirements.

## Reverse proxy (nginx)

```nginx
server {
    listen 443 ssl;
    server_name cloud.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Storage directories

Created automatically on startup:

- `data/` — SQLite database
- `storage/firmware/` — firmware packages
- `storage/reports/` — generated reports
- `storage/artifacts/` — support artifacts
- `storage/backups/` — backup archives

Ensure the service user has write permissions.

## Health check

`GET /health` returns `{"status": "ok", "version": "..."}`.

## Security checklist

1. Change JWT and HMAC secrets
2. Change bootstrap admin password after first login
3. Use HTTPS via reverse proxy
4. Restrict admin UI to VPN or IP allowlist
5. Use PostgreSQL with encrypted connections in production
