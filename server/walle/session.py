# SPDX-License-Identifier: MIT
"""One connected robot: the whole wake-to-speech pipeline lives here.

Flow for a single turn::

    utt_begin ─┐
    audio…     ├─> buffer ─> STT ─> intent router ─┬─> device/home action ─┐
    utt_end   ─┘                                   └─> LLM (chat only)  ───┤
                                                                           v
                              robot <─ audio chunks <─ TTS <─ reply text ──┘

Everything that can be answered without the LLM is (see :mod:`walle.intent`),
which is what keeps the common cases fast and fully offline.

Timings for every stage are recorded and logged as one line per turn, because
"where did the two seconds go" is the question you will ask most often while
tuning this. See docs/04-latency.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from array import array
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from . import intent as intent_router
from . import protocol as proto
from .audio import PcmArray, duration_ms, pcm_from_bytes
from .config import Config
from .llm import LlmEngine, Message, Reply, build_system_prompt
from .memory import MemoryStore
from .stt import SttEngine
from .tools import DeviceRegistry
from .tts import TtsEngine
from .vision import describe as describe_detections

log = logging.getLogger(__name__)

# Guard rail: a robot that never sends utt_end (crashed mid-sentence, bad
# Wi-Fi) must not be able to grow this buffer forever.
MAX_UTTERANCE_SECONDS = 20
MAX_UTTERANCE_SAMPLES = proto.AUDIO_SAMPLE_RATE * MAX_UTTERANCE_SECONDS

# A JPEG from the OV2640 at VGA is 25-45 kB; 512 kB is a generous ceiling that
# still stops a malformed cam_meta from eating memory.
MAX_JPEG_BYTES = 512 * 1024

CAMERA_TIMEOUT_S = 4.0


@dataclass
class Brain:
    """The shared, expensive things. Built once and handed to every session."""

    config: Config
    stt: SttEngine
    tts: TtsEngine
    llm: LlmEngine
    vision: Any
    memory: MemoryStore
    devices: DeviceRegistry
    smarthome: Any = None


@dataclass
class TurnTimings:
    """Milliseconds per stage, for the one-line latency log."""

    capture: int = 0
    stt: int = 0
    think: int = 0
    tts_first: int = 0
    total: int = 0
    path: str = ""

    def render(self) -> str:
        return (
            f"capture={self.capture}ms stt={self.stt}ms think={self.think}ms "
            f"tts_first={self.tts_first}ms total={self.total}ms via={self.path}"
        )


class Session:
    """Handles one WebSocket connection from one robot."""

    def __init__(
        self,
        brain: Brain,
        device_id: str,
        send_text: Callable[[str], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self.brain = brain
        self.device_id = device_id
        self._send_text = send_text
        self._send_bytes = send_bytes

        self._capture = array("h")
        self._capturing = False
        self._utt_started = 0.0
        self._seq = 0
        self._overflowed = False

        # Camera frame assembly.
        self._jpeg_parts: list[bytes] = []
        self._jpeg_expected = 0
        self._jpeg_waiter: asyncio.Future[bytes] | None = None

        # One turn at a time. If the user talks over a reply the new turn wins,
        # but the two never run concurrently and interleave their speech.
        self._turn_lock = asyncio.Lock()
        self._speaking_task: asyncio.Task | None = None
        self.last_state: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Outgoing helpers
    # ------------------------------------------------------------------

    async def send(self, msg_type: str, **fields) -> None:
        await self._send_text(proto.encode_json(msg_type, **fields))

    async def _send_audio_chunk(self, payload: bytes, flags: int = proto.FLAG_NONE) -> None:
        self._seq = (self._seq + 1) & 0xFFFF
        await self._send_bytes(proto.encode_bin(proto.BinType.AUDIO_DOWN, payload,
                                                flags=flags, seq=self._seq))

    async def set_face(self, face: str | None, hold_ms: int = 0) -> None:
        if face and face in proto.FACES:
            await self.send(proto.MSG_FACE, e=face, ms=hold_ms)

    # ------------------------------------------------------------------
    # Incoming: control messages
    # ------------------------------------------------------------------

    async def on_control(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("t", "")

        if msg_type == proto.MSG_HELLO:
            await self._on_hello(msg)
        elif msg_type == proto.MSG_WAKE:
            log.info("[%s] wake word %r (score %s)", self.device_id,
                     msg.get("word"), msg.get("score"))
            await self.set_face("listening")
        elif msg_type == proto.MSG_UTT_BEGIN:
            self._begin_capture()
        elif msg_type == proto.MSG_UTT_END:
            await self._end_capture(msg)
        elif msg_type == proto.MSG_CAM_META:
            self._begin_jpeg(msg)
        elif msg_type == proto.MSG_STATE:
            self.last_state = {k: v for k, v in msg.items() if k != "t"}
        elif msg_type == proto.MSG_LOG:
            log.info("[%s] device %s: %s", self.device_id, msg.get("lvl"), msg.get("msg"))
        else:
            log.warning("[%s] unknown control message %r", self.device_id, msg_type)

    async def _on_hello(self, msg: dict[str, Any]) -> None:
        log.info(
            "[%s] connected: fw=%s proto=%s caps=%s",
            self.device_id, msg.get("fw"), msg.get("proto"), msg.get("caps"),
        )
        if msg.get("proto") != proto.PROTO_VERSION:
            log.error(
                "[%s] protocol mismatch: device speaks %s, server speaks %s. "
                "Reflash the firmware from this checkout.",
                self.device_id, msg.get("proto"), proto.PROTO_VERSION,
            )
        await self.send(
            proto.MSG_HELLO_ACK,
            proto=proto.PROTO_VERSION,
            sr=proto.AUDIO_SAMPLE_RATE,
            stt=self.brain.stt.name,
            tts=self.brain.tts.name,
            llm=self.brain.llm.name,
        )
        await self.set_face("idle")

    # ------------------------------------------------------------------
    # Incoming: binary frames
    # ------------------------------------------------------------------

    async def on_binary(self, data: bytes) -> None:
        try:
            frame = proto.decode_bin(data)
        except proto.ProtocolError as exc:
            log.warning("[%s] bad binary frame: %s", self.device_id, exc)
            return

        if frame.type is proto.BinType.AUDIO_UP:
            self._on_audio(frame)
        elif frame.type is proto.BinType.JPEG_UP:
            self._on_jpeg(frame)
        else:
            log.warning("[%s] unexpected binary type %s", self.device_id, frame.type)

    def _on_audio(self, frame: proto.BinFrame) -> None:
        if not self._capturing:
            # Audio outside an utterance is normal right after a barge-in;
            # drop it quietly.
            return
        if len(self._capture) >= MAX_UTTERANCE_SAMPLES:
            if not self._overflowed:
                log.warning("[%s] utterance exceeded %ds, truncating",
                            self.device_id, MAX_UTTERANCE_SECONDS)
                self._overflowed = True
            return
        if frame.payload:
            self._capture.extend(pcm_from_bytes(frame.payload))

    # ------------------------------------------------------------------
    # Utterance lifecycle
    # ------------------------------------------------------------------

    def _begin_capture(self) -> None:
        self._capture = array("h")
        self._capturing = True
        self._overflowed = False
        self._utt_started = time.monotonic()

        # Barge-in: the user started talking, so abandon whatever we were
        # saying. The firmware flushes its own buffer on the same event.
        if self._speaking_task and not self._speaking_task.done():
            self._speaking_task.cancel()

    async def _end_capture(self, msg: dict[str, Any]) -> None:
        if not self._capturing:
            return
        self._capturing = False

        samples = self._capture
        self._capture = array("h")

        if not msg.get("speech", True):
            # The device's own VAD heard nothing but room noise. Skip the whole
            # pipeline - this saves a pointless Whisper run on every false wake.
            log.info("[%s] utterance had no speech, ignoring", self.device_id)
            await self.set_face("idle")
            return

        if len(samples) < proto.AUDIO_SAMPLE_RATE // 4:  # < 250 ms
            log.info("[%s] utterance too short (%d samples)", self.device_id, len(samples))
            await self.set_face("idle")
            return

        timings = TurnTimings(capture=int((time.monotonic() - self._utt_started) * 1000))
        self._speaking_task = asyncio.create_task(self._run_turn(samples, timings))

    async def _run_turn(self, samples: PcmArray, timings: TurnTimings) -> None:
        started = time.monotonic()
        try:
            async with self._turn_lock:
                await self._process(samples, timings, started)
        except asyncio.CancelledError:
            log.info("[%s] turn cancelled (barge-in)", self.device_id)
            raise
        except Exception:
            log.exception("[%s] turn failed", self.device_id)
            await self.send(proto.MSG_ERROR, msg="internal error")
            await self.set_face("confused", 1500)
            await self._say("Sorry, something went wrong in my head.")

    # ------------------------------------------------------------------
    # The pipeline
    # ------------------------------------------------------------------

    async def _process(self, samples: PcmArray, timings: TurnTimings, started: float) -> None:
        await self.set_face("thinking")

        # --- 1. speech to text ---------------------------------------------
        transcript = await self.brain.stt.transcribe(samples, proto.AUDIO_SAMPLE_RATE)
        timings.stt = transcript.latency_ms

        if transcript.is_empty:
            log.info("[%s] nothing intelligible (%.1fs of audio)",
                     self.device_id, duration_ms(samples) / 1000)
            await self.set_face("confused", 1200)
            await self.set_face("idle")
            return

        log.info("[%s] heard: %r", self.device_id, transcript.text)

        # --- 2. route -------------------------------------------------------
        think_started = time.monotonic()
        detected = intent_router.route(transcript.text, self.brain.devices)
        timings.path = detected.kind

        reply = await self._handle_intent(detected, transcript.text)
        timings.think = int((time.monotonic() - think_started) * 1000)

        # --- 3. act ---------------------------------------------------------
        if reply.face:
            await self.set_face(reply.face, 2500)
        if reply.move:
            await self.send(proto.MSG_MOVE, cmd=reply.move, speed=60, ms=800)

        # --- 4. speak -------------------------------------------------------
        if reply.text:
            timings.tts_first = await self._say(reply.text)

        timings.total = int((time.monotonic() - started) * 1000) + timings.capture
        log.info("[%s] turn done: %s", self.device_id, timings.render())

        await self.brain.memory.add_turn(
            self.device_id, transcript.text, reply.text, timings.total
        )

    async def _handle_intent(self, detected: intent_router.Intent, raw_text: str) -> Reply:
        """Executes one intent and returns what the robot should say."""
        kind = detected.kind

        if kind == "stop":
            await self.send(proto.MSG_MOVE, cmd="stop")
            return Reply(text=detected.speech, face=detected.face)

        if kind == "move":
            await self.send(proto.MSG_MOVE, **detected.params)
            return Reply(text=detected.speech, face=detected.face)

        if kind == "head":
            await self.send(proto.MSG_HEAD, deg=detected.params["deg"])
            return Reply(text=detected.speech or "", face=detected.face)

        if kind == "look":
            return await self._handle_look(raw_text)

        if kind == "smarthome":
            return await self._handle_smarthome(detected)

        if kind == "remember":
            await self.brain.memory.set_fact(
                self.device_id, detected.params["key"], detected.params["value"]
            )
            return Reply(text=detected.speech, face=detected.face)

        if kind == "forget":
            count = await self.brain.memory.forget(self.device_id)
            await self.brain.memory.clear_history(self.device_id)
            log.info("[%s] forgot %d facts and the conversation history",
                     self.device_id, count)
            return Reply(text=detected.speech, face=detected.face)

        if kind == "sleep":
            return Reply(text=detected.speech, face="sleep")

        if kind == "status":
            return Reply(text=self._status_sentence(), face="idle")

        return await self._handle_chat(raw_text)

    # ------------------------------- handlers ---------------------------

    def _status_sentence(self) -> str:
        state = self.last_state
        percent = state.get("batt_pct")
        rssi = state.get("rssi")
        if not percent:
            return "I am plugged in and feeling fine."
        mood = "plenty of charge" if percent > 40 else "getting low on charge"
        signal = ""
        if isinstance(rssi, int) and rssi < -75:
            signal = " My wifi signal is weak, though."
        return f"Battery is at {percent} percent, so I have {mood}.{signal}"

    async def _handle_look(self, raw_text: str) -> Reply:
        jpeg = await self._request_frame()
        if jpeg is None:
            return Reply(text="I could not get a picture from my camera.", face="sad")

        detections = await self.brain.vision.detect(jpeg)
        if not detections:
            reason = getattr(self.brain.vision, "unavailable_reason", None)
            if reason:
                log.warning("[%s] vision unavailable: %s", self.device_id, reason)
                return Reply(text="My eyes are not working right now.", face="sad")
            return Reply(text="I do not recognise anything from here.", face="confused")

        summary = describe_detections(detections)
        best = detections[0]
        # Answer directly - a local model adds a second of latency to say the
        # same thing. The LLM only gets involved if the user asked something
        # *about* what was seen, which falls through to chat instead.
        return Reply(text=f"I can see {summary}. The {best.label} is {best.where}.",
                     face="happy")

    async def _handle_smarthome(self, detected: intent_router.Intent) -> Reply:
        if detected.speech:  # e.g. "I don't know which device you mean"
            return Reply(text=detected.speech, face=detected.face)

        backend = self.brain.smarthome
        if backend is None:
            return Reply(
                text="Smart home control is switched off in my settings.",
                face="confused",
            )

        entity_id = detected.params["entity_id"]
        service = detected.params["service"]
        data = detected.params.get("data") or {}
        alias = detected.params.get("alias") or entity_id

        ok, detail = await backend.call(entity_id, service, **data)
        await self.brain.memory.log_event(
            self.device_id,
            "smarthome",
            f"{'ok' if ok else 'refused'}: {entity_id} {service} ({detail})",
        )

        if not ok:
            log.warning("[%s] smart home refused: %s", self.device_id, detail)
            return Reply(text=f"I am not allowed to do that. {detail}.", face="sad")

        verb = {"turn_on": "on", "turn_off": "off", "toggle": "toggled"}.get(service, "set")
        if service == "set_percentage":
            return Reply(text=f"Setting the {alias} to {data.get('percentage')} percent.",
                         face="happy")
        if service == "toggle":
            return Reply(text=f"I {verb} the {alias}.", face="happy")
        return Reply(text=f"Turning the {alias} {verb}.", face="happy")

    async def _handle_chat(self, raw_text: str) -> Reply:
        facts = await self.brain.memory.get_facts(self.device_id)
        history = await self.brain.memory.recent_turns(
            self.device_id, limit=self.brain.config.llm.history_turns
        )

        smart_home_hint = None
        if self.brain.smarthome and len(self.brain.devices):
            names = ", ".join(self.brain.devices.known_names[:8])
            smart_home_hint = (
                f"You can control these things in the house: {names}. If the person "
                "asks you to switch one on or off, tell them you are doing it."
            )

        messages = [
            Message(
                "system",
                build_system_prompt(user_facts=facts, smart_home_hint=smart_home_hint),
            )
        ]
        for turn in history:
            messages.append(Message("user", turn.user_text))
            messages.append(Message("assistant", turn.robot_text))
        messages.append(Message("user", raw_text))

        reply = await self.brain.llm.chat(messages)

        if not reply.text:
            error = reply.meta.get("error", "")
            spoken = {
                "unreachable": "I cannot reach my thinking box right now.",
                "model-missing": "My language model is not installed yet.",
                "auth": "My cloud key was rejected.",
                "rate-limit": "I am being rate limited. Try again in a moment.",
            }.get(error, "I could not think of an answer.")
            log.error("[%s] llm failed: %s", self.device_id, error or "empty reply")
            return Reply(text=spoken, face="sad")

        return reply

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------

    def _begin_jpeg(self, msg: dict[str, Any]) -> None:
        expected = int(msg.get("len", 0) or 0)
        if expected <= 0 or expected > MAX_JPEG_BYTES:
            log.warning("[%s] refusing camera frame of %d bytes", self.device_id, expected)
            self._jpeg_expected = 0
            return
        self._jpeg_parts = []
        self._jpeg_expected = expected

    def _on_jpeg(self, frame: proto.BinFrame) -> None:
        if frame.is_first:
            self._jpeg_parts = []
        if self._jpeg_expected <= 0:
            return

        self._jpeg_parts.append(frame.payload)
        total = sum(len(p) for p in self._jpeg_parts)
        if total > self._jpeg_expected:
            log.warning("[%s] camera frame overran its declared length", self.device_id)
            self._jpeg_parts = []
            self._jpeg_expected = 0
            return

        if frame.is_last:
            jpeg = b"".join(self._jpeg_parts)
            self._jpeg_parts = []
            self._jpeg_expected = 0
            if self._jpeg_waiter and not self._jpeg_waiter.done():
                self._jpeg_waiter.set_result(jpeg)

    async def _request_frame(self) -> bytes | None:
        """Asks the robot for a still and waits for it to arrive."""
        loop = asyncio.get_running_loop()
        self._jpeg_waiter = loop.create_future()
        await self.send(proto.MSG_CAM, action="capture", q=12)
        try:
            jpeg = await asyncio.wait_for(self._jpeg_waiter, timeout=CAMERA_TIMEOUT_S)
            log.info("[%s] camera frame: %d bytes", self.device_id, len(jpeg))
            return jpeg
        except asyncio.TimeoutError:
            log.warning("[%s] camera did not respond in %.0fs", self.device_id, CAMERA_TIMEOUT_S)
            return None
        finally:
            self._jpeg_waiter = None

    # ------------------------------------------------------------------
    # Speech output
    # ------------------------------------------------------------------

    async def _say(self, text: str) -> int:
        """Renders and streams speech. Returns time-to-first-audio in ms.

        Sentence by sentence: the robot starts talking as soon as the first
        sentence is rendered rather than waiting for the whole paragraph.
        """
        started = time.monotonic()
        await self.send(proto.MSG_SAY_BEGIN, text=text[:200])
        await self.set_face("speaking")

        first_audio_ms = 0
        sent_any = False

        try:
            streamer = getattr(self.brain.tts, "stream", None)
            if streamer is not None:
                async for speech in streamer(text):
                    if not first_audio_ms:
                        first_audio_ms = int((time.monotonic() - started) * 1000)
                    await self._stream_pcm(speech.samples)
                    sent_any = True
            else:
                speech = await self.brain.tts.synthesize(text)
                first_audio_ms = int((time.monotonic() - started) * 1000)
                await self._stream_pcm(speech.samples)
                sent_any = bool(speech.samples)
        except asyncio.CancelledError:
            # Barge-in mid-sentence. Tell the device to stop so it does not sit
            # waiting for a say_end that will never come.
            await self.send(proto.MSG_SAY_END, reason="interrupted")
            raise

        if not sent_any:
            log.error("[%s] TTS produced no audio for %r", self.device_id, text[:60])

        await self._send_audio_chunk(b"", flags=proto.FLAG_LAST)
        await self.send(proto.MSG_SAY_END)
        await self.set_face("idle")
        return first_audio_ms

    async def _stream_pcm(self, samples: PcmArray) -> None:
        """Sends PCM as protocol frames, paced so the device buffer survives.

        The robot has ~4 seconds of buffer, so we can push well ahead of
        real time - but not infinitely. Yielding every 10 frames (200 ms of
        audio) keeps the event loop responsive without throttling the stream.
        """
        if not samples:
            return
        raw = samples.tobytes()
        for index, chunk in enumerate(proto.chunk_audio(raw)):
            flags = proto.FLAG_FIRST if index == 0 else proto.FLAG_NONE
            await self._send_audio_chunk(chunk, flags=flags)
            if index % 10 == 9:
                await asyncio.sleep(0)

    # ------------------------------------------------------------------

    async def close(self) -> None:
        if self._speaking_task and not self._speaking_task.done():
            self._speaking_task.cancel()
        self._capturing = False
