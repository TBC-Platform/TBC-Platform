# SPDX-License-Identifier: MIT
"""Piper TTS backend.

Piper is a small VITS model that runs comfortably on CPU: a one-sentence reply
renders in roughly 150-300 ms on a Mac Mini, which is fast enough that we can
just wait for the whole clip rather than streaming partial audio.

The one trick worth knowing is *sentence splitting*. Piper's latency scales
with the length of the text, so a three-sentence answer rendered as one blob
means the robot stays silent for a second. Rendering sentence by sentence and
streaming each one as it finishes cuts time-to-first-word to the cost of the
first sentence alone - typically under 200 ms.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from array import array
from pathlib import Path

from ..audio import PcmArray, pcm_from_wav, peak_normalize, resample_linear, robot_voice
from ..config import TtsConfig
from ..protocol import AUDIO_SAMPLE_RATE
from .base import Speech, TtsEngine

log = logging.getLogger(__name__)

# Split on sentence enders, keeping the punctuation with the sentence so Piper
# still gets the prosody cue.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str, max_chars: int = 220) -> list[str]:
    """Splits text into speakable chunks.

    Long clauses without punctuation get broken at a comma, then at a hard
    character limit, so one runaway sentence cannot stall playback.
    """
    text = " ".join(text.split())
    if not text:
        return []

    chunks: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > max_chars:
            cut = sentence.rfind(",", 0, max_chars)
            if cut < max_chars // 3:
                cut = sentence.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].lstrip(", ")
        if sentence:
            chunks.append(sentence)
    return chunks


class PiperTts(TtsEngine):
    name = "piper"

    def __init__(self, cfg: TtsConfig) -> None:
        self.cfg = cfg
        self._binary = shutil.which(cfg.binary) or cfg.binary
        self._model = str(Path(cfg.model_path).expanduser())
        self._lock = asyncio.Lock()

    async def synthesize(self, text: str) -> Speech:
        """Renders the whole text. Prefer :meth:`stream` for live replies."""
        started = time.monotonic()
        combined = array("h")
        async for chunk in self.stream(text):
            combined.extend(chunk.samples)
        return Speech(
            samples=combined,
            sample_rate=AUDIO_SAMPLE_RATE,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def stream(self, text: str):
        """Yields one :class:`Speech` per sentence, as soon as it is ready.

        This is what makes the robot start talking quickly on long answers.
        """
        for sentence in split_sentences(text):
            speech = await self._render_one(sentence)
            if speech.samples:
                yield speech

    async def _render_one(self, sentence: str) -> Speech:
        started = time.monotonic()
        cmd = [
            self._binary,
            "--model", self._model,
            "--output_file", "-",          # WAV on stdout
            "--length_scale", str(self.cfg.length_scale),
            "--noise_scale", str(self.cfg.noise_scale),
        ]

        async with self._lock:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(sentence.encode("utf-8")),
                    timeout=self.cfg.timeout_s,
                )
            except FileNotFoundError:
                log.error("piper binary not found: %s", self._binary)
                return Speech(samples=array("h"), sample_rate=AUDIO_SAMPLE_RATE)
            except asyncio.TimeoutError:
                log.error("piper timed out after %.1fs", self.cfg.timeout_s)
                return Speech(samples=array("h"), sample_rate=AUDIO_SAMPLE_RATE)

        if proc.returncode != 0 or not stdout:
            log.error(
                "piper exited %s: %s",
                proc.returncode,
                stderr.decode("utf-8", "replace").strip()[-300:],
            )
            return Speech(samples=array("h"), sample_rate=AUDIO_SAMPLE_RATE)

        samples, rate = pcm_from_wav(stdout)
        samples = self._post_process(samples, rate)
        latency = int((time.monotonic() - started) * 1000)
        log.debug("tts: %d ms for %d chars", latency, len(sentence))
        return Speech(samples=samples, sample_rate=AUDIO_SAMPLE_RATE, latency_ms=latency)

    def _post_process(self, samples: PcmArray, rate: int) -> PcmArray:
        # Order matters: resample first (cheapest on the shortest array),
        # then normalise, then apply the effect so the robot buzz is not
        # amplified by the normaliser.
        samples = resample_linear(samples, rate, AUDIO_SAMPLE_RATE)
        samples = peak_normalize(samples, target_peak=0.85)
        if self.cfg.robot_effect > 0:
            samples = robot_voice(samples, AUDIO_SAMPLE_RATE, self.cfg.robot_effect)
        return samples

    async def warmup(self) -> None:
        try:
            speech = await self._render_one("Ready.")
            if speech.samples:
                log.info("tts warmed up (%s), %d ms", Path(self._model).name, speech.latency_ms)
            else:
                log.warning("tts warmup produced no audio - check the Piper model path")
        except Exception:  # pragma: no cover
            log.warning("tts warmup failed", exc_info=True)
