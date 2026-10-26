"""Report generation — CSV, Excel, PDF."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AttendancePunch, Connector, Device, Employee, ReportJob, new_id, utcnow
from app.services.employees import reported_person_name, unnamed_employee_label


def _fetch_report_data(db: Session, report_type: str, org_id: str, params: dict) -> list[dict[str, Any]]:
    if report_type == "devices":
        rows = db.scalars(select(Device).where(Device.organisation_id == org_id)).all()
        return [
            {
                "id": d.id,
                "name": d.name,
                "terminal_code": d.terminal_code,
                "status": d.status,
                "health_status": d.health_status,
                "health_score": d.health_score,
                "firmware": d.firmware,
                "ip_address": d.ip_address,
            }
            for d in rows
        ]
    if report_type == "connectors":
        rows = db.scalars(select(Connector).where(Connector.organisation_id == org_id)).all()
        return [
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "version": c.version,
                "connected_devices": c.connected_devices,
                "queue_size": c.queue_size,
            }
            for c in rows
        ]
    if report_type == "employees":
        rows = db.scalars(select(Employee).where(Employee.organisation_id == org_id)).all()
        return [
            {
                "id": e.id,
                "employee_code": e.employee_code,
                "name": reported_person_name(e.name, e.employee_code) or unnamed_employee_label(e.employee_code),
                "designation": e.designation,
                "status": e.status,
                "branch_id": e.branch_id,
            }
            for e in rows
        ]
    if report_type == "attendance":
        q = select(AttendancePunch).where(AttendancePunch.organisation_id == org_id)
        start = params.get("start_date")
        end = params.get("end_date")
        if start:
            q = q.where(AttendancePunch.punch_time >= start)
        if end:
            q = q.where(AttendancePunch.punch_time <= end)
        rows = db.scalars(q.order_by(AttendancePunch.punch_time.desc()).limit(10000)).all()
        emp_rows = db.scalars(select(Employee).where(Employee.organisation_id == org_id)).all()
        names = {str(e.employee_code): e.name for e in emp_rows}
        return [
            {
                "employee_name": reported_person_name(names.get(str(p.device_employee_code)), p.device_employee_code)
                or unnamed_employee_label(p.device_employee_code),
                "employee_code": p.device_employee_code,
                "punch_time": p.punch_time,
                "event_type": p.event_type,
                "terminal_code": p.terminal_code,
                "verification_method": p.verification_method,
            }
            for p in rows
        ]
    return []


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_excel(path: Path, rows: list[dict[str, Any]], title: str) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])
    wb.save(path)


def _write_pdf(path: Path, rows: list[dict[str, Any]], title: str) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4))
    styles = getSampleStyleSheet()
    elements = [Paragraph(title, styles["Title"]), Spacer(1, 12)]
    if rows:
        headers = list(rows[0].keys())
        data = [headers] + [[str(row.get(h, "")) for h in headers] for row in rows[:500]]
        table = Table(data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f4c5c")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4f8")]),
                ]
            )
        )
        elements.append(table)
    else:
        elements.append(Paragraph("No data available.", styles["Normal"]))
    doc.build(elements)


def create_report_job(
    db: Session,
    *,
    organisation_id: str,
    report_type: str,
    format: str,
    parameters: dict | None = None,
    created_by: str = "",
    settings: Settings,
) -> ReportJob:
    job = ReportJob(
        id=new_id(),
        organisation_id=organisation_id,
        report_type=report_type,
        parameters_json=parameters or {},
        status="pending",
        format=format,
        created_by=created_by,
    )
    db.add(job)
    db.flush()
    try:
        rows = _fetch_report_data(db, report_type, organisation_id, parameters or {})
        ext = {"csv": "csv", "excel": "xlsx", "xlsx": "xlsx", "pdf": "pdf"}.get(format.lower(), "csv")
        filename = f"{report_type}_{job.id}.{ext}"
        path = settings.report_dir / filename
        if ext == "csv":
            _write_csv(path, rows)
        elif ext == "xlsx":
            _write_excel(path, rows, report_type)
        elif ext == "pdf":
            _write_pdf(path, rows, f"Brixigo Report — {report_type}")
        job.file_path = str(path)
        job.status = "completed"
        job.completed_at = utcnow()
    except Exception as exc:
        job.status = "failed"
        job.file_path = ""
        job.parameters_json = {**(parameters or {}), "error": str(exc)}
    db.flush()
    write_audit(
        db,
        organisation_id=organisation_id,
        actor_id=created_by,
        action="report.create",
        resource_type="report_job",
        resource_id=job.id,
        message=f"Report {report_type} ({format}) — {job.status}",
    )
    return job


def get_report_job(db: Session, job_id: str) -> ReportJob | None:
    return db.get(ReportJob, job_id)


def list_report_jobs(
    db: Session,
    *,
    organisation_id: str | None = None,
    limit: int = 50,
) -> list[ReportJob]:
    q = select(ReportJob).order_by(ReportJob.created_at.desc())
    if organisation_id:
        q = q.where(ReportJob.organisation_id == organisation_id)
    return list(db.scalars(q.limit(limit)))
