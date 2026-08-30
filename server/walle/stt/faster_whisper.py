# SPDX-License-Identifier: MIT
"""faster-whisper backend (CTranslate2).

An in-process alternative to the whisper.cpp subprocess. Usually the better
choice on a Linux box with an NVIDIA GPU, where CTranslate2's CUDA path beats
whisper.cpp comfortably. On Apple Silicon, whisper.cpp with Metal is still
ahead, which is why it remains the default.

Optional dependency: ``pip install faster-whisper``.
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..audio import PcmArray
from ..config import SttConfig
from .base import SttEngine, Transcript

log = logging.getLogger(__name__)


class FasterWhisperStt(SttEngine):
    name = "faster-whisper"

    def __init__(self, cfg: SttConfig) -> None:
        self.cfg = cfg
        self._model = None
        self._lock = asyncio.Lock()

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "faster-whisper is not installed. Either `pip install faster-whisper` "
                "or set WALLE_STT_BACKEND=whispercpp."
            ) from exc

        # int8 quantisation on CPU is the single biggest speed win available
        # here and costs almost nothing in accuracy for short commands.
        self._model = WhisperModel(
            self.cfg.model_path,
            device="auto",
            compute_type="int8",
            cpu_threads=self.cfg.threads,
        )
        return self._model

    def _transcribe_sync(self, pcm_f32, sample_rate: int) -> tuple[str, int]:
        model = self._ensure_model()
        segments, _info = model.transcribe(
            pcm_f32,
            language=self.cfg.language,
            beam_size=max(1, self.cfg.beam_size),
            # faster-whisper's built-in VAD trims leading/trailing silence,
            # which is free latency: less audio to decode.
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            initial_prompt=self.cfg.initial_prompt or None,
            condition_on_previous_text=False,
        )
        parts = [seg.text.strip() for seg in segments]
        return " ".join(p for p in parts if p).strip(), len(parts)

    async def transcribe(self, samples: PcmArray, sample_rate: int) -> Transcript:
        if not samples:
            return Transcript(text="")
        started = time.monotonic()

        # faster-whisper wants float32 in [-1, 1].
        pcm_f32 = [s / 32768.0 for s in samples]

        async with self._lock:
            try:
                text, segment_count = await asyncio.wait_for(
                    asyncio.to_thread(self._transcribe_sync, pcm_f32, sample_rate),
                    timeout=self.cfg.timeout_s,
                )
            except asyncio.TimeoutError:
                log.error("faster-whisper timed out after %.1fs", self.cfg.timeout_s)
                return Transcript(text="", meta={"error": "timeout"})
            except RuntimeError as exc:
                log.error("%s", exc)
                return Transcript(text="", meta={"error": "unavailable"})

        latency = int((time.monotonic() - started) * 1000)
        log.info("stt: %d ms -> %r", latency, text)
        return Transcript(text=text, language=self.cfg.language,
                          latency_ms=latency, meta={"segments": segment_count})

    async def warmup(self) -> None:
        try:
            await asyncio.to_thread(self._ensure_model)
            log.info("faster-whisper model loaded")
        except Exception:  # pragma: no cover
            log.warning("faster-whisper warmup failed", exc_info=True)
