from __future__ import annotations

import json
import sys
import tempfile
import time
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
from app.services import live, registry
from app.services.exporter import build_docx, build_pdf, build_transcript_pdf
from app.services.extractor import analyze_meeting, transcribe_meeting
from app.services.media import prepare_audio
from app.ui.i18n import LANGUAGES, text


st.set_page_config(page_title="Briefly AI — протокол совещания", page_icon="✨", layout="wide")

EXAMPLE_RESULT = SAMPLES_DIR / "synthetic_meeting.result.json"
LANG_LABELS = {"ru": "русский", "kk": "қазақша", "mix": "шала", "en": "english"}
STATUS_FROM_ACTION = {"Выполнено": "done", "Просрочено": "failed", "В работе": "progress"}
THEMES = {
    "light": {"base": "light", "primaryColor": "#5B3FE0", "backgroundColor": "#FFFFFF", "secondaryBackgroundColor": "#F6F4FF", "textColor": "#1E1B3A"},
    "dark": {"base": "dark", "primaryColor": "#9A8CFF", "backgroundColor": "#0F0E17", "secondaryBackgroundColor": "#1C1A2E", "textColor": "#ECEAFF"},
}

st.session_state.setdefault("lang", "ru")
if "theme" not in st.session_state:  # the theme option is global: show the one currently applied
    try:
        st.session_state["theme"] = "dark" if st._config.get_option("theme.base") == "dark" else "light"
    except Exception:  # noqa: BLE001
        st.session_state["theme"] = "light"


def t(key: str, **kwargs: object) -> str:
    return text(key, st.session_state.get("lang", "ru"), **kwargs)


def _apply_theme(mode: str) -> None:
    """Switch Streamlit's theme at runtime (applies on the next rerun)."""
    try:
        for option, value in THEMES[mode].items():
            st._config.set_option(f"theme.{option}", value)
    except Exception:  # noqa: BLE001 - theme switching is cosmetic
        pass


dark = st.session_state["theme"] == "dark"
pill_bg, pill_border, muted = ("#1C1A2E", "#2E2B47", "#B8B5D6") if dark else ("#F7F7FB", "#ECEAF5", "#5B5B72")
st.markdown(
    f"""
<style>
.block-container {{max-width: 1120px; padding-top: 1.6rem;}}
.hero {{text-align: center; margin: 0 auto 1.2rem; max-width: 780px;}}
.hero .logo {{display: inline-block; font-weight: 800; padding: .3rem .9rem; border-radius: 999px; background: {pill_bg}; color: #7A63FF; border: 1px solid {pill_border};}}
.hero h1 {{font-size: 2.3rem; line-height: 1.15; margin: .8rem 0 .5rem; font-weight: 800;
  background: linear-gradient(90deg, #6D4DFF, #2F80ED); -webkit-background-clip: text; background-clip: text; color: transparent;}}
.hero p {{color: {muted}; font-size: 1.02rem; margin: 0 auto;}}
.steps {{display: flex; gap: .6rem; justify-content: center; flex-wrap: wrap; margin-top: .9rem;}}
.steps span, .chips span {{background: {pill_bg}; border: 1px solid {pill_border}; border-radius: 999px; padding: .28rem .8rem; font-size: .88rem;}}
.chips {{display: flex; gap: .45rem; flex-wrap: wrap; margin: .1rem 0 .8rem;}}
.badge {{display: inline-block; font-weight: 700; font-size: .78rem; padding: .18rem .65rem; border-radius: 999px; margin-bottom: .35rem;}}
.badge.done {{background: #DCFCE7; color: #166534;}}
.badge.progress {{background: #FEF3C7; color: #92400E;}}
.badge.failed {{background: #FEE2E2; color: #991B1B;}}
.task-title {{font-weight: 700; font-size: 1.02rem; margin: .1rem 0 .25rem;}}
.task-meta {{color: {muted}; font-size: .9rem;}}
.live-line {{padding: .35rem .6rem; border-radius: .6rem; background: {pill_bg}; margin-bottom: .3rem;}}
.live-line b {{color: #7A63FF; margin-right: .4rem;}}
</style>
""",
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- helpers


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


def _badge(status: str) -> str:
    return f"<span class='badge {status}'>● {t('status_' + status)}</span>"


def _process(upload: Any, meeting_title: str, meeting_date: date, expected_speakers: int | None, bar: Any) -> dict[str, Any]:
    started = time.time()

    def report(share: float, message: str) -> None:
        elapsed = time.time() - started
        eta = f" · {t('eta', eta=_stamp(elapsed * (1 - share) / share))}" if share > 0.04 else ""
        bar.progress(min(max(share, 0.0), 1.0), text=f"{message}{eta}")

    suffix = Path(upload.name).suffix.lower() or ".audio"
    audio_bytes = upload.getvalue()
    with tempfile.TemporaryDirectory(prefix="briefly-") as temp_dir:
        source_path = Path(temp_dir) / f"recording{suffix}"
        source_path.write_bytes(audio_bytes)
        audio_path = prepare_audio(source_path, temp_dir)
        segments, language, duration = transcribe_meeting(audio_path, expected_speakers, progress=lambda share, message: report(0.4 * share, message))
        if not segments:
            raise RuntimeError("В записи не обнаружена речь. Проверьте файл.")
        analysis, analysis_mode, analysis_note = analyze_meeting(segments, meeting_date.isoformat(), progress=lambda share, message: report(0.4 + 0.6 * share, message))
    bar.progress(1.0, text=t("done"))
    return {
        "title": meeting_title.strip() or t("meeting_title_default"),
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
    status = t("live_recording") if session.running else t("live_stopped")
    position = session.elapsed if session.running else session._position
    st.caption(f"{status} · {_stamp(position)} · {t('live_phrases')}: {len(session.lines)}")
    for line in session.lines[-12:]:
        st.markdown(f"<div class='live-line'><b>{_stamp(line['start'])}</b><code>{line['lang']}</code> {line['text']}</div>", unsafe_allow_html=True)
    if session.running and not session.lines:
        st.caption(t("live_wait"))
    if session.error:
        st.warning(session.error)


# --------------------------------------------------------------------------- pages


def page_new() -> None:
    st.markdown(
        f"""<div class="hero"><span class="logo">✨ Briefly AI</span><h1>{t('hero_title')}</h1><p>{t('hero_text')}</p>
<div class="steps"><span>{t('step1')}</span><span>{t('step2')}</span><span>{t('step3')}</span></div></div>""",
        unsafe_allow_html=True,
    )
    live_source = None
    with st.container(border=True):
        file_tab, record_tab, live_tab = st.tabs([t("tab_file"), t("tab_record"), t("tab_live")])
        with file_tab:
            upload = st.file_uploader(t("upload_label"), type=["wav", "mp3", "m4a", "ogg", "mp4", "mov", "mkv", "webm", "avi"], help=t("upload_help"))
        with record_tab:
            recorded = st.audio_input(t("record_label"))
        with live_tab:
            live_source = _live_tab()
        title_col, date_col, people_col = st.columns([2, 1, 1])
        meeting_title = title_col.text_input(t("meeting_title"), value=t("meeting_title_default"))
        meeting_date = date_col.date_input(t("meeting_date"), value=date.today(), help=t("meeting_date_help"))
        expected_speakers = people_col.number_input(t("participants_count"), min_value=0, max_value=30, value=0, help=t("participants_help"))
        consent = st.checkbox(t("consent"))
        source = upload or recorded or live_source
        create_col, example_col = st.columns([2, 1])
        start_processing = create_col.button(t("btn_create"), type="primary", disabled=not (source and consent), width="stretch")
        open_example = EXAMPLE_RESULT.is_file() and example_col.button(t("btn_example"), width="stretch")

    if open_example:
        _load_example()
        st.switch_page(PAGES["protocol"])
    if start_processing and source:
        progress = st.status(t("processing"), expanded=True)
        succeeded = False
        try:
            bar = progress.progress(0.0, text=t("preparing"))
            result = _process(source, meeting_title, meeting_date, int(expected_speakers) or None, bar)
            st.session_state["meeting_result"] = result
            st.session_state["speaker_names"] = _names_from(result["participants"])
            progress.update(label=t("done"), state="complete", expanded=False)
            succeeded = True
        except Exception as error:  # noqa: BLE001 - shown to the user
            progress.update(label=t("failed"), state="error", expanded=True)
            st.error(str(error))
        if succeeded:
            st.switch_page(PAGES["protocol"])
    st.caption(t("footer"))


def _live_tab() -> Any:
    ready, reason = live.available()
    if not ready:
        st.info(reason)
        return None
    st.caption(t("live_caption"))
    session = st.session_state.get("live_session")
    if session is None or not session.running:
        microphones = live.microphones()
        if not microphones:
            st.warning(t("no_mic"))
            return None
        default = next((i for i, (_, name) in enumerate(microphones) if "MacBook" in name or "Built-in" in name), 0)
        choice = st.selectbox(t("live_source"), microphones, index=default, format_func=lambda item: item[1])
        st.caption(t("live_zoom"))
        if st.button(f"▶ {t('live_start')}", type="primary"):
            new_session = live.LiveSession(device_index=choice[0])
            with st.spinner(t("live_calibrating")):
                new_session.start()
            st.session_state["live_session"] = new_session
            st.rerun()
    elif st.button(f"■ {t('live_stop')}", type="primary"):
        with st.spinner(t("live_finishing")):
            session.stop()
        st.rerun()
    if session is None:
        return None
    _live_panel()
    if session.running or not session.chunks:
        return None
    wav_path = Path(tempfile.gettempdir()) / f"briefly-live-{id(session)}.wav"
    session.save_wav(wav_path)
    wav_bytes = wav_path.read_bytes()
    st.audio(wav_bytes, format="audio/wav")
    pdf_bytes = build_transcript_pdf("Стенограмма (живая расшифровка)", date.today().isoformat(), [{"start": line["start"], "text": line["text"]} for line in session.lines])
    st.download_button(t("export_transcript"), data=pdf_bytes, file_name="briefly-live-transcript.pdf", mime="application/pdf")
    st.caption(t("live_after"))
    return SimpleNamespace(name="live.wav", getvalue=lambda data=wav_bytes: data)


def page_protocol() -> None:
    result = st.session_state.get("meeting_result")
    if not result:
        st.info(t("no_result"))
        if st.button(t("nav_new"), type="primary"):
            st.switch_page(PAGES["new"])
        return
    st.title(result["title"])
    languages = ", ".join(LANG_LABELS.get(code, code) for code in str(result["language"]).split("+") if code)
    st.markdown(
        f"<div class='chips'><span>📅 {result['date']}</span><span>⏱ {_stamp(result['duration'])}</span><span>🗣 {languages or '—'}</span>"
        f"<span>🤖 {result.get('model', '')}</span><span>🔒 local</span></div>",
        unsafe_allow_html=True,
    )
    if result.get("analysis_note"):
        st.warning(result["analysis_note"])

    speaker_names = st.session_state.setdefault("speaker_names", {})
    actions = [dict(item) for item in result["actions"]]
    for action in actions:
        action["speaker"] = speaker_names.get(action.get("speaker", ""), action.get("speaker", ""))
        for field, default in (("task", ""), ("assignee", "Не определён"), ("assigned_by", ""), ("deadline", "Не указан"), ("deadline_iso", ""), ("status", "В работе"), ("priority", "средний"), ("time", ""), ("source_quote", "")):
            action.setdefault(field, default)

    metric_cols = st.columns(4)
    metric_cols[0].metric(t("metric_tasks"), len(actions))
    metric_cols[1].metric(t("metric_people"), len(_speaker_ids(result["transcript"])))
    metric_cols[2].metric(t("metric_no_deadline"), sum(1 for row in actions if not str(row.get("deadline_iso", "")).strip()))
    metric_cols[3].metric(t("metric_duration"), _stamp(result["duration"]))

    if st.button(f"📝 {t('btn_summary')}"):
        st.session_state["show_summary"] = not st.session_state.get("show_summary", False)
    if st.session_state.get("show_summary"):
        with st.container(border=True):
            st.markdown(f"**{t('summary_title')}**")
            st.write(result["summary"] or "—")

    tasks_tab, summary_tab, people_tab, transcript_tab, export_tab, agent_tab = st.tabs(
        [t("tab_tasks"), t("tab_summary"), t("tab_people"), t("tab_transcript"), t("tab_export"), t("tab_agent")]
    )
    with people_tab:
        st.caption(t("people_caption"))
        for speaker in _speaker_ids(result["transcript"]):
            speaker_names[speaker] = st.text_input(speaker, value=speaker_names.get(speaker, speaker), key=f"name_{speaker}")

    with tasks_tab:
        current_actions = _tasks_tab(result, actions)
    with summary_tab:
        st.write(result["summary"] or "—")
        if result.get("summary_items"):
            st.dataframe(pd.DataFrame(result["summary_items"]).rename(columns={"topic": "Направление / доклад", "indicator": "Показатель", "problem": "Проблема"}), hide_index=True, width="stretch")
    final_transcript = [{**item, "speaker": speaker_names.get(item.get("speaker", ""), item.get("speaker", ""))} for item in result["transcript"]]
    with transcript_tab:
        st.dataframe(
            pd.DataFrame([{t("col_time"): f"{_stamp(item['start'])}–{_stamp(item['end'])}", t("col_speaker"): item["speaker"], t("col_lang"): LANG_LABELS.get(item.get("lang", ""), item.get("lang", "")), t("col_text"): item["text"]} for item in final_transcript]),
            hide_index=True,
            width="stretch",
        )
        st.download_button(t("export_transcript"), data=build_transcript_pdf(result["title"], result["date"], final_transcript), file_name="briefly-transcript.pdf", mime="application/pdf")
    with export_tab:
        _export_tab(result, current_actions, final_transcript, speaker_names)
    with agent_tab:
        st.caption(t("agent_caption"))
        if result.get("agent_trace"):
            st.dataframe(pd.DataFrame(result["agent_trace"]).rename(columns={"step": "Шаг", "seconds": "Секунд", "result": "Результат"}), hide_index=True, width="stretch")


def _tasks_tab(result: dict[str, Any], actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with st.expander(f"✏️ {t('edit_tasks')}"):
        columns = ["task", "assignee", "deadline", "deadline_iso", "status", "priority", "assigned_by", "time", "source_quote"]
        edited = st.data_editor(
            pd.DataFrame(actions, columns=columns),
            hide_index=True,
            num_rows="dynamic",
            width="stretch",
            column_config={
                "task": st.column_config.TextColumn("Поручение", width="large"),
                "assignee": st.column_config.TextColumn(t("responsible")),
                "deadline": st.column_config.TextColumn(t("deadline")),
                "deadline_iso": st.column_config.TextColumn("YYYY-MM-DD"),
                "status": st.column_config.SelectboxColumn(t("status"), options=["В работе", "Просрочено", "Выполнено"]),
                "priority": st.column_config.SelectboxColumn("Срочность", options=["высокий", "средний", "низкий"]),
                "assigned_by": st.column_config.TextColumn(t("assigned_by")),
                "time": st.column_config.TextColumn(t("col_time")),
                "source_quote": st.column_config.TextColumn(t("quote"), width="large"),
            },
            key="actions_editor",
        )
    current = edited.fillna("").to_dict(orient="records")
    if st.button(f"➕ {t('add_all')}"):
        added = sum(registry.add(row, result["title"], result["date"]) for row in current)
        st.toast(t("added_n", n=added))
    for flag in result.get("flags", []):
        st.warning(flag, icon="⚠️")
    for index, row in enumerate(current):
        status = registry.effective_status({"status": STATUS_FROM_ACTION.get(str(row.get("status")), "progress"), "deadline_iso": row.get("deadline_iso", "")})
        with st.container(border=True):
            text_col, button_col = st.columns([5, 1.4])
            deadline = row.get("deadline") or t("no_deadline")
            if row.get("deadline_iso"):
                deadline = f"{deadline} ({row['deadline_iso']})"
            text_col.markdown(
                f"{_badge(status)}<div class='task-title'>{row.get('task', '')}</div>"
                f"<div class='task-meta'>👤 {t('responsible')}: <b>{row.get('assignee', '')}</b> · 📅 {t('deadline')}: {deadline}"
                f" · 🗣 {t('assigned_by')}: {row.get('assigned_by', '') or '—'} · ⏱ {row.get('time', '') or '—'}</div>",
                unsafe_allow_html=True,
            )
            if row.get("source_quote"):
                text_col.caption(f"«{row['source_quote']}»")
            if registry.contains(row, result["title"], result["date"]):
                button_col.button(t("in_registry"), key=f"reg_{index}", disabled=True, width="stretch")
            elif button_col.button(t("add_to_registry"), key=f"reg_{index}", type="primary", width="stretch"):
                registry.add(row, result["title"], result["date"])
                st.toast(t("added"))
                st.rerun()
    if result.get("audio") and current:
        with st.expander(f"🔊 {t('listen')}"):
            labels = [f"{row['time'] or '00:00'} — {row['task']}" for row in current]
            choice = st.selectbox(t("tab_tasks"), options=range(len(labels)), format_func=lambda i: labels[i])
            st.audio(result["audio"], format=result.get("audio_format", "audio/mpeg"), start_time=_seconds(current[choice]["time"]))
    return current


def _export_tab(result: dict[str, Any], current_actions: list[dict[str, Any]], final_transcript: list[dict[str, Any]], speaker_names: dict[str, str]) -> None:
    participants = [{"speaker": speaker, "name": speaker_names.get(speaker, speaker)} for speaker in _speaker_ids(result["transcript"])]
    docx_bytes = build_docx(result["title"], result["date"], result["summary"], current_actions, final_transcript, participants=participants, summary_items=result.get("summary_items"))
    pdf_bytes = build_pdf(result["title"], result["date"], result["summary"], current_actions, final_transcript, participants=participants, summary_items=result.get("summary_items"))
    csv_columns = {"task": "Поручение", "assignee": "Ответственный", "deadline": "Срок", "deadline_iso": "Срок (дата)", "status": "Статус", "priority": "Срочность", "assigned_by": "Кто поручил", "time": "Время", "source_quote": "Цитата"}
    csv_actions = pd.DataFrame(current_actions, columns=csv_columns).rename(columns=csv_columns)
    csv_actions = csv_actions.map(lambda value: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value)
    safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in result["title"]).strip("_") or "meeting"
    st.caption(t("export_caption"))
    docx_col, pdf_col = st.columns(2)
    docx_col.download_button(f"📄 {t('export_docx')}", data=docx_bytes, file_name=f"{safe_name}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", width="stretch", type="primary")
    pdf_col.download_button(f"📕 {t('export_pdf')}", data=pdf_bytes, file_name=f"{safe_name}.pdf", mime="application/pdf", width="stretch")
    transcript_col, csv_col = st.columns(2)
    transcript_col.download_button(f"💬 {t('export_transcript')}", data=build_transcript_pdf(result["title"], result["date"], final_transcript), file_name=f"{safe_name}-transcript.pdf", mime="application/pdf", width="stretch")
    csv_col.download_button(f"📊 {t('export_csv')}", data=csv_actions.to_csv(index=False).encode("utf-8-sig"), file_name="porucheniya.csv", mime="text/csv", width="stretch")


def page_registry() -> None:
    st.title(t("nav_registry"))
    st.caption(t("registry_caption"))
    tasks = registry.load()
    if not tasks:
        st.info(t("registry_empty"))
        return
    counts = {status: sum(registry.effective_status(task) == status for task in tasks) for status in registry.STATUSES}
    count_cols = st.columns(3)
    for column, status in zip(count_cols, ("progress", "done", "failed")):
        column.markdown(f"{_badge(status)}", unsafe_allow_html=True)
        column.metric(t("status_" + status), counts[status], label_visibility="collapsed")
    options = ["all", "progress", "done", "failed"]
    chosen = st.radio(t("filter_status"), options, horizontal=True, format_func=lambda item: t("all") if item == "all" else t("status_" + item))
    for task in tasks:
        status = registry.effective_status(task)
        if chosen != "all" and status != chosen:
            continue
        with st.container(border=True):
            info_col, status_col, delete_col = st.columns([5, 1.6, 0.8])
            deadline = task.get("deadline") or t("no_deadline")
            if task.get("deadline_iso"):
                deadline = f"{deadline} ({task['deadline_iso']})"
            info_col.markdown(
                f"{_badge(status)}<div class='task-title'>{task.get('task', '')}</div>"
                f"<div class='task-meta'>👤 {task.get('assignee', '')} · 📅 {deadline} · 🗂 {t('meeting')}: {task.get('meeting', '')} ({task.get('meeting_date', '')})</div>",
                unsafe_allow_html=True,
            )
            new_status = status_col.selectbox(t("status"), registry.STATUSES, index=registry.STATUSES.index(status), format_func=lambda item: t("status_" + item), key=f"status_{task['id']}_{st.session_state['lang']}")
            if new_status != status:
                registry.set_status(task["id"], new_status)
                st.rerun()
            if delete_col.button("🗑", key=f"del_{task['id']}", help=t("delete")):
                registry.remove(task["id"])
                st.rerun()


def page_about() -> None:
    st.title(t("about_title"))
    st.write(t("about_text"))
    with st.container(border=True):
        st.subheader(f"🎥 {t('zoom_title')}")
        st.markdown(t("zoom_text"))
    with st.container(border=True):
        st.subheader(f"🧠 {t('models_title')}")
        st.markdown(
            "- Диаризация: sherpa-onnx (pyannote segmentation 3.0 + 3D-Speaker), без токенов\n"
            "- Распознавание: whisper-turbo-kaz-rus (казахский и шала) и whisper-large-v3-turbo (русский)\n"
            "- Живая расшифровка: SpeechRecognition (захват микрофона) + локальная модель\n"
            "- ИИ-агент поручений: Ollama qwen3:4b, строгая JSON-схема, самопроверка, пересчёт сроков кодом\n"
            "- Выгрузка: DOCX (python-docx), PDF (reportlab, шрифт DejaVu)"
        )


PAGES = {
    "new": st.Page(page_new, title=t("nav_new"), icon="🎙️", url_path="new", default=True),
    "protocol": st.Page(page_protocol, title=t("nav_protocol"), icon="📄", url_path="protocol"),
    "registry": st.Page(page_registry, title=t("nav_registry"), icon="✅", url_path="registry"),
    "about": st.Page(page_about, title=t("nav_about"), icon="ℹ️", url_path="about"),
}

with st.sidebar:
    st.markdown("### ✨ Briefly AI")
    st.caption(t("sidebar_tagline"))
navigation = st.navigation(list(PAGES.values()), position="sidebar")
with st.sidebar:
    st.divider()
    current_lang = st.session_state["lang"]
    language = st.selectbox(t("language"), list(LANGUAGES), index=list(LANGUAGES).index(current_lang), format_func=lambda code: LANGUAGES[code], key=f"lang_{current_lang}")
    theme = st.radio(t("theme"), ["light", "dark"], index=["light", "dark"].index(st.session_state["theme"]), horizontal=True, format_func=lambda mode: t("theme_" + mode), key=f"theme_{current_lang}")
    st.caption(f"🔒 {t('sidebar_privacy')}")
    if language != st.session_state["lang"] or theme != st.session_state["theme"]:
        st.session_state["lang"], st.session_state["theme"] = language, theme
        _apply_theme(theme)
        st.rerun()
navigation.run()
