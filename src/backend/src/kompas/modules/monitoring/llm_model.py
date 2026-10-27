"""Local language model for diary text triage (D-034, D-075): option-logit scoring on CPU.

One forward pass per text, no generation: the prompt lists the four options as letters A–D
(stable / attention / urgent / unsure), and the probabilities are a softmax over the logits of those
four letter tokens at the answer position (the JEV-CPU / SemIf "direct" method used in the
benchmark, ``research/text_triage``). Prompt styles, versioned in ``knowledge_data/text_model``:

* ``zero_shot`` — upstream's English system prompt + a JSON message {evidence, criterion, options};
* ``few_shot`` — a Russian system prompt with worked examples as earlier chat turns;
* ``translate_few_shot`` (default, D-075) — the text is first translated RU→EN by a local MarianMT
  model (opus-mt-ru-en, beam 4, ≤ 256 tokens — the benchmark's settings), then scored with the
  English version of the few-shot prompt.

The prompt part shared by every text (system prompt, few-shot examples — ~600 of ~630 tokens) is run
through the model once, on the first text, and its key/value cache kept; each text then runs only its own
~20–30 tokens on top (same probabilities, ~8× less compute). If a prompt's tokens don't start with the
cached prefix, the full prompt is run.

Patient text never leaves the server: weights load from the local Hugging Face cache
(``HF_HUB_OFFLINE=1`` is respected). torch/transformers are imported lazily, so only the worker
process pays for them; the API process never loads the model.
"""

import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Literal

from kompas.modules.monitoring.domain.text_model import ModelVerdict, TriageOption

PromptStyle = Literal["zero_shot", "few_shot", "translate_few_shot"]
PROMPT_FILES: dict[str, str] = {
    "zero_shot": "zero_shot.json",
    "few_shot": "few_shot_v1.json",
    "translate_few_shot": "few_shot_v1_en.json",
}
STYLE_LABEL: dict[str, str] = {
    "zero_shot": "zero-shot",
    "few_shot": "few-shot",
    "translate_few_shot": "RU→EN + few-shot",
}
TRANSLATOR_ID = "Helsinki-NLP/opus-mt-ru-en"
TRANSLATOR_REVISION = "fbd6dc73284f95536648512cc21d57f19191961a"  # the revision the benchmark used
LETTERS = "ABCD"
OPTION_ORDER = (TriageOption.STABLE, TriageOption.ATTENTION, TriageOption.URGENT, TriageOption.UNSURE)
ZERO_SHOT_SYSTEM = (
    "Apply the supplied criterion to the supplied evidence. Choose exactly one listed option. "
    "Respond with only its uppercase letter, with no explanation or reasoning."
)
MAX_TEXT_CHARS = 4000  # diary texts are short; very long input is truncated, never rejected


class LocalLLMModel:
    """A ``TextTriageModel`` backed by a local causal LM (e.g. Qwen3-4B), optionally with a local
    translator in front. Thread-safe; loads on first use (or explicitly with ``load``)."""

    def __init__(
        self,
        model_id: str,
        *,
        revision: str | None,
        prompt_style: PromptStyle,
        prompt_dir: Path,
        threads: int = 8,
        translator_id: str = TRANSLATOR_ID,
        translator_revision: str | None = TRANSLATOR_REVISION,
    ) -> None:
        self._model_id = model_id
        self._revision = revision
        self._style = prompt_style
        self._threads = threads
        self._translator_id = translator_id
        self._translator_revision = translator_revision
        raw = (prompt_dir / PROMPT_FILES[prompt_style]).read_text(encoding="utf-8")
        self._prompt: dict[str, Any] = json.loads(raw)
        if prompt_style == "zero_shot":
            ids = [o["id"] for o in self._prompt["options"]]
            if ids != [o.value for o in OPTION_ORDER]:
                raise ValueError(f"zero_shot.json options must be {[o.value for o in OPTION_ORDER]}, got {ids}")
        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        short = model_id.rsplit("/", 1)[-1]
        self._version = f"{short}@{(revision or 'main')[:8]}:{prompt_style}:{digest}"
        if prompt_style == "translate_few_shot":
            self._version += f"+opus-mt@{(translator_revision or 'main')[:8]}"
        self._lock = threading.Lock()
        self._model: Any = None
        self._tokenizer: Any = None
        self._slots: list[int] = []
        self._prefix_ids: list[int] = []
        self._prefix_cache: Any = None
        self._mt_model: Any = None
        self._mt_tokenizer: Any = None

    @property
    def name(self) -> str:
        return f"{self._model_id.rsplit('/', 1)[-1]} ({STYLE_LABEL[self._style]})"

    @property
    def version(self) -> str:
        return self._version

    def load(self) -> None:
        """Load weights and tokenizers (idempotent). Raises if a checkpoint is missing or incomplete."""
        with self._lock:
            if self._model is not None:
                return
            import torch
            import transformers

            # more threads than usable CPUs makes OpenMP spin against itself (e.g. 8 on a 6-vCPU VM)
            torch.set_num_threads(max(1, min(self._threads, len(os.sched_getaffinity(0)))))
            common: dict[str, Any] = {"trust_remote_code": False}
            if self._revision:
                common["revision"] = self._revision
            tokenizer = transformers.AutoTokenizer.from_pretrained(self._model_id, **common)
            loaded: Any = transformers.AutoModelForCausalLM.from_pretrained(
                self._model_id, dtype=torch.float32, low_cpu_mem_usage=True, output_loading_info=True, **common
            )
            model, info = loaded
            if any(info.get(k) for k in ("missing_keys", "mismatched_keys", "error_msgs")):
                raise RuntimeError(f"checkpoint {self._model_id} did not load completely: {info}")
            model.eval()
            slots = []
            for letter in LETTERS:
                encoded = tokenizer.encode(letter, add_special_tokens=False)
                if len(encoded) != 1 or tokenizer.decode(encoded) != letter:
                    raise ValueError(f"answer letter {letter!r} is not a single token for {self._model_id}")
                slots.append(encoded[0])
            if self._style == "translate_few_shot":
                mt: dict[str, Any] = {"revision": self._translator_revision} if self._translator_revision else {}
                self._mt_tokenizer = transformers.MarianTokenizer.from_pretrained(self._translator_id, **mt)
                mt_model: Any = transformers.MarianMTModel.from_pretrained(self._translator_id, **mt)
                self._mt_model = mt_model.eval()
            self._model, self._tokenizer, self._slots = model, tokenizer, slots

    def _ensure_prefix(self) -> None:
        """Run the shared prefix once (call with the lock held). Done on the first text, not in
        ``load``: on a slow CPU it takes minutes, and the worker loads the model before its loop."""
        if self._prefix_cache is not None or self._prefix_ids:
            return
        import torch

        self._prefix_ids = self._shared_prefix()
        if self._prefix_ids:
            self._prefix_cache = self._model(
                input_ids=torch.tensor([self._prefix_ids], dtype=torch.long), use_cache=True
            ).past_key_values

    def _prompt_ids(self, body: str) -> list[int]:
        prompt = self._tokenizer.apply_chat_template(
            self._messages(body), tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        return list(self._tokenizer.encode(prompt, add_special_tokens=False))

    def _shared_prefix(self) -> list[int]:
        """Tokens every prompt starts with: the common prefix of two prompts with different texts, cut
        just after its last special token (special tokens are split off before BPE, so the text can't
        merge across that boundary)."""
        a, b = self._prompt_ids("a"), self._prompt_ids("Zz 1")
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        special = set(self._tokenizer.all_special_ids)
        while n > 0 and a[n - 1] not in special:
            n -= 1
        return a[:n]

    def translate(self, text: str) -> str:
        """RU→EN with the local MarianMT model (benchmark settings: beam 4, at most 256 tokens)."""
        self.load()
        import torch

        with self._lock, torch.inference_mode():
            batch = self._mt_tokenizer([text], return_tensors="pt", truncation=True, max_length=256)
            out = self._mt_model.generate(**batch, max_new_tokens=256, num_beams=4)
        return str(self._mt_tokenizer.decode(out[0], skip_special_tokens=True))

    def _messages(self, text: str) -> list[dict[str, str]]:
        if self._style in ("few_shot", "translate_few_shot"):
            p = self._prompt
            messages = [{"role": "system", "content": p["system"]}]
            for example, letter in p["examples"]:
                messages.append({"role": "user", "content": p["user_template"].format(text=example)})
                messages.append({"role": "assistant", "content": letter})
            messages.append({"role": "user", "content": p["user_template"].format(text=text)})
            return messages
        payload = {
            "evidence": text,
            "criterion": self._prompt["instruction"],
            "options": [
                {"letter": LETTERS[i], "description": f"{o['label']} — {o['description']}"}
                for i, o in enumerate(self._prompt["options"])
            ],
        }
        return [
            {"role": "system", "content": ZERO_SHOT_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    def classify(self, text: str) -> ModelVerdict:
        self.load()
        import torch

        start = time.perf_counter()
        body = text.strip()[:MAX_TEXT_CHARS] or "(пусто)"
        if self._style == "translate_few_shot":
            body = self.translate(body)
        ids = self._prompt_ids(body)
        with self._lock, torch.inference_mode():
            self._ensure_prefix()
            n = len(self._prefix_ids)
            if self._prefix_cache is not None and len(ids) > n and ids[:n] == self._prefix_ids:
                try:
                    logits = self._model(
                        input_ids=torch.tensor([ids[n:]], dtype=torch.long),
                        attention_mask=torch.ones((1, len(ids)), dtype=torch.long),
                        past_key_values=self._prefix_cache,
                        use_cache=True,
                        logits_to_keep=1,
                    ).logits[0, -1, :]
                finally:  # back to the shared prefix for the next text (negative = tokens to drop)
                    added = self._prefix_cache.get_seq_length() - n
                    if added > 0:
                        self._prefix_cache.crop(-added)
            else:
                logits = self._model(
                    input_ids=torch.tensor([ids], dtype=torch.long),
                    attention_mask=torch.ones((1, len(ids)), dtype=torch.long),
                    use_cache=False,
                    logits_to_keep=1,
                ).logits[0, -1, :]
        selected = [float(logits[s]) for s in self._slots]
        top = max(selected)
        weights = [math.exp(v - top) for v in selected]
        total = sum(weights)
        probabilities = {option: w / total for option, w in zip(OPTION_ORDER, weights, strict=True)}
        return ModelVerdict(self.name, self._version, probabilities, (time.perf_counter() - start) * 1000)
