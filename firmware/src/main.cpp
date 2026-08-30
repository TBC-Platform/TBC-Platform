// SPDX-License-Identifier: MIT
// ---------------------------------------------------------------------------
// Wall-E desk robot - main orchestration loop.
//
// Design in one paragraph: the microphone is the clock. audio_in::readFrame()
// blocks until the I2S DMA has exactly 20 ms of audio, so the loop below runs
// at a steady 50 Hz without a single delay() call, and every subsystem gets
// serviced between frames. The ESP32 does only what an ESP32 is good at -
// capturing audio, detecting the wake word offline, moving servos, drawing a
// face - and ships everything else to the server over one WebSocket.
//
// State machine:
//
//   BOOT -> CONNECTING -> IDLE <-> LISTENING -> THINKING -> SPEAKING
//                          ^                                    |
//                          +------------------------------------+
//
// Anything can drop back to CONNECTING if the link dies, and to UPDATING when
// an OTA starts.
// ---------------------------------------------------------------------------
#include <Arduino.h>

#include "config.h"
#include "core/protocol.h"
#include "hal/audio_in.h"
#include "hal/audio_out.h"
#include "hal/camera.h"
#include "hal/display.h"
#include "hal/motors.h"
#include "hal/power.h"
#include "net/ota.h"
#include "net/wifi_manager.h"
#include "net/ws_link.h"
#include "secrets.h"
#include "voice/vad.h"
#include "voice/wakeword.h"

namespace {

enum State : uint8_t {
  ST_BOOT,
  ST_CONNECTING,
  ST_IDLE,
  ST_LISTENING,
  ST_THINKING,
  ST_SPEAKING,
  ST_UPDATING,
};

State gState = ST_BOOT;
uint32_t gStateSince = 0;

int16_t gFrame[WALLE_AUDIO_FRAME_SAMPLES];
uint32_t gUttStart = 0;
bool gWakewordAvailable = false;

// Button (BOOT) handling: short press = push to talk, 3 s hold = reboot.
bool gBtnDown = false;
uint32_t gBtnDownAt = 0;
bool gBtnConsumed = false;

uint32_t gLastStateReport = 0;
uint32_t gUnmuteAt = 0;      // re-arm the wake word after Wall-E stops talking
bool gPendingOta = false;
char gOtaUrl[192] = {0};

// The server sets this while it is streaming speech; we only leave SPEAKING
// once the server has said it is done AND the local buffer has drained.
bool gServerSaidDone = true;

const char *stateName(State s) {
  switch (s) {
    case ST_BOOT: return "boot";
    case ST_CONNECTING: return "connecting";
    case ST_IDLE: return "idle";
    case ST_LISTENING: return "listening";
    case ST_THINKING: return "thinking";
    case ST_SPEAKING: return "speaking";
    case ST_UPDATING: return "updating";
  }
  return "?";
}

void setState(State s) {
  if (gState == s) return;
  gState = s;
  gStateSince = millis();

  switch (s) {
    case ST_CONNECTING:
      display::setExpression(EXPR_SLEEP);
      display::setStatusLine("connecting");
      break;
    case ST_IDLE:
      display::setExpression(EXPR_IDLE);
      display::setStatusLine(nullptr);
      wakeword::setMuted(false);
      break;
    case ST_LISTENING:
      display::setExpression(EXPR_LISTENING);
      break;
    case ST_THINKING:
      display::setExpression(EXPR_THINKING);
      break;
    case ST_SPEAKING:
      display::setExpression(EXPR_SPEAKING);
      break;
    default:
      break;
  }
  Serial.printf("[state] -> %s\n", stateName(s));
}

// --------------------------- utterance capture -----------------------------

void beginUtterance(const char *reason) {
  if (!ws_link::isConnected()) return;

  // Acknowledge instantly. The chirp plays from local memory in a couple of
  // milliseconds, so the user gets feedback long before the server has done
  // anything. This one line is worth more perceived responsiveness than any
  // amount of pipeline tuning.
  audio_out::playWakeChirp();

  vad::reset();
  gUttStart = millis();
  ws_link::sendUttBegin();

  // Ship the pre-roll first so the recogniser hears the whole sentence,
  // including the syllables spoken before the wake word finished scoring.
  static int16_t preroll[(WALLE_AUDIO_SAMPLE_RATE * VOICE_PREROLL_MS) / 1000];
  const size_t n = audio_in::copyPreroll(preroll, sizeof(preroll) / sizeof(preroll[0]));
  for (size_t off = 0; off < n; off += WALLE_AUDIO_FRAME_SAMPLES) {
    const size_t chunk = (n - off) > WALLE_AUDIO_FRAME_SAMPLES
                             ? WALLE_AUDIO_FRAME_SAMPLES
                             : (n - off);
    ws_link::sendAudio(preroll + off, chunk,
                       off == 0 ? WALLE_FLAG_FIRST : WALLE_FLAG_NONE);
  }

  setState(ST_LISTENING);
  Serial.printf("[voice] listening (%s), preroll %u samples\n", reason, (unsigned)n);
}

void endUtterance(bool speechDetected) {
  const uint32_t ms = millis() - gUttStart;
  ws_link::sendAudio(nullptr, 0, WALLE_FLAG_LAST);
  ws_link::sendUttEnd(ms, speechDetected);

  if (!speechDetected) {
    // Nothing but silence: almost certainly a false wake. Don't make the
    // server pay for a transcription, just go back to sleep.
    display::setExpression(EXPR_CONFUSED, 700);
    setState(ST_IDLE);
    Serial.println("[voice] no speech, discarding");
    return;
  }
  setState(ST_THINKING);
}

// ------------------------- server message handling -------------------------

void handleControl(const char *type, JsonObjectConst msg) {
  if (!strcmp(type, MSG_HELLO_ACK)) {
    const int proto = msg["proto"] | 0;
    if (proto != WALLE_PROTO_VERSION) {
      Serial.printf("[link] protocol mismatch: server=%d device=%d\n",
                    proto, WALLE_PROTO_VERSION);
      display::showBanner("Version mismatch", "update firmware");
      return;
    }
    Serial.println("[link] server ready");
    setState(ST_IDLE);

  } else if (!strcmp(type, MSG_FACE)) {
    const Expression e = display::expressionFromName(msg["e"] | "");
    if (e != EXPR_COUNT) {
      display::setExpression(e, msg["ms"] | 0);
    }

  } else if (!strcmp(type, MSG_SAY_BEGIN)) {
    gServerSaidDone = false;
    // Mute the wake word while we talk, or Wall-E hears himself say "Wall-E"
    // and wakes up in an infinite loop. (Ask how we know.)
    wakeword::setMuted(true);
    setState(ST_SPEAKING);
    const char *text = msg["text"] | "";
    if (*text) Serial.printf("[say] %s\n", text);

  } else if (!strcmp(type, MSG_SAY_END)) {
    gServerSaidDone = true;

  } else if (!strcmp(type, MSG_MOVE)) {
    const MoveCmd cmd = motors::cmdFromName(msg["cmd"] | "stop");
    motors::move(cmd, msg["speed"] | 60, msg["ms"] | 0);

  } else if (!strcmp(type, MSG_HEAD)) {
    motors::setHead(msg["deg"] | 90);

  } else if (!strcmp(type, MSG_CAM)) {
#if WALLE_ENABLE_CAMERA
    if (!camera::isReady()) {
      ws_link::sendLog("warn", "camera not available");
      return;
    }
    const uint8_t q = msg["q"] | 12;
    camera::setQuality(q);
    size_t len = 0;
    uint16_t w = 0, h = 0;
    const uint8_t *jpeg = camera::capture(&len, &w, &h);
    if (jpeg) {
      ws_link::sendJpeg(jpeg, len, w, h);
      camera::release();
      Serial.printf("[cam] sent %ux%u, %u bytes\n", w, h, (unsigned)len);
    } else {
      ws_link::sendLog("error", "capture failed");
    }
#else
    ws_link::sendLog("warn", "camera disabled in this build");
#endif

  } else if (!strcmp(type, MSG_OTA)) {
    const char *url = msg["url"] | "";
    if (*url) {
      // Do not flash from inside the WebSocket callback - finish this loop
      // iteration first so the socket can close cleanly.
      strncpy(gOtaUrl, url, sizeof(gOtaUrl) - 1);
      gOtaUrl[sizeof(gOtaUrl) - 1] = '\0';
      gPendingOta = true;
    }

  } else if (!strcmp(type, MSG_ERROR)) {
    const char *m = msg["msg"] | "server error";
    Serial.printf("[link] error: %s\n", m);
    display::setExpression(EXPR_CONFUSED, 1500);
    if (gState == ST_THINKING || gState == ST_SPEAKING) setState(ST_IDLE);

  } else {
    Serial.printf("[link] unknown message '%s'\n", type);
  }
}

void handleAudio(const int16_t *samples, size_t count, uint8_t flags) {
  if (count) {
    const size_t accepted = audio_out::write(samples, count);
    if (accepted < count) {
      // Buffer full. Dropping the tail of a frame is inaudible; stalling the
      // WebSocket loop to wait for room is not.
      Serial.println("[audio] playback buffer full, dropped samples");
    }
  }
  if (flags & WALLE_FLAG_LAST) gServerSaidDone = true;
}

// ------------------------------- button ------------------------------------

void serviceButton() {
  // BOOT button is active-low with an internal pull-up.
  const bool down = (digitalRead(PIN_BUTTON) == LOW);
  const uint32_t now = millis();

  if (down && !gBtnDown) {
    gBtnDown = true;
    gBtnDownAt = now;
    gBtnConsumed = false;
  } else if (down && gBtnDown && !gBtnConsumed && (now - gBtnDownAt) > 3000) {
    gBtnConsumed = true;
    display::showBanner("Rebooting...");
    delay(300);
    ESP.restart();
  } else if (!down && gBtnDown) {
    gBtnDown = false;
    const uint32_t heldMs = now - gBtnDownAt;
    if (!gBtnConsumed && heldMs > 40) {  // 40 ms debounce
      if (gState == ST_IDLE) {
        beginUtterance("button");
      } else if (gState == ST_SPEAKING) {
        // Barge-in: shut up and listen.
        audio_out::flush();
        gServerSaidDone = true;
        setState(ST_IDLE);
      }
    }
  }
}

// ---------------------------- periodic reporting ---------------------------

void reportState() {
  const uint32_t now = millis();
  if (now - gLastStateReport < STATE_REPORT_INTERVAL_MS) return;
  gLastStateReport = now;
  if (!ws_link::isConnected()) return;
  ws_link::sendState(stateName(gState), power::batteryMillivolts(),
                     power::batteryPercent(), wifi_manager::rssi());
}

void serviceBattery() {
  power::tick();
  // Critically low: motion is what actually browns out the board, so cut it
  // and let the voice assistant keep working.
  motors::setInhibited(power::isCritical());
  if (power::isCritical() && gState == ST_IDLE) {
    display::setStatusLine("battery low");
  }
}

}  // namespace

// --------------------------------- setup -----------------------------------

void setup() {
  Serial.begin(WALLE_SERIAL_BAUD);
  delay(200);
  Serial.printf("\nWall-E %s booting\n", WALLE_FW_VERSION);

  pinMode(PIN_BUTTON, INPUT_PULLUP);

  if (!display::begin()) Serial.println("[init] OLED not found (check SDA/SCL)");
  display::showBanner("Wall-E", "starting up");

  if (!power::begin()) Serial.println("[init] battery ADC failed");
  if (!motors::begin()) Serial.println("[init] servos failed");

  if (!audio_in::begin()) {
    Serial.println("[init] microphone failed - halting");
    display::showBanner("Mic failed", "check I2S wiring");
    // No microphone means no voice assistant. Sit here blinking rather than
    // pretending to work.
    while (true) {
      display::tick();
      delay(50);
    }
  }
  if (!audio_out::begin()) Serial.println("[init] speaker failed");

#if WALLE_ENABLE_CAMERA
  if (!camera::begin()) Serial.println("[init] camera failed (vision disabled)");
#endif

  gWakewordAvailable = wakeword::begin();
  Serial.printf("[init] wake word backend: %s\n",
                gWakewordAvailable ? wakeword::backendName() : "button only");

  display::showBanner("Wall-E", "joining wifi");
  if (wifi_manager::begin()) {
    Serial.printf("[init] wifi ok, ip=%s\n", wifi_manager::ipAddress());
  } else {
    Serial.println("[init] wifi timeout - will keep retrying");
  }

  ota::begin();
  ws_link::begin(handleControl, handleAudio);

  setState(ST_CONNECTING);
  Serial.println("[init] ready");
}

// ---------------------------------- loop -----------------------------------

void loop() {
  // 1. Audio first. This blocks for ~20 ms and is what paces the whole loop.
  const size_t got = audio_in::readFrame(gFrame);

  // 2. Service everything that must run regardless of state.
  wifi_manager::tick();
  ws_link::loop();
  ota::tick();
  motors::tick();
  serviceBattery();
  display::tick();
  serviceButton();
  audio_out::tick();
  reportState();

  if (gPendingOta) {
    gPendingOta = false;
    setState(ST_UPDATING);
    ota::pullUpdate(gOtaUrl);  // reboots on success
    setState(ST_IDLE);
    return;
  }
  if (ota::inProgress()) return;

  // Show the live input level while listening, and the output level while
  // talking, so the face always reflects what is actually happening.
  display::setAudioLevel(gState == ST_SPEAKING ? audio_out::level()
                                               : audio_in::lastLevelPercent());

  // 3. Link supervision. Anything below here needs a live server.
  if (!ws_link::isConnected()) {
    if (gState != ST_CONNECTING && gState != ST_UPDATING) {
      motors::stop();
      setState(ST_CONNECTING);
    }
    return;
  }

  if (got == 0) return;  // I2S hiccup; nothing else to do this round

  // 4. Wake-word detection runs on every frame in every state except while
  //    Wall-E is talking (wakeword::setMuted handles that internally).
  const bool woke = gWakewordAvailable && wakeword::feed(gFrame, got);

  // 5. State machine.
  switch (gState) {
    case ST_CONNECTING:
      // handleControl() moves us to IDLE when hello_ack lands.
      break;

    case ST_IDLE:
      if (woke) {
        ws_link::sendWake(wakeword::lastWord(), wakeword::lastScore());
        beginUtterance("wakeword");
      }
      break;

    case ST_LISTENING: {
      ws_link::sendAudio(gFrame, got, WALLE_FLAG_NONE);
      const bool done = vad::feedRms(audio_in::lastRms());
      if (done || vad::timedOut()) {
        endUtterance(vad::heardSpeech());
      }
      break;
    }

    case ST_THINKING:
      // The server owns this phase. Bail out if it goes quiet for too long so
      // the robot never gets stuck staring at the ceiling.
      if (millis() - gStateSince > 20000) {
        Serial.println("[link] server timed out");
        display::setExpression(EXPR_CONFUSED, 1500);
        setState(ST_IDLE);
      }
      break;

    case ST_SPEAKING:
      if (gServerSaidDone && !audio_out::busy()) {
        // Small guard band before re-arming the wake word: the speaker cone
        // and the room are still ringing for a few tens of milliseconds.
        if (gUnmuteAt == 0) {
          gUnmuteAt = millis() + 200;
        } else if (millis() >= gUnmuteAt) {
          gUnmuteAt = 0;
          audio_in::clearPreroll();  // drop our own voice from the pre-roll
          setState(ST_IDLE);
        }
      } else {
        gUnmuteAt = 0;
      }
      break;

    default:
      break;
  }
}
