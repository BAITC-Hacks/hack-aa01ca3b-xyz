"""Download local model weights once; the app itself then runs with the network disabled.

No accounts or tokens are needed for the default pipeline:
* speech recognition: openai/whisper-large-v3-turbo + abilmansplus/whisper-turbo-ksc2
  with the abilmansplus/whisper-turbo-kaz-rus-v1 LoRA adapter (Hugging Face, public);
* speaker diarization: sherpa-onnx pyannote-segmentation-3.0 + 3D-Speaker embeddings
  (GitHub releases of k2-fsa/sherpa-onnx);
* fallback recognizer: faster-whisper `small`.
Optional: pyannote Community-1 diarization, only when HF_TOKEN is set.
"""

from __future__ import annotations

import os
import tarfile
import urllib.request
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import hf_hub_download, snapshot_download  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
SHERPA_DIR = MODELS / "sherpa"
SHERPA_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_ARCHIVE = f"{SHERPA_RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDING_FILE = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
EMBEDDING_URL = f"{SHERPA_RELEASES}/speaker-recongition-models/{EMBEDDING_FILE}"

HF_FILES = {
    "openai/whisper-large-v3-turbo": [
        "config.json", "generation_config.json", "preprocessor_config.json", "tokenizer.json", "tokenizer_config.json",
        "vocab.json", "merges.txt", "normalizer.json", "added_tokens.json", "special_tokens_map.json", "model.safetensors",
    ],
    "abilmansplus/whisper-turbo-kaz-rus-v1": ["adapter_config.json", "adapter_model.safetensors"],
    "abilmansplus/whisper-turbo-ksc2": ["config.json", "generation_config.json", "model.safetensors"],
}


def download_sherpa() -> None:
    SHERPA_DIR.mkdir(parents=True, exist_ok=True)
    if not (SHERPA_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx").is_file():
        archive = SHERPA_DIR / "segmentation.tar.bz2"
        print("Диаризация: сегментация pyannote-3.0 (ONNX)…")
        urllib.request.urlretrieve(SEGMENTATION_ARCHIVE, archive)
        with tarfile.open(archive) as bundle:
            bundle.extractall(SHERPA_DIR)
        archive.unlink()
    if not (SHERPA_DIR / EMBEDDING_FILE).is_file():
        print("Диаризация: эмбеддинги голоса 3D-Speaker (ONNX)…")
        urllib.request.urlretrieve(EMBEDDING_URL, SHERPA_DIR / EMBEDDING_FILE)


def download_whisper() -> None:
    for repo_id, files in HF_FILES.items():
        print(f"Распознавание речи: {repo_id}…")
        for filename in files:
            hf_hub_download(repo_id, filename)


def download_fallback() -> None:
    from faster_whisper import WhisperModel

    model = os.getenv("WHISPER_MODEL", "small")
    print(f"Запасной распознаватель faster-whisper: {model}…")
    WhisperModel(model, device="cpu", compute_type="int8", download_root=str(MODELS / "whisper-cache"), local_files_only=False)


def download_pyannote() -> None:
    token = os.getenv("HF_TOKEN")
    if not token:
        return
    repo_id = os.getenv("DIARIZATION_MODEL", "pyannote/speaker-diarization-community-1")
    print(f"Необязательная диаризация pyannote: {repo_id}…")
    snapshot_download(repo_id=repo_id, local_dir=str(MODELS / "pyannote-speaker-diarization-community-1"), token=token)


def main() -> None:
    download_sherpa()
    download_whisper()
    download_fallback()
    download_pyannote()
    print("Модели загружены. Теперь приложение работает без доступа к сети. Не забудьте: `ollama pull qwen3:4b`.")


if __name__ == "__main__":
    main()
