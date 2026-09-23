"""Live meeting transcription from the microphone.

SpeechRecognition (https://github.com/Uberi/speech_recognition) captures the microphone and cuts
speech into phrases by pauses (energy-based voice activity detection with ambient-noise calibration).
Recognition stays local: every phrase goes to the same Kazakh/Russian Whisper models as uploaded
recordings. The library's cloud recognizers (Google, Azure, Wit, …) are never called — the task
forbids sending audio outside the customer's perimeter.
"""

from __future__ import annotations

import queue
import threading
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

SAMPLE_RATE = 16_000


def available() -> tuple[bool, str]:
    """(True, "") when SpeechRecognition and PyAudio are installed."""
    try:
        import pyaudio  # noqa: F401
        import speech_recognition  # noqa: F401
    except ImportError as exc:
        return False, f"Для живой расшифровки: pip install SpeechRecognition pyaudio (macOS: brew install portaudio). ({exc})"
    return True, ""


def microphones() -> list[tuple[int, str]]:
    """Input devices as (index, name)."""
    import pyaudio

    audio = pyaudio.PyAudio()
    try:
        devices = [audio.get_device_info_by_index(index) for index in range(audio.get_device_count())]
        return [(int(info["index"]), str(info["name"])) for info in devices if int(info.get("maxInputChannels", 0)) > 0]
    finally:
        audio.terminate()


class LiveSession:
    """Microphone → phrases (SpeechRecognition) → local recognition in a worker thread."""

    def __init__(self, device_index: int | None = None, phrase_limit: float = 15.0) -> None:
        self.device_index = device_index
        self.phrase_limit = phrase_limit
        self.lines: list[dict[str, Any]] = []
        self.chunks: list[np.ndarray] = []
        self.error = ""
        self.started = 0.0
        self.running = False
        self._queue: queue.Queue[tuple[float, np.ndarray]] = queue.Queue()
        self._stop_listening: Any = None
        self._worker: threading.Thread | None = None
        self._position = 0.0

    def start(self) -> None:
        import speech_recognition as sr

        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 0.8
        recognizer.dynamic_energy_threshold = True
        microphone = sr.Microphone(device_index=self.device_index, sample_rate=SAMPLE_RATE)
        with microphone as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.8)
        self.started = time.time()
        self.running = True
        self._worker = threading.Thread(target=self._recognize_loop, daemon=True)
        self._worker.start()
        self._stop_listening = recognizer.listen_in_background(microphone, self._on_phrase, phrase_time_limit=self.phrase_limit)

    def _on_phrase(self, _recognizer: Any, audio: Any) -> None:
        raw = audio.get_raw_data(convert_rate=SAMPLE_RATE, convert_width=2)
        self._queue.put((time.time() - self.started, np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0))

    def _recognize_loop(self) -> None:
        from . import stt

        try:
            stt.transcribe_phrase(np.zeros(SAMPLE_RATE, dtype=np.float32))  # warm up the model before the first phrase
        except Exception as exc:  # noqa: BLE001 - shown to the user in the interface
            self.error = str(exc)
        while self.running or not self._queue.empty():
            try:
                _, samples = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            start = self._position
            self.chunks.append(samples)
            self._position += len(samples) / SAMPLE_RATE + 0.3
            try:
                text, language = stt.transcribe_phrase(samples)
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                continue
            if text:
                self.lines.append({"start": round(start, 1), "end": round(start + len(samples) / SAMPLE_RATE, 1), "text": text, "lang": language})

    def stop(self) -> None:
        if self._stop_listening:
            self._stop_listening(wait_for_stop=False)
        self.running = False
        if self._worker:
            self._worker.join(timeout=120)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started if self.started else 0.0

    def save_wav(self, path: str | Path) -> Path:
        """All captured phrases with 0.3 s gaps — the recording the full protocol is built from."""
        gap = np.zeros(int(0.3 * SAMPLE_RATE), dtype=np.float32)
        joined = np.concatenate([part for chunk in self.chunks for part in (chunk, gap)]) if self.chunks else gap
        path = Path(path)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(SAMPLE_RATE)
            output.writeframes((np.clip(joined, -1, 1) * 32767).astype(np.int16).tobytes())
        return path
