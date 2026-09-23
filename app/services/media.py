"""Local-only media preparation helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def prepare_audio(source: str | Path, work_dir: str | Path) -> Path:
    """Extract a mono 16 kHz WAV from video, or pass an audio file through."""
    source = Path(source)
    if source.suffix.lower() not in VIDEO_SUFFIXES:
        return source
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Для обработки видео установите ffmpeg и добавьте его в PATH.")
    output = Path(work_dir) / "meeting-audio.wav"
    command = [ffmpeg, "-nostdin", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600, check=False)
    if result.returncode != 0 or not output.exists():
        details = result.stderr[-1200:].strip()
        raise RuntimeError(f"Не удалось извлечь аудио из видео. {details}")
    return output
