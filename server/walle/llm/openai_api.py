# SPDX-License-Identifier: MIT
"""OpenAI-compatible cloud backend.

Also works with anything that speaks the same /v1/chat/completions shape
(Groq, Together, LM Studio, llama.cpp's server, vLLM) - point
WALLE_OPENAI_BASE_URL at it.

Note the trade this makes: it is the only part of the system that sends
anything off your network, and it only ever sends the transcribed *text*, never
audio and never camera frames. Voice commands (lights, movement, the camera)
are handled by the offline intent router and keep working with the internet
unplugged.
"""

from __future__ import annotations

import logging
import time

import httpx

from ..config import LlmConfig
from .base import LlmEngine, Message, Reply, parse_markers

log = logging.getLogger(__name__)


class OpenAiLlm(LlmEngine):
    name = "openai"

    def __init__(self, cfg: LlmConfig) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.openai_base_url.rstrip("/"),
            timeout=httpx.Timeout(cfg.timeout_s, connect=5.0),
            headers={"Authorization": f"Bearer {cfg.openai_api_key}"},
        )

    async def chat(self, messages: list[Message]) -> Reply:
        started = time.monotonic()
        payload = {
            "model": self.cfg.openai_model,
            "messages": [m.as_dict() for m in messages],
            "temperature": self.cfg.temperature,
            "max_tokens": self.cfg.max_tokens,
        }

        try:
            resp = await self._client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            log.error("openai HTTP %s: %s", exc.response.status_code, exc.response.text[:200])
            code = exc.response.status_code
            hint = "auth" if code in (401, 403) else "rate-limit" if code == 429 else "http-error"
            return Reply(text="", meta={"error": hint})
        except httpx.HTTPError as exc:
            log.error("openai unreachable: %s", exc)
            return Reply(text="", meta={"error": "unreachable"})
        except ValueError:
            return Reply(text="", meta={"error": "bad-json"})

        choices = data.get("choices") or []
        raw = choices[0]["message"]["content"] if choices else ""
        reply = parse_markers(raw or "")
        reply.latency_ms = int((time.monotonic() - started) * 1000)
        usage = data.get("usage") or {}
        reply.meta = {
            "tokens": usage.get("completion_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
        }
        log.info("llm: %d ms -> %r", reply.latency_ms, reply.text[:80])
        return reply

    async def close(self) -> None:
        await self._client.aclose()
