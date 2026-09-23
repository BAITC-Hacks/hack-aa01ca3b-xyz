"""Local Whisper speech recognition with per-turn language routing.

* Kazakh and mixed Kazakh-Russian speech (шала-казахский) →
  ``abilmansplus/whisper-turbo-kaz-rus-v1``: a LoRA adapter over
  ``abilmansplus/whisper-turbo-ksc2`` (Whisper large-v3-turbo fine-tuned on the
  ISSAI Kazakh Speech Corpus 2 and the Russian Golos corpus).
* Russian speech → ``openai/whisper-large-v3-turbo`` (lower Russian WER, punctuation).

Each diarized speaker turn is recognized separately, so every line of the
transcript keeps its speaker, timestamps and language tag. All weights are read
from the local Hugging Face cache; audio never leaves the machine.
"""

from __future__ import annotations

import gc
import math
import os
import re
from functools import lru_cache
from typing import Any, Callable

import numpy as np


SAMPLE_RATE = 16_000
BASE_MODEL = os.getenv("ASR_BASE_MODEL", "openai/whisper-large-v3-turbo")
KZ_BASE_MODEL = os.getenv("ASR_KZ_BASE_MODEL", "abilmansplus/whisper-turbo-ksc2")
KZ_ADAPTER = os.getenv("ASR_KZ_ADAPTER", "abilmansplus/whisper-turbo-kaz-rus-v1")


def _total_ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (AttributeError, ValueError, OSError):
        return 16.0


# auto: both models in memory, ru → base model, kk/mix → kaz-rus (≥ 12 GB RAM)
# two-pass: kaz-rus for every line, unload it, then the base model re-recognizes pure Russian lines (< 12 GB RAM)
# kz-only: everything → kaz-rus (fastest, no punctuation)
ROUTING = os.getenv("ASR_ROUTING") or ("auto" if _total_ram_gb() >= 12 else "two-pass")
MAX_CHUNK_S = 20.0
MIN_CHUNK_S = 0.35

KAZAKH_LETTERS = set("әғқңөұүһі")
KAZAKH_WORDS = {"бар", "жоқ", "керек", "бойынша", "және", "мен", "бұл", "ол", "біз", "бізде", "сіз", "сізге", "үшін",
                "бүгін", "рахмет", "жарайды", "ме", "ма", "қалай", "апта", "екі", "үш", "сөз", "келісемін", "менің"}
RUSSIAN_WORDS = {"и", "в", "на", "по", "что", "это", "до", "к", "с", "не", "для", "а", "но", "так", "из", "за", "от",
                 "мы", "вы", "нужно", "надо", "хорошо", "пусть", "сразу", "уже", "все", "как", "или", "же", "бы"}

ProgressFn = Callable[[float, str], None]


def device() -> str:
    forced = os.getenv("ASR_DEVICE")
    if forced:
        return forced
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _dtype():
    import torch

    return torch.float16 if device() in {"cuda", "mps"} else torch.float32


def _cached(repo_id: str, filename: str) -> bool:
    from huggingface_hub import try_to_load_from_cache

    return isinstance(try_to_load_from_cache(repo_id, filename), str)


def kz_available() -> bool:
    return (
        _cached(KZ_BASE_MODEL, "model.safetensors")
        and _cached(KZ_ADAPTER, "adapter_model.safetensors")
        and _cached(BASE_MODEL, "preprocessor_config.json")
    )


def base_available() -> bool:
    return _cached(BASE_MODEL, "model.safetensors") and _cached(BASE_MODEL, "preprocessor_config.json")


@lru_cache(maxsize=1)
def _processor():
    from transformers import WhisperProcessor

    return WhisperProcessor.from_pretrained(BASE_MODEL)


@lru_cache(maxsize=1)
def _kz_model():
    from peft import PeftModel
    from transformers import WhisperForConditionalGeneration

    base = WhisperForConditionalGeneration.from_pretrained(KZ_BASE_MODEL, dtype=_dtype())
    model = PeftModel.from_pretrained(base, KZ_ADAPTER).merge_and_unload()
    return model.to(device()).eval()


@lru_cache(maxsize=1)
def _base_model():
    from transformers import WhisperForConditionalGeneration

    model = WhisperForConditionalGeneration.from_pretrained(BASE_MODEL, dtype=_dtype())
    return model.to(device()).eval()


def release_models(kz: bool = True, base: bool = True) -> None:
    """Free ASR weights before the next model or the local LLM starts (matters on 8 GB laptops)."""
    if kz:
        _kz_model.cache_clear()
    if base:
        _base_model.cache_clear()
    gc.collect()
    import torch

    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _features(chunk: np.ndarray):
    inputs = _processor()(chunk, sampling_rate=SAMPLE_RATE, return_tensors="pt")
    return inputs.input_features.to(device(), dtype=_dtype())


def _russian_probability(model: Any, features: Any) -> float:
    """P(ru) among Kazakh and Russian from a Whisper model's language-ID head."""
    import torch

    lang_to_id = model.generation_config.lang_to_id
    ids = [lang_to_id["<|ru|>"], lang_to_id["<|kk|>"]]
    start = torch.tensor([[model.generation_config.decoder_start_token_id]], device=features.device)
    with torch.no_grad():
        logits = model(input_features=features, decoder_input_ids=start).logits[0, -1]
    probs = torch.softmax(logits[ids].float(), dim=-1)
    return float(probs[0])


def _generate(model: Any, features: Any, language: str | None) -> str:
    import torch

    with torch.no_grad():
        ids = model.generate(
            features,
            language=language,
            task="transcribe",
            num_beams=int(os.getenv("ASR_BEAMS", "1")),
        )
    return _processor().batch_decode(ids, skip_special_tokens=True)[0].strip()


def language_tag(text: str, detected: str) -> str:
    """ru / kk / mix label for a recognized line (shown in the transcript)."""
    words = re.findall(r"\w+", text.lower())
    has_kk = any(KAZAKH_LETTERS & set(word) or word in KAZAKH_WORDS for word in words)
    has_ru = any(word in RUSSIAN_WORDS for word in words)
    if has_kk and has_ru:
        return "mix"
    if has_kk:
        return "kk"
    if has_ru:
        return "ru"
    return detected if detected in {"ru", "kk"} else "kk"


def prepare_turns(turns: list[tuple[float, float, str]], gap: float = 0.8) -> list[tuple[float, float, str]]:
    """Merge adjacent turns of one speaker and split long monologues into ≤20 s windows."""
    merged: list[tuple[float, float, str]] = []
    for start, end, speaker in sorted(turns):
        if merged and merged[-1][2] == speaker and start - merged[-1][1] <= gap and end - merged[-1][0] <= MAX_CHUNK_S:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]), speaker)
        else:
            merged.append((start, end, speaker))
    windows: list[tuple[float, float, str]] = []
    for start, end, speaker in merged:
        pieces = max(1, math.ceil((end - start) / MAX_CHUNK_S))
        step = (end - start) / pieces
        windows.extend((start + i * step, start + (i + 1) * step, speaker) for i in range(pieces))
    return [window for window in windows if window[1] - window[0] >= MIN_CHUNK_S]


def transcribe_turns(waveform: np.ndarray, turns: list[tuple[float, float, str]], progress: ProgressFn | None = None) -> list[dict[str, Any]]:
    """Recognize every speaker turn with the model that fits its language."""
    use_kz = kz_available()
    use_base = base_available() and (ROUTING == "auto" or not use_kz)
    if not use_kz and not use_base:
        raise FileNotFoundError("Не найдены локальные модели распознавания речи. Выполните `python scripts/download_models.py`.")

    if ROUTING == "two-pass" and use_kz:
        return _two_pass(waveform, prepare_turns(turns), base_available(), progress)
    threshold = float(os.getenv("ASR_RU_THRESHOLD", "0.8"))
    windows = prepare_turns(turns)
    segments: list[dict[str, Any]] = []
    for index, (start, end, speaker) in enumerate(windows):
        chunk = waveform[int(start * SAMPLE_RATE): int(end * SAMPLE_RATE)]
        features = _features(chunk)
        p_russian = _russian_probability(_base_model(), features) if use_base else 0.0
        text, engine, detected = "", "", ""
        if use_base and (p_russian >= threshold or not use_kz):
            text, engine, detected = _generate(_base_model(), features, "ru"), "whisper-large-v3-turbo", "ru"
            if use_kz and language_tag(text, "ru") != "ru":
                text = ""  # Kazakh words in a "Russian" line: re-recognize with the Kazakh model
        if not text:
            kz_language = "ru" if _russian_probability(_kz_model(), features) >= 0.5 else "kk"
            text, engine, detected = _generate(_kz_model(), features, kz_language), "whisper-turbo-kaz-rus", kz_language
        if text:
            segments.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "speaker": speaker,
                "text": text,
                "lang": language_tag(text, detected),
                "p_ru": round(p_russian, 2),
                "asr_model": engine,
            })
        if progress:
            progress((index + 1) / len(windows), f"Распознано реплик: {index + 1} из {len(windows)}")
    return segments


def _two_pass(waveform: np.ndarray, windows: list[tuple[float, float, str]], use_base: bool, progress: ProgressFn | None) -> list[dict[str, Any]]:
    """Low-memory routing: only one Whisper model is loaded at a time."""
    segments: list[dict[str, Any]] = []
    share = 0.6 if use_base else 1.0
    for index, (start, end, speaker) in enumerate(windows):
        features = _features(waveform[int(start * SAMPLE_RATE): int(end * SAMPLE_RATE)])
        language = "ru" if _russian_probability(_kz_model(), features) >= 0.5 else "kk"
        text = _generate(_kz_model(), features, language)
        if text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "speaker": speaker, "text": text,
                             "lang": language_tag(text, language), "asr_model": "whisper-turbo-kaz-rus"})
        if progress:
            progress(share * (index + 1) / len(windows), f"Распознано реплик: {index + 1} из {len(windows)}")
    if not use_base:
        return segments
    release_models(kz=True, base=False)
    russian = [segment for segment in segments if segment["lang"] == "ru"]
    for index, segment in enumerate(russian):
        chunk = waveform[int(segment["start"] * SAMPLE_RATE): int(segment["end"] * SAMPLE_RATE)]
        text = _generate(_base_model(), _features(chunk), "ru")
        if text and language_tag(text, "ru") == "ru":
            segment.update(text=text, asr_model="whisper-large-v3-turbo")
        if progress:
            progress(0.6 + 0.4 * (index + 1) / len(russian), f"Уточняем русские реплики: {index + 1} из {len(russian)}")
    return segments


def transcribe_phrase(samples: np.ndarray) -> tuple[str, str]:
    """Recognize one live phrase (fast path for live captions): Kazakh/Russian model with its own kk/ru choice."""
    if len(samples) < MIN_CHUNK_S * SAMPLE_RATE:
        return "", ""
    step = int(MAX_CHUNK_S * SAMPLE_RATE)
    texts, language = [], "ru"
    for offset in range(0, len(samples), step):
        features = _features(samples[offset: offset + step])
        if kz_available():
            language = "ru" if _russian_probability(_kz_model(), features) >= 0.5 else "kk"
            texts.append(_generate(_kz_model(), features, language))
        else:
            texts.append(_generate(_base_model(), features, "ru"))
    text = " ".join(part for part in texts if part).strip()
    return text, language_tag(text, language)
