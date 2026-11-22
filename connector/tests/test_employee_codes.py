"""Connector shows Emp001 and treats mission 001 as the same employee."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.employee_codes import (
    codes_align,
    display_employee_code,
    mission_employee_code,
    set_active_policy,
)
from services.employee_directory import enrich_employee_fields


def teardown_function():
    set_active_policy(prefix="", omit_prefix=False)


def test_emp001_aligns_with_mission_001():
    assert codes_align("Emp001", "001")
    assert mission_employee_code("Emp001", omit_prefix=True, prefix="Emp") == "001"
    assert display_employee_code("001", prefix="Emp") == "Emp001"
    assert display_employee_code("Emp001", prefix="Emp") == "Emp001"


def test_directory_shows_prefix_and_keeps_device_code():
    set_active_policy(prefix="Emp", omit_prefix=True)
    row = enrich_employee_fields({"device_employee_code": "001", "employee_name": "Asha"})
    assert row["employee_code"] == "001"
    assert row["display_employee_code"] == "Emp001"
    assert row["mission_employee_code"] == "001"
    assert row["employee_display_name"] == "Asha (Emp001)"


def test_directory_without_policy_is_unchanged():
    set_active_policy(prefix="", omit_prefix=False)
    row = enrich_employee_fields({"device_employee_code": "28", "employee_name": ""})
    assert row["employee_code"] == "28"
    assert row["display_employee_code"] == "28"
    assert row["employee_name_reported"] is False
