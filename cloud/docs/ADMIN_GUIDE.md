# Brixigo Cloud — Admin Guide

## Getting started

1. Navigate to `/admin` on your cloud instance.
2. Sign in with your organisation admin credentials (bootstrap super-admin on first deploy).
3. Use the left sidebar to access all 20 management modules.

## Modules

### Dashboard & Monitoring
- **Dashboard** — live stats, attendance trend chart, device health breakdown.
- **Monitoring** — online/offline/warning/critical device counts.
- **Analytics** — uptime percentage, queue alerts, connector status.

### Infrastructure
- **Connectors** — register on-premise agents, view heartbeat status, restart/upgrade/reconnect.
- **Devices** — fleet search, bulk enable/disable, CSV export.
- **Command Center** — create and track signed remote commands.
- **Provisioning** — wizard: discover pending connectors, approve, assign device locations.

### People & biometrics
- **Employees** — CRUD, bulk device assignment, branch transfer, manual sync.
- **Templates** — encrypted biometric template storage with version history.

### Operations
- **Firmware** — upload packages, schedule bulk upgrades.
- **Support** — download logs, run diagnostics, clear connector queue.
- **Reports** — generate CSV/Excel/PDF reports (devices, connectors, employees, attendance).
- **Notifications** — view and acknowledge alerts.

### Governance
- **Audit Logs** — searchable activity trail.
- **Settings** — organisation system policies.
- **Webhooks** — event subscription endpoints.

### Tenancy
- **Organisations** — top-level tenants.
- **Branches** — physical sites.
- **Departments** — HR organisational units.
- **Locations** — buildings, floors, gates hierarchy.

## Registering a connector

1. Go to **Connectors** → register via API or admin action.
2. Copy the one-time API key shown in the response.
3. Configure the on-premise connector with `cloud_url`, `connector_id`, and API key.
4. Use **Provisioning → Discover** to approve connectors in `unknown` status.

## Roles

| Role | Typical access |
|------|----------------|
| super_admin | Full platform |
| organisation_admin | Own org, all modules |
| it_administrator | Connectors, devices, commands, firmware |
| hr_manager | Employees, templates, reports |
| support_engineer | Support, connectors, commands |
| auditor | Read-only audit, reports, analytics |

## API access

All admin operations are available via REST at `/api/v1/`. Obtain a JWT via `POST /api/v1/auth/login` and pass `Authorization: Bearer <token>`.

See [API.md](./API.md) for endpoint reference.
