# Developer Guide

## Repository Layout

```text
connector/
  app/                 # bootstrap, constants, DI container, logging
  adapters/            # IBiometricAdapter + eSSL/ZKTeco + ZK protocol
  api/                 # CloudClient
  config/              # encrypted settings manager
  models/              # dataclasses / enums
  scheduler/           # PollScheduler (ThreadPoolExecutor)
  security/            # Fernet + command security
  services/            # engine + Phase 2 services
  storage/             # SQLite + repositories
  ui/                  # PySide6 dashboard dialogs
  docs/                # enterprise documentation
  tests/               # pytest
```

## Local Development

```powershell
cd connector
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -q
python main.py
```

## Adding a Manufacturer

1. Implement `IBiometricAdapter` under `adapters/<vendor>/`
2. Register in `adapters/registry.py`
3. Optionally extend discovery probe heuristics in `services/network_scanner.py`
4. Keep device commands (`enable/disable/clear/restart`) best-effort with clear errors

## Extending Remote Commands

1. Add enum value in `models.CommandType` and `app.constants.REMOTE_COMMAND_TYPES`
2. Handle in `services/command_service.py::_dispatch`
3. Ensure cloud signs payload with shared secret
4. Cover with unit tests around security gate + dispatch

## Database Migrations

Additive only. New columns are applied in `storage/database.py` via
`DEVICE_COLUMN_MIGRATIONS` / `HEARTBEAT_COLUMN_MIGRATIONS`. Never drop Phase 1 columns.

## Testing Tips

- Use temp SQLite via fixtures (`tests/test_core.py`)
- Mock `CloudClient` for command/config/health push tests
- For protocol tests, prefer a device simulator or recorded fixtures

## Packaging

```powershell
.\installer\build.ps1
```

Produces tray + service executables and Inno Setup installer artifacts.

## Coding Standards

- Extend existing modules; do not rewrite architecture
- No placeholders / TODOs in production paths
- Preserve backward compatibility of config and SQLite
- Keep UI thin; business logic stays in services
