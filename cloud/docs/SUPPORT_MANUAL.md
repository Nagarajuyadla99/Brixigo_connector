# Brixigo Cloud — Support Manual

## Common issues

### Connector shows offline

1. Check connector agent process is running on-premise.
2. Verify network connectivity to cloud URL.
3. Confirm API key has not been rotated.
4. Review `last_heartbeat_at` in Connectors module.
5. Background worker marks connectors offline after `connector_offline_seconds` (default 300s).

**Actions:** Use **Reconnect** or **Restart** from Connectors module to queue remote commands.

### Device offline but connector online

1. Check device IP/port and network path.
2. Review device `last_health_at` timestamp.
3. Inspect connector logs via **Support → Download Logs**.

### Queue overflow alert

Triggered when `queue_size >= queue_overflow_threshold` (default 5000).

**Actions:**
- **Support → Clear Queue** sends `clear_queue` command.
- Investigate upstream network or cloud connectivity from connector.

### Commands stuck in queued

1. Verify command has not expired (`expires_at`).
2. Confirm connector is online and polling commands.
3. Check HMAC secret matches between cloud and connector.

### Employee sync not reaching devices

1. Confirm employee has `device_ids` assigned.
2. Those devices must belong to active connectors.
3. Use **Employees → Sync** to re-enqueue `EmployeeSyncEvent`.

## Diagnostic commands

| Action | Command type | Module |
|--------|-------------|--------|
| Download logs | `download_logs` | Support |
| Run diagnostics | `run_diagnostics` | Support |
| Clear queue | `clear_queue` | Support |
| Restart connector | `connector_restart` | Connectors |
| Firmware upgrade | `firmware_upgrade` | Firmware |

## Artifacts

Support artifacts uploaded by connectors appear in **Support** module. Files are stored under `storage/artifacts/`.

## Escalation data to collect

1. Connector ID and organisation
2. Recent audit log entries (filter by connector/actor)
3. Pending commands list
4. Notification history (unacknowledged)
5. Support artifact bundle if available

## Health endpoints

- Cloud: `GET /health`
- Connector: refer to on-premise connector documentation

## Contact

For platform-level issues, contact your Brixigo platform administrator with organisation code and connector IDs.
