"""Offline speech-to-text, speaker diarization, and local-LLM minutes."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from app.core.config import MODELS_DIR
from . import deadlines

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_CACHE = Path(os.getenv("WHISPER_CACHE", MODELS_DIR / "whisper-cache"))
DIARIZATION_PATH = Path(
    os.getenv(
        "DIARIZATION_MODEL_PATH",
        MODELS_DIR / "pyannote-speaker-diarization-community-1",
    )
)
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")

# Pyannote's optional usage telemetry is disabled before it is imported.
os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

ProgressFn = Callable[[float, str], None]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _notify(progress: ProgressFn | None, share: float, text: str) -> None:
    if progress:
        progress(min(max(share, 0.0), 1.0), text)


# --------------------------------------------------------------------------- speech


def transcribe_meeting(
    audio_path: str | Path, expected_speakers: int | None = None, progress: ProgressFn | None = None
) -> tuple[list[dict[str, Any]], str, float]:
    """Diarize first, then recognize each speaker turn with the local model that fits its language.

    Returns segments ``{id, start, end, speaker, text, lang, asr_model}``, a language summary
    (e.g. ``kk+mix+ru``) and the duration in seconds. Falls back to the faster-whisper
    pipeline when the Kazakh/Russian Whisper models are not downloaded.
    """
    from faster_whisper.audio import decode_audio

    from . import stt as asr

    if not asr.kz_available() and not asr.base_available():
        _notify(progress, 0.1, "Модели kaz-rus не найдены — используем faster-whisper.")
        segments, language, duration = transcribe(audio_path)
        return diarize(audio_path, segments, expected_speakers), language, duration

    waveform = decode_audio(str(audio_path), sampling_rate=16_000)
    duration = len(waveform) / 16_000
    _notify(progress, 0.05, "Определяем, кто и когда говорил (диаризация)…")
    turns = _speaker_turns(waveform, expected_speakers)
    speakers_found = len({label for _, _, label in turns})
    _notify(progress, 0.15, f"Найдено голосов: {speakers_found}. Распознаём речь по репликам…")
    segments = asr.transcribe_turns(
        waveform, turns, progress=lambda share, text: _notify(progress, 0.15 + 0.8 * share, text)
    )
    if os.getenv("LOW_MEMORY", "1") == "1":
        asr.release_models()

    order = list(dict.fromkeys(segment["speaker"] for segment in segments))
    aliases = {label: f"SPEAKER_{index + 1:02d}" for index, label in enumerate(order)}
    for index, segment in enumerate(segments, start=1):
        segment["speaker"] = aliases[segment["speaker"]]
        segment["id"] = index
    languages = sorted({segment["lang"] for segment in segments})
    return segments, "+".join(languages) or "unknown", duration


def _speaker_turns(waveform: Any, expected_speakers: int | None) -> list[tuple[float, float, str]]:
    from . import diarize as diarization_sherpa

    backend = os.getenv("DIARIZATION_BACKEND", "sherpa")
    if backend == "pyannote" and DIARIZATION_PATH.exists():
        return _pyannote_turns(waveform, expected_speakers)
    if diarization_sherpa.available():
        return diarization_sherpa.diarize_waveform(waveform, expected_speakers)
    if DIARIZATION_PATH.exists():
        return _pyannote_turns(waveform, expected_speakers)
    return [(0.0, len(waveform) / 16_000, "spk0")]


@lru_cache(maxsize=1)
def _whisper_model() -> Any:
    from faster_whisper import WhisperModel

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


def _pyannote_turns(waveform: Any, expected_speakers: int | None) -> list[tuple[float, float, str]]:
    import numpy as np
    import torch

    audio = {"waveform": torch.from_numpy(np.asarray(waveform, dtype=np.float32)).unsqueeze(0), "sample_rate": 16_000}
    pipeline = _diarization_pipeline()
    output = pipeline(audio, num_speakers=expected_speakers) if expected_speakers else pipeline(audio)
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", output)
    return [(float(interval.start), float(interval.end), str(label)) for interval, _, label in annotation.itertracks(yield_label=True)]


def transcribe(audio_path: str | Path) -> tuple[list[dict[str, Any]], str, float]:
    """Legacy path: transcribe the whole file with faster-whisper."""
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
    """Legacy path: assign speaker turns to transcript segments by maximum time overlap."""
    from faster_whisper.audio import decode_audio

    waveform = decode_audio(str(audio_path), sampling_rate=16_000)
    turns = _speaker_turns(waveform, expected_speakers)
    label_order = list(dict.fromkeys(label for _, _, label in sorted(turns)))
    aliases = {label: f"SPEAKER_{index + 1:02d}" for index, label in enumerate(label_order)}

    aligned: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        start, end = float(segment["start"]), float(segment["end"])
        overlaps = [
            (max(0.0, min(end, turn_end) - max(start, turn_start)), label)
            for turn_start, turn_end, label in turns
        ]
        overlap, label = max(overlaps, default=(0.0, ""))
        assigned = aliases.get(label, "Спикер не определён") if overlap > 0 else "Спикер не определён"
        aligned.append({**segment, "id": index, "speaker": assigned, "lang": segment.get("lang", "")})
    return aligned


# --------------------------------------------------------------------------- local LLM agent


def _ollama_url() -> str:
    base = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urllib.parse.urlparse(base)
    host = parsed.hostname or ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    docker_ollama = (
        os.getenv("ALLOW_DOCKER_OLLAMA") == "1"
        and host.lower() == "ollama"
        and parsed.port == 11434
        and parsed.username is None
    )
    if parsed.scheme != "http" or not (is_loopback or docker_ollama):
        raise ValueError("OLLAMA_URL должен указывать на локальный адрес, например http://127.0.0.1:11434")
    return base


def _ollama_json(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Call the local LLM with a JSON schema; retry once with a larger context if the JSON comes back cut."""
    try:
        return _ollama_json_once(prompt, schema, _context_size(prompt))
    except json.JSONDecodeError:
        return _ollama_json_once(prompt, schema, 16384)


def _context_size(prompt: str) -> int:
    """Enough context for the prompt (≈2.5 characters per token for Cyrillic) plus the answer."""
    needed = int(len(prompt) / 2.5) + 3072
    return max(int(os.getenv("OLLAMA_NUM_CTX", "8192")), min(16384, ((needed + 1023) // 1024) * 1024))


def _ollama_json_once(prompt: str, schema: dict[str, Any], num_ctx: int) -> dict[str, Any]:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "60s"),
        "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": 3072},
    }
    request = urllib.request.Request(
        f"{_ollama_url()}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=int(os.getenv("OLLAMA_TIMEOUT", "600"))) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body.get("message", {}).get("content", "")
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("Локальная модель вернула неожиданный формат результата.")
    return result


OVERVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "participants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"speaker": {"type": "string"}, "name": {"type": "string"}, "role": {"type": "string"}},
                "required": ["speaker", "name", "role"],
            },
        },
        "summary": {"type": "string"},
        "summary_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"topic": {"type": "string"}, "indicator": {"type": "string"}, "problem": {"type": "string"}},
                "required": ["topic", "indicator", "problem"],
            },
        },
    },
    "required": ["participants", "summary", "summary_items"],
}

ACTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "assignee": {"type": "string"},
                    "assignee_speaker": {"type": "string"},
                    "assigned_by": {"type": "string"},
                    "deadline": {"type": "string"},
                    "deadline_iso": {"type": "string"},
                    "segment_id": {"type": "integer"},
                    "source_quote": {"type": "string"},
                    "priority": {"type": "string", "enum": ["высокий", "средний", "низкий"]},
                },
                "required": ["task", "assignee", "assignee_speaker", "assigned_by", "deadline", "deadline_iso", "segment_id", "source_quote", "priority"],
            },
        },
        "flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["actions", "flags"],
}

ACTION_RULES = """Правила извлечения поручений:
1. Поручение — действие, которое конкретный человек или подразделение должен выполнить после совещания. Обычный доклад, мнение или вопрос — не поручение. Предложение участника становится поручением, только если председатель его принял («Согласен», «Логично», «Хорошо», «Келісемін»). «Это на вас» / «это ваше» после предложения участника = поручение ЭТОМУ участнику выполнить его же предложение — отдельное поручение со своим сроком.
2. Делегирование: «пусть Ерлан подготовит…» — ответственный Ерлан, даже если его нет на совещании.
3. Ответственным может быть подразделение («юридический департамент»).
4. Если срок обсуждали несколько раз («две недели?» — «маловато» — «три недели, к пятнадцатому октября»), бери ПОСЛЕДНИЙ согласованный срок.
5. Если срок не назван — deadline = «Не указан», deadline_iso = "". НИКОГДА не придумывай срок.
6. deadline — ДОСЛОВНАЯ фраза о сроке из речи, как её произнесли («до пятницы», «за две недели», «к пятнадцатому октября», «жұмаға дейін»), а НЕ дата. deadline_iso — дата YYYY-MM-DD по календарю ниже, считая от даты совещания: «до конца недели» и «на этой неделе» = ближайшая пятница, «следующая неделя» = пятница следующей недели, «за N недель» = дата совещания + N×7 дней. Даты дополнительно проверяет программа.
7. В конце совещания председатель часто подводит итоги. Итоги НЕ создают новых поручений: не дублируй уже найденные. Если итоги противоречат обсуждению (другой ответственный или срок), верь конкретной договорённости из обсуждения и добавь запись в flags.
8. Самопоручение («за неделю дам смету») — ответственный сам говорящий.
9. Одна реплика может содержать несколько поручений разным людям или с разными сроками — выпиши каждое отдельно. Связанные действия одного ответственного с общим сроком («подготовить уведомление и обновить шаблон договора») — одно поручение.
10. Речь может быть на казахском или смешанной (шала-казахский). Формулируй task по-русски, кратко, с глагола («Подготовить…», «Провести…»).
11. assignee — имя и, если известно, должность или подразделение. assignee_speaker — метка SPEAKER_XX ответственного, если он говорил на совещании, иначе "".
12. assigned_by — кто дал поручение. segment_id — номер реплики [#N], где поручение дано окончательно. source_quote — короткая дословная цитата из этой реплики.
13. priority: «высокий» — безопасность, штрафы, срыв сроков, срок до 7 дней; «низкий» — справочные задачи без срока; иначе «средний».
14. В flags запиши поручения без срока и без явного ответственного, а также противоречия."""


FEW_SHOT = """Разобранный пример (другое совещание, дата примера 2026-09-23, среда):
[#1 00:00] SPEAKER_01: Коллеги, начнём. Марат Серикович, что по складу?
[#2 00:05] SPEAKER_02 (Марат Серикович): Инвентаризация на восемьдесят процентов, не хватает людей.
[#3 00:12] SPEAKER_01: Пусть Алия из кадров до пятницы найдёт двух временных сотрудников. Инвентаризацию закончите за неделю?
[#4 00:20] SPEAKER_02 (Марат Серикович): За неделю не успеем, нужно две.
[#5 00:24] SPEAKER_01: Хорошо, две недели, но отчёт к девятому октября.
[#6 00:30] SPEAKER_03 (Динара Болатовна): Предлагаю провести аудит поставщиков.
[#7 00:34] SPEAKER_01: Согласен, Динара Болатовна, это на вас, до конца месяца. И подготовьте письмо поставщикам.
Правильный ответ:
{"actions": [
 {"task": "Найти двух временных сотрудников", "assignee": "Алия (отдел кадров)", "assignee_speaker": "", "assigned_by": "председатель", "deadline": "до пятницы", "deadline_iso": "2026-09-25", "segment_id": 3, "source_quote": "Пусть Алия из кадров до пятницы найдёт двух временных сотрудников", "priority": "высокий"},
 {"task": "Завершить инвентаризацию и сдать отчёт", "assignee": "Марат Серикович", "assignee_speaker": "SPEAKER_02", "assigned_by": "председатель", "deadline": "к девятому октября", "deadline_iso": "2026-10-09", "segment_id": 5, "source_quote": "две недели, но отчёт к девятому октября", "priority": "средний"},
 {"task": "Провести аудит поставщиков", "assignee": "Динара Болатовна", "assignee_speaker": "SPEAKER_03", "assigned_by": "председатель", "deadline": "до конца месяца", "deadline_iso": "2026-09-30", "segment_id": 7, "source_quote": "Динара Болатовна, это на вас, до конца месяца", "priority": "средний"},
 {"task": "Подготовить письмо поставщикам", "assignee": "Динара Болатовна", "assignee_speaker": "SPEAKER_03", "assigned_by": "председатель", "deadline": "Не указан", "deadline_iso": "", "segment_id": 7, "source_quote": "И подготовьте письмо поставщикам", "priority": "низкий"}
], "flags": ["Поручение без срока: «Подготовить письмо поставщикам» (Динара Болатовна)."]}
Почему так: Алия не выступала, но поручение делегировано ей; срок инвентаризации — последний согласованный («к девятому октября»), а не «за неделю»; «это на вас» после предложения Динары Болатовны — её поручение; у письма срок не назван."""


def _meeting_calendar(meeting_date: str, days: int = 45) -> str:
    start = date.fromisoformat(meeting_date)
    return "; ".join(
        f"{(start + timedelta(days=offset)).isoformat()} {WEEKDAYS[(start + timedelta(days=offset)).weekday()]}"
        for offset in range(days)
    )


def _transcript_text(segments: list[dict[str, Any]], names: dict[str, str] | None = None) -> str:
    names = names or {}
    lines = []
    for index, item in enumerate(segments, start=1):
        speaker = str(item.get("speaker", "Спикер не определён"))
        label = f"{speaker} ({names[speaker]})" if names.get(speaker) else speaker
        lang = f" ({item['lang']})" if item.get("lang") else ""
        lines.append(f"[#{item.get('id', index)} {_stamp(item['start'])}] {label}{lang}: {item['text']}")
    transcript = "\n".join(lines)
    limit = int(os.getenv("TRANSCRIPT_CHAR_LIMIT", "24000"))
    if len(transcript) > limit:
        transcript = transcript[:limit] + "\n[Транскрипт обрезан для локального анализа]"
    return transcript


def _overview_prompt(transcript: str, meeting_date: str) -> str:
    return f"""Ты — секретарь совещания в казахстанской компании. Ниже транскрипт: [#номер мм:сс] SPEAKER_XX (язык): реплика. Речь на русском, казахском или смешанная.

Задача 1. Определи участников: для каждой метки SPEAKER_XX найди имя (обычно имя-отчество) и должность. Подсказки: к человеку обращаются по имени перед его репликой («Жандос Талгатович, по инвестициям что у нас?» → следующий говорящий — Жандос Талгатович); председатель открывает и закрывает совещание и раздаёт поручения. Если имя нельзя установить из текста — name = "". Никогда не придумывай имена, которых нет в транскрипте.
Задача 2. Кратко перескажи итоги совещания на русском (3–5 предложений) в поле summary.
Задача 3. summary_items — по каждому направлению или докладу: topic (направление и докладчик), indicator (ключевой показатель, цифра), problem (озвученная проблема). Не выдумывай — только то, что сказано.

Дата совещания: {meeting_date}.

Транскрипт:
{transcript}"""


def _actions_prompt(transcript: str, meeting_date: str) -> str:
    weekday = WEEKDAYS[date.fromisoformat(meeting_date).weekday()]
    return f"""Ты — секретарь совещания. Извлеки из транскрипта ВСЕ поручения (кто, что, к какому сроку).

{ACTION_RULES}

{FEW_SHOT if os.getenv("AGENT_FEW_SHOT", "1") == "1" else ""}

Теперь реальное совещание. Дата совещания: {meeting_date} ({weekday}).
Календарь от даты совещания: {_meeting_calendar(meeting_date)}.

Транскрипт ([#номер мм:сс] SPEAKER_XX (имя) (язык): реплика):
{transcript}"""


def _verify_prompt(transcript: str, meeting_date: str, actions: list[dict[str, Any]]) -> str:
    weekday = WEEKDAYS[date.fromisoformat(meeting_date).weekday()]
    draft = json.dumps({"actions": actions}, ensure_ascii=False, indent=1)
    return f"""Ты — контролёр качества протокола. Проверь черновой список поручений по транскрипту и верни исправленный список.
Проверь каждое поручение: действительно ли оно было дано; правильный ли ответственный (с учётом делегирования); взят ли последний согласованный срок; правильно ли срок переведён в дату по календарю; нет ли дубликатов из итогов совещания; не пропущено ли поручение из обсуждения; не слиты ли в одно поручения с разными сроками или из разных реплик. Удали выдуманные поручения. Добавь пропущенные. Не придумывай имена, которых нет в транскрипте.

{ACTION_RULES}

Дата совещания: {meeting_date} ({weekday}).
Календарь: {_meeting_calendar(meeting_date)}.

Черновик:
{draft}

Транскрипт:
{transcript}"""


def _clean_actions(
    raw_actions: list[Any], segments: list[dict[str, Any]], meeting_date: str, names: dict[str, str], flags: list[str]
) -> list[dict[str, Any]]:
    by_id = {int(item.get("id", index)): item for index, item in enumerate(segments, start=1)}
    start_date = date.fromisoformat(meeting_date)
    today = date.today()
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_actions:
        if not isinstance(item, dict) or not str(item.get("task", "")).strip():
            continue
        action = {
            key: str(item.get(key) or "").strip()
            for key in ("task", "assignee", "assignee_speaker", "assigned_by", "deadline", "deadline_iso", "source_quote", "priority")
        }
        key = (action["task"].lower()[:60], action["assignee"].lower())
        if key in seen:
            continue
        seen.add(key)
        action["assignee"] = action["assignee"] or "Не определён"
        action["deadline"] = action["deadline"] or "Не указан"
        action["priority"] = action["priority"] or "средний"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", action["deadline"]):
            phrase, iso = deadlines.from_quote(action["source_quote"], meeting_date)
            if phrase:
                action["deadline"], action["deadline_iso"] = phrase, iso
        resolved = deadlines.resolve(action["deadline"], meeting_date)
        if resolved:
            action["deadline_iso"] = resolved
        elif action["deadline"].lower().startswith("не указ"):
            action["deadline_iso"] = ""
        try:
            due = date.fromisoformat(action["deadline_iso"]) if action["deadline_iso"] else None
        except ValueError:
            due = None
        if due and due < start_date:
            flags.append(f"Срок «{action['deadline']}» для «{action['task']}» раньше даты совещания — дата сброшена, проверьте вручную.")
            due = None
        action["deadline_iso"] = due.isoformat() if due else ""
        if not due and action["deadline"].lower().startswith("не указ"):
            flags.append(f"Поручение без срока: «{action['task']}» ({action['assignee']}).")
        speaker = action.pop("assignee_speaker")
        action["speaker"] = f"{speaker} ({names[speaker]})" if names.get(speaker) else (speaker or "не выступал на записи")
        try:
            segment = by_id.get(int(item.get("segment_id") or 0))
        except (TypeError, ValueError):
            segment = None
        action["time"] = _stamp(segment["start"]) if segment else ""
        action["segment_id"] = int(segment.get("id", 0)) if segment else 0
        if not action["source_quote"] and segment:
            action["source_quote"] = segment["text"][:200]
        action["status"] = "Просрочено" if due and due < today else "В работе"
        cleaned.append(action)
    return cleaned


def _local_llm_analysis(segments: list[dict[str, Any]], meeting_date: str, progress: ProgressFn | None = None) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []

    def step(name: str, share: float, run: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        _notify(progress, share, f"ИИ-агент: {name}…")
        started = time.time()
        result = run()
        trace.append({"step": name, "seconds": round(time.time() - started, 1)})
        return result

    raw_transcript = _transcript_text(segments)
    try:
        overview = step("определяю участников и итоги", 0.1, lambda: _ollama_json(_overview_prompt(raw_transcript, meeting_date), OVERVIEW_SCHEMA))
    except (ValueError, json.JSONDecodeError) as exc:  # keep going: names come from addresses, summary from the text
        trace.append({"step": "определяю участников и итоги", "seconds": 0, "result": f"ошибка модели, упрощённый режим ({exc})"})
        overview = {"participants": [], "summary": _heuristic_analysis(segments)["summary"], "summary_items": []}
    address_names = _names_from_addresses(segments)
    participants_raw = [item for item in overview.get("participants", []) if isinstance(item, dict)]
    known = {str(item.get("speaker", "")) for item in participants_raw}
    participants_raw += [{"speaker": speaker, "name": "", "role": ""} for speaker in address_names if speaker not in known]
    taken = {name.lower()[:5] for name in address_names.values()}
    own_words = {
        str(item.get("speaker", "")): " ".join(str(seg.get("text", "")).lower() for seg in segments if seg.get("speaker") == item.get("speaker"))
        for item in participants_raw
    }
    for item in participants_raw:
        speaker, name = str(item.get("speaker", "")), str(item.get("name", ""))
        key = name.lower()[:5]
        if speaker in address_names:
            item["name"] = address_names[speaker]  # names from how people were addressed win over the LLM guess
        elif not (_grounded(name, raw_transcript) and re.search(PATRONYMIC, name.lower())) or key in taken or key in own_words.get(speaker, ""):
            item["name"] = ""  # not said, not a person's name, someone else's, or the speaker addresses this person
        if item["name"]:
            taken.add(item["name"].lower()[:5])
    overview["participants"] = participants_raw
    names = {
        str(item.get("speaker", "")).strip(): str(item.get("name", "")).strip()
        for item in overview.get("participants", [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }
    trace[-1]["result"] = f"участников с именем: {len(names)}"
    named_transcript = _transcript_text(segments, names)
    try:
        drafted = step("извлекаю поручения", 0.4, lambda: _ollama_json(_actions_prompt(named_transcript, meeting_date), ACTIONS_SCHEMA))
    except (ValueError, json.JSONDecodeError):
        raise  # without draft assignments the whole analysis falls back to the transparent keyword mode
    actions = drafted.get("actions", [])
    flags = [str(flag) for flag in drafted.get("flags", [])]
    trace[-1]["result"] = f"черновик: {len(actions)} поручений"
    if os.getenv("AGENT_VERIFY", "1") == "1":
        try:
            verified = step("проверяю поручения по транскрипту", 0.7, lambda: _ollama_json(_verify_prompt(named_transcript, meeting_date, actions), ACTIONS_SCHEMA))
        except (ValueError, json.JSONDecodeError) as exc:  # a failed self-check keeps the draft
            trace.append({"step": "проверяю поручения по транскрипту", "seconds": 0, "result": f"пропущено ({exc})"})
            verified = {}
        if verified.get("actions"):
            actions = verified["actions"]
            flags = [str(flag) for flag in verified.get("flags", [])] or flags
        trace[-1]["result"] = f"после проверки: {len(actions)} поручений"
    for item in actions:
        if isinstance(item, dict) and not _grounded(str(item.get("assigned_by", "")), raw_transcript):
            segment = next((seg for seg in segments if seg.get("id") == item.get("segment_id")), None)
            speaker = str(segment.get("speaker", "")) if segment else ""
            item["assigned_by"] = names.get(speaker) or speaker or "председатель"
    cleaned = _clean_actions(actions, segments, meeting_date, names, flags)
    participants = [
        {"speaker": str(item.get("speaker", "")), "name": str(item.get("name", "")), "role": str(item.get("role", ""))}
        for item in overview.get("participants", [])
        if isinstance(item, dict)
    ]
    summary_items = [
        {key: str(item.get(key, "")) for key in ("topic", "indicator", "problem")}
        for item in overview.get("summary_items", [])
        if isinstance(item, dict)
    ]
    return {
        "summary": str(overview.get("summary") or "Саммари не сформировано."),
        "summary_items": summary_items,
        "participants": participants,
        "actions": cleaned,
        "flags": [flag for flag in dict.fromkeys(flags) if _trusted_flag(flag)],
        "agent_trace": trace,
        "model": OLLAMA_MODEL,
    }


_TASK_MARKERS = re.compile(
    r"\b(поручаю|поручение|нужно|надо|необходимо|долж\w*|пусть|подготов\w*|сдел\w*|обеспеч\w*|организ\w*|найти|найдите|найди|найду|найдёт|собрать|соберите|зафикс\w*|направ\w*|обнов\w*|провести|проведите|проведёт|пришл\w*|выстав\w*|ищите|пропишите|тапсырамын|тапсырма|керек|орындау|дайындау)\b",
    re.IGNORECASE,
)


def _heuristic_analysis(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Fallback when Ollama is unavailable; results are visibly marked for review."""
    texts = [item["text"].strip() for item in segments if item.get("text", "").strip()]
    summary = " ".join(texts[:3])[:900] or "Транскрипт не содержит распознанной речи."
    actions: list[dict[str, Any]] = []
    for index, item in enumerate(segments, start=1):
        text = item.get("text", "").strip()
        if not text or not _TASK_MARKERS.search(text):
            continue
        due_match = re.search(r"\b(?:до|к|дейін|мерзімі|сроком)\s+([^,.!?;]{1,50})", text, re.IGNORECASE)
        iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
        actions.append(
            {
                "task": text,
                "assignee": "Не определён",
                "assigned_by": str(item.get("speaker", "")),
                "speaker": str(item.get("speaker", "Спикер не определён")),
                "deadline": due_match.group(1).strip() if due_match else "Не указан",
                "deadline_iso": iso_match.group(1) if iso_match else "",
                "status": "В работе",
                "priority": "средний",
                "time": _stamp(item.get("start", 0)),
                "segment_id": int(item.get("id", index)),
                "source_quote": text,
            }
        )
    return {"summary": summary, "summary_items": [], "participants": [], "actions": actions, "flags": [], "agent_trace": [], "model": "heuristic"}


def analyze_meeting(
    segments: list[dict[str, Any]], meeting_date: str, progress: ProgressFn | None = None
) -> tuple[dict[str, Any], str, str | None]:
    """Use the local Ollama agent; fall back to transparent keyword extraction if it is offline."""
    if not segments:
        return {"summary": "Речь не распознана.", "actions": []}, "fallback", "В записи не найден распознанный текст."
    try:
        return _local_llm_analysis(segments, meeting_date, progress), "ollama", None
    except (OSError, TimeoutError, ValueError, KeyError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return _heuristic_analysis(segments), "fallback", f"Локальный Ollama недоступен; включено упрощённое извлечение. Проверьте поручения вручную. ({exc})"


PATRONYMIC = r"(?:(?:ович|евич|овн|евн|ичн|иничн)[а-яё]{0,3}|улы|ұлы|кызы|қызы)"
_LETTERS = "А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі"
VOCATIVE = re.compile(rf"([{_LETTERS}]{{3,}})\s+([{_LETTERS}]+{PATRONYMIC})\b", re.IGNORECASE)


def _names_from_addresses(segments: list[dict[str, Any]]) -> dict[str, str]:
    """Deterministic speaker naming: «…Тимур Болатович, что по Павлодару?» names the NEXT speaker.

    A name + patronymic in the second half of a turn (or anywhere in a short turn) votes for the
    speaker who talks next; each name goes to the speaker with the most votes.
    """
    votes: dict[str, dict[str, int]] = {}
    display: dict[str, str] = {}
    for current, following in zip(segments, segments[1:]):
        if current.get("speaker") == following.get("speaker"):
            continue
        text = str(current.get("text", ""))
        matches = list(VOCATIVE.finditer(text))
        if not matches:
            continue
        match = matches[-1]
        words_before = len(text[: match.start()].split())
        total_words = len(text.split())
        if total_words > 10 and words_before < total_words / 2 and "?" not in text:
            continue
        key = match.group(1).lower()[:5]
        patronymic = re.sub(r"(ов|ев)н(ы|е|ой|у)$", r"\1на", match.group(2).lower())
        patronymic = re.sub(r"(ич)(а|у|ем|е)$", r"\1", patronymic)
        display.setdefault(key, f"{match.group(1).capitalize()} {patronymic.capitalize()}")
        speaker_votes = votes.setdefault(str(following.get("speaker")), {})
        speaker_votes[key] = speaker_votes.get(key, 0) + 1
    names: dict[str, str] = {}
    used: set[str] = set()
    for speaker, counter in sorted(votes.items(), key=lambda item: -max(item[1].values())):
        for key, _ in sorted(counter.items(), key=lambda item: -item[1]):
            if key not in used:
                names[speaker] = display[key]
                used.add(key)
                break
    return names


def _trusted_flag(flag: str) -> bool:
    """Keep program-checked flags (no deadline, bad date) and LLM notes about contradictions only."""
    return flag.startswith(("Поручение без срока", "Срок «")) or "противореч" in flag.lower()


def _grounded(name: str, transcript: str) -> bool:
    """True when at least one word of the name (≥4 letters, stem of 4) occurs in the transcript."""
    words = [word for word in re.findall(r"\w+", name.lower()) if len(word) >= 4]
    if not words:
        return False
    text = transcript.lower()
    return any(word[:4] in text for word in words)


def _stamp(seconds: float) -> str:
    total = max(0, int(float(seconds or 0)))
    return f"{total // 60:02d}:{total % 60:02d}"
