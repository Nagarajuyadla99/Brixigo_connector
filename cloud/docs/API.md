# Brixigo Cloud — API Reference

## Admin API

Base URL: `/api/v1`

Authentication: `Authorization: Bearer <JWT>` (except login).

## Auth

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/login` | Login — returns JWT |
| GET | `/auth/me` | Current user profile |

## Tenancy

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/tenancy/organisations` | List / create organisations |
| GET/PATCH | `/tenancy/organisations/{id}` | Get / update organisation |
| GET/POST | `/tenancy/branches` | Branch CRUD |
| PATCH/DELETE | `/tenancy/branches/{id}` | Update / delete branch |
| GET/POST | `/tenancy/departments` | Department CRUD |
| GET/POST | `/tenancy/buildings` | Building CRUD |
| GET/POST | `/tenancy/floors` | Floor CRUD |
| GET/POST | `/tenancy/gates` | Gate CRUD |

## Connectors

| Method | Path | Description |
|--------|------|-------------|
| GET | `/connectors` | List connectors |
| GET | `/connectors/{id}` | Get connector |
| POST | `/connectors/register` | Register connector (returns API key) |
| POST | `/connectors/{id}/deactivate` | Deactivate |
| DELETE | `/connectors/{id}` | Delete |
| POST | `/connectors/{id}/restart` | Queue restart command |
| POST | `/connectors/{id}/upgrade` | Queue upgrade command |
| POST | `/connectors/{id}/reconnect` | Queue reconnect command |

## Devices

| Method | Path | Description |
|--------|------|-------------|
| GET | `/devices` | List with search/filter |
| GET | `/devices/{id}` | Get device |
| POST | `/devices/bulk` | Bulk enable/disable |
| GET | `/devices/export/csv` | Export fleet CSV |

## Commands

| Method | Path | Description |
|--------|------|-------------|
| POST | `/commands` | Create signed command |
| GET | `/commands` | List by status/connector |

## Employees

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/employees` | List / create |
| GET/PATCH/DELETE | `/employees/{id}` | CRUD |
| POST | `/employees/{id}/sync` | Re-enqueue sync events |
| POST | `/employees/bulk-assign` | Assign devices to employees |
| POST | `/employees/{id}/transfer` | Transfer branch/dept |

## Templates

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/templates` | List / store encrypted template |
| POST | `/templates/{id}/restore` | Restore previous version |
| GET | `/templates/compare/{a}/{b}` | Compare checksums |

## Provisioning

| Method | Path | Description |
|--------|------|-------------|
| GET | `/provisioning/discover` | Pending connectors |
| POST | `/provisioning/approve` | Approve connector |
| POST | `/provisioning/assign` | Assign device location |
| GET | `/provisioning/unassigned-devices` | Devices without branch |

## Firmware

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/firmware/packages` | List / upload package |
| GET/POST | `/firmware/jobs` | List / create upgrade jobs |

## Support

| Method | Path | Description |
|--------|------|-------------|
| POST | `/support/connectors/{id}/download-logs` | Queue log download |
| POST | `/support/connectors/{id}/diagnostics` | Queue diagnostics |
| GET | `/support/artifacts` | List artifacts |
| POST | `/support/connectors/{id}/clear-queue` | Clear connector queue |

## Analytics & Monitoring

| Method | Path | Description |
|--------|------|-------------|
| GET | `/analytics/summary` | Dashboard aggregates |
| GET | `/monitoring/dashboard` | Live status counts |

## Reports

| Method | Path | Description |
|--------|------|-------------|
| POST | `/reports` | Create report job |
| GET | `/reports` | List jobs |
| GET | `/reports/{id}/download` | Download completed report |

## Payroll / website attendance feed

| Method | Path | Description |
|--------|------|-------------|
| GET | `/payroll/punches` | Org-scoped punch feed for HRMS / Brixigo website |

Query params: `since` (ISO), `terminal_code`, `employee_code`, `today`, `order` (`asc`\|`desc`), `limit` (1–5000).

Each punch is joined to the organisation employee roster on `(organisation_id, device_employee_code = employee_code)`. Response fields include:

- `employee_id`, `employee_name`, `employee_matched`
- `device_employee_code` / `employee_code`
- `punch_time`, `event_type`, `verification_method`, `terminal_code`, `organisation_id`
- summary: `matched_employees`, `unmatched_employees`

### Website sync pattern

1. Create Cloud employees whose `employee_code` equals the biometric device user ID for that organisation.
2. Website authenticates: `POST /api/v1/auth/login` → JWT.
3. Poll `GET /api/v1/payroll/punches?since=<last_sync_iso>&order=asc` (store last `created_at` / punch id).
4. Optionally mirror the roster with `GET /api/v1/employees`.
5. Map attendance UI on `employee_name` when `employee_matched` is true; show code + unmatched otherwise.

Tenant isolation: JWT org context scopes both employees and punches — never cross organisations by name alone.

## Notifications & Audit

| Method | Path | Description |
|--------|------|-------------|
| GET | `/notifications` | List notifications |
| POST | `/notifications/{id}/ack` | Acknowledge |
| GET | `/audit` | Search audit logs |

## Settings & Webhooks

| Method | Path | Description |
|--------|------|-------------|
| GET/PUT | `/settings/policies` | System policies |
| GET/POST/DELETE | `/webhooks` | Webhook subscriptions |

## Error responses

- `401` — Missing or invalid JWT
- `403` — RBAC module access denied or tenant isolation violation
- `404` — Resource not found
- `400` — Validation error

## RBAC modules

Endpoints check `can_access(role, module)` against: `connectors`, `devices`, `commands`, `employees`, `templates`, `firmware`, `support`, `reports`, `analytics`, `audit`, `settings`, `tenancy`.

`super_admin` bypasses all module checks.

---

## Connector Agent API

Base URL: `/api/wf/biometric-enterprise`

Compatible with on-premise `connector/api/cloud_client.py`. Do not change path shapes.

Headers (authenticated calls):

- `X-Organisation-Id`
- `X-Connector-Id`
- `X-Device-Token` (connector API key)
- Optional `Authorization: Bearer <JWT>`
- `X-Connector-Version`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Platform health (root, not under prefix) |
| GET | `/time` | Server UTC time |
| GET | `/connectors/updates/latest` | Self-update check |
| POST | `/connectors/register` | Agent register / refresh |
| GET | `/connectors/{id}/config` | Remote configuration |
| POST | `/connectors/{terminal_code}/push` | Attendance upload (gzip supported) |
| POST | `/connectors/{id}/heartbeat` | Heartbeat metrics |
| GET | `/connectors/{id}/status` | Connector status |
| POST | `/connectors/{id}/device-health` | Device health samples |
| POST | `/connectors/{id}/devices/status` | Device online/offline |
| GET | `/connectors/{id}/commands` | Poll HMAC-signed commands |
| POST | `/connectors/{id}/commands/{cid}/ack` | Command acknowledgement |
| POST | `/connectors/{id}/alerts` | Push alerts |
| POST | `/connectors/{id}/monitoring` | Monitoring snapshot |
| POST | `/devices` | Register / upsert device |
| GET | `/connectors/{id}/employees/sync` | Pending employee sync events |
| POST | `/connectors/{id}/employees/sync/{eid}/ack` | Ack employee sync |
| GET | `/connectors/{id}/policies` | Device / org policies |
| POST | `/connectors/{id}/inventory` | Inventory push |
| POST | `/connectors/{id}/notifications` | Notification push |
| POST | `/connectors/{id}/reports` | Report payload push |
| POST | `/connectors/{id}/support/diagnostics` | Diagnostics suite |
| POST | `/connectors/{id}/support/artifacts` | Artifact metadata |
| GET | `/connectors/{id}/dashboard` | Connector-local dashboard summary |

Commands are HMAC-SHA256 signed with `command_hmac_secret` using the same canonical JSON fields as the connector security gate (`cloud_command_id`, `command_type`, `device_id`, `expires_at`, `issued_at`, `nonce`, `payload`).

