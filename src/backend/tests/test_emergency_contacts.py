"""Emergency contact validation shares the patient name constraints."""

import pytest
from pydantic import ValidationError

from kompas.modules.identity.schemas import EmergencyContact

BODY = dict(last_name=" Иванов ", first_name=" Иван ", role="Брат", phone="79991234567")


@pytest.mark.parametrize("phone,expected", [
    ("7 (999) 123-45-67", "+79991234567"),
    ("+7 999 123 45 67", "+79991234567"),
    ("8 (999) 123-45-67", "89991234567"),
    ("9991234567", "+79991234567"),
])
def test_phone_normalization(phone: str, expected: str) -> None:
    contact = EmergencyContact.model_validate({**BODY, "phone": phone})
    assert contact.phone == expected
    assert (contact.last_name, contact.first_name, contact.middle_name) == ("Иванов", "Иван", "")


@pytest.mark.parametrize("field,value", [
    ("last_name", " "), ("first_name", " "),
    ("last_name", "я" * 61), ("first_name", "я" * 61), ("middle_name", "я" * 61),
    ("role", "я" * 16), ("role", " "),
    ("phone", "123"), ("phone", "799912345678"), ("phone", "7abc9991234567"), ("phone", ""),
])
def test_invalid_contact(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        EmergencyContact.model_validate({**BODY, field: value})


def test_maximum_lengths() -> None:
    contact = EmergencyContact.model_validate({
        **BODY, "last_name": "я" * 60, "first_name": "я" * 60, "middle_name": "я" * 60, "role": "я" * 15,
    })
    assert len(contact.role) == 15
