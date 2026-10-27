"""Text normalisation shared by every lookup (drug search, keyword matching, complaint codes)."""

import re

_MARKS = re.compile(r"[®™©]")
_SPACES = re.compile(r"\s+")


def normalize_name(text: str) -> str:
    """Lower-case, ё → е, collapse whitespace, strip ® and ™ marks and surrounding spaces.

    ``"  Сертралин®  Канон "`` → ``"сертралин канон"``.
    """
    text = _MARKS.sub("", text).lower().replace("ё", "е")
    return _SPACES.sub(" ", text).strip()
