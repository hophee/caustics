"""Patient name validation at the API boundary."""

import pytest
from pydantic import ValidationError

from kompas.modules.identity.schemas import PatientCreate


@pytest.mark.parametrize("field", ["last_name", "first_name", "middle_name"])
def test_name_length(field: str) -> None:
    body = dict(last_name="Иванов", first_name="Иван", birth_date="1990-01-01", sex="male")
    assert getattr(PatientCreate.model_validate({**body, field: "я" * 60}), field) == "я" * 60
    with pytest.raises(ValidationError):
        PatientCreate.model_validate({**body, field: "я" * 61})


def test_name_whitespace_and_optional_patronymic() -> None:
    body = dict(last_name=" Иванов ", first_name=" Иван ", birth_date="1990-01-01", sex="male")
    patient = PatientCreate.model_validate(body)
    assert (patient.last_name, patient.first_name, patient.middle_name) == ("Иванов", "Иван", "")
    for field in ("last_name", "first_name"):
        with pytest.raises(ValidationError):
            PatientCreate.model_validate({**body, field: "   "})
