"""Offline speech-to-text, speaker diarization, and local-LLM minutes."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio


ROOT = Path(__file__).resolve().parents[1]
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_CACHE = Path(os.getenv("WHISPER_CACHE", ROOT / "models" / "whisper-cache"))
DIARIZATION_PATH = Path(
    os.getenv(
        "DIARIZATION_MODEL_PATH",
        ROOT / "models" / "pyannote-speaker-diarization-community-1",
    )
)

# Pyannote's optional usage telemetry is disabled before it is imported.
os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"


@lru_cache(maxsize=1)
def _whisper_model() -> WhisperModel:
    if not WHISPER_CACHE.exists():
        raise FileNotFoundError(
            f"Не найдена локальная модель Whisper в {WHISPER_CACHE}. "
            "Сначала выполните `python scripts/download_models.py`."
        )
    return WhisperModel(WHISPER_MODEL, device="auto", compute_type="int8", download_root=str(WHISPER_CACHE), local_files_only=True)


@lru_cache(maxsize=1)
def _diarization_pipeline() -> Any:
    if not DIARIZATION_PATH.exists():
        raise FileNotFoundError(
            f"Не найдена локальная модель диаризации: {DIARIZATION_PATH}. "
            "Сначала выполните `python scripts/download_models.py`."
        )
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(str(DIARIZATION_PATH))
    if pipeline is None:
        raise RuntimeError("pyannote не смог загрузить локальную модель диаризации.")
    return pipeline


def transcribe(audio_path: str | Path) -> tuple[list[dict[str, Any]], str, float]:
    """Transcribe audio locally and return timestamped segments, language, duration."""
    segments_iter, info = _whisper_model().transcribe(
        str(audio_path),
        language=None,
        beam_size=5,
        vad_filter=True,
        word_timestamps=False,
        condition_on_previous_text=True,
    )
    segments = [
        {"start": float(segment.start), "end": float(segment.end), "text": segment.text.strip()}
        for segment in segments_iter
        if segment.text.strip()
    ]
    return segments, str(info.language or "unknown"), float(info.duration or 0.0)


def diarize(audio_path: str | Path, segments: list[dict[str, Any]], expected_speakers: int | None = None) -> list[dict[str, Any]]:
    """Assign local speaker turns to transcript segments by maximum time overlap."""
    import numpy as np
    import torch

    waveform = decode_audio(str(audio_path), sampling_rate=16_000)
    pipeline = _diarization_pipeline()
    audio = {"waveform": torch.from_numpy(np.asarray(waveform, dtype=np.float32)).unsqueeze(0), "sample_rate": 16_000}
    output = pipeline(audio, num_speakers=expected_speakers) if expected_speakers else pipeline(audio)
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = output.speaker_diarization

    turns: list[tuple[float, float, str]] = []
    for interval, _, label in annotation.itertracks(yield_label=True):
        turns.append((float(interval.start), float(interval.end), str(label)))
    label_order = list(dict.fromkeys(label for _, _, label in sorted(turns)))
    aliases = {label: f"SPEAKER_{index + 1:02d}" for index, label in enumerate(label_order)}

    aligned: list[dict[str, Any]] = []
    for segment in segments:
        start, end = float(segment["start"]), float(segment["end"])
        overlaps = [
            (max(0.0, min(end, turn_end) - max(start, turn_start)), label)
            for turn_start, turn_end, label in turns
        ]
        overlap, label = max(overlaps, default=(0.0, ""))
        assigned = aliases.get(label, "Спикер не определён") if overlap > 0 else "Спикер не определён"
        aligned.append({**segment, "speaker": assigned})
    return aligned


def _ollama_url() -> str:
    base = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urllib.parse.urlparse(base)
    host = parsed.hostname or ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if parsed.scheme != "http" or not is_loopback:
        raise ValueError("OLLAMA_URL должен указывать на локальный адрес, например http://127.0.0.1:11434")
    return base


def _local_llm_analysis(segments: list[dict[str, Any]], meeting_date: str) -> dict[str, Any]:
    transcript = "\n".join(
        f"[{_stamp(item['start'])}-{_stamp(item['end'])}] [{item.get('speaker', 'Спикер не определён')}] {item['text']}"
        for item in segments
    )
    if len(transcript) > 32_000:
        transcript = transcript[:32_000] + "\n[Транскрипт обрезан для локального анализа]"
    prompt = f"""Проанализируй протокол совещания. Вход может быть на русском, казахском или на их смеси. Не добавляй факты, которых нет в тексте. Не считай обычное обсуждение поручением. Если ответственный или срок не названы, укажи «Не определён» или «Не указан». Поле speaker должно совпадать с меткой спикера в исходной реплике, если её можно определить. Дата совещания: {meeting_date}.

Верни только JSON-объект формата:
{{"summary":"краткое саммари на русском языке","actions":[{{"task":"конкретное поручение","assignee":"имя или Не определён","speaker":"SPEAKER_01 или Спикер не определён","deadline":"срок словами или Не указан","deadline_iso":"YYYY-MM-DD если дата точна, иначе пустая строка","status":"В работе","source_quote":"короткая точная цитата"}}]}}

Транскрипт:
{transcript}
"""
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
        "messages": [{"role": "user", "content": prompt}],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }
    request = urllib.request.Request(
        f"{_ollama_url()}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body.get("message", {}).get("content", "")
    result = json.loads(content)
    if not isinstance(result, dict) or not isinstance(result.get("actions", []), list):
        raise ValueError("Локальная модель вернула неожиданный формат результата.")
    result.setdefault("summary", "Саммари не сформировано.")
    valid_actions = [item for item in result.get("actions", []) if isinstance(item, dict)]
    result["actions"] = valid_actions
    for action in valid_actions:
        for field, default in (("task", ""), ("assignee", "Не определён"), ("speaker", "Спикер не определён"), ("deadline", "Не указан"), ("deadline_iso", ""), ("status", "В работе"), ("source_quote", "")):
            action[field] = str(action.get(field) or default)
        if action["status"] not in {"В работе", "Просрочено", "Выполнено"}:
            action["status"] = "В работе"
    return result


_TASK_MARKERS = re.compile(
    r"\b(поручаю|поручение|нужно|надо|необходимо|должен|должна|подготовить|сделать|обеспечить|тапсырамын|тапсырма|керек|орындау|дайындау)\b",
    re.IGNORECASE,
)


def _heuristic_analysis(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Fallback when Ollama is unavailable; results are visibly marked for review."""
    texts = [item["text"].strip() for item in segments if item.get("text", "").strip()]
    summary = " ".join(texts[:3])[:900] or "Транскрипт не содержит распознанной речи."
    actions: list[dict[str, str]] = []
    for item in segments:
        text = item.get("text", "").strip()
        if not text or not _TASK_MARKERS.search(text):
            continue
        due_match = re.search(r"\b(?:до|к|дейін|мерзімі|сроком)\s+([^,.!?;]{1,50})", text, re.IGNORECASE)
        iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        actions.append(
            {
                "task": text,
                "assignee": "Не определён",
                "speaker": str(item.get("speaker", "Спикер не определён")),
                "deadline": due_match.group(1).strip() if due_match else "Не указан",
                "deadline_iso": iso_match.group(1) if iso_match else "",
                "status": "В работе",
                "source_quote": text,
            }
        )
        return {"summary": summary, "actions": actions}


def analyze_meeting(segments: list[dict[str, Any]], meeting_date: str) -> tuple[dict[str, Any], str, str | None]:
    """Use local Ollama; fall back to transparent keyword extraction if it is offline."""
    if not segments:
        return {"summary": "Речь не распознана.", "actions": []}, "fallback", "В записи не найден распознанный текст."
    try:
        return _local_llm_analysis(segments, meeting_date), "ollama", None
    except (OSError, TimeoutError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return _heuristic_analysis(segments), "fallback", f"Локальный Ollama недоступен; включено упрощённое извлечение. Проверьте поручения вручную. ({exc})"


def _stamp(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"
