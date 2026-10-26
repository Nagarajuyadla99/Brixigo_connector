# Admin Guide

## Multi-Branch Device Organization

Each device supports:

- Organization
- Branch
- Department
- Building
- Floor
- Gate

Use these fields consistently so cloud reports and local filters stay meaningful.

## Device Lifecycle

| Action | Effect |
|---|---|
| Add | Registers device locally (optional cloud register via API) |
| Edit | Updates identity / hierarchy / polling |
| Enable / Disable | Controls polling without deleting history |
| Reconnect | Fresh health + info refresh |
| Delete | Removes device and cascaded queue rows |

## Discovery Workflow

1. Scan subnet
2. Review manufacturer / model / firmware / MAC / SDK
3. **Add** or **Ignore**
4. Re-scan anytime with **Refresh Scan**

Ignored endpoints remain suppressed until unignored in DB/admin tooling.

## Health Monitoring

Every minute the connector collects:

- Online status / ping
- Memory & storage usage (capacity-based when firmware supports it)
- User / log counts and capacities
- Firmware / device time
- Battery / CPU temperature when available
- Restart count when exposed by firmware

Snapshots are stored locally and pushed to cloud.

## Remote Commands

Cloud may issue signed commands:

- Restart Device
- Sync Time
- Download Attendance
- Clear Attendance
- Enable / Disable Device
- Refresh Device Info
- Test Connection

Requirements:

- Matching **Command Signing Secret**
- Unique nonce (replay rejected)
- Optional expiry timestamp

Results are acknowledged back to cloud and written to audit/sync logs.

## Remote Configuration

Cloud can publish:

- Polling / heartbeat / health / command intervals
- Upload batch size and retry count
- Worker thread count
- Logging level
- Queue alert threshold
- TLS / proxy options

Applied automatically; no manual file edits required.

## Alerts

Generated for:

- Device offline
- Internet lost
- Queue growing
- Upload failed
- Low storage
- Connector restarted
- API authentication failed

Acknowledge alerts from the Alerts tab after remediation.

## Logs

Use **Log Viewer** categories:

- Connection
- Attendance/Sync
- Upload
- Health
- Error

Search and export for support tickets.
