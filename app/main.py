from __future__ import annotations

import tempfile
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

# Make package imports work when Streamlit runs this file by path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import SAMPLES_DIR
from app.services.exporter import build_docx, build_pdf
from app.services.media import prepare_audio
from app.services.extractor import analyze_meeting, transcribe_meeting


st.set_page_config(page_title="Хаттама — протокол совещания", page_icon="🎙️", layout="wide")

LANG_LABELS = {"ru": "русский", "kk": "казахский", "mix": "шала (смешанная)", "en": "английский"}


def _stamp(seconds: float) -> str:
    total = max(0, int(float(seconds or 0)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _seconds(stamp: str) -> int:
    try:
        minutes, seconds = str(stamp).split(":")
        return int(minutes) * 60 + int(seconds)
    except ValueError:
        return 0


def _speaker_ids(segments: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(item.get("speaker", "Спикер не определён")) for item in segments))


def _process(upload: Any, meeting_title: str, meeting_date: date, expected_speakers: int | None, bar: Any) -> dict[str, Any]:
    suffix = Path(upload.name).suffix.lower() or ".audio"
    audio_bytes = upload.getvalue()
    with tempfile.TemporaryDirectory(prefix="hackalem-") as temp_dir:
        source_path = Path(temp_dir) / f"recording{suffix}"
        source_path.write_bytes(audio_bytes)
        audio_path = prepare_audio(source_path, temp_dir)
        segments, language, duration = transcribe_meeting(
            audio_path, expected_speakers, progress=lambda share, text: bar.progress(0.6 * share, text=text)
        )
        if not segments:
            raise RuntimeError("В записи не найдена речь. Проверьте файл.")
        analysis, analysis_mode, analysis_note = analyze_meeting(
            segments, meeting_date.isoformat(), progress=lambda share, text: bar.progress(0.6 + 0.4 * share, text=text)
        )
        bar.progress(1.0, text="Протокол готов")
        return {
            "title": meeting_title.strip() or "Протокол совещания",
            "date": meeting_date.isoformat(),
            "language": language,
            "duration": duration,
            "transcript": segments,
            "summary": str(analysis.get("summary", "")),
            "summary_items": analysis.get("summary_items", []),
            "participants": analysis.get("participants", []),
            "actions": analysis.get("actions", []),
            "flags": analysis.get("flags", []),
            "agent_trace": analysis.get("agent_trace", []),
            "model": analysis.get("model", ""),
            "analysis_mode": analysis_mode,
            "analysis_note": analysis_note,
            "audio": audio_bytes,
            "audio_format": {".mp3": "audio/mpeg", ".wav": "audio/wav", ".audio": "audio/wav", ".m4a": "audio/mp4"}.get(suffix, f"audio/{suffix.lstrip('.')}"),
        }


st.title("🎙️ Хаттама")
st.subheader("Автопротоколирование совещаний с фиксацией поручений — русский, қазақша, шала")
st.write("Загрузите запись. Приложение разделит голоса участников, распознает русскую, казахскую и смешанную речь, определит, кто есть кто, соберёт поручения со сроками и подготовит протокол.")
st.info("Аудио и транскрипт обрабатываются только на этом компьютере. Внешние API не используются — решение можно развернуть в закрытом контуре. Участники должны быть уведомлены о записи и её обработке ИИ.", icon="🔒")

with st.expander("Подготовка моделей"):
    st.markdown(
        """1. Один раз скачайте модели: `python scripts/download_models.py` (аккаунты и токены не нужны).
2. Запустите локальный Ollama и скачайте модель: `ollama pull qwen3:4b`. Без неё приложение покажет упрощённый результат по ключевым словам.

Скачиваются только веса моделей. Во время обработки аудио и текста сеть не используется."""
    )

left, right = st.columns([2, 1])
with left:
    meeting_title = st.text_input("Название совещания", value="Совещание")
    upload = st.file_uploader(
        "Аудио или видео",
        type=["wav", "mp3", "m4a", "ogg", "mp4", "mov", "mkv", "webm", "avi"],
        help="Файл хранится только во временной папке и удаляется после обработки.",
    )
    recorded = st.audio_input("…или запишите совещание прямо здесь (микрофон этого компьютера)")
    if recorded and not upload:
        upload = recorded
with right:
    meeting_date = st.date_input("Дата совещания", value=date.today(), help="От этой даты считаются сроки «до пятницы», «за две недели» и т. п.")
    expected_speakers = st.number_input("Ожидаемое число участников (подсказка)", min_value=0, max_value=30, value=0, help="0 — определить автоматически")

consent = st.checkbox("Участники уведомлены о записи и расшифровке с помощью ИИ; при демонстрации реальные данные обезличены.")
action_col, example_col = st.columns([1, 2])
start_processing = action_col.button("Создать протокол", type="primary", disabled=not (upload and consent), width="content")
EXAMPLE_RESULT = SAMPLES_DIR / "synthetic_meeting.result.json"
if EXAMPLE_RESULT.is_file() and example_col.button("Открыть готовый пример (запись из data/samples/, обработана заранее)"):
    import json

    example = json.loads(EXAMPLE_RESULT.read_text(encoding="utf-8"))
    st.session_state["meeting_result"] = {
        "title": "Пример: синтетическое совещание (ru / kk / шала)",
        "analysis_mode": "ollama",
        "analysis_note": None,
        "audio": (SAMPLES_DIR / "synthetic_meeting.mp3").read_bytes(),
        "audio_format": "audio/mpeg",
        **{key: example.get(key, default) for key, default in (
            ("date", "2026-09-23"), ("language", ""), ("duration", 0.0), ("transcript", []), ("summary", ""),
            ("summary_items", []), ("participants", []), ("actions", []), ("flags", []), ("agent_trace", []), ("model", ""),
        )},
    }
    st.session_state["speaker_names"] = {
        item["speaker"]: " — ".join(part for part in (item.get("name", ""), item.get("role", "")) if part)
        for item in example.get("participants", [])
        if item.get("speaker") and item.get("name")
    }

if start_processing and upload:
    progress = st.status("Обработка идёт на этом компьютере…", expanded=True)
    try:
        bar = progress.progress(0.0, text="Подготовка записи…")
        result = _process(upload, meeting_title, meeting_date, int(expected_speakers) or None, bar)
        st.session_state["meeting_result"] = result
        st.session_state["speaker_names"] = {
            item["speaker"]: " — ".join(part for part in (item.get("name", ""), item.get("role", "")) if part)
            for item in result["participants"]
            if item.get("speaker") and item.get("name")
        }
        progress.update(label="Протокол готов", state="complete", expanded=False)
    except Exception as error:
        progress.update(label="Не удалось обработать запись", state="error", expanded=True)
        st.error(str(error))


result = st.session_state.get("meeting_result")
if result:
    st.divider()
    st.header(result["title"])
    languages = ", ".join(LANG_LABELS.get(code, code) for code in str(result["language"]).split("+"))
    st.caption(f"Дата: {result['date']} · Языки в записи: {languages} · Длительность: {_stamp(result['duration'])} · Модель анализа: {result.get('model', '')}")
    if result.get("analysis_note"):
        st.warning(result["analysis_note"])

    st.subheader("Саммари")
    st.write(result["summary"] or "Саммари не сформировано.")
    if result.get("summary_items"):
        st.dataframe(
            pd.DataFrame(result["summary_items"]).rename(columns={"topic": "Направление / доклад", "indicator": "Показатель", "problem": "Проблема"}),
            hide_index=True,
            width="stretch",
        )

    speaker_names = st.session_state.setdefault("speaker_names", {})
    with st.expander("Участники (определены ИИ по обращениям — можно исправить)", expanded=True):
        st.caption("Голоса различает диаризация, имена ИИ-агент выводит из обращений («Тимур Болатович, что по Павлодару?»). Проверьте и при необходимости исправьте.")
        for speaker in _speaker_ids(result["transcript"]):
            speaker_names[speaker] = st.text_input(speaker, value=speaker_names.get(speaker, speaker), key=f"name_{speaker}")

    actions = [dict(item) for item in result["actions"]]
    for action in actions:
        action["speaker"] = speaker_names.get(action.get("speaker", ""), action.get("speaker", "Спикер не определён"))
        for field, default in (("task", ""), ("assignee", "Не определён"), ("assigned_by", ""), ("deadline", "Не указан"), ("deadline_iso", ""), ("status", "В работе"), ("priority", "средний"), ("time", ""), ("source_quote", "")):
            action.setdefault(field, default)
    columns = ["task", "assignee", "deadline", "deadline_iso", "status", "priority", "assigned_by", "time", "source_quote"]
    action_frame = pd.DataFrame(actions, columns=columns)
    st.subheader(f"Поручения ({len(action_frame)})")
    edited_actions = st.data_editor(
        action_frame,
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        column_config={
            "task": st.column_config.TextColumn("Поручение", width="large"),
            "assignee": st.column_config.TextColumn("Ответственный"),
            "deadline": st.column_config.TextColumn("Срок (как сказано)"),
            "deadline_iso": st.column_config.TextColumn("Срок (дата)", help="YYYY-MM-DD для напоминаний"),
            "status": st.column_config.SelectboxColumn("Статус", options=["В работе", "Просрочено", "Выполнено"]),
            "priority": st.column_config.SelectboxColumn("Срочность", options=["высокий", "средний", "низкий"]),
            "assigned_by": st.column_config.TextColumn("Кто поручил"),
            "time": st.column_config.TextColumn("Время", help="Момент записи, где дано поручение"),
            "source_quote": st.column_config.TextColumn("Цитата из записи", width="large"),
        },
        key="actions_editor",
    )
    current_actions = edited_actions.fillna("").to_dict(orient="records")
    for flag in result.get("flags", []):
        st.warning(flag, icon="⚠️")

    if result.get("audio"):
        with st.expander("🔊 Доказательство: прослушать момент, где дано поручение"):
            labels = [f"{row['time'] or '00:00'} — {row['task']}" for row in current_actions]
            if labels:
                choice = st.selectbox("Поручение", options=range(len(labels)), format_func=lambda index: labels[index])
                st.audio(result["audio"], format=result.get("audio_format", "audio/mpeg"), start_time=_seconds(current_actions[choice]["time"]))
                st.caption(f"Цитата: «{current_actions[choice]['source_quote']}»")

    csv_columns = {
        "task": "Поручение",
        "assignee": "Ответственный",
        "deadline": "Срок",
        "deadline_iso": "Срок (дата)",
        "status": "Статус",
        "priority": "Срочность",
        "assigned_by": "Кто поручил",
        "time": "Время",
        "source_quote": "Цитата",
    }
    csv_actions = pd.DataFrame(current_actions, columns=csv_columns).rename(columns=csv_columns)
    csv_actions = csv_actions.map(
        lambda value: "'" + value
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
        else value
    )
    st.download_button(
        "Скачать поручения CSV",
        data=csv_actions.to_csv(index=False).encode("utf-8-sig"),
        file_name="porucheniya.csv",
        mime="text/csv",
        help="Скачивает текущие поручения с учётом правок и статусов.",
    )
    counts = {label: sum(row.get("status") == label for row in current_actions) for label in ["В работе", "Просрочено", "Выполнено"]}
    metrics = st.columns(3)
    for slot, (label, count) in zip(metrics, counts.items()):
        slot.metric(label, count)

    reminders: list[tuple[str, str]] = []
    for row in current_actions:
        due_text = str(row.get("deadline_iso", "")).strip()
        if not due_text or row.get("status") == "Выполнено":
            continue
        try:
            due_date = date.fromisoformat(due_text)
        except ValueError:
            continue
        if due_date < date.today():
            reminders.append(("Просрочено", f"{row.get('task', 'Поручение')} — {row.get('assignee', '')}"))
        elif (due_date - date.today()).days <= 7:
            reminders.append(("Срок скоро", f"{row.get('task', 'Поручение')} — {row.get('assignee', '')}, до {due_text}"))
    if reminders:
        st.subheader("Напоминания по срокам")
        for label, task in reminders:
            if label == "Просрочено":
                st.error(f"{label}: {task}")
            else:
                st.warning(f"{label} (7 дней): {task}")

    transcript_view = [
        {
            "Время": f"{_stamp(item['start'])}–{_stamp(item['end'])}",
            "Спикер": speaker_names.get(item.get("speaker", ""), item.get("speaker", "Спикер не определён")),
            "Язык": LANG_LABELS.get(item.get("lang", ""), item.get("lang", "")),
            "Реплика": item["text"],
        }
        for item in result["transcript"]
    ]
    with st.expander(f"Полный транскрипт ({len(transcript_view)} реплик)"):
        st.dataframe(pd.DataFrame(transcript_view), hide_index=True, width="stretch")

    if result.get("agent_trace"):
        with st.expander("🤖 Как работал ИИ-агент"):
            st.caption("Цепочка шагов локальной модели: определение участников → извлечение поручений → самопроверка по транскрипту → детерминированная проверка дат.")
            st.dataframe(
                pd.DataFrame(result["agent_trace"]).rename(columns={"step": "Шаг", "seconds": "Секунд", "result": "Результат"}),
                hide_index=True,
                width="stretch",
            )

    st.subheader("Экспорт протокола")
    final_transcript = [
        {**item, "speaker": speaker_names.get(item.get("speaker", ""), item.get("speaker", "Спикер не определён"))}
        for item in result["transcript"]
    ]
    participants = [
        {"speaker": speaker, "name": speaker_names.get(speaker, speaker)} for speaker in _speaker_ids(result["transcript"])
    ]
    docx_bytes = build_docx(result["title"], result["date"], result["summary"], current_actions, final_transcript, participants=participants, summary_items=result.get("summary_items"))
    pdf_bytes = build_pdf(result["title"], result["date"], result["summary"], current_actions, final_transcript, participants=participants, summary_items=result.get("summary_items"))
    safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in result["title"]).strip("_") or "meeting"
    docx_col, pdf_col = st.columns(2)
    docx_col.download_button("Скачать DOCX", data=docx_bytes, file_name=f"{safe_name}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", width="stretch")
    pdf_col.download_button("Скачать PDF", data=pdf_bytes, file_name=f"{safe_name}.pdf", mime="application/pdf", width="stretch")

st.caption("Протокол и извлечённые поручения нужно проверить перед рассылкой. Интеграции с Teams, Zoom, Google Meet и СЭД — следующий этап развития.")
