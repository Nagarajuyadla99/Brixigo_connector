# Architecture Guide — Brixigo Connector Agent (Enterprise Phase 2)

## Overview

The connector is a local Windows agent that polls biometric devices, queues attendance
events in SQLite, and uploads them securely to Brixigo Cloud. Phase 2 extends the
existing Phase 1 runtime with discovery, health, remote control, alerts, and monitoring
without changing the core adapter/queue/upload architecture.

## Runtime Topology

```text
Biometric Devices (eSSL / ZKTeco / …)
        │  TCP/UDP 4370
        ▼
 Device Adapters (IBiometricAdapter)
        │
        ▼
 PollScheduler ── ThreadPoolExecutor (independent per-device workers)
        │
        ▼
 PollService ──► attendance_queue (SQLite)
        │
        ▼
 UploadEngine ── HTTPS gzip batches ──► Brixigo Cloud

 ConnectorEngine
  ├─ PollScheduler
  ├─ UploadEngine
  ├─ HeartbeatService
  ├─ HealthMonitorService
  ├─ CommandService
  ├─ RemoteConfigService
  ├─ CleanupService
  ├─ NetworkScanner
  ├─ AlertService
  └─ NetworkDiagnostics
```

## SOLID Boundaries

| Module | Responsibility |
|---|---|
| `adapters/` | Vendor-specific device I/O |
| `services/` | Application use-cases |
| `storage/` | Persistence / repositories |
| `api/cloud_client.py` | Cloud transport |
| `security/` | Encryption, signatures, TLS |
| `ui/` | Presentation only |
| `scheduler/` | Concurrent polling orchestration |

## Data Model (Phase 2 additions)

- `devices` — hierarchy + telemetry columns
- `device_health` — per-minute samples
- `remote_commands` / `command_nonces` — control plane + replay protection
- `alerts` / `audit_logs` — operations + compliance
- `discovery_cache` / `discovery_ignore` — scan approval workflow
- `heartbeat_history` / `upload_metrics` — monitoring KPIs

## Cloud API Surface

Base: `{cloud}/api/wf/biometric-enterprise`

| Method | Path | Purpose |
|---|---|---|
| POST | `/connectors/{terminal}/push` | Attendance upload |
| POST | `/connectors/{id}/heartbeat` | Host heartbeat |
| POST | `/connectors/{id}/monitoring` | Cloud dashboard feed |
| POST | `/connectors/{id}/device-health` | Device health push |
| GET | `/connectors/{id}/config` | Remote configuration |
| GET | `/connectors/{id}/commands` | Pending commands |
| POST | `/connectors/{id}/commands/{cid}/ack` | Command result |
| POST | `/connectors/{id}/alerts` | Alert fan-out |

## Security Controls

1. Fernet encryption for config + device passwords
2. Optional custom CA bundle / TLS verification
3. HMAC-SHA256 command signatures
4. Nonce replay store
5. Audit log for admin and cloud actions

## Scalability Notes

- Persistent worker pool avoids per-tick executor churn
- In-flight device set prevents duplicate concurrent polls
- Uploaded punches cleaned by retention policy
- Health samples bounded per device
- Designed for 100+ devices and high daily punch volume
