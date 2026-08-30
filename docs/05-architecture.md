# Architecture

## The one decision everything follows from

**The ESP32 is a sensor and an actuator. The server is the brain.**

An ESP32-S3 has 512 KB of internal RAM and no floating-point throughput worth
speaking of. It can capture audio, detect a wake word, drive servos and draw a
face — genuinely well. It cannot run speech recognition over an open
vocabulary, a language model, or object detection worth the name. Every attempt
to make it do so trades away accuracy, flexibility and memory for a bragging
right.

So the split is: **roughly 80 % of the compute lives off-device**, and the 20 %
that stays is the part that must be instant, private, or work when the network
is down.

## Layers

```
┌──────────────────────── ESP32-S3 ──────────────────────────┐
│                                                            │
│  hal/       audio_in  audio_out  display  motors  camera   │
│             power                                          │
│                     ▲                                      │
│  voice/     wakeword (ESP-SR, offline)  vad                 │
│                     ▲                                      │
│  main.cpp   state machine: IDLE ⇄ LISTENING → THINKING      │
│                                    → SPEAKING              │
│                     ▲                                      │
│  net/       ws_link  wifi_manager  ota                     │
└─────────────────────┬──────────────────────────────────────┘
                      │  one WebSocket over Wi-Fi
                      │  JSON control + binary audio/JPEG
┌─────────────────────┴──────────── server ──────────────────┐
│  app.py       FastAPI: /ws, /health, /events, /history     │
│                     ▲                                      │
│  session.py   orchestration: one robot, one turn at a time │
│                     ▲                                      │
│  intent.py    offline routing ─────► device + home actions │
│                     │ (only open questions fall through)   │
│                     ▼                                      │
│  stt/  tts/  llm/  vision/    swappable inference backends │
│  memory/      SQLite: history, facts, audit log            │
│  smarthome/   allowlisted control (HA or MQTT)             │
└────────────────────────────────────────────────────────────┘
```

Each layer only knows about the one below it. `session.py` never imports
`httpx`; `whispercpp.py` never imports FastAPI. That is what makes the backends
genuinely swappable — `WALLE_STT_BACKEND=faster-whisper` changes one line in a
factory function and nothing else.

## Why an offline intent router sits in front of the LLM

Because most of what people say to a desk robot is not a question.

"Stop." "Turn on the lamp." "Look left." "Remember my name is Sam." These are
commands with exactly one correct interpretation, and routing them through a
language model buys you: a second of latency, a dependency on the network, a
non-zero chance of the model deciding to be creative, and — with a cloud
backend — a bill.

`intent.py` matches them by pattern and executes them directly. Only genuinely
open-ended input reaches the LLM. This is what lets the project claim that
**voice commands work with the internet unplugged**: unplug it, and everything
except free-form chat still works exactly as before.

## The link protocol

One WebSocket, two frame types.

**Text frames** are JSON control messages, each with a `t` field:

| Direction | Message | Purpose |
|---|---|---|
| → server | `hello` | Version, capabilities, auth token |
| → server | `wake` | Wake word fired |
| → server | `utt_begin` / `utt_end` | Utterance boundaries; `utt_end` carries whether any speech was heard |
| → server | `cam_meta` | A JPEG is about to arrive, and how big it is |
| → server | `state` | Battery, RSSI, heap, uptime — every 5 s |
| → server | `log` | Device-side diagnostics |
| → device | `hello_ack` | Protocol version, which engines are loaded |
| → device | `face` | Show an expression |
| → device | `say_begin` / `say_end` | Speech is starting / finished |
| → device | `turn_end` | This turn is over. Sent even when nothing was said, so the device never waits on a timeout |
| → device | `move` / `head` | Drive the tracks / pan the head |
| → device | `cam` | Capture a still |
| → device | `ota` | Fetch and flash a firmware image |
| → device | `error` | Something went wrong; say so with a face |

**Binary frames** carry audio and JPEG behind an 8-byte header (magic, type,
flags, sequence, length). Audio is 16 kHz mono S16LE **in both directions**, so
the ESP32 never resamples — Piper's 22.05 kHz output is downsampled on the
server, where CPU is free.

The constants live in two places by necessity —
[`firmware/src/core/protocol.h`](../firmware/src/core/protocol.h) and
[`server/walle/protocol.py`](../server/walle/protocol.py). `test_protocol.py`
reads the C header and fails if they disagree, which turns a class of bug that
would otherwise appear as garbled audio on real hardware into a failing test.

## The device state machine

```
BOOT → CONNECTING → IDLE ⇄ LISTENING → THINKING → SPEAKING
                     ↑                                │
                     └────────────────────────────────┘
```

- **IDLE** — wake word running on every frame, face blinking.
- **LISTENING** — streaming 20 ms frames, VAD watching for the end.
- **THINKING** — the server owns this. It ends when speech starts arriving or
  when `turn_end` says the turn produced none; the 20 s timeout is a
  backstop for a server that vanished mid-turn, not the normal exit.
- **SPEAKING** — wake word muted (otherwise the robot hears itself say its own
  name and wakes up in a loop), buffer draining, mouth animating to the audio
  envelope.

The microphone paces the whole loop: `audio_in::readFrame()` blocks until I2S
has exactly 20 ms of audio, which gives a steady 50 Hz main loop with no
`delay()` anywhere in the firmware and no risk of the display or the network
starving the audio path.

## Failure behaviour

Every layer degrades rather than stopping:

| Failure | What happens |
|---|---|
| Wi-Fi drops | Motors stop, face goes to sleep, exponential-backoff reconnect |
| Server unreachable | Same; the device retries forever without rebooting |
| Wake word model missing | Falls back to push-to-talk on the BOOT button |
| Camera fails to init | Vision disabled, everything else works |
| OLED missing | Logged, everything else works |
| Microphone fails | **Fatal** — this is the one thing there is no point continuing without |
| LLM unreachable | Robot says "I cannot reach my thinking box"; offline intents still work |
| TTS produces nothing | Logged loudly; the turn still completes |
| Battery critical | Motion inhibited, voice keeps working |
| OTA fails | Old firmware still in the other flash slot; the robot reboots into it |

## Extending it

| To add | Touch |
|---|---|
| A new expression | `FACE_NAMES` + a case in `drawFace()`; `protocol.FACES` |
| A new voice command | One matcher in `intent.py`, one handler in `session.py` |
| A different STT engine | New class in `stt/`, one line in `build_stt()` |
| A different LLM | New class in `llm/`, one line in `build_llm()` |
| A new sensor | New module in `hal/`, read it in `loop()`, add it to `sendState()` |
| A new device command | One message type in both protocol files, one handler in each |
