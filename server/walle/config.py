# SPDX-License-Identifier: MIT
"""Configuration, loaded from environment variables (and optionally a .env).

Deliberately plain dataclasses rather than a settings framework: every knob is
visible in one file, and ``python -m walle.config`` prints the resolved values
so a non-engineer can see exactly what the server thinks it is doing.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader. Real values already in the environment win, so
    ``WALLE_LLM_BACKEND=openai python -m walle`` still overrides the file."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: tuple[str, ...] = ()) -> list[str]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(slots=True)
class ServerConfig:
    host: str = field(default_factory=lambda: _env("WALLE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("WALLE_PORT", 8765))
    # Shared secret the robot presents in the X-Walle-Token header. Empty means
    # "no auth", which the server refuses to start with unless you also set
    # WALLE_ALLOW_NO_AUTH=1 - an unauthenticated socket that can drive motors
    # and read your microphone is not a default anybody should get by accident.
    auth_token: str = field(default_factory=lambda: _env("WALLE_AUTH_TOKEN", ""))
    allow_no_auth: bool = field(default_factory=lambda: _env_bool("WALLE_ALLOW_NO_AUTH", False))
    log_level: str = field(default_factory=lambda: _env("WALLE_LOG_LEVEL", "INFO"))
    data_dir: Path = field(default_factory=lambda: Path(_env("WALLE_DATA_DIR", str(DEFAULT_DATA_DIR))))


@dataclass(slots=True)
class SttConfig:
    # "whispercpp" (subprocess, the documented default) or "faster-whisper"
    # (in-process, needs the Python package).
    backend: str = field(default_factory=lambda: _env("WALLE_STT_BACKEND", "whispercpp"))
    # Path to the whisper.cpp `whisper-cli` binary (older builds call it `main`).
    binary: str = field(default_factory=lambda: _env("WALLE_WHISPER_BIN", "whisper-cli"))
    model_path: str = field(default_factory=lambda: _env("WALLE_WHISPER_MODEL", "models/ggml-base.en-q5_1.bin"))
    language: str = field(default_factory=lambda: _env("WALLE_STT_LANGUAGE", "en"))
    # 0 lets whisper.cpp pick; on a Mac Mini M-series, 4 performance cores is
    # the sweet spot - more threads start fighting the Metal backend.
    threads: int = field(default_factory=lambda: _env_int("WALLE_STT_THREADS", 4))
    # See docs/03-research-notes.md: beam search buys ~1-2% WER for ~40% more
    # latency, which is the wrong trade for short voice commands.
    beam_size: int = field(default_factory=lambda: _env_int("WALLE_STT_BEAM", 1))
    # Bias the recogniser towards the words this robot actually hears.
    initial_prompt: str = field(default_factory=lambda: _env("WALLE_STT_PROMPT", ""))
    timeout_s: float = field(default_factory=lambda: _env_float("WALLE_STT_TIMEOUT", 30.0))


@dataclass(slots=True)
class TtsConfig:
    backend: str = field(default_factory=lambda: _env("WALLE_TTS_BACKEND", "piper"))
    binary: str = field(default_factory=lambda: _env("WALLE_PIPER_BIN", "piper"))
    model_path: str = field(default_factory=lambda: _env("WALLE_PIPER_MODEL", "models/en_US-lessac-medium.onnx"))
    # Piper's own rate. 1.0 is natural; Wall-E sounds better a touch slower.
    length_scale: float = field(default_factory=lambda: _env_float("WALLE_TTS_LENGTH_SCALE", 1.05))
    noise_scale: float = field(default_factory=lambda: _env_float("WALLE_TTS_NOISE_SCALE", 0.667))
    # Robot voice: a light ring-mod/bitcrush pass applied after Piper. Set to
    # 0.0 for the plain human voice.
    robot_effect: float = field(default_factory=lambda: _env_float("WALLE_TTS_ROBOT", 0.35))
    timeout_s: float = field(default_factory=lambda: _env_float("WALLE_TTS_TIMEOUT", 30.0))


@dataclass(slots=True)
class LlmConfig:
    # "ollama" (local, default and fully private) or "openai" (cloud).
    backend: str = field(default_factory=lambda: _env("WALLE_LLM_BACKEND", "ollama"))
    model: str = field(default_factory=lambda: _env("WALLE_LLM_MODEL", "llama3.2:3b"))
    ollama_url: str = field(default_factory=lambda: _env("WALLE_OLLAMA_URL", "http://127.0.0.1:11434"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: _env("WALLE_OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str = field(default_factory=lambda: _env("WALLE_OPENAI_MODEL", "gpt-4o-mini"))
    temperature: float = field(default_factory=lambda: _env_float("WALLE_LLM_TEMPERATURE", 0.6))
    # Short replies are not a stylistic preference here: every extra token is
    # extra TTS audio and extra seconds before the robot stops talking.
    max_tokens: int = field(default_factory=lambda: _env_int("WALLE_LLM_MAX_TOKENS", 160))
    timeout_s: float = field(default_factory=lambda: _env_float("WALLE_LLM_TIMEOUT", 45.0))
    # How many past turns to replay as context.
    history_turns: int = field(default_factory=lambda: _env_int("WALLE_LLM_HISTORY_TURNS", 8))


@dataclass(slots=True)
class VisionConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("WALLE_VISION_ENABLED", True))
    # "tflite" (TensorFlow Lite runtime + a COCO SSD model) or "none".
    backend: str = field(default_factory=lambda: _env("WALLE_VISION_BACKEND", "tflite"))
    model_path: str = field(default_factory=lambda: _env("WALLE_VISION_MODEL", "models/ssd_mobilenet_v1.tflite"))
    labels_path: str = field(default_factory=lambda: _env("WALLE_VISION_LABELS", "models/coco_labels.txt"))
    min_score: float = field(default_factory=lambda: _env_float("WALLE_VISION_MIN_SCORE", 0.45))
    max_results: int = field(default_factory=lambda: _env_int("WALLE_VISION_MAX_RESULTS", 5))
    save_frames: bool = field(default_factory=lambda: _env_bool("WALLE_VISION_SAVE_FRAMES", False))


@dataclass(slots=True)
class SmartHomeConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("WALLE_SMARTHOME_ENABLED", False))
    # "homeassistant" or "mqtt".
    backend: str = field(default_factory=lambda: _env("WALLE_SMARTHOME_BACKEND", "homeassistant"))
    ha_url: str = field(default_factory=lambda: _env("WALLE_HA_URL", "http://homeassistant.local:8123"))
    ha_token: str = field(default_factory=lambda: _env("WALLE_HA_TOKEN", ""))
    mqtt_host: str = field(default_factory=lambda: _env("WALLE_MQTT_HOST", "127.0.0.1"))
    mqtt_port: int = field(default_factory=lambda: _env_int("WALLE_MQTT_PORT", 1883))
    mqtt_username: str = field(default_factory=lambda: _env("WALLE_MQTT_USERNAME", ""))
    mqtt_password: str = field(default_factory=lambda: _env("WALLE_MQTT_PASSWORD", ""))
    mqtt_prefix: str = field(default_factory=lambda: _env("WALLE_MQTT_PREFIX", "walle"))
    # THE allowlist. Only these entities can ever be touched, no matter what
    # the model asks for. Read docs/06-smart-home-security.md before widening
    # it - this is the whole safety story for smart home control.
    allowed_entities: list[str] = field(default_factory=lambda: _env_list("WALLE_ALLOWED_ENTITIES"))
    # Domains the robot may act on at all. `lock` and `cover` are absent on
    # purpose: a voice assistant that can unlock your front door on a
    # misheard word is a burglary waiting to happen.
    allowed_domains: list[str] = field(
        default_factory=lambda: _env_list("WALLE_ALLOWED_DOMAINS", ("light", "switch", "fan", "scene", "media_player"))
    )
    timeout_s: float = field(default_factory=lambda: _env_float("WALLE_SMARTHOME_TIMEOUT", 8.0))


@dataclass(slots=True)
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    tts: TtsConfig = field(default_factory=TtsConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    smarthome: SmartHomeConfig = field(default_factory=SmartHomeConfig)

    @classmethod
    def load(cls, dotenv: Path | None = None) -> Config:
        _load_dotenv(dotenv or (REPO_ROOT / ".env"))
        return cls()

    def validate(self) -> list[str]:
        """Returns a list of human-readable problems. Empty means good to go.

        This is intentionally advisory rather than fatal for everything except
        authentication: a missing Piper model should print a clear warning and
        let you keep testing the rest of the pipeline.
        """
        problems: list[str] = []

        if not self.server.auth_token and not self.server.allow_no_auth:
            problems.append(
                "WALLE_AUTH_TOKEN is not set. Generate one with "
                "`python -c \"import secrets;print(secrets.token_urlsafe(32))\"` and put "
                "the same value in the firmware's secrets.h. "
                "(Set WALLE_ALLOW_NO_AUTH=1 only on a machine nobody else can reach.)"
            )
        elif self.server.auth_token and len(self.server.auth_token) < 16:
            problems.append("WALLE_AUTH_TOKEN is shorter than 16 characters - use a longer one.")

        if self.stt.backend == "whispercpp":
            if not shutil.which(self.stt.binary) and not Path(self.stt.binary).is_file():
                problems.append(f"whisper.cpp binary not found: {self.stt.binary}")
            if not Path(self.stt.model_path).is_file():
                problems.append(f"Whisper model not found: {self.stt.model_path} (run server/scripts/fetch_models.sh)")

        if self.tts.backend == "piper":
            if not shutil.which(self.tts.binary) and not Path(self.tts.binary).is_file():
                problems.append(f"Piper binary not found: {self.tts.binary}")
            if not Path(self.tts.model_path).is_file():
                problems.append(f"Piper voice not found: {self.tts.model_path} (run server/scripts/fetch_models.sh)")

        if self.llm.backend == "openai" and not self.llm.openai_api_key:
            problems.append("WALLE_LLM_BACKEND=openai but OPENAI_API_KEY is empty.")

        if self.smarthome.enabled:
            if self.smarthome.backend == "homeassistant" and not self.smarthome.ha_token:
                problems.append("Smart home is enabled with the Home Assistant backend but WALLE_HA_TOKEN is empty.")
            if not self.smarthome.allowed_entities:
                problems.append(
                    "Smart home is enabled but WALLE_ALLOWED_ENTITIES is empty, so every "
                    "command will be refused. List the entity IDs the robot may control."
                )

        return problems


def _describe(obj: Any, indent: int = 0) -> str:
    """Pretty-prints a config tree with secrets masked."""
    lines = []
    pad = "  " * indent
    for f in fields(obj):
        value = getattr(obj, f.name)
        if is_dataclass(value):
            lines.append(f"{pad}{f.name}:")
            lines.append(_describe(value, indent + 1))
        else:
            if any(word in f.name for word in ("token", "password", "api_key")) and value:
                value = f"<set, {len(str(value))} chars>"
            lines.append(f"{pad}{f.name} = {value!r}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - manual diagnostic
    cfg = Config.load()
    print(_describe(cfg))
    issues = cfg.validate()
    print("\nvalidation:", "OK" if not issues else "")
    for issue in issues:
        print(f"  - {issue}")
