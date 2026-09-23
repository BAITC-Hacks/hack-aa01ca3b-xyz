"""FastAPI adapter for the local HackAlem processing services."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.exporter import build_docx, build_pdf
from app.services.extractor import analyze_meeting, transcribe_meeting
from app.services.media import prepare_audio


app = FastAPI(
    title="Хаттама API",
    description="Локальная обработка записей совещаний без внешних ИИ API.",
    version="1.0.0",
)

ALLOWED_SUFFIXES = {".wav", ".mp3", ".m4a", ".ogg", ".mp4", ".mov", ".mkv", ".webm", ".avi"}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


class ExportRequest(BaseModel):
    format: str
    title: str
    date: str
    summary: str = ""
    actions: list[dict[str, Any]] = Field(default_factory=list)
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    participants: list[dict[str, Any]] = Field(default_factory=list)
    summary_items: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "Хаттама API"}


@app.post("/api/meetings/process")
def process_meeting(
    file: UploadFile = File(...),
    title: str = Form("Совещание"),
    meeting_date: str = Form(...),
    expected_speakers: int = Form(0),
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

            audio_path = prepare_audio(source_path, temp_dir)
            segments, language, duration = transcribe_meeting(audio_path, expected_speakers or None)
            analysis, analysis_mode, analysis_note = analyze_meeting(segments, parsed_date.isoformat())

        return {
            "title": title.strip() or "Протокол совещания",
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
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"Не удалось обработать запись: {error}") from error
    finally:
        file.file.close()


@app.post("/api/meetings/export")
def export_meeting(request: ExportRequest) -> Response:
    """Build a PDF or DOCX from the reviewed result sent by the browser."""
    if request.format not in {"pdf", "docx"}:
        raise HTTPException(status_code=400, detail="Формат экспорта должен быть pdf или docx.")
    builder = build_pdf if request.format == "pdf" else build_docx
    content = builder(
        request.title,
        request.date,
        request.summary,
        request.actions,
        request.transcript,
        participants=request.participants,
        summary_items=request.summary_items,
    )
    media_type = "application/pdf" if request.format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    filename = "meeting.pdf" if request.format == "pdf" else "meeting.docx"
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
