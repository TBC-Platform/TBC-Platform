# SPDX-License-Identifier: MIT
"""Shared fixtures and the fake engines used by the pipeline tests."""

from __future__ import annotations

import sys
from array import array
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from walle.config import Config  # noqa: E402
from walle.llm.base import LlmEngine, Reply  # noqa: E402
from walle.memory import MemoryStore  # noqa: E402
from walle.protocol import AUDIO_SAMPLE_RATE  # noqa: E402
from walle.session import Brain  # noqa: E402
from walle.stt.base import SttEngine, Transcript  # noqa: E402
from walle.tools import DeviceRegistry  # noqa: E402
from walle.tts.base import Speech, TtsEngine  # noqa: E402


class FakeStt(SttEngine):
    """Returns whatever you queue, so tests never touch a real recogniser."""

    name = "fake-stt"

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls = 0

    async def transcribe(self, samples, sample_rate):
        self.calls += 1
        return Transcript(text=self.text, latency_ms=1)


class FakeTts(TtsEngine):
    name = "fake-tts"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synthesize(self, text: str) -> Speech:
        self.spoken.append(text)
        # 100 ms of silence is enough to exercise the chunking path.
        return Speech(samples=array("h", bytes(3200)), sample_rate=AUDIO_SAMPLE_RATE)


class FakeLlm(LlmEngine):
    name = "fake-llm"

    def __init__(self, reply: str = "I am a robot.") -> None:
        self.reply = reply
        self.messages = None
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        self.messages = messages
        return Reply(text=self.reply, latency_ms=1)


class FakeVision:
    name = "fake-vision"
    unavailable_reason = None

    def __init__(self, detections=None) -> None:
        self.detections = detections or []
        self.frames: list[bytes] = []

    async def detect(self, jpeg: bytes):
        self.frames.append(jpeg)
        return self.detections


class FakeSmartHome:
    name = "fake-home"

    def __init__(self, ok: bool = True, detail: str = "done") -> None:
        self.ok = ok
        self.detail = detail
        self.calls: list[tuple[str, str, dict]] = []

    async def call(self, entity_id: str, service: str, **data):
        self.calls.append((entity_id, service, data))
        return self.ok, self.detail

    async def state(self, entity_id: str):
        return "on"

    async def close(self) -> None:
        return None


@pytest.fixture
async def memory(tmp_path):
    store = MemoryStore(tmp_path / "test.db")
    await store.open()
    yield store
    await store.close()


@pytest.fixture
def devices() -> DeviceRegistry:
    return DeviceRegistry(
        {
            "desk lamp": "light.desk_lamp",
            "office light": "light.office_ceiling",
            "fan": "fan.office",
        }
    )


@pytest.fixture
async def brain(memory, devices) -> Brain:
    return Brain(
        config=Config(),
        stt=FakeStt(),
        tts=FakeTts(),
        llm=FakeLlm(),
        vision=FakeVision(),
        memory=memory,
        devices=devices,
        smarthome=FakeSmartHome(),
    )
