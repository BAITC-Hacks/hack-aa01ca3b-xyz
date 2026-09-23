"""Local-first meeting transcription and minutes assistant."""

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Apply KEY=VALUE pairs from the repository .env without overriding real environment variables."""
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if sep and key and value and not key.startswith("#"):
            os.environ.setdefault(key, value)


_load_dotenv()
