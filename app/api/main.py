"""FastAPI adapter for the local HackAlem processing services."""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.api.schemas import MeetingExportRequest
from app.services.exporter import build_docx, build_pdf
from app.services.extractor import analyze_meeting, transcribe_meeting
from app.services.media import prepare_audio
from app.services import stt


app = FastAPI(
    title="Хаттама API",
    description="Локальная обработка записей совещаний без внешних ИИ API.",
    version="1.0.0",
)

ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".ogg", ".mp4", ".mov", ".mkv", ".webm", ".avi"}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_CONCURRENT_JOBS = 1
_processing_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness check; does not load large ML models."""
    return {"status": "ok", "service": "Хаттама API"}


@app.get("/api/health/ready")
def readiness() -> dict[str, Any]:
    """Report local model availability without starting model downloads."""
    try:
        speech_models = {"kazakh_mixed": stt.kz_available(), "russian": stt.base_available()}
    except Exception:
        speech_models = {"kazakh_mixed": False, "russian": False}
    ready = any(speech_models.values())
    return {
        "status": "ready" if ready else "setup_required",
        "speech_models": speech_models,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "note": None if ready else "Скачайте локальные модели командой python scripts/download_models.py.",
    }


@app.post("/api/meetings/process")
def process_meeting(
    file: UploadFile = File(...),
    title: str = Form("Совещание"),
    meeting_date: str = Form(...),
    expected_speakers: int = Form(0, ge=0, le=30),
    consent: bool = Form(False),
) -> dict[str, Any]:
    """Process one recording and return a reviewable transcript and action list."""
    if not consent:
        raise HTTPException(status_code=400, detail="Подтвердите уведомление участников о записи.")
    suffix = Path(file.filename or "recording.wav").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail="Формат записи не поддерживается.")
    try:
        parsed_date = date.fromisoformat(meeting_date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Дата должна быть в формате YYYY-MM-DD.") from error
    if not _processing_slots.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Сервер уже обрабатывает другую запись. Повторите позже.")

    try:
        with tempfile.TemporaryDirectory(prefix="khatta-api-") as temp_dir:
            source_path = Path(temp_dir) / f"recording{suffix}"
            total = 0
            with source_path.open("wb") as destination:
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Размер записи превышает 512 МБ.")
                    destination.write(chunk)
            if total == 0:
                raise HTTPException(status_code=400, detail="Загруженный файл пуст.")

            audio_path = prepare_audio(source_path, temp_dir)
            segments, language, duration = transcribe_meeting(audio_path, expected_speakers or None)
            analysis, analysis_mode, analysis_note = analyze_meeting(segments, parsed_date.isoformat())

        return {
            "title": title.strip()[:160] or "Протокол совещания",
            "date": parsed_date.isoformat(),
            "language": language,
            "duration": duration,
            "transcript": segments,
            "summary": analysis.get("summary", ""),
            "summary_items": analysis.get("summary_items", []),
            "participants": analysis.get("participants", []),
            "actions": analysis.get("actions", []),
            "flags": analysis.get("flags", []),
            "agent_trace": analysis.get("agent_trace", []),
            "analysis_mode": analysis_mode,
            "analysis_note": analysis_note,
        }
    except HTTPException:
        raise
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail="Не найдены локальные модели обработки. Скачайте модели по инструкции README.") from error
    except TimeoutError as error:
        raise HTTPException(status_code=504, detail="Обработка превысила допустимое время ожидания.") from error
    except Exception as error:
        # Keep machine paths and decoder internals in server logs, not in client responses.
        logging.getLogger(__name__).exception("Meeting processing failed")
        raise HTTPException(status_code=422, detail="Не удалось обработать запись. Проверьте формат файла и журналы сервера.") from error
    finally:
        try:
            file.file.close()
        finally:
            _processing_slots.release()


@app.post("/api/meetings/export")
def export_meeting(request: MeetingExportRequest) -> Response:
    """Build a PDF or DOCX from the reviewed result sent by the browser."""
    builder = build_pdf if request.format == "pdf" else build_docx
    transcript = [segment.model_dump() for segment in request.transcript]
    content = builder(
        request.title,
        request.date.isoformat(),
        request.summary,
        request.actions,
        transcript,
        participants=request.participants,
        summary_items=request.summary_items,
    )
    media_type = "application/pdf" if request.format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    filename = "meeting.pdf" if request.format == "pdf" else "meeting.docx"
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
