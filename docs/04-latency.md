# Latency: the budget, and how to measure yours

The server logs one line per turn:

```
15:04:31 INFO  session        [walle-01] turn done: capture=2140ms stt=380ms think=610ms tts_first=190ms total=3320ms via=chat
```

That is the whole measurement story. `capture` is how long the person spoke,
so the part you control is everything after it.

## The budget

Targets for a Mac Mini M2 class server on a quiet 5 GHz network, answering
"what's the capital of France" with a local 3B model:

| Stage | Target | What it covers |
|---|---|---|
| wake → chirp | **< 250 ms** | WakeNet detection, then local audio. The only number the user feels. |
| end of speech → `utt_end` | 700 ms | The VAD's silence tail. Fixed by `VOICE_SILENCE_TAIL_MS`. |
| `stt` | 300–500 ms | whisper.cpp, `base.en-q5_1`, greedy |
| `think` | 400–900 ms | Local 3B model, ~40 tokens out. **0–5 ms for an offline intent.** |
| `tts_first` | 150–300 ms | Piper, first sentence only |
| first audio → speaker | 20–40 ms | WebSocket + device buffer |
| **end of speech → first word** | **≈ 1.6–2.4 s** | |

Two things make the numbers look very different in practice:

- **Offline intents skip almost all of it.** "Turn on the desk lamp" never
  touches STT's slower paths or the LLM at all: `via=smarthome`, `think` in
  single-digit milliseconds. Device and home commands land in well under a
  second.
- **Perceived latency is the chirp**, not the total. The robot answers within a
  quarter second, every time, and then thinks out loud with a face.

## Reading the log

| Field | If it is high | Look at |
|---|---|---|
| `capture` | Nothing wrong — that is how long the person talked | — |
| `stt` | > 1 s | Wrong model size, beam search on, cold process, too many threads |
| `think` | > 2 s | Model not resident (`keep_alive`), model too large, `max_tokens` too high |
| `think` on `via=chat` only | Expected | Offline intents should be ~0 |
| `tts_first` | > 600 ms | Piper model too large, or the first sentence is enormous |
| `total` ≫ sum of parts | Network | Wi-Fi retries, or the device is far from the AP |

## Measuring it properly

You do not need the robot. The simulator speaks the real protocol:

```bash
cd server
# 1. Record a question (any tool; 16 kHz mono WAV is ideal but it resamples)
# 2. Send it and time the whole round trip
time python3 scripts/simulate_robot.py --wav question.wav --token "$WALLE_AUTH_TOKEN"
```

It prints every control message the server sends — faces, moves, camera
requests — and writes the reply audio to `reply.wav`. Compare `time`'s wall
clock against the server's per-stage line and you can see exactly where a slow
turn went.

To isolate one stage:

```bash
# STT alone
time whisper-cli -m models/ggml-base.en-q5_1.bin -f question.wav -bs 1 -bo 1 -nt

# LLM alone
time curl -s http://127.0.0.1:11434/api/chat -d '{
  "model":"llama3.2:3b","stream":false,
  "messages":[{"role":"user","content":"What is the capital of France?"}]}' | tail -c 200

# TTS alone
time (echo "Paris is the capital of France." | piper --model models/en_US-lessac-medium.onnx --output_file /tmp/o.wav)
```

## If it is too slow

In the order worth trying:

1. **Check the model is resident.** `ollama ps` should list your model. If it is
   not there, the first turn pays several seconds of loading. The server's
   `warmup()` handles this at startup — if you restarted Ollama since, restart
   the server too.
2. **Use a smaller LLM.** `llama3.2:3b` → `llama3.2:1b` roughly halves `think`.
   For a desk robot that answers in two sentences, 1B is often enough.
3. **Cap the reply length.** `WALLE_LLM_MAX_TOKENS=100`. Every token is both
   generation time *and* speech the user has to sit through.
4. **Check your STT flags.** `WALLE_STT_BEAM=1`. If you changed it, change it
   back.
5. **Thread count.** `WALLE_STT_THREADS=4` on Apple Silicon, physical cores on
   x86. More is not better.
6. **Move the robot closer to the access point**, or put the server on ethernet.
   `rssi` in the state messages tells you; below −75 dBm you will see retries.
7. **Turn off the robot voice effect** (`WALLE_TTS_ROBOT=0`) if you are on a
   very slow machine. It is a pure-Python pass over the samples — a few
   milliseconds normally, more on a Pi.

## Things that are *not* worth optimising

- **Audio compression.** 256 kbit/s of PCM is nothing on Wi-Fi, and every codec
  adds latency at both ends.
- **The 20 ms frame size.** Smaller frames mean more TCP writes for no
  perceptible gain; larger ones add latency directly.
- **The display refresh.** 20 fps on an I2C OLED costs about 3 ms per frame and
  runs between audio frames.
- **Micro-optimising the resampler.** It runs once per sentence and takes single
  digit milliseconds. Whisper takes four hundred.
