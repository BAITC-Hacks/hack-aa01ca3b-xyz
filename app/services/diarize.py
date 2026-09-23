"""Token-free local speaker diarization with sherpa-onnx.

Uses the pyannote segmentation-3.0 model exported to ONNX plus a 3D-Speaker
embedding model. Both are downloaded once by `scripts/download_models.py`
from the sherpa-onnx GitHub releases; no Hugging Face account is needed.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.core.config import MODELS_DIR

SHERPA_DIR = Path(os.getenv("SHERPA_MODELS_DIR", MODELS_DIR / "sherpa"))
SEGMENTATION_MODEL = SHERPA_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBEDDING_MODEL = SHERPA_DIR / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"


def available() -> bool:
    return SEGMENTATION_MODEL.is_file() and EMBEDDING_MODEL.is_file()


@lru_cache(maxsize=4)
def _pipeline(num_speakers: int, threshold: float):
    import sherpa_onnx

    threads = int(os.getenv("DIARIZATION_THREADS", str(min(8, os.cpu_count() or 4))))
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEGMENTATION_MODEL)),
            num_threads=threads,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMBEDDING_MODEL), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers, threshold=threshold),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Некорректная конфигурация диаризации sherpa-onnx. Проверьте файлы в models/sherpa.")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


def diarize_waveform(waveform: np.ndarray, expected_speakers: int | None = None) -> list[tuple[float, float, str]]:
    """Return (start, end, label) speaker turns for a 16 kHz mono waveform."""
    if not available():
        raise FileNotFoundError(
            f"Не найдены модели диаризации в {SHERPA_DIR}. Выполните `python scripts/download_models.py`."
        )
    threshold = float(os.getenv("DIARIZATION_THRESHOLD", "0.5"))
    pipeline = _pipeline(int(expected_speakers or -1), threshold)
    samples = np.ascontiguousarray(waveform, dtype=np.float32)
    result = pipeline.process(samples).sort_by_start_time()
    return [(float(item.start), float(item.end), f"spk{int(item.speaker)}") for item in result]
