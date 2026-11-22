"""Employee code alignment: Emp001 on the server equals 001 on the mission."""

from app.services.employee_codes import (
    codes_align,
    display_employee_code,
    mission_employee_code,
)


def test_prefixed_server_code_aligns_with_mission_number():
    assert codes_align("Emp001", "001")
    assert codes_align("EMP1119", "1119")
    assert codes_align("Emp001", "1")
    assert not codes_align("Emp001", "Emp002")
    assert not codes_align("Emp001", "AB001")


def test_mission_gets_the_number_and_server_keeps_the_prefix():
    assert mission_employee_code("Emp001", omit_prefix=True, prefix="Emp") == "001"
    assert mission_employee_code("Emp001", omit_prefix=False, prefix="Emp") == "Emp001"
    assert mission_employee_code("25", omit_prefix=True, prefix="Emp") == "25"
    assert mission_employee_code("STF001", omit_prefix=True, prefix="Emp") == "STF001"
    assert display_employee_code("001", prefix="Emp") == "Emp001"
    assert display_employee_code("Emp001", prefix="Emp") == "Emp001"
    assert display_employee_code("25", prefix="") == "25"
