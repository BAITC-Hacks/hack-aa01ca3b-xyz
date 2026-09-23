"""Download local model weights once; the app itself runs with network disabled."""

from __future__ import annotations

import os
from pathlib import Path

from faster_whisper import WhisperModel
from huggingface_hub import snapshot_download


ROOT = Path(__file__).resolve().parents[1]
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
DIARIZATION_MODEL = os.getenv("DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1")
WHISPER_CACHE = ROOT / "models" / "whisper-cache"
DIARIZATION_PATH = ROOT / "models" / "pyannote-speaker-diarization-community-1"


def main() -> None:
    token = os.getenv("HF_TOKEN")
    print(f"Загрузка локальной модели Whisper: {WHISPER_MODEL}")
    WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", download_root=str(WHISPER_CACHE), local_files_only=False)
    print(f"Загрузка локальной модели диаризации: {DIARIZATION_MODEL}")
    snapshot_download(repo_id=DIARIZATION_MODEL, local_dir=str(DIARIZATION_PATH), token=token)
    print("Модели загружены. Теперь приложение может работать без доступа к сети.")


if __name__ == "__main__":
    main()
