# SPDX-License-Identifier: MIT
"""LLM backends."""

from __future__ import annotations

from ..config import LlmConfig
from .base import LlmEngine, Message, Reply, parse_markers
from .prompt import build_system_prompt


def build_llm(cfg: LlmConfig) -> LlmEngine:
    backend = cfg.backend.strip().lower()
    if backend == "ollama":
        from .ollama import OllamaLlm

        return OllamaLlm(cfg)
    if backend in {"openai", "openai-compatible"}:
        from .openai_api import OpenAiLlm

        return OpenAiLlm(cfg)
    raise ValueError(f"unknown WALLE_LLM_BACKEND {cfg.backend!r}; expected 'ollama' or 'openai'")


__all__ = ["LlmEngine", "Message", "Reply", "build_llm", "build_system_prompt", "parse_markers"]
