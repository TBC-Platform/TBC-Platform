# SPDX-License-Identifier: MIT
"""End-to-end pipeline tests with fake engines.

These exercise the real Session code path - protocol framing, capture,
routing, action dispatch and speech streaming - without needing a robot, a
recogniser or a model.
"""

from __future__ import annotations

import asyncio
from array import array

import pytest
from conftest import FakeVision

from walle import protocol as proto
from walle.session import Session
from walle.vision.detector import Detection


class Wire:
    """Captures everything the server sends to the robot."""

    def __init__(self) -> None:
        self.control: list[dict] = []
        self.audio: list[bytes] = []

    async def send_text(self, text: str) -> None:
        self.control.append(proto.decode_json(text))

    async def send_bytes(self, data: bytes) -> None:
        frame = proto.decode_bin(data)
        self.audio.append(frame.payload)

    def types(self) -> list[str]:
        return [m["t"] for m in self.control]

    def first(self, msg_type: str) -> dict | None:
        return next((m for m in self.control if m["t"] == msg_type), None)

    @property
    def spoken(self) -> str:
        said = self.first(proto.MSG_SAY_BEGIN)
        return said["text"] if said else ""


@pytest.fixture
def wire() -> Wire:
    return Wire()


@pytest.fixture
def session(brain, wire) -> Session:
    return Session(brain, "walle-test", wire.send_text, wire.send_bytes)


async def speak_to(session: Session, heard: str, *, seconds: float = 1.0,
                   speech: bool = True) -> None:
    """Drives a whole utterance through the session, as the firmware would."""
    session.brain.stt.text = heard
    await session.on_control({"t": proto.MSG_UTT_BEGIN, "sr": proto.AUDIO_SAMPLE_RATE})

    pcm = array("h", bytes(int(proto.AUDIO_SAMPLE_RATE * seconds) * 2)).tobytes()
    for chunk in proto.chunk_audio(pcm):
        await session.on_binary(proto.encode_bin(proto.BinType.AUDIO_UP, chunk))

    await session.on_control({"t": proto.MSG_UTT_END, "ms": int(seconds * 1000),
                              "speech": speech})
    # _end_capture spawns the turn as a task; let it finish.
    if session._speaking_task:
        await session._speaking_task


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------

async def test_hello_is_acknowledged_with_the_protocol_version(session, wire):
    await session.on_control({"t": proto.MSG_HELLO, "fw": "1.0.0",
                              "proto": proto.PROTO_VERSION, "caps": ["mic"]})
    ack = wire.first(proto.MSG_HELLO_ACK)
    assert ack is not None
    assert ack["proto"] == proto.PROTO_VERSION
    assert ack["sr"] == proto.AUDIO_SAMPLE_RATE


async def test_protocol_mismatch_is_logged_but_still_acked(session, wire, caplog):
    await session.on_control({"t": proto.MSG_HELLO, "proto": 99})
    assert wire.first(proto.MSG_HELLO_ACK) is not None
    assert "protocol mismatch" in caplog.text


# ---------------------------------------------------------------------------
# Chat path
# ---------------------------------------------------------------------------

async def test_open_question_reaches_the_llm_and_is_spoken(session, wire):
    session.brain.llm.reply = "Paris is the capital of France."
    await speak_to(session, "what is the capital of france")

    assert session.brain.llm.calls == 1
    assert wire.spoken == "Paris is the capital of France."
    assert proto.MSG_SAY_BEGIN in wire.types()
    assert proto.MSG_SAY_END in wire.types()
    assert wire.audio, "no audio was streamed"
    # The last audio frame closes the stream.
    assert wire.audio[-1] == b""


async def test_history_and_facts_are_given_to_the_llm(session, brain):
    await brain.memory.set_fact("walle-test", "name", "Sam")
    await brain.memory.add_turn("walle-test", "earlier question", "earlier answer")

    await speak_to(session, "and what about now")

    messages = brain.llm.messages
    assert messages[0].role == "system"
    assert "name: Sam" in messages[0].content
    assert any(m.content == "earlier question" for m in messages)
    assert messages[-1].content == "and what about now"


async def test_the_turn_is_written_to_memory(session, brain):
    session.brain.llm.reply = "A short answer."
    await speak_to(session, "a question")
    turns = await brain.memory.recent_turns("walle-test")
    assert turns[-1].user_text == "a question"
    assert turns[-1].robot_text == "A short answer."


async def test_llm_failure_is_spoken_not_raised(session, wire):
    from walle.llm.base import Reply

    async def broken(_messages):
        return Reply(text="", meta={"error": "unreachable"})

    session.brain.llm.chat = broken
    await speak_to(session, "anything at all")
    assert "cannot reach" in wire.spoken
    assert wire.first(proto.MSG_FACE)["e"] in {"thinking", "sad", "speaking", "idle"}


# ---------------------------------------------------------------------------
# Offline intents (no LLM involved)
# ---------------------------------------------------------------------------

async def test_movement_command_never_reaches_the_llm(session, wire, brain):
    await speak_to(session, "go forward for two seconds")

    move = wire.first(proto.MSG_MOVE)
    assert move["cmd"] == "forward"
    assert brain.llm.calls == 0, "movement must be handled offline"


async def test_stop_is_dispatched(session, wire, brain):
    await speak_to(session, "stop")
    assert wire.first(proto.MSG_MOVE)["cmd"] == "stop"
    assert brain.llm.calls == 0


async def test_head_command(session, wire, brain):
    await speak_to(session, "look left")
    assert wire.first(proto.MSG_HEAD)["deg"] < 90
    assert brain.llm.calls == 0


async def test_remember_stores_a_fact_offline(session, brain):
    await speak_to(session, "my name is Sam")
    assert await brain.memory.get_facts("walle-test") == ["name: Sam"]
    assert brain.llm.calls == 0


async def test_forget_clears_facts_and_history(session, brain):
    await brain.memory.set_fact("walle-test", "name", "Sam")
    await brain.memory.add_turn("walle-test", "old", "old")
    await speak_to(session, "forget everything")
    assert await brain.memory.get_facts("walle-test") == []


async def test_status_uses_the_last_state_report(session, wire):
    await session.on_control({"t": proto.MSG_STATE, "s": "idle", "batt_pct": 18, "rssi": -80})
    await speak_to(session, "how much battery do you have")
    assert "18 percent" in wire.spoken
    assert "wifi signal is weak" in wire.spoken


# ---------------------------------------------------------------------------
# Smart home
# ---------------------------------------------------------------------------

async def test_smart_home_command_is_executed(session, wire, brain):
    await speak_to(session, "turn on the desk lamp")
    assert brain.smarthome.calls == [("light.desk_lamp", "turn_on", {})]
    assert "desk lamp on" in wire.spoken
    assert brain.llm.calls == 0


async def test_refused_smart_home_command_is_explained(session, wire, brain):
    brain.smarthome.ok = False
    brain.smarthome.detail = "light.desk_lamp is not allowlisted"
    await speak_to(session, "turn on the desk lamp")
    assert "not allowed" in wire.spoken


async def test_smart_home_actions_are_audited(session, brain):
    await speak_to(session, "turn on the desk lamp")
    events = await brain.memory.recent_events()
    assert events and events[0]["kind"] == "smarthome"
    assert "light.desk_lamp" in events[0]["detail"]


async def test_smart_home_disabled_is_reported(session, wire, brain):
    brain.smarthome = None
    await speak_to(session, "turn on the desk lamp")
    assert "switched off in my settings" in wire.spoken


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------

async def test_look_requests_a_frame_and_describes_it(brain, wire):
    brain.vision = FakeVision([Detection("person", 0.9, (0.0, 0.7, 1.0, 0.95))])
    session = Session(brain, "walle-test", wire.send_text, wire.send_bytes)

    async def answer_camera() -> None:
        # Wait for the server to ask, then reply as the firmware does.
        for _ in range(200):
            if wire.first(proto.MSG_CAM):
                break
            await asyncio.sleep(0.005)
        await session.on_control({"t": proto.MSG_CAM_META, "len": 4, "w": 640, "h": 480})
        await session.on_binary(proto.encode_bin(
            proto.BinType.JPEG_UP, b"\xff\xd8\xff\xd9",
            flags=proto.FLAG_FIRST | proto.FLAG_LAST))

    responder = asyncio.create_task(answer_camera())
    await speak_to(session, "what do you see")
    await responder

    assert brain.vision.frames == [b"\xff\xd8\xff\xd9"]
    assert "person" in wire.spoken
    assert "on my right" in wire.spoken


async def test_look_without_a_camera_reply_is_handled(session, wire, monkeypatch):
    monkeypatch.setattr("walle.session.CAMERA_TIMEOUT_S", 0.05)
    await speak_to(session, "what do you see")
    assert "could not get a picture" in wire.spoken


async def test_oversized_camera_frame_is_refused(session, caplog):
    await session.on_control({"t": proto.MSG_CAM_META, "len": 99_000_000})
    assert "refusing camera frame" in caplog.text
    assert session._jpeg_expected == 0


async def test_camera_overrun_is_dropped(session, caplog):
    await session.on_control({"t": proto.MSG_CAM_META, "len": 4})
    await session.on_binary(proto.encode_bin(proto.BinType.JPEG_UP, b"toolongpayload",
                                             flags=proto.FLAG_FIRST))
    assert "overran" in caplog.text


# ---------------------------------------------------------------------------
# Sleep intent
# ---------------------------------------------------------------------------

async def test_sleep_leaves_the_sleep_face_on_screen(session, wire):
    """The speaking face overwrites it mid-reply, so the LAST face must be sleep."""
    await speak_to(session, "goodnight")
    faces = [m["e"] for m in wire.control if m["t"] == proto.MSG_FACE]
    assert faces[-1] == "sleep", faces
    assert "speaking" in faces  # it really did go through the speech path


async def test_sleep_stops_the_motors(session, wire):
    await speak_to(session, "goodnight")
    assert wire.first(proto.MSG_MOVE)["cmd"] == "stop"


async def test_a_normal_reply_still_ends_on_idle(session, wire):
    await speak_to(session, "what is the capital of france")
    faces = [m["e"] for m in wire.control if m["t"] == proto.MSG_FACE]
    assert faces[-1] == "idle", faces


# ---------------------------------------------------------------------------
# Downlink pacing
# ---------------------------------------------------------------------------

async def test_long_replies_are_paced_to_the_device_buffer(brain, wire, monkeypatch):
    """Unpaced streaming overflows the firmware's ring and truncates the reply.

    Two seconds of audio must not be handed over in one burst. The lead is
    shrunk to 0.2 s so the assertion is decisive without making the test itself
    wait out a realistic buffer.
    """
    import time as _time
    from array import array as _array

    from walle.tts.base import Speech

    monkeypatch.setattr("walle.session.PLAYBACK_LEAD_S", 0.2)

    class LongTts:
        name = "long-tts"

        async def synthesize(self, text):
            # 2 seconds of silence at the protocol rate.
            return Speech(samples=_array("h", bytes(proto.AUDIO_SAMPLE_RATE * 2 * 2)),
                          sample_rate=proto.AUDIO_SAMPLE_RATE)

    brain.tts = LongTts()
    session = Session(brain, "walle-test", wire.send_text, wire.send_bytes)

    started = _time.monotonic()
    await speak_to(session, "tell me something long")
    elapsed = _time.monotonic() - started

    # 2 s of audio with a 0.2 s lead cannot legitimately finish instantly.
    assert elapsed > 0.5, f"streamed 2s of audio in {elapsed:.2f}s - not paced"
    # And it must all still arrive - pacing must not drop anything.
    total = sum(len(chunk) for chunk in wire.audio) // 2
    assert total == proto.AUDIO_SAMPLE_RATE * 2


async def test_short_replies_are_not_delayed(session, wire):
    """Pacing must not slow down the common case."""
    import time as _time

    started = _time.monotonic()
    await speak_to(session, "what is the capital of france")
    assert _time.monotonic() - started < 0.5


# ---------------------------------------------------------------------------
# Capture edge cases
# ---------------------------------------------------------------------------

async def test_every_turn_ends_with_turn_end(session, wire):
    """The device leaves THINKING on turn_end, so it must always arrive."""
    await speak_to(session, "what is the capital of france")
    assert wire.types()[-1] == proto.MSG_TURN_END


async def test_turn_end_is_sent_even_when_nothing_was_understood(session, wire):
    """Otherwise the robot sits in THINKING for 20 s after every misheard word."""
    await speak_to(session, "Thank you.")  # a Whisper silence hallucination
    assert proto.MSG_SAY_BEGIN not in wire.types()
    assert wire.types()[-1] == proto.MSG_TURN_END


async def test_turn_end_is_sent_when_an_engine_fails(session, wire):
    async def broken(_samples, _rate):
        raise RuntimeError("recogniser exploded")

    session.brain.stt.transcribe = broken
    await speak_to(session, "anything")
    assert wire.types()[-1] == proto.MSG_TURN_END
    assert wire.first(proto.MSG_ERROR) is not None


async def test_turn_end_carries_a_reason_for_skipped_utterances(session, wire):
    await speak_to(session, "unused", speech=False)
    turn_end = wire.first(proto.MSG_TURN_END)
    assert turn_end["reason"] == "no-speech"


async def test_barge_in_does_not_send_turn_end(session, wire, brain):
    """A cancelled turn must not tell the device to go idle - it is listening."""
    import asyncio as _asyncio

    async def slow(_messages):
        await _asyncio.sleep(5)
        raise AssertionError("should have been cancelled")

    brain.llm.chat = slow
    session.brain.stt.text = "tell me a long story"
    await session.on_control({"t": proto.MSG_UTT_BEGIN})
    pcm = array("h", bytes(proto.AUDIO_SAMPLE_RATE * 2)).tobytes()
    for chunk in proto.chunk_audio(pcm):
        await session.on_binary(proto.encode_bin(proto.BinType.AUDIO_UP, chunk))
    await session.on_control({"t": proto.MSG_UTT_END, "ms": 1000, "speech": True})

    first_turn = session._speaking_task
    await _asyncio.sleep(0.05)               # let the turn reach the LLM
    await session.on_control({"t": proto.MSG_UTT_BEGIN})  # user interrupts
    with pytest.raises(_asyncio.CancelledError):
        await first_turn

    assert proto.MSG_TURN_END not in wire.types()


async def test_no_speech_flag_skips_the_whole_pipeline(session, brain, wire):
    """A false wake must not cost a Whisper run."""
    await speak_to(session, "should not be used", speech=False)
    assert brain.stt.calls == 0
    assert proto.MSG_SAY_BEGIN not in wire.types()


async def test_utterance_shorter_than_250ms_is_ignored(session, brain):
    await speak_to(session, "hello", seconds=0.1)
    assert brain.stt.calls == 0


async def test_empty_transcript_is_not_sent_to_the_llm(session, brain, wire):
    await speak_to(session, "   ")
    assert brain.llm.calls == 0
    assert proto.MSG_SAY_BEGIN not in wire.types()


async def test_whisper_silence_hallucination_is_discarded(session, brain, wire):
    """Whisper says "Thank you." to silence; the robot must not answer it."""
    await speak_to(session, "Thank you.")
    assert brain.llm.calls == 0
    assert proto.MSG_SAY_BEGIN not in wire.types()


async def test_audio_outside_an_utterance_is_dropped(session):
    await session.on_binary(proto.encode_bin(proto.BinType.AUDIO_UP, bytes(640)))
    assert len(session._capture) == 0


async def test_capture_is_capped(session):
    from walle.session import MAX_UTTERANCE_SAMPLES

    await session.on_control({"t": proto.MSG_UTT_BEGIN})
    frame = bytes(proto.AUDIO_FRAME_BYTES)
    for _ in range((MAX_UTTERANCE_SAMPLES // proto.AUDIO_FRAME_SAMPLES) + 50):
        await session.on_binary(proto.encode_bin(proto.BinType.AUDIO_UP, frame))
    assert len(session._capture) <= MAX_UTTERANCE_SAMPLES


async def test_malformed_binary_frame_is_ignored(session, caplog):
    await session.on_binary(b"\x00\x01\x02")
    assert "bad binary frame" in caplog.text


async def test_unknown_control_message_is_ignored(session, caplog):
    await session.on_control({"t": "definitely-not-a-real-message"})
    assert "unknown control message" in caplog.text


async def test_device_log_messages_are_surfaced(session, caplog):
    import logging

    caplog.set_level(logging.INFO)  # the device log line is INFO, not WARNING
    await session.on_control({"t": proto.MSG_LOG, "lvl": "warn", "msg": "camera failed"})
    assert "camera failed" in caplog.text
