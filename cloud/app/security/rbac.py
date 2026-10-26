"""RBAC permission matrix and helpers."""

from __future__ import annotations

from app.models import Role

# Module → roles allowed (super_admin always allowed)
RBAC_MATRIX: dict[str, set[str]] = {
    "connectors": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.IT_ADMIN.value,
        Role.SUPPORT_ENGINEER.value,
        Role.AUDITOR.value,
    },
    "devices": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.IT_ADMIN.value,
        Role.BRANCH_ADMIN.value,
        Role.SUPPORT_ENGINEER.value,
        Role.AUDITOR.value,
        Role.HR_MANAGER.value,
    },
    "commands": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.IT_ADMIN.value,
        Role.SUPPORT_ENGINEER.value,
    },
    "employees": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.HR_MANAGER.value,
        Role.BRANCH_ADMIN.value,
        Role.AUDITOR.value,
    },
    "templates": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.HR_MANAGER.value,
        Role.IT_ADMIN.value,
        Role.AUDITOR.value,
    },
    "firmware": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.IT_ADMIN.value,
        Role.SUPPORT_ENGINEER.value,
    },
    "support": {
        Role.PLATFORM_ADMIN.value,
        Role.IT_ADMIN.value,
        Role.SUPPORT_ENGINEER.value,
    },
    "reports": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.HR_MANAGER.value,
        Role.IT_ADMIN.value,
        Role.BRANCH_ADMIN.value,
        Role.AUDITOR.value,
        Role.SUPPORT_ENGINEER.value,
    },
    "attendance": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.HR_MANAGER.value,
        Role.IT_ADMIN.value,
        Role.BRANCH_ADMIN.value,
        Role.AUDITOR.value,
        Role.SUPPORT_ENGINEER.value,
    },
    "analytics": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.IT_ADMIN.value,
        Role.AUDITOR.value,
    },
    "audit": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.AUDITOR.value,
        Role.SUPPORT_ENGINEER.value,
    },
    "settings": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
        Role.IT_ADMIN.value,
    },
    "tenancy": {
        Role.PLATFORM_ADMIN.value,
        Role.ORG_ADMIN.value,
    },
}


def can_access(role: str, module: str) -> bool:
    if role == Role.SUPER_ADMIN.value:
        return True
    return role in RBAC_MATRIX.get(module, set())
