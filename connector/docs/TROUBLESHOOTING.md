# Troubleshooting Guide

## Connector Status Stopped

- Open tray → Restart Service / Restart Engine
- Confirm Windows Service `BrixigoConnector` is Running
- Check `logs/service.log` and `logs/crash.log`

## Internet / Cloud Offline

1. Diagnostics → Check Internet
2. Diagnostics → DNS Test
3. Diagnostics → Check API
4. Diagnostics → Latency / Certificate
5. Verify proxy settings and firewall HTTPS 443

## Device Offline

1. Device Details → confirm IP/port
2. Diagnostics → Ping Device / Check Port
3. Verify comm password
4. Confirm no duplicate connector polling the same device aggressively
5. Review `logs/connection.log`

## Queue Growing

- Check internet/cloud auth
- Review Failed Uploads metric
- Confirm terminal codes exist in cloud
- Increase worker threads / batch size via remote config
- Inspect `logs/upload.log`

## Upload Authentication Failed

- Re-enter API key / JWT in Settings
- Confirm Organisation ID and Connector ID
- Alert `api_auth_failed` will fire; fix credentials then acknowledge

## Discovery Finds Nothing

- Confirm range syntax: `192.168.1.1-254` or `192.168.1.0/24`
- Devices must expose ZK-compatible TCP port (default 4370)
- Temporarily disable host firewall for scan host
- Try known device IP alone first

## Remote Command Rejected

- Configure matching command signing secret
- Ensure cloud sends nonce + signature
- Check audit logs for `command_replay_rejected` / `command_signature_rejected`

## High Memory / Disk

- Cleanup worker removes old uploaded punches and rotated logs
- Reduce health retention indirectly by keeping default cleanup enabled
- Ensure `data/` is on a disk with free space

## Collecting Support Bundle

Export from Log Viewer:

- connector.log
- error.log
- upload.log
- connection.log
- health.log

Include dashboard screenshot (queue, cloud status, version).
