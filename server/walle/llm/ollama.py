# SPDX-License-Identifier: MIT
"""Ollama backend - the default, fully local, nothing leaves the LAN."""

from __future__ import annotations

import logging
import time

import httpx

from ..config import LlmConfig
from .base import LlmEngine, Message, Reply, parse_markers

log = logging.getLogger(__name__)


class OllamaLlm(LlmEngine):
    name = "ollama"

    def __init__(self, cfg: LlmConfig) -> None:
        self.cfg = cfg
        self._client = httpx.AsyncClient(
            base_url=cfg.ollama_url.rstrip("/"),
            timeout=httpx.Timeout(cfg.timeout_s, connect=5.0),
        )

    async def chat(self, messages: list[Message]) -> Reply:
        started = time.monotonic()
        payload = {
            "model": self.cfg.model,
            "messages": [m.as_dict() for m in messages],
            "stream": False,
            "options": {
                "temperature": self.cfg.temperature,
                "num_predict": self.cfg.max_tokens,
                # Stop the model dead if it starts writing a dialogue with
                # itself, which small models love to do.
                "stop": ["\nUser:", "\nHuman:", "\nWall-E:"],
                # A modest context: the history is short by design, and a
                # smaller window means faster prefill.
                "num_ctx": 2048,
                # Keep the model resident so the next turn skips model loading.
                # This is worth several seconds on the second question.
            },
            "keep_alive": "30m",
        }

        try:
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:200]
            log.error("ollama HTTP %s: %s", exc.response.status_code, body)
            hint = "model-missing" if exc.response.status_code == 404 else "http-error"
            return Reply(text="", meta={"error": hint, "model": self.cfg.model})
        except httpx.HTTPError as exc:
            log.error("ollama unreachable at %s: %s", self.cfg.ollama_url, exc)
            return Reply(text="", meta={"error": "unreachable"})
        except ValueError:
            log.error("ollama returned invalid JSON")
            return Reply(text="", meta={"error": "bad-json"})

        raw = (data.get("message") or {}).get("content", "")
        reply = parse_markers(raw)
        reply.latency_ms = int((time.monotonic() - started) * 1000)
        # eval_count is the number of tokens generated; useful for tuning
        # max_tokens against the time the robot spends talking.
        reply.meta = {
            "tokens": data.get("eval_count", 0),
            "prompt_tokens": data.get("prompt_eval_count", 0),
        }
        log.info("llm: %d ms, %s tokens -> %r",
                 reply.latency_ms, reply.meta["tokens"], reply.text[:80])
        return reply

    async def warmup(self) -> None:
        """Loads the model into memory. Without this the first question of the
        day waits several seconds for weights to page in."""
        try:
            resp = await self._client.post(
                "/api/generate",
                json={"model": self.cfg.model, "prompt": "", "keep_alive": "30m"},
                timeout=120.0,
            )
            resp.raise_for_status()
            log.info("ollama model %s loaded", self.cfg.model)
        except httpx.HTTPError as exc:
            log.warning("could not preload ollama model %s: %s", self.cfg.model, exc)

    async def close(self) -> None:
        await self._client.aclose()
