"""Resolve organisation / branch / connector names for human-readable API + UI."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Branch, Connector, Device, Organisation


class NameResolver:
    """Batch-load names once per request."""

    def __init__(self, db: Session):
        self._orgs: dict[str, str] = {}
        self._branches: dict[str, str] = {}
        self._connectors: dict[str, str] = {}
        self._devices: dict[str, str] = {}
        for org in db.scalars(select(Organisation)).all():
            self._orgs[org.id] = org.name
        for br in db.scalars(select(Branch)).all():
            self._branches[br.id] = br.name
        for conn in db.scalars(select(Connector)).all():
            self._connectors[conn.id] = conn.name
        for dev in db.scalars(select(Device)).all():
            self._devices[dev.id] = dev.name

    def org(self, org_id: str | None) -> str:
        return self._orgs.get(org_id or "", "") or "—"

    def branch(self, branch_id: str | None) -> str:
        return self._branches.get(branch_id or "", "") or "—"

    def connector(self, connector_id: str | None) -> str:
        return self._connectors.get(connector_id or "", "") or "—"

    def device(self, device_id: str | None) -> str:
        return self._devices.get(device_id or "", "") or "—"


def normalize_identity(value: str) -> str:
    """Compare human IDs ignoring spaces, dashes, and underscores."""
    return "".join(ch.lower() for ch in (value or "") if ch.isalnum())


def resolve_organisation(db: Session, name: str) -> Organisation | None:
    """Find organisation by display name, code, or internal id."""
    key = (name or "").strip()
    if not key:
        return None
    org = db.scalar(select(Organisation).where(Organisation.name == key))
    if org:
        return org
    org = db.scalar(select(Organisation).where(Organisation.code == key))
    if org:
        return org
    org = db.get(Organisation, key)
    if org:
        return org
    needle = normalize_identity(key)
    if not needle:
        return None
    for candidate in db.scalars(select(Organisation)).all():
        if needle in (
            normalize_identity(candidate.name),
            normalize_identity(candidate.code),
            normalize_identity(candidate.id),
        ):
            return candidate
    return None


def resolve_branch(db: Session, org_id: str, name: str) -> Branch | None:
    key = (name or "").strip()
    if not key:
        return None
    branch = db.scalar(
        select(Branch).where(Branch.organisation_id == org_id, Branch.name == key)
    )
    if branch:
        return branch
    branch = db.scalar(
        select(Branch).where(Branch.organisation_id == org_id, Branch.code == key)
    )
    if branch:
        return branch
    return db.get(Branch, key)


def enrich_connector(row: dict, names: NameResolver) -> dict:
    row["organisation_name"] = names.org(row.get("organisation_id"))
    row["branch_name"] = names.branch(row.get("branch_id"))
    return row


def enrich_device(row: dict, names: NameResolver) -> dict:
    row["organisation_name"] = names.org(row.get("organisation_id"))
    row["branch_name"] = names.branch(row.get("branch_id"))
    row["connector_name"] = names.connector(row.get("connector_id"))
    return row


def enrich_command(row: dict, names: NameResolver) -> dict:
    row["organisation_name"] = names.org(row.get("organisation_id"))
    row["connector_name"] = names.connector(row.get("connector_id"))
    row["device_name"] = names.device(row.get("device_id"))
    return row


def enrich_employee(row: dict, names: NameResolver) -> dict:
    row["organisation_name"] = names.org(row.get("organisation_id"))
    row["branch_name"] = names.branch(row.get("branch_id"))
    return row
