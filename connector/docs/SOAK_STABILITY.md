# Soak & Stability Operations — Brixigo Connector 3.0.2

## Purpose

Validate 24 / 48 / 72 hour continuous operation before wide rollout.

## Built-in tools

```powershell
cd connector
.\.venv\Scripts\Activate.ps1

# Engine soak (poll + upload cycles)
python main.py soak --cycles 100 --sleep 5

# Live device soak helper (lab)
python tools\live_device_soak.py --help
```

## What to watch

| Signal | Healthy | Action if bad |
|--------|---------|---------------|
| Pending queue | Flat or sawtooth near zero when online | Check upload.log / cloud auth |
| Dead-letter count | 0 or rare | Fix root cause; do not blindly requeue |
| CPU | Low average, brief spikes on poll | Reduce max workers / batch if sustained |
| Memory | Stable after warmup | Restart service; capture heap notes for engineering |
| Threads | Stable worker count | Restart; report leak if climbs every hour |
| SQLite locks | Rare `database is locked` | Confirm single engine (service owns sync) |
| Heartbeat | Updates every interval | Network / cloud health |
| Disk logs | Daily rotation, ~30 days | Cleanup service / disk space |

## Network recovery drill

1. Disconnect WAN for 30+ minutes while devices keep punching  
2. Confirm queue grows locally  
3. Restore WAN — uploads resume with exponential backoff  
4. Confirm no duplicate payroll punches  

## Service recovery drill

1. `taskkill /F /IM BrixigoConnectorService.exe` (lab only)  
2. Windows recovery should restart within failure action timings  
3. Confirm `uploading` rows recover and heartbeat resumes  

## Dual-engine check

```powershell
sc query BrixigoConnector
# Tray tooltip should indicate UI-only when service is RUNNING
```

Never run a second headless/`python main.py headless` alongside the service.
