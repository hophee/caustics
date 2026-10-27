"""ICD-10 codes as a value type.

Codes arrive with mixed case, stray spaces and Cyrillic look-alike letters (Е, К, Т, ...). They are
normalised once on construction; the original spelling is kept for display. Rules never match codes
by string truncation; they use ``IcdPattern``.
"""

import re
from dataclasses import dataclass

from kompas.core.errors import DefinitionError, InvalidInputError

# Cyrillic letters that look like Latin ones and appear in hand-typed codes.
_LOOKALIKES = str.maketrans("АВЕКМНОРСТХаевкмнорстх", "ABEKMHOPCTXaebkmhopctx")
_CODE = re.compile(r"^[A-Z]\d{2}(\.\d{1,2})?$")
_CATEGORY = re.compile(r"^[A-Z]\d{2}$")


def _normalise(raw: str) -> str:
    return re.sub(r"\s+", "", raw.translate(_LOOKALIKES)).upper().replace(",", ".")


@dataclass(frozen=True, slots=True)
class IcdCode:
    """A normalised ICD-10 code such as ``F33.2`` or ``N18``.

    ``code`` is canonical (upper-case Latin, no spaces); ``original`` is what was entered.
    """

    code: str
    original: str

    @classmethod
    def parse(cls, raw: str) -> "IcdCode":
        """Normalise ``raw`` (Cyrillic look-alikes → Latin, upper-case, strip spaces).

        Raises:
            InvalidInputError: the result does not look like an ICD-10 code (letter + 2 digits,
            optional ``.`` + 1–2 digits).
        """
        code = _normalise(raw)
        if not _CODE.match(code):
            raise InvalidInputError(f"Некорректный код МКБ-10: {raw!r}")
        return cls(code=code, original=raw)

    @property
    def category(self) -> str:
        """Three-character category: ``"F33"`` for ``F33.2``."""
        return self.code[:3]

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class IcdPattern:
    """A set of codes a rule applies to.

    Forms: a category (``N18``: N18 and all its subcodes), a subcode (``E74.3``: that code and its
    subcodes) or a range of categories (``K70-K76``: every category from K70 to K76 inclusive).
    """

    text: str

    @classmethod
    def parse(cls, raw: str) -> "IcdPattern":
        """Validate and normalise a pattern.

        Raises:
            DefinitionError: malformed pattern (used when loading rule files).
        """
        text = _normalise(raw)
        if "-" in text:
            low, _, high = text.partition("-")
            if not (_CATEGORY.match(low) and _CATEGORY.match(high)) or low[0] != high[0] or low > high:
                raise DefinitionError(f"Некорректный диапазон МКБ-10: {raw!r}")
        elif not _CODE.match(text):
            raise DefinitionError(f"Некорректный шаблон МКБ-10: {raw!r}")
        return cls(text=text)

    def matches(self, code: IcdCode) -> bool:
        """True if ``code`` belongs to this pattern."""
        if "-" in self.text:
            low, _, high = self.text.partition("-")
            return low <= code.category <= high
        if "." in self.text:
            return code.code == self.text or code.code.startswith(self.text)
        return code.category == self.text

    def __str__(self) -> str:
        return self.text
