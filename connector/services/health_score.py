"""System health score for the connector operations dashboard.

The score is computed from live application state — never hard-coded.

Formula (weights sum to 1.0)
----------------------------
overall = round(
    connectivity        * 0.20  +
    device_availability * 0.30  +
    upload_success      * 0.25  +
    latency             * 0.10  +
    clock_sync          * 0.15
)

Each component is a 0–100 integer. Status bands:

* Healthy   ≥ 85
* Warning   ≥ 60
* Critical  < 60

Component rules
---------------
Connectivity
    Internet online + cloud healthy → 100
    Internet online + cloud slow/degraded → 70
    Internet online + cloud unavailable/auth/tls → 35
    Internet offline → 0

Device availability
    online / enabled_devices * 100  (100 if no devices registered)

Upload success
    uploaded / (uploaded + failed + pending) * 100 for the current day
    100 if there is no attendance volume yet

Latency
    avg_ms ≤ 1500 → 100
    avg_ms ≤ 3000 → 95
    avg_ms ≤ 10000 → 70
    else → 40
    100 if no samples

Clock sync
    100 if no clock mismatches
    70 if any warning-level drift
    25 if any device reports an unset/impossible clock (e.g. year 2000)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.constants import API_LATENCY_ACCEPTABLE_MS, API_LATENCY_HEALTHY_MS, API_LATENCY_SLOW_MS

WEIGHT_CONNECTIVITY = 0.20
WEIGHT_DEVICE_AVAILABILITY = 0.30
WEIGHT_UPLOAD_SUCCESS = 0.25
WEIGHT_LATENCY = 0.10
WEIGHT_CLOCK_SYNC = 0.15

STATUS_HEALTHY = "healthy"
STATUS_WARNING = "warning"
STATUS_CRITICAL = "critical"


@dataclass(frozen=True)
class HealthBreakdown:
    overall: int
    status: str
    label: str
    connectivity: int
    device_availability: int
    upload_success: int
    latency: int
    clock_sync: int
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "status": self.status,
            "label": self.label,
            "connectivity": self.connectivity,
            "device_availability": self.device_availability,
            "upload_success": self.upload_success,
            "latency": self.latency,
            "clock_sync": self.clock_sync,
            "notes": dict(self.notes),
            "weights": {
                "connectivity": WEIGHT_CONNECTIVITY,
                "device_availability": WEIGHT_DEVICE_AVAILABILITY,
                "upload_success": WEIGHT_UPLOAD_SUCCESS,
                "latency": WEIGHT_LATENCY,
                "clock_sync": WEIGHT_CLOCK_SYNC,
            },
        }


def _clamp(value: float) -> int:
    return int(max(0, min(100, round(value))))


def connectivity_score(internet_status: str, cloud_status: str) -> int:
    internet = str(internet_status or "").strip().lower()
    cloud = str(cloud_status or "").strip().lower()
    if internet in {"offline", "unavailable", "error"}:
        return 0
    if cloud in {"connected", "healthy", "ok", "online"}:
        return 100
    if cloud in {"slow", "slightly slow", "degraded", "unknown"}:
        return 70
    if cloud in {"unavailable", "timeout", "tls_error", "auth_error", "error"}:
        return 35
    if not cloud or cloud in {"—", "-"}:
        return 60
    return 80


def device_availability_score(*, online: int, total_enabled: int, total: int | None = None) -> int:
    denom = int(total_enabled)
    if denom <= 0:
        denom = int(total or 0)
    if denom <= 0:
        return 100
    return _clamp(100.0 * max(0, int(online)) / denom)


def upload_success_score(*, uploaded: int, failed: int, pending: int) -> int:
    uploaded = max(0, int(uploaded))
    failed = max(0, int(failed))
    pending = max(0, int(pending))
    total = uploaded + failed + pending
    if total <= 0:
        return 100
    # Pending is a soft penalty: still collected, not yet confirmed.
    effective_success = uploaded + pending * 0.35
    return _clamp(100.0 * effective_success / total)


def latency_score(average_ms: float | None) -> int:
    if average_ms is None:
        return 100
    avg = float(average_ms)
    if avg <= 0:
        return 100
    if avg <= API_LATENCY_HEALTHY_MS:
        return 100
    if avg <= API_LATENCY_ACCEPTABLE_MS:
        return 95
    if avg <= API_LATENCY_SLOW_MS:
        return 70
    return 40


def clock_sync_score(*, mismatch_count: int, critical_mismatch: bool) -> int:
    if critical_mismatch:
        return 25
    if int(mismatch_count) > 0:
        return 70
    return 100


def status_for_score(score: int) -> tuple[str, str]:
    if score >= 85:
        return STATUS_HEALTHY, "Healthy"
    if score >= 60:
        return STATUS_WARNING, "Attention required"
    return STATUS_CRITICAL, "Critical"


def compute_health_score(
    *,
    internet_status: str,
    cloud_status: str,
    online_devices: int,
    enabled_devices: int,
    total_devices: int = 0,
    uploaded_today: int = 0,
    failed_uploads: int = 0,
    pending_uploads: int = 0,
    average_upload_ms: float | None = None,
    clock_mismatch_count: int = 0,
    critical_clock_mismatch: bool = False,
) -> HealthBreakdown:
    conn = connectivity_score(internet_status, cloud_status)
    devices = device_availability_score(
        online=online_devices,
        total_enabled=enabled_devices,
        total=total_devices,
    )
    uploads = upload_success_score(
        uploaded=uploaded_today,
        failed=failed_uploads,
        pending=pending_uploads,
    )
    latency = latency_score(average_upload_ms)
    clocks = clock_sync_score(
        mismatch_count=clock_mismatch_count,
        critical_mismatch=critical_clock_mismatch,
    )
    overall = _clamp(
        conn * WEIGHT_CONNECTIVITY
        + devices * WEIGHT_DEVICE_AVAILABILITY
        + uploads * WEIGHT_UPLOAD_SUCCESS
        + latency * WEIGHT_LATENCY
        + clocks * WEIGHT_CLOCK_SYNC
    )
    status, label = status_for_score(overall)
    return HealthBreakdown(
        overall=overall,
        status=status,
        label=label,
        connectivity=conn,
        device_availability=devices,
        upload_success=uploads,
        latency=latency,
        clock_sync=clocks,
        notes={
            "online_devices": int(online_devices),
            "enabled_devices": int(enabled_devices),
            "total_devices": int(total_devices),
            "clock_mismatch_count": int(clock_mismatch_count),
            "critical_clock_mismatch": bool(critical_clock_mismatch),
        },
    )


def compute_health_score_from_snapshot(snap: Mapping[str, Any]) -> HealthBreakdown:
    """Convenience wrapper used by ConnectorEngine.dashboard()."""
    return compute_health_score(
        internet_status=str(snap.get("internet_status") or ""),
        cloud_status=str(snap.get("cloud_status") or ""),
        online_devices=int(snap.get("online_devices") or snap.get("connected_devices") or 0),
        enabled_devices=int(snap.get("enabled_devices") or 0),
        total_devices=int(snap.get("total_devices") or 0),
        uploaded_today=int(snap.get("uploaded_today") or 0),
        failed_uploads=int(snap.get("failed_uploads") or 0),
        pending_uploads=int(snap.get("pending_uploads") or snap.get("queue_count") or 0),
        average_upload_ms=snap.get("average_upload_ms"),
        clock_mismatch_count=int(snap.get("clock_mismatch_count") or 0),
        critical_clock_mismatch=bool(snap.get("critical_clock_mismatch")),
    )
