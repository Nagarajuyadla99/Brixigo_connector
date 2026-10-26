# Brixigo Cloud — Architecture

## Overview

Brixigo Cloud is a multi-tenant enterprise device management platform that centralises on-premise **Connector** agents, biometric devices, employees, templates, remote commands, and operational analytics.

```
┌─────────────────┐     HTTPS/JWT      ┌──────────────────────────────┐
│  Admin Browser  │ ◄────────────────► │  Brixigo Cloud (FastAPI)     │
│  dashboard.html │                    │  /api/v1/*  admin routers    │
└─────────────────┘                    └──────────────┬───────────────┘
                                                      │
                    ┌─────────────────────────────────┼─────────────────────┐
                    │                                 │                     │
              ┌─────▼─────┐                   ┌───────▼───────┐    ┌───────▼───────┐
              │ SQLAlchemy │                   │ Background    │    │ Storage dirs  │
              │ SQLite/PG  │                   │ Worker Thread │    │ firmware,     │
              └───────────┘                   └───────────────┘    │ reports, etc. │
                                                                   └───────────────┘
┌─────────────────┐     API Key + HMAC     ┌──────────────────────────────┐
│ On-premise      │ ◄────────────────────► │  Connector Agent (separate)  │
│ Connector       │                        │  connector/ package          │
└─────────────────┘                        └──────────────────────────────┘
```

## Layers

| Layer | Location | Responsibility |
|-------|----------|----------------|
| API | `app/api/admin/` | JWT auth, RBAC, tenant isolation, REST endpoints |
| Services | `app/services/` | Business logic, SQLAlchemy queries, side effects |
| Models | `app/models/` | Multi-tenant schema (orgs, devices, commands, etc.) |
| Security | `app/security/` | JWT, bcrypt, RBAC matrix, HMAC command signing |
| Workers | `app/workers/` | Stale detection, command expiry, alert generation |
| UI | `templates/`, `static/` | Single-page admin dashboard |

## Multi-tenancy

- Every tenant-owned row carries `organisation_id`.
- `tenant_filter_org_id(user)` restricts queries for non-platform roles.
- `SUPER_ADMIN` and `PLATFORM_ADMIN` may span organisations.

## Command security

Remote commands are signed with HMAC-SHA256 via `build_signed_command()`, compatible with the on-premise connector security gate. Each command has a unique nonce and TTL.

## Data retention

Configurable via `SystemPolicy` and settings: attendance retention, audit retention, heartbeat history.

## Optional Redis

When `BRIXIGO_CLOUD_ENABLE_REDIS=true`, the background worker uses a distributed lock to avoid duplicate ticks across replicas.
