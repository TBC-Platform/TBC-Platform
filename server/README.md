# Server — the robot's brain

Python 3.10+. FastAPI, one WebSocket, swappable inference backends.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set WALLE_AUTH_TOKEN
./scripts/fetch_models.sh
python3 -m walle
```

Check your configuration at any time:

```bash
python3 -m walle.config       # prints every setting, masks secrets, lists problems
```

## Test it without a robot

```bash
python3 scripts/simulate_robot.py --wav question.wav --token "$WALLE_AUTH_TOKEN"
```

A fake robot that speaks the real protocol: it connects, streams your WAV as
20 ms frames, prints every command the server sends back (faces, movement,
camera requests) and writes the reply audio to `reply.wav`. Add `--realtime` to
pace the upload at 1× as real hardware would, or `--silence 2` to send silence
and just prove the link works.

## Layout

```
walle/
  app.py          FastAPI: /ws, /health, /events, /history
  session.py      one connected robot; the whole turn pipeline
  intent.py       offline command routing (no LLM involved)
  protocol.py     wire format, mirrored in firmware/src/core/protocol.h
  config.py       every setting, from the environment or .env
  audio.py        WAV, resampling, the robot voice effect
  stt/            whisper.cpp | faster-whisper
  tts/            piper
  llm/            ollama | any OpenAI-compatible API
  vision/         TensorFlow Lite object detection
  memory/         SQLite: history, facts, audit log
  smarthome/      Home Assistant | MQTT, behind an allowlist
  tools/          spoken device names -> entity ids
scripts/
  fetch_models.sh     downloads the models
  simulate_robot.py   a robot, without the robot
```

## Endpoints

| Endpoint | Purpose |
|---|---|
| `WS /ws` | The robot's connection. Requires `X-Walle-Token`. |
| `GET /health` | Which engines loaded, which devices are online |
| `GET /events` | Audit log: every smart home action, allowed and refused |
| `GET /history?device=walle-01` | Recent conversation turns |

## Swapping engines

Every backend is chosen by one environment variable and built by one factory
function. Nothing else in the codebase knows the difference.

```bash
WALLE_STT_BACKEND=faster-whisper     # better on an NVIDIA GPU
WALLE_LLM_BACKEND=openai             # cloud instead of local
WALLE_TTS_ROBOT=0.0                  # plain voice instead of the robot buzz
WALLE_VISION_ENABLED=0               # skip object detection entirely
```

To add your own: subclass the base in `stt/base.py`, `tts/base.py` or
`llm/base.py`, then add one line to the corresponding `build_*()` function.

## Optional extras

```bash
pip install tflite-runtime Pillow numpy   # object detection ("what do you see?")
pip install faster-whisper                # alternative STT, good on CUDA
pip install aiomqtt                       # MQTT smart home backend
```

Each is genuinely optional: without them the feature degrades with a clear log
line rather than crashing.

## Tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest -q          # 156 tests, no hardware, no network
python3 -m ruff check .
```

Coverage is concentrated where mistakes are expensive: the wire format
(the C header is both parsed for constant drift *and* compiled with `g++
-Werror` so the real C encoder and decoder are checked against the Python ones
byte for byte), the smart home allowlist, and the full session pipeline driven
end to end with fake engines.
