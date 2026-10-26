"""Human-readable attendance sync summaries for the dashboard and tray."""

from __future__ import annotations


def format_sync_dialog(results: list[dict]) -> tuple[str, str, bool]:
    """
    Return (title, body, overall_success) for Sync Now / Sync Selected.

    Never claims the upload queue was flushed when records remain pending.
    """
    if not results:
        return "Sync", "No devices to sync.", True

    ok = sum(1 for r in results if r.get("success"))
    total = len(results)
    pending_any = any(int(r.get("pending") or 0) > 0 for r in results)
    serialization_any = any(r.get("serialization_error") for r in results)
    overall = ok == total and not serialization_any

    blocks: list[str] = []
    for row in results:
        name = _device_label(row)
        status = row.get("status") or ("SUCCESS" if row.get("success") else "FAILED")
        duration = row.get("duration_sec")
        duration_line = ""
        if isinstance(duration, (int, float)):
            duration_line = f"Duration: {float(duration):.1f} sec"
        lines = [
            f"Device: {name}",
            f"Status: {status}",
            "",
            f"Attendance records read: {int(row.get('fetched') or 0)}",
            f"New records: {int(row.get('inserted') or 0)}",
            f"Duplicates: {int(row.get('duplicates') or 0)}",
            f"Uploaded: {int(row.get('uploaded') or 0)}",
            f"Pending: {int(row.get('pending') or 0)}",
            f"Failed: {int(row.get('failed') or row.get('upload_failed') or 0)}",
        ]
        if duration_line:
            lines.append(duration_line)
        if not row.get("success"):
            lines.append("")
            if row.get("serialization_error"):
                lines.append("Error:")
                lines.append("Attendance timestamp could not be serialized to JSON.")
            else:
                err = str(row.get("error") or row.get("upload_error") or "").strip()
                if err:
                    lines.append("Error:")
                    lines.append(err)
                if "tcp connect failed" in err.lower():
                    already_explained = any(
                        token in err.lower()
                        for token in (
                            "no path to the terminal",
                            "lan scan",
                            "this pc (",
                            "client isolation",
                        )
                    )
                    if not already_explained:
                        lines.append("")
                        lines.append(
                            "The terminal did not accept TCP 4370. Punches already "
                            "stored on this PC stay visible. Auto-sync retries every 1 minute."
                        )
                        if any(
                            token in err.lower()
                            for token in ("10065", "10051", "unreachable")
                        ):
                            lines.append(
                                "Windows has no route/ARP to that IP. Power on the "
                                "device, confirm it is on this PC's LAN, or use Discover devices."
                            )
        blocks.append("\n".join(lines).rstrip())

    detail = "\n\n".join(blocks)
    if ok == 0:
        title = "Sync Failed"
        reason = "Attendance synchronization failed."
        if serialization_any:
            reason = (
                "Device is connected, but attendance synchronization failed.\n\n"
                "Reason:\nAttendance upload serialization error.\n\n"
                "Pending attendance records have been retained and will be retried automatically."
            )
        body = f"Sync failed for {total} device{'s' if total != 1 else ''}.\n\n{reason}\n\n{detail}"
        return title, body, False

    title = "Sync Complete" if overall else "Sync Incomplete"
    header = f"Synced {ok}/{total} device{'s' if total != 1 else ''}."
    if pending_any:
        header += "\nCloud upload continues in the background."
    return title, f"{header}\n\n{detail}", overall


def format_sync_tray(results: list[dict]) -> str:
    title, body, _ok = format_sync_dialog(results)
    first = body.split("\n\n", 1)[0]
    return f"{title}: {first}"


def _device_label(row: dict) -> str:
    name = str(row.get("device_name") or "").strip()
    manufacturer = str(row.get("manufacturer") or "").strip()
    model = str(row.get("model") or "").strip()
    if manufacturer and model:
        vendor = f"{manufacturer} {model}".strip()
        if name and name.lower() not in vendor.lower():
            return f"{vendor} ({name})"
        return vendor
    if name:
        return name
    return str(row.get("device_id") or "Device")
