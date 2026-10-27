"""Free-text triage models (D-034, D-035).

A model scores four options for a text: stable / attention / urgent / unsure («Неоднозначно»).
Models run locally; patient text never leaves the server.

Where it runs: CPU-heavy models run only in the background worker, never in a request. The request
path uses the structured rules plus the keyword model, so an urgent checkbox or phrase alerts
instantly; the worker's model verdict can raise the level afterwards. If a model fails or times out,
the keyword verdict is used and the result is flagged ``model_unavailable`` — never a silent STABLE.

Heavy models (Liquid d1, option-logit scoring on Qwen) implement the same protocol in an optional
package so the core installs without ML dependencies.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from kompas.core.text import normalize_name
from kompas.modules.knowledge.triage_rules import KeywordLists


class TriageOption(StrEnum):
    STABLE = "stable"
    ATTENTION = "attention"
    URGENT = "urgent"
    UNSURE = "unsure"


@dataclass(frozen=True, slots=True)
class ModelVerdict:
    """Raw output of a model for one text: a probability per option (sums to 1).

    ``model_version`` identifies weights + prompt (e.g. ``"d1-3b@rev:prompt-sha"``) for audit.
    """

    model_name: str
    model_version: str
    probabilities: Mapping[TriageOption, float]
    latency_ms: float

    def top(self) -> tuple[TriageOption, float]:
        """Most likely option and its probability."""
        option = max(self.probabilities, key=lambda o: self.probabilities[o])
        return option, self.probabilities[option]


class TextTriageModel(Protocol):
    """Anything that can score a diary text."""

    @property
    def name(self) -> str: ...

    def classify(self, text: str) -> ModelVerdict:
        """Score ``text``. Must handle empty and very long input (truncate) without raising for
        content reasons; infrastructure failures may raise and are handled by the caller."""
        ...


class KeywordModel:
    """Deterministic phrase matcher (probability 1.0 on the matched level). The safety net.

    Phrases and text are compared after ``normalize_name``. Urgent phrases win over attention
    phrases. Known weaknesses: negations ("мыслей о самоубийстве нет") and hidden meaning.
    """

    def __init__(self, keywords: KeywordLists) -> None:
        self._urgent = tuple(normalize_name(k) for k in keywords.urgent)
        self._attention = tuple(normalize_name(k) for k in keywords.attention)

    @property
    def name(self) -> str:
        return "keywords"

    def matches(self, text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Matched (urgent, attention) phrases — shown to the doctor as the reason."""
        normalized = normalize_name(text)
        return (
            tuple(k for k in self._urgent if k in normalized),
            tuple(k for k in self._attention if k in normalized),
        )

    def classify(self, text: str) -> ModelVerdict:
        start = time.perf_counter()
        urgent, attention = self.matches(text)
        level = TriageOption.URGENT if urgent else TriageOption.ATTENTION if attention else TriageOption.STABLE
        probabilities = {option: float(option is level) for option in TriageOption}
        return ModelVerdict(self.name, "keywords@1", probabilities, (time.perf_counter() - start) * 1000)
