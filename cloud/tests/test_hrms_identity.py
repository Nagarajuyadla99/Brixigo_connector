"""HRMS employee identity resolution tests."""

from __future__ import annotations

from app.services.hrms_identity import resolve_hrms_person_name, validate_hrms_employee_id


def test_resolve_hrms_person_name_prefers_snapshot_on_mismatch():
    name = resolve_hrms_person_name(
        roster_name="Nagatvenkatadurga",
        snapshot_name="Umamaheswari",
        employee_code="4",
    )
    assert name == "Umamaheswari"


def test_resolve_hrms_person_name_uses_roster_when_agreeing():
    name = resolve_hrms_person_name(
        roster_name="Umamaheswari",
        snapshot_name="Umamaheswari",
        employee_code="4",
    )
    assert name == "Umamaheswari"


def test_validate_hrms_employee_id_rejects_mismatch():
    ok, msg = validate_hrms_employee_id(
        employee_code="4",
        canonical_id="stable-id-4",
        returned_id="wrong-id-3",
    )
    assert ok is False
    assert "mismatch" in msg.lower()
