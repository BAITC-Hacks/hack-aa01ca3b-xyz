from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import streamlit as st

# Make package imports work when Streamlit runs this file by path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import SAMPLES_DIR
from app.services import live
from app.services.exporter import build_docx, build_pdf
from app.services.extractor import analyze_meeting, transcribe_meeting
from app.services.media import prepare_audio


st.set_page_config(page_title="Briefly AI — протокол совещания", page_icon="✨", layout="wide")

LANG_LABELS = {"ru": "русский", "kk": "казахский", "mix": "шала (смешанная)", "en": "английский"}
EXAMPLE_RESULT = SAMPLES_DIR / "synthetic_meeting.result.json"

st.markdown(
    """
<style>
.block-container {max-width: 1120px; padding-top: 2.2rem;}
.hero {text-align: center; margin: 0 auto 1.4rem; max-width: 760px;}
.hero .logo {display: inline-block; font-weight: 800; font-size: 1.05rem; letter-spacing: .02em;
  padding: .35rem .9rem; border-radius: 999px; background: #F1ECFF; color: #5B3FE0;}
.hero h1 {font-size: 2.5rem; line-height: 1.15; margin: .9rem 0 .5rem; font-weight: 800;
  background: linear-gradient(90deg, #5B3FE0, #2F80ED); -webkit-background-clip: text; background-clip: text; color: transparent;}
.hero p {color: #5B5B72; font-size: 1.05rem; margin: 0 auto;}
.steps {display: flex; gap: .6rem; justify-content: center; flex-wrap: wrap; margin-top: 1rem;}
.steps span {background: #F7F7FB; border: 1px solid #ECEAF5; border-radius: 999px; padding: .35rem .85rem; font-size: .9rem; color: #3A3A55;}
.chips {display: flex; gap: .45rem; flex-wrap: wrap; margin: .2rem 0 .8rem;}
.chips span {background: #F4F2FF; color: #4A3AB8; border-radius: 999px; padding: .2rem .7rem; font-size: .85rem;}
.live-line {padding: .35rem .6rem; border-radius: .6rem; background: #F8F8FC; margin-bottom: .3rem;}
.live-line b {color: #5B3FE0; margin-right: .4rem;}
.live-line code {margin-right: .4rem;}
</style>
""",
    unsafe_allow_html=True,
)


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


def _names_from(participants: list[dict[str, Any]]) -> dict[str, str]:
    return {
        item["speaker"]: " — ".join(part for part in (item.get("name", ""), item.get("role", "")) if part)
        for item in participants
        if item.get("speaker") and item.get("name")
    }


def _process(upload: Any, meeting_title: str, meeting_date: date, expected_speakers: int | None, bar: Any) -> dict[str, Any]:
    suffix = Path(upload.name).suffix.lower() or ".audio"
    audio_bytes = upload.getvalue()
    with tempfile.TemporaryDirectory(prefix="briefly-") as temp_dir:
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


def _load_example() -> None:
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
    st.session_state["speaker_names"] = _names_from(example.get("participants", []))


@st.fragment(run_every=1.5)
def _live_panel() -> None:
    session = st.session_state.get("live_session")
    if session is None:
        return
    status = "🔴 Идёт запись" if session.running else "⏹ Запись остановлена"
    st.caption(f"{status} · {_stamp(session.elapsed if session.running else session._position)} · распознано фраз: {len(session.lines)}")
    for line in session.lines[-12:]:
        st.markdown(
            f"<div class='live-line'><b>{_stamp(line['start'])}</b><code>{line['lang']}</code>{line['text']}</div>",
            unsafe_allow_html=True,
        )
    if session.running and not session.lines:
        st.caption("Говорите — фразы появятся здесь через пару секунд после паузы.")
    if session.error:
        st.warning(session.error)


# --------------------------------------------------------------------------- hero

st.markdown(
    """
<div class="hero">
  <span class="logo">✨ Briefly AI</span>
  <h1>Совещание → протокол с поручениями</h1>
  <p>Загрузите или запишите встречу: ИИ разделит голоса, распознает русский, қазақша и шала,
  найдёт поручения со сроками и соберёт протокол. Всё работает на этом компьютере — без облака.</p>
  <div class="steps"><span>① Запись</span><span>② ИИ-анализ</span><span>③ Протокол DOCX / PDF</span></div>
</div>
""",
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- input card

live_source = None
with st.container(border=True):
    file_tab, record_tab, live_tab = st.tabs(["📁 Загрузить файл", "🎙️ Записать сейчас", "⚡ Живая расшифровка"])
    with file_tab:
        upload = st.file_uploader(
            "Перетащите аудио или видео совещания",
            type=["wav", "mp3", "m4a", "ogg", "mp4", "mov", "mkv", "webm", "avi"],
            help="Файл хранится только во временной папке и удаляется после обработки.",
        )
    with record_tab:
        recorded = st.audio_input("Нажмите на микрофон и говорите — запись останется на этом компьютере")
    with live_tab:
        ready, reason = live.available()
        if not ready:
            st.info(reason)
        else:
            st.caption(
                "Реплики появляются на экране во время совещания. Микрофон и нарезку фраз даёт библиотека "
                "SpeechRecognition, распознаёт локальная модель kaz-rus — звук никуда не отправляется."
            )
            session = st.session_state.get("live_session")
            if session is None or not session.running:
                microphones = live.microphones()
                if microphones:
                    default = next((i for i, (_, name) in enumerate(microphones) if "MacBook" in name or "Built-in" in name), 0)
                    choice = st.selectbox("Микрофон", microphones, index=default, format_func=lambda item: item[1])
                    if st.button("▶ Начать живую расшифровку", type="secondary"):
                        new_session = live.LiveSession(device_index=choice[0])
                        with st.spinner("Калибруем микрофон по шуму в комнате…"):
                            new_session.start()
                        st.session_state["live_session"] = new_session
                        st.rerun()
                else:
                    st.warning("Микрофон не найден.")
            else:
                if st.button("■ Остановить запись", type="primary"):
                    with st.spinner("Дораспознаём последние фразы…"):
                        session.stop()
                    st.rerun()
            if session is not None:
                _live_panel()
                if not session.running and session.chunks:
                    wav_path = Path(tempfile.gettempdir()) / f"briefly-live-{id(session)}.wav"
                    session.save_wav(wav_path)
                    wav_bytes = wav_path.read_bytes()
                    st.audio(wav_bytes, format="audio/wav")
                    st.caption("Нажмите «Создать протокол» — ИИ разберёт всю запись целиком: разделит голоса и найдёт поручения.")
                    live_source = SimpleNamespace(name="live.wav", getvalue=lambda data=wav_bytes: data)

    title_col, date_col, people_col = st.columns([2, 1, 1])
    meeting_title = title_col.text_input("Название совещания", value="Совещание")
    meeting_date = date_col.date_input("Дата", value=date.today(), help="От этой даты считаются сроки «до пятницы», «за две недели» и т. п.")
    expected_speakers = people_col.number_input("Участников", min_value=0, max_value=30, value=0, help="0 — определить автоматически")
    consent = st.checkbox("Участники уведомлены о записи и расшифровке с помощью ИИ; реальные данные при демонстрации обезличены.")

    source = upload or recorded or live_source
    create_col, example_col = st.columns([2, 1])
    start_processing = create_col.button("✨ Создать протокол", type="primary", disabled=not (source and consent), width="stretch")
    if EXAMPLE_RESULT.is_file() and example_col.button("👀 Посмотреть пример", width="stretch"):
        _load_example()

if start_processing and source:
    progress = st.status("ИИ работает на этом компьютере…", expanded=True)
    try:
        bar = progress.progress(0.0, text="Подготовка записи…")
        result = _process(source, meeting_title, meeting_date, int(expected_speakers) or None, bar)
        st.session_state["meeting_result"] = result
        st.session_state["speaker_names"] = _names_from(result["participants"])
        progress.update(label="Протокол готов", state="complete", expanded=False)
    except Exception as error:
        progress.update(label="Не удалось обработать запись", state="error", expanded=True)
        st.error(str(error))

# --------------------------------------------------------------------------- results

result = st.session_state.get("meeting_result")
if result:
    st.divider()
    st.subheader(result["title"])
    languages = ", ".join(LANG_LABELS.get(code, code) for code in str(result["language"]).split("+") if code)
    st.markdown(
        f"<div class='chips'><span>📅 {result['date']}</span><span>⏱ {_stamp(result['duration'])}</span>"
        f"<span>🗣 {languages or '—'}</span><span>🤖 {result.get('model', '')}</span><span>🔒 локально</span></div>",
        unsafe_allow_html=True,
    )
    if result.get("analysis_note"):
        st.warning(result["analysis_note"])

    speaker_names = st.session_state.setdefault("speaker_names", {})
    actions_tab, summary_tab, people_tab, transcript_tab, export_tab, agent_tab = st.tabs(
        ["✅ Поручения", "📝 Саммари", "👥 Участники", "💬 Транскрипт", "⬇️ Протокол", "🤖 ИИ-агент"]
    )

    with people_tab:
        st.caption("Голоса различает диаризация. Имена берутся из обращений («Тимур Болатович, что по Павлодару?» → следующий голос). Проверьте и при необходимости исправьте.")
        for speaker in _speaker_ids(result["transcript"]):
            speaker_names[speaker] = st.text_input(speaker, value=speaker_names.get(speaker, speaker), key=f"name_{speaker}")

    actions = [dict(item) for item in result["actions"]]
    for action in actions:
        action["speaker"] = speaker_names.get(action.get("speaker", ""), action.get("speaker", "Спикер не определён"))
        for field, default in (("task", ""), ("assignee", "Не определён"), ("assigned_by", ""), ("deadline", "Не указан"), ("deadline_iso", ""), ("status", "В работе"), ("priority", "средний"), ("time", ""), ("source_quote", "")):
            action.setdefault(field, default)

    with actions_tab:
        columns = ["task", "assignee", "deadline", "deadline_iso", "status", "priority", "assigned_by", "time", "source_quote"]
        edited_actions = st.data_editor(
            pd.DataFrame(actions, columns=columns),
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
            st.markdown("**🔔 Напоминания по срокам**")
            for label, task in reminders:
                (st.error if label == "Просрочено" else st.warning)(f"{label}: {task}")

        if result.get("audio") and current_actions:
            with st.expander("🔊 Доказательство: прослушать момент, где дано поручение"):
                labels = [f"{row['time'] or '00:00'} — {row['task']}" for row in current_actions]
                choice = st.selectbox("Поручение", options=range(len(labels)), format_func=lambda index: labels[index])
                st.audio(result["audio"], format=result.get("audio_format", "audio/mpeg"), start_time=_seconds(current_actions[choice]["time"]))
                st.caption(f"Цитата: «{current_actions[choice]['source_quote']}»")

    counts = {label: sum(row.get("status") == label for row in current_actions) for label in ["В работе", "Просрочено", "Выполнено"]}
    no_deadline = sum(1 for row in current_actions if not str(row.get("deadline_iso", "")).strip())

    with summary_tab:
        metric_cols = st.columns(4)
        metric_cols[0].metric("Поручений", len(current_actions))
        metric_cols[1].metric("Участников", len(_speaker_ids(result["transcript"])))
        metric_cols[2].metric("Без срока", no_deadline)
        metric_cols[3].metric("Просрочено", counts["Просрочено"])
        st.write(result["summary"] or "Саммари не сформировано.")
        if result.get("summary_items"):
            st.dataframe(
                pd.DataFrame(result["summary_items"]).rename(columns={"topic": "Направление / доклад", "indicator": "Показатель", "problem": "Проблема"}),
                hide_index=True,
                width="stretch",
            )

    with transcript_tab:
        transcript_view = [
            {
                "Время": f"{_stamp(item['start'])}–{_stamp(item['end'])}",
                "Спикер": speaker_names.get(item.get("speaker", ""), item.get("speaker", "Спикер не определён")),
                "Язык": LANG_LABELS.get(item.get("lang", ""), item.get("lang", "")),
                "Реплика": item["text"],
            }
            for item in result["transcript"]
        ]
        st.dataframe(pd.DataFrame(transcript_view), hide_index=True, width="stretch")

    with export_tab:
        final_transcript = [
            {**item, "speaker": speaker_names.get(item.get("speaker", ""), item.get("speaker", "Спикер не определён"))}
            for item in result["transcript"]
        ]
        participants = [{"speaker": speaker, "name": speaker_names.get(speaker, speaker)} for speaker in _speaker_ids(result["transcript"])]
        docx_bytes = build_docx(result["title"], result["date"], result["summary"], current_actions, final_transcript, participants=participants, summary_items=result.get("summary_items"))
        pdf_bytes = build_pdf(result["title"], result["date"], result["summary"], current_actions, final_transcript, participants=participants, summary_items=result.get("summary_items"))
        csv_columns = {
            "task": "Поручение", "assignee": "Ответственный", "deadline": "Срок", "deadline_iso": "Срок (дата)", "status": "Статус",
            "priority": "Срочность", "assigned_by": "Кто поручил", "time": "Время", "source_quote": "Цитата",
        }
        csv_actions = pd.DataFrame(current_actions, columns=csv_columns).rename(columns=csv_columns)
        csv_actions = csv_actions.map(lambda value: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value)
        safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in result["title"]).strip("_") or "meeting"
        st.caption("Протокол включает участников, саммари, таблицу поручений со сроками и полный транскрипт. Проверьте перед рассылкой.")
        docx_col, pdf_col, csv_col = st.columns(3)
        docx_col.download_button("📄 DOCX", data=docx_bytes, file_name=f"{safe_name}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", width="stretch", type="primary")
        pdf_col.download_button("📕 PDF", data=pdf_bytes, file_name=f"{safe_name}.pdf", mime="application/pdf", width="stretch")
        csv_col.download_button("📊 Поручения CSV", data=csv_actions.to_csv(index=False).encode("utf-8-sig"), file_name="porucheniya.csv", mime="text/csv", width="stretch")

    with agent_tab:
        st.caption("Цепочка шагов локальной модели: участники по обращениям → поручения → самопроверка по транскрипту → точный пересчёт сроков в даты программой.")
        if result.get("agent_trace"):
            st.dataframe(
                pd.DataFrame(result["agent_trace"]).rename(columns={"step": "Шаг", "seconds": "Секунд", "result": "Результат"}),
                hide_index=True,
                width="stretch",
            )

st.caption("Briefly AI · аудио и текст не покидают этот компьютер · интеграции с Teams, Zoom, Google Meet и СЭД — следующий этап.")
