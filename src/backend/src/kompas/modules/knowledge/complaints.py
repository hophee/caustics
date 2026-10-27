"""Complaints catalogue for the patient diary (8 categories, 36 complaints; from revision 6)."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kompas.core.errors import DefinitionError, NotFoundError
from kompas.modules.knowledge._files import read_yaml, validate_model


class Complaint(BaseModel):
    """One complaint the patient can tick, with a plain-language description."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str  # stable id used by diary entries and triage rules, e.g. "suicidal_thoughts"
    name: str
    description: str
    critical: bool = False  # ticking it creates an alert immediately (D-030)


class ComplaintCategory(BaseModel):
    """A group of related complaints shown together in the form."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    name: str
    complaints: tuple[Complaint, ...]


class ComplaintCatalog(BaseModel):
    """All categories; complaint codes are unique across the catalogue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    categories: tuple[ComplaintCategory, ...]

    def get(self, code: str) -> Complaint:
        """Return a complaint by code. Raises NotFoundError if unknown."""
        return self._find(code)[1]

    def category_of(self, code: str) -> ComplaintCategory:
        """Return the category containing complaint ``code``. Raises NotFoundError if unknown."""
        return self._find(code)[0]

    def codes(self) -> frozenset[str]:
        """Every complaint code."""
        return frozenset(c.code for cat in self.categories for c in cat.complaints)

    def critical_codes(self) -> frozenset[str]:
        """Codes of complaints marked ``critical``."""
        return frozenset(c.code for cat in self.categories for c in cat.complaints if c.critical)

    def _find(self, code: str) -> tuple[ComplaintCategory, Complaint]:
        for category in self.categories:
            for complaint in category.complaints:
                if complaint.code == code:
                    return category, complaint
        raise NotFoundError(f"Неизвестная жалоба: {code}")


def load_complaints(path: Path) -> ComplaintCatalog:
    """Parse ``complaints.yaml``.

    Raises:
        DefinitionError: malformed file or duplicate complaint codes.
    """
    catalog = validate_model(ComplaintCatalog, read_yaml(path), path.name)
    codes = [c.code for cat in catalog.categories for c in cat.complaints]
    duplicates = sorted({c for c in codes if codes.count(c) > 1})
    if duplicates:
        raise DefinitionError(f"{path.name}: коды жалоб повторяются: {', '.join(duplicates)}")
    return catalog
