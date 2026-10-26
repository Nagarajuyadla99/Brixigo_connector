"""Admin API router — aggregates all management endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.admin import (
    analytics,
    audit,
    auth,
    commands,
    connectors,
    devices,
    employees,
    firmware,
    monitoring,
    notifications,
    payroll,
    provisioning,
    reports,
    settings,
    support,
    templates,
    tenancy,
    webhooks,
)

router = APIRouter()

router.include_router(auth.router)
router.include_router(tenancy.router)
router.include_router(connectors.router)
router.include_router(devices.router)
router.include_router(monitoring.router)
router.include_router(commands.router)
router.include_router(employees.router)
router.include_router(templates.router)
router.include_router(provisioning.router)
router.include_router(firmware.router)
router.include_router(support.router)
router.include_router(analytics.router)
router.include_router(reports.router)
router.include_router(payroll.router)
router.include_router(notifications.router)
router.include_router(audit.router)
router.include_router(settings.router)
router.include_router(webhooks.router)
