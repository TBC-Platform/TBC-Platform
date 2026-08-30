# SPDX-License-Identifier: MIT
"""whisper.cpp backend - the default, and the fastest CPU option on a Mac Mini.

We drive the ``whisper-cli`` binary as a subprocess rather than binding the
library. That sounds slower than it is: process startup is ~15 ms, and in
exchange we get crash isolation (a bad audio buffer cannot take the server
down), trivial installation, and the ability to swap in a new whisper.cpp
build without touching Python.

The audio is piped through a temporary WAV file because whisper.cpp's stdin
support has historically been build-dependent; a file in /tmp on a modern SSD
costs well under a millisecond.

See docs/03-research-notes.md for the flag-by-flag reasoning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from ..audio import PcmArray, wav_from_pcm
from ..config import SttConfig
from .base import SttEngine, Transcript

log = logging.getLogger(__name__)


class WhisperCppStt(SttEngine):
    name = "whisper.cpp"

    def __init__(self, cfg: SttConfig) -> None:
        self.cfg = cfg
        self._binary = shutil.which(cfg.binary) or cfg.binary
        self._model = str(Path(cfg.model_path).expanduser())
        self._lock = asyncio.Lock()

    def _build_command(self, wav_path: str, out_prefix: str) -> list[str]:
        cmd = [
            self._binary,
            "-m", self._model,
            "-f", wav_path,
            "-l", self.cfg.language,
            "-t", str(self.cfg.threads),
            # Greedy decode. Beam search costs ~40% more time for ~1-2% WER on
            # short commands, and latency is what the user actually feels.
            "-bs", str(max(1, self.cfg.beam_size)),
            # No timestamps, no progress spam, JSON out so we never have to
            # parse whisper's pretty-printed console format.
            "-nt",
            "-np",
            "-oj",
            "-of", out_prefix,
        ]
        if self.cfg.beam_size <= 1:
            # -bo 1 disables best-of sampling, the other hidden latency tax.
            cmd += ["-bo", "1"]
        if self.cfg.initial_prompt:
            cmd += ["--prompt", self.cfg.initial_prompt]
        return cmd

    async def transcribe(self, samples: PcmArray, sample_rate: int) -> Transcript:
        if not samples:
            return Transcript(text="")

        started = time.monotonic()
        wav_bytes = wav_from_pcm(samples, sample_rate)

        with tempfile.TemporaryDirectory(prefix="walle-stt-") as tmpdir:
            wav_path = os.path.join(tmpdir, "utt.wav")
            out_prefix = os.path.join(tmpdir, "utt")
            Path(wav_path).write_bytes(wav_bytes)
            cmd = self._build_command(wav_path, out_prefix)

            # One transcription at a time: whisper.cpp already saturates the
            # cores it was given, and two concurrent runs make both slower.
            async with self._lock:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _stdout, stderr = await asyncio.wait_for(
                        proc.communicate(), timeout=self.cfg.timeout_s
                    )
                except FileNotFoundError:
                    log.error("whisper.cpp binary not found: %s", self._binary)
                    return Transcript(text="", meta={"error": "binary-missing"})
                except asyncio.TimeoutError:
                    log.error("whisper.cpp timed out after %.1fs", self.cfg.timeout_s)
                    return Transcript(text="", meta={"error": "timeout"})

            if proc.returncode != 0:
                log.error(
                    "whisper.cpp exited %s: %s",
                    proc.returncode,
                    stderr.decode("utf-8", "replace").strip()[-400:],
                )
                return Transcript(text="", meta={"error": "nonzero-exit"})

            text, meta = _read_json_output(Path(out_prefix + ".json"))

        latency = int((time.monotonic() - started) * 1000)
        log.info("stt: %d ms for %.1fs of audio -> %r",
                 latency, len(samples) / sample_rate, text)
        return Transcript(
            text=text,
            language=self.cfg.language,
            latency_ms=latency,
            meta=meta,
        )

    async def warmup(self) -> None:
        """Runs 200 ms of silence through the model so it is resident in the
        page cache before the first real question."""
        from array import array

        try:
            result = await self.transcribe(array("h", bytes(2 * 3200)), 16000)
            error = result.meta.get("error")
            if error:
                # Reporting "warmed up" here would be an outright lie, and it is
                # the log line someone reads when the robot hears nothing.
                log.warning("stt is not usable (%s) - check WALLE_WHISPER_BIN "
                            "and WALLE_WHISPER_MODEL", error)
            else:
                log.info("stt warmed up (%s)", Path(self._model).name)
        except Exception:  # pragma: no cover - warmup must never be fatal
            log.warning("stt warmup failed; first request will be slower", exc_info=True)


def _read_json_output(path: Path) -> tuple[str, dict]:
    """Parses whisper.cpp's -oj output into plain text + segment metadata."""
    if not path.is_file():
        return "", {"error": "no-output"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", {"error": "bad-json"}

    segments = data.get("transcription", []) or []
    text = " ".join(seg.get("text", "").strip() for seg in segments).strip()
    # Collapse the double spaces that segment joining leaves behind.
    text = " ".join(text.split())
    return text, {"segments": len(segments)}
