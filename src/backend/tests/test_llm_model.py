"""The production text model reproduces the benchmark (research/text_triage) exactly.

Uses Qwen3-0.6B (small) from the local Hugging Face cache; skipped when it isn't downloaded.
Reference probabilities: research/text_triage/results/jevcpu-qwen3-0.6b.jsonl (same revision, prompt).
"""

import os
from pathlib import Path

import pytest

from kompas.modules.monitoring.domain.text_model import TriageOption

MODEL, REVISION = "Qwen/Qwen3-0.6B", "c1899de289a04d12100db370d81485cdf75e47ca"
PROMPTS = Path(__file__).resolve().parents[1] / "knowledge_data" / "text_model"
# id 17 of the test set (a news text + "у меня таких мыслей нет") and its benchmark probabilities
TEXT_17_PROBS = {"stable": 0.0, "attention": 0.202, "urgent": 0.797, "unsure": 0.0}


def _cached() -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    return isinstance(try_to_load_from_cache(MODEL, "config.json", revision=REVISION), str)


@pytest.mark.skipif(not _cached(), reason="Qwen3-0.6B not in the local Hugging Face cache")
def test_matches_benchmark_probabilities() -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from kompas.modules.monitoring.llm_model import LocalLLMModel

    model = LocalLLMModel(MODEL, revision=REVISION, prompt_style="zero_shot", prompt_dir=PROMPTS, threads=4)
    testset = Path(__file__).resolve().parents[3] / "research" / "text_triage" / "testset.jsonl"
    import json

    text = next(json.loads(line)["text"] for line in testset.read_text().splitlines() if json.loads(line)["id"] == 17)
    verdict = model.classify(text)
    assert abs(sum(verdict.probabilities.values()) - 1) < 1e-6
    for option, expected in TEXT_17_PROBS.items():
        assert verdict.probabilities[TriageOption(option)] == pytest.approx(expected, abs=0.002)
    assert verdict.model_version.startswith("Qwen3-0.6B@c1899de2:zero_shot:")


def test_prompt_files_are_valid() -> None:
    from kompas.modules.monitoring.llm_model import LocalLLMModel

    for style in ("zero_shot", "few_shot", "translate_few_shot"):
        model = LocalLLMModel(MODEL, revision=REVISION, prompt_style=style, prompt_dir=PROMPTS)  # no weights loaded
        messages = model._messages("тест")
        assert messages[0]["role"] == "system" and "тест" in messages[-1]["content"]


@pytest.mark.skipif(not _cached(), reason="Qwen3-0.6B not in the local Hugging Face cache")
def test_translate_then_classify_matches_benchmark() -> None:
    """Same probabilities as research/text_triage/results/translate-qwen3-0.6b.jsonl for test-set id 67."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import json

    from kompas.modules.monitoring.llm_model import LocalLLMModel

    model = LocalLLMModel(MODEL, revision=REVISION, prompt_style="translate_few_shot", prompt_dir=PROMPTS, threads=4)
    research = Path(__file__).resolve().parents[3] / "research" / "text_triage"
    reference = next(
        json.loads(line)["probs"]
        for line in (research / "results" / "translate-qwen3-0.6b.jsonl").read_text().splitlines()
        if json.loads(line)["id"] == 67
    )
    assert model.translate("Не хочу больше жить.") == "I don't want to live anymore."
    verdict = model.classify("Не хочу больше жить.")
    for option, expected in reference.items():
        assert verdict.probabilities[TriageOption(option)] == pytest.approx(expected, abs=0.002)
    assert "+opus-mt@fbd6dc73" in verdict.model_version


@pytest.mark.skipif(not _cached(), reason="Qwen3-0.6B not in the local Hugging Face cache")
@pytest.mark.parametrize("style", ["zero_shot", "few_shot", "translate_few_shot"])
def test_prefix_cache_gives_same_probabilities(style: str) -> None:
    """The shared prompt prefix is run once; texts scored on top of it match a full run, and the
    cache is back to the prefix after each text."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from kompas.modules.monitoring.llm_model import LocalLLMModel

    model = LocalLLMModel(MODEL, revision=REVISION, prompt_style=style, prompt_dir=PROMPTS, threads=4)  # type: ignore[arg-type]
    model.load()
    assert model._prefix_cache is None  # built on the first text, not at load
    texts = ["Не хочу больше жить.", '"Всё хорошо", — сказала я врачу.', ""]
    cached = [model.classify(t).probabilities for t in texts]
    assert len(model._prefix_ids) > 20
    assert model._prefix_cache.get_seq_length() == len(model._prefix_ids)
    model._prefix_cache = None
    for text, probs in zip(texts, cached, strict=True):
        full = model.classify(text).probabilities
        for option in TriageOption:
            assert probs[option] == pytest.approx(full[option], abs=1e-3)
