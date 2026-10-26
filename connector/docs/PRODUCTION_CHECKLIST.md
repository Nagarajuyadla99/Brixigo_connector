# Production Release Checklist — Brixigo Connector Agent 3.0.2

Use this checklist for every customer deployment. Do not skip soak verification on the first site of a release train.

## Pre-flight

- [ ] Build artifacts present: `BrixigoConnector.exe`, `BrixigoConnectorService.exe`
- [ ] Installer or Client Share Pack version matches `APP_VERSION` (3.0.2)
- [ ] Cloud tenant created; organisation ID and device/API token issued
- [ ] Command HMAC secret agreed (`command_signing_secret` == cloud `BRIXIGO_CLOUD_COMMAND_HMAC_SECRET`)
- [ ] Master key generated and stored in the customer password vault
- [ ] Firewall openings confirmed (see Network below)

## Installation

- [ ] Run installer **as Administrator** (or Client Share `Install-Client.ps1`)
- [ ] Confirm service `BrixigoConnector` exists once (no duplicates)
- [ ] Confirm start type is **Automatic (Delayed Start)**
- [ ] Confirm failure actions restart at 5s / 15s / 60s
- [ ] Set machine env (if not passed to setup script):
  - `BRIXIGO_CONNECTOR_ENV=production`
  - `BRIXIGO_CONNECTOR_MASTER_KEY=<vault secret>`
- [ ] Restart service after setting environment variables
- [ ] Launch tray — tooltip should say **UI (service manages sync)** when service is running

## Configuration

- [ ] Open Dashboard → Settings
- [ ] Organisation ID, API key, HTTPS cloud URL, connector ID saved
- [ ] Command signing secret saved
- [ ] `verify_ssl` remains enabled
- [ ] Validation rejects HTTP URLs / empty org / empty API key

## Device setup

- [ ] Add ZKTeco or eSSL device (IP, port 4370, comm password, terminal code)
- [ ] Scaffold manufacturers (Anviz/Matrix/etc.) are **not** used in production
- [ ] Test connection succeeds
- [ ] Device clock sync within drift threshold
- [ ] Manual Sync Now (service poll cycle) downloads punches

## Cloud registration & health

- [ ] Connector registers / appears in cloud admin
- [ ] Heartbeat updates within 2 intervals
- [ ] Monitoring snapshot accepted
- [ ] Device health samples appear

## Live punch verification

- [ ] Punch on device
- [ ] Row appears in local SQLite queue then status `uploaded`
- [ ] Punch visible in Brixigo Payroll / HRMS with correct employee code and terminal
- [ ] Idempotent re-download does **not** create duplicates

## Resilience

- [ ] Disconnect LAN → punches buffer offline → reconnect uploads with backoff
- [ ] Stop service → restart → queue recovers (`uploading` → `pending` if stale)
- [ ] Kill process / crash recovery via Windows service failure actions
- [ ] Exhausted retries move to `dead` (dead-letter), not silent skip
- [ ] Tray Exit does **not** stop the Windows service engine

## Soak test

- [ ] 24h continuous run with real or lab device
- [ ] Optional 48h / 72h for flagship sites
- [ ] No unbounded queue growth under normal connectivity
- [ ] Memory / handle counts stable (Task Manager or soak tools)
- [ ] Logs rotate daily; no secrets in log files

## Acceptance

- [ ] Operations sign-off on punch latency (device → cloud)
- [ ] Backup of `data/connector.config.enc` + `data/*.db*` + master key documented
- [ ] Rollback package (previous installer) retained on USB/share
- [ ] Customer contacts and escalation path recorded

## Rollback

1. Stop service: `sc stop BrixigoConnector`
2. Uninstall or overwrite with previous version binaries
3. Restore `data/` backup if schema incompatible (rare — migrations are additive)
4. Start service; verify heartbeat + one live punch

## Recovery (queue)

1. Confirm service running and internet OK
2. Inspect `logs/upload.log` and `logs/error.log`
3. Stale `uploading` rows auto-recover within ~60 minutes
4. Inspect `dead` rows; correct root cause; re-queue only with engineering guidance
5. Never delete `idempotency_key` uniqueness without payroll impact review
