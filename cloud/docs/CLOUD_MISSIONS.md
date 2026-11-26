# Cloud Missions (Port 80 URL Push)

**Cloud Missions** are partner biometric devices (Silicon Wireless STL040 / BAMS) that push attendance by calling a cloud URL over **HTTP Port 80** (or HTTPS 443). This is separate from LAN devices polled on **TCP 4370** by the Windows Connector.

## Flow

```
Mission punch (login)
  → HTTP GET /attendance?  or  POST /attservice?
  → connector.brixigo.com (this Cloud app)
  → AttendancePunch stored (source=cloud-mission)
  → HRMS outbox → a.brixigo.com
```

## Admin UI

Open **Cloud Missions** in the cloud dashboard:

1. Save the 5-digit **STL SID** (device Org ID).
2. Copy the SMS host URL (Port 80).
3. Watch devices and the URL punch feed on the same page.

## Device configuration (SMS)

```text
*555#001#connector.brixigo.com/attendance?#80#
*555#007#9999988#          # SID 99999 + MID 88
```

JSON POST devices may use `connector.brixigo.com/attservice?` with `MachineID` = SID+MID.

## Protocol acknowledgements

| Mode | Request | Response |
|------|---------|----------|
| GET | `?$SID&MID&RFID&DOT,...*` | `$RFID=0#` |
| POST JSON | `{"MachineID","Attendance":[...]}` | `{"DeviceResponse": 0}` |

PunchTime format: `DDMMYYYYHHMMSS`. PunchStatus `0`=IN, `1`=OUT.

## Related files

- Ingest: `app/services/stl_attendance.py`, `app/api/partners/stl.py`
- Admin: `app/api/admin/cloud_missions.py`
- Deploy: `docs/DEPLOYMENT.md` (nginx for connector.brixigo.com)
