# Brixigo Cloud — Scaling Guide

## Vertical scaling

Increase CPU/RAM on the cloud host. Uvicorn worker count:

```bash
uvicorn app.main:app --workers 4 --host 0.0.0.0 --port 8080
```

Rule of thumb: `workers = (2 × CPU cores) + 1`. Each worker has its own DB connection pool.

## Database scaling

### SQLite → PostgreSQL

SQLite suits single-node deployments (< 100 connectors). Migrate to PostgreSQL when:

- Multiple uvicorn workers need concurrent writes
- Connector count exceeds ~50 active agents
- Report generation contends with heartbeat writes

```env
BRIXIGO_CLOUD_DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/brixigo_cloud
```

### PostgreSQL tuning

- Connection pool: `pool_size=20`, `max_overflow=10` (adjust in `database.py` for high load)
- Index hot paths: `ix_commands_connector_status`, `ix_devices_org_branch`, `ix_audit_org_created` (already defined)
- Partition `attendance_punches` and `heartbeat_history` by month at 10M+ rows

## Horizontal scaling

```
                    ┌─────────────┐
                    │   nginx LB  │
                    └──────┬──────┘
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌────────────┐  ┌────────────┐  ┌────────────┐
    │ Cloud #1   │  │ Cloud #2   │  │ Cloud #3   │
    └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
          │               │               │
          └───────────────┼───────────────┘
                          ▼
                   ┌─────────────┐
                   │ PostgreSQL  │
                   └─────────────┘
                          │
                   ┌─────────────┐
                   │    Redis    │  (worker lock)
                   └─────────────┘
```

### Requirements for multi-instance

1. **Shared PostgreSQL** — all instances point to same `DATABASE_URL`
2. **Shared storage** — NFS/S3 for `storage/firmware`, `storage/reports`, `storage/artifacts`
3. **Redis** — enable `BRIXIGO_CLOUD_ENABLE_REDIS=true` so only one worker runs background ticks
4. **Sticky sessions not required** — JWT is stateless

## Background worker

The in-process worker runs every 60 seconds:

- Marks stale connectors/devices offline
- Expires queued commands
- Raises notifications for offline/queue overflow

With Redis, a distributed lock prevents duplicate notifications across replicas.

## Read replicas

For analytics-heavy workloads:

1. Route `analytics` and `reports` services to a read replica connection
2. Keep writes on primary
3. Accept eventual consistency for dashboard charts (≤ replication lag)

## Caching (future enhancement)

Hot endpoints for caching:

- `GET /monitoring/dashboard` — TTL 30s
- `GET /analytics/summary` — TTL 60s
- `GET /tenancy/branches` — TTL 300s

Use Redis cache with organisation-scoped keys.

## Connector agent scaling

Connector agents scale independently on-premise. Each connector handles its local device fleet. Cloud platform scales with connector count, not device count directly.

| Connectors | Recommended setup |
|------------|-------------------|
| 1–20 | Single instance, SQLite OK |
| 20–100 | 2–4 workers, PostgreSQL |
| 100–500 | 3+ instances behind LB, PostgreSQL, Redis |
| 500+ | Sharded by organisation or dedicated cells |

## Monitoring metrics

Track:

- Request latency p95 per endpoint
- DB connection pool utilisation
- Background worker tick duration
- Connector heartbeat lag distribution
- Command queue depth per connector

## Rate limiting

Default `rate_limit_per_minute=600` in settings. Enforce at reverse proxy (nginx `limit_req`) for production.
