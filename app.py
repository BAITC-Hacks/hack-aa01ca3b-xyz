from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from hackalem.export import build_docx, build_pdf
from hackalem.media import prepare_audio
from hackalem.processing import analyze_meeting, diarize, transcribe


st.set_page_config(page_title="HackAlem AI — протокол совещания", page_icon="🎙️", layout="wide")


def _stamp(seconds: float) -> str:
    total = max(0, int(float(seconds or 0)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _speaker_ids(segments: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(item.get("speaker", "Спикер не определён")) for item in segments))


def _process(upload: Any, meeting_title: str, meeting_date: date, expected_speakers: int | None) -> dict[str, Any]:
    suffix = Path(upload.name).suffix.lower() or ".audio"
    with tempfile.TemporaryDirectory(prefix="hackalem-") as temp_dir:
        source_path = Path(temp_dir) / f"recording{suffix}"
        source_path.write_bytes(upload.getvalue())
        audio_path = prepare_audio(source_path, temp_dir)
        segments, language, duration = transcribe(audio_path)
        turns = diarize(audio_path, segments, expected_speakers)
        if not turns and segments:
            raise RuntimeError("Диаризация не вернула говорящих. Проверьте запись и модель pyannote.")
        analysis, analysis_mode, analysis_note = analyze_meeting(turns, meeting_date.isoformat())
        return {
            "title": meeting_title.strip() or "Протокол совещания",
            "date": meeting_date.isoformat(),
            "language": language,
            "duration": duration,
            "transcript": turns,
            "summary": str(analysis.get("summary", "")),
            "actions": analysis.get("actions", []),
            "analysis_mode": analysis_mode,
            "analysis_note": analysis_note,
        }


st.title("🎙️ HackAlem AI")
st.subheader("Автопротоколирование совещаний с фиксацией поручений")
st.write("Загрузите запись. Приложение распознает русскую, казахскую и смешанную речь, подпишет реплики спикерами, соберёт поручения и подготовит протокол.")
st.info("Аудио и транскрипт обрабатываются локально. Приложение не отправляет их во внешние API. Участники должны быть уведомлены о записи и её обработке.", icon="🔒")

with st.expander("Подготовка моделей"):
    st.markdown(
        """1. Один раз установите модели командой `python scripts/download_models.py`.
2. Для скачивания pyannote заранее примите условия модели `pyannote/speaker-diarization-community-1` на Hugging Face и задайте `HF_TOKEN`.
3. Для качественного извлечения поручений запустите локальный Ollama и модель `qwen2.5:3b`. Без неё приложение покажет упрощённый результат по ключевым словам.

Загрузка весов моделей происходит отдельно. Во время обработки аудио и текста сеть не используется."""
    )

left, right = st.columns([2, 1])
with left:
    meeting_title = st.text_input("Название совещания", value="Совещание")
    upload = st.file_uploader(
        "Аудио или видео",
        type=["wav", "mp3", "m4a", "mp4", "mov", "mkv", "webm", "avi"],
        help="Для видео требуется ffmpeg. Файл хранится только во временной папке и удаляется после обработки.",
    )
with right:
    meeting_date = st.date_input("Дата совещания", value=date.today())
    expected_speakers = st.number_input("Ожидаемое число участников (подсказка)", min_value=0, max_value=30, value=0, help="0 — определить автоматически")

consent = st.checkbox("Запись сделана с согласия участников; при демонстрации реальные данные обезличены.")
start_processing = st.button("Создать протокол", type="primary", disabled=not (upload and consent), use_container_width=False)

if start_processing and upload:
    progress = st.status("Обработка идёт на этом компьютере…", expanded=True)
    try:
        progress.write("Распознавание речи, затем определение спикеров и извлечение поручений.")
        result = _process(upload, meeting_title, meeting_date, int(expected_speakers) or None)
        st.session_state["meeting_result"] = result
        progress.update(label="Протокол готов", state="complete", expanded=False)
    except Exception as error:
        progress.update(label="Не удалось обработать запись", state="error", expanded=True)
        st.error(str(error))


result = st.session_state.get("meeting_result")
if result:
    st.divider()
    st.header(result["title"])
    lang_labels = {"ru": "русский", "kk": "казахский", "en": "английский"}
    language_label = lang_labels.get(result["language"], result["language"])
    st.caption(f"Дата: {result['date']} · Распознанный язык: {language_label} · Длительность: {_stamp(result['duration'])}")
    if result.get("analysis_note"):
        st.warning(result["analysis_note"])

    st.subheader("Саммари")
    st.write(result["summary"] or "Саммари не сформировано.")

    speaker_names = st.session_state.setdefault("speaker_names", {})
    with st.expander("Имена участников"):
        st.caption("Диаризация различает голоса, но не устанавливает личность. При необходимости сопоставьте метки с именами вручную.")
        for speaker in _speaker_ids(result["transcript"]):
            speaker_names[speaker] = st.text_input(speaker, value=speaker_names.get(speaker, speaker), key=f"name_{speaker}")

    actions = [dict(item) for item in result["actions"]]
    for action in actions:
        action["speaker"] = speaker_names.get(action.get("speaker", ""), action.get("speaker", "Спикер не определён"))
        action.setdefault("task", "")
        action.setdefault("assignee", "Не определён")
        action.setdefault("deadline", "Не указан")
        action.setdefault("deadline_iso", "")
        action.setdefault("status", "В работе")
        action.setdefault("source_quote", "")
    action_frame = pd.DataFrame(actions, columns=["task", "assignee", "speaker", "deadline", "deadline_iso", "status", "source_quote"])
    st.subheader(f"Поручения ({len(action_frame)})")
    edited_actions = st.data_editor(
        action_frame,
        hide_index=True,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "task": st.column_config.TextColumn("Поручение", width="large"),
            "assignee": st.column_config.TextColumn("Ответственный"),
            "speaker": st.column_config.TextColumn("Спикер"),
            "deadline": st.column_config.TextColumn("Срок"),
            "deadline_iso": st.column_config.TextColumn("Срок (дата)", help="YYYY-MM-DD для напоминаний"),
            "status": st.column_config.SelectboxColumn("Статус", options=["В работе", "Просрочено", "Выполнено"]),
            "source_quote": st.column_config.TextColumn("Фрагмент записи", width="large"),
        },
        key="actions_editor",
    )
    current_actions = edited_actions.fillna("").to_dict(orient="records")
    csv_columns = {
        "task": "Поручение",
        "assignee": "Ответственный",
        "speaker": "Спикер",
        "deadline": "Срок",
        "deadline_iso": "Срок (дата)",
        "status": "Статус",
        "source_quote": "Фрагмент записи",
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
            reminders.append(("Просрочено", str(row.get("task", "Поручение"))))
        elif (due_date - date.today()).days <= 7:
            reminders.append(("Срок скоро", str(row.get("task", "Поручение"))))
    if reminders:
        st.subheader("Напоминания по срокам")
        for label, task in reminders:
            if label == "Просрочено":
                st.error(f"{label}: {task}")
            else:
                st.warning(f"{label} (7 дней): {task}")

    transcript_view = [
        {"Время": f"{_stamp(item['start'])}–{_stamp(item['end'])}", "Спикер": speaker_names.get(item.get("speaker", ""), item.get("speaker", "Спикер не определён")), "Реплика": item["text"]}
        for item in result["transcript"]
    ]
    with st.expander(f"Полный транскрипт ({len(transcript_view)} фрагментов)"):
        st.dataframe(pd.DataFrame(transcript_view), hide_index=True, use_container_width=True)

    st.subheader("Экспорт протокола")
    final_transcript = [
        {**item, "speaker": speaker_names.get(item.get("speaker", ""), item.get("speaker", "Спикер не определён"))}
        for item in result["transcript"]
    ]
    docx_bytes = build_docx(result["title"], result["date"], result["summary"], current_actions, final_transcript)
    pdf_bytes = build_pdf(result["title"], result["date"], result["summary"], current_actions, final_transcript)
    safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in result["title"]).strip("_") or "meeting"
    docx_col, pdf_col = st.columns(2)
    docx_col.download_button("Скачать DOCX", data=docx_bytes, file_name=f"{safe_name}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
    pdf_col.download_button("Скачать PDF", data=pdf_bytes, file_name=f"{safe_name}.pdf", mime="application/pdf", use_container_width=True)

st.caption("Протокол и извлечённые поручения нужно проверить перед рассылкой. Автоматическая интеграция с Teams, Zoom, Google Meet и СЭД не входит в этот прототип.")
