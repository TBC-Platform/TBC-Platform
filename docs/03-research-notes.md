# Research notes

The four questions this project set out to answer, and what the answers led to
in the code.

**On numbers in this document:** figures marked *measured* come from published
benchmarks by the projects themselves or from widely reproduced community
results, and are cited. Figures marked *budget* are this design's targets,
derived from those benchmarks — they are what you should expect, not what
anyone measured on your desk. [`04-latency.md`](04-latency.md) explains how to
measure your own build, and the server prints a per-stage breakdown for every
turn so you can check the budget against reality in about thirty seconds.

---

## 1. What is the lowest-latency wake-word → response setup on an ESP32?

**Short answer:** wake word on-device, everything else on a server, over one
persistent WebSocket, with audio streamed *while the user is still speaking*.
That gets you to ≈1 second perceived latency. The single biggest win is not in
the pipeline at all — it is playing an acknowledgement chirp locally the
instant the wake word fires.

### Where the time actually goes

The naive implementation — detect wake word, record until silence, upload a WAV,
wait, download an MP3, play it — spends most of its time waiting for things that
could have overlapped:

| Stage | Naive | This design | How |
|---|---|---|---|
| Wake word detect | 200 ms | 200 ms | Unavoidable; it is part of the model |
| Record utterance | 2000 ms | *overlapped* | Audio streams in 20 ms frames as it is captured |
| Upload | 400 ms | ~0 ms | Already uploaded by the time speech ends |
| End-of-speech detect | 1500 ms | 700 ms | On-device VAD with a 700 ms tail, not a fixed timeout |
| STT | 900 ms | 400 ms | Greedy decode, quantised model, warm process |
| LLM | 1200 ms | 600 ms | Small local model, kept resident, short reply cap |
| TTS | 800 ms | 200 ms | Sentence-by-sentence: only the *first* sentence blocks |
| Download + play | 300 ms | ~20 ms | Streamed, device buffers ahead |
| **Total** | **≈ 7.3 s** | **≈ 2.1 s** | *(budget)* |

And of that 2.1 s, the user perceives roughly **0.2 s**, because the robot
chirps immediately on the wake word and shows a listening face. Perceived
latency is what matters; the rest is the robot "thinking", which people
tolerate happily as long as something acknowledged them.

### The seven decisions that follow from this

1. **Stream audio during capture, not after.** `main.cpp` sends each 20 ms frame
   the moment I2S produces it. Upload time disappears into the time the user
   spends talking.
2. **Acknowledge locally and instantly.** `audio_out::playWakeChirp()` generates
   two sine tones in a few milliseconds from local memory. This costs nothing and
   is worth more than every other optimisation combined.
3. **Send a pre-roll.** The 320 ms of audio *before* the wake word finished
   scoring is buffered and sent first (`VOICE_PREROLL_MS`), so the recogniser
   hears the whole sentence. Without it, "Wall-E, turn on the lamp" arrives as
   "urn on the lamp" and you pay a full extra turn to the misunderstanding.
4. **Detect end-of-speech on the device.** A 700 ms silence tail
   (`VOICE_SILENCE_TAIL_MS`) beats any fixed recording length. Shorter starts
   clipping people mid-thought; longer is noticeably sluggish.
5. **Drop false wakes before they cost anything.** If the device's VAD heard no
   speech at all, `utt_end` carries `speech: false` and the server skips the
   whole pipeline. False wakes become free instead of costing a Whisper run.
6. **One persistent WebSocket.** A fresh TCP+TLS handshake is 100–300 ms on a
   home network, per turn. The socket stays open, with a heartbeat to notice
   when it silently dies.
7. **Speak sentence by sentence.** `PiperTts.stream()` yields each sentence as
   it renders. Time-to-first-word depends on the first sentence only, not the
   whole answer.

### What is deliberately *not* done

- **No on-device STT.** An ESP32-S3 can run a tiny command recogniser, but not
  open-vocabulary speech recognition. Trying costs you accuracy, flexibility and
  most of your RAM.
- **No compression on the audio uplink.** 16 kHz mono PCM is 256 kbit/s. Wi-Fi
  has that to spare, and every codec adds encode and decode latency at both ends
  for no benefit inside a LAN.
- **No streaming (partial-result) STT.** It would save perhaps 200 ms on the
  recognition stage and roughly triples the complexity of the session layer.
  Worth revisiting; not worth starting with.

---

## 2. Best offline wake-word engine for ESP32: Porcupine vs openWakeWord vs ESP-SR

**Answer: ESP-SR (WakeNet), and it is not close — on an ESP32-S3.**

| | **ESP-SR (WakeNet 9)** | **Porcupine** | **openWakeWord** |
|---|---|---|---|
| Runs on ESP32-S3? | **Yes, natively** | Yes (ported) | **No** |
| Runs on plain ESP32? | No (needs S3 vector ops) | Yes | No |
| Licence | Apache 2.0 (models: Espressif free-use) | Apache 2.0 SDK, **commercial licence for custom keywords** | **Apache 2.0, fully free** |
| Custom wake word | Paid service from Espressif | Paid tier | **Free, train your own** |
| Cloud needed at runtime | No | No | No |
| RAM on device | ~200 KB (internal) | ~100 KB | ~50 MB — server class |
| CPU on an S3 | ~15 % of one core | ~20 % | n/a |
| Latency | ~200 ms *(measured, Espressif)* | ~150 ms *(measured, Picovoice)* | ~250 ms on a PC |
| Accuracy | Very good on its stock words | Excellent | Good; improves with training |
| Stock keywords | "Hi ESP", "Alexa", "Hi Lexin", others | ~15 free English words | Community models |

### Why ESP-SR wins here

It is the only one of the three **designed for this chip**. WakeNet uses the
S3's vector instructions, ships as part of the Arduino core, and Espressif ships
several free stock keywords. Everything else on the board keeps working while it
runs. The firmware uses it through the raw `esp_wn_iface` C API rather than the
Arduino `ESP_SR` wrapper, because the wrapper wants to own I2S and this project
needs the same microphone stream for capture as well as detection.

**Porcupine** is genuinely excellent and slightly faster, and it is the right
answer if you are on a plain ESP32 rather than an S3. The catch is licensing:
custom keywords — that is, actually saying "Wall-E" — require a paid plan. For
an MIT-licensed project people are meant to fork, that is a bad foundation.

**openWakeWord is the wrong tool for a microcontroller and the right tool for
your server.** It needs tens of megabytes of RAM and a full Python runtime. But
it trains free custom wake words from synthetic speech in about an hour on a
laptop — so if you genuinely want the robot to answer to "Wall-E", the practical
route is: run openWakeWord *on the server*, stream a low-rate audio preview from
the device, and use ESP-SR's "Hi ESP" as a cheap first-stage gate. This project
does not ship that, because it doubles the always-on network traffic for a
cosmetic gain, but the architecture leaves room for it.

### The practical recommendation

| Situation | Use |
|---|---|
| ESP32-S3, happy with "Hi ESP" | **ESP-SR** — what this repo ships |
| ESP32-S3, must say "Wall-E" | ESP-SR as a gate + openWakeWord on the server |
| Plain ESP32 (no S3) | Porcupine, and accept the licence terms |
| No wake word wanted at all | `WALLE_WAKEWORD_BACKEND=0` — push-to-talk on the BOOT button |

Set `DET_MODE_95` instead of `DET_MODE_90` in `wakeword.cpp` if the robot wakes
up to the television. It is the one knob that matters.

---

## 3. How do you optimise whisper.cpp for real-time STT alongside an ESP32?

The single most useful realisation: **you are not transcribing podcasts, you are
transcribing three-second commands.** Almost every default in whisper.cpp is
tuned for long-form accuracy, and almost every one of them can be traded for
latency here with barely measurable accuracy loss.

### The flags that matter, in order of payoff

| Change | Effect | Why |
|---|---|---|
| **`-bs 1 -bo 1`** (greedy) | **−40 % latency** | Beam search buys 1–2 % WER on long-form audio. On "turn on the desk lamp" it buys essentially nothing. This is the biggest single win. |
| **Quantised model** (`q5_1`) | **−35 % latency, −55 % RAM** | `ggml-base.en-q5_1.bin` is 57 MB against 148 MB, with WER within noise of the full model. |
| **`.en` model, not multilingual** | **−15 % latency** | An English-only model does not spend capacity deciding which language it heard. |
| **Right thread count** | ±20 % | 4 on Apple Silicon (performance cores only), physical-core count on x86. More threads past that makes it *slower*. |
| **Keep the process warm** | −300 ms on the first call | `warmup()` runs 200 ms of silence at startup so the weights are in the page cache. |
| **Trim silence before decoding** | −10–30 % | Decode time scales with audio length. The device's VAD already trims the tail; `faster-whisper`'s `vad_filter` does the same in-process. |
| **`--prompt` with your vocabulary** | Accuracy, not speed | Feeding it "Wall-E, desk lamp, kitchen" makes it stop hearing "wally" and "death lamp". Free. |

### Which model

| Model | Size (q5_1) | Latency, 3 s clip | Verdict |
|---|---|---|---|
| `tiny.en` | 32 MB | ~150 ms | Too many errors on names and nouns |
| **`base.en`** | **57 MB** | **~400 ms** | **The default here.** Best latency-per-error point for commands |
| `small.en` | 190 MB | ~1100 ms | Noticeably better on accented speech. Worth it if `base.en` mishears you |
| `medium.en` | 540 MB | ~3000 ms | Not for interactive use |

*(Latencies: Apple Silicon M2 class, Metal enabled, greedy decode — budget
figures derived from whisper.cpp's published benchmarks.)*

### Platform notes

**Mac Mini (M1/M2/M4).** Build with Metal on, which is the default now:

```bash
git clone https://github.com/ggerganov/whisper.cpp && cd whisper.cpp
cmake -B build -DGGML_METAL=ON && cmake --build build -j --config Release
```

Use whisper.cpp here, not faster-whisper — CTranslate2 has no Metal backend and
runs on CPU only, which is roughly 3× slower on this hardware. Set
`WALLE_STT_THREADS=4`; the efficiency cores add contention, not throughput.

**PC with an NVIDIA GPU.** Use `faster-whisper` instead
(`WALLE_STT_BACKEND=faster-whisper`). CTranslate2's CUDA path beats whisper.cpp
comfortably, and `compute_type="float16"` on a 6 GB card gives you `small.en` at
`base.en` speeds.

**CPU-only x86.** whisper.cpp with `q5_1`, threads set to your *physical* core
count. Check that your build picked up AVX2 — `whisper-cli` prints its enabled
instruction sets at startup, and a build without AVX2 is about half speed.

**Raspberry Pi 5.** `tiny.en` or `base.en` at roughly 2–4× real time. Fine for
commands, painful for conversation. Do not try to run the LLM on the same board.

### What the server does with all this

`walle/stt/whispercpp.py` runs `whisper-cli` as a subprocess with these flags
and `-oj` for JSON output. The subprocess is deliberate: process startup is
~15 ms, and in exchange a malformed audio buffer cannot take the server down,
installation is "put the binary on PATH", and you can swap in a new whisper.cpp
build without touching Python. An `asyncio.Lock` serialises calls, because two
concurrent transcriptions on the same cores make both slower.

---

## 4. What is the safest way to control smart home devices without exposing the home network?

Full treatment in [`06-smart-home-security.md`](06-smart-home-security.md). The
answer in four rules, all enforced in code:

1. **Never open a port.** The robot connects out to the server; the server
   connects out to Home Assistant. Nothing listens on the internet, so there is
   no inbound attack surface. Remote access, if you want it, is a VPN
   (WireGuard or Tailscale) — never a port forward.
2. **Allowlist, and fail closed.** Only entities in `WALLE_ALLOWED_ENTITIES` can
   be touched. An empty list refuses everything rather than allowing everything
   (`smarthome/allowlist.py`, and the tests that prove it).
3. **Some things are never voice-controllable.** Locks, garage doors, alarm
   panels, water valves and cameras are refused unconditionally — not
   configurable, because a speech recogniser is a machine that occasionally
   mishears, and "unlock the front door" is not a sentence that should ever be
   one homophone away.
4. **Least-privilege credentials.** The Home Assistant token belongs to a
   dedicated non-admin user with access only to the allowlisted entities. If the
   server is compromised, the attacker gets your lamps.

Plus one thing that is not security but is what makes security debuggable: every
attempted action, allowed or refused, is written to a local audit log you can
read at `GET /events`.
