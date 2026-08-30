<div align="center">

# Wall-E — an open-source desk robot that listens

A small tracked robot with a face, a camera and a voice. It hears you, thinks on
a computer you already own, and answers out loud — **with no cloud account and
no subscription**.

Built on an **ESP32-S3**. MIT licensed, top to bottom.

</div>

---

## What it does

- **Wakes to a spoken word** — detected on the robot itself, offline. No audio
  leaves your house waiting for a wake word.
- **Understands and answers** — speech recognition, a language model and speech
  synthesis, all running on your own PC or Mac.
- **Drives around** on printed tracks, pans its head, and pulls faces on a small
  OLED.
- **Looks at things** — "what do you see?" and it tells you.
- **Turns your lights on** — optional Home Assistant or MQTT control, behind a
  strict allowlist.
- **Remembers you** — your name, your preferences, your last few conversations,
  in a file on your own machine that you can delete at any time.

**Voice commands keep working with the internet unplugged.** Movement, the
camera, the lights and memory are all handled locally. Only free-form
conversation needs a language model, and even that runs on your own machine by
default.

## What it costs

| Build | Cost |
|---|---|
| Voice assistant only | **≈ $42** |
| Full robot, USB powered | **≈ $63** |
| Full robot, battery powered | **≈ $83** |

Plus a computer to be the brain — a Mac Mini, any PC, or a laptop that is
usually on. Full breakdown in [`docs/01-parts-list.md`](docs/01-parts-list.md).

---

# Setup

**You do not need to be a programmer to build this.** You need to be able to
copy and paste commands into a terminal, and to solder about a dozen wires.
Read a step fully before doing it, and do them in order.

Budget an evening for the software and an evening for the hardware.

## What you need first

- A **computer that stays on** when you want to talk to the robot — a Mac Mini,
  a desktop PC, or a laptop. This is the robot's brain.
- The **parts** from [`docs/01-parts-list.md`](docs/01-parts-list.md).
- A **3D printer**, or a friend with one, or a printing service. Files and print
  settings are in [`3d-models/`](3d-models/).
- A soldering iron and about an hour of patience.

---

## Part 1 — Set up the brain (30 minutes)

Everything here happens on your computer, not the robot.

### 1.1 Get the code

```bash
git clone https://github.com/TBC-Platform/TBC-Platform.git walle
cd walle/server
```

### 1.2 Install Python bits

You need Python 3.10 or newer. Check with `python3 --version`.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> Leave this terminal open. Everything below happens in it. If you close it,
> `cd` back to `walle/server` and run `source .venv/bin/activate` again.

### 1.3 Install the three AI engines

**Speech recognition — whisper.cpp**

```bash
# macOS
brew install whisper-cpp

# Linux / Windows (WSL): build it, takes about two minutes
git clone https://github.com/ggerganov/whisper.cpp /tmp/whisper.cpp
cmake -B /tmp/whisper.cpp/build -S /tmp/whisper.cpp
cmake --build /tmp/whisper.cpp/build -j --config Release
sudo cp /tmp/whisper.cpp/build/bin/whisper-cli /usr/local/bin/
```

**Speech synthesis — Piper**

Download the release for your system from
[github.com/rhasspy/piper/releases](https://github.com/rhasspy/piper/releases),
unpack it, and put the `piper` binary somewhere on your `PATH`
(`/usr/local/bin` works).

**The language model — Ollama**

Install from [ollama.com/download](https://ollama.com/download), then:

```bash
ollama pull llama3.2:3b
```

> On a machine with less than 8 GB of RAM, use `llama3.2:1b` instead and set
> `WALLE_LLM_MODEL=llama3.2:1b` in the next step.

**Check all three are found:**

```bash
which whisper-cli piper ollama
```

Three paths should print. If one is missing, that install did not work — fix it
before continuing.

### 1.4 Download the models

```bash
./scripts/fetch_models.sh
```

About 250 MB: the speech recogniser, a voice, and the object detector.

### 1.5 Configure

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Open `.env` in any text editor and paste that random string as
`WALLE_AUTH_TOKEN`. **Keep the terminal open — you need this string again in
Part 2.** It is the password the robot uses to talk to the server.

Check the configuration:

```bash
python3 -m walle.config
```

It prints every setting and lists any problems. Fix anything it complains about.

### 1.6 Start it

```bash
python3 -m walle
```

You should see:

```
Wall-E server starting
listening on ws://0.0.0.0:8765/ws  (stt=whisper.cpp tts=piper llm=ollama ...)
models warm, ready for the first question
```

**Test it without any hardware.** In a second terminal:

```bash
cd walle/server && source .venv/bin/activate
python3 scripts/simulate_robot.py --silence 2 --token "PASTE_YOUR_TOKEN_HERE"
```

A fake robot connects, sends two seconds of silence and prints what the server
says back. If that works, your brain is built. **Do not move on until it does.**

### 1.7 Find your computer's address on the network

```bash
# macOS
ipconfig getifaddr en0
# Linux
hostname -I | awk '{print $1}'
# Windows
ipconfig | findstr IPv4
```

Write down the number — something like `192.168.1.50`. The robot needs it.

---

## Part 2 — Flash the robot (20 minutes)

### 2.1 Install PlatformIO

```bash
pip install platformio
```

### 2.2 Fill in your details

```bash
cd ../firmware
cp include/secrets.example.h include/secrets.h
```

Open `include/secrets.h` and set five things:

| Setting | What to put |
|---|---|
| `WIFI_SSID` | Your Wi-Fi network name |
| `WIFI_PASSWORD` | Your Wi-Fi password |
| `WALLE_SERVER_HOST` | The IP address from step 1.7 |
| `WALLE_AUTH_TOKEN` | The **exact same** random string from step 1.5 |
| `WALLE_OTA_PASSWORD` | Anything you will remember |

> The robot must be on the **same Wi-Fi network** as the server, and on **2.4
> GHz** — ESP32s cannot see 5 GHz networks. If your router names both bands the
> same thing, that is a common and very confusing cause of "it won't connect".

### 2.3 Upload

Plug the board into USB and:

```bash
pio run -e walle-minimal -t upload
pio device monitor
```

`walle-minimal` is a stripped-down build with no camera, no motors and no wake
word — the fastest way to prove the basics work. You should see:

```
Wall-E 1.0.0 booting
[init] wifi ok, ip=192.168.1.61
[link] server ready
```

And in the server terminal: `[walle-01] connected`.

**Press the BOOT button on the board, say something, and let go.** The robot
should answer through your speaker. That is the whole system working.

### 2.4 Switch to the full build

Once the basics work and the hardware is wired up:

```bash
pio run -t upload
```

This enables the camera, the motors and the offline wake word. From now on you
can update the robot over Wi-Fi without a cable — see
[`firmware/README.md`](firmware/README.md).

---

## Part 3 — Build the body

Print the parts, wire it up, screw it together:

- **Printing and assembly:** [`3d-models/README.md`](3d-models/README.md)
- **Wiring, with a diagram:** [`docs/02-wiring.md`](docs/02-wiring.md)

Wire it up on the bench and get it working *before* you close the body — it is
much easier to re-seat a loose wire when you can see it. The wiring guide has a
six-stage bring-up order that saves a lot of guessing.

---

## Talking to it

Say the wake word (**"Hi ESP"** by default — see below), wait for the chirp, then
speak.

| Say | What happens |
|---|---|
| "What's the capital of France?" | Asks the language model |
| "Go forward for two seconds" | Drives — no language model involved |
| "Turn left" · "Stop" · "Dance" | Movement |
| "Look left" · "Look right" | Pans the head |
| "What do you see?" | Takes a photo and describes it |
| "Turn on the desk lamp" | Smart home, if you set it up |
| "My name is Sam" | Remembers it, forever |
| "Remember that I like strong coffee" | Also remembered |
| "Forget everything" | Wipes its memory of you |
| "How much battery do you have?" | Reads its own battery |
| "Goodnight" | Goes to sleep |

**Why "Hi ESP" and not "Wall-E"?** The offline wake-word engine ships with a set
of free stock words, and custom ones need a paid licence. The reasoning, and how
to get a custom word if you want one, is in
[`docs/03-research-notes.md`](docs/03-research-notes.md). You can also set
`WALLE_WAKEWORD_BACKEND=0` and just press the button.

---

## Smart home control

Off by default, and worth reading before turning it on:
[`docs/06-smart-home-security.md`](docs/06-smart-home-security.md).

The short version — locks, garage doors, alarms and water valves can **never**
be controlled by voice, no matter how you configure it. A speech recogniser
mishears sometimes, and "unlock the front door" should not be one homophone
away from something you might say to a toy robot.

To enable it, in `server/.env`:

```bash
WALLE_SMARTHOME_ENABLED=1
WALLE_HA_URL=http://homeassistant.local:8123
WALLE_HA_TOKEN=<token from a dedicated NON-ADMIN Home Assistant user>
WALLE_ALLOWED_ENTITIES=light.desk_lamp,light.office_ceiling
```

Then teach it your names for things, in `server/data/devices.json`:

```json
{ "desk lamp": "light.desk_lamp", "the lamp": "light.desk_lamp" }
```

---

## When something goes wrong

| Symptom | Try this |
|---|---|
| Robot never connects to Wi-Fi | It is a 5 GHz network — ESP32s need 2.4 GHz |
| `[link] error: unauthorised` | The token in `secrets.h` does not exactly match `.env` |
| Connects, then disconnects repeatedly | Wrong IP in `secrets.h`, or a firewall is blocking port 8765 |
| It hears nothing | The mic's L/R pin must be wired to GND. See [`docs/02-wiring.md`](docs/02-wiring.md) |
| It hears me but says nothing | Check `whisper-cli` and `piper` are on your `PATH`; the server logs the error |
| Answers take five seconds | See [`docs/04-latency.md`](docs/04-latency.md) — usually a too-large model |
| Reboots whenever it moves | Missing 470 µF capacitor on the 5 V rail |
| Blank OLED | I2C address is `0x3D` not `0x3C`, or SDA/SCL are swapped |

The server prints a timing breakdown for every single turn, which answers most
"why is it doing that" questions on its own:

```
[walle-01] heard: 'turn on the desk lamp'
[walle-01] turn done: capture=1420ms stt=310ms think=4ms tts_first=180ms total=1914ms via=smarthome
```

---

## Repository layout

```
firmware/     ESP32-S3 code (Arduino framework, built with PlatformIO)
server/       Python brain: speech, language model, vision, smart home
3d-models/    Printable STLs + parametric OpenSCAD sources
docs/         Parts list, wiring, research notes, architecture, security
```

Deeper reading:

| Document | What is in it |
|---|---|
| [`docs/01-parts-list.md`](docs/01-parts-list.md) | Every part, what it costs, why that one |
| [`docs/02-wiring.md`](docs/02-wiring.md) | Diagram, pin map, bring-up order |
| [`docs/03-research-notes.md`](docs/03-research-notes.md) | Wake-word engine comparison, Whisper tuning, latency research |
| [`docs/04-latency.md`](docs/04-latency.md) | Where the time goes and how to measure it |
| [`docs/05-architecture.md`](docs/05-architecture.md) | Layers, protocol, state machine, failure behaviour |
| [`docs/06-smart-home-security.md`](docs/06-smart-home-security.md) | Threat model and the four rules |

## Contributing

```bash
cd server && pip install -r requirements-dev.txt && python3 -m pytest && python3 -m ruff check .
cd ../3d-models && python3 -m pytest tools -q
python3 docs/check_bom.py
```

Everything runs without hardware. The test suite covers the wire protocol, the
intent router, the smart home allowlist, the full session pipeline with fake
engines, and every 3D part's watertightness.

## Licence

MIT — see [`LICENSE`](LICENSE). Firmware, server, models and documentation.
Build it, sell it, remix it.

Wall-E is a character owned by Disney/Pixar. This is an unaffiliated hobby
project inspired by the design; the name is used descriptively, and nothing here
is endorsed by them.
